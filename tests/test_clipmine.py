"""Cutting clips, verifying them, and deleting the raw video.

Verification is tested against real encoded files rather than arrays, because
every check here exists to catch something that happens between "ffmpeg exited
0" and "this is a training example".
"""
from __future__ import annotations

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from training import clipmine as C
from training import manifest as M
from training import verify as V


def write_video(path, frames=120, w=160, h=120, fps=20, kind="moving"):
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps,
                          (w, h))
    rng = np.random.default_rng(3)
    for i in range(frames):
        if kind == "blank":
            img = np.full((h, w, 3), 128, dtype=np.uint8)
        elif kind == "frozen":
            img = (rng.integers(0, 255, (h, w, 3))
                   if i == 0 else img)          # noqa: F821 - same frame reused
            img = img.astype(np.uint8)
        else:
            img = (rng.integers(60, 90, (h, w, 3))).astype(np.uint8)
            cx = 20 + (i * 2) % (w - 40)
            cv2.rectangle(img, (cx, 40), (cx + 24, 80), (230, 230, 230), -1)
        out.write(img)
    out.release()
    return path


# --------------------------------------------------------- clip planning
def test_clips_are_spread_across_the_source_not_taken_from_the_front():
    """Ten consecutive clips off the start are one situation sampled ten
    times."""
    plan = C.plan_clips(duration_s=300.0, clip_s=6.0, stride_s=25.0,
                        max_clips=5)
    assert len(plan) == 5
    starts = [s for s, _e in plan]
    assert starts[0] == 0.0
    assert starts[-1] + 6.0 == pytest.approx(300.0)
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert min(gaps) > 25.0


def test_every_planned_clip_is_the_requested_length():
    for s, e in C.plan_clips(300.0, 6.0, 25.0, 8):
        assert e - s == pytest.approx(6.0)


def test_a_source_shorter_than_one_clip_yields_nothing():
    assert C.plan_clips(3.0, 6.0, 25.0, 5) == []


def test_a_source_with_room_for_exactly_one_clip_yields_one():
    plan = C.plan_clips(7.0, 6.0, 25.0, 5)
    assert len(plan) == 1 and plan[0] == (0.0, 6.0)


def test_the_plan_never_runs_past_the_end():
    for s, e in C.plan_clips(60.0, 8.0, 5.0, 20):
        assert e <= 60.0 + 1e-6


def test_asking_for_more_clips_than_fit_gives_what_fits():
    assert len(C.plan_clips(60.0, 6.0, 25.0, 50)) <= 3


# ------------------------------------------------------------ verification
def test_a_good_clip_passes_every_check(tmp_path):
    src = write_video(tmp_path / "src.mp4", frames=200)
    dest = tmp_path / "clip.mp4"
    import fetch_testset
    assert C.cut(fetch_testset.ffmpeg(), src, dest, 1.0, 6.0, width=128)
    v = V.verify_clip(dest, 6.0)
    assert v.ok, v.reason
    assert v.duration_s == pytest.approx(6.0, abs=0.4)


def test_a_blank_clip_is_rejected(tmp_path):
    """A lens cap, a dropped stream, or the grey frame a decoder emits on
    error."""
    src = write_video(tmp_path / "blank.mp4", kind="blank")
    dest = tmp_path / "clip.mp4"
    import fetch_testset
    C.cut(fetch_testset.ffmpeg(), src, dest, 0.0, 5.0, width=128)
    v = V.verify_clip(dest, 5.0)
    assert not v.ok and "not_blank" in [c.name for c in v.failures]


def test_darkness_is_not_treated_as_blankness():
    """MEVA's genuine night footage sits at mean brightness 30. A brightness
    floor would throw away exactly the data the product is short of."""
    rng = np.random.default_rng(1)
    dark = (rng.integers(10, 55, (120, 160, 3))).astype(np.uint8)
    assert V.frame_variance(dark) > V.MIN_VARIANCE
    assert V.check_not_blank([V.frame_variance(dark)] * 8).ok


def test_a_uniform_frame_has_no_variance():
    flat = np.full((120, 160, 3), 200, dtype=np.uint8)
    assert V.frame_variance(flat) == 0.0
    assert not V.check_not_blank([V.frame_variance(flat)]).ok


def test_a_frozen_clip_is_rejected():
    """A stalled stream teaches a video model nothing."""
    rng = np.random.default_rng(5)
    same = (rng.integers(0, 255, (120, 160, 3))).astype(np.uint8)
    motions = [V.motion_between(same, same) for _ in range(7)]
    assert not V.check_not_frozen(motions).ok


def test_a_moving_clip_passes_the_frozen_check():
    rng = np.random.default_rng(6)
    a = (rng.integers(0, 255, (120, 160, 3))).astype(np.uint8)
    b = (rng.integers(0, 255, (120, 160, 3))).astype(np.uint8)
    assert V.check_not_frozen([V.motion_between(a, b)]).ok


def test_a_clip_that_lost_most_of_its_length_is_rejected():
    """`-ss` seeks to a keyframe: ask for 6s and you can get 2. A manifest
    claiming 6s over a 2s file trains on padding."""
    assert not V.check_duration(2.0, 6.0).ok
    assert "keyframe" in V.check_duration(2.0, 6.0).detail


def test_a_small_seek_error_is_tolerated():
    assert V.check_duration(5.8, 6.0).ok


