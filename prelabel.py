#!/usr/bin/env python3
"""Have a big slow model propose labels, so a person only has to correct them.

    python prelabel.py --clip 3 --every 60
    python prelabel.py --all-clips --frames 25 --model yolo11m-seg.pt
    # then open /train, page through the frames, fix and approve

WHY THIS EXISTS

Labelling from scratch is the reason most people never build a test set. Two
hundred objects is two hundred clicks. Model-assisted labelling turns that
into two hundred *glances*: the outlines are already there, most of them are
right, and the work becomes rejecting the wrong ones and adding what was
missed. That is perhaps ten times faster, and it is how essentially every
real dataset gets built.

THE TRAP, AND WHAT IS DONE ABOUT IT

If a model proposes the labels and the same model is then measured against
them, the score is meaningless — it would be marking its own paper, and
anything both missed is invisible to the measurement. Two things guard that:

  Everything written here is `draft`, never `approved`. The measurement
  harnesses read `approved` by default, so an unreviewed proposal cannot
  quietly become a result. A human has to look at it and press the button.

  The proposing model is deliberately NOT the model being measured. It is a
  bigger one at a much lower confidence floor, run offline where slowness does
  not matter. It finds things the live detector cannot, which is the point:
  the labels have to be able to say the detector missed something.

What remains — objects that even the big model missed — is a real bias, and it
makes recall look better than it is. Only human eyes fix that, which is why
the workbench has a "tag something the AI missed" button, and why this script
prints a reminder to use it.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import annotations as ann_mod
from app import segment as segment_mod
from app.db import Database
from app.main import load_config

# Big enough to be worth running, small enough to download and run on a CPU
# in a sensible time. yolo11m-seg is ~2.5x the parameters of the nano model
# the cameras run and finds markedly more small objects.
PROPOSER = "yolo11m-seg.pt"
FLOOR = 0.12          # far below the live floor: recall now, precision later


def frames_to_do(fps: float, total: int, every: int, n: int,
                 start: int, end: int) -> list:
    """Which frames to propose on.

    Spread out, not consecutive. Twenty-five consecutive frames of a car park
    are one picture sampled twenty-five times; twenty-five frames a minute
    apart are twenty-five different situations, and only the second kind
    tells you anything about the camera.
    """
    end = min(end or total, total)
    if end <= start:
        return []
    if every > 0:
        step = max(1, int(every * fps))
        picks = list(range(start, end, step))
    else:
        step = max(1, (end - start) // max(1, n))
        picks = list(range(start, end, step))
    return picks[:n] if n else picks


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--clip", type=int, default=0, help="one training clip id")
    p.add_argument("--all-clips", action="store_true")
    p.add_argument("--every", type=float, default=0,
                   help="propose on a frame every N seconds")
    p.add_argument("--frames", type=int, default=20,
                   help="how many frames per clip (default 20)")
    p.add_argument("--start", type=int, default=0, help="first frame")
    p.add_argument("--end", type=int, default=0, help="last frame")
    p.add_argument("--model", default=PROPOSER)
    p.add_argument("--conf", type=float, default=FLOOR)
    p.add_argument("--imgsz", type=int, default=1280,
                   help="bigger than the live detector's on purpose — this "
                        "runs offline, where slow is free")
    p.add_argument("--max-objects", type=int, default=60)
    p.add_argument("--by", default="prelabel", help="who to record as author")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    cfg = load_config(args.config)
    db = Database(cfg["storage"]["db_path"])
    try:
        clips = db.list_training_clips()
        if args.clip:
            clips = [c for c in clips if c["id"] == args.clip]
        elif not args.all_clips:
            clips = clips[:1]
        clips = [c for c in clips if Path(c["path"]).exists()]
        if not clips:
            print("No training clips available. Add some first:")
            print("  python fetch_testset.py --camera G424 --clips 6")
            return 1

        seg = segment_mod.YoloSegmenter(args.model, conf=args.conf,
                                        imgsz=args.imgsz,
                                        max_objects=args.max_objects)
        print(f"proposer: {args.model} at conf {args.conf}, imgsz {args.imgsz}")
        print(f"          (the cameras run "
              f"{(cfg.get('detection') or {}).get('model')} at conf "
              f"{(cfg.get('detection') or {}).get('confidence')} — "
              "deliberately different)\n")

        grand = 0
        for clip in clips:
            fps, total, w, h = segment_mod.clip_shape(clip["path"])
            picks = frames_to_do(fps, total, args.every, args.frames,
                                 args.start, args.end)
            if not picks:
                print(f"{clip['filename']}: nothing to do")
                continue
            already = {r["frame_index"] for r in
                       db.object_annotations(clip["id"])}
            todo = [f for f in picks if f not in already]
            print(f"{clip['filename']}: {len(todo)} frames to propose on"
                  + (f" ({len(picks) - len(todo)} already have labels)"
                     if len(picks) != len(todo) else ""))
            if args.dry_run:
                print(f"   would do frames: {todo[:12]}"
                      + (" ..." if len(todo) > 12 else ""))
                continue

            written = 0
            t0 = time.time()
            wanted = set(todo)
            for idx, ts, img in segment_mod.iter_frames(clip["path"], 0, 1, 0):
                if idx not in wanted:
                    continue
                objects = seg.segment(img, idx)
                rows = []
                for o in objects:
                    if not o.polygons:
                        continue
                    cls = (o.class_name if o.class_name in ann_mod.CLASSES
                           else "unknown")
                    a = ann_mod.Annotation(
                        clip_id=clip["id"], frame_index=idx, timestamp_ms=ts,
                        category=cls, source="yolo_segmentation",
                        bbox=o.bbox, frame_width=w, frame_height=h,
                        original_polygon=o.polygons,
                        detection_confidence=round(float(o.confidence), 3),
                        model=seg.name, temporary_object_id=o.temporary_object_id,
                        tags=["prelabelled"],
                        notes="proposed by a model — check before approving",
                        review_status="draft",       # never approved from here
                        needs_review=True,
                        review_note="nobody has looked at this yet",
                        created_by=args.by, created_at=time.time(),
                        updated_by=args.by, updated_at=time.time())
                    rows.append(a.row())
                if rows:
                    db.add_object_annotations(rows)
                    written += len(rows)
                wanted.discard(idx)
                print(f"   frame {idx}: {len(rows)} objects "
                      f"({len(wanted)} frames left)   ", end="\r", flush=True)
                if not wanted:
                    break
            print(f"   {written} proposals on {len(todo)} frames "
                  f"in {time.time() - t0:.0f}s" + " " * 20)
            grand += written

        if grand and not args.dry_run:
            print(f"\n{grand} draft annotations written.")
            print("\nThey are DRAFTS. Nothing measures against them until "
                  "somebody approves them.")
            print("Open /train, pick the clip, and for each frame:")
            print("  - delete the outlines that are wrong")
            print("  - use “Tag something the AI missed” for what it did not "
                  "offer — this is")
            print("    the part that decides whether your recall number is "
                  "honest, because")
            print("    objects no model proposed are invisible to the "
                  "measurement otherwise")
            print("  - then Submit and Approve")
            print("\nThen:  python sweep_detector.py")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
