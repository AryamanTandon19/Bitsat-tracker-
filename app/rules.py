"""The five anomaly rules (A1–A5) evaluated over tracker output.

Pure Python + injectable clock so every rule is unit-testable without video,
YOLO or OpenCV. The pipeline feeds `update()` once per processed frame.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime

# Event types
UNAUTHORIZED_VEHICLE = "unauthorized_vehicle"    # A1
UNIDENTIFIED_VEHICLE = "unidentified_vehicle"    # A1 (plate unreadable)
LOITERING = "loitering"                          # A2
VEHICLE_CONTACT = "vehicle_contact"              # A3
RESTRICTED_ZONE = "restricted_zone"              # A4
CAMERA_TAMPER = "camera_tamper"                  # A5
CAMERA_OFFLINE = "camera_offline"                # A5
SUSPICIOUS_ACTIVITY = "suspicious_activity"      # candidate trigger (wide net)

SEVERITY = {
    UNAUTHORIZED_VEHICLE: "HIGH",
    UNIDENTIFIED_VEHICLE: "LOW",
    LOITERING: "MEDIUM",
    VEHICLE_CONTACT: "MEDIUM",
    RESTRICTED_ZONE: "HIGH",
    CAMERA_TAMPER: "HIGH",
    CAMERA_OFFLINE: "HIGH",
    SUSPICIOUS_ACTIVITY: "MEDIUM",
}

TITLES = {
    UNAUTHORIZED_VEHICLE: "Unauthorized vehicle entry",
    UNIDENTIFIED_VEHICLE: "Unidentified vehicle (plate unreadable)",
    LOITERING: "Person loitering near vehicles",
    VEHICLE_CONTACT: "Possible vehicle contact — one vehicle leaving",
    RESTRICTED_ZONE: "Person in restricted area",
    CAMERA_TAMPER: "Camera tampered (obscured/blinded)",
    CAMERA_OFFLINE: "Camera offline (stream lost)",
    SUSPICIOUS_ACTIVITY: "Suspicious activity",
}

TRACK_EXPIRY_S = 6.0  # forget a track this long after it was last seen


@dataclass
class Event:
    ts: float
    camera: str
    event_type: str
    severity: str
    description: str
    plate: str | None = None
    track_ids: list[int] = field(default_factory=list)
    confidence: float = 0.0
    score: float = 0.0        # 0..1 from the scoring layer (0 = not scored)
    score_why: str = ""       # the signals that produced it, in words


# ---------------------------------------------------------------- geometry
def point_in_polygon(pt: tuple[float, float], poly: list) -> bool:
    """Ray-casting point-in-polygon. `poly` is [[x, y], ...]."""
    if not poly or len(poly) < 3:
        return False
    x, y = pt
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    return inter / (area_a + area_b - inter)


def dist(p, q) -> float:
    return math.hypot(p[0] - q[0], p[1] - q[1])


def parse_hhmm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


def _all_registered(states) -> bool | None:
    """True only if every vehicle we could identify is on the registry.
    None when no plate was read — `all([])` is True, which would have read a
    total absence of information as a clean bill of health."""
    known = [bool(st.plate_registered) for st in states if st and st.plate]
    return all(known) if known else None


def is_night(now: datetime, start: str, end: str) -> bool:
    """True if `now` falls in [start, end), handling the midnight wrap."""
    sh, sm = parse_hhmm(start)
    eh, em = parse_hhmm(end)
    cur = now.hour * 60 + now.minute
    s, e = sh * 60 + sm, eh * 60 + em
    return (s <= cur < e) if s <= e else (cur >= s or cur < e)


# ------------------------------------------------------------------ state
@dataclass
class TrackState:
    cls_name: str
    first_seen: float
    last_seen: float
    xyxy: tuple = (0, 0, 0, 0)
    positions: list = field(default_factory=list)   # [(ts, (x, y))] foot points
    entry_zone_since: float | None = None
    parking_zone_since: float | None = None
    parking_entry_pos: tuple | None = None
    max_parking_displacement: float = 0.0
    plate: str | None = None
    plate_registered: bool | None = None
    was_moving: bool = False

    def speed(self, window_s: float = 1.5) -> float:
        """Pixels/second over the recent position history."""
        if len(self.positions) < 2:
            return 0.0
        t_end, p_end = self.positions[-1]
        for t0, p0 in reversed(self.positions):
            if t_end - t0 >= window_s:
                return dist(p_end, p0) / max(t_end - t0, 1e-6)
        t0, p0 = self.positions[0]
        return dist(p_end, p0) / max(t_end - t0, 1e-6)


class RulesEngine:
    """One instance per camera."""

    def __init__(self, camera: str, zones: dict, cfg: dict,
                 now_fn=time.time, localtime_fn=datetime.now):
        self.camera = camera
        self.zones = {k: (v or []) for k, v in (zones or {}).items()}
        self.cfg = cfg
        self.now_fn = now_fn
        self.localtime_fn = localtime_fn
        self.tracks: dict[int, TrackState] = {}
        self._debounce_until: dict[tuple, float] = {}
        # culprit tracking: track_id -> flag-expiry ts
        self.flag_seconds = float(cfg.get("flag_seconds", 60))
        self._flagged: dict[int, float] = {}
        # A3 contact bookkeeping: pair -> contact ts
        self._contacts: dict[tuple[int, int], float] = {}
        self._contact_fired: set[tuple[int, int]] = set()
        # A5 tamper bookkeeping
        self._tamper_since: float | None = None
        self._offline_fired = False
        self._tamper_ok = True
        # Scoring layer. Replaces the fixed SEVERITY table when on; the table
        # is kept as the fallback so a site can switch back in one line.
        self.scoring_cfg = cfg.get("scoring") or {}
        self.scoring_on = bool(self.scoring_cfg.get("enabled", True))
        # (camera, event_type) -> {"false_alarm_rate", "confirmed_rate"};
        # refreshed from the DB by the pipeline, empty until then
        self.verdict_rates: dict = {}
        # what the layer chose not to raise, for the tuning page
        self.dismissed: list[dict] = []

    # ------------------------------------------------------------ helpers
    def _night(self) -> bool:
        nh = self.cfg.get("night_hours", {})
        return is_night(self.localtime_fn(),
                        nh.get("start", "23:00"), nh.get("end", "05:00"))

    def _debounced(self, key: tuple, ts: float) -> bool:
        """True if this (subject, event_type) is still inside its quiet
        period. Otherwise arms the quiet period and returns False."""
        if ts < self._debounce_until.get(key, 0):
            return True
        self._debounce_until[key] = ts + float(self.cfg.get("debounce_s", 120))
        return False

    def _rule_enabled(self, name: str) -> bool:
        return bool(self.cfg.get(name, {}).get("enabled", True))

    def _score_context(self, etype: str, track_ids: list[int],
                       ts: float, registered: bool | None,
                       plate: str | None) -> dict:
        """Everything the scoring layer is allowed to know about this firing.
        Read off state the engine already keeps — no new tracking."""
        dwell = 0.0
        for tid in track_ids:
            st = self.tracks.get(tid)
            if st and st.positions:
                dwell = max(dwell, ts - st.positions[0][0])
        rates = self.verdict_rates.get((self.camera, etype), {})
        return {
            "night": self._night(),
            "registered": registered,
            "plate_known": None if registered is not None else bool(plate),
            "dwell_s": dwell,
            "at_vehicle": etype in (VEHICLE_CONTACT, LOITERING),
            "restricted": etype == RESTRICTED_ZONE,
            "false_alarm_rate": rates.get("false_alarm_rate", 0.0),
            "confirmed_rate": rates.get("confirmed_rate", 0.0),
        }

    def _emit(self, events: list, ts: float, etype: str, description: str,
              track_ids: list[int], plate: str | None = None,
              confidence: float = 0.0, debounce_key=None,
              registered: bool | None = None):
        key = debounce_key or (tuple(sorted(track_ids)) or self.camera, etype)
        if self._debounced(key, ts):
            return

        severity, score, why = SEVERITY[etype], 0.0, ""
        if self.scoring_on:
            from .scoring import score_event
            ctx = self._score_context(etype, track_ids, ts, registered, plate)
            s = score_event(etype, ctx, self.scoring_cfg)
            severity, score, why = s.severity, s.value, s.explain()
            if s.dismissed:
                # Not an alert. Kept visible for tuning so "why did it stay
                # quiet?" is as answerable as "why did it alarm?".
                self.dismissed.append({"ts": ts, "event_type": etype,
                                       "score": score, "why": why,
                                       "description": description})
                del self.dismissed[:-200]
                return

        # flag every track involved in a fired anomaly as a "culprit" so the
        # annotator can mark it green and follow it across the frame
        for tid in track_ids:
            self._flagged[tid] = ts + self.flag_seconds
        events.append(Event(ts=ts, camera=self.camera, event_type=etype,
                            severity=severity, description=description,
                            plate=plate, track_ids=track_ids,
                            confidence=confidence, score=score,
                            score_why=why))

    # -------------------------------------------------- culprit tracking
    def active_flags(self, ts: float | None = None) -> set:
        """track_ids currently flagged as culprits (unexpired)."""
        ts = ts if ts is not None else self.now_fn()
        return {tid for tid, exp in self._flagged.items() if exp >= ts}

    def flag_trails(self, ts: float | None = None, window_s: float = 8.0) -> dict:
        """Recent foot-point path for each active culprit, for a green trail."""
        ts = ts if ts is not None else self.now_fn()
        out = {}
        for tid in self.active_flags(ts):
            st = self.tracks.get(tid)
            if st:
                out[tid] = [p for t, p in st.positions if ts - t <= window_s]
        return out

    # ------------------------------------------------------------- update
    def update(self, detections, ts: float | None = None,
               plate_info: dict | None = None) -> list[Event]:
        """detections: objects with .track_id/.cls_name/.xyxy/.foot_point
        (duck-typed; see detector.Detection). plate_info maps track_id ->
        {"plate": str|None, "registered": bool|None}."""
        ts = ts if ts is not None else self.now_fn()
        plate_info = plate_info or {}
        events: list[Event] = []
        # vehicle boxes this frame — used by the zone-free loitering fallback
        self._frame_vehicles = [d.xyxy for d in detections
                                if d.cls_name != "person"]

        for d in detections:
            st = self.tracks.get(d.track_id)
            if st is None:
                st = self.tracks[d.track_id] = TrackState(
                    cls_name=d.cls_name, first_seen=ts, last_seen=ts)
            st.last_seen = ts
            st.xyxy = d.xyxy
            st.positions.append((ts, d.foot_point))
            if len(st.positions) > 100:
                del st.positions[:-100]
            pi = plate_info.get(d.track_id)
            if pi:
                st.plate = pi.get("plate") or st.plate
                if pi.get("registered") is not None:
                    st.plate_registered = pi["registered"]
            if st.speed() > 5:
                st.was_moving = True

            if d.cls_name != "person":  # vehicles
                self._rule_a1(events, st, d, ts)
            else:
                self._rule_a2(events, st, d, ts)
                self._rule_a4(events, st, d, ts)

        self._rule_a3(events, detections, ts)
        self._expire_tracks(ts, events)
        return events

    # -------------------------------------------------------- A1: entry
    def _rule_a1(self, events, st: TrackState, d, ts: float):
        if not self._rule_enabled("unauthorized_vehicle"):
            return
        zone = self.zones.get("entry")
        in_zone = point_in_polygon(d.foot_point, zone)
        if not in_zone:
            return
        if st.entry_zone_since is None:
            st.entry_zone_since = ts
        if st.plate is not None:
            if st.plate_registered:
                return  # silence is a feature
            self._emit(events, ts, UNAUTHORIZED_VEHICLE,
                       f"Vehicle {d.cls_name} #{d.track_id} entered with "
                       f"unregistered plate {st.plate}",
                       [d.track_id], plate=st.plate, confidence=0.9,
                       debounce_key=(d.track_id, UNAUTHORIZED_VEHICLE),
                       registered=False)
        else:
            timeout = float(self.cfg.get("unauthorized_vehicle", {})
                            .get("plate_read_timeout_s", 5))
            if ts - st.entry_zone_since >= timeout:
                self._emit(events, ts, UNIDENTIFIED_VEHICLE,
                           f"Vehicle {d.cls_name} #{d.track_id} entered; plate "
                           f"could not be read within {timeout:.0f}s",
                           [d.track_id], confidence=0.5,
                           debounce_key=(d.track_id, UNIDENTIFIED_VEHICLE))

    # ---------------------------------------------------- A2: loitering
    def _rule_a2(self, events, st: TrackState, d, ts: float):
        if not self._rule_enabled("loitering"):
            return
        zone = self.zones.get("parking")
        cfg = self.cfg.get("loitering", {})
        # If a parking zone is drawn, use it. Otherwise fall back to "lingering
        # near any vehicle" so uploaded clips flag the person with no zone setup.
        if zone and len(zone) >= 3:
            in_region = point_in_polygon(d.foot_point, zone)
        else:
            in_region = self._near_any_vehicle(d.xyxy, cfg)
        if not in_region:
            st.parking_zone_since = None
            st.parking_entry_pos = None
            st.max_parking_displacement = 0.0
            return
        if st.parking_zone_since is None:
            st.parking_zone_since = ts
            st.parking_entry_pos = d.foot_point
            st.max_parking_displacement = 0.0
        st.max_parking_displacement = max(
            st.max_parking_displacement, dist(d.foot_point, st.parking_entry_pos))
        dwell_needed = float(cfg.get("night_dwell_s", 20)) if self._night() \
            else float(cfg.get("dwell_s", 45))
        dwell = ts - st.parking_zone_since
        if dwell >= dwell_needed and \
                st.max_parking_displacement < float(cfg.get("max_displacement_px", 120)):
            self._emit(events, ts, LOITERING,
                       f"Person #{d.track_id} loitering in parking zone for "
                       f"{dwell:.0f}s (moved {st.max_parking_displacement:.0f}px)",
                       [d.track_id], confidence=0.7,
                       debounce_key=(d.track_id, LOITERING),
                       registered=self._nearby_vehicle_registered(d.xyxy, cfg))

    def _nearby_vehicle_registered(self, person_xyxy, cfg) -> bool | None:
        """Is the vehicle this person is standing at one the society knows?

        None when no plate has been read — the scorer treats that as "no
        information" rather than guessing either way.
        """
        radius = float(cfg.get("near_vehicle_px", 160))
        px = ((person_xyxy[0] + person_xyxy[2]) / 2, person_xyxy[3])
        best, best_d = None, radius
        for st in self.tracks.values():
            if st.plate is None or not st.positions:
                continue
            d = dist(px, st.positions[-1][1])
            if d <= best_d:
                best, best_d = st, d
        return None if best is None else bool(best.plate_registered)

    def _near_any_vehicle(self, person_xyxy, cfg) -> bool:
        """True if the person's box overlaps any vehicle box expanded by
        `near_vehicle_px` — the zone-free loitering region."""
        px = float(cfg.get("near_vehicle_px", 150))
        for v in getattr(self, "_frame_vehicles", []):
            expanded = (v[0] - px, v[1] - px, v[2] + px, v[3] + px)
            if iou(person_xyxy, expanded) > 0:
                return True
        return False

    # ---------------------------------------- A3: possible vehicle contact
    def _rule_a3(self, events, detections, ts: float):
        if not self._rule_enabled("vehicle_contact"):
            return
        cfg = self.cfg.get("vehicle_contact", {})
        thr = float(cfg.get("iou_threshold", 0.02))
        window = float(cfg.get("depart_window_s", 15))
        depart_speed = float(cfg.get("depart_speed_px_s", 60))

        vehicles = [d for d in detections if d.cls_name != "person"]
        # register touching pairs (require at least one to be/have been moving)
        for i in range(len(vehicles)):
            for j in range(i + 1, len(vehicles)):
                a, b = vehicles[i], vehicles[j]
                pair = tuple(sorted((a.track_id, b.track_id)))
                if pair in self._contact_fired:
                    continue
                sa = self.tracks.get(a.track_id)
                sb = self.tracks.get(b.track_id)
                moving = (sa and sa.speed() > 5) or (sb and sb.speed() > 5)
                if iou(a.xyxy, b.xyxy) > thr and moving:
                    self._contacts.setdefault(pair, ts)

        # check departures within the window
        for pair, contact_ts in list(self._contacts.items()):
            if ts - contact_ts > window:
                del self._contacts[pair]
                continue
            for tid in pair:
                st = self.tracks.get(tid)
                departed = st is None  # track vanished (left the scene)
                fast = st is not None and st.speed() > depart_speed
                if departed or fast:
                    other = pair[0] if pair[1] == tid else pair[1]
                    plates = [p for p in
                              ((self.tracks.get(t).plate if self.tracks.get(t) else None)
                               for t in pair) if p]
                    self._contact_fired.add(pair)
                    del self._contacts[pair]
                    self._emit(events, ts, VEHICLE_CONTACT,
                               f"Possible contact between vehicles #{pair[0]} and "
                               f"#{pair[1]}; vehicle #{tid} departed"
                               + (f" (plates: {', '.join(plates)})" if plates else ""),
                               list(pair),
                               plate=plates[0] if plates else None,
                               confidence=0.4,
                               debounce_key=(pair, VEHICLE_CONTACT),
                               registered=_all_registered(
                                   self.tracks.get(t) for t in pair))
                    break

    # --------------------------------------------- A4: restricted at night
    def _rule_a4(self, events, st: TrackState, d, ts: float):
        if not self._rule_enabled("restricted_zone"):
            return
        if not self._night():
            return
        if point_in_polygon(d.foot_point, self.zones.get("restricted")):
            self._emit(events, ts, RESTRICTED_ZONE,
                       f"Person #{d.track_id} in restricted zone during night hours",
                       [d.track_id], confidence=0.8,
                       debounce_key=(d.track_id, RESTRICTED_ZONE))

    # ------------------------------------------------------- A5: tamper
    def update_frame_stats(self, mean_brightness: float, laplacian_var: float,
                           ts: float | None = None) -> list[Event]:
        if not self._rule_enabled("tamper"):
            return []
        ts = ts if ts is not None else self.now_fn()
        cfg = self.cfg.get("tamper", {})
        bad = (mean_brightness < float(cfg.get("dark_threshold", 12))
               or mean_brightness > float(cfg.get("bright_threshold", 243))
               or laplacian_var < float(cfg.get("blur_threshold", 12.0)))
        events: list[Event] = []
        if bad:
            if self._tamper_since is None:
                self._tamper_since = ts
            if ts - self._tamper_since >= float(cfg.get("condition_hold_s", 5)):
                self._emit(events, ts, CAMERA_TAMPER,
                           f"Frame near-black/near-white/blurred for "
                           f"{ts - self._tamper_since:.0f}s "
                           f"(brightness={mean_brightness:.0f}, blur={laplacian_var:.1f})",
                           [], confidence=0.8,
                           debounce_key=(self.camera, CAMERA_TAMPER))
        else:
            self._tamper_since = None
        return events

    def update_stream_status(self, online: bool, offline_since: float | None,
                             ts: float | None = None) -> list[Event]:
        if not self._rule_enabled("tamper"):
            return []
        ts = ts if ts is not None else self.now_fn()
        cfg = self.cfg.get("tamper", {})
        events: list[Event] = []
        if online:
            self._offline_fired = False
            return events
        if offline_since is None:
            return events
        if not self._offline_fired and \
                ts - offline_since >= float(cfg.get("offline_alert_s", 30)):
            self._offline_fired = True
            self._emit(events, ts, CAMERA_OFFLINE,
                       f"Stream down for {ts - offline_since:.0f}s and not reconnecting",
                       [], confidence=1.0,
                       debounce_key=(self.camera, CAMERA_OFFLINE))
        return events

    # ----------------------------------------------------------- expiry
    def _expire_tracks(self, ts: float, events: list):
        for tid, st in list(self.tracks.items()):
            if ts - st.last_seen > TRACK_EXPIRY_S:
                del self.tracks[tid]
        # drop stale contact pairs referencing dead tracks beyond the window
        window = float(self.cfg.get("vehicle_contact", {}).get("depart_window_s", 15))
        for pair, cts in list(self._contacts.items()):
            if ts - cts > window:
                del self._contacts[pair]
        # drop expired culprit flags
        for tid, exp in list(self._flagged.items()):
            if exp < ts:
                del self._flagged[tid]
