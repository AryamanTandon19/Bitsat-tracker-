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


# =====================================================================
# Step A — polygons: selecting by an object's actual outline, not its box
# =====================================================================
from app.tagging import (has_mask, is_simple, mask_area, mask_hit_test,
                         point_in_mask, point_in_polygon, polygon_area,
                         polygon_bbox, validate_polygon)

SQUARE = [[100, 100], [300, 100], [300, 300], [100, 300]]
TRIANGLE = [[0, 0], [100, 0], [0, 100]]


class Seg:
    """A segmented object: box plus outline, as the API will return it."""
    def __init__(self, xyxy, polygon=None, polygons=None, cls_name="car"):
        self.xyxy, self.cls_name = xyxy, cls_name
        if polygon is not None:
            self.polygon = polygon
        if polygons is not None:
            self.polygons = polygons


# ------------------------------------------------------------ area
def test_polygon_area_is_the_shoelace_area():
    assert polygon_area(SQUARE) == 40000
    assert polygon_area(TRIANGLE) == 5000


def test_winding_direction_does_not_change_the_area():
    """Ultralytics does not guarantee consistent winding."""
    assert polygon_area(list(reversed(SQUARE))) == polygon_area(SQUARE)


def test_a_degenerate_polygon_has_no_area():
    assert polygon_area([[0, 0], [10, 10]]) == 0.0
    assert polygon_area([]) == 0.0
    assert polygon_area([[0, 0], [10, 0], [20, 0]]) == 0.0   # collinear


def test_polygon_bbox_wraps_the_outline():
    r = polygon_bbox([[50, 80], [200, 10], [140, 300]])
    assert (r.x1, r.y1, r.x2, r.y2) == (50, 10, 200, 300)
    with pytest.raises(ValueError):
        polygon_bbox([])


# -------------------------------------------------- point in polygon
def test_a_point_inside_the_outline_is_inside():
    assert point_in_polygon(200, 200, SQUARE)


def test_a_point_outside_the_outline_is_outside():
    assert not point_in_polygon(50, 50, SQUARE)
    assert not point_in_polygon(400, 200, SQUARE)


def test_the_boundary_counts_as_inside():
    """Clicking the edge of a bag strap or a bicycle frame is aiming at it."""
    assert point_in_polygon(100, 200, SQUARE)      # on an edge
    assert point_in_polygon(100, 100, SQUARE)      # on a vertex
    assert point_in_polygon(300, 300, SQUARE)


def test_a_concave_outline_excludes_its_notch():
    """An L-shape: the corner cut out of it is not part of the object."""
    L = [[0, 0], [200, 0], [200, 80], [80, 80], [80, 200], [0, 200]]
    assert point_in_polygon(40, 40, L)             # in the arm
    assert not point_in_polygon(150, 150, L)       # in the notch


def test_a_polygon_with_too_few_points_contains_nothing():
    assert not point_in_polygon(5, 5, [[0, 0], [10, 10]])


# ---------------------------------------------------- validation
def test_a_valid_outline_survives_validation():
    assert validate_polygon(SQUARE, 1920, 1080) == [
        (100.0, 100.0), (300.0, 100.0), (300.0, 300.0), (100.0, 300.0)]


def test_an_outline_is_clipped_into_the_frame():
    out = validate_polygon([[-50, -50], [5000, -50], [5000, 5000], [-50, 5000]],
                           1920, 1080)
    assert all(0 <= x <= 1920 and 0 <= y <= 1080 for x, y in out)


def test_too_few_points_is_refused_with_a_usable_message():
    with pytest.raises(ValueError, match="at least 3 points"):
        validate_polygon([[0, 0], [10, 10]], 1920, 1080)


def test_an_outline_with_no_area_is_refused():
    with pytest.raises(ValueError, match="too small"):
        validate_polygon([[10, 10], [12, 10], [12, 12], [10, 12]], 1920, 1080)


