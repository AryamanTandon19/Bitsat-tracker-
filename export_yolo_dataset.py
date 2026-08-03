#!/usr/bin/env python3
"""Turn approved annotations into a YOLO dataset you can fine-tune on.

    python export_yolo_dataset.py --out datasets/society
    yolo detect train data=datasets/society/data.yaml model=yolo11n.pt epochs=80 imgsz=960

READ THIS BEFORE TRAINING ANYTHING

Fine-tuning is the *last* thing to try, not the first. The model already knows
what a car and a person look like; what it does not know is what they look
like on your camera, at your height, in your light. That is usually fixed by
input size and the confidence floor, which cost nothing:

    python sweep_detector.py          # do this first

If the sweep says a config change gets you there, stop. Training is a GPU, a
day, and a model you now have to maintain, in exchange for something a line of
YAML already did.

If you do train, the honest requirements are:
  * a few thousand labelled objects, not a few hundred
  * from several different scenes, times of day and weather
  * with the val split held out by CLIP, which this script enforces

HOW THE SPLIT IS DONE, AND WHY IT MATTERS

Frames from one tracked object are near-identical. Split those at random and
almost every validation frame has a near-twin in training; the model scores
beautifully on validation and no better in the car park. It is the most common
way a small dataset lies to its owner.

So the split here is by clip: every frame of a clip goes to one side or the
other, and a clip's tracks cannot straddle the boundary. With only one clip
this script refuses to split at all and says so, because a validation set
drawn from the same clip would be decoration.
"""
from __future__ import annotations

import argparse
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import measure
from app import segment as segment_mod
from app.annotations import Annotation
from app.db import Database
from app.main import load_config

# The classes worth training on: what the alerting rules actually reason about.
# A wider vocabulary from a small dataset gives every extra class a handful of
# examples, which teaches the model nothing and dilutes the ones that matter.
TRAIN_CLASSES = ("person", "car", "motorcycle", "bicycle", "bus", "truck")


def norm_polygon(poly, w: int, h: int) -> list:
    out = []
    for x, y in poly:
        out.append(min(max(x / w, 0.0), 1.0))
        out.append(min(max(y / h, 0.0), 1.0))
    return out


