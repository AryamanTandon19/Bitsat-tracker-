"""Two-tier live AI review: gating, escalation, cost tracking (fake client)."""
from datetime import datetime
from types import SimpleNamespace

from app.ai_review import TieredReviewer, estimate_cost_usd
from app.db import Database

DAY = lambda: datetime(2026, 7, 4, 14, 0)
NIGHT = lambda: datetime(2026, 7, 4, 23, 30)

CFG = {"enabled": True, "tier1_model": "claude-haiku-4-5",
       "tier2_model": "claude-opus-4-8", "escalate": True, "cameras": [],
       "schedule": "always", "max_frames": 10, "daily_cap_per_camera": 100,
       "usd_to_inr": 90.0}


class FakeResp:
    def __init__(self, text, in_tok=1000, out_tok=100):
        self.content = [SimpleNamespace(type="text", text=text)]
        self.usage = SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok)


class FakeClient:
    """Returns scripted responses per model; records calls."""
    def __init__(self, by_model):
        self.by_model = by_model
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw["model"])
        return self.by_model[kw["model"]]


def reviewer(tmp_path, cfg=None, client=None, localtime=DAY):
    db = Database(str(tmp_path / "ai.db"))
    r = TieredReviewer(cfg or CFG, db, localtime_fn=localtime)
    r._client = client
    return r, db


def ev():
    return SimpleNamespace(ts=1000.0, camera="gate",
                           event_type="suspicious_activity",
                           severity="MEDIUM", description="d",
                           plate=None, track_ids=[1], confidence=0.5)


def test_cost_estimate():
    # 1M input tokens on haiku = $1; 1M output = $5
    assert estimate_cost_usd("claude-haiku-4-5", 1_000_000, 0) == 1.0
    assert estimate_cost_usd("claude-opus-4-8", 0, 1_000_000) == 25.0
    assert estimate_cost_usd("unknown-model", 1_000_000, 0) == 5.0  # default


def test_not_suspicious_stops_at_tier1(tmp_path):
    client = FakeClient({
        "claude-haiku-4-5": FakeResp('{"suspicious": false, "summary": "people walking"}'),
    })
    r, db = reviewer(tmp_path, client=client)
    out = r.review_clip(ev(), 1, "gate", [b"jpg1", b"jpg2"])
    assert out is not None
    assert out["suspicious"] is False
    assert client.calls == ["claude-haiku-4-5"]        # opus never called
    assert db.ai_reviews_last_24h("gate") == 1
    assert db.ai_cost_summary()["last_24h"]["calls"] == 1
    db.close()


def test_suspicious_escalates_to_tier2(tmp_path):
    client = FakeClient({
        "claude-haiku-4-5": FakeResp('{"suspicious": true, "summary": "person forcing car door"}'),
        "claude-opus-4-8": FakeResp('[{"time_s": 12, "activity": "person smashes car window", "severity": "HIGH"}]'),
    })
    r, db = reviewer(tmp_path, client=client)
    out = r.review_clip(ev(), 1, "gate", [b"jpg"])
    assert out["suspicious"] is True
    assert client.calls == ["claude-haiku-4-5", "claude-opus-4-8"]
    assert out["findings"][0]["activity"] == "person smashes car window"
    assert "12.0s" in out["alert_text"] and "HIGH" in out["alert_text"]
    assert out["cost_inr"] > 0
    # both calls costed and logged
    assert db.ai_cost_summary()["last_24h"]["calls"] == 2
    db.close()


def test_night_only_schedule_gates_daytime(tmp_path):
    cfg = {**CFG, "schedule": "night_only"}
    client = FakeClient({})
    r, db = reviewer(tmp_path, cfg=cfg, client=client, localtime=DAY)
    assert r.active_for("gate") == (False, "outside-schedule")
    assert r.review_clip(ev(), 1, "gate", [b"jpg"]) is None
    assert client.calls == []
    r.localtime_fn = NIGHT
    assert r.active_for("gate") == (True, "ok")
    db.close()


def test_camera_allowlist(tmp_path):
    cfg = {**CFG, "cameras": ["parking"]}
    r, db = reviewer(tmp_path, cfg=cfg, client=FakeClient({}))
    assert r.active_for("gate") == (False, "camera-not-enabled")
    assert r.active_for("parking") == (True, "ok")
    db.close()


def test_daily_cap_brakes_spend(tmp_path):
    cfg = {**CFG, "daily_cap_per_camera": 2}
    client = FakeClient({
        "claude-haiku-4-5": FakeResp('{"suspicious": false, "summary": "x"}'),
    })
    r, db = reviewer(tmp_path, cfg=cfg, client=client)
    assert r.review_clip(ev(), 1, "gate", [b"jpg"]) is not None
    assert r.review_clip(ev(), 2, "gate", [b"jpg"]) is not None
    assert r.review_clip(ev(), 3, "gate", [b"jpg"]) is None   # cap reached
    assert len(client.calls) == 2
    db.close()


def test_disabled_reviewer_skips(tmp_path):
    r, db = reviewer(tmp_path, cfg={**CFG, "enabled": False})
    assert r.active_for("gate") == (False, "disabled")
    db.close()


def test_cost_summary_per_camera(tmp_path):
    db = Database(str(tmp_path / "c.db"))
    db.insert_ai_usage("gate", 1, "claude-haiku-4-5", 1000, 100, 0.0015)
    db.insert_ai_usage("gate", 1, "claude-opus-4-8", 2000, 300, 0.0175)
    db.insert_ai_usage("parking", 2, "claude-haiku-4-5", 1000, 100, 0.0015)
    s = db.ai_cost_summary()
    assert s["last_24h"]["calls"] == 3
    assert abs(s["last_24h"]["cost_usd"] - 0.0205) < 1e-9
    cams = {r["camera"]: r for r in s["per_camera_30d"]}
    assert cams["gate"]["calls"] == 2
    db.close()