def test_an_outline_clipped_to_a_line_is_refused():
    """Entirely off the left edge: everything clamps to x=0."""
    with pytest.raises(ValueError):
        validate_polygon([[-90, 100], [-50, 200], [-70, 300]], 1920, 1080)


def test_a_closing_duplicate_point_is_dropped():
    out = validate_polygon(SQUARE + [[100, 100]], 1920, 1080)
    assert len(out) == 4


def test_self_intersection_is_flagged_but_not_refused():
    """A crossed outline still renders and is still selectable; refusing a
    correction over a mathematical nicety would be worse."""
    star = [[50, 0], [20, 90], [95, 35], [5, 35], [80, 90]]   # pentagram
    assert not is_simple(star)
    assert is_simple(SQUARE)
    assert validate_polygon(star, 1920, 1080)      # accepted anyway


def test_a_bow_tie_is_caught_by_the_area_check_not_the_crossing_check():
    """A four-point bow tie's two lobes wind opposite ways, so the shoelace
    area cancels to exactly zero. It is rejected for having no area — which is
    right, it encloses nothing — and not for crossing itself."""
    bow = [[0, 0], [100, 100], [100, 0], [0, 100]]
    assert not is_simple(bow)
    assert polygon_area(bow) == 0.0
    with pytest.raises(ValueError, match="too small"):
        validate_polygon(bow, 1920, 1080)


# ------------------------------------------------------ mask hits
def test_an_object_with_an_outline_reports_one():
    assert has_mask(Seg((100, 100, 300, 300), polygon=SQUARE))
    assert not has_mask(Det((100, 100, 300, 300)))


def test_a_click_inside_the_outline_hits_the_mask():
    car = Seg((100, 100, 300, 300), polygon=SQUARE)
    assert point_in_mask(car, 200, 200)
    assert not point_in_mask(car, 110, 400)


def test_a_click_in_the_box_but_outside_the_outline_misses():
    """The whole reason masks beat boxes. A triangular object's box has three
    corners that are not the object."""
    wedge = Seg((0, 0, 100, 100), polygon=TRIANGLE)
    assert Rect(0, 0, 100, 100).contains(90, 90)   # inside the box
    assert not point_in_mask(wedge, 90, 90)        # outside the shape


def test_two_cars_that_overlap_as_boxes_but_not_as_shapes():
    """Parked at an angle: the rectangles intersect, the outlines do not.
    Clicking one must not offer the other."""
    a = Seg((0, 0, 200, 200), polygon=[[0, 0], [200, 0], [0, 200]], cls_name="car A")
    b = Seg((0, 0, 200, 200), polygon=[[200, 0], [200, 200], [20, 200]],
            cls_name="car B")
    out = select([a, b], 20, 20)
    assert out["selected"] is a and out["alternatives"] == []
    out = select([a, b], 180, 180)
    assert out["selected"] is b and out["alternatives"] == []


def test_the_smallest_containing_outline_wins():
    bus = Seg((0, 0, 800, 600), polygon=[[0, 0], [800, 0], [800, 600], [0, 600]],
              cls_name="bus")
    person = Seg((300, 200, 380, 450),
                 polygon=[[300, 200], [380, 200], [380, 450], [300, 450]],
                 cls_name="person")
    hits = mask_hit_test([bus, person], 340, 300)
    assert [h.cls_name for h in hits] == ["person", "bus"]


def test_overlapping_masks_all_come_back_as_candidates():
    a = Seg((0, 0, 400, 400), polygon=[[0, 0], [400, 0], [400, 400], [0, 400]])
    b = Seg((100, 100, 300, 300), polygon=[[100, 100], [300, 100], [300, 300],
                                           [100, 300]])
    out = select([a, b], 200, 200)
    assert out["method"] == "mask_hit"
    assert out["selected"] is b and out["alternatives"] == [a]


