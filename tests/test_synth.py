"""The synthetic behaviour dataset: deterministic, labelled, and separable.

This is the fixture the brain is proven on before real footage exists, so it
has to be trustworthy in three ways: the same seed gives the same data, the
labels are right, and the one pair the brain must tell apart — a static object
vs a loiterer — really is different in the feature that separates them.
"""
from __future__ import annotations

from training import synth
from training import splits as S


def test_the_same_seed_gives_byte_identical_rows():
    a = synth.dataset(per_scenario=8, seed=3)
    b = synth.dataset(per_scenario=8, seed=3)
    assert a == b


def test_a_different_seed_gives_different_motion():
    a = synth.dataset(per_scenario=8, seed=1)
    b = synth.dataset(per_scenario=8, seed=2)
    assert a != b


def test_labels_match_the_scenario():
    rows = synth.dataset(per_scenario=6, seed=0)
    for r in rows:
        expected = r["label"] in synth.SUSPICIOUS
        assert bool(r["suspicious"]) is expected


def test_both_classes_are_present():
    rows = synth.dataset(per_scenario=10, seed=0)
    assert any(r["suspicious"] for r in rows)
    assert any(not r["suspicious"] for r in rows)


def test_no_source_video_straddles_two_splits():
    """The invariant the whole training package rests on."""
    rows = synth.dataset(per_scenario=12, seed=0)
    seen: dict = {}
    for r in rows:
        seen.setdefault(r["source_video"], set()).add(r["split"])
    assert all(len(v) == 1 for v in seen.values())


def test_every_scenario_reaches_the_test_split():
    """Stratified splitting must not park an entire scenario in train."""
    rows = synth.dataset(per_scenario=15, seed=0)
    test_labels = {r["label"] for r in rows if r["split"] == S.TEST}
    assert set(synth.SCENARIOS) <= test_labels


def test_furniture_is_still_and_loiter_is_not():
    """The discriminating signal. Both sit near the car for a long time; only
    the loiterer moves. If this ever fails, the fire-hydrant defence is gone."""
    rows = synth.dataset(per_scenario=20, seed=0)
    fur = [r["features"]["stillness"] for r in rows if r["label"] == "furniture"]
    loi = [r["features"]["stillness"] for r in rows if r["label"] == "loiter"]
    assert min(fur) > 0.5           # furniture reads as static
    assert max(loi) < 0.2           # a loiterer never does


def test_furniture_is_marked_a_hard_negative():
    rows = synth.dataset(per_scenario=6, seed=0)
    fur = [r for r in rows if r["label"] == "furniture"]
    assert fur and all(r["hard_negative"] for r in fur)
    assert all(not r["hard_negative"] for r in rows if r["label"] == "walk_past")
