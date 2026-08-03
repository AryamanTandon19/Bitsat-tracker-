"""The clip manifest — the one contract every training step reads.

Everything downstream (splits, loaders, evaluation, the hard-negative queue)
reads this file and nothing else. Raw footage is downloaded, cut, verified and
then **deleted**; the manifest plus a few hundred kilobytes of clips is what
survives. If a fact about a clip is not in here, it does not exist.

One line of JSON per clip, appended. JSONL rather than a single JSON array so
`clipmine.py` can append after each verified clip and a crash halfway through
a batch costs you the current clip, not the batch.

Two couplings are enforced in code rather than left to discipline:

  **Labels must match `app.specialist`'s class names exactly.** That module's
  docstring records why: it resolves the suspicious class *by name*, because
  an inverted index silently turns 0.92 "break-in" into 0.08. A manifest that
  says "break_in" where the model says "HOUSE_BREAK_IN" would train a model
  whose outputs are read backwards, and nothing would fail loudly.

  **Clip length must sit inside the model's window.** `app.specialist` feeds a
  4.0 s rolling clip. A 2-second training clip teaches the model on a window
  it will never see in production, and a 30-second one is mostly frames the
  model never gets. 4-8 s is the band; the manifest refuses anything else.

Paths are stored relative to the manifest, with forward slashes, so a dataset
built on Windows still loads on the box in the building.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.specialist import (UCF_CLASSES, UCF_SUSPICIOUS, VEHICLE_CLASSES,
                            VEHICLE_SUSPICIOUS)

# Which specialist a clip trains. Mirrors config.yaml's
# `hybrid.specialist.break_in` / `.vehicle`, so a manifest cannot name a
# specialist the application has no slot for.
SPECIALISTS = {
    "break_in": {"classes": UCF_CLASSES, "suspicious": UCF_SUSPICIOUS},
    "vehicle": {"classes": VEHICLE_CLASSES, "suspicious": VEHICLE_SUSPICIOUS},
}

SPLITS = ("train", "val", "test")

# The band the model's own 4.0 s window makes meaningful (see module docstring).
MIN_CLIP_S = 4.0
MAX_CLIP_S = 8.0


class ManifestError(ValueError):
    """Raised with a sentence somebody can act on, not a code."""


@dataclass
class ClipRecord:
    """One verified training clip."""
    clip_id: str
    path: str                      # relative to the manifest, forward slashes
    source_video: str              # THE SPLIT KEY — see training/splits.py
    dataset: str                   # "ucf-crime" | "meva" | "site" | ...
    label: str                     # MUST be one of the specialist's classes
    specialist: str                # "break_in" | "vehicle"
    start_s: float
    end_s: float
    fps: float = 0.0
    camera_id: str = ""
    night: bool = False
    hard_negative: bool = False
    hn_reason: str = ""
    crop: list | None = None       # [x1,y1,x2,y2] person+object union, or None
    verified_by: str = ""
    verified_at: float = 0.0
    split: str = ""
    notes: str = ""

    @property
    def duration_s(self) -> float:
        return round(self.end_s - self.start_s, 3)

    @property
    def suspicious(self) -> bool:
        return self.label == SPECIALISTS[self.specialist]["suspicious"]

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_dict(cls, d: dict) -> "ClipRecord":
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(d) - known
        if unknown:
            raise ManifestError(
                f"clip {d.get('clip_id', '?')}: unknown field(s) "
                f"{', '.join(sorted(unknown))}")
        missing = {"clip_id", "path", "source_video", "dataset", "label",
                   "specialist", "start_s", "end_s"} - set(d)
        if missing:
            raise ManifestError(
                f"clip {d.get('clip_id', '?')}: missing "
                f"{', '.join(sorted(missing))}")
        return cls(**d)


def normalise_path(p, root=None) -> str:
    """-> a forward-slash path relative to the manifest's directory.

    A dataset built on a Windows laptop is routinely trained on elsewhere.
    Absolute `C:\\...` paths in a manifest make it single-machine, and the
    backslashes make it a JSON escaping problem as well.
    """
    p = Path(p)
    if root is not None:
        root = Path(root)
        try:
            p = p.relative_to(root)
        except ValueError:
            pass                    # already relative, or genuinely elsewhere
    return p.as_posix()


def validate(rec: ClipRecord) -> ClipRecord:
    """Refuse anything that would train a model to be quietly wrong."""
    if rec.specialist not in SPECIALISTS:
        raise ManifestError(
            f"clip {rec.clip_id}: specialist must be one of "
            f"{', '.join(sorted(SPECIALISTS))}, not {rec.specialist!r}")
    classes = SPECIALISTS[rec.specialist]["classes"]
    if rec.label not in classes:
        raise ManifestError(
            f"clip {rec.clip_id}: label {rec.label!r} is not a class of the "
            f"{rec.specialist} specialist. It must be exactly one of "
            f"{', '.join(classes)} — app.specialist resolves the suspicious "
            "class by name, so a mismatch reads the model backwards.")
    if not rec.source_video:
        raise ManifestError(
            f"clip {rec.clip_id}: source_video is required — it is the key "
            "the train/val/test split is made on, and without it clips from "
            "one video leak across splits.")
    if rec.end_s <= rec.start_s:
        raise ManifestError(f"clip {rec.clip_id}: end_s must be after start_s")
    if not (MIN_CLIP_S - 1e-6 <= rec.duration_s <= MAX_CLIP_S + 1e-6):
        raise ManifestError(
            f"clip {rec.clip_id}: {rec.duration_s}s is outside the "
            f"{MIN_CLIP_S}-{MAX_CLIP_S}s band. The model reads a "
            "4.0s window; shorter trains it on something it never sees, "
            "longer is mostly frames it never gets.")
    if rec.start_s < 0:
        raise ManifestError(f"clip {rec.clip_id}: start_s cannot be negative")
    if rec.split and rec.split not in SPLITS:
        raise ManifestError(
            f"clip {rec.clip_id}: split must be one of {', '.join(SPLITS)}")
    if rec.hard_negative and rec.suspicious:
        raise ManifestError(
            f"clip {rec.clip_id}: a hard negative cannot be labelled "
            f"{rec.label} — the whole point of one is that it LOOKS "
            "suspicious and is not.")
    if rec.crop is not None:
        if len(rec.crop) != 4:
            raise ManifestError(f"clip {rec.clip_id}: crop must be [x1,y1,x2,y2]")
        x1, y1, x2, y2 = [float(v) for v in rec.crop]
        if x2 <= x1 or y2 <= y1:
            raise ManifestError(f"clip {rec.clip_id}: crop has no area")
    return rec


def make_record(**kw) -> ClipRecord:
    """Build and validate in one step. `verified_at` defaults to now."""
    kw.setdefault("verified_at", time.time())
    return validate(ClipRecord(**kw))


# --------------------------------------------------------------- file io
def write(path, records, append: bool = False) -> int:
    """Write records as JSONL. Returns how many were written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    n = 0
    with open(path, mode, encoding="utf-8") as f:
        for rec in records:
            f.write(rec.to_json() + "\n")
            n += 1
    return n


