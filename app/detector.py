"""YOLO detection + ByteTrack tracking wrapper (Ultralytics)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

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
        log.info("detector ready (device=%s, classes=%s)", self.device,
                 sorted(wanted))

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
            frame, persist=True, tracker="bytetrack.yaml",
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


def annotate(frame, detections: list[Detection], zones: dict | None = None):
    """Draw boxes/zones on a frame for the dashboard MJPEG view."""
    import cv2
    colors = {"person": (0, 200, 255)}
    if zones:
        zone_colors = {"entry": (255, 160, 0), "parking": (0, 255, 120),
                       "restricted": (0, 0, 255)}
        for zname, poly in zones.items():
            if len(poly or []) >= 3:
                import numpy as np
                pts = np.array(poly, np.int32).reshape((-1, 1, 2))
                cv2.polylines(frame, [pts], True, zone_colors.get(zname, (200, 200, 200)), 2)
                cv2.putText(frame, zname, tuple(poly[0]),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                            zone_colors.get(zname, (200, 200, 200)), 2)
    for d in detections:
        x1, y1, x2, y2 = [int(v) for v in d.xyxy]
        color = colors.get(d.cls_name, (60, 220, 60))
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.putText(frame, f"{d.cls_name}#{d.track_id}", (x1, max(15, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
    return frame
