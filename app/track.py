"""Following one object through moving video.

Everything before this worked on a single paused frame. That is the easy half.
A car reversing out of a bay does not travel in a straight line: it swings,
the visible shape changes from a side view to a three-quarter view to a back
view, half of it disappears behind the car in the next bay, and for four
frames the model calls it a truck. A person walking is worse — they sway,
their arms change the outline every frame, they stop to look at their phone,
and at 20 pixels wide the detector loses them entirely every second or so.

So the design here starts from what actually goes wrong rather than from a
clean motion model:

  **Match on masks, not boxes.** A car turning through 90 degrees changes its
  bounding rectangle enormously while its pixels overlap almost completely
  from frame to frame. Box IoU calls that a new object; mask IoU does not.

  **Predict only to aim, never to invent.** A constant-velocity guess is used
  to shift the previous outline before comparing it, which is what lets the
  match survive fast motion. It is never written down as an observation. If
  nothing matches, we say we lost it. A tracker that quietly emits its own
  prediction as a detection produces beautiful, confident, wrong labels, and
  a training set built from those is worse than no training set.

  **Gates scale with the object.** A fixed "objects move less than 60 pixels"
  rule is wrong at both ends: it loses a car near the camera and it happily
  swaps two pedestrians at the far end of the car park. Every threshold here
  is relative to the object's own size.

  **Bridge short gaps by interpolating between two real sightings**, mark
  those frames as interpolated so nobody mistakes them for evidence, and flag
  them for review when the gap was long enough that the object could have done
  something interesting inside it.

  **Say when a match was ugly.** A sudden jump, a sudden change of size, a
  low overlap — each of those is recorded on the frame it happened, with a
  sentence saying which. Those are the frames a person should look at, and
  handing someone 200 frames with no idea which three are wrong is the same
  as handing them nothing.

What this is not: a re-identification system. If an object leaves the picture
and comes back two minutes later, this will not know it is the same one, and
it does not pretend to. Linking those is a person's judgement, and the
workbench lets them make it.
"""
from __future__ import annotations

import math
import time
from collections import Counter
from dataclasses import dataclass, field

from .tagging import (polygon_area, polygon_bbox, polygon_centroid,
                      polygon_iou)

# Why a frame was flagged. Kept as short phrases because they are shown to a
# person next to the frame they describe.
JUMPED = "moved further in one step than it should have"
SHRANK = "changed size sharply — it may have merged with something else"
WEAK = "the outlines barely overlapped, so this match is a guess"
RECLASSED = "the model changed its mind about what this is"
GAP = "reconstructed between two real sightings, not observed"


@dataclass
class TrackConfig:
    """Every threshold that decides whether two outlines are the same object.

    The defaults are for car-park CCTV at roughly 20-30fps with a stride of 2:
    a few frames apart, objects that are the same object overlap a lot. Push
    the stride up and these have to loosen, which is why they travel together
    in one object rather than being scattered through the code.
    """
    stride: int = 2                 # process every Nth frame
    max_frames: int = 400           # a hard ceiling on one job
    iou_gate: float = 0.12          # below this it is not a match at all
    weak_iou: float = 0.35          # above the gate but worth a person's eye
    distance_gate: float = 2.2      # in multiples of the object's own radius
    jump_ratio: float = 1.1         # a step this big, relative to size, is odd
    area_ratio: float = 2.6         # a size change this big is a merge
    max_gap: int = 6                # processed frames we will bridge across
    review_gap: int = 2             # gaps longer than this want checking
    class_stickiness: float = 0.5   # how much a class match is worth in scoring
    velocity_smoothing: float = 0.6  # how fast the motion estimate follows


