#!/usr/bin/env python3
"""Validation harness — measures whether the candidate trigger CATCHES real
incidents (recall) and how often it fires on normal footage (cost proxy).

This is the pass/fail test for the trigger. Point it at a folder of labelled
clips and it runs the free trigger over each, then prints:

  * RECALL  — of the incident clips, how many did the trigger flag during the
              actual incident window. This should be ~100%. A miss = FAIL.
  * FIRE RATE on normal clips — how often it fired with no incident (Claude
              filters these cheaply; lower is cheaper).
  * COVERAGE — fraction of total footage flagged (the cost proxy: less is
              cheaper to review).

Setup:
    testset/
      clips/            <- put your .mp4 clips here
      labels.csv        <- filename,type,incident,start_s,end_s,notes

Usage:
    python validate_triggers.py                       # uses ./testset
    python validate_triggers.py --testset testset
    # auto-fill incident windows from the UCF-Crime annotation file:
    python validate_triggers.py --ucf-annotations Temporal_Anomaly_Annotation_for_Testing_Videos.txt
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import yaml

from app.detector import Detector
from app.trigger import CandidateTrigger, merge_windows, windows_overlap

INCIDENT_TYPES = {"vehicle_theft", "loitering", "tampering", "trespass_night",
                  "camera_tamper", "stealing", "burglary", "robbery",
                  "vandalism", "shoplifting"}


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_ucf_annotations(path: str) -> dict[str, tuple[bool, float, float]]:
    """UCF-Crime 'Temporal_Anomaly_Annotation...' lines look like:
        Stealing079_x264.mp4  Stealing  1350  1800  -1  -1
    values are FRAME numbers (start/end of the anomaly), -1 = none.
    Returns {filename: (incident?, start_frame, end_frame)}."""
    out = {}
    for line in Path(path).read_text().splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        name = parts[0]
        try:
            s1, e1 = int(parts[2]), int(parts[3])
        except ValueError:
            continue
        if s1 < 0:
            out[name] = (False, 0.0, 0.0)
        else:
            out[name] = (True, float(s1), float(e1))  # frames, converted later
    return out


def load_labels(testset: Path, ucf: dict | None) -> list[dict]:
    labels = []
    csv_path = testset / "labels.csv"
    if csv_path.exists():
        with csv_path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                labels.append({
                    "filename": (row.get("filename") or "").strip(),
                    "type": (row.get("type") or "").strip().lower(),
                    "incident": (row.get("incident") or "").strip().lower() == "yes",
                    "start_s": _f(row.get("start_s")),
                    "end_s": _f(row.get("end_s")),
                    "notes": (row.get("notes") or "").strip(),
                    "ucf_frames": None,
                })
    # bring in any clips present on disk but not in labels.csv (use UCF if we can)
    known = {l["filename"] for l in labels}
    clips_dir = testset / "clips"
    for p in sorted(clips_dir.glob("*")) if clips_dir.exists() else []:
        if p.suffix.lower() not in (".mp4", ".avi", ".mov", ".mkv") \
                or p.name in known:
            continue
        entry = {"filename": p.name, "type": _guess_type(p.name),
                 "incident": False, "start_s": None, "end_s": None,
                 "notes": "(auto)", "ucf_frames": None}
        labels.append(entry)
    # overlay UCF annotations (frames -> seconds happens at run time, needs fps)
    if ucf:
        for l in labels:
            if l["filename"] in ucf:
                inc, s_f, e_f = ucf[l["filename"]]
                l["incident"] = inc
                l["ucf_frames"] = (s_f, e_f) if inc else None
                if l["type"] not in INCIDENT_TYPES and inc:
                    l["type"] = _guess_type(l["filename"])
    return labels


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _guess_type(name: str) -> str:
    low = name.lower()
    for t in INCIDENT_TYPES:
        if t.replace("_", "") in low.replace("_", ""):
            return t
    return "normal"


def run_clip(clip_path: Path, detector: Detector, cfg: dict,
             process_fps: float):
    """Run the trigger over one clip; return (candidate_windows, duration_s)."""
    import cv2
    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        return [], 0.0
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    if not (1 <= fps <= 120):
        fps = 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    step = max(1, int(round(fps / process_fps)))
    # daytime clock so night-only signal doesn't fire on every clip during tuning
    trig = CandidateTrigger(cfg.get("cameras", [{}])[0].get("zones", {}),
                            cfg["rules"]["trigger"],
                            localtime_fn=lambda: __import__("datetime").datetime(2026, 7, 4, 14, 0))
    cand_times = []
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % step == 0:
            ts = idx / fps
            dets = detector.track(frame)
            fire, _ = trig.is_candidate(dets, ts)
            if fire:
                cand_times.append(ts)
        idx += 1
    cap.release()
    duration = (total / fps) if total else (idx / fps)
    return merge_windows(cand_times), duration


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--testset", default="testset")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--ucf-annotations", default="")
    args = ap.parse_args()

    testset = Path(args.testset)
    if not (testset / "clips").exists():
        print(f"No clips folder at {testset/'clips'} — create it and add .mp4 "
              f"clips (see the top of this file / README).")
        return 1

    cfg = load_config(args.config)
    ucf = parse_ucf_annotations(args.ucf_annotations) if args.ucf_annotations else None
    labels = load_labels(testset, ucf)
    if not labels:
        print("No clips found to validate.")
        return 1

    print("Loading detector (first run downloads the model)...")
    detector = Detector(cfg["detection"])
    process_fps = float(cfg["detection"].get("process_fps", 6))

    incidents_total = incidents_hit = 0
    normal_total = normal_fired = 0
    total_dur = total_flagged = 0.0
    rows = []

    import cv2
    for lb in labels:
        clip = testset / "clips" / lb["filename"]
        if not clip.exists():
            continue
        # convert UCF frame window -> seconds using this clip's fps
        if lb.get("ucf_frames"):
            cap = cv2.VideoCapture(str(clip))
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            cap.release()
            fps = fps if 1 <= fps <= 120 else 25.0
            lb["start_s"], lb["end_s"] = (lb["ucf_frames"][0] / fps,
                                          lb["ucf_frames"][1] / fps)

        windows, dur = run_clip(clip, detector, cfg, process_fps)
        total_dur += dur
        total_flagged += sum(e - s for s, e in windows)
        fired = bool(windows)

        if lb["incident"]:
            incidents_total += 1
            inc_win = (lb.get("start_s") or 0.0,
                       lb.get("end_s") if lb.get("end_s") is not None else dur)
            hit = any(windows_overlap(w, (inc_win[0] - 2, inc_win[1] + 2))
                      for w in windows)
            incidents_hit += hit
            status = "HIT ✓" if hit else "MISS ✗ (recall failure!)"
        else:
            normal_total += 1
            normal_fired += fired
            status = "fired" if fired else "quiet"
        rows.append((lb["filename"], lb["type"],
                     "incident" if lb["incident"] else "normal",
                     len(windows), status))

    # ---- report ----
    print("\n" + "=" * 74)
    print(f"{'clip':32} {'type':14} {'label':9} {'wins':5} result")
    print("-" * 74)
    for name, typ, lab, nw, status in rows:
        print(f"{name[:32]:32} {typ[:14]:14} {lab:9} {nw:<5} {status}")
    print("=" * 74)

    recall = (incidents_hit / incidents_total) if incidents_total else None
    fire_rate = (normal_fired / normal_total) if normal_total else None
    coverage = (total_flagged / total_dur) if total_dur else 0.0

    print(f"\nINCIDENTS: caught {incidents_hit}/{incidents_total}"
          + (f"  ->  RECALL = {recall*100:.0f}%" if recall is not None else ""))
    if recall is not None and recall < 1.0:
        print("  ⚠️  Trigger MISSED an incident — loosen sensitivity "
              "(raise near_vehicle_px, lower dwell_s) and re-run.")
    print(f"NORMAL:    fired on {normal_fired}/{normal_total}"
          + (f"  ->  false-trigger rate = {fire_rate*100:.0f}%"
             if fire_rate is not None else ""))
    print(f"COVERAGE:  {coverage*100:.0f}% of footage flagged for review "
          f"(cost proxy — lower is cheaper)")
    print("\nGoal: RECALL = 100% first (never miss), then reduce coverage/"
          "false-triggers to cut cost.")
    return 0 if (recall is None or recall >= 1.0) else 2


if __name__ == "__main__":
    sys.exit(main())
