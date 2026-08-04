#!/usr/bin/env python3
"""Download a little, cut clips, verify them, write the manifest, delete the raw.

    python -m training.clipmine --source meva --camera G424 --clips 200
    python -m training.clipmine --source meva --hours night --budget-gb 1
    python -m training.clipmine --source local --dir E:/ucf --specialist break_in \\
        --label HOUSE_BREAK_IN

The laptop cannot hold a surveillance dataset. It does not need to: a verified
six-second clip is a few hundred kilobytes, and the 200 MB source it came from
is worthless once the clip exists. So this walks one source video at a time —
download, cut, verify, record, **delete** — and never has more than one raw
file on disk.

Every clip that survives is appended to the manifest immediately, so a crash
or a closed laptop lid costs the clip in flight and nothing already earned.
Re-running skips source videos the manifest already covers.

ON RESOLUTION — a correction to the first draft of docs/TRAINING_PLAN.md

The plan said to store clips at 128x128, since that is what the model reads.
That is wrong, and would have quietly foreclosed the most valuable idea in the
plan: cropping to the person-vehicle pair rather than classifying the whole
frame. A crop cannot be taken from a 128x128 thumbnail — the pixels are gone.

So clips are stored at `--width` (640 by default), which keeps cropping
possible and still costs a few hundred kilobytes each. Downscaling to 128
happens at training time, from the crop, where it is reversible if the idea
changes.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_testset as meva
from training import manifest as M
from training import verify as V

# What each specialist's negatives should contain, for the has_object check.
# A clip labelled "normal vehicle activity" with no vehicle in it is grass.
EXPECTED_OBJECTS = {
    "vehicle": ("car", "truck", "bus", "motorcycle"),
    "break_in": ("person",),
}


def plan_clips(duration_s: float, clip_s: float, stride_s: float,
               max_clips: int, start_at: float = 0.0) -> list:
    """Where to cut, spread across the source rather than taken from the front.

    Ten consecutive clips off the start of a five-minute video are one
    situation sampled ten times. Spreading them across the whole source is the
    difference between ten examples and one.
    """
    if clip_s <= 0 or duration_s <= start_at:
        return []
    usable = duration_s - start_at
    if usable < clip_s:
        return []
    n_possible = int(usable // max(stride_s, clip_s))
    n = min(max_clips, max(1, n_possible))
    if n == 1:
        return [(round(start_at, 3), round(start_at + clip_s, 3))]
    # even spacing across everything that fits, last clip ending at the end
    span = usable - clip_s
    step = span / (n - 1)
    out = []
    for i in range(n):
        s = start_at + i * step
        out.append((round(s, 3), round(s + clip_s, 3)))
    return out


def probe_duration(path) -> float:
    """Seconds, via the decoder we already depend on."""
    import cv2
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            return 0.0
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
        return float(n / fps) if fps > 0 else 0.0
    finally:
        cap.release()


# How far before the target to place the fast seek. Everything after it is
# decoded, so this is the cost of accuracy; two seconds is comfortably more
# than a surveillance encoder's keyframe interval.
PREROLL_S = 2.0


def cut(ff: str, src, dest, start_s: float, dur_s: float,
        width: int = 640, crf: int = 28) -> bool:
    """Cut one clip, at the length that was actually asked for.

    Seek placement is not a detail. Measured on a real MEVA source (480
    frames, 30fps), asking for six seconds three different ways:

        -ss 0 before -i   ->  118 frames (3.93s)   a third of the clip lost
        -ss 0 after  -i   ->  178 frames (5.93s)   correct
        -ss 5 before -i   ->  180 frames (6.00s)   correct

    Fast seek (before `-i`) is wrong at the very start of these files and fine
    in the middle; accurate seek (after `-i`) is always right but decodes
    everything up to the mark, which is slow four minutes into a source. So
    this does the standard hybrid: jump most of the way with a fast seek, then
    walk the last couple of seconds accurately.

    Re-encoding rather than stream-copying is also deliberate — a copy lands
    on whatever keyframe is nearby, which is the drift `verify.check_duration`
    exists to catch.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    scale = ["-vf", f"scale={width}:-2"] if width else []

    if start_s <= PREROLL_S:
        seek = ["-i", str(src), "-ss", f"{start_s:.3f}"]
    else:
        seek = ["-ss", f"{start_s - PREROLL_S:.3f}", "-i", str(src),
                "-ss", f"{PREROLL_S:.3f}"]

    r = subprocess.run(
        [ff, "-y", "-loglevel", "error", *seek, "-t", f"{dur_s:.3f}", *scale,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
         "-an", "-movflags", "+faststart", str(dest)],
        capture_output=True, text=True)
    if r.returncode != 0:
        print(f"      ffmpeg: {r.stderr.strip()[:140]}")
    return dest.exists() and dest.stat().st_size > 2000


