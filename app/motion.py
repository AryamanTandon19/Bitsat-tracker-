"""Vehicle motion-burst scoring — the pose-free smash detector.

A glass smash is a violent, localized pixel change ON the car while the car
itself is parked. We measure it with plain frame differencing inside each
vehicle box: cheap, works at any resolution, and does not depend on the pose
model seeing an arm. The trigger decides what a score means (threshold,
parked-gate, person-nearby gate).
"""
from __future__ import annotations

import cv2


class VehicleMotion:
    """Per-camera/per-clip frame-difference scorer for vehicle boxes."""

    def __init__(self):
        self._prev_gray = None

    def scores(self, frame, vehicle_dets) -> dict[int, float]:
        """Return {vehicle track_id: mean abs pixel change inside its box}.
        First frame returns {} (nothing to diff against)."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        prev, self._prev_gray = self._prev_gray, gray
        if prev is None or prev.shape != gray.shape:
            return {}
        out: dict[int, float] = {}
        h, w = gray.shape[:2]
        for d in vehicle_dets:
            x1, y1, x2, y2 = [int(v) for v in d.xyxy]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            if x2 - x1 < 4 or y2 - y1 < 4:
                continue
            diff = cv2.absdiff(gray[y1:y2, x1:x2], prev[y1:y2, x1:x2])
            out[d.track_id] = float(diff.mean())
        return out
