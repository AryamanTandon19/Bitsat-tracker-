"""The labelling workbench: marks, boxes, and the export the harnesses read."""
from __future__ import annotations

import io
import re

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from app import dashboard, train
from app.db import Database
from app.main import load_config

from .conftest import signin


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


def _upload(client, name="night.mp4", body=b"\x00" * 4096, source="balcony"):
    return client.post("/api/train/clips",
                       files={"file": (name, io.BytesIO(body), "video/mp4")},
                       data={"source": source})


# ---------------------------------------------------------------- uploads
def test_a_clip_can_be_uploaded_and_listed(client, ctx):
    r = _upload(client)
    assert r.status_code == 200
    rows = client.get("/api/train/clips").json()
    assert len(rows) == 1
    assert rows[0]["filename"] == "night.mp4" and rows[0]["source"] == "balcony"
    assert rows[0]["marks"] == 0 and rows[0]["boxes"] == 0


def test_uploads_never_silently_overwrite_each_other(client):
    a = _upload(client).json()
    b = _upload(client).json()
    assert a["filename"] != b["filename"]        # footage is not replaceable


def test_a_dangerous_filename_cannot_escape_the_folder(client, ctx):
    r = _upload(client, name="../../etc/passwd.mp4")
    assert r.status_code == 200
    from pathlib import Path
    stored = Path(ctx.db.list_training_clips()[0]["path"])
    assert stored.parent == Path(ctx.config["storage"]["training_dir"])
    assert ".." not in stored.name


def test_a_non_video_is_refused(client):
    r = client.post("/api/train/clips",
                    files={"file": ("notes.txt", io.BytesIO(b"x"), "text/plain")})
    assert r.status_code == 400


def test_an_empty_file_is_refused(client):
    r = client.post("/api/train/clips",
                    files={"file": ("empty.mp4", io.BytesIO(b""), "video/mp4")})
    assert r.status_code == 400


def test_deleting_a_clip_keeps_the_footage(client, ctx):
    """Labels are cheap to redo. Footage is not."""
    cid = _upload(client).json()["id"]
    from pathlib import Path
    path = Path(ctx.db.get_training_clip(cid)["path"])
    r = client.delete(f"/api/train/clips/{cid}")
    assert r.status_code == 200
    assert client.get("/api/train/clips").json() == []
    assert path.exists()


# ------------------------------------------------------------------ marks
def test_a_mark_records_what_happened_and_who_said_so(client, ctx):
    cid = _upload(client).json()["id"]
    r = client.post("/api/train/marks",
                    data={"clip_id": cid, "start_s": 12, "end_s": 41,
                          "label": "trying door handles", "verdict": "incident",
                          "note": "this is the one"})
    assert r.status_code == 200
    m = client.get(f"/api/train/marks?clip_id={cid}").json()[0]
    assert m["label"] == "trying door handles" and m["verdict"] == "incident"
    assert m["marked_by"] == "A. Admin"       # from the session, not the form


def test_a_mark_must_end_after_it_starts(client):
    cid = _upload(client).json()["id"]
    for start, end in ((10, 10), (10, 5)):
        r = client.post("/api/train/marks",
                        data={"clip_id": cid, "start_s": start, "end_s": end,
                              "label": "x", "verdict": "normal"})
        assert r.status_code == 400


def test_a_verdict_must_be_one_of_two_things(client):
    cid = _upload(client).json()["id"]
    r = client.post("/api/train/marks",
                    data={"clip_id": cid, "start_s": 1, "end_s": 2,
                          "label": "x", "verdict": "maybe"})
    assert r.status_code == 400


def test_marks_on_a_missing_clip_are_refused(client):
    r = client.post("/api/train/marks",
                    data={"clip_id": 9999, "start_s": 1, "end_s": 2,
                          "label": "x", "verdict": "normal"})
    assert r.status_code == 404


def test_a_mark_can_be_removed(client):
    cid = _upload(client).json()["id"]
    mid = client.post("/api/train/marks",
                      data={"clip_id": cid, "start_s": 1, "end_s": 2,
                            "label": "x", "verdict": "normal"}).json()["id"]
    assert client.delete(f"/api/train/marks/{mid}").status_code == 200
    assert client.get("/api/train/marks").json() == []


