#!/usr/bin/env python3
"""Try several detector settings against the same labels, and print the table.

Run this BEFORE considering any fine-tuning. Training is days of work and a
GPU; input size and the confidence floor are one line of config each, and on
small objects they are usually worth more than either. The only way to know
which is to measure them against the same labelled frames, which is what this
does.

Every configuration sees exactly the same frames in the same order, decoded
once and handed to all of them, so the comparison is paired: differences
between rows are differences between configurations, not between samples.

    python sweep_detector.py
    python sweep_detector.py --preset small-objects
    python sweep_detector.py --models yolo11n.pt,yolo11s.pt --imgsz 640,1280
    python sweep_detector.py --json sweep.json

The seconds-per-frame column is not a footnote. A society runs this on one
cheap box watching several cameras; a setting that finds every person at four
seconds a frame is a setting that watches one camera at a quarter of real
time, which is not a security system. Read recall and cost together.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import measure
from app import segment as segment_mod
from app.annotations import Annotation
from app.db import Database
from app.main import load_config

# Named starting points, so the useful comparisons do not depend on somebody
# guessing good numbers on the command line.
PRESETS = {
    "default": {
        "models": ["yolo11n.pt"], "imgsz": [640], "conf": [0.35],
        "why": "just the production setting, as a baseline",
    },
    "small-objects": {
        "models": ["yolo11n.pt", "yolo11s.pt"], "imgsz": [640, 1280],
        "conf": [0.35, 0.15],
        "why": "the two knobs that move small-object recall, and the next "
               "model size up",
    },
    "cheap": {
        "models": ["yolo11n.pt"], "imgsz": [640, 960, 1280],
        "conf": [0.35, 0.25, 0.15],
        "why": "same weights throughout — everything here is free to deploy",
    },
    "thorough": {
        "models": ["yolo11n.pt", "yolo11s.pt", "yolo11m.pt"],
        "imgsz": [640, 1280], "conf": [0.35, 0.15],
        "why": "adds the medium model; downloads ~40MB and is slow on CPU",
    },
}


def parse_list(s: str, cast):
    return [cast(x.strip()) for x in s.split(",") if x.strip()]


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--preset", default="small-objects", choices=sorted(PRESETS))
    p.add_argument("--models", default="", help="comma separated weights")
    p.add_argument("--imgsz", default="", help="comma separated input sizes")
    p.add_argument("--conf", default="", help="comma separated floors")
    p.add_argument("--status", default="approved",
                   help="which labels to trust (approved | submitted | any)")
    p.add_argument("--iou", type=float, default=0.5)
    p.add_argument("--clip", type=int, default=0)
    p.add_argument("--max-frames", type=int, default=0,
                   help="cap the frames scored, for a quick look")
    p.add_argument("--class-agnostic", action="store_true",
                   help="score 'did it see something there', ignoring class")
    p.add_argument("--json", default="")
    args = p.parse_args()

    preset = PRESETS[args.preset]
    models = parse_list(args.models, str) if args.models else preset["models"]
    sizes = parse_list(args.imgsz, int) if args.imgsz else preset["imgsz"]
    confs = parse_list(args.conf, float) if args.conf else preset["conf"]

    cfg = load_config(args.config)
    base = dict(cfg.get("detection") or {})
    db = Database(cfg["storage"]["db_path"])
    try:
        rows = db.object_annotations(
            args.clip or None,
            review_status="" if args.status == "any" else args.status)
        anns = [Annotation.from_row(r) for r in rows]
        clips = {c["id"]: c for c in db.list_training_clips()}
    finally:
        db.close()

    usable, interpolated, off_vocab = measure.usable_labels(anns)
    if not usable:
        print(f"No usable labels with status '{args.status}'.")
        print(f"  {len(anns)} annotations found"
              + (f", {interpolated} reconstructed by the tracker" if interpolated else "")
              + (f", {off_vocab} outside the detector's classes" if off_vocab else ""))
        print("\nTag objects in /train, approve them, then run this again.")
        print("Or pre-label a batch first:  python prelabel.py --clip N")
        return 1

    by_frame: dict = defaultdict(list)
    for a in usable:
        by_frame[(a.clip_id, a.frame_index)].append(a)
    frames = sorted(by_frame)
    if args.max_frames:
        frames = frames[:args.max_frames]

    share, distinct = measure.track_share(usable)
    print(f"labels: {len(usable)} objects on {len(frames)} frames "
          f"({args.status})")
    if share > 0.4:
        print(f"        ! {share * 100:.0f}% of them come from {distinct} "
              f"followed object{'' if distinct == 1 else 's'} — good enough to "
              "compare settings against")
        print("          each other, not to quote as this camera's accuracy")
    print(f"combinations: {len(models)} models x {len(sizes)} sizes x "
          f"{len(confs)} floors = {len(models) * len(sizes) * len(confs)}")
    print(f"({preset['why']})\n")

    from app.detector import Detector
    runs = []
    for m in models:
        for s in sizes:
            for c in confs:
                d = {**base, "model": m, "imgsz": s, "confidence": c}
                name = f"{Path(m).stem} @{s} conf{c}"
                try:
                    runs.append((name, d, Detector(d), measure.Tally(name)))
                except Exception as e:                      # noqa: BLE001
                    print(f"  ! {name} could not load: {e}")
    if not runs:
        print("no detector could be loaded")
        return 1

    # One throwaway inference each, before anything is timed. The first call
    # into a model pays for lazy CUDA/BLAS init and warm-up allocations, and
    # without this that cost lands entirely on whichever configuration happens
    # to be first in the list — which made the cheapest row look like the
    # slowest one.
    warm = next((list(segment_mod.iter_frames(clips[c]["path"], f, 1, 1))
                 for c, f in frames[:1]
                 if c in clips and Path(clips[c]["path"]).exists()), None)
    if warm:
        for _name, _d, det, _t in runs:
            det.track(warm[0][2])

    # Decode each frame once and give it to every configuration. Decoding is
    # the shared cost and the frames must be identical for the comparison to
    # be paired.
    print(f"scoring {len(frames)} frames against {len(runs)} configurations "
          "(this is the slow part)\n")
    for n, (clip_id, frame_index) in enumerate(frames, 1):
        clip = clips.get(clip_id)
        if not clip or not Path(clip["path"]).exists():
            continue
        got = list(segment_mod.iter_frames(clip["path"], frame_index, 1, 1))
        if not got:
            continue
        _, _, img = got[0]
        truth = [{"bbox": a.bbox, "cls": a.category}
                 for a in by_frame[(clip_id, frame_index)]]
        where = {"clip": clip["filename"], "frame": frame_index}

        for _name, _d, det, tally in runs:
            t0 = time.time()
            found = [{"bbox": x.xyxy, "cls": x.cls_name, "conf": x.conf}
                     for x in det.track(img)]
            took = time.time() - t0
            matcher = (measure.match if args.class_agnostic
                       else measure.match_per_class)
            matched, missed, extra = matcher(truth, found, args.iou)
            tally.add_frame(truth, matched, missed, extra, took, where)
        if n % 10 == 0 or n == len(frames):
            print(f"  {n}/{len(frames)} frames", end="\r", flush=True)
    print(" " * 40, end="\r")

    # ---- the table ------------------------------------------------------
    sizes_seen = [name for _lo, _hi, name in measure.BUCKETS
                  if any(name in t.counts for *_x, t in runs)]
    head = f"{'configuration':<24}{'recall':>8}{'s/frame':>9}  "
    head += "".join(f"{n.split(' ')[0]:>9}" for n in sizes_seen)
    head += f"{'unlabelled':>12}"
    print(head)
    print("-" * len(head))

    best = None
    for name, _d, _det, t in runs:
        r = t.recall()
        row = f"{name:<24}{(f'{r * 100:.1f}%' if r is not None else '-'):>8}"
        row += f"{t.seconds_per_frame:>9.2f}  "
        for bucket in sizes_seen:
            br = t.recall(bucket)
            row += f"{(f'{br * 100:.0f}%' if br is not None else '-'):>9}"
        row += f"{t.extra:>12}"
        print(row)
        if r is not None and (best is None or r > best[1]):
            best = (name, r, t)

    print("\nsize columns are the first word of each bucket: "
          + ", ".join(f"{n.split(' ')[0]}={n}" for n in sizes_seen))
    print("'unlabelled' counts detections with no label — partly real objects "
          "nobody tagged,")
    print("so treat it as an upper bound on false positives, not a count.")

    if best:
        base_row = next((t for n, _d, _x, t in runs
                         if n.startswith(Path(base.get("model", "")).stem)
                         and f"@{base.get('imgsz')}" in n
                         and f"conf{base.get('confidence')}" in n), None)
        print(f"\nbest recall: {best[0]} at {best[1] * 100:.1f}%")
        if base_row is not None and base_row is not best[2]:
            gain = (best[1] - base_row.recall()) * 100
            cost = (best[2].seconds_per_frame
                    / max(base_row.seconds_per_frame, 1e-9))
            print(f"vs production ({base_row.name}): "
                  f"{gain:+.1f} points of recall for {cost:.1f}x the "
                  "inference time")
            print("\nIf that trade is acceptable it is a config change in "
                  "config.yaml under `detection:`,")
            print("and no training is needed. If it is not, that is the "
                  "argument for fine-tuning —")
            print("smaller weights that already know what your camera looks "
                  "like beat bigger generic ones.")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "status": args.status, "iou": args.iou,
            "frames": len(frames), "labels": len(usable),
            "track_share": round(share, 3), "distinct_tracks": distinct,
            "runs": [t.summary() for _n, _d, _x, t in runs]}, indent=1))
        print(f"\nwritten to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
