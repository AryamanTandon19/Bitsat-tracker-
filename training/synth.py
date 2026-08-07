#!/usr/bin/env python3
"""Synthetic behaviour clips — so the brain can be built and proven before a
single hour of real CCTV is downloaded.

    python -m training.synth --out training/data/synth_features.jsonl

Why this exists
---------------
The brain (`app/brain.py`) learns from the geometry features in
`training/features.py`. Those features need *motion over time* — a person
approaching, lingering, circling, leaving. Real footage is the eventual
source, but it is not here yet, and a machine-learning pipeline that has never
once run end to end is a pipeline you do not know is correct.

So this module generates the *motion*, not the pixels. It moves a person-box
and a vehicle-box through a few seconds of frames according to the physics of a
handful of real scenarios, then feeds those boxes through the **exact same**
`features.pair_features` the production extractor uses. The result is a labelled
feature dataset that:

  * proves the whole chain (tracks -> features -> brain -> score) works **now**;
  * gives the brain something to train on today, so there is a working,
    trained brain to demonstrate before real data arrives;
  * stays as a permanent regression fixture — if a refactor ever breaks the
    features, these separable scenarios stop being separable and a test fails.

**This is a self-check, not a real-world accuracy number.** Synthetic motion is
cleaner than a car park at dusk. A number measured here says the machinery is
sound; it says nothing about how the brain does on Burglary017. That number
comes only from the untouched real holdout (plan step 17). Every report built
from this data is labelled `synthetic` for exactly that reason.

The scenarios
-------------
Normal (the negative class — ordinary life beside a car park)::

    walk_past     someone crosses the frame, briefly near a parked car
    own_car       resident walks to their own (registered) car and drives off
    delivery      someone approaches, pauses, and leaves again
    furniture     a static object misread as a person — the fire-hydrant case:
                  *near a car for the whole clip, but never moves a pixel*

Suspicious (the positive class)::

    loiter        stays close for 30-40s, milling, never leaving
    circle        walks a lap around the vehicle, in and out of the close band
    break_in      reaches the vehicle, stays in contact, crouches at the door

The discriminating pair is `furniture` vs `loiter`: both sit near the car for a
long time. Only one of them *moves*. A rule that alerts on "near for a while"
fires on both; the brain has to learn the difference, and these two scenarios
are what prove it did.
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from training import features as F
from training.extract import write_rows
from training import splits as S

# The scene. One parked vehicle, fixed, and one person who does something near
# it. Pixels are arbitrary — every feature is scale-free — but concrete numbers
# make the scenarios readable and debuggable.
VEH_FOOT = (500.0, 400.0)      # where the car meets the ground
VEH_W, VEH_H = 140.0, 100.0
VEH_RADIUS = math.hypot(VEH_W, VEH_H) / 2      # ~86 px = "one car-length"
PERSON_W, PERSON_H = 34.0, 90.0
DT = 0.34                        # seconds between frames (~3 fps, matches stride)

PERSON_ID, VEHICLE_ID = 1, 9

NORMAL = ("walk_past", "own_car", "delivery", "furniture")
SUSPICIOUS = ("loiter", "circle", "break_in")
SCENARIOS = NORMAL + SUSPICIOUS


def _vehicle_box() -> F.Box:
    vx, vy = VEH_FOOT
    return F.Box(vx - VEH_W / 2, vy - VEH_H, vx + VEH_W / 2, vy)


def _person_box(cx: float, foot_y: float, h: float = PERSON_H,
                w: float = PERSON_W, conf: float = 0.9) -> F.Box:
    return F.Box(cx - w / 2, foot_y - h, cx + w / 2, foot_y, conf=conf)


def _frames(track: list) -> list:
    """A person trajectory [(cx, foot_y, h, conf), ...] -> MultiFrames.

    The vehicle is present in every frame; the person walks through them.
    """
    veh = _vehicle_box()
    out = []
    for i, (cx, foot_y, h, conf) in enumerate(track):
        out.append(F.MultiFrame(
            ts=round(i * DT, 3),
            people={PERSON_ID: _person_box(cx, foot_y, h=h, conf=conf)},
            vehicles={VEHICLE_ID: veh}))
    return out


# ----------------------------------------------------------------- scenarios
# Each returns (frames, Context). rng drives per-instance variation so no two
# instances produce an identical feature vector — a model that "separates" a
# dataset of clones has proved nothing.

def _wobble(rng, sigma: float) -> float:
    return rng.gauss(0.0, sigma)


def walk_past(rng) -> tuple:
    """Crosses the frame in a roughly straight line, passing near the car."""
    n = rng.randint(16, 22)
    y = VEH_FOOT[1] + rng.uniform(20, 45)          # passes in front of the car
    x0, x1 = 120.0, 900.0
    track = []
    for i in range(n):
        cx = x0 + (x1 - x0) * i / (n - 1) + _wobble(rng, 4)
        track.append((cx, y + _wobble(rng, 3), PERSON_H, 0.9))
    return _frames(track), F.Context(hour=14.0)


def own_car(rng) -> tuple:
    """Resident walks to their own car, pauses at the door, drives off. The
    vehicle is registered — the single strongest 'this is fine' signal."""
    approach = rng.randint(8, 11)
    pause = rng.randint(6, 9)
    leave = rng.randint(8, 11)
    door = (VEH_FOOT[0] + rng.uniform(-15, 15), VEH_FOOT[1] + rng.uniform(12, 22))
    track = []
    for i in range(approach):
        f = i / (approach - 1)
        cx = 160 + (door[0] - 160) * f + _wobble(rng, 3)
        cy = 430 + (door[1] - 430) * f + _wobble(rng, 3)
        track.append((cx, cy, PERSON_H, 0.9))
    for _ in range(pause):
        track.append((door[0] + _wobble(rng, 4), door[1] + _wobble(rng, 3),
                      PERSON_H, 0.9))
    for i in range(leave):
        f = i / (leave - 1)
        cx = door[0] + (900 - door[0]) * f + _wobble(rng, 3)
        cy = door[1] + (430 - door[1]) * f + _wobble(rng, 3)
        track.append((cx, cy, PERSON_H, 0.9))
    return _frames(track), F.Context(hour=9.0, vehicle_registered=True)


def delivery(rng) -> tuple:
    """Approaches to about a car-length away, pauses briefly, leaves again."""
    approach = rng.randint(6, 9)
    pause = rng.randint(8, 12)
    leave = rng.randint(6, 9)
    stop = (VEH_FOOT[0] + rng.uniform(70, 110), VEH_FOOT[1] + rng.uniform(30, 60))
    track = []
    for i in range(approach):
        f = i / (approach - 1)
        track.append((160 + (stop[0] - 160) * f + _wobble(rng, 3),
                      440 + (stop[1] - 440) * f + _wobble(rng, 3), PERSON_H, 0.9))
    for _ in range(pause):
        track.append((stop[0] + _wobble(rng, 3), stop[1] + _wobble(rng, 3),
                      PERSON_H, 0.9))
    for i in range(leave):
        f = i / (leave - 1)
        track.append((stop[0] + (160 - stop[0]) * f + _wobble(rng, 3),
                      stop[1] + (440 - stop[1]) * f + _wobble(rng, 3),
                      PERSON_H, 0.9))
    return _frames(track), F.Context(hour=13.0)


def furniture(rng) -> tuple:
    """The fire hydrant. A static object detected as a person, near the car for
    the entire clip, that never moves. The hard negative the whole product
    exists to not alert on."""
    n = rng.randint(90, 120)                       # present a long time
    cx = VEH_FOOT[0] + rng.uniform(55, 75)         # inside the close band...
    cy = VEH_FOOT[1] + rng.uniform(-4, 4)
    conf = rng.uniform(0.42, 0.55)                 # the mediocre confidence of a misread
    # sub-pixel jitter from detector noise, never real motion: it stays below
    # features.STILL_SPEED so the `stillness` feature reads ~1.0, which is the
    # whole tell of a static object misread as a person
    track = [(cx + _wobble(rng, 0.02), cy + _wobble(rng, 0.02), PERSON_H, conf)
             for _ in range(n)]
    night = rng.random() < 0.3
    return _frames(track), F.Context(hour=2.0 if night else 15.0, night=night)


def loiter(rng) -> tuple:
    """Stays within a car-length for 30-40s, milling around, never leaving.
    Unlike furniture, it *moves* — small, aimless, stop-start motion."""
    n = rng.randint(90, 120)
    cx0 = VEH_FOOT[0] + rng.uniform(45, 70)
    cy0 = VEH_FOOT[1] + rng.uniform(-8, 12)
    track = []
    cx, cy = cx0, cy0
    for _ in range(n):
        cx += _wobble(rng, 9)                       # a random walk, staying put
        cy += _wobble(rng, 6)
        cx = max(cx0 - 40, min(cx0 + 40, cx))       # tethered near the car
        cy = max(cy0 - 25, min(cy0 + 25, cy))
        track.append((cx, cy, PERSON_H + _wobble(rng, 3), 0.85))
    night = rng.random() < 0.4
    return _frames(track), F.Context(hour=1.0 if night else 20.0, night=night)


def circle(rng) -> tuple:
    """Walks a lap (or two) around the vehicle, dipping in and out of the close
    band — the classic 'casing it' pattern."""
    n = rng.randint(70, 100)
    laps = rng.uniform(1.2, 2.2)
    r = rng.uniform(70, 95)
    phase = rng.uniform(0, math.tau)
    track = []
    for i in range(n):
        a = phase + laps * math.tau * i / (n - 1)
        cx = VEH_FOOT[0] + r * math.cos(a) + _wobble(rng, 4)
        cy = VEH_FOOT[1] + 0.5 * r * math.sin(a) + _wobble(rng, 3)  # foreshortened
        track.append((cx, cy, PERSON_H, 0.88))
    night = rng.random() < 0.4
    return _frames(track), F.Context(hour=23.0 if night else 18.0, night=night)


def break_in(rng) -> tuple:
    """Reaches the vehicle, stays in contact, crouches at the door. The one the
    hand-written gate should also catch — the clearest positive."""
    approach = rng.randint(8, 12)
    at_car = rng.randint(45, 70)
    door = (VEH_FOOT[0] + rng.uniform(-10, 10), VEH_FOOT[1] + rng.uniform(-6, 8))
    track = []
    for i in range(approach):
        f = i / (approach - 1)
        track.append((150 + (door[0] - 150) * f + _wobble(rng, 3),
                      440 + (door[1] - 440) * f + _wobble(rng, 3), PERSON_H, 0.9))
    for j in range(at_car):
        # crouch in the middle of the stay: the box gets shorter
        crouch = math.sin(math.pi * j / at_car)          # 0 -> 1 -> 0
        h = PERSON_H - 32 * crouch
        track.append((door[0] + _wobble(rng, 3), door[1] + _wobble(rng, 2),
                      h, 0.9))
    night = rng.random() < 0.5
    return _frames(track), F.Context(hour=2.0 if night else 21.0, night=night)


def loading(rng) -> tuple:
    """A resident loading luggage into their own boot: reaches the car, opens
    the trunk, makes several trips to and fro carrying things, then leaves.

    This is the hardest *normal* case in the whole set — long dwell AND contact
    AND back-and-forth motion, which is precisely what a break-in also looks
    like. It is the natural hard negative: a fresh brain that has never seen it
    tends to flag it, and that mistake is what the hard-negative loop mines and
    fixes. (See training/hardneg.py.) It is a registered vehicle, because it is
    the owner's car.
    """
    approach = rng.randint(8, 11)
    trips = rng.randint(3, 5)
    boot = (VEH_FOOT[0] + rng.uniform(-10, 12), VEH_FOOT[1] + rng.uniform(-4, 10))
    track = []
    for i in range(approach):
        f = i / (approach - 1)
        track.append((160 + (boot[0] - 160) * f + _wobble(rng, 3),
                      440 + (boot[1] - 440) * f + _wobble(rng, 3), PERSON_H, 0.9))
    # each trip: step away to fetch a bag, come back to the boot and set it down
    for _ in range(trips):
        away = (boot[0] + rng.uniform(-90, -50), boot[1] + rng.uniform(15, 45))
        for f in (0.5, 1.0, 0.5, 0.0):           # out and back
            cx = boot[0] + (away[0] - boot[0]) * f + _wobble(rng, 3)
            cy = boot[1] + (away[1] - boot[1]) * f + _wobble(rng, 3)
            track.append((cx, cy, PERSON_H, 0.9))
        for _ in range(rng.randint(3, 6)):        # at the boot, loading
            track.append((boot[0] + _wobble(rng, 3), boot[1] + _wobble(rng, 2),
                          PERSON_H, 0.9))
    for i in range(approach):                     # drive-off walk to the door
        f = i / (approach - 1)
        track.append((boot[0] + (760 - boot[0]) * f + _wobble(rng, 3),
                      boot[1] + (430 - boot[1]) * f + _wobble(rng, 3),
                      PERSON_H, 0.9))
    return _frames(track), F.Context(hour=11.0, vehicle_registered=True)


GENERATORS = {
    "walk_past": walk_past, "own_car": own_car, "delivery": delivery,
    "furniture": furniture, "loiter": loiter, "circle": circle,
    "break_in": break_in,
    # available for the hard-negative loop and richer training, but deliberately
    # NOT in SCENARIOS: it is a confuser used on purpose to exercise the miner,
    # so it stays out of the default balanced dataset.
    "loading": loading,
}


# ------------------------------------------------------------------ assembly
@dataclass
class _Rec:
    """Just enough of a manifest record for splits.assign()/report()."""
    source_video: str
    label: str
    specialist: str = "behavior"
    hard_negative: bool = False
    night: bool = False
    duration_s: float = 0.0
    split: str = ""


def _row(scenario: str, instance: int, frames, ctx) -> dict | None:
    """Turn one generated clip into a feature row, exactly as extract.py does."""
    pairs = F.candidate_pairs(frames)
    if (PERSON_ID, VEHICLE_ID) not in pairs:
        return None                                  # never came near — no candidate
    window = F.window_for(frames, PERSON_ID, VEHICLE_ID)
    feats = F.pair_features(window, ctx)
    duration = round((len(frames) - 1) * DT, 2)
    suspicious = scenario in SUSPICIOUS
    return {
        "clip_id": f"synth-{scenario}-{instance:04d}",
        # one clip per source video, so the source-separation split is exact
        "source_video": f"synth:{scenario}:{instance:04d}",
        "split": "",
        "label": scenario,
        "specialist": "behavior",
        "suspicious": int(suspicious),
        # furniture is the hard negative: near for a long time, but normal
        "hard_negative": int(scenario == "furniture"),
        "person_id": PERSON_ID, "vehicle_id": VEHICLE_ID,
        "camera_id": f"synth-{scenario}",
        "night": int(bool(ctx.night)),
        "duration_s": duration,
        "features": feats,
        "why": F.explain(feats),
    }


def make_rows(scenario: str, count: int, seed: int = 0,
              split: str = "train") -> list:
    """Feature rows for one scenario — handy for the hard-negative loop and for
    topping up the normal library with a specific behaviour. Every row is tagged
    with the given split and a unique source video, so nothing leaks."""
    if scenario not in GENERATORS:
        raise ValueError(f"unknown scenario {scenario!r}")
    rng = random.Random((hash(scenario) ^ seed) & 0xFFFFFFFF)
    rows = []
    for i in range(count):
        r = random.Random(rng.random())
        frames, ctx = GENERATORS[scenario](r)
        row = _row(scenario, i, frames, ctx)
        if row is not None:
            row["split"] = split
            rows.append(row)
    return rows


def dataset(per_scenario: int = 60, seed: int = 0,
            val_fraction: float = 0.2, test_fraction: float = 0.2) -> list:
    """Generate the full labelled, split feature dataset.

    Deterministic in ``seed``: the same seed yields byte-identical rows, so the
    dataset is a stable fixture and a stable input to a training run.
    """
    rng = random.Random(seed)
    rows = []
    for scenario in SCENARIOS:
        gen = GENERATORS[scenario]
        for i in range(per_scenario):
            # a fresh, deterministic sub-stream per instance
            r = random.Random(rng.random())
            frames, ctx = gen(r)
            row = _row(scenario, i, frames, ctx)
            if row is not None:
                rows.append(row)

    # split by source video (one clip each here), stratified by scenario, so
    # every scenario is represented in train/val/test and nothing leaks
    recs = [_Rec(source_video=row["source_video"], label=row["label"],
                 hard_negative=bool(row["hard_negative"]),
                 night=bool(row["night"]), duration_s=row["duration_s"])
            for row in rows]
    S.assign(recs, val_fraction=val_fraction, test_fraction=test_fraction,
             seed=seed)
    S.check_separation(recs)
    for row, rec in zip(rows, recs):
        row["split"] = rec.split
    return rows


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default="training/data/synth_features.jsonl")
    p.add_argument("--per-scenario", type=int, default=60)
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args(argv)

    rows = dataset(per_scenario=args.per_scenario, seed=args.seed)
    n = write_rows(args.out, rows)
    pos = sum(r["suspicious"] for r in rows)
    by_split: dict = {}
    for r in rows:
        by_split[r["split"]] = by_split.get(r["split"], 0) + 1
    print(f"wrote {n} synthetic feature rows -> {args.out}")
    print(f"  labels : {pos} suspicious, {n - pos} normal")
    print(f"  splits : {by_split}")
    print("\n  NOTE: synthetic self-check data. It proves the pipeline runs and")
    print("  separates clean scenarios; it is NOT a real-world accuracy number.")
    print(f"\nNext:  python -m training.brain_train --features {args.out} "
          "--synth-source")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
