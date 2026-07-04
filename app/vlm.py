"""Optional VLM layer: one-line incident description from clip keyframes.

Off by default (config: vlm.enabled). Sends up to 6 keyframes to the Anthropic
API and asks for a one-line JSON description. Fails gracefully — no API key,
no `anthropic` package, or any API error simply returns None.
"""
from __future__ import annotations

import base64
import json
import logging
import os

log = logging.getLogger(__name__)

PROMPT = (
    "These are keyframes from a CCTV anomaly clip at a residential society in "
    "India. The detected anomaly type is: {event_type}. Describe the incident "
    "in ONE short line for a security alert. Respond with JSON only: "
    '{{"description": "<one line>"}}'
)

# Full-video "smart review": Claude actually watches the frames and reports
# what is happening — this is what understands theft/break-in/vandalism, which
# geometry rules cannot.
REVIEW_PROMPT = """You are a security analyst reviewing CCTV frames from a \
residential society in India. The frames are in time order (each is labelled \
with its time in seconds).

Identify ONLY genuinely suspicious or criminal activity, for example: breaking \
a car window, breaking into or tampering with a vehicle, forced entry, \
stealing something, vandalism, a fight, or a person acting furtively / hiding / \
scoping out vehicles. Do NOT report normal activity such as people simply \
walking, cars parked or driving normally, or someone getting into their own \
car normally.

Respond with ONLY a JSON array (no prose). Each item must be:
  {"time_s": <number: the labelled time of the frame where it's clearest>,
   "activity": "<short plain-English description of what is happening>",
   "severity": "HIGH" | "MEDIUM" | "LOW"}
If you see nothing genuinely suspicious, return an empty array: []"""


class VLMDescriber:
    def __init__(self, cfg: dict):
        self.enabled = bool(cfg.get("enabled"))
        self.model = cfg.get("model", "claude-sonnet-4-6")
        # scene review benefits from the strongest vision model
        self.review_model = cfg.get("review_model", "claude-opus-4-8")
        self.max_keyframes = int(cfg.get("max_keyframes", 6))
        self.review_max_frames = int(cfg.get("review_max_frames", 16))
        self.api_key = os.environ.get(cfg.get("api_key_env", "ANTHROPIC_API_KEY"), "")
        self._client = None
        if self.enabled and self.api_key:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self.api_key)
            except Exception as e:
                log.warning("anthropic SDK unavailable, VLM disabled: %s", e)

    @property
    def available(self) -> bool:
        return self._client is not None

    def review_video(self, frames_with_times: list[tuple[float, bytes]]) -> list[dict]:
        """Claude watches time-labelled keyframes and returns a list of
        suspicious-activity findings: [{time_s, activity, severity}]. Returns
        [] on no findings or any failure (never raises)."""
        if not self._client or not frames_with_times:
            return []
        frames = frames_with_times[: self.review_max_frames]
        content = []
        for t, jpg in frames:
            content.append({"type": "text", "text": f"Frame at {t:.1f}s:"})
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg",
                           "data": base64.standard_b64encode(jpg).decode("ascii")},
            })
        content.append({"type": "text", "text": REVIEW_PROMPT})
        try:
            resp = self._client.messages.create(
                model=self.review_model, max_tokens=1500,
                messages=[{"role": "user", "content": content}])
            text = next((b.text for b in resp.content if b.type == "text"), "")
            return self._parse_findings(text)
        except Exception as e:
            log.warning("AI review failed: %s", e)
            return []

    @staticmethod
    def _parse_findings(text: str) -> list[dict]:
        start, end = text.find("["), text.rfind("]")
        if start < 0 or end <= start:
            return []
        try:
            items = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return []
        out = []
        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict):
                continue
            out.append({
                "time_s": round(float(it.get("time_s", 0) or 0), 1),
                "activity": str(it.get("activity", "")).strip(),
                "severity": str(it.get("severity", "MEDIUM")).upper()
                if str(it.get("severity", "")).upper() in ("HIGH", "MEDIUM", "LOW")
                else "MEDIUM",
            })
        return out

    def describe(self, keyframes_jpeg: list[bytes], event_type: str) -> str | None:
        if not self._client or not keyframes_jpeg:
            return None
        try:
            content = []
            for jpg in keyframes_jpeg[: self.max_keyframes]:
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": base64.standard_b64encode(jpg).decode("ascii"),
                    },
                })
            content.append({"type": "text",
                            "text": PROMPT.format(event_type=event_type)})
            response = self._client.messages.create(
                model=self.model,
                max_tokens=256,
                messages=[{"role": "user", "content": content}],
            )
            text = next((b.text for b in response.content if b.type == "text"), "")
            # tolerate code fences / stray text around the JSON
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                return json.loads(text[start:end + 1]).get("description")
        except Exception as e:
            log.warning("VLM description failed (continuing without): %s", e)
        return None
