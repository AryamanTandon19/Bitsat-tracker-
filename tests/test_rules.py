"""Rules engine tests — pure Python, synthetic tracks, controlled clock."""
from dataclasses import dataclass
from datetime import datetime

from app.rules import (CAMERA_OFFLINE, CAMERA_TAMPER, LOITERING,
                       RESTRICTED_ZONE, UNAUTHORIZED_VEHICLE,
                       UNIDENTIFIED_VEHICLE, VEHICLE_CONTACT, RulesEngine,
                       iou, is_night, point_in_polygon)


@dataclass
class Det:
    track_id: int
    cls_name: str
    xyxy: tuple
    conf: float = 0.9

    @property
    def foot_point(self):
        x1, y1, x2, y2 = self.xyxy
        return ((x1 + x2) / 2, y2)


SQUARE = [[0, 0], [100, 0], [100, 100], [0, 100]]  # 100x100 zone at origin

CFG = {
    "night_hours": {"start": "23:00", "end": "05:00"},
    "debounce_s": 120,
    "flag_seconds": 60,
    "unauthorized_vehicle": {"enabled": True, "plate_read_timeout_s": 5},
    "loitering": {"enabled": True, "dwell_s": 45, "night_dwell_s": 20,
                  "max_displacement_px": 120, "near_vehicle_px": 150},
    "vehicle_contact": {"enabled": True, "iou_threshold": 0.02,
                        "depart_window_s": 15, "depart_speed_px_s": 60},
    "restricted_zone": {"enabled": True},
    "tamper": {"enabled": True, "dark_threshold": 12, "bright_threshold": 243,
               "blur_threshold": 12.0, "condition_hold_s": 5,
               "offline_alert_s": 30},
}

DAY = lambda: datetime(2026, 7, 4, 14, 0)     # 2 pm
NIGHT = lambda: datetime(2026, 7, 4, 23, 30)  # 11:30 pm


def engine(zones=None, localtime=DAY):
    return RulesEngine("gate", zones or {}, CFG, localtime_fn=localtime)


# ---------------------------------------------------------------- geometry
def test_point_in_polygon():
    assert point_in_polygon((50, 50), SQUARE)
    assert not point_in_polygon((150, 50), SQUARE)
    assert not point_in_polygon((50, 50), [])       # no zone drawn
    assert not point_in_polygon((50, 50), [[0, 0], [1, 1]])  # degenerate


def test_iou():
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
    assert 0 < iou((0, 0, 10, 10), (5, 0, 15, 10)) < 1


def test_is_night_wraps_midnight():
    assert is_night(datetime(2026, 1, 1, 23, 30), "23:00", "05:00")
    assert is_night(datetime(2026, 1, 1, 2, 0), "23:00", "05:00")
    assert not is_night(datetime(2026, 1, 1, 12, 0), "23:00", "05:00")
    assert not is_night(datetime(2026, 1, 1, 22, 59), "23:00", "05:00")


# --------------------------------------------------------------------- A1
def test_a1_unauthorized_vehicle():
    e = engine(zones={"entry": SQUARE})
    car = Det(1, "car", (30, 30, 70, 70))
    evs = e.update([car], ts=1000.0,
                   plate_info={1: {"plate": "MH12CD4567", "registered": False}})
    assert [ev.event_type for ev in evs] == [UNAUTHORIZED_VEHICLE]
    assert evs[0].plate == "MH12CD4567"
    assert evs[0].severity == "HIGH"


def test_a1_registered_vehicle_is_silent():
    e = engine(zones={"entry": SQUARE})
    car = Det(1, "car", (30, 30, 70, 70))
    for t in range(10):
        evs = e.update([car], ts=1000.0 + t,
                       plate_info={1: {"plate": "WB02AB1234", "registered": True}})
        assert evs == []


def test_a1_unreadable_plate_after_timeout():
    e = engine(zones={"entry": SQUARE})
    car = Det(1, "car", (30, 30, 70, 70))
    all_evs = []
    for t in range(8):
        all_evs += e.update([car], ts=1000.0 + t)
    types = [ev.event_type for ev in all_evs]
    assert types == [UNIDENTIFIED_VEHICLE]
    assert all_evs[0].severity == "LOW"