def already_mined(records) -> set:
    return {r.source_video for r in records}


def mine_one_source(ff: str, raw_path, source_video: str, *, out_dir: Path,
                    manifest_path: Path, specialist: str, label: str,
                    dataset: str, clip_s: float, stride_s: float,
                    max_clips: int, width: int, crf: int, camera_id: str = "",
                    night: bool = False, hard_negative: bool = False,
                    hn_reason: str = "", detector=None,
                    verified_by: str = "clipmine") -> dict:
    """Cut, verify and record every clip from ONE raw video."""
    duration = probe_duration(raw_path)
    if duration <= 0:
        return {"kept": 0, "rejected": 0, "why": ["source would not decode"]}

    plan = plan_clips(duration, clip_s, stride_s, max_clips)
    kept, rejected, why = 0, 0, []
    want = EXPECTED_OBJECTS.get(specialist, ()) if detector is not None else ()

    for i, (start_s, end_s) in enumerate(plan):
        clip_id = f"{dataset}_{source_video}_t{int(start_s):05d}"
        dest = out_dir / dataset / f"{clip_id}.mp4"
        if not cut(ff, raw_path, dest, start_s, end_s - start_s, width, crf):
            rejected += 1
            why.append(f"{clip_id}: ffmpeg produced nothing")
            continue

        verdict = V.verify_clip(dest, end_s - start_s, detector=detector,
                                want_classes=want)
        if not verdict.ok:
            dest.unlink(missing_ok=True)
            rejected += 1
            why.append(f"{clip_id}: {verdict.reason}")
            continue

        try:
            rec = M.make_record(
                clip_id=clip_id,
                path=M.normalise_path(dest, manifest_path.parent),
                source_video=source_video, dataset=dataset, label=label,
                specialist=specialist, start_s=start_s, end_s=end_s,
                fps=round(verdict.frames / max(verdict.duration_s, 1e-6), 2),
                camera_id=camera_id, night=night,
                hard_negative=hard_negative, hn_reason=hn_reason,
                verified_by=verified_by)
        except M.ManifestError as e:
            dest.unlink(missing_ok=True)
            rejected += 1
            why.append(str(e))
            continue

        M.append_one(manifest_path, rec)     # one clip at a time; crash-safe
        kept += 1
        print(f"      [{i+1}/{len(plan)}] {clip_id}  "
              f"{verdict.duration_s:.1f}s ok", flush=True)

    return {"kept": kept, "rejected": rejected, "why": why}


# ------------------------------------------------------------------ sources
def meva_batch(args, mined: set) -> list:
    """(s3_key, source_video, night) for MEVA clips not already in the manifest."""
    hours = None
    if args.hours:
        hours = (set(meva.NIGHT_HOURS) if args.hours.strip().lower() == "night"
                 else {h.strip().zfill(2) for h in args.hours.split(",") if h.strip()})
    keys = meva.list_keys(args.camera, limit=args.sources * 4,
                          prefix=args.pool, hours=hours, spread=True)
    out = []
    for k in keys:
        source_video = Path(k).stem.replace(".", "_")
        if source_video in mined:
            continue
        hour = Path(k).parts[2] if len(Path(k).parts) > 2 else ""
        out.append((k, source_video, hour in meva.NIGHT_HOURS))
        if len(out) >= args.sources:
            break
    return out


