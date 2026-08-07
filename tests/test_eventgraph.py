"""The event graph: a track's story, read as a sequence.

The point is that ordinary sub-events in a meaningful order become a named
event — and that a person merely walking past never does.
"""
from __future__ import annotations

from app import eventgraph as G
from app.eventgraph import EventGraph


def _walk(graph, tid, steps):
    """steps: list of (ts, kinds). Replays them into the graph."""
    for ts, kinds in steps:
        graph.observe(tid, ts, kinds)


# ------------------------------------------------------------- recording
def test_a_burst_of_one_state_is_thinned_but_keeps_its_span():
    g = EventGraph(refractory_s=2.0)
    for ts in range(0, 8):                       # 'near' every second for 8s
        g.observe(1, float(ts), {G.NEAR_VEHICLE})
    kinds = [k for _, k in g.timeline(1)]
    assert kinds.count(G.NEAR_VEHICLE) < 8       # thinned
    assert g.dwell_seconds(1, now=7.5) >= 5      # but the run is still ~7s


def test_a_gap_breaks_the_dwell_run():
    g = EventGraph(refractory_s=2.0, gap_tol_s=4.0)
    g.observe(1, 0.0, {G.NEAR_VEHICLE})
    g.observe(1, 2.0, {G.NEAR_VEHICLE})          # first visit: 0-2s
    g.observe(1, 30.0, {G.NEAR_VEHICLE})         # came back much later
    g.observe(1, 32.0, {G.NEAR_VEHICLE})
    assert g.dwell_seconds(1, now=32.5) < 5      # only this visit counts


# ------------------------------------------------------------- sequences
def test_ordered_subsequence_matching():
    g = EventGraph()
    _walk(g, 1, [(0.0, {G.IN_ENTRY}), (1.0, {G.NEAR_VEHICLE}),
                 (2.0, {G.HAND_INTERACTION})])
    assert g.has_sequence(1, [G.IN_ENTRY, G.HAND_INTERACTION])
    assert not g.has_sequence(1, [G.HAND_INTERACTION, G.IN_ENTRY])   # wrong order
    assert g.has_sequence(1, [G.IN_ENTRY, G.NEAR_VEHICLE], within_s=5)
    assert not g.has_sequence(1, [G.IN_ENTRY, G.HAND_INTERACTION], within_s=1)


# --------------------------------------------------------------- derive
def test_a_passer_by_produces_no_event():
    g = EventGraph(dwell_s=5.0)
    g.observe(1, 0.0, {G.NEAR_VEHICLE})          # near for a moment, then gone
    g.observe(1, 1.0, {G.NEAR_VEHICLE})
    assert g.derive(1, now=1.5) is None


def test_approach_linger_interact_is_vehicle_tampering():
    g = EventGraph(dwell_s=5.0)
    for ts in range(0, 9):
        g.observe(1, float(ts), {G.NEAR_VEHICLE})
    g.observe(1, 8.0, {G.AT_VEHICLE, G.HAND_INTERACTION})
    ev = g.derive(1, now=9.0)
    assert ev is not None and ev.chain == "vehicle_tampering_sequence"
    assert any("approached" in r for r in ev.reasons)
    assert any("stayed" in r for r in ev.reasons)


def test_entry_disturbance_restricted_is_forced_entry():
    g = EventGraph(dwell_s=5.0)
    _walk(g, 1, [(0.0, {G.IN_ENTRY, G.NEAR_VEHICLE}),
                 (3.0, {G.SURFACE_CHANGE}),
                 (5.0, {G.IN_RESTRICTED})])
    ev = g.derive(1, now=5.5)
    assert ev is not None and ev.chain == "forced_entry_sequence"
    assert ev.severity == "HIGH"


def test_forced_entry_outranks_tampering_when_both_apply():
    g = EventGraph(dwell_s=5.0)
    for ts in range(0, 7):
        g.observe(1, float(ts), {G.NEAR_VEHICLE})
    g.observe(1, 2.0, {G.IN_ENTRY})
    g.observe(1, 4.0, {G.BREAK_IN})
    g.observe(1, 6.0, {G.IN_RESTRICTED})
    assert g.derive(1, now=6.5).chain == "forced_entry_sequence"


def test_the_derived_event_explains_itself_in_order():
    g = EventGraph(dwell_s=5.0)
    for ts in range(0, 8):
        g.observe(1, float(ts), {G.NEAR_VEHICLE})
    g.observe(1, 7.0, {G.CROUCHING})
    ev = g.derive(1, now=8.0)
    assert ev.summary().startswith("vehicle_tampering_sequence:")


# ------------------------------------------------------------- lifecycle
def test_quiet_tracks_are_pruned():
    g = EventGraph(memory_s=60.0)
    g.observe(1, 0.0, {G.NEAR_VEHICLE})
    g.observe(2, 100.0, {G.NEAR_VEHICLE})        # 'now' is ~100
    g.prune(now=100.0)
    assert g.timeline(1) == [] and g.timeline(2)


# ------------------------------------------------- signal mapping
def test_signals_map_to_graph_kinds():
    kinds = G.sub_events_from_signals(
        zones_hit={"entry", "restricted"},
        reasons={"person_near_vehicle", "possible_break_in"},
        pose={"pose_crouching"}, motion_high=True)
    assert kinds == {G.IN_ENTRY, G.IN_RESTRICTED, G.NEAR_VEHICLE, G.BREAK_IN,
                     G.CROUCHING, G.HIGH_MOTION}


def test_unknown_signals_are_ignored():
    assert G.sub_events_from_signals(reasons={"something_else"}) == set()


# --------------------------------------------- the pipeline wiring
def test_pipeline_update_derives_a_sequence_over_time():
    """Replay frames through CameraPipeline._update_event_graph exactly as the
    live loop would, and confirm a real sequence surfaces."""
    import types

    from app.detector import Detection
    from app.main import CameraPipeline

    pipe = types.SimpleNamespace(
        event_graph=EventGraph(dwell_s=5.0, refractory_s=2.0),
        zones={}, trigger=types.SimpleNamespace(last_involved={1}),
        _graph_pruned=0.0, _last_graph_event=None)
    update = CameraPipeline._update_event_graph.__get__(pipe)
    person = Detection(track_id=1, cls_name="person", conf=0.9,
                       xyxy=(10, 10, 40, 100))

    chain = None
    for t in range(0, 9):                        # near the vehicle for ~8s
        chain = update([person], {"person_near_vehicle"}, {}, float(t))
    # then reach in
    chain = update([person], {"person_near_vehicle", "person_at_vehicle"},
                   {1: {"pose_reaching"}}, 9.0)
    assert chain == "vehicle_tampering_sequence"
    assert pipe._last_graph_event is not None
