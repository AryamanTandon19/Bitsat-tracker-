#!/usr/bin/env python3
"""Zone editor: click points on a camera frame to draw entry / parking /
restricted polygons; saves them back into config.yaml.

Usage:
    python zones.py --camera gate                # uses config.yaml
    python zones.py --camera gate --zone parking

Keys:
    left-click  add point          u  undo last point
    n           next zone type     c  clear current zone
    s           save to config     q  quit without saving current edits
"""
from __future__ import annotations

import argparse
import sys

import cv2
import yaml

ZONE_ORDER = ["entry", "parking", "restricted"]
COLORS = {"entry": (255, 160, 0), "parking": (0, 255, 120),
          "restricted": (0, 0, 255)}


def grab_frame(url: str):
    cap = cv2.VideoCapture(url)
    try:
        ok, frame = cap.read()
        if not ok:
            raise SystemExit(f"cannot read a frame from {url}")
        return frame
    finally:
        cap.release()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--camera", required=True)
    ap.add_argument("--zone", choices=ZONE_ORDER, default="entry")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    cam = next((c for c in config.get("cameras", [])
                if c["name"] == args.camera), None)
    if cam is None:
        raise SystemExit(f"camera '{args.camera}' not in {args.config}")
    cam.setdefault("zones", {})
    zones = {z: list(cam["zones"].get(z) or []) for z in ZONE_ORDER}

    frame = grab_frame(cam["url"])
    current = [args.zone]  # boxed for the mouse callback

    def on_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            zones[current[0]].append([int(x), int(y)])

    win = f"zones — {args.camera}"
    cv2.namedWindow(win)
    cv2.setMouseCallback(win, on_mouse)
    print(__doc__)

    while True:
        vis = frame.copy()
        for zname, poly in zones.items():
            color = COLORS[zname]
            for p in poly:
                cv2.circle(vis, tuple(p), 4, color, -1)
            if len(poly) >= 2:
                for i in range(len(poly)):
                    cv2.line(vis, tuple(poly[i]),
                             tuple(poly[(i + 1) % len(poly)]), color,
                             2 if zname == current[0] else 1)
            if poly:
                cv2.putText(vis, zname, tuple(poly[0]),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        cv2.putText(vis, f"editing: {current[0]}  (n=next zone, u=undo, "
                         f"c=clear, s=save, q=quit)",
                    (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.imshow(win, vis)
        key = cv2.waitKey(30) & 0xFF
        if key == ord("q"):
            break
        elif key == ord("n"):
            current[0] = ZONE_ORDER[(ZONE_ORDER.index(current[0]) + 1) % len(ZONE_ORDER)]
        elif key == ord("u") and zones[current[0]]:
            zones[current[0]].pop()
        elif key == ord("c"):
            zones[current[0]] = []
        elif key == ord("s"):
            cam["zones"] = {z: zones[z] for z in ZONE_ORDER}
            with open(args.config, "w", encoding="utf-8") as f:
                yaml.safe_dump(config, f, sort_keys=False, allow_unicode=True)
            print(f"saved zones for '{args.camera}' to {args.config}")
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
