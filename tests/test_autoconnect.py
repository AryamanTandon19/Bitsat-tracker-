"""Zero-touch connect: find and add the cameras on startup, no clicks.

Everything is injected — no socket is opened, no RTSP stream is touched — so
these run in milliseconds and describe the logic, not the network.
"""
from __future__ import annotations

from app import autoconnect as AC
from app.discovery import Candidate, Device


def a_device(ip="192.168.1.108"):
    return Device(ip=ip)


def a_working(url_contains="realmonitor"):
    """A prober that only the Dahua substream answers, with these creds."""
    def prober(ip, user, pw, ch, port=554):
        ok = user == "admin" and pw == "" and ch <= 2
        return Candidate(
            url=f"rtsp://{user}:{pw}@{ip}:{port}/cam/realmonitor?channel={ch}&subtype=1",
            vendor="dahua/cpplus", channel=ch, ok=ok,
            width=704 if ok else 0, height=576 if ok else 0,
            error="" if ok else "no")
    return prober


class Recorder:
    def __init__(self):
        self.added = []

    def add(self, name, url, vendor, channel, width, height):
        self.added.append({"name": name, "url": url, "vendor": vendor,
                           "channel": channel})
        return len(self.added)

    def names(self):
        return {c["name"] for c in self.added}


# ------------------------------------------------------------- networks
def test_it_derives_a_slash_24_from_the_machines_own_address():
    nets = AC.local_networks()
    assert any(n.endswith("/24") for n in nets)


def test_the_auto_keyword_resolves_to_the_local_networks():
    rec = Recorder()
    summary = AC.discover_and_add(
        None, {"networks": ["auto"]}, rec.add, rec.names,
        scanner=lambda net: [], prober=a_working())
    assert summary["networks"] == AC.local_networks()


# --------------------------------------------------------- credentials
def test_operator_credentials_are_tried_before_the_defaults():
    creds = AC.credentials({"credentials": [{"username": "root", "password": "s3cret"}]})
    assert creds[0] == ("root", "s3cret")
    assert ("admin", "") in creds            # defaults still present after


def test_the_factory_defaults_are_there_with_no_config():
    creds = AC.credentials({})
    assert ("admin", "") in creds and ("admin", "12345") in creds


def test_credentials_accept_both_dict_and_pair_forms():
    creds = AC.credentials({"credentials": [["u", "p"], {"username": "a", "password": "b"}]})
    assert ("u", "p") in creds and ("a", "b") in creds


# ---------------------------------------------------------- the sweep
def test_a_dvr_that_answers_the_defaults_is_connected_with_no_input():
    rec = Recorder()
    summary = AC.discover_and_add(
        None, {"channels": 4}, rec.add, rec.names,
        scanner=lambda net: [a_device()] if net == "192.168.1.0/24" else [],
        prober=a_working())
    # channels 1 and 2 stream, 3 and 4 do not
    assert len(summary["added"]) == 2
    assert {c["channel"] for c in rec.added} == {1, 2}
    assert all("****" in c["url"] for c in summary["added"])


def test_cameras_are_named_by_where_they_are():
    rec = Recorder()
    AC.discover_and_add(
        None, {"channels": 2}, rec.add, rec.names,
        scanner=lambda net: [a_device("192.168.1.108")],
        prober=a_working())
    assert rec.names() == {"cam-108-1", "cam-108-2"}


def test_a_camera_already_known_is_not_added_twice():
    rec = Recorder()
    rec.added.append({"name": "cam-108-1", "url": "x", "vendor": "", "channel": 1})
    AC.discover_and_add(
        None, {"channels": 1}, rec.add, lambda: {"cam-108-1"},
        scanner=lambda net: [a_device("192.168.1.108")],
        prober=a_working())
    # channel 1 was already known, so nothing new is added for it
    assert [c["name"] for c in rec.added].count("cam-108-1") == 1


def test_two_dvrs_do_not_collide_on_names():
    rec = Recorder()
    AC.discover_and_add(
        None, {"channels": 1},
        rec.add, rec.names,
        scanner=lambda net: [a_device("192.168.1.108"),
                             a_device("192.168.1.109")],
        prober=a_working())
    assert rec.names() == {"cam-108-1", "cam-109-1"}


def test_a_dvr_with_a_changed_password_is_left_for_manual_add():
    """The defaults will not open it, and that is not an error — it is added
    once by hand and remembered."""
    rec = Recorder()
    summary = AC.discover_and_add(
        None, {}, rec.add, rec.names,
        scanner=lambda net: [a_device()],
        prober=lambda ip, u, pw, ch, port=554:
            Candidate(url="x", vendor="", channel=ch, ok=False, error="auth"))
    assert summary["added"] == [] and summary["devices"] == 1


def test_an_empty_network_is_reported_not_crashed():
    rec = Recorder()
    summary = AC.discover_and_add(
        None, {}, rec.add, rec.names,
        scanner=lambda net: [], prober=a_working())
    assert summary["added"] == [] and summary["devices"] == 0


def test_a_bad_network_range_is_skipped_not_fatal():
    rec = Recorder()

    def scanner(net):
        raise ValueError("too big")

    summary = AC.discover_and_add(None, {"networks": ["10.0.0.0/8"]},
                                  rec.add, rec.names, scanner=scanner,
                                  prober=a_working())
    assert summary["added"] == []


# --------------------------------------------------------------- switch
def test_auto_connect_can_be_turned_off():
    class Ctx:
        pass
    started = AC.start(Ctx(), {"enabled": False}, lambda *a: None, lambda: set())
    assert started is None
