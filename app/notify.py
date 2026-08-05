"""Telegram notifications: instant message + clip video, with rate capping.

Routing: every alert goes to the guard + manager chats; A1/A3 additionally go
to the registered owner's chat_id when the involved plate is registered.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from .rules import TITLES, UNAUTHORIZED_VEHICLE, VEHICLE_CONTACT

log = logging.getLogger(__name__)


def _india_tz():
    """India Standard Time. Prefer the tz database (handles any future rule
    changes); fall back to a fixed +05:30 offset when the OS ships no tz data
    (common on Windows without the `tzdata` package), so the app never crashes
    on import."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Asia/Kolkata")
    except Exception:
        return timezone(timedelta(hours=5, minutes=30), "IST")


IST = _india_tz()
API = "https://api.telegram.org/bot{token}/{method}"
MAX_CLIP_BYTES = 49 * 1024 * 1024  # Telegram bot API cap is 50MB

# Hindi titles/labels for guard-friendly alerts (config: telegram.language: hi)
TITLES_HI = {
    "unauthorized_vehicle": "अनधिकृत वाहन प्रवेश",
    "unidentified_vehicle": "अज्ञात वाहन (नंबर प्लेट नहीं पढ़ी गई)",
    "loitering": "वाहनों के पास संदिग्ध व्यक्ति",
    "vehicle_contact": "संभावित वाहन टक्कर",
    "restricted_zone": "प्रतिबंधित क्षेत्र में व्यक्ति",
    "camera_tamper": "कैमरे से छेड़छाड़",
    "camera_offline": "कैमरा ऑफ़लाइन",
    "suspicious_activity": "संदिग्ध गतिविधि",
}
SEVERITY_HI = {"HIGH": "उच्च", "MEDIUM": "मध्यम", "LOW": "निम्न"}
LABELS = {
    "en": {"camera": "Camera", "plate": "Plate", "unreadable": "unreadable",
           "update": "UPDATE — same incident", "correct": "✅ Correct",
           "false": "❌ False alarm"},
    "hi": {"camera": "कैमरा", "plate": "नंबर प्लेट",
           "unreadable": "पढ़ने योग्य नहीं", "update": "अपडेट — वही घटना",
           "correct": "✅ सही", "false": "❌ गलत अलार्म"},
}


def build_feedback_keyboard(event_id: int, language: str = "en") -> dict:
    """Inline ✅/❌ buttons — guard feedback that tunes the system over time."""
    lb = LABELS.get(language, LABELS["en"])
    return {"inline_keyboard": [[
        {"text": lb["correct"], "callback_data": f"fb:{event_id}:correct"},
        {"text": lb["false"], "callback_data": f"fb:{event_id}:false_alarm"},
    ]]}


def parse_feedback_callback(data: str) -> tuple[int, str] | None:
    """'fb:<event_id>:<verdict>' -> (event_id, verdict) or None."""
    parts = (data or "").split(":")
    if len(parts) == 3 and parts[0] == "fb" and parts[1].isdigit() \
            and parts[2] in ("correct", "false_alarm"):
        return int(parts[1]), parts[2]
    return None


