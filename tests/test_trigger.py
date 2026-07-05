"""Candidate-trigger logic + windowing — pure Python, synthetic detections."""
from dataclasses import dataclass
from datetime import datetime

from app.trigger import (AT_NIGHT, LINGERING, NEAR_VEHICLE, CandidateTrigger,
                         merge_windows, windows_overlap)


@dataclass
class Det:
    track_id: int
    cls_name: str
    xyxy: tuple

    @property
    def foot_point(self):
        x1, y1, x2, y2 = self.xyxy
        return ((x1 + x2) / 2, y2)


CFG = {"on_near_vehicle": True, "on_loiter": True, "on_zone": True,
       "on_night_person": True, "near_vehicle_px": 180, "dwell_s": 8,
       "night_hours": {"start": "23:00", "end": "05:00"}}
DAY = lambda: datetime(2026, 7, 4, 14, 0)
NIGHT = lambda: datetime(2026, 7, 4, 23, 30)


def trig(zones=None, localtime=DAY, cfg=None):
    return CandidateTrigger(zones or {}, cfg or CFG, localtime_fn=localtime)


def test_person_near_vehicle_fires():
    t = trig()
    car = Det(1, "car", (100, 100, 200, 160))
    person = Det(7, "person", (205, 120, 225, 180))     # just right of the car
    fire, reasons = t.is_candidate([car, person], ts=1.0)
    assert fire and NEAR_VEHICLE in reasons


def test_person_far_from_vehicle_quiet_in_daytime():
    t = trig()
    car = Det(1, "car", (100, 100, 200, 160))
    person = Det(7, "person", (600, 300, 620, 360))      # far away
    fire, reasons = t.is_candidate([car, person], ts=1.0)
    assert not fire


def test_lingering_fires_after_dwell():
    t = trig()
    person = Det(7, "person", (600, 300, 620, 360))      # no vehicle nearby
    fire0, _ = t.is_candidate([person], ts=0.0)
    assert not fire0                                      # just arrived
    fire1, reasons = t.is_candidate([person], ts=9.0)     # 9s >= dwell 8s
    assert fire1 and LINGERING in reasons


def test_night_person_always_fires():
    t = trig(localtime=NIGHT)
    person = Det(7, "person", (600, 300, 620, 360))
    fire, reasons = t.is_candidate([person], ts=1.0)
    assert fire and AT_NIGHT in reasons


def test_zone_person_fires():
    zones = {"restricted": [[0, 0], [100, 0], [100, 100], [0, 100]]}
    t = trig(zones=zones)
    person = Det(7, "person", (40, 40, 60, 90))          # foot point inside zone
    fire, reasons = t.is_candidate([person], ts=1.0)
    assert fire and "person_in_restricted" in reasons


def test_no_person_never_fires():
    t = trig(localtime=NIGHT)
    car = Det(1, "car", (100, 100, 200, 160))
    assert t.is_candidate([car], ts=1.0) == (False, [])
    assert t.is_candidate([], ts=1.0) == (False, [])


def test_toggles_disable_signals():
    cfg = {**CFG, "on_night_person": False, "on_loiter": False}
    t = trig(localtime=NIGHT, cfg=cfg)
    person = Det(7, "person", (600, 300, 620, 360))
    # night + lingering both off, no vehicle, no zone -> quiet
    assert not t.is_candidate([person], ts=20.0)[0]


def test_merge_windows():
    assert merge_windows([]) == []
    # points 1,2,3 merge (gap<=3); 20 separate
    w = merge_windows([1.0, 2.0, 3.0, 20.0], gap_s=3.0, pad_s=0.0)
    assert w == [(1.0, 3.0), (20.0, 20.0)]
    # padding clamps at 0
    assert merge_windows([1.0], pad_s=2.0) == [(0.0, 3.0)]


def test_windows_overlap():
    assert windows_overlap((0, 10), (5, 15))
    assert windows_overlap((0, 10), (10, 20))       # touching counts
    assert not windows_overlap((0, 10), (11, 20))
