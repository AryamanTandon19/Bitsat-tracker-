"""Parking-slot occupancy: arrivals, departures, and not crying wolf."""
from __future__ import annotations

import pytest

from app.slots import (INTRUDER, OCCUPIED, VACATED, Slot, SlotTracker,
                       point_in_polygon)

BOX = [[0, 0], [100, 0], [100, 100], [0, 100]]
BOX2 = [[200, 0], [300, 0], [300, 100], [200, 100]]


class Veh:
    def __init__(self, tid, foot):
        self.track_id, self.foot_point = tid, foot


def slot(sid=1, plate="WB02AB1234", poly=None, label="B-12"):
    return Slot(id=sid, camera="parking", label=label, polygon=poly or BOX,
                plate=plate, flat_number="B-402", owner_name="A. Tandon")


def tracker(*slots, **cfg):
    return SlotTracker(list(slots) or [slot()],
                       {"occupy_confirm_s": 10, "vacate_confirm_s": 20, **cfg})


# ------------------------------------------------------------------ geometry
def test_a_car_is_in_its_slot_or_it_is_not():
    assert point_in_polygon((50, 50), BOX)
    assert not point_in_polygon((150, 50), BOX)
    assert not point_in_polygon((50, 50), [])          # slot not drawn yet


# ----------------------------------------------------------------- arrivals
def test_a_car_must_settle_before_the_slot_counts_as_taken():
    t = tracker()
    car = Veh(1, (50, 50))
    info = {1: {"plate": "WB02AB1234"}}
    assert t.update([car], info, ts=0) == []            # just arrived
    assert t.update([car], info, ts=5) == []            # still settling
    changes = t.update([car], info, ts=11)
    assert [c.kind for c in changes] == [OCCUPIED]
    assert t.occupant(1) == "WB02AB1234"


def test_a_car_driving_through_never_takes_the_slot():
    t = tracker()
    info = {1: {"plate": "WB02AB1234"}}
    t.update([Veh(1, (50, 50))], info, ts=0)
    t.update([Veh(1, (50, 50))], info, ts=4)
    assert t.update([], {}, ts=8) == []                 # gone before it settled
    assert t.occupant(1) is None


# --------------------------------------------------------------- departures
def test_the_owner_is_told_when_their_car_leaves():
    t = tracker()
    car, info = Veh(1, (50, 50)), {1: {"plate": "WB02AB1234"}}
    t.update([car], info, ts=0)
    t.update([car], info, ts=11)                        # settled
    assert t.update([], {}, ts=20) == []                # not yet sure
    changes = t.update([], {}, ts=40)
    assert [c.kind for c in changes] == [VACATED]
    assert changes[0].plate == "WB02AB1234"
    assert changes[0].message() == "WB02AB1234 left B-12"
    assert t.occupant(1) is None


def test_a_dropped_frame_is_not_a_departure():
    """The tracker loses a parked car constantly — someone walks in front of
    it, the exposure shifts. Announcing that would get the app muted."""
    t = tracker()
    car, info = Veh(1, (50, 50)), {1: {"plate": "WB02AB1234"}}
    t.update([car], info, ts=0)
    t.update([car], info, ts=11)
    assert t.update([], {}, ts=14) == []                # blink
    assert t.update([car], info, ts=17) == []           # back, nothing said
    assert t.occupant(1) == "WB02AB1234"
    # and the clock restarts, so a later blink is judged on its own
    assert t.update([], {}, ts=30) == []
    assert t.update([car], info, ts=33) == []


def test_a_plate_that_goes_unreadable_is_not_a_new_car():
    t = tracker()
    car = Veh(1, (50, 50))
    t.update([car], {1: {"plate": "WB02AB1234"}}, ts=0)
    t.update([car], {1: {"plate": "WB02AB1234"}}, ts=11)
    # glare: same car, no plate read this frame
    assert t.update([car], {1: {"plate": None}}, ts=20) == []
    assert t.occupant(1) == "WB02AB1234"


# ---------------------------------------------------------------- intruders
def test_a_different_car_in_an_assigned_slot_is_flagged():
    t = tracker()
    other = Veh(2, (50, 50))
    info = {2: {"plate": "DL8CAF5031"}}
    t.update([other], info, ts=0)
    changes = t.update([other], info, ts=11)
    assert [c.kind for c in changes] == [OCCUPIED, INTRUDER]
    assert "not its space" in changes[1].message()


def test_the_right_car_in_its_own_slot_is_not_flagged():
    t = tracker()
    car, info = Veh(1, (50, 50)), {1: {"plate": "WB02AB1234"}}
    t.update([car], info, ts=0)
    assert [c.kind for c in t.update([car], info, ts=11)] == [OCCUPIED]


def test_an_unassigned_slot_flags_nobody():
    t = tracker(slot(plate=None, label="Visitor 3"))
    car, info = Veh(1, (50, 50)), {1: {"plate": "DL8CAF5031"}}
    t.update([car], info, ts=0)
    assert [c.kind for c in t.update([car], info, ts=11)] == [OCCUPIED]


def test_an_intruder_is_announced_once_not_every_frame():
    t = tracker()
    other, info = Veh(2, (50, 50)), {2: {"plate": "DL8CAF5031"}}
    t.update([other], info, ts=0)
    t.update([other], info, ts=11)
    for ts in (20, 40, 90, 300):
        assert t.update([other], info, ts=ts) == []


# ------------------------------------------------------------- several slots
def test_slots_are_independent():
    t = tracker(slot(1, "WB02AB1234", BOX, "B-12"),
                slot(2, "WB06CD4412", BOX2, "B-13"))
    a, b = Veh(1, (50, 50)), Veh(2, (250, 50))
    info = {1: {"plate": "WB02AB1234"}, 2: {"plate": "WB06CD4412"}}
    t.update([a, b], info, ts=0)
    t.update([a, b], info, ts=11)
    assert t.occupancy() == {1: "WB02AB1234", 2: "WB06CD4412"}
    t.update([b], info, ts=20)                          # only A drove off
    changes = t.update([b], info, ts=45)                # ...confirmed
    assert [(c.kind, c.slot.id) for c in changes] == [(VACATED, 1)]
    assert t.occupancy() == {1: None, 2: "WB06CD4412"}


def test_a_car_outside_every_slot_is_ignored():
    t = tracker()
    assert t.update([Veh(9, (500, 500))], {}, ts=0) == []
    assert t.update([Veh(9, (500, 500))], {}, ts=60) == []


# ---------------------------------------------------------------- start-up
def test_priming_adopts_what_is_already_parked_silently():
    """Everything sitting in its space at boot must not be announced as having
    just arrived."""
    t = tracker()
    car, info = Veh(1, (50, 50)), {1: {"plate": "WB02AB1234"}}
    t.prime([car], info, ts=0)
    assert t.occupant(1) == "WB02AB1234"
    assert t.update([car], info, ts=5) == []
    # but a real departure after priming is still reported. Two observations,
    # because a duration cannot be measured from a single one.
    assert t.update([], {}, ts=30) == []
    assert [c.kind for c in t.update([], {}, ts=60)] == [VACATED]
