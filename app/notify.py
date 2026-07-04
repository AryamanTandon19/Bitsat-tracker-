"""Telegram notifications: instant message + clip video, with rate capping.

Routing: every alert goes to the guard + manager chats; A1/A3 additionally go
to the registered owner's chat_id when the involved plate is registered.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from .rules import TITLES, UNAUTHORIZED_VEHICLE, VEHICLE_CONTACT

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
API = "https://api.telegram.org/bot{token}/{method}"
MAX_CLIP_BYTES = 49 * 1024 * 1024  # Telegram bot API cap is 50MB


class TelegramNotifier:
    def __init__(self, cfg: dict, db, max_per_hour: int = 10):
        self.cfg = cfg
        self.db = db
        self.enabled = bool(cfg.get("enabled"))
        self.token = cfg.get("bot_token", "")
        self.chat_ids = {k: str(v) for k, v in (cfg.get("chat_ids") or {}).items() if v}
        self.send_clips = bool(cfg.get("send_clips", True))
        self.max_per_hour = max_per_hour
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

        text = self.format_message(event, vlm_description)
        for label, chat in chats.items():
            status = "sent" if self._send_text(chat, text) else "failed"
            if status == "sent" and clip_path and self.send_clips:
                if not self._send_video(chat, clip_path):
                    status = "sent-no-clip"
            self.db.insert_notification(event_id, chat, status)
            log.info("notify %s (%s): %s", label, chat, status)

    # ------------------------------------------------------------------
    @staticmethod
    def format_message(event, vlm_description: str | None = None) -> str:
        t = datetime.fromtimestamp(event.ts, IST).strftime("%d %b %Y %H:%M:%S IST")
        desc = vlm_description or event.description
        return (f"\U0001F6A8 [{event.severity}] {TITLES[event.event_type]}\n"
                f"\U0001F4CD Camera: {event.camera}  |  \U0001F550 {t}\n"
                f"\U0001F697 Plate: {event.plate or 'unreadable'}\n"
                f"\U0001F4C4 {desc}")

    def _send_text(self, chat_id: str, text: str) -> bool:
        try:
            r = requests.post(API.format(token=self.token, method="sendMessage"),
                              data={"chat_id": chat_id, "text": text}, timeout=15)
            return r.ok
        except requests.RequestException as e:
            log.warning("sendMessage failed: %s", e)
            return False

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
