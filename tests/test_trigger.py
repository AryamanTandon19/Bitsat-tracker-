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


def test_last_involved_tracks_the_right_person():
    t = trig()
    car = Det(1, "car", (100, 100, 200, 160))
    near = Det(7, "person", (205, 120, 225, 180))    # beside the car
    far = Det(8, "person", (600, 300, 620, 360))     # far away, just arrived
    fire, _ = t.is_candidate([car, near, far], ts=1.0)
    assert fire
    assert t.last_involved == {7}                    # only the person near the car


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


# ------------------------------------------------------- theft chain (A6)
CHAIN_CFG = {**CFG, "on_touch": True, "on_departure": True,
             "touch_arm_s": 3, "parked_min_s": 6, "depart_frac": 0.6,
             "link_s": 600}


def car_at(x, tid=1):
    return Det(tid, "car", (x, 100, x + 100, 160))


def test_theft_chain_smash_then_drive_fires():
    """Thief lingers by a parked car, then the car drives away -> DEPARTURE."""
    from app.trigger import DEPARTURE
    t = trig(cfg=CHAIN_CFG)
    thief = Det(7, "person", (205, 120, 225, 180))       # beside the car
    # thief lingers next to the parked car for 10s (dwell 8s -> suspicious)
    for ts in range(0, 11):
        t.is_candidate([car_at(100), thief], ts=float(ts))
    # thief leaves, then the car drives away (moved > 0.6 * width)
    fire, reasons = t.is_candidate([car_at(300)], ts=15.0)
    assert fire and DEPARTURE in reasons
    assert t.last_departure["vehicle"] == 1
    assert 7 in t.last_departure["people"]


def test_plain_departure_is_silent():
    """A parked car driving away with NO prior activity = owner, no alert."""
    t = trig(cfg=CHAIN_CFG)
    for ts in range(0, 11):
        t.is_candidate([car_at(100)], ts=float(ts))      # parked, nobody near
    fire, reasons = t.is_candidate([car_at(300)], ts=15.0)
    assert not fire


def test_owner_quick_pickup_is_silent_chain():
    """Owner walks up, brief touch (<touch_arm_s), drives off: near_vehicle
    fires (wide net) but the DEPARTURE chain must NOT fire."""
    from app.trigger import DEPARTURE
    t = trig(cfg=CHAIN_CFG)
    for ts in range(0, 9):
        t.is_candidate([car_at(100)], ts=float(ts))      # car parked alone
    owner = Det(9, "person", (150, 110, 175, 170))       # overlaps the car
    t.is_candidate([car_at(100), owner], ts=9.0)         # 1-frame touch only
    fire, reasons = t.is_candidate([car_at(300)], ts=12.0)
    assert DEPARTURE not in reasons


def test_sustained_touch_arms_the_chain():
    """Reaching into the car for > touch_arm_s = suspicious -> chain armed."""
    from app.trigger import AT_VEHICLE, DEPARTURE
    t = trig(cfg=CHAIN_CFG)
    thief = Det(7, "person", (150, 110, 175, 170))       # overlapping the car
    for ts in range(0, 7):                               # parked 6s w/ touching
        fire, reasons = t.is_candidate([car_at(100), thief], ts=float(ts))
    assert AT_VEHICLE in reasons                         # wide net saw the touch
    fire, reasons = t.is_candidate([car_at(300)], ts=10.0)
    assert fire and DEPARTURE in reasons


def test_moving_car_never_counts_as_parked():
    """A car just driving through never 'departs' (it was never parked)."""
    from app.trigger import DEPARTURE
    t = trig(cfg=CHAIN_CFG, localtime=NIGHT)             # night arms everything
    walker = Det(7, "person", (205, 120, 225, 180))
    for i, x in enumerate(range(100, 800, 70)):          # moves every frame
        fire, reasons = t.is_candidate([car_at(x), walker], ts=float(i))
        assert DEPARTURE not in reasons


# --------------------------------------------- instant break-in escalation
def test_arm_swing_at_car_fires_break_in_instantly():
    """The smash moment itself: arm strike beside a car -> BREAK_IN on that
    very frame, no waiting for dwell or departure."""
    from app.trigger import BREAK_IN
    t = trig(cfg=CHAIN_CFG)
    thief = Det(7, "person", (205, 120, 225, 180))       # beside the car
    fire, reasons = t.is_candidate([car_at(100), thief], ts=0.0,
                                   pose_signals={7: {"pose_arm_swing"}})
    assert fire and BREAK_IN in reasons                  # first frame!
    assert 7 in t.last_involved


def test_sustained_reach_fires_break_in():
    from app.trigger import BREAK_IN
    t = trig(cfg=CHAIN_CFG)
    thief = Det(7, "person", (150, 110, 175, 170))       # overlapping the car
    fire, reasons = t.is_candidate([car_at(100), thief], ts=0.0)
    assert BREAK_IN not in reasons                       # touch just started
    fire, reasons = t.is_candidate([car_at(100), thief], ts=4.0)  # > 3s touch
    assert fire and BREAK_IN in reasons


