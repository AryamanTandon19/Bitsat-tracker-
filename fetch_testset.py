#!/usr/bin/env python3
"""Build an evaluation set from public CCTV, when you have no footage of your own.

    python fetch_testset.py --list-cameras        # see what the scenes look like
    python fetch_testset.py --camera G424 --clips 6
    python validate_triggers.py                   # now it has something to measure

WHAT THIS SOLVES

Nothing in this project has been measured, because measuring needs labelled
footage and getting a society's DVR takes permission you may not have yet.
Public surveillance datasets close most of that gap today, for free.

WHAT IT DOES NOT SOLVE — read this before trusting a number it produces

This is a stand-in, not a substitute. Public footage is someone else's camera:
a different height, a different lens, different light, different cars, no
Indian plates. A false-alarm rate measured here tells you the system is
sane on real CCTV. It does not tell you what it will do on YOUR gate.
Treat it as a floor to clear before a pilot, never as the pilot's result.

It also carries almost no theft. MEVA is people going about their day, which
is exactly the *negative* class we are short of — the ordinary footage a
security system must stay quiet through. For the positive class you need
UCF-Crime (--source ucf, needs a manual download) or, honestly, half an hour
of you and a friend acting one out in your own car park.

THE SOURCE

MEVA (Multiview Extended Video with Activities): 328 hours of multi-camera
surveillance shot at a mock town, released CC BY-4.0 and hosted free on AWS
Open Data. No account, no agreement, no credentials. Camera G424 looks down
on a car park from roughly the height a society camera sits at, which is the
closest free thing to our actual problem.

    https://mevadata.org        CC BY-4.0
"""
from __future__ import annotations

import argparse
import csv
import re
import shutil
import subprocess
import sys
from pathlib import Path

BUCKET = "https://mevadata-public-01.s3.amazonaws.com"
PREFIX = "drop-4-hadcv22"

# What each MEVA camera is pointed at, from looking at every one of them.
# The useful column is "vehicles": a camera with no cars in it cannot tell us
# anything about a system whose whole job is watching cars.
CAMERAS = {
    "G424": ("car park, elevated — the closest match to a society camera", True),
    "G340": ("car park at distance + footpath", True),
    "G341": ("outdoor, coach and service vehicles", True),
    "G301": ("garage forecourt, one or two vehicles", True),
    "G336": ("footpath and green, vehicles far off", False),
    "G436": ("campus walkway, car park at the edge", False),
    "G638": ("building frontage, pedestrians", False),
    "G505": ("covered entrance, pedestrians", False),
    "G506": ("building entrance + a few parked cars", True),
    "G326": ("indoor stairwell", False),
    "G331": ("indoor waiting room", False),
    "G421": ("indoor hall", False),
    "G508": ("indoor corridor", False),
    "G509": ("stairwell", False),
}

DAYS = ["2018-03-15", "2018-03-08", "2018-03-11", "2018-03-05"]

# The bigger pool. `drop-4-hadcv22` holds one hour of one morning, which is why
# a test set built from it is one afternoon on one camera — the exact criticism
# that makes a false-alarm rate unusable. This drop has eight days and hours
# from midnight to five in the evening, and G424 appears in most of them.
#
# Measured from the bucket rather than remembered:
#   2018-03-05  09 10 11 13 14      2018-03-12  10 11
#   2018-03-07  09 10 11 12 16 17   2018-03-13  15 16 17
#   2018-03-09  10                  2018-03-14  07
#   2018-03-11  00 11 12 13 14 16 17  2018-03-15  14 15 16
# Hour 00 on 2018-03-11 is the only night footage in the set, and night is
# when a car park actually gets broken into, so it is worth more per minute
# than everything else here.
POOL = "drops-123-r13"
NIGHT_HOURS = {"00", "01", "02", "03", "04", "05", "19", "20", "21", "22", "23"}


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _get(url: str) -> str:
    return _run(["curl", "-s", "-m", "60", url]).stdout


def list_days(prefix: str) -> list[str]:
    xml = _get(f"{BUCKET}/?list-type=2&delimiter=/&prefix={prefix}/")
    return sorted(re.findall(rf"<Prefix>{prefix}/([\d-]+)/</Prefix>", xml))


def list_hours(prefix: str, day: str) -> list[str]:
    xml = _get(f"{BUCKET}/?list-type=2&delimiter=/&prefix={prefix}/{day}/")
    return sorted(re.findall(rf"<Prefix>{prefix}/{day}/(\d+)/</Prefix>", xml))