# ------------------------------------------------------------------ boxes
def test_a_box_is_stored_against_a_moment(client, ctx):
    cid = _upload(client).json()["id"]
    r = client.post("/api/train/boxes",
                    data={"clip_id": cid, "t_s": 20.5, "cls": "person",
                          "x1": .1, "y1": .2, "x2": .3, "y2": .8})
    assert r.status_code == 200
    b = client.get(f"/api/train/boxes?clip_id={cid}").json()[0]
    assert b["cls"] == "person" and b["t_s"] == 20.5


def test_a_box_with_no_area_is_refused(client):
    cid = _upload(client).json()["id"]
    r = client.post("/api/train/boxes",
                    data={"clip_id": cid, "t_s": 1, "cls": "car",
                          "x1": .5, "y1": .5, "x2": .5, "y2": .9})
    assert r.status_code == 400


def test_an_unknown_class_is_refused(client):
    cid = _upload(client).json()["id"]
    r = client.post("/api/train/boxes",
                    data={"clip_id": cid, "t_s": 1, "cls": "spaceship",
                          "x1": .1, "y1": .1, "x2": .2, "y2": .2})
    assert r.status_code == 400


# ----------------------------------------------------------------- export
def test_export_produces_the_csv_the_harness_reads(client, ctx):
    cid = _upload(client, name="theft.mp4").json()["id"]
    client.post("/api/train/marks",
                data={"clip_id": cid, "start_s": 12, "end_s": 41,
                      "label": "trying door handles", "verdict": "incident"})
    client.post("/api/train/marks",
                data={"clip_id": cid, "start_s": 50, "end_s": 58,
                      "label": "reaching inside", "verdict": "incident"})
    body = client.get("/api/train/export").text
    header, row = body.strip().splitlines()[:2]
    assert header == "filename,type,incident,start_s,end_s,notes"
    assert row.startswith("theft.mp4,trying door handles,1,12.0,58.0")
    # the window spans every incident mark, which is what the harness asks about
    assert "reaching inside" in row


def test_a_clip_with_only_normal_marks_exports_as_normal(client, ctx):
    cid = _upload(client, name="quiet.mp4").json()["id"]
    client.post("/api/train/marks",
                data={"clip_id": cid, "start_s": 1, "end_s": 9,
                      "label": "resident parking", "verdict": "normal"})
    rows = client.get("/api/train/export").text.strip().splitlines()
    assert rows[1].startswith("quiet.mp4,normal,0,,")


def test_an_unmarked_clip_is_listed_as_unmarked_not_dropped(client):
    """Dropping it would make the test set look more complete than it is."""
    _upload(client, name="todo.mp4")
    row = client.get("/api/train/export").text.strip().splitlines()[1]
    assert row.startswith("todo.mp4,unmarked,0")
    assert "NOT YET LABELLED" in row


def test_export_is_offered_as_a_download(client):
    r = client.get("/api/train/export")
    assert "labels.csv" in r.headers["content-disposition"]
    assert r.headers["content-type"].startswith("text/csv")


# ------------------------------------------------------------ permissions
def test_a_guard_cannot_label_footage(ctx):
    """Labelling changes what the whole system believes."""
    cl = TestClient(dashboard.create_app(ctx))
    signin(cl, ctx.db, "guard")
    assert cl.get("/api/train/clips").status_code == 403
    assert cl.get("/api/train/export").status_code == 403


def test_anonymous_callers_get_nothing(ctx):
    cl = TestClient(dashboard.create_app(ctx))
    for url in ("/api/train/clips", "/api/train/marks", "/api/train/boxes",
                "/api/train/export"):
        assert cl.get(url).status_code == 401, url


# ------------------------------------------------------------------ page
def test_the_page_is_served(client):
    r = client.get("/train")
    assert r.status_code == 200 and "VisionGuard" in r.text
    assert "no-store" in r.headers["cache-control"]


def test_every_element_the_script_reaches_for_exists(client):
    html = client.get("/train").text
    ids = set(re.findall(r'id="([^"]+)"', html))
    used = set(re.findall(r'\$\("#([\w-]+)"\)', html))
    assert used <= ids, f"script looks up missing ids: {used - ids}"


def test_the_page_says_what_boxes_do_and_do_not_do(client):
    """Calling this 'training the detector' would be a lie, and someone would
    reasonably expect accuracy to improve because they drew boxes."""
    html = client.get("/train").text
    assert "does <b>not</b> teach the detector" in html
