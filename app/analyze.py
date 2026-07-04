"""Offline analysis of an uploaded video file.

Runs the same detector + rules used on live cameras, but over a video file in
"video time" (the clock is derived from frame index / fps, so a 45s loitering
threshold means 45s of footage, not wall-clock). Produces the same Event
objects and, optionally, clips extracted straight from the uploaded file.

Zone-based rules (A1 unauthorized, A2 loitering, A4 restricted) only fire when
zones are supplied — pass zones from an existing camera, or draw them. A3
(vehicle contact) and A5 (tamper) work with no zones.

YOLO (ultralytics) is required for detection; import is lazy so the module and
its non-detection helpers load without it.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2

from .camera import frame_stats
from .detector import annotate
from .plates import fuzzy_match
from .rules import RulesEngine

log = logging.getLogger(__name__)


@dataclass
class AnalyzeJob:
    id: str
    filename: str
    status: str = "queued"        # queued | running | done | error
    progress: float = 0.0         # 0..1
    message: str = ""
    events: list = field(default_factory=list)   # serialized event dicts
    clips: dict = field(default_factory=dict)    # event_index -> clip path
    error: str | None = None

    def public(self) -> dict:
        return {"id": self.id, "filename": self.filename, "status": self.status,
                "progress": round(self.progress, 3), "message": self.message,
                "events": self.events, "error": self.error}


class VideoAnalyzer:
    """Holds job state; runs each analysis on a background thread."""

    def __init__(self, config: dict, out_dir: str = "clips/uploads"):
        self.config = config
        self.out_dir = Path(out_dir)
        self.jobs: dict[str, AnalyzeJob] = {}
        self._detector = None  # lazily built, reused across jobs

    def submit(self, path: str, filename: str, zones: dict | None = None,
               registry: list[str] | None = None) -> AnalyzeJob:
        job = AnalyzeJob(id=uuid.uuid4().hex[:12], filename=filename)
        self.jobs[job.id] = job
        threading.Thread(target=self._run, args=(job, path, zones or {},
                                                 registry or []),
                         daemon=True, name=f"analyze-{job.id}").start()
        return job

    def get(self, job_id: str) -> AnalyzeJob | None:
        return self.jobs.get(job_id)

    # ------------------------------------------------------------------
    def _run(self, job: AnalyzeJob, path: str, zones: dict, registry: list[str]):
        try:
            job.status = "running"
            self._analyze(job, path, zones, registry)
            job.status = "done"
            job.message = f"{len(job.events)} anomalies found"
        except Exception as e:
            log.exception("analysis failed")
            job.status = "error"
            job.error = str(e)

    def _analyze(self, job: AnalyzeJob, path: str, zones: dict,
                 registry: list[str]):
        if self._detector is None:
            from .detector import Detector
            self._detector = Detector(self.config["detection"])
        det = self._detector

        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            raise RuntimeError("cannot open uploaded video")
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        if not (1 <= fps <= 120):
            fps = 25.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0

        clock = [0.0]
        rules = RulesEngine("upload", zones, self.config["rules"],
                            now_fn=lambda: clock[0])
        pcfg = self.config.get("plates", {})
        fuzzy_d = int(pcfg.get("fuzzy_max_distance", 1))
        # process at the configured inference fps
        proc_fps = float(self.config["detection"].get("process_fps", 6))
        step = max(1, int(round(fps / proc_fps)))

        from .plates import PlateReader
        plate_reader = PlateReader(pcfg)

        idx = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % step != 0:
                idx += 1
                continue
            ts = idx / fps
            clock[0] = ts
            detections = det.track(frame)

            plate_info = {}
            for d in detections:
                if not d.is_vehicle:
                    continue
                plate = plate_reader.read(frame, d.xyxy, d.track_id)
                if plate:
                    match = fuzzy_match(plate, registry, fuzzy_d)
                    plate_info[d.track_id] = {"plate": match or plate,
                                              "registered": match is not None}

            events = rules.update(detections, ts, plate_info)
            mean, lap = frame_stats(frame)
            events += rules.update_frame_stats(mean, lap, ts)

            for ev in events:
                self._record(job, path, ev, fps)

            if total:
                job.progress = min(1.0, idx / total)
            idx += 1
        cap.release()
        job.progress = 1.0

    def _record(self, job: AnalyzeJob, src_path: str, ev, fps: float):
        event_index = len(job.events)
        clip_path = None
        try:
            clip_path = self._extract_clip(src_path, ev, fps, event_index, job.id)
        except Exception:
            log.exception("clip extraction failed")
        if clip_path:
            job.clips[event_index] = clip_path
        job.events.append({
            "index": event_index,
            "event_type": ev.event_type,
            "severity": ev.severity,
            "video_time_s": round(ev.ts, 1),
            "plate": ev.plate,
            "track_ids": ev.track_ids,
            "confidence": ev.confidence,
            "description": ev.description,
            "clip": clip_path,
        })

    def _extract_clip(self, src_path: str, ev, fps: float, event_index: int,
                      job_id: str) -> str | None:
        clips_cfg = self.config.get("clips", {})
        pre = float(clips_cfg.get("pre_event_s", 10))
        post = float(clips_cfg.get("post_event_s", 20))
        out_fps = int(clips_cfg.get("fps", 10))
        start = max(0.0, ev.ts - pre)
        end = ev.ts + post

        cap = cv2.VideoCapture(src_path)
        cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000.0)
        ok, frame = cap.read()
        if not ok:
            cap.release()
            return None
        h, w = frame.shape[:2]
        out_dir = self.out_dir / job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{event_index:02d}_{ev.event_type}"
        path = out_dir / f"{stem}.mp4"
        vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                             out_fps, (w, h))
        step_ms = 1000.0 / out_fps
        t = start
        try:
            while t <= end:
                cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
                ok, fr = cap.read()
                if not ok:
                    break
                if fr.shape[:2] != (h, w):
                    fr = cv2.resize(fr, (w, h))
                vw.write(fr)
                t += step_ms / 1000.0
        finally:
            vw.release()
            cap.release()

        (out_dir / f"{stem}.json").write_text(json.dumps({
            "event_type": ev.event_type, "severity": ev.severity,
            "video_time_s": ev.ts, "plate": ev.plate,
            "track_ids": ev.track_ids, "confidence": ev.confidence,
            "description": ev.description,
        }, indent=2))
        return str(path)
