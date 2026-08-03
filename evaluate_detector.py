#!/usr/bin/env python3
"""Measure the live detector against the labels people made in /train.

This is the point of the whole tagging workbench. Until now the labels went
into a database and stayed there; this reads them back, runs the *production*
detector — the same weights, the same confidence floor, the same classes as
the cameras — over exactly the frames somebody tagged, and reports how much of
what a person could see the system actually sees.

The number that matters is recall by object size. "The detector has 91%
recall" is a number about the dataset. "The detector sees 94% of objects over
80 pixels and 38% of objects under 30" is a number about *your camera*, and it
is an argument you can take to a committee meeting: it says either move the
camera, add light, or pay for a bigger model.

Only annotations somebody signed off are used by default, and frames that were
reconstructed by the tracker rather than observed are excluded outright — they
are not evidence of anything and scoring against them measures the tracker,
not the detector.

    python evaluate_detector.py
    python evaluate_detector.py --status submitted --iou 0.4
    python evaluate_detector.py --clip 3 --json report.json

To compare several settings instead of measuring one, use sweep_detector.py.
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


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--status", default="approved",
                   help="which labels to trust: approved (default), "
                        "submitted, draft, or 'any'")
    p.add_argument("--iou", type=float, default=0.5,
                   help="overlap needed to call it the same object")
    p.add_argument("--clip", type=int, default=0, help="just this clip")
    p.add_argument("--class-agnostic", action="store_true",
                   help="count a detection as correct even if it named the "
                        "wrong class — measures 'did it see something there'")
    p.add_argument("--json", default="", help="write the full report here")
    args = p.parse_args()

    cfg = load_config(args.config)
    db = Database(cfg["storage"]["db_path"])
    try:
        rows = db.object_annotations(
            args.clip or None,
            review_status="" if args.status == "any" else args.status)
        anns = [Annotation.from_row(r) for r in rows]
        clips = {c["id"]: c for c in db.list_training_clips()}
    finally:
        db.close()

    usable, interpolated, off_vocabulary = measure.usable_labels(anns)
    if not usable:
        print(f"No usable labels with status '{args.status}'.")
        print(f"  {len(anns)} annotations found"
              + (f", {interpolated} reconstructed by the tracker"
                 if interpolated else "")
              + (f", {off_vocabulary} outside the detector's classes"
                 if off_vocabulary else ""))
        print("\nTag objects in /train and approve them, then run this again.")
        print("To get a head start:  python prelabel.py --all-clips")
        return 1

    by_frame: dict = defaultdict(list)
    for a in usable:
        by_frame[(a.clip_id, a.frame_index)].append(a)

    from app.detector import Detector
    det = Detector(cfg["detection"])
    name = (f"{cfg['detection'].get('model')} @"
            f"{cfg['detection'].get('imgsz')} conf "
            f"{cfg['detection'].get('confidence')}")
    print(f"detector: {name} on {det.device}")
    print(f"labels:   {len(usable)} objects on {len(by_frame)} frames "
          f"({args.status})\n")

    tally = measure.Tally(name)
    matcher = measure.match if args.class_agnostic else measure.match_per_class
    per_frame = []

    for (clip_id, frame_index), truth_anns in sorted(by_frame.items()):
        clip = clips.get(clip_id)
        if not clip or not Path(clip["path"]).exists():
            print(f"  ! clip {clip_id} is gone from disk, skipping its "
                  f"{len(truth_anns)} labels")
            continue
        got = list(segment_mod.iter_frames(clip["path"], frame_index, 1, 1))
        if not got:
            print(f"  ! clip {clip_id} frame {frame_index} would not decode")
            continue
        _, _, img = got[0]

        t0 = time.time()
        found = [{"bbox": d.xyxy, "cls": d.cls_name, "conf": d.conf}
                 for d in det.track(img)]
        took = time.time() - t0
        truth = [{"bbox": a.bbox, "cls": a.category, "id": a.id}
                 for a in truth_anns]
        matched, missed, extra = matcher(truth, found, args.iou)
        tally.add_frame(truth, matched, missed, extra, took,
                        {"clip": clip["filename"], "frame": frame_index})
        per_frame.append({
            "clip": clip["filename"], "clip_id": clip_id,
            "frame": frame_index, "labelled": len(truth),
            "found": len(found), "matched": len(matched),
            "missed": len(missed), "unlabelled_detections": len(extra),
            "missed_objects": [{"class": truth[i]["cls"],
                                "bbox": [round(v) for v in truth[i]["bbox"]]}
                               for i in missed],
        })

    print("BY CLASS")
    for cls in measure.COMPARABLE:
        c = tally.counts.get(cls)
        if not c or not c["truth"]:
            continue
        print(f"  {cls:12s} {c['hit']:4d}/{c['truth']:<4d} seen   "
              f"{c['hit'] / c['truth'] * 100:5.1f}% recall")

    print("\nBY SIZE  (this is the one that tells you what to do)")
    for _lo, _hi, bucket in measure.BUCKETS:
        c = tally.counts.get(bucket)
        if not c or not c["truth"]:
            continue
        print(f"  {bucket:20s} {c['hit']:4d}/{c['truth']:<4d} seen   "
              f"{c['hit'] / c['truth'] * 100:5.1f}% recall")

    recall = tally.recall()
    print(f"\nOVERALL  {tally.found}/{tally.labelled} labelled objects found "
          f"({recall * 100:.1f}% recall) across {tally.frames} frames "
          f"at {tally.seconds_per_frame:.2f}s each")

    # Two hundred labels off one followed object is one object measured two
    # hundred times, not two hundred measurements. Saying so here is the
    # difference between a number somebody can act on and one that will
    # embarrass them in a meeting.
    share, distinct = measure.track_share(usable)
    if share > 0.4:
        print(f"\n  ! {share * 100:.0f}% of these labels come from "
              f"{distinct} followed object{'' if distinct == 1 else 's'}. "
              "The same object on 200 frames is one object measured 200")
        print("    times, not 200 independent measurements — this tells you "
              "how the detector handles")
        print("    THAT object at THAT distance, and nothing yet about the "
              "camera in general. Tag")
        print("    objects on scattered frames, in different light, before "
              "quoting the percentage.")

    if args.status != "approved":
        print(f"\n  ! these are '{args.status}' labels, not approved ones. If "
              "they were proposed by a")
        print("    model, anything that model also missed is invisible here "
              "and recall is flattered.")

    print(f"\n         {tally.extra} detections had no label — some are real "
          "objects nobody tagged, so")
    print("         this is an upper bound on false positives, not a count "
          "of them")
    if interpolated:
        print(f"         {interpolated} reconstructed frames left out: they "
              "are not sightings")
    if off_vocabulary:
        print(f"         {off_vocabulary} objects outside the detector's "
              "classes left out")

    worst = sorted(per_frame, key=lambda f: -f["missed"])[:5]
    if worst and worst[0]["missed"]:
        print("\nWORST FRAMES")
        for f in worst:
            if not f["missed"]:
                break
            print(f"  {f['clip']} frame {f['frame']}: missed {f['missed']} of "
                  f"{f['labelled']} — "
                  + ", ".join(m["class"] for m in f["missed_objects"][:6]))

    print("\nNext:  python sweep_detector.py    "
          "# is a config change enough, before training anything?")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"status": args.status, "iou": args.iou,
             "class_agnostic": args.class_agnostic,
             "model": cfg["detection"].get("model"),
             "confidence": cfg["detection"].get("confidence"),
             "track_share": round(share, 3), "distinct_tracks": distinct,
             **tally.summary(), "frames_detail": per_frame}, indent=1))
        print(f"\nfull report written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
