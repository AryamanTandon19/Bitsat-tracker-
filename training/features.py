"""Turn a candidate window into numbers a person can argue with.

This is Tier 0 of the plan, and the baseline everything later has to beat. It
takes what the free layer already produces — tracked boxes over a few seconds —
and turns one (person, vehicle) pair into a fixed-length vector. No pixels are
read.

That last point is the whole argument for doing this first. `app/specialist.py`
records how the previous approach failed: a full-frame video model "observed to
drift up to ~0.72-0.78 on unrelated live scenes". It had learned what those
scenes looked like. A feature vector built from geometry and time cannot drift
that way, because it never sees a scene — only how far a person was from a car,
how long they stayed, and whether they walked past or milled around.

It is also the only tier that is explainable by construction. "Stood within
half a car-length for 41 seconds, reversed direction 6 times, never walked
away" is a sentence a guard and a committee can both check against the video.
A softmax cannot be checked against anything.

Design rules, both learned the hard way elsewhere in this codebase:

  **Everything scale-free.** Distances are in multiples of the vehicle's own
  radius, never pixels. `app/track.py` needed the same rule for the same
  reason: a fixed pixel threshold is simultaneously too tight for a car near
  the camera and too loose for a person at the far end of the lot.

  **Totals and runs are different features.** Ten one-second visits and one
  ten-second stand both total ten seconds, and only one of them is loitering.

Pure Python. No numpy, no torch, no database — every feature is testable from
a list of boxes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.rules import iou

# How near, on the ground, two things must be before overlapping boxes mean
# they are actually touching. Half a vehicle radius: close enough to be at the
# door, far enough that someone crossing the frame in front of the car does
# not qualify.
CONTACT_RADII = 0.5

# The order IS the contract: a model trained on this vector is read back with
# the same order. Appending is safe, reordering silently retrains meaning onto
# the wrong column.
FEATURES = (
    # -- how close, and for how long ------------------------------------
    "min_gap_radii",            # closest approach, in vehicle radii
    "mean_gap_radii",
    "frac_frames_close",        # share of the window spent within one radius
    "contact_frames",           # frames where the boxes actually overlap
    "near_s",                   # seconds spent close
    "longest_near_run_s",       # the longest UNBROKEN close stretch
    "revisits",                 # how many separate close stretches
    "dwell_s",                  # how long the person was present at all
    # -- how they moved --------------------------------------------------
    "approach_speed_radii_s",   # rate the gap closed (negative = leaving)
    "mean_speed_radii_s",
    "speed_variation",          # stop-start movement vs steady walking
    "straightness",             # 1.0 = walked straight past; low = milled
    "direction_reversals",
    "net_displacement_radii",
    # -- shape, as a pose proxy that needs no pose model -----------------
    "height_drop_ratio",        # crouching shortens the box
    "aspect_variation",
    # -- context ---------------------------------------------------------
    "night",
    "hour_sin", "hour_cos",
    "vehicle_registered",
    "person_size_radii",        # how big they are = how far away, how reliable
    "detection_confidence",
    "people_in_frame",
    "vehicles_in_frame",
    "camera_false_alarm_rate",
)


@dataclass
class Box:
    """One detection in one frame."""
    x1: float
    y1: float
    x2: float
    y2: float
    conf: float = 1.0

    @property
    def w(self) -> float:
        return max(0.0, self.x2 - self.x1)

    @property
    def h(self) -> float:
        return max(0.0, self.y2 - self.y1)

    @property
    def center(self) -> tuple:
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)

    @property
    def foot(self) -> tuple:
        """Where the object meets the ground. Two people at different depths
        have very different box centres and comparable foot points, so this is
        the honest thing to measure distance between."""
        return ((self.x1 + self.x2) / 2, self.y2)

    @property
    def radius(self) -> float:
        return max(1.0, math.hypot(self.w, self.h) / 2)

    @property
    def xyxy(self) -> tuple:
        return (self.x1, self.y1, self.x2, self.y2)


@dataclass
class Frame:
    """One moment: the pair being scored, plus what else was around."""
    ts: float
    person: Box | None = None
    vehicle: Box | None = None
    people_in_frame: int = 0
    vehicles_in_frame: int = 0


@dataclass
class Context:
    """What the geometry cannot know."""
    night: bool = False
    hour: float = 12.0
    vehicle_registered: bool = False
    camera_false_alarm_rate: float = 0.0
    zone: str = ""
    extra: dict = field(default_factory=dict)


# ------------------------------------------------------------- primitives
def gap_radii(person: Box, vehicle: Box) -> float:
    """Foot-to-foot distance in multiples of the vehicle's radius.

    0 means standing on it. 1 means about one car-length away. The number
    means the same thing on a camera looking down a long car park as on one
    two metres above a gate, which pixels never would.
    """
    px, py = person.foot
    vx, vy = vehicle.foot
    return math.dist((px, py), (vx, vy)) / vehicle.radius


def _runs(flags: list, stamps: list) -> list:
    """Contiguous True stretches -> [(start_ts, end_ts), ...]."""
    out, start = [], None
    for flag, ts in zip(flags, stamps):
        if flag and start is None:
            start = ts
        elif not flag and start is not None:
            out.append((start, ts))
            start = None
    if start is not None:
        out.append((start, stamps[-1]))
    return out


def _heading(a: tuple, b: tuple) -> float | None:
    dx, dy = b[0] - a[0], b[1] - a[1]
    if math.hypot(dx, dy) < 1e-9:
        return None
    return math.atan2(dy, dx)


def _angle_between(h1: float, h2: float) -> float:
    d = abs(h1 - h2) % math.tau
    return min(d, math.tau - d)


# ------------------------------------------------------------- extraction
def pair_features(frames: list, ctx: Context | None = None,
                  close_radii: float = 1.0,
                  reversal_angle: float = math.pi * 0.6,
                  min_step_radii: float = 0.05) -> dict:
    """One (person, vehicle) pair over a window -> the feature dict.

    Frames where the person is missing are skipped rather than interpolated:
    the tracker loses people routinely, and inventing positions would put
    fabricated motion into a feature the model then learns from.
    """
    ctx = ctx or Context()
    seen = [f for f in frames if f.person is not None]
    out = {name: 0.0 for name in FEATURES}

    out["night"] = 1.0 if ctx.night else 0.0
    out["hour_sin"] = math.sin(math.tau * (ctx.hour % 24) / 24)
    out["hour_cos"] = math.cos(math.tau * (ctx.hour % 24) / 24)
    out["vehicle_registered"] = 1.0 if ctx.vehicle_registered else 0.0
    out["camera_false_alarm_rate"] = float(ctx.camera_false_alarm_rate)
    if not seen:
        return out

    stamps = [f.ts for f in seen]
    out["dwell_s"] = round(stamps[-1] - stamps[0], 3)
    out["people_in_frame"] = max(f.people_in_frame for f in seen)
    out["vehicles_in_frame"] = max(f.vehicles_in_frame for f in seen)
    out["detection_confidence"] = round(
        sum(f.person.conf for f in seen) / len(seen), 4)

    # scale: the vehicle when there is one, otherwise the person themselves
    ref = next((f.vehicle.radius for f in seen if f.vehicle), None) \
        or seen[0].person.radius
    out["person_size_radii"] = round(
        sum(f.person.radius for f in seen) / len(seen) / ref, 4)

    # ---- proximity ----------------------------------------------------
    paired = [f for f in seen if f.vehicle is not None]
    if paired:
        gaps = [gap_radii(f.person, f.vehicle) for f in paired]
        out["min_gap_radii"] = round(min(gaps), 4)
        out["mean_gap_radii"] = round(sum(gaps) / len(gaps), 4)
        close = [g <= close_radii for g in gaps]
        out["frac_frames_close"] = round(sum(close) / len(close), 4)
        # Contact needs BOTH: overlapping in the image and standing on nearly
        # the same ground. Box overlap alone is not contact — a person walking
        # ten metres in front of a parked car overlaps it in every frame, and
        # a camera looking down a car park sees that constantly. Calling that
        # "touching the vehicle" would be one of the larger false-alarm
        # sources in the whole system.
        out["contact_frames"] = float(sum(
            1 for f, g in zip(paired, gaps)
            if g <= CONTACT_RADII and iou(f.person.xyxy, f.vehicle.xyxy) > 0))

        runs = _runs(close, [f.ts for f in paired])
        out["near_s"] = round(sum(b - a for a, b in runs), 3)
        out["longest_near_run_s"] = round(
            max((b - a for a, b in runs), default=0.0), 3)
        # a "revisit" is a fresh approach, not a tracker hiccup
        out["revisits"] = float(sum(1 for a, b in runs if b - a > 0.4))

        if len(paired) >= 2:
            span = paired[-1].ts - paired[0].ts
            if span > 1e-6:
                out["approach_speed_radii_s"] = round(
                    (gaps[0] - gaps[-1]) / span, 4)
    else:
        # No vehicle in the window at all. Leave the proximity features at
        # zero but say so, rather than letting "never measured" look like
        # "measured as touching".
        out["min_gap_radii"] = 99.0
        out["mean_gap_radii"] = 99.0

    # ---- movement ------------------------------------------------------
    pts = [f.person.foot for f in seen]
    steps = [math.dist(a, b) / ref for a, b in zip(pts, pts[1:])]
    dts = [b - a for a, b in zip(stamps, stamps[1:])]
    speeds = [s / dt for s, dt in zip(steps, dts) if dt > 1e-6]
    path = sum(steps)
    net = math.dist(pts[0], pts[-1]) / ref
    out["net_displacement_radii"] = round(net, 4)
    out["straightness"] = round(net / path, 4) if path > 1e-6 else 0.0
    if speeds:
        mean = sum(speeds) / len(speeds)
        out["mean_speed_radii_s"] = round(mean, 4)
        if mean > 1e-9 and len(speeds) > 1:
            var = sum((s - mean) ** 2 for s in speeds) / len(speeds)
            out["speed_variation"] = round(math.sqrt(var) / mean, 4)

    # heading reversals: only count steps big enough to have a real direction
    headings = [h for a, b, step in zip(pts, pts[1:], steps)
                for h in [_heading(a, b)]
                if h is not None and step >= min_step_radii]
    out["direction_reversals"] = float(sum(
        1 for h1, h2 in zip(headings, headings[1:])
        if _angle_between(h1, h2) >= reversal_angle))

    # ---- shape, as a crouch proxy --------------------------------------
    heights = [f.person.h for f in seen]
    tallest = max(heights)
    if tallest > 1e-6:
        out["height_drop_ratio"] = round(1.0 - min(heights) / tallest, 4)
    aspects = [f.person.w / f.person.h for f in seen if f.person.h > 1e-6]
    if len(aspects) > 1:
        mean_a = sum(aspects) / len(aspects)
        if mean_a > 1e-9:
            var = sum((a - mean_a) ** 2 for a in aspects) / len(aspects)
            out["aspect_variation"] = round(math.sqrt(var) / mean_a, 4)

    return out


def to_vector(feats: dict) -> list:
    """Fixed order, every time. Missing features are 0.0, not dropped."""
    return [float(feats.get(name, 0.0)) for name in FEATURES]


def explain(feats: dict, top: int = 4) -> str:
    """The features that would make a person suspicious, in words.

    Not a model explanation — that comes from the trained model's own feature
    importances. This is for reading a candidate while building the dataset,
    and for the case the product exists for: a guard checking a claim against
    the video.

    Context and CONTRADICTING evidence are never truncated. "The vehicle is
    registered to a resident" is the single most important thing in the
    sentence when it is true — `app/fusion.py` treats it as a downgrade — and
    dropping it because four other phrases came first would show a guard
    exactly the half of the story that makes them act.
    """
    bits = []
    gap = feats.get("min_gap_radii", 99)
    contact = feats.get("contact_frames", 0) > 0
    if contact:
        bits.append("was touching or overlapping the vehicle")
    elif gap < 0.6:
        bits.append(f"came within {gap:.1f} car-lengths")
    if feats.get("longest_near_run_s", 0) >= 20:
        bits.append(f"stayed close for {feats['longest_near_run_s']:.0f}s "
                    "without a break")
    elif feats.get("near_s", 0) >= 20:
        bits.append(f"close for {feats['near_s']:.0f}s in total")
    if feats.get("revisits", 0) >= 2:
        bits.append(f"came back {int(feats['revisits'])} separate times")
    if feats.get("straightness", 1) < 0.35 and feats.get("dwell_s", 0) > 5:
        bits.append("milled around rather than walking past")
    if feats.get("direction_reversals", 0) >= 3:
        bits.append(f"changed direction {int(feats['direction_reversals'])} times")
    if feats.get("height_drop_ratio", 0) > 0.25:
        bits.append("crouched or bent down")

    always = []
    if feats.get("night"):
        always.append("at night")
    if feats.get("vehicle_registered"):
        always.append("but the vehicle is registered to a resident")

    kept = bits[:top] + always
    return "; ".join(kept) if kept else "nothing notable"


# ------------------------------------------------------------ candidates
def candidate_pairs(frames: list, max_gap_radii: float = 3.0) -> list:
    """Which (person, vehicle) pairs in a window are worth scoring at all.

    `frames` here carries every track, keyed by id, rather than one chosen
    pair. A person who never came within three car-lengths of a vehicle is not
    a candidate for "did something to that vehicle", and scoring every pairing
    on a busy frame is quadratic for no gain.
    """
    pairs: dict = {}
    for f in frames:
        for pid, pbox in (f.people or {}).items():
            for vid, vbox in (f.vehicles or {}).items():
                g = gap_radii(pbox, vbox)
                if g <= max_gap_radii:
                    key = (pid, vid)
                    pairs[key] = min(pairs.get(key, 1e9), g)
    return [k for k, _g in sorted(pairs.items(), key=lambda kv: kv[1])]


@dataclass
class MultiFrame:
    """A frame carrying every track, for candidate_pairs()."""
    ts: float
    people: dict = field(default_factory=dict)      # track_id -> Box
    vehicles: dict = field(default_factory=dict)


def window_for(frames: list, person_id, vehicle_id) -> list:
    """Pull one pair's Frames out of a list of MultiFrames."""
    return [Frame(ts=f.ts,
                  person=(f.people or {}).get(person_id),
                  vehicle=(f.vehicles or {}).get(vehicle_id),
                  people_in_frame=len(f.people or {}),
                  vehicles_in_frame=len(f.vehicles or {}))
            for f in frames]