def test_a1_outside_zone_is_silent():
    e = engine(zones={"entry": SQUARE})
    car = Det(1, "car", (200, 200, 260, 260))  # outside
    evs = e.update([car], ts=1000.0,
                   plate_info={1: {"plate": "MH12CD4567", "registered": False}})
    assert evs == []


# --------------------------------------------------------------------- A2
def loiter_run(e, dwell_s, wiggle=5):
    """Person stands (almost) still inside the parking zone for dwell_s."""
    out = []
    for t in range(int(dwell_s) + 1):
        x = 50 + (t % 2) * wiggle
        p = Det(7, "person", (x - 5, 40, x + 5, 60))
        out += e.update([p], ts=2000.0 + t)
    return out


def test_a2_loitering_daytime():
    e = engine(zones={"parking": SQUARE})
    evs = loiter_run(e, 45)
    assert [ev.event_type for ev in evs] == [LOITERING]


def test_a2_no_alert_below_dwell():
    e = engine(zones={"parking": SQUARE})
    evs = loiter_run(e, 40)
    assert evs == []


def test_a2_night_threshold_is_shorter():
    e = engine(zones={"parking": SQUARE}, localtime=NIGHT)
    evs = loiter_run(e, 20)
    assert [ev.event_type for ev in evs] == [LOITERING]


def test_a2_zone_free_loiter_near_vehicle():
    # No parking zone drawn: a person lingering next to a car must still flag.
    e = engine(zones={})
    car = Det(1, "car", (100, 100, 200, 160))
    evs = []
    for t in range(46):
        person = Det(7, "person", (205, 120, 225, 180))  # right beside the car
        evs += e.update([car, person], ts=2000.0 + t)
    assert [ev.event_type for ev in evs] == [LOITERING]
    assert 7 in e.active_flags(2000.0 + 45)             # person flagged green


def test_a2_zone_free_person_far_from_vehicle_is_silent():
    e = engine(zones={})
    car = Det(1, "car", (100, 100, 200, 160))
    evs = []
    for t in range(46):
        person = Det(7, "person", (600, 300, 620, 360))  # far from the car
        evs += e.update([car, person], ts=2000.0 + t)
    assert evs == []


def test_a2_walking_through_is_not_loitering():
    e = engine(zones={"parking": [[0, 0], [1000, 0], [1000, 100], [0, 100]]})
    evs = []
    for t in range(50):  # crosses the zone, large displacement
        x = 10 + t * 15
        p = Det(7, "person", (x - 5, 40, x + 5, 60))
        evs += e.update([p], ts=2000.0 + t)
    assert evs == []


# --------------------------------------------------------------------- A3
def test_a3_contact_then_departure():
    e = engine()
    evs = []
    # vehicle 1 parked, vehicle 2 approaches and touches it
    for t in range(5):
        parked = Det(1, "car", (100, 100, 200, 160))
        moving = Det(2, "car", (210 - t * 15, 100, 310 - t * 15, 160))
        evs += e.update([parked, moving], ts=3000.0 + t)
    assert evs == []  # contact registered, nothing fired yet
    # vehicle 2 speeds away
    for t in range(5, 9):
        parked = Det(1, "car", (100, 100, 200, 160))
        moving = Det(2, "car", (150 + (t - 4) * 120, 100, 250 + (t - 4) * 120, 160))
        evs += e.update([parked, moving], ts=3000.0 + t)
    assert [ev.event_type for ev in evs] == [VEHICLE_CONTACT]
    assert "Possible" in evs[0].description


def test_a3_contact_then_vanish():
    e = engine()
    evs = []
    for t in range(5):
        parked = Det(1, "car", (100, 100, 200, 160))
        moving = Det(2, "car", (215 - t * 15, 100, 315 - t * 15, 160))
        evs += e.update([parked, moving], ts=3000.0 + t)
    # vehicle 2 disappears; after track expiry (~6s) but within the 15s window
    evs += e.update([Det(1, "car", (100, 100, 200, 160))], ts=3011.0)
    evs += e.update([Det(1, "car", (100, 100, 200, 160))], ts=3012.0)
    assert [ev.event_type for ev in evs] == [VEHICLE_CONTACT]


