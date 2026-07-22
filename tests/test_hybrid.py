"""Hybrid security monitor — buffer/timing/routing/evidence, no torch.

A scripted fake bank drives the scoring path deterministically so the whole
monitor (except the real torch inference, which lives in SpecialistBank) is
exercised here."""
import numpy as np

from app.fusion import CONFIRMED_INCIDENT, fuse
from app.hybrid import (HybridSecurityMonitor, build_evidence,
                        route_from_reasons)
from app.temporal import ConfirmResult


def frame(v=100):
    return np.full((20, 24, 3), v, dtype=np.uint8)


class FakeBank:
    """Stand-in for SpecialistBank.score with scripted per-model outputs."""
    enabled = True

    def __init__(self, scripts=None):
        self.scripts = {k: list(v) for k, v in (scripts or {}).items()}
        self.calls = {"break_in": 0, "vehicle": 0}

    def warmup(self):
        return {"break_in": True, "vehicle": True}

    def score(self, frames, which, bgr=True):
        self.calls[which] += 1
        seq = self.scripts.get(which)
        if not seq:
            return None
        return seq.pop(0)


def monitor(enabled=True, bank=None, **spec):
    cfg = {"specialist": {"enabled": enabled, "min_clip_span_s": 0.0,
                          "infer_every_s": 0.0, **spec}}
    return HybridSecurityMonitor(cfg, "cam1", bank=bank)


def test_disabled_monitor_never_runs():
    m = monitor(enabled=False, bank=FakeBank({"vehicle": [0.99]}))
    obs = m.observe(frame(), ts=10.0)
    assert not obs.ran and not obs.scores


def test_span_gate_blocks_until_enough_history():
    # default 3.6s span requirement; two frames 1s apart must not score
    m = monitor(min_clip_span_s=3.6, infer_every_s=0.0,
                bank=FakeBank({"vehicle": [0.9, 0.9]}))
    assert not m.observe(frame(), ts=0.0).ran      # 1 frame
    assert not m.observe(frame(), ts=1.0).ran      # span 1.0 < 3.6


def test_first_frame_has_no_pair_to_score():
    m = monitor(bank=FakeBank({"vehicle": [0.9]}))
    assert not m.observe(frame(), ts=0.0).ran      # len(sel)==1


def test_inference_cadence_is_respected():
    m = monitor(infer_every_s=2.0, min_clip_span_s=0.0,
                bank=FakeBank({"vehicle": [0.5] * 10}))
    m.observe(frame(), ts=0.0)                      # warm (1 frame)
    r1 = m.observe(frame(), ts=1.0)                 # scores (span ok, cadence ok)
    assert r1.ran
    r2 = m.observe(frame(), ts=2.0)                 # only 1s later -> blocked
    assert not r2.ran
    r3 = m.observe(frame(), ts=3.1)                 # >2s after last -> scores
    assert r3.ran


def test_scores_confirm_on_two_of_three_plus_strong():
    m = monitor(bank=FakeBank({"vehicle": [0.76, 0.40, 0.91]}))
    m.observe(frame(), ts=0.0)                       # warmup, no score
    r1 = m.observe(frame(), ts=1.0, route={"vehicle": True})
    r2 = m.observe(frame(), ts=2.0, route={"vehicle": True})
    r3 = m.observe(frame(), ts=3.0, route={"vehicle": True})
    assert r1.ran and "vehicle" not in r1.confirmed  # 1 window
    assert "vehicle" not in r2.confirmed             # 2 windows, peak<0.85 yet
    assert "vehicle" in r3.confirmed                 # 2/3 >=0.70 and peak 0.91
    assert isinstance(r3.states["vehicle"], ConfirmResult)


def test_routing_can_skip_a_model():
    bank = FakeBank({"vehicle": [0.9, 0.9], "break_in": [0.9, 0.9]})
    m = monitor(bank=bank)
    m.observe(frame(), ts=0.0)
    m.observe(frame(), ts=1.0, route={"vehicle": True, "break_in": False})
    assert bank.calls["vehicle"] == 1 and bank.calls["break_in"] == 0


def test_unavailable_model_yields_no_score_but_still_runs():
    m = monitor(bank=FakeBank({}))                   # score() returns None
    m.observe(frame(), ts=0.0)
    r = m.observe(frame(), ts=1.0)
    assert r.ran and not r.scores and not r.confirmed


def test_reset_clears_buffer_and_history():
    m = monitor(bank=FakeBank({"vehicle": [0.9, 0.9, 0.9]}))
    m.observe(frame(), ts=0.0)
    m.observe(frame(), ts=1.0)
    m.reset()
    # after reset, first observe again has no pair -> no score
    assert not m.observe(frame(), ts=10.0).ran


def test_route_from_reasons():
    assert route_from_reasons(["person_near_vehicle"])["vehicle"]
    assert not route_from_reasons(["person_near_vehicle"])["break_in"]
    assert route_from_reasons(["person_in_restricted"])["break_in"]
    assert not route_from_reasons(["person_lingering"])["vehicle"]


def test_build_evidence_buckets_reasons():
    from app.hybrid import SpecialistObservation
    obs = SpecialistObservation(ran=True, scores={"vehicle": 0.9},
                                confirmed={"vehicle"})
    ev = build_evidence("cam1",
                        ["person_near_vehicle", "pose_reaching",
                         "person_in_restricted",
                         "vehicle_departure_after_activity"],
                        obs, relationship=True)
    assert "person_near_vehicle" in ev.context_signals
    assert "person_in_restricted" in ev.context_signals   # zone -> context
    assert "pose_reaching" in ev.pose_motion_signals
    assert ev.specialist_confirmed == {"vehicle"}
    assert ev.state_chain == "vehicle_departure_after_activity"
    assert ev.relationship is True


def test_end_to_end_confirmed_incident_via_fusion():
    """Monitor confirmation + free-layer interaction -> CONFIRMED via fuse()."""
    from app.hybrid import SpecialistObservation
    obs = SpecialistObservation(ran=True, scores={"vehicle": 0.91},
                                confirmed={"vehicle"})
    ev = build_evidence("cam1", ["person_near_vehicle", "person_at_vehicle"],
                        obs, relationship=True)
    assert fuse(ev).decision == CONFIRMED_INCIDENT
