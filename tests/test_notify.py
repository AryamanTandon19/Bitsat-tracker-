from types import SimpleNamespace

from app.notify import TelegramNotifier


def test_message_format():
    ev = SimpleNamespace(ts=1751640000.0, camera="gate",
                         event_type="unauthorized_vehicle", severity="HIGH",
                         description="Vehicle car #3 entered with unregistered "
                                     "plate MH12CD4567",
                         plate="MH12CD4567", track_ids=[3], confidence=0.9)
    msg = TelegramNotifier.format_message(ev)
    assert "\U0001F6A8 [HIGH] Unauthorized vehicle entry" in msg
    assert "Camera: gate" in msg
    assert "IST" in msg
    assert "Plate: MH12CD4567" in msg


def test_message_format_uses_the_location_when_given():
    """When a camera has a filled-in location, the alert names the place, not
    the device — "B-Block Main Gate, facing main road", not "gate"."""
    ev = SimpleNamespace(ts=1751640000.0, camera="gate",
                         event_type="unauthorized_vehicle", severity="HIGH",
                         description="x", plate="MH12CD4567",
                         track_ids=[3], confidence=0.9)
    msg = TelegramNotifier.format_message(
        ev, location="B-Block Main Gate, facing main road")
    assert "Camera: B-Block Main Gate, facing main road" in msg
    assert "Camera: gate" not in msg


def test_message_format_falls_back_to_the_camera_name_without_a_location():
    ev = SimpleNamespace(ts=1751640000.0, camera="gate",
                         event_type="unauthorized_vehicle", severity="HIGH",
                         description="x", plate="MH12CD4567",
                         track_ids=[3], confidence=0.9)
    assert "Camera: gate" in TelegramNotifier.format_message(ev)


def test_message_format_unreadable_plate_and_vlm():
    ev = SimpleNamespace(ts=1751640000.0, camera="parking",
                         event_type="loitering", severity="MEDIUM",
                         description="fallback text", plate=None,
                         track_ids=[7], confidence=0.7)
    msg = TelegramNotifier.format_message(ev, "A person lingered beside two parked cars.")
    assert "Plate: unreadable" in msg
    assert "A person lingered beside two parked cars." in msg
    assert "fallback text" not in msg


def test_disabled_notifier_logs_skip(tmp_path):
    from app.db import Database
    db = Database(str(tmp_path / "n.db"))
    n = TelegramNotifier({"enabled": False, "chat_ids": {"guard": "111"}}, db)
    ev = SimpleNamespace(ts=1751640000.0, camera="gate",
                         event_type="loitering", severity="MEDIUM",
                         description="d", plate=None, track_ids=[1],
                         confidence=0.5)
    eid = db.insert_event(ev.ts, "gate", "loitering", "MEDIUM", None, [1], 0.5, "d")
    n.notify_event(ev, eid)  # must not raise, must not hit the network
    assert db.notifications_last_hour("gate") == 0  # skipped, not "sent"
    db.close()
