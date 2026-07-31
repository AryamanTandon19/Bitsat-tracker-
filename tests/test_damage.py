"""Damage lookup: "who hit my car while it was parked in B-12 last Tuesday?"."""
from __future__ import annotations

import time

import pytest

from app import damage
from app.db import Database

BOX = [[0, 0], [100, 0], [100, 100], [0, 100]]
PLATE = "WB02AB1234"


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    yield d
    d.close()


@pytest.fixture()
def parked(db):
    """A car parked in B-12 from 12 hours ago until 2 hours ago."""
    now = time.time()
    sid = db.add_slot("parking", "B-12", BOX, PLATE, "B-402")
    db.record_slot_activity(sid, "occupied", PLATE, now - 12 * 3600)
    db.record_slot_activity(sid, "vacated", PLATE, now - 2 * 3600)
    return sid, now


def _event(db, ago_h, etype="vehicle_contact", camera="parking",
           severity="MEDIUM", score=0.0, clip=False):
    eid = db.insert_event(time.time() - ago_h * 3600, camera, etype, severity,
                          None, [1], 0.5, f"{etype} {ago_h}h ago", score=score)
    if clip:
        db.insert_clip(eid, "/tmp/c.mp4", "", 0, 0)
    return eid


# ------------------------------------------------------------- the windows
def test_a_parked_period_is_reconstructed_from_the_register(db, parked):
    sid, now = parked
    w = db.slot_windows(plate=PLATE)
    assert len(w) == 1
    assert w[0].label == "B-12" and w[0].camera == "parking"
    assert w[0].start == pytest.approx(now - 12 * 3600)
    assert w[0].end == pytest.approx(now - 2 * 3600)


def test_a_car_that_has_not_moved_yet_is_still_searchable(db):
    """The commonest damage case: it is still sitting there with the scrape."""
    now = time.time()
    sid = db.add_slot("parking", "B-12", BOX, PLATE)
    db.record_slot_activity(sid, "occupied", PLATE, now - 6 * 3600)
    w = db.slot_windows(plate=PLATE)
    assert len(w) == 1 and w[0].end is None
    assert w[0].covers(now - 60)


def test_windows_outside_the_asked_period_are_dropped(db, parked):
    sid, now = parked
    assert db.slot_windows(plate=PLATE, since=now - 1 * 3600) == []
    assert db.slot_windows(plate=PLATE, until=now - 20 * 3600) == []
    assert len(db.slot_windows(plate=PLATE, since=now - 24 * 3600)) == 1


def test_separate_stays_are_separate_windows(db):
    now = time.time()
    sid = db.add_slot("parking", "B-12", BOX, PLATE)
    for start, end in ((30, 26), (20, 14), (8, 3)):
        db.record_slot_activity(sid, "occupied", PLATE, now - start * 3600)
        db.record_slot_activity(sid, "vacated", PLATE, now - end * 3600)
    assert len(db.slot_windows(plate=PLATE)) == 3


# -------------------------------------------------------------- the search
def test_only_what_happened_while_it_was_parked_comes_back(db, parked):
    _event(db, ago_h=7)                       # during the stay
    _event(db, ago_h=1)                       # after it left
    _event(db, ago_h=20)                      # before it arrived
    out = damage.search(db, plate=PLATE)
    assert len(out["candidates"]) == 1
    assert "7h ago" in out["candidates"][0]["description"]


def test_another_camera_is_not_searched(db, parked):
    """B-12 is watched by one camera; the gate camera cannot have seen it."""
    _event(db, ago_h=7, camera="gate")
    assert damage.search(db, plate=PLATE)["candidates"] == []


def test_a_person_merely_walking_past_is_not_evidence(db, parked):
    _event(db, ago_h=7, etype="camera_offline")
    _event(db, ago_h=6, etype="unidentified_vehicle")
    assert damage.search(db, plate=PLATE)["candidates"] == []


def test_contact_outranks_loitering(db, parked):
    _event(db, ago_h=8, etype="loitering")
    _event(db, ago_h=6, etype="vehicle_contact")
    kinds = [c["event_type"] for c in damage.search(db, plate=PLATE)["candidates"]]
    assert kinds == ["vehicle_contact", "loitering"]


def test_a_clip_and_a_high_score_lift_a_candidate(db, parked):
    _event(db, ago_h=8, etype="loitering", score=0.2)
    _event(db, ago_h=6, etype="loitering", score=0.9, severity="HIGH", clip=True)
    top = damage.search(db, plate=PLATE)["candidates"][0]
    assert "6h ago" in top["description"]
    assert "clip available" in top["why"] and "raised as HIGH" in top["why"]


def test_the_answer_says_what_was_searched_even_when_nothing_is_found(db, parked):
    """'We looked at 19:40-07:15 on the parking camera and found nothing' is a
    real answer; an empty list on its own is not."""
    out = damage.search(db, plate=PLATE)
    assert out["candidates"] == []
    assert len(out["windows"]) == 1
    assert out["windows"][0]["label"] == "B-12"


def test_every_candidate_can_say_why_it_is_there(db, parked):
    _event(db, ago_h=6, etype="vehicle_contact", score=0.7, clip=True)
    c = damage.search(db, plate=PLATE)["candidates"][0]
    assert "vehicle contact on parking" in c["why"]
    assert "threat score 0.70" in c["why"]
    assert c["relevance"] > 1.0


def test_search_can_start_from_the_slot_instead_of_the_plate(db, parked):
    sid, _ = parked
    _event(db, ago_h=6)
    assert len(damage.search(db, slot_id=sid)["candidates"]) == 1


def test_results_are_capped(db, parked):
    for h in range(3, 12):
        _event(db, ago_h=h, etype="loitering")
    assert len(damage.search(db, plate=PLATE, limit=4)["candidates"]) == 4


# ------------------------------------------------------------------- HTTP
def test_the_endpoint_needs_something_to_search_for(tmp_path, db):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from app import dashboard
    from app.main import load_config

    from .conftest import signin

    class Ctx:
        pass
    c = Ctx()
    c.config = load_config("config.yaml")
    c.config["dashboard"]["auth"] = {"enabled": False}
    c.config_path = str(tmp_path / "config.yaml")
    c.db = db
    c.workers, c.pipelines, c.analyzer, c.assistant = {}, {}, None, None
    cl = TestClient(dashboard.create_app(c))
    signin(cl, db, "guard")

    assert cl.get("/api/damage").status_code == 400
    r = cl.get("/api/damage?plate=wb02-ab-1234")
    assert r.status_code == 200 and "windows" in r.json()
