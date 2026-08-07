"""The hard-negative loop: the model's own mistakes, fed back until it stops.

The load-bearing test is `test_the_loop_reduces_false_alarms`: it runs the whole
cycle — V1 makes false alarms on a confusing-but-normal behaviour, the miner
catches them, they are promoted to training, and V2 makes far fewer — while
keeping its recall on real break-ins. That is the product's central promise.
"""
from __future__ import annotations

import pytest

pytest.importorskip("sklearn")

from app.brain import BehaviorBrain
from training import hardneg, synth


def _brain(train, val):
    b = BehaviorBrain()
    b.fit(train, val, synthetic=True)
    return b


def _false_alarms(brain, clips):
    return sum(1 for r in clips if brain.score(r["features"]).suspicious)


@pytest.fixture(scope="module")
def base():
    rows = synth.dataset(per_scenario=60, seed=0)
    return {
        "train": [r for r in rows if r["split"] == "train"],
        "val": [r for r in rows if r["split"] == "val"],
        "break_in_test": [r for r in rows
                          if r["label"] == "break_in" and r["split"] == "test"],
    }


# --------------------------------------------------------------- the loop
def test_the_loop_reduces_false_alarms(base):
    v1 = _brain(base["train"], base["val"])

    # a confusing but entirely normal behaviour V1 has never seen
    loading = synth.make_rows("loading", 40, seed=5)
    fa1 = _false_alarms(v1, loading)
    assert fa1 >= 15, f"the confuser should actually fool V1 ({fa1})"

    # mine the mistakes, promote them, retrain
    hits = hardneg.mine(v1, loading)
    assert len(hits) == fa1
    v2 = _brain(base["train"] + hits, base["val"])

    # judge V2 on *unseen* loading clips — the honest test
    fa2 = _false_alarms(v2, synth.make_rows("loading", 40, seed=99))
    assert fa2 < fa1 and (fa1 - fa2) >= 8, f"V2 should improve: {fa1} -> {fa2}"

    # and it must not have gone blind to the real thing
    recall = _false_alarms(v2, base["break_in_test"])
    assert recall == len(base["break_in_test"]), "V2 lost break-in recall"


# --------------------------------------------------------------- mining
def test_mining_never_turns_a_real_positive_into_a_negative(base):
    """A correct catch is not a false alarm. Feeding the miner a genuine
    break-in must not produce a hard negative that teaches the model to ignore
    one."""
    v1 = _brain(base["train"], base["val"])
    positives = [r for r in base["break_in_test"]]
    assert positives
    assert hardneg.mine(v1, positives) == []


def test_mined_rows_are_normal_hard_negatives_bound_for_training(base):
    v1 = _brain(base["train"], base["val"])
    hits = hardneg.mine(v1, synth.make_rows("loading", 20, seed=5))
    assert hits
    for r in hits:
        assert r["suspicious"] == 0
        assert r["hard_negative"] == 1
        assert r["split"] == "train"
        assert r["source_video"].startswith(hardneg.HARDNEG_PREFIX + ":")
        assert "brain_score" in r


def test_worst_offenders_come_first(base):
    v1 = _brain(base["train"], base["val"])
    hits = hardneg.mine(v1, synth.make_rows("loading", 30, seed=5))
    scores = [r["brain_score"] for r in hits]
    assert scores == sorted(scores, reverse=True)


# --------------------------------------------------------------- promote
def test_promote_appends_without_leaking_or_duplicating(tmp_path, base):
    feats = str(tmp_path / "features.jsonl")
    from training.extract import write_rows
    write_rows(feats, base["train"])           # a training file to grow

    v1 = _brain(base["train"], base["val"])
    hits = hardneg.mine(v1, synth.make_rows("loading", 20, seed=5))

    added = hardneg.promote(hits, feats)
    assert added == len(hits)

    from training.extract import read_rows
    after = read_rows(feats)
    # no mined clip shares a source video with an original clip
    orig_sources = {r["source_video"] for r in base["train"]}
    mined_sources = {r["source_video"] for r in after
                     if r.get("hard_negative") and str(r["label"]).startswith(
                         "hard_negative")}
    assert mined_sources.isdisjoint(orig_sources)

    # promoting again is idempotent
    assert hardneg.promote(hits, feats) == 0


# --------------------------------------------------------------- the CLI
def test_the_cli_mines_and_promotes_end_to_end(tmp_path):
    # a trained brain on disk
    model = str(tmp_path / "brain.joblib")
    from training.brain_train import main as train_main
    train_main(["--synth", "--per-scenario", "40", "--out", model])

    queue = str(tmp_path / "hn.jsonl")
    rc = hardneg.main(["mine", "--model", model, "--synth-confuser", "loading",
                       "--synth-count", "30", "--out", queue])
    assert rc == 0

    from training.extract import read_rows, write_rows
    mined = read_rows(queue)
    assert mined and all(r["hard_negative"] for r in mined)

    feats = str(tmp_path / "features.jsonl")
    write_rows(feats, [])
    rc = hardneg.main(["promote", "--queue", queue, "--into", feats])
    assert rc == 0
    assert len(read_rows(feats)) == len(mined)
