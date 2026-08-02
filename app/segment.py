"""Single-frame instance segmentation for the tagging workbench.

Step B of Object Tagging: everything around the model, with the model itself
still a stand-in. That ordering is deliberate. Frame decoding, the response
shape, the coordinate space, caching and the whole overlay can be built and
tested without a 6MB download, and when the real YOLO11-seg lands in step C it
only has to satisfy an interface that already has a passing test suite behind
it.

Two rules the rest of the system depends on:

  Everything here speaks original-video pixels. Not display pixels, not
  normalised 0..1, not the letterboxed player's idea of a coordinate. One
  space, converted at the edge in tagging.py.

  Masks travel as polygons. A full-resolution bitmap is megabytes per object
  per frame; the outline of the same car is a few dozen numbers, renders on a
  canvas directly, and is what gets stored.
"""
from __future__ import annotations

import hashlib
import math
import threading
from dataclasses import dataclass, field

from .tagging import polygon_area, polygon_bbox

# Classes the workbench offers. A superset of what the live detector tracks,
# because a person tagging footage cares about bags and packages that the
# alerting layer has no rule for yet.
CLASSES = ("person", "car", "motorcycle", "bicycle", "bus", "truck",
           "bag", "package", "animal", "door", "gate", "window",
           "unknown", "custom")


@dataclass
class SegmentedObject:
    """One object on one frame. Box and mask both, always.

    The box stays because ByteTrack associates on boxes, spatial indexing is
    cheaper on boxes, and a mask that fails to render still has something to
    draw. The mask is what a person selects with.
    """
    temporary_object_id: str
    class_name: str
    confidence: float
    bbox: tuple                       # (x1, y1, x2, y2) original pixels
    polygons: list = field(default_factory=list)
    class_id: int = -1
    track_id: int | None = None

    @property
    def polygon(self) -> list:
        """The largest piece. Convenience for callers that want one outline;
        selection always uses every piece."""
        return max(self.polygons, key=polygon_area) if self.polygons else []

    def public(self) -> dict:
        x1, y1, x2, y2 = self.bbox
        return {
            "temporary_object_id": self.temporary_object_id,
            "class_id": self.class_id,
            "class_name": self.class_name,
            "confidence": round(float(self.confidence), 4),
            "bbox": {"x_min": round(x1, 1), "y_min": round(y1, 1),
                     "x_max": round(x2, 1), "y_max": round(y2, 1)},
            # rounded to whole pixels: sub-pixel precision on a mask outline is
            # noise, and it halves the size of the response
            "polygons": [[[round(x), round(y)] for x, y in poly]
                         for poly in self.polygons],
            "polygon": [[round(x), round(y)] for x, y in self.polygon],
            "track_id": self.track_id,
        }


@dataclass
class FrameSegmentation:
    clip_id: int
    frame_index: int
    timestamp_ms: int
    frame_width: int
    frame_height: int
    model: str
    objects: list = field(default_factory=list)

    def public(self) -> dict:
        return {"clip_id": self.clip_id, "frame_index": self.frame_index,
                "timestamp_ms": self.timestamp_ms,
                "frame_width": self.frame_width,
                "frame_height": self.frame_height, "model": self.model,
                "objects": [o.public() for o in self.objects]}


class Segmenter:
    """What step C has to implement. Kept tiny on purpose."""

    name = "abstract"

    def segment(self, frame, frame_index: int) -> list:
        raise NotImplementedError


class MockSegmenter(Segmenter):
    """Deterministic stand-in outlines — no model, no download, no GPU.

    It is here so the overlay, the click path and the storage format can be
    finished and tested first. It reads nothing from the image, so it must
    never be mistaken for a detection: the model name it reports says `mock`
    and the API echoes that back, so a caller can always tell whether it is
    looking at a real result.

    Shapes are derived from the frame index, so the same frame always gives
    the same answer and a test can assert on it.
    """

    name = "mock"

    def __init__(self, count: int = 3):
        self.count = count

    def segment(self, frame, frame_index: int) -> list:
        h, w = (frame.shape[0], frame.shape[1]) if frame is not None else (1080, 1920)
        seed = int(hashlib.sha256(str(frame_index).encode()).hexdigest()[:8], 16)
        out = []
        for i in range(self.count):
            r = (seed >> (i * 5)) % 1000 / 1000.0
            cx = w * (0.2 + 0.3 * i + 0.08 * r)
            cy = h * (0.45 + 0.15 * r)
            rx, ry = w * 0.09, h * 0.13
            # an octagon: enough sides to read as a shape rather than a box,
            # few enough to stay legible when a person drags its points
            poly = []
            for k in range(8):
                a = math.tau * k / 8
                wobble = 1.0 + 0.18 * math.sin(a * 3 + r * 6)
                poly.append((min(max(cx + rx * wobble * math.cos(a), 0), w),
                             min(max(cy + ry * wobble * math.sin(a), 0), h)))
            box = polygon_bbox(poly)
            out.append(SegmentedObject(
                temporary_object_id=f"frame{frame_index}-object{i}",
                class_name=("car", "person", "bag")[i % 3],
                confidence=round(0.72 + 0.09 * r, 3),
                bbox=(box.x1, box.y1, box.x2, box.y2),
                polygons=[poly], class_id=i))
        return out


