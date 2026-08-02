"""Step B: single-frame segmentation, with the model still a stand-in.

Nothing here downloads a model. The point of building this order is that the
frame decoding, the response shape, the coordinate space, the cache and the
click path can all be finished and proven before step C swaps a real YOLO11-seg
in behind the same interface.
"""
from __future__ import annotations

import subprocess

import numpy as np
import pytest

from app import segment
from app.segment import (CLASSES, FrameSegmentation, MockSegmenter,
                         SegmentationCache, SegmentedObject, build_segmenter,
                         frame_index_for)
from app.tagging import point_in_mask, polygon_area

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
cv2 = pytest.importorskip("cv2")

from fastapi.testclient import TestClient

from app import dashboard
from app.db import Database
from app.main import load_config

from .conftest import signin


# --------------------------------------------------------------- fixtures
@pytest.fixture()
def clip_file(tmp_path):
    """A real 3-second 320x240 mp4, so frame decoding is genuinely exercised."""
    import imageio_ffmpeg
    path = tmp_path / "sample.mp4"
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "testsrc=size=320x240:rate=10:duration=3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", str(path)], check=True)
    return path


@pytest.fixture()
def ctx(tmp_path, clip_file):
    class Ctx:
        pass
    c = Ctx()
    c.config = load_config("config.yaml")
    c.config["dashboard"]["auth"] = {"enabled": False}
    c.config.setdefault("storage", {})["training_dir"] = str(tmp_path / "clips")
    c.config["train"] = {"segmenter": "mock", "mock_objects": 3}
    c.config_path = str(tmp_path / "config.yaml")
    c.db = Database(str(tmp_path / "t.db"))
    c.workers, c.pipelines, c.analyzer, c.assistant = {}, {}, None, None
    c.clip_id = c.db.add_training_clip(clip_file.name, str(clip_file), 3.0,
                                       "admin", "synthetic")
    yield c
    c.db.close()


@pytest.fixture()
def client(ctx):
    cl = TestClient(dashboard.create_app(ctx))
    signin(cl, ctx.db, "admin")
    return cl


# ------------------------------------------------------- frame indexing
def test_one_timestamp_always_means_one_frame():
    """A frame index that drifted between calls would leave a cached mask a
    frame off the object it outlines."""
    assert frame_index_for(0, 30) == 0
    assert frame_index_for(1000, 30) == 30
    assert frame_index_for(4200, 30) == 126
    assert frame_index_for(4200, 30) == frame_index_for(4200.0, 30.0)


def test_the_index_rounds_rather_than_truncates():
    assert frame_index_for(4183, 30) == 125      # 125.49 -> 125
    assert frame_index_for(4200, 30) == 126      # 126.0
    assert frame_index_for(4217, 30) == 127      # 126.51 -> 127


def test_a_clip_with_no_frame_rate_is_refused():
    with pytest.raises(ValueError, match="frame rate"):
        frame_index_for(1000, 0)


def test_a_negative_timestamp_cannot_produce_a_negative_index():
    assert frame_index_for(-500, 30) == 0


# ---------------------------------------------------------- the stand-in
def test_the_mock_is_deterministic():
    a = MockSegmenter().segment(None, 126)
    b = MockSegmenter().segment(None, 126)
    assert [o.public() for o in a] == [o.public() for o in b]


def test_different_frames_give_different_outlines():
    a = MockSegmenter().segment(None, 10)
    b = MockSegmenter().segment(None, 400)
    assert [o.polygon for o in a] != [o.polygon for o in b]


def test_the_mock_declares_itself_as_a_mock():
    """It reads nothing from the image, so it must never be mistaken for a
    detection."""
    assert MockSegmenter().name == "mock"


def test_mock_outlines_have_area_and_sit_inside_the_frame():
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    for o in MockSegmenter().segment(frame, 7):
        assert polygon_area(o.polygon) > 0
        for x, y in o.polygon:
            assert 0 <= x <= 320 and 0 <= y <= 240


