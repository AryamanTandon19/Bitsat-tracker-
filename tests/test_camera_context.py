"""Where a camera is and what it looks at.

The point of this feature is that a guard reading an alert on their phone
should know where to walk. So the tests are about the phrase the alert ends up
carrying — and about it never being worse than the bare camera name.
"""
from __future__ import annotations

from app.db import Database, describe_camera_context


# ------------------------------------------------- the phrasing (pure)
def test_label_and_facing_read_as_a_sentence():
    ctx = {"label": "B-Block Main Gate", "facing": "main road"}
    assert (describe_camera_context("cam-3", ctx)
            == "B-Block Main Gate, facing main road")


def test_block_stands_in_when_there_is_no_label():
    ctx = {"block": "B-Block", "facing": "main road"}
    assert describe_camera_context("cam-3", ctx) == "B-Block, facing main road"


def test_label_alone_is_enough():
    assert describe_camera_context("cam-3", {"label": "Rear Gate"}) == "Rear Gate"


def test_facing_alone_still_keeps_the_camera_name_for_reference():
    ctx = {"facing": "the visitor lane"}
    assert (describe_camera_context("cam-3", ctx)
            == "cam-3, facing the visitor lane")


def test_no_context_is_never_worse_than_the_camera_name():
    assert describe_camera_context("cam-3", None) == "cam-3"
    assert describe_camera_context("cam-3", {}) == "cam-3"


def test_blank_fields_are_treated_as_unset():
    ctx = {"label": "   ", "block": "", "facing": "  "}
    assert describe_camera_context("gate", ctx) == "gate"


# --------------------------------------------------- persistence (DB)
def test_context_survives_and_upserts(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    try:
        db.set_camera_context("gate", label="Front Gate", facing="the road",
                              actor="admin")
        assert db.describe_camera("gate") == "Front Gate, facing the road"
        # correcting it does not pile up rows
        db.set_camera_context("gate", label="B-Block Gate", actor="admin")
        assert db.describe_camera("gate") == "B-Block Gate"
        assert list(db.list_camera_context()) == ["gate"]
    finally:
        db.close()


def test_setting_context_is_audited(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    try:
        db.set_camera_context("gate", label="Front Gate", actor="alice")
        rows = [r for r in db.audit_rows()
                if r["action"] == "CAMERA_CHANGE"]
        assert rows and rows[-1]["actor"] == "alice"
        assert "context" in rows[-1]["details_json"]
    finally:
        db.close()


def test_an_unset_camera_describes_as_its_name(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    try:
        assert db.describe_camera("never-set") == "never-set"
    finally:
        db.close()
