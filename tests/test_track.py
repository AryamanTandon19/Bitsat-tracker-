"""Following one object through moving video.

The cases here are the ones real footage produces, not the ones a clean
motion model produces: an object that curves, one that wobbles, one that
changes shape as it turns, one that disappears behind something, one the model
briefly calls something else, and two objects that pass each other closely
enough to swap if the matching is careless.
"""
from __future__ import annotations

import math

import pytest

from app import track as T


# --------------------------------------------------------------- helpers
class Obj:
    """Stands in for a SegmentedObject: only the fields the tracker reads."""

    def __init__(self, polys, cls="car", conf=0.8):
        self.polygons = [[(float(x), float(y)) for x, y in p] for p in polys]
        pts = [p for poly in self.polygons for p in poly]
        self.bbox = (min(p[0] for p in pts), min(p[1] for p in pts),
                     max(p[0] for p in pts), max(p[1] for p in pts))
        self.class_name = cls
        self.confidence = conf


def blob(cx, cy, rx=40.0, ry=25.0, n=12, rot=0.0):
    """An ellipse-ish ring — close enough to a car seen from above."""
    return [(cx + rx * math.cos(math.tau * k / n + rot),
             cy + ry * math.sin(math.tau * k / n + rot)) for k in range(n)]


def anchor_at(cx, cy, cls="car", frame=0, **kw):
    return T.observation_from(Obj([blob(cx, cy, **kw)], cls), frame, frame * 40)


def path(points, cls="car", start=1, step=1, **kw):
    """(frame, ts, [object]) for an object walking through `points`."""
    for i, (x, y) in enumerate(points):
        f = start + i * step
        yield f, f * 40, [Obj([blob(x, y, **kw)], cls)]


# ------------------------------------------------------------- geometry
def test_resampling_keeps_the_shape_and_changes_the_point_count():
    ring = blob(100, 100, n=7)
    out = T.resample_polygon(ring, 40)
    assert len(out) == 40
    from app.tagging import polygon_area
    # a resampled ring of the same shape has nearly the same area
    assert abs(polygon_area(out) - polygon_area(ring)) / polygon_area(ring) < 0.05


def test_resampling_spaces_the_points_evenly():
    out = T.resample_polygon(blob(0, 0, rx=50, ry=50, n=6), 24)
    gaps = [math.dist(out[i], out[(i + 1) % len(out)]) for i in range(len(out))]
    assert max(gaps) / min(gaps) < 1.15


def test_alignment_stops_the_shape_twisting_when_it_is_blended():
    a = T.resample_polygon(blob(0, 0), 24)
    rotated = a[9:] + a[:9]                 # same ring, different start point
    aligned = T.align_polygon(a, rotated)
    assert math.dist(aligned[0], a[0]) < 1e-6


def test_a_blend_halfway_between_two_positions_lands_halfway():
    a = blob(0, 0)
    b = blob(100, 0)
    mid = T.interpolate_polygon(a, b, 0.5)
    from app.tagging import polygon_centroid
    cx, _ = polygon_centroid(mid)
    assert 49 < cx < 51


def test_a_blend_at_the_ends_is_the_end_shapes():
    a, b = blob(0, 0), blob(100, 40)
    from app.tagging import polygon_centroid, polygon_iou
    assert polygon_iou([T.interpolate_polygon(a, b, 0.0)], [a]) > 0.95
    assert polygon_iou([T.interpolate_polygon(a, b, 1.0)], [b]) > 0.95


def test_interpolating_from_nothing_gives_the_other_shape():
    b = blob(10, 10)
    assert T.interpolate_polygon([], b, 0.5) == [(x, y) for x, y in b]


# -------------------------------------------------------- straight lines
def test_an_object_moving_in_a_straight_line_is_followed():
    a = anchor_at(100, 100)
    t = T.follow(a, path([(100 + 12 * i, 100) for i in range(1, 21)]))
    assert t.frames == 21 and t.observed == 21
    assert t.lost_at is None
    assert t.flagged == 0


def test_the_tracklet_reports_where_it_started_and_ended():
    a = anchor_at(100, 100)
    t = T.follow(a, path([(100 + 12 * i, 100) for i in range(1, 11)]))
    assert t.span == (0, 10)


# -------------------------------------------------- the awkward real cases
def test_an_object_that_curves_is_still_followed():
    """A car reversing out of a bay swings through an arc. A tracker that
    assumes straight-line motion loses it exactly when it turns."""
    pts = [(200 + 80 * math.sin(i / 9), 200 + 60 * (1 - math.cos(i / 9)))
           for i in range(1, 26)]
    t = T.follow(anchor_at(200, 200), path(pts))
    assert t.observed == 26, f"lost it at {t.lost_at}: {t.lost_why}"
    assert t.lost_at is None


