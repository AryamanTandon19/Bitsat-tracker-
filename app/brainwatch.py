"""Watch the brain model file and hot-reload it when it changes.

The self-improve loop (training/self_improve.py) retrains the brain in a
separate process and atomically replaces the model file. This watcher is how the
*running* system notices: it polls the file's modification time and, when it
moves, calls back to reload the live brain. That keeps training and serving
decoupled — the retrain can run on a schedule, in another process, even on
another machine that drops the file in — and the cameras pick it up on their own
within a minute, no restart.

The polling loop is a thread, but the decision ("has it changed?") is a pure
function of the last two mtimes, so it is tested without a clock or a disk.
"""
from __future__ import annotations

import logging
import os
import threading

log = logging.getLogger(__name__)


def changed(prev: float | None, current: float | None) -> bool:
    """True when the file has meaningfully changed since we last looked.

    Fires on a first appearance (None -> a time) and on any later modification,
    but never on 'still absent' (None -> None) or 'unchanged'.
    """
    if current is None:
        return False
    return prev is None or current != prev


class ModelWatcher(threading.Thread):
    def __init__(self, path: str, on_change, interval_s: float = 60.0,
                 mtime_fn=None):
        super().__init__(name="brain-watch", daemon=True)
        self.path = path
        self.on_change = on_change
        self.interval_s = float(interval_s)
        self._mtime_fn = mtime_fn or self._disk_mtime
        # baseline to the current state so we only fire on changes AFTER start
        # (the model present at boot was already loaded by AppContext).
        self._last = self._mtime_fn(path)
        self._stop = threading.Event()

    @staticmethod
    def _disk_mtime(path: str):
        try:
            return os.path.getmtime(path)
        except OSError:
            return None

    def check_once(self) -> bool:
        """Poll once; reload if changed. Returns whether it reloaded. Separated
        from the loop so a test can drive it a tick at a time."""
        current = self._mtime_fn(self.path)
        if changed(self._last, current):
            self._last = current
            try:
                self.on_change()
            except Exception:                            # noqa: BLE001
                log.exception("brain reload callback failed")
            return True
        self._last = current
        return False

    def run(self) -> None:
        while not self._stop.wait(self.interval_s):
            self.check_once()

    def stop(self) -> None:
        self._stop.set()
