"""Integration tests: camera worker + rolling buffer + clip saving on a
synthetic video file. YOLO-dependent parts skip when ultralytics is absent."""
import time
from types import SimpleNamespace

import pytest

cv2 = pytest.importorskip("cv2")

from app.camera import CameraWorker, frame_stats
from app.clips import ClipSaver
from app.db import Database
from tests.make_sample_video import make


@pytest.fixture(scope="module")
def sample_video(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("vid") / "sample.mp4")
    make(path, seconds=6, fps=15)
    return path


def test_worker_reads_and_buffers(sample_video):
    w = CameraWorker("test", sample_video, buffer_s=30, loop_file=False)
    w.start()
    deadline = time.time() + 15
    while time.time() < deadline and not w.file_ended:
        time.sleep(0.2)
    w.stop()
    assert w.file_ended
    frames = w.buffer_snapshot()
    assert len(frames) > 20                       # ~10fps buffered over 6s
    assert frames == sorted(frames, key=lambda f: f[0])
    frame, ts = w.latest_frame()
    assert frame is not None and ts > 0


def test_frame_stats_detects_black_frame():
    import numpy as np
    black = np.zeros((360, 640, 3), np.uint8)
    mean, lap = frame_stats(black)
    assert mean < 12 and lap < 12

    noisy = (np.random.rand(360, 640, 3) * 255).astype(np.uint8)
    mean2, lap2 = frame_stats(noisy)
    assert lap2 > 12


def test_clip_saver_writes_clip_and_sidecar(tmp_path, sample_video):
    db = Database(str(tmp_path / "t.db"))
    w = CameraWorker("gate", sample_video, buffer_s=30, loop_file=True)
    w.start()
    time.sleep(3)  # accumulate some buffer

    saver = ClipSaver({"dir": str(tmp_path / "clips"), "pre_event_s": 2,
                       "post_event_s": 1, "fps": 10}, db)
    event = SimpleNamespace(ts=time.time(), camera="gate",
                            event_type="loitering", severity="MEDIUM",
                            description="test", plate=None, track_ids=[1],
                            confidence=0.7)
    eid = db.insert_event(event.ts, "gate", "loitering", "MEDIUM", None,
                          [1], 0.7, "test")
    t = saver.save_async(w, event, eid)
    t.join(timeout=20)
    w.stop()

    clips = list((tmp_path / "clips").rglob("*.mp4"))
    sidecars = list((tmp_path / "clips").rglob("*.json"))
    assert len(clips) == 1 and len(sidecars) == 1

    cap = cv2.VideoCapture(str(clips[0]))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    assert n > 10  # ~3s of buffered material at 10fps

    import json
    meta = json.loads(sidecars[0].read_text())
    assert meta["event_type"] == "loitering" and meta["camera"] == "gate"

    row = db.get_clip(1)
    assert row is not None and row["event_id"] == eid
    ok, _ = db.verify_audit_chain()
    assert ok
    db.close()


def test_yolo_detection_smoke(sample_video):
    """Full detector pass — runs only when ultralytics is installed."""
    pytest.importorskip("ultralytics")
    from app.detector import Detector
    det = Detector({"model": "yolo11n.pt", "device": "cpu", "imgsz": 320,
                    "confidence": 0.25})
    cap = cv2.VideoCapture(sample_video)
    ok, frame = cap.read()
    cap.release()
    assert ok
    detections = det.track(frame)      # synthetic boxes: expect no crash
    assert isinstance(detections, list)
