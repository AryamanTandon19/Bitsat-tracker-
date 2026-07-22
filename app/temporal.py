"""Per-camera temporal confirmation for specialist model scores.

The ChatGPT branch confirmed a suspicious model score only when it persisted:
at least 2 of the latest 3 overlapping 4-second inference windows scored
>= 0.70, plus (after live domain-shift false positives around 0.72-0.78) a
"strong evidence" gate requiring a recent peak >= 0.85.

Its one real architectural bug: histories were keyed by event_type on a single
engine instance, so two cameras sharing an engine could combine windows. This
module keys every history by ``(camera_id, event_type)`` so cameras and event
types are fully isolated, and exposes an explicit reset lifecycle
(disconnect / scene change / incident complete).

Pure Python — no torch, no numpy — so it is fully unit-testable. The thresholds
here are DEVELOPMENT calibration values, not production constants.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass
class ConfirmResult:
    """Everything the fusion layer / debug overlay needs to explain a decision."""
    camera_id: str
    event_type: str
    score: float                 # the score just submitted
    history: list[float]         # recent scores, oldest -> newest
    suspicious_votes: int        # how many history entries >= base_threshold
    required_hits: int
    history_size: int
    peak: float                  # max score in history
    base_threshold: float
    strong_gate: float
    temporal_ok: bool            # >= required_hits of the last history_size
    strong_ok: bool              # peak >= strong_gate
    confirmed: bool              # temporal_ok AND strong_ok
    reason: str                  # human-readable one-liner


class TemporalConfirmer:
    """Sliding-window vote confirmation, isolated per (camera_id, event_type).

    Parameters mirror the ChatGPT event engine's development settings::

        history_size   = 3      # latest N overlapping inference windows
        required_hits  = 2      # this many must be "suspicious"
        base_threshold = 0.70   # score >= this counts as a suspicious vote
        strong_gate    = 0.85   # recent peak must reach this to CONFIRM
        require_strong = True   # enforce the strong-evidence gate

    ``require_strong`` exists because 0.85 was a live safeguard, not a validated
    production constant. Turn it off only with a calibrated holdout set.
    """

    def __init__(self, history_size: int = 3, required_hits: int = 2,
                 base_threshold: float = 0.70, strong_gate: float = 0.85,
                 require_strong: bool = True):
        if required_hits > history_size:
            raise ValueError("required_hits cannot exceed history_size")
        self.history_size = int(history_size)
        self.required_hits = int(required_hits)
        self.base_threshold = float(base_threshold)
        self.strong_gate = float(strong_gate)
        self.require_strong = bool(require_strong)
        # (camera_id, event_type) -> bounded deque of recent scores
        self._hist: dict[tuple[str, str], deque] = {}

    def _key(self, camera_id: str, event_type: str) -> tuple[str, str]:
        return (str(camera_id), str(event_type))

    def update(self, camera_id: str, event_type: str,
               score: float) -> ConfirmResult:
        """Record a new score for this (camera, event type) and evaluate.

        A confirmation only becomes possible once the history is full
        (history_size entries) — this prevents a single high frame from
        confirming on its own."""
        key = self._key(camera_id, event_type)
        dq = self._hist.get(key)
        if dq is None:
            dq = deque(maxlen=self.history_size)
            self._hist[key] = dq
        dq.append(float(score))

        history = list(dq)
        votes = sum(1 for s in history if s >= self.base_threshold)
        peak = max(history) if history else 0.0
        full = len(history) == self.history_size
        temporal_ok = full and votes >= self.required_hits
        strong_ok = peak >= self.strong_gate
        confirmed = temporal_ok and (strong_ok or not self.require_strong)

        return ConfirmResult(
            camera_id=str(camera_id), event_type=str(event_type),
            score=float(score), history=history,
            suspicious_votes=votes, required_hits=self.required_hits,
            history_size=self.history_size, peak=peak,
            base_threshold=self.base_threshold, strong_gate=self.strong_gate,
            temporal_ok=temporal_ok, strong_ok=strong_ok, confirmed=confirmed,
            reason=self._describe(full, votes, temporal_ok, strong_ok,
                                  confirmed))
    def _describe(self, full, votes, temporal_ok, strong_ok, confirmed) -> str:
        if confirmed:
            return (f"CONFIRMED: {votes}/{self.history_size} windows "
                    f">= {self.base_threshold:g} and peak >= "
                    f"{self.strong_gate:g}")
        if not full:
            return "warming up: history not yet full"
        if not temporal_ok:
            return (f"only {votes}/{self.history_size} suspicious windows "
                    f"(need {self.required_hits})")
        if self.require_strong and not strong_ok:
            return (f"temporal met ({votes}/{self.history_size}) but peak "
                    f"below strong gate {self.strong_gate:g}")
        return "not confirmed"

    def snapshot(self, camera_id: str, event_type: str) -> list[float]:
        """Read current history without submitting a score (for overlays)."""
        dq = self._hist.get(self._key(camera_id, event_type))
        return list(dq) if dq else []

    def reset(self, camera_id: str | None = None,
              event_type: str | None = None) -> None:
        """Clear histories. Scope by argument:

        * ``reset()`` — everything (e.g. full restart).
        * ``reset(camera_id=X)`` — every event type for camera X
          (camera disconnect / scene reset).
        * ``reset(camera_id=X, event_type=Y)`` — just that pair
          (incident complete / re-arm)."""
        if camera_id is None:
            self._hist.clear()
            return
        cam = str(camera_id)
        if event_type is None:
            for key in [k for k in self._hist if k[0] == cam]:
                self._hist.pop(key, None)
        else:
            self._hist.pop(self._key(cam, event_type), None)
