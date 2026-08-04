"""Track features — the explainable baseline everything later must beat.

The scenarios are the ones that decide the product: someone walking past a car
and someone working at it look nearly identical to a full-frame video model
and completely different here.
"""
from __future__ import annotations

import math

import pytest

from training.features import (FEATURES, Box, Context, Frame, MultiFrame,
                               candidate_pairs, explain, gap_radii,
                               pair_features, to_vector, window_for)

# a car roughly 200x100 at the middle of the frame
CAR = Box(400, 300, 600, 400)


def person_at(x, y, h=60, w=24, conf=0.8):
    return Box(x - w / 2, y - h, x + w / 2, y, conf)


def walk(xs, y=400, ts0=0.0, step_s=0.5, vehicle=CAR, **kw):
    """A person walking through the given x positions."""
    return [Frame(ts=ts0 + i * step_s, person=person_at(x, y, **kw),
                  vehicle=vehicle, people_in_frame=1, vehicles_in_frame=1)
            for i, x in enumerate(xs)]


# ------------------------------------------------------------- primitives
def test_distance_is_measured_in_the_vehicles_own_radii():
    """The same number must mean the same thing on a camera looking down a
    long car park and one two metres above a gate."""
    near = gap_radii(person_at(500, 400), CAR)
    assert near == pytest.approx(0.0, abs=0.05)
    big_car = Box(0, 0, 400, 200)                 # twice the size
    small_car = Box(0, 0, 200, 100)
    p = person_at(300, 200)
    assert gap_radii(p, big_car) < gap_radii(p, small_car)


def test_distance_is_measured_foot_to_foot():
    """Two people at different depths have very different box centres and
    comparable foot points."""
    tall = Box(490, 200, 510, 400)
    short = Box(490, 340, 510, 400)
    assert gap_radii(tall, CAR) == pytest.approx(gap_radii(short, CAR))


# --------------------------------------------------------- the vector shape
def test_the_vector_is_always_the_same_length_and_order():
    a = to_vector(pair_features(walk([100, 200, 300])))
    b = to_vector(pair_features([]))
    assert len(a) == len(b) == len(FEATURES)


def test_an_empty_window_still_gives_context_features():
    ctx = Context(night=True, hour=2.0, vehicle_registered=True)
    f = pair_features([], ctx)
    assert f["night"] == 1.0 and f["vehicle_registered"] == 1.0
    assert f["dwell_s"] == 0.0


def test_a_missing_feature_reads_as_zero_not_an_error():
    assert to_vector({"night": 1.0})[FEATURES.index("night")] == 1.0


# ------------------------------------------------ walking past vs loitering
def test_walking_straight_past_looks_nothing_like_loitering():
    """The discriminator the whole tier rests on."""
    passing = pair_features(walk([100, 200, 300, 400, 500, 600, 700, 800, 900]))
    loitering = pair_features(walk(
        [480, 500, 480, 510, 490, 505, 485, 500, 495], step_s=4.0))

    assert passing["straightness"] > 0.9
    assert loitering["straightness"] < 0.3
    assert loitering["longest_near_run_s"] > passing["longest_near_run_s"]
    assert loitering["direction_reversals"] > passing["direction_reversals"]


def test_someone_who_ends_where_they_started_was_not_passing_through():
    there_and_back = pair_features(walk([500, 700, 900, 700, 500]))
    assert there_and_back["net_displacement_radii"] < 0.1
    assert there_and_back["straightness"] < 0.1


def test_a_long_stand_and_many_brief_visits_are_different_features():
    """Ten one-second visits and one ten-second stand both total ten seconds,
    and only one of them is loitering."""
    stand = pair_features(walk([500] * 21, step_s=1.0))
    visits = pair_features(walk([500, 900] * 10 + [500], step_s=1.0))
    assert stand["longest_near_run_s"] > visits["longest_near_run_s"] * 3
    assert visits["revisits"] > stand["revisits"]


# -------------------------------------------------------------- proximity
def test_closing_on_a_vehicle_gives_a_positive_approach_speed():
    approach = pair_features(walk([1000, 800, 600, 500], step_s=1.0))
    assert approach["approach_speed_radii_s"] > 0


def test_walking_away_gives_a_negative_approach_speed():
    leaving = pair_features(walk([500, 600, 800, 1000], step_s=1.0))
    assert leaving["approach_speed_radii_s"] < 0


def test_standing_at_the_vehicle_counts_as_contact():
    inside = pair_features(walk([500, 505, 510], y=395))
    assert inside["contact_frames"] > 0


def test_walking_in_front_of_a_car_is_not_touching_it():
    """The bug a demo caught: box overlap alone made every scenario report
    "touching the vehicle". A person ten metres in front of a parked car
    overlaps it in every frame of a camera looking down a car park, and
    calling that contact would be one of the larger false-alarm sources in
    the system."""
    in_front = walk([460, 500, 540], y=560)      # well below the car on the
    f = pair_features(in_front)                  # ground, overlapping in image
    assert f["contact_frames"] == 0.0
    assert f["min_gap_radii"] > 0.5


def test_the_overlap_still_has_to_happen():
    """Near on the ground but not overlapping — beside the car, not at it."""
    beside = pair_features(walk([900, 905, 910], y=400))
    assert beside["contact_frames"] == 0.0


def test_never_coming_near_records_a_large_gap_not_a_zero_one():
    far = pair_features(walk([2000, 2100, 2200]))
    assert far["min_gap_radii"] > 5
    assert far["frac_frames_close"] == 0.0


def test_a_window_with_no_vehicle_says_so_rather_than_looking_like_contact():
    """The falsy trap: 'never measured' must not read as 'measured as
    touching'."""
    f = pair_features([Frame(ts=i * 0.5, person=person_at(100 + i * 50, 400))
                       for i in range(6)])
    assert f["min_gap_radii"] == 99.0
    assert f["contact_frames"] == 0.0