def test_every_object_carries_a_box_as_well_as_a_mask():
    """ByteTrack associates on boxes; a mask that fails to render still has
    something to draw."""
    for o in MockSegmenter().segment(None, 3):
        x1, y1, x2, y2 = o.bbox
        assert x2 > x1 and y2 > y1
        assert o.polygons


# -------------------------------------------------------- serialization
def test_the_response_shape_is_what_the_frontend_expects():
    o = SegmentedObject("frame1-object0", "car", 0.9123,
                        (421.4, 181.2, 781.9, 511.4),
                        [[(436, 211), (703, 188), (759, 466), (476, 501)]], 2)
    pub = o.public()
    assert pub["class_name"] == "car" and pub["class_id"] == 2
    assert pub["confidence"] == 0.9123
    assert pub["bbox"] == {"x_min": 421.4, "y_min": 181.2,
                           "x_max": 781.9, "y_max": 511.4}
    assert pub["polygon"][0] == [436, 211]
    assert pub["polygons"] == [pub["polygon"]]


def test_polygon_points_are_whole_pixels():
    """Sub-pixel precision on an outline is noise and doubles the payload."""
    o = SegmentedObject("x", "car", 0.5, (0, 0, 10, 10),
                        [[(1.234, 2.789), (9.5, 2.1), (5.0, 9.9)]])
    assert all(isinstance(v, int) for pt in o.public()["polygon"] for v in pt)


def test_an_object_split_in_two_keeps_both_pieces():
    o = SegmentedObject("x", "car", 0.5, (0, 0, 400, 100),
                        [[(0, 0), (100, 0), (100, 100)],
                         [(300, 0), (400, 0), (400, 100)]])
    assert len(o.public()["polygons"]) == 2
    # .polygon is the largest piece, for callers that want just one
    assert polygon_area(o.polygon) == max(polygon_area(p) for p in o.polygons)


def test_an_object_with_no_mask_still_serializes():
    o = SegmentedObject("x", "car", 0.5, (0, 0, 10, 10))
    assert o.public()["polygon"] == [] and o.public()["polygons"] == []


def test_frame_segmentation_serializes_whole():
    f = FrameSegmentation(1, 126, 4200, 1920, 1080, "mock",
                          MockSegmenter().segment(None, 126))
    pub = f.public()
    assert pub["frame_index"] == 126 and pub["model"] == "mock"
    assert pub["frame_width"] == 1920 and len(pub["objects"]) == 3


# ---------------------------------------------------------------- cache
def test_the_cache_returns_what_was_put_in():
    c = SegmentationCache(4)
    assert c.get(("a", 1)) is None and c.misses == 1
    c.put(("a", 1), "value")
    assert c.get(("a", 1)) == "value" and c.hits == 1


def test_the_cache_is_bounded():
    """Segmentation is cheap to redo and expensive to hoard."""
    c = SegmentationCache(3)
    for i in range(5):
        c.put(("clip", i), i)
    assert len(c) == 3
    assert c.get(("clip", 0)) is None      # oldest evicted
    assert c.get(("clip", 4)) == 4


def test_clearing_the_cache_empties_it():
    c = SegmentationCache(4)
    c.put(("a", 1), "v")
    c.clear()
    assert len(c) == 0 and c.get(("a", 1)) is None


# ------------------------------------------------------------- factory
def test_the_factory_defaults_to_the_mock():
    assert build_segmenter({}).name == "mock"
    assert build_segmenter({"segmenter": "mock"}).name == "mock"


def test_an_unknown_segmenter_fails_loudly():
    """Silently returning outlines nobody should trust would be worse."""
    with pytest.raises(ValueError, match="use 'yolo' or 'mock'"):
        build_segmenter({"segmenter": "detectron"})


