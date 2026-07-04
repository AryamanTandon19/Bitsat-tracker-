"""Claude-powered tuning assistant.

Lets a non-technical operator correct the system in plain language — "that
loitering alert was wrong, make it less sensitive" — and get a proposed change
to the *thresholds* in config.yaml, which they review and apply. The assistant
can only touch tuning knobs (rules / detection / plates / clips); it can never
edit telegram tokens, camera URLs or auth. Every applied change is audited.

Claude access is optional and lazy — no API key means the endpoint returns a
clear message instead of crashing.
"""
from __future__ import annotations

import copy
import json
import logging
import os

import yaml

log = logging.getLogger(__name__)

# Only these top-level config sections may be changed by the assistant.
EDITABLE_SECTIONS = ("rules", "detection", "plates", "clips")

SYSTEM_PROMPT = """You are the tuning assistant for "Society AI Watchdog", a \
CCTV anomaly-detection system for an Indian residential society. You help a \
non-technical guard/manager correct and tune the system in plain language.

The five anomalies and their key thresholds (all under config `rules`):
- unauthorized_vehicle (A1): vehicle in the entry zone whose plate isn't in \
the registry. `plate_read_timeout_s` = seconds before alerting "plate unreadable".
- loitering (A2): person in the parking zone. `dwell_s` / `night_dwell_s` = \
seconds before it's loitering; `max_displacement_px` = max movement to still \
count as loitering. Raise dwell_s to make it LESS sensitive.
- vehicle_contact (A3): two vehicles touch then one leaves fast. \
`iou_threshold` (lower = more sensitive), `depart_window_s`, `depart_speed_px_s`.
- restricted_zone (A4): person in restricted zone at night.
- tamper (A5): camera blinded/blurred/offline. `dark_threshold`, \
`bright_threshold`, `blur_threshold`, `condition_hold_s`, `offline_alert_s`.
Global: `debounce_s` (quiet period per object/event), \
`max_notifications_per_hour`. Detection: `confidence` (higher = fewer, surer \
detections), `process_fps`.

You are given the current config (editable sections only) and recent events.
When the user asks for a change, respond with a short plain-language reply AND, \
if a config change is warranted, a patch.

Reply ONLY with JSON in this exact shape:
{"reply": "<one or two friendly sentences>",
 "patch": {"rules.loitering.dwell_s": 60},
 "explanation": "<what this changes and the expected effect>"}

- `patch` maps dotted config paths to new values. Omit `patch` (or use {}) when \
no change is needed (e.g. the user is just asking a question).
- Only touch paths under: rules, detection, plates, clips. Never telegram, \
cameras, dashboard, storage, vlm.
- Keep values sane and numeric where the current value is numeric.
- Do not invent new config keys; only adjust existing ones."""


def editable_view(config: dict) -> dict:
    return {k: config.get(k) for k in EDITABLE_SECTIONS if k in config}


def _set_path(d: dict, dotted: str, value):
    parts = dotted.split(".")
    node = d
    for p in parts[:-1]:
        if not isinstance(node.get(p), dict):
            raise KeyError(dotted)
        node = node[p]
    if parts[-1] not in node:
        raise KeyError(dotted)
    node[parts[-1]] = value


def validate_patch(config: dict, patch: dict) -> tuple[dict, list[str]]:
    """Return (clean_patch, rejected_paths). A path is rejected if it isn't
    under an editable section or doesn't already exist in the config."""
    clean, rejected = {}, []
    for path, value in (patch or {}).items():
        top = path.split(".")[0]
        if top not in EDITABLE_SECTIONS:
            rejected.append(path)
            continue
        probe = copy.deepcopy(config)
        try:
            _set_path(probe, path, value)
        except KeyError:
            rejected.append(path)
            continue
        clean[path] = value
    return clean, rejected


def apply_patch(config_path: str, config: dict, patch: dict, db=None,
                actor: str = "assistant") -> dict:
    """Apply a validated patch: mutate the live config dict IN PLACE (so running
    rule engines pick up new thresholds), persist to YAML, and audit it.
    Returns {applied: {path: {old, new}}, rejected: [...]}."""
    clean, rejected = validate_patch(config, patch)
    applied = {}
    for path, value in clean.items():
        parts = path.split(".")
        node = config
        for p in parts[:-1]:
            node = node[p]
        old = node.get(parts[-1])
        node[parts[-1]] = value
        applied[path] = {"old": old, "new": value}

    if applied:
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(config, f, sort_keys=False, allow_unicode=True)
        if db is not None:
            db.append_audit(actor, "CONFIG_CHANGE", {"applied": applied})
    return {"applied": applied, "rejected": rejected}


class TuningAssistant:
    def __init__(self, cfg: dict):
        self.cfg = cfg or {}
        self.enabled = bool(self.cfg.get("enabled"))
        self.model = self.cfg.get("model", "claude-opus-4-8")
        self.api_key = os.environ.get(
            self.cfg.get("api_key_env", "ANTHROPIC_API_KEY"), "")
        self._client = None
        if self.enabled and self.api_key:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self.api_key)
            except Exception as e:
                log.warning("anthropic SDK unavailable, assistant disabled: %s", e)

    @property
    def available(self) -> bool:
        return self._client is not None

    def chat(self, message: str, config: dict, recent_events: list,
             history: list | None = None) -> dict:
        """Return {reply, patch, explanation}. Never raises for API issues."""
        if not self.available:
            return {"reply": "Assistant is not configured. Set assistant.enabled "
                             "and ANTHROPIC_API_KEY to enable Claude tuning.",
                    "patch": {}, "explanation": ""}
        context = {
            "current_config": editable_view(config),
            "recent_events": recent_events[:20],
        }
        messages = []
        for turn in (history or [])[-6:]:
            role = turn.get("role")
            if role in ("user", "assistant") and turn.get("content"):
                messages.append({"role": role, "content": str(turn["content"])})
        messages.append({
            "role": "user",
            "content": f"Context (JSON):\n{json.dumps(context)}\n\nOperator: {message}",
        })
        try:
            resp = self._client.messages.create(
                model=self.model, max_tokens=1024,
                system=SYSTEM_PROMPT, messages=messages)
            text = next((b.text for b in resp.content if b.type == "text"), "")
            return self._parse(text)
        except Exception as e:
            log.warning("assistant call failed: %s", e)
            return {"reply": f"Assistant error: {e}", "patch": {},
                    "explanation": ""}

    @staticmethod
    def _parse(text: str) -> dict:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start:end + 1])
                return {"reply": data.get("reply", ""),
                        "patch": data.get("patch") or {},
                        "explanation": data.get("explanation", "")}
            except json.JSONDecodeError:
                pass
        return {"reply": text.strip() or "(no reply)", "patch": {},
                "explanation": ""}