@dataclass
class Observation:
    """One outline of one object on one frame."""
    frame_index: int
    timestamp_ms: int
    polygons: list
    bbox: tuple
    confidence: float
    class_name: str
    kind: str = "tracked"           # anchor | tracked | interpolated
    iou: float | None = None        # overlap with the previous sighting
    needs_review: bool = False
    why: str = ""

    @property
    def main_ring(self) -> list:
        """The biggest visible piece. An object split in two by a pillar has
        one large piece and one sliver, and following the sliver would make a
        parked car appear to jitter across the frame."""
        return main_ring_of(self)

    @property
    def centroid(self) -> tuple:
        return centroid_of(self)

    @property
    def area(self) -> float:
        return area_of(self)

    @property
    def radius(self) -> float:
        """Half the diagonal of its box — one number for 'how big is this',
        used to scale every distance threshold."""
        w = self.bbox[2] - self.bbox[0]
        h = self.bbox[3] - self.bbox[1]
        return max(1.0, math.hypot(w, h) / 2)


@dataclass
class Tracklet:
    """One object's journey through a stretch of video."""
    category: str
    observations: list = field(default_factory=list)
    lost_at: int | None = None
    lost_why: str = ""
    model: str = ""
    stride: int = 1

    @property
    def frames(self) -> int:
        return len(self.observations)

    @property
    def observed(self) -> int:
        return sum(1 for o in self.observations if o.kind != "interpolated")

    @property
    def reconstructed(self) -> int:
        return sum(1 for o in self.observations if o.kind == "interpolated")

    @property
    def flagged(self) -> int:
        return sum(1 for o in self.observations if o.needs_review)

    @property
    def span(self) -> tuple:
        if not self.observations:
            return (0, 0)
        return (self.observations[0].frame_index,
                self.observations[-1].frame_index)

    def summary(self) -> dict:
        a, b = self.span
        return {"category": self.category, "start_frame": a, "end_frame": b,
                "frames": self.frames, "observed": self.observed,
                "reconstructed": self.reconstructed, "flagged": self.flagged,
                "lost_at": self.lost_at, "lost_why": self.lost_why,
                "model": self.model, "stride": self.stride}


# ------------------------------------------------------------- geometry
def resample_polygon(poly, n: int) -> list:
    """Re-space a ring's points evenly along its perimeter.

    Two outlines of the same car on consecutive frames have different numbers
    of points in different places, so they cannot be blended point by point
    until they are made comparable. Arc length is the right parameter: it is
    what makes the nose of the car stay the nose of the car.
    """
    pts = [(float(x), float(y)) for x, y in poly]
    if len(pts) < 2 or n < 3:
        return pts
    closed = pts + [pts[0]]
    seg = [math.dist(closed[i], closed[i + 1]) for i in range(len(pts))]
    total = sum(seg)
    if total <= 0:
        return pts[:n] or pts
    out, target, walked, i = [], 0.0, 0.0, 0
    step = total / n
    for _ in range(n):
        while i < len(seg) and walked + seg[i] < target:
            walked += seg[i]
            i += 1
        if i >= len(seg):
            out.append(closed[-1])
            continue
        t = (target - walked) / seg[i] if seg[i] else 0.0
        x = closed[i][0] + (closed[i + 1][0] - closed[i][0]) * t
        y = closed[i][1] + (closed[i + 1][1] - closed[i][1]) * t
        out.append((x, y))
        target += step
    return out


def align_polygon(a, b) -> list:
    """Rotate ring `b` so its first point is the one nearest `a`'s first point.

    Without this the blend between two frames twists: the shapes are right but
    the correspondence is off by a few vertices, and the interpolated outline
    in the middle looks like the object briefly turned inside out.
    """
    if not a or not b:
        return list(b)
    ax, ay = a[0]
    best = min(range(len(b)), key=lambda i: (b[i][0] - ax) ** 2
               + (b[i][1] - ay) ** 2)
    return list(b[best:]) + list(b[:best])


def interpolate_polygon(a, b, t: float, points: int = 48) -> list:
    """A shape part way between two outlines.

    Straight-line motion of every vertex, which is exactly as much as we can
    honestly claim about a frame nobody looked at. Objects do not move in
    straight lines — that is the whole problem — so these frames are labelled
    `interpolated` and are never presented as observations.
    """
    if not a:
        return [(x, y) for x, y in b]
    if not b:
        return [(x, y) for x, y in a]
    ra = resample_polygon(a, points)
    rb = align_polygon(ra, resample_polygon(b, points))
    t = min(max(float(t), 0.0), 1.0)
    return [(ax + (bx - ax) * t, ay + (by - ay) * t)
            for (ax, ay), (bx, by) in zip(ra, rb)]