# ------------------------------------------------------------- the API
def test_segmenting_a_frame_returns_outlines(client, ctx):
    r = client.post(f"/api/train/clips/{ctx.clip_id}/segment-frame",
                    data={"timestamp_ms": 1000})
    assert r.status_code == 200
    body = r.json()
    assert body["model"] == "mock"
    assert body["frame_index"] == 10          # 1s at 10fps
    assert body["frame_width"] == 320 and body["frame_height"] == 240
    assert len(body["objects"]) == 3
    assert body["objects"][0]["polygon"]


def test_the_same_frame_is_served_from_cache(client, ctx):
    for _ in range(3):
        client.post(f"/api/train/clips/{ctx.clip_id}/segment-frame",
                    data={"timestamp_ms": 1000})
    stats = client.get("/api/train/segment-cache").json()
    assert stats["entries"] == 1 and stats["hits"] >= 2


def test_force_refresh_bypasses_the_cache(client, ctx):
    client.post(f"/api/train/clips/{ctx.clip_id}/segment-frame",
                data={"timestamp_ms": 1000})
    before = client.get("/api/train/segment-cache").json()["hits"]
    client.post(f"/api/train/clips/{ctx.clip_id}/segment-frame",
                data={"timestamp_ms": 1000, "force_refresh": "true"})
    assert client.get("/api/train/segment-cache").json()["hits"] == before


def test_clearing_the_cache_does_not_touch_saved_work(client, ctx):
    """Cached AI results are disposable. Annotations are not."""
    ctx.db.add_training_mark(ctx.clip_id, 1, 2, "a car", "normal", "", "admin")
    client.post(f"/api/train/clips/{ctx.clip_id}/segment-frame",
                data={"timestamp_ms": 1000})
    assert client.delete("/api/train/segment-cache").status_code == 200
    assert client.get("/api/train/segment-cache").json()["entries"] == 0
    assert len(ctx.db.training_marks(ctx.clip_id)) == 1


def test_a_timestamp_past_the_end_is_refused(client, ctx):
    r = client.post(f"/api/train/clips/{ctx.clip_id}/segment-frame",
                    data={"timestamp_ms": 999999})
    assert r.status_code == 400 and "past the end" in r.json()["detail"]


def test_a_negative_timestamp_is_refused(client, ctx):
    r = client.post(f"/api/train/clips/{ctx.clip_id}/segment-frame",
                    data={"timestamp_ms": -100})
    assert r.status_code == 400


def test_a_missing_clip_is_a_404(client):
    r = client.post("/api/train/clips/9999/segment-frame",
                    data={"timestamp_ms": 0})
    assert r.status_code == 404


def test_a_clip_whose_file_has_gone_is_a_404(client, ctx, tmp_path):
    cid = ctx.db.add_training_clip("ghost.mp4", str(tmp_path / "ghost.mp4"),
                                   5.0, "admin", "")
    assert client.post(f"/api/train/clips/{cid}/segment-frame",
                       data={"timestamp_ms": 0}).status_code == 404


def test_a_model_that_fails_to_load_reports_it(client, ctx, monkeypatch):
    class Broken:
        name = "broken"
        def segment(self, frame, idx):
            raise RuntimeError("model weights are missing")
    monkeypatch.setattr(segment, "build_segmenter", lambda cfg: Broken())
    cl = TestClient(dashboard.create_app(ctx))
    signin(cl, ctx.db, "admin")
    r = cl.post(f"/api/train/clips/{ctx.clip_id}/segment-frame",
                data={"timestamp_ms": 1000})
    assert r.status_code == 503 and "weights are missing" in r.json()["detail"]


def test_a_frame_with_nothing_on_it_is_a_valid_answer(client, ctx, monkeypatch):
    class Empty:
        name = "mock"
        def segment(self, frame, idx):
            return []
    monkeypatch.setattr(segment, "build_segmenter", lambda cfg: Empty())
    cl = TestClient(dashboard.create_app(ctx))
    signin(cl, ctx.db, "admin")
    r = cl.post(f"/api/train/clips/{ctx.clip_id}/segment-frame",
                data={"timestamp_ms": 1000})
    assert r.status_code == 200 and r.json()["objects"] == []


