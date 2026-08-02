"""Tagging several objects at once, and following one through the video.

The tracking endpoint runs a real job on a worker thread, so these tests use
the mock segmenter and a generated clip: the point is the plumbing — that a
job starts, reports progress, writes a track and its frames, and that the
frames it reconstructed stay distinguishable from the frames it saw.
"""
from __future__ import annotations

import io
import json
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from fastapi.testclient import TestClient

from app import dashboard
from app.db import Database
from app.main import load_config

from .conftest import signin


def make_clip(path, frames=40, w=320, h=240, fps=20):
    """A moving blob, written as a real video file so the decoder has
    something honest to read."""
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps,
                          (w, h))
    for i in range(frames):
        img = np.zeros((h, w, 3), dtype=np.uint8)
        cx = 40 + i * 5
        cv2.rectangle(img, (cx - 18, 100), (cx + 18, 140), (200, 200, 200), -1)
        out.write(img)
    out.release()
    return path


@pytest.fixture()
def ctx(tmp_path):
    class Ctx:
        pass
    c = Ctx()
    c.config = load_config("config.yaml")
    c.config["dashboard"]["auth"] = {"enabled": False}
    c.config.setdefault("storage", {})["training_dir"] = str(tmp_path / "clips")
    # the stand-in segmenter: deterministic shapes, no model, no download
    c.config["train"] = {**(c.config.get("train") or {}), "segmenter": "mock",
                         "mock_objects": 3,
                         "tracking": {"stride": 1, "max_frames": 12}}
    c.config_path = str(tmp_path / "config.yaml")
    c.db = Database(str(tmp_path / "t.db"))
    c.workers, c.pipelines, c.analyzer, c.assistant = {}, {}, None, None
    yield c
    c.db.close()


@pytest.fixture()
def client(ctx):
    cl = TestClient(dashboard.create_app(ctx))
    signin(cl, ctx.db, "admin", name="A. Admin")
    return cl


