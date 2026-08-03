"""Rising-edge incident gating — one alert per incident, not one per event.

The live path already groups events into incidents (`db.insert_event` threads
events on a camera within a window onto one `incident_id`) and the notifier
already labels follow-ups "UPDATE — same incident". What neither of them does
is *stop sending*. A sustained break-in emits an event every few seconds, and
every one of them becomes a Telegram message, until
`max_notifications_per_hour` cuts the camera off entirely for the rest of the
hour.

That last part is the real danger, and it is worth stating plainly: the crude
hourly cap is the only thing preventing a flood, and when it fires it silences
*everything* on that camera — including the escalation from MEDIUM to HIGH that
is the one message somebody actually needed. A rate limit that suppresses the
most important alert because thirty unimportant ones came first is worse than
no rate limit.

So this module decides, per event, whether it deserves to interrupt a person:

    open       first event of a new incident            -> ALERT
    sustain    more of the same, no worse               -> record, stay quiet
    escalate   it got meaningfully worse than its peak  -> ALERT
    remind     still going after a long time            -> ALERT (rarely)
    reopen     quiet long enough that this is new        -> ALERT

The design rule is that quiet must never lose information: escalation always
gets through, and everything suppressed is still written to the database and
visible in the console. What is suppressed is the *interruption*, not the
record.

Grouping is by camera and time, deliberately not by track id. A tracker
routinely loses and re-acquires a person — that is measured behaviour, not a
hypothetical — and keying on track id means one person walking behind a van
becomes two incidents and two alerts. Merging two genuinely separate incidents
on one camera into a single alert is the safer error: the operator gets one
notification listing both, instead of being taught that the app cries wolf.

Pure Python: no torch, no cv2, no database. Fully unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Bigger = more severe. NOTE this is the opposite direction from
# `analyze._SEV_RANK`, which sorts ascending so `min()` picks the worst. Both
# are internally consistent; keep them straight when reading across modules.
SEVERITY_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}

OPEN = "open"
SUSTAIN = "sustain"
ESCALATE = "escalate"
REMIND = "remind"
REOPEN = "reopen"
CLOSED = "closed"


def severity_rank(severity: str) -> int:
    """Unknown severities rank as MEDIUM rather than 0.

    An event type nobody has classified yet must not be silently treated as
    the least important thing on the camera.
    """
    return SEVERITY_RANK.get(str(severity).upper(), 2)


@dataclass
class Decision:
    """Why this event did or did not interrupt somebody."""
    action: str
    notify: bool
    camera: str
    reason: str
    incident_events: int = 1
    peak_severity: str = ""
    peak_score: float = 0.0
    age_s: float = 0.0
    suppressed_since_alert: int = 0

    def public(self) -> dict:
        return {"action": self.action, "notify": self.notify,
                "camera": self.camera, "reason": self.reason,
                "incident_events": self.incident_events,
                "peak_severity": self.peak_severity,
                "peak_score": round(self.peak_score, 3),
                "age_s": round(self.age_s, 1),
                "suppressed_since_alert": self.suppressed_since_alert}


@dataclass
class Incident:
    """One continuing situation on one camera."""
    camera: str
    opened_ts: float
    last_ts: float
    peak_severity: str
    peak_score: float
    events: int = 1
    alerts: int = 1
    last_alert_ts: float = 0.0
    suppressed_since_alert: int = 0
    track_ids: set = field(default_factory=set)
    event_types: set = field(default_factory=set)

    @property
    def duration_s(self) -> float:
        return max(0.0, self.last_ts - self.opened_ts)

    def summary(self) -> dict:
        return {"camera": self.camera, "opened_ts": self.opened_ts,
                "last_ts": self.last_ts, "duration_s": round(self.duration_s, 1),
                "events": self.events, "alerts": self.alerts,
                "peak_severity": self.peak_severity,
                "peak_score": round(self.peak_score, 3),
                "track_ids": sorted(self.track_ids),
                "event_types": sorted(self.event_types)}


class IncidentGate:
    """Per-camera rising-edge state machine.

    One instance for the whole application; cameras are isolated by key, so a
    busy gate camera can never suppress a quiet parking one.
    """

    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        # Quiet for this long and the next event is a NEW incident. Wants to be
        # comfortably longer than the gap the tracker leaves when it loses
        # somebody behind a vehicle, or one person becomes two incidents.
        self.cooldown_s = float(cfg.get("cooldown_s", 60.0))
        # A still-running incident may interrupt again this rarely, so a
        # genuinely long event is not forgotten after its first alert.
        self.remind_after_s = float(cfg.get("remind_after_s", 900.0))
        # How much the 0..1 score must rise above the incident's peak to count
        # as "meaningfully worse". Small jitter is not an escalation.
        self.escalate_score_delta = float(cfg.get("escalate_score_delta", 0.15))
        # Infrastructure alerts that must always get through: a blinded or
        # offline camera is not "more of the same incident", it is the reason
        # the operator can no longer see the incident at all.
        self.always_notify = set(cfg.get("always_notify") or
                                 ("camera_tamper", "camera_offline"))
        self.enabled = bool(cfg.get("enabled", True))
        self._open: dict[str, Incident] = {}
        self.closed: list[Incident] = []

    # ------------------------------------------------------------------
    def observe(self, camera: str, ts: float, severity: str,
                event_type: str = "", score: float = 0.0,
                track_ids=None) -> Decision:
        """Decide what this event should do. Call once per event, in order."""
        track_ids = set(track_ids or ())
        rank = severity_rank(severity)

        if not self.enabled:
            return Decision(OPEN, True, camera,
                            "incident gating is off — every event alerts",
                            peak_severity=severity, peak_score=score)

        if event_type in self.always_notify:
            # Recorded on the incident so the operator sees it in context, but
            # never suppressed.
            inc = self._open.get(camera)
            if inc is not None:
                self._absorb(inc, ts, rank, severity, score, event_type,
                             track_ids)
                inc.alerts += 1
                inc.last_alert_ts = ts
                inc.suppressed_since_alert = 0
            return Decision(ESCALATE, True, camera,
                            f"{event_type} always reaches the operator",
                            incident_events=inc.events if inc else 1,
                            peak_severity=inc.peak_severity if inc else severity,
                            peak_score=inc.peak_score if inc else score,
                            age_s=inc.duration_s if inc else 0.0)

        inc = self._open.get(camera)

        # ---- nothing open, or the last one went quiet long enough ----------
        if inc is None or (ts - inc.last_ts) > self.cooldown_s:
            reopened = inc is not None
            if reopened:
                self._close(camera, ts)
            self._open[camera] = Incident(
                camera=camera, opened_ts=ts, last_ts=ts,
                peak_severity=severity, peak_score=float(score),
                last_alert_ts=ts, track_ids=set(track_ids),
                event_types={event_type} if event_type else set())
            return Decision(
                REOPEN if reopened else OPEN, True, camera,
                ("nothing on this camera for "
                 f"{self.cooldown_s:.0f}s, so this is a new incident"
                 if reopened else "first event of a new incident"),
                peak_severity=severity, peak_score=float(score))

        # ---- something is already open -------------------------------------
        was_rank = severity_rank(inc.peak_severity)
        was_score = inc.peak_score
        self._absorb(inc, ts, rank, severity, score, event_type, track_ids)

        if rank > was_rank:
            inc.alerts += 1
            inc.last_alert_ts = ts
            n = inc.suppressed_since_alert
            inc.suppressed_since_alert = 0
            return Decision(
                ESCALATE, True, camera,
                f"severity rose from {inc_sev(was_rank)} to {severity.upper()}",
                incident_events=inc.events, peak_severity=inc.peak_severity,
                peak_score=inc.peak_score, age_s=inc.duration_s,
                suppressed_since_alert=n)

        if score - was_score >= self.escalate_score_delta:
            inc.alerts += 1
            inc.last_alert_ts = ts
            n = inc.suppressed_since_alert
            inc.suppressed_since_alert = 0
            return Decision(
                ESCALATE, True, camera,
                f"score rose {was_score:.2f} -> {score:.2f}",
                incident_events=inc.events, peak_severity=inc.peak_severity,
                peak_score=inc.peak_score, age_s=inc.duration_s,
                suppressed_since_alert=n)

        if ts - inc.last_alert_ts >= self.remind_after_s:
            inc.alerts += 1
            inc.last_alert_ts = ts
            n = inc.suppressed_since_alert
            inc.suppressed_since_alert = 0
            return Decision(
                REMIND, True, camera,
                f"still going after {inc.duration_s / 60:.0f} minutes",
                incident_events=inc.events, peak_severity=inc.peak_severity,
                peak_score=inc.peak_score, age_s=inc.duration_s,
                suppressed_since_alert=n)

        inc.suppressed_since_alert += 1
        return Decision(
            SUSTAIN, False, camera,
            "same incident, no worse than it already was — recorded, "
            "no second alert",
            incident_events=inc.events, peak_severity=inc.peak_severity,
            peak_score=inc.peak_score, age_s=inc.duration_s,
            suppressed_since_alert=inc.suppressed_since_alert)

    # ------------------------------------------------------------------
    def _absorb(self, inc: Incident, ts: float, rank: int, severity: str,
                score: float, event_type: str, track_ids: set) -> None:
        inc.last_ts = max(inc.last_ts, ts)
        inc.events += 1
        inc.track_ids |= track_ids
        if event_type:
            inc.event_types.add(event_type)
        if rank > severity_rank(inc.peak_severity):
            inc.peak_severity = str(severity).upper()
        inc.peak_score = max(inc.peak_score, float(score))

    def _close(self, camera: str, ts: float) -> Incident | None:
        inc = self._open.pop(camera, None)
        if inc is not None:
            self.closed.append(inc)
            # a workbench, not a log store: keep the tail, drop the rest
            if len(self.closed) > 200:
                del self.closed[:-200]
        return inc

    def tick(self, now: float) -> list:
        """Close incidents that have gone quiet. Returns the ones just closed.

        Call periodically. Without it an incident stays open until the next
        event arrives, so "it ended" is never noticed on a camera that goes
        silent — which is exactly what happens when the incident is over.
        """
        done = []
        for camera, inc in list(self._open.items()):
            if now - inc.last_ts > self.cooldown_s:
                closed = self._close(camera, now)
                if closed is not None:
                    done.append(closed)
        return done

    # ------------------------------------------------------------------
    def open_incidents(self) -> list:
        return [i.summary() for i in self._open.values()]

    def stats(self) -> dict:
        """What the gate has actually saved, so the effect is measurable."""
        alerts = sum(i.alerts for i in self._open.values()) \
            + sum(i.alerts for i in self.closed)
        events = sum(i.events for i in self._open.values()) \
            + sum(i.events for i in self.closed)
        return {"open": len(self._open), "closed": len(self.closed),
                "events": events, "alerts": alerts,
                "suppressed": max(0, events - alerts),
                "alerts_per_incident": round(
                    alerts / max(1, len(self._open) + len(self.closed)), 2)}

    def reset(self, camera: str | None = None) -> None:
        """Forget state — for a camera reconnect, or a scene change."""
        if camera is None:
            self._open.clear()
        else:
            self._open.pop(camera, None)


def inc_sev(rank: int) -> str:
    for name, r in SEVERITY_RANK.items():
        if r == rank:
            return name
    return "MEDIUM"