class YoloSegmenter(Segmenter):
    """Real instance segmentation, one frame at a time.

    Deliberately separate from the live detector. `app/detector.py` keeps
    running yolo11n on the cameras; this loads yolo11n-seg only when someone
    opens the tagging workbench and asks for outlines. Two models in one
    process is a few extra MB of RAM, and the alternative — swapping the
    production detector for a segmentation one — would change what every
    camera reports on the strength of a labelling feature nobody has used yet.

    Loaded once, on first use, and kept. Loading per request would make every
    click pay a second of import and weight-loading.

    The confidence floor is lower here than on the live cameras, deliberately.
    Alerting wants precision — a false alarm costs someone's attention. A
    labelling tool wants recall: a wrong outline is dismissed with one click,
    but an object that was never offered cannot be tagged at all. People are
    the case that forces it — on a car-park camera a person is 16-27px wide
    where a car is 200, and a threshold tuned for cars quietly loses half of
    them.
    """

    def __init__(self, weights: str = "yolo11n-seg.pt", conf: float = 0.25,
                 imgsz: int = 640, device: str = "auto",
                 classes: list | None = None, max_objects: int = 40):
        self.weights = weights
        self.conf = float(conf)
        self.imgsz = int(imgsz)
        self.device = device
        self.classes = classes
        self.max_objects = int(max_objects)
        self._model = None
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self.weights

    def _pick_device(self) -> str:
        if self.device and self.device != "auto":
            return self.device
        try:
            import torch
            if torch.cuda.is_available():
                return "cuda:0"
        except Exception:
            pass
        return "cpu"

    def load(self):
        """Idempotent, thread-safe, and loud when it fails.

        A model that cannot load must say why: the workbench sits behind this
        and 'no objects found' would be a lie.
        """
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from ultralytics import YOLO
            except ImportError as e:
                raise RuntimeError(
                    "ultralytics is not installed, so outlines cannot be "
                    f"produced ({e})") from e
            try:
                self._model = YOLO(self.weights)
            except Exception as e:
                raise RuntimeError(
                    f"could not load {self.weights}: {e}. It downloads on "
                    "first use — check the machine has network access, or "
                    "place the file beside config.yaml") from e
            return self._model

    def segment(self, frame, frame_index: int) -> list:
        model = self.load()
        kw = {"verbose": False, "conf": self.conf, "imgsz": self.imgsz,
              "device": self._pick_device()}
        if self.classes:
            kw["classes"] = self.classes
        result = model.predict(frame, **kw)[0]
        try:
            return self._to_objects(result, frame_index)
        finally:
            # the result holds GPU/CPU tensors for the whole frame; on a small
            # machine, holding one per cached entry adds up fast
            del result

    def _to_objects(self, result, frame_index: int) -> list:
        masks = getattr(result, "masks", None)
        boxes = getattr(result, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return []
        names = getattr(result, "names", {}) or {}
        # .xy is already in original-frame pixels — the one thing we must not
        # re-scale ourselves, because Ultralytics has already undone its own
        # letterboxing and doing it twice is silently wrong
        polys = list(masks.xy) if masks is not None else []

        out = []
        for i in range(min(len(boxes), self.max_objects)):
            b = boxes[i]
            x1, y1, x2, y2 = _floats(b.xyxy[0])
            cls_id = int(b.cls[0]) if b.cls is not None else -1
            poly = []
            if i < len(polys):
                poly = [(float(px), float(py)) for px, py in polys[i]]
                if polygon_area(poly) <= 0:
                    poly = []          # degenerate contour: fall back to box
            out.append(SegmentedObject(
                temporary_object_id=f"frame{frame_index}-object{i}",
                class_name=str(names.get(cls_id, cls_id)),
                confidence=float(b.conf[0]) if b.conf is not None else 0.0,
                bbox=(x1, y1, x2, y2),
                polygons=[poly] if len(poly) >= 3 else [],
                class_id=cls_id,
                track_id=int(b.id[0]) if getattr(b, "id", None) is not None
                else None))
        return out


class SegmentationCache:
    """(clip, frame) -> result, with a hard cap.

    Segmentation of one frame is cheap to redo and expensive to hoard, so this
    is bounded and disposable. Nothing here is an annotation: approved user
    work lives in the database and is never touched by cache eviction.
    """

    def __init__(self, max_entries: int = 64):
        self.max_entries = max_entries
        self._data: dict = {}
        self._order: list = []
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key):
        with self._lock:
            hit = self._data.get(key)
            if hit is None:
                self.misses += 1
            else:
                self.hits += 1
            return hit

    def put(self, key, value):
        with self._lock:
            if key not in self._data and len(self._order) >= self.max_entries:
                oldest = self._order.pop(0)
                self._data.pop(oldest, None)
            if key not in self._data:
                self._order.append(key)
            self._data[key] = value

    def clear(self):
        with self._lock:
            self._data.clear()
            self._order.clear()

    def __len__(self):
        return len(self._data)