def shift_polygons(polys, dx: float, dy: float) -> list:
    return [[(x + dx, y + dy) for x, y in p] for p in polys]


def main_ring_of(obj) -> list:
    """The largest ring of anything that carries polygons.

    A candidate off the segmenter and an Observation of ours are different
    types with the same geometry, and the matcher has to read both.
    """
    polys = getattr(obj, "polygons", None) or []
    return max(polys, key=polygon_area) if polys else []


def centroid_of(obj) -> tuple:
    ring = main_ring_of(obj)
    if ring:
        return polygon_centroid(ring)
    b = getattr(obj, "bbox", None)
    if not b:
        raise ValueError("this object has neither an outline nor a box")
    return ((float(b[0]) + float(b[2])) / 2, (float(b[1]) + float(b[3])) / 2)


def box_of(obj) -> tuple:
    b = getattr(obj, "bbox", None)
    if b:
        return tuple(float(v) for v in b)
    ring = main_ring_of(obj)
    if not ring:
        raise ValueError("this object has neither an outline nor a box")
    r = polygon_bbox(ring)
    return (r.x1, r.y1, r.x2, r.y2)


def boxes_overlap(a, b, pad: float = 0.0) -> bool:
    """Do two rectangles touch? The cheap test that comes first.

    Rasterising two polygons to compare them costs about as much as running
    the segmentation model on the whole frame — measured, on a car-park frame
    with twenty-four objects, at 1.6 seconds against the model's 0.11. Almost
    all of that was spent proving that a car at one end of the picture does
    not overlap a person at the other. A mask lives inside its own box, so
    boxes that do not touch cannot have masks that do, and this rejects them
    with eight comparisons instead of nine thousand point-in-polygon tests.
    """
    return not (a[2] + pad < b[0] or b[2] + pad < a[0]
                or a[3] + pad < b[1] or b[3] + pad < a[1])


def area_of(obj) -> float:
    polys = getattr(obj, "polygons", None) or []
    a = sum(polygon_area(p) for p in polys)
    if a:
        return a
    b = getattr(obj, "bbox", None) or (0, 0, 0, 0)
    return max(0.0, (float(b[2]) - float(b[0])) * (float(b[3]) - float(b[1])))


