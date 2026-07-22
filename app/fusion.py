"""Explainable evidence fusion — the hybrid's final decision gate.

The whole product principle: *no single heuristic and no single model score may
declare a critical incident by itself.* This layer collects independent groups
of evidence and fuses them into one of four review states, exposing exactly
which reasons were accepted and which were rejected so every decision is
auditable.

States (increasing severity)::

    NORMAL             nothing worth surfacing
    WATCH              some evidence; keep monitoring, no alert
    AI_REVIEW          enough to spend a Claude / VLM review on
    CONFIRMED_INCIDENT corroborated across independent evidence -> human alert

Evidence groups (from the handoff, STEP 8):

    1. context        proximity / zone / dwell / time-of-day       (weak alone)
    2. pose_motion    crouch / reach / arm-swing / disturbance      (action)
    3. specialist     temporally-confirmed learned model score      (learned)
    4. state_chain    remembered sequence, e.g. tamper -> departure (sequence)
    5. relationship   person<->relevant object interaction present  (grounding)
    6. contradictory  evidence AGAINST (recognized owner, routine)  (downgrade)

Key guards this encodes:

* A confirmed specialist score with NO interaction/action/chain caps at WATCH —
  this is exactly the live domain-shift case (a stationary person in front of an
  unrelated camera scored ~0.75).
* Heuristics/action without a confirmed model reach AI_REVIEW at most, never
  CONFIRMED on their own.
* CONFIRMED requires a learned OR sequence backbone PLUS at least one
  independent corroborating group, and no strong contradiction.

Pure Python — fully unit-testable, no torch/numpy.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# decision states, ordered by severity so we can cap/compare
NORMAL = "NORMAL"
WATCH = "WATCH"
AI_REVIEW = "AI_REVIEW"
CONFIRMED_INCIDENT = "CONFIRMED_INCIDENT"
_ORDER = {NORMAL: 0, WATCH: 1, AI_REVIEW: 2, CONFIRMED_INCIDENT: 3}


@dataclass
class Evidence:
    """One frame/window of fused evidence for a single (camera, incident).

    All fields default empty so callers set only what they observed."""
    camera_id: str = ""
    # group 1: weak context signals (free-layer reasons like person_near_vehicle)
    context_signals: set[str] = field(default_factory=set)
    # group 2: pose / motion action signals (crouch, reach, arm_swing, disturbance)
    pose_motion_signals: set[str] = field(default_factory=set)
    # group 3: specialist models that TEMPORALLY CONFIRMED this window
    specialist_confirmed: set[str] = field(default_factory=set)
    specialist_scores: dict[str, float] = field(default_factory=dict)
    # group 4: remembered sequence (e.g. "vehicle_departure_after_activity")
    state_chain: str | None = None
    # group 5: is the actor actually interacting with the relevant object?
    relationship: bool = False
    # group 6: evidence AGAINST — strong ones veto, soft ones cap at AI_REVIEW
    contradictions: set[str] = field(default_factory=set)


# contradictions strong enough to veto a critical alert down to WATCH
STRONG_CONTRADICTIONS = {
    "recognized_owner", "authorized_vehicle", "registered_plate",
    "authorized_access",
}


@dataclass
class FusionResult:
    decision: str
    confidence: float                 # 0..1, uncalibrated (see note)
    accepted: list[str]               # evidence that supported the decision
    rejected: list[str]               # evidence considered but not sufficient
    evidence: Evidence
    calibration_note: str = (
        "development thresholds; not production-calibrated — verify on an "
        "untouched multi-camera holdout before publishing accuracy")

    @property
    def is_alert(self) -> bool:
        """CONFIRMED_INCIDENT is the only state that pages a human."""
        return self.decision == CONFIRMED_INCIDENT


def fuse(ev: Evidence) -> FusionResult:
    """Fuse an :class:`Evidence` bundle into a :class:`FusionResult`."""
    accepted: list[str] = []
    rejected: list[str] = []

    specialist = sorted(ev.specialist_confirmed)
    action = bool(ev.pose_motion_signals)
    chain = ev.state_chain is not None
    context = bool(ev.context_signals)
    interaction = bool(ev.relationship)
    strong_contra = ev.contradictions & STRONG_CONTRADICTIONS
    soft_contra = ev.contradictions - STRONG_CONTRADICTIONS

    # independent corroborating groups (weak context excluded on purpose)
    corroborators: list[str] = []
    if interaction:
        corroborators.append("person<->object interaction")
    if action:
        corroborators.append("pose/motion: " + ", ".join(
            sorted(ev.pose_motion_signals)))
    if chain:
        corroborators.append(f"state chain: {ev.state_chain}")

    # ── base decision ────────────────────────────────────────────────────
    if specialist and (interaction or action or chain):
        decision = CONFIRMED_INCIDENT
        accepted.append("specialist confirmed: " + ", ".join(specialist))
        accepted.extend(corroborators)
    elif chain and (interaction or action):
        # a real remembered sequence corroborated by real interaction/action —
        # crime is a sequence, not one 4s clip. Confirm even without a model.
        decision = CONFIRMED_INCIDENT
        accepted.append(f"state chain: {ev.state_chain}")
        accepted.extend(c for c in corroborators if not c.startswith("state"))
    elif specialist and not (interaction or action or chain):
        # domain-shift guard: model fired but nothing grounds it to the scene
        decision = WATCH
        rejected.append(
            "specialist confirmed (" + ", ".join(specialist) + ") but no "
            "person<->object interaction / action / sequence — WATCH only "
            "(domain-shift guard)")
    elif (action and interaction) or chain:
        # grounded action, or a bare sequence, but no learned confirmation —
        # worth a paid Claude/VLM look, not an autonomous critical alert
        decision = AI_REVIEW
        accepted.extend(corroborators)
        if specialist_scores_present(ev):
            rejected.append("specialist scored but did not temporally confirm")
    elif action or context or specialist_scores_present(ev):
        decision = WATCH
        if action:
            accepted.append("pose/motion: " + ", ".join(
                sorted(ev.pose_motion_signals)))
        if context:
            accepted.append("context: " + ", ".join(
                sorted(ev.context_signals)))
        if specialist_scores_present(ev) and not specialist:
            rejected.append("specialist below confirmation threshold")
    else:
        decision = NORMAL

    # ── contradiction handling (downgrade only) ──────────────────────────
    if strong_contra and _ORDER[decision] > _ORDER[WATCH]:
        rejected.append("strong contradiction caps at WATCH: " + ", ".join(
            sorted(strong_contra)))
        decision = WATCH
    elif soft_contra and _ORDER[decision] > _ORDER[AI_REVIEW]:
        rejected.append("soft contradiction caps at AI_REVIEW: " + ", ".join(
            sorted(soft_contra)))
        decision = AI_REVIEW

    return FusionResult(
        decision=decision,
        confidence=_confidence(decision, ev, len(corroborators)),
        accepted=accepted, rejected=rejected, evidence=ev)


def specialist_scores_present(ev: Evidence) -> bool:
    """True if any specialist produced a score this window (confirmed or not)."""
    return bool(ev.specialist_scores)


def _confidence(decision: str, ev: Evidence, n_corroborators: int) -> float:
    """A transparent, deliberately conservative confidence heuristic. This is
    NOT a calibrated probability — it just scales with how much independent
    evidence agrees, so the dashboard can sort/rank incidents."""
    if decision == NORMAL:
        return 0.0
    base = {WATCH: 0.35, AI_REVIEW: 0.6, CONFIRMED_INCIDENT: 0.8}[decision]
    peak = max(ev.specialist_scores.values(), default=0.0)
    bonus = 0.05 * n_corroborators + 0.1 * peak
    return round(min(0.99, base + bonus), 3)
