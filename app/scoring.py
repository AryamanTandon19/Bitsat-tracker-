"""Track-level threat scoring — the layer that replaces the severity table.

The old model was a dictionary: loitering is always MEDIUM, contact is always
MEDIUM. That is why the system fired MEDIUM at everything. A person walking to
their own car at 6pm and a stranger crouching by a stranger's car at 3am
produced exactly the same alert, because the only thing consulted was the name
of the rule.

This scores the *situation* instead. Every rule still fires as before; what
changes is that firing now produces a number between 0 and 1, built from the
things that actually distinguish those two cases — the hour, how long they
stayed, whether the vehicle is on the registry, whether a guard has been
telling us this exact alert is nothing.

Three properties matter more than the exact weights:

  * It is explainable. Every score carries the list of signals that produced
    it, in words. A security product that cannot answer "why did this alarm?"
    does not survive its first argument with a resident.
  * It is tunable without code. Weights and thresholds live in config.yaml,
    so a site that is noisy can be calmed without a release.
  * It learns from the guards. False-alarm taps on the operator app feed back
    in as a damping term, so an alert a site keeps dismissing gets quieter at
    that site — which is the whole point of collecting them.

Pure functions, no I/O, no camera: every branch is unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .rules import (CAMERA_OFFLINE, CAMERA_TAMPER, LOITERING, RESTRICTED_ZONE,
                    SUSPICIOUS_ACTIVITY, UNAUTHORIZED_VEHICLE,
                    UNIDENTIFIED_VEHICLE, VEHICLE_CONTACT)

# Where a rule starts before anything about the situation is known. These are
# priors, not verdicts — a loitering report begins below the alert line and has
# to earn its way up.
BASE = {
    UNAUTHORIZED_VEHICLE: 0.60,
    UNIDENTIFIED_VEHICLE: 0.28,
    LOITERING:            0.34,
    VEHICLE_CONTACT:      0.46,
    RESTRICTED_ZONE:      0.58,
    CAMERA_TAMPER:        0.80,
    CAMERA_OFFLINE:       0.82,
    SUSPICIOUS_ACTIVITY:  0.30,
}

# A camera going dark is a fact about equipment, not a judgement about a
# person, so nothing may damp it — including a site that dismisses it often.
FIXED = {CAMERA_OFFLINE}

WEIGHTS = {
    "night":          0.12,   # after dark
    "registered":    -0.34,   # the vehicle is on the society's registry
    "unknown_plate":  0.08,   # a plate we could not read or do not know
    "dwell":          0.20,   # cap for staying put; scaled by how long
    "at_vehicle":     0.11,   # in contact with a vehicle, not just passing
    "restricted":     0.15,   # inside a zone marked off-limits
    "repeat":         0.10,   # same subject seen again recently
    "false_alarms":  -0.26,   # this site keeps telling us this is nothing
    "confirmed":      0.16,   # ...or keeps telling us it is real
}

# score -> severity. Below `dismiss` the event is not raised at all.
THRESHOLDS = {"dismiss": 0.30, "low": 0.45, "medium": 0.65}

# above this, the situation is worth spending money on
AI_REVIEW_ABOVE = 0.55


@dataclass
class Signal:
    """One reason the score moved, in words a resident could follow."""
    name: str
    delta: float
    why: str


@dataclass
class Score:
    value: float
    severity: str                     # DISMISS | LOW | MEDIUM | HIGH
    signals: list[Signal] = field(default_factory=list)

    @property
    def dismissed(self) -> bool:
        return self.severity == "DISMISS"

    def wants_ai(self, above: float = AI_REVIEW_ABOVE) -> bool:
        return self.value >= above

    def explain(self) -> str:
        """One line, strongest reason first — this is what a guard reads."""
        if not self.signals:
            return "no distinguishing signals"
        ranked = sorted(self.signals, key=lambda s: -abs(s.delta))
        return "; ".join(s.why for s in ranked)


def _cfg(cfg: dict, section: str, key: str, default: float) -> float:
    return float((cfg.get(section) or {}).get(key, default))


def severity_for(value: float, cfg: dict | None = None) -> str:
    cfg = cfg or {}
    t = {**THRESHOLDS, **(cfg.get("thresholds") or {})}
    if value < float(t["dismiss"]):
        return "DISMISS"
    if value < float(t["low"]):
        return "LOW"
    if value < float(t["medium"]):
        return "MEDIUM"
    return "HIGH"


def score_event(event_type: str, ctx: dict | None = None,
                cfg: dict | None = None) -> Score:
    """Score one firing.

    ctx is everything known about the situation; every key is optional, and a
    missing key simply contributes nothing rather than guessing:

      night           bool   after the configured night hours
      registered      bool   the vehicle is on the registry
      plate_known     bool   a plate was read at all
      dwell_s         float  how long the subject has stayed
      at_vehicle      bool   in contact with a vehicle
      restricted      bool   inside an off-limits zone
      repeat          bool   this subject was seen recently before
      false_alarm_rate float 0..1, how often this site dismisses this type
      confirmed_rate   float 0..1, how often this site confirms it
    """
    ctx, cfg = ctx or {}, cfg or {}
    w = {**WEIGHTS, **(cfg.get("weights") or {})}
    value = _cfg(cfg, "base", event_type, BASE.get(event_type, 0.35))
    signals: list[Signal] = []

    def add(name: str, delta: float, why: str):
        nonlocal value
        if not delta:
            return
        value += delta
        signals.append(Signal(name, delta, why))

    if event_type in FIXED:
        return Score(round(min(1.0, max(0.0, value)), 3),
                     severity_for(value, cfg), signals)

    if ctx.get("night"):
        add("night", w["night"], "after dark")

    # The registry is the strongest thing we know. A resident's own car being
    # where a resident's car lives is the single most common false alarm.
    if ctx.get("registered") is True:
        add("registered", w["registered"], "vehicle is on the registry")
    elif ctx.get("plate_known") is False:
        add("unknown_plate", w["unknown_plate"], "plate could not be read")

    dwell = float(ctx.get("dwell_s") or 0)
    ref = _cfg(cfg, "reference", "dwell_s", 45.0)
    if dwell > 0 and ref > 0:
        # saturating: 5 minutes is not four times as suspicious as 75 seconds
        frac = min(1.0, dwell / (ref * 3))
        add("dwell", round(w["dwell"] * frac, 4),
            f"stayed {int(dwell)}s")

    if ctx.get("at_vehicle"):
        add("at_vehicle", w["at_vehicle"], "in contact with a vehicle")
    if ctx.get("restricted"):
        add("restricted", w["restricted"], "inside a restricted zone")
    if ctx.get("repeat"):
        add("repeat", w["repeat"], "same subject seen again recently")

    # What the guards have been telling us about this kind of alert here.
    fa = min(1.0, max(0.0, float(ctx.get("false_alarm_rate") or 0)))
    if fa > 0:
        add("false_alarms", round(w["false_alarms"] * fa, 4),
            f"{int(fa * 100)}% of these were marked false alarms here")
    conf = min(1.0, max(0.0, float(ctx.get("confirmed_rate") or 0)))
    if conf > 0:
        add("confirmed", round(w["confirmed"] * conf, 4),
            f"{int(conf * 100)}% of these were confirmed real here")

    value = round(min(1.0, max(0.0, value)), 3)
    return Score(value, severity_for(value, cfg), signals)
