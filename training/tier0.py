#!/usr/bin/env python3
"""Tier 0: a gradient-boosted tree on track features. No GPU, no pixels.

    python -m training.tier0 --features training/data/features.jsonl
    python -m training.tier0 --gate-only          # no positives yet? still useful

The baseline every later model must beat. It trains in seconds on a CPU, needs
a few hundred examples rather than a few thousand, and reports which features
carried the decision — so a claim can be checked against the video instead of
believed.

TWO MODES, BECAUSE THE DATA ARRIVES IN TWO STAGES

  **gate** — with negatives only. A hand-written rule over the same features
  ("close for a long time, and did not simply walk past") measured against
  ordinary footage. This gives a FALSE-ALARM RATE, which is priority #1, and
  it needs no positive examples at all. It says nothing about recall, and
  says so.

  **model** — once UCF positives exist, the same features go into a
  HistGradientBoostingClassifier and the full picture appears.

The threshold is chosen on validation, never on test, and the choice is made
by fixing recall and minimising false alarms — not by maximising accuracy. On
a set that is 98% normal, "always say normal" scores 98% accurate and is
worthless.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from training import features as F
from training.extract import read_rows

# The hand-written gate, in the vocabulary of the features. Deliberately the
# thing a person would say out loud: they stayed close for a while, and they
# did not simply walk past. Anything a tree learns has to beat this.
GATE = {
    "min_near_s": 12.0,        # unbroken seconds within a vehicle radius
    "max_straightness": 0.45,  # 1.0 = walked in a straight line
    "min_contact_frames": 1.0,
}

# Below this many training rows, sklearn's internal early-stopping holdout is
# too small to mean anything. See train_model().
EARLY_STOPPING_MIN_ROWS = 2000


def gate_fires(feats: dict, gate: dict | None = None) -> bool:
    """True if this pair looks worth interrupting somebody for."""
    g = {**GATE, **(gate or {})}
    lingered = feats.get("longest_near_run_s", 0.0) >= g["min_near_s"]
    not_passing = feats.get("straightness", 1.0) <= g["max_straightness"]
    touched = feats.get("contact_frames", 0.0) >= g["min_contact_frames"]
    return bool(lingered and not_passing and touched)


def confusion(y_true, y_pred) -> dict:
    tp = sum(1 for t, p in zip(y_true, y_pred) if t and p)
    fp = sum(1 for t, p in zip(y_true, y_pred) if not t and p)
    fn = sum(1 for t, p in zip(y_true, y_pred) if t and not p)
    tn = sum(1 for t, p in zip(y_true, y_pred) if not t and not p)
    recall = tp / (tp + fn) if tp + fn else None
    precision = tp / (tp + fp) if tp + fp else None
    specificity = tn / (tn + fp) if tn + fp else None
    balanced = ((recall + specificity) / 2
                if recall is not None and specificity is not None else None)
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "recall": recall, "precision": precision,
            "false_positive_rate": (fp / (fp + tn)) if fp + tn else None,
            "balanced_accuracy": balanced}


def alerts_per_hour(fires: int, seconds: float) -> float | None:
    return (fires / (seconds / 3600.0)) if seconds > 0 else None


def split_rows(rows) -> dict:
    out: dict = {"train": [], "val": [], "test": [], "": []}
    for r in rows:
        out.setdefault(r.get("split") or "", []).append(r)
    return out


def footage_seconds(rows) -> float:
    """Seconds of distinct footage, not summed over pairs.

    A clip with four candidate pairs is still one clip's worth of time; adding
    it up per pair would quarter the false-alarm rate by arithmetic.
    """
    seen = {}
    for r in rows:
        seen[r["clip_id"]] = r.get("duration_s", 0.0)
    return sum(seen.values())


# ------------------------------------------------------------------ modes
def run_gate(rows, gate=None) -> dict:
    fires = [gate_fires(r["features"], gate) for r in rows]
    truth = [bool(r["suspicious"]) for r in rows]
    seconds = footage_seconds(rows)
    out = {"rows": len(rows), "clips": len({r["clip_id"] for r in rows}),
           "footage_s": round(seconds, 1),
           "fired": sum(fires),
           "alerts_per_hour": alerts_per_hour(sum(fires), seconds)}
    if any(truth):
        out["confusion"] = confusion(truth, fires)
    else:
        out["false_alarms"] = sum(fires)     # every row is a negative
    out["examples"] = [r["why"] for r, f in zip(rows, fires) if f][:5]
    return out


def train_model(train_rows, val_rows, seed: int = 0):
    """Fit the tree. Returns (model, threshold, report) or None if impossible."""
    from sklearn.ensemble import HistGradientBoostingClassifier

    ytr = [r["suspicious"] for r in train_rows]
    if len(set(ytr)) < 2:
        return None
    Xtr = [F.to_vector(r["features"]) for r in train_rows]

    # Class weighting rather than resampling: the ratio is the real ratio, and
    # duplicating rare positives teaches the tree that a handful of specific
    # examples are very important.
    counts = Counter(ytr)
    weight = {c: len(ytr) / (2 * n) for c, n in counts.items()}

    # Early stopping only when there is enough data to hold a slice back.
    # Measured: forcing it on with 40 examples stopped after 10 iterations and
    # produced a model that could not separate two obviously separable
    # scenarios — the internal validation split was four rows, so its estimate
    # of "no longer improving" was noise. This tier is *for* the few-hundred
    # regime, so at that size the regularisation does the work instead.
    small = len(ytr) < EARLY_STOPPING_MIN_ROWS
    model = HistGradientBoostingClassifier(
        max_depth=4, max_iter=200, learning_rate=0.06,
        l2_regularization=1.0, early_stopping=not small, random_state=seed)
    model.fit(Xtr, ytr, sample_weight=[weight[y] for y in ytr])

    # Threshold on VALIDATION, and chosen to minimise false alarms at a fixed
    # recall — not to maximise accuracy, which on a 98%-normal set rewards
    # saying "normal" every time.
    best = (0.5, None)
    if val_rows and len({r["suspicious"] for r in val_rows}) > 1:
        Xv = [F.to_vector(r["features"]) for r in val_rows]
        yv = [bool(r["suspicious"]) for r in val_rows]
        probs = model.predict_proba(Xv)[:, 1]
        best_fp = None
        for t in [i / 100 for i in range(5, 100, 5)]:
            c = confusion(yv, [p >= t for p in probs])
            if c["recall"] is not None and c["recall"] >= 0.80:
                if best_fp is None or c["fp"] < best_fp:
                    best_fp, best = c["fp"], (t, c)
    return model, best[0], best[1]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--features", default="training/data/features.jsonl")
    p.add_argument("--gate-only", action="store_true")
    p.add_argument("--near-s", type=float, default=GATE["min_near_s"])
    p.add_argument("--straightness", type=float, default=GATE["max_straightness"])
    p.add_argument("--json", default="")
    args = p.parse_args(argv)

    rows = read_rows(args.features)
    if not rows:
        print(f"no feature rows at {args.features}")
        print("  python -m training.extract")
        return 1

    gate = {"min_near_s": args.near_s, "max_straightness": args.straightness}
    by = split_rows(rows)
    pos = sum(r["suspicious"] for r in rows)
    print(f"rows     : {len(rows)} from "
          f"{len({r['clip_id'] for r in rows})} clips, "
          f"{len({r['source_video'] for r in rows})} source videos")
    print(f"labels   : {pos} suspicious, {len(rows) - pos} normal")
    print(f"splits   : " + ", ".join(
        f"{k or 'unassigned'} {len(v)}" for k, v in by.items() if v))

    # ---- the hand-written gate, on everything --------------------------
    print(f"\nGATE  (close for >= {gate['min_near_s']:.0f}s unbroken, "
          f"straightness <= {gate['max_straightness']:.2f}, and touching)")
    g = run_gate(rows, gate)
    print(f"  fired on {g['fired']} of {g['rows']} candidate pairs "
          f"across {g['clips']} clips ({g['footage_s']:.0f}s of footage)")
    if g.get("alerts_per_hour") is not None:
        print(f"  -> {g['alerts_per_hour']:.1f} alerts per hour")
    if "confusion" in g:
        c = g["confusion"]
        print(f"  recall {c['recall']:.2f}  precision "
              f"{c['precision'] if c['precision'] is not None else float('nan'):.2f}"
              f"  FPR {c['false_positive_rate']:.3f}")
    else:
        print(f"  every row is ordinary footage, so all {g['fired']} firings "
              "are FALSE ALARMS")
    for e in g["examples"]:
        print(f"     fired: {e}")

    # ---- the model, if there is anything to learn ----------------------
    report = {"gate": g}
    if not args.gate_only:
        trained = train_model(by["train"] or rows, by["val"])
        if trained is None:
            print("\nMODEL  not trained: the data has only one class.")
            print("  A classifier needs positives. Mine UCF-Crime incidents:")
            print("    python -m training.clipmine --source local --dir <ucf> \\")
            print("        --specialist break_in --label HOUSE_BREAK_IN")
            print("\n  Until then the gate above is the honest measurement: it "
                  "reports the")
            print("  false-alarm side, which is priority #1, and says nothing "
                  "about recall.")
        else:
            model, threshold, val_c = trained
            print(f"\nMODEL  HistGradientBoosting, threshold {threshold:.2f} "
                  "chosen on validation")
            if val_c:
                print(f"  val: recall {val_c['recall']:.2f}  "
                      f"FPR {val_c['false_positive_rate']:.3f}  "
                      f"balanced {val_c['balanced_accuracy']:.2f}")
            if by["test"]:
                Xt = [F.to_vector(r["features"]) for r in by["test"]]
                yt = [bool(r["suspicious"]) for r in by["test"]]
                probs = model.predict_proba(Xt)[:, 1]
                c = confusion(yt, [q >= threshold for q in probs])
                print(f"  test: {c}")
                report["test"] = c
            imp = permutation_importance(model, by["val"] or by["train"])
            if imp:
                print("\n  what carried the decision:")
                for name, score in imp[:8]:
                    print(f"    {name:<26}{score:+.3f}")
                report["importances"] = imp
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1, default=str))
        print(f"\nwritten to {args.json}")
    return 0


def permutation_importance(model, rows, repeats: int = 3) -> list:
    """Which features the model actually leaned on.

    Permutation rather than a built-in score: it measures the effect on THIS
    data of destroying one column, which is what somebody asking "why did it
    say that" wants to know.
    """
    if not rows or len({r["suspicious"] for r in rows}) < 2:
        return []
    import random

    X = [F.to_vector(r["features"]) for r in rows]
    y = [bool(r["suspicious"]) for r in rows]
    base = confusion(y, model.predict(X))["balanced_accuracy"]
    if base is None:
        return []
    rng = random.Random(0)
    out = []
    for i, name in enumerate(F.FEATURES):
        drops = []
        for _ in range(repeats):
            shuffled = [row[:] for row in X]
            col = [row[i] for row in shuffled]
            rng.shuffle(col)
            for row, v in zip(shuffled, col):
                row[i] = v
            c = confusion(y, model.predict(shuffled))["balanced_accuracy"]
            if c is not None:
                drops.append(base - c)
        if drops:
            out.append((name, sum(drops) / len(drops)))
    return sorted(out, key=lambda kv: -kv[1])


if __name__ == "__main__":
    raise SystemExit(main())
