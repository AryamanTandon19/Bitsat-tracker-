"""Deleting old footage by itself, so the box never fills up and never hoards.

Two failures this prevents, one operational and one legal:

  A camera recording around the clock fills any disk. On the cheap box this is
  meant to run on, that is days, not months, and a full disk does not degrade
  gracefully — writes fail, clips stop saving, and the whole thing stops being
  a security system precisely when something happens. The test has to survive
  running for its whole duration, unattended, which means old clips have to go.

  Under India's DPDP Act a society holding residents' video indefinitely, with
  no schedule and no way to erase it, is a compliance problem regardless of the
  disk. A stated retention window that enforces itself is the honest answer.

So a janitor runs on a timer and does two things, in order:

  1. delete anything older than the retention window, always;
  2. if the disk is still tighter than the floor, delete the oldest clips that
     remain until it is not — age first, because the oldest footage is the
     least likely to still matter.

It only ever removes CLIP FILES. Events, the audit chain and the vehicle
registry are small and are the record of what happened; they are never touched
here. And it removes through the same audited path a person uses, so a deletion
by the janitor is as traceable as a deletion by hand.

The decisions are pure functions of (clips, now, disk free) so they are tested
without a disk or a clock; the thread is a thin loop on top.
"""
from __future__ import annotations

import logging
import shutil
import threading
import time
from pathlib import Path

log = logging.getLogger("watchdog")

DAY_S = 86400.0


def expired(clips, now: float, keep_days: float) -> list:
    """Clip rows older than the window. `clips` are dicts with created_at."""
    if keep_days <= 0:
        return []
    cutoff = now - keep_days * DAY_S
    return [c for c in clips
            if not c.get("deleted") and (c.get("created_at") or 0) < cutoff]


def overflow(clips, free_gb: float, min_free_gb: float,
             already: set | None = None) -> list:
    """Oldest surviving clips to delete until the disk clears the floor.

    Returns nothing when there is room. This is a fallback for a burst of
    activity that fills the disk faster than the age window would, not the
    normal path — most days `expired` does all the work.
    """
    if free_gb >= min_free_gb:
        return []
    already = already or set()
    live = sorted((c for c in clips
                   if not c.get("deleted") and c["id"] not in already),
                  key=lambda c: c.get("created_at") or 0)
    # We cannot know each file's size without stat-ing it, and the point is to
    # act before that is cheap to do reliably, so free up a fixed slice of the
    # oldest — a quarter of what remains — and let the next tick reassess.
    take = max(1, len(live) // 4)
    return live[:take]


def clip_paths(clip: dict) -> list:
    out = []
    for key in ("path", "sidecar_path"):
        p = clip.get(key)
        if p:
            out.append(Path(p))
    return out


def free_gb(path: str) -> float:
    try:
        return shutil.disk_usage(path).free / 1024 ** 3
    except OSError:
        return float("inf")            # cannot tell: do not delete on this basis


def sweep(db, cfg: dict, disk_path: str = ".", now: float | None = None,
          deleter=None) -> dict:
    """One pass. Returns a summary of what it removed and why.

    `deleter(db, clip_id, actor, reason)` is injected so the whole policy is
    testable without touching a file; it defaults to the audited deletion the
    console uses.
    """
    now = time.time() if now is None else now
    keep_days = float(cfg.get("clip_days", 14))
    min_free = float(cfg.get("min_free_gb", 2.0))
    if deleter is None:
        from .clips import delete_clip_file
        deleter = delete_clip_file

    clips = [c for c in db.all_clips() if not c.get("deleted")]

    removed_age, removed_space, done = 0, 0, set()
    for c in expired(clips, now, keep_days):
        if deleter(db, c["id"], "retention",
                   f"older than {keep_days:.0f} days"):
            removed_age += 1
            done.add(c["id"])

    fg = free_gb(disk_path)
    if fg < min_free:
        for c in overflow(clips, fg, min_free, already=done):
            if deleter(db, c["id"], "retention",
                       f"disk below {min_free:.1f} GB free"):
                removed_space += 1
                done.add(c["id"])
        fg = free_gb(disk_path)

    summary = {"removed_by_age": removed_age,
               "removed_for_space": removed_space,
               "free_gb": round(fg, 2), "keep_days": keep_days,
               "min_free_gb": min_free, "at": now}
    if removed_age or removed_space:
        log.info("retention: removed %d clip(s) past %d days and %d for space; "
                 "%.1f GB free", removed_age, keep_days, removed_space, fg)
    return summary


class Janitor(threading.Thread):
    """Runs `sweep` on a timer. Daemon, so it never holds the process open."""

    def __init__(self, db, cfg: dict, disk_path: str = "."):
        super().__init__(name="retention", daemon=True)
        self.db = db
        self.cfg = cfg or {}
        self.disk_path = disk_path
        self.interval = float(self.cfg.get("interval_s", 3600))
        self.last: dict = {}
        self._stop = threading.Event()

    def run(self):
        # a short wait first, so startup is not competing with a disk sweep
        self._stop.wait(min(60.0, self.interval))
        while not self._stop.is_set():
            try:
                self.last = sweep(self.db, self.cfg, self.disk_path)
            except Exception:                           # noqa: BLE001
                log.exception("retention sweep failed")
            self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()
