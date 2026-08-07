#!/usr/bin/env python3
"""Run the detector over mined clips and turn them into feature rows.

    python -m training.extract --manifest training/data/manifest.jsonl
    python -m training.extract --limit 20 --out training/data/features.jsonl

This is the bridge between step 2 (clips on disk) and step 5 (a model trained
on numbers). For every clip in the manifest it runs the **production
detector** — the same weights the cameras run — tracks objects across the
clip, finds the (person, vehicle) pairs that ever came near each other, and
writes one feature row per pair.

Using the production detector rather than a bigger offline one is deliberate
here, and the opposite of the choice `prelabel.py` makes. Pre-labelling wants
recall, because a human is going to check it. This is measuring what the
system will actually see at runtime, so it has to see exactly what the system
sees — including the objects it misses.

Each row carries its clip's `source_video` and `split`, so the source
separation established in step 1 survives all the way into training.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from training import features as F
from training import manifest as M

VEHICLES = {"car", "truck", "bus", "motorcycle"}


def track_clip(path, detector, stride: int = 3, max_frames: int = 0,
               low_light: str = "auto") -> list:
    """Decode a clip and return MultiFrames with tracked boxes.

    `stride` skips frames: the pipeline itself only infers at `process_fps`
    (6 by default against 30 fps source), so tracking every frame here would
    measure a system nobody runs.

    `low_light` mirrors the live pipeline: dark night frames are brightened
    (CLAHE + gamma) BEFORE detection, exactly as `app/main.py` does before YOLO.
    Without this the model would learn from un-brightened night footage and then
    meet brightened footage in production — a silent day/night train-serve skew
    that would quietly wreck night recall. "auto" only touches dark frames.
    """
    import cv2

    from app.enhance import enhance_frame

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return []
    out = []
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        idx = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % stride == 0:
                proc = (enhance_frame(frame, low_light)
                        if low_light and low_light != "off" else frame)
                people, vehicles = {}, {}
                for d in detector.track(proc):
                    box = F.Box(*d.xyxy, conf=d.conf)
                    if d.cls_name == "person":
                        people[d.track_id] = box
                    elif d.cls_name in VEHICLES:
                        vehicles[d.track_id] = box
                out.append(F.MultiFrame(ts=idx / fps, people=people,
                                        vehicles=vehicles))
            idx += 1
            if max_frames and idx >= max_frames:
                break
    finally:
        cap.release()
    return out


def rows_for_clip(rec, frames, camera_rates=None) -> list:
    """One feature row per (person, vehicle) pair worth scoring."""
    if not frames:
        return []
    hour = 2.0 if rec.night else 14.0        # MEVA hour is in the filename;
    ctx_base = dict(night=rec.night, hour=hour,                     # good enough
                    camera_false_alarm_rate=float(
                        (camera_rates or {}).get(rec.camera_id, 0.0)))

    out = []
    for person_id, vehicle_id in F.candidate_pairs(frames):
        window = F.window_for(frames, person_id, vehicle_id)
        feats = F.pair_features(window, F.Context(**ctx_base))
        out.append({
            "clip_id": rec.clip_id,
            "source_video": rec.source_video,
            "split": rec.split,
            "label": rec.label,
            "specialist": rec.specialist,
            "suspicious": int(rec.suspicious),
            "hard_negative": int(rec.hard_negative),
            "person_id": int(person_id), "vehicle_id": int(vehicle_id),
            "camera_id": rec.camera_id, "night": int(rec.night),
            "duration_s": rec.duration_s,
            "features": feats,
            "why": F.explain(feats),
        })
    return out


def write_rows(path, rows) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    return len(rows)


def read_rows(path) -> list:
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--manifest", default="training/data/manifest.jsonl")
    p.add_argument("--out", default="training/data/features.jsonl")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--stride", type=int, default=3,
                   help="detect every Nth frame; the live pipeline runs at "
                        "process_fps, not every frame")
    p.add_argument("--limit", type=int, default=0, help="first N clips only")
    args = p.parse_args(argv)

    man = Path(args.manifest)
    try:
        records = M.read(man)
    except M.ManifestError as e:
        print(e)
        return 1
    if args.limit:
        records = records[:args.limit]
    if not records:
        print("no clips in the manifest — run training.clipmine first")
        return 1

    import yaml
    from app.detector import Detector
    cfg = yaml.safe_load(open(args.config))
    det = Detector(cfg["detection"])
    low_light = cfg["detection"].get("low_light", "auto")
    print(f"detector : {cfg['detection'].get('model')} @"
          f"{cfg['detection'].get('imgsz')} conf "
          f"{cfg['detection'].get('confidence')} on {det.device}")
    print(f"low-light: {low_light}  (brightens dark frames before detection, "
          "matching live)")
    print(f"clips    : {len(records)}  (every {args.stride}th frame)\n")

    rows, t0, seconds = [], time.time(), 0.0
    for i, rec in enumerate(records, 1):
        clip_path = man.parent / rec.path
        if not clip_path.exists():
            print(f"  ! {rec.clip_id}: file is gone")
            continue
        frames = track_clip(clip_path, det, args.stride, low_light=low_light)
        got = rows_for_clip(rec, frames)
        rows += got
        seconds += rec.duration_s
        print(f"  [{i}/{len(records)}] {rec.clip_id}  "
              f"{len(frames)} frames, {len(got)} pairs", flush=True)

    n = write_rows(args.out, rows)
    took = time.time() - t0
    print(f"\n{n} feature rows from {len(records)} clips "
          f"({seconds:.0f}s of footage) in {took:.0f}s")

    by_split: dict = defaultdict(int)
    for r in rows:
        by_split[r["split"] or "unassigned"] += 1
    print(f"by split : {dict(by_split)}")
    pos = sum(r["suspicious"] for r in rows)
    print(f"labels   : {pos} suspicious, {len(rows) - pos} normal")
    if pos == 0:
        print("\n  ! every row is a negative. A classifier needs both classes;")
        print("    until UCF-Crime positives are mined this measures the "
              "false-alarm")
        print("    side only, which is priority #1 but not the whole picture.")
    print(f"\nNext:  python -m training.tier0 --features {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
