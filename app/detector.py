"""YOLO detection + ByteTrack tracking wrapper (Ultralytics)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# bundled tracker configs shipped with the app
_TRACKER_DIR = Path(__file__).resolve().parent / "trackers"

VEHICLE_CLASSES = {"car", "motorcycle", "bus", "truck", "bicycle"}
PERSON_CLASS = "person"


@dataclass
class Detection:
    track_id: int
    cls_name: str
    conf: float
    xyxy: tuple[float, float, float, float]

    @property
    def center(self) -> tuple[float, float]:
        x1, y1, x2, y2 = self.xyxy
        return ((x1 + x2) / 2, (y1 + y2) / 2)

    @property
    def foot_point(self) -> tuple[float, float]:
        """Bottom-center — the point used for zone tests (where the object
        touches the ground)."""
        x1, y1, x2, y2 = self.xyxy
        return ((x1 + x2) / 2, y2)

    @property
    def is_vehicle(self) -> bool:
        return self.cls_name in VEHICLE_CLASSES

    @property
    def is_person(self) -> bool:
        return self.cls_name == PERSON_CLASS


class Detector:
    """One instance per camera (tracker state is per-stream)."""

    def __init__(self, cfg: dict):
        from ultralytics import YOLO
        self.model = YOLO(cfg.get("model", "yolo11n.pt"))
        self.imgsz = int(cfg.get("imgsz", 640))
        self.conf = float(cfg.get("confidence", 0.35))
        self.device = self._pick_device(cfg.get("device", "auto"))
        names = self.model.names  # {id: name}
        wanted = set(cfg.get("classes") or
                     list(VEHICLE_CLASSES | {PERSON_CLASS}))
        self.class_ids = [i for i, n in names.items() if n in wanted]
        self.names = names
        self.tracker = self._resolve_tracker(cfg.get("tracker", "bytetrack.yaml"))
        log.info("detector ready (device=%s, tracker=%s, classes=%s)",
                 self.device, self.tracker, sorted(wanted))

    @staticmethod
    def _resolve_tracker(name: str) -> str:
        """Accept a built-in ultralytics name (bytetrack.yaml / botsort.yaml),
        one of our bundled configs (e.g. 'botsort_reid'), or an explicit path."""
        if not name.endswith(".yaml"):
            name += ".yaml"
        bundled = _TRACKER_DIR / name
        if bundled.exists():
            return str(bundled)
        return name  # let ultralytics resolve built-ins / user paths

    @staticmethod
    def _pick_device(pref: str) -> str:
        if pref != "auto":
            return pref
        try:
            import torch
            if torch.cuda.is_available():
                return "cuda:0"
        except Exception:
            pass
        return "cpu"

    def track(self, frame) -> list[Detection]:
        results = self.model.track(
            frame, persist=True, tracker=self.tracker,
            imgsz=self.imgsz, conf=self.conf, classes=self.class_ids,
            device=self.device, verbose=False)
        out: list[Detection] = []
        r = results[0]
        if r.boxes is None or r.boxes.id is None:
            return out
        for box, tid, cls, conf in zip(r.boxes.xyxy, r.boxes.id,
                                       r.boxes.cls, r.boxes.conf):
            out.append(Detection(
                track_id=int(tid),
                cls_name=self.names[int(cls)],
                conf=float(conf),
                xyxy=tuple(float(v) for v in box.tolist())))
        return out


CULPRIT_GREEN = (0, 255, 0)


def annotate(frame, detections: list[Detection], zones: dict | None = None,
             flagged: set | None = None, trails: dict | None = None):
    """Draw boxes/zones on a frame for the dashboard MJPEG view.

    `flagged` is a set of track_ids that triggered an anomaly — these are drawn
    with a bright-green "CULPRIT" box so a guard can follow the person/vehicle
    across the frame. `trails` maps track_id -> list of (x, y) recent foot
    points, drawn as a green path behind flagged tracks for easy tracking.
    """
    import cv2
    flagged = flagged or set()
    trails = trails or {}
    default_colors = {"person": (0, 200, 255)}
    if zones:
        import numpy as np
        zone_colors = {"entry": (255, 160, 0), "parking": (0, 255, 120),
                       "restricted": (0, 0, 255)}
        for zname, poly in zones.items():
            if len(poly or []) >= 3:
                pts = np.array(poly, np.int32).reshape((-1, 1, 2))
                c = zone_colors.get(zname, (200, 200, 200))
                cv2.polylines(frame, [pts], True, c, 2)
                cv2.putText(frame, zname, tuple(poly[0]),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, c, 2)

    # motion trails for flagged tracks (draw first, behind the boxes)
    for tid, pts in trails.items():
        if tid in flagged and len(pts) >= 2:
            for i in range(1, len(pts)):
                cv2.line(frame, (int(pts[i - 1][0]), int(pts[i - 1][1])),
                         (int(pts[i][0]), int(pts[i][1])), CULPRIT_GREEN, 2)

    for d in detections:
        x1, y1, x2, y2 = [int(v) for v in d.xyxy]
        if d.track_id in flagged:
            cv2.rectangle(frame, (x1, y1), (x2, y2), CULPRIT_GREEN, 3)
            label = f"CULPRIT {d.cls_name}#{d.track_id}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
            cv2.rectangle(frame, (x1, max(0, y1 - th - 8)), (x1 + tw + 4, y1),
                          CULPRIT_GREEN, -1)
            cv2.putText(frame, label, (x1 + 2, max(12, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 2)
        else:
            color = default_colors.get(d.cls_name, (60, 220, 60))
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.putText(frame, f"{d.cls_name}#{d.track_id}",
                        (x1, max(15, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
    return frame