def test_a_wobbling_walker_is_still_followed():
    """People sway. The side-to-side motion is a large fraction of their own
    width, which is exactly the scale a fixed distance gate gets wrong."""
    pts = [(60 + 3.5 * i, 300 + 9 * math.sin(i * 1.3)) for i in range(1, 31)]
    t = T.follow(anchor_at(60, 300, "person", rx=9, ry=20),
                 path(pts, "person", rx=9, ry=20))
    assert t.observed == 31, f"lost it at {t.lost_at}: {t.lost_why}"
    assert t.category == "person"


def test_an_object_changing_shape_as_it_turns_is_still_followed():
    """Side view to three-quarter view to back view: the box changes hugely,
    the pixels do not."""
    frames = []
    for i in range(1, 21):
        rx = 40 - 1.4 * i           # shortening as it turns towards the camera
        ry = 25 + 0.7 * i
        frames.append((i, i * 40,
                       [Obj([blob(120 + 7 * i, 150, rx=max(8, rx), ry=ry,
                                  rot=i * 0.07)])]))
    t = T.follow(anchor_at(120, 150), iter(frames))
    assert t.observed == 21, f"lost it at {t.lost_at}: {t.lost_why}"


def test_the_model_changing_its_mind_about_the_class_does_not_lose_it():
    """At twenty pixels wide YOLO flips between person and bicycle several
    times a second. Refusing to match across that loses the object for a
    reason that has nothing to do with where it is."""
    frames = []
    for i in range(1, 16):
        cls = "person" if i % 3 else "bicycle"
        frames.append((i, i * 40, [Obj([blob(50 + 6 * i, 200, rx=10, ry=22)],
                                       cls)]))
    t = T.follow(anchor_at(50, 200, "person", rx=10, ry=22), iter(frames))
    assert t.observed == 16
    assert t.category == "person"          # the majority verdict, not the last


def test_a_reclassified_frame_is_flagged_for_a_person_to_look_at():
    frames = [(1, 40, [Obj([blob(56, 200)], "car")]),
              (2, 80, [Obj([blob(62, 200)], "truck")])]
    t = T.follow(anchor_at(50, 200), iter(frames))
    flagged = [o for o in t.observations if o.needs_review]
    assert flagged and T.RECLASSED in flagged[0].why


# ------------------------------------------------------------ occlusion
def test_a_short_disappearance_is_bridged_and_labelled_as_reconstructed():
    frames = []
    for i in range(1, 13):
        # gone behind a van for frames 5, 6 and 7
        objs = [] if i in (5, 6, 7) else [Obj([blob(100 + 14 * i, 180)])]
        frames.append((i, i * 40, objs))
    t = T.follow(anchor_at(100, 180), iter(frames))
    assert t.lost_at is None
    assert t.reconstructed == 3
    made_up = [o for o in t.observations if o.kind == "interpolated"]
    assert {o.frame_index for o in made_up} == {5, 6, 7}
    # and they are not passed off as sightings
    assert all(o.confidence == 0.0 for o in made_up)


def test_a_bridged_shape_actually_lands_between_the_two_real_ones():
    frames = [(1, 40, [Obj([blob(120, 180)])]),
              (2, 80, []), (3, 120, []),
              (4, 160, [Obj([blob(240, 180)])])]
    t = T.follow(anchor_at(100, 180), iter(frames))
    from app.tagging import polygon_centroid
    mid = [o for o in t.observations if o.frame_index == 2][0]
    cx, _ = polygon_centroid(mid.main_ring)
    assert 140 < cx < 180, cx


def test_a_long_disappearance_gives_up_rather_than_inventing_a_path():
    frames = [(i, i * 40, []) for i in range(1, 16)]
    t = T.follow(anchor_at(100, 100), iter(frames))
    assert t.lost_at is not None
    assert t.reconstructed == 0          # nothing was made up on the way out
    assert "hidden" in t.lost_why or "left" in t.lost_why


def test_a_gap_long_enough_to_matter_is_flagged():
    cfg = T.TrackConfig(review_gap=1)
    frames = [(1, 40, [Obj([blob(114, 180)])]),
              (2, 80, []), (3, 120, []),
              (4, 160, [Obj([blob(156, 180)])])]
    t = T.follow(anchor_at(100, 180), iter(frames), cfg)
    made_up = [o for o in t.observations if o.kind == "interpolated"]
    assert made_up and all(o.needs_review for o in made_up)
    assert T.GAP in made_up[0].why


# ------------------------------------------------------- staying honest
def test_it_does_not_swap_onto_a_second_object_passing_close_by():
    """Two people crossing. The wrong one is nearer for a moment; the right
    one still overlaps far better."""
    frames = []
    for i in range(1, 13):
        ours = Obj([blob(80 + 9 * i, 200, rx=11, ry=24)], "person")
        other = Obj([blob(260 - 9 * i, 205, rx=11, ry=24)], "person")
        frames.append((i, i * 40, [other, ours]))   # deliberately out of order
    t = T.follow(anchor_at(80, 200, "person", rx=11, ry=24), iter(frames))
    end = t.observations[-1]
    assert end.centroid[0] > 170, "it jumped onto the object coming the other way"