# ------------------------------------------------------ click selection
def _click_centre_of_first_object(client, clip_id, display=(640, 480)):
    seg = client.post(f"/api/train/clips/{clip_id}/segment-frame",
                      data={"timestamp_ms": 1000}).json()
    o = seg["objects"][0]
    fx = (o["bbox"]["x_min"] + o["bbox"]["x_max"]) / 2
    fy = (o["bbox"]["y_min"] + o["bbox"]["y_max"]) / 2
    # the test clip is 4:3 and so is the display, so there are no bars here
    sx = display[0] / seg["frame_width"]
    sy = display[1] / seg["frame_height"]
    return o, client.post(f"/api/train/clips/{clip_id}/select-object",
                          data={"timestamp_ms": 1000,
                                "display_x": fx * sx, "display_y": fy * sy,
                                "display_width": display[0],
                                "display_height": display[1]}).json()


def test_clicking_an_object_selects_it_by_its_outline(client, ctx):
    obj, out = _click_centre_of_first_object(client, ctx.clip_id)
    assert out["selection_method"] == "mask_hit"
    assert out["recommended_object_id"] == obj["temporary_object_id"]
    assert out["clicked_on_video"] is True
    assert out["recommended"]["polygon"]


def test_the_selection_reports_the_frame_point_it_used(client, ctx):
    _, out = _click_centre_of_first_object(client, ctx.clip_id)
    assert 0 <= out["frame_point"]["x"] <= 320
    assert 0 <= out["frame_point"]["y"] <= 240


def test_every_candidate_comes_back_not_just_the_winner(client, ctx):
    _, out = _click_centre_of_first_object(client, ctx.clip_id)
    assert out["recommended_object_id"] in out["overlapping_candidates"]
    assert len(out["objects"]) == 3      # the whole frame, for the overlay


def test_a_click_on_empty_space_falls_through_to_drawing(client, ctx):
    out = client.post(f"/api/train/clips/{ctx.clip_id}/select-object",
                      data={"timestamp_ms": 1000, "display_x": 2, "display_y": 2,
                            "display_width": 640, "display_height": 480}).json()
    assert out["selection_method"] in ("manual_box", "nearby_detection")


def test_a_letterboxed_click_still_lands_on_the_object(client, ctx):
    """4:3 footage in a 16:9 player: bars at the sides. Same object, different
    display coordinates."""
    seg = client.post(f"/api/train/clips/{ctx.clip_id}/segment-frame",
                      data={"timestamp_ms": 1000}).json()
    o = seg["objects"][0]
    fx = (o["bbox"]["x_min"] + o["bbox"]["x_max"]) / 2
    fy = (o["bbox"]["y_min"] + o["bbox"]["y_max"]) / 2
    dw, dh = 1600, 900
    scale = min(dw / 320, dh / 240)
    off_x = (dw - 320 * scale) / 2
    out = client.post(f"/api/train/clips/{ctx.clip_id}/select-object",
                      data={"timestamp_ms": 1000,
                            "display_x": off_x + fx * scale,
                            "display_y": fy * scale,
                            "display_width": dw, "display_height": dh}).json()
    assert out["recommended_object_id"] == o["temporary_object_id"]


def test_a_click_on_a_black_bar_says_so(client, ctx):
    out = client.post(f"/api/train/clips/{ctx.clip_id}/select-object",
                      data={"timestamp_ms": 1000, "display_x": 5,
                            "display_y": 450, "display_width": 1600,
                            "display_height": 900}).json()
    assert out["clicked_on_video"] is False


def test_a_zero_sized_display_is_refused(client, ctx):
    r = client.post(f"/api/train/clips/{ctx.clip_id}/select-object",
                    data={"timestamp_ms": 1000, "display_x": 10, "display_y": 10,
                          "display_width": 0, "display_height": 480})
    assert r.status_code == 400


