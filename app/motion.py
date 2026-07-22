"""Vehicle motion-burst scoring — the pose-free smash / break-in detector.

A break-in is a small, FORCEFUL, LOCALIZED movement on a parked car (a strike,
a pry, a reach through a window). On low-resolution CCTV the whole-car average
frame-difference washes that out — measured on real 452x342 night footage, the
whole-box mean during a real reach-in was only ~5-20 (below any usable
threshold), while the LOCALIZED PEAK density in the same region spiked to
90-160 and fell back to ~15 the moment the person left.

So we score the *peak local density* of frame-differencing inside the vehicle
box (a small patch mean, then its maximum) instead of the box average. When a
person overlaps the vehicle we focus on that contact region — where a break-in
actually happens — which also rejects motion elsewhere on the car (taillights,
reflections). Cheap, pure OpenCV, resolution-robust. The trigger applies the
threshold, the parked-gate and the person-gate.
"""
from __future__ import annotations

import cv2


class VehicleMotion:
    """Per-camera/per-clip localized frame-difference scorer for vehicle boxes."""

    def __init__(self):
        self._prev_gray = None

    @staticmethod
    def _peak_local(diff) -> float:
        """Strongest localized motion density in a diff patch: average the diff
        over a small kxk window (kills single-pixel noise) and take the max."""
        h, w = diff.shape[:2]
        if h < 2 or w < 2:
            return 0.0
        k = max(3, min(h, w) // 6)
        return float(cv2.blur(diff, (k, k)).max())

    def scores(self, frame, vehicle_dets, persons=None) -> dict[int, float]:
        """Return {vehicle track_id: localized motion-burst score}.

        `persons` (optional): when a person overlaps a vehicle, the score is
        measured in the person<->vehicle contact region (the window/door where a
        break-in happens) instead of the whole car. First frame returns {}."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        prev, self._prev_gray = self._prev_gray, gray
        if prev is None or prev.shape != gray.shape:
            return {}
        H, W = gray.shape[:2]
        persons = persons or []
        out: dict[int, float] = {}
        for d in vehicle_dets:
            vx1, vy1, vx2, vy2 = [int(v) for v in d.xyxy]
            vx1, vy1 = max(0, vx1), max(0, vy1)
            vx2, vy2 = min(W, vx2), min(H, vy2)
            if vx2 - vx1 < 4 or vy2 - vy1 < 4:
                continue
            # focus on the largest person<->vehicle overlap (the contact region)
            rx1, ry1, rx2, ry2 = vx1, vy1, vx2, vy2
            best_area = 0
            for p in persons:
                px1, py1, px2, py2 = [int(v) for v in p.xyxy]
                ix1, iy1 = max(vx1, px1), max(vy1, py1)
                ix2, iy2 = min(vx2, px2), min(vy2, py2)
                area = (ix2 - ix1) * (iy2 - iy1)
                if ix2 - ix1 > 2 and iy2 - iy1 > 2 and area > best_area:
                    best_area = area
                    rx1, ry1, rx2, ry2 = ix1, iy1, ix2, iy2
            diff = cv2.absdiff(gray[ry1:ry2, rx1:rx2], prev[ry1:ry2, rx1:rx2])
            out[d.track_id] = self._peak_local(diff)
        return out
