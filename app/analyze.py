"""Offline analysis of an uploaded video file.

Runs the same detector + rules used on live cameras over a video file in "video
time" (the clock is derived from frame index / fps, so a 45s loitering
threshold means 45s of footage). Produces:

  * a list of anomaly Events, and
  * a single ANNOTATED playback video (the whole clip with every detection
    boxed, culprits in green with a "CULPRIT" tag, and a red anomaly banner
    while an event is active) — encoded as browser-playable H.264 so it can be
    embedded and scrubbed directly in the dashboard.

Zone-based rules (unauthorized A1, restricted A4) need zones. Loitering (A2)
works even with no zones — it falls back to "lingering near any vehicle", so a
theft-style clip flags the person. Vehicle contact (A3) and tamper (A5) are
zone-free.

YOLO (ultralytics) is required for detection; import is lazy.
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import cv2

from .camera import frame_stats
from .enhance import enhance_frame
from .fusion import AI_REVIEW, CONFIRMED_INCIDENT, fuse
from .hybrid import HybridSecurityMonitor, build_evidence, route_from_reasons
from .motion import VehicleMotion
from .plates import fuzzy_match
from .rules import SUSPICIOUS_ACTIVITY as SUSPICIOUS
from .rules import RulesEngine
from .trigger import (AT_VEHICLE, BREAK_IN, DEPARTURE, NEAR_VEHICLE,
                      CandidateTrigger)

log = logging.getLogger(__name__)
SUSPICIOUS_REFRACTORY_S = 20.0   # min gap between suspicious-activity events
ESCALATION_REFRACTORY_S = 30.0   # min gap between HIGH break-in/theft events

REASON_TEXT = {
    "person_near_vehicle": "person close to a vehicle",
    "person_lingering": "person lingering in view",
    "person_at_night": "person present at night",
    "person_in_restricted": "person in restricted zone",
    "person_in_parking": "person in parking zone",
    "person_in_entry": "person in entry zone",
    "pose_crouching": "person crouching",
    "pose_reaching": "person reaching/arm raised",
    "pose_arm_swing": "fast arm movement (possible strike)",
    "person_at_vehicle": "person touching / reaching into a vehicle",
    "vehicle_departure_after_activity":
        "parked vehicle driven away soon after suspicious activity around it",
    "possible_break_in": "strike or sustained reach at a vehicle (break-in)",
    "vehicle_disturbance": "sudden violent movement ON a parked vehicle",
}

CULPRIT_GREEN = (0, 255, 0)
BANNER_HOLD_S = 3.0            # keep the red anomaly banner up this long
SEVERITY_COLOR = {"HIGH": (0, 0, 255), "MEDIUM": (0, 165, 255), "LOW": (0, 200, 255)}


@dataclass
class AnalyzeJob:
    id: str
    filename: str
    status: str = "queued"        # queued | running | encoding | done | error
    progress: float = 0.0         # 0..1
    message: str = ""
    events: list = field(default_factory=list)   # serialized rule-based events
    ai_findings: list = field(default_factory=list)   # Claude scene-review findings
    ai_note: str = ""                            # message when AI review unavailable
    annotated_path: str | None = None            # playback video path
    error: str | None = None

    def public(self) -> dict:
        return {"id": self.id, "filename": self.filename, "status": self.status,
                "progress": round(self.progress, 3), "message": self.message,
                "events": self.events, "ai_findings": self.ai_findings,
                "ai_note": self.ai_note, "error": self.error,
                "video_ready": bool(self.annotated_path)}


def _pose_worth_running(trig_cfg: dict, detections) -> bool:
    """Skip the (slow) pose model unless somebody is actually close to a
    vehicle — that's where crouch/reach/swing matters. Saves a lot of CPU."""
    if not trig_cfg.get("pose_only_near_vehicle", True):
        return True
    near_r = float(trig_cfg.get("near_vehicle_px", 180))
    persons = [d for d in detections if d.is_person]
    vehicles = [d for d in detections if d.is_vehicle]
    for p in persons:
        px = (p.xyxy[0] + p.xyxy[2]) / 2
        py = (p.xyxy[1] + p.xyxy[3]) / 2
        for v in vehicles:
            if (v.xyxy[0] - near_r <= px <= v.xyxy[2] + near_r and
                    v.xyxy[1] - near_r <= py <= v.xyxy[3] + near_r):
                return True
    return False