# --------------------------------------------------------- permissions
def test_a_guard_cannot_segment_or_select(ctx):
    cl = TestClient(dashboard.create_app(ctx))
    signin(cl, ctx.db, "guard")
    assert cl.post(f"/api/train/clips/{ctx.clip_id}/segment-frame",
                   data={"timestamp_ms": 0}).status_code == 403
    assert cl.get("/api/train/segment-cache").status_code == 403


def test_anonymous_callers_get_nothing(ctx):
    cl = TestClient(dashboard.create_app(ctx))
    assert cl.post(f"/api/train/clips/{ctx.clip_id}/segment-frame",
                   data={"timestamp_ms": 0}).status_code == 401


# --------------------------------------------------------------- page
def test_the_overlay_controls_are_on_the_page(client):
    html = client.get("/train").text
    for control in ("id=\"seg\"", "id=\"selmode\"", "id=\"t-mask\"",
                    "id=\"t-box\"", "id=\"t-label\"", "id=\"clearsel\""):
        assert control in html, control


def test_the_page_draws_masks_not_only_boxes(client):
    html = client.get("/train").text
    assert "function drawMasks" in html
    assert "polygons" in html


def test_the_classes_offered_cover_the_tagging_vocabulary():
    for c in ("person", "car", "bag", "package", "door", "unknown"):
        assert c in CLASSES


# =====================================================================
# Step C — the real segmenter. No test here downloads a model: the
# ultralytics result is faked, because what needs proving is the
# conversion and the failure handling, not that YOLO can see a car.
# =====================================================================
from app.segment import YoloSegmenter


class FakeBox:
    def __init__(self, xyxy, cls, conf, tid=None):
        self.xyxy = [xyxy]
        self.cls = [cls]
        self.conf = [conf]
        self.id = [tid] if tid is not None else None


class FakeBoxes:
    def __init__(self, boxes):
        self._b = boxes
    def __len__(self):
        return len(self._b)
    def __getitem__(self, i):
        return self._b[i]


class FakeMasks:
    def __init__(self, polys):
        self.xy = polys


class FakeResult:
    def __init__(self, boxes, polys=None, names=None):
        self.boxes = FakeBoxes(boxes)
        self.masks = FakeMasks(polys) if polys is not None else None
        self.names = names or {0: "person", 2: "car"}


class FakeModel:
    def __init__(self, result):
        self.result = result
        self.calls = []
    def predict(self, frame, **kw):
        self.calls.append(kw)
        return [self.result]


def _seg_with(result, **kw):
    s = YoloSegmenter(**kw)
    s._model = FakeModel(result)
    return s


def test_the_real_segmenter_is_not_loaded_until_it_is_used():
    """Opening the app must not cost a model load."""
    s = YoloSegmenter()
    assert s._model is None
    assert s.name == "yolo11n-seg.pt"


def test_the_factory_builds_it_from_config():
    s = build_segmenter({"segmenter": "yolo", "seg_model": "yolo11n-seg.pt",
                         "seg_confidence": 0.5})
    assert isinstance(s, YoloSegmenter)
    assert s.conf == 0.5 and s._model is None


def test_ultralytics_output_becomes_our_objects():
    r = FakeResult([FakeBox([100.0, 200.0, 300.0, 400.0], 2, 0.91)],
                   [[(110, 210), (290, 205), (295, 390), (105, 395)]])
    objs = _seg_with(r).segment(None, 126)
    assert len(objs) == 1
    o = objs[0]
    assert o.class_name == "car" and o.class_id == 2
    assert o.confidence == pytest.approx(0.91)
    assert o.bbox == (100.0, 200.0, 300.0, 400.0)
    assert len(o.polygon) == 4
    assert o.temporary_object_id == "frame126-object0"


