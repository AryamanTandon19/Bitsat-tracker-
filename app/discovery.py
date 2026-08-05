"""Finding the DVR, guessing its RTSP path, and proving the stream works.

Connecting cameras is the first thing anyone does with this software and, until
now, the worst part of it: run `discover.py`, read the output, hand-edit
`config.yaml`, restart the process, hope. A guard cannot do that, and a
committee member certainly cannot.

This module is the same knowledge as a service, so the console can scan, probe,
show a still frame, and add a working camera without anybody touching a file.

Three things it is careful about:

  **Passwords never go near config.yaml.** An RTSP URL carries
  `user:password@host` in plain text, and `config.yaml` is tracked in git —
  that is exactly the mistake the security audit flagged. Camera credentials
  live in the database, and every string this module hands back to a browser
  or a log has the password masked.

  **The substream first, always.** Every vendor pattern here is listed
  substream-before-mainstream. A 4K main stream will saturate a cheap box and
  buy nothing: detection runs at 640px. Getting this the wrong way round is
  the difference between four cameras on one machine and one.

  **A pattern is only "working" when a frame actually decodes.** Port 554 being
  open means a device is there, not that these credentials open that channel.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

RTSP_PORT = 554

# {ip} {port} {user} {pw} {ch}. Substream FIRST in every family: it is the one
# that should be used, and the first pattern that yields a frame wins.
URL_PATTERNS = (
    ("hikvision", "rtsp://{user}:{pw}@{ip}:{port}/Streaming/Channels/{ch}02"),
    ("hikvision-main", "rtsp://{user}:{pw}@{ip}:{port}/Streaming/Channels/{ch}01"),
    ("dahua/cpplus", "rtsp://{user}:{pw}@{ip}:{port}/cam/realmonitor?channel={ch}&subtype=1"),
    ("dahua/cpplus-main", "rtsp://{user}:{pw}@{ip}:{port}/cam/realmonitor?channel={ch}&subtype=0"),
    ("generic-live", "rtsp://{user}:{pw}@{ip}:{port}/live/ch{ch}"),
    ("generic-h264", "rtsp://{user}:{pw}@{ip}:{port}/h264/ch{ch}/sub/av_stream"),
    ("uniview", "rtsp://{user}:{pw}@{ip}:{port}/media/video{ch}"),
    ("onvif-profile", "rtsp://{user}:{pw}@{ip}:{port}/profile{ch}/media.smp"),
)


def mask(url: str) -> str:
    """Hide the password in anything shown to a person or written to a log.

    Logs get copied into tickets and screenshots get sent to investors — this
    session has already seen an API key leak that way.
    """
    return re.sub(r"(rtsp://[^:/@]+:)([^@]*)(@)", r"\1****\3", url or "")


def credentials_of(url: str) -> tuple:
    """(user, password) out of an RTSP URL, or ('', '')."""
    m = re.match(r"rtsp://([^:/@]+):([^@]*)@", url or "")
    return (m.group(1), m.group(2)) if m else ("", "")


def host_of(url: str) -> str:
    m = re.match(r"rtsp://(?:[^@]*@)?([^:/]+)", url or "")
    return m.group(1) if m else ""


@dataclass
class Candidate:
    """One RTSP URL that might be a camera."""
    url: str
    vendor: str
    channel: int
    ok: bool = False
    error: str = ""
    width: int = 0
    height: int = 0

    @property
    def safe_url(self) -> str:
        return mask(self.url)

    def public(self) -> dict:
        return {"url": self.safe_url, "vendor": self.vendor,
                "channel": self.channel, "ok": self.ok, "error": self.error,
                "width": self.width, "height": self.height}


@dataclass
class Device:
    """A host with an RTSP port open."""
    ip: str
    port: int = RTSP_PORT
    channels: list = field(default_factory=list)

    def public(self) -> dict:
        return {"ip": self.ip, "port": self.port,
                "channels": [c.public() for c in self.channels]}


# --------------------------------------------------------------- scanning
def port_open(ip: str, port: int = RTSP_PORT, timeout: float = 0.7) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def hosts_in(cidr: str, limit: int = 512) -> list:
    """Usable addresses in a CIDR, capped.

    The cap is a guard against somebody pasting a /8 and the box spending the
    afternoon opening two million sockets.
    """
    net = ipaddress.ip_network(cidr, strict=False)
    if net.num_addresses > limit * 8:
        raise ValueError(
            f"{cidr} has {net.num_addresses} addresses — scan a smaller range, "
            "usually a /24 like 192.168.1.0/24")
    return [str(h) for h in net.hosts()][:limit]


def scan(cidr: str, port: int = RTSP_PORT, workers: int = 64,
         timeout: float = 0.7) -> list:
    """Which hosts on this network have an RTSP port open."""
    hosts = hosts_in(cidr)
    found = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for ip, ok in zip(hosts, ex.map(
                lambda h: port_open(h, port, timeout), hosts)):
            if ok:
                found.append(Device(ip=ip, port=port))
    return found


# ---------------------------------------------------------------- probing
def open_stream(url: str, timeout_s: float = 6.0) -> tuple:
    """(ok, width, height, error). Reads one frame — nothing less proves it."""
    try:
        import cv2
    except ImportError as e:                       # pragma: no cover
        return False, 0, 0, f"opencv is not installed ({e})"

    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    try:
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(timeout_s * 1000))
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(timeout_s * 1000))
        if not cap.isOpened():
            return False, 0, 0, "the stream would not open"
        ok, frame = cap.read()
        if not ok or frame is None:
            return False, 0, 0, "opened, but no frame decoded"
        h, w = frame.shape[:2]
        return True, int(w), int(h), ""
    except Exception as e:                          # noqa: BLE001
        return False, 0, 0, str(e)
    finally:
        cap.release()


def candidates_for(ip: str, user: str, pw: str, channel: int,
                   port: int = RTSP_PORT) -> list:
    return [Candidate(url=pattern.format(ip=ip, port=port, user=user, pw=pw,
                                         ch=channel),
                      vendor=vendor, channel=channel)
            for vendor, pattern in URL_PATTERNS]


def probe_channel(ip: str, user: str, pw: str, channel: int,
                  port: int = RTSP_PORT, timeout_s: float = 6.0,
                  opener=None) -> Candidate | None:
    """First pattern that yields a frame wins. Substreams are tried first.

    `opener` resolves at call time rather than being bound as a default. A
    default argument captures the function at definition, which made this
    impossible to substitute — the tests were quietly opening real six-second
    RTSP connections to an address that does not exist.
    """
    opener = opener or open_stream
    last = None
    for cand in candidates_for(ip, user, pw, channel, port):
        ok, w, h, err = opener(cand.url, timeout_s)
        cand.ok, cand.width, cand.height, cand.error = ok, w, h, err
        if ok:
            return cand
        last = cand
    return last                                    # the last failure, for its
                                                   # error message


def probe_device(ip: str, user: str, pw: str, channels: int = 4,
                 port: int = RTSP_PORT, timeout_s: float = 6.0,
                 opener=None) -> Device:
    opener = opener or open_stream
    dev = Device(ip=ip, port=port)
    for ch in range(1, max(1, channels) + 1):
        found = probe_channel(ip, user, pw, ch, port, timeout_s, opener)
        if found is not None:
            dev.channels.append(found)
    return dev


def working(device: Device) -> list:
    return [c for c in device.channels if c.ok]


def advice(device: Device) -> str:
    """What to tell somebody whose DVR answered but gave up no streams."""
    if working(device):
        return ""
    errors = {c.error for c in device.channels if c.error}
    if any("no frame decoded" in e for e in errors):
        return ("The DVR accepted the connection but sent no video. That is "
                "usually the wrong channel number, or a channel with no "
                "camera plugged into it.")
    return ("Port is open but nothing streamed. Check the username and "
            "password first — most DVRs ship with admin and a password set "
            "during installation. If those are right, the RTSP path is in the "
            "DVR's own web interface under Network > RTSP.")
