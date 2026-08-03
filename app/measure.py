"""Comparing what a detector found against what a person said was there.

Shared by evaluate_detector.py (one configuration, in detail) and
sweep_detector.py (many configurations, side by side), so there is one
matching rule and it is the tested one. A measurement harness that disagrees
with itself between two scripts is worse than no harness, because both numbers
look official.

The tally is deliberately kept by *size bucket* as well as by class. On a
car-park camera "78% recall" hides the only thing worth knowing: whether the
misses are the far-away people, in which case the fix is a bigger input size
or a lower floor, or the near ones, in which case something is wrong.
"""
from __future__ import annotations

from collections import defaultdict

# What the live detector has classes for. The workbench vocabulary is wider —
# bags, packages, gates — and an object the model was never asked to find
# cannot be a miss on its part, so it is excluded rather than counted against.
COMPARABLE = ("person", "car", "motorcycle", "bicycle", "bus", "truck")

# Pixels of box diagonal. Chosen where the answer changes: on society CCTV a
# person at 40px and a car at 250px are different detection problems, and one
# average hides both.
BUCKETS = ((0, 40, "tiny (<40px)"), (40, 80, "small (40-80px)"),
           (80, 160, "medium (80-160px)"), (160, 10 ** 9, "large (>160px)"))


def bucket_for(diagonal: float) -> str:
    for lo, hi, name in BUCKETS:
        if lo <= diagonal < hi:
            return name
    return BUCKETS[-1][2]


def diagonal(box) -> float:
    return ((box[2] - box[0]) ** 2 + (box[3] - box[1]) ** 2) ** 0.5


def box_iou(a, b) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    union = (max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
             + max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1]) - inter)
    return inter / union if union > 0 else 0.0


def match(truth: list, found: list, iou_gate: float) -> tuple:
    """Greedy one-to-one matching, best overlap first.

    Greedy rather than optimal on purpose: at these object counts it agrees
    with the optimal assignment almost always, and a rule somebody can follow
    in their head is worth more in a measurement harness than the last half
    percent.

    Returns (matched, missed, extra) — pairs of indices, truth indices nothing
    was found for, and found indices nothing was labelled for.
    """
    pairs = []
    for i, t in enumerate(truth):
        for j, d in enumerate(found):
            v = box_iou(t["bbox"], d["bbox"])
            if v >= iou_gate:
                pairs.append((v, i, j))
    pairs.sort(reverse=True)
    used_t: set = set()
    used_d: set = set()
    matched = []
    for v, i, j in pairs:
        if i in used_t or j in used_d:
            continue
        used_t.add(i)
        used_d.add(j)
        matched.append((i, j, v))
    return (matched,
            [i for i in range(len(truth)) if i not in used_t],
            [j for j in range(len(found)) if j not in used_d])


def match_per_class(truth: list, found: list, iou_gate: float) -> tuple:
    """The same, but a car found where a person is does not count as seeing
    the person. This is the default: a system that reports 'vehicle' when a
    man is climbing through a window has not seen the man."""
    matched: list = []
    missed: list = []
    left = list(range(len(found)))
    for cls in {t["cls"] for t in truth}:
        ti = [i for i, t in enumerate(truth) if t["cls"] == cls]
        di = [j for j in left if found[j]["cls"] == cls]
        m, ms, _ = match([truth[i] for i in ti], [found[j] for j in di],
                         iou_gate)
        for a, b, v in m:
            matched.append((ti[a], di[b], v))
            left.remove(di[b])
        missed += [ti[i] for i in ms]
    return matched, missed, left


class Tally:
    """Running totals, by class and by size, plus the frames that went worst."""

    def __init__(self, name: str = ""):
        self.name = name
        self.counts: dict = defaultdict(lambda: {"truth": 0, "hit": 0})
        self.extra = 0
        self.frames = 0
        self.seconds = 0.0
        self.worst: list = []

    def add_frame(self, truth: list, matched: list, missed: list,
                  extra: list, seconds: float = 0.0, where: dict | None = None):
        self.frames += 1
        self.extra += len(extra)
        self.seconds += seconds
        for i, _j, _v in matched:
            self._count(truth[i], True)
        for i in missed:
            self._count(truth[i], False)
        if missed and where is not None:
            self.worst.append({**where, "missed": len(missed),
                               "labelled": len(truth),
                               "classes": sorted({truth[i]["cls"]
                                                  for i in missed})})

    def _count(self, t: dict, hit: bool):
        for key in (t["cls"], bucket_for(diagonal(t["bbox"]))):
            self.counts[key]["truth"] += 1
            if hit:
                self.counts[key]["hit"] += 1

    def recall(self, key: str | None = None) -> float | None:
        if key is None:
            truth = sum(v["truth"] for k, v in self.counts.items()
                        if k in COMPARABLE)
            hit = sum(v["hit"] for k, v in self.counts.items()
                      if k in COMPARABLE)
        else:
            truth = self.counts.get(key, {}).get("truth", 0)
            hit = self.counts.get(key, {}).get("hit", 0)
        return hit / truth if truth else None

    @property
    def labelled(self) -> int:
        return sum(v["truth"] for k, v in self.counts.items()
                   if k in COMPARABLE)

    @property
    def found(self) -> int:
        return sum(v["hit"] for k, v in self.counts.items() if k in COMPARABLE)

    @property
    def seconds_per_frame(self) -> float:
        return self.seconds / self.frames if self.frames else 0.0

    def summary(self) -> dict:
        return {
            "name": self.name, "frames": self.frames,
            "labelled": self.labelled, "found": self.found,
            "recall": round(self.recall(), 4) if self.recall() is not None else None,
            "unlabelled_detections": self.extra,
            "seconds_per_frame": round(self.seconds_per_frame, 3),
            "by_class": {c: dict(self.counts[c]) for c in COMPARABLE
                         if c in self.counts},
            "by_size": {name: dict(self.counts[name])
                        for _lo, _hi, name in BUCKETS if name in self.counts},
        }


def track_share(anns) -> tuple:
    """How much of a label set is one object followed through video.

    Two hundred labels off one tracked car is one object measured two hundred
    times, not two hundred measurements. Every report that quotes a percentage
    has to be able to say this, or somebody will take a number built from one
    person walking across one car park into a meeting.
    """
    from_tracks = sum(1 for a in anns if getattr(a, "track_ref", None))
    distinct = len({a.track_ref for a in anns if getattr(a, "track_ref", None)})
    share = from_tracks / len(anns) if anns else 0.0
    return share, distinct


def usable_labels(anns) -> tuple:
    """Split a label set into what can be scored and why the rest cannot.

    Frames the tracker reconstructed are dropped outright: they are not
    sightings, and scoring a detector against our own interpolation measures
    the interpolation.
    """
    keep, interpolated, off_vocabulary = [], 0, 0
    for a in anns:
        if a.source == "interpolated":
            interpolated += 1
        elif a.category not in COMPARABLE:
            off_vocabulary += 1
        else:
            keep.append(a)
    return keep, interpolated, off_vocabulary
