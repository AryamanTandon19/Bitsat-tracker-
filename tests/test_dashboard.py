"""Dashboard API end-to-end: registry management, clip deletion flow, audit."""
import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from app import dashboard
from app.db import Database
from app.main import load_config


AUTH = ("admin", "testpass")


@pytest.fixture
def client(tmp_path):
    class Ctx:
        pass
    ctx = Ctx()
    ctx.config = load_config(os.path.join(os.path.dirname(__file__), "..",
                                          "config.yaml"))
    ctx.config["dashboard"]["auth"] = {"enabled": True, "username": AUTH[0],
                                       "password": AUTH[1]}
    ctx.db = Database(str(tmp_path / "t.db"))
    ctx.workers = {}
    ctx.pipelines = {}
    c = TestClient(dashboard.create_app(ctx))
    c.auth = AUTH
    yield c, ctx.db, tmp_path
    ctx.db.close()


def test_auth_required(client):
    c, _, _ = client
    c.auth = None
    assert c.get("/").status_code == 401
    assert c.get("/api/events").status_code == 401
    assert c.get("/", auth=("admin", "wrong")).status_code == 401
    assert c.get("/", auth=AUTH).status_code == 200


def test_index_and_registry(client):
    c, db, _ = client
    assert c.get("/").status_code == 200
    r = c.post("/api/registry", data={"plate": "mh 12 cd 4567",
                                      "owner_name": "Test"})
    assert r.json() == {"ok": True, "plate": "MH12CD4567"}
    assert c.get("/api/registry").json()[0]["plate_number"] == "MH12CD4567"
    assert c.delete("/api/registry/MH12CD4567").json() == {"ok": True}
    assert c.delete("/api/registry/MH12CD4567").status_code == 404


def test_clip_delete_requires_name_and_reason(client):
    c, db, tmp = client
    eid = db.insert_event(1000.0, "gate", "loitering", "MEDIUM", None,
                          [1], 0.7, "x")
    p = tmp / "fake.mp4"
    p.write_bytes(b"x")
    cid = db.insert_clip(eid, str(p), "", 990, 1020)

    # whitespace-only name rejected, file untouched
    r = c.post(f"/api/clips/{cid}/delete", data={"name": " ", "reason": "r"})
    assert r.status_code in (400, 422)
    assert p.exists()

    # proper deletion: file gone, audit row permanent, chain intact
    r = c.post(f"/api/clips/{cid}/delete",
               data={"name": "Guard", "reason": "privacy request"})
    assert r.json() == {"ok": True}
    assert not p.exists()
    assert c.get(f"/clips/{cid}").status_code == 404
    assert c.get("/api/events").json()[0]["clip_deleted"] == 1
    ok, problems = db.verify_audit_chain()
    assert ok, problems
    audit = c.get("/api/audit").json()
    assert any(a["action"] == "CLIP_DELETED" for a in audit)

    # double delete rejected
    r = c.post(f"/api/clips/{cid}/delete",
               data={"name": "Guard", "reason": "again"})
    assert r.status_code == 404
