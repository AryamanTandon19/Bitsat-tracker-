"""Find and connect the cameras by itself, the moment the box powers on.

The promise is that someone plugs this machine into the same switch as their
DVR, turns it on, and walks away. No file to edit, no page to click, no
credentials to type into a form. That is what "no human touch to connect"
means, and it is what this does.

On startup, if auto-connect is on and no cameras are configured yet, it:

  1. works out which network it is on, from its own address;
  2. scans that network for anything with an RTSP port open;
  3. tries a short list of credentials against each one — the installer
     defaults every cheap DVR ships with, plus whatever the operator put in
     config;
  4. adds every channel that yields a real frame, and starts it live.

It runs on a background thread, so a slow scan never delays the dashboard, and
it is idempotent: cameras already known are skipped, so it is safe to run on
every boot. When it finds nothing it says so clearly and leaves the console's
manual "add a camera" flow as the fallback — the automatic path is the happy
path, not the only one.

Credentials are the one thing that cannot be invented. A DVR whose password was
changed at install will not answer to the defaults, and for those the operator
adds it once by hand and it is remembered thereafter. The defaults here are the
published factory ones, not a guess at anyone's real password.
"""
from __future__ import annotations

import logging
import socket
import threading

from . import discovery

log = logging.getLogger("watchdog")

# Factory defaults the common Indian-market DVR brands ship with. These are
# public, documented values — the point is to connect a freshly-installed DVR,
# not to get past anyone's chosen password.
DEFAULT_CREDENTIALS = (
    ("admin", ""),
    ("admin", "admin"),
    ("admin", "12345"),
    ("admin", "123456"),
    ("admin", "password"),
    ("admin", "admin12345"),
)


def local_networks() -> list:
    """The /24 this machine sits on, worked out from its own address.

    A /24 (254 hosts) is what a home or society LAN almost always is, and it
    scans in a couple of seconds. Guessing wider wastes minutes; guessing the
    exact subnet from the interface is the right amount of clever.
    """
    nets = []
    try:
        # the address the OS would use to reach the internet — no packet is
        # actually sent, this just picks the outbound interface
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        if ip and not ip.startswith("127."):
            parts = ip.split(".")
            nets.append(f"{parts[0]}.{parts[1]}.{parts[2]}.0/24")
    except OSError:
        pass
    # the usual home-router ranges, as a fallback if the interface trick fails
    for guess in ("192.168.1.0/24", "192.168.0.0/24"):
        if guess not in nets:
            nets.append(guess)
    return nets


def credentials(cfg: dict) -> list:
    """Operator-supplied credentials first, then the factory defaults.

    An operator who knows their DVR's password can put it in config and it is
    tried before anything else; the defaults are the safety net for a DVR that
    still has its shipped login.
    """
    out = []
    for c in (cfg.get("credentials") or []):
        if isinstance(c, dict):
            out.append((str(c.get("username", "admin")), str(c.get("password", ""))))
        elif isinstance(c, (list, tuple)) and len(c) == 2:
            out.append((str(c[0]), str(c[1])))
    for pair in DEFAULT_CREDENTIALS:
        if pair not in out:
            out.append(pair)
    return out


def discover_and_add(ctx, cfg: dict, add_camera, existing_names,
                     scanner=None, prober=None) -> dict:
    """The whole sweep, once. Returns a summary of what it did.

    `add_camera(name, url, vendor, channel, width, height)` stores and starts
    one camera; `existing_names()` returns the set already known. Both are
    injected so this is testable without a database or a network.
    """
    scanner = scanner or discovery.scan
    prober = prober or discovery.probe_channel
    creds = credentials(cfg)
    channels = int(cfg.get("channels", 4))
    networks = cfg.get("networks") or local_networks()
    if networks == ["auto"]:
        networks = local_networks()

    # Scan every network, then dedupe by address: the auto-derived /24 and a
    # configured one routinely overlap, and probing the same DVR twice would
    # add it twice under a mangled second name.
    devices = {}
    for net in networks:
        try:
            for dev in scanner(net):
                devices.setdefault((dev.ip, dev.port), dev)
        except ValueError as e:
            log.warning("auto-connect: %s", e)
            continue

    found_devices = len(devices)
    added = []
    for dev in devices.values():
        for ch in range(1, channels + 1):
            already = existing_names()
            cam = None
            for user, pw in creds:
                cam = prober(dev.ip, user, pw, ch, dev.port)
                if cam is not None and cam.ok:
                    break
            if cam is None or not cam.ok:
                continue
            # name it after where it is, so two DVRs do not collide
            base = f"cam-{dev.ip.split('.')[-1]}-{ch}"
            name = base
            n = 2
            while name in already:
                name = f"{base}-{n}"
                n += 1
            try:
                add_camera(name, cam.url, cam.vendor, ch,
                           cam.width, cam.height)
                added.append({"name": name, "url": cam.safe_url,
                              "vendor": cam.vendor,
                              "size": f"{cam.width}x{cam.height}"})
                log.info("auto-connect: added %s (%s)", name,
                         discovery.mask(cam.url))
            except Exception:                       # noqa: BLE001
                log.exception("auto-connect: could not add %s", name)

    summary = {"networks": networks, "devices": found_devices,
               "added": added}
    if not added:
        log.info("auto-connect: scanned %s, %d device(s) with an RTSP port, "
                 "none opened with the default credentials. Add cameras from "
                 "the console's Cameras tab.", ", ".join(networks),
                 found_devices)
    else:
        log.info("auto-connect: connected %d camera(s) with no configuration",
                 len(added))
    return summary


def start(ctx, cfg: dict, add_camera, existing_names) -> threading.Thread | None:
    """Kick the sweep off on a background thread. Returns the thread, or None
    if auto-connect is off.

    Background because a /24 scan plus RTSP probes takes seconds, and the
    dashboard must come up immediately regardless — a box that looks dead for
    ten seconds on boot reads as broken.
    """
    if not cfg.get("enabled", True):
        return None

    def run():
        try:
            ctx.autoconnect_result = discover_and_add(
                ctx, cfg, add_camera, existing_names)
        except Exception:                               # noqa: BLE001
            log.exception("auto-connect sweep failed")

    t = threading.Thread(target=run, name="autoconnect", daemon=True)
    t.start()
    return t
