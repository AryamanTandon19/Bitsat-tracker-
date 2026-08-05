"""Finding a DVR, guessing its RTSP path, and never leaking the password.

Connecting a camera is the first thing anyone does with this software. It used
to mean running a script, reading its output, hand-editing config.yaml and
restarting — which a guard cannot do.
"""
from __future__ import annotations

import pytest

from app import discovery as D

URL = "rtsp://admin:hunter2@192.168.1.108:554/Streaming/Channels/102"


# ------------------------------------------------------------- passwords
def test_the_password_is_masked_for_display():
    assert D.mask(URL) == "rtsp://admin:****@192.168.1.108:554/Streaming/Channels/102"
    assert "hunter2" not in D.mask(URL)


def test_masking_survives_awkward_passwords():
    for pw in ("p@ss:word", "", "a/b", "####"):
        url = f"rtsp://admin:{pw}@10.0.0.5:554/live/ch1"
        assert pw not in D.mask(url) or pw == ""
        assert D.mask(url).startswith("rtsp://admin:****@")


def test_masking_leaves_a_url_without_credentials_alone():
    plain = "rtsp://192.168.1.9:554/live/ch1"
    assert D.mask(plain) == plain


def test_masking_handles_nothing():
    assert D.mask("") == "" and D.mask(None) == ""


def test_credentials_can_be_read_back_for_reconnecting():
    assert D.credentials_of(URL) == ("admin", "hunter2")
    assert D.credentials_of("rtsp://10.0.0.1/live") == ("", "")


def test_the_host_can_be_read_back():
    assert D.host_of(URL) == "192.168.1.108"
    assert D.host_of("rtsp://10.0.0.1:554/live") == "10.0.0.1"


# ---------------------------------------------------------------- ranges
def test_a_normal_subnet_expands():
    hosts = D.hosts_in("192.168.1.0/24")
    assert len(hosts) == 254 and hosts[0] == "192.168.1.1"


def test_an_absurd_range_is_refused_with_advice():
    """A pasted /8 would spend the afternoon opening two million sockets."""
    with pytest.raises(ValueError, match="smaller range"):
        D.hosts_in("10.0.0.0/8")


def test_a_single_host_works():
    assert D.hosts_in("192.168.1.50/32") == ["192.168.1.50"]


# --------------------------------------------------------------- probing
def test_the_substream_is_tried_before_the_main_stream():
    """A 4K main stream saturates a cheap box and buys nothing — detection
    runs at 640px. Getting this backwards is the difference between four
    cameras on one machine and one."""
    names = [v for v, _p in D.URL_PATTERNS]
    assert names.index("hikvision") < names.index("hikvision-main")
    assert names.index("dahua/cpplus") < names.index("dahua/cpplus-main")


def test_every_pattern_fills_in_completely():
    for _vendor, pattern in D.URL_PATTERNS:
        url = pattern.format(ip="1.2.3.4", port=554, user="u", pw="p", ch=2)
        assert "{" not in url and url.startswith("rtsp://")


def test_the_first_pattern_that_yields_a_frame_wins():
    tried = []

    def opener(url, timeout_s=6.0):
        tried.append(url)
        ok = "realmonitor" in url            # pretend it is a Dahua
        return (ok, 704, 576, "" if ok else "no")

    got = D.probe_channel("10.0.0.5", "admin", "pw", 1, opener=opener)
    assert got.ok and got.vendor == "dahua/cpplus"
    assert got.width == 704
    # it stopped as soon as one worked
    assert len(tried) == 3


def test_a_channel_with_nothing_reports_the_last_error_rather_than_none():
    """Somebody needs to be told why, not handed a blank."""
    got = D.probe_channel("10.0.0.5", "admin", "pw", 1,
                          opener=lambda u, t=6.0: (False, 0, 0, "auth failed"))
    assert got is not None and not got.ok and got.error == "auth failed"


def test_every_channel_is_probed():
    dev = D.probe_device("10.0.0.5", "admin", "pw", channels=3,
                         opener=lambda u, t=6.0: (True, 640, 480, ""))
    assert len(dev.channels) == 3
    assert [c.channel for c in dev.channels] == [1, 2, 3]
    assert len(D.working(dev)) == 3


def test_a_probe_result_never_carries_the_password_outward():
    dev = D.probe_device("10.0.0.5", "admin", "hunter2", channels=1,
                         opener=lambda u, t=6.0: (True, 640, 480, ""))
    blob = str([c.public() for c in dev.channels])
    assert "hunter2" not in blob and "****" in blob


# ---------------------------------------------------------------- advice
def test_a_device_that_answered_but_sent_no_video_gets_specific_advice():
    dev = D.probe_device("10.0.0.5", "admin", "pw", channels=1,
                         opener=lambda u, t=6.0:
                         (False, 0, 0, "opened, but no frame decoded"))
    assert "no camera plugged into it" in D.advice(dev)


def test_a_device_that_refused_everything_is_told_to_check_credentials():
    dev = D.probe_device("10.0.0.5", "admin", "pw", channels=1,
                         opener=lambda u, t=6.0:
                         (False, 0, 0, "the stream would not open"))
    assert "username and password" in D.advice(dev)


def test_a_working_device_gets_no_advice():
    dev = D.probe_device("10.0.0.5", "admin", "pw", channels=1,
                         opener=lambda u, t=6.0: (True, 640, 480, ""))
    assert D.advice(dev) == ""


# ------------------------------------------------------------- port scan
def test_a_closed_port_is_not_a_device():
    assert D.port_open("192.0.2.1", 554, timeout=0.05) is False


def test_the_stream_opener_can_actually_be_substituted(monkeypatch):
    """It could not, and the tests hung for ten minutes finding out.

    `opener=open_stream` as a default argument binds the function at
    definition time, so patching the module attribute changed nothing and
    every probe opened a real six-second RTSP connection to an address that
    does not exist. Resolving it at call time fixes both testing and
    dependency injection.
    """
    monkeypatch.setattr(D, "open_stream",
                        lambda url, timeout_s=6.0: (True, 1, 1, ""))
    got = D.probe_channel("192.0.2.1", "u", "p", 1)
    assert got is not None and got.ok
