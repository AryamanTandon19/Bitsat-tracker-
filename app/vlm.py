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


class VLMDescriber:
    def __init__(self, cfg: dict):
        self.enabled = bool(cfg.get("enabled"))
        self.model = cfg.get("model", "claude-sonnet-4-6")
        self.max_keyframes = int(cfg.get("max_keyframes", 6))
        self.api_key = os.environ.get(cfg.get("api_key_env", "ANTHROPIC_API_KEY"), "")
        self._client = None
        if self.enabled and self.api_key:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self.api_key)
            except Exception as e:
                log.warning("anthropic SDK unavailable, VLM disabled: %s", e)

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
