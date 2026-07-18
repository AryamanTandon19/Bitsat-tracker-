"""Two-tier live AI review with cost tracking.

On every triggered event, keyframes from the evidence clip go through:

  Tier 1 (cheap screener, default Haiku):  "anything suspicious? yes/no + line"
  Tier 2 (strong reviewer, default Opus):  only if tier 1 says yes — full
                                            findings with times + severities.

Every API call's token usage and estimated cost is written to the ai_usage
table, so the dashboard can show exactly what the AI layer spends — the
measurement that makes cost-cutting decisions possible.

Controls (config `ai_review`): per-camera allowlist, schedule (always /
night_only), rolling 24h per-camera review cap, models, frame count.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime

from .rules import is_night
from .vlm import REVIEW_PROMPT, VLMDescriber

log = logging.getLogger(__name__)

# USD per million tokens (input, output) — for cost estimates only
PRICES_USD_PER_MTOK = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
}
DEFAULT_PRICE = (5.0, 25.0)

TIER1_PROMPT = """You are a fast CCTV screener for a residential society in \
India. These are time-labelled keyframes from ONE flagged moment. Decide if \
anything genuinely suspicious or criminal may be happening (breaking into or \
tampering with a vehicle, stealing, vandalism, forced entry, a fight, furtive \
behaviour). Normal walking/parking/driving is NOT suspicious.
Respond ONLY with JSON: {"suspicious": true|false, "summary": "<one line>"}"""


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    pi, po = PRICES_USD_PER_MTOK.get(model, DEFAULT_PRICE)
    return (input_tokens * pi + output_tokens * po) / 1e6


class TieredReviewer:
    def __init__(self, cfg: dict, db, localtime_fn=datetime.now):
        self.cfg = cfg or {}
        self.db = db
        self.localtime_fn = localtime_fn
        self.enabled = bool(self.cfg.get("enabled"))
        self.tier1_model = self.cfg.get("tier1_model", "claude-haiku-4-5")
        self.tier2_model = self.cfg.get("tier2_model", "claude-opus-4-8")
        self.escalate = bool(self.cfg.get("escalate", True))
        self.cameras = list(self.cfg.get("cameras") or [])   # [] = all cameras
        self.schedule = self.cfg.get("schedule", "always")   # always | night_only
        self.night_hours = self.cfg.get("night_hours",
                                        {"start": "23:00", "end": "05:00"})
        self.max_frames = int(self.cfg.get("max_frames", 10))
        self.daily_cap = int(self.cfg.get("daily_cap_per_camera", 100))
        self.usd_to_inr = float(self.cfg.get("usd_to_inr", 90.0))
        self._client = None
        if self.enabled:
            api_key = os.environ.get(
                self.cfg.get("api_key_env", "ANTHROPIC_API_KEY"), "")
            if api_key:
                try:
                    import anthropic
                    self._client = anthropic.Anthropic(api_key=api_key)
                except Exception as e:
                    log.warning("anthropic SDK unavailable — AI review off: %s", e)
            else:
                log.warning("ai_review.enabled but no API key in env — AI review off")

    @property
    def available(self) -> bool:
        return self._client is not None

    # ------------------------------------------------------------- gating
    def active_for(self, camera: str) -> tuple[bool, str]:
        """Should this camera's events be AI-reviewed right now?"""
        if not self.enabled:
            return False, "disabled"
        if not self.available:
            return False, "no-api-key"
        if self.cameras and camera not in self.cameras:
            return False, "camera-not-enabled"
        if self.schedule == "night_only" and not is_night(
                self.localtime_fn(), self.night_hours.get("start", "23:00"),
                self.night_hours.get("end", "05:00")):
            return False, "outside-schedule"
        if self.db.ai_reviews_last_24h(camera) >= self.daily_cap:
            return False, "daily-cap-reached"
        return True, "ok"

    # ------------------------------------------------------------- review
    def review_clip(self, event, event_id: int, camera: str,
                    keyframes_jpeg: list[bytes]) -> dict | None:
        """Run tier 1 (and tier 2 if warranted). Logs usage + review rows.
        Returns {suspicious, summary, findings, alert_text, cost_inr} or None."""
        ok, why = self.active_for(camera)
        if not ok:
            log.info("AI review skipped for %s: %s", camera, why)
            return None
        frames = keyframes_jpeg[: self.max_frames]
        if not frames:
            return None
        total_cost = 0.0

        # ---- tier 1: cheap screen
        text, cost = self._call(self.tier1_model, frames, TIER1_PROMPT,
                                camera, event_id, max_tokens=300)
        total_cost += cost
        suspicious, summary = self._parse_tier1(text)
        self.db.insert_ai_review(event_id, camera, 1, self.tier1_model,
                                 suspicious, summary, [])
        findings = []

        # ---- tier 2: strong reviewer on escalation
        if suspicious and self.escalate:
            text2, cost2 = self._call(self.tier2_model, frames, REVIEW_PROMPT,
                                      camera, event_id, max_tokens=1500)
            total_cost += cost2
            findings = VLMDescriber._parse_findings(text2)
            best = findings[0]["activity"] if findings else summary
            self.db.insert_ai_review(event_id, camera, 2, self.tier2_model,
                                     bool(findings), best, findings)
            if findings:
                summary = best

        alert_text = self._alert_text(suspicious, summary, findings)
        return {"suspicious": suspicious, "summary": summary,
                "findings": findings, "alert_text": alert_text,
                "cost_inr": round(total_cost * self.usd_to_inr, 2)}

    # ---------------------------------------------------------- internals
    def _call(self, model: str, frames: list[bytes], prompt: str,
              camera: str, event_id: int, max_tokens: int) -> tuple[str, float]:
        content = []
        for i, jpg in enumerate(frames):
            content.append({"type": "text", "text": f"Frame {i + 1}:"})
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg",
                           "data": base64.standard_b64encode(jpg).decode("ascii")}})
        content.append({"type": "text", "text": prompt})
        try:
            resp = self._client.messages.create(
                model=model, max_tokens=max_tokens,
                messages=[{"role": "user", "content": content}])
            text = next((b.text for b in resp.content if b.type == "text"), "")
            in_tok = getattr(resp.usage, "input_tokens", 0)
            out_tok = getattr(resp.usage, "output_tokens", 0)
        except Exception as e:
            log.warning("AI call failed (%s): %s", model, e)
            return "", 0.0
        cost = estimate_cost_usd(model, in_tok, out_tok)
        self.db.insert_ai_usage(camera, event_id, model, in_tok, out_tok, cost)
        return text, cost

    @staticmethod
    def _parse_tier1(text: str) -> tuple[bool, str]:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start:end + 1])
                return (bool(data.get("suspicious")),
                        str(data.get("summary", "")).strip())
            except json.JSONDecodeError:
                pass
        return False, ""

    @staticmethod
    def _alert_text(suspicious: bool, summary: str, findings: list) -> str:
        if not suspicious:
            return f"AI screened: nothing clearly criminal. {summary}".strip()
        if findings:
            parts = [f"[{f['severity']}] {f['time_s']}s — {f['activity']}"
                     for f in findings[:4]]
            return "AI review: " + "; ".join(parts)
        return f"AI review: {summary}"
