"""Evidence fusion decision gate — pure Python, no torch."""
from app.fusion import (AI_REVIEW, CONFIRMED_INCIDENT, NORMAL, WATCH, Evidence,
                        fuse)


def test_nothing_is_normal():
    assert fuse(Evidence()).decision == NORMAL


def test_specialist_alone_is_watch_not_confirmed():
    """Domain-shift guard: a confirmed model score with no grounding -> WATCH."""
    ev = Evidence(specialist_confirmed={"break_in"},
                  specialist_scores={"break_in": 0.9})
    r = fuse(ev)
    assert r.decision == WATCH and not r.is_alert
    assert any("domain-shift" in x for x in r.rejected)


def test_specialist_plus_interaction_confirms():
    ev = Evidence(specialist_confirmed={"vehicle"},
                  specialist_scores={"vehicle": 0.9},
                  relationship=True)
    r = fuse(ev)
    assert r.decision == CONFIRMED_INCIDENT and r.is_alert


def test_specialist_plus_action_confirms():
    ev = Evidence(specialist_confirmed={"break_in"},
                  pose_motion_signals={"pose_arm_swing"})
    assert fuse(ev).decision == CONFIRMED_INCIDENT


def test_heuristics_alone_never_confirm():
    """No single heuristic may declare a critical incident."""
    ev = Evidence(pose_motion_signals={"pose_crouching"},
                  context_signals={"person_near_vehicle"})
    assert fuse(ev).decision in (WATCH, AI_REVIEW)
    assert fuse(ev).decision != CONFIRMED_INCIDENT


def test_grounded_action_without_model_is_ai_review():
    ev = Evidence(pose_motion_signals={"pose_arm_swing"}, relationship=True)
    assert fuse(ev).decision == AI_REVIEW


def test_context_only_is_watch():
    ev = Evidence(context_signals={"person_near_vehicle", "person_at_night"})
    assert fuse(ev).decision == WATCH


def test_state_chain_with_action_confirms():
    """Theft chain (remembered sequence) + real interaction -> confirmed."""
    ev = Evidence(state_chain="vehicle_departure_after_activity",
                  pose_motion_signals={"pose_reaching"})
    assert fuse(ev).decision == CONFIRMED_INCIDENT


def test_bare_state_chain_is_ai_review():
    ev = Evidence(state_chain="vehicle_departure_after_activity")
    assert fuse(ev).decision == AI_REVIEW


def test_strong_contradiction_caps_at_watch():
    ev = Evidence(specialist_confirmed={"vehicle"}, relationship=True,
                  contradictions={"recognized_owner"})
    r = fuse(ev)
    assert r.decision == WATCH
    assert any("strong contradiction" in x for x in r.rejected)


def test_soft_contradiction_caps_at_ai_review():
    ev = Evidence(specialist_confirmed={"break_in"},
                  pose_motion_signals={"pose_arm_swing"},
                  contradictions={"daytime_routine"})
    r = fuse(ev)
    assert r.decision == AI_REVIEW
    assert any("soft contradiction" in x for x in r.rejected)


def test_specialist_scored_but_not_confirmed_is_downgraded():
    ev = Evidence(specialist_scores={"break_in": 0.6},   # scored, not confirmed
                  relationship=True,
                  pose_motion_signals={"pose_reaching"})
    r = fuse(ev)
    # grounded action -> AI_REVIEW, and it notes the model didn't confirm
    assert r.decision == AI_REVIEW
    assert any("did not temporally confirm" in x for x in r.rejected)


def test_confidence_increases_with_corroboration():
    weak = fuse(Evidence(context_signals={"person_near_vehicle"}))
    strong = fuse(Evidence(specialist_confirmed={"vehicle"},
                           specialist_scores={"vehicle": 0.95},
                           relationship=True,
                           pose_motion_signals={"pose_reaching"}))
    assert strong.confidence > weak.confidence
    assert strong.is_alert


def test_result_exposes_reasons_for_audit():
    ev = Evidence(specialist_confirmed={"vehicle"}, relationship=True)
    r = fuse(ev)
    assert r.accepted            # non-empty: what supported it
    assert r.calibration_note    # honesty note always present
