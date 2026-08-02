"""Saved object annotations — the output of the tagging workbench.

Step D of Object Tagging, and the step the previous three exist for. Steps A
to C get an outline onto the screen under someone's cursor; this is where that
outline becomes a durable record with a person's judgement attached, and where
that record turns into a file a training pipeline can read.

Three decisions worth stating plainly, because they are the ones that would be
expensive to change later:

  **The model's polygon is never overwritten.** `original_polygon` is what the
  segmenter produced, stored verbatim; `corrected_polygon` is what the human
  ended up with, stored separately and NULL until they actually move a point.
  Keeping both costs a few hundred bytes per object and buys the single most
  useful number in the whole workbench — how far the model was from what a
  person accepted. Overwrite the original and that number is gone forever, and
  you are left grading the model against its own homework.

  **Everything is in original-video pixels**, like the rest of the tagging
  path, and the frame size is stored alongside so an export can normalise
  without going back to the video file.

  **A polygon is stored as a list of polygons.** One object can be split by a
  lamp post into two visible pieces, and a format that assumes a single ring
  cannot say so. Callers that want one outline take the largest.

Review status is a straight line with a way back: draft -> submitted ->
approved | rejected, and anything can be sent back to draft. The point is not
ceremony, it is that a mixed pile of half-finished and checked labels is worse
than no labels, because you cannot tell which is which.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from .segment import CLASSES
from .tagging import polygon_area, polygon_bbox, polygon_iou

# How the outline came to exist. Recorded per annotation because the answer
# changes what the record is worth: an outline the model drew and a person
# accepted is evidence about the model, an outline a person drew by hand is
# evidence about the footage, and averaging the two measures nothing.
SOURCES = (
    "yolo_segmentation",        # the segmenter's mask, selected by clicking it
    "yolo_detection_fallback",  # only a box was available; the mask is the box
    "manual_polygon",           # drawn point by point, no model involved
    "manual_box",               # dragged as a rectangle
    "sam_refinement",           # a promptable model tightened it (not wired yet)
    "tracked",                  # the same object, followed to this frame
    "interpolated",             # NOT SEEN here: reconstructed between two
                                # frames where it was. Kept distinct from
                                # `tracked` because a training set that cannot
                                # tell an observation from a reconstruction
                                # will happily learn from the reconstructions.
)

STATUSES = ("draft", "submitted", "approved", "rejected")

# Who may move to what. Approving is not in here as a special case — the route
# decides who is allowed to ask; this only says which moves are meaningful.
TRANSITIONS: dict[str, set[str]] = {
    "draft":     {"submitted", "draft"},
    "submitted": {"approved", "rejected", "draft"},
    "approved":  {"draft", "submitted"},
    "rejected":  {"draft", "submitted"},
}

MAX_POINTS = 400        # a mask outline with more than this is noise, not detail
MAX_TAGS = 12
MAX_TAG_LEN = 40
MAX_TEXT = 400


# --------------------------------------------------------------- polygons
def normalize_polygons(raw, frame_w: float = 0, frame_h: float = 0) -> list:
    """Whatever the caller sent -> a clean list of polygons, or [].

    Accepts a JSON string, a single polygon `[[x, y], ...]`, or a list of them.
    Points are coerced to floats, clamped to the frame when its size is known,
    and any ring left with fewer than three points or no area is dropped —
    a degenerate polygon renders as nothing and selects nothing, so storing it
    would only produce an object that cannot be clicked and cannot be seen.
    """
    if raw is None or raw == "" or raw == []:
        return []
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError) as e:
            raise ValueError(f"polygon is not valid JSON: {e}")
    if not isinstance(raw, (list, tuple)):
        raise ValueError("polygon must be a list of points")
    if not raw:
        return []          # "[]" is how the page says "clear the correction"

    first = raw[0]
    single = (isinstance(first, (list, tuple)) and len(first) == 2
              and all(isinstance(v, (int, float)) for v in first))
    rings = [raw] if single else raw

    out = []
    for ring in rings:
        if not isinstance(ring, (list, tuple)):
            raise ValueError("polygon must be a list of [x, y] points")
        pts = []
        for p in ring:
            if not isinstance(p, (list, tuple)) or len(p) != 2:
                raise ValueError("each point must be [x, y]")
            try:
                x, y = float(p[0]), float(p[1])
            except (TypeError, ValueError):
                raise ValueError("point coordinates must be numbers")
            if x != x or y != y:                       # NaN
                raise ValueError("point coordinates must be numbers")
            if frame_w > 0:
                x = min(max(x, 0.0), float(frame_w))
            if frame_h > 0:
                y = min(max(y, 0.0), float(frame_h))
            pts.append((x, y))
        if len(pts) > MAX_POINTS:
            raise ValueError(f"a polygon may have at most {MAX_POINTS} points")
        if len(pts) >= 3 and polygon_area(pts) > 0:
            out.append(pts)
    return out


def polygons_json(polys) -> str:
    """Store rounded to whole pixels. Sub-pixel precision on a hand-dragged
    outline is a fiction, and it doubles the size of every row."""
    return json.dumps([[[round(float(x), 1), round(float(y), 1)]
                        for x, y in poly] for poly in polys])


def polygons_from_json(s) -> list:
    if not s:
        return []
    try:
        return [[(float(x), float(y)) for x, y in poly] for poly in json.loads(s)]
    except (ValueError, TypeError):
        return []


def bbox_of(polys, fallback=None) -> tuple:
    """Box round every piece. Falls back to what the caller had when there is
    no outline — a detection-only annotation still needs a box."""
    pts = [p for poly in polys for p in poly]
    if not pts:
        if fallback is None:
            raise ValueError("an annotation needs either a polygon or a box")
        return tuple(float(v) for v in fallback)
    r = polygon_bbox(pts)
    return (r.x1, r.y1, r.x2, r.y2)


def drift(original, corrected) -> float | None:
    """How far a person moved the model's outline: 0 = accepted it as drawn,
    1 = replaced it entirely. None when they never touched it, which is not
    the same as zero and must not be averaged in as if it were."""
    if not corrected:
        return None
    if not original:
        return None
    return round(1.0 - polygon_iou(original, corrected), 4)


# ------------------------------------------------------------- the record
@dataclass
class Annotation:
    """One tagged object on one frame."""
    clip_id: int
    frame_index: int
    timestamp_ms: int
    category: str
    source: str
    bbox: tuple
    frame_width: int
    frame_height: int
    original_polygon: list = field(default_factory=list)
    corrected_polygon: list = field(default_factory=list)
    custom_label: str = ""
    tags: list = field(default_factory=list)
    notes: str = ""
    detection_confidence: float | None = None
    user_confidence: float | None = None
    model: str = ""
    track_id: int | None = None
    temporary_object_id: str = ""
    review_status: str = "draft"
    track_ref: int | None = None      # which tracking run produced this
    mask_source: str = ""             # anchor | tracked | interpolated
    needs_review: bool = False        # the tracker was not confident here
    review_note: str = ""             # and this is what bothered it
    id: int = 0
    created_by: str = ""
    created_at: float = 0.0
    updated_by: str = ""
    updated_at: float = 0.0
    reviewed_by: str = ""
    reviewed_at: float | None = None

    @property
    def polygon(self) -> list:
        """What to draw and what to export: the human's version when there is
        one, the model's otherwise."""
        return self.corrected_polygon or self.original_polygon

    @property
    def corrected(self) -> bool:
        return bool(self.corrected_polygon)

    def public(self) -> dict:
        return {
            "id": self.id,
            "clip_id": self.clip_id,
            "frame_index": self.frame_index,
            "timestamp_ms": self.timestamp_ms,
            "category": self.category,
            "custom_label": self.custom_label,
            "label": self.custom_label or self.category,
            "source": self.source,
            "detection_confidence": self.detection_confidence,
            "user_confidence": self.user_confidence,
            "bbox": {"x_min": round(self.bbox[0], 1),
                     "y_min": round(self.bbox[1], 1),
                     "x_max": round(self.bbox[2], 1),
                     "y_max": round(self.bbox[3], 1)},
            "frame_width": self.frame_width,
            "frame_height": self.frame_height,
            "original_polygon": [[[round(x), round(y)] for x, y in p]
                                 for p in self.original_polygon],
            "corrected_polygon": [[[round(x), round(y)] for x, y in p]
                                  for p in self.corrected_polygon],
            "polygon": [[[round(x), round(y)] for x, y in p]
                        for p in self.polygon],
            "corrected": self.corrected,
            "drift": drift(self.original_polygon, self.corrected_polygon),
            "tags": list(self.tags),
            "notes": self.notes,
            "model": self.model,
            "track_id": self.track_id,
            "temporary_object_id": self.temporary_object_id,
            "review_status": self.review_status,
            "track_ref": self.track_ref,
            "mask_source": self.mask_source,
            "needs_review": self.needs_review,
            "review_note": self.review_note,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_by": self.updated_by,
            "updated_at": self.updated_at,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at,
        }

    def row(self) -> dict:
        """Column values, ready for the database."""
        x1, y1, x2, y2 = self.bbox
        return {
            "clip_id": self.clip_id, "frame_index": self.frame_index,
            "timestamp_ms": self.timestamp_ms, "category": self.category,
            "custom_label": self.custom_label, "source": self.source,
            "detection_confidence": self.detection_confidence,
            "user_confidence": self.user_confidence,
            "x1": x1, "y1": y1, "x2": x2, "y2": y2,
            "frame_width": self.frame_width, "frame_height": self.frame_height,
            "original_polygon": polygons_json(self.original_polygon),
            "corrected_polygon": (polygons_json(self.corrected_polygon)
                                  if self.corrected_polygon else None),
            "tags": json.dumps(self.tags), "notes": self.notes,
            "model": self.model, "track_id": self.track_id,
            "temporary_object_id": self.temporary_object_id,
            "review_status": self.review_status,
            "track_ref": self.track_ref, "mask_source": self.mask_source,
            "needs_review": 1 if self.needs_review else 0,
            "review_note": self.review_note,
            "created_by": self.created_by, "created_at": self.created_at,
            "updated_by": self.updated_by, "updated_at": self.updated_at,
            "reviewed_by": self.reviewed_by, "reviewed_at": self.reviewed_at,
        }

    @classmethod
    def from_row(cls, r) -> "Annotation":
        r = dict(r)
        return cls(
            id=r["id"], clip_id=r["clip_id"], frame_index=r["frame_index"],
            timestamp_ms=r["timestamp_ms"], category=r["category"],
            custom_label=r["custom_label"] or "", source=r["source"],
            detection_confidence=r["detection_confidence"],
            user_confidence=r["user_confidence"],
            bbox=(r["x1"], r["y1"], r["x2"], r["y2"]),
            frame_width=r["frame_width"], frame_height=r["frame_height"],
            original_polygon=polygons_from_json(r["original_polygon"]),
            corrected_polygon=polygons_from_json(r["corrected_polygon"]),
            tags=_tags_from_json(r["tags"]), notes=r["notes"] or "",
            model=r["model"] or "", track_id=r["track_id"],
            temporary_object_id=r["temporary_object_id"] or "",
            review_status=r["review_status"],
            track_ref=r.get("track_ref"),
            mask_source=r.get("mask_source") or "",
            needs_review=bool(r.get("needs_review")),
            review_note=r.get("review_note") or "",
            created_by=r["created_by"] or "", created_at=r["created_at"],
            updated_by=r["updated_by"] or "", updated_at=r["updated_at"],
            reviewed_by=r["reviewed_by"] or "", reviewed_at=r["reviewed_at"],
        )


