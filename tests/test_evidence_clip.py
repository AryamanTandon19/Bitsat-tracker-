"""The evidence rule: a clip is stored only when the alert is real.

An AI review that clears an alert, or an operator who marks it a false alarm,
discards the clip instead of keeping it for two weeks. This is the "false alarm
=> no clip" half of the product's evidence flow.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.db import Database
from app.main import AppContext


def _event(ts=1000.0):
    return SimpleNamespace(
        ts=ts, camera="gate", event_type="suspicious_activity",
        severity="MEDIUM", plate=None, track_ids=[1], confidence=0.5,
        description="desc", score=0.0, score_why="")


def _db_with_clip(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    ev = _event()
    eid = db.insert_event(ev.ts, ev.camera, ev.event_type, ev.severity,
                          ev.plate, ev.track_ids, ev.confidence, ev.description)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video")
    db.insert_clip(eid, str(clip), "", ev.ts - 180, ev.ts + 180)
    return db, eid, clip, ev


def _stub(db, *, suspicious, keep_only=True, reviewer_enabled=True):
    """A stand-in AppContext carrying only what _on_clip_ready touches."""
    notified = []
    stub = SimpleNamespace(
        db=db,
        config={"clips": {"keep_only_confirmed": keep_only}},
        take_decision=lambda eid: None,             # None => the notify path
        reviewer=SimpleNamespace(
            enabled=reviewer_enabled, max_frames=3,
            review_clip=lambda ev, eid, cam, kf: {
                "suspicious": suspicious, "summary": "s",
                "alert_text": "ALERT", "cost_inr": 0.0}),
        clip_saver=SimpleNamespace(
            keyframes_at=lambda p, t: [b"f"], keyframes=lambda p: [b"f"]),
        vlm=SimpleNamespace(enabled=False),
        notifier=SimpleNamespace(
            notify_event=lambda ev, eid, cp, desc: notified.append(eid)),
        _notified=notified)
    stub._discard_clip = AppContext._discard_clip.__get__(stub)
    stub._on_clip_ready = AppContext._on_clip_ready.__get__(stub)
    return stub


# --------------------------------------------------- the AI-review gate
def test_a_cleared_alert_discards_the_clip_and_sends_nothing(tmp_path):
    db, eid, clip, ev = _db_with_clip(tmp_path)
    stub = _stub(db, suspicious=False)
    stub._on_clip_ready(ev, eid, str(clip))
    assert not clip.exists()                        # not kept as evidence
    assert db.clip_for_event(eid) is None           # and marked deleted
    assert stub._notified == []                     # no alert went out
    db.close()


def test_a_confirmed_alert_keeps_the_clip_and_alerts(tmp_path):
    db, eid, clip, ev = _db_with_clip(tmp_path)
    stub = _stub(db, suspicious=True)
    stub._on_clip_ready(ev, eid, str(clip))
    assert clip.exists()
    assert db.clip_for_event(eid) is not None
    assert stub._notified == [eid]
    db.close()


def test_the_gate_is_off_when_keep_only_confirmed_is_false(tmp_path):
    db, eid, clip, ev = _db_with_clip(tmp_path)
    stub = _stub(db, suspicious=False, keep_only=False)
    stub._on_clip_ready(ev, eid, str(clip))
    assert clip.exists() and stub._notified == [eid]   # old behaviour preserved
    db.close()


def test_without_a_reviewer_nothing_is_discarded(tmp_path):
    """No AI verdict to act on, so the clip is kept and the alert is sent."""
    db, eid, clip, ev = _db_with_clip(tmp_path)
    stub = _stub(db, suspicious=False, reviewer_enabled=False)
    stub._on_clip_ready(ev, eid, str(clip))
    assert clip.exists() and stub._notified == [eid]
    db.close()


# ------------------------------------------------ operator discard
def test_operator_discard_removes_the_clip_once(tmp_path):
    db, eid, clip, ev = _db_with_clip(tmp_path)
    stub = SimpleNamespace(db=db)
    stub._discard_clip = AppContext._discard_clip.__get__(stub)
    discard = AppContext.discard_event_clip.__get__(stub)
    assert discard(eid, "operator marked false alarm") is True
    assert not clip.exists() and db.clip_for_event(eid) is None
    assert discard(eid, "again") is False           # nothing left to remove
    db.close()
