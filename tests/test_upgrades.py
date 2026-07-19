"""Priority-list upgrades: smart sampling, incident grouping, feedback,
Hindi alerts, incident-aware notifications."""
from types import SimpleNamespace

from app.clips import smart_sample_times
from app.db import Database
from app.notify import (TelegramNotifier, build_feedback_keyboard,
                        parse_feedback_callback)


# ---------------------------------------------------- smart frame sampling
def test_smart_sampling_is_dense_around_focus():
    times = smart_sample_times(60.0, focus_times=[30.0], max_frames=24)
    near = [t for t in times if abs(t - 30.0) <= 2.5]
    assert len(near) >= 8                     # dense cluster at the incident
    assert times == sorted(times)
    assert len(times) <= 24
    assert times[0] >= 0 and times[-1] <= 60.0


def test_smart_sampling_no_focus_still_covers():
    times = smart_sample_times(60.0, focus_times=[], max_frames=12)
    assert len(times) >= 4
    assert times[0] == 0.0 and abs(times[-1] - 60.0) < 1e-6


def test_smart_sampling_caps_at_max_frames():
    times = smart_sample_times(120.0, focus_times=[10, 40, 80, 110],
                               max_frames=24)
    assert len(times) <= 24
    # every focus moment still has nearby coverage after capping
    for f in (10, 40, 80, 110):
        assert any(abs(t - f) <= 2.5 for t in times)


# ------------------------------------------------------ incident grouping
def test_events_group_into_incidents(tmp_path):
    db = Database(str(tmp_path / "i.db"))
    e1 = db.insert_event(1000.0, "gate", "suspicious_activity", "MEDIUM",
                         None, [1], 0.5, "a")
    e2 = db.insert_event(1050.0, "gate", "loitering", "MEDIUM",
                         None, [1], 0.7, "b")             # within 120s window
    e3 = db.insert_event(1500.0, "gate", "loitering", "MEDIUM",
                         None, [2], 0.7, "c")             # much later — new
    e4 = db.insert_event(1055.0, "parking", "loitering", "MEDIUM",
                         None, [3], 0.7, "d")             # other camera — new
    rows = {r["id"]: r for r in db.recent_events()}
    assert rows[e1]["incident_id"] == e1
    assert rows[e2]["incident_id"] == e1                  # grouped
    assert rows[e3]["incident_id"] == e3                  # new incident
    assert rows[e4]["incident_id"] == e4                  # per-camera
    db.close()


# ---------------------------------------------------------- guard feedback
def test_feedback_keyboard_and_parse():
    kb = build_feedback_keyboard(42)
    btns = kb["inline_keyboard"][0]
    assert btns[0]["callback_data"] == "fb:42:correct"
    assert btns[1]["callback_data"] == "fb:42:false_alarm"
    assert parse_feedback_callback("fb:42:correct") == (42, "correct")
    assert parse_feedback_callback("fb:42:false_alarm") == (42, "false_alarm")
    assert parse_feedback_callback("garbage") is None
    assert parse_feedback_callback("fb:x:correct") is None
    assert parse_feedback_callback("fb:42:hacked") is None


def test_feedback_recorded_and_audited(tmp_path):
    db = Database(str(tmp_path / "f.db"))
    eid = db.insert_event(1000.0, "gate", "loitering", "MEDIUM", None, [1],
                          0.7, "x")
    db.insert_feedback(eid, "false_alarm", "Ramesh")
    assert db.feedback_summary() == {"false_alarm": 1}
    ok, _ = db.verify_audit_chain()
    assert ok
    actions = [r["action"] for r in db.audit_rows()]
    assert "FEEDBACK" in actions
    db.close()


# ----------------------------------------------------------- Hindi alerts
def ev(**kw):
    base = dict(ts=1751640000.0, camera="gate",
                event_type="suspicious_activity", severity="HIGH",
                description="desc", plate=None, track_ids=[1], confidence=0.5)
    base.update(kw)
    return SimpleNamespace(**base)


def test_hindi_message():
    msg = TelegramNotifier.format_message(ev(), language="hi")
    assert "संदिग्ध गतिविधि" in msg          # title in Hindi
    assert "उच्च" in msg                      # HIGH in Hindi
    assert "कैमरा: gate" in msg


def test_incident_number_and_update_marker():
    msg = TelegramNotifier.format_message(ev(), incident_id=7, is_update=True)
    assert "Incident #7" in msg
    assert "UPDATE" in msg
    fresh = TelegramNotifier.format_message(ev(), incident_id=7)
    assert "UPDATE" not in fresh
