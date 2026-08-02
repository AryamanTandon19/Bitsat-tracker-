"""Click-to-select geometry. Everything downstream is wrong if this is."""
from __future__ import annotations

import pytest

from app.tagging import (Rect, hit_test, in_letterbox, letterbox, nearby,
                         select, to_frame_coords, validate_box)

HD = (1920, 1080)


class Det:                       # duck-types app.detector.Detection
    def __init__(self, xyxy, cls_name="car"):
        self.xyxy, self.cls_name = xyxy, cls_name


# --------------------------------------------------------- coordinates
def test_an_exactly_matching_player_maps_one_to_one():
    assert to_frame_coords(480, 270, 1920, 1080, *HD) == (480, 270)


def test_a_scaled_player_scales_the_click():
    # half-size player: a click at 240,135 is 480,270 in the footage
    assert to_frame_coords(240, 135, 960, 540, *HD) == (480, 270)


def test_a_letterboxed_player_removes_the_top_and_bottom_bars():
    """A 16:9 video in a square player: the picture is wide, so the bars are
    above and below it."""
    off_x, off_y, _ = letterbox(1080, 1080, *HD)
    assert off_x == 0 and off_y == pytest.approx(236.25)
    # the centre of the picture is the centre of the frame
    x, y = to_frame_coords(540, 540, 1080, 1080, *HD)
    assert (round(x), round(y)) == (960, 540)
    # the top edge of the picture is y=0, not y=236
    _, y = to_frame_coords(540, 236.25, 1080, 1080, *HD)
    assert y == pytest.approx(0, abs=0.5)


def test_a_pillarboxed_player_removes_the_side_bars():
    """A 4:3 video in a 16:9 player: the picture is tall, so the bars are at
    the sides."""
    x, y = to_frame_coords(960, 540, 1920, 1080, 640, 480)
    assert (round(x), round(y)) == (320, 240)
    off_x, off_y, _ = letterbox(1920, 1080, 640, 480)
    assert off_y == 0 and off_x == pytest.approx(240)


def test_ignoring_letterboxing_would_be_wrong_by_a_lot():
    """The bug this module exists to prevent."""
    # an off-centre click, scaling each axis independently as if there were
    # no bars — the centre would agree by luck, which is how this ships broken
    naive_y = 270 / 1080 * 1080
    _, real_y = to_frame_coords(540, 270, 1080, 1080, *HD)
    assert naive_y == 270 and real_y == pytest.approx(60)
    assert abs(naive_y - real_y) > 200


def test_a_click_on_a_black_bar_snaps_into_the_frame():
    x, y = to_frame_coords(540, 10, 1080, 1080, *HD)   # 10px down = a bar
    assert y == 0 and 0 <= x <= 1920
    assert not in_letterbox(540, 10, 1080, 1080, *HD)
    assert in_letterbox(540, 540, 1080, 1080, *HD)


def test_a_click_can_never_land_outside_the_frame():
    for cx, cy in ((-50, -50), (5000, 5000), (0, 0), (1920, 1080)):
        x, y = to_frame_coords(cx, cy, 1920, 1080, *HD)
        assert 0 <= x <= 1920 and 0 <= y <= 1080


def test_fullscreen_is_just_another_display_size():
    small = to_frame_coords(120, 67.5, 480, 270, *HD)
    full = to_frame_coords(640, 360, 2560, 1440, *HD)
    assert small == pytest.approx(full, abs=1)


def test_zero_sized_dimensions_are_refused():
    for args in ((0, 100, 1920, 1080), (100, 0, 1920, 1080),
                 (100, 100, 0, 1080), (100, 100, 1920, 0)):
        with pytest.raises(ValueError):
            letterbox(*args)


# ------------------------------------------------------------ hit test
def test_a_click_inside_a_box_selects_it():
    car = Det((100, 100, 300, 250))
    assert hit_test([car], 200, 175) == [car]
    assert hit_test([car], 50, 50) == []


def test_the_smallest_containing_box_wins():
    """A person in front of a bus: the person's box sits inside the bus's,
    and the person is what was clicked."""
    bus = Det((0, 0, 800, 600), "bus")
    person = Det((300, 200, 380, 450), "person")
    hits = hit_test([bus, person], 340, 300)
    assert [h.cls_name for h in hits] == ["person", "bus"]


def test_overlapping_boxes_all_come_back_as_alternatives():
    a, b, c = Det((0, 0, 400, 400)), Det((100, 100, 300, 300)), Det((0, 0, 50, 50))
    out = select([a, b, c], 200, 200)
    assert out["selected"] is b
    assert out["alternatives"] == [a]        # c does not contain the point
    assert out["method"] == "ai_detection"


# -------------------------------------------------------------- nearby
def test_a_click_beside_an_object_suggests_it():
    car = Det((100, 100, 300, 250))
    out = select([car], 320, 175, radius=60)
    assert out["method"] == "nearby_detection" and out["selected"] is car


def test_nearby_is_measured_to_the_edge_not_the_centre():
    """A click by a long parked car should prefer the car over a small bag
    whose centre happens to be nearer."""
    car = Det((0, 100, 900, 300), "car")
    bag = Det((940, 180, 970, 210), "bag")
    out = nearby([car, bag], 920, 200, radius=100)
    assert [b.cls_name for b in out] == ["car", "bag"]


def test_nothing_near_falls_through_to_drawing_a_box():
    out = select([Det((0, 0, 50, 50))], 1500, 900)
    assert out["method"] == "manual_box" and out["selected"] is None


def test_the_radius_is_respected():
    car = Det((100, 100, 300, 250))
    assert nearby([car], 400, 175, radius=60) == []
    assert nearby([car], 400, 175, radius=120) == [car]


# --------------------------------------------------------- manual boxes
def test_a_box_drawn_backwards_is_normalised():
    r = validate_box(300, 250, 100, 100, *HD)
    assert (r.x1, r.y1, r.x2, r.y2) == (100, 100, 300, 250)


def test_a_box_is_clipped_to_the_frame():
    r = validate_box(-50, -50, 5000, 5000, *HD)
    assert (r.x1, r.y1, r.x2, r.y2) == (0, 0, 1920, 1080)


def test_a_box_with_no_area_is_refused_with_a_usable_message():
    with pytest.raises(ValueError, match="too small"):
        validate_box(100, 100, 101, 400, *HD)
    with pytest.raises(ValueError, match="too small"):
        validate_box(100, 100, 100, 100, *HD)


def test_a_box_entirely_outside_the_frame_is_refused():
    with pytest.raises(ValueError):
        validate_box(3000, 3000, 4000, 4000, *HD)


# ------------------------------------------------------- input shapes
def test_boxes_may_arrive_in_any_of_the_shapes_the_app_uses():
    point = (200, 175)
    for box in (Rect(100, 100, 300, 250),
                Det((100, 100, 300, 250)),
                (100, 100, 300, 250),
                {"x1": 100, "y1": 100, "x2": 300, "y2": 250},
                {"xyxy": [100, 100, 300, 250]}):
        assert hit_test([box], *point) == [box]


def test_a_real_detection_object_works_unchanged():
    from app.detector import Detection
    d = Detection(track_id=3, cls_name="car", conf=0.9,
                  xyxy=(100.0, 100.0, 300.0, 250.0))
    out = select([d], 200, 175)
    assert out["selected"] is d and out["method"] == "ai_detection"
