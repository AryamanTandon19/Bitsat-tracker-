"""Upload-analysis wiring: dashboard endpoints + (YOLO-gated) full run."""
import os
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
cv2 = pytest.importorskip("cv2")

from fastapi.testclient import TestClient

from app import dashboard
from app.analyze import VideoAnalyzer
from app.db import Database
from app.main import load_config
from tests.make_sample_video import make


@pytest.fixture
def ctx(tmp_path):
    class Ctx:
        pass
    c = Ctx()
    c.config = load_config("config.yaml")
    c.config["dashboard"]["auth"] = {"enabled": False}
    c.config_path = str(tmp_path / "config.yaml")
    c.db = Database(str(tmp_path / "t.db"))
    c.workers = {}
    c.pipelines = {}
    c.analyzer = VideoAnalyzer(c.config, out_dir=str(tmp_path / "uploads"))
    c.assistant = None
    yield c
    c.db.close()


def test_upload_endpoint_accepts_video(ctx, tmp_path):
    vid = str(tmp_path / "s.mp4")
    make(vid, seconds=2, fps=10)
    client = TestClient(dashboard.create_app(ctx))
    with open(vid, "rb") as f:
        r = client.post("/api/analyze", files={"file": ("s.mp4", f, "video/mp4")},
                        data={"zones_from": ""})
    # 503 if ultralytics missing at submit time is not expected — submit only
    # queues; job errors are surfaced via status polling.
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    assert client.get(f"/api/analyze/{job_id}").json()["id"] == job_id


def test_upload_rejects_when_analyzer_disabled(ctx, tmp_path):
    ctx.analyzer = None
    vid = str(tmp_path / "s.mp4")
    make(vid, seconds=1, fps=10)
    client = TestClient(dashboard.create_app(ctx))
    with open(vid, "rb") as f:
        r = client.post("/api/analyze", files={"file": ("s.mp4", f, "video/mp4")})
    assert r.status_code == 503


def test_cameras_endpoint(ctx):
    client = TestClient(dashboard.create_app(ctx))
    assert client.get("/api/cameras").json() == ["gate"]


def test_full_analysis_run(ctx, tmp_path):
    """End-to-end analyze — only when ultralytics is installed."""
    pytest.importorskip("ultralytics")
    vid = str(tmp_path / "s.mp4")
    make(vid, seconds=3, fps=12)
    job = ctx.analyzer.submit(vid, "s.mp4", zones={}, registry=[])
    deadline = time.time() + 90
    while time.time() < deadline and job.status in ("queued", "running"):
        time.sleep(0.5)
    assert job.status == "done", job.error
    assert isinstance(job.events, list)
