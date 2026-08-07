"""The resident owner app: a magic link that shows a resident only their own
alerts, clips and gate history — and the security proof that it shows them
nothing else.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from app import dashboard
from app.db import Database
from app.main import AppContext, load_config

from .conftest import signin

PLATE_A, PLATE_B = "MH12AB1234", "MH14CD5678"


@pytest.fixture()
def ctx(tmp_path):
    class Ctx:
        pass
    c = Ctx()
    c.config = load_config("config.yaml")
    c.config["dashboard"]["auth"] = {"enabled": False}
    c.config["cameras"] = []
    c.config["clips"] = {"keep_only_confirmed": True}
    c.db = Database(str(tmp_path / "t.db"))
    c.workers, c.pipelines, c.analyzer, c.assistant = {}, {}, None, None
    c.discard_event_clip = AppContext.discard_event_clip.__get__(c)
    c._discard_clip = AppContext._discard_clip.__get__(c)

    # two residents, each with a car
    c.db.add_vehicle(PLATE_A, owner_name="Asha", flat_number="A-1")
    c.db.add_vehicle(PLATE_B, owner_name="Bala", flat_number="B-2")

    # an alert about each, A's with a real clip file on disk
    c.ev_a = c.db.insert_event(1000.0, "gate", "unauthorized_vehicle", "HIGH",
                               PLATE_A, [1], 0.9, "Vehicle at gate")
    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"videobytes")
    c.db.insert_clip(c.ev_a, str(clip), "", 820.0, 1180.0)
    c.clip_a = clip
    c.ev_b = c.db.insert_event(1001.0, "gate", "unauthorized_vehicle", "HIGH",
                               PLATE_B, [2], 0.9, "Vehicle at gate")

    # a gate crossing for each
    c.db.record_gate_crossing(PLATE_A, "gate", 900.0)
    c.db.record_gate_crossing(PLATE_B, "gate", 901.0)

    c.token_a = c.db.issue_owner_token(PLATE_A)
    yield c
    c.db.close()


@pytest.fixture()
def client(ctx):
    return TestClient(dashboard.create_app(ctx))


def _login(client, token):
    return client.post("/api/owner/login", data={"token": token})


# ------------------------------------------------------------- login
def test_a_valid_link_signs_the_resident_in(client, ctx):
    r = _login(client, ctx.token_a)
    assert r.status_code == 200 and r.json()["plate"] == PLATE_A
    assert client.get("/api/owner/me").json()["flat_number"] == "A-1"


def test_a_bad_link_is_refused(client):
    assert _login(client, "not-a-real-token").status_code == 401


def test_without_a_link_nothing_is_visible(client):
    assert client.get("/api/owner/me").status_code == 401
    assert client.get("/api/owner/alerts").status_code == 401


def test_a_revoked_link_stops_working(client, ctx):
    ctx.db.revoke_owner_token(ctx.token_a)
    assert _login(client, ctx.token_a).status_code == 401


# --------------------------------------------- scoping (the security core)
def test_a_resident_sees_only_their_own_alerts(client, ctx):
    _login(client, ctx.token_a)
    rows = client.get("/api/owner/alerts").json()
    ids = {r["id"] for r in rows}
    assert ctx.ev_a in ids and ctx.ev_b not in ids


def test_a_resident_cannot_open_another_cars_clip(client, ctx):
    _login(client, ctx.token_a)
    assert client.get(f"/owner/clip/{ctx.ev_a}").status_code == 200
    assert client.get(f"/owner/clip/{ctx.ev_b}").status_code == 404


def test_a_resident_cannot_act_on_another_cars_alert(client, ctx):
    _login(client, ctx.token_a)
    r = client.post(f"/api/owner/alerts/{ctx.ev_b}/feedback",
                    data={"verdict": "false_alarm"})
    assert r.status_code == 404
    # B's event was untouched
    assert ctx.db.event_verdicts([ctx.ev_b]).get(ctx.ev_b) is None


def test_a_resident_sees_only_their_own_gate_history(client, ctx):
    _login(client, ctx.token_a)
    visits = client.get("/api/owner/visits").json()
    assert visits and all(v["plate"] == PLATE_A for v in visits)


# --------------------------------------------- feedback closes the loop
def test_marking_false_records_it_and_discards_the_clip(client, ctx):
    _login(client, ctx.token_a)
    r = client.post(f"/api/owner/alerts/{ctx.ev_a}/feedback",
                    data={"verdict": "false_alarm"})
    assert r.status_code == 200 and r.json()["clip_discarded"] is True
    assert not ctx.clip_a.exists()
    assert ctx.db.event_verdicts([ctx.ev_a])[ctx.ev_a]["verdict"] == "false_alarm"


def test_marking_real_keeps_the_clip(client, ctx):
    _login(client, ctx.token_a)
    client.post(f"/api/owner/alerts/{ctx.ev_a}/feedback",
                data={"verdict": "real"})
    assert ctx.clip_a.exists()


# --------------------------------------------- the operator mints the link
def test_operator_mints_a_working_link(ctx):
    cl = TestClient(dashboard.create_app(ctx))
    signin(cl, ctx.db, "admin")
    r = cl.post(f"/api/registry/{PLATE_A}/owner-link")
    assert r.status_code == 200
    token = r.json()["path"].split("token=")[1]
    # the freshly-minted link logs a resident in
    owner_cl = TestClient(dashboard.create_app(ctx))
    assert _login(owner_cl, token).json()["plate"] == PLATE_A


def test_a_guard_cannot_mint_owner_links(ctx):
    cl = TestClient(dashboard.create_app(ctx))
    signin(cl, ctx.db, "guard")
    assert cl.post(f"/api/registry/{PLATE_A}/owner-link").status_code == 403


# --------------------------------------------------------- the PWA shell
def test_the_app_shell_and_manifest_are_served(client):
    assert client.get("/owner").status_code == 200
    m = client.get("/owner/manifest.webmanifest")
    assert m.status_code == 200 and m.json()["start_url"] == "/owner"
    assert client.get("/owner/sw.js").status_code == 200
    assert "svg" in client.get("/owner/icon.svg").headers["content-type"]