# -------------------------------------------------------- pose without pose
def test_crouching_shows_up_as_a_height_drop():
    """A cheap crouch proxy that needs no pose model: bending shortens the
    box."""
    frames = walk([500] * 6)
    for f in frames[2:4]:
        f.person = Box(f.person.x1, f.person.y1 + 30, f.person.x2, f.person.y2)
    f = pair_features(frames)
    assert f["height_drop_ratio"] > 0.4


def test_standing_upright_shows_no_height_drop():
    assert pair_features(walk([500] * 6))["height_drop_ratio"] == 0.0


# ------------------------------------------------------- static furniture
def test_something_that_never_moves_reads_as_completely_still():
    """Measured on real footage: 70% of candidate pairs involved a "person"
    that never moved a pixel, and rendering one showed a fire hydrant detected
    as a person in 45 of 45 frames at median confidence 0.46."""
    hydrant = pair_features(walk([500] * 12, step_s=2.0))
    assert hydrant["stillness"] == 1.0


def test_a_person_who_shifts_while_waiting_is_not_furniture():
    """The distinction that matters: a loiterer stands still too, and that is
    the behaviour we are hunting. Furniture never moves at all."""
    waiting = pair_features(walk([500, 503, 499, 506, 501, 508], step_s=2.0))
    assert waiting["stillness"] < 1.0


def test_someone_walking_is_not_still_at_all():
    assert pair_features(walk([100, 300, 500, 700]))["stillness"] == 0.0


# ---------------------------------------------------------------- context
def test_the_hour_is_cyclical_so_midnight_is_next_to_one_am():
    a = pair_features([], Context(hour=23.5))
    b = pair_features([], Context(hour=0.5))
    d = math.dist((a["hour_sin"], a["hour_cos"]), (b["hour_sin"], b["hour_cos"]))
    far = pair_features([], Context(hour=12.0))
    d_far = math.dist((a["hour_sin"], a["hour_cos"]),
                      (far["hour_sin"], far["hour_cos"]))
    assert d < d_far


def test_the_cameras_own_false_alarm_history_is_a_feature():
    f = pair_features([], Context(camera_false_alarm_rate=0.8))
    assert f["camera_false_alarm_rate"] == 0.8


def test_detection_confidence_is_carried_through():
    frames = walk([500, 520, 540], conf=0.3)
    assert pair_features(frames)["detection_confidence"] == pytest.approx(0.3)


def test_frames_where_the_person_was_lost_are_skipped_not_interpolated():
    """Inventing positions would put fabricated motion into a feature the
    model then learns from."""
    frames = walk([500, 520, 540, 560])
    frames[1].person = None
    f = pair_features(frames)
    assert f["dwell_s"] > 0
    assert f["mean_speed_radii_s"] >= 0


# -------------------------------------------------------------- candidates
def test_only_pairs_that_came_close_are_worth_scoring():
    near_car = Box(400, 300, 600, 400)
    far_car = Box(4000, 300, 4200, 400)
    frames = [MultiFrame(ts=i * 0.5,
                         people={7: person_at(500 + i * 5, 400)},
                         vehicles={1: near_car, 2: far_car})
              for i in range(6)]
    pairs = candidate_pairs(frames)
    assert (7, 1) in pairs and (7, 2) not in pairs


def test_candidates_come_back_closest_first():
    frames = [MultiFrame(ts=0.0,
                         people={7: person_at(500, 400)},
                         vehicles={1: Box(400, 300, 600, 400),
                                   2: Box(700, 300, 900, 400)})]
    assert candidate_pairs(frames)[0] == (7, 1)


def test_a_window_can_be_pulled_out_for_one_pair():
    frames = [MultiFrame(ts=i * 0.5,
                         people={7: person_at(500, 400), 9: person_at(100, 400)},
                         vehicles={1: CAR})
              for i in range(4)]
    w = window_for(frames, 7, 1)
    assert len(w) == 4
    assert all(f.people_in_frame == 2 for f in w)
    assert all(f.person is not None and f.vehicle is not None for f in w)


def test_a_pair_that_never_appears_gives_an_empty_window():
    frames = [MultiFrame(ts=0.0, people={7: person_at(500, 400)},
                         vehicles={1: CAR})]
    w = window_for(frames, 99, 1)
    assert w[0].person is None


# --------------------------------------------------------------- readable
def test_the_explanation_is_a_sentence_a_guard_can_check():
    frames = walk([500] * 21, step_s=2.0)
    text = explain(pair_features(frames, Context(night=True)))
    assert "car-length" in text or "stayed close" in text
    assert "night" in text


def test_nothing_notable_says_so():
    assert explain(pair_features(walk([2000, 2500, 3000]))) == "nothing notable"


def test_a_registered_vehicle_appears_as_evidence_against():
    text = explain(pair_features(walk([500] * 21, step_s=2.0),
                                 Context(vehicle_registered=True)))
    assert "registered" in text


def test_contradicting_evidence_survives_truncation():
    """The bug this caught: `top=4` cut off "the vehicle is registered to a
    resident", which is the one phrase that would stop a guard acting. It has
    to survive however many suspicious phrases came first."""
    frames = walk([500, 505, 500, 508, 502, 506, 500, 504] * 3, step_s=2.0)
    for f in frames[4:8]:
        f.person = Box(f.person.x1, f.person.y1 + 30, f.person.x2, f.person.y2)
    text = explain(pair_features(frames,
                                 Context(night=True, vehicle_registered=True)),
                   top=2)
    assert "registered" in text and "night" in text