def _hybrid_overlay(fr) -> str:
    """One debug-overlay line summarizing the hybrid decision (STEP 9: never
    hide a decision — show raw scores, what confirmed, and the final state)."""
    ev = fr.evidence
    scores = " ".join(f"{k}={v:.2f}" for k, v in
                      sorted(ev.specialist_scores.items())) or "no-score"
    confirmed = ",".join(sorted(ev.specialist_confirmed)) or "-"
    return f"HYBRID {fr.decision} spec[{scores}] confirmed[{confirmed}]"


def _open_writer(path: str, w: int, h: int, fps: float):
    """Return (kind, handle). Prefer browser-playable H.264 via the ffmpeg
    binary bundled with imageio-ffmpeg; fall back to OpenCV mp4v (which most
    browsers can't play inline, but at least produces a downloadable file)."""
    fps = max(1.0, min(fps, 30.0))
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        proc = subprocess.Popen(
            [exe, "-y", "-loglevel", "error",
             "-f", "rawvideo", "-pix_fmt", "bgr24",
             "-s", f"{w}x{h}", "-r", f"{fps:.3f}", "-i", "-",
             "-an", "-c:v", "libx264", "-preset", "veryfast",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", path],
            stdin=subprocess.PIPE)
        return ("ffmpeg", proc)
    except Exception as e:
        log.warning("imageio-ffmpeg unavailable (%s) — falling back to mp4v; "
                    "browser inline playback may not work. "
                    "Run: pip install imageio-ffmpeg", e)
        vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
        return ("cv2", vw)


def _write(writer, frame):
    kind, handle = writer
    if kind == "ffmpeg":
        handle.stdin.write(frame.tobytes())
    else:
        handle.write(frame)


def _close(writer):
    kind, handle = writer
    if kind == "ffmpeg":
        try:
            handle.stdin.close()
        finally:
            handle.wait()
    else:
        handle.release()


class VideoAnalyzer:
    """Holds job state; runs each analysis on a background thread."""

    def __init__(self, config: dict, out_dir: str = "clips/uploads"):
        self.config = config
        self.out_dir = Path(out_dir)
        self.jobs: dict[str, AnalyzeJob] = {}
        self._detector = None  # lazily built, reused across jobs

    def submit(self, path: str, filename: str, zones: dict | None = None,
               registry: list[str] | None = None,
               delete_source: bool = False, ai_review: bool = False) -> AnalyzeJob:
        job = AnalyzeJob(id=uuid.uuid4().hex[:12], filename=filename)
        self.jobs[job.id] = job
        threading.Thread(target=self._run,
                         args=(job, path, zones or {}, registry or [],
                               delete_source, ai_review),
                         daemon=True, name=f"analyze-{job.id}").start()
        return job

    def get(self, job_id: str) -> AnalyzeJob | None:
        return self.jobs.get(job_id)

    # ------------------------------------------------------------------
    def _run(self, job, path, zones, registry, delete_source=False,
             ai_review=False):
        try:
            job.status = "running"
            self._analyze(job, path, zones, registry)
            if ai_review:
                self._ai_review(job, path)
            job.status = "done"
            job.message = (f"{len(job.events)} rule alerts, "
                           f"{len(job.ai_findings)} AI findings")
        except Exception as e:
            log.exception("analysis failed")
            job.status = "error"
            job.error = str(e)
        finally:
            if delete_source:  # privacy: never keep the raw uploaded footage
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    pass

    def _analyze(self, job, path, zones, registry):
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
        proc_fps = float(self.config["detection"].get("process_fps", 6))
        step = max(1, int(round(fps / proc_fps)))

        clock = [0.0]
        rules = RulesEngine("upload", zones, self.config["rules"],
                            now_fn=lambda: clock[0])
        # the same candidate trigger that validate_triggers.py measures —
        # surfaces "suspicious activity" even when no strict rule fires
        trig_cfg = self.config["rules"].get("trigger", {})
        trig = CandidateTrigger(zones, trig_cfg,
                                localtime_fn=__import__("datetime").datetime.now)
        pose = None
        if trig_cfg.get("on_pose", True):
            from .pose import PoseEstimator
            pose = PoseEstimator({**trig_cfg,
                                  "pose_model": self.config["detection"]
                                  .get("pose_model", "yolo11n-pose.pt")})
        last_suspicious_ts = -1e9
        last_escalation_ts = -1e9
        motion = VehicleMotion()
        # hybrid specialist layer (R3D-18 models + temporal confirm + fusion).
        # Fully gated: with hybrid.specialist.enabled off (default) the monitor
        # short-circuits and the free-layer behaviour below is unchanged.
        monitor = HybridSecurityMonitor(self.config.get("hybrid", {}), "upload")
        monitor.warmup()
        hybrid_on = monitor.enabled
        debug_overlay = bool((self.config.get("analyze") or {})
                             .get("debug_overlay", True))
        overlay_lines: list[str] = []
        trig_flags: dict[int, float] = {}   # track_id -> green-box hold expiry
        TRIG_FLAG_HOLD_S = 10.0
        pcfg = self.config.get("plates", {})
        from .plates import PlateReader
        plate_reader = PlateReader(pcfg)
        fuzzy_d = int(pcfg.get("fuzzy_max_distance", 1))

        # low-light enhancement so dark night subjects become detectable
        low_light = (self.config.get("detection") or {}).get("low_light", "auto")

        out_dir = self.out_dir / job.id
        out_dir.mkdir(parents=True, exist_ok=True)
        annotated = out_dir / "annotated.mp4"

        writer = None
        out_w = out_h = None
        last_boxes = []      # [(tid, cls, xyxy)]
        last_flagged = set()
        event_marks = []     # [(ts, type, severity)]
        idx = 0
        # speed: by default only the ANALYZED frames go into the output video
        # (proc_fps instead of full fps) — ~4x less drawing + encoding work.
        full_fps = bool((self.config.get("analyze") or {})
                        .get("full_fps_video", False))

        while True:
            ok, frame = cap.read()
            if not ok:
                break
            ts = idx / fps
            clock[0] = ts
            if low_light and low_light != "off":
                frame = enhance_frame(frame, low_light)   # brighten before YOLO

            if idx % step == 0:
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

                # candidate trigger: green-box involved people and raise a
                # "suspicious activity" event (with a refractory gap)
                pose_signals = {}
                persons = [d for d in detections if d.is_person]
                pose_ran = False
                if pose is not None and persons and \
                        _pose_worth_running(trig_cfg, detections):
                    pose_signals = pose.analyze_frame(frame, persons, ts)
                    pose_ran = True
                vehicle_dets = [d for d in detections if d.is_vehicle]
                motion_scores = motion.scores(frame, vehicle_dets)
                fire, reasons = trig.is_candidate(detections, ts, pose_signals,
                                                  motion=motion_scores)
                break_in = BREAK_IN in reasons
                theft_chain = DEPARTURE in reasons

                # hybrid specialist scoring + explainable fusion. When the
                # hybrid layer is on, fusion is the FINAL gate: it can confirm a
                # free-layer alarm with model evidence or downgrade one that
                # lacks corroboration (false-alarm defense). When off,
                # fusion_result stays None and the free layer decides alone.
                fusion_result = None
                if hybrid_on:
                    try:
                        # route the vehicle model whenever a car is on screen —
                        # not only when a person was detected near it — so a
                        # smash is still scored if YOLO missed the person
                        route = route_from_reasons(reasons)
                        if vehicle_dets:
                            route["vehicle"] = True
                        obs = monitor.observe(frame, ts, route=route)
                        relationship = bool(trig.last_involved) and bool(
                            {NEAR_VEHICLE, AT_VEHICLE} & set(reasons))
                        contradictions = set()
                        if any(pi.get("registered")
                               for pi in plate_info.values()):
                            contradictions.add("registered_plate")
                        fusion_result = fuse(build_evidence(
                            "upload", reasons, obs, relationship=relationship,
                            contradictions=contradictions))
                    except Exception:
                        log.exception("hybrid layer failed; free layer only")
                        fusion_result = None

                if debug_overlay:
                    overlay_lines = self._overlay_lines(
                        pose, pose_ran, pose_signals, motion_scores,
                        trig_cfg, reasons, persons, trig, ts)
                    if fusion_result is not None:
                        overlay_lines.append(_hybrid_overlay(fusion_result))

                escalate = (break_in or theft_chain) and \
                    ts - last_escalation_ts >= ESCALATION_REFRACTORY_S
                if fire and (escalate or
                             ts - last_suspicious_ts >= SUSPICIOUS_REFRACTORY_S):
                    why = ", ".join(REASON_TEXT.get(r, r) for r in reasons)
                    desc = f"Suspicious activity: {why}"
                    sev = "MEDIUM"
                    if theft_chain:
                        sev = "HIGH"
                        dep = trig.last_departure or {}
                        desc = (f"POSSIBLE VEHICLE THEFT: vehicle drove away "
                                f"{dep.get('gap_s', '?')}s after suspicious "
                                f"activity around it ({why})")
                    elif break_in:
                        sev = "HIGH"
                        desc = ("POSSIBLE BREAK-IN AT VEHICLE: strike/reach "
                                f"detected at the car ({why})")
                    # fusion overrides severity when the hybrid layer is on:
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
                        last_suspicious_ts = ts
                        if escalate and sev == "HIGH":
                            last_escalation_ts = ts
                        from types import SimpleNamespace
                        events.append(SimpleNamespace(
                            ts=ts, event_type=SUSPICIOUS, severity=sev,
                            description=desc,
                            plate=None, track_ids=sorted(trig.last_involved),
                            confidence=0.8 if theft_chain else 0.5))

                if fire:
                    for tid in trig.last_involved:
                        trig_flags[tid] = ts + TRIG_FLAG_HOLD_S
                last_boxes = [(d.track_id, d.cls_name, d.xyxy) for d in detections]
                last_flagged = rules.active_flags(ts) | \
                    {tid for tid, exp in trig_flags.items() if exp >= ts}
                for ev in events:
                    self._append_event(job, ev)
                    event_marks.append((ev.ts, ev.event_type, ev.severity))
                if total:
                    job.progress = min(0.98, idx / total)

            if full_fps or idx % step == 0:
                if writer is None:
                    h, w = frame.shape[:2]
                    out_w, out_h = w - (w % 2), h - (h % 2)  # even dims: H.264
                    writer = _open_writer(str(annotated), out_w, out_h,
                                          fps if full_fps else fps / step)
                vis = frame[:out_h, :out_w].copy()
                self._draw(vis, last_boxes, last_flagged)
                self._draw_banner(vis, ts, event_marks)
                if debug_overlay and overlay_lines:
                    self._draw_overlay(vis, overlay_lines)
                _write(writer, vis)
            idx += 1

        cap.release()
        if writer is not None:
            job.status = "encoding"
            _close(writer)
            job.annotated_path = str(annotated)
        job.progress = 1.0

    # ------------------------------------------------------------------
    def _ai_review(self, job, path):
        """Claude watches keyframes across the clip and reports what's actually
        happening (theft, break-in, vandalism...) — real scene understanding,
        not geometry rules. Frames are sampled DENSELY around the moments the
        free layer flagged, so the actual smash/entry is likely captured."""
        from .clips import smart_sample_times
        from .vlm import VLMDescriber
        reviewer = VLMDescriber({**self.config.get("vlm", {}), "enabled": True})
        if not reviewer.available:
            job.ai_note = ("Smart AI Review needs an Anthropic API key. Set the "
                           "ANTHROPIC_API_KEY environment variable and try again.")
            return
        duration = self._video_duration(path)
        focus = [e["video_time_s"] for e in job.events]
        times = smart_sample_times(duration, focus, reviewer.review_max_frames)
        frames = self._keyframes_at_times(path, times)
        if not frames:
            job.ai_note = "could not read frames for AI review"
            return
        job.ai_findings = reviewer.review_video(frames)
        if not job.ai_findings:
            job.ai_note = "Claude reviewed the clip and found nothing clearly suspicious."

    @staticmethod
    def _video_duration(path) -> float:
        cap = cv2.VideoCapture(path)
        try:
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            fps = fps if 1 <= fps <= 120 else 25.0
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
            return total / fps if total else 0.0
        finally:
            cap.release()

    @staticmethod
    def _keyframes_at_times(path, times):
        cap = cv2.VideoCapture(path)
        out = []
        try:
            for t in times:
                cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000.0)
                ok, frame = cap.read()
                if not ok:
                    continue
                h, w = frame.shape[:2]
                if w > 768:
                    frame = cv2.resize(frame, (768, int(h * 768 / w)))
                ok2, jpg = cv2.imencode(".jpg", frame,
                                        [cv2.IMWRITE_JPEG_QUALITY, 80])
                if ok2:
                    out.append((round(t, 1), jpg.tobytes()))
            return out
        finally:
            cap.release()

    @staticmethod
    def _sample_keyframes(path, max_frames):
        cap = cv2.VideoCapture(path)
        try:
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
            if total <= 0:
                return []
            duration = total / (fps if fps > 0 else 25.0)
            n = min(max_frames, max(1, total))
            out = []
            for i in range(n):
                t = duration * i / max(n - 1, 1)
                cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
                ok, frame = cap.read()
                if not ok:
                    continue
                # downscale to keep the API payload light
                h, w = frame.shape[:2]
                if w > 768:
                    frame = cv2.resize(frame, (768, int(h * 768 / w)))
                ok2, jpg = cv2.imencode(".jpg", frame,
                                        [cv2.IMWRITE_JPEG_QUALITY, 80])
                if ok2:
                    out.append((round(t, 1), jpg.tobytes()))
            return out
        finally:
            cap.release()

    def _append_event(self, job, ev):
        job.events.append({
            "index": len(job.events),
            "event_type": ev.event_type,
            "severity": ev.severity,
            "video_time_s": round(ev.ts, 1),
            "plate": ev.plate,
            "track_ids": ev.track_ids,
            "confidence": ev.confidence,
            "description": ev.description,
        })

    @staticmethod
    def _overlay_lines(pose, pose_ran, pose_signals, motion_scores,
                       trig_cfg, reasons, persons, trig, ts) -> list[str]:
        """Human-readable snapshot of everything the free layer saw this
        frame — burned into the debug overlay so misses can be diagnosed
        by just pausing the output video."""
        if pose is None:
            pstate = "off"
        elif getattr(pose, "_failed", False):
            pstate = "FAILED"
        elif not pose_ran:
            pstate = "skip"                      # nobody near a vehicle
        else:
            pstate = "on"
        lines = [f"FREE LAYER t={ts:.1f}s  pose:{pstate}"]
        thresh = float(trig_cfg.get("disturb_thresh", 16.0))
        for vid, score in sorted(motion_scores.items())[:3]:
            st = trig._veh.get(vid)
            parked = bool(st and not st["departed"] and ts - st["ts0"] >= 2.0)
            mark = "!" if score >= thresh else ""
            lines.append(f"veh#{vid} motion={score:.0f}{mark}"
                         f" (thr {thresh:.0f}) parked={'y' if parked else 'n'}")
        for p in persons[:3]:
            dwell = ts - trig._person_since.get(p.track_id, ts)
            sigs = ",".join(sorted(pose_signals.get(p.track_id, ()))) or "-"
            lines.append(f"person#{p.track_id} dwell={dwell:.0f}s pose={sigs}")
        if reasons:
            lines.append(("fired: " + ",".join(reasons))[:64])
        return lines[:6]

    @staticmethod
    def _draw_overlay(frame, lines):
        x0 = frame.shape[1] - 330
        y = 46
        for line in lines:
            (tw, th), _ = cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX,
                                          0.45, 1)
            cv2.rectangle(frame, (x0 - 4, y - th - 4), (x0 + tw + 4, y + 4),
                          (0, 0, 0), -1)
            cv2.putText(frame, line, (x0, y), cv2.FONT_HERSHEY_SIMPLEX,
                        0.45, (0, 255, 255), 1)
            y += th + 10

    @staticmethod
    def _draw(frame, boxes, flagged):
        for tid, cls, xyxy in boxes:
            x1, y1, x2, y2 = [int(v) for v in xyxy]
            if tid in flagged:
                cv2.rectangle(frame, (x1, y1), (x2, y2), CULPRIT_GREEN, 3)
                label = f"CULPRIT {cls}#{tid}"
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
                cv2.rectangle(frame, (x1, max(0, y1 - th - 8)),
                              (x1 + tw + 4, y1), CULPRIT_GREEN, -1)
                cv2.putText(frame, label, (x1 + 2, max(12, y1 - 6)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 2)
            else:
                color = (0, 200, 255) if cls == "person" else (60, 220, 60)
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                cv2.putText(frame, f"{cls}#{tid}", (x1, max(15, y1 - 6)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

    @staticmethod
    def _draw_banner(frame, ts, event_marks):
        active = [(t, et, sev) for (t, et, sev) in event_marks
                  if t <= ts <= t + BANNER_HOLD_S]
        if not active:
            return
        t, et, sev = max(active, key=lambda m: m[0])
        w = frame.shape[1]
        color = SEVERITY_COLOR.get(sev, (0, 0, 255))
        cv2.rectangle(frame, (0, 0), (w, 34), color, -1)
        cv2.putText(frame, f"ANOMALY: {et.replace('_', ' ').upper()} [{sev}]",
                    (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
