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
REVIEW_PROMPT = """You are a security analyst reviewing CCTV keyframes from a \
residential society in India. Frames are in time order, each labelled with its \
time in seconds.

CRITICAL — reason across the WHOLE SEQUENCE, not frame-by-frame. The frames \
are snapshots; the action often happens BETWEEN them and must be inferred by \
comparing frames:
- person beside a closed car in one frame, inside it in a later frame -> they \
likely broke in (even if the break itself isn't captured)
- a parked car present in earlier frames that is GONE (or being driven off) in \
later frames, after a stranger was at it -> the car was likely stolen/driven away
- two people working the same vehicle -> treat them as one incident (lookout + \
actor is a classic theft pattern)

Report only genuinely suspicious or criminal activity. Normal walking, normal \
parking/driving, and an owner casually entering their car are NOT suspicious.

Severity ladder — apply it strictly:
- HIGH: entering/breaking into a vehicle that isn't clearly theirs, window \
smashing, driving a vehicle away after tampering/entry, vehicle or property \
theft in progress, forced entry, violence, visible weapon
- MEDIUM: tampering with/testing doors or windows, reaching into a vehicle, \
furtive scoping or repeated circling of vehicles, vandalism
- LOW: mildly unusual behaviour worth a human glance

If the sequence shows an ESCALATING incident (scoping -> tampering -> entry -> \
vehicle leaves), report the LATER, more severe stages as HIGH — the outcome \
defines the threat, not the first frame.

Respond with ONLY a JSON array (no prose). Each item:
  {"time_s": <number: labelled time of the clearest frame for this finding>,
   "activity": "<short plain-English description, stating inferences plainly, \
e.g. 'car driven away by the person who tampered with it — likely theft'>",
   "severity": "HIGH" | "MEDIUM" | "LOW"}
If nothing is genuinely suspicious, return: []"""


class VLMDescriber:
    def __init__(self, cfg: dict):
        self.enabled = bool(cfg.get("enabled"))
        self.model = cfg.get("model", "claude-sonnet-4-6")
        # scene review benefits from the strongest vision model
        self.review_model = cfg.get("review_model", "claude-opus-4-8")
        self.max_keyframes = int(cfg.get("max_keyframes", 6))
        self.review_max_frames = int(cfg.get("review_max_frames", 16))
        key_env = cfg.get("api_key_env", "ANTHROPIC_API_KEY")
        self.api_key = os.environ.get(key_env, "").strip()
        self._client = None
        # Why the reviewer is off, so callers can show an accurate message
        # rather than always blaming a missing key.
        self.off_reason = ""
        if self.enabled and self.api_key:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self.api_key)
            except ImportError as e:
                self.off_reason = ("the anthropic SDK is not installed on the "
                                   "server (pip install anthropic)")
                log.warning("anthropic SDK not installed, VLM disabled: %s", e)
            except Exception as e:
                self.off_reason = f"the Anthropic client failed to start ({e})"
                log.warning("anthropic client init failed, VLM disabled: %s", e)
        elif self.enabled:
            self.off_reason = (f"no API key found in the {key_env} environment "
                               "variable")

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
