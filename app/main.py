"""Entrypoint: starts one worker + pipeline per camera and the dashboard.

    python -m app.main --config config.yaml
"""
from __future__ import annotations

import argparse
import logging
import threading
import time

import cv2
import yaml

from .analyze import VideoAnalyzer
from .assistant import TuningAssistant
from .camera import CameraWorker, frame_stats
from .clips import ClipSaver
from .db import Database
from .detector import Detector, annotate
from .notify import TelegramNotifier
from .plates import PlateReader, fuzzy_match
from .rules import RulesEngine
from .vlm import VLMDescriber

log = logging.getLogger("watchdog")


class CameraPipeline(threading.Thread):
    """Consumes frames from a CameraWorker at process_fps, runs detection,
    tracking, plate OCR and the rules engine; hands anomalies to the clip
    saver + notifier. Restarts itself on unexpected errors."""

    def __init__(self, name: str, worker: CameraWorker, zones: dict, ctx):
        super().__init__(name=f"pipe-{name}", daemon=True)
        self.cam_name = name
        self.worker = worker
        self.zones = zones or {}
        self.ctx = ctx
        self.rules = RulesEngine(name, self.zones, ctx.config["rules"])
        self.detector: Detector | None = None
        self.annotated_jpeg: bytes | None = None
        self._stop = threading.Event()
        self._last_ts = 0.0

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.is_set():
            try:
                self._loop()
                return
            except Exception:
                log.exception("[%s] pipeline crashed — restarting in 5s",
                              self.cam_name)
                self._stop.wait(5)

    def _loop(self):
        cfg = self.ctx.config
        if self.detector is None:
            self.detector = Detector(cfg["detection"])
        interval = 1.0 / float(cfg["detection"].get("process_fps", 6))
        fuzzy_d = int(cfg["plates"].get("fuzzy_max_distance", 1))

        while not self._stop.is_set():
            t0 = time.time()
            # A5: stream-offline check runs even with no frames
            for ev in self.rules.update_stream_status(
                    self.worker.online, self.worker.offline_since):
                self._handle_event(ev)

            frame, ts = self.worker.latest_frame()
            if frame is None or ts <= self._last_ts:
                self._stop.wait(0.1)
                continue
            self._last_ts = ts

            detections = self.detector.track(frame)

            # plates: only on vehicle crops, throttled inside PlateReader
            plate_info = {}
            registry = None
            for d in detections:
                if not d.is_vehicle:
                    continue
                plate = self.ctx.plate_reader.read(frame, d.xyxy, d.track_id)
                if plate:
                    if registry is None:
                        registry = self.ctx.db.registry_plates()
                    match = fuzzy_match(plate, registry, fuzzy_d)
                    plate_info[d.track_id] = {
                        "plate": match or plate,
                        "registered": match is not None}

            events = self.rules.update(detections, ts, plate_info)
            mean, lap = frame_stats(frame)
            events += self.rules.update_frame_stats(mean, lap, ts)
            for ev in events:
                self._handle_event(ev)

            # annotated frame for the dashboard
            vis = annotate(frame.copy(), detections, self.zones)
            ok, jpg = cv2.imencode(".jpg", vis, [cv2.IMWRITE_JPEG_QUALITY, 75])
            if ok:
                self.annotated_jpeg = jpg.tobytes()

            delay = interval - (time.time() - t0)
            if delay > 0:
                self._stop.wait(delay)

    def _handle_event(self, ev):
        log.warning("EVENT [%s] %s: %s", ev.severity, ev.event_type, ev.description)
        event_id = self.ctx.db.insert_event(
            ev.ts, ev.camera, ev.event_type, ev.severity, ev.plate,
            ev.track_ids, ev.confidence, ev.description)
        self.ctx.clip_saver.save_async(self.worker, ev, event_id)


class AppContext:
    def __init__(self, config: dict, config_path: str = "config.yaml"):
        self.config = config
        self.config_path = config_path
        self.db = Database(config["storage"]["db_path"])
        seeded = self.db.seed_registry_from_csv(config["storage"]["registry_csv"])
        if seeded:
            log.info("registry: imported %d plates from CSV", seeded)
        self.plate_reader = PlateReader(config.get("plates", {}))
        self.notifier = TelegramNotifier(
            config.get("telegram", {}), self.db,
            max_per_hour=int(config["rules"].get("max_notifications_per_hour", 10)))
        self.vlm = VLMDescriber(config.get("vlm", {}))
        self.clip_saver = ClipSaver(config.get("clips", {}), self.db,
                                    on_clip_ready=self._on_clip_ready)
        acfg = config.get("analyze", {})
        self.analyzer = VideoAnalyzer(config, acfg.get("out_dir", "clips/uploads")) \
            if acfg.get("enabled", True) else None
        self.assistant = TuningAssistant(config.get("assistant", {}))
        self.workers: dict[str, CameraWorker] = {}
        self.pipelines: dict[str, CameraPipeline] = {}

    def _on_clip_ready(self, event, event_id: int, clip_path: str):
        desc = None
        if self.vlm.enabled:
            desc = self.vlm.describe(self.clip_saver.keyframes(clip_path),
                                     event.event_type)
        self.notifier.notify_event(event, event_id, clip_path, desc)

    def start_cameras(self):
        buffer_s = int(self.config["clips"].get("buffer_s", 60))
        for cam in self.config.get("cameras", []):
            name = cam["name"]
            worker = CameraWorker(name, cam["url"], buffer_s=buffer_s,
                                  loop_file=bool(cam.get("loop_file")))
            worker.start()
            pipe = CameraPipeline(name, worker, cam.get("zones", {}), self)
            pipe.start()
            self.workers[name] = worker
            self.pipelines[name] = pipe
            log.info("camera '%s' started (%s)", name, cam["url"])

    def stop(self):
        for p in self.pipelines.values():
            p.stop()
        for w in self.workers.values():
            w.stop()


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    parser = argparse.ArgumentParser(description="Society AI Watchdog")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--no-dashboard", action="store_true",
                        help="run pipelines only (headless)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s")

    config = load_config(args.config)
    ctx = AppContext(config, config_path=args.config)
    ctx.start_cameras()

    try:
        if args.no_dashboard:
            while True:
                time.sleep(3600)
        else:
            import uvicorn
            from .dashboard import create_app
            dcfg = config.get("dashboard", {})
            uvicorn.run(create_app(ctx),
                        host=dcfg.get("host", "0.0.0.0"),
                        port=int(dcfg.get("port", 8000)),
                        log_level="warning")
    except KeyboardInterrupt:
        pass
    finally:
        ctx.stop()


if __name__ == "__main__":
    main()
