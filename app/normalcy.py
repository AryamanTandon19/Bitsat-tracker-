"""Learning what is permanently there, so it stops being an alarm.

The single measured cause of this system crying wolf is a static object misread
as a person. On MEVA's car park a red fire hydrant was detected as a person in
45 of 45 sampled frames, "loitered for 45 seconds", and fired an alert — and
would keep firing, every debounce interval, for as long as the camera ran. A
lamppost, a bin, a bollard, a reflection, a parked car's wing mirror: every
site has several, and each is a permanent siren.

A person cannot be paid to sit and label these on every camera, and the
product's promise is that nobody has to. So the system learns them itself, from
nothing but time:

    **Furniture is always there; a person is passing through.**

That one sentence is the whole method. This keeps, per camera, a coarse grid of
how *persistently* each class is seen in each cell. A cell where "person" is
present in most frames over an hour is not a person — a real person raises a
cell briefly and is gone. A cell where "car" is present almost always is a
parking space, which is context worth knowing too.

Deliberate properties:

  **It decays.** Occupancy is an exponential moving average, so a car that
  parks becomes "normal" after a while and a car that leaves fades back out.
  The map tracks the site as it actually is, not as it was at install.

  **It never suppresses motion.** Suppression requires BOTH that the location
  is persistently occupied by this class AND that the object itself is barely
  moving. A person who walks to exactly where a bollard stands is moving, and
  is not suppressed. This is the same guard the tracking work needed: a
  loiterer stands still too, so stillness alone can never be the test.

  **It is honest about confidence.** A cell needs a minimum number of
  observations before it is allowed to suppress anything. On a camera that has
  been up for ninety seconds it suppresses nothing, and says so.

Pure Python and normalised coordinates, so it is resolution-independent and
testable without a frame. Persistence to the database is a thin layer on top.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

# Grid resolution. 32x18 over a 16:9 frame is a cell roughly the width of a
# person at mid-distance — fine enough to tell a bollard from the car beside
# it, coarse enough that one detection's jitter stays in one or two cells.
COLS = 32
ROWS = 18

# How fast the map forgets, as a half-life in frames. Chosen by working
# backwards from the behaviour wanted at the live rate (~6fps):
#   * continuous furniture should read as static within ~30s (≈180 frames):
#     occupancy = 1-(1-d)^180 crosses 0.65 near a 120-frame half-life.
#   * something that leaves should fade back below the threshold within a few
#     minutes of absence, which the same constant gives.
# Longer felt safer but left a real fire hydrant firing for the first ten
# minutes of every deployment; this is the value that actually catches it.
HALFLIFE_OBS = 120.0

# A cell must have been hit this many times (decayed) before its occupancy may
# suppress anything. Set below the point where occupancy itself crosses the
# threshold, so persistence — not a brief flurry — is always the binding test.
MIN_OBS = 60

# Occupancy at or above this, for the object's own class, marks the cell as
# "this class lives here".
STATIC_OCCUPANCY = 0.65

# How far a track must travel from where it was first seen, in cell widths,
# before it is considered a mobile object — a person — for good. Lifetime
# displacement, not frame-to-frame speed: the tracker loses and re-acquires a
# static object under a fresh id constantly, and a person moving slowly stays
# inside one cell for several frames. What separates them cleanly is that a
# person GOT somewhere and furniture never did.
MOBILE_CELLS = 1.0

# Classes a persistent presence is worth learning. A persistently-present
# "person" is the false alarm we are hunting; a persistently-present "car" is
# a parking space, which the slots feature also cares about.
TRACKED = ("person", "car", "truck", "bus", "motorcycle", "bicycle")

_DECAY = math.log(2.0) / HALFLIFE_OBS


@dataclass
class Cell:
    """One grid cell's memory of one class."""
    occupancy: float = 0.0     # EMA of "was this class here", 0..1
    observations: float = 0.0  # decayed count, for the confidence gate
    last_ts: float = 0.0


@dataclass
class Verdict:
    """Why a detection was or was not treated as furniture."""
    static: bool
    reason: str
    occupancy: float = 0.0
    observations: float = 0.0
    moving: bool = False

    def public(self) -> dict:
        return {"static": self.static, "reason": self.reason,
                "occupancy": round(self.occupancy, 3),
                "observations": round(self.observations, 1),
                "moving": self.moving}


