#!/usr/bin/env python3
"""Train VisionGuard on real footage — one command, on your own GPU laptop.

    python -m training.pilot_train                 # false-alarm side (MEVA normals)
    python -m training.pilot_train --ucf D:\\ucf    # + real crime positives (recall)

This runs the whole real-data pipeline end to end, in the storage-safe way (it
never keeps more than a little raw video on disk at once):

    mine a spread of real MEVA cameras  ->  (optional) mine your UCF-Crime clips
    ->  assign source-separated splits  ->  extract features with the detector
    ->  train the brain  ->  print the numbers

Why a spread of cameras: on any one scene, people and cars may rarely interact,
so a single camera yields few useful windows (measured: ~0.3/clip on a hospital
forecourt). Mining many cameras across day and night captures whatever
interaction exists and gives the brain a varied idea of "normal". A GPU makes
this cheap — the slow part is the detector, and that is exactly what your GPU is
for.

Everything here just calls the ordinary tools (clipmine, splits_cli, extract,
brain_train, evaluate_continuous) in order — so if a step fails you can run it by
hand from the command it prints. `--dry-run` prints the commands and runs
nothing.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# A spread across the big MEVA pool (drops-123-r13, 329h, hours 00-17). Picked
# for having the most footage available; override with --cameras if you like.
DEFAULT_CAMERAS = ["G330", "G331", "G420", "G421", "G423", "G474",
                   "G475", "G476", "G479", "G505", "G506", "G508"]
POOL = "drops-123-r13"


def plan(args) -> list[list[str]]:
    """Build the exact command sequence. Pure, so it can be shown and tested."""
    py = [sys.executable, "-m"]
    out = args.out
    cmds: list[list[str]] = []

    if not args.skip_mine:
        cams = [c.strip() for c in args.cameras.split(",") if c.strip()]
        for cam in cams:
            base = py + ["training.clipmine", "--source", "meva",
                         "--camera", cam, "--pool", args.pool,
                         "--sources", str(args.sources), "--clips", str(args.clips),
                         "--clip-s", "6", "--width", str(args.width),
                         "--budget-gb", str(args.budget_gb), "--out", out,
                         "--specialist", "vehicle"]
            cmds.append(base)
            if args.night:                       # a dedicated night pass per camera
                cmds.append(base + ["--hours", "night"])
        if args.ucf:
            # real positives: your local UCF-Crime clips, mined as break-ins
            cmds.append(py + ["training.clipmine", "--source", "local",
                              "--dir", args.ucf, "--out", out,
                              "--specialist", "break_in",
                              "--label", "HOUSE_BREAK_IN"])

    manifest = str(Path(out) / "manifest.jsonl")
    features = str(Path(out) / "features.jsonl")
    cmds.append(py + ["training.splits_cli", "--manifest", manifest])
    cmds.append(py + ["training.extract", "--manifest", manifest,
                      "--out", features, "--stride", str(args.stride)])
    cmds.append(py + ["training.brain_train", "--features", features,
                      "--out", args.model])
    if args.eval_meva:
        cmds.append(py + ["training.evaluate_continuous", "--meva",
                          str(args.eval_meva), "--camera", "G330"])
    return cmds


def run(cmds: list[list[str]]) -> int:
    for i, cmd in enumerate(cmds, 1):
        print(f"\n{'='*70}\n[{i}/{len(cmds)}] {' '.join(cmd)}\n{'='*70}",
              flush=True)
        r = subprocess.run(cmd)
        if r.returncode != 0:
            print(f"\n! step {i} failed (exit {r.returncode}).")
            print("  You can re-run just that command above by hand once it's "
                  "fixed,\n  then re-run pilot_train with --skip-mine to carry on.")
            return r.returncode
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cameras", default=",".join(DEFAULT_CAMERAS),
                   help="MEVA camera ids to mine, comma-separated")
    p.add_argument("--pool", default=POOL)
    p.add_argument("--sources", type=int, default=8,
                   help="source videos per camera")
    p.add_argument("--clips", type=int, default=6, help="clips per source video")
    p.add_argument("--width", type=int, default=1280,
                   help="keep clips this wide (people survive at 1280)")
    p.add_argument("--budget-gb", type=float, default=3.0,
                   help="never keep more than this much clip data at once")
    p.add_argument("--night", action="store_true",
                   help="also do a dedicated night pass per camera")
    p.add_argument("--ucf", default="",
                   help="folder of your downloaded UCF-Crime clips (real positives)")
    p.add_argument("--stride", type=int, default=3,
                   help="detect every Nth frame during extraction")
    p.add_argument("--out", default="training/data")
    p.add_argument("--model", default="models/brain.joblib")
    p.add_argument("--eval-meva", type=int, default=2,
                   help="also score N fresh MEVA videos for a false-alarm number "
                        "(0 to skip)")
    p.add_argument("--skip-mine", action="store_true",
                   help="don't mine again; just re-split/extract/train")
    p.add_argument("--dry-run", action="store_true",
                   help="print the commands and run nothing")
    args = p.parse_args(argv)

    cmds = plan(args)
    if args.dry_run:
        for c in cmds:
            print(" ".join(c))
        return 0

    try:
        import torch
        dev = "cuda (your GPU)" if torch.cuda.is_available() else "CPU (slow!)"
    except Exception:                            # noqa: BLE001
        dev = "unknown"
    print(f"detector will run on: {dev}")
    if "CPU" in dev:
        print("  ! no GPU detected — this will be very slow. See "
              "docs/TRAIN_ON_YOUR_LAPTOP.md")

    rc = run(cmds)
    if rc == 0:
        print("\n" + "=" * 70)
        print("Done. The brain is trained on real footage and saved to "
              f"{args.model}.")
        print("The number you may quote is the held-out 'test' line above — and\n"
              "only after you have real positives (--ucf) does 'recall' mean\n"
              "anything. Without them you have measured the false-alarm side only,\n"
              "which is priority #1 but not the whole picture.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
