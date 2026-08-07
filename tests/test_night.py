"""Night handling: training must brighten dark frames exactly as production
does, or the model learns from footage it will never actually see after dark.
"""
from __future__ import annotations

import numpy as np
import pytest

from training import extract as E


class FakeCap:
    def __init__(self, frames):
        self.frames, self.i = frames, 0

    def isOpened(self):
        return True

    def get(self, _prop):
        return 10.0

    def read(self):
        if self.i < len(self.frames):
            f = self.frames[self.i]
            self.i += 1
            return True, f
        return False, None

    def release(self):
        pass


class FakeDetector:
    def __init__(self):
        self.seen = []

    def track(self, frame):
        self.seen.append(frame)
        return []


def _wire(monkeypatch, frames):
    import cv2
    monkeypatch.setattr(cv2, "VideoCapture", lambda *_a, **_k: FakeCap(frames))
    # a marker so we can tell an enhanced frame from a raw one
    monkeypatch.setattr("app.enhance.enhance_frame",
                        lambda frame, mode: ("ENHANCED", mode))


def test_extraction_brightens_frames_before_detection(monkeypatch):
    frames = [np.zeros((4, 4, 3), np.uint8) for _ in range(3)]
    _wire(monkeypatch, frames)
    det = FakeDetector()
    E.track_clip("x.mp4", det, stride=1, low_light="auto")
    assert det.seen and all(f == ("ENHANCED", "auto") for f in det.seen)


def test_off_means_the_raw_frame_reaches_the_detector(monkeypatch):
    frames = [np.zeros((4, 4, 3), np.uint8) for _ in range(2)]
    _wire(monkeypatch, frames)
    det = FakeDetector()
    E.track_clip("x.mp4", det, stride=1, low_light="off")
    assert det.seen and all(isinstance(f, np.ndarray) for f in det.seen)


# --------------------------------------------------- the day/night report
class FakeVerdict:
    def __init__(self, suspicious):
        self.suspicious = suspicious


class FakeBrain:
    """Flags a row iff its features say so — enough to exercise the report."""
    def score(self, feats):
        return FakeVerdict(bool(feats.get("flag")))


def _row(cid, night, suspicious, flag):
    return {"clip_id": cid, "duration_s": 10.0, "night": night,
            "suspicious": suspicious, "features": {"flag": flag}}


def test_night_breakdown_prints_both_when_both_present(capsys):
    from training.brain_train import _print_night_breakdown
    rows = [_row("d1", 0, 0, False), _row("d2", 0, 1, True),
            _row("n1", 1, 0, True), _row("n2", 1, 1, True)]
    _print_night_breakdown(rows, FakeBrain())
    out = capsys.readouterr().out
    assert "day" in out and "night" in out


def test_night_breakdown_is_silent_without_both(capsys):
    from training.brain_train import _print_night_breakdown
    _print_night_breakdown([_row("d1", 0, 0, False)], FakeBrain())
    assert capsys.readouterr().out == ""
