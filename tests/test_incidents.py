"""Incident merging — collapse per-frame events into incidents. Pure Python."""
from app.analyze import _merge_incidents


def ev(t, sev="MEDIUM", tids=None, desc="", etype="suspicious_activity"):
    return {"video_time_s": t, "severity": sev, "track_ids": tids or [],
            "description": desc, "event_type": etype}


def test_empty():
    assert _merge_incidents([]) == []


def test_close_events_merge_into_one_incident():
    evs = [ev(10.0, "MEDIUM", [7]), ev(12.0, "HIGH", [7]), ev(14.0, "MEDIUM", [8])]
    inc = _merge_incidents(evs, gap_s=20)
    assert len(inc) == 1
    i = inc[0]
    assert i["start_s"] == 10.0 and i["end_s"] == 14.0
    assert i["severity"] == "HIGH"           # peak severity wins
    assert i["track_ids"] == [7, 8]          # union
    assert i["count"] == 3


def test_far_apart_events_are_separate_incidents():
    evs = [ev(5.0, "MEDIUM"), ev(60.0, "HIGH")]
    inc = _merge_incidents(evs, gap_s=20)
    assert len(inc) == 2
    assert inc[0]["start_s"] == 5.0 and inc[1]["start_s"] == 60.0


def test_headline_is_the_strongest_events_description():
    evs = [ev(10.0, "MEDIUM", desc="person near vehicle"),
           ev(12.0, "HIGH", desc="POSSIBLE VEHICLE THEFT: drove away")]
    inc = _merge_incidents(evs, gap_s=20)
    assert inc[0]["summary"] == "POSSIBLE VEHICLE THEFT: drove away"


def test_unsorted_input_is_handled():
    inc = _merge_incidents([ev(14.0), ev(10.0), ev(12.0)], gap_s=20)
    assert len(inc) == 1 and inc[0]["start_s"] == 10.0
