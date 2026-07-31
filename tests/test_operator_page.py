"""The operator PWA is served whole: page, manifest, service worker, icons."""
from __future__ import annotations

import json
import re

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from app import dashboard, operator
from app.db import Database
from app.main import load_config


@pytest.fixture()
def client(tmp_path):
    class Ctx:
        pass
    c = Ctx()
    c.config = load_config("config.yaml")
    c.config["dashboard"]["auth"] = {"enabled": False}
    c.config_path = str(tmp_path / "config.yaml")
    c.db = Database(str(tmp_path / "t.db"))
    c.workers, c.pipelines, c.analyzer, c.assistant = {}, {}, None, None
    yield TestClient(dashboard.create_app(c))
    c.db.close()


def test_page_is_served_and_never_cached(client):
    r = client.get("/operator")
    assert r.status_code == 200
    assert "VisionGuard Operator" in r.text
    assert "no-store" in r.headers["cache-control"]


def test_page_does_not_shadow_the_console(client):
    assert "Operator" not in client.get("/").text[:400]


def test_manifest_is_installable(client):
    r = client.get("/operator/manifest.webmanifest")
    assert r.status_code == 200
    m = json.loads(r.text)
    # Chrome refuses to offer "add to home screen" without these
    assert m["start_url"] == "/operator" and m["display"] == "standalone"
    sizes = {i["sizes"] for i in m["icons"]}
    assert {"192x192", "512x512"} <= sizes


def test_icons_render_as_real_pngs(client):
    for size in (192, 512):
        r = client.get(f"/operator/icon-{size}.png")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert client.get("/operator/icon-99.png").status_code == 404


def test_icon_is_the_requested_size(client):
    import cv2
    import numpy as np
    buf = np.frombuffer(client.get("/operator/icon-512.png").content, np.uint8)
    assert cv2.imdecode(buf, cv2.IMREAD_COLOR).shape[:2] == (512, 512)


def test_the_typeface_is_bundled_not_fetched_from_a_cdn(client):
    html = client.get("/operator").text
    # a font CDN would silently fall back to the system font at a gate with
    # no signal, which is the one place this app has to work
    assert "fonts.googleapis.com" not in html and "fonts.gstatic.com" not in html
    assert 'src:url(/operator/font.woff2)' in html

    r = client.get("/operator/font.woff2")
    assert r.status_code == 200
    assert r.headers["content-type"] == "font/woff2"
    assert r.content[:4] == b"wOF2"
    assert "immutable" in r.headers["cache-control"]


def test_the_service_worker_caches_the_typeface(client):
    # otherwise the shell loads offline but loses the font
    assert "/operator/font.woff2" in client.get("/operator/sw.js").text


def test_service_worker_is_served_uncached(client):
    r = client.get("/operator/sw.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    # a stale service worker can pin a broken app forever
    assert "no-store" in r.headers["cache-control"]


def test_service_worker_never_caches_api_responses(client):
    # stale alerts are worse than no alerts
    assert 'url.pathname.startsWith("/api/")' in client.get("/operator/sw.js").text


def test_every_element_the_script_reaches_for_exists(client):
    html = client.get("/operator").text
    ids = set(re.findall(r'id="([^"]+)"', html))
    used = set(re.findall(r'\$\("#([\w-]+)"\)', html))
    assert used <= ids, f"script looks up missing ids: {used - ids}"


def test_the_page_only_calls_endpoints_that_exist(client):
    html = client.get("/operator").text
    paths = set(re.findall(r'["`](/api/[\w/{}$.-]+)', html))
    # strip template interpolation and query strings back to a routable path
    for p in paths:
        p = re.sub(r"\$\{[^}]*\}", "1", p).split("?")[0]
        assert client.get(p).status_code != 404, p


def test_reduced_motion_override_outranks_the_rules_it_disables(client):
    """The background blooms are set up on .aurora span:nth-child(N), so a
    plain `.aurora span{animation:none}` loses on specificity and the screen
    keeps moving for someone who asked it to stop. Verified in a browser:
    frames 6s apart are pixel-identical under reduced motion."""
    css = client.get("/operator").text
    guard = css.split("prefers-reduced-motion")[1]
    assert ".aurora span:nth-child(n){animation:none}" in guard


def test_severity_reads_as_a_whole_panel_not_a_stripe(client):
    css = client.get("/operator").text
    for sev, rgb in (("sev-HIGH", "255,97,131"), ("sev-MEDIUM", "255,180,92"),
                     ("done", "74,222,158")):
        block = css.split(f".card.{sev}{{")[1].split("}")[0]
        assert f"linear-gradient(152deg,rgba({rgb}" in block
        assert "border-color:rgba" in block


def test_untriaged_alerts_sort_above_handled_ones(client):
    assert "(!!a.verdict - !!b.verdict) || (b.ts - a.ts)" in client.get("/operator").text
