"""Slot management over HTTP, and the owner notification that makes it useful."""
from __future__ import annotations

import json
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from app import dashboard, slots as slots_mod
from app.db import Database
from app.main import load_config

from .conftest import signin

BOX = [[0, 0], [100, 0], [100, 100], [0, 100]]


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
    cl = TestClient(dashboard.create_app(ctx))
    signin(cl, ctx.db, "admin")
    return cl


def test_a_slot_can_be_drawn_and_assigned(client, ctx):
    ctx.db.add_vehicle("WB02AB1234", owner_name="A. Tandon", flat_number="B-402")
    r = client.post("/api/slots", data={"camera": "parking", "label": "B-12",
                                        "polygon": json.dumps(BOX),
                                        "plate": "wb02-ab-1234",
                                        "flat_number": "B-402"})
    assert r.status_code == 200
    slot = client.get("/api/slots").json()[0]
    assert slot["label"] == "B-12"
    assert slot["plate"] == "WB02AB1234"          # normalized on the way in
    assert slot["owner_name"] == "A. Tandon"      # joined from the registry
    assert slot["polygon"] == BOX
    assert "polygon_json" not in slot             # the raw column stays inside


def test_a_slot_needs_a_real_polygon_and_a_label(client):
    bad = [
        {"camera": "p", "label": "B-1", "polygon": "not json"},
        {"camera": "p", "label": "B-1", "polygon": json.dumps([[0, 0], [1, 1]])},
        {"camera": "p", "label": " ", "polygon": json.dumps(BOX)},
    ]
    for data in bad:
        assert client.post("/api/slots", data=data).status_code == 400


def test_slots_can_be_removed(client):
    sid = client.post("/api/slots", data={"camera": "parking", "label": "B-12",
                                          "polygon": json.dumps(BOX)}
                      ).json()["id"]
    assert client.delete(f"/api/slots/{sid}").status_code == 200
    assert client.get("/api/slots").json() == []
    assert client.delete(f"/api/slots/{sid}").status_code == 404


def test_only_an_admin_may_change_the_slot_map(ctx):
    """A guard can see the map; redrawing it is a registry change."""
    cl = TestClient(dashboard.create_app(ctx))
    signin(cl, ctx.db, "guard")
    assert cl.get("/api/slots").status_code == 200
    r = cl.post("/api/slots", data={"camera": "p", "label": "B-1",
                                    "polygon": json.dumps(BOX)})
    assert r.status_code == 403


def test_slot_changes_are_audited(client, ctx):
    client.post("/api/slots", data={"camera": "parking", "label": "B-12",
                                    "polygon": json.dumps(BOX)})
    assert "SLOT_CHANGE" in [a["action"] for a in ctx.db.audit_rows()]


def test_activity_answers_when_did_my_car_leave(client, ctx):
    sid = ctx.db.add_slot("parking", "B-12", BOX, "WB02AB1234", "B-402")
    ctx.db.record_slot_activity(sid, "occupied", "WB02AB1234", time.time() - 7200)
    ctx.db.record_slot_activity(sid, "vacated", "WB02AB1234", time.time() - 600,
                                notified=True)
    rows = client.get("/api/slots/activity?plate=wb02ab1234").json()
    assert [r["kind"] for r in rows] == ["vacated", "occupied"]
    assert rows[0]["label"] == "B-12" and rows[0]["notified"] == 1


# ----------------------------------------------------- the owner is told
def test_only_the_owner_hears_that_their_car_moved(ctx, monkeypatch):
    """A neighbour's comings and goings are nobody else's business — a system
    that broadcasts them is a surveillance complaint waiting to happen."""
    from app.notify import TelegramNotifier

    ctx.db.add_vehicle("WB02AB1234", owner_name="A. Tandon", flat_number="B-402",
                       telegram_chat_id="111")
    ctx.db.add_vehicle("WB06CD4412", owner_name="S. Roy", telegram_chat_id="222")
    n = TelegramNotifier({"enabled": True, "bot_token": "t",
                          "chat_ids": {"guard": "999"}}, ctx.db)
    sent = []
    monkeypatch.setattr(n, "_send_text",
                        lambda cid, text, kb=None: sent.append((cid, text)) or True)

    slot = slots_mod.Slot(1, "parking", "B-12", BOX, "WB02AB1234", "B-402")
    change = slots_mod.SlotChange(slots_mod.VACATED, slot, "WB02AB1234",
                                  time.time())
    assert n.notify_slot_owner(change, "14:05") is True
    assert [c for c, _ in sent] == ["111"]        # not the guard, not the neighbour
    assert "WB02AB1234 left B-12 at 14:05." in sent[0][1]
    assert "nothing to do" in sent[0][1]