class CameraNormalcy:
    """One camera's learned background. Fed every processed frame."""

    def __init__(self, camera: str, cols: int = COLS, rows: int = ROWS,
                 min_obs: int = MIN_OBS, static_occ: float = STATIC_OCCUPANCY):
        self.camera = camera
        self.cols = cols
        self.rows = rows
        self.min_obs = min_obs
        self.static_occ = static_occ
        # (class, col, row) -> Cell. Sparse: most of the grid is never touched.
        self.cells: dict = {}
        self.frames = 0
        # per track: where it was first seen, and the furthest it has strayed
        # from there. A track that has ever moved MOBILE_CELLS is a person for
        # the rest of its life and is never suppressed.
        self._origin: dict = {}
        self._moved: dict = {}

    # ------------------------------------------------------------------
    def _cell_of(self, box, frame_w: float, frame_h: float) -> tuple:
        """Which grid cell a box's foot point falls in.

        Foot point, not centre: two people at different depths share a centre
        height a metre apart, and where they touch the ground is the honest
        location — the same choice the feature extractor makes.
        """
        x1, y1, x2, y2 = box
        fx = (x1 + x2) / 2 / max(1.0, frame_w)
        fy = y2 / max(1.0, frame_h)
        col = min(self.cols - 1, max(0, int(fx * self.cols)))
        row = min(self.rows - 1, max(0, int(fy * self.rows)))
        return col, row

    def observe(self, detections, frame_w: float, frame_h: float,
                ts: float | None = None) -> None:
        """Fold one frame's detections into the map.

        `detections` is anything with `.cls_name`, `.xyxy`, and optionally
        `.track_id` — the live Detection, or a duck-typed test double.
        """
        ts = time.time() if ts is None else ts
        self.frames += 1

        # Which (class, cell) were occupied this frame, and where each track is.
        hits = set()
        for d in detections:
            cls = getattr(d, "cls_name", None)
            if cls not in TRACKED:
                continue
            col, row = self._cell_of(d.xyxy, frame_w, frame_h)
            hits.add((cls, col, row))
            tid = getattr(d, "track_id", None)
            if tid is not None:
                if tid not in self._origin:
                    self._origin[tid] = (col, row)
                ocol, orow = self._origin[tid]
                self._moved[tid] = max(self._moved.get(tid, 0.0),
                                       math.hypot(col - ocol, row - orow))

        # Every cell already in the map is stepped: a hit pushes its occupancy
        # towards 1, a miss towards 0. This is what makes "always here"
        # converge to 1 and "seen once" fade back out.
        for key, cell in self.cells.items():
            self._step(cell, key in hits, ts)
            hits.discard(key)
        # cells hit this frame that had no prior memory
        for key in hits:
            cell = self.cells[key] = Cell()
            self._step(cell, True, ts)

    def _step(self, cell: Cell, hit: bool, ts: float) -> None:
        h = 1.0 if hit else 0.0
        # occupancy: an exponential moving average of hit/miss, so it is the
        # fraction of recent frames this class was present in this cell.
        cell.occupancy += _DECAY * (h - cell.occupancy)
        # observations: a decayed count of actual hits, the confidence gate.
        # A cell hit almost every frame saturates near 1/_DECAY; a cell hit
        # once sits near 1. Suppression is only allowed once this is high, so
        # a spot must have been genuinely occupied many times.
        cell.observations = cell.observations * (1.0 - _DECAY) + h
        cell.last_ts = ts

    # ------------------------------------------------------------------
    def is_mobile(self, detection) -> bool:
        """Has this track ever travelled far enough to be a person?

        A track with no id is treated as mobile — an untracked detection
        carries no lifetime, and refusing to suppress it is the safe default.
        """
        tid = getattr(detection, "track_id", None)
        if tid is None:
            return True
        return self._moved.get(tid, 0.0) >= MOBILE_CELLS

    def classify(self, detection, frame_w: float, frame_h: float,
                 ts: float | None = None) -> Verdict:
        """Is this detection furniture — a persistent static thing — or real?"""
        ts = time.time() if ts is None else ts
        cls = getattr(detection, "cls_name", None)
        col, row = self._cell_of(detection.xyxy, frame_w, frame_h)
        cell = self.cells.get((cls, col, row))

        if cell is None or cell.observations < self.min_obs:
            return Verdict(False, "not enough history here yet",
                           occupancy=cell.occupancy if cell else 0.0,
                           observations=cell.observations if cell else 0.0)

        if cell.occupancy < self.static_occ:
            return Verdict(False, "this class is not persistent here",
                           occupancy=cell.occupancy,
                           observations=cell.observations)

        if self.is_mobile(detection):
            # persistent location, but this track GOT here by moving — a real
            # person standing where a bollard also stands. Furniture never
            # moved to get anywhere.
            return Verdict(False, "persistent spot, but this one has moved",
                           occupancy=cell.occupancy,
                           observations=cell.observations, moving=True)

        return Verdict(
            True,
            f"a {cls} is seen here {cell.occupancy*100:.0f}% of the time and "
            "this one is not moving — learned furniture",
            occupancy=cell.occupancy, observations=cell.observations)

    # ------------------------------------------------------------------
    def static_map(self) -> list:
        """Every cell currently classed as furniture — for the overlay and for
        anyone asking what the camera has decided is background."""
        out = []
        for (cls, col, row), cell in self.cells.items():
            if cell.observations >= self.min_obs and \
                    cell.occupancy >= self.static_occ:
                out.append({"class": cls, "col": col, "row": row,
                            "occupancy": round(cell.occupancy, 3)})
        return out

    def summary(self) -> dict:
        static = self.static_map()
        return {"camera": self.camera, "frames": self.frames,
                "cells_tracked": len(self.cells),
                "static_cells": len(static),
                "static": static}

    # ---- persistence -------------------------------------------------
    def to_rows(self) -> list:
        """Flatten for the database. Only cells worth saving."""
        return [{"camera": self.camera, "cls": cls, "col": col, "row": row,
                 "occupancy": cell.occupancy, "observations": cell.observations,
                 "last_ts": cell.last_ts}
                for (cls, col, row), cell in self.cells.items()
                if cell.observations >= 1.0]

    def load_rows(self, rows) -> None:
        """Restore from the database, so a restart does not relearn from zero.

        A camera that has learned its background over a week must not forget it
        because the box rebooted at 3am.
        """
        for r in rows:
            self.cells[(r["cls"], int(r["col"]), int(r["row"]))] = Cell(
                occupancy=float(r["occupancy"]),
                observations=float(r["observations"]),
                last_ts=float(r["last_ts"] or 0.0))