def test_owner_brief_touch_no_break_in():
    from app.trigger import BREAK_IN
    t = trig(cfg=CHAIN_CFG)
    owner = Det(9, "person", (150, 110, 175, 170))
    fire, reasons = t.is_candidate([car_at(100), owner], ts=0.0)
    assert BREAK_IN not in reasons
    # owner walks around (not touching), comes back — timer must have reset
    away = Det(9, "person", (500, 110, 525, 170))
    t.is_candidate([car_at(100), away], ts=2.0)
    fire, reasons = t.is_candidate([car_at(100), owner], ts=4.0)
    assert BREAK_IN not in reasons                       # fresh touch again


def test_crouch_while_touching_fires_break_in():
    from app.trigger import BREAK_IN
    t = trig(cfg=CHAIN_CFG)
    thief = Det(7, "person", (150, 110, 175, 170))       # overlapping
    fire, reasons = t.is_candidate([car_at(100), thief], ts=0.0,
                                   pose_signals={7: {"pose_crouching"}})
    assert fire and BREAK_IN in reasons


def test_swing_far_from_any_car_is_not_break_in():
    """Someone exercising/waving far from vehicles: pose fires (wide net)
    but no BREAK_IN escalation."""
    from app.trigger import BREAK_IN
    t = trig(cfg=CHAIN_CFG)
    person = Det(7, "person", (600, 300, 620, 360))      # far from the car
    fire, reasons = t.is_candidate([car_at(100), person], ts=0.0,
                                   pose_signals={7: {"pose_arm_swing"}})
    assert BREAK_IN not in reasons


# ------------------------------------------ pose-free smash (disturbance)
DIST_CFG = {**CHAIN_CFG, "on_disturb": True, "disturb_thresh": 16,
            "disturb_frames": 2}


def _park(t, frames=5):
    """Let car #1 sit parked for a few seconds first."""
    for ts in range(frames):
        t.is_candidate([car_at(100)], ts=float(ts))


def test_pixel_burst_on_parked_car_fires_break_in():
    """The smash without any pose model: violent frame-diff on a parked car
    with a person beside it -> DISTURBANCE, then BREAK_IN on the 2nd frame."""
    from app.trigger import BREAK_IN, DISTURBANCE
    t = trig(cfg=DIST_CFG)
    _park(t)
    thief = Det(7, "person", (205, 120, 225, 180))
    fire, reasons = t.is_candidate([car_at(100), thief], ts=5.0,
                                   motion={1: 40.0})
    assert fire and DISTURBANCE in reasons and BREAK_IN not in reasons
    fire, reasons = t.is_candidate([car_at(100), thief], ts=5.2,
                                   motion={1: 35.0})
    assert BREAK_IN in reasons                        # sustained burst
    assert 7 in t.last_involved


def test_burst_without_person_nearby_is_silent():
    from app.trigger import DISTURBANCE
    t = trig(cfg=DIST_CFG)
    _park(t)
    far = Det(7, "person", (600, 300, 620, 360))
    fire, reasons = t.is_candidate([car_at(100), far], ts=5.0,
                                   motion={1: 40.0})
    assert DISTURBANCE not in reasons                 # tree shadow, not smash


def test_burst_on_moving_car_is_silent():
    """A moving car always has high frame-diff — must not count."""
    from app.trigger import DISTURBANCE
    t = trig(cfg=DIST_CFG)
    walker = Det(7, "person", (205, 120, 225, 180))
    for i, x in enumerate(range(100, 500, 80)):       # car driving through
        fire, reasons = t.is_candidate([car_at(x), walker], ts=float(i),
                                       motion={1: 60.0})
        assert DISTURBANCE not in reasons


def test_small_motion_below_threshold_is_silent():
    from app.trigger import DISTURBANCE
    t = trig(cfg=DIST_CFG)
    _park(t)
    person = Det(7, "person", (205, 120, 225, 180))
    fire, reasons = t.is_candidate([car_at(100), person], ts=5.0,
                                   motion={1: 8.0})   # gentle reflection etc.
    assert DISTURBANCE not in reasons


def test_disturbance_arms_theft_chain():
    """Burst on parked car, thief walks away, car leaves -> DEPARTURE."""
    from app.trigger import DEPARTURE
    t = trig(cfg=DIST_CFG)
    _park(t)
    thief = Det(7, "person", (205, 120, 225, 180))
    t.is_candidate([car_at(100), thief], ts=5.0, motion={1: 40.0})
    t.is_candidate([car_at(100)], ts=8.0)             # thief gone
    fire, reasons = t.is_candidate([car_at(300)], ts=12.0)
    assert fire and DEPARTURE in reasons
