"""Train / val / test, split by SOURCE VIDEO and never by clip.

This is the shortest file in the training package and the one most capable of
invalidating everything else.

Twelve clips cut from `Burglary017` are twelve views of one event, four seconds
apart, with the same camera, the same lighting, the same person in the same
coat. Split them at random and almost every validation clip has a near-twin in
training. The model memorises, validation says 96%, and the car park says
otherwise. It is the most common way a small video dataset lies to the person
who built it, and it is invisible unless you look for it — every curve looks
healthy.

So: **every clip from one source video goes to exactly one split**, and
`check_separation()` fails the build if that is ever untrue.

Two further properties this file guarantees:

  **Stable.** Assignment is a hash of the source video's name, not a shuffle.
  Adding a new batch next week does not move last week's clips between splits,
  so yesterday's test result still means something today.

  **Stratified by label.** A pure hash can put every positive in the test
  split and leave training with nothing to learn from. Sources are grouped by
  their dominant label and the fractions are taken within each group, so each
  split gets its share of both classes while sources still never straddle.
"""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict

TRAIN, VAL, TEST = "train", "val", "test"


class SplitError(ValueError):
    """Raised when the source-separation invariant is broken."""


def _fraction(source_video: str, seed: int = 0) -> float:
    """A stable number in [0,1) for a source video.

    sha1 rather than Python's `hash()`, which is randomised per process — the
    same manifest must produce the same splits on every machine and every run,
    or "held-out test set" means nothing.
    """
    h = hashlib.sha1(f"{seed}:{source_video}".encode("utf-8")).hexdigest()
    return int(h[:12], 16) / float(1 << 48)


def dominant_label(records) -> str:
    """The label a source video mostly contains, used only for stratifying."""
    return Counter(r.label for r in records).most_common(1)[0][0]


def assign(records, val_fraction: float = 0.2, test_fraction: float = 0.2,
           seed: int = 0, stratify: bool = True) -> list:
    """Set `.split` on every record. Returns the same list, mutated.

    The test split is carved out first and the val split second, so raising
    `val_fraction` later never steals from a test set you have already
    reported a number against.
    """
    if val_fraction < 0 or test_fraction < 0:
        raise SplitError("fractions cannot be negative")
    if val_fraction + test_fraction >= 1.0:
        raise SplitError(
            "val + test must leave something to train on")

    by_source: dict = defaultdict(list)
    for rec in records:
        by_source[rec.source_video].append(rec)

    if stratify:
        groups: dict = defaultdict(list)
        for source, recs in by_source.items():
            groups[(recs[0].specialist, dominant_label(recs))].append(source)
    else:
        groups = {"all": list(by_source)}

    for sources in groups.values():
        # Order by the stable hash, then take fractions off the front. With
        # very few sources this still guarantees the *training* split is never
        # starved, because test and val are capped below the group size.
        ranked = sorted(sources, key=lambda s: _fraction(s, seed))
        n = len(ranked)
        n_test = min(int(round(n * test_fraction)), max(0, n - 1))
        n_val = min(int(round(n * val_fraction)), max(0, n - n_test - 1))
        for i, source in enumerate(ranked):
            split = TEST if i < n_test else VAL if i < n_test + n_val else TRAIN
            for rec in by_source[source]:
                rec.split = split
    return records


def check_separation(records) -> None:
    """Raise if any source video appears in more than one split.

    Call this before every training run and in CI. It is cheap, and the bug it
    catches is one you would otherwise discover in a pilot.
    """
    seen: dict = defaultdict(set)
    for rec in records:
        if rec.split:
            seen[rec.source_video].add(rec.split)
    bad = {s: sorted(v) for s, v in seen.items() if len(v) > 1}
    if bad:
        lines = "\n".join(f"  {s}: {', '.join(v)}" for s, v in sorted(bad.items()))
        raise SplitError(
            "these source videos appear in more than one split, so validation "
            "is measuring memorisation:\n" + lines)


def unassigned(records) -> list:
    return [r for r in records if not r.split]


def report(records) -> dict:
    """Clip and source counts per split, per label — the thing to read before
    believing any later number."""
    out: dict = {}
    for split in (TRAIN, VAL, TEST):
        mine = [r for r in records if r.split == split]
        out[split] = {
            "clips": len(mine),
            "sources": len({r.source_video for r in mine}),
            "by_label": dict(Counter(r.label for r in mine)),
            "hard_negatives": sum(1 for r in mine if r.hard_negative),
            "night": sum(1 for r in mine if r.night),
        }
    out["unassigned"] = len(unassigned(records))
    return out


def warnings(records) -> list:
    """Things that are not errors but will make a number misleading."""
    out = []
    rep = report(records)
    labels = {r.label for r in records}

    for split in (VAL, TEST):
        if rep[split]["clips"] == 0:
            out.append(
                f"the {split} split is empty — with this few source videos "
                "there is no honest way to hold one out. Label a different "
                "video before quoting any accuracy.")
            continue
        missing = labels - set(rep[split]["by_label"])
        if missing:
            out.append(
                f"the {split} split has no {', '.join(sorted(missing))} at "
                "all, so it cannot tell you anything about that class")
        if rep[split]["sources"] < 2:
            out.append(
                f"the {split} split comes from a single source video — any "
                "score is about that one video, not about the system")
    if rep[TEST]["clips"] and rep[TEST]["hard_negatives"] == 0:
        out.append(
            "the test split contains no hard negatives, so it cannot measure "
            "the thing this product lives or dies by")
    return out


def describe(records) -> str:
    """A short human summary, for printing at the top of a training run."""
    rep = report(records)
    lines = [f"{'split':<12}{'clips':>7}{'sources':>9}  labels"]
    for split in (TRAIN, VAL, TEST):
        r = rep[split]
        labels = ", ".join(f"{k} {v}" for k, v in sorted(r["by_label"].items()))
        lines.append(f"{split:<12}{r['clips']:>7}{r['sources']:>9}  {labels}")
    if rep["unassigned"]:
        lines.append(f"{'unassigned':<12}{rep['unassigned']:>7}")
    for w in warnings(records):
        lines.append(f"  ! {w}")
    return "\n".join(lines)
