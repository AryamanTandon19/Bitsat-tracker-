"""The loop that closes itself: a guard taps 'false alarm', and that alert's
geometry becomes a training hard negative — no manual labelling.
"""
from __future__ import annotations

from app.db import Database

FEATS = {"longest_near_run_s": 20.0, "straightness": 0.1, "contact_frames": 3.0}


def _db(tmp_path):
    return Database(str(tmp_path / "t.db"))


# --------------------------------------------------------- the auto-queue
def test_a_false_alarm_verdict_queues_a_hard_negative(tmp_path):
    db = _db(tmp_path)
    try:
        db.save_event_features(7, FEATS, camera="gate", night=True)
        db.insert_feedback(7, "false_alarm", "guard-1")
        rows = db.export_hard_negative_rows()
        assert len(rows) == 1
        r = rows[0]
        assert r["suspicious"] == 0 and r["hard_negative"] == 1
        assert r["split"] == "train"
        assert r["source_video"] == "operator:7"
        assert r["night"] == 1 and r["camera_id"] == "gate"
        assert r["features"] == FEATS
    finally:
        db.close()


def test_a_real_verdict_does_not_queue_anything(tmp_path):
    db = _db(tmp_path)
    try:
        db.save_event_features(7, FEATS)
        db.insert_feedback(7, "correct", "guard-1")   # Telegram "correct" = real
        db.insert_feedback(8, "real", "guard-1")       # console "real"
        assert db.export_hard_negative_rows() == []
    finally:
        db.close()


def test_an_alert_without_captured_features_is_a_silent_no_op(tmp_path):
    db = _db(tmp_path)
    try:
        # free-layer-only alert: no geometry stored, so nothing to learn from
        db.insert_feedback(99, "false_alarm", "guard-1")
        assert db.export_hard_negative_rows() == []
    finally:
        db.close()


def test_changing_the_verdict_twice_queues_once(tmp_path):
    db = _db(tmp_path)
    try:
        db.save_event_features(7, FEATS)
        db.insert_feedback(7, "false_alarm", "guard-1")
        db.insert_feedback(7, "false_alarm", "guard-2")
        assert len(db.export_hard_negative_rows()) == 1
    finally:
        db.close()


def test_queueing_is_audited(tmp_path):
    db = _db(tmp_path)
    try:
        db.save_event_features(7, FEATS)
        db.insert_feedback(7, "false_alarm", "alice")
        actions = [r["action"] for r in db.audit_rows()]
        assert "HARD_NEGATIVE_QUEUED" in actions
    finally:
        db.close()


def test_promoted_rows_are_hidden_from_the_default_export(tmp_path):
    db = _db(tmp_path)
    try:
        db.save_event_features(7, FEATS)
        db.insert_feedback(7, "false_alarm", "guard-1")
        db.mark_hard_negatives_promoted([7])
        assert db.export_hard_negative_rows() == []
        assert len(db.export_hard_negative_rows(include_promoted=True)) == 1
    finally:
        db.close()


# ----------------------------------------------------------------- the CLI
def test_from_feedback_cli_exports_then_promotes(tmp_path):
    dbp = tmp_path / "t.db"
    db = Database(str(dbp))
    db.save_event_features(7, FEATS, camera="gate")
    db.insert_feedback(7, "false_alarm", "guard-1")
    db.close()                                        # release before the CLI opens it

    from training import hardneg
    from training.extract import read_rows, write_rows

    queue = str(tmp_path / "hn.jsonl")
    rc = hardneg.main(["from-feedback", "--db", str(dbp), "--out", queue])
    assert rc == 0
    exported = read_rows(queue)
    assert len(exported) == 1 and exported[0]["hard_negative"] == 1

    # a second run exports nothing (already marked promoted in the DB)
    rc = hardneg.main(["from-feedback", "--db", str(dbp), "--out", queue])
    assert rc == 0
    assert len(read_rows(queue)) == 1

    # and they promote into a features file through the normal path
    feats = str(tmp_path / "features.jsonl")
    write_rows(feats, [])
    rc = hardneg.main(["promote", "--queue", queue, "--into", feats])
    assert rc == 0
    assert len(read_rows(feats)) == 1
