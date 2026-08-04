#!/usr/bin/env python3
"""Which cameras actually show people USING vehicles?

    python -m training.survey_cameras
    python -m training.survey_cameras --cameras G341,G301 --sources 2

Step 5 established the blocker: MEVA's G424 is a car park with cars parked in
it and people walking past them. Across 598 seconds and 378 candidate pairs,
nothing came within half a vehicle-radius; the closest thing to an interaction
was a cyclist riding past a van.

Mining a camera costs a couple of hundred megabytes per source and an hour of
detection, so guessing which one to mine next is expensive. This samples one
source per camera at a coarse stride and reports the only statistic that
matters for the hard-negative problem: **how close do people actually get to
vehicles here.**

It downloads, measures, and deletes. Nothing accumulates.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_testset as meva
from training import features as F
from training.extract import VEHICLES

# Cameras `fetch_testset.CAMERAS` says have vehicles in view. G424 is included
# as the control: we know what its numbers look like.
DEFAULT = ("G424", "G340", "G341", "G301", "G506")

# What counts as an interaction. 0.5 radii is the contact threshold from
# training/features.py; 1.0 is "at the car" without necessarily touching it.
AT_VEHICLE = 0.5
NEAR_VEHICLE = 1.0


def survey_video(path, detector, stride: int = 10) -> dict:
    """Closest person-vehicle approach anywhere in one video."""
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return {}
    gaps: list = []
    frames_with_both = 0
    people_seen = vehicles_seen = 0
    idx = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % stride == 0:
                people, vehicles = [], []
                for d in detector.track(frame):
                    box = F.Box(*d.xyxy, conf=d.conf)
                    if d.cls_name == "person":
                        people.append(box)
                    elif d.cls_name in VEHICLES:
                        vehicles.append(box)
                people_seen += len(people)
                vehicles_seen += len(vehicles)
                if people and vehicles:
                    frames_with_both += 1
                    gaps.append(min(F.gap_radii(p, v)
                                    for p in people for v in vehicles))
            idx += 1
    finally:
        cap.release()

    gaps.sort()
    return {"frames_scored": idx // stride,
            "frames_with_both": frames_with_both,
            "people_detections": people_seen,
            "vehicle_detections": vehicles_seen,
            "min_gap": round(gaps[0], 2) if gaps else None,
            "median_gap": round(gaps[len(gaps) // 2], 2) if gaps else None,
            "frames_at_vehicle": sum(1 for g in gaps if g <= AT_VEHICLE),
            "frames_near_vehicle": sum(1 for g in gaps if g <= NEAR_VEHICLE)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cameras", default=",".join(DEFAULT))
    p.add_argument("--sources", type=int, default=1,
                   help="source videos to sample per camera")
    p.add_argument("--stride", type=int, default=10)
    p.add_argument("--hours", default="")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--production", action="store_true",
                   help="survey with the live detector instead of a sensitive "
                        "one. Answers a different question — see below.")
    p.add_argument("--json", default="")
    args = p.parse_args(argv)

    import yaml
    from app.detector import Detector
    cfg = yaml.safe_load(open(args.config))
    det_cfg = dict(cfg["detection"])
    if not args.production:
        # This survey asks about the FOOTAGE, not about the system. Using the
        # production detector answers a different question and answers it
        # misleadingly: it was measured at 6% recall on 40-80px people, so
        # "no people in this camera" would really mean "no people this model
        # can see". A sensitive setting is the right instrument for asking
        # what is actually in the video.
        det_cfg.update(model="yolo11s.pt", imgsz=1280, confidence=0.15)
    det = Detector(det_cfg)
    print(f"detector : {det_cfg.get('model')} @{det_cfg.get('imgsz')} conf "
          f"{det_cfg.get('confidence')} on {det.device}, "
          f"every {args.stride}th frame")
    print("           (sensitive on purpose — this asks what is in the "
          "footage,\n            not what the live system can see)\n"
          if not args.production else "")

    hours = None
    if args.hours:
        hours = (set(meva.NIGHT_HOURS) if args.hours.strip().lower() == "night"
                 else {h.strip().zfill(2) for h in args.hours.split(",")})

    tmp = Path("training/data/_survey")
    tmp.mkdir(parents=True, exist_ok=True)
    out: dict = {}

    for cam in [c.strip() for c in args.cameras.split(",") if c.strip()]:
        desc = meva.CAMERAS.get(cam, ("unknown view", False))[0]
        print(f"  {cam}  {desc}")
        keys = meva.list_keys(cam, limit=args.sources * 3, prefix=meva.POOL,
                              hours=hours, spread=True)
        if not keys:
            print("      no clips found\n")
            continue
        agg = {"frames_scored": 0, "frames_with_both": 0,
               "people_detections": 0, "vehicle_detections": 0,
               "frames_at_vehicle": 0, "frames_near_vehicle": 0,
               "min_gap": None, "sources": 0}
        for k in keys[:args.sources]:
            dest = tmp / Path(k).name
            r = subprocess.run(["curl", "-sSL", "-m", "1800",
                                f"{meva.BUCKET}/{k}", "-o", str(dest)],
                               capture_output=True, text=True)
            if r.returncode != 0 or not dest.exists():
                print(f"      download failed for {dest.name}")
                continue
            try:
                s = survey_video(dest, det, args.stride)
            finally:
                dest.unlink(missing_ok=True)
            if not s:
                continue
            agg["sources"] += 1
            for key in ("frames_scored", "frames_with_both", "people_detections",
                        "vehicle_detections", "frames_at_vehicle",
                        "frames_near_vehicle"):
                agg[key] += s[key]
            if s["min_gap"] is not None:
                agg["min_gap"] = (s["min_gap"] if agg["min_gap"] is None
                                  else min(agg["min_gap"], s["min_gap"]))
        out[cam] = agg
        print(f"      {agg['people_detections']:5d} person detections, "
              f"{agg['vehicle_detections']:5d} vehicle")
        print(f"      closest approach {agg['min_gap']}, "
              f"{agg['frames_near_vehicle']} frames within 1 radius, "
              f"{agg['frames_at_vehicle']} within {AT_VEHICLE}\n")

    print(f"{'camera':<8}{'people':>8}{'vehicles':>10}{'closest':>9}"
          f"{'<1r':>7}{'<0.5r':>8}  verdict")
    print("-" * 74)
    for cam, a in sorted(out.items(),
                         key=lambda kv: -(kv[1]["frames_at_vehicle"])):
        if a["frames_at_vehicle"] >= 5:
            verdict = "USABLE hard negatives"
        elif a["frames_near_vehicle"] >= 5:
            verdict = "people come near, none at"
        elif a["people_detections"] == 0:
            verdict = "no people at all"
        elif a["vehicle_detections"] == 0:
            verdict = "no vehicles at all"
        else:
            verdict = "people and cars, never together"
        print(f"{cam:<8}{a['people_detections']:>8}{a['vehicle_detections']:>10}"
              f"{str(a['min_gap']):>9}{a['frames_near_vehicle']:>7}"
              f"{a['frames_at_vehicle']:>8}  {verdict}")

    best = [c for c, a in out.items() if a["frames_at_vehicle"] >= 5]
    print()
    if best:
        print(f"Mine these for hard negatives:  {', '.join(best)}")
        print("  python -m training.clipmine --source meva --camera "
              f"{best[0]} --sources 10")
    else:
        print("No camera in this sample shows anyone at a vehicle.")
        print("MEVA is a poor source of hard negatives for this product; the")
        print("fastest honest route is half an hour staged in your own car")
        print("park — unlocking doors, loading a boot, a delivery, standing")
        print("around — which is your camera as well.")

    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=1))
        print(f"\nwritten to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
