"""Localized vehicle motion-burst scorer — pure OpenCV, no torch."""
import numpy as np
from app.motion import VehicleMotion


class D:
    def __init__(self, tid, box):
        self.track_id = tid
        self.xyxy = box


def test_first_frame_returns_empty():
    m = VehicleMotion()
    assert m.scores(np.zeros((50, 50, 3), np.uint8), [D(1, (0, 0, 50, 50))]) == {}


def test_localized_burst_scores_high_in_a_big_box():
    """A small forceful change in a big car box: whole-box mean would be ~2,
    but the localized peak must register strongly."""
    m = VehicleMotion()
    base = np.full((200, 200, 3), 30, np.uint8)
    veh = D(1, (0, 0, 200, 200))
    m.scores(base, [veh])                       # prime previous frame
    f2 = base.copy()
    f2[90:110, 90:110] = 220                     # 20x20 localized spike
    assert m.scores(f2, [veh])[1] > 50


def test_static_scene_scores_low():
    m = VehicleMotion()
    base = np.full((200, 200, 3), 30, np.uint8)
    veh = D(1, (0, 0, 200, 200))
    m.scores(base, [veh])
    assert m.scores(base.copy(), [veh])[1] < 5   # nothing moved


def test_person_overlap_focuses_on_contact_region():
    m = VehicleMotion()
    base = np.full((200, 200, 3), 30, np.uint8)
    veh = D(1, (0, 0, 200, 200))
    per = D(2, (80, 80, 140, 140))
    m.scores(base, [veh], [per])
    f2 = base.copy()
    f2[95:115, 95:115] = 220                     # change inside contact region
    assert m.scores(f2, [veh], [per])[1] > 50
