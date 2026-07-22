"""Hybrid security monitor — faithful port of the ChatGPT LiveSecurityMonitor.

Adapts ``visionguard/live_security.py`` into the Claude codebase. Keeps the
exact clip contract (rolling ~4s buffer of 128x128 RGB frames, one inference
every ~2s, require ~3.6s of span before scoring), scores with the specialist
bank, and confirms per-``(camera_id, event_type)`` via the temporal confirmer
(2-of-3 >= 0.70 with a peak >= 0.85 strong gate).

Two deliberate adaptations of the original:

* **Timestamp-driven, not wall-clock.** The original called
  ``time.monotonic()`` internally; here every frame carries its own ``ts`` so
  the same monitor works for offline video analysis (video time) and is
  deterministic to unit-test.
* **Per-camera isolation.** One monitor + one confirmer key per camera; the
  original shared a single engine's temporal history across event types on one
  instance.

Everything is gated behind ``hybrid.specialist.enabled``; with it off (default)
``observe`` returns an empty observation and nothing changes. The torch call is
isolated inside the bank, so this module imports and unit-tests without torch.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .fusion import Evidence
from .specialist import FRAME_SIZE, SpecialistBank
from .temporal import ConfirmResult, TemporalConfirmer
from .trigger import (AT_NIGHT, AT_VEHICLE, BREAK_IN, DEPARTURE, DISTURBANCE,
                      LINGERING, NEAR_VEHICLE)

# free-layer reason -> fusion evidence group. Zone reasons ("person_in_*") are
# matched by prefix below, not listed here.
CONTEXT_REASONS = {NEAR_VEHICLE, LINGERING, AT_NIGHT}
ACTION_REASONS = {AT_VEHICLE, DISTURBANCE, BREAK_IN,
                  "pose_crouching", "pose_reaching", "pose_arm_swing"}


@dataclass
class SpecialistObservation:
    """What the specialist layer saw on one frame (only ``ran`` frames score)."""
    ran: bool = False                       # did an inference tick fire?
    scores: dict[str, float] = field(default_factory=dict)      # which -> prob
    confirmed: set[str] = field(default_factory=set)            # confirmed models
    states: dict[str, ConfirmResult] = field(default_factory=dict)  # for overlay


class HybridSecurityMonitor:
    """One per camera / per analyzed clip."""

    def __init__(self, hybrid_cfg: dict | None, camera_id: str, *,
                 bank: SpecialistBank | None = None):
        cfg = hybrid_cfg or {}
        spec = cfg.get("specialist", {})
        temp = cfg.get("temporal", {})
        self.camera_id = str(camera_id)
        self.enabled = bool(spec.get("enabled", False))
        self.clip_seconds = float(spec.get("clip_seconds", 4.0))
        self.infer_every_s = float(spec.get("infer_every_s", 2.0))
        self.min_span_s = float(spec.get("min_clip_span_s", 3.6))
        self.size = FRAME_SIZE
        self.bank = bank if bank is not None else SpecialistBank(spec)
        self.confirmer = TemporalConfirmer(
            history_size=int(temp.get("history_size", 3)),
            required_hits=int(temp.get("required_hits", 2)),
            base_threshold=float(temp.get("base_threshold", 0.70)),
            strong_gate=float(temp.get("strong_gate", 0.85)),
            require_strong=bool(temp.get("require_strong", True)))
        self._frames: deque = deque()        # (ts, rgb128)
        self._last_infer = float("-inf")
        self._warmed = False

    def warmup(self) -> dict[str, bool]:
        """Load weights once (so failures surface at startup). No-op if off."""
        if self.enabled and not self._warmed:
            self._warmed = True
            return self.bank.warmup()
        return {}

    def _store(self, frame_bgr, ts: float) -> None:
        import cv2
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        rgb = cv2.resize(rgb, (self.size, self.size),
                         interpolation=cv2.INTER_AREA)
        self._frames.append((float(ts), rgb))
        oldest = ts - self.clip_seconds - 1.0     # keep one clip + margin
        while self._frames and self._frames[0][0] < oldest:
            self._frames.popleft()

    def observe(self, frame_bgr, ts: float,
                route: dict | None = None) -> SpecialistObservation:
        """Add a frame; on inference ticks, score routed models and confirm.

        ``route`` optionally gates which models run this tick (STEP 4 model
        routing), e.g. ``{"vehicle": True, "break_in": False}``. Omitted =
        run both, matching the original live monitor."""
        if not self.enabled:
            return SpecialistObservation()
        self._store(frame_bgr, ts)
        if ts - self._last_infer < self.infer_every_s:
            return SpecialistObservation()

        start = ts - self.clip_seconds
        sel = [(t, f) for (t, f) in self._frames if t >= start]
        if len(sel) < 2 or (sel[-1][0] - sel[0][0]) < self.min_span_s:
            return SpecialistObservation()      # not enough history yet

        self._last_infer = ts
        rgb_frames = [f for _, f in sel]
        route = route or {"break_in": True, "vehicle": True}
        obs = SpecialistObservation(ran=True)
        for which in ("break_in", "vehicle"):
            if not route.get(which, True):
                continue
            score = self.bank.score(rgb_frames, which=which, bgr=False)
            if score is None:                   # model unavailable/disabled
                continue
            obs.scores[which] = score
            st = self.confirmer.update(self.camera_id, which, score)
            obs.states[which] = st
            if st.confirmed:
                obs.confirmed.add(which)
        return obs

    def reset(self) -> None:
        """Clear buffer + temporal history for this camera (disconnect / scene
        change / incident complete)."""
        self._frames.clear()
        self._last_infer = float("-inf")
        self.confirmer.reset(self.camera_id)


def route_from_reasons(reasons) -> dict:
    """Default STEP-4 model routing derived from free-layer reasons.

    * vehicle security: a person is near/at a vehicle.
    * house break-in: a person is in an entry/restricted zone (structure
      interaction). With no zones drawn this stays off — draw zones or override.

    Returns a ``{model: bool}`` dict; callers may override.
    """
    rset = set(reasons or ())
    near_vehicle = bool(rset & {NEAR_VEHICLE, AT_VEHICLE, DISTURBANCE})
    at_structure = any(r in rset for r in
                       ("person_in_entry", "person_in_restricted"))
    return {"vehicle": near_vehicle, "break_in": at_structure}


def build_evidence(camera_id: str, reasons, obs: SpecialistObservation, *,
                   relationship: bool, state_chain: str | None = None,
                   contradictions: set | None = None) -> Evidence:
    """Fold free-layer reasons + a specialist observation into a fusion
    :class:`Evidence` bundle. Zone reasons map to context by prefix."""
    rset = set(reasons or ())
    context = {r for r in rset
               if r in CONTEXT_REASONS or r.startswith("person_in_")}
    action = {r for r in rset if r in ACTION_REASONS}
    if state_chain is None and DEPARTURE in rset:
        state_chain = DEPARTURE
    return Evidence(
        camera_id=str(camera_id),
        context_signals=context,
        pose_motion_signals=action,
        specialist_confirmed=set(obs.confirmed),
        specialist_scores=dict(obs.scores),
        state_chain=state_chain,
        relationship=bool(relationship),
        contradictions=set(contradictions or ()))
