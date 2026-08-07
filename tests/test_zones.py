"""Per-camera zones: drawing entry / parking / restricted areas, storing them
by name (so config cameras get them too), and applying them to a running camera
without a restart.
"""
from __future__ import annotations

import json

import pytest

from app.db import ZONE_KINDS, Database, clean_zones
from app.rules import RulesEngine
from app.trigger import CandidateTrigger

TRI = [[10, 10], [200, 10], [200, 200]]      # a valid 3-point polygon


# ------------------------------------------------------------ clean_zones
def test_the_three_known_kinds_always_exist():
    z = clean_zones({})
    assert set(ZONE_KINDS) <= set(z)
    assert all(z[k] == [] for k in ZONE_KINDS)


def test_a_polygon_needs_three_points():
    z = clean_zones({"parking": [[0, 0], [1, 1]]})
    assert z["parking"] == []                # two points enclose nothing
    assert clean_zones({"entry": TRI})["entry"] == [[10.0, 10.0], [200.0, 10.0],
                                                    [200.0, 200.0]]


def test_points_are_coerced_to_floats_and_junk_is_dropped():
    z = clean_zones({"entry": [[1, 2], ["x", 3], [4, 5], [6, 7]]})
    assert z["entry"] == [[1.0, 2.0], [4.0, 5.0], [6.0, 7.0]]


def test_a_site_may_draw_its_own_kind():
    z = clean_zones({"loading_bay": TRI})
    assert z["loading_bay"] == [[10.0, 10.0], [200.0, 10.0], [200.0, 200.0]]


# ------------------------------------------------------------- persistence
def test_db_round_trip_and_upsert(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    try:
        db.set_camera_zones("gate", {"entry": TRI}, actor="admin")
        assert db.get_camera_zones("gate")["entry"] == [[10.0, 10.0],
                                                        [200.0, 10.0], [200.0, 200.0]]
        # redraw: replaces, does not accumulate rows
        db.set_camera_zones("gate", {"parking": TRI}, actor="admin")
        z = db.get_camera_zones("gate")
        assert z["parking"] and z["entry"] == []
        assert list(db.list_camera_zones()) == ["gate"]
    finally:
        db.close()


def test_setting_zones_is_audited(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    try:
        db.set_camera_zones("gate", {"entry": TRI}, actor="alice")
        rows = [r for r in db.audit_rows() if r["action"] == "CAMERA_CHANGE"]
        assert rows and "zones" in rows[-1]["details_json"]
    finally:
        db.close()


def test_unknown_camera_has_no_zones(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    try:
        assert db.get_camera_zones("never") is None
    finally:
        db.close()


# --------------------------------------------------------------- hot-apply
def test_rules_engine_swaps_zones_live():
    r = RulesEngine("cam", {"entry": []}, {"night_hours": {}})
    r.set_zones({"entry": TRI})
    assert r.zones["entry"] == TRI


def test_trigger_swaps_zones_live():
    t = CandidateTrigger({"parking": []}, {})
    t.set_zones({"parking": TRI})
    assert t.zones["parking"] == TRI


# ------------------------------------------------------------------ the API
pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient       # noqa: E402

from app import dashboard                        # noqa: E402
from app.main import load_config                 # noqa: E402

from .conftest import signin                     # noqa: E402


class FakePipe:
    def __init__(self):
        self.annotated_jpeg = b"\xff\xd8jpeg\xff\xd9"
        self.applied = None

    def set_zones(self, zones):
        self.applied = zones


@pytest.fixture()
def ctx(tmp_path):
    class Ctx:
        def set_camera_zones(self, name, zones, actor=""):
            clean = self.db.set_camera_zones(name, zones, actor)
            pipe = self.pipelines.get(name)
            if pipe is not None:
                pipe.set_zones(clean)
            return clean

    c = Ctx()
    c.config = load_config("config.yaml")
    c.config["dashboard"]["auth"] = {"enabled": False}
    c.config["cameras"] = []
    c.db = Database(str(tmp_path / "t.db"))
    c.workers, c.pipelines, c.analyzer, c.assistant = {}, {}, None, None
    yield c
    c.db.close()


@pytest.fixture()
def client(ctx):
    cl = TestClient(dashboard.create_app(ctx))
    signin(cl, ctx.db, "admin", name="A. Admin")
    return cl


def test_save_then_get_zones(client):
    r = client.post("/api/cameras/zones",
                    data={"name": "gate", "zones_json": json.dumps({"entry": TRI})})
    assert r.status_code == 200, r.text
    assert r.json()["counts"]["entry"] == 3
    got = client.get("/api/cameras/gate/zones").json()
    assert got["zones"]["entry"] == [[10.0, 10.0], [200.0, 10.0], [200.0, 200.0]]


def test_a_config_camera_with_no_db_row_still_gets_zones(client, ctx):
    """Keyed by name, so a camera from config.yaml carries zones too."""
    ctx.config["cameras"] = [{"name": "old", "url": "rtsp://a:b@1.2.3.4/live"}]
    client.post("/api/cameras/zones",
                data={"name": "old", "zones_json": json.dumps({"restricted": TRI})})
    assert ctx.db.get_camera_zones("old")["restricted"]


def test_saving_zones_applies_them_to_a_running_camera(client, ctx):
    pipe = FakePipe()
    ctx.pipelines["gate"] = pipe
    client.post("/api/cameras/zones",
                data={"name": "gate", "zones_json": json.dumps({"entry": TRI})})
    assert pipe.applied is not None and pipe.applied["entry"]


def test_degenerate_polygons_are_cleaned_before_storing(client):
    client.post("/api/cameras/zones",
                data={"name": "gate",
                      "zones_json": json.dumps({"parking": [[0, 0], [1, 1]]})})
    assert client.get("/api/cameras/gate/zones").json()["zones"]["parking"] == []


def test_invalid_json_is_refused(client):
    r = client.post("/api/cameras/zones",
                    data={"name": "gate", "zones_json": "not json"})
    assert r.status_code == 400


def test_a_nameless_camera_is_refused(client):
    r = client.post("/api/cameras/zones",
                    data={"name": "  ", "zones_json": "{}"})
    assert r.status_code == 400


def test_a_guard_cannot_edit_zones(ctx):
    cl = TestClient(dashboard.create_app(ctx))
    signin(cl, ctx.db, "guard")
    r = cl.post("/api/cameras/zones",
                data={"name": "gate", "zones_json": "{}"})
    assert r.status_code == 403


# --------------------------------------------------------------- snapshot
def test_snapshot_returns_a_frame_when_one_exists(client, ctx):
    ctx.workers["gate"] = object()               # present so /stream-style checks pass
    ctx.pipelines["gate"] = FakePipe()
    r = client.get("/snapshot/gate")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"


def test_snapshot_404_without_a_frame(client):
    assert client.get("/snapshot/ghost").status_code == 404
