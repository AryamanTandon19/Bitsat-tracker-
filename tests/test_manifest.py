"""The clip manifest — the contract every training step reads.

Most of these defend against a dataset that trains a model to be quietly
wrong, which is worse than one that fails loudly.
"""
from __future__ import annotations

import json

import pytest

from training import manifest as M


def rec(**over):
    d = {"clip_id": "ucf_burglary017_t0042",
         "path": "clips/ucf/burglary017_t0042.mp4",
         "source_video": "Burglary017_x264",
         "dataset": "ucf-crime",
         "label": "HOUSE_BREAK_IN",
         "specialist": "break_in",
         "start_s": 42.0, "end_s": 48.0, "fps": 30.0}
    d.update(over)
    return M.ClipRecord(**d)


# ---------------------------------------------------------------- labels
def test_a_valid_record_passes():
    assert M.validate(rec()).duration_s == 6.0


def test_a_label_the_model_does_not_know_is_refused():
    """app.specialist resolves the suspicious class BY NAME. A manifest saying
    'break_in' where the model says 'HOUSE_BREAK_IN' trains a model whose
    output is then read backwards, and nothing fails loudly."""
    with pytest.raises(M.ManifestError, match="resolves the suspicious"):
        M.validate(rec(label="break_in"))


def test_a_label_from_the_other_specialist_is_refused():
    with pytest.raises(M.ManifestError, match="not a class of the"):
        M.validate(rec(label="VEHICLE_THEFT_OR_TAMPERING"))


def test_the_labels_are_exactly_the_ones_the_application_uses():
    """If app.specialist's class tuples ever change, this fails rather than
    the manifest silently drifting out of step with the model."""
    from app.specialist import (UCF_CLASSES, UCF_SUSPICIOUS, VEHICLE_CLASSES,
                                VEHICLE_SUSPICIOUS)
    assert M.SPECIALISTS["break_in"]["classes"] == UCF_CLASSES
    assert M.SPECIALISTS["break_in"]["suspicious"] == UCF_SUSPICIOUS
    assert M.SPECIALISTS["vehicle"]["classes"] == VEHICLE_CLASSES
    assert M.SPECIALISTS["vehicle"]["suspicious"] == VEHICLE_SUSPICIOUS


def test_an_unknown_specialist_is_refused():
    with pytest.raises(M.ManifestError, match="specialist must be"):
        M.validate(rec(specialist="face_recognition"))


def test_the_suspicious_flag_follows_the_label():
    assert M.validate(rec()).suspicious is True
    assert M.validate(rec(label="NORMAL")).suspicious is False


# ------------------------------------------------------------- clip length
def test_a_clip_shorter_than_the_models_window_is_refused():
    """The model reads 4.0s. A 2s clip trains it on something it never sees."""
    with pytest.raises(M.ManifestError, match="outside the"):
        M.validate(rec(start_s=10.0, end_s=12.0))


def test_a_clip_far_longer_than_the_window_is_refused():
    with pytest.raises(M.ManifestError, match="outside the"):
        M.validate(rec(start_s=0.0, end_s=30.0))


def test_the_band_edges_are_allowed():
    M.validate(rec(start_s=0.0, end_s=4.0))
    M.validate(rec(start_s=0.0, end_s=8.0))


def test_a_backwards_clip_is_refused():
    with pytest.raises(M.ManifestError, match="after start_s"):
        M.validate(rec(start_s=48.0, end_s=42.0))


def test_a_negative_start_is_refused():
    with pytest.raises(M.ManifestError, match="negative"):
        M.validate(rec(start_s=-4.0, end_s=1.0))


# ----------------------------------------------------------- the split key
def test_a_clip_with_no_source_video_is_refused():
    """Without it there is no way to keep clips of one event out of two
    splits, and every later number is measuring memorisation."""
    with pytest.raises(M.ManifestError, match="source_video is required"):
        M.validate(rec(source_video=""))


# --------------------------------------------------------- hard negatives
def test_a_hard_negative_cannot_be_labelled_suspicious():
    """The whole point of one is that it LOOKS suspicious and is not."""
    with pytest.raises(M.ManifestError, match="hard negative cannot"):
        M.validate(rec(hard_negative=True))


def test_a_hard_negative_on_the_normal_class_is_fine():
    M.validate(rec(label="NORMAL", hard_negative=True,
                   hn_reason="resident unlocking their own door at night"))


# ------------------------------------------------------------------ crops
def test_a_crop_with_no_area_is_refused():
    with pytest.raises(M.ManifestError, match="no area"):
        M.validate(rec(crop=[10, 10, 10, 40]))


def test_a_malformed_crop_is_refused():
    with pytest.raises(M.ManifestError, match=r"\[x1,y1,x2,y2\]"):
        M.validate(rec(crop=[10, 10, 40]))


def test_no_crop_is_allowed():
    assert M.validate(rec(crop=None)).crop is None


# ------------------------------------------------------------------- paths
def test_paths_are_stored_relative_with_forward_slashes(tmp_path):
    """A dataset built on a Windows laptop gets trained on elsewhere."""
    p = tmp_path / "clips" / "ucf" / "a.mp4"
    assert M.normalise_path(p, tmp_path) == "clips/ucf/a.mp4"


