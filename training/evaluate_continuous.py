#!/usr/bin/env python3
"""Run the gate over WHOLE videos and count how often it would wake somebody.

    python -m training.evaluate_continuous --video path/to/five_minutes.mp4
    python -m training.evaluate_continuous --meva 3      # fetch and score 3

Short training clips cannot measure a false-alarm rate, and the first attempt
proved it: scored over 6-second clips the gate fired zero times, which looked
like a triumph and was arithmetic. The gate asks for twelve unbroken seconds
near a vehicle; no six-second clip can contain twelve seconds of anything.

A false-alarm rate is a property of continuous footage. So this walks a whole
video with a sliding window — the same shape the live pipeline uses — scores
every candidate pair in each window, and passes the firings through the very
same `app.incidents.IncidentGate` the product runs, so what is counted is
alerts a person would actually receive rather than raw firings.

Both numbers are printed, because the difference between them is exactly what
the rising-edge logic is worth.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.incidents import IncidentGate
from training import features as F
from training import tier0
from training.extract import VEHICLES


def score_video(path, detector, *, window_s: float = 30.0,
                step_s: float = 5.0, stride: int = 3,
                gate: dict | None = None, night: bool = False,
                incident_cfg: dict | None = None, verbose: bool = True) -> dict:
    """Slide a window over one video and count firings and alerts."""
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"could not open {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = total / fps if fps else 0.0

    frames: list = []
    firings, alerts, worst = 0, 0, []
    ig = IncidentGate(incident_cfg or {"cooldown_s": 60})
    camera = Path(path).stem
    idx, next_score = 0, window_s
    t0 = time.time()

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            ts = idx / fps
            if idx % stride == 0:
                people, vehicles = {}, {}
                for d in detector.track(frame):
                    box = F.Box(*d.xyxy, conf=d.conf)
                    if d.cls_name == "person":
                        people[d.track_id] = box
                    elif d.cls_name in VEHICLES:
                        vehicles[d.track_id] = box
                frames.append(F.MultiFrame(ts=ts, people=people,
                                           vehicles=vehicles))
                frames = [f for f in frames if f.ts >= ts - window_s]

            if ts >= next_score and frames:
                next_score += step_s
                for pid, vid in F.candidate_pairs(frames):
                    feats = F.pair_features(
                        F.window_for(frames, pid, vid),
                        F.Context(night=night, hour=2.0 if night else 14.0))
                    if tier0.gate_fires(feats, gate):
                        firings += 1
                        d = ig.observe(camera, ts, "MEDIUM",
                                       "suspicious_activity",
                                       score=0.5, track_ids=[pid])
                        if d.notify:
                            alerts += 1
                            worst.append({"at_s": round(ts, 1),
                                          "why": F.explain(feats)})
            idx += 1
            if verbose and idx % (int(fps) * 60) == 0:
                print(f"      {ts/60:.0f} min ... {firings} firings, "
                      f"{alerts} alerts", end="\r", flush=True)
    finally:
        cap.release()

    return {"video": Path(path).name, "duration_s": round(duration, 1),
            "firings": firings, "alerts": alerts,
            "firings_per_hour": tier0.alerts_per_hour(firings, duration),
            "alerts_per_hour": tier0.alerts_per_hour(alerts, duration),
            "examples": worst[:5], "took_s": round(time.time() - t0, 1)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--video", action="append", default=[],
                   help="a full-length video; repeat for several")
    p.add_argument("--meva", type=int, default=0,
                   help="fetch and score this many full MEVA sources")
    p.add_argument("--camera", default="G424")
    p.add_argument("--hours", default="")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--window-s", type=float, default=30.0)
    p.add_argument("--step-s", type=float, default=5.0)
    p.add_argument("--stride", type=int, default=3)
    p.add_argument("--near-s", type=float, default=tier0.GATE["min_near_s"])
    p.add_argument("--straightness", type=float,
                   default=tier0.GATE["max_straightness"])
    p.add_argument("--keep", action="store_true",
                   help="keep fetched MEVA sources (they are ~200MB each)")
    p.add_argument("--json", default="")
    args = p.parse_args(argv)

    videos = [Path(v) for v in args.video]
    fetched: list = []
    if args.meva:
        import subprocess

        import fetch_testset as meva
        hours = None
        if args.hours:
            hours = (set(meva.NIGHT_HOURS)
                     if args.hours.strip().lower() == "night"
                     else {h.strip().zfill(2) for h in args.hours.split(",")})
        keys = meva.list_keys(args.camera, limit=args.meva * 3,
                              prefix=meva.POOL, hours=hours, spread=True)
        tmp = Path("training/data/_continuous")
        tmp.mkdir(parents=True, exist_ok=True)
        for k in keys[:args.meva]:
            dest = tmp / Path(k).name
            if not dest.exists():
                print(f"  downloading {dest.name} ...", flush=True)
                r = subprocess.run(["curl", "-sSL", "-m", "1800",
                                    f"{meva.BUCKET}/{k}", "-o", str(dest)],
                                   capture_output=True, text=True)
                if r.returncode != 0 or not dest.exists():
                    print(f"    failed: {r.stderr.strip()[:100]}")
                    continue
            videos.append(dest)
            fetched.append(dest)

    if not videos:
        print("nothing to score — pass --video or --meva N")
        return 1

    import yaml
    from app.detector import Detector
    cfg = yaml.safe_load(open(args.config))
    det = Detector(cfg["detection"])
    gate = {"min_near_s": args.near_s, "max_straightness": args.straightness}
    print(f"detector : {cfg['detection'].get('model')} on {det.device}")
    print(f"gate     : close >= {gate['min_near_s']:.0f}s unbroken, "
          f"straightness <= {gate['max_straightness']:.2f}, touching")
    print(f"window   : {args.window_s:.0f}s, scored every "
          f"{args.step_s:.0f}s\n")

    results, total_s, total_f, total_a = [], 0.0, 0, 0
    try:
        for v in videos:
            print(f"  {v.name}", flush=True)
            r = score_video(v, det, window_s=args.window_s,
                            step_s=args.step_s, stride=args.stride, gate=gate)
            results.append(r)
            total_s += r["duration_s"]
            total_f += r["firings"]
            total_a += r["alerts"]
            print(f"      {r['duration_s']:.0f}s -> {r['firings']} firings, "
                  f"{r['alerts']} alerts   ({r['took_s']:.0f}s to score)")
    finally:
        if not args.keep:
            for f in fetched:
                f.unlink(missing_ok=True)

    print(f"\n{total_s/60:.1f} minutes of ordinary footage")
    print(f"  candidate firings : {total_f}"
          f"   ({tier0.alerts_per_hour(total_f, total_s):.1f} / hour)")
    print(f"  alerts a person gets: {total_a}"
          f"   ({tier0.alerts_per_hour(total_a, total_s):.1f} / hour)")
    print("\n  Every one of these is a FALSE alarm: this is ordinary footage.")
    print("  The gap between the two lines is what the rising-edge logic in")
    print("  app/incidents.py is worth.")

    seen = [e for r in results for e in r["examples"]][:6]
    if seen:
        print("\n  what fired:")
        for e in seen:
            print(f"    {e['at_s']/60:5.1f} min  {e['why']}")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"minutes": round(total_s / 60, 1), "firings": total_f,
             "alerts": total_a, "gate": gate,
             "firings_per_hour": tier0.alerts_per_hour(total_f, total_s),
             "alerts_per_hour": tier0.alerts_per_hour(total_a, total_s),
             "videos": results}, indent=1))
        print(f"\nwritten to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
