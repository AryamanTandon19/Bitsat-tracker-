"""Operator accounts, sessions and what each role is allowed to do."""
from __future__ import annotations

import time

import pytest

from app import auth
from app.db import Database
from app.users import bootstrap_admin

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from app import dashboard
from app.main import load_config

from .conftest import TEST_PASSWORD, signin


@pytest.fixture()
def ctx(tmp_path):
    class Ctx:
        pass
    c = Ctx()
    c.config = load_config("config.yaml")
    c.config["dashboard"]["auth"] = {"enabled": False}
    c.config_path = str(tmp_path / "config.yaml")
    c.db = Database(str(tmp_path / "t.db"))
    c.workers, c.pipelines, c.analyzer, c.assistant = {}, {}, None, None
    yield c
    c.db.close()


@pytest.fixture()
def client(ctx):
    return TestClient(dashboard.create_app(ctx))


# ------------------------------------------------------------- passwords
def test_a_password_is_never_stored_in_the_clear(ctx):
    ctx.db.add_user("ramesh", "Ramesh K.", "guard", "hunter2-hunter2")
    stored = ctx.db.get_user("ramesh")["pw_hash"]
    assert "hunter2" not in stored
    assert stored.startswith("scrypt$")


def test_the_same_password_hashes_differently_each_time(ctx):
    a = auth.hash_password("same password")
    b = auth.hash_password("same password")
    assert a != b                      # random salt per hash
    assert auth.verify_password("same password", a)
    assert auth.verify_password("same password", b)


def test_verify_rejects_wrong_passwords_and_junk():
    h = auth.hash_password("right")
    assert not auth.verify_password("wrong", h)
    assert not auth.verify_password("right", "not-a-hash")
    assert not auth.verify_password("right", "md5$abc$def")


def test_authenticate_checks_the_password(ctx):
    ctx.db.add_user("ramesh", "Ramesh K.", "guard", TEST_PASSWORD)
    assert ctx.db.authenticate("ramesh", TEST_PASSWORD) is not None
    assert ctx.db.authenticate("ramesh", "wrong") is None
    assert ctx.db.authenticate("nobody", TEST_PASSWORD) is None


def test_a_disabled_account_cannot_sign_in(ctx):
    ctx.db.add_user("ramesh", "Ramesh K.", "guard", TEST_PASSWORD)
    ctx.db.set_user_active("ramesh", False)
    assert ctx.db.authenticate("ramesh", TEST_PASSWORD) is None


# -------------------------------------------------------------- sessions
def test_only_the_hash_of_a_session_token_is_stored(ctx, client):
    signin(client, ctx.db, "guard")
    token = client.cookies.get(auth.SESSION_COOKIE)
    rows = ctx.db._conn.execute("SELECT token_hash FROM sessions").fetchall()
    assert token not in [r["token_hash"] for r in rows]
    assert auth.token_hash(token) in [r["token_hash"] for r in rows]


def test_a_session_expires(ctx):
    ctx.db.add_user("ramesh", "R", "guard", TEST_PASSWORD)
    _, th = auth.new_token()
    ctx.db.create_session(1, th, time.time() + 60)
    assert ctx.db.session_user(th) is not None
    assert ctx.db.session_user(th, now=time.time() + 120) is None


def test_disabling_an_account_kills_its_live_sessions(ctx, client):
    signin(client, ctx.db, "guard")
    assert client.get("/api/me").status_code == 200
    ctx.db.set_user_active("guard", False)
    assert client.get("/api/me").status_code == 401


def test_changing_a_password_signs_that_account_out(ctx, client):
    signin(client, ctx.db, "guard")
    ctx.db.set_user_password("guard", "a-brand-new-password")
    assert client.get("/api/me").status_code == 401


def test_logout_drops_the_session_server_side(ctx, client):
    signin(client, ctx.db, "guard")
    token = client.cookies.get(auth.SESSION_COOKIE)
    client.post("/api/logout")
    # even replaying the old cookie must not work
    client.cookies.set(auth.SESSION_COOKIE, token)
    assert client.get("/api/me").status_code == 401


def test_a_forged_cookie_is_rejected(client):
    client.cookies.set(auth.SESSION_COOKIE, "obviously-not-a-real-token")
    assert client.get("/api/me").status_code == 401