def test_an_implausible_jump_is_recorded_rather_than_accepted_silently():
    frames = [(1, 40, [Obj([blob(210, 100)])])]      # five car-lengths in a frame
    t = T.follow(anchor_at(100, 100), iter(frames))
    hit = [o for o in t.observations if o.kind == "tracked"]
    if hit:                                   # accepted, but only with a warning
        assert hit[0].needs_review and T.JUMPED in hit[0].why
    else:
        assert t.observations[-1].kind == "anchor"


def test_a_sudden_change_of_size_is_flagged_as_a_possible_merge():
    frames = [(1, 40, [Obj([blob(112, 100, rx=110, ry=80)])])]
    t = T.follow(anchor_at(100, 100), iter(frames))
    tracked = [o for o in t.observations if o.kind == "tracked"]
    assert tracked and tracked[0].needs_review
    assert T.SHRANK in tracked[0].why


def test_a_weak_overlap_is_flagged_even_when_it_is_the_best_available():
    frames = [(1, 40, [Obj([blob(150, 100)])])]      # touching, barely
    t = T.follow(anchor_at(100, 100), iter(frames))
    tracked = [o for o in t.observations if o.kind == "tracked"]
    assert tracked and tracked[0].needs_review


def test_nothing_at_all_in_the_frames_is_not_a_track():
    t = T.follow(anchor_at(100, 100), iter([]))
    assert t.frames == 1 and t.observed == 1     # just the anchor


def test_the_run_stops_at_the_frame_ceiling():
    cfg = T.TrackConfig(max_frames=5)
    t = T.follow(anchor_at(0, 100), path([(6 * i, 100) for i in range(1, 40)]),
                 cfg)
    assert t.frames == 6                          # anchor plus five


def test_progress_is_reported_while_it_runs():
    seen = []
    T.follow(anchor_at(0, 100), path([(9 * i, 100) for i in range(1, 8)]),
             on_progress=lambda n, f, _: seen.append((n, f)))
    assert seen[0] == (1, 1) and seen[-1][0] == 7


def test_the_summary_says_what_was_seen_and_what_was_made_up():
    frames = [(1, 40, [Obj([blob(114, 180)])]), (2, 80, []),
              (3, 120, [Obj([blob(142, 180)])])]
    s = T.follow(anchor_at(100, 180), iter(frames), model="yolo11n-seg").summary()
    assert s["observed"] == 3 and s["reconstructed"] == 1
    assert s["frames"] == 4 and s["model"] == "yolo11n-seg"
    assert s["start_frame"] == 0 and s["end_frame"] == 3


# ------------------------------------------------------------ prediction
def test_the_motion_estimate_aims_ahead_but_is_damped_over_a_gap():
    f = T.Follower(anchor_at(0, 100))
    f.vx, f.vy = 20.0, 0.0
    one = f.predict(1)[0]
    six = f.predict(6)[0]
    assert one == pytest.approx(20.0)
    assert six < 20.0 * 6, "a six-frame-ahead guess must not be taken at face value"


def test_far_away_objects_are_rejected_without_rasterising_them():
    """The measured version of this: on a car-park frame with 24 objects,
    comparing every mask cost 1.60s while the segmentation model itself cost
    0.11s. Almost all of it went on proving that a car at one end of the
    picture does not overlap a person at the other. Boxes settle that.
    """
    calls = []
    real = T.polygon_iou

    def counted(a, b, samples=96):
        calls.append(1)
        return real(a, b, samples)

    T.polygon_iou = counted
    try:
        crowd = [Obj([blob(300 + 200 * i, 700, rx=45, ry=28)]) for i in range(8)]
        frames = [(1, 40, crowd + [Obj([blob(112, 100)])])]
        T.follow(anchor_at(100, 100), iter(frames))
    finally:
        T.polygon_iou = real
    assert len(calls) <= 2, f"rasterised {len(calls)} of 9 candidates"


def test_the_prefilter_does_not_change_which_object_is_chosen():
    """A cheap rejection is only allowed if it rejects things the expensive
    test would have rejected anyway."""
    crowd = [Obj([blob(600 + 90 * i, 400, rx=40, ry=25)]) for i in range(6)]
    frames = [(i, i * 40, crowd + [Obj([blob(100 + 11 * i, 100)])])
              for i in range(1, 11)]
    t = T.follow(anchor_at(100, 100), iter(frames))
    assert t.observed == 11
    assert t.observations[-1].centroid[0] < 300


def test_a_prediction_is_never_written_down_as_an_observation():
    """The failure mode this guards against is a tracker emitting its own
    guesses as detections: confident, plausible, and wrong."""
    frames = [(i, i * 40, []) for i in range(1, 4)]
    t = T.follow(anchor_at(100, 100), iter(frames))
    assert all(o.kind == "anchor" for o in t.observations)
