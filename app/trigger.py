"""The candidate trigger — the cheap "is this worth a closer look?" gate.

This is deliberately a WIDE NET, not a smart judge: its only job is to decide,
using free YOLO output, whether a moment might be worth forwarding to the
(paid) Claude review. It errs toward firing — a false trigger costs a rupee,
a missed incident costs everything. Claude does the precision filtering.

Pure Python + duck-typed detections (see detector.Detection), so it is fully
unit-testable and its sensitivity can be validated/tuned with real footage via
validate_triggers.py.
"""
from __future__ import annotations

from datetime import datetime

from .rules import iou, is_night, point_in_polygon

# signal names (also used as human-readable reasons)
NEAR_VEHICLE = "person_near_vehicle"
LINGERING = "person_lingering"
AT_NIGHT = "person_at_night"


class CandidateTrigger:
    """One instance per camera / per analyzed clip."""

    def __init__(self, zones: dict, cfg: dict, now_fn=None,
                 localtime_fn=datetime.now):
        self.zones = {k: (v or []) for k, v in (zones or {}).items()}
        self.cfg = cfg or {}
        self.localtime_fn = localtime_fn
        self._person_since: dict[int, float] = {}   # track_id -> first-seen ts
        self._last_seen: dict[int, float] = {}

    def _night(self) -> bool:
        nh = self.cfg.get("night_hours", {"start": "23:00", "end": "05:00"})
        return is_night(self.localtime_fn(), nh.get("start", "23:00"),
                        nh.get("end", "05:00"))

    def is_candidate(self, detections, ts: float) -> tuple[bool, list[str]]:
        """Return (fire?, reasons) for this frame."""
        persons = [d for d in detections if d.cls_name == "person"]
        vehicles = [d for d in detections if d.cls_name != "person"]
        reasons: set[str] = set()

        # maintain per-person dwell timers (expire after a short absence)
        seen = {p.track_id for p in persons}
        for tid in list(self._person_since):
            if tid not in seen and ts - self._last_seen.get(tid, ts) > 3.0:
                self._person_since.pop(tid, None)
                self._last_seen.pop(tid, None)
        for p in persons:
            self._person_since.setdefault(p.track_id, ts)
            self._last_seen[p.track_id] = ts

        near_r = float(self.cfg.get("near_vehicle_px", 180))
        dwell_s = float(self.cfg.get("dwell_s", 8))
        night = self._night()

        for p in persons:
            if self.cfg.get("on_near_vehicle", True):
                for v in vehicles:
                    expanded = (v.xyxy[0] - near_r, v.xyxy[1] - near_r,
                                v.xyxy[2] + near_r, v.xyxy[3] + near_r)
                    if iou(p.xyxy, expanded) > 0:
                        reasons.add(NEAR_VEHICLE)
                        break
            if self.cfg.get("on_loiter", True) and \
                    ts - self._person_since[p.track_id] >= dwell_s:
                reasons.add(LINGERING)
            if self.cfg.get("on_zone", True):
                for zname in ("restricted", "parking", "entry"):
                    if point_in_polygon(p.foot_point, self.zones.get(zname)):
                        reasons.add(f"person_in_{zname}")
            if night and self.cfg.get("on_night_person", True):
                reasons.add(AT_NIGHT)

        return (bool(reasons), sorted(reasons))


def merge_windows(candidate_times: list[float], gap_s: float = 3.0,
                  pad_s: float = 2.0) -> list[tuple[float, float]]:
    """Collapse a list of candidate timestamps into [start, end] windows,
    joining points separated by <= gap_s and padding each end by pad_s."""
    if not candidate_times:
        return []
    ts = sorted(candidate_times)
    windows = []
    start = prev = ts[0]
    for t in ts[1:]:
        if t - prev <= gap_s:
            prev = t
        else:
            windows.append((max(0.0, start - pad_s), prev + pad_s))
            start = prev = t
    windows.append((max(0.0, start - pad_s), prev + pad_s))
    return windows


def windows_overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return not (a[1] < b[0] or a[0] > b[1])
