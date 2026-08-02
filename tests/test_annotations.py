"""Saved annotations: the record, its validation, and what comes out the end.

The rule this file exists to defend is the one that cannot be fixed later:
the model's own polygon is written once and is never reachable by an edit.
Lose it and every number about how good the model is becomes the model
grading itself.
"""
from __future__ import annotations

import json

import pytest

from app import annotations as A

SQUARE = [[(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]]


def payload(**over):
    p = {"clip_id": 1, "frame_index": 30, "timestamp_ms": 1200,
         "category": "car", "source": "yolo_segmentation",
         "frame_width": 1920, "frame_height": 1080,
         "original_polygon": json.dumps([[[10, 10], [90, 10], [90, 90], [10, 90]]]),
         "detection_confidence": 0.82, "model": "yolo11n-seg"}
    p.update(over)
    return p


# ------------------------------------------------------------- polygons
def test_a_single_ring_and_a_list_of_rings_are_both_accepted():
    one = A.normalize_polygons([[0, 0], [10, 0], [10, 10]])
    many = A.normalize_polygons([[[0, 0], [10, 0], [10, 10]]])
    assert one == many
    assert len(one) == 1


def test_a_json_string_is_accepted_because_that_is_what_a_form_sends():
    assert A.normalize_polygons('[[[0,0],[10,0],[10,10]]]') == \
        [[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]]


def test_broken_json_says_so_rather_than_storing_nothing():
    with pytest.raises(ValueError, match="valid JSON"):
        A.normalize_polygons("[[[0,0],")


def test_points_are_clamped_to_the_frame():
    got = A.normalize_polygons([[-50, -50], [3000, 0], [3000, 2000]], 1920, 1080)
    assert got == [[(0.0, 0.0), (1920.0, 0.0), (1920.0, 1080.0)]]


def test_a_ring_with_no_area_is_dropped_not_stored():
    # a line has points but nothing to click, and nothing to draw
    assert A.normalize_polygons([[[0, 0], [10, 10], [20, 20]]]) == []


def test_a_ring_with_two_points_is_dropped():
    assert A.normalize_polygons([[[0, 0], [10, 10]]]) == []


def test_an_absurd_number_of_points_is_refused():
    huge = [[i, i * i % 977] for i in range(A.MAX_POINTS + 1)]
    with pytest.raises(ValueError, match="at most"):
        A.normalize_polygons(huge)


def test_nan_coordinates_are_refused():
    with pytest.raises(ValueError, match="numbers"):
        A.normalize_polygons([[0, 0], [float("nan"), 5], [10, 10]])


def test_the_round_trip_through_json_keeps_the_shape():
    polys = A.normalize_polygons(SQUARE)
    assert A.polygons_from_json(A.polygons_json(polys)) == polys


def test_bad_stored_json_reads_back_as_empty_rather_than_crashing():
    # a row written by an older version, or by hand: the page must still open
    assert A.polygons_from_json("not json") == []


def test_the_box_covers_every_piece_of_a_split_object():
    polys = [[(0, 0), (10, 0), (10, 10)], [(50, 50), (60, 50), (60, 60)]]
    assert A.bbox_of(polys) == (0, 0, 60, 60)


def test_a_detection_with_no_outline_keeps_the_box_it_came_with():
    assert A.bbox_of([], (5, 6, 7, 8)) == (5.0, 6.0, 7.0, 8.0)


# ------------------------------------------------------------------ iou
def test_identical_outlines_agree_completely():
    assert A.polygon_iou(SQUARE, SQUARE) == 1.0


def test_outlines_that_do_not_touch_agree_not_at_all():
    far = [[(500.0, 500.0), (600.0, 500.0), (600.0, 600.0)]]
    assert A.polygon_iou(SQUARE, far) == 0.0


def test_half_overlap_is_about_a_third():
    # two 100x100 squares offset by 50: intersection 5000, union 15000
    other = [[(50.0, 0.0), (150.0, 0.0), (150.0, 100.0), (50.0, 100.0)]]
    assert 0.30 < A.polygon_iou(SQUARE, other) < 0.37


def test_drift_is_none_when_nobody_touched_it():
    # not zero: "the human agreed" and "the human never looked" are different
    # facts, and averaging the second in as the first flatters the model
    assert A.drift(SQUARE, []) is None


def test_drift_is_zero_when_the_human_kept_the_shape():
    assert A.drift(SQUARE, [list(SQUARE[0])]) == 0.0


# ------------------------------------------------------------ validation
def test_a_category_outside_the_vocabulary_is_refused():
    with pytest.raises(ValueError, match="category must be"):
        A.build(payload(category="spaceship"))


def test_an_unknown_selection_source_is_refused():
    with pytest.raises(ValueError, match="source must be"):
        A.build(payload(source="vibes"))


def test_custom_needs_a_label_saying_what_it_is():
    with pytest.raises(ValueError, match="custom category"):
        A.build(payload(category="custom"))
    A.build(payload(category="custom", custom_label="water tank"))


def test_an_annotation_with_neither_polygon_nor_box_is_refused():
    with pytest.raises(ValueError, match="polygon or a box"):
        A.build(payload(original_polygon=""))


def test_a_zero_area_box_does_not_count_as_a_box():
    with pytest.raises(ValueError, match="polygon or a box"):
        A.build(payload(original_polygon="", x1=10, y1=10, x2=10, y2=10))


def test_the_frame_size_must_be_known():
    with pytest.raises(ValueError, match="frame size"):
        A.build(payload(frame_width=0))


def test_a_negative_timestamp_is_refused():
    with pytest.raises(ValueError, match="timestamp"):
        A.build(payload(timestamp_ms=-5))


def test_tags_are_split_trimmed_and_deduplicated_in_order():
    a = A.build(payload(tags=" forced , at night ,, forced , boot open "))
    assert a.tags == ["forced", "at night", "boot open"]


def test_too_many_tags_is_refused():
    with pytest.raises(ValueError, match="at most"):
        A.clean_tags(",".join(f"t{i}" for i in range(A.MAX_TAGS + 1)))


def test_confidence_outside_zero_to_one_is_refused():
    with pytest.raises(ValueError, match="between 0 and 1"):
        A.clean_confidence(1.4)


def test_zero_confidence_survives_because_it_is_a_real_answer():
    # the falsy trap: 0.0 means "I am not sure at all", not "did not say"
    assert A.clean_confidence(0) == 0.0
    assert A.clean_confidence("") is None


def test_the_box_follows_the_polygon_rather_than_what_was_sent():
    a = A.build(payload(x1=0, y1=0, x2=1000, y2=1000))
    assert a.bbox == (10.0, 10.0, 90.0, 90.0)


def test_a_detection_only_annotation_keeps_its_box():
    a = A.build(payload(original_polygon="", source="yolo_detection_fallback",
                        x1=4, y1=6, x2=40, y2=60))
    assert a.bbox == (4.0, 6.0, 40.0, 60.0)
    assert a.polygon == []


# ------------------------------------------------------------------ edits
def test_an_edit_writes_the_corrected_polygon_and_leaves_the_original_alone():
    a = A.build(payload())
    before = [list(p) for p in a.original_polygon]
    A.apply_edit(a, {"corrected_polygon": json.dumps(
        [[[12, 12], [88, 12], [88, 88], [12, 88]]])}, "admin")
    assert a.original_polygon == before
    assert a.corrected_polygon and a.corrected_polygon != a.original_polygon
    assert a.corrected is True


def test_the_original_polygon_is_not_an_editable_field_at_all():
    a = A.build(payload())
    before = [list(p) for p in a.original_polygon]
    A.apply_edit(a, {"original_polygon": json.dumps([[[0, 0], [1, 0], [1, 1]]])})
    assert a.original_polygon == before


def test_the_box_moves_with_a_corrected_outline():
    a = A.build(payload())
    A.apply_edit(a, {"corrected_polygon": json.dumps(
        [[[0, 0], [200, 0], [200, 200], [0, 200]]])})
    assert a.bbox == (0.0, 0.0, 200.0, 200.0)


def test_clearing_the_correction_puts_the_model_back_in_charge():
    a = A.build(payload())
    A.apply_edit(a, {"corrected_polygon": json.dumps(
        [[[0, 0], [200, 0], [200, 200]]])})
    A.apply_edit(a, {"corrected_polygon": "[]"})
    assert a.corrected_polygon == []
    assert a.polygon == a.original_polygon


def test_a_corrected_shape_with_no_area_is_refused_rather_than_saved():
    a = A.build(payload())
    with pytest.raises(ValueError, match="no area"):
        A.apply_edit(a, {"corrected_polygon": json.dumps(
            [[[0, 0], [10, 10], [20, 20]]])})


def test_an_edit_that_names_no_field_changes_nothing_but_the_timestamp():
    a = A.build(payload(), "admin", now=100.0)
    A.apply_edit(a, {}, "someone", now=200.0)
    assert a.category == "car" and a.tags == [] and a.updated_at == 200.0


def test_editing_to_a_bad_category_is_refused_and_nothing_is_written():
    a = A.build(payload())
    with pytest.raises(ValueError):
        A.apply_edit(a, {"category": "spaceship"})
    assert a.category == "car"


# ----------------------------------------------------------- review flow
def test_the_review_path_runs_forwards():
    assert A.can_transition("draft", "submitted")
    assert A.can_transition("submitted", "approved")
    assert A.can_transition("submitted", "rejected")


def test_a_draft_cannot_be_approved_without_being_submitted():
    assert not A.can_transition("draft", "approved")


def test_anything_can_be_sent_back_to_draft():
    for s in A.STATUSES:
        if s != "draft":
            assert A.can_transition(s, "draft")


def test_a_status_that_does_not_exist_is_refused():
    assert not A.can_transition("draft", "wonderful")


# --------------------------------------------------------------- output
def test_the_public_shape_carries_both_polygons_and_the_gap_between_them():
    a = A.build(payload())
    A.apply_edit(a, {"corrected_polygon": json.dumps(
        [[[20, 20], [90, 20], [90, 90], [20, 90]]])})
    p = a.public()
    assert p["original_polygon"] and p["corrected_polygon"]
    assert p["polygon"] == p["corrected_polygon"]     # what to draw
    assert 0 < p["drift"] < 1
    assert p["label"] == "car"


def test_a_custom_label_is_what_the_object_is_called():
    a = A.build(payload(category="custom", custom_label="water tank"))
    assert a.public()["label"] == "water tank"


def test_the_summary_averages_drift_over_corrected_ones_only():
    a = A.build(payload())                              # untouched
    b = A.build(payload())
    A.apply_edit(b, {"corrected_polygon": json.dumps(
        [[[10, 10], [90, 10], [90, 50], [10, 50]]])})   # roughly half
    s = A.summarise([a, b])
    assert s["total"] == 2 and s["corrected"] == 1
    assert 0.3 < s["mean_drift"] < 0.7


def test_the_summary_reports_no_drift_when_nobody_has_corrected_anything():
    assert A.summarise([A.build(payload())])["mean_drift"] is None


def test_coco_export_has_the_pieces_a_training_pipeline_reads():
    a = A.build(payload())
    a.id = 7
    doc = A.to_coco([a], {1: {"filename": "night.mp4", "source": "gate"}})
    assert len(doc["images"]) == 1 and len(doc["annotations"]) == 1
    img, ann = doc["images"][0], doc["annotations"][0]
    assert img["file_name"] == "night.mp4#30"
    assert img["width"] == 1920 and img["height"] == 1080
    assert ann["bbox"] == [10.0, 10.0, 80.0, 80.0]
    assert ann["segmentation"] == [[10, 10, 90, 10, 90, 90, 10, 90]]
    assert ann["area"] == 6400.0
    names = {c["id"]: c["name"] for c in doc["categories"]}
    assert names[ann["category_id"]] == "car"


def test_coco_export_leaves_out_rejected_labels():
    good = A.build(payload())
    bad = A.build(payload(review_status="rejected"))
    doc = A.to_coco([good, bad], {})
    assert len(doc["annotations"]) == 1


def test_coco_export_exports_the_corrected_shape_not_the_original():
    a = A.build(payload())
    A.apply_edit(a, {"corrected_polygon": json.dumps(
        [[[0, 0], [50, 0], [50, 50], [0, 50]]])})
    doc = A.to_coco([a], {})
    assert doc["annotations"][0]["segmentation"] == [[0, 0, 50, 0, 50, 50, 0, 50]]
    assert doc["annotations"][0]["visionguard"]["corrected_by_human"] is True


def test_two_objects_on_one_frame_share_one_image_entry():
    doc = A.to_coco([A.build(payload()), A.build(payload())], {})
    assert len(doc["images"]) == 1 and len(doc["annotations"]) == 2


def test_objects_on_different_frames_do_not():
    doc = A.to_coco([A.build(payload()), A.build(payload(frame_index=31))], {})
    assert len(doc["images"]) == 2
