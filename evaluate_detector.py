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
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import segment as segment_mod
from app.annotations import Annotation
from app.db import Database
from app.main import load_config

# The workbench vocabulary is wider than the detector's. Anything the live
# model has no class for cannot be a miss on its part, so it is left out of
# the score rather than counted against it.
COMPARABLE = {"person", "car", "motorcycle", "bicycle", "bus", "truck"}

# Size buckets in pixels of box diagonal. Chosen because this is where the
# answer changes: on a car-park camera a person at 40px is a different
# detection problem from a car at 250px, and one average hides both.
BUCKETS = [(0, 40, "tiny (<40px)"), (40, 80, "small (40-80px)"),
           (80, 160, "medium (80-160px)"), (160, 10 ** 9, "large (>160px)")]


def bucket_for(diag: float) -> str:
    for lo, hi, name in BUCKETS:
        if lo <= diag < hi:
            return name
    return BUCKETS[-1][2]


def box_iou(a, b) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def match(truth: list, found: list, iou_gate: float) -> tuple:
    """Greedy one-to-one matching, best overlap first.

    Greedy rather than optimal (Hungarian) on purpose: at these object counts
    the two agree almost always, and a matcher somebody can follow in their
    head is worth more here than the last half percent.
    """
    pairs = []
    for i, t in enumerate(truth):
        for j, d in enumerate(found):
            v = box_iou(t["bbox"], d["bbox"])
            if v >= iou_gate:
                pairs.append((v, i, j))
    pairs.sort(reverse=True)
    used_t, used_d, matched = set(), set(), []
    for v, i, j in pairs:
        if i in used_t or j in used_d:
            continue
        used_t.add(i)
        used_d.add(j)
        matched.append((i, j, v))
    missed = [i for i in range(len(truth)) if i not in used_t]
    extra = [j for j in range(len(found)) if j not in used_d]
    return matched, missed, extra


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
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

    # Reconstructed frames are not sightings. Scoring against them would
    # measure our own interpolation, and it would flatter or damn the detector
    # for a shape no camera ever produced.
    usable = [a for a in anns
              if a.source != "interpolated" and a.category in COMPARABLE]
    dropped_interp = sum(1 for a in anns if a.source == "interpolated")
    dropped_class = sum(1 for a in anns
                        if a.source != "interpolated"
                        and a.category not in COMPARABLE)

    if not usable:
        print(f"No usable labels with status '{args.status}'.")
        print(f"  {len(anns)} annotations found"
              + (f", {dropped_interp} reconstructed by the tracker" if dropped_interp else "")
              + (f", {dropped_class} outside the detector's classes" if dropped_class else ""))
        print("\nTag some objects in /train and approve them, then run this again.")
        return 1

    by_frame: dict = defaultdict(list)
    for a in usable:
        by_frame[(a.clip_id, a.frame_index)].append(a)

    from app.detector import Detector
    det = Detector(cfg["detection"])
    print(f"detector: {cfg['detection'].get('model')} at conf "
          f"{cfg['detection'].get('confidence')} on {det.device}")
    print(f"labels:   {len(usable)} objects on {len(by_frame)} frames "
          f"({args.status})\n")

    stats = defaultdict(lambda: {"truth": 0, "hit": 0, "extra": 0})
    per_frame = []
    total_extra = 0

    for (clip_id, frame_index), truth_anns in sorted(by_frame.items()):
        clip = clips.get(clip_id)
        if not clip or not Path(clip["path"]).exists():
            print(f"  ! clip {clip_id} is gone from disk, skipping its "
                  f"{len(truth_anns)} labels")
            continue
        fps, _, _, _ = segment_mod.clip_shape(clip["path"])
        got = list(segment_mod.iter_frames(clip["path"], frame_index, 1, 1))
        if not got:
            print(f"  ! clip {clip_id} frame {frame_index} would not decode")
            continue
        _, _, img = got[0]

        found = [{"bbox": d.xyxy, "cls": d.cls_name, "conf": d.conf}
                 for d in det.track(img)]
        truth = [{"bbox": a.bbox, "cls": a.category, "id": a.id}
                 for a in truth_anns]

        if not args.class_agnostic:
            # match within class, so a car found where a person is does not
            # count as seeing the person
            matched, missed, extra = [], [], list(range(len(found)))
            for cls in {t["cls"] for t in truth}:
                ti = [i for i, t in enumerate(truth) if t["cls"] == cls]
                di = [j for j in extra if found[j]["cls"] == cls]
                m, ms, _ = match([truth[i] for i in ti],
                                 [found[j] for j in di], args.iou)
                for a, b, v in m:
                    matched.append((ti[a], di[b], v))
                    extra.remove(di[b])
                missed += [ti[i] for i in ms]
        else:
            matched, missed, extra = match(truth, found, args.iou)

        for i, _j, _v in matched:
            t = truth[i]
            diag = ((t["bbox"][2] - t["bbox"][0]) ** 2
                    + (t["bbox"][3] - t["bbox"][1]) ** 2) ** 0.5
            stats[t["cls"]]["truth"] += 1
            stats[t["cls"]]["hit"] += 1
            stats[bucket_for(diag)]["truth"] += 1
            stats[bucket_for(diag)]["hit"] += 1
        for i in missed:
            t = truth[i]
            diag = ((t["bbox"][2] - t["bbox"][0]) ** 2
                    + (t["bbox"][3] - t["bbox"][1]) ** 2) ** 0.5
            stats[t["cls"]]["truth"] += 1
            stats[bucket_for(diag)]["truth"] += 1
        total_extra += len(extra)

        per_frame.append({
            "clip": clip["filename"], "clip_id": clip_id,
            "frame": frame_index, "labelled": len(truth),
            "found": len(found), "matched": len(matched),
            "missed": len(missed), "unlabelled_detections": len(extra),
            "missed_objects": [{"class": truth[i]["cls"],
                                "bbox": [round(v) for v in truth[i]["bbox"]]}
                               for i in missed],
        })

    hits = sum(v["hit"] for k, v in stats.items() if k in COMPARABLE)
    truths = sum(v["truth"] for k, v in stats.items() if k in COMPARABLE)

    print("BY CLASS")
    for cls in sorted(COMPARABLE):
        s = stats.get(cls)
        if not s or not s["truth"]:
            continue
        print(f"  {cls:12s} {s['hit']:4d}/{s['truth']:<4d} seen   "
              f"{s['hit'] / s['truth'] * 100:5.1f}% recall")

    print("\nBY SIZE  (this is the one that tells you what to do)")
    for _lo, _hi, name in BUCKETS:
        s = stats.get(name)
        if not s or not s["truth"]:
            continue
        print(f"  {name:20s} {s['hit']:4d}/{s['truth']:<4d} seen   "
              f"{s['hit'] / s['truth'] * 100:5.1f}% recall")

    print(f"\nOVERALL  {hits}/{truths} labelled objects found "
          f"({hits / truths * 100:.1f}% recall) across {len(per_frame)} frames")

    # Two hundred labels off one followed object is one object measured two
    # hundred times, not two hundred measurements. Saying so here is the
    # difference between a number somebody can act on and one that will
    # embarrass them in a meeting.
    from_tracks = sum(1 for a in usable if a.track_ref)
    distinct = len({a.track_ref for a in usable if a.track_ref})
    if from_tracks > len(usable) * 0.4:
        share = from_tracks / len(usable) * 100
        print(f"\n  ! {share:.0f}% of these labels come from "
              f"{distinct} followed object{'' if distinct == 1 else 's'}. "
              "The same object on 200 frames is one object measured 200")
        print("    times, not 200 independent measurements — this tells you "
              "how the detector handles")
        print("    THAT object at THAT distance, and nothing yet about the "
              "camera in general. Tag")
        print("    objects on scattered frames, in different light, before "
              "quoting the percentage.")
    print(f"         {total_extra} detections had no label — some are real "
          "objects nobody tagged, so this is an upper bound on false "
          "positives, not a count of them")
    if dropped_interp:
        print(f"         {dropped_interp} reconstructed frames left out: they "
              "are not sightings")
    if dropped_class:
        print(f"         {dropped_class} objects outside the detector's "
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

    if args.json:
        report = {"status": args.status, "iou": args.iou,
                  "class_agnostic": args.class_agnostic,
                  "model": cfg["detection"].get("model"),
                  "confidence": cfg["detection"].get("confidence"),
                  "overall": {"labelled": truths, "found": hits,
                              "recall": round(hits / truths, 4) if truths else None,
                              "unlabelled_detections": total_extra},
                  "by_class": {k: dict(v) for k, v in stats.items()
                               if k in COMPARABLE},
                  "by_size": {name: dict(stats[name])
                              for _l, _h, name in BUCKETS if name in stats},
                  "frames": per_frame}
        Path(args.json).write_text(json.dumps(report, indent=1))
        print(f"\nfull report written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
