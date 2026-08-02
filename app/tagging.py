"""Click-to-select geometry: turning a click in a browser into an object.

This is step one of Object Tagging, and it is deliberately the least exciting
part, because it is the part everything else is wrong without. If a click at
the top-left of a letterboxed video maps to the wrong pixel, every box, every
track and every label built on top of it is quietly wrong too, and nothing
downstream will ever tell you.

So this module is pure geometry — no video, no model, no database — and every
branch of it is tested. Coordinates in the rest of the system always mean
*original video pixels*; the browser's numbers are converted here, once, on the
way in.

The three questions it answers:

  where did they click      -> to_frame_coords()
  what is under that point  -> hit_test()
  and if nothing is         -> nearby()
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    """A box in original-video pixels. x2 > x1 and y2 > y1, always."""
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def area(self) -> float:
        return max(0.0, self.x2 - self.x1) * max(0.0, self.y2 - self.y1)

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)

    def contains(self, x: float, y: float) -> bool:
        return self.x1 <= x <= self.x2 and self.y1 <= y <= self.y2

    def distance_to(self, x: float, y: float) -> float:
        """0 inside the box; otherwise the distance to its nearest edge.

        Edge distance, not centre distance: a click just outside a long parked
        car should prefer that car over a small bag whose centre happens to be
        closer.
        """
        dx = max(self.x1 - x, 0.0, x - self.x2)
        dy = max(self.y1 - y, 0.0, y - self.y2)
        return (dx * dx + dy * dy) ** 0.5


def letterbox(display_w: float, display_h: float,
              video_w: float, video_h: float) -> tuple[float, float, float]:
    """Where the picture actually sits inside the player.

    A <video> preserves aspect ratio, so unless the element happens to match
    the footage exactly there are black bars — at the sides or top and bottom.
    Returns (offset_x, offset_y, scale): the bar sizes in display pixels, and
    how many display pixels make one video pixel.
    """
    if min(display_w, display_h, video_w, video_h) <= 0:
        raise ValueError("display and video dimensions must all be positive")
    scale = min(display_w / video_w, display_h / video_h)
    shown_w, shown_h = video_w * scale, video_h * scale
    return (display_w - shown_w) / 2, (display_h - shown_h) / 2, scale


def to_frame_coords(click_x: float, click_y: float,
                    display_w: float, display_h: float,
                    video_w: float, video_h: float) -> tuple[float, float]:
    """Browser click -> original video pixel.

    Handles letterboxing, browser scaling, fullscreen and any responsive
    layout, because all of those only ever change the display size — which is
    measured and passed in rather than assumed.

    A click on a black bar is clamped to the frame edge instead of returning a
    negative coordinate: the user aimed at the picture and missed by a few
    pixels, and refusing them is worse than snapping.
    """
    off_x, off_y, scale = letterbox(display_w, display_h, video_w, video_h)
    x = (click_x - off_x) / scale
    y = (click_y - off_y) / scale
    return (min(max(x, 0.0), video_w), min(max(y, 0.0), video_h))


def in_letterbox(click_x: float, click_y: float,
                 display_w: float, display_h: float,
                 video_w: float, video_h: float) -> bool:
    """True if the click landed on the picture rather than on a black bar.
    The UI uses this to say "you clicked outside the video" instead of
    silently selecting something at the edge."""
    off_x, off_y, scale = letterbox(display_w, display_h, video_w, video_h)
    return (off_x <= click_x <= display_w - off_x
            and off_y <= click_y <= display_h - off_y)


# ---------------------------------------------------------------- polygons
# A detector box says "the car is somewhere in this rectangle". A segmentation
# mask says "the car is these pixels". For selection that difference is the
# whole point: two cars parked at an angle have overlapping rectangles but
# disjoint outlines, and clicking one should not offer you the other.
#
# Masks travel as polygons in original-video pixels — the same coordinate space
# as everything else here — because a polygon is a few dozen numbers where a
# full-resolution bitmap is megabytes per object per frame.

def polygon_area(poly) -> float:
    """Shoelace. Returns absolute area, so winding direction does not matter —
    Ultralytics is not guaranteed to hand them back consistently wound."""
    pts = _points(poly)
    if len(pts) < 3:
        return 0.0
    total = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def polygon_bbox(poly) -> Rect:
    """The rectangle a polygon sits in — for fast rejection before the
    (much more expensive) point-in-polygon test, and as the box handed to
    ByteTrack."""
    pts = _points(poly)
    if not pts:
        raise ValueError("an empty polygon has no bounding box")
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return Rect(min(xs), min(ys), max(xs), max(ys))


def point_in_polygon(x: float, y: float, poly) -> bool:
    """Ray casting, with the boundary counted as inside.

    Boundary handling matters here in a way it does not for zones: a person
    clicking the edge of a thin object — a bag strap, a bicycle frame — is
    aiming at it, and half those clicks land on the outline itself.

    (rules.py and slots.py have their own copies for zone tests. Kept separate
    on purpose: those take [[x, y], ...] only and treat the boundary the other
    way, and this module stays free of their imports.)
    """
    pts = _points(poly)
    if len(pts) < 3:
        return False
    n = len(pts)
    inside = False
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        if _on_segment(x, y, x1, y1, x2, y2):
            return True
        if (y1 > y) != (y2 > y):
            xin = (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1
            if x < xin:
                inside = not inside
    return inside


def _on_segment(px, py, x1, y1, x2, y2, tol: float = 1e-9) -> bool:
    cross = (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)
    if abs(cross) > tol * max(1.0, abs(x2 - x1) + abs(y2 - y1)):
        return False
    return (min(x1, x2) - tol <= px <= max(x1, x2) + tol
            and min(y1, y2) - tol <= py <= max(y1, y2) + tol)


def validate_polygon(poly, video_w: float, video_h: float,
                     min_points: int = 3, min_area: float = 16.0) -> list:
    """Clip a polygon into the frame and check it is usable.

    Raises ValueError with wording a person can act on, because these messages
    end up in front of one who is drawing an outline by hand.

    Self-intersection is deliberately *not* rejected. A bow-tie polygon is
    still selectable and still renders; refusing it would block a legitimate
    correction over a mathematical nicety. `is_simple()` is there for a UI that
    wants to warn.
    """
    pts = _points(poly)
    if len(pts) < min_points:
        raise ValueError(f"an outline needs at least {min_points} points")
    clipped = [(min(max(x, 0.0), float(video_w)),
                min(max(y, 0.0), float(video_h))) for x, y in pts]
    # drop consecutive duplicates, which clipping can create along an edge
    out = [p for i, p in enumerate(clipped)
           if i == 0 or p != clipped[i - 1]]
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    if len(out) < min_points:
        raise ValueError("that outline collapsed to a line — redraw it")
    if polygon_area(out) < min_area:
        raise ValueError("that outline is too small — draw a larger one")
    return out


def is_simple(poly) -> bool:
    """False if any two non-adjacent edges cross. Advisory only — see
    validate_polygon."""
    pts = _points(poly)
    n = len(pts)
    if n < 4:
        return True
    for i in range(n):
        a1, a2 = pts[i], pts[(i + 1) % n]
        for j in range(i + 1, n):
            if j == i or (j + 1) % n == i or j == (i + 1) % n:
                continue
            if _segments_cross(a1, a2, pts[j], pts[(j + 1) % n]):
                return False
    return True


def _segments_cross(p1, p2, p3, p4) -> bool:
    def side(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return (v > 1e-12) - (v < -1e-12)
    d1, d2 = side(p3, p4, p1), side(p3, p4, p2)
    d3, d4 = side(p1, p2, p3), side(p1, p2, p4)
    return d1 * d2 < 0 and d3 * d4 < 0


def _points(poly) -> list:
    """Accept [[x, y], ...], [(x, y), ...], or an object/dict carrying one."""
    if poly is None:
        return []
    if isinstance(poly, dict):
        poly = poly.get("polygon") or poly.get("points") or []
    else:
        poly = getattr(poly, "polygon", poly)
    return [(float(p[0]), float(p[1])) for p in poly]


def _polygons(obj) -> list:
    """Every polygon belonging to one object. Segmentation can return more
    than one piece for a single thing — a car split by a pole in front of it —
    and dropping the smaller piece would make part of the car unclickable."""
    if obj is None:
        return []
    polys = None
    if isinstance(obj, dict):
        polys = obj.get("polygons")
        if polys is None and (obj.get("polygon") or obj.get("points")):
            polys = [obj.get("polygon") or obj.get("points")]
    else:
        polys = getattr(obj, "polygons", None)
        if polys is None and getattr(obj, "polygon", None) is not None:
            polys = [obj.polygon]
    return [_points(p) for p in (polys or []) if len(_points(p)) >= 3]


def has_mask(obj) -> bool:
    return bool(_polygons(obj))


def mask_area(obj) -> float:
    return sum(polygon_area(p) for p in _polygons(obj))


def point_in_mask(obj, x: float, y: float) -> bool:
    """Inside any of the object's polygons. Rejects on the bounding box first,
    which is a handful of comparisons against a few hundred."""
    for poly in _polygons(obj):
        if polygon_bbox(poly).contains(x, y) and point_in_polygon(x, y, poly):
            return True
    return False


def mask_hit_test(objects, x: float, y: float) -> list:
    """Objects whose outline contains the point, smallest first.

    Same rule as boxes — the smallest thing containing the click is what was
    aimed at — but on real shapes, so a click between two overlapping cars
    picks the one actually under the cursor rather than whichever rectangle
    happens to be smaller.
    """
    hits = [o for o in objects if point_in_mask(o, x, y)]
    return sorted(hits, key=mask_area)


def hit_test(boxes, x: float, y: float) -> list:
    """Every box containing the point, most specific first.

    Smallest area first, because when a person is standing in front of a bus
    the person's box is inside the bus's box, and the person is what was
    clicked. The caller shows the first one and offers the rest as
    alternatives — which is what the overlap menu is.
    """
    hits = [b for b in boxes if _rect(b).contains(x, y)]
    return sorted(hits, key=lambda b: _rect(b).area)


def nearby(boxes, x: float, y: float, radius: float = 60.0) -> list:
    """Boxes within `radius` of the point, closest edge first.

    For the case where someone clicks just beside a thing rather than on it —
    common with small or thin objects on a low-resolution camera.
    """
    scored = [(_rect(b).distance_to(x, y), b) for b in boxes]
    return [b for d, b in sorted(scored, key=lambda p: p[0]) if 0 < d <= radius]


def select(objects, x: float, y: float, radius: float = 60.0) -> dict:
    """The whole click-to-select decision, in one call.

    Priority, most specific first:
      1. an outline containing the point   -> mask_hit
      2. a box containing the point        -> ai_detection
      3. anything near the point           -> nearby_detection
      4. nothing                           -> manual_box

    Masks outrank boxes because a box is an approximation of the thing and the
    outline *is* the thing: two cars parked at an angle overlap as rectangles
    but not as shapes. Objects with no mask still work, so this stays correct
    for the plain detector.

    Returns what was chosen, what else it could have been, and *how* — the UI
    needs the last one to word itself honestly ("is this the one?" reads
    differently from "nothing there; here is what is close").
    """
    masked = [o for o in objects if has_mask(o)]
    if masked:
        hits = mask_hit_test(masked, x, y)
        if hits:
            return {"method": "mask_hit", "selected": hits[0],
                    "alternatives": hits[1:], "point": (x, y)}
    hits = hit_test(objects, x, y)
    if hits:
        return {"method": "ai_detection", "selected": hits[0],
                "alternatives": hits[1:], "point": (x, y)}
    close = nearby(objects, x, y, radius)
    if close:
        return {"method": "nearby_detection", "selected": close[0],
                "alternatives": close[1:], "point": (x, y)}
    return {"method": "manual_box", "selected": None, "alternatives": [],
            "point": (x, y)}


def validate_box(x1: float, y1: float, x2: float, y2: float,
                 video_w: float, video_h: float,
                 min_size: float = 4.0) -> Rect:
    """Normalise and check a hand-drawn box. Raises ValueError with a message
    a person can act on, because these end up in front of one."""
    x1, x2 = sorted((float(x1), float(x2)))
    y1, y2 = sorted((float(y1), float(y2)))
    x1, y1 = max(0.0, x1), max(0.0, y1)
    x2, y2 = min(float(video_w), x2), min(float(video_h), y2)
    if x2 - x1 < min_size or y2 - y1 < min_size:
        raise ValueError("that box is too small — drag a larger one")
    return Rect(x1, y1, x2, y2)


def _rect(b) -> Rect:
    """Accept a Rect, a detector Detection, a SegmentedObject, a 4-tuple or a
    dict.

    The detector calls its box `xyxy` and the segmenter calls it `bbox`. Both
    are read here rather than making one of them rename, because the mask path
    worked without this and only the box-fallback path crashed — a bug that
    hides until someone clicks a gap between two objects.
    """
    if isinstance(b, Rect):
        return b
    for attr in ("xyxy", "bbox"):
        v = getattr(b, attr, None)
        if v is not None:
            return Rect(*[float(n) for n in v])
    if isinstance(b, dict):
        if "xyxy" in b:
            return Rect(*[float(v) for v in b["xyxy"]])
        box = b.get("bbox")
        if isinstance(box, dict):
            return Rect(float(box["x_min"]), float(box["y_min"]),
                        float(box["x_max"]), float(box["y_max"]))
        if box is not None:
            return Rect(*[float(v) for v in box])
        return Rect(float(b["x1"]), float(b["y1"]),
                    float(b["x2"]), float(b["y2"]))
    return Rect(*[float(v) for v in b])