def test_the_session_cookie_is_not_readable_by_scripts(ctx, client):
    ctx.db.add_user("guard", "G", "guard", TEST_PASSWORD)
    r = client.post("/api/login", data={"username": "guard",
                                        "password": TEST_PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


def test_login_does_not_reveal_whether_an_account_exists(ctx, client):
    ctx.db.add_user("ramesh", "R", "guard", TEST_PASSWORD)
    a = client.post("/api/login", data={"username": "ramesh", "password": "no"})
    b = client.post("/api/login", data={"username": "ghost", "password": "no"})
    assert a.status_code == b.status_code == 401
    assert a.json()["detail"] == b.json()["detail"]


# ----------------------------------------------------------------- roles
def test_permissions_per_role():
    assert auth.can("guard", "triage") and auth.can("guard", "gate")
    assert not auth.can("guard", "notices")
    assert auth.can("committee", "notices")
    assert not auth.can("committee", "registry")
    assert auth.can("admin", "registry") and auth.can("admin", "users")
    assert not auth.can("nonsense-role", "triage")


def test_operator_endpoints_reject_anonymous_callers(client, ctx):
    eid = ctx.db.insert_event(time.time(), "gate", "LOITERING", "MEDIUM", None,
                              [1], 0.5, "x")
    for method, url in [("get", "/api/visits"), ("get", "/api/visits/open"),
                        ("get", "/api/visits/overstays"),
                        ("get", "/api/notices"), ("get", "/api/me")]:
        assert getattr(client, method)(url).status_code == 401, url
    assert client.post(f"/api/events/{eid}/feedback",
                       data={"verdict": "real"}).status_code == 401
    assert client.post("/api/notices",
                       data={"title": "x", "body": "y"}).status_code == 401


def test_a_guard_may_triage_but_not_message_residents(client, ctx):
    signin(client, ctx.db, "guard")
    eid = ctx.db.insert_event(time.time(), "gate", "LOITERING", "MEDIUM", None,
                              [1], 0.5, "x")
    assert client.post(f"/api/events/{eid}/feedback",
                       data={"verdict": "real"}).status_code == 200
    assert client.get("/api/visits").status_code == 200
    r = client.post("/api/notices", data={"title": "x", "body": "y"})
    assert r.status_code == 403
    assert "guard" in r.json()["detail"]


def test_a_committee_member_may_message_residents(client, ctx):
    signin(client, ctx.db, "committee")
    assert client.post("/api/notices",
                       data={"title": "x", "body": "y"}).status_code == 200


def test_me_reports_the_role_and_its_permissions(client, ctx):
    signin(client, ctx.db, "committee", name="C. Member")
    me = client.get("/api/me").json()
    assert me["name"] == "C. Member" and me["role"] == "committee"
    assert set(me["can"]) == {"triage", "gate", "notices"}


# ------------------------------------------------------------- bootstrap
def test_first_run_creates_one_admin_with_a_generated_password(ctx):
    created = bootstrap_admin(ctx.db)
    assert created is not None
    username, password = created
    assert username == "admin"
    assert len(password) >= 12          # generated, never a shipped default
    assert ctx.db.authenticate("admin", password)["role"] == "admin"
    # and it does not fire again on a database that already has accounts
    assert bootstrap_admin(ctx.db) is None


def test_bootstrap_passwords_differ_between_installs(tmp_path):
    pws = set()
    for i in range(3):
        db = Database(str(tmp_path / f"{i}.db"))
        pws.add(bootstrap_admin(db)[1])
        db.close()
    assert len(pws) == 3


# ----------------------------------------------------------------- audit
def test_sign_in_and_account_changes_are_audited(ctx, client):
    signin(client, ctx.db, "admin")
    actions = [a["action"] for a in ctx.db.audit_rows()]
    assert "LOGIN" in actions and "USER_ADD" in actions


def test_a_password_never_reaches_the_audit_log(ctx):
    ctx.db.add_user("ramesh", "R", "guard", "super-secret-value")
    ctx.db.set_user_password("ramesh", "another-secret-value")
    blob = "".join(a["details_json"] for a in ctx.db.audit_rows())
    assert "super-secret-value" not in blob
    assert "another-secret-value" not in blob