def _tags_from_json(s) -> list:
    if not s:
        return []
    try:
        v = json.loads(s)
    except (ValueError, TypeError):
        return []
    return [str(t) for t in v] if isinstance(v, list) else []


# ------------------------------------------------------------ validation
def clean_tags(raw) -> list:
    """'forced, at night ,, forced' -> ['forced', 'at night'].

    Order is kept and duplicates are dropped, because tags are read by people
    as much as by code and a list that reorders itself on every save is a list
    nobody trusts.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = [t for chunk in raw.split(",") for t in [chunk.strip()] if t]
    else:
        parts = [str(t).strip() for t in raw if str(t).strip()]
    out = list(dict.fromkeys(parts))
    if len(out) > MAX_TAGS:
        raise ValueError(f"at most {MAX_TAGS} tags")
    for t in out:
        if len(t) > MAX_TAG_LEN:
            raise ValueError(f"tag '{t[:20]}...' is too long")
    return out


def clean_text(s, what: str = "text") -> str:
    s = (s or "").strip()
    if len(s) > MAX_TEXT:
        raise ValueError(f"{what} is too long (max {MAX_TEXT} characters)")
    return s


def clean_confidence(v) -> float | None:
    """None means 'not stated'. Zero means 'I am not sure at all', which is a
    real answer and must survive the round trip."""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError("confidence must be a number between 0 and 1")
    if not 0.0 <= f <= 1.0:
        raise ValueError("confidence must be between 0 and 1")
    return round(f, 3)


def validate(category: str, source: str, custom_label: str = "") -> None:
    if category not in CLASSES:
        raise ValueError(f"category must be one of {', '.join(CLASSES)}")
    if source not in SOURCES:
        raise ValueError(f"source must be one of {', '.join(SOURCES)}")
    if category == "custom" and not custom_label.strip():
        raise ValueError("a custom category needs a label saying what it is")


def can_transition(current: str, target: str) -> bool:
    if target not in STATUSES:
        return False
    return target in TRANSITIONS.get(current, set())


def build(payload: dict, actor: str = "", now: float | None = None) -> Annotation:
    """Validate a submitted annotation and return the record to store.

    Raises ValueError with a sentence a person can act on — these messages go
    straight to the workbench, and "invalid input" helps nobody.
    """
    now = time.time() if now is None else now
    fw = int(payload.get("frame_width") or 0)
    fh = int(payload.get("frame_height") or 0)
    if fw <= 0 or fh <= 0:
        raise ValueError("the frame size must be known to store an annotation")

    category = str(payload.get("category") or "").strip()
    source = str(payload.get("source") or "").strip()
    custom_label = clean_text(payload.get("custom_label"), "label")
    validate(category, source, custom_label)

    original = normalize_polygons(payload.get("original_polygon"), fw, fh)
    corrected = normalize_polygons(payload.get("corrected_polygon"), fw, fh)

    box = payload.get("bbox")
    if box is None and all(k in payload for k in ("x1", "y1", "x2", "y2")):
        box = (payload["x1"], payload["y1"], payload["x2"], payload["y2"])
    if box is not None:
        box = tuple(float(v) for v in box)
        if box[2] <= box[0] or box[3] <= box[1]:
            box = None                       # a zero-area box is not a box
    if not original and not corrected and box is None:
        raise ValueError("an annotation needs either a polygon or a box")

    # The box always describes what will actually be drawn, so a corrected
    # outline moves the box with it rather than leaving a stale rectangle.
    bbox = bbox_of(corrected or original, box)

    frame_index = int(payload.get("frame_index") or 0)
    if frame_index < 0:
        raise ValueError("frame index cannot be negative")
    ts = float(payload.get("timestamp_ms") or 0)
    if ts < 0:
        raise ValueError("timestamp cannot be negative")

    status = str(payload.get("review_status") or "draft")
    if status not in STATUSES:
        raise ValueError(f"review status must be one of {', '.join(STATUSES)}")

    track_id = payload.get("track_id")
    track_ref = payload.get("track_ref")
    return Annotation(
        track_ref=int(track_ref) if track_ref not in (None, "", "null") else None,
        mask_source=str(payload.get("mask_source") or ""),
        needs_review=bool(payload.get("needs_review")),
        review_note=clean_text(payload.get("review_note"), "note"),
        clip_id=int(payload["clip_id"]), frame_index=frame_index,
        timestamp_ms=int(round(ts)), category=category, source=source,
        bbox=bbox, frame_width=fw, frame_height=fh,
        original_polygon=original, corrected_polygon=corrected,
        custom_label=custom_label, tags=clean_tags(payload.get("tags")),
        notes=clean_text(payload.get("notes"), "note"),
        detection_confidence=clean_confidence(payload.get("detection_confidence")),
        user_confidence=clean_confidence(payload.get("user_confidence")),
        model=clean_text(payload.get("model"), "model name"),
        track_id=int(track_id) if track_id not in (None, "", "null") else None,
        temporary_object_id=clean_text(payload.get("temporary_object_id"), "id"),
        review_status=status,
        created_by=actor, created_at=now, updated_by=actor, updated_at=now)


def apply_edit(ann: Annotation, payload: dict, actor: str = "",
               now: float | None = None) -> Annotation:
    """Edit in place, except for the one field that is never edited.

    `original_polygon` is not reachable from here on purpose. An edit that
    moves points writes `corrected_polygon`; the model's own answer stays
    exactly as it arrived, for as long as the row exists.
    """
    now = time.time() if now is None else now
    if "category" in payload or "custom_label" in payload:
        category = str(payload.get("category", ann.category) or "").strip()
        label = clean_text(payload.get("custom_label", ann.custom_label), "label")
        validate(category, ann.source, label)
        ann.category, ann.custom_label = category, label
    if "corrected_polygon" in payload:
        polys = normalize_polygons(payload["corrected_polygon"],
                                   ann.frame_width, ann.frame_height)
        if payload["corrected_polygon"] in (None, "", "[]", []) and not polys:
            ann.corrected_polygon = []       # "put it back how the model had it"
        else:
            if not polys:
                raise ValueError("that shape has no area — nothing to save")
            ann.corrected_polygon = polys
        ann.bbox = bbox_of(ann.polygon, ann.bbox)
    if "tags" in payload:
        ann.tags = clean_tags(payload["tags"])
    if "notes" in payload:
        ann.notes = clean_text(payload["notes"], "note")
    if "user_confidence" in payload:
        ann.user_confidence = clean_confidence(payload["user_confidence"])
    ann.updated_by, ann.updated_at = actor, now
    return ann


# ---------------------------------------------------------------- export
def summarise(rows) -> dict:
    """What the workbench has actually produced, in numbers.

    `mean_drift` deliberately counts only the annotations somebody corrected.
    Folding in the untouched ones as zeros would say the model is doing well
    when what happened is that nobody looked.
    """
    anns = [Annotation.from_row(r) if not isinstance(r, Annotation) else r
            for r in rows]
    by_status: dict[str, int] = {s: 0 for s in STATUSES}
    by_category: dict[str, int] = {}
    by_source: dict[str, int] = {}
    drifts = []
    for a in anns:
        by_status[a.review_status] = by_status.get(a.review_status, 0) + 1
        by_category[a.category] = by_category.get(a.category, 0) + 1
        by_source[a.source] = by_source.get(a.source, 0) + 1
        d = drift(a.original_polygon, a.corrected_polygon)
        if d is not None:
            drifts.append(d)
    return {
        "total": len(anns),
        # An "annotation" produced by interpolation is not a sighting. Counted
        # separately so a total of 800 never gets read as 800 observations.
        "observed": sum(1 for a in anns if a.source != "interpolated"),
        "reconstructed": sum(1 for a in anns if a.source == "interpolated"),
        "needs_review": sum(1 for a in anns if a.needs_review),
        "tracks": len({a.track_ref for a in anns if a.track_ref}),
        "by_status": by_status,
        "by_category": dict(sorted(by_category.items(),
                                   key=lambda kv: -kv[1])),
        "by_source": by_source,
        "corrected": len(drifts),
        "mean_drift": round(sum(drifts) / len(drifts), 4) if drifts else None,
        "frames": len({(a.clip_id, a.frame_index) for a in anns}),
        "clips": len({a.clip_id for a in anns}),
    }


def to_coco(rows, clips_by_id: dict, exclude: tuple = ("rejected",)) -> dict:
    """COCO instance segmentation, which is what training pipelines read.

    Written out rather than invented so these labels are usable by anything —
    the point of the workbench is not to feed one script of ours.

    One "image" per (clip, frame). The file name carries the frame number
    because the frames themselves are not extracted to disk; whoever consumes
    this pulls them from the clip, and the number is how.
    """
    anns = [Annotation.from_row(r) if not isinstance(r, Annotation) else r
            for r in rows]
    anns = [a for a in anns if a.review_status not in exclude]
    anns.sort(key=lambda a: (a.clip_id, a.frame_index, a.id))

    categories = [{"id": i + 1, "name": c} for i, c in enumerate(CLASSES)]
    cat_id = {c["name"]: c["id"] for c in categories}

    images, image_id = [], {}
    out = []
    for a in anns:
        key = (a.clip_id, a.frame_index)
        if key not in image_id:
            image_id[key] = len(images) + 1
            clip = clips_by_id.get(a.clip_id) or {}
            images.append({
                "id": image_id[key],
                "file_name": f"{clip.get('filename', a.clip_id)}#{a.frame_index}",
                "width": a.frame_width, "height": a.frame_height,
                "clip_id": a.clip_id, "frame_index": a.frame_index,
                "timestamp_ms": a.timestamp_ms,
                "source": clip.get("source") or "",
            })
        polys = a.polygon
        x1, y1, x2, y2 = a.bbox
        out.append({
            "id": a.id, "image_id": image_id[key],
            "category_id": cat_id.get(a.category, cat_id["unknown"]),
            "segmentation": [[round(v, 1) for xy in poly for v in xy]
                             for poly in polys],
            "bbox": [round(x1, 1), round(y1, 1),
                     round(x2 - x1, 1), round(y2 - y1, 1)],
            "area": round(sum(polygon_area(p) for p in polys)
                          or max(0.0, (x2 - x1) * (y2 - y1)), 1),
            "iscrowd": 0,
            # everything below is ours, not COCO's — readers ignore what they
            # do not know, and dropping it would throw away the judgement that
            # is the entire reason these rows exist
            "visionguard": {
                "custom_label": a.custom_label, "tags": a.tags,
                "notes": a.notes, "selection_source": a.source,
                "detection_confidence": a.detection_confidence,
                "user_confidence": a.user_confidence,
                "review_status": a.review_status, "model": a.model,
                "corrected_by_human": a.corrected,
                "drift": drift(a.original_polygon, a.corrected_polygon),
                "created_by": a.created_by,
                # the honest bit: `observed` false means no model and no
                # person ever looked at this frame — the shape was
                # reconstructed between two frames where one of them did
                "observed": a.source != "interpolated",
                "track_id": a.track_ref,
                "mask_source": a.mask_source,
                "needs_review": a.needs_review,
            },
        })
    return {
        "info": {"description": "VisionGuard tagging workbench",
                 "exported_at": time.time(),
                 "note": "images are frames inside the named clips, not files"},
        "licenses": [],
        "images": images, "annotations": out, "categories": categories,
    }
