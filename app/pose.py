"""Pose signals — stick-figure (skeleton) reading for the free layer.

A free YOLO pose model finds 17 body joints per person (COCO keypoints). From
joint positions we derive simple movement signals:

  pose_crouching  — hips dropped low toward the ankles (kneeling/crouching)
  pose_reaching   — wrist raised above the shoulder or stretched far out
  pose_arm_swing  — wrist moving very fast (like striking at glass)

These feed the candidate trigger as extra reasons. The geometry is pure
Python (`signals_from_keypoints`) so it is unit-testable without any model.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# COCO keypoint indices
NOSE = 0
L_SHOULDER, R_SHOULDER = 5, 6
L_WRIST, R_WRIST = 9, 10
L_HIP, R_HIP = 11, 12
L_KNEE, R_KNEE = 13, 14
L_ANKLE, R_ANKLE = 15, 16

MIN_KP_CONF = 0.3

CROUCHING = "pose_crouching"
REACHING = "pose_reaching"
ARM_SWING = "pose_arm_swing"


def _avg(k, conf, idxs):
    pts = [(k[i][0], k[i][1]) for i in idxs if conf[i] >= MIN_KP_CONF]
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def signals_from_keypoints(keypoints, confs, bbox, prev_wrists=None,
                           ts: float = 0.0, cfg: dict | None = None):
    """Return (signals: set[str], wrist_state) for one person.

    keypoints: 17 x (x, y); confs: 17 confidences; bbox: (x1, y1, x2, y2).
    prev_wrists: previous (ts, [(x, y), ...]) for swing speed (or None).
    """
    cfg = cfg or {}
    signals: set[str] = set()
    x1, y1, x2, y2 = bbox
    h = max(y2 - y1, 1.0)
    w = max(x2 - x1, 1.0)

    hip = _avg(keypoints, confs, [L_HIP, R_HIP])
    ankle = _avg(keypoints, confs, [L_ANKLE, R_ANKLE])
    shoulder = _avg(keypoints, confs, [L_SHOULDER, R_SHOULDER])

    # crouching: hips have dropped close to the ankles (standing ~0.45–0.55 of
    # box height between hip and ankle; crouching squeezes this)
    crouch_ratio = float(cfg.get("crouch_ratio", 0.32))
    if hip and ankle and (ankle[1] - hip[1]) < crouch_ratio * h:
        signals.add(CROUCHING)

    # reaching: a confident wrist above shoulder height, or stretched far
    # sideways from the body
    wrists = [(keypoints[i][0], keypoints[i][1]) for i in (L_WRIST, R_WRIST)
              if confs[i] >= MIN_KP_CONF]
    reach_x = float(cfg.get("reach_x_ratio", 0.5))
    for wx, wy in wrists:
        if shoulder and wy < shoulder[1]:
            signals.add(REACHING)
        if shoulder and abs(wx - shoulder[0]) > reach_x * w:
            signals.add(REACHING)

    # arm swing: wrist speed vs body height per second
    swing_speed = float(cfg.get("swing_speed_ratio", 1.5))  # x body-height/s
    if prev_wrists and wrists:
        pts_prev = prev_wrists[1]
        dt = max(ts - prev_wrists[0], 1e-3)
        if dt < 1.5 and pts_prev:
            for wx, wy in wrists:
                nearest = min(pts_prev,
                              key=lambda p: (p[0] - wx) ** 2 + (p[1] - wy) ** 2)
                dist = ((nearest[0] - wx) ** 2 + (nearest[1] - wy) ** 2) ** 0.5
                if dist / dt > swing_speed * h:
                    signals.add(ARM_SWING)
                    break

    return signals, (ts, wrists)


def _iou(a, b) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0:
        return 0.0
    aa = (a[2] - a[0]) * (a[3] - a[1])
    ab = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (aa + ab - inter)


class PoseEstimator:
    """Runs the pose model once per processed frame (only when persons are
    present) and maps skeletons back to tracked person boxes."""

    def __init__(self, cfg: dict):
        self.cfg = cfg or {}
        self.model_name = self.cfg.get("pose_model", "yolo11n-pose.pt")
        self._model = None
        self._failed = False
        self._wrists: dict[int, tuple] = {}   # track_id -> (ts, [(x, y)...])

    def _ensure_model(self) -> bool:
        if self._model is not None:
            return True
        if self._failed:
            return False
        try:
            from ultralytics import YOLO
            self._model = YOLO(self.model_name)
            log.info("pose model loaded: %s", self.model_name)
            return True
        except Exception as e:
            log.warning("pose model unavailable (%s) — pose signals off", e)
            self._failed = True
            return False

    def analyze_frame(self, frame, person_dets, ts: float) -> dict[int, set]:
        """person_dets: tracked person detections (with .track_id/.xyxy).
        Returns {track_id: set(signals)}."""
        if not person_dets or not self._ensure_model():
            return {}
        try:
            res = self._model.predict(frame, verbose=False, conf=0.3)[0]
        except Exception as e:
            log.debug("pose inference failed: %s", e)
            return {}
        if res.keypoints is None or res.boxes is None or not len(res.boxes):
            return {}
        out: dict[int, set] = {}
        kp_xy = res.keypoints.xy.tolist()
        kp_conf = (res.keypoints.conf.tolist()
                   if res.keypoints.conf is not None
                   else [[1.0] * 17] * len(kp_xy))
        boxes = res.boxes.xyxy.tolist()
        for k, confs, box in zip(kp_xy, kp_conf, boxes):
            if len(k) < 17:
                continue
            # match this skeleton to the closest tracked person
            best, best_iou = None, 0.25
            for d in person_dets:
                i = _iou(box, d.xyxy)
                if i > best_iou:
                    best, best_iou = d, i
            if best is None:
                continue
            signals, wrist_state = signals_from_keypoints(
                k, confs, best.xyxy, self._wrists.get(best.track_id), ts,
                self.cfg)
            self._wrists[best.track_id] = wrist_state
            if signals:
                out.setdefault(best.track_id, set()).update(signals)
        # forget stale wrist history
        for tid in list(self._wrists):
            if ts - self._wrists[tid][0] > 5.0:
                del self._wrists[tid]
        return out
