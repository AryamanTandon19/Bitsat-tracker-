"""Rising-edge incident gating: one alert per incident, not one per event.

The behaviour these defend is the difference between a guard who reads the
alerts and a guard who has muted the app.
"""
from __future__ import annotations

from app.incidents import (ESCALATE, OPEN, REMIND, REOPEN, SUSTAIN,
                           IncidentGate, severity_rank)


def gate(**cfg):
    return IncidentGate({"cooldown_s": 60, "remind_after_s": 900, **cfg})


# ------------------------------------------------------------- the basics
def test_the_first_event_alerts():
    d = gate().observe("gate", 100.0, "MEDIUM", "loitering")
    assert d.action == OPEN and d.notify is True


def test_a_sustained_incident_alerts_once():
    """The whole point. Thirty events over a minute is one situation, and a
    guard who gets thirty messages mutes the app."""
    g = gate()
    first = g.observe("gate", 100.0, "MEDIUM", "loitering")
    rest = [g.observe("gate", 100.0 + i * 2, "MEDIUM", "loitering")
            for i in range(1, 30)]
    assert first.notify is True
    assert not any(d.notify for d in rest)
    assert all(d.action == SUSTAIN for d in rest)
    assert g.stats()["alerts"] == 1 and g.stats()["events"] == 30


def test_the_suppressed_events_are_still_counted():
    """Quiet must not mean lost: the events are recorded, only the
    interruption is withheld."""
    g = gate()
    g.observe("gate", 100.0, "MEDIUM")
    for i in range(1, 6):
        d = g.observe("gate", 100.0 + i, "MEDIUM")
    assert d.suppressed_since_alert == 5
    assert d.incident_events == 6


# ------------------------------------------------------------- escalation
def test_getting_worse_always_gets_through():
    """The failure this prevents: a hard rate limit silences the camera after
    thirty MEDIUM events, and then the HIGH one never arrives."""
    g = gate()
    g.observe("gate", 100.0, "MEDIUM", "loitering")
    for i in range(1, 40):
        g.observe("gate", 100.0 + i, "MEDIUM", "loitering")
    d = g.observe("gate", 140.0, "HIGH", "possible_break_in")
    assert d.action == ESCALATE and d.notify is True
    assert "MEDIUM to HIGH" in d.reason


def test_it_does_not_escalate_twice_for_the_same_severity():
    g = gate()
    g.observe("gate", 100.0, "MEDIUM")
    assert g.observe("gate", 102.0, "HIGH").notify is True
    assert g.observe("gate", 104.0, "HIGH").notify is False


def test_dropping_back_down_is_not_an_escalation():
    g = gate()
    g.observe("gate", 100.0, "HIGH")
    d = g.observe("gate", 102.0, "LOW")
    assert d.action == SUSTAIN and d.notify is False
    assert d.peak_severity == "HIGH"          # the incident keeps its peak


def test_a_meaningful_rise_in_score_escalates():
    """Severity is coarse. The 0..1 score is the finer signal, and a jump
    from 0.40 to 0.70 within one severity band is worth interrupting for."""
    g = gate()
    g.observe("gate", 100.0, "MEDIUM", score=0.40)
    d = g.observe("gate", 104.0, "MEDIUM", score=0.70)
    assert d.action == ESCALATE and d.notify is True


def test_score_jitter_is_not_an_escalation():
    g = gate()
    g.observe("gate", 100.0, "MEDIUM", score=0.50)
    for s in (0.52, 0.48, 0.55, 0.51):
        assert g.observe("gate", 104.0, "MEDIUM", score=s).notify is False


# --------------------------------------------------------------- lifecycle
def test_a_new_incident_after_quiet_alerts_again():
    g = gate(cooldown_s=60)
    g.observe("gate", 100.0, "MEDIUM")
    d = g.observe("gate", 400.0, "MEDIUM")
    assert d.action == REOPEN and d.notify is True


def test_quiet_shorter_than_the_cooldown_is_the_same_incident():
    g = gate(cooldown_s=60)
    g.observe("gate", 100.0, "MEDIUM")
    assert g.observe("gate", 150.0, "MEDIUM").notify is False


def test_the_tracker_losing_someone_does_not_split_the_incident():
    """Measured behaviour, not hypothetical: the tracker drops a person behind
    a van for a few seconds routinely. Keying on track id would make one
    person two incidents and two alerts."""
    g = gate()
    g.observe("gate", 100.0, "MEDIUM", track_ids=[7])
    d = g.observe("gate", 112.0, "MEDIUM", track_ids=[41])   # re-acquired
    assert d.notify is False
    assert g.open_incidents()[0]["track_ids"] == [7, 41]


def test_a_very_long_incident_reminds_once():
    g = gate(remind_after_s=600)
    g.observe("gate", 0.0, "MEDIUM")
    for t in range(30, 600, 30):
        assert g.observe("gate", float(t), "MEDIUM").notify is False
    d = g.observe("gate", 620.0, "MEDIUM")
    assert d.action == REMIND and d.notify is True


