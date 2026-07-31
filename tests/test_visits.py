"""Visitor log (automated gate register) behaviour."""
from __future__ import annotations

import time

import pytest

from app.db import Database


@pytest.fixture()
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


def test_first_crossing_opens_a_visit(db):
    t = time.time()
    out = db.record_gate_crossing("WB02AB1234", "gate", t)
    assert out["action"] == "entry"
    assert out["visit"]["exit_ts"] is None
    assert out["visit"]["registered"] == 0
    assert [v["plate"] for v in db.open_visits()] == ["WB02AB1234"]


def test_repeat_sightings_are_debounced(db):
    t = time.time()
    db.record_gate_crossing("WB02AB1234", "gate", t)
    # Same car still lingering in frame a few seconds later.
    out = db.record_gate_crossing("WB02AB1234", "gate", t + 5)
    assert out["action"] == "ignored"
    assert out["visit"]["last_seen_ts"] == pytest.approx(t + 5)
    assert len(db.open_visits()) == 1


def test_a_car_pausing_at_the_gate_is_not_an_instant_exit(db):
    t = time.time()
    db.record_gate_crossing("WB02AB1234", "gate", t)
    # Past the debounce but inside min_visit_s — still the same arrival.
    out = db.record_gate_crossing("WB02AB1234", "gate", t + 40,
                                  debounce_s=10.0, min_visit_s=120.0)
    assert out["action"] == "ignored"
    assert len(db.open_visits()) == 1


def test_second_crossing_closes_the_visit(db):
    t = time.time()
    db.record_gate_crossing("WB02AB1234", "gate", t)
    out = db.record_gate_crossing("WB02AB1234", "gate", t + 3600)
    assert out["action"] == "exit"
    assert out["visit"]["exit_ts"] == pytest.approx(t + 3600)
    assert out["visit"]["exit_camera"] == "gate"
    assert db.open_visits() == []


def test_a_return_trip_opens_a_fresh_visit(db):
    t = time.time()
    db.record_gate_crossing("WB02AB1234", "gate", t)
    db.record_gate_crossing("WB02AB1234", "gate", t + 3600)
    out = db.record_gate_crossing("WB02AB1234", "gate", t + 7200)
    assert out["action"] == "entry"
    assert len(db.recent_visits(plate="WB02AB1234")) == 2


def test_registered_vehicles_carry_owner_details(db):
    db.add_vehicle("WB02AB1234", owner_name="A. Tandon", flat_number="B-402")
    out = db.record_gate_crossing("WB02AB1234", "gate", time.time())
    v = out["visit"]
    assert v["registered"] == 1
    assert v["owner_name"] == "A. Tandon"
    assert v["flat_number"] == "B-402"


def test_recent_visits_filters(db):
    t = time.time()
    db.add_vehicle("WB02AB1234", owner_name="Resident")
    db.record_gate_crossing("WB02AB1234", "gate", t)
    db.record_gate_crossing("DL8CAF5031", "gate", t)
    assert len(db.recent_visits()) == 2
    assert [v["plate"] for v in db.recent_visits(registered=True)] == ["WB02AB1234"]
    assert [v["plate"] for v in db.recent_visits(registered=False)] == ["DL8CAF5031"]
    assert [v["plate"] for v in db.recent_visits(plate="8CAF")] == ["DL8CAF5031"]


def test_a_gate_camera_records_one_crossing_per_track(db, monkeypatch):
    """The pipeline must not log a crossing on every analyzed frame."""
    from app.main import CameraPipeline

    pipe = CameraPipeline.__new__(CameraPipeline)      # no camera/YOLO needed
    pipe.cam_name = "gate"
    pipe.vl_cfg = {}
    pipe._logged_tracks = {}

    class Ctx:
        pass
    pipe.ctx = Ctx()
    pipe.ctx.db = db

    info = {7: {"plate": "WB02AB1234", "registered": False}}
    t = time.time()
    for i in range(30):                                 # 30 frames, one car
        pipe._log_gate_crossings(info, t + i * 0.16)
    assert len(db.recent_visits()) == 1

    # The car comes back hours later under a new track id -> the exit.
    pipe._log_gate_crossings({9: info[7]}, t + 7200)
    visits = db.recent_visits()
    assert len(visits) == 1 and visits[0]["exit_ts"] is not None


def test_overstays_only_flag_unregistered_vehicles(db):
    old = time.time() - 20 * 3600
    db.add_vehicle("WB02AB1234")
    db.record_gate_crossing("WB02AB1234", "gate", old)   # resident, parked
    db.record_gate_crossing("DL8CAF5031", "gate", old)   # visitor, never left
    db.record_gate_crossing("MH12XY0007", "gate", time.time())  # just arrived
    assert [v["plate"] for v in db.overstaying_visits(hours=12)] == ["DL8CAF5031"]


# ------------------------------------------------------------ API surface
@pytest.fixture()
def client(db, tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
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
    signin(cl, db, "guard")      # the register is personal data; it needs one
    return cl


def _seed(db):
    t = time.time()
    db.add_vehicle("WB02AB1234", owner_name="A. Tandon", flat_number="B-402")
    db.record_gate_crossing("WB02AB1234", "gate", t - 20 * 3600)  # resident, in
    db.record_gate_crossing("DL8CAF5031", "gate", t - 20 * 3600)  # visitor, in
    db.record_gate_crossing("MH12XY0007", "gate", t - 7200)
    db.record_gate_crossing("MH12XY0007", "gate", t - 60)         # came and went


def test_visits_endpoint_lists_the_register(client, db):
    _seed(db)
    assert len(client.get("/api/visits").json()) == 3


def test_visits_endpoint_filters(client, db):
    _seed(db)
    plates = [v["plate"] for v in client.get("/api/visits?registered=0").json()]
    assert plates == ["MH12XY0007", "DL8CAF5031"]
    # plates are normalized, so a guard can type it however they like
    hit = client.get("/api/visits?plate=wb02-ab-1234").json()
    assert [v["plate"] for v in hit] == ["WB02AB1234"]


def test_open_and_overstay_endpoints(client, db):
    _seed(db)
    assert {v["plate"] for v in client.get("/api/visits/open").json()} == \
        {"WB02AB1234", "DL8CAF5031"}
    over = client.get("/api/visits/overstays").json()
    assert [v["plate"] for v in over] == ["DL8CAF5031"]
