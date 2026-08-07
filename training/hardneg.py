#!/usr/bin/env python3
"""The hard-negative loop — how the system stops making the same mistake twice.

    # 1. run the current brain over footage you know is ordinary, and collect
    #    every clip it wrongly flags (its false alarms)
    python -m training.hardneg mine \
        --model models/brain.joblib \
        --features training/data/normal_features.jsonl \
        --out training/data/hard_negatives.jsonl

    # 2. (optional) open that file and delete any line that is NOT actually
    #    normal — a real incident hiding in the "normal" footage

    # 3. fold the confirmed mistakes back into training as hard negatives
    python -m training.hardneg promote \
        --queue training/data/hard_negatives.jsonl \
        --into training/data/features.jsonl

    # 4. retrain — the new model has been shown exactly what it got wrong
    python -m training.brain_train --features training/data/features.jsonl \
        --out models/brain.joblib

Why this is the most important loop in the product
--------------------------------------------------
False alarms are the thing that gets a security system unplugged. A delivery
driver read as a break-in, an owner loading their boot read as tampering — each
one teaches the guard to ignore it, and an ignored system is worthless. You
cannot write a rule for every such case; there are too many. So instead you let
the model make its mistakes on footage you *know* is ordinary, catch every
mistake automatically, and train it not to repeat them. Do that in a loop and
the false-alarm rate falls with every pass:

    model V1  ->  run over normal footage  ->  collect false alarms
              ->  (human glance)  ->  add as hard negatives  ->  retrain
              ->  model V2  ->  repeat

A "hard negative" is just a normal clip the model found hard — it was confident,
and it was wrong. Those are worth far more per example than random normal clips,
because they sit exactly on the boundary the model is getting wrong.

Safety
------
Mined clips are always **normal** (that is the whole premise: the input footage
is ordinary), so they enter training as `suspicious=0, hard_negative=1`. They go
into the **train** split only — you fix a model by training on its mistakes, not
by hiding them in the test set — and each gets a fresh, uniquely-tagged source
video, so promoting them can never leak a clip across the train/val/test line.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.brain import BehaviorBrain
from training.extract import read_rows, write_rows

HARDNEG_PREFIX = "hardneg"


def to_hard_negative(row: dict, score: float) -> dict:
    """Recast a wrongly-flagged normal clip as a training hard negative."""
    orig_source = row.get("source_video", "unknown")
    orig_label = row.get("label", "normal")
    return {
        "clip_id": f"hn-{row.get('clip_id', 'x')}",
        # a fresh, clearly-tagged source id so it can never collide with an
        # existing clip and leak across splits
        "source_video": f"{HARDNEG_PREFIX}:{orig_source}",
        "split": "train",                       # mistakes are fixed in training
        "label": f"hard_negative:{orig_label}",
        "specialist": row.get("specialist", "behavior"),
        "suspicious": 0,                        # it is normal — that is the point
        "hard_negative": 1,
        "person_id": row.get("person_id", 0),
        "vehicle_id": row.get("vehicle_id", 0),
        "camera_id": row.get("camera_id", ""),
        "night": row.get("night", 0),
        "duration_s": row.get("duration_s", 0.0),
        "features": row["features"],
        "why": row.get("why", ""),
        "brain_score": round(float(score), 4),  # for the human reviewer
    }


def mine(brain: BehaviorBrain, rows: list, min_score: float | None = None) -> list:
    """Return the rows the brain flags as suspicious — its false alarms on
    footage that is meant to be ordinary — worst (highest score) first.

    A row that is *labelled* suspicious is skipped: a correct catch is not a
    false alarm, and turning it into a hard negative would teach the model to
    ignore a real incident.
    """
    thresh = brain.threshold if min_score is None else float(min_score)
    hits = []
    for row in rows:
        if row.get("suspicious"):               # a genuine positive, not a mistake
            continue
        v = brain.score(row["features"])
        if v.suspicious or v.score >= thresh:
            hits.append(to_hard_negative(row, v.score))
    hits.sort(key=lambda r: -r["brain_score"])
    return hits


def promote(queue_rows: list, into_path: str) -> int:
    """Append accepted hard negatives to the training features file.

    Rows already present (same clip_id) are not duplicated, so re-running the
    loop is safe. Returns how many new rows were added.
    """
    existing = read_rows(into_path)
    have = {r.get("clip_id") for r in existing}
    fresh = [r for r in queue_rows if r.get("clip_id") not in have]
    if fresh:
        write_rows(into_path, existing + fresh)
    return len(fresh)


# ------------------------------------------------------------------ CLI
def _load_normal_rows(args) -> list:
    if args.synth_confuser:
        from training.synth import make_rows
        rows = make_rows(args.synth_confuser, args.synth_count, seed=args.seed)
        print(f"synthetic confuser: {len(rows)} '{args.synth_confuser}' clips "
              "(all normal)")
        return rows
    rows = read_rows(args.features)
    if not rows:
        print(f"no feature rows at {args.features} — run training.extract first")
    return rows


def cmd_mine(args) -> int:
    brain = BehaviorBrain.load(args.model)
    if brain is None or not brain.ready:
        print(f"no trained brain at {args.model} — train one first "
              "(training.brain_train)")
        return 1
    rows = _load_normal_rows(args)
    if not rows:
        return 1
    hits = mine(brain, rows, args.min_score)
    n = write_rows(args.out, hits)
    footage = len({r["clip_id"] for r in rows})
    print(f"\nran the brain over {len(rows)} normal windows ({footage} clips)")
    print(f"  it wrongly flagged {n} of them — these are the false alarms")
    if hits:
        print("  worst offenders:")
        for r in hits[:5]:
            print(f"    {r['brain_score']:.2f}  {r['label']:<22} {r['why'][:60]}")
    print(f"\nwritten to {args.out}")
    print("  review it (delete any line that is really an incident), then:")
    print(f"    python -m training.hardneg promote --queue {args.out} "
          "--into training/data/features.jsonl")
    return 0


def cmd_promote(args) -> int:
    queue = read_rows(args.queue)
    if not queue:
        print(f"nothing to promote in {args.queue}")
        return 1
    added = promote(queue, args.into)
    print(f"added {added} hard negatives to {args.into} "
          f"({len(queue) - added} were already there)")
    print("\nnow retrain so the model learns from its mistakes:")
    print(f"    python -m training.brain_train --features {args.into} "
          "--out models/brain.joblib")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("mine", help="collect the brain's false alarms")
    m.add_argument("--model", default="models/brain.joblib")
    m.add_argument("--features", default="training/data/normal_features.jsonl",
                   help="feature rows from footage you know is ordinary")
    m.add_argument("--out", default="training/data/hard_negatives.jsonl")
    m.add_argument("--min-score", type=float, default=None,
                   help="flag threshold; default is the brain's own")
    m.add_argument("--synth-confuser", default="",
                   help="demo: mine a synthetic confuser scenario, e.g. loading")
    m.add_argument("--synth-count", type=int, default=40)
    m.add_argument("--seed", type=int, default=0)
    m.set_defaults(func=cmd_mine)

    pr = sub.add_parser("promote", help="add reviewed hard negatives to training")
    pr.add_argument("--queue", default="training/data/hard_negatives.jsonl")
    pr.add_argument("--into", default="training/data/features.jsonl")
    pr.set_defaults(func=cmd_promote)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
