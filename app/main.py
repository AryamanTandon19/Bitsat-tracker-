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

from .ai_review import TieredReviewer
from .analyze import VideoAnalyzer
from .assistant import TuningAssistant
from .camera import CameraWorker, frame_stats
from .clips import ClipSaver
from .db import Database
from .detector import Detector, annotate
from .enhance import enhance_frame
from .notify import TelegramNotifier
from .plates import PlateReader, fuzzy_match
from .rules import SUSPICIOUS_ACTIVITY, Event, RulesEngine
from . import analyze as analyze_mod
from .trigger import BREAK_IN as TRIG_BREAK_IN
from .trigger import DEPARTURE as TRIG_DEPARTURE
from .trigger import CandidateTrigger
from .vlm import VLMDescriber

SUSPICIOUS_REFRACTORY_S = 60.0  # min gap between live suspicious events/camera
ESCALATION_REFRACTORY_S = 30.0  # min gap between HIGH break-in/theft alerts
TRIG_FLAG_HOLD_S = 10.0         # keep the green box on trigger subjects

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
        trig_cfg = ctx.config["rules"].get("trigger", {})
        self.trig_cfg = trig_cfg
        self.trigger = CandidateTrigger(self.zones, trig_cfg)
        from .motion import VehicleMotion
        self.motion = VehicleMotion()
        self.pose = None
        if trig_cfg.get("on_pose", True):
            from .pose import PoseEstimator
            self.pose = PoseEstimator({**trig_cfg,
                                       "pose_model": ctx.config["detection"]
                                       .get("pose_model", "yolo11n-pose.pt")})
        self.detector: Detector | None = None
        self.annotated_jpeg: bytes | None = None
        self._stop = threading.Event()
        self._last_ts = 0.0
        self._last_suspicious_ts = 0.0
        self._last_escalation_ts = 0.0
        self._trig_flags: dict[int, float] = {}

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
            low_light = cfg["detection"].get("low_light", "auto")
            if low_light and low_light != "off":
                frame = enhance_frame(frame, low_light)   # brighten before YOLO

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

            # candidate trigger: live "suspicious activity" (the AI-review gate)
            pose_signals = {}
            persons = [d for d in detections if d.is_person]
            if self.pose is not None and persons and \
                    analyze_mod._pose_worth_running(self.trig_cfg, detections):
                pose_signals = self.pose.analyze_frame(frame, persons, ts)
            motion_scores = self.motion.scores(
                frame, [d for d in detections if d.is_vehicle], persons)
            fire, reasons = self.trigger.is_candidate(detections, ts,
                                                      pose_signals,
                                                      motion=motion_scores)
            if fire:
                for tid in self.trigger.last_involved:
                    self._trig_flags[tid] = ts + TRIG_FLAG_HOLD_S
                break_in = TRIG_BREAK_IN in reasons
                theft_chain = TRIG_DEPARTURE in reasons
                escalate = (break_in or theft_chain) and \
                    ts - self._last_escalation_ts >= ESCALATION_REFRACTORY_S
                if escalate or \
                        ts - self._last_suspicious_ts >= SUSPICIOUS_REFRACTORY_S:
                    self._last_suspicious_ts = ts
                    if escalate:
                        self._last_escalation_ts = ts
                    desc = "Suspicious activity: " + ", ".join(reasons)
                    sev = "MEDIUM"
                    if theft_chain:
                        sev = "HIGH"
                        dep = self.trigger.last_departure or {}
                        desc = (f"POSSIBLE VEHICLE THEFT: vehicle drove away "
                                f"{dep.get('gap_s', '?')}s after suspicious "
                                f"activity around it")
                    elif break_in:
                        sev = "HIGH"
                        desc = ("POSSIBLE BREAK-IN AT VEHICLE: strike/reach "
                                "detected at the car (" + ", ".join(reasons) + ")")
                    events.append(Event(
                        ts=ts, camera=self.cam_name,
                        event_type=SUSPICIOUS_ACTIVITY, severity=sev,
                        description=desc,
                        track_ids=sorted(self.trigger.last_involved),
                        confidence=0.8 if theft_chain else 0.5))

            for ev in events:
                self._handle_event(ev)

            # annotated frame for the dashboard — flagged culprits in green
            flagged = self.rules.active_flags(ts) | \
                {tid for tid, exp in self._trig_flags.items() if exp >= ts}
            vis = annotate(frame.copy(), detections, self.zones,
                           flagged=flagged,
                           trails=self.rules.flag_trails(ts))
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
        self.notifier.start_feedback_poller()
        self.vlm = VLMDescriber(config.get("vlm", {}))
        self.reviewer = TieredReviewer(config.get("ai_review", {}), self.db)
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
        keyframes = None
        # full pipeline: two-tier AI review (Haiku screen -> Opus findings).
        # Sample densely around the event moment inside the clip.
        if self.reviewer.enabled:
            from .clips import smart_sample_times
            pre_s = float(self.config["clips"].get("pre_event_s", 10))
            post_s = float(self.config["clips"].get("post_event_s", 20))
            times = smart_sample_times(pre_s + post_s, [pre_s],
                                       self.reviewer.max_frames)
            keyframes = self.clip_saver.keyframes_at(clip_path, times)
            result = self.reviewer.review_clip(event, event_id,
                                               event.camera, keyframes)
            if result:
                desc = result["alert_text"]
                log.info("AI review [%s]: %s (₹%.2f)", event.camera,
                         result["summary"] or "not suspicious",
                         result["cost_inr"])
        # fallback: simple one-shot VLM description
        if desc is None and self.vlm.enabled:
            if keyframes is None:
                keyframes = self.clip_saver.keyframes(clip_path)
            desc = self.vlm.describe(keyframes, event.event_type)
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