# ------------------------------------------------------------- following
class Follower:
    """Holds on to one object, frame after frame.

    One instance per tracked object. It is a plain state machine rather than a
    filter bank because everything it does has to be explainable to whoever is
    looking at a frame it got wrong.
    """

    def __init__(self, anchor: Observation, cfg: TrackConfig | None = None):
        self.cfg = cfg or TrackConfig()
        self.anchor = anchor
        self.last = anchor
        self.vx = self.vy = 0.0
        self.misses = 0
        self.classes = Counter([anchor.class_name])
        self.observations = [anchor]
        self.pending: list = []          # frames skipped while it was missing
        self.lost_at: int | None = None
        self.lost_why = ""

    # -- prediction ------------------------------------------------------
    def predict(self, frame_index: int) -> tuple:
        """Where the object probably is, used only to aim the comparison.

        Velocity is damped as the gap grows: one frame ahead a constant-speed
        guess is good, six frames ahead it is a fiction, and pretending
        otherwise is how a tracker latches onto the wrong car.
        """
        gap = max(0, frame_index - self.last.frame_index)
        damp = 1.0 / (1.0 + 0.35 * max(0, gap - 1))
        return (self.vx * gap * damp, self.vy * gap * damp)

    def _score(self, obs_polys, obs, cand, dx, dy) -> tuple:
        """(iou, distance_in_radii) for one candidate."""
        moved = shift_polygons(obs_polys, dx, dy)
        iou = polygon_iou(moved, cand.polygons, samples=48) if cand.polygons \
            else 0.0
        px, py = obs.centroid
        cx, cy = centroid_of(cand)
        dist = math.hypot(cx - (px + dx), cy - (py + dy)) / obs.radius
        return iou, dist

    # -- one step --------------------------------------------------------
    def step(self, frame_index: int, timestamp_ms: int,
             candidates: list) -> Observation | None:
        """Take the frame's objects and decide which, if any, is ours."""
        cfg = self.cfg
        dx, dy = self.predict(frame_index)
        best = None
        best_iou = 0.0
        best_dist = float("inf")

        # Two cheap passes before the expensive one. Everything on the frame
        # is a candidate in principle; almost none of it is worth rasterising.
        here = self.last.bbox
        moved_box = (here[0] + dx, here[1] + dy, here[2] + dx, here[3] + dy)
        px, py = self.last.centroid
        near = []
        for cand in candidates:
            try:
                cbox = box_of(cand)
            except ValueError:
                continue
            cx, cy = centroid_of(cand)
            dist = math.hypot(cx - (px + dx), cy - (py + dy)) / self.last.radius
            if dist > cfg.distance_gate and not boxes_overlap(moved_box, cbox):
                continue          # too far to be close, too far apart to overlap
            near.append((dist, cand))
        near.sort(key=lambda t: t[0])

        for dist, cand in near[:12]:
            iou, dist = self._score(self.last.polygons, self.last, cand, dx, dy)
            if iou < cfg.iou_gate and dist > cfg.distance_gate:
                continue
            # Class agreement is a tiebreak, not a requirement. At 20 pixels
            # the model flips between person, bicycle and nothing several
            # times a second; refusing to match across that loses the object
            # for reasons that have nothing to do with where it is.
            bonus = cfg.class_stickiness if cand.class_name in self.classes else 0.0
            rank = iou + bonus * 0.1 - dist * 0.02
            best_rank = best_iou + (cfg.class_stickiness * 0.1
                                    if best and best.class_name in self.classes
                                    else 0.0) - best_dist * 0.02
            if best is None or rank > best_rank:
                best, best_iou, best_dist = cand, iou, dist

        if best is None:
            self.misses += 1
            self.pending.append((frame_index, timestamp_ms))
            if self.misses > cfg.max_gap:
                self.lost_at = frame_index
                self.lost_why = (f"nothing matched for {self.misses} processed "
                                 "frames — it left, or it is hidden")
            return None

        obs = Observation(
            frame_index=frame_index, timestamp_ms=timestamp_ms,
            polygons=[list(p) for p in best.polygons],
            bbox=tuple(float(v) for v in best.bbox),
            confidence=float(best.confidence), class_name=best.class_name,
            kind="tracked", iou=round(best_iou, 3))

        reasons = []
        if best_dist > cfg.jump_ratio:
            reasons.append(JUMPED)
        if self.last.area > 0:
            ratio = obs.area / self.last.area if obs.area else 0.0
            if ratio and (ratio > cfg.area_ratio or ratio < 1 / cfg.area_ratio):
                reasons.append(SHRANK)
        if best_iou < cfg.weak_iou:
            reasons.append(WEAK)
        if best.class_name != self.classes.most_common(1)[0][0]:
            reasons.append(RECLASSED)
        if reasons:
            obs.needs_review = True
            obs.why = "; ".join(reasons)

        self._bridge(obs)
        self._advance(obs)
        return obs

    def _bridge(self, obs: Observation) -> None:
        """Fill the frames we could not see it on, between two frames we could."""
        if not self.pending:
            return
        long_gap = len(self.pending) > self.cfg.review_gap
        a, b = self.last, obs
        span = b.frame_index - a.frame_index
        for idx, ts in self.pending:
            t = (idx - a.frame_index) / span if span else 0.5
            polys = [interpolate_polygon(a.main_ring, b.main_ring, t)]
            polys = [p for p in polys if len(p) >= 3]
            if polys:
                r = polygon_bbox([pt for p in polys for pt in p])
                box = (r.x1, r.y1, r.x2, r.y2)
            else:
                box = tuple(av + (bv - av) * t
                            for av, bv in zip(a.bbox, b.bbox))
            self.observations.append(Observation(
                frame_index=idx, timestamp_ms=ts, polygons=polys, bbox=box,
                confidence=0.0, class_name=self.classes.most_common(1)[0][0],
                kind="interpolated", needs_review=long_gap,
                why=GAP if long_gap else ""))
        self.pending.clear()

    def _advance(self, obs: Observation) -> None:
        gap = max(1, obs.frame_index - self.last.frame_index)
        px, py = self.last.centroid
        cx, cy = obs.centroid
        s = self.cfg.velocity_smoothing
        self.vx = s * ((cx - px) / gap) + (1 - s) * self.vx
        self.vy = s * ((cy - py) / gap) + (1 - s) * self.vy
        self.last = obs
        self.misses = 0
        self.classes[obs.class_name] += 1
        self.observations.append(obs)

    # -- result ----------------------------------------------------------
    @property
    def lost(self) -> bool:
        return self.lost_at is not None

    def tracklet(self, model: str = "", stride: int = 1) -> Tracklet:
        obs = sorted(self.observations, key=lambda o: o.frame_index)
        return Tracklet(category=self.classes.most_common(1)[0][0],
                        observations=obs, lost_at=self.lost_at,
                        lost_why=self.lost_why, model=model, stride=stride)