def test_mask_coordinates_are_taken_as_original_pixels():
    """Ultralytics .xy has already undone its own letterboxing. Re-scaling it
    here would be silently wrong in a way nothing downstream could detect."""
    poly = [(110.5, 210.5), (290, 205), (295, 390), (105, 395)]
    r = FakeResult([FakeBox([100.0, 200.0, 300.0, 400.0], 2, 0.9)], [poly])
    o = _seg_with(r).segment(None, 1)[0]
    assert o.polygon == [(110.5, 210.5), (290.0, 205.0), (295.0, 390.0),
                         (105.0, 395.0)]


def test_a_result_with_no_masks_still_gives_boxes():
    """A detection-only result must degrade to a box, not vanish."""
    r = FakeResult([FakeBox([10.0, 10.0, 50.0, 50.0], 0, 0.8)], polys=None)
    o = _seg_with(r).segment(None, 1)[0]
    assert o.bbox == (10.0, 10.0, 50.0, 50.0) and o.polygons == []


def test_a_degenerate_contour_falls_back_to_the_box():
    """A mask that collapses to a line would make the object unclickable."""
    r = FakeResult([FakeBox([10.0, 10.0, 50.0, 50.0], 0, 0.8)],
                   [[(10, 10), (20, 20), (30, 30)]])       # collinear
    o = _seg_with(r).segment(None, 1)[0]
    assert o.polygons == []


def test_an_empty_frame_returns_nothing_rather_than_failing():
    assert _seg_with(FakeResult([])).segment(None, 1) == []


def test_a_track_id_is_carried_through_when_present():
    r = FakeResult([FakeBox([1.0, 1.0, 9.0, 9.0], 0, 0.7, tid=7)],
                   [[(1, 1), (9, 1), (9, 9)]])
    assert _seg_with(r).segment(None, 1)[0].track_id == 7


def test_one_busy_frame_cannot_flood_the_interface():
    boxes = [FakeBox([float(i), 1.0, float(i + 5), 9.0], 0, 0.5)
             for i in range(200)]
    objs = _seg_with(FakeResult(boxes), max_objects=40).segment(None, 1)
    assert len(objs) == 40


def test_confidence_and_size_reach_the_model():
    r = FakeResult([FakeBox([1.0, 1.0, 9.0, 9.0], 0, 0.7)])
    s = _seg_with(r, conf=0.42, imgsz=960, device="cpu")
    s.segment(None, 1)
    assert s._model.calls[0]["conf"] == 0.42
    assert s._model.calls[0]["imgsz"] == 960
    assert s._model.calls[0]["device"] == "cpu"


def test_the_device_falls_back_to_cpu():
    assert YoloSegmenter(device="cpu")._pick_device() == "cpu"
    assert YoloSegmenter(device="auto")._pick_device() in ("cpu", "cuda:0")


def test_a_model_that_cannot_load_says_why_and_how_to_fix_it():
    """'No objects found' would be a lie if the weights never loaded."""
    s = YoloSegmenter(weights="/nonexistent/nope-seg.pt")
    with pytest.raises(RuntimeError) as e:
        s.load()
    msg = str(e.value)
    assert "nope-seg.pt" in msg and ("network" in msg or "beside config" in msg)


def test_loading_twice_returns_the_same_model():
    """Per-request loading would make every click pay for it."""
    s = YoloSegmenter()
    sentinel = object()
    s._model = sentinel
    assert s.load() is sentinel and s.load() is sentinel


def test_the_output_is_selectable_by_the_geometry_layer():
    """The two halves meet here: a real-shaped result must click correctly."""
    r = FakeResult([FakeBox([100.0, 100.0, 300.0, 300.0], 2, 0.9)],
                   [[(100, 100), (300, 100), (300, 300), (100, 300)]])
    objs = _seg_with(r).segment(None, 1)
    from app.tagging import select
    assert select(objs, 200, 200)["method"] == "mask_hit"
    assert select(objs, 500, 500)["method"] == "manual_box"