def append_one(path, rec: ClipRecord) -> None:
    """Append a single verified clip.

    Called by clipmine.py after each clip is cut and checked, so a crash
    halfway through a 2 GB batch costs the current clip rather than the batch.
    """
    write(path, [validate(rec)], append=True)


def read(path, strict: bool = True) -> list:
    """Read a manifest. `strict` raises on the first bad row.

    Non-strict is for inspecting a manifest you suspect is damaged; training
    always reads strict, because a silently skipped clip is a silently
    different dataset.
    """
    path = Path(path)
    if not path.exists():
        raise ManifestError(f"no manifest at {path}")
    out, seen = [], set()
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rec = validate(ClipRecord.from_dict(json.loads(line)))
        except (ValueError, TypeError) as e:
            if strict:
                raise ManifestError(f"{path}:{lineno}: {e}") from None
            continue
        if rec.clip_id in seen:
            if strict:
                raise ManifestError(
                    f"{path}:{lineno}: duplicate clip_id {rec.clip_id!r}")
            continue
        seen.add(rec.clip_id)
        out.append(rec)
    return out


# --------------------------------------------------------------- reporting
def summarise(records) -> dict:
    """What the dataset actually contains, in the terms that decide whether
    it is trainable."""
    by_specialist: dict = {}
    for rec in records:
        s = by_specialist.setdefault(
            rec.specialist,
            {"clips": 0, "sources": set(), "labels": {}, "hard_negatives": 0,
             "night": 0, "seconds": 0.0, "datasets": set()})
        s["clips"] += 1
        s["sources"].add(rec.source_video)
        s["labels"][rec.label] = s["labels"].get(rec.label, 0) + 1
        s["hard_negatives"] += int(rec.hard_negative)
        s["night"] += int(rec.night)
        s["seconds"] += rec.duration_s
        s["datasets"].add(rec.dataset)
    for s in by_specialist.values():
        s["sources"] = len(s["sources"])
        s["datasets"] = sorted(s["datasets"])
        s["seconds"] = round(s["seconds"], 1)
    return {"clips": len(records),
            "sources": len({r.source_video for r in records}),
            "by_specialist": by_specialist}


def readiness(records, specialist: str) -> dict:
    """Is there enough here to train this specialist, and what is missing?

    The thresholds come from the plan: a fine-tune needs roughly 1,500 clips
    across at least 8 source videos, and a class balance worse than 1:4 will
    make the model predict the majority class and score well doing it.
    """
    mine = [r for r in records if r.specialist == specialist]
    if not mine:
        return {"specialist": specialist, "ready": False, "clips": 0,
                "blockers": ["no clips at all"]}
    classes = SPECIALISTS[specialist]["classes"]
    counts = {c: sum(1 for r in mine if r.label == c) for c in classes}
    sources = len({r.source_video for r in mine})
    hn = sum(1 for r in mine if r.hard_negative)
    smallest = min(counts.values())
    largest = max(counts.values())

    blockers = []
    if len(mine) < 1500:
        blockers.append(
            f"{len(mine)} clips — a fine-tune wants ~1500; below that it "
            "overfits, which is what happened last time")
    if sources < 8:
        blockers.append(
            f"only {sources} source videos — the model will learn those "
            "scenes, not the behaviour")
    if smallest == 0:
        missing = [c for c, n in counts.items() if n == 0]
        blockers.append(f"no examples at all of {', '.join(missing)}")
    elif largest / smallest > 4:
        blockers.append(
            f"class balance {largest}:{smallest} — beyond about 4:1 the model "
            "learns to predict the majority class and scores well doing it")
    if hn < len(mine) * 0.15:
        blockers.append(
            f"only {hn} hard negatives ({hn / len(mine) * 100:.0f}%) — these "
            "are what stop false alarms, and false alarms are the thing that "
            "kills this product")
    return {"specialist": specialist, "ready": not blockers,
            "clips": len(mine), "sources": sources, "by_label": counts,
            "hard_negatives": hn, "blockers": blockers}
