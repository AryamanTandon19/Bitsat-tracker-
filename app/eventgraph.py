"""The event graph — a per-track timeline of observable sub-events.

The whole product principle is that crime is a *sequence*, not a single frame.
A break-in is "approached the car, stayed by the door, reached in, drove off" —
each step ordinary on its own, the order of them not. So instead of asking one
model "is this theft?", the system records what it can actually observe for each
tracked person…

    TRACK 17
      ├─ enters entry zone      t=0.0
      ├─ approaches vehicle      t=2.1
      ├─ stayed 6s               t=8.0
      ├─ hand interaction        t=8.4
      └─ high motion             t=9.0

…and lets rules read that history. A sequence like *near a vehicle → lingered →
interacted* becomes `vehicle_tampering_sequence`; *entry zone → surface
disturbance → restricted interior* becomes `forced_entry_sequence`. These feed
`app/fusion.py` as a `state_chain`, which the fusion layer already treats as an
independent line of evidence — so a remembered sequence can corroborate a live
action, but (per the no-single-model rule) never confirms an incident alone.

Every node carries its timestamp, so the derived event is fully explainable:
"approached at 0:02, stayed 6s, reached in at 0:08" is checkable against the
video. Pure Python — no torch, no numpy — so it is entirely unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ---- the vocabulary of observable sub-events -----------------------------
IN_ENTRY = "in_entry"
IN_PARKING = "in_parking"
IN_RESTRICTED = "in_restricted"
NEAR_VEHICLE = "near_vehicle"
AT_VEHICLE = "at_vehicle"                 # touching / at the body of the car
HAND_INTERACTION = "hand_interaction"
HIGH_MOTION = "high_motion"
CROUCHING = "crouching"
REACHING = "reaching"
ARM_SWING = "arm_swing"
SURFACE_CHANGE = "surface_change"         # the car's surface/appearance disturbed
BREAK_IN = "break_in"
ENTERS_VEHICLE = "enters_vehicle"
DEPARTS = "departs"

# actions that "ground" a sequence — a real interaction with the scene, as
# opposed to merely being present
ACTIONS = {HAND_INTERACTION, HIGH_MOTION, CROUCHING, REACHING, ARM_SWING,
           SURFACE_CHANGE, BREAK_IN}

# a short, human phrase per kind, for the explanation a guard reads
PHRASES = {
    IN_ENTRY: "in the entry zone", IN_PARKING: "in the parking zone",
    IN_RESTRICTED: "in a restricted area", NEAR_VEHICLE: "approached a vehicle",
    AT_VEHICLE: "reached the vehicle", HAND_INTERACTION: "hand interaction",
    HIGH_MOTION: "sudden motion", CROUCHING: "crouched", REACHING: "reached in",
    ARM_SWING: "arm swing", SURFACE_CHANGE: "the car's surface was disturbed",
    BREAK_IN: "strike/reach at the car", ENTERS_VEHICLE: "entered the vehicle",
    DEPARTS: "the vehicle left",
}


@dataclass
class Node:
    ts: float
    kind: str
    data: dict = field(default_factory=dict)


@dataclass
class DerivedEvent:
    chain: str                    # the state-chain name for fusion
    reasons: list                 # human steps, in order
    severity: str                 # HIGH / MEDIUM — a hint, fusion still decides

    def summary(self) -> str:
        return f"{self.chain}: " + " → ".join(self.reasons)


class EventGraph:
    """Per-track timelines and the sequences derived from them.

    `refractory_s` collapses a burst of the same sub-event into one node (a
    person is "near the vehicle" on every frame, but that is one fact, not
    forty). `memory_s` is how long a track's history is kept once it goes quiet,
    so the graph does not grow without bound on a busy camera.
    """

    def __init__(self, refractory_s: float = 2.0, memory_s: float = 120.0,
                 dwell_s: float = 5.0, gap_tol_s: float = 4.0):
        self.refractory_s = float(refractory_s)
        self.memory_s = float(memory_s)
        self.dwell_s = float(dwell_s)
        self.gap_tol_s = float(gap_tol_s)
        self._tl: dict[int, list] = {}          # track_id -> [Node, ...]
        self._last_seen: dict[int, float] = {}

    # -- recording -----------------------------------------------------------
    def observe(self, track_id: int, ts: float, kinds, data: dict | None = None):
        """Record the sub-events seen for one track on one frame."""
        tid = int(track_id)
        self._last_seen[tid] = ts
        tl = self._tl.setdefault(tid, [])
        for kind in kinds:
            # thin a burst to one node per refractory window — a person is "near
            # the vehicle" on every frame, but recording a node every ~2s is
            # enough to keep the run's span while bounding the timeline. Crucially
            # the node timestamps are left untouched, so duration is preserved.
            recent = next((n for n in reversed(tl) if n.kind == kind), None)
            if recent is not None and ts - recent.ts < self.refractory_s:
                continue
            tl.append(Node(ts=float(ts), kind=str(kind), data=dict(data or {})))

    def prune(self, now: float) -> None:
        """Forget tracks that have gone quiet longer than the memory window."""
        gone = [t for t, seen in self._last_seen.items()
                if now - seen > self.memory_s]
        for t in gone:
            self.forget(t)

    def forget(self, track_id: int) -> None:
        self._tl.pop(int(track_id), None)
        self._last_seen.pop(int(track_id), None)

    # -- querying ------------------------------------------------------------
    def timeline(self, track_id: int) -> list:
        """(ts, kind) pairs in order — for the dashboard and for explanations."""
        return [(n.ts, n.kind) for n in self._tl.get(int(track_id), [])]

    def has(self, track_id: int, kind: str) -> bool:
        return any(n.kind == kind for n in self._tl.get(int(track_id), []))

    def has_any(self, track_id: int, kinds) -> bool:
        want = set(kinds)
        return any(n.kind in want for n in self._tl.get(int(track_id), []))

    def first_ts(self, track_id: int, kind: str) -> float | None:
        for n in self._tl.get(int(track_id), []):
            if n.kind == kind:
                return n.ts
        return None

    def has_sequence(self, track_id: int, kinds, within_s: float | None = None):
        """True if `kinds` appear in this order (not necessarily adjacent)."""
        nodes = self._tl.get(int(track_id), [])
        it = iter(nodes)
        stamps = []
        for k in kinds:
            for n in it:
                if n.kind == k:
                    stamps.append(n.ts)
                    break
            else:
                return False
        if within_s is not None and stamps:
            return (stamps[-1] - stamps[0]) <= within_s
        return True

    def dwell_seconds(self, track_id: int, now: float,
                      kinds=(NEAR_VEHICLE, AT_VEHICLE)) -> float:
        """Length of the most recent unbroken run of 'near a vehicle'.

        A run is broken by a gap longer than `gap_tol_s` (the person walked off
        and came back), so this measures *this* visit, not the whole history.
        """
        want = set(kinds)
        stamps = [n.ts for n in self._tl.get(int(track_id), []) if n.kind in want]
        if not stamps:
            return 0.0
        start = stamps[0]
        prev = stamps[0]
        for ts in stamps[1:]:
            if ts - prev > self.gap_tol_s:
                start = ts
            prev = ts
        end = now if now - prev <= self.gap_tol_s else prev
        return max(0.0, end - start)

    # -- the derived events (sequences that matter) --------------------------
    def derive(self, track_id: int, now: float) -> DerivedEvent | None:
        """Turn a track's history into at most one derived event.

        Conservative on purpose: a derived event needs a genuine multi-step
        story over several seconds, which is far stronger evidence than any one
        frame — and it is still only *one* line of evidence into fusion.
        """
        tid = int(track_id)
        if tid not in self._tl:
            return None
        near = self.has_any(tid, (NEAR_VEHICLE, AT_VEHICLE))
        dwelled = self.dwell_seconds(tid, now) >= self.dwell_s
        actions = [k for k in (BREAK_IN, SURFACE_CHANGE, HAND_INTERACTION,
                               HIGH_MOTION, REACHING, CROUCHING, ARM_SWING)
                   if self.has(tid, k)]

        # forced entry: got into a controlled zone, disturbed a surface, ended
        # up in the restricted interior — the strongest, so checked first
        if self.has(tid, IN_RESTRICTED) and \
                self.has_any(tid, (SURFACE_CHANGE, BREAK_IN, ARM_SWING)) and \
                (self.has(tid, IN_ENTRY) or near):
            return DerivedEvent(
                "forced_entry_sequence",
                self._steps(tid, [IN_ENTRY, NEAR_VEHICLE, SURFACE_CHANGE,
                                  BREAK_IN, ARM_SWING, IN_RESTRICTED]),
                "HIGH")

        # vehicle tampering: approached, lingered, then interacted with the car
        if near and dwelled and actions:
            return DerivedEvent(
                "vehicle_tampering_sequence",
                self._steps(tid, [NEAR_VEHICLE, AT_VEHICLE] + actions)
                + [f"stayed {self.dwell_seconds(tid, now):.0f}s"],
                "HIGH" if (BREAK_IN in actions or SURFACE_CHANGE in actions)
                else "MEDIUM")

        # loitering then any interaction — weaker, worth a look not an alarm
        if dwelled and actions:
            return DerivedEvent(
                "loiter_then_interaction",
                self._steps(tid, [NEAR_VEHICLE] + actions), "MEDIUM")
        return None

    def _steps(self, track_id: int, order) -> list:
        """Human phrases for the sub-events this track actually showed, in the
        order they are listed — the explanation behind a derived event."""
        present = {n.kind for n in self._tl.get(int(track_id), [])}
        out = []
        for kind in order:
            if kind in present and PHRASES.get(kind) and \
                    PHRASES[kind] not in out:
                out.append(PHRASES[kind])
        return out


def sub_events_from_signals(*, zones_hit=None, reasons=None, pose=None,
                            motion_high: bool = False) -> set:
    """Map the pipeline's existing per-frame signals to event-graph kinds.

    One place that knows how the free layer's vocabulary (`app/trigger.py`
    reasons, pose labels, zone names) becomes the graph's vocabulary, so the
    wiring in `app/main.py` stays a one-liner and this stays unit-testable.
    """
    out: set = set()
    zmap = {"entry": IN_ENTRY, "parking": IN_PARKING, "restricted": IN_RESTRICTED}
    for z in (zones_hit or ()):
        if z in zmap:
            out.add(zmap[z])
    # keys are the exact reason strings app/trigger.py emits
    rmap = {
        "person_near_vehicle": NEAR_VEHICLE, "person_at_vehicle": AT_VEHICLE,
        "vehicle_disturbance": SURFACE_CHANGE, "possible_break_in": BREAK_IN,
        "vehicle_departure_after_activity": DEPARTS,
    }
    for r in (reasons or ()):
        if r in rmap:
            out.add(rmap[r])
    pmap = {"pose_crouching": CROUCHING, "pose_reaching": REACHING,
            "pose_arm_swing": ARM_SWING, "crouching": CROUCHING,
            "reaching": REACHING, "arm_swing": ARM_SWING}
    for p in (pose or ()):
        if p in pmap:
            out.add(pmap[p])
    if motion_high:
        out.add(HIGH_MOTION)
    return out
