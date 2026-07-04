import sqlite3

from app.db import Database


def make_db(tmp_path):
    return Database(str(tmp_path / "test.db"))


def test_audit_chain_intact(tmp_path):
    db = make_db(tmp_path)
    db.append_audit("tester", "CLIP_SAVED", {"clip_id": 1})
    db.append_audit("tester", "NOTIFICATION_SENT", {"event_id": 1})
    db.append_audit("guard", "CLIP_DELETED", {"clip_id": 1, "reason": "test"})
    ok, problems = db.verify_audit_chain()
    assert ok, problems
    db.close()


def test_audit_detects_content_tampering(tmp_path):
    path = str(tmp_path / "test.db")
    db = Database(path)
    db.append_audit("tester", "CLIP_SAVED", {"clip_id": 1})
    db.append_audit("tester", "CLIP_SAVED", {"clip_id": 2})
    db.close()

    conn = sqlite3.connect(path)  # attacker edits a row directly
    conn.execute("UPDATE audit_log SET details_json = ? WHERE id = 1",
                 ('{"clip_id": 999}',))
    conn.commit()
    conn.close()

    db = Database(path)
    ok, problems = db.verify_audit_chain()
    assert not ok
    assert any("tampered" in p for p in problems)
    db.close()


def test_audit_detects_deleted_row(tmp_path):
    path = str(tmp_path / "test.db")
    db = Database(path)
    for i in range(3):
        db.append_audit("tester", "CLIP_SAVED", {"clip_id": i})
    db.close()

    conn = sqlite3.connect(path)
    conn.execute("DELETE FROM audit_log WHERE id = 2")
    conn.commit()
    conn.close()

    db = Database(path)
    ok, problems = db.verify_audit_chain()
    assert not ok
    db.close()


def test_registry_and_audit_integration(tmp_path):
    db = make_db(tmp_path)
    db.add_vehicle("WB02AB1234", "Ravi", "+91980", "A-101", actor="dashboard")
    assert db.registry_plates() == ["WB02AB1234"]
    assert db.vehicle_by_plate("WB02AB1234")["owner_name"] == "Ravi"
    assert db.remove_vehicle("WB02AB1234", actor="dashboard")
    assert db.registry_plates() == []
    actions = [r["action"] for r in db.audit_rows()]
    assert actions == ["REGISTRY_CHANGE", "REGISTRY_CHANGE"]
    ok, _ = db.verify_audit_chain()
    assert ok
    db.close()


def test_csv_seed(tmp_path):
    csv_file = tmp_path / "registry.csv"
    csv_file.write_text(
        "plate_number,owner_name,owner_phone,flat_number,telegram_chat_id\n"
        "wb02ab1234,Ravi,+91980,A-101,\n"
        "DL8CAF5031,Meera,+91981,B-204,12345\n")
    db = make_db(tmp_path)
    assert db.seed_registry_from_csv(str(csv_file)) == 2
    assert set(db.registry_plates()) == {"WB02AB1234", "DL8CAF5031"}
    # idempotent
    assert db.seed_registry_from_csv(str(csv_file)) == 0
    db.close()


def test_events_and_clips(tmp_path):
    db = make_db(tmp_path)
    eid = db.insert_event(1000.0, "gate", "unauthorized_vehicle", "HIGH",
                          "MH12CD4567", [3], 0.9, "test event")
    cid = db.insert_clip(eid, "clips/x.mp4", "clips/x.json", 990.0, 1020.0)
    evs = db.recent_events()
    assert evs[0]["clip_id"] == cid and evs[0]["plate"] == "MH12CD4567"
    db.mark_clip_deleted(cid, actor="guard", reason="privacy request")
    assert db.get_clip(cid)["deleted"] == 1
    db.insert_notification(eid, "12345", "sent")
    assert db.notifications_last_hour("gate") == 1
    ok, _ = db.verify_audit_chain()
    assert ok
    db.close()
