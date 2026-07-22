"""Per-camera temporal confirmation — pure Python, no torch."""
from app.temporal import TemporalConfirmer


def conf(**kw):
    return TemporalConfirmer(**kw)


def test_warmup_never_confirms_on_first_frames():
    c = conf()
    r1 = c.update("cam1", "break_in", 0.99)
    assert not r1.confirmed and "warming up" in r1.reason
    r2 = c.update("cam1", "break_in", 0.99)
    assert not r2.confirmed          # history still not full (needs 3)


def test_two_of_three_plus_strong_gate_confirms():
    c = conf()
    c.update("cam1", "break_in", 0.76)      # suspicious
    c.update("cam1", "break_in", 0.40)      # normal
    r = c.update("cam1", "break_in", 0.91)  # suspicious + strong peak
    assert r.confirmed
    assert r.suspicious_votes == 2 and r.peak >= 0.85


def test_temporal_met_but_no_strong_peak_is_blocked():
    # exactly the live false-positive shape: 0.73/0.77/0.74 -> 3 votes, peak<0.85
    c = conf()
    c.update("cam1", "break_in", 0.73)
    c.update("cam1", "break_in", 0.77)
    r = c.update("cam1", "break_in", 0.74)
    assert r.temporal_ok and not r.strong_ok and not r.confirmed
    assert "strong gate" in r.reason


def test_only_one_suspicious_window_not_confirmed():
    c = conf()
    c.update("cam1", "vehicle", 0.20)
    c.update("cam1", "vehicle", 0.90)       # single strong spike
    r = c.update("cam1", "vehicle", 0.10)
    assert not r.confirmed and r.suspicious_votes == 1


def test_require_strong_disabled_confirms_on_votes_alone():
    c = conf(require_strong=False)
    c.update("cam1", "break_in", 0.72)
    c.update("cam1", "break_in", 0.40)
    r = c.update("cam1", "break_in", 0.74)  # 2/3 votes, peak only 0.74
    assert r.confirmed


def test_cameras_are_isolated():
    """The core bug fix: cam1 and cam2 must never share a window history."""
    c = conf()
    c.update("cam1", "break_in", 0.90)
    c.update("cam2", "break_in", 0.90)
    c.update("cam1", "break_in", 0.90)
    r2 = c.update("cam2", "break_in", 0.10)  # cam2 only has [0.90, 0.10]
    assert not r2.confirmed and len(r2.history) == 2
    r1 = c.update("cam1", "break_in", 0.90)  # cam1 has three 0.90s
    assert r1.confirmed


def test_event_types_are_isolated():
    c = conf()
    c.update("cam1", "break_in", 0.90)
    c.update("cam1", "vehicle", 0.90)
    assert c.snapshot("cam1", "break_in") == [0.90]
    assert c.snapshot("cam1", "vehicle") == [0.90]


def test_reset_scopes():
    c = conf()
    c.update("cam1", "break_in", 0.9)
    c.update("cam1", "vehicle", 0.9)
    c.update("cam2", "break_in", 0.9)
    c.reset("cam1", "break_in")              # one pair
    assert c.snapshot("cam1", "break_in") == []
    assert c.snapshot("cam1", "vehicle") == [0.9]
    c.reset("cam1")                          # whole camera
    assert c.snapshot("cam1", "vehicle") == []
    assert c.snapshot("cam2", "break_in") == [0.9]
    c.reset()                                # everything
    assert c.snapshot("cam2", "break_in") == []


def test_history_is_bounded():
    c = conf(history_size=3)
    for s in (0.1, 0.2, 0.3, 0.4, 0.5):
        r = c.update("cam1", "break_in", s)
    assert r.history == [0.3, 0.4, 0.5]


def test_required_hits_cannot_exceed_history():
    try:
        conf(history_size=2, required_hits=3)
        assert False, "should have raised"
    except ValueError:
        pass