def list_keys(camera: str | None, limit: int = 400, prefix: str = PREFIX,
              hours: set | None = None, days: list | None = None,
              spread: bool = False) -> list[str]:
    """Clip keys for a camera, optionally restricted to certain hours.

    `spread` takes at most one clip per (day, hour) instead of filling up from
    the first hour it finds. Twenty clips from one hour are one situation
    sampled twenty times; twenty clips from twenty different hours are twenty
    situations, and only the second kind can tell you what a threshold does
    across a day.
    """
    days = days or list_days(prefix) or DAYS
    per_slot: list = []
    for day in days:
        for hour in list_hours(prefix, day) or [""]:
            if hours and hour not in hours:
                continue
            where = f"{prefix}/{day}/{hour}/" if hour else f"{prefix}/{day}/"
            found = re.findall(r"<Key>([^<]+\.avi)</Key>",
                               _get(f"{BUCKET}/?list-type=2&max-keys=800"
                                    f"&prefix={where}"))
            if camera:
                found = [k for k in found if f".{camera}." in k]
            if not found:
                continue
            per_slot.append(sorted(found))
            if not spread and sum(len(s) for s in per_slot) >= limit:
                break
        if not spread and sum(len(s) for s in per_slot) >= limit:
            break

    if spread:                       # round-robin: one from each slot, then
        out: list = []               # a second from each, and so on
        for i in range(max((len(s) for s in per_slot), default=0)):
            for slot in per_slot:
                if i < len(slot):
                    out.append(slot[i])
            if len(out) >= limit:
                break
        return out[:limit]
    return sorted({k for slot in per_slot for k in slot})[:limit]


def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:                       # the wheel we already depend on bundles one
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        sys.exit("ffmpeg not found; pip install imageio-ffmpeg")


