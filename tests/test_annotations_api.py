"""The tagging endpoints: saving an object, correcting it, and getting it back.

The acceptance criterion this file is written against is the plain one — a
mask you tagged is still there, in the shape you left it, after the page is
closed and reopened.
"""
from __future__ import annotations

import io
import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from app import dashboard
from app.db import Database
from app.main import load_config

from .conftest import signin

BOX = [[10, 10], [90, 10], [90, 90], [10, 90]]


@pytest.fixture()
def ctx(tmp_path):
    class Ctx:
        pass
    c = Ctx()
    c.config = load_config("config.yaml")
    c.config["dashboard"]["auth"] = {"enabled": False}
    c.config.setdefault("storage", {})["training_dir"] = str(tmp_path / "clips")
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
def clip(client):
    r = client.post("/api/train/clips",
                    files={"file": ("gate.mp4", io.BytesIO(b"\0" * 2048),
                                    "video/mp4")},
                    data={"source": "gate camera"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def tag(client, clip_id, **over):
    data = {"clip_id": clip_id, "frame_index": 30, "timestamp_ms": 1200,
            "category": "car", "source": "yolo_segmentation",
            "frame_width": 1920, "frame_height": 1080,
            "original_polygon": json.dumps([BOX]),
            "detection_confidence": 0.82, "model": "yolo11n-seg"}
    data.update(over)
    return client.post("/api/train/annotations", data=data)


# ------------------------------------------------------------- the basics
def test_a_tagged_object_is_saved_and_comes_back(client, clip):
    r = tag(client, clip, tags="half hidden, at night", notes="behind the pillar",
            user_confidence=0.7)
    assert r.status_code == 200, r.text
    got = client.get(f"/api/train/annotations?clip_id={clip}").json()
    assert len(got) == 1
    a = got[0]
    assert a["category"] == "car" and a["review_status"] == "draft"
    assert a["tags"] == ["half hidden", "at night"]
    assert a["notes"] == "behind the pillar"
    assert a["user_confidence"] == 0.7
    assert a["created_by"] == "admin"
    assert a["original_polygon"] == [BOX]


def test_a_saved_mask_survives_the_page_being_closed(client, clip, ctx):
    tag(client, clip)
    # a brand new app on the same database: nothing in memory carries over
    fresh = TestClient(dashboard.create_app(ctx))
    signin(fresh, ctx.db, "admin")
    got = fresh.get(f"/api/train/annotations?clip_id={clip}").json()
    assert len(got) == 1 and got[0]["polygon"] == [BOX]


def test_tagging_a_clip_that_does_not_exist_is_a_404(client):
    assert tag(client, 999).status_code == 404


def test_a_bad_category_gets_a_sentence_not_a_code(client, clip):
    r = tag(client, clip, category="spaceship")
    assert r.status_code == 400
    assert "category must be one of" in r.json()["detail"]


def test_the_clip_list_counts_the_tags(client, clip):
    tag(client, clip)
    tag(client, clip, frame_index=31)
    row = [c for c in client.get("/api/train/clips").json() if c["id"] == clip][0]
    assert row["annotations"] == 2


def test_annotations_can_be_narrowed_to_one_frame(client, clip):
    tag(client, clip, frame_index=30)
    tag(client, clip, frame_index=90)
    got = client.get(f"/api/train/annotations?clip_id={clip}&frame_index=90").json()
    assert len(got) == 1 and got[0]["frame_index"] == 90


# ------------------------------------------------- the correction, stored
def test_correcting_the_outline_keeps_the_model_s_version(client, clip):
    ann = tag(client, clip).json()
    mine = [[20, 20], [80, 20], [80, 80], [20, 80]]
    r = client.patch(f"/api/train/annotations/{ann['id']}",
                     data={"corrected_polygon": json.dumps([mine])})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["original_polygon"] == [BOX]        # untouched
    assert a["corrected_polygon"] == [mine]
    assert a["polygon"] == [mine]                # what gets drawn and exported
    assert a["corrected"] is True and a["drift"] > 0


def test_the_original_cannot_be_overwritten_even_if_asked_directly(client, clip):
    ann = tag(client, clip).json()
    client.patch(f"/api/train/annotations/{ann['id']}",
                 data={"original_polygon": json.dumps([[[0, 0], [5, 0], [5, 5]]]),
                       "notes": "trying it on"})
    a = client.get(f"/api/train/annotations?clip_id={clip}").json()[0]
    assert a["original_polygon"] == [BOX]
    assert a["notes"] == "trying it on"          # the legitimate part applied


def test_the_box_moves_with_a_corrected_outline(client, clip):
    ann = tag(client, clip).json()
    a = client.patch(f"/api/train/annotations/{ann['id']}",
                     data={"corrected_polygon":
                           json.dumps([[[0, 0], [200, 0], [200, 200], [0, 200]]])
                           }).json()
    assert a["bbox"] == {"x_min": 0.0, "y_min": 0.0,
                         "x_max": 200.0, "y_max": 200.0}


def test_the_correction_can_be_thrown_away(client, clip):
    ann = tag(client, clip).json()
    client.patch(f"/api/train/annotations/{ann['id']}",
                 data={"corrected_polygon": json.dumps([[[0, 0], [9, 0], [9, 9]]])})
    a = client.patch(f"/api/train/annotations/{ann['id']}",
                     data={"corrected_polygon": "[]"}).json()
    assert a["corrected"] is False and a["polygon"] == [BOX]


def test_an_edit_naming_nothing_editable_is_refused(client, clip):
    ann = tag(client, clip).json()
    r = client.patch(f"/api/train/annotations/{ann['id']}", data={"nonsense": "1"})
    assert r.status_code == 400


def test_editing_something_that_is_not_there_is_a_404(client):
    assert client.patch("/api/train/annotations/999",
                        data={"notes": "hello"}).status_code == 404


# ------------------------------------------------------------ hand-drawn
def test_something_the_model_missed_can_be_drawn_by_hand(client, clip):
    r = tag(client, clip, source="manual_polygon", original_polygon="",
            corrected_polygon=json.dumps([[[5, 5], [50, 5], [50, 50]]]),
            detection_confidence="", model="")
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["source"] == "manual_polygon"
    assert a["original_polygon"] == []          # there was no model answer
    assert a["detection_confidence"] is None
    assert a["drift"] is None                   # nothing to be a correction of


def test_an_annotation_with_no_shape_at_all_is_refused(client, clip):
    r = tag(client, clip, original_polygon="")
    assert r.status_code == 400
    assert "polygon or a box" in r.json()["detail"]


def test_a_detection_with_only_a_box_is_still_taggable(client, clip):
    r = tag(client, clip, original_polygon="", source="yolo_detection_fallback",
            x1=100, y1=100, x2=180, y2=240)
    assert r.status_code == 200, r.text
    assert r.json()["bbox"]["x_max"] == 180.0


# --------------------------------------------------------------- review
def test_a_draft_is_submitted_then_approved(client, clip):
    ann = tag(client, clip).json()
    a = client.post(f"/api/train/annotations/{ann['id']}/review",
                    data={"review_status": "submitted"}).json()
    assert a["review_status"] == "submitted"
    a = client.post(f"/api/train/annotations/{ann['id']}/review",
                    data={"review_status": "approved"}).json()
    assert a["review_status"] == "approved" and a["reviewed_by"] == "admin"


def test_a_draft_cannot_jump_straight_to_approved(client, clip):
    ann = tag(client, clip).json()
    r = client.post(f"/api/train/annotations/{ann['id']}/review",
                    data={"review_status": "approved"})
    assert r.status_code == 400 and "cannot go from draft" in r.json()["detail"]


def test_a_committee_account_may_not_label_at_all(ctx):
    # labelling is behind `registry`, which only an admin has — so the weaker
    # account cannot even reach the endpoint, let alone approve on it
    cl = TestClient(dashboard.create_app(ctx))
    signin(cl, ctx.db, "committee")
    assert cl.get("/api/train/annotations").status_code == 403


def test_deleting_a_tag_removes_it(client, clip):
    ann = tag(client, clip).json()
    assert client.delete(f"/api/train/annotations/{ann['id']}").status_code == 200
    assert client.get(f"/api/train/annotations?clip_id={clip}").json() == []


def test_deleting_a_clip_takes_its_tags_with_it(client, clip, ctx):
    tag(client, clip)
    client.delete(f"/api/train/clips/{clip}", params={"reason": "wrong footage"})
    assert ctx.db.object_annotations() == []


# --------------------------------------------------------------- export
def test_the_stats_say_how_much_of_the_model_was_corrected(client, clip):
    a = tag(client, clip).json()
    tag(client, clip, frame_index=31)
    client.patch(f"/api/train/annotations/{a['id']}",
                 data={"corrected_polygon":
                       json.dumps([[[10, 10], [90, 10], [90, 50], [10, 50]]])})
    s = client.get("/api/train/annotations/stats").json()
    assert s["total"] == 2 and s["corrected"] == 1
    assert s["by_status"]["draft"] == 2
    assert s["by_category"]["car"] == 2
    assert 0.3 < s["mean_drift"] < 0.7
    assert s["frames"] == 2 and s["clips"] == 1


def test_the_coco_export_names_the_clip_and_the_frame(client, clip):
    tag(client, clip)
    doc = client.get("/api/train/annotations/export").json()
    assert doc["images"][0]["file_name"].endswith("#30")
    assert doc["images"][0]["source"] == "gate camera"
    assert doc["annotations"][0]["segmentation"] == \
        [[10, 10, 90, 10, 90, 90, 10, 90]]


def test_the_export_downloads_as_a_file(client, clip):
    tag(client, clip)
    r = client.get("/api/train/annotations/export")
    assert "annotations.json" in r.headers["content-disposition"]