def _floats(v) -> list:
    """Ultralytics hands back tensors; read them without importing torch.
    Anything sequence-like works, which also lets the tests fake a result
    without a 700MB dependency."""
    if hasattr(v, "tolist"):
        v = v.tolist()
    return [float(n) for n in v]


def frame_index_for(timestamp_ms: float, fps: float) -> int:
    """One timestamp always means one frame.

    Rounding rather than truncating, and done in exactly one place, so the
    index a client is given back can be sent again and land on the same
    picture. A frame index that drifts by one between calls would make a
    cached mask sit a frame off the object it outlines.
    """
    if fps <= 0:
        raise ValueError("this clip reports no frame rate; it may be corrupt")
    return max(0, int(round(timestamp_ms / 1000.0 * fps)))


def read_frame(path: str, timestamp_ms: float):
    """Decode one frame. Returns (frame, frame_index, fps, width, height).

    The frame is handed back and not written anywhere: storing a JPEG per
    inspected frame would fill a small disk with things nobody asked to keep.
    """
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError("this clip could not be opened")
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        idx = frame_index_for(timestamp_ms, fps)
        if frames and idx >= frames:
            raise ValueError("that moment is past the end of the clip")
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok or frame is None:
            raise ValueError("that frame could not be decoded")
        return frame, idx, fps, w, h
    finally:
        cap.release()


def iter_frames(path: str, start_index: int = 0, stride: int = 1,
                limit: int = 0):
    """Walk a clip forward from a frame, yielding (index, timestamp_ms, frame).

    Sequential, and that is the whole point. `cap.set(POS_FRAMES)` makes the
    decoder seek to the nearest keyframe and roll forward again for *every*
    frame; on a clip with keyframes two seconds apart, reading two hundred
    frames that way decodes several thousand. Reading straight through decodes
    two hundred. On the same MEVA clip that is the difference between a
    tracking run finishing and someone giving up on it.

    `stride` skips frames without decoding cost being wasted on them — we
    still have to decode each one, but only every Nth goes to the model, which
    is where the time actually goes.
    """
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError("this clip could not be opened")
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        if fps <= 0:
            raise ValueError("this clip reports no frame rate; it may be corrupt")
        if start_index > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(start_index))
        idx = int(start_index)
        given = 0
        stride = max(1, int(stride))
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                return
            if (idx - start_index) % stride == 0:
                yield idx, int(round(idx / fps * 1000)), frame
                given += 1
                if limit and given >= limit:
                    return
            idx += 1
    finally:
        cap.release()


def clip_shape(path: str) -> tuple:
    """(fps, frame_count, width, height) without decoding anything."""
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError("this clip could not be opened")
    try:
        return (cap.get(cv2.CAP_PROP_FPS) or 0.0,
                int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0),
                int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    finally:
        cap.release()


def build_segmenter(cfg: dict | None = None) -> Segmenter:
    """Pick the segmenter from config, lazily and once.

    `mock` until step C lands the real one. A bad name fails loudly here rather
    than silently returning outlines nobody should trust.
    """
    cfg = cfg or {}
    kind = str(cfg.get("segmenter", "mock")).lower()
    if kind == "mock":
        return MockSegmenter(int(cfg.get("mock_objects", 3)))
    if kind in ("yolo", "yolo-seg", "yolo11-seg"):
        return YoloSegmenter(
            weights=cfg.get("seg_model", "yolo11n-seg.pt"),
            conf=float(cfg.get("seg_confidence", 0.25)),
            imgsz=int(cfg.get("seg_imgsz", 640)),
            device=cfg.get("seg_device", "auto"),
            classes=cfg.get("seg_classes"),
            max_objects=int(cfg.get("max_objects", 40)))
    raise ValueError(
        f"unknown segmenter {kind!r} — use 'yolo' or 'mock'")
