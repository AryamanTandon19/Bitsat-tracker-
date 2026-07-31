"""Damage lookup — "who hit my car while it was parked in B-12 last Tuesday?"

This is the question a security system gets asked after the fact, and the one
almost none of them can answer. A resident finds a scrape at 9am. The footage
exists, but it is eleven hours of it across four cameras, and nobody is going
to watch that, so the dispute ends in a shrug.

The slot map turns it into a narrow search. The register already knows the car
was in B-12 from 19:40 until 07:15, and which camera watches B-12 — so the
answer can only be inside that window, on that camera. Everything else is
noise and can be discarded before a human looks at anything.

What comes back is ranked, not filtered: the resident is told what the system
noticed and how confident it is, and decides for themselves. A tool that
quietly drops the one clip that mattered is worse than one that returns twelve
and puts the likeliest first.

Ranking is deliberately explainable, for the same reason the scoring layer is:
this evidence may end up in an argument between neighbours, and "the computer
said so" does not survive that conversation.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Event types that could plausibly mark a vehicle. A person merely walking past
# is not evidence of anything, so it does not appear here.
CONTACT_TYPES = {
    "vehicle_contact": 1.00,      # geometry saw two vehicles touch
    "suspicious_activity": 0.55,  # the trigger fired near the car
    "loitering": 0.45,            # someone stayed beside it
    "unauthorized_vehicle": 0.35, # an unknown car was moving nearby
    "restricted_zone": 0.30,
}


@dataclass
class Window:
    """A period the vehicle is known to have been parked in one slot."""
    slot_id: int
    label: str
    camera: str
    plate: str | None
    start: float
    end: float | None            # None = still parked

    def covers(self, ts: float) -> bool:
        return ts >= self.start and (self.end is None or ts <= self.end)


@dataclass
class Candidate:
    event: dict
    relevance: float
    reasons: list[str] = field(default_factory=list)

    def explain(self) -> str:
        return "; ".join(self.reasons) or "happened while the car was parked"


def rank_events(events: list[dict], window: Window) -> list[Candidate]:
    """Order what happened during a parked window by how likely it is to be
    the thing that caused the damage."""
    out: list[Candidate] = []
    for ev in events:
        if not window.covers(ev["ts"]):
            continue
        base = CONTACT_TYPES.get(ev["event_type"])
        if base is None:
            continue
        reasons = [f"{ev['event_type'].replace('_', ' ')} on {ev['camera']}"]
        rel = base

        # The scoring layer already judged how much the situation stood out;
        # reuse that rather than inventing a second opinion.
        score = float(ev.get("score") or 0)
        if score:
            rel += 0.30 * score
            reasons.append(f"threat score {score:.2f}")
        if (ev.get("severity") or "").upper() == "HIGH":
            rel += 0.15
            reasons.append("raised as HIGH at the time")

        # Footage a person can actually look at is worth more than a log line.
        if ev.get("clip_id") and not ev.get("clip_deleted"):
            rel += 0.20
            reasons.append("clip available")
        if ev.get("ai_summary"):
            reasons.append(f"AI: {ev['ai_summary']}")

        out.append(Candidate(ev, round(min(2.0, rel), 3), reasons))

    out.sort(key=lambda c: (-c.relevance, -c.event["ts"]))
    return out


def search(db, plate: str | None = None, slot_id: int | None = None,
           since: float | None = None, until: float | None = None,
           limit: int = 20) -> dict:
    """Find what could have marked a vehicle, over the periods it was parked.

    Returns the windows searched as well as the candidates: "we looked at
    19:40-07:15 on the parking camera and found nothing" is a real answer, and
    a far more useful one than an empty list with no explanation.
    """
    windows = db.slot_windows(plate=plate, slot_id=slot_id,
                              since=since, until=until)
    results: list[Candidate] = []
    for w in windows:
        events = db.events_between(w.camera, w.start,
                                   w.end if w.end is not None else (until or 0)
                                   or db.now())
        results.extend(rank_events(events, w))

    results.sort(key=lambda c: (-c.relevance, -c.event["ts"]))
    return {
        "windows": [{"slot_id": w.slot_id, "label": w.label, "camera": w.camera,
                     "plate": w.plate, "start": w.start, "end": w.end}
                    for w in windows],
        "candidates": [{"relevance": c.relevance, "why": c.explain(),
                        **{k: c.event.get(k) for k in
                           ("id", "ts", "camera", "event_type", "severity",
                            "description", "score", "clip_id", "clip_deleted",
                            "ai_summary")}}
                       for c in results[:limit]],
    }
