"""Train/val/test by SOURCE VIDEO, never by clip.

The bug these defend against does not announce itself: every training curve
looks healthy, validation reads 96%, and the car park disagrees. Twelve clips
cut from one burglary are twelve views of one event, and if some are in train
and some in val then val is measuring memorisation.
"""
from __future__ import annotations

import pytest

from training import splits as S
from training.manifest import ClipRecord


def rec(clip_id, source, label="HOUSE_BREAK_IN", specialist="break_in", **kw):
    return ClipRecord(clip_id=clip_id, path=f"c/{clip_id}.mp4",
                      source_video=source, dataset="ucf-crime", label=label,
                      specialist=specialist, start_s=0.0, end_s=6.0, **kw)


def dataset(n_sources=10, clips_each=5, label="HOUSE_BREAK_IN"):
    out = []
    for s in range(n_sources):
        for c in range(clips_each):
            out.append(rec(f"{label[:4]}_{s}_{c}", f"video_{label[:4]}_{s}",
                           label=label))
    return out


# --------------------------------------------------- the invariant itself
def test_no_source_video_ever_lands_in_two_splits():
    records = S.assign(dataset(12) + dataset(12, label="NORMAL"))
    S.check_separation(records)                 # must not raise
    by_source = {}
    for r in records:
        by_source.setdefault(r.source_video, set()).add(r.split)
    assert all(len(v) == 1 for v in by_source.values())


def test_a_leaked_source_is_caught_and_named():
    records = S.assign(dataset(10))
    records[0].split = "train"
    records[1].split = "test"                   # same source_video, two splits
    with pytest.raises(S.SplitError, match=records[0].source_video):
        S.check_separation(records)


def test_the_error_explains_why_it_matters():
    records = S.assign(dataset(10))
    records[0].split, records[1].split = "train", "val"
    with pytest.raises(S.SplitError, match="measuring memorisation"):
        S.check_separation(records)


def test_clips_with_no_split_do_not_trip_the_check():
    records = dataset(4)
    S.check_separation(records)
    assert len(S.unassigned(records)) == 20


# ------------------------------------------------------------- stability
def test_assignment_is_stable_across_runs():
    """Same manifest, same splits — on every machine and every run. Otherwise
    'held-out test set' means nothing between two Tuesdays."""
    a = {r.clip_id: r.split for r in S.assign(dataset(10))}
    b = {r.clip_id: r.split for r in S.assign(dataset(10))}
    assert a == b


def test_adding_new_clips_does_not_move_the_old_ones():
    """Next week's batch must not reshuffle last week's test set, or the
    number you reported last week is retroactively meaningless."""
    first = {r.clip_id: r.split for r in S.assign(dataset(10))}
    grown = S.assign(dataset(10) + dataset(4, label="NORMAL"))
    after = {r.clip_id: r.split for r in grown if r.clip_id in first}
    assert after == first


def test_a_different_seed_gives_a_different_split():
    a = {r.clip_id: r.split for r in S.assign(dataset(10), seed=0)}
    b = {r.clip_id: r.split for r in S.assign(dataset(10), seed=99)}
    assert a != b


def test_the_split_does_not_depend_on_python_s_hash_randomisation():
    """sha1, not hash() — the latter is salted per process."""
    assert S._fraction("Burglary017_x264") == S._fraction("Burglary017_x264")
    assert 0.0 <= S._fraction("anything") < 1.0


# ---------------------------------------------------------- stratification
def test_every_split_gets_both_classes():
    """A pure hash can put all the positives in test and leave training with
    nothing to learn from."""
    records = S.assign(dataset(10) + dataset(10, label="NORMAL"))
    rep = S.report(records)
    for split in ("train", "val", "test"):
        assert set(rep[split]["by_label"]) == {"HOUSE_BREAK_IN", "NORMAL"}, \
            f"{split} is missing a class: {rep[split]['by_label']}"


def test_the_fractions_are_roughly_respected():
    records = S.assign(dataset(20) + dataset(20, label="NORMAL"),
                       val_fraction=0.2, test_fraction=0.2)
    rep = S.report(records)
    total = sum(rep[s]["sources"] for s in ("train", "val", "test"))
    assert total == 40
    assert 6 <= rep["test"]["sources"] <= 10
    assert 6 <= rep["val"]["sources"] <= 10


def test_specialists_are_stratified_separately():
    records = S.assign(dataset(8) + dataset(8, label="NORMAL")
                       + [rec(f"v_{i}_{c}", f"veh_{i}",
                              label="VEHICLE_THEFT_OR_TAMPERING",
                              specialist="vehicle")
                          for i in range(8) for c in range(3)])
    S.check_separation(records)
    veh = [r for r in records if r.specialist == "vehicle"]
    assert len({r.split for r in veh}) == 3


# ------------------------------------------------------------ small sets
def test_training_is_never_left_empty():
    """With three sources, taking 20% for each of val and test must not
    round the training split away to nothing."""
    records = S.assign(dataset(3))
    assert any(r.split == "train" for r in records)


def test_a_single_source_all_goes_to_training():
    """There is no honest way to hold out from one video, so it does not
    pretend to — and `warnings()` says so out loud."""
    records = S.assign(dataset(1))
    assert {r.split for r in records} == {"train"}
    assert any("no honest way" in w for w in S.warnings(records))


def test_impossible_fractions_are_refused():
    with pytest.raises(S.SplitError, match="something to train on"):
        S.assign(dataset(10), val_fraction=0.6, test_fraction=0.5)
    with pytest.raises(S.SplitError, match="negative"):
        S.assign(dataset(10), val_fraction=-0.1)


# --------------------------------------------------------------- warnings
def test_a_split_missing_a_class_is_flagged():
    records = S.assign(dataset(6), val_fraction=0.2, test_fraction=0.2)
    warn = S.warnings(records)
    assert any("NORMAL" in w or "no honest way" in w or "single source" in w
               for w in warn)


def test_a_test_split_from_one_video_is_flagged():
    records = S.assign(dataset(5), val_fraction=0.2, test_fraction=0.2)
    assert any("single source video" in w for w in S.warnings(records))


def test_a_test_split_with_no_hard_negatives_is_flagged():
    records = S.assign(dataset(10) + dataset(10, label="NORMAL"))
    assert any("hard negatives" in w for w in S.warnings(records))


def test_hard_negatives_in_the_test_split_clear_that_warning():
    records = dataset(10) + [rec(f"hn_{i}_{c}", f"hn_video_{i}", label="NORMAL",
                                 hard_negative=True, hn_reason="delivery")
                             for i in range(10) for c in range(3)]
    S.assign(records)
    assert not any("hard negatives" in w for w in S.warnings(records))


# --------------------------------------------------------------- reporting
def test_the_report_counts_clips_and_sources_separately():
    records = S.assign(dataset(10, clips_each=5))
    rep = S.report(records)
    assert sum(rep[s]["clips"] for s in ("train", "val", "test")) == 50
    assert sum(rep[s]["sources"] for s in ("train", "val", "test")) == 10


def test_describe_is_readable_and_carries_the_warnings():
    text = S.describe(S.assign(dataset(4)))
    assert "train" in text and "sources" in text
    assert "!" in text                       # small dataset -> warnings shown