def test_a_file_with_no_frames_is_rejected(tmp_path):
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"\x00" * 64)
    v = V.verify_clip(empty, 6.0)
    assert not v.ok and "decodes" in [c.name for c in v.failures]


# ------------------------------------------------------- the object check
class FakeDetector:
    def __init__(self, classes):
        self.classes = classes

    def track(self, frame):
        return [type("D", (), {"cls_name": c})() for c in self.classes]


def test_a_clip_without_the_object_its_label_claims_is_rejected(tmp_path):
    """A clip labelled NORMAL_VEHICLE_ACTIVITY with no vehicle in it is
    grass."""
    src = write_video(tmp_path / "src.mp4", frames=200)
    dest = tmp_path / "clip.mp4"
    import fetch_testset
    C.cut(fetch_testset.ffmpeg(), src, dest, 1.0, 6.0, width=128)
    v = V.verify_clip(dest, 6.0, detector=FakeDetector(["person"]),
                      want_classes=("car", "truck"))
    assert not v.ok and "has_object" in [c.name for c in v.failures]


def test_a_clip_with_the_object_passes(tmp_path):
    src = write_video(tmp_path / "src.mp4", frames=200)
    dest = tmp_path / "clip.mp4"
    import fetch_testset
    C.cut(fetch_testset.ffmpeg(), src, dest, 1.0, 6.0, width=128)
    v = V.verify_clip(dest, 6.0, detector=FakeDetector(["car"]),
                      want_classes=("car", "truck"))
    assert v.ok, v.reason


def test_a_skipped_object_check_is_reported_not_silently_passed(tmp_path):
    """A verification that quietly stops verifying is worse than none."""
    src = write_video(tmp_path / "src.mp4", frames=200)
    dest = tmp_path / "clip.mp4"
    import fetch_testset
    C.cut(fetch_testset.ffmpeg(), src, dest, 1.0, 6.0, width=128)
    v = V.verify_clip(dest, 6.0, detector=None, want_classes=("car",))
    assert v.ok
    assert any("no detector" in s for s in v.skipped)


# --------------------------------------------------- the whole cycle
def test_mining_a_source_writes_verified_clips_and_a_manifest(tmp_path):
    import fetch_testset
    src = write_video(tmp_path / "long.mp4", frames=600)   # 30s at 20fps
    out = tmp_path / "data"
    man = out / "manifest.jsonl"
    res = C.mine_one_source(
        fetch_testset.ffmpeg(), src, "long_source", out_dir=out,
        manifest_path=man, specialist="vehicle",
        label="NORMAL_VEHICLE_ACTIVITY", dataset="test", clip_s=6.0,
        stride_s=6.0, max_clips=3, width=128, crf=30)
    assert res["kept"] == 3, res["why"]
    records = M.read(man)
    assert len(records) == 3
    assert {r.source_video for r in records} == {"long_source"}
    assert all(r.label == "NORMAL_VEHICLE_ACTIVITY" for r in records)
    assert all((out / "test").joinpath(f"{r.clip_id}.mp4").exists()
               for r in records)


def test_a_rejected_clip_leaves_no_file_behind(tmp_path):
    """Otherwise the disk fills with clips nothing references."""
    import fetch_testset
    src = write_video(tmp_path / "blank.mp4", frames=400, kind="blank")
    out = tmp_path / "data"
    res = C.mine_one_source(
        fetch_testset.ffmpeg(), src, "blank_source", out_dir=out,
        manifest_path=out / "manifest.jsonl", specialist="vehicle",
        label="NORMAL_VEHICLE_ACTIVITY", dataset="test", clip_s=6.0,
        stride_s=6.0, max_clips=2, width=128, crf=30)
    assert res["kept"] == 0 and res["rejected"] == 2
    assert list((out / "test").glob("*.mp4")) == []


def test_mining_is_resumable(tmp_path):
    """A closed laptop lid costs the clip in flight, not the batch."""
    import fetch_testset
    src = write_video(tmp_path / "long.mp4", frames=600)
    out = tmp_path / "data"
    man = out / "manifest.jsonl"
    C.mine_one_source(fetch_testset.ffmpeg(), src, "s1", out_dir=out,
                      manifest_path=man, specialist="vehicle",
                      label="NORMAL_VEHICLE_ACTIVITY", dataset="test",
                      clip_s=6.0, stride_s=6.0, max_clips=2, width=128, crf=30)
    assert C.already_mined(M.read(man)) == {"s1"}


def test_a_source_that_will_not_decode_is_reported_not_crashed(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    res = C.mine_one_source(
        "ffmpeg", bad, "bad_source", out_dir=tmp_path / "d",
        manifest_path=tmp_path / "d" / "m.jsonl", specialist="vehicle",
        label="NORMAL_VEHICLE_ACTIVITY", dataset="test", clip_s=6.0,
        stride_s=6.0, max_clips=2, width=128, crf=30)
    assert res["kept"] == 0 and "would not decode" in res["why"][0]


def test_stored_clips_keep_enough_resolution_to_crop_later(tmp_path):
    """The correction to the plan: storing at 128x128 would foreclose the
    crop-to-the-interaction idea, because the pixels would be gone."""
    import fetch_testset
    src = write_video(tmp_path / "src.mp4", frames=200, w=640, h=480)
    dest = tmp_path / "clip.mp4"
    C.cut(fetch_testset.ffmpeg(), src, dest, 0.0, 5.0, width=640)
    cap = cv2.VideoCapture(str(dest))
    try:
        assert int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) == 640
    finally:
        cap.release()
