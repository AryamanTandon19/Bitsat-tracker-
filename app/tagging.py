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


def select(boxes, x: float, y: float, radius: float = 60.0) -> dict:
    """The whole click-to-select decision, in one call.

    Returns what was chosen, what else it could have been, and *how* it was
    chosen — the UI needs the last one to word itself honestly ("is this the
    one?" reads differently from "nothing there; here is what is close").
    """
    hits = hit_test(boxes, x, y)
    if hits:
        return {"method": "ai_detection", "selected": hits[0],
                "alternatives": hits[1:], "point": (x, y)}
    close = nearby(boxes, x, y, radius)
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
    """Accept a Rect, a detector Detection, a 4-tuple or a dict."""
    if isinstance(b, Rect):
        return b
    xyxy = getattr(b, "xyxy", None)
    if xyxy is not None:
        return Rect(*[float(v) for v in xyxy])
    if isinstance(b, dict):
        if "xyxy" in b:
            return Rect(*[float(v) for v in b["xyxy"]])
        return Rect(float(b["x1"]), float(b["y1"]),
                    float(b["x2"]), float(b["y2"]))
    return Rect(*[float(v) for v in b])
