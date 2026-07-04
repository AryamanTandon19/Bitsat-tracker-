"""Tuning-assistant patch validation + apply (pure, no Claude call needed)."""
import yaml

from app.assistant import (TuningAssistant, apply_patch, editable_view,
                           validate_patch)
from app.db import Database
from app.main import load_config


def base_config(tmp_path):
    cfg = load_config("config.yaml")
    return cfg


def test_editable_view_hides_secrets():
    cfg = load_config("config.yaml")
    view = editable_view(cfg)
    assert "telegram" not in view and "dashboard" not in view
    assert "rules" in view and "detection" in view


def test_validate_patch_accepts_existing_editable_paths():
    cfg = load_config("config.yaml")
    clean, rejected = validate_patch(cfg, {"rules.loitering.dwell_s": 60})
    assert clean == {"rules.loitering.dwell_s": 60}
    assert rejected == []


def test_validate_patch_rejects_secrets_and_unknown_keys():
    cfg = load_config("config.yaml")
    clean, rejected = validate_patch(cfg, {
        "telegram.bot_token": "hacked",          # forbidden section
        "cameras.0.url": "rtsp://evil",          # forbidden section
        "rules.loitering.made_up_key": 5,        # non-existent key
        "rules.loitering.dwell_s": 55,           # OK
    })
    assert clean == {"rules.loitering.dwell_s": 55}
    assert set(rejected) == {"telegram.bot_token", "cameras.0.url",
                             "rules.loitering.made_up_key"}


def test_apply_patch_mutates_live_config_and_persists(tmp_path):
    cfg = load_config("config.yaml")
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(cfg))
    db = Database(str(tmp_path / "a.db"))

    old = cfg["rules"]["loitering"]["dwell_s"]
    res = apply_patch(str(p), cfg, {"rules.loitering.dwell_s": 999,
                                    "telegram.bot_token": "nope"}, db=db)
    # live dict mutated in place (running rule engines see the new value)
    assert cfg["rules"]["loitering"]["dwell_s"] == 999
    assert res["applied"]["rules.loitering.dwell_s"] == {"old": old, "new": 999}
    assert res["rejected"] == ["telegram.bot_token"]
    # persisted to disk
    saved = yaml.safe_load(p.read_text())
    assert saved["rules"]["loitering"]["dwell_s"] == 999
    # secret untouched on disk
    assert saved["telegram"]["bot_token"] == cfg["telegram"]["bot_token"]
    # audited
    actions = [r["action"] for r in db.audit_rows()]
    assert "CONFIG_CHANGE" in actions
    ok, _ = db.verify_audit_chain()
    assert ok
    db.close()


def test_assistant_disabled_returns_message():
    a = TuningAssistant({"enabled": False})
    assert not a.available
    out = a.chat("make it less sensitive", load_config("config.yaml"), [])
    assert "not configured" in out["reply"].lower()
    assert out["patch"] == {}


def test_assistant_parse_handles_json_and_prose():
    parse = TuningAssistant._parse
    good = parse('{"reply":"done","patch":{"rules.loitering.dwell_s":60},'
                 '"explanation":"less sensitive"}')
    assert good["patch"] == {"rules.loitering.dwell_s": 60}
    # code-fenced JSON
    fenced = parse('```json\n{"reply":"ok","patch":{}}\n```')
    assert fenced["reply"] == "ok" and fenced["patch"] == {}
    # plain prose fallback
    prose = parse("I can't do that.")
    assert prose["patch"] == {} and "can't" in prose["reply"]
