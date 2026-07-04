#!/usr/bin/env python3
"""Generate a small synthetic CCTV-style video (moving car-ish rectangle and a
person-ish rectangle) for pipeline smoke tests and the M1 demo without
hardware. Not a substitute for real footage — YOLO won't detect rectangles;
use it to exercise decoding, buffering and clip writing.

Usage:  python tests/make_sample_video.py [out.mp4] [seconds]
"""
import sys

import cv2
import numpy as np


def make(path="tests/sample_gate.mp4", seconds=20, fps=15, size=(640, 360)):
    w, h = size
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    frames = int(seconds * fps)
    for i in range(frames):
        img = np.full((h, w, 3), 70, np.uint8)
        cv2.rectangle(img, (0, h - 60), (w, h), (50, 50, 50), -1)  # road
        # "car" drives left to right
        cx = int((i / frames) * (w + 200)) - 100
        cv2.rectangle(img, (cx, h - 140), (cx + 120, h - 70), (30, 90, 200), -1)
        cv2.rectangle(img, (cx + 20, h - 100), (cx + 100, h - 80),
                      (240, 240, 240), -1)  # plate-ish patch
        # "person" stands near the parking area
        px = 480 + int(5 * np.sin(i / 10))
        cv2.rectangle(img, (px, h - 200), (px + 30, h - 110), (200, 180, 60), -1)
        cv2.putText(img, f"synthetic frame {i}", (10, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        vw.write(img)
    vw.release()
    print(f"wrote {path} ({seconds}s @ {fps}fps)")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "tests/sample_gate.mp4"
    secs = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    make(out, secs)
