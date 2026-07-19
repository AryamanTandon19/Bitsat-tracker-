"""Pose-signal geometry (pure Python — no model needed)."""
from app.pose import (ARM_SWING, CROUCHING, REACHING, signals_from_keypoints)


def person(h=200.0, w=60.0):
    """Standing person bbox at origin: (x1,y1,x2,y2)."""
    return (0.0, 0.0, w, h)


def kp_standing():
    """17 keypoints of a normal standing person in a 60x200 box."""
    k = [(30.0, 0.0)] * 17
    k[5], k[6] = (20.0, 50.0), (40.0, 50.0)      # shoulders
    k[9], k[10] = (15.0, 100.0), (45.0, 100.0)   # wrists at hip height
    k[11], k[12] = (22.0, 100.0), (38.0, 100.0)  # hips mid-body
    k[13], k[14] = (24.0, 150.0), (36.0, 150.0)  # knees
    k[15], k[16] = (25.0, 195.0), (35.0, 195.0)  # ankles at the bottom
    return k


def conf_all(v=0.9):
    return [v] * 17


def test_standing_person_no_signals():
    signals, _ = signals_from_keypoints(kp_standing(), conf_all(), person())
    assert signals == set()


def test_crouching_detected():
    k = kp_standing()
    # hips dropped near the ankles (deep crouch)
    k[11], k[12] = (22.0, 150.0), (38.0, 150.0)
    signals, _ = signals_from_keypoints(k, conf_all(), person())
    assert CROUCHING in signals


def test_reaching_wrist_above_shoulder():
    k = kp_standing()
    k[9] = (15.0, 30.0)   # left wrist raised above shoulder line (y=50)
    signals, _ = signals_from_keypoints(k, conf_all(), person())
    assert REACHING in signals


def test_reaching_sideways_stretch():
    k = kp_standing()
    k[10] = (85.0, 90.0)  # right wrist stretched far right of the shoulders
    signals, _ = signals_from_keypoints(k, conf_all(), person())
    assert REACHING in signals


def test_arm_swing_needs_fast_wrist():
    k = kp_standing()
    prev = (0.0, [(15.0, 100.0), (45.0, 100.0)])
    # 0.2s later, wrist moved 80px -> 400 px/s > 1.5 * 200 body-height/s
    k[9] = (15.0, 180.0)
    k9_far = [(x, y) for x, y in k]
    signals, _ = signals_from_keypoints(k9_far, conf_all(), person(),
                                        prev_wrists=prev, ts=0.2)
    # 80px in 0.2s = 400px/s > 300 (1.5 * 200) -> swing
    assert ARM_SWING in signals


def test_slow_wrist_is_not_swing():
    k = kp_standing()
    prev = (0.0, [(15.0, 100.0), (45.0, 100.0)])
    k[9] = (15.0, 110.0)  # 10px in 0.2s = 50 px/s — slow
    signals, _ = signals_from_keypoints(k, conf_all(), person(),
                                        prev_wrists=prev, ts=0.2)
    assert ARM_SWING not in signals


def test_low_confidence_joints_ignored():
    k = kp_standing()
    k[11], k[12] = (22.0, 150.0), (38.0, 150.0)   # crouch positions...
    conf = conf_all()
    conf[11] = conf[12] = 0.1                      # ...but hips barely visible
    signals, _ = signals_from_keypoints(k, conf, person())
    assert CROUCHING not in signals


def test_trigger_merges_pose_signals():
    from dataclasses import dataclass
    from app.trigger import CandidateTrigger

    @dataclass
    class Det:
        track_id: int
        cls_name: str
        xyxy: tuple

        @property
        def foot_point(self):
            x1, y1, x2, y2 = self.xyxy
            return ((x1 + x2) / 2, y2)

    from datetime import datetime
    t = CandidateTrigger({}, {"on_near_vehicle": False, "on_loiter": False,
                              "on_zone": False, "on_night_person": False,
                              "on_pose": True},
                         localtime_fn=lambda: datetime(2026, 7, 4, 14, 0))
    p = Det(7, "person", (600, 300, 620, 360))
    # without pose: quiet
    assert not t.is_candidate([p], ts=1.0)[0]
    # with a crouch signal: fires and involves the person
    fire, reasons = t.is_candidate([p], ts=2.0,
                                   pose_signals={7: {CROUCHING}})
    assert fire and CROUCHING in reasons
    assert t.last_involved == {7}
