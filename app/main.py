"""Entrypoint: starts one worker + pipeline per camera and the dashboard.

    python -m app.main --config config.yaml
"""
from __future__ import annotations

import argparse
import logging
import os
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
from .fusion import AI_REVIEW, CONFIRMED_INCIDENT, fuse
from .hybrid import HybridSecurityMonitor, build_evidence, route_from_reasons
from .notify import TelegramNotifier
from .plates import PlateReader, fuzzy_match
from .rules import SUSPICIOUS_ACTIVITY, Event, RulesEngine
from . import slots as slots_mod
from . import users as users_mod
from . import analyze as analyze_mod
from .trigger import AT_VEHICLE as TRIG_AT_VEHICLE
from .trigger import BREAK_IN as TRIG_BREAK_IN
from .trigger import DEPARTURE as TRIG_DEPARTURE
from .trigger import NEAR_VEHICLE as TRIG_NEAR_VEHICLE
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
        # hybrid specialist layer (R3D-18 models + temporal confirm + fusion).
        # No-op unless hybrid.specialist.enabled — free-layer behaviour unchanged.
        self.monitor = HybridSecurityMonitor(ctx.config.get("hybrid", {}), name)
        self.monitor.warmup()
        self.hybrid_on = self.monitor.enabled
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
        # visitor log: only gate-facing cameras write to the register, so a
        # parking camera watching the same cars all day doesn't log crossings.
        vl = ctx.config.get("visitor_log") or {}
        gate_cams = vl.get("cameras") or []
        self.visitor_log = bool(vl.get("enabled", True)) and \
            (not gate_cams or name in gate_cams)
        self.vl_cfg = vl
        self._logged_tracks: dict[int, float] = {}   # track_id -> ts logged
        self._rates_refreshed = 0.0                  # verdict-rate cache
        # parking slots: drawn spaces, optionally assigned to a vehicle
        self.slot_cfg = ctx.config.get("slots") or {}
        self.slots = None                            # built on the first frame
        self._slots_loaded = 0.0

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

            if self.visitor_log and plate_info:
                self._log_gate_crossings(plate_info, ts)

            if self.slot_cfg.get("enabled", True):
                self._track_slots(detections, plate_info, ts)

            # Feed the guards' verdicts back into scoring. Read every few
            # minutes, not every frame: it is a slow-moving aggregate and this
            # is the hot loop.
            if ts - self._rates_refreshed > 300:
                self._rates_refreshed = ts
                try:
                    self.rules.verdict_rates = self.ctx.db.verdict_rates()
                except Exception:
                    log.exception("[%s] could not read verdict rates",
                                  self.cam_name)

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
            break_in = TRIG_BREAK_IN in reasons
            theft_chain = TRIG_DEPARTURE in reasons

            # hybrid specialist scoring + fusion. Runs every analyzed frame so
            # the rolling clip buffer stays warm; when enabled, fusion is the
            # FINAL gate on severity (and can suppress an uncorroborated
            # free-layer alarm). No-op / free-layer-only when disabled.
            fusion_result = None
            if self.hybrid_on:
                try:
                    route = route_from_reasons(reasons)
                    if any(d.is_vehicle for d in detections):
                        route["vehicle"] = True
                    obs = self.monitor.observe(frame, ts, route=route)
                    relationship = bool(self.trigger.last_involved) and bool(
                        {TRIG_NEAR_VEHICLE, TRIG_AT_VEHICLE} & set(reasons))
                    contradictions = set()
                    if any(pi.get("registered") for pi in plate_info.values()):
                        contradictions.add("registered_plate")
                    fusion_result = fuse(build_evidence(
                        self.cam_name, reasons, obs, relationship=relationship,
                        contradictions=contradictions))
                except Exception:
                    log.exception("[%s] hybrid layer failed; free layer only",
                                  self.cam_name)
                    fusion_result = None

            if fire:
                for tid in self.trigger.last_involved:
                    self._trig_flags[tid] = ts + TRIG_FLAG_HOLD_S
                escalate = (break_in or theft_chain) and \
                    ts - self._last_escalation_ts >= ESCALATION_REFRACTORY_S
                if escalate or \
                        ts - self._last_suspicious_ts >= SUSPICIOUS_REFRACTORY_S:
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
                    # fusion is the final gate when the hybrid layer is on:
                    # CONFIRMED->HIGH, AI_REVIEW->MEDIUM, WATCH/NORMAL->suppress
                    if fusion_result is not None:
                        sev = {CONFIRMED_INCIDENT: "HIGH",
                               AI_REVIEW: "MEDIUM"}.get(fusion_result.decision)
                        if sev is not None:
                            ev_why = "; ".join(fusion_result.accepted) \
                                or "context only"
                            desc = (f"[{fusion_result.decision}] {desc} "
                                    f"| evidence: {ev_why}")
                    if sev is not None:
                        self._last_suspicious_ts = ts
                        if escalate and sev == "HIGH":
                            self._last_escalation_ts = ts
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

    def _log_gate_crossings(self, plate_info: dict, ts: float):
        """One gate crossing per tracked vehicle, not per frame.

        A vehicle keeps the same track id for as long as it stays in view, so
        the track id is the natural unit of "one pass through the gate". The
        time debounce inside the DB is the backstop for when the tracker drops
        and re-acquires the same car under a new id.
        """
        debounce = float(self.vl_cfg.get("debounce_s", 60.0))
        min_visit = float(self.vl_cfg.get("min_visit_s", 30.0))
        for tid, info in plate_info.items():
            if tid in self._logged_tracks:
                continue
            self._logged_tracks[tid] = ts
            try:
                out = self.ctx.db.record_gate_crossing(
                    info["plate"], self.cam_name, ts,
                    debounce_s=debounce, min_visit_s=min_visit)
            except Exception:
                log.exception("[%s] visitor log write failed", self.cam_name)
                continue
            if out and out["action"] != "ignored":
                who = out["visit"]["owner_name"] or (
                    "resident" if out["visit"]["registered"] else "visitor")
                log.info("[%s] gate %s: %s (%s)", self.cam_name,
                         out["action"], info["plate"], who)
        # keep the seen-track map from growing without bound
        if len(self._logged_tracks) > 500:
            cutoff = ts - 3600
            self._logged_tracks = {k: v for k, v in self._logged_tracks.items()
                                   if v > cutoff}

    def _track_slots(self, detections, plate_info: dict, ts: float):
        """Watch the assigned spaces on this camera and tell owners when their
        own vehicle moves."""
        vehicles = [d for d in detections if d.is_vehicle]
        # Slots are edited from the console, so pick changes up without a
        # restart — but not on every frame.
        if self.slots is None or ts - self._slots_loaded > 120:
            self._slots_loaded = ts
            try:
                rows = self.ctx.db.list_slots(self.cam_name)
            except Exception:
                log.exception("[%s] could not load parking slots", self.cam_name)
                rows = []
            slots = [slots_mod.Slot(id=r["id"], camera=r["camera"],
                                    label=r["label"], polygon=r["polygon"],
                                    plate=r["plate"],
                                    flat_number=r["flat_number"],
                                    owner_name=r.get("owner_name"))
                     for r in rows]
            first = self.slots is None
            self.slots = slots_mod.SlotTracker(slots, self.slot_cfg)
            if first and slots:
                # adopt what is already parked, or every car sitting in its own
                # space at startup is announced as having just arrived
                self.slots.prime(vehicles, plate_info, ts)
                return

        if not self.slots.slots:
            return
        for change in self.slots.update(vehicles, plate_info, ts):
            sent = False
            if change.kind == slots_mod.VACATED and \
                    self.slot_cfg.get("notify_owner", True):
                try:
                    when = time.strftime("%H:%M", time.localtime(change.ts))
                    sent = self.ctx.notifier.notify_slot_owner(change, when)
                except Exception:
                    log.exception("[%s] slot notification failed", self.cam_name)
            try:
                self.ctx.db.record_slot_activity(change.slot.id, change.kind,
                                                 change.plate, change.ts, sent)
            except Exception:
                log.exception("[%s] could not record slot activity",
                              self.cam_name)
            log.info("[%s] slot: %s", self.cam_name, change.message())

    def _handle_event(self, ev):
        log.warning("EVENT [%s] %s: %s", ev.severity, ev.event_type, ev.description)
        event_id = self.ctx.db.insert_event(
            ev.ts, ev.camera, ev.event_type, ev.severity, ev.plate,
            ev.track_ids, ev.confidence, ev.description,
            score=ev.score, score_why=ev.score_why)
        self.ctx.clip_saver.save_async(self.worker, ev, event_id)