def test_a_path_outside_the_root_is_left_alone(tmp_path):
    assert M.normalise_path("/somewhere/else/a.mp4", tmp_path).endswith("a.mp4")


# --------------------------------------------------------------- file i/o
def test_a_manifest_round_trips(tmp_path):
    path = tmp_path / "manifest.jsonl"
    records = [rec(clip_id=f"c{i}") for i in range(3)]
    assert M.write(path, records) == 3
    back = M.read(path)
    assert [r.clip_id for r in back] == ["c0", "c1", "c2"]
    assert back[0].source_video == "Burglary017_x264"


def test_appending_one_clip_at_a_time_works(tmp_path):
    """clipmine.py appends after each verified clip, so a crash halfway
    through a 2GB batch costs the clip and not the batch."""
    path = tmp_path / "manifest.jsonl"
    for i in range(4):
        M.append_one(path, rec(clip_id=f"c{i}"))
    assert len(M.read(path)) == 4


def test_appending_validates_before_writing(tmp_path):
    path = tmp_path / "manifest.jsonl"
    M.append_one(path, rec(clip_id="good"))
    with pytest.raises(M.ManifestError):
        M.append_one(path, rec(clip_id="bad", label="nonsense"))
    assert [r.clip_id for r in M.read(path)] == ["good"]


def test_a_duplicate_clip_id_is_refused(tmp_path):
    path = tmp_path / "manifest.jsonl"
    M.write(path, [rec(clip_id="same"), rec(clip_id="same")])
    with pytest.raises(M.ManifestError, match="duplicate clip_id"):
        M.read(path)


def test_a_bad_row_names_the_line(tmp_path):
    path = tmp_path / "manifest.jsonl"
    M.write(path, [rec(clip_id="a")])
    with open(path, "a") as f:
        f.write(json.dumps({"clip_id": "b"}) + "\n")
    with pytest.raises(M.ManifestError, match=r":2:"):
        M.read(path)


def test_non_strict_reading_skips_damage(tmp_path):
    path = tmp_path / "manifest.jsonl"
    M.write(path, [rec(clip_id="a")])
    with open(path, "a") as f:
        f.write("{not json\n")
    assert len(M.read(path, strict=False)) == 1


def test_blank_lines_and_comments_are_ignored(tmp_path):
    path = tmp_path / "manifest.jsonl"
    path.write_text("# built by clipmine\n\n" + rec().to_json() + "\n")
    assert len(M.read(path)) == 1


def test_an_unknown_field_is_refused_rather_than_dropped(tmp_path):
    """Silently ignoring a field means a typo'd `hard_negatives` looks like it
    worked and quietly does nothing."""
    with pytest.raises(M.ManifestError, match="unknown field"):
        M.ClipRecord.from_dict({**json.loads(rec().to_json()),
                                "hard_negatives": True})


def test_a_missing_manifest_says_so(tmp_path):
    with pytest.raises(M.ManifestError, match="no manifest at"):
        M.read(tmp_path / "nope.jsonl")


# -------------------------------------------------------------- reporting
def test_the_summary_counts_what_decides_trainability():
    records = [rec(clip_id="a", source_video="v1"),
               rec(clip_id="b", source_video="v1", label="NORMAL"),
               rec(clip_id="c", source_video="v2", label="NORMAL",
                   hard_negative=True, night=True)]
    s = M.summarise(records)
    assert s["clips"] == 3 and s["sources"] == 2
    b = s["by_specialist"]["break_in"]
    assert b["labels"] == {"HOUSE_BREAK_IN": 1, "NORMAL": 2}
    assert b["hard_negatives"] == 1 and b["night"] == 1
    assert b["seconds"] == 18.0


def test_readiness_says_a_tiny_dataset_is_not_ready():
    r = M.readiness([rec()], "break_in")
    assert r["ready"] is False
    assert any("1500" in b for b in r["blockers"])
    assert any("source videos" in b for b in r["blockers"])


def test_readiness_flags_a_missing_class():
    records = [rec(clip_id=f"c{i}", source_video=f"v{i}") for i in range(20)]
    r = M.readiness(records, "break_in")
    assert any("no examples at all of NORMAL" in b for b in r["blockers"])


def test_readiness_flags_a_lopsided_class_balance():
    records = [rec(clip_id=f"c{i}", source_video=f"v{i}") for i in range(20)]
    records += [rec(clip_id="n", source_video="vn", label="NORMAL")]
    r = M.readiness(records, "break_in")
    assert any("class balance" in b for b in r["blockers"])


def test_readiness_flags_too_few_hard_negatives():
    records = ([rec(clip_id=f"a{i}", source_video=f"v{i}") for i in range(10)]
               + [rec(clip_id=f"b{i}", source_video=f"w{i}", label="NORMAL")
                  for i in range(10)])
    r = M.readiness(records, "break_in")
    assert any("hard negatives" in b for b in r["blockers"])


def test_readiness_of_a_specialist_with_no_clips_says_so():
    r = M.readiness([rec()], "vehicle")
    assert r["ready"] is False and r["clips"] == 0
