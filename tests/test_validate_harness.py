"""Validation-harness helpers: UCF annotation parsing + label loading."""
from pathlib import Path

from validate_triggers import _guess_type, load_labels, parse_ucf_annotations


def test_parse_ucf_annotations(tmp_path):
    f = tmp_path / "ann.txt"
    f.write_text(
        "Stealing079_x264.mp4  Stealing  1350  1800  -1  -1\n"
        "Normal_Videos_003_x264.mp4  Normal  -1  -1  -1  -1\n"
        "Burglary024_x264.mp4  Burglary  90  240  600  720\n")
    ann = parse_ucf_annotations(str(f))
    assert ann["Stealing079_x264.mp4"] == (True, 1350.0, 1800.0)
    assert ann["Normal_Videos_003_x264.mp4"] == (False, 0.0, 0.0)
    assert ann["Burglary024_x264.mp4"][0] is True


def test_guess_type():
    assert _guess_type("Stealing079_x264.mp4") == "stealing"
    assert _guess_type("park_loitering_02.mp4") == "loitering"
    assert _guess_type("Normal_Videos_003.mp4") == "normal"
    assert _guess_type("random_clip.mp4") == "normal"


def test_load_labels_csv(tmp_path):
    (tmp_path / "clips").mkdir()
    (tmp_path / "labels.csv").write_text(
        "filename,type,incident,start_s,end_s,notes\n"
        "gate_theft_01.mp4,vehicle_theft,yes,42,68,break-in\n"
        "gate_normal_01.mp4,normal,no,,,resident parks\n")
    labels = load_labels(tmp_path, None)
    by_name = {l["filename"]: l for l in labels}
    assert by_name["gate_theft_01.mp4"]["incident"] is True
    assert by_name["gate_theft_01.mp4"]["start_s"] == 42.0
    assert by_name["gate_normal_01.mp4"]["incident"] is False
    assert by_name["gate_normal_01.mp4"]["start_s"] is None


def test_ucf_overlay_fills_windows(tmp_path):
    (tmp_path / "clips").mkdir()
    # a clip on disk with no labels.csv entry, filled from UCF annotation
    (tmp_path / "clips" / "Stealing079_x264.mp4").write_bytes(b"x")
    ucf = parse_ucf_annotations_str(
        "Stealing079_x264.mp4  Stealing  1350  1800  -1  -1\n", tmp_path)
    labels = load_labels(tmp_path, ucf)
    lb = next(l for l in labels if l["filename"] == "Stealing079_x264.mp4")
    assert lb["incident"] is True
    assert lb["ucf_frames"] == (1350.0, 1800.0)
    assert lb["type"] == "stealing"


def parse_ucf_annotations_str(text: str, tmp_path: Path):
    f = tmp_path / "ann.txt"
    f.write_text(text)
    return parse_ucf_annotations(str(f))
