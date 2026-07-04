"""Anomaly clip extraction: 10s before + 20s after the event.

Pre-event frames come from the camera's rolling JPEG buffer; post-event frames
are collected by waiting on the same buffer. Each clip gets a sidecar JSON.
Nothing else is ever written to disk.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)


def _safe(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in s)


class ClipSaver:
    def __init__(self, cfg: dict, db, on_clip_ready=None):
        self.dir = Path(cfg.get("dir", "clips"))
        self.pre_s = float(cfg.get("pre_event_s", 10))
        self.post_s = float(cfg.get("post_event_s", 20))
        self.fps = int(cfg.get("fps", 10))
        self.db = db
        self.on_clip_ready = on_clip_ready  # callback(event, event_id, clip_path)

    def save_async(self, worker, event, event_id: int):
        """Fire-and-forget: waits post_event_s then writes the clip."""
        t = threading.Thread(target=self._save, args=(worker, event, event_id),
                             daemon=True, name=f"clip-{event_id}")
        t.start()
        return t

    def _save(self, worker, event, event_id: int):
        try:
            start_ts = event.ts - self.pre_s
            end_ts = event.ts + self.post_s
            # wait until the post-event window has elapsed (buffer keeps filling)
            wait = end_ts - time.time() + 0.5
            if wait > 0:
                time.sleep(wait)
            frames = worker.buffer_snapshot(since_ts=start_ts)
            frames = [(ts, jpg) for ts, jpg in frames if ts <= end_ts]
            if not frames:
                log.warning("no buffered frames for event %s", event_id)
                return
            path, sidecar = self._write(frames, event, event_id)
            self.db.insert_clip(event_id, str(path), str(sidecar),
                                frames[0][0], frames[-1][0])
            log.info("clip saved: %s", path)
            if self.on_clip_ready:
                self.on_clip_ready(event, event_id, str(path))
        except Exception:
            log.exception("clip save failed for event %s", event_id)

    def _write(self, frames, event, event_id: int):
        day = datetime.fromtimestamp(event.ts).strftime("%Y-%m-%d")
        tstr = datetime.fromtimestamp(event.ts).strftime("%H%M%S")
        out_dir = self.dir / day
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{_safe(event.camera)}_{_safe(event.event_type)}_{tstr}_{event_id}"
        path = out_dir / f"{stem}.mp4"
        sidecar = out_dir / f"{stem}.json"

        first = cv2.imdecode(np.frombuffer(frames[0][1], np.uint8), cv2.IMREAD_COLOR)
        h, w = first.shape[:2]
        vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                             self.fps, (w, h))
        try:
            for _, jpg in frames:
                img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
                if img is None:
                    continue
                if img.shape[:2] != (h, w):
                    img = cv2.resize(img, (w, h))
                vw.write(img)
        finally:
            vw.release()

        sidecar.write_text(json.dumps({
            "event_id": event_id,
            "event_type": event.event_type,
            "severity": event.severity,
            "camera": event.camera,
            "event_ts": event.ts,
            "event_time_iso": datetime.fromtimestamp(event.ts).isoformat(),
            "clip_start_ts": frames[0][0],
            "clip_end_ts": frames[-1][0],
            "plate": event.plate,
            "track_ids": event.track_ids,
            "confidence": event.confidence,
            "description": event.description,
        }, indent=2))
        return path, sidecar

    def keyframes(self, clip_path: str, n: int = 6) -> list[bytes]:
        """Evenly-spaced JPEG keyframes from a saved clip (for the VLM)."""
        cap = cv2.VideoCapture(clip_path)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        out = []
        try:
            if total <= 0:
                return out
            for i in range(n):
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(i * (total - 1) / max(n - 1, 1)))
                ok, frame = cap.read()
                if ok:
                    ok2, jpg = cv2.imencode(".jpg", frame,
                                            [cv2.IMWRITE_JPEG_QUALITY, 80])
                    if ok2:
                        out.append(jpg.tobytes())
        finally:
            cap.release()
        return out


def delete_clip_file(db, clip_id: int, actor_name: str, reason: str) -> bool:
    """Dashboard-initiated deletion: file removed, audit row permanent."""
    clip = db.get_clip(clip_id)
    if not clip or clip["deleted"]:
        return False
    p = Path(clip["path"])
    if p.is_file():
        p.unlink()
    if clip["sidecar_path"]:
        sp = Path(clip["sidecar_path"])
        if sp.is_file():
            sp.unlink()
    db.mark_clip_deleted(clip_id, actor=actor_name, reason=reason)
    return True
