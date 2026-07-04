#!/usr/bin/env python3
"""RTSP discovery helper: scan the LAN for DVR/NVRs (port 554 open), then try
common RTSP URL patterns with your credentials and print the ones that work.

Usage:
    python discover.py --network 192.168.1.0/24 --user admin --password pass123
    python discover.py --host 192.168.1.108 --user admin --password pass123 --channels 4
"""
from __future__ import annotations

import argparse
import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor

# {ip}, {user}, {pw}, {ch} placeholders. subtype/stream 1 = substream (use this
# for AI inference); change 102->101 / subtype=1->0 for the main stream.
URL_PATTERNS = [
    # Hikvision (channel 1 substream = 102, channel 2 = 202, ...)
    "rtsp://{user}:{pw}@{ip}:554/Streaming/Channels/{ch}02",
    "rtsp://{user}:{pw}@{ip}:554/Streaming/Channels/{ch}01",
    # CP Plus / Dahua
    "rtsp://{user}:{pw}@{ip}:554/cam/realmonitor?channel={ch}&subtype=1",
    "rtsp://{user}:{pw}@{ip}:554/cam/realmonitor?channel={ch}&subtype=0",
    # Generic / ONVIF-ish fallbacks
    "rtsp://{user}:{pw}@{ip}:554/live/ch{ch}",
    "rtsp://{user}:{pw}@{ip}:554/h264/ch{ch}/sub/av_stream",
]


def port_open(ip: str, port: int = 554, timeout: float = 0.7) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def scan_network(cidr: str) -> list[str]:
    hosts = [str(h) for h in ipaddress.ip_network(cidr, strict=False).hosts()]
    print(f"scanning {len(hosts)} hosts on {cidr} for port 554 ...")
    found = []
    with ThreadPoolExecutor(max_workers=64) as ex:
        for ip, ok in zip(hosts, ex.map(port_open, hosts)):
            if ok:
                print(f"  [+] {ip} has port 554 open")
                found.append(ip)
    return found


def try_stream(url: str, timeout_s: float = 6.0) -> bool:
    """True if the RTSP URL yields at least one decodable frame."""
    import cv2
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    try:
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(timeout_s * 1000))
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(timeout_s * 1000))
        if not cap.isOpened():
            return False
        ok, frame = cap.read()
        return ok and frame is not None
    finally:
        cap.release()


def probe_host(ip: str, user: str, pw: str, channels: int) -> list[str]:
    working = []
    for ch in range(1, channels + 1):
        for pattern in URL_PATTERNS:
            url = pattern.format(ip=ip, user=user, pw=pw, ch=ch)
            shown = url.replace(pw, "****") if pw else url
            print(f"  trying {shown} ... ", end="", flush=True)
            if try_stream(url):
                print("OK")
                working.append(url)
                break  # next channel; first working pattern wins
            print("no")
    return working


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--network", help="CIDR to scan, e.g. 192.168.1.0/24")
    ap.add_argument("--host", help="probe a single known DVR/NVR IP")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="")
    ap.add_argument("--channels", type=int, default=4,
                    help="number of camera channels to try (default 4)")
    args = ap.parse_args()

    if not args.network and not args.host:
        ap.error("provide --network or --host")

    hosts = [args.host] if args.host else scan_network(args.network)
    if not hosts:
        print("no devices with port 554 found")
        return

    all_working = []
    for ip in hosts:
        print(f"\nprobing {ip} ...")
        all_working += probe_host(ip, args.user, args.password, args.channels)

    print("\n" + "=" * 60)
    if all_working:
        print("Working streams — paste into config.yaml under cameras:")
        for url in all_working:
            print(f'  - name: cam{all_working.index(url) + 1}\n    url: "{url}"')
    else:
        print("No working RTSP streams found. Check credentials, or find the")
        print("RTSP path in your DVR's manual / web UI (Network > RTSP).")


if __name__ == "__main__":
    main()