def local_batch(args, mined: set) -> list:
    """Videos already on disk — UCF-Crime and anything else not fetchable."""
    root = Path(args.dir)
    if not root.exists():
        sys.exit(f"no such directory: {root}")
    out = []
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() not in (".mp4", ".avi", ".mkv", ".mov"):
            continue
        source_video = p.stem.replace(".", "_")
        if source_video in mined:
            continue
        out.append((p, source_video, args.night))
        if len(out) >= args.sources:
            break
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", default="meva", choices=("meva", "local"))
    p.add_argument("--dir", default="", help="local source: folder of videos")
    p.add_argument("--out", default="training/data",
                   help="where clips and the manifest live")
    p.add_argument("--manifest", default="", help="default: <out>/manifest.jsonl")

    p.add_argument("--specialist", default="vehicle",
                   choices=sorted(M.SPECIALISTS))
    p.add_argument("--label", default="",
                   help="default: the specialist's NORMAL class")
    p.add_argument("--dataset", default="",
                   help="default: the --source name")

    p.add_argument("--sources", type=int, default=20,
                   help="how many source videos to mine this run")
    p.add_argument("--clips", type=int, default=10,
                   help="clips per source video")
    p.add_argument("--clip-s", type=float, default=6.0)
    p.add_argument("--stride-s", type=float, default=25.0,
                   help="minimum spacing between clips from one source")
    p.add_argument("--width", type=int, default=640,
                   help="stored width; 0 keeps native. NOT 128 — see the "
                        "module docstring: a crop cannot be taken later from "
                        "a 128x128 thumbnail")
    p.add_argument("--crf", type=int, default=28)

    p.add_argument("--budget-gb", type=float, default=3.0,
                   help="stop once this much raw video has been downloaded")
    p.add_argument("--camera", default="G424", help="meva camera id")
    p.add_argument("--pool", default=meva.POOL)
    p.add_argument("--hours", default="", help="e.g. 00,16 or 'night'")
    p.add_argument("--night", action="store_true",
                   help="local source: mark these clips as night footage")
    p.add_argument("--hard-negative", action="store_true",
                   help="mark this whole batch as hard negatives")
    p.add_argument("--hn-reason", default="",
                   help="why they are hard, e.g. 'delivery at the door'")
    p.add_argument("--check-objects", action="store_true",
                   help="reject clips that do not contain what their label "
                        "claims. Needs the detector (ultralytics).")
    p.add_argument("--keep-raw", action="store_true",
                   help="do NOT delete the source video. Only for debugging — "
                        "the whole point of this tool is that it deletes.")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    dataset = args.dataset or args.source
    label = args.label or M.SPECIALISTS[args.specialist]["classes"][0]
    if label not in M.SPECIALISTS[args.specialist]["classes"]:
        sys.exit(f"label {label!r} is not a class of the {args.specialist} "
                 f"specialist: {M.SPECIALISTS[args.specialist]['classes']}")

    out_dir = Path(args.out)
    manifest_path = Path(args.manifest) if args.manifest \
        else out_dir / "manifest.jsonl"
    existing = M.read(manifest_path, strict=False) if manifest_path.exists() else []
    mined = already_mined(existing)
    print(f"manifest : {manifest_path}  ({len(existing)} clips from "
          f"{len(mined)} sources already)")
    print(f"label    : {label}  ({args.specialist} specialist)")
    print(f"storage  : {args.width or 'native'}px wide, crf {args.crf}"
          + ("  [hard negatives]" if args.hard_negative else ""))

    detector = None
    if args.check_objects:
        try:
            import yaml
            from app.detector import Detector
            cfg = yaml.safe_load(open("config.yaml"))["detection"]
            detector = Detector(cfg)
            print(f"objects  : verifying with {cfg.get('model')}")
        except Exception as e:                            # noqa: BLE001
            print(f"objects  : NOT verified — {e}")
    else:
        print("objects  : not verified (pass --check-objects to reject clips "
              "that do not contain what their label claims)")

    batch = (meva_batch(args, mined) if args.source == "meva"
             else local_batch(args, mined))
    if not batch:
        print("\nnothing new to mine — every source is already in the manifest")
        return 0
    print(f"\n{len(batch)} new source videos this run\n")

    if args.dry_run:
        for src, name, night in batch:
            print(f"  would mine {name}{'  [night]' if night else ''}")
        return 0

    ff = meva.ffmpeg()
    tmp = out_dir / "_raw"
    tmp.mkdir(parents=True, exist_ok=True)
    downloaded = 0.0
    totals = {"kept": 0, "rejected": 0}
    rejects: list = []

    for n, (src, source_video, night) in enumerate(batch, 1):
        if downloaded / 1024 ** 3 >= args.budget_gb:
            print(f"\nstopping: {downloaded / 1024**3:.1f} GB downloaded, "
                  f"budget was {args.budget_gb} GB")
            break

        if args.source == "meva":
            raw = tmp / Path(src).name
            print(f"  [{n}/{len(batch)}] downloading {Path(src).name} ...",
                  flush=True)
            r = subprocess.run(["curl", "-sSL", "-m", "1800",
                                f"{meva.BUCKET}/{src}", "-o", str(raw)],
                               capture_output=True, text=True)
            if r.returncode != 0 or not raw.exists() or raw.stat().st_size < 1e6:
                print(f"      download failed: {r.stderr.strip()[:120]}")
                raw.unlink(missing_ok=True)
                continue
            downloaded += raw.stat().st_size
        else:
            raw = Path(src)
            print(f"  [{n}/{len(batch)}] {raw.name}", flush=True)

        try:
            res = mine_one_source(
                ff, raw, source_video, out_dir=out_dir,
                manifest_path=manifest_path, specialist=args.specialist,
                label=label, dataset=dataset, clip_s=args.clip_s,
                stride_s=args.stride_s, max_clips=args.clips,
                width=args.width, crf=args.crf,
                camera_id=args.camera if args.source == "meva" else "",
                night=night, hard_negative=args.hard_negative,
                hn_reason=args.hn_reason, detector=detector)
        finally:
            # The point of the whole tool: the 200 MB source is worthless once
            # the clips exist, and keeping it is how a laptop fills up.
            if args.source == "meva" and not args.keep_raw:
                raw.unlink(missing_ok=True)

        totals["kept"] += res["kept"]
        totals["rejected"] += res["rejected"]
        rejects += res["why"][:3]
        print(f"      kept {res['kept']}, rejected {res['rejected']}"
              f"   ({downloaded / 1024**3:.2f} GB downloaded so far)")

    print(f"\nkept {totals['kept']} clips, rejected {totals['rejected']}")
    if rejects:
        print("\nwhy clips were rejected (first few):")
        for r in rejects[:10]:
            print(f"  {r}")

    records = M.read(manifest_path, strict=False)
    print(f"\nmanifest now holds {len(records)} clips from "
          f"{len({r.source_video for r in records})} source videos")
    for spec in sorted({r.specialist for r in records}):
        ready = M.readiness(records, spec)
        state = "READY" if ready["ready"] else "not ready"
        print(f"\n  {spec}: {ready['clips']} clips, "
              f"{ready['sources']} sources — {state}")
        for b in ready["blockers"]:
            print(f"    - {b}")

    print("\nNext:  python -m training.splits_cli   # assign train/val/test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
