"""The loop closes: a guard's taps change what the pipeline raises next time.

This is the claim the product rests on — that feedback is worth collecting
because it feeds back. These tests exercise it end to end through the real
database and the real rules engine, not through the scorer alone.
"""
from __future__ import annotations

import time
from datetime import datetime

import pytest

from app.db import Database
from app.rules import LOITERING, RulesEngine

NIGHT = lambda: datetime(2026, 7, 4, 23, 30)

CFG = {
    "night_hours": {"start": "23:00", "end": "05:00"},
    "debounce_s": 0,
    "loitering": {"enabled": True, "dwell_s": 30, "night_dwell_s": 20,
                  "max_displacement_px": 500, "near_vehicle_px": 150},
    "unauthorized_vehicle": {"enabled": False},
    "restricted_zone": {"enabled": False},
    "vehicle_contact": {"enabled": False},
    "tamper": {"enabled": False},
}


class Det:
    def __init__(self, tid, cls_name, xyxy):
        self.track_id, self.cls_name, self.xyxy = tid, cls_name, xyxy
        self.is_person = cls_name == "person"
        self.is_vehicle = cls_name in ("car", "truck", "bus", "motorcycle")
        self.foot_point = ((xyxy[0] + xyxy[2]) / 2, xyxy[3])


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    yield d
    d.close()


def _loiterer(engine, t0, seconds=90):
    """Run a person standing still long enough to fire loitering."""
    person = Det(1, "person", (40, 40, 60, 90))
    events = []
    for i in range(0, seconds, 5):
        events += engine.update([person], ts=t0 + i)
    return events


def test_an_alert_fires_and_carries_its_reasoning(db):
    e = RulesEngine("gate", {"parking": [[0, 0], [200, 0], [200, 200], [0, 200]]},
                    CFG, localtime_fn=NIGHT)
    evs = _loiterer(e, 1000.0)
    assert {x.event_type for x in evs} == {LOITERING}
    assert evs[0].score > 0
    assert "after dark" in evs[0].score_why


def test_a_site_that_dismisses_this_alert_stops_being_told(db):
    zones = {"parking": [[0, 0], [200, 0], [200, 200], [0, 200]]}
    e = RulesEngine("gate", zones, CFG, localtime_fn=NIGHT)
    first = _loiterer(e, 1000.0)
    assert first, "baseline must fire, or the test proves nothing"
    baseline = first[0].score

    # A guard works through a week of these and marks every one a false alarm.
    now = time.time()
    for i in range(12):
        eid = db.insert_event(now - i * 3600, "gate", LOITERING, "MEDIUM",
                              None, [1], 0.7, "loitering")
        db.insert_feedback(eid, "false_alarm", "Ramesh K.")

    rates = db.verdict_rates()
    assert rates[("gate", LOITERING)]["false_alarm_rate"] == 1.0

    # The pipeline picks those up and the same situation scores lower.
    e2 = RulesEngine("gate", zones, CFG, localtime_fn=NIGHT)
    e2.verdict_rates = rates
    second = _loiterer(e2, 2000.0)
    if second:
        assert second[0].score < baseline
    else:
        # quietened all the way below the dismiss line — recorded, not raised
        assert e2.dismissed and e2.dismissed[-1]["score"] < baseline


def test_dismissals_at_one_camera_do_not_quieten_another(db):
    zones = {"parking": [[0, 0], [200, 0], [200, 200], [0, 200]]}
    now = time.time()
    for i in range(12):
        eid = db.insert_event(now - i * 3600, "gate", LOITERING, "MEDIUM",
                              None, [1], 0.7, "x")
        db.insert_feedback(eid, "false_alarm", "Ramesh K.")
    rates = db.verdict_rates()

    quiet = RulesEngine("gate", zones, CFG, localtime_fn=NIGHT)
    quiet.verdict_rates = rates
    loud = RulesEngine("parking", zones, CFG, localtime_fn=NIGHT)
    loud.verdict_rates = rates          # same table, different camera

    q = _loiterer(quiet, 1000.0)
    l = _loiterer(loud, 1000.0)
    q_score = q[0].score if q else quiet.dismissed[-1]["score"]
    assert l and l[0].score > q_score


def test_a_couple_of_dismissals_are_an_opinion_not_a_pattern(db):
    """Two taps must not retune a site."""
    now = time.time()
    for i in range(2):
        eid = db.insert_event(now - i * 60, "gate", LOITERING, "MEDIUM",
                              None, [1], 0.7, "x")
        db.insert_feedback(eid, "false_alarm", "Ramesh K.")
    assert db.verdict_rates() == {}          # below min_samples


def test_only_the_latest_verdict_on_an_event_counts(db):
    now = time.time()
    for i in range(6):
        eid = db.insert_event(now - i * 60, "gate", LOITERING, "MEDIUM",
                              None, [1], 0.7, "x")
        db.insert_feedback(eid, "false_alarm", "Guard 1")
        db.insert_feedback(eid, "real", "Guard 2")     # corrected on review
    r = db.verdict_rates()[("gate", LOITERING)]
    assert r["confirmed_rate"] == 1.0 and r["false_alarm_rate"] == 0.0


def test_turning_the_layer_off_restores_the_old_fixed_table(db):
    """A site that wants the previous behaviour has one line to change."""
    zones = {"parking": [[0, 0], [200, 0], [200, 200], [0, 200]]}
    e = RulesEngine("gate", zones, {**CFG, "scoring": {"enabled": False}},
                    localtime_fn=NIGHT)
    evs = _loiterer(e, 1000.0)
    assert evs[0].severity == "MEDIUM"       # straight from SEVERITY[]
    assert evs[0].score == 0.0