class AppContext:
    def __init__(self, config: dict, config_path: str = "config.yaml"):
        self.config = config
        self.config_path = config_path
        self.db = Database(config["storage"]["db_path"])
        seeded = self.db.seed_registry_from_csv(config["storage"]["registry_csv"])
        if seeded:
            log.info("registry: imported %d plates from CSV", seeded)
        # Without an account nobody can sign in to the operator app at all, so
        # a fresh install makes one. The password is generated and shown once
        # rather than shipped as a default, which would be a published
        # credential on every deployment of this software.
        first = users_mod.bootstrap_admin(self.db)
        if first:
            log.warning("=" * 62)
            log.warning("  operator app: first-run account created")
            log.warning("      username: %s", first[0])
            log.warning("      password: %s", first[1])
            log.warning("  Shown once. Change it: python -m app.users passwd admin")
            log.warning("=" * 62)
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
            # Honor a PORT env var (Railway/Render/most hosts set it); else config.
            uvicorn.run(create_app(ctx),
                        host=dcfg.get("host", "0.0.0.0"),
                        port=int(os.environ.get("PORT") or dcfg.get("port", 8000)),
                        log_level="warning")
    except KeyboardInterrupt:
        pass
    finally:
        ctx.stop()


if __name__ == "__main__":
    main()