def fetch(camera: str, n_clips: int, out_dir: Path, seg_s: int,
          segments_per_clip: int, prefix: str = PREFIX,
          hours: set | None = None, spread: bool = False) -> list[tuple[str, str]]:
    """Download clips, cut each into short segments, return (file, note)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir.parent / "_download"
    tmp.mkdir(exist_ok=True)
    keys = list_keys(camera, prefix=prefix, hours=hours, spread=spread)
    if not keys:
        sys.exit(f"no clips found for camera {camera}"
                 + (f" in hours {sorted(hours)}" if hours else ""))
    slots = sorted({tuple(Path(k).parts[1:3]) for k in keys[:n_clips]})
    print(f"{len(keys)} five-minute clips available on {camera}; "
          f"taking {min(n_clips, len(keys))} across {len(slots)} "
          f"day/hour slot{'' if len(slots) == 1 else 's'}")

    made: list[tuple[str, str]] = []
    ff = ffmpeg()
    for i, key in enumerate(keys[:n_clips], 1):
        raw = tmp / Path(key).name
        if not raw.exists():
            print(f"  [{i}/{n_clips}] downloading {Path(key).name} ...", flush=True)
            r = _run(["curl", "-sSL", "-m", "900", f"{BUCKET}/{key}",
                      "-o", str(raw)])
            if r.returncode != 0 or not raw.exists() or raw.stat().st_size < 1e6:
                print(f"      failed: {r.stderr.strip()[:120]}")
                continue

        # Cut into short segments. The harness decodes every frame, so five
        # minutes per clip would make a run take all afternoon for no extra
        # information — the scene barely changes.
        stem = Path(key).stem.replace(".", "_")
        for s in range(segments_per_clip):
            dest = out_dir / f"meva_{stem}_seg{s}.mp4"
            if dest.exists():
                made.append((dest.name, f"MEVA {camera}"))
                continue
            # +faststart moves the moov atom to the front. Without it a
            # browser cannot read the duration or seek until the whole file
            # has downloaded — the Train page showed a black player and no
            # timeline, which looked like a broken upload rather than a
            # muxing default.
            r = _run([ff, "-y", "-loglevel", "error",
                      "-ss", str(s * seg_s), "-t", str(seg_s),
                      "-i", str(raw), "-c:v", "libx264", "-preset", "veryfast",
                      "-crf", "26", "-an", "-movflags", "+faststart",
                      str(dest)])
            if dest.exists() and dest.stat().st_size > 10_000:
                made.append((dest.name, f"MEVA {camera}"))
            else:
                print(f"      segment {s} failed: {r.stderr.strip()[:120]}")
        raw.unlink(missing_ok=True)          # 105MB each; do not hoard them
    return made


def write_labels(rows: list[tuple[str, str]], labels: Path):
    """Append to labels.csv without disturbing anything already labelled.

    Everything from MEVA lands as `normal` with incident=0, which is the
    honest default: it is ordinary activity, and ordinary activity is exactly
    the class we need to prove the system stays quiet through. If you spot a
    genuine incident in one, change its row by hand — that is what the file
    is for.
    """
    existing: dict[str, dict] = {}
    if labels.exists():
        with labels.open() as f:
            for row in csv.DictReader(f):
                existing[row["filename"]] = row

    fields = ["filename", "type", "incident", "start_s", "end_s", "notes"]
    added = 0
    for name, note in rows:
        if name in existing:
            continue
        existing[name] = {"filename": name, "type": "normal", "incident": "0",
                          "start_s": "", "end_s": "",
                          "notes": f"{note} (CC BY-4.0) — public stand-in, not our site"}
        added += 1

    with labels.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in existing.values():
            w.writerow({k: row.get(k, "") for k in fields})
    return added, len(existing)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", default="G424",
                    help="MEVA camera id (default G424, a car park)")
    ap.add_argument("--clips", type=int, default=4,
                    help="how many 5-minute source clips to pull")
    ap.add_argument("--segment-s", type=int, default=60,
                    help="length of each cut segment")
    ap.add_argument("--segments-per-clip", type=int, default=3)
    ap.add_argument("--testset", default="testset")
    ap.add_argument("--list-cameras", action="store_true")
    ap.add_argument("--pool", default=POOL, choices=[POOL, PREFIX],
                    help=f"which MEVA drop to draw from. {POOL} (default) has "
                         f"eight days and hours 00-17; {PREFIX} has one hour "
                         "of one morning")
    ap.add_argument("--hours", default="",
                    help="restrict to these hours, e.g. 00,16,17. "
                         "'night' means " + ",".join(sorted(NIGHT_HOURS)))
    ap.add_argument("--spread", action="store_true",
                    help="take at most one clip per day/hour instead of "
                         "filling up from the first hour found — this is what "
                         "stops a test set being one afternoon")
    ap.add_argument("--list-times", action="store_true",
                    help="show which days and hours the pool actually has")
    args = ap.parse_args(argv)

    if args.list_cameras:
        print(f"{'camera':<8}{'vehicles':<11}view")
        for cam, (desc, veh) in CAMERAS.items():
            print(f"{cam:<8}{'yes' if veh else 'no':<11}{desc}")
        print("\nCameras without vehicles cannot tell you anything about a "
              "system whose job is watching cars.")
        return 0

    if args.list_times:
        print(f"pool {args.pool}\n")
        for day in list_days(args.pool):
            hrs = list_hours(args.pool, day)
            night = [h for h in hrs if h in NIGHT_HOURS]
            print(f"  {day}  {' '.join(hrs) or '(none)'}"
                  + ("   <- includes night" if night else ""))
        print("\nNight is when a car park actually gets broken into, so an "
              "hour of it is worth")
        print("more than a day of lunchtime. Use --hours night --spread.")
        return 0

    if args.camera not in CAMERAS:
        sys.exit(f"unknown camera {args.camera}; try --list-cameras")
    if not CAMERAS[args.camera][1]:
        print(f"warning: {args.camera} is {CAMERAS[args.camera][0]} — no vehicles\n")

    hours = None
    if args.hours:
        hours = (set(NIGHT_HOURS) if args.hours.strip().lower() == "night"
                 else {h.strip().zfill(2) for h in args.hours.split(",") if h.strip()})

    root = Path(args.testset)
    rows = fetch(args.camera, args.clips, root / "clips",
                 args.segment_s, args.segments_per_clip,
                 prefix=args.pool, hours=hours, spread=args.spread)
    if not rows:
        sys.exit("nothing downloaded")
    added, total = write_labels(rows, root / "labels.csv")

    print(f"\n{len(rows)} segments in {root / 'clips'}; "
          f"{added} new rows, {total} total in labels.csv")
    print("\nAll of it is labelled `normal` — MEVA is ordinary activity, which is")
    print("the class we were most short of. You still need incidents:")
    print("  * UCF-Crime (Stealing / Burglary / Vandalism) — real crime CCTV")
    print("  * or stage a few yourself: it is the fastest honest way to get the")
    print("    positive class, and it is footage of YOUR car park.")
    print("\nThen:  python validate_triggers.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
