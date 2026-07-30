"""Backend the operator app runs on: alert triage + notices to residents."""
from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from app import dashboard
from app.db import Database
from app.main import load_config


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


def _event(db, description="person near vehicle"):
    return db.insert_event(time.time(), "gate", "LOITERING", "MEDIUM", None,
                           [1], 0.8, description)


# ----------------------------------------------------------------- triage
def test_untriaged_event_has_no_verdict(client, ctx):
    _event(ctx.db)
    assert client.get("/api/events").json()[0]["verdict"] is None


def test_marking_a_false_alarm_sticks(client, ctx):
    eid = _event(ctx.db)
    r = client.post(f"/api/events/{eid}/feedback",
                    data={"verdict": "false_alarm", "user_name": "Guard 1"})
    assert r.status_code == 200
    row = client.get("/api/events").json()[0]
    assert row["verdict"] == "false_alarm"
    assert row["verdict_by"] == "Guard 1"


def test_a_later_verdict_overrides_an_earlier_one(client, ctx):
    eid = _event(ctx.db)
    client.post(f"/api/events/{eid}/feedback", data={"verdict": "false_alarm"})
    client.post(f"/api/events/{eid}/feedback", data={"verdict": "real"})
    assert client.get("/api/events").json()[0]["verdict"] == "real"


def test_bad_verdict_and_missing_event_are_rejected(client, ctx):
    eid = _event(ctx.db)
    assert client.post(f"/api/events/{eid}/feedback",
                       data={"verdict": "maybe"}).status_code == 400
    assert client.post("/api/events/9999/feedback",
                       data={"verdict": "real"}).status_code == 404


def test_verdict_lookup_is_one_query_for_many_events(client, ctx):
    ids = [_event(ctx.db) for _ in range(5)]
    ctx.db.insert_feedback(ids[2], "real", "Guard 2")
    rows = {r["id"]: r["verdict"] for r in client.get("/api/events").json()}
    assert rows[ids[2]] == "real"
    assert rows[ids[0]] is None


# ---------------------------------------------------------------- notices
def test_posting_a_notice_records_it(client, ctx):
    r = client.post("/api/notices", data={"title": "Water cut",
                                          "body": "Tomorrow 10am-1pm",
                                          "author": "Committee"})
    assert r.status_code == 200
    # Telegram is off in the test config, so nothing is delivered — but the
    # notice must still be on record.
    assert r.json()["recipients"] == 0
    n = client.get("/api/notices").json()[0]
    assert n["title"] == "Water cut" and n["audience"] == "all"
    assert n["sent_ts"] is not None


def test_notice_validation(client, ctx):
    assert client.post("/api/notices", data={"title": " ", "body": "x"}
                       ).status_code == 400
    assert client.post("/api/notices", data={"title": "x", "body": "y",
                                             "audience": "everyone"}
                       ).status_code == 400
    assert client.post("/api/notices", data={"title": "x", "body": "y",
                                             "audience": "flat"}
                       ).status_code == 400


def test_notice_to_a_single_flat(client, ctx):
    r = client.post("/api/notices", data={"title": "Car lights on",
                                          "body": "WB02AB1234", "audience": "flat",
                                          "flat_number": "B-402"})
    assert r.status_code == 200
    n = client.get("/api/notices").json()[0]
    assert n["audience"] == "flat" and n["flat_number"] == "B-402"


def test_notice_reaches_registered_residents(client, ctx, monkeypatch):
    from app.notify import TelegramNotifier

    ctx.db.add_vehicle("WB02AB1234", owner_name="A", flat_number="B-402",
                       telegram_chat_id="111")
    ctx.db.add_vehicle("DL8CAF5031", owner_name="B", flat_number="C-101",
                       telegram_chat_id="222")
    notifier = TelegramNotifier({"enabled": True, "bot_token": "t",
                                 "chat_ids": {"guard": "999"}}, ctx.db)
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(notifier, "_send_text",
                        lambda cid, text, keyboard=None: sent.append((cid, text)) or True)
    ctx.notifier = notifier

    client.post("/api/notices", data={"title": "Gate closed", "body": "Use side gate"})
    assert {c for c, _ in sent} == {"999", "111", "222"}
    assert sent[0][1] == "Gate closed\n\nUse side gate"

    sent.clear()
    client.post("/api/notices", data={"title": "Move your car", "body": "now",
                                      "audience": "flat", "flat_number": "B-402"})
    assert [c for c, _ in sent] == ["111"]   # only that flat, not the guard
