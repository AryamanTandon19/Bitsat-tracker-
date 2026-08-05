"""The console's data endpoints are closed to anyone without a session.

This is the security hole the audit flagged: the old login was a JavaScript
password comparison with the credentials in the page source, and every data
endpoint behind it was open to plain curl. These tests are the proof it is
shut, and the guard against it quietly reopening.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from app import dashboard
from app.db import Database
from app.main import load_config

from .conftest import signin

# Everything that exposes a resident's data or lets someone change the system.
SENSITIVE_GET = ["/api/events", "/api/status", "/api/registry", "/api/cameras",
                 "/api/costs", "/api/audit", "/clips/1", "/stream/gate"]


@pytest.fixture()
def ctx(tmp_path):
    class Ctx:
        pass
    c = Ctx()
    c.config = load_config("config.yaml")
    c.config["dashboard"]["auth"] = {"enabled": False}   # session-only, as prod
    c.config_path = str(tmp_path / "config.yaml")
    c.db = Database(str(tmp_path / "t.db"))
    c.workers, c.pipelines, c.analyzer, c.assistant = {}, {}, None, None
    yield c
    c.db.close()


def test_the_shell_is_served_without_a_login_so_you_can_reach_the_form(ctx):
    """The page itself must load — it IS the login screen. Only the data
    behind it is protected."""
    c = TestClient(dashboard.create_app(ctx))
    assert c.get("/").status_code == 200
    assert c.get("/health").status_code == 200


def test_every_sensitive_endpoint_refuses_an_anonymous_request(ctx):
    c = TestClient(dashboard.create_app(ctx))
    for path in SENSITIVE_GET:
        assert c.get(path).status_code in (401, 403), \
            f"{path} answered an anonymous request"


def test_writing_the_registry_anonymously_is_refused(ctx):
    c = TestClient(dashboard.create_app(ctx))
    r = c.post("/api/registry", data={"plate": "XX01YY0000",
                                      "owner_name": "Intruder"})
    assert r.status_code in (401, 403)
    # and nothing was written
    assert ctx.db.list_vehicles() == []


def test_deleting_a_clip_anonymously_is_refused(ctx):
    c = TestClient(dashboard.create_app(ctx))
    r = c.post("/api/clips/1/delete", data={"name": "x", "reason": "y"})
    assert r.status_code in (401, 403)


def test_changing_thresholds_anonymously_is_refused(ctx):
    c = TestClient(dashboard.create_app(ctx))
    assert c.post("/api/assistant/apply",
                  json={"patch": {}}).status_code in (401, 403)


def test_a_signed_in_admin_can_reach_them(ctx):
    c = TestClient(dashboard.create_app(ctx))
    signin(c, ctx.db, "admin")
    assert c.get("/api/events").status_code == 200
    assert c.get("/api/registry").status_code == 200
    assert c.get("/api/audit").status_code == 200


def test_a_guard_can_view_but_not_touch_the_registry_or_the_audit(ctx):
    """A guard triages; they do not edit the vehicle registry or read the
    money and the audit trail."""
    c = TestClient(dashboard.create_app(ctx))
    signin(c, ctx.db, "guard")
    assert c.get("/api/events").status_code == 200         # triage: allowed
    assert c.post("/api/registry",
                  data={"plate": "MH01AB1234"}).status_code == 403
    assert c.get("/api/audit").status_code == 403
    assert c.get("/api/costs").status_code == 403


def test_the_basic_auth_deployment_still_works(tmp_path):
    """A site using the legacy shared-password gate must be unaffected: the
    app-level Basic check authenticates, and require() then treats it as the
    admin it already validated."""
    class Ctx:
        pass
    c = Ctx()
    c.config = load_config("config.yaml")
    c.config["dashboard"]["auth"] = {"enabled": True, "username": "admin",
                                     "password": "secret"}
    c.db = Database(str(tmp_path / "t.db"))
    c.workers, c.pipelines, c.analyzer, c.assistant = {}, {}, None, None
    cl = TestClient(dashboard.create_app(c))
    assert cl.get("/api/events").status_code == 401                  # no creds
    assert cl.get("/api/events", auth=("admin", "secret")).status_code == 200
    assert cl.get("/api/audit", auth=("admin", "secret")).status_code == 200
    c.db.close()
