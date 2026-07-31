"""The scoring layer — what replaced 'everything is MEDIUM'."""
from __future__ import annotations

from app.rules import (CAMERA_OFFLINE, LOITERING, UNAUTHORIZED_VEHICLE,
                       VEHICLE_CONTACT)
from app.scoring import score_event, severity_for


def test_the_same_rule_no_longer_gives_the_same_answer():
    """The whole point. A resident at their own car in the evening and a
    stranger at a stranger's car at 3am both fire LOITERING, and used to
    produce an identical MEDIUM."""
    resident = score_event(LOITERING, {"registered": True, "dwell_s": 20})
    stranger = score_event(LOITERING, {"night": True, "plate_known": False,
                                       "dwell_s": 180, "at_vehicle": True})
    assert resident.value < stranger.value
    assert resident.severity != stranger.severity
    assert resident.dismissed          # never should have been raised
    assert stranger.severity == "HIGH"


def test_a_registry_match_is_the_strongest_calming_signal():
    ctx = {"night": True, "dwell_s": 60, "at_vehicle": True}
    unknown = score_event(LOITERING, ctx)
    known = score_event(LOITERING, {**ctx, "registered": True})
    assert known.value < unknown.value - 0.3


def test_night_raises_and_is_named():
    day = score_event(LOITERING, {"dwell_s": 60})
    night = score_event(LOITERING, {"dwell_s": 60, "night": True})
    assert night.value > day.value
    assert "after dark" in night.explain()


def test_dwell_saturates():
    """Five minutes is not four times as suspicious as seventy-five seconds."""
    a = score_event(LOITERING, {"dwell_s": 60})
    b = score_event(LOITERING, {"dwell_s": 120})
    c = score_event(LOITERING, {"dwell_s": 600})
    assert a.value < b.value < c.value
    assert (c.value - b.value) < (b.value - a.value)


def test_missing_context_contributes_nothing_rather_than_guessing():
    bare = score_event(LOITERING)
    assert bare.signals == []
    assert bare.value == 0.34          # the prior, untouched


def test_score_is_clamped_to_the_unit_interval():
    hot = score_event(UNAUTHORIZED_VEHICLE,
                      {"night": True, "plate_known": False, "dwell_s": 9999,
                       "at_vehicle": True, "restricted": True, "repeat": True,
                       "confirmed_rate": 1.0})
    cold = score_event(LOITERING, {"registered": True, "false_alarm_rate": 1.0})
    assert 0.0 <= cold.value <= hot.value <= 1.0


# ------------------------------------------------- learning from the guards
def test_a_site_that_keeps_dismissing_an_alert_gets_a_quieter_one():
    ctx = {"night": True, "dwell_s": 90}
    fresh = score_event(LOITERING, ctx)
    tired = score_event(LOITERING, {**ctx, "false_alarm_rate": 0.9})
    assert tired.value < fresh.value
    assert "false alarms" in tired.explain()


def test_a_site_that_keeps_confirming_an_alert_gets_a_louder_one():
    ctx = {"night": True, "dwell_s": 90}
    fresh = score_event(LOITERING, ctx)
    real = score_event(LOITERING, {**ctx, "confirmed_rate": 0.9})
    assert real.value > fresh.value


def test_feedback_never_silences_a_camera_going_dark():
    """An offline camera is a fact about equipment, not a judgement, so no
    amount of dismissing may quieten it."""
    a = score_event(CAMERA_OFFLINE, {})
    b = score_event(CAMERA_OFFLINE, {"false_alarm_rate": 1.0, "registered": True})
    assert a.value == b.value
    assert b.severity == "HIGH"


# -------------------------------------------------------------- thresholds
def test_severity_bands():
    assert severity_for(0.10) == "DISMISS"
    assert severity_for(0.35) == "LOW"
    assert severity_for(0.50) == "MEDIUM"
    assert severity_for(0.90) == "HIGH"


def test_thresholds_are_tunable_without_code():
    cfg = {"thresholds": {"dismiss": 0.05, "low": 0.10, "medium": 0.15}}
    assert severity_for(0.20, cfg) == "HIGH"
    assert severity_for(0.20) == "DISMISS"    # same score, default thresholds


def test_weights_and_bases_are_tunable_without_code():
    cfg = {"base": {LOITERING: 0.9}, "weights": {"night": 0.0}}
    s = score_event(LOITERING, {"night": True}, cfg)
    assert s.value == 0.9              # base moved, night weight zeroed out
    assert s.severity == "HIGH"


def test_ai_review_is_gated_on_the_score():
    cheap = score_event(LOITERING, {"registered": True})
    worth_it = score_event(VEHICLE_CONTACT,
                           {"night": True, "at_vehicle": True, "dwell_s": 120})
    assert not cheap.wants_ai()
    assert worth_it.wants_ai()


# ----------------------------------------------------------- explainability
def test_every_score_can_say_why():
    s = score_event(LOITERING, {"night": True, "registered": True,
                                "dwell_s": 90, "at_vehicle": True})
    why = s.explain()
    for phrase in ("registry", "after dark", "stayed", "contact"):
        assert phrase in why
    # strongest contribution is named first
    assert why.startswith("vehicle is on the registry")


def test_signals_carry_their_own_arithmetic():
    s = score_event(LOITERING, {"night": True})
    assert [(x.name, x.delta) for x in s.signals] == [("night", 0.12)]
    assert round(0.34 + 0.12, 3) == s.value
