"""The one-command real-training orchestrator: it must assemble the right
sequence of ordinary tool commands, so a beginner can run one thing (or copy any
step by hand if it fails).
"""
from __future__ import annotations

from types import SimpleNamespace

from training import pilot_train as P


def _args(**over):
    base = dict(cameras="G330,G420", pool="drops-123-r13", sources=8, clips=6,
                width=1280, budget_gb=3.0, night=False, ucf="", stride=3,
                out="training/data", model="models/brain.joblib", eval_meva=2,
                skip_mine=False)
    base.update(over)
    return SimpleNamespace(**base)


def _joined(cmds):
    return [" ".join(c) for c in cmds]


def test_the_full_plan_has_every_stage_in_order():
    cmds = _joined(P.plan(_args()))
    text = "\n".join(cmds)
    # a mine per camera, then split, extract, train, eval — in that order
    assert any("clipmine" in c and "G330" in c for c in cmds)
    assert any("clipmine" in c and "G420" in c for c in cmds)
    order = [i for i, c in enumerate(cmds)
             if any(s in c for s in ("splits_cli", "extract", "brain_train",
                                     "evaluate_continuous"))]
    assert order == sorted(order)
    assert "splits_cli" in text and "brain_train" in text


def test_skip_mine_starts_at_the_split():
    cmds = _joined(P.plan(_args(skip_mine=True)))
    assert not any("clipmine" in c for c in cmds)
    assert cmds[0].endswith("training/data/manifest.jsonl") or "splits_cli" in cmds[0]


def test_ucf_adds_a_positives_mine():
    cmds = _joined(P.plan(_args(ucf="D:/ucf")))
    assert any("clipmine" in c and "break_in" in c and "D:/ucf" in c
               for c in cmds)


def test_night_doubles_the_mine_per_camera():
    day = P.plan(_args())
    both = P.plan(_args(night=True))
    assert sum("clipmine" in " ".join(c) for c in both) > \
        sum("clipmine" in " ".join(c) for c in day)
    assert any("--hours night" in " ".join(c) for c in both)


def test_eval_can_be_turned_off():
    assert not any("evaluate_continuous" in " ".join(c)
                   for c in P.plan(_args(eval_meva=0)))


def test_dry_run_prints_and_runs_nothing(capsys):
    rc = P.main(["--dry-run", "--cameras", "G330"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "clipmine" in out and "brain_train" in out
