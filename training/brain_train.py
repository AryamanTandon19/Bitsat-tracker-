#!/usr/bin/env python3
"""Train the behaviour brain and write it to disk. One command.

    # prove the whole pipeline today, on synthetic motion
    python -m training.brain_train --synth --out models/brain.joblib

    # the real thing, once clips have been mined and features extracted
    python -m training.brain_train --features training/data/features.jsonl \
        --out models/brain.joblib

What it does, in order:

  1. loads feature rows (from `--features`, or generates synthetic ones with
     `--synth`);
  2. checks the source-separation invariant — the same guard `training/splits`
     enforces, because a leak here silently inflates every number after it;
  3. fits the brain (`app/brain.py`): the unsupervised anomaly head always, the
     supervised head automatically if there are enough real positives;
  4. calibrates the operating threshold on the **validation** split;
  5. evaluates on the **test** split — false alarms per hour is the headline —
     and prints what carried the decision;
  6. saves the brain to `--out`.

The number this prints is only as real as the data it read. On `--synth` it is
labelled SYNTHETIC and proves the machinery, not the product. The number you
may quote comes from the untouched real holdout (plan step 17), once real
footage exists.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.brain import BehaviorBrain
from training.extract import read_rows


def _split(rows: list) -> dict:
    out: dict = {"train": [], "val": [], "test": [], "": []}
    for r in rows:
        out.setdefault(r.get("split") or "", []).append(r)
    return out


def _check_separation(rows: list) -> str | None:
    """Return an error string if any source video straddles splits."""
    from collections import defaultdict

    seen = defaultdict(set)
    for r in rows:
        if r.get("split"):
            seen[r["source_video"]].add(r["split"])
    bad = {s: sorted(v) for s, v in seen.items() if len(v) > 1}
    if bad:
        lines = "\n".join(f"    {s}: {', '.join(v)}" for s, v in sorted(bad.items()))
        return ("source videos appear in more than one split — validation would "
                "measure memorisation:\n" + lines)
    return None


def _confusion(rows: list, brain: BehaviorBrain) -> dict:
    tp = fp = fn = tn = 0
    for r in rows:
        pred = brain.score(r["features"]).suspicious
        truth = bool(r["suspicious"])
        tp += pred and truth
        fp += pred and not truth
        fn += (not pred) and truth
        tn += (not pred) and not truth
    recall = tp / (tp + fn) if (tp + fn) else None
    precision = tp / (tp + fp) if (tp + fp) else None
    fpr = fp / (fp + tn) if (fp + tn) else None
    # distinct footage seconds, not per-pair, so alerts/hour is not divided down
    secs = {}
    for r in rows:
        secs[r["clip_id"]] = r.get("duration_s", 0.0)
    total_s = sum(secs.values())
    fp_clips = len({r["clip_id"] for r in rows
                    if brain.score(r["features"]).suspicious and not r["suspicious"]})
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "recall": recall, "precision": precision, "false_positive_rate": fpr,
            "false_alarms_per_hour": (fp_clips / (total_s / 3600.0)
                                      if total_s > 0 else None)}


def _print_night_breakdown(rows: list, brain: BehaviorBrain) -> None:
    """Split the test metrics by day vs night.

    Night is the hard case for cheap CCTV, and an overall number hides it: a
    model can look fine on a set that is mostly daytime while quietly failing
    after dark. Printing the two side by side makes that failure visible instead
    of averaged away.
    """
    night = [r for r in rows if r.get("night")]
    day = [r for r in rows if not r.get("night")]
    if not night or not day:
        return                                   # need both to compare
    print("  by light:")
    for name, subset in (("day  ", day), ("night", night)):
        c = _confusion(subset, brain)
        rec = "n/a" if c["recall"] is None else f"{c['recall']:.2f}"
        fph = ("n/a" if c["false_alarms_per_hour"] is None
               else f"{c['false_alarms_per_hour']:.1f}")
        print(f"    {name}: recall {rec}  FPR {c['false_positive_rate']:.3f}  "
              f"false-alarms/hr {fph}  (n={len(subset)})")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--features", default="training/data/features.jsonl",
                   help="feature rows from training.extract")
    p.add_argument("--synth", action="store_true",
                   help="generate synthetic rows instead (a pipeline self-check)")
    p.add_argument("--per-scenario", type=int, default=80,
                   help="synthetic clips per scenario (with --synth)")
    p.add_argument("--out", default="models/brain.joblib")
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args(argv)

    synthetic = args.synth
    if synthetic:
        from training.synth import dataset
        rows = dataset(per_scenario=args.per_scenario, seed=args.seed)
        print(f"SYNTHETIC self-check: {len(rows)} rows "
              f"({args.per_scenario}/scenario)")
    else:
        rows = read_rows(args.features)
        if not rows:
            print(f"no feature rows at {args.features}")
            print("  run  python -m training.extract   (or pass --synth)")
            return 1
        print(f"loaded {len(rows)} feature rows from {args.features}")

    err = _check_separation(rows)
    if err:
        print("\nREFUSING TO TRAIN — " + err)
        return 2

    by = _split(rows)
    train = by["train"] or rows
    pos = sum(r["suspicious"] for r in rows)
    print(f"  labels : {pos} suspicious, {len(rows) - pos} normal")
    print(f"  splits : train {len(by['train'])}, val {len(by['val'])}, "
          f"test {len(by['test'])}")

    brain = BehaviorBrain()
    report = brain.fit(train, by["val"], synthetic=synthetic)
    head = ("supervised head" if report["supervised_head"]
            else "no supervised head (too few positives — anomaly + gate only)")
    print(f"\ntrained: anomaly head + {head}")
    print(f"  threshold {report['threshold']}  ({report.get('threshold_basis')})")
    if report.get("val"):
        print(f"  val: {report['val']}")

    if by["test"]:
        c = _confusion(by["test"], brain)
        tag = "SYNTHETIC — pipeline check, not a real number" if synthetic \
            else "held-out test"
        print(f"\ntest ({tag}):")
        if c["recall"] is not None:
            print(f"  recall {c['recall']:.2f}   "
                  f"precision {c['precision'] if c['precision'] is not None else float('nan'):.2f}   "
                  f"FPR {c['false_positive_rate']:.3f}")
        if c["false_alarms_per_hour"] is not None:
            print(f"  false alarms/hour: {c['false_alarms_per_hour']:.1f}")
        print(f"  confusion: tp={c['tp']} fp={c['fp']} fn={c['fn']} tn={c['tn']}")
        _print_night_breakdown(by["test"], brain)

    out = brain.save(args.out)
    print(f"\nsaved brain -> {out}")
    if synthetic:
        print("  (synthetic weights — retrain on real footage before a pilot)")
    print("\nThe live pipeline loads this automatically at startup if the file "
          "exists;\nwith it absent, the system runs on the free layer exactly "
          "as before.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
