"""Learning what is permanently there, so it stops being an alarm.

The scenario every test circles: a static object misread as a person, sitting
beside a car park, firing a loitering alert every debounce interval forever.
The fix has to catch that without ever silencing a real person.
"""
from __future__ import annotations

from app.normalcy import (MIN_OBS, STATIC_OCCUPANCY, CameraNormalcy)


class Det:
    """A detection: class, box, and an optional track id."""
    def __init__(self, cls, x1, y1, x2, y2, track_id=None):
        self.cls_name = cls
        self.xyxy = (x1, y1, x2, y2)
        self.track_id = track_id


W, H = 1920, 1080


def hydrant(track_id=9):
    """A 'person' that never moves — 29x61px at the same spot, like the real
    fire hydrant that started all this."""
    return Det("person", 122, 489, 151, 550, track_id)


def feed(nm, det, n, ts0=0.0, dt=0.16):
    """Show the map one detection for n frames."""
    for i in range(n):
        nm.observe([det] if det else [], W, H, ts=ts0 + i * dt)
    return ts0 + n * dt


# ------------------------------------------------------------- the core
def test_a_static_object_is_eventually_learned_as_furniture():
    nm = CameraNormalcy("gate")
    ts = feed(nm, hydrant(), 300)
    v = nm.classify(hydrant(), W, H, ts=ts)
    assert v.static is True
    assert "furniture" in v.reason
    assert v.occupancy > STATIC_OCCUPANCY


def test_it_suppresses_nothing_before_it_has_learned():
    """Ninety seconds after install it knows nothing and must say so, not
    guess."""
    nm = CameraNormalcy("gate")
    feed(nm, hydrant(), 20)
    v = nm.classify(hydrant(), W, H, ts=100.0)
    assert v.static is False
    assert "history" in v.reason


def test_the_confidence_gate_needs_real_evidence():
    nm = CameraNormalcy("gate")
    feed(nm, hydrant(), MIN_OBS // 2)
    assert nm.classify(hydrant(), W, H, ts=50.0).static is False


# ------------------------------------------- never silence a real person
def test_a_person_walking_through_is_not_furniture():
    """A person crosses the frame once. Every cell they touch is seen for a
    handful of frames and never again — that must never become 'furniture'."""
    nm = CameraNormalcy("gate")
    # a long history of an empty car park with one parked car
    car = Det("car", 800, 600, 1000, 720)
    feed(nm, car, 400)
    # now a person walks left to right along the bottom
    ts = 400 * 0.16
    for i in range(25):
        p = Det("person", 100 + i * 60, 900, 130 + i * 60, 980, track_id=5)
        nm.observe([car, p], W, H, ts=ts + i * 0.16)
        v = nm.classify(p, W, H, ts=ts + i * 0.16)
        assert v.static is False, f"suppressed a walking person at step {i}"


def test_a_person_who_walked_to_a_bollard_and_stopped_is_not_furniture():
    """The hard case, and the reason mobility is judged over a track's whole
    life rather than frame to frame: a real person walks across the car park
    and stops beside the bollard. They GOT there by moving; furniture never
    did. Even standing still in the furniture cell, they are not suppressed."""
    nm = CameraNormalcy("gate")
    feed(nm, hydrant(track_id=1), 300)          # learn the bollard
    ts = 300 * 0.16
    # person 77 walks a long way across the frame, then stands at the bollard
    for i in range(20):
        x = 1400 - i * 64                       # travels many cells
        p = Det("person", x, 489, x + 29, 550, track_id=77)
        nm.observe([hydrant(track_id=1), p], W, H, ts=ts + i * 0.16)
    # now they are standing still at the bollard's exact cell for a while
    for i in range(10):
        p = Det("person", 122, 489, 151, 550, track_id=77)
        nm.observe([hydrant(track_id=1), p], W, H, ts=ts + (20 + i) * 0.16)
    v = nm.classify(Det("person", 122, 489, 151, 550, track_id=77),
                    W, H, ts=ts + 30 * 0.16)
    assert v.moving is True and v.static is False


def test_a_re_identified_static_object_is_still_suppressed():
    """The tracker loses and re-acquires the fire hydrant under a fresh id
    constantly. A new track that appears at a learned-furniture cell and never
    moves IS the hydrant — suppressing it regardless of id is the whole point,
    because otherwise every re-id fires a fresh alert forever."""
    nm = CameraNormalcy("gate")
    feed(nm, hydrant(track_id=1), 300)
    ts = 300 * 0.16
    # the hydrant re-ids to 999 and, being furniture, does not move
    for i in range(5):
        nm.observe([hydrant(track_id=999)], W, H, ts=ts + i * 0.16)
    v = nm.classify(hydrant(track_id=999), W, H, ts=ts + 5 * 0.16)
    assert v.static is True


# ---------------------------------------------------------- adaptation
def test_the_map_forgets_when_something_leaves():
    nm = CameraNormalcy("gate")
    ts = feed(nm, hydrant(), 300)
    assert nm.classify(hydrant(), W, H, ts=ts).static is True
    # the object is gone; the car park is empty for a long time
    ts = feed(nm, None, 800, ts0=ts)
    assert nm.classify(hydrant(), W, H, ts=ts).static is False


def test_a_parked_car_becomes_normal_and_is_reported_as_a_spot():
    nm = CameraNormalcy("gate")
    car = Det("car", 800, 600, 1000, 720)
    feed(nm, car, 300)
    spots = [c for c in nm.static_map() if c["class"] == "car"]
    assert spots, "a persistently parked car should register as a spot"


# ------------------------------------------------------------- the map
def test_the_static_map_lists_learned_furniture():
    nm = CameraNormalcy("gate")
    feed(nm, hydrant(), 300)
    s = nm.summary()
    assert s["static_cells"] >= 1
    assert any(c["class"] == "person" for c in s["static"])


def test_two_cameras_do_not_share_a_background():
    a = CameraNormalcy("gate")
    b = CameraNormalcy("parking")
    feed(a, hydrant(), 300)
    assert a.classify(hydrant(), W, H, ts=50).static is True
    assert b.classify(hydrant(), W, H, ts=50).static is False


# ---------------------------------------------------------- persistence
def test_a_learned_background_survives_a_restart():
    """A camera that learned its background over a week must not forget it
    because the box rebooted at 3am."""
    nm = CameraNormalcy("gate")
    ts = feed(nm, hydrant(), 300)
    rows = nm.to_rows()
    assert rows

    restored = CameraNormalcy("gate")
    restored.load_rows(rows)
    v = restored.classify(hydrant(), W, H, ts=ts)
    assert v.static is True


def test_only_cells_with_evidence_are_persisted():
    nm = CameraNormalcy("gate")
    feed(nm, hydrant(), 300)
    # a car flickers once far away
    nm.observe([Det("car", 1800, 1000, 1850, 1050)], W, H, ts=100.0)
    rows = nm.to_rows()
    assert all(r["observations"] >= 1.0 for r in rows)


def test_a_non_tracked_class_is_ignored():
    nm = CameraNormalcy("gate")
    for i in range(300):
        nm.observe([Det("bag", 100, 100, 130, 160)], W, H, ts=i * 0.16)
    assert nm.summary()["cells_tracked"] == 0
