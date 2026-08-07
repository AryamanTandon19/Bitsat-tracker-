"""The live scorer: turn a detection stream into windows, judge the hottest
pair, and hold up only when the behaviour persists.
"""
from __future__ import annotations

import random

import pytest

pytest.importorskip("sklearn")

from app.brain import BehaviorBrain
from app.brain_live import LiveBrainScorer, hour_and_night
from app.detector import Detection
from training import synth


@pytest.fixture(scope="module")
def brain():
    rows = synth.dataset(per_scenario=50, seed=0)
    by: dict = {"train": [], "val": []}
    for r in rows:
        if r["split"] in by:
            by[r["split"]].append(r)
    b = BehaviorBrain()
    b.fit(by["train"], by["val"], synthetic=True)
    return b


def _detections(mframe):
    dets = []
    for tid, box in mframe.people.items():
        dets.append(Detection(track_id=tid, cls_name="person",
                              conf=box.conf, xyxy=box.xyxy))
    for tid, box in mframe.vehicles.items():
        dets.append(Detection(track_id=tid, cls_name="car",
                              conf=box.conf, xyxy=box.xyxy))
    return dets


def _replay(brain, scenario, seed=7):
    frames, ctx = synth.GENERATORS[scenario](random.Random(seed))
    sc = LiveBrainScorer(brain, f"cam-{scenario}",
                         {"infer_every_s": 2.0, "min_span_s": 5.0})
    last = None
    for mf in frames:
        r = sc.observe(_detections(mf), mf.ts,
                       night=bool(ctx.night), hour=ctx.hour)
        if r is not None:
            last = r
    return last


def test_it_confirms_the_suspicious_scenarios(brain):
    for scenario in ("loiter", "circle", "break_in"):
        r = _replay(brain, scenario)
        assert r is not None, f"{scenario} produced no reading"
        assert r.verdict.suspicious and r.confirmed, scenario


def test_it_stays_quiet_through_ordinary_ones(brain):
    for scenario in ("walk_past", "delivery", "furniture"):
        r = _replay(brain, scenario)
        # either no near-pair reading at all, or a reading that is not suspicious
        assert r is None or not r.verdict.suspicious, scenario


def test_the_static_object_is_never_confirmed(brain):
    r = _replay(brain, "furniture")
    assert r is None or not r.confirmed


def test_it_throttles_between_inference_ticks(brain):
    """Many frames in, at most one reading per infer_every_s window."""
    frames, ctx = synth.GENERATORS["loiter"](random.Random(1))
    sc = LiveBrainScorer(brain, "cam", {"infer_every_s": 2.0, "min_span_s": 5.0})
    readings = [sc.observe(_detections(mf), mf.ts, hour=ctx.hour)
                for mf in frames]
    got = [r for r in readings if r is not None]
    span = frames[-1].ts - frames[0].ts
    assert len(got) <= span / 2.0 + 1        # one per ~2s at most


def test_no_reading_before_enough_history(brain):
    """A single frame can never confirm anything."""
    frames, _ = synth.GENERATORS["break_in"](random.Random(1))
    sc = LiveBrainScorer(brain, "cam", {"min_span_s": 6.0})
    assert sc.observe(_detections(frames[0]), frames[0].ts) is None


def test_hour_and_night_reads_the_clock():
    import datetime as dt

    midnight = dt.datetime(2025, 1, 1, 2, 0).timestamp()
    noon = dt.datetime(2025, 1, 1, 12, 0).timestamp()
    assert hour_and_night(midnight)[1] is True
    assert hour_and_night(noon)[1] is False
