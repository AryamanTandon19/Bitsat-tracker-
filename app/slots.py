"""Parking-slot occupancy — who is parked where, and who just left.

A society already knows something a generic camera system never does: which
car belongs in which space. Once a slot is drawn and assigned, two things fall
out almost for free, and neither needs a model or a token:

  * **Your car left its spot.** The owner gets told the moment their vehicle
    moves, with the time. If they moved it, they ignore the message. If they
    did not, they have found out in seconds rather than in the morning. This
    is the feature that sells the system to residents rather than to a
    committee, and it costs one polygon test per vehicle per frame.

  * **Someone else is in your space.** A vehicle sitting in an assigned slot
    that is not the assigned vehicle is worth one calm message, not an alarm —
    nine times in ten it is a guest who parked badly.

The hard part is not the geometry, it is not crying wolf. A tracker drops a
parked car for a few frames all the time — a person walks in front of it, the
exposure shifts, YOLO has a bad moment. Announcing "your car has left" every
time that happens would get the notifications muted in a day, so nothing is
believed until it has held for a configured number of seconds.

Pure logic, no camera and no database: every branch is unit-testable.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

OCCUPIED = "occupied"
VACATED = "vacated"
INTRUDER = "intruder"


def point_in_polygon(pt, poly) -> bool:
    """Ray casting. Duplicated from rules.py on purpose — this module stays
    importable without pulling the rules engine in."""
    if not poly or len(poly) < 3:
        return False
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xin = (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-9) + x1
            if x < xin:
                inside = not inside
    return inside


@dataclass
class Slot:
    id: int
    camera: str
    label: str                      # "B-12", "Visitor 3"
    polygon: list                   # [[x, y], ...] in source-frame pixels
    plate: str | None = None        # the vehicle that belongs here
    flat_number: str | None = None
    owner_name: str | None = None

    @property
    def assigned(self) -> bool:
        return bool(self.plate)


@dataclass
class SlotChange:
    kind: str                       # occupied | vacated | intruder
    slot: Slot
    plate: str | None
    ts: float

    def message(self) -> str:
        where = f"{self.slot.label}"
        if self.kind == VACATED:
            plate = self.plate or "The vehicle"
            return f"{plate} left {where}"
        if self.kind == INTRUDER:
            plate = self.plate or "An unidentified vehicle"
            return f"{plate} is parked in {where}, which is not its space"
        return f"{self.plate or 'A vehicle'} parked in {where}"


@dataclass
class _State:
    """What we currently believe about one slot, and what we are waiting on."""
    plate: str | None = None            # confirmed occupant
    since: float = 0.0
    pending_plate: str | None = None    # candidate, not yet held long enough
    # None, not 0.0: a first sighting at ts=0 is falsy and would reset its own
    # timer forever, so the slot could never fill on a clock that starts at zero
    pending_since: float | None = None
    empty_since: float | None = None    # first frame it looked empty
    intruder_announced: bool = False


class SlotTracker:
    """One per camera. Feed it the vehicles it can see; it tells you what
    changed, once it is sure."""

    def __init__(self, slots: list[Slot], cfg: dict | None = None):
        cfg = cfg or {}
        self.slots = {s.id: s for s in slots}
        # A car has to sit still this long before we call the slot taken...
        self.occupy_s = float(cfg.get("occupy_confirm_s", 15))
        # ...and be gone this long before we tell anyone it left. This is the
        # number that decides whether the feature is trusted or muted.
        self.vacate_s = float(cfg.get("vacate_confirm_s", 25))
        self._state: dict[int, _State] = {s.id: _State() for s in slots}

    # ------------------------------------------------------------------
    def occupant(self, slot_id: int) -> str | None:
        st = self._state.get(slot_id)
        return st.plate if st else None

    def occupancy(self) -> dict[int, str | None]:
        return {sid: st.plate for sid, st in self._state.items()}

    def _slot_for(self, foot_point) -> Slot | None:
        for slot in self.slots.values():
            if point_in_polygon(foot_point, slot.polygon):
                return slot
        return None

    def update(self, vehicles, plate_info: dict | None = None,
               ts: float | None = None) -> list[SlotChange]:
        """vehicles: objects with .track_id and .foot_point (duck-typed).
        plate_info: track_id -> {"plate": str|None}. Returns confirmed changes.
        """
        ts = time.time() if ts is None else ts
        plate_info = plate_info or {}
        changes: list[SlotChange] = []

        # what is standing in each slot right now, this frame
        seen: dict[int, str | None] = {}
        for v in vehicles:
            slot = self._slot_for(v.foot_point)
            if slot is None:
                continue
            plate = (plate_info.get(v.track_id) or {}).get("plate")
            # a slot holds one car; first seen wins, but a readable plate beats
            # an unreadable one
            if slot.id not in seen or (plate and not seen[slot.id]):
                seen[slot.id] = plate

        for sid, slot in self.slots.items():
            st = self._state[sid]
            here = sid in seen
            plate = seen.get(sid)

            if here:
                st.empty_since = None
                if st.plate is not None and (plate is None or plate == st.plate):
                    # same car still there (or a frame where the plate was
                    # unreadable) — nothing to say
                    st.pending_plate, st.pending_since = None, None
                    continue
                # a new candidate occupant
                if st.pending_plate != plate or st.pending_since is None:
                    st.pending_plate, st.pending_since = plate, ts
                elif ts - st.pending_since >= self.occupy_s:
                    st.plate, st.since = plate, ts
                    st.pending_plate, st.pending_since = None, None
                    st.intruder_announced = False
                    changes.append(SlotChange(OCCUPIED, slot, plate, ts))
                    if slot.assigned and plate and plate != slot.plate:
                        st.intruder_announced = True
                        changes.append(SlotChange(INTRUDER, slot, plate, ts))
            else:
                st.pending_plate, st.pending_since = None, None
                if st.plate is None:
                    continue
                if st.empty_since is None:
                    st.empty_since = ts          # might just be a dropped frame
                elif ts - st.empty_since >= self.vacate_s:
                    left = st.plate
                    st.plate, st.since, st.empty_since = None, 0.0, None
                    st.intruder_announced = False
                    changes.append(SlotChange(VACATED, slot, left, ts))
        return changes

    def prime(self, vehicles, plate_info: dict | None = None,
              ts: float | None = None):
        """Adopt what is currently parked as the starting state, silently.

        Called on startup: without it, every car already sitting in its own
        space at boot would be announced as having just arrived.
        """
        ts = time.time() if ts is None else ts
        plate_info = plate_info or {}
        for v in vehicles:
            slot = self._slot_for(v.foot_point)
            if slot is None:
                continue
            st = self._state[slot.id]
            st.plate = (plate_info.get(v.track_id) or {}).get("plate")
            st.since, st.empty_since = ts, None
