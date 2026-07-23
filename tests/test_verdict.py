"""AI verdict headline — pure Python, no API."""
from app.analyze import _verdict_headline


def test_empty_findings_no_verdict():
    assert _verdict_headline([]) == ""


def test_picks_highest_severity():
    finds = [
        {"time_s": 5.0, "activity": "person walks by", "severity": "LOW"},
        {"time_s": 51.0, "activity": "car driven away after tampering", "severity": "HIGH"},
        {"time_s": 12.0, "activity": "reaches into car window", "severity": "MEDIUM"},
    ]
    v = _verdict_headline(finds)
    assert v == "HIGH at 51s — car driven away after tampering"


def test_earliest_breaks_ties():
    finds = [
        {"time_s": 30.0, "activity": "b", "severity": "MEDIUM"},
        {"time_s": 12.0, "activity": "a", "severity": "MEDIUM"},
    ]
    assert _verdict_headline(finds).startswith("MEDIUM at 12s")


def test_missing_activity_has_fallback():
    assert "suspicious activity" in _verdict_headline(
        [{"time_s": 3.0, "severity": "LOW"}])
