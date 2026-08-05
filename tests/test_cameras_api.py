"""Adding a camera from the console: scan, probe, add, start — no restart.

The security-relevant tests are the ones that matter most here: these
endpoints scan the local network and store DVR passwords.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from app import dashboard
from app import discovery as D
from app.db import Database
from app.main import load_config

from .conftest import signin


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    class Ctx:
        def __init__(self):
            self.started = {}
            self.stopped = []

        def start_camera(self, name, url, zones=None, loop_file=False):
            self.started[name] = url
            return True

        def stop_camera(self, name):
            self.stopped.append(name)
            self.started.pop(name, None)
            return True

    c = Ctx()
    c.config = load_config("config.yaml")
    c.config["dashboard"]["auth"] = {"enabled": False}
    c.config["cameras"] = []
    c.config_path = str(tmp_path / "config.yaml")
    c.db = Database(str(tmp_path / "t.db"))
    c.workers, c.pipelines, c.analyzer, c.assistant = {}, {}, None, None
    # never touch a real network from a test
    monkeypatch.setattr(D, "open_stream",
                        lambda url, timeout_s=6.0:
                        (("realmonitor" in url), 704, 576,
                         "" if "realmonitor" in url else "no frame"))
    yield c
    c.db.close()


@pytest.fixture()
def client(ctx):
    cl = TestClient(dashboard.create_app(ctx))
    signin(cl, ctx.db, "admin", name="A. Admin")
    return cl


def add(client, **over):
    data = {"name": "gate", "host": "192.168.1.108", "channel": 1,
            "username": "admin", "password": "hunter2"}
    data.update(over)
    return client.post("/api/cameras/add", data=data)


# ------------------------------------------------------------------ auth
def test_every_camera_endpoint_needs_a_real_session(ctx):
    """These scan the local network and store DVR passwords. The HTTP Basic
    gate is disabled in config.yaml, so they hang off the session auth."""
    anon = TestClient(dashboard.create_app(ctx))
    assert anon.get("/api/cameras/list").status_code in (401, 403)
    assert anon.post("/api/cameras/scan",
                     data={"network": "10.0.0.0/24"}).status_code in (401, 403)
    assert anon.post("/api/cameras/probe",
                     data={"host": "10.0.0.1"}).status_code in (401, 403)
    assert add(anon).status_code in (401, 403)


def test_a_guard_account_cannot_add_cameras(ctx):
    cl = TestClient(dashboard.create_app(ctx))
    signin(cl, ctx.db, "guard")
    assert add(cl).status_code == 403


# ---------------------------------------------------------------- probing
def test_probing_finds_the_working_pattern(client):
    r = client.post("/api/cameras/probe",
                    data={"host": "192.168.1.108", "username": "admin",
                          "password": "hunter2", "channels": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["working"] == 2
    assert all(c["vendor"] == "dahua/cpplus" for c in body["channels"])


def test_a_probe_response_never_contains_the_password(client):
    r = client.post("/api/cameras/probe",
                    data={"host": "192.168.1.108", "username": "admin",
                          "password": "hunter2", "channels": 1})
    assert "hunter2" not in r.text
    assert "****" in r.text


def test_an_absurd_channel_count_is_refused(client):
    r = client.post("/api/cameras/probe",
                    data={"host": "10.0.0.1", "channels": 500})
    assert r.status_code == 400


def test_an_oversized_scan_range_is_refused_with_advice(client):
    r = client.post("/api/cameras/scan", data={"network": "10.0.0.0/8"})
    assert r.status_code == 400 and "smaller range" in r.json()["detail"]


# ------------------------------------------------------------------ add
def test_adding_a_camera_stores_it_and_starts_it(client, ctx):
    r = add(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["running"] is True and body["vendor"] == "dahua/cpplus"
    assert body["width"] == 704
    assert ctx.started["gate"].startswith("rtsp://admin:hunter2@")
    assert ctx.db.camera_by_name("gate") is not None


def test_the_add_response_never_contains_the_password(client):
    assert "hunter2" not in add(client).text


def test_a_camera_that_does_not_stream_is_refused_rather_than_stored(
        client, ctx, monkeypatch):
    monkeypatch.setattr(D, "open_stream",
                        lambda url, timeout_s=6.0: (False, 0, 0, "auth failed"))
    r = add(client)
    assert r.status_code == 400 and "auth failed" in r.json()["detail"]
    assert ctx.db.camera_by_name("gate") is None
    assert "gate" not in ctx.started


def test_a_duplicate_name_is_refused(client):
    assert add(client).status_code == 200
    assert add(client).status_code == 409


def test_a_nameless_camera_is_refused(client):
    assert add(client, name="   ").status_code == 400


def test_adding_a_camera_is_audited_without_the_password(client, ctx):
    add(client)
    rows = [r for r in ctx.db.audit_rows()
            if r["action"] == "CAMERA_CHANGE"]
    assert rows and "hunter2" not in rows[-1]["details_json"]
    assert "****" in rows[-1]["details_json"]


# ----------------------------------------------------------------- list
def test_the_list_masks_passwords_and_shows_live_state(client, ctx):
    add(client)
    rows = client.get("/api/cameras/list").json()
    assert len(rows) == 1
    assert "hunter2" not in str(rows) and "****" in rows[0]["url"]
    assert rows[0]["from"] == "database"


def test_cameras_from_the_config_file_are_listed_too(client, ctx):
    ctx.config["cameras"] = [{"name": "old", "url": "rtsp://a:b@1.2.3.4/live"}]
    rows = client.get("/api/cameras/list").json()
    names = {r["name"]: r["from"] for r in rows}
    assert names["old"] == "config.yaml"
    assert "b@" not in str(rows)


# --------------------------------------------------------- enable/delete
def test_disabling_a_camera_stops_it_without_deleting_it(client, ctx):
    cam_id = add(client).json()["id"]
    r = client.post(f"/api/cameras/{cam_id}/enabled", data={"enabled": "false"})
    assert r.status_code == 200
    assert "gate" in ctx.stopped
    assert ctx.db.get_camera(cam_id)["enabled"] == 0


def test_re_enabling_starts_it_again(client, ctx):
    cam_id = add(client).json()["id"]
    client.post(f"/api/cameras/{cam_id}/enabled", data={"enabled": "false"})
    client.post(f"/api/cameras/{cam_id}/enabled", data={"enabled": "true"})
    assert "gate" in ctx.started


def test_deleting_a_camera_stops_it_and_removes_it(client, ctx):
    cam_id = add(client).json()["id"]
    assert client.delete(f"/api/cameras/{cam_id}").status_code == 200
    assert "gate" in ctx.stopped
    assert ctx.db.camera_by_name("gate") is None


def test_deleting_something_that_is_not_there_is_a_404(client):
    assert client.delete("/api/cameras/999").status_code == 404