class TelegramNotifier:
    def __init__(self, cfg: dict, db, max_per_hour: int = 10):
        self.cfg = cfg
        self.db = db
        self.enabled = bool(cfg.get("enabled"))
        self.token = cfg.get("bot_token", "")
        self.chat_ids = {k: str(v) for k, v in (cfg.get("chat_ids") or {}).items() if v}
        self.send_clips = bool(cfg.get("send_clips", True))
        self.language = cfg.get("language", "en")
        self.feedback_buttons = bool(cfg.get("feedback_buttons", True))
        self.max_per_hour = max_per_hour
        self._poller_started = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def notify_event(self, event, event_id: int, clip_path: str | None = None,
                     vlm_description: str | None = None):
        """Send alert + clip to all routed chats. Runs in the caller thread
        (call from a clip/worker thread, not the video loop)."""
        chats = dict(self.chat_ids)
        # owner routing for A1/A3
        if event.event_type in (UNAUTHORIZED_VEHICLE, VEHICLE_CONTACT) and event.plate:
            owner = self.db.vehicle_by_plate(event.plate)
            if owner and owner.get("telegram_chat_id"):
                chats[f"owner:{event.plate}"] = str(owner["telegram_chat_id"])

        if not self.enabled or not self.token or not chats:
            log.info("telegram disabled — would have sent: %s", TITLES[event.event_type])
            for label in chats or {"guard": ""}:
                self.db.insert_notification(event_id, chats.get(label, "disabled"),
                                            "skipped-disabled")
            return

        with self._lock:
            if self.db.notifications_last_hour(event.camera) >= self.max_per_hour:
                log.warning("notification cap reached for %s — logging only",
                            event.camera)
                for label, chat in chats.items():
                    self.db.insert_notification(event_id, chat, "suppressed-cap")
                return

        # incident threading: follow-ups of an already-alerted incident are
        # marked as updates and skip the (heavy) video re-upload
        incident_id = None
        is_update = False
        row = self.db.get_event(event_id)
        if row and row.get("incident_id"):
            incident_id = row["incident_id"]
            is_update = self.db.notifications_for_incident(incident_id) > 0

        location = self.db.describe_camera(event.camera)
        text = self.format_message(event, vlm_description,
                                   language=self.language,
                                   incident_id=incident_id, is_update=is_update,
                                   location=location)
        keyboard = build_feedback_keyboard(event_id, self.language) \
            if self.feedback_buttons else None
        for label, chat in chats.items():
            status = "sent" if self._send_text(chat, text, keyboard) else "failed"
            if status == "sent" and clip_path and self.send_clips and not is_update:
                if not self._send_video(chat, clip_path):
                    status = "sent-no-clip"
            self.db.insert_notification(event_id, chat, status)
            log.info("notify %s (%s): %s", label, chat, status)

    # ------------------------------------------------------------------
    @staticmethod
    def format_message(event, vlm_description: str | None = None,
                       language: str = "en", incident_id: int | None = None,
                       is_update: bool = False,
                       location: str | None = None) -> str:
        t = datetime.fromtimestamp(event.ts, IST).strftime("%d %b %Y %H:%M:%S IST")
        desc = vlm_description or event.description
        lb = LABELS.get(language, LABELS["en"])
        if language == "hi":
            title = TITLES_HI.get(event.event_type, TITLES[event.event_type])
            sev = SEVERITY_HI.get(event.severity, event.severity)
        else:
            title = TITLES[event.event_type]
            sev = event.severity
        head = f"\U0001F6A8 [{sev}] {title}"
        if incident_id:
            head += f"  (#गुट {incident_id})" if language == "hi" \
                else f"  (Incident #{incident_id})"
        lines = [head]
        if is_update:
            lines.append(f"\U0001F501 {lb['update']}")
        lines.append(f"\U0001F4CD {lb['camera']}: {location or event.camera}"
                     f"  |  \U0001F550 {t}")
        lines.append(f"\U0001F697 {lb['plate']}: {event.plate or lb['unreadable']}")
        lines.append(f"\U0001F4C4 {desc}")
        return "\n".join(lines)

    def notify_slot_owner(self, change, when: str) -> bool:
        """Tell one resident their car moved. Owner only — nobody else needs
        to know when a neighbour comes and goes, and a system that tells them
        is a surveillance complaint waiting to happen."""
        if not self.enabled or not self.token:
            return False
        plate = change.slot.plate or change.plate
        owner = self.db.vehicle_by_plate(plate) if plate else None
        chat = (owner or {}).get("telegram_chat_id")
        if not chat:
            return False
        return self._send_text(str(chat),
                               f"{change.message()} at {when}.\n\n"
                               "If that was you, nothing to do.")

    def broadcast_notice(self, title: str, body: str, audience: str = "all",
                         flat_number: str = "") -> int:
        """Push a committee announcement to residents. Returns how many chats
        it reached — 0 (not an error) when Telegram is off or nobody in the
        registry has a chat id yet."""
        if not self.enabled or not self.token:
            return 0
        chats: dict[str, str] = {}
        if audience == "flat" and flat_number:
            for v in self.db.list_vehicles():
                if v.get("flat_number") == flat_number and v.get("telegram_chat_id"):
                    chats[str(v["telegram_chat_id"])] = str(v["telegram_chat_id"])
        else:
            chats.update({v: v for v in self.chat_ids.values()})
            for v in self.db.list_vehicles():
                if v.get("telegram_chat_id"):
                    chats[str(v["telegram_chat_id"])] = str(v["telegram_chat_id"])
        text = f"{title}\n\n{body}" if title else body
        return sum(1 for c in chats.values() if self._send_text(c, text))

    def _send_text(self, chat_id: str, text: str, keyboard: dict | None = None) -> bool:
        try:
            payload = {"chat_id": chat_id, "text": text}
            if keyboard:
                import json as _json
                payload["reply_markup"] = _json.dumps(keyboard)
            r = requests.post(API.format(token=self.token, method="sendMessage"),
                              data=payload, timeout=15)
            return r.ok
        except requests.RequestException as e:
            log.warning("sendMessage failed: %s", e)
            return False

    # ------------------------------------------- guard feedback poller
    def start_feedback_poller(self):
        """Long-polls Telegram for ✅/❌ button presses and records them."""
        if not (self.enabled and self.token and self.feedback_buttons) \
                or self._poller_started:
            return
        self._poller_started = True
        threading.Thread(target=self._poll_loop, daemon=True,
                         name="tg-feedback").start()

    def _poll_loop(self):
        offset = 0
        while True:
            try:
                r = requests.get(
                    API.format(token=self.token, method="getUpdates"),
                    params={"timeout": 50, "offset": offset,
                            "allowed_updates": '["callback_query"]'},
                    timeout=60)
                if not r.ok:
                    continue
                for upd in r.json().get("result", []):
                    offset = max(offset, upd["update_id"] + 1)
                    cq = upd.get("callback_query")
                    if not cq:
                        continue
                    parsed = parse_feedback_callback(cq.get("data", ""))
                    if parsed:
                        event_id, verdict = parsed
                        user = (cq.get("from") or {}).get("first_name", "")
                        self.db.insert_feedback(event_id, verdict, user)
                        log.info("feedback: event %s -> %s (%s)",
                                 event_id, verdict, user)
                    try:
                        requests.post(
                            API.format(token=self.token,
                                       method="answerCallbackQuery"),
                            data={"callback_query_id": cq["id"],
                                  "text": "Recorded ✅"}, timeout=10)
                    except requests.RequestException:
                        pass
            except requests.RequestException:
                import time as _time
                _time.sleep(5)
            except Exception:
                log.exception("feedback poller error")
                import time as _time
                _time.sleep(10)

    def _send_video(self, chat_id: str, clip_path: str) -> bool:
        p = Path(clip_path)
        if not p.exists() or p.stat().st_size > MAX_CLIP_BYTES:
            return False
        try:
            with p.open("rb") as f:
                r = requests.post(
                    API.format(token=self.token, method="sendVideo"),
                    data={"chat_id": chat_id},
                    files={"video": (p.name, f, "video/mp4")}, timeout=120)
            return r.ok
        except requests.RequestException as e:
            log.warning("sendVideo failed: %s", e)
            return False