def test_an_owner_with_no_telegram_is_not_an_error(ctx, monkeypatch):
    from app.notify import TelegramNotifier

    ctx.db.add_vehicle("WB02AB1234", owner_name="A. Tandon")   # no chat id
    n = TelegramNotifier({"enabled": True, "bot_token": "t"}, ctx.db)
    monkeypatch.setattr(n, "_send_text", lambda *a, **k: pytest.fail("sent"))
    slot = slots_mod.Slot(1, "parking", "B-12", BOX, "WB02AB1234")
    change = slots_mod.SlotChange(slots_mod.VACATED, slot, "WB02AB1234", 0)
    assert n.notify_slot_owner(change, "14:05") is False


def test_nothing_is_sent_when_telegram_is_off(ctx):
    from app.notify import TelegramNotifier
    n = TelegramNotifier({"enabled": False}, ctx.db)
    slot = slots_mod.Slot(1, "parking", "B-12", BOX, "WB02AB1234")
    change = slots_mod.SlotChange(slots_mod.VACATED, slot, "WB02AB1234", 0)
    assert n.notify_slot_owner(change, "14:05") is False


# ------------------------------------------------- pipeline wiring
def _pipeline(ctx, cam="parking", cfg=None):
    """A CameraPipeline with only the slot machinery on it — no camera, no
    YOLO, no threads."""
    from app.main import CameraPipeline
    p = CameraPipeline.__new__(CameraPipeline)
    p.cam_name = cam
    p.ctx = ctx
    p.slot_cfg = {"occupy_confirm_s": 10, "vacate_confirm_s": 20,
                  **(cfg or {})}
    p.slots = None
    p._slots_loaded = 0.0
    return p


class Veh:
    is_vehicle = True
    def __init__(self, tid, foot):
        self.track_id, self.foot_point = tid, foot


def test_the_pipeline_does_not_announce_cars_already_parked_at_startup(ctx,
                                                                      monkeypatch):
    ctx.db.add_vehicle("WB02AB1234", owner_name="A", telegram_chat_id="111")
    ctx.db.add_slot("parking", "B-12", BOX, "WB02AB1234", "B-402")

    class Notifier:
        sent = []
        def notify_slot_owner(self, change, when):
            self.sent.append(change.kind)
            return True
    ctx.notifier = Notifier()

    p = _pipeline(ctx)
    car, info = Veh(1, (50, 50)), {1: {"plate": "WB02AB1234"}}
    p._track_slots([car], info, ts=1000)          # boot: car already there
    assert Notifier.sent == []
    assert ctx.db.slot_activity() == []
    assert p.slots.occupant(1) == "WB02AB1234"


def test_the_pipeline_notifies_and_records_a_real_departure(ctx):
    ctx.db.add_vehicle("WB02AB1234", owner_name="A", telegram_chat_id="111")
    ctx.db.add_slot("parking", "B-12", BOX, "WB02AB1234", "B-402")

    class Notifier:
        sent = []
        def notify_slot_owner(self, change, when):
            self.sent.append((change.kind, when))
            return True
    ctx.notifier = Notifier()

    p = _pipeline(ctx)
    car, info = Veh(1, (50, 50)), {1: {"plate": "WB02AB1234"}}
    p._track_slots([car], info, ts=1000)          # primed
    p._track_slots([], {}, ts=1010)               # gone...
    p._track_slots([], {}, ts=1040)               # ...confirmed

    assert [k for k, _ in Notifier.sent] == ["vacated"]
    rows = ctx.db.slot_activity()
    assert [r["kind"] for r in rows] == ["vacated"]
    assert rows[0]["plate"] == "WB02AB1234" and rows[0]["notified"] == 1


def test_a_camera_with_no_slots_drawn_does_nothing(ctx):
    p = _pipeline(ctx, cam="gate")
    ctx.notifier = None                            # would raise if touched
    p._track_slots([Veh(1, (50, 50))], {}, ts=1000)
    p._track_slots([], {}, ts=1100)
    assert ctx.db.slot_activity() == []


def test_a_failed_notification_still_records_the_departure(ctx):
    """The record is the point; Telegram being down must not lose it."""
    ctx.db.add_slot("parking", "B-12", BOX, "WB02AB1234", "B-402")

    class Broken:
        def notify_slot_owner(self, change, when):
            raise RuntimeError("telegram is down")
    ctx.notifier = Broken()

    p = _pipeline(ctx)
    car, info = Veh(1, (50, 50)), {1: {"plate": "WB02AB1234"}}
    p._track_slots([car], info, ts=1000)
    p._track_slots([], {}, ts=1010)
    p._track_slots([], {}, ts=1040)
    rows = ctx.db.slot_activity()
    assert [r["kind"] for r in rows] == ["vacated"]
    assert rows[0]["notified"] == 0                # honest about not reaching them
