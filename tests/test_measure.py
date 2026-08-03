"""Matching what a detector found against what a person said was there.

One matching rule, shared by evaluate_detector.py and sweep_detector.py. A
harness that disagrees with itself between two scripts is worse than none,
because both numbers look official.
"""
from __future__ import annotations

from app import measure as M


def t(x1, y1, x2, y2, cls="car"):
    return {"bbox": (x1, y1, x2, y2), "cls": cls}


# ------------------------------------------------------------------ iou
def test_identical_boxes_overlap_completely():
    assert M.box_iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0


def test_boxes_that_do_not_touch_do_not_overlap():
    assert M.box_iou((0, 0, 10, 10), (50, 50, 60, 60)) == 0.0


def test_boxes_that_share_only_an_edge_do_not_overlap():
    assert M.box_iou((0, 0, 10, 10), (10, 0, 20, 10)) == 0.0


def test_half_overlap_is_a_third():
    # two 10x10 squares offset by 5: intersection 50, union 150
    assert abs(M.box_iou((0, 0, 10, 10), (5, 0, 15, 10)) - 1 / 3) < 1e-9


# --------------------------------------------------------------- buckets
def test_size_buckets_split_where_the_answer_changes():
    assert M.bucket_for(20) == "tiny (<40px)"
    assert M.bucket_for(60) == "small (40-80px)"
    assert M.bucket_for(120) == "medium (80-160px)"
    assert M.bucket_for(400) == "large (>160px)"


def test_a_bucket_boundary_belongs_to_the_larger_bucket():
    assert M.bucket_for(40) == "small (40-80px)"


def test_the_diagonal_is_what_sizes_an_object():
    assert M.diagonal((0, 0, 3, 4)) == 5.0


# -------------------------------------------------------------- matching
def test_a_detection_on_a_labelled_object_is_a_hit():
    matched, missed, extra = M.match([t(0, 0, 10, 10)], [t(1, 1, 11, 11)], 0.5)
    assert len(matched) == 1 and not missed and not extra


def test_a_labelled_object_with_nothing_near_it_is_a_miss():
    matched, missed, extra = M.match([t(0, 0, 10, 10)], [t(90, 90, 99, 99)], 0.5)
    assert not matched and missed == [0] and extra == [0]


def test_one_detection_cannot_satisfy_two_labels():
    """Otherwise two cars parked nose to tail could both be 'found' by one
    box, and the recall number would be a lie in the most common case."""
    truth = [t(0, 0, 10, 10), t(2, 2, 12, 12)]
    matched, missed, _extra = M.match(truth, [t(1, 1, 11, 11)], 0.4)
    assert len(matched) == 1 and len(missed) == 1


def test_the_better_overlap_wins_when_two_detections_compete():
    truth = [t(0, 0, 10, 10)]
    found = [t(4, 4, 14, 14), t(0, 0, 10, 10)]
    matched, _missed, extra = M.match(truth, found, 0.3)
    assert matched[0][1] == 1 and extra == [0]


def test_a_weak_overlap_below_the_gate_is_not_a_match():
    matched, missed, _e = M.match([t(0, 0, 10, 10)], [t(8, 8, 18, 18)], 0.5)
    assert not matched and missed == [0]


def test_finding_a_car_where_a_person_is_does_not_count_as_seeing_them():
    """A system that reports 'vehicle' while a man climbs through a window
    has not seen the man."""
    truth = [t(0, 0, 10, 20, "person")]
    found = [t(0, 0, 10, 20, "car")]
    matched, missed, extra = M.match_per_class(truth, found, 0.5)
    assert not matched and missed == [0] and extra == [0]
    # and class-agnostic matching, which is a different question, does
    matched, missed, _e = M.match(truth, found, 0.5)
    assert len(matched) == 1


def test_each_class_is_matched_independently():
    truth = [t(0, 0, 10, 10, "car"), t(0, 0, 10, 20, "person")]
    found = [t(0, 0, 10, 20, "person"), t(1, 1, 11, 11, "car")]
    matched, missed, extra = M.match_per_class(truth, found, 0.5)
    assert len(matched) == 2 and not missed and not extra


# ---------------------------------------------------------------- tally
def test_the_tally_counts_by_class_and_by_size_at_once():
    tally = M.Tally("test")
    truth = [t(0, 0, 100, 100, "car"), t(0, 0, 20, 20, "person")]
    tally.add_frame(truth, [(0, 0, 0.9)], [1], [], 0.5)
    assert tally.counts["car"] == {"truth": 1, "hit": 1}
    assert tally.counts["person"] == {"truth": 1, "hit": 0}
    assert tally.counts["medium (80-160px)"]["hit"] == 1
    assert tally.counts["tiny (<40px)"]["hit"] == 0
    assert tally.recall() == 0.5
    assert tally.recall("person") == 0.0


def test_recall_of_something_never_seen_is_none_not_zero():
    """Zero means 'it missed them all'. None means 'you have not labelled
    any', and printing 0% for that would send somebody chasing a bug."""
    assert M.Tally().recall("bus") is None


def test_seconds_per_frame_is_averaged_over_frames():
    tally = M.Tally()
    tally.add_frame([t(0, 0, 10, 10)], [(0, 0, 1.0)], [], [], 0.2)
    tally.add_frame([t(0, 0, 10, 10)], [(0, 0, 1.0)], [], [], 0.4)
    assert abs(tally.seconds_per_frame - 0.3) < 1e-9


def test_only_frames_with_misses_are_recorded_as_worst():
    tally = M.Tally()
    tally.add_frame([t(0, 0, 10, 10)], [(0, 0, 1.0)], [], [], 0.1, {"frame": 1})
    tally.add_frame([t(0, 0, 10, 10)], [], [0], [], 0.1, {"frame": 2})
    assert [w["frame"] for w in tally.worst] == [2]


def test_classes_the_detector_has_no_class_for_stay_out_of_the_total():
    tally = M.Tally()
    tally.add_frame([t(0, 0, 10, 10, "car"), t(0, 0, 9, 9, "bag")],
                    [(0, 0, 1.0)], [1], [])
    assert tally.labelled == 1 and tally.found == 1
    assert tally.recall() == 1.0


# --------------------------------------------------------------- honesty
class FakeAnn:
    def __init__(self, source="yolo_segmentation", category="car",
                 track_ref=None):
        self.source = source
        self.category = category
        self.track_ref = track_ref


def test_reconstructed_frames_are_not_scored_against():
    """Scoring a detector against our own interpolation measures the
    interpolation."""
    anns = [FakeAnn(), FakeAnn(source="interpolated"), FakeAnn(category="bag")]
    keep, interp, off = M.usable_labels(anns)
    assert len(keep) == 1 and interp == 1 and off == 1


def test_the_share_coming_from_one_followed_object_is_reported():
    anns = [FakeAnn(track_ref=1) for _ in range(9)] + [FakeAnn()]
    share, distinct = M.track_share(anns)
    assert abs(share - 0.9) < 1e-9 and distinct == 1


def test_a_label_set_with_no_tracks_reports_no_share():
    share, distinct = M.track_share([FakeAnn(), FakeAnn()])
    assert share == 0.0 and distinct == 0


def test_an_empty_label_set_does_not_divide_by_zero():
    assert M.track_share([]) == (0.0, 0)
