"""Low-light frame enhancement for night CCTV.

Dark night footage hides small, low-contrast subjects (a person at a car) so
well that YOLO detects only the big bright objects. Brightening + local-contrast
(CLAHE) the frame BEFORE detection makes those subjects visible to the detector
— the single biggest lever on dark-scene recall. Cheap (a few ms), pure cv2.

`auto` mode only enhances frames that are actually dark, so daytime footage is
untouched.
"""
from __future__ import annotations

import cv2
import numpy as np

_GAMMA_CACHE: dict[float, np.ndarray] = {}


def _gamma_table(gamma: float) -> np.ndarray:
    t = _GAMMA_CACHE.get(gamma)
    if t is None:
        # gamma > 1 brightens: out = (in/255) ** (1/gamma) * 255
        t = ((np.arange(256) / 255.0) ** (1.0 / gamma) * 255.0).astype(np.uint8)
        _GAMMA_CACHE[gamma] = t
    return t


def _is_dark(frame, dark_thresh: float) -> bool:
    """A scene needs enhancement if it is globally dim OR has large dark
    regions (a bright road can lift the global mean while a subject sits in
    shadow — exactly the night-CCTV failure case)."""
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(g.mean()) < dark_thresh or float((g < 50).mean()) > 0.30


def enhance_frame(frame, mode="auto", *, dark_thresh: float = 110.0,
                  clip_limit: float = 2.5, gamma: float = 1.6):
    """Return a brightened/contrast-boosted copy of `frame` (BGR).

    mode: "off" (return as-is) | "on" (always enhance) | "auto" (enhance only
    when the scene is globally dim or has large dark regions). CLAHE is applied
    to the L channel (local contrast) and a gamma curve lifts the shadows.
    """
    if mode in ("off", None, False):
        return frame
    if mode == "auto" and not _is_dark(frame, dark_thresh):
        return frame                      # already well-lit — leave it
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(8, 8))
    l = clahe.apply(l)
    out = cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)
    if gamma and gamma != 1.0:
        out = cv2.LUT(out, _gamma_table(float(gamma)))
    return out
