"""The behaviour brain: it separates ordinary from suspicious, explains itself,
survives a save/load, and — the load-bearing property — never pages a human on
its own score.
"""
from __future__ import annotations

import pytest

pytest.importorskip("sklearn")
pytest.importorskip("joblib")

from app.brain import NAME, BehaviorBrain
from app.fusion import CONFIRMED_INCIDENT, WATCH, Evidence, fuse
from training import synth


@pytest.fixture(scope="module")
def data():
    rows = synth.dataset(per_scenario=50, seed=0)
    by: dict = {"train": [], "val": [], "test": []}
    for r in rows:
        by[r["split"]].append(r)
    return by


@pytest.fixture(scope="module")
def brain(data):
    b = BehaviorBrain()
    b.fit(data["train"], data["val"], synthetic=True)
    return b


def _one(data, label):
    return next(r for r in data["test"] if r["label"] == label)


# ------------------------------------------------------------- separation
def test_it_flags_the_suspicious_scenarios(brain, data):
    for label in ("loiter", "circle", "break_in"):
        v = brain.score(_one(data, label)["features"])
        assert v.suspicious, f"{label} should be suspicious ({v.score})"


def test_it_stays_quiet_on_ordinary_life(brain, data):
    for label in ("walk_past", "delivery", "own_car"):
        v = brain.score(_one(data, label)["features"])
        assert not v.suspicious, f"{label} should be normal ({v.score})"


def test_the_fire_hydrant_is_not_an_alert(brain, data):
    """A static object misread as a person, near the car the whole clip. This
    is THE false alarm the product exists to not send."""
    v = brain.score(_one(data, "furniture")["features"])
    assert not v.suspicious
    assert v.score < 0.5


def test_recall_and_false_alarms_on_the_held_out_split(brain, data):
    tp = fp = fn = tn = 0
    for r in data["test"]:
        pred = brain.score(r["features"]).suspicious
        truth = bool(r["suspicious"])
        tp += pred and truth
        fp += pred and not truth
        fn += (not pred) and truth
        tn += (not pred) and not truth
    recall = tp / (tp + fn)
    fpr = fp / (fp + tn)
    # synthetic, clean data: the machinery must clear a low bar or it is broken
    assert recall >= 0.9
    assert fpr <= 0.1


# ----------------------------------------------------------- explainability
def test_a_verdict_carries_human_reasons(brain, data):
    v = brain.score(_one(data, "break_in")["features"])
    assert v.reasons and isinstance(v.reasons[0], str)
    assert v.reasons[0] != "brain not trained"


# --------------------------------------------------------------- the gate
def test_the_gate_is_an_interpretable_floor(brain, data):
    """Even if the model score were low, the plain-language gate (lingered,
    touched, did not walk past) can still mark a break-in suspicious."""
    v = brain.score(_one(data, "break_in")["features"])
    # break-in trips the gate in the synthetic set; the floor holds
    assert v.gate or v.score >= v.threshold


# ------------------------------------------------------------- persistence
def test_save_and_load_round_trip(brain, data, tmp_path):
    p = str(tmp_path / "brain.joblib")
    brain.save(p)
    loaded = BehaviorBrain.load(p)
    assert loaded is not None and loaded.ready
    for label in ("break_in", "furniture", "walk_past"):
        f = _one(data, label)["features"]
        assert loaded.score(f).score == pytest.approx(brain.score(f).score, abs=1e-9)


def test_loading_a_missing_file_returns_none():
    assert BehaviorBrain.load("does/not/exist.joblib") is None


def test_an_untrained_brain_scores_nothing_rather_than_crashing():
    b = BehaviorBrain()
    assert not b.ready
    v = b.score({})
    assert v.score == 0.0 and not v.suspicious


# ------------------------------------------------- the no-single-model rule
def test_the_brain_alone_never_confirms_an_incident(brain, data):
    """Its proximity is the same evidence its score is built from, so it must
    not self-corroborate. Fusion caps a lone confirmed brain at WATCH."""
    ev, _ = brain.to_evidence(_one(data, "break_in")["features"], "cam",
                              confirmed=True)
    assert fuse(ev).decision == WATCH


def test_the_brain_confirms_only_with_independent_corroboration(brain, data):
    ev = Evidence(camera_id="cam", pose_motion_signals={"pose_crouching"},
                  relationship=True)
    brain.contribute(ev, _one(data, "break_in")["features"], confirmed=True)
    assert ev.specialist_scores[NAME] > 0
    assert fuse(ev).decision == CONFIRMED_INCIDENT


def test_a_registered_vehicle_downgrades_the_brains_own_evidence(brain, data):
    feats = dict(_one(data, "break_in")["features"])
    feats["vehicle_registered"] = 1.0
    ev = Evidence(camera_id="cam", pose_motion_signals={"pose_crouching"},
                  relationship=True)
    brain.contribute(ev, feats, confirmed=True)
    assert "registered_plate" in ev.contradictions
    assert fuse(ev).decision == WATCH


# --------------------------------------------- the negatives-only regime
def test_the_training_cli_runs_end_to_end(tmp_path):
    """One command: generate synthetic motion, train, evaluate, save a model."""
    from training.brain_train import main

    out = str(tmp_path / "brain.joblib")
    rc = main(["--synth", "--per-scenario", "20", "--out", out])
    assert rc == 0
    b = BehaviorBrain.load(out)
    assert b is not None and b.ready
    assert b.meta.get("synthetic") is True


def test_it_ships_from_ordinary_footage_alone(data):
    """Real early footage is all negatives. The brain must still fit its
    anomaly head, set a threshold, and keep the fire hydrant below it."""
    neg_train = [r for r in data["train"] if not r["suspicious"]]
    neg_val = [r for r in data["val"] if not r["suspicious"]]
    b = BehaviorBrain()
    report = b.fit(neg_train, neg_val)
    assert b.ready
    assert report["supervised_head"] is False       # no positives => anomaly only
    fur = next(r for r in data["test"] if r["label"] == "furniture")
    assert not b.score(fur["features"]).suspicious
