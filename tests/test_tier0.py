"""The Tier 0 gate and its scoring — the baseline every later model must beat.

Most of these defend against a measurement that looks good and means nothing,
which is the failure the first real run actually produced.
"""
from __future__ import annotations

import pytest

from training import tier0
from training.features import Box, Context, Frame, pair_features

CAR = Box(400, 300, 600, 400)


def person(x, y=400, h=60):
    return Box(x - 12, y - h, x + 12, y, 0.8)


def scene(xs, y=400, step=1.0):
    return [Frame(ts=i * step, person=person(x, y), vehicle=CAR,
                  people_in_frame=1, vehicles_in_frame=1)
            for i, x in enumerate(xs)]


def row(feats, **kw):
    d = {"clip_id": "c1", "source_video": "v1", "split": "train",
         "suspicious": 0, "duration_s": 60.0, "features": feats, "why": ""}
    d.update(kw)
    return d


# ------------------------------------------------------------------- gate
def test_loitering_at_a_car_fires_the_gate():
    feats = pair_features(scene([505, 500, 503, 499, 502, 500], step=4.0))
    assert tier0.gate_fires(feats)


def test_walking_straight_past_does_not_fire():
    feats = pair_features(scene([100, 300, 500, 700, 900, 1100], y=560))
    assert not tier0.gate_fires(feats)


def test_a_brief_visit_does_not_fire():
    """Being at a car for four seconds is unlocking it, not stealing it."""
    feats = pair_features(scene([505, 500, 503, 500], step=1.0))
    assert not tier0.gate_fires(feats)


def test_every_condition_is_necessary():
    """The gate is an AND. If any one clause stopped mattering the false-alarm
    rate would move and nothing would say so."""
    firing = pair_features(scene([505, 500, 503, 499, 502, 500], step=4.0))
    assert tier0.gate_fires(firing)
    assert not tier0.gate_fires({**firing, "longest_near_run_s": 1.0})
    assert not tier0.gate_fires({**firing, "straightness": 0.99})
    assert not tier0.gate_fires({**firing, "contact_frames": 0.0})


def test_the_thresholds_can_be_overridden():
    feats = pair_features(scene([505, 498, 504, 499], step=1.0))
    assert not tier0.gate_fires(feats)               # only 3s of dwell
    assert tier0.gate_fires(feats, {"min_near_s": 1.0})


def test_early_stopping_is_off_at_the_data_size_this_tier_is_for():
    """Measured: forcing it on with 40 examples stopped after 10 iterations
    and produced a model that could not separate two obviously separable
    scenarios — the internal holdout was four rows, so "no longer improving"
    was noise."""
    assert tier0.EARLY_STOPPING_MIN_ROWS > 500


# ------------------------------------------------------------------ rates
def test_alerts_per_hour_is_scaled_from_the_footage_length():
    assert tier0.alerts_per_hour(6, 3600.0) == 6.0
    assert tier0.alerts_per_hour(1, 60.0) == 60.0


def test_no_footage_gives_no_rate_rather_than_a_division_error():
    assert tier0.alerts_per_hour(3, 0.0) is None


def test_footage_is_counted_once_per_clip_not_once_per_pair():
    """A clip with four candidate pairs is still one clip's worth of time.
    Summing per pair would quarter the false-alarm rate by arithmetic."""
    rows = [row({}, clip_id="c1"), row({}, clip_id="c1"),
            row({}, clip_id="c1"), row({}, clip_id="c2")]
    assert tier0.footage_seconds(rows) == 120.0


# ------------------------------------------------------------- confusion
def test_the_confusion_matrix_adds_up():
    c = tier0.confusion([True, True, False, False], [True, False, True, False])
    assert (c["tp"], c["fn"], c["fp"], c["tn"]) == (1, 1, 1, 1)
    assert c["recall"] == 0.5 and c["precision"] == 0.5
    assert c["false_positive_rate"] == 0.5
    assert c["balanced_accuracy"] == 0.5


def test_recall_of_a_set_with_no_positives_is_none_not_zero():
    """Zero would read as 'it missed every incident'. None is 'there were no
    incidents to miss', and printing 0% for that sends somebody bug-hunting."""
    c = tier0.confusion([False, False], [False, True])
    assert c["recall"] is None
    assert c["false_positive_rate"] == 0.5


def test_precision_with_nothing_predicted_is_none():
    assert tier0.confusion([True, False], [False, False])["precision"] is None


# ------------------------------------------------------ the honest report
def test_a_negative_only_set_reports_false_alarms_not_a_confusion_matrix():
    """With no positives there is no recall to report, and pretending
    otherwise is how a meaningless number gets quoted."""
    quiet = pair_features(scene([100, 300, 500, 700], y=560))
    loud = pair_features(scene([505, 500, 503, 499, 502, 500], step=4.0))
    out = tier0.run_gate([row(quiet), row(loud)])
    assert "confusion" not in out
    assert out["false_alarms"] == 1
    assert out["fired"] == 1


def test_a_labelled_set_gets_a_confusion_matrix():
    loud = pair_features(scene([505, 500, 503, 499, 502, 500], step=4.0))
    out = tier0.run_gate([row(loud, suspicious=1)])
    assert out["confusion"]["tp"] == 1


def test_the_rate_uses_distinct_footage():
    loud = pair_features(scene([505, 500, 503, 499, 502, 500], step=4.0))
    out = tier0.run_gate([row(loud, clip_id="c1"), row(loud, clip_id="c1")])
    assert out["footage_s"] == 60.0
    assert out["fired"] == 2
    assert out["alerts_per_hour"] == pytest.approx(120.0)


# ------------------------------------------------------------- the model
def test_training_is_refused_when_there_is_only_one_class():
    """A classifier needs both. Returning None rather than a model that always
    says 'normal' and scores 98%."""
    quiet = pair_features(scene([100, 300, 500], y=560))
    assert tier0.train_model([row(quiet)] * 20, []) is None


def test_a_model_trains_and_separates_the_two_scenarios():
    sk = pytest.importorskip("sklearn")            # noqa: F841
    quiet = [row(pair_features(scene([100 + i * 20, 300, 500, 700], y=560)),
                 suspicious=0) for i in range(20)]
    loud = [row(pair_features(scene([505, 500 + i, 503, 499, 502, 500],
                                    step=4.0)), suspicious=1)
            for i in range(20)]
    trained = tier0.train_model(quiet + loud, quiet[:5] + loud[:5])
    assert trained is not None
    model, threshold, _c = trained
    from training.features import to_vector
    assert model.predict([to_vector(loud[0]["features"])])[0] == 1
    assert model.predict([to_vector(quiet[0]["features"])])[0] == 0
    assert 0.0 < threshold < 1.0


def test_splitting_rows_keeps_the_split_labels():
    rows = [row({}, split="train"), row({}, split="test"), row({}, split="")]
    by = tier0.split_rows(rows)
    assert len(by["train"]) == 1 and len(by["test"]) == 1 and len(by[""]) == 1
