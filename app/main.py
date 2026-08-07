"""Entrypoint: starts one worker + pipeline per camera and the dashboard.

    python -m app.main --config config.yaml
"""
from __future__ import annotations

import argparse
import json
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
from . import brain_live
from . import discovery
from .enhance import enhance_frame
from .fusion import AI_REVIEW, CONFIRMED_INCIDENT, fuse
from .hybrid import (HybridSecurityMonitor, SpecialistObservation,
                     build_evidence, route_from_reasons)
from .incidents import IncidentGate
from .notify import TelegramNotifier
from .plates import PlateReader, fuzzy_match
from .eventgraph import EventGraph, sub_events_from_signals
from .rules import SUSPICIOUS_ACTIVITY, Event, RulesEngine, point_in_polygon
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
BRAIN_FEATURE_MAX_AGE_S = 5.0   # attach a brain window to an alert within this

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
        # Zones a guard drew in the console (stored by name) are the source of
        # truth and override whatever came from config / the cameras row, so an
        # on-site correction sticks across restarts.
        try:
            drawn = ctx.db.get_camera_zones(name)
        except Exception:                            # noqa: BLE001
            drawn = None
        if drawn:
            self.zones = drawn
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
        # behaviour brain: a learned, explainable judgement of how a person
        # moved near a vehicle. One scorer per camera, sharing the read-only
        # brain loaded at startup. It refines the free layer's candidates —
        # suppressing ordinary ones, corroborating real ones — via fusion, and
        # never alerts on its own. Absent brain => free layer unchanged.
        self.brain = getattr(ctx, "brain", None)
        self.brain_scorer = None
        if self.brain is not None and self.brain.ready:
            self.brain_scorer = brain_live.LiveBrainScorer(
                self.brain, name, ctx.config.get("brain") or {})
        # the most recent brain window (ts, features, night), stashed so an
        # alert can carry the geometry behind it — that is what an operator's
        # later "false alarm" turns into a training hard negative.
        self._last_brain = None
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
        # learned normalcy: what is permanently there, so it stops alarming.
        # No-op unless enabled. Loaded from the DB so a reboot keeps its
        # learning; furniture track ids are recomputed every frame.
        from .normalcy import CameraNormalcy
        self.normalcy_on = bool((ctx.config.get("normalcy") or {})
                                .get("enabled", True))
        self.normalcy = CameraNormalcy(name)
        if self.normalcy_on:
            try:
                self.normalcy.load_rows(ctx.db.load_normalcy(name))
            except Exception:
                log.exception("[%s] could not load learned normalcy", name)
        self._furniture_tids: set = set()
        self._normalcy_saved = 0.0
        # event graph: each track's timeline of observable sub-events, so a
        # sequence (approached -> lingered -> reached in) becomes a named event
        # that fusion reads as one more line of evidence. Additive: it enriches
        # the fusion state_chain where fusion already runs, and changes nothing
        # about the free layer on its own.
        eg_cfg = ctx.config.get("eventgraph") or {}
        self.event_graph = EventGraph(
            refractory_s=float(eg_cfg.get("refractory_s", 2.0)),
            memory_s=float(eg_cfg.get("memory_s", 120.0)),
            dwell_s=float(eg_cfg.get("dwell_s", 5.0)))
        self._graph_pruned = 0.0
        self._last_graph_event = None

    def stop(self):
        self._stop.set()

    def set_zones(self, zones: dict) -> None:
        """Apply freshly-drawn zones to this running camera immediately."""
        self.zones = zones or {}
        self.rules.set_zones(self.zones)
        self.trigger.set_zones(self.zones)

    def _update_event_graph(self, detections, reasons, pose_signals, ts):
        """Feed the event graph this frame; return the strongest derived chain.

        Every person accrues zone membership and their own pose; the persons the
        trigger flagged as involved also get the frame's vehicle-interaction
        reasons. Returns the highest-severity derived chain across tracks (or
        None) for fusion to weigh — the graph never raises an alert by itself.
        """
        involved = self.trigger.last_involved
        rank = {"HIGH": 2, "MEDIUM": 1}
        best = None
        for d in detections:
            if not d.is_person:
                continue
            tid = int(d.track_id)
            zones_hit = [z for z, poly in self.zones.items()
                         if poly and point_in_polygon(d.foot_point, poly)]
            rs = reasons if tid in involved else ()
            kinds = sub_events_from_signals(
                zones_hit=zones_hit, reasons=rs,
                pose=(pose_signals or {}).get(tid, ()))
            if kinds:
                self.event_graph.observe(tid, ts, kinds)
            ev = self.event_graph.derive(tid, ts)
            if ev is not None and (best is None or
                                   rank.get(ev.severity, 0) > rank.get(best.severity, 0)):
                best = ev
        if ts - self._graph_pruned > 30:
            self._graph_pruned = ts
            self.event_graph.prune(ts)
        self._last_graph_event = best
        return best.chain if best is not None else None

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

            # Learn what is permanently here, and mark this frame's furniture.
            # A "person" that never moves and is always in the same spot is a
            # fire hydrant, not a loiterer — the measured cause of this system
            # crying wolf. Events anchored only to furniture are dropped below.
            if self.normalcy_on:
                h, w = frame.shape[:2]
                self.normalcy.observe(detections, w, h, ts)
                self._furniture_tids = {
                    d.track_id for d in detections
                    if d.track_id is not None
                    and self.normalcy.classify(d, w, h, ts).static}
                if ts - self._normalcy_saved > 300:
                    self._normalcy_saved = ts
                    try:
                        self.ctx.db.save_normalcy(self.cam_name,
                                                  self.normalcy.to_rows())
                    except Exception:
                        log.exception("[%s] could not save learned normalcy",
                                      self.cam_name)

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
            # Close incidents that have gone quiet. Without this an incident
            # stays open until the next event, so "it ended" is never noticed
            # on a camera that has fallen silent — which is exactly what
            # happens when the incident is over.
            self.ctx.incident_gate.tick(ts)

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

            # event graph: accrue every person's timeline and derive sequences.
            # Always runs (cheap, pure), so history is complete; its output only
            # feeds fusion below, never raises an alert by itself.
            graph_chain = None
            try:
                graph_chain = self._update_event_graph(
                    detections, reasons, pose_signals, ts)
            except Exception:                            # noqa: BLE001
                log.exception("[%s] event graph failed", self.cam_name)

            # hybrid specialist scoring + fusion. Runs every analyzed frame so
            # the rolling clip buffer stays warm; when enabled, fusion is the
            # FINAL gate on severity (and can suppress an uncorroborated
            # free-layer alarm). No-op / free-layer-only when disabled.
            fusion_result = None
            if self.hybrid_on or self.brain_scorer is not None:
                try:
                    if self.hybrid_on:
                        route = route_from_reasons(reasons)
                        if any(d.is_vehicle for d in detections):
                            route["vehicle"] = True
                        obs = self.monitor.observe(frame, ts, route=route)
                    else:
                        obs = SpecialistObservation()
                    relationship = bool(self.trigger.last_involved) and bool(
                        {TRIG_NEAR_VEHICLE, TRIG_AT_VEHICLE} & set(reasons))
                    contradictions = set()
                    if any(pi.get("registered") for pi in plate_info.values()):
                        contradictions.add("registered_plate")
                    ev_bundle = build_evidence(
                        self.cam_name, reasons, obs, relationship=relationship,
                        state_chain=graph_chain, contradictions=contradictions)
                    # the brain refines candidates; fusion stays the final gate,
                    # so a lone brain score never pages a human (see app/fusion).
                    if self.brain_scorer is not None:
                        reg_tids = {tid for tid, pi in plate_info.items()
                                    if pi.get("registered")}
                        hour, night = brain_live.hour_and_night(ts)
                        reading = self.brain_scorer.observe(
                            detections, ts, registered_tids=reg_tids,
                            night=night, hour=hour)
                        if reading is not None:
                            self.brain.contribute(ev_bundle, reading.features,
                                                  confirmed=reading.confirmed)
                            self._last_brain = (ts, reading.features, night)
                    fusion_result = fuse(ev_bundle)
                except Exception:
                    log.exception("[%s] hybrid/brain layer failed; free layer "
                                  "only", self.cam_name)
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
                    # the event graph explains the *sequence* behind the alert,
                    # e.g. "approached the vehicle → stayed 7s → reached in"
                    if self._last_graph_event is not None:
                        desc += " | sequence: " + " → ".join(
                            self._last_graph_event.reasons)
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
                if self._is_furniture_event(ev):
                    log.info("[%s] dropped %s on learned furniture (tracks %s)",
                             self.cam_name, ev.event_type, ev.track_ids)
                    continue
                self._handle_event(ev)

            # annotated frame for the dashboard — flagged culprits in green.
            # Learned furniture is never a culprit: once the system has decided
            # a "person" that never moves is a fire hydrant and stopped
            # alerting on it, flagging it green on the live view would tell the
            # operator the opposite of what the alerting layer has concluded.
            flagged = (self.rules.active_flags(ts) |
                       {tid for tid, exp in self._trig_flags.items()
                        if exp >= ts}) - self._furniture_tids
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

    def _is_furniture_event(self, ev) -> bool:
        """True if this event is anchored ONLY to learned static furniture.

        A camera-tamper or offline event has no track and is never dropped —
        those are exactly the alerts that must always get through. An event
        naming several objects survives if even one of them is a real, moving
        thing: a person interacting with a bollard is still a person.
        """
        if not self.normalcy_on or not ev.track_ids:
            return False
        return all(tid in self._furniture_tids for tid in ev.track_ids)

    def _handle_event(self, ev):
        log.warning("EVENT [%s] %s: %s", ev.severity, ev.event_type, ev.description)
        event_id = self.ctx.db.insert_event(
            ev.ts, ev.camera, ev.event_type, ev.severity, ev.plate,
            ev.track_ids, ev.confidence, ev.description,
            score=ev.score, score_why=ev.score_why)

        # keep the geometry behind this alert if the brain scored it recently, so
        # a later "false alarm" verdict can become a training hard negative.
        lb = self._last_brain
        if lb is not None and 0 <= ev.ts - lb[0] <= BRAIN_FEATURE_MAX_AGE_S:
            try:
                self.ctx.db.save_event_features(event_id, lb[1], ev.camera, lb[2])
            except Exception:                            # noqa: BLE001
                log.exception("[%s] could not store alert features",
                              self.cam_name)

        # Rising edge: the event is always recorded, but it only interrupts
        # somebody if it opens an incident or makes an open one worse. Decided
        # here rather than at notify time because the state machine has to see
        # every event, in order, and clip saving is asynchronous.
        decision = self.ctx.incident_gate.observe(
            ev.camera, ev.ts, ev.severity, ev.event_type,
            score=ev.score, track_ids=ev.track_ids)
        self.ctx.remember_decision(event_id, decision)
        if not decision.notify:
            log.info("EVENT [%s] held: %s (%d events, %d held since the last "
                     "alert)", ev.camera, decision.reason,
                     decision.incident_events, decision.suppressed_since_alert)

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
        self.incident_gate = IncidentGate(config.get("incidents", {}))
        # The behaviour brain: a learned, explainable score for how a person
        # moved near a vehicle. Loaded from disk if a trained model exists;
        # with none present the whole system runs on the free layer exactly as
        # before — nothing to configure to keep today's behaviour.
        from .brain import BehaviorBrain
        brain_cfg = config.get("brain") or {}
        self.brain = None
        if brain_cfg.get("enabled", True):
            path = brain_cfg.get("model_path", "models/brain.joblib")
            self.brain = BehaviorBrain.load(path)
            if self.brain is not None and self.brain.ready:
                kind = ("supervised+anomaly" if self.brain.clf is not None
                        else "anomaly-only")
                synth = " [SYNTHETIC — retrain on real footage]" \
                    if self.brain.meta.get("synthetic") else ""
                log.info("behaviour brain loaded: %s (%s)%s", path, kind, synth)
            else:
                log.info("no behaviour brain at %s — running on the free layer",
                         path)
        # Delete old footage on a timer so the box never fills up and never
        # hoards a resident's video. Watches the clip directory's disk.
        ret_cfg = config.get("retention") or {}
        self.janitor = None
        if ret_cfg.get("enabled", True):
            from . import retention
            disk = (config.get("clips") or {}).get("out_dir", "clips")
            self.janitor = retention.Janitor(self.db, ret_cfg, disk)
            self.janitor.start()
        # event_id -> Decision, handed from _handle_event to _on_clip_ready.
        # Bounded: clip saving is asynchronous but not unbounded, and a
        # decision nobody collected must not pin memory forever.
        self._decisions: dict[int, object] = {}
        self._decisions_lock = threading.Lock()
        self.workers: dict[str, CameraWorker] = {}
        self.pipelines: dict[str, CameraPipeline] = {}

    def set_camera_zones(self, name: str, zones: dict, actor: str = "") -> dict:
        """Persist drawn zones and apply them live if the camera is running."""
        clean = self.db.set_camera_zones(name, zones, actor)
        pipe = self.pipelines.get(name)
        if pipe is not None:
            pipe.set_zones(clean)
        return clean

    def _discard_clip(self, event_id: int, clip_path: str, reason: str,
                      actor: str = "ai_review") -> None:
        """A cleared alert is not evidence: mark its clip deleted and remove the
        file(s). Safe when there is no clip on record."""
        row = self.db.clip_for_event(event_id)
        if row is not None:
            self.db.mark_clip_deleted(row["id"], actor, reason)
            paths = [row.get("path"), row.get("sidecar_path")]
        else:
            paths = [clip_path]
        for p in paths:
            if p:
                try:
                    os.remove(p)
                except OSError:
                    pass

    def discard_event_clip(self, event_id: int, reason: str,
                           actor: str = "operator") -> bool:
        """Public: drop an event's clip when it turns out to be a false alarm
        (e.g. an operator's ❌). Returns True if a clip was removed."""
        row = self.db.clip_for_event(event_id)
        if row is None:
            return False
        self._discard_clip(event_id, row.get("path", ""), reason, actor)
        return True

    def remember_decision(self, event_id: int, decision) -> None:
        with self._decisions_lock:
            self._decisions[event_id] = decision
            if len(self._decisions) > 500:
                for old in sorted(self._decisions)[:len(self._decisions) - 500]:
                    self._decisions.pop(old, None)

    def take_decision(self, event_id: int):
        """Pop the gate's verdict for this event. Missing means 'let it
        through' — a lost decision must never silence a real alert."""
        with self._decisions_lock:
            return self._decisions.pop(event_id, None)

    def _on_clip_ready(self, event, event_id: int, clip_path: str):
        decision = self.take_decision(event_id)
        # A held event still gets its clip and its database row — what is
        # withheld is the interruption, not the record. It also skips the paid
        # AI review, because re-reviewing the same incident every few seconds
        # is the same waste in money that the alert was in attention.
        if decision is not None and not decision.notify:
            log.info("clip saved for held event %s (%s)", event_id,
                     decision.reason)
            return

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
                # the evidence rule: a clip is kept ONLY when the alert is real.
                # If the AI review clears it, there is no alert and no evidence —
                # the clip is discarded rather than stored for 14 days. Inert
                # unless a reviewer is configured (no key => no verdict to gate
                # on, so nothing changes).
                keep_only = bool((self.config.get("clips") or {})
                                 .get("keep_only_confirmed", True))
                if keep_only and not result.get("suspicious"):
                    self._discard_clip(
                        event_id, clip_path,
                        "AI review cleared it (not suspicious)")
                    log.info("cleared by AI review — no alert sent, clip "
                             "discarded (event %s)", event_id)
                    return
        # fallback: simple one-shot VLM description
        if desc is None and self.vlm.enabled:
            if keyframes is None:
                keyframes = self.clip_saver.keyframes(clip_path)
            desc = self.vlm.describe(keyframes, event.event_type)
        self.notifier.notify_event(event, event_id, clip_path, desc)

    def start_cameras(self):
        """Cameras from config.yaml, then any added from the console.

        Both sources are supported on purpose: a headless install still
        configures itself from a file, while a guard adding a camera from the
        console should not have to edit YAML or restart anything.
        """
        for cam in self.config.get("cameras", []):
            self.start_camera(cam["name"], cam["url"],
                              zones=cam.get("zones", {}),
                              loop_file=bool(cam.get("loop_file")))
        try:
            for row in self.db.list_cameras(enabled_only=True):
                if row["name"] in self.workers:
                    continue                      # config.yaml wins on a clash
                zones = json.loads(row["zones_json"] or "{}")
                self.start_camera(row["name"], row["url"], zones=zones)
        except Exception:                          # noqa: BLE001
            log.exception("could not start cameras stored in the database")

        # Zero-touch connect: if nothing is configured, find the cameras on the
        # network by ourselves. Plug the box into the DVR's switch, power it on,
        # walk away. Runs on a background thread so the dashboard is up at once.
        ac_cfg = self.config.get("autoconnect") or {}
        if ac_cfg.get("enabled", True) and not self.workers:
            from . import autoconnect

            def _add(name, url, vendor, channel, width, height):
                cid = self.db.add_camera(name, url, vendor, channel, width,
                                         height, added_by="autoconnect")
                self.db.append_audit("autoconnect", "CAMERA_CHANGE",
                                     {"op": "auto-add", "name": name,
                                      "url": discovery.mask(url)})
                self.start_camera(name, url)
                return cid

            autoconnect.start(self, ac_cfg, _add,
                              lambda: set(self.workers) |
                              {c["name"] for c in self.db.list_cameras()})

    def start_camera(self, name: str, url: str, zones: dict | None = None,
                     loop_file: bool = False) -> bool:
        """Start one camera now. Safe to call while the system is running."""
        if name in self.workers:
            return False
        buffer_s = int(self.config["clips"].get("buffer_s", 60))
        worker = CameraWorker(name, url, buffer_s=buffer_s,
                              loop_file=loop_file)
        worker.start()
        pipe = CameraPipeline(name, worker, zones or {}, self)
        pipe.start()
        self.workers[name] = worker
        self.pipelines[name] = pipe
        # masked: an RTSP URL carries its password, and logs get pasted into
        # tickets and screenshots
        log.info("camera '%s' started (%s)", name, discovery.mask(url))
        return True

    def stop_camera(self, name: str) -> bool:
        """Stop and forget one camera, without disturbing the others."""
        pipe = self.pipelines.pop(name, None)
        worker = self.workers.pop(name, None)
        if pipe is not None:
            pipe.stop()
        if worker is not None:
            worker.stop()
        self.incident_gate.reset(name)
        log.info("camera '%s' stopped", name)
        return worker is not None

    def stop(self):
        if self.janitor is not None:
            self.janitor.stop()
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
