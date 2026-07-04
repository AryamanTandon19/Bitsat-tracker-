"""Threaded per-camera frame reader with auto-reconnect and a rolling buffer.

RTSP is read via an FFmpeg subprocess when ffmpeg is on PATH (much more robust
than OpenCV's RTSP demuxer), otherwise via cv2.VideoCapture. Video files always
use cv2.VideoCapture and are paced to their native FPS (looped in demo mode).

The rolling buffer stores JPEG-encoded frames (bounds memory: ~60s * 10fps *
~50KB ≈ 30MB/camera) and is the source for both live MJPEG streaming and
pre-event clip material.
"""
from __future__ import annotations

import collections
import logging
import shutil
import subprocess
import threading
import time

import cv2
import numpy as np

log = logging.getLogger(__name__)

BUFFER_FPS = 10  # frames kept in the rolling buffer per second


def is_rtsp(url: str) -> bool:
    return url.lower().startswith(("rtsp://", "rtmp://", "http://", "https://"))


class CameraWorker(threading.Thread):
    """Reads frames continuously; never lets one bad camera kill the app."""

    def __init__(self, name: str, url: str, buffer_s: int = 60,
                 loop_file: bool = False):
        super().__init__(name=f"cam-{name}", daemon=True)
        self.cam_name = name
        self.url = url
        self.loop_file = loop_file
        self.buffer = collections.deque(maxlen=int(buffer_s * BUFFER_FPS))
        self._buf_lock = threading.Lock()
        self._latest: np.ndarray | None = None
        self._latest_ts = 0.0
        self._latest_lock = threading.Lock()
        self._stop = threading.Event()
        self.online = False
        self.last_frame_ts = 0.0
        self.offline_since: float | None = None
        self.frame_size: tuple[int, int] | None = None  # (w, h)
        self._file_ended = False

    # ------------------------------------------------------------------ api
    def stop(self):
        self._stop.set()

    @property
    def file_ended(self) -> bool:
        """True when a non-looping file source has been fully consumed."""
        return self._file_ended

    def latest_frame(self):
        """Most recent decoded frame (BGR ndarray) and its timestamp."""
        with self._latest_lock:
            return (None, 0.0) if self._latest is None else (self._latest.copy(), self._latest_ts)

    def buffer_snapshot(self, since_ts: float = 0.0) -> list[tuple[float, bytes]]:
        """(ts, jpeg_bytes) pairs from the rolling buffer, oldest first."""
        with self._buf_lock:
            return [(ts, jpg) for ts, jpg in self.buffer if ts >= since_ts]

    # ------------------------------------------------------------ internals
    def _publish(self, frame: np.ndarray, ts: float):
        self.online = True
        self.offline_since = None
        self.last_frame_ts = ts
        if self.frame_size is None:
            h, w = frame.shape[:2]
            self.frame_size = (w, h)
        with self._latest_lock:
            self._latest = frame
            self._latest_ts = ts
        # Downsample buffer writes to BUFFER_FPS
        with self._buf_lock:
            if not self.buffer or ts - self.buffer[-1][0] >= (1.0 / BUFFER_FPS) * 0.9:
                ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if ok:
                    self.buffer.append((ts, jpg.tobytes()))

    def _mark_offline(self):
        if self.online or self.offline_since is None:
            self.offline_since = time.time()
        self.online = False

    def run(self):
        backoff = 1.0
        while not self._stop.is_set():
            try:
                if is_rtsp(self.url) and shutil.which("ffmpeg"):
                    self._read_ffmpeg()
                else:
                    self._read_opencv()
                backoff = 1.0
            except Exception as e:
                log.warning("[%s] reader crashed: %s", self.cam_name, e)
            if self._stop.is_set() or self._file_ended:
                break
            self._mark_offline()
            log.info("[%s] reconnecting in %.0fs", self.cam_name, backoff)
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 30.0)

    # -- OpenCV reader (files + RTSP fallback) ------------------------------
    def _read_opencv(self):
        cap = cv2.VideoCapture(self.url)
        if not cap.isOpened():
            raise RuntimeError(f"cannot open source {self.url}")
        src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        if not (1 <= src_fps <= 120):
            src_fps = 25.0
        file_mode = not is_rtsp(self.url)
        frame_interval = 1.0 / src_fps
        try:
            while not self._stop.is_set():
                t0 = time.time()
                ok, frame = cap.read()
                if not ok:
                    if file_mode and self.loop_file:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    if file_mode:
                        log.info("[%s] file source finished", self.cam_name)
                        self._file_ended = True
                        return
                    raise RuntimeError("stream read failed")
                self._publish(frame, time.time())
                if file_mode:  # pace playback like a live source
                    delay = frame_interval - (time.time() - t0)
                    if delay > 0:
                        self._stop.wait(delay)
        finally:
            cap.release()

    # -- FFmpeg subprocess reader (robust RTSP) -----------------------------
    def _read_ffmpeg(self):
        probe = self._probe_size()
        w, h = probe
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error",
               "-rtsp_transport", "tcp", "-i", self.url,
               "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, bufsize=w * h * 3 * 4)
        frame_bytes = w * h * 3
        try:
            while not self._stop.is_set():
                data = proc.stdout.read(frame_bytes)
                if data is None or len(data) < frame_bytes:
                    raise RuntimeError("ffmpeg stream ended")
                frame = np.frombuffer(data, np.uint8).reshape((h, w, 3))
                self._publish(frame.copy(), time.time())
        finally:
            proc.kill()

    def _probe_size(self) -> tuple[int, int]:
        cap = cv2.VideoCapture(self.url)
        try:
            if cap.isOpened():
                ok, frame = cap.read()
                if ok:
                    return frame.shape[1], frame.shape[0]
        finally:
            cap.release()
        raise RuntimeError("cannot probe stream size")


def frame_stats(frame: np.ndarray) -> tuple[float, float]:
    """(mean brightness, Laplacian variance) — inputs to the tamper rule (A5)."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (320, 180)) if gray.shape[1] > 320 else gray
    return float(small.mean()), float(cv2.Laplacian(small, cv2.CV_64F).var())
