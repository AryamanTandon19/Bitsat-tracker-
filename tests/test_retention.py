"""Deleting old footage by itself: the box never fills up and never hoards.

The policy is pure functions of (clips, now, disk free), so these test the
decisions without a disk or a clock.
"""
from __future__ import annotations

from app import retention as R

DAY = R.DAY_S


def clip(cid, age_days, deleted=False):
    return {"id": cid, "path": f"/c/{cid}.mp4", "sidecar_path": None,
            "created_at": 1_000_000.0 - age_days * DAY, "deleted": deleted}


NOW = 1_000_000.0


# ------------------------------------------------------------------- age
def test_clips_older_than_the_window_are_expired():
    clips = [clip(1, 20), clip(2, 5), clip(3, 15)]
    gone = {c["id"] for c in R.expired(clips, NOW, keep_days=14)}
    assert gone == {1, 3}


def test_a_fresh_clip_is_kept():
    assert R.expired([clip(1, 1)], NOW, keep_days=14) == []


def test_an_already_deleted_clip_is_not_expired_again():
    assert R.expired([clip(1, 30, deleted=True)], NOW, keep_days=14) == []


def test_a_zero_window_keeps_everything_rather_than_deleting_all():
    """A misconfigured 0 must not be read as 'delete every clip now'."""
    assert R.expired([clip(1, 100)], NOW, keep_days=0) == []


# ----------------------------------------------------------------- space
def test_nothing_is_removed_for_space_when_there_is_room():
    clips = [clip(i, i) for i in range(1, 20)]
    assert R.overflow(clips, free_gb=10.0, min_free_gb=2.0) == []


def test_the_oldest_clips_go_first_when_the_disk_is_tight():
    clips = [clip(1, 30), clip(2, 20), clip(3, 10), clip(4, 1)]
    gone = R.overflow(clips, free_gb=0.5, min_free_gb=2.0)
    assert gone and gone[0]["id"] == 1          # oldest first


def test_clips_already_removed_this_pass_are_not_chosen_again():
    clips = [clip(1, 30), clip(2, 20)]
    gone = R.overflow(clips, free_gb=0.5, min_free_gb=2.0, already={1})
    assert all(c["id"] != 1 for c in gone)


# -------------------------------------------------------------- the sweep
class FakeDB:
    def __init__(self, clips):
        self._clips = {c["id"]: dict(c) for c in clips}

    def all_clips(self, include_deleted=False):
        return [dict(c) for c in self._clips.values()
                if include_deleted or not c["deleted"]]


def deleter_for(db, log):
    def delete(_db, cid, actor, reason):
        db._clips[cid]["deleted"] = True
        log.append((cid, actor, reason))
        return True
    return delete


def test_a_sweep_removes_old_clips_through_the_audited_path(monkeypatch):
    db = FakeDB([clip(1, 30), clip(2, 2)])
    log = []
    monkeypatch.setattr(R, "free_gb", lambda p: 100.0)   # plenty of room
    summary = R.sweep(db, {"clip_days": 14}, now=NOW,
                      deleter=deleter_for(db, log))
    assert summary["removed_by_age"] == 1
    assert log == [(1, "retention", "older than 14 days")]
    assert db._clips[2]["deleted"] is False


def test_a_full_disk_triggers_a_space_sweep_after_the_age_sweep(monkeypatch):
    db = FakeDB([clip(i, i) for i in range(1, 9)])       # ages 1..8 days
    log = []
    monkeypatch.setattr(R, "free_gb", lambda p: 0.5)     # below the floor
    summary = R.sweep(db, {"clip_days": 30, "min_free_gb": 2.0}, now=NOW,
                      deleter=deleter_for(db, log))
    assert summary["removed_by_age"] == 0                # none old enough
    assert summary["removed_for_space"] >= 1             # but space was tight
    # and it took the oldest
    assert log[0][0] == 1 and "space" in log[0][2] or "disk" in log[0][2]


def test_a_sweep_with_room_and_nothing_old_removes_nothing(monkeypatch):
    db = FakeDB([clip(1, 2), clip(2, 3)])
    monkeypatch.setattr(R, "free_gb", lambda p: 100.0)
    summary = R.sweep(db, {"clip_days": 14, "min_free_gb": 2.0}, now=NOW,
                      deleter=deleter_for(db, []))
    assert summary["removed_by_age"] == 0
    assert summary["removed_for_space"] == 0


def test_an_unreadable_disk_never_triggers_a_space_deletion(monkeypatch):
    """If the free space cannot be read it returns infinity, so the space
    sweep never fires on a bad reading and deletes footage it should not."""
    db = FakeDB([clip(1, 2)])
    monkeypatch.setattr(R, "shutil", type("S", (), {
        "disk_usage": staticmethod(lambda p: (_ for _ in ()).throw(OSError()))}))
    assert R.free_gb("/nowhere") == float("inf")
