#!/usr/bin/env python3
"""What would a guard's phone actually have shown, over this footage?

    python evaluate_alerts.py

`validate_triggers.py` measures the candidate trigger — the wide net that
decides what is worth a closer look. This measures the thing above it: the
rules engine plus the scoring layer, which is what now decides whether a
person's phone buzzes at all.

The two answer different questions and both matter. The trigger firing often
is by design (it is cheap and its job is not to miss). Alerts firing often is
the product being useless.

Reports, per clip: how many events the rules raised, how many the scoring
layer dismissed before anyone saw them, and what survived — which is the
number a resident would experience as "how often does this thing cry wolf".
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

import cv2
import yaml

from app.detector import Detector
from app.rules import RulesEngine


def load_labels(testset: Path) -> dict[str, dict]:
    f = testset / "labels.csv"
    if not f.exists():
        return {}
    with f.open() as fh:
        return {r["filename"]: r for r in csv.DictReader(fh)}


def run_clip(path: Path, detector, cfg: dict, fps_target: float = 4.0):
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(round(fps / fps_target)))
    engine = RulesEngine("parking", {}, cfg["rules"])
    raised, i = [], 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i % step == 0:
            ts = i / fps
            for ev in engine.update(detector.track(frame), ts=ts):
                raised.append(ev)
        i += 1
    cap.release()
    return raised, engine.dismissed, i / fps


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--testset", default="testset")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--fps", type=float, default=4.0,
                    help="frames analysed per second of footage")
    args = ap.parse_args(argv)

    cfg = yaml.safe_load(Path(args.config).read_text())
    root = Path(args.testset)
    clips = sorted(p for p in (root / "clips").glob("*")
                   if p.suffix.lower() in {".mp4", ".avi", ".mkv", ".mov"})
    if not clips:
        sys.exit(f"no clips in {root / 'clips'} — run fetch_testset.py first")
    labels = load_labels(root)

    print("Loading detector...", flush=True)
    detector = Detector(cfg["detection"])

    scoring_on = (cfg.get("scoring") or {}).get("enabled", True)
    print(f"scoring layer: {'ON' if scoring_on else 'OFF'}\n")
    print(f"{'clip':<44}{'label':<9}{'alerts':<8}{'dismissed':<11}minutes")
    print("-" * 84)

    tot_alerts = tot_dismissed = 0.0
    tot_minutes = 0.0
    by_sev: Counter = Counter()
    noisy_clips = 0
    for p in clips:
        lab = labels.get(p.name, {})
        kind = lab.get("type", "?")
        raised, dismissed, seconds = run_clip(p, detector, cfg, args.fps)
        for ev in raised:
            by_sev[ev.severity] += 1
        tot_alerts += len(raised)
        tot_dismissed += len(dismissed)
        tot_minutes += seconds / 60
        noisy_clips += bool(raised)
        print(f"{p.name[:43]:<44}{kind:<9}{len(raised):<8}{len(dismissed):<11}"
              f"{seconds/60:.1f}")

    print("-" * 84)
    print(f"\n{len(clips)} clips, {tot_minutes:.1f} minutes of footage")
    print(f"alerts raised      : {tot_alerts:.0f}"
          f"   ({tot_alerts/max(tot_minutes,1e-9):.2f} per minute)")
    print(f"dismissed by score : {tot_dismissed:.0f}"
          f"   (never reached anyone)")
    if tot_alerts + tot_dismissed:
        share = tot_dismissed / (tot_alerts + tot_dismissed) * 100
        print(f"                     the scoring layer suppressed {share:.0f}% "
              f"of what the rules raised")
    print(f"clips that alerted : {noisy_clips}/{len(clips)}")
    if by_sev:
        print("by severity        : " +
              ", ".join(f"{k} {v}" for k, v in sorted(by_sev.items())))

    normals = [p for p in clips if labels.get(p.name, {}).get("incident") == "0"]
    if normals and tot_alerts:
        print("\nEvery clip here is ordinary activity. On this footage each alert "
              "is\na false alarm, so the number above is the noise a resident "
              "would live with.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