def observation_from(obj, frame_index: int, timestamp_ms: int) -> Observation:
    """A SegmentedObject -> the Observation an anchor needs."""
    polys = [[(float(x), float(y)) for x, y in p]
             for p in (getattr(obj, "polygons", None) or [])]
    box = getattr(obj, "bbox", None)
    if box is None:
        r = polygon_bbox([pt for p in polys for pt in p])
        box = (r.x1, r.y1, r.x2, r.y2)
    return Observation(
        frame_index=frame_index, timestamp_ms=timestamp_ms, polygons=polys,
        bbox=tuple(float(v) for v in box),
        confidence=float(getattr(obj, "confidence", 0.0)),
        class_name=getattr(obj, "class_name", "unknown"), kind="anchor")


def follow(anchor: Observation, frames, cfg: TrackConfig | None = None,
           model: str = "", on_progress=None) -> Tracklet:
    """Run an anchor forward over `frames`.

    `frames` yields (frame_index, timestamp_ms, [SegmentedObject]) in order.
    Kept as an iterator so the caller owns decoding: this module is testable
    without a video file, and the job runner can stream rather than hold four
    hundred frames of masks in memory.
    """
    cfg = cfg or TrackConfig()
    f = Follower(anchor, cfg)
    seen = 0
    for frame_index, timestamp_ms, objects in frames:
        if frame_index <= anchor.frame_index:
            continue
        f.step(frame_index, timestamp_ms, objects)
        seen += 1
        if on_progress:
            on_progress(seen, frame_index, f)
        if f.lost or seen >= cfg.max_frames:
            break
    # frames we never saw it on, at the end, are not observations of anything
    f.pending.clear()
    return f.tracklet(model=model, stride=cfg.stride)


# ---------------------------------------------------------------- jobs
class TrackJob:
    """One background tracking run, and everything a caller needs to watch it.

    Tracking a minute of video is a minute or more of work, so it cannot
    happen inside a request. The state here is deliberately plain — a dict on
    the way out, a flag on the way in — because the alternative is a queue
    system for a workbench that one person uses at a time.
    """

    def __init__(self, job_id: str, clip_id: int, frame_index: int,
                 requested_by: str = ""):
        self.id = job_id
        self.clip_id = clip_id
        self.frame_index = frame_index
        self.requested_by = requested_by
        self.state = "queued"           # queued | running | done | failed | cancelled
        self.processed = 0
        self.total = 0
        self.at_frame = frame_index
        self.error = ""
        self.tracklet: Tracklet | None = None
        self.saved: list = []
        self.track_id: int | None = None
        self.started = time.time()
        self.finished: float | None = None
        self.cancel = False

    def public(self) -> dict:
        out = {"id": self.id, "clip_id": self.clip_id, "state": self.state,
               "processed": self.processed, "total": self.total,
               "at_frame": self.at_frame, "error": self.error,
               "started": self.started, "finished": self.finished,
               "track_id": self.track_id, "saved": len(self.saved)}
        if self.tracklet is not None:
            out["result"] = self.tracklet.summary()
        return out