def test_a3_slow_side_by_side_no_alert():
    e = engine()
    evs = []
    for t in range(20):  # two parked cars, nobody moving
        a = Det(1, "car", (100, 100, 200, 160))
        b = Det(2, "car", (195, 100, 295, 160))
        evs += e.update([a, b], ts=3000.0 + t)
    assert evs == []


# --------------------------------------------------------------------- A4
def test_a4_restricted_zone_at_night():
    e = engine(zones={"restricted": SQUARE}, localtime=NIGHT)
    p = Det(9, "person", (40, 40, 60, 80))
    evs = e.update([p], ts=4000.0)
    assert [ev.event_type for ev in evs] == [RESTRICTED_ZONE]


def test_a4_restricted_zone_daytime_silent():
    e = engine(zones={"restricted": SQUARE}, localtime=DAY)
    p = Det(9, "person", (40, 40, 60, 80))
    assert e.update([p], ts=4000.0) == []


# --------------------------------------------------------------------- A5
def test_a5_tamper_needs_hold_time():
    e = engine()
    assert e.update_frame_stats(5.0, 100.0, ts=5000.0) == []   # dark starts
    assert e.update_frame_stats(5.0, 100.0, ts=5003.0) == []   # 3s < 5s
    evs = e.update_frame_stats(5.0, 100.0, ts=5006.0)
    assert [ev.event_type for ev in evs] == [CAMERA_TAMPER]


def test_a5_tamper_resets_when_frame_recovers():
    e = engine()
    e.update_frame_stats(5.0, 100.0, ts=5000.0)
    e.update_frame_stats(128.0, 200.0, ts=5002.0)  # recovered
    assert e.update_frame_stats(5.0, 100.0, ts=5004.0) == []  # timer restarted


def test_a5_blur_triggers():
    e = engine()
    e.update_frame_stats(128.0, 2.0, ts=5000.0)
    evs = e.update_frame_stats(128.0, 2.0, ts=5006.0)
    assert [ev.event_type for ev in evs] == [CAMERA_TAMPER]


def test_a5_offline():
    e = engine()
    assert e.update_stream_status(False, offline_since=6000.0, ts=6010.0) == []
    evs = e.update_stream_status(False, offline_since=6000.0, ts=6031.0)
    assert [ev.event_type for ev in evs] == [CAMERA_OFFLINE]
    # fires once until it comes back online
    assert e.update_stream_status(False, offline_since=6000.0, ts=6040.0) == []


# -------------------------------------------------------- culprit tracking
def test_culprit_flag_set_on_anomaly_and_expires():
    e = engine(zones={"entry": SQUARE})
    car = Det(1, "car", (30, 30, 70, 70))
    assert e.active_flags(1000.0) == set()
    e.update([car], ts=1000.0,
             plate_info={1: {"plate": "MH12CD4567", "registered": False}})
    # track 1 is now flagged as a culprit
    assert 1 in e.active_flags(1000.0)
    assert 1 in e.active_flags(1000.0 + 59)     # within flag_seconds
    assert 1 not in e.active_flags(1000.0 + 61)  # expired


def test_culprit_flag_persons_from_loitering():
    e = engine(zones={"parking": SQUARE})
    loiter_run(e, 45)
    flags = e.active_flags(2000.0 + 45)
    assert 7 in flags                            # the loitering person
    trails = e.flag_trails(2000.0 + 45)
    assert 7 in trails and len(trails[7]) >= 2   # has a path to draw


def test_no_flags_without_anomaly():
    e = engine(zones={"entry": SQUARE})
    car = Det(1, "car", (30, 30, 70, 70))
    e.update([car], ts=1000.0,
             plate_info={1: {"plate": "WB02AB1234", "registered": True}})
    assert e.active_flags(1000.0) == set()       # registered car = no culprit


# ---------------------------------------------------------------- debounce
def test_debounce_per_track_and_type():
    e = engine(zones={"entry": SQUARE})
    car = Det(1, "car", (30, 30, 70, 70))
    info = {1: {"plate": "MH12CD4567", "registered": False}}
    assert len(e.update([car], ts=1000.0, plate_info=info)) == 1
    assert e.update([car], ts=1030.0, plate_info=info) == []      # quiet period
    assert len(e.update([car], ts=1000.0 + 121, plate_info=info)) == 1  # re-arms
