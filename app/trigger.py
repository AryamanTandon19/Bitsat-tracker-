"""The candidate trigger — the cheap "is this worth a closer look?" gate.

This is deliberately a WIDE NET, not a smart judge: its only job is to decide,
using free YOLO output, whether a moment might be worth forwarding to the
(paid) Claude review. It errs toward firing — a false trigger costs a rupee,
a missed incident costs everything. Claude does the precision filtering.

Pure Python + duck-typed detections (see detector.Detection), so it is fully
unit-testable and its sensitivity can be validated/tuned with real footage via
validate_triggers.py.
"""
from __future__ import annotations

from datetime import datetime

from .rules import iou, is_night, point_in_polygon

# signal names (also used as human-readable reasons)
NEAR_VEHICLE = "person_near_vehicle"
LINGERING = "person_lingering"
AT_NIGHT = "person_at_night"
AT_VEHICLE = "person_at_vehicle"                       # touching/reaching into it
DEPARTURE = "vehicle_departure_after_activity"         # the theft chain
BREAK_IN = "possible_break_in"    # INSTANT escalation: strike/reach at a vehicle
DISTURBANCE = "vehicle_disturbance"   # violent pixel burst ON a parked vehicle


class CandidateTrigger:
    """One instance per camera / per analyzed clip."""

    def __init__(self, zones: dict, cfg: dict, now_fn=None,
                 localtime_fn=datetime.now):
        self.zones = {k: (v or []) for k, v in (zones or {}).items()}
        self.cfg = cfg or {}
        self.localtime_fn = localtime_fn
        self._person_since: dict[int, float] = {}   # track_id -> first-seen ts
        self._last_seen: dict[int, float] = {}
        self.last_involved: set[int] = set()  # person track_ids behind last fire
        # theft-chain state: per-vehicle parking anchor + suspicious-activity
        # memory, so "someone messed with this car" links to "car drove away"
        self._veh: dict[int, dict] = {}          # tid -> anchor state
        self._veh_activity: dict[int, float] = {}  # tid -> last suspicious ts
        self._veh_people: dict[int, set[int]] = {}  # tid -> people involved
        self._touch_since: dict[int, float] = {}  # person tid -> touch start ts
        self._disturb_streak: dict[int, int] = {}  # vehicle tid -> burst frames
        self.last_departure: dict | None = None   # info about the last chain fire

    def _night(self) -> bool:
        nh = self.cfg.get("night_hours", {"start": "23:00", "end": "05:00"})
        return is_night(self.localtime_fn(), nh.get("start", "23:00"),
                        nh.get("end", "05:00"))

    def is_candidate(self, detections, ts: float,
                     pose_signals: dict | None = None,
                     motion: dict | None = None) -> tuple[bool, list[str]]:
        """Return (fire?, reasons) for this frame. pose_signals optionally maps
        person track_id -> set of pose reasons (crouching/reaching/swing).
        motion optionally maps vehicle track_id -> frame-diff score (see
        app.motion.VehicleMotion) for the pose-free smash detector."""
        persons = [d for d in detections if d.cls_name == "person"]
        vehicles = [d for d in detections if d.cls_name != "person"]
        reasons: set[str] = set()

        # maintain per-person dwell timers (expire after a short absence)
        seen = {p.track_id for p in persons}
        for tid in list(self._person_since):
            if tid not in seen and ts - self._last_seen.get(tid, ts) > 3.0:
                self._person_since.pop(tid, None)
                self._last_seen.pop(tid, None)
        for p in persons:
            self._person_since.setdefault(p.track_id, ts)
            self._last_seen[p.track_id] = ts

        near_r = float(self.cfg.get("near_vehicle_px", 180))
        dwell_s = float(self.cfg.get("dwell_s", 8))
        night = self._night()
        involved: set[int] = set()
        veh_near_people: dict[int, set[int]] = {}  # vehicle tid -> person tids

        for p in persons:
            fired_for_p = False
            suspicious_for_p = False   # signals strong enough to arm the chain
            near_vehicles: list = []   # vehicles this person is close to
            if self.cfg.get("on_near_vehicle", True):
                for v in vehicles:
                    expanded = (v.xyxy[0] - near_r, v.xyxy[1] - near_r,
                                v.xyxy[2] + near_r, v.xyxy[3] + near_r)
                    if iou(p.xyxy, expanded) > 0:
                        reasons.add(NEAR_VEHICLE)
                        fired_for_p = True
                        near_vehicles.append(v)
            touching = sustained_touch = False
            if self.cfg.get("on_touch", True):
                for v in vehicles:
                    if iou(p.xyxy, v.xyxy) > 0.02:   # actually overlapping it
                        touching = True
                        reasons.add(AT_VEHICLE)
                        fired_for_p = True
                        if v not in near_vehicles:
                            near_vehicles.append(v)
                        break
                if touching:
                    # a quick touch is what an owner does opening the door;
                    # only a SUSTAINED touch (reaching in / working the door)
                    # is suspicious enough to arm the theft chain
                    t0 = self._touch_since.setdefault(p.track_id, ts)
                    if ts - t0 >= float(self.cfg.get("touch_arm_s", 3)):
                        suspicious_for_p = sustained_touch = True
                else:
                    self._touch_since.pop(p.track_id, None)
            if self.cfg.get("on_loiter", True) and \
                    ts - self._person_since[p.track_id] >= dwell_s:
                reasons.add(LINGERING)
                fired_for_p = suspicious_for_p = True
            if self.cfg.get("on_zone", True):
                for zname in ("restricted", "parking", "entry"):
                    if point_in_polygon(p.foot_point, self.zones.get(zname)):
                        reasons.add(f"person_in_{zname}")
                        fired_for_p = True
                        if zname == "restricted":
                            suspicious_for_p = True
            if night and self.cfg.get("on_night_person", True):
                reasons.add(AT_NIGHT)
                fired_for_p = suspicious_for_p = True
            p_pose = set()
            if pose_signals and self.cfg.get("on_pose", True):
                for sig in pose_signals.get(p.track_id, ()):
                    reasons.add(sig)
                    p_pose.add(sig)
                    fired_for_p = suspicious_for_p = True
            # INSTANT break-in escalation — fires at the smash moment itself,
            # not later at the drive-away. Requires the person to be at/near a
            # vehicle plus a strong action: an arm strike, a sustained reach
            # into the car, or crouching while touching it.
            if self.cfg.get("on_break_in", True) and near_vehicles and (
                    "pose_arm_swing" in p_pose
                    or sustained_touch
                    or ("pose_crouching" in p_pose and touching)):
                reasons.add(BREAK_IN)
                fired_for_p = suspicious_for_p = True
            if fired_for_p:
                involved.add(p.track_id)
            for v in near_vehicles:
                veh_near_people.setdefault(v.track_id, set()).add(p.track_id)
            # arm the theft chain: remember suspicious people per nearby vehicle
            if suspicious_for_p:
                for v in near_vehicles:
                    self._veh_activity[v.track_id] = ts
                    self._veh_people.setdefault(v.track_id, set()).add(p.track_id)

        # pose-free smash detector: a violent pixel burst ON a parked vehicle.
        # Glass shattering IS a pixel burst — it does not need the pose model to
        # see the arm. By default it also requires a detected person nearby, but
        # if YOLO can't see the person (small/low-res/occluded), set
        # `disturb_needs_person: false` so the burst still fires as a
        # DISTURBANCE. Without a person it does NOT auto-escalate to BREAK_IN —
        # it stays a signal for the fusion layer to weigh (a lone burst is only
        # WATCH; a burst that the vehicle model ALSO confirms becomes an alert).
        if motion and self.cfg.get("on_disturb", True):
            thresh = float(self.cfg.get("disturb_thresh", 16.0))
            need = int(self.cfg.get("disturb_frames", 2))
            needs_person = self.cfg.get("disturb_needs_person", True)
            for vid, score in motion.items():
                st = self._veh.get(vid)
                parked = st is not None and not st["departed"] and \
                    ts - st["ts0"] >= 2.0
                people_near = veh_near_people.get(vid, set())
                has_person = bool(people_near)
                if score >= thresh and parked and (has_person or not needs_person):
                    self._disturb_streak[vid] = \
                        self._disturb_streak.get(vid, 0) + 1
                    reasons.add(DISTURBANCE)
                    self._veh_activity[vid] = ts     # arms the theft chain too
                    if has_person:
                        involved |= people_near
                        self._veh_people.setdefault(vid, set()).update(people_near)
                        if self._disturb_streak[vid] >= need:
                            reasons.add(BREAK_IN)    # instant HIGH escalation
                    # no detected person: DISTURBANCE only — corroboration
                    # (the vehicle model) is what turns it into an alert
                else:
                    self._disturb_streak.pop(vid, None)

        # theft chain: parked vehicle drives away after suspicious activity
        if self.cfg.get("on_departure", True):
            dep = self._update_vehicles(vehicles, ts)
            if dep is not None:
                reasons.add(DEPARTURE)
                involved |= dep["people"]
                self.last_departure = dep

        self.last_involved = involved
        return (bool(reasons), sorted(reasons))

    def _update_vehicles(self, vehicles, ts: float) -> dict | None:
        """Track each vehicle's parking anchor. Returns departure info when a
        vehicle that sat parked >= parked_min_s starts moving AND someone acted
        suspiciously around it within link_s. A plain departure (no prior
        activity — e.g. the owner just driving off) stays silent."""
        parked_min = float(self.cfg.get("parked_min_s", 6))
        depart_frac = float(self.cfg.get("depart_frac", 0.6))
        link_s = float(self.cfg.get("link_s", 600))
        fired = None
        seen = set()
        for v in vehicles:
            tid = v.track_id
            seen.add(tid)
            x1, y1, x2, y2 = v.xyxy
            cx, cy, wv = (x1 + x2) / 2, (y1 + y2) / 2, max(1.0, x2 - x1)
            st = self._veh.get(tid)
            if st is None:
                self._veh[tid] = {"ts0": ts, "cx": cx, "cy": cy, "w": wv,
                                  "last": ts, "departed": False}
                continue
            st["last"] = ts
            dist = ((cx - st["cx"]) ** 2 + (cy - st["cy"]) ** 2) ** 0.5
            parked_for = ts - st["ts0"]
            if dist >= depart_frac * st["w"]:
                # moved a lot: departure if it was parked long enough
                if parked_for >= parked_min and not st["departed"]:
                    st["departed"] = True
                    act = self._veh_activity.get(tid)
                    if act is not None and ts - act <= link_s:
                        fired = {"vehicle": tid, "gap_s": round(ts - act, 1),
                                 "people": set(self._veh_people.get(tid, set()))}
                st.update(ts0=ts, cx=cx, cy=cy, w=wv)
            elif parked_for < parked_min and dist >= 0.25 * st["w"]:
                # still drifting (never really parked) — follow it
                st.update(ts0=ts, cx=cx, cy=cy, w=wv)
            elif st["departed"] and dist < 0.2 * st["w"] and \
                    parked_for >= parked_min:
                st["departed"] = False        # parked again: re-arm
        # expire vehicles unseen for a while
        for tid in list(self._veh):
            if tid not in seen and ts - self._veh[tid]["last"] > 5.0:
                self._veh.pop(tid, None)
                self._veh_activity.pop(tid, None)
                self._veh_people.pop(tid, None)
        return fired


def merge_windows(candidate_times: list[float], gap_s: float = 3.0,
                  pad_s: float = 2.0) -> list[tuple[float, float]]:
    """Collapse a list of candidate timestamps into [start, end] windows,
    joining points separated by <= gap_s and padding each end by pad_s."""
    if not candidate_times:
        return []
    ts = sorted(candidate_times)
    windows = []
    start = prev = ts[0]
    for t in ts[1:]:
        if t - prev <= gap_s:
            prev = t
        else:
            windows.append((max(0.0, start - pad_s), prev + pad_s))
            start = prev = t
    windows.append((max(0.0, start - pad_s), prev + pad_s))
    return windows


def windows_overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return not (a[1] < b[0] or a[0] > b[1])
