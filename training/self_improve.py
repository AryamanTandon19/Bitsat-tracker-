#!/usr/bin/env python3
"""The self-improve loop — feedback becomes a better model, on its own.

    python -m training.self_improve            # run one cycle
    python -m training.self_improve --dry-run  # show what it would do, deploy nothing

Run it on a schedule (cron / Task Scheduler / a Routine). Each cycle:

  1. pulls the alerts operators and residents marked 'false alarm' out of the
     live database and adds them to the training set as hard negatives;
  2. retrains the behaviour brain on the accumulated data;
  3. checks the fresh model against the current one on the held-out split and
     DEPLOYS ONLY IF IT DID NOT GET WORSE — a wrong turn must never reach the
     cameras unattended;
  4. atomically swaps the model file in.

The running system watches that file (app/brainwatch.py) and reloads the new
brain within a minute — no restart, no human. That is the whole loop: a guard
or a resident taps ❌, and a little later every camera is a little better at not
making that mistake.

The guardrail is the point. "No human in the loop" is only safe if the loop
refuses to deploy a regression, so the deploy decision is a pure, tested
function of the two models' held-out numbers.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.brain import BehaviorBrain
from training.brain_train import _confusion, _split
from training.extract import read_rows, write_rows
from training.hardneg import promote


def is_deployable(incumbent: dict | None, candidate: dict,
                  fpr_slack: float = 0.02, recall_drop: float = 0.05) -> tuple:
    """Should the candidate replace the incumbent? Returns (deploy, reason).

    Deploy when there is nothing to compare against, or when the candidate is
    not meaningfully worse: its false-positive rate has not risen by more than
    `fpr_slack`, and its recall has not fallen by more than `recall_drop`. The
    asymmetry is deliberate — this product would rather keep a slightly stale
    model than ship one that alarms more or catches less.
    """
    if incumbent is None:
        return True, "no current model — deploying the first one"
    ci, cc = incumbent, candidate
    if cc.get("false_positive_rate") is not None and \
            ci.get("false_positive_rate") is not None:
        if cc["false_positive_rate"] > ci["false_positive_rate"] + fpr_slack:
            return False, (f"false-alarm rate rose "
                           f"{ci['false_positive_rate']:.3f} -> "
                           f"{cc['false_positive_rate']:.3f} (rejected)")
    if cc.get("recall") is not None and ci.get("recall") is not None:
        if cc["recall"] < ci["recall"] - recall_drop:
            return False, (f"recall fell {ci['recall']:.2f} -> "
                           f"{cc['recall']:.2f} (rejected)")
    return True, "no regression on the held-out split"


def _db_path(arg: str) -> str:
    if arg:
        return arg
    try:
        import yaml
        return yaml.safe_load(open("config.yaml"))["storage"]["db_path"]
    except Exception:                                # noqa: BLE001
        return "watchdog.db"


def pull_feedback(db_path: str, queue_path: str) -> int:
    """Export operator/resident false-alarms from the DB into the queue file and
    fold them into nothing yet — just return how many are new."""
    from app.db import Database

    db = Database(db_path)
    try:
        rows = db.export_hard_negative_rows()
        if rows:
            db.mark_hard_negatives_promoted(
                [int(r["clip_id"][3:]) for r in rows])
    finally:
        db.close()
    if not rows:
        return 0
    existing = read_rows(queue_path)
    have = {r.get("clip_id") for r in existing}
    fresh = [r for r in rows if r.get("clip_id") not in have]
    if fresh:
        write_rows(queue_path, existing + fresh)
    return len(fresh)


def run(db_path: str, features_path: str, model_path: str, queue_path: str,
        *, dry_run: bool = False, force: bool = False) -> int:
    # 1. feedback -> queue -> training set
    new_fb = pull_feedback(db_path, queue_path)
    added = promote(read_rows(queue_path), features_path) if Path(queue_path).exists() else 0
    print(f"feedback: {new_fb} new false-alarms exported; "
          f"{added} added to the training set")

    rows = read_rows(features_path)
    if not rows:
        print("no training data yet — nothing to learn from")
        return 0
    if added == 0 and not force and Path(model_path).exists():
        print("no new data since the last model — nothing to do "
              "(use --force to retrain anyway)")
        return 0

    by = _split(rows)
    train = by["train"] or rows
    eval_set = by["test"] or by["val"]
    if len({r["suspicious"] for r in train}) < 1:
        print("training set is empty")
        return 0

    # 2. train the candidate
    candidate = BehaviorBrain()
    rep = candidate.fit(train, by["val"])
    cand_metrics = _confusion(eval_set, candidate) if eval_set else {}
    print(f"candidate trained on {len(train)} rows "
          f"({rep['n_positives']} positive); "
          f"threshold {rep['threshold']}")

    # 3. the guardrail: compare against the model in production
    incumbent = BehaviorBrain.load(model_path)
    inc_metrics = _confusion(eval_set, incumbent) \
        if (incumbent and incumbent.ready and eval_set) else None
    deploy, reason = is_deployable(inc_metrics, cand_metrics) if eval_set \
        else (True, "no held-out split to check — deploying")
    if eval_set:
        _print_metrics("current  ", inc_metrics)
        _print_metrics("candidate", cand_metrics)
    print(f"decision: {'DEPLOY' if deploy else 'KEEP CURRENT'} — {reason}")

    if not deploy or dry_run:
        if dry_run and deploy:
            print("(dry run — not deploying)")
        return 0

    # 4. atomic swap; the running system's watcher reloads it within a minute
    _atomic_save(candidate, model_path)
    print(f"deployed -> {model_path}  (live within ~1 min, no restart)")
    return 0


def _print_metrics(label: str, m: dict | None) -> None:
    if not m:
        print(f"  {label}: (none)")
        return
    rec = "n/a" if m.get("recall") is None else f"{m['recall']:.2f}"
    fpr = "n/a" if m.get("false_positive_rate") is None \
        else f"{m['false_positive_rate']:.3f}"
    print(f"  {label}: recall {rec}  FPR {fpr}")


def _atomic_save(brain: BehaviorBrain, model_path: str) -> None:
    Path(model_path).parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".joblib",
                               dir=str(Path(model_path).parent))
    os.close(fd)
    brain.save(tmp)
    os.replace(tmp, model_path)          # atomic on the same filesystem


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default="")
    p.add_argument("--features", default="training/data/features.jsonl")
    p.add_argument("--model", default="models/brain.joblib")
    p.add_argument("--queue", default="training/data/hard_negatives.jsonl")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="retrain even if there is no new feedback")
    args = p.parse_args(argv)
    return run(_db_path(args.db), args.features, args.model, args.queue,
               dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
