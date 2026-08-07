"""Run the behaviour brain on a live (or replayed) detection stream.

`app/brain.py` judges one *window* of a tracked (person, vehicle) pair. This
module is the plumbing that turns the pipeline's frame-by-frame detections into
those windows: it keeps a rolling buffer of tracked boxes per camera, and every
couple of seconds it finds the pair that came closest, reduces their recent
history to the geometry features, and asks the brain.

It returns the brain's verdict *and* whether it has held up across several
overlapping windows (the same 2-of-3 temporal smoothing the pixel specialists
use, via `brain.make_confirmer()`), so a single noisy window never confirms.

Where the output goes is `app/main.py`: the verdict is folded into the fusion
Evidence the pipeline already builds, and **fusion stays the final gate**. The
brain therefore refines the free layer's candidates — suppressing the ones that
are ordinary, corroborating the ones that are not — but never raises an alert on
its own. That is the product's core rule, kept intact.

One scorer per camera (own buffer, own temporal history); they all share one
read-only `BehaviorBrain`. Pure-ish Python — imports no torch — so it unit-tests
against hand-built detections.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from .brain import NAME, BehaviorBrain, BrainVerdict
from training import features as F


@dataclass
class BrainReading:
    """What the scorer produced on an inference tick."""
    person_id: int
    vehicle_id: int
    features: dict
    verdict: BrainVerdict
    confirmed: bool


class LiveBrainScorer:
    """Accumulates detections for one camera and scores the hottest pair."""

    def __init__(self, brain: BehaviorBrain, camera_id: str,
                 cfg: dict | None = None):
        cfg = cfg or {}
        self.brain = brain
        self.camera_id = str(camera_id)
        # Behaviour needs a long memory: loitering is a 30-40s story, so the
        # window is far longer than the pixel specialists' 4s clip.
        self.window_s = float(cfg.get("window_s", 40.0))
        self.infer_every_s = float(cfg.get("infer_every_s", 2.0))
        self.min_span_s = float(cfg.get("min_span_s", 6.0))
        self.confirmer = brain.make_confirmer()
        self._frames: deque = deque()
        self._last_infer = float("-inf")

    def observe(self, detections, ts: float, *, registered_tids=frozenset(),
                night: bool = False, hour: float = 12.0) -> BrainReading | None:
        """Add one frame of detections; on a tick, score and return a reading.

        Returns None between ticks, before enough history exists, or when no
        person came near a vehicle — the common case, cheaply.
        """
        self._store(detections, ts)
        if ts - self._last_infer < self.infer_every_s:
            return None

        window = [f for f in self._frames if f.ts >= ts - self.window_s]
        if len(window) < 2 or (window[-1].ts - window[0].ts) < self.min_span_s:
            return None

        pairs = F.candidate_pairs(window)
        if not pairs:
            return None
        self._last_infer = ts

        # candidate_pairs is sorted closest-first; the nearest pair is the one
        # most worth a judgement.
        person_id, vehicle_id = pairs[0]
        pair_frames = F.window_for(window, person_id, vehicle_id)
        ctx = F.Context(night=bool(night), hour=float(hour),
                        vehicle_registered=vehicle_id in registered_tids)
        feats = F.pair_features(pair_frames, ctx)

        verdict = self.brain.score(feats)
        cr = self.confirmer.update(self.camera_id, NAME, verdict.score)
        return BrainReading(person_id=int(person_id), vehicle_id=int(vehicle_id),
                            features=feats, verdict=verdict,
                            confirmed=bool(cr.confirmed))

    def _store(self, detections, ts: float) -> None:
        people, vehicles = {}, {}
        for d in detections:
            box = F.Box(*d.xyxy, conf=float(getattr(d, "conf", 1.0)))
            if d.is_person:
                people[int(d.track_id)] = box
            elif d.is_vehicle:
                vehicles[int(d.track_id)] = box
        self._frames.append(F.MultiFrame(ts=float(ts), people=people,
                                         vehicles=vehicles))
        cutoff = ts - self.window_s - 1.0
        while self._frames and self._frames[0].ts < cutoff:
            self._frames.popleft()

    def reset(self) -> None:
        """Forget everything (camera disconnect / scene change / incident end)."""
        self._frames.clear()
        self._last_infer = float("-inf")
        self.confirmer.reset(self.camera_id)


def hour_and_night(ts: float, night_start: int = 19, night_end: int = 6) -> tuple:
    """Local hour and a simple night flag for the context vector.

    A crude but honest default: real deployments can override with the camera's
    own day/night reading. Kept here so the live wiring has one place to get it.
    """
    import datetime as _dt

    hour = _dt.datetime.fromtimestamp(ts).hour
    night = hour >= night_start or hour < night_end
    return float(hour), bool(night)