def test_going_quiet_closes_the_incident():
    g = gate(cooldown_s=60)
    g.observe("gate", 100.0, "MEDIUM")
    assert g.tick(130.0) == []                 # not quiet long enough
    done = g.tick(200.0)
    assert len(done) == 1 and done[0].events == 1
    assert g.open_incidents() == []


def test_a_closed_incident_remembers_what_happened():
    g = gate(cooldown_s=60)
    g.observe("gate", 100.0, "HIGH", "possible_break_in", track_ids=[3])
    g.observe("gate", 110.0, "MEDIUM", "loitering", track_ids=[4])
    s = g.tick(300.0)[0].summary()
    assert s["peak_severity"] == "HIGH" and s["events"] == 2
    assert s["track_ids"] == [3, 4]
    assert s["event_types"] == ["loitering", "possible_break_in"]
    assert s["duration_s"] == 10.0


# ----------------------------------------------------------------- cameras
def test_cameras_are_isolated():
    """A busy gate camera must never suppress a quiet parking one."""
    g = gate()
    g.observe("gate", 100.0, "MEDIUM")
    d = g.observe("parking", 101.0, "MEDIUM")
    assert d.action == OPEN and d.notify is True


def test_one_camera_going_quiet_does_not_close_another():
    g = gate(cooldown_s=60)
    g.observe("gate", 100.0, "MEDIUM")
    g.observe("parking", 190.0, "MEDIUM")
    done = g.tick(200.0)
    assert [i.camera for i in done] == ["gate"]
    assert [i["camera"] for i in g.open_incidents()] == ["parking"]


# ------------------------------------------------------------ infrastructure
def test_a_blinded_camera_always_reaches_the_operator():
    """Not "more of the same incident" — it is the reason the operator can no
    longer see the incident at all."""
    g = gate()
    g.observe("gate", 100.0, "MEDIUM", "loitering")
    d = g.observe("gate", 102.0, "HIGH", "camera_tamper")
    assert d.notify is True


def test_a_camera_going_offline_is_never_suppressed():
    g = gate()
    g.observe("gate", 100.0, "HIGH", "camera_offline")
    for i in range(1, 6):
        assert g.observe("gate", 100.0 + i, "HIGH", "camera_offline").notify


# --------------------------------------------------------------- safeguards
def test_an_unknown_severity_is_not_treated_as_the_least_important():
    assert severity_rank("no-such-severity") == severity_rank("MEDIUM")


def test_severity_is_case_insensitive():
    assert severity_rank("high") == severity_rank("HIGH")


def test_gating_can_be_turned_off_entirely():
    """An escape hatch that behaves exactly as the system did before."""
    g = IncidentGate({"enabled": False})
    for i in range(5):
        assert g.observe("gate", 100.0 + i, "MEDIUM").notify is True


def test_the_stats_say_how_many_interruptions_were_saved():
    g = gate()
    g.observe("gate", 100.0, "MEDIUM")
    for i in range(1, 25):
        g.observe("gate", 100.0 + i, "MEDIUM")
    s = g.stats()
    assert s["events"] == 25 and s["alerts"] == 1 and s["suppressed"] == 24


def test_the_break_in_gets_through_where_a_rate_cap_would_have_silenced_it():
    """The scenario this whole module exists for, measured.

    Someone works at a car for four minutes. The rules layer emits ~40 MEDIUM
    events (the per-track debounce does not hold, because track ids churn as
    the tracker loses and re-acquires them). Then the break-in fires.

    With only `max_notifications_per_hour = 10` in the way, the first ten
    MEDIUM events use the entire budget and the HIGH break-in — event 41 — is
    dropped. The one message that mattered is the one nobody gets.
    """
    cap = 10
    stream = [(i * 6.0, "MEDIUM", "loitering", 0.45) for i in range(40)]
    stream.append((240.0, "HIGH", "possible_break_in", 0.88))

    # what a bare hourly cap does
    sent_under_cap = []
    for ts, sev, et, _sc in stream:
        if len(sent_under_cap) >= cap:
            continue
        sent_under_cap.append(sev)
    assert "HIGH" not in sent_under_cap, "the cap was supposed to silence it"

    # what the gate does
    g = gate()
    alerted = [(sev, d.action) for ts, sev, et, sc in stream
               for d in [g.observe("gate", ts, sev, et, score=sc)]
               if d.notify]
    assert [a[0] for a in alerted] == ["MEDIUM", "HIGH"]
    assert alerted[1][1] == ESCALATE
    assert g.stats()["suppressed"] == 39


def test_resetting_a_camera_forgets_only_that_camera():
    g = gate()
    g.observe("gate", 100.0, "MEDIUM")
    g.observe("parking", 100.0, "MEDIUM")
    g.reset("gate")
    assert [i["camera"] for i in g.open_incidents()] == ["parking"]
    assert g.observe("gate", 101.0, "MEDIUM").action == OPEN