@pytest.fixture()
def clip(client, tmp_path):
    src = make_clip(tmp_path / "src.mp4")
    r = client.post("/api/train/clips",
                    files={"file": ("moving.mp4", io.BytesIO(src.read_bytes()),
                                    "video/mp4")},
                    data={"source": "generated"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def start_track(client, clip_id, timestamp_ms=100, **over):
    """Pick the first object the segmenter offers on that frame, then follow it.

    Following needs an explicit target: "follow this one" has to name a this,
    and an endpoint that guesses would happily follow the wrong car.
    """
    seg = client.post(f"/api/train/clips/{clip_id}/segment-frame",
                      data={"timestamp_ms": timestamp_ms})
    assert seg.status_code == 200, seg.text
    objects = seg.json()["objects"]
    assert objects, "the segmenter found nothing to follow"
    data = {"timestamp_ms": timestamp_ms,
            "temporary_object_id": objects[0]["temporary_object_id"]}
    data.update(over)
    return client.post(f"/api/train/clips/{clip_id}/track", data=data)


def wait_for(client, job_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        s = client.get(f"/api/train/track-jobs/{job_id}").json()
        if s["state"] in ("done", "failed", "cancelled"):
            return s
        time.sleep(0.15)
    raise AssertionError("the tracking job never finished")


# ---------------------------------------------------------------- batch
def batch(client, clip_id, objects, **over):
    data = {"clip_id": clip_id, "frame_index": 4, "timestamp_ms": 200,
            "objects": json.dumps(objects)}
    data.update(over)
    return client.post("/api/train/annotations/batch", data=data)


def obj(cx, cy, cls="car"):
    return {"class_name": cls, "confidence": 0.8,
            "polygons": [[[cx - 20, cy - 10], [cx + 20, cy - 10],
                          [cx + 20, cy + 10], [cx - 20, cy + 10]]],
            "frame_width": 320, "frame_height": 240, "model": "mock"}


def test_several_objects_are_tagged_in_one_go(client, clip):
    r = batch(client, clip, [obj(60, 60), obj(160, 60), obj(260, 60)])
    assert r.status_code == 200, r.text
    assert r.json()["saved"] == 3
    assert len(client.get(f"/api/train/annotations?clip_id={clip}").json()) == 3


def test_a_mixed_selection_keeps_each_object_s_own_class(client, clip):
    """Five cars and the person walking between them is the normal case;
    forcing one category on all of them would mislabel the odd one out."""
    batch(client, clip, [obj(60, 60, "car"), obj(160, 60, "person")],
          keep_own_class=True)
    got = client.get(f"/api/train/annotations?clip_id={clip}").json()
    assert sorted(a["category"] for a in got) == ["car", "person"]


def test_one_category_can_be_forced_across_the_whole_selection(client, clip):
    batch(client, clip, [obj(60, 60, "car"), obj(160, 60, "truck")],
          keep_own_class=False, category="car")
    got = client.get(f"/api/train/annotations?clip_id={clip}").json()
    assert {a["category"] for a in got} == {"car"}


def test_forcing_a_category_without_naming_one_is_refused(client, clip):
    r = batch(client, clip, [obj(60, 60)], keep_own_class=False)
    assert r.status_code == 400


def test_a_shared_label_and_tags_reach_every_object(client, clip):
    batch(client, clip, [obj(60, 60), obj(160, 60)],
          custom_label="row A", tags="daylight, far side")
    got = client.get(f"/api/train/annotations?clip_id={clip}").json()
    assert all(a["custom_label"] == "row A" for a in got)
    assert all(a["tags"] == ["daylight", "far side"] for a in got)


def test_an_empty_selection_is_refused(client, clip):
    assert batch(client, clip, []).status_code == 400


def test_one_bad_object_fails_the_whole_batch_rather_than_half_saving_it(
        client, clip):
    bad = obj(60, 60)
    bad["polygons"] = []
    bad.pop("frame_width")
    r = batch(client, clip, [obj(20, 20), bad])
    assert r.status_code == 400
    assert client.get(f"/api/train/annotations?clip_id={clip}").json() == []


def test_objects_that_is_not_json_says_so(client, clip):
    r = client.post("/api/train/annotations/batch",
                    data={"clip_id": clip, "frame_index": 1,
                          "timestamp_ms": 50, "objects": "{not json"})
    assert r.status_code == 400 and "JSON" in r.json()["detail"]


# --------------------------------------------------------------- tracking
def test_following_an_object_writes_a_track_and_its_frames(client, clip):
    start = start_track(client, clip, custom_label="the blob")
    assert start.status_code == 200, start.text
    job = start.json()
    assert job["state"] in ("queued", "running")

    done = wait_for(client, job["id"])
    assert done["state"] == "done", done.get("error")
    assert done["track_id"]
    assert done["saved"] > 1

    tracks = client.get(f"/api/train/tracks?clip_id={clip}").json()
    assert len(tracks) == 1
    t = tracks[0]
    assert t["custom_label"] == "the blob"
    assert t["frames"] == done["saved"]
    assert t["end_frame"] > t["start_frame"]
    assert t["created_by"] == "admin"


def test_the_frames_of_a_track_are_ordinary_annotations(client, clip):
    """No second kind of annotation: a frame the tracker produced is
    corrected, reviewed and exported exactly like a hand-tagged one."""
    job = start_track(client, clip).json()
    done = wait_for(client, job["id"])
    rows = client.get(f"/api/train/annotations?clip_id={clip}").json()
    assert len(rows) == done["saved"]
    assert all(r["track_ref"] == done["track_id"] for r in rows)
    assert all(r["source"] in ("tracked", "interpolated") for r in rows)
    assert all(r["review_status"] == "draft" for r in rows)


def test_a_reconstructed_frame_never_claims_to_have_been_seen(client, clip):
    job = start_track(client, clip).json()
    wait_for(client, job["id"])
    rows = client.get(f"/api/train/annotations?clip_id={clip}").json()
    for r in rows:
        if r["source"] == "interpolated":
            assert r["detection_confidence"] is None
            assert r["mask_source"] == "interpolated"


def test_the_track_detail_returns_the_whole_path(client, clip):
    job = start_track(client, clip).json()
    done = wait_for(client, job["id"])
    d = client.get(f"/api/train/tracks/{done['track_id']}").json()
    assert d["track"]["id"] == done["track_id"]
    assert len(d["frames"]) == done["saved"]
    frames = [f["frame_index"] for f in d["frames"]]
    assert frames == sorted(frames)


def test_approving_a_track_approves_every_frame_in_it(client, clip):
    job = start_track(client, clip).json()
    done = wait_for(client, job["id"])
    tid = done["track_id"]
    client.post(f"/api/train/tracks/{tid}/review",
                data={"review_status": "submitted"})
    r = client.post(f"/api/train/tracks/{tid}/review",
                    data={"review_status": "approved"}).json()
    assert r["frames_changed"] == done["saved"]
    rows = client.get(f"/api/train/annotations?clip_id={clip}").json()
    assert all(x["review_status"] == "approved" for x in rows)


def test_a_track_cannot_jump_straight_to_approved(client, clip):
    job = start_track(client, clip).json()
    done = wait_for(client, job["id"])
    r = client.post(f"/api/train/tracks/{done['track_id']}/review",
                    data={"review_status": "approved"})
    assert r.status_code == 400


def test_relabelling_a_track_relabels_its_frames(client, clip):
    job = start_track(client, clip).json()
    done = wait_for(client, job["id"])
    client.patch(f"/api/train/tracks/{done['track_id']}",
                 data={"category": "truck", "custom_label": "the white van"})
    rows = client.get(f"/api/train/annotations?clip_id={clip}").json()
    assert {r["category"] for r in rows} == {"truck"}
    assert {r["custom_label"] for r in rows} == {"the white van"}


def test_deleting_a_track_takes_its_frames_with_it(client, clip):
    job = start_track(client, clip).json()
    done = wait_for(client, job["id"])
    assert client.delete(
        f"/api/train/tracks/{done['track_id']}").status_code == 200
    assert client.get(f"/api/train/annotations?clip_id={clip}").json() == []


def test_deleting_a_clip_takes_its_tracks_with_it(client, clip, ctx):
    job = start_track(client, clip).json()
    wait_for(client, job["id"])
    client.delete(f"/api/train/clips/{clip}", params={"reason": "wrong clip"})
    assert ctx.db.object_tracks() == []
    assert ctx.db.object_annotations() == []


def test_asking_to_follow_nothing_says_so(client, clip):
    r = client.post(f"/api/train/clips/{clip}/track",
                    data={"timestamp_ms": 100,
                          "temporary_object_id": "does-not-exist"})
    assert r.status_code == 404


def test_a_job_that_does_not_exist_is_a_404(client):
    assert client.get("/api/train/track-jobs/nope").status_code == 404


def test_a_run_can_be_stopped(client, clip):
    job = start_track(client, clip, timestamp_ms=0).json()
    client.delete(f"/api/train/track-jobs/{job['id']}")
    end = wait_for(client, job["id"])
    assert end["state"] in ("cancelled", "done")


def test_the_stats_count_reconstructions_apart_from_sightings(client, clip):
    job = start_track(client, clip).json()
    done = wait_for(client, job["id"])
    s = client.get("/api/train/annotations/stats").json()
    assert s["total"] == done["saved"]
    assert s["observed"] + s["reconstructed"] == s["total"]
    assert s["tracks"] == 1


def test_the_export_says_which_frames_were_actually_observed(client, clip):
    job = start_track(client, clip).json()
    wait_for(client, job["id"])
    doc = client.get("/api/train/annotations/export").json()
    assert doc["annotations"]
    for a in doc["annotations"]:
        vg = a["visionguard"]
        assert vg["observed"] is (vg["mask_source"] != "interpolated")
        assert vg["track_id"]