def norm_box(box, w: int, h: int) -> list:
    x1, y1, x2, y2 = box
    cx = (x1 + x2) / 2 / w
    cy = (y1 + y2) / 2 / h
    return [min(max(v, 0.0), 1.0) for v in
            (cx, cy, (x2 - x1) / w, (y2 - y1) / h)]


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--out", default="datasets/society")
    p.add_argument("--status", default="approved",
                   help="approved (default), submitted, or any")
    p.add_argument("--task", default="detect", choices=("detect", "segment"))
    p.add_argument("--val-fraction", type=float, default=0.25)
    p.add_argument("--include-reconstructed", action="store_true",
                   help="include frames the tracker filled in. Off by "
                        "default: an approximate outline nobody saw is a "
                        "worse teacher than no example at all")
    p.add_argument("--jpeg-quality", type=int, default=92)
    p.add_argument("--seed", type=int, default=7)
    args = p.parse_args()

    cfg = load_config(args.config)
    db = Database(cfg["storage"]["db_path"])
    try:
        rows = db.object_annotations(
            review_status="" if args.status == "any" else args.status)
        anns = [Annotation.from_row(r) for r in rows]
        clips = {c["id"]: c for c in db.list_training_clips()}
    finally:
        db.close()

    keep = []
    dropped = Counter()
    for a in anns:
        if a.source == "interpolated" and not args.include_reconstructed:
            dropped["reconstructed by the tracker"] += 1
        elif a.category not in TRAIN_CLASSES:
            dropped[f"class '{a.category}' is not trained on"] += 1
        elif args.task == "segment" and not a.polygon:
            dropped["no outline, only a box"] += 1
        else:
            keep.append(a)

    if not keep:
        print(f"Nothing to export with status '{args.status}'.")
        for why, n in dropped.most_common():
            print(f"  {n} dropped: {why}")
        print("\nLabel some footage first:")
        print("  python prelabel.py --all-clips --frames 25   # propose")
        print("  # then correct and approve them in /train")
        return 1

    by_clip: dict = defaultdict(list)
    for a in keep:
        by_clip[a.clip_id].append(a)

    clip_ids = sorted(by_clip)
    rng = random.Random(args.seed)
    rng.shuffle(clip_ids)
    n_val = int(round(len(clip_ids) * args.val_fraction))
    if len(clip_ids) < 2:
        val_ids: set = set()
        print("! Only one clip has labels, so there is no honest way to hold "
              "out a validation set.")
        print("  Everything goes to train. Label a second, different clip "
              "before you trust any")
        print("  validation number this produces.\n")
    else:
        n_val = max(1, n_val)
        val_ids = set(clip_ids[:n_val])

    out = Path(args.out)
    made = {"train": 0, "val": 0}
    objects = {"train": 0, "val": 0}
    per_class: dict = defaultdict(Counter)
    sizes: dict = defaultdict(Counter)

    for clip_id in sorted(by_clip):
        clip = clips.get(clip_id)
        if not clip or not Path(clip["path"]).exists():
            print(f"  ! clip {clip_id} is gone from disk, skipping "
                  f"{len(by_clip[clip_id])} labels")
            continue
        split = "val" if clip_id in val_ids else "train"
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)

        frames: dict = defaultdict(list)
        for a in by_clip[clip_id]:
            frames[a.frame_index].append(a)

        wanted = set(frames)
        stem = Path(clip["filename"]).stem
        import cv2
        for idx, _ts, img in segment_mod.iter_frames(clip["path"], 0, 1, 0):
            if idx not in wanted:
                continue
            h, w = img.shape[:2]
            name = f"{stem}_f{idx:06d}"
            cv2.imwrite(str(out / "images" / split / f"{name}.jpg"), img,
                        [cv2.IMWRITE_JPEG_QUALITY, args.jpeg_quality])
            lines = []
            for a in frames[idx]:
                cid = TRAIN_CLASSES.index(a.category)
                if args.task == "segment":
                    ring = max(a.polygon, key=len)
                    vals = norm_polygon(ring, w, h)
                else:
                    vals = norm_box(a.bbox, w, h)
                lines.append(str(cid) + " "
                             + " ".join(f"{v:.6f}" for v in vals))
                per_class[split][a.category] += 1
                sizes[split][measure.bucket_for(measure.diagonal(a.bbox))] += 1
                objects[split] += 1
            (out / "labels" / split / f"{name}.txt").write_text(
                "\n".join(lines) + "\n")
            made[split] += 1
            wanted.discard(idx)
            if not wanted:
                break

    data_yaml = out / "data.yaml"
    data_yaml.write_text(
        f"# Written by export_yolo_dataset.py from the VisionGuard workbench.\n"
        f"# Split by clip, not by frame: frames of one tracked object are\n"
        f"# near-identical and splitting them at random inflates validation.\n"
        f"path: {out.resolve()}\n"
        f"train: images/train\n"
        f"val: images/{'val' if val_ids else 'train'}\n"
        f"names:\n"
        + "".join(f"  {i}: {c}\n" for i, c in enumerate(TRAIN_CLASSES)))

    print(f"\nwrote {out.resolve()}")
    print(f"  train: {made['train']} frames, {objects['train']} objects")
    print(f"  val:   {made['val']} frames, {objects['val']} objects"
          + ("  (none — single clip)" if not val_ids else ""))
    for split in ("train", "val"):
        if per_class[split]:
            print(f"  {split} by class: "
                  + ", ".join(f"{c} {n}" for c, n in
                              per_class[split].most_common()))
    for why, n in dropped.most_common():
        print(f"  {n} dropped: {why}")

    # Splitting by clip is right, but it has a consequence worth saying out
    # loud: if the only clip with people lands in train, validation cannot
    # tell you anything about people — which is the class the whole product
    # is about. Better to know that than to read a confident mAP.
    if val_ids:
        thin = [c for c in TRAIN_CLASSES
                if per_class["train"].get(c, 0) >= 20
                and per_class["val"].get(c, 0) < 5]
        if thin:
            print("\n! validation barely contains: "
                  + ", ".join(f"{c} ({per_class['val'].get(c, 0)})"
                              for c in thin))
            print("  Splitting by clip is correct, but it means a class that "
                  "lives in one clip ends")
            print("  up on one side. Any validation score for those classes "
                  "is noise. Label the")
            print("  same kinds of object in more than one clip before "
                  "trusting it.")

    total = objects["train"] + objects["val"]
    print("\nIS THIS ENOUGH TO TRAIN ON?")
    if total < 500:
        print(f"  No. {total} objects is a demonstration, not a dataset. "
              "Fine-tuning on this will")
        print("  make the model worse on everything else and better on "
              "nothing. Aim for a few")
        print("  thousand, across several scenes and times of day.")
    elif len(clip_ids) < 4:
        print(f"  {total} objects, but from only {len(clip_ids)} clips. The "
              "model will learn this")
        print("  camera at this hour. Add clips from other cameras, other "
              "light, other weather.")
    else:
        print(f"  {total} objects across {len(clip_ids)} clips — worth a try. "
              "Run the sweep first")
        print("  anyway, so you can tell whether training actually beat a "
              "config change.")

    print(f"\n  yolo {args.task} train data={data_yaml} "
          f"model=yolo11n{'-seg' if args.task == 'segment' else ''}.pt "
          f"epochs=80 imgsz=960")
    print("\nThen point config.yaml `detection: model:` at "
          "runs/*/weights/best.pt and re-run")
    print("sweep_detector.py to see whether it actually helped on held-out "
          "clips.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
