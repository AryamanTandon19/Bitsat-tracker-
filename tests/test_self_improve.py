"""The self-improve loop: feedback retrains the brain and it goes live — but
only if it did not get worse. The guardrail and the hot-reload are the two
load-bearing parts, so they get the most tests.
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("sklearn")

from app.brain import BehaviorBrain
from app.brainwatch import ModelWatcher, changed
from training import self_improve, synth
from training.extract import read_rows, write_rows


# --------------------------------------------------- the guardrail (pure)
def test_the_first_model_always_deploys():
    ok, _ = self_improve.is_deployable(None, {"recall": 0.5,
                                              "false_positive_rate": 0.3})
    assert ok


def test_a_worse_false_alarm_rate_is_rejected():
    inc = {"recall": 0.8, "false_positive_rate": 0.10}
    cand = {"recall": 0.8, "false_positive_rate": 0.20}
    ok, reason = self_improve.is_deployable(inc, cand)
    assert not ok and "false-alarm" in reason


def test_a_collapse_in_recall_is_rejected():
    inc = {"recall": 0.85, "false_positive_rate": 0.10}
    cand = {"recall": 0.60, "false_positive_rate": 0.10}
    ok, reason = self_improve.is_deployable(inc, cand)
    assert not ok and "recall" in reason


def test_an_equal_or_better_model_deploys():
    inc = {"recall": 0.80, "false_positive_rate": 0.12}
    cand = {"recall": 0.83, "false_positive_rate": 0.08}
    assert self_improve.is_deployable(inc, cand)[0]


def test_a_tiny_wobble_within_slack_still_deploys():
    inc = {"recall": 0.80, "false_positive_rate": 0.10}
    cand = {"recall": 0.78, "false_positive_rate": 0.11}   # within slack
    assert self_improve.is_deployable(inc, cand)[0]


# --------------------------------------------------- the watcher (pure)
def test_change_detection():
    assert changed(None, None) is False        # still absent
    assert changed(None, 5.0) is True          # appeared
    assert changed(5.0, 5.0) is False          # unchanged
    assert changed(5.0, 6.0) is True           # modified


def test_the_watcher_fires_once_per_change():
    now = {"t": 100.0}
    fired = []
    w = ModelWatcher("x", lambda: fired.append(1), mtime_fn=lambda p: now["t"])
    assert w.check_once() is False             # baseline == current
    now["t"] = 200.0
    assert w.check_once() is True and fired == [1]
    assert w.check_once() is False             # no further change


# --------------------------------------------------- feedback pull
def test_pull_feedback_exports_once(tmp_path):
    from app.db import Database

    dbp = str(tmp_path / "t.db")
    db = Database(dbp)
    db.save_event_features(7, {"straightness": 0.1}, camera="gate")
    db.insert_feedback(7, "false_alarm", "guard")
    db.close()

    queue = str(tmp_path / "q.jsonl")
    assert self_improve.pull_feedback(dbp, queue) == 1
    assert len(read_rows(queue)) == 1
    assert self_improve.pull_feedback(dbp, queue) == 0     # already exported


# --------------------------------------------------- the full run
def _features(tmp_path, n=40, seed=0):
    p = str(tmp_path / "feat.jsonl")
    write_rows(p, synth.dataset(per_scenario=n, seed=seed))
    return p


def test_run_trains_and_deploys_the_first_model(tmp_path):
    feat = _features(tmp_path)
    model = str(tmp_path / "brain.joblib")
    rc = self_improve.run(str(tmp_path / "e.db"), feat, model,
                          str(tmp_path / "q.jsonl"), force=True)
    assert rc == 0
    b = BehaviorBrain.load(model)
    assert b is not None and b.ready


def test_run_skips_when_there_is_nothing_new(tmp_path):
    feat = _features(tmp_path)
    model = str(tmp_path / "brain.joblib")
    q = str(tmp_path / "q.jsonl")
    self_improve.run(str(tmp_path / "e.db"), feat, model, q, force=True)
    mtime = os.path.getmtime(model)
    self_improve.run(str(tmp_path / "e.db"), feat, model, q)   # no new data
    assert os.path.getmtime(model) == mtime                    # untouched


def test_a_rejected_candidate_never_overwrites_the_live_model(tmp_path, monkeypatch):
    feat = _features(tmp_path)
    model = str(tmp_path / "brain.joblib")
    q = str(tmp_path / "q.jsonl")
    self_improve.run(str(tmp_path / "e.db"), feat, model, q, force=True)
    mtime = os.path.getmtime(model)
    monkeypatch.setattr(self_improve, "is_deployable",
                        lambda inc, cand, **k: (False, "rejected by test"))
    self_improve.run(str(tmp_path / "e.db"), feat, model, q, force=True)
    assert os.path.getmtime(model) == mtime      # the good model stayed live


# --------------------------------------------------- hot-reload
def test_reload_brain_updates_the_context_and_every_camera(tmp_path):
    from app.main import AppContext

    # a real trained brain on disk
    model = str(tmp_path / "brain.joblib")
    b = BehaviorBrain()
    rows = synth.dataset(per_scenario=30, seed=0)
    b.fit([r for r in rows if r["split"] == "train"],
          [r for r in rows if r["split"] == "val"])
    b.save(model)

    class Pipe:
        def set_brain(self, brain):
            self.got = brain

    pipe = Pipe()
    stub = SimpleNamespace(config={"brain": {"model_path": model}},
                           pipelines={"cam": pipe}, brain=None)
    reload = AppContext.reload_brain.__get__(stub)
    assert reload() is True
    assert stub.brain is not None and stub.brain.ready
    assert pipe.got is stub.brain            # the camera got the new brain


def test_reload_is_a_safe_noop_when_the_file_is_missing(tmp_path):
    from app.main import AppContext

    stub = SimpleNamespace(
        config={"brain": {"model_path": str(tmp_path / "nope.joblib")}},
        pipelines={}, brain="unchanged")
    reload = AppContext.reload_brain.__get__(stub)
    assert reload() is False
    assert stub.brain == "unchanged"