def test_an_object_split_into_two_pieces_is_clickable_in_both():
    """A car with a pole in front of it comes back as two polygons. Dropping
    the smaller piece would make part of the car unselectable."""
    split = Seg((0, 0, 400, 100),
                polygons=[[[0, 0], [100, 0], [100, 100], [0, 100]],
                          [[300, 0], [400, 0], [400, 100], [300, 100]]])
    assert point_in_mask(split, 50, 50)
    assert point_in_mask(split, 350, 50)
    assert not point_in_mask(split, 200, 50)       # the pole between them
    assert mask_area(split) == 20000


# ---------------------------------------------------- selection order
def test_a_mask_outranks_a_box():
    boxed = Det((0, 0, 500, 500), "big box")
    masked = Seg((100, 100, 300, 300), polygon=SQUARE, cls_name="segmented")
    out = select([boxed, masked], 200, 200)
    assert out["method"] == "mask_hit" and out["selected"] is masked


def test_a_box_still_works_when_nothing_has_a_mask():
    """The plain detector must keep working unchanged."""
    out = select([Det((100, 100, 300, 250))], 200, 175)
    assert out["method"] == "ai_detection"


def test_a_click_missing_every_outline_falls_back_to_the_box():
    """Inside the wedge's box but outside its shape: better to offer the box
    than nothing at all."""
    wedge = Seg((0, 0, 100, 100), polygon=TRIANGLE)
    out = select([wedge], 90, 90)
    assert out["method"] == "ai_detection" and out["selected"] is wedge


def test_a_click_far_from_everything_falls_through_to_drawing():
    out = select([Seg((0, 0, 100, 100), polygon=TRIANGLE)], 1500, 900)
    assert out["method"] == "manual_box" and out["selected"] is None


def test_masks_arrive_in_any_of_the_shapes_the_api_uses():
    for obj in ({"xyxy": [100, 100, 300, 300], "polygon": SQUARE},
                {"x1": 100, "y1": 100, "x2": 300, "y2": 300, "points": SQUARE},
                {"xyxy": [100, 100, 300, 300], "polygons": [SQUARE]},
                Seg((100, 100, 300, 300), polygon=SQUARE)):
        assert has_mask(obj) and point_in_mask(obj, 200, 200)


def test_letterboxed_click_then_mask_hit_end_to_end():
    """The two halves together: a click in a letterboxed player lands inside
    the right outline."""
    car = Seg((900, 400, 1100, 700),
              polygon=[[900, 400], [1100, 400], [1100, 700], [900, 700]])
    # 1080-square player, 16:9 footage: 236.25px bars top and bottom
    x, y = to_frame_coords(540, 500, 1080, 1080, *HD)
    assert (round(x), round(y)) == (960, 469)      # inside the car outline
    assert select([car], x, y)["method"] == "mask_hit"


def test_a_segmented_object_works_on_the_box_path_too():
    """The detector calls its box `xyxy`; the segmenter calls it `bbox`. The
    mask path reads neither, so a mismatch here hides until someone clicks a
    gap between two objects and the box fallback runs."""
    from app.segment import SegmentedObject
    o = SegmentedObject("x", "car", 0.9, (100, 100, 300, 250),
                        [[(100, 100), (300, 100), (300, 250)]])
    assert hit_test([o], 290, 240) == [o]          # box path
    assert nearby([o], 340, 175, radius=60) == [o]  # nearby path
    assert select([o], 120, 110)["method"] == "mask_hit"


def test_the_api_response_shape_is_selectable_as_it_comes_back():
    """A caller can feed /segment-frame's own JSON straight back in."""
    from app.segment import MockSegmenter
    objs = [o.public() for o in MockSegmenter().segment(None, 42)]
    assert all(has_mask(o) for o in objs)
    inside = objs[0]["polygon"][0]
    out = select(objs, inside[0], inside[1])
    assert out["method"] == "mask_hit"
