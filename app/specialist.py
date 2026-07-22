"""Specialist R3D-18 inference wrappers — the ChatGPT branch's learned models.

Two temporal-CNN classifiers trained on UCF-Crime-derived footage:

    visionguard_ucf_binary_v3.pt      -> P(HOUSE_BREAK_IN)
    visionguard_vehicle_security_v2.pt -> P(VEHICLE_THEFT_OR_TAMPERING)

These are EVIDENCE, not oracles. A full-frame R3D-18 clip score does not know
which tracked person touched which door/vehicle, and the models were observed
to drift up to ~0.72-0.78 on unrelated live scenes. The fusion layer
(app.fusion) is what turns a score into a decision; the temporal confirmer
(app.temporal) is what smooths single-frame spikes. This module's only job is
to reproduce the EXACT training-time preprocessing and expose a clean
``probability(clip)`` call.

Design notes
------------
* Preprocessing is pure NumPy + cv2 so it is unit-testable with no torch and no
  model weights present.
* torch is imported lazily, inside the model wrapper. If torch or the .pt file
  is missing the model reports ``available == False`` and ``probability()``
  returns ``None`` — the rest of the app keeps running (the Claude free layer
  never depended on these models).
* The suspicious-class index is resolved BY NAME, preferring class names
  embedded in the checkpoint, then config, then the documented default order.
  An inverted index would silently turn 0.92 "break-in" into 0.08 — so the
  resolved mapping is logged loudly at load time.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger(__name__)

# ── exact training-time preprocessing constants (do not "clean up") ──────────
CLIP_SECONDS = 4.0                 # rolling clip duration fed to the model
NUM_FRAMES = 16                    # frames evenly sampled across the clip
FRAME_SIZE = 128                   # each frame resized to 128x128
# Kinetics normalization used by torchvision's r3d_18 pretraining:
MEAN = (0.43216, 0.394666, 0.37645)
STD = (0.22803, 0.22145, 0.216989)

# documented default class orders (MUST match each model's training manifest;
# the checkpoint's own class list wins over these if present).
UCF_CLASSES = ("HOUSE_BREAK_IN", "NORMAL")
UCF_SUSPICIOUS = "HOUSE_BREAK_IN"
VEHICLE_CLASSES = ("NORMAL_VEHICLE_ACTIVITY", "VEHICLE_THEFT_OR_TAMPERING")
VEHICLE_SUSPICIOUS = "VEHICLE_THEFT_OR_TAMPERING"


def preprocess_clip(frames: list[np.ndarray], *, bgr: bool = True) -> np.ndarray:
    """Reproduce the specialist models' exact input pipeline.

    Parameters
    ----------
    frames : list of HxWx3 uint8 arrays (a rolling ~4s window of raw frames).
    bgr    : True if frames are OpenCV BGR (they are converted to RGB); pass
             False if you already hold RGB.

    Returns
    -------
    np.ndarray of shape [1, 3, 16, 128, 128], float32 — batch-ready. Convert to
    a torch tensor at the call site (kept as NumPy so this is torch-free and
    testable).

    Steps (identical to training): sample 16 frames evenly with
    ``linspace(0, n-1, 16)`` (repeat the last frame if fewer than 16 exist),
    BGR->RGB, resize to 128x128 with INTER_AREA, scale to [0,1], then normalize
    per channel with the Kinetics mean/std.
    """
    import cv2

    if not frames:
        raise ValueError("preprocess_clip needs at least one frame")

    # even temporal sampling; pad by repeating the last frame if short
    n = len(frames)
    idx = np.linspace(0, n - 1, NUM_FRAMES).astype(np.int64)
    sampled = [frames[min(i, n - 1)] for i in idx]

    mean = np.asarray(MEAN, dtype=np.float32).reshape(3, 1, 1)
    std = np.asarray(STD, dtype=np.float32).reshape(3, 1, 1)

    chans_t = []  # per-frame [3,128,128]
    for fr in sampled:
        if bgr:
            fr = cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)
        fr = cv2.resize(fr, (FRAME_SIZE, FRAME_SIZE),
                        interpolation=cv2.INTER_AREA)
        fr = fr.astype(np.float32) / 255.0          # [H,W,3]
        fr = np.transpose(fr, (2, 0, 1))            # [3,H,W]
        fr = (fr - mean) / std
        chans_t.append(fr)

    clip = np.stack(chans_t, axis=1)                # [3, T, H, W]
    return clip[np.newaxis, ...].astype(np.float32)  # [1,3,T,H,W]


@dataclass
class SpecialistModel:
    """One learned classifier, loaded lazily with graceful degradation."""

    name: str                       # human label, e.g. "break_in"
    weights_path: str               # path to the .pt checkpoint
    classes: tuple[str, ...]        # class order (fallback if not in ckpt)
    suspicious_class: str           # which class name is the "alert" class
    device: str = "auto"            # auto | cpu | cuda:0
    _model: object = field(default=None, repr=False)
    _torch: object = field(default=None, repr=False)
    _resolved_classes: tuple[str, ...] = ()
    _suspicious_idx: int = -1
    available: bool = False
    load_error: str = ""

    def load(self) -> bool:
        """Attempt to load torch + the checkpoint. Safe to call repeatedly;
        returns ``available``. Never raises — records ``load_error`` instead."""
        if self.available:
            return True
        try:
            import torch
        except Exception as e:                       # torch not installed here
            self.load_error = f"torch unavailable: {e}"
            log.warning("[specialist %s] %s", self.name, self.load_error)
            return False
        try:
            import os
            if not os.path.exists(self.weights_path):
                self.load_error = f"weights not found: {self.weights_path}"
                log.warning("[specialist %s] %s", self.name, self.load_error)
                return False
            self._torch = torch
            dev = self._pick_device(torch)
            ckpt = torch.load(self.weights_path, map_location=dev)
            model, ckpt_classes = self._build_model(torch, ckpt)
            model.eval().to(dev)
            self._model = model
            self._device = dev
            self._resolve_classes(ckpt_classes)
            self.available = True
            log.info("[specialist %s] loaded (%s) classes=%s suspicious=%s@%d",
                     self.name, dev, self._resolved_classes,
                     self.suspicious_class, self._suspicious_idx)
            return True
        except Exception as e:                       # bad checkpoint / arch
            self.load_error = f"load failed: {e}"
            log.exception("[specialist %s] load failed", self.name)
            return False

    def _pick_device(self, torch) -> str:
        if self.device != "auto":
            return self.device
        try:
            return "cuda:0" if torch.cuda.is_available() else "cpu"
        except Exception:
            return "cpu"

    def _build_model(self, torch, ckpt):
        """Accept either a pickled nn.Module or a state_dict-style checkpoint.

        For a state_dict we reconstruct torchvision's r3d_18 with a 2-logit
        head. Returns (model, class_names_or_None)."""
        import torch.nn as nn

        # a fully pickled module
        if isinstance(ckpt, nn.Module):
            return ckpt, getattr(ckpt, "classes", None)

        classes = None
        state = ckpt
        if isinstance(ckpt, dict):
            for key in ("classes", "class_names", "labels", "idx_to_class"):
                if key in ckpt:
                    classes = self._coerce_classes(ckpt[key])
                    break
            for key in ("model", "state_dict", "model_state_dict", "weights"):
                if key in ckpt and isinstance(ckpt[key], dict):
                    state = ckpt[key]
                    break

        from torchvision.models.video import r3d_18
        n_cls = len(classes) if classes else len(self.classes)
        model = r3d_18(weights=None)
        model.fc = nn.Linear(model.fc.in_features, n_cls)
        model.load_state_dict(state)
        return model, classes

    @staticmethod
    def _coerce_classes(val) -> tuple[str, ...] | None:
        if isinstance(val, dict):                    # {idx: name}
            try:
                return tuple(val[k] for k in sorted(val))
            except Exception:
                return tuple(str(v) for v in val.values())
        if isinstance(val, (list, tuple)):
            return tuple(str(v) for v in val)
        return None

    def _resolve_classes(self, ckpt_classes):
        self._resolved_classes = ckpt_classes or self.classes
        if self.suspicious_class in self._resolved_classes:
            self._suspicious_idx = self._resolved_classes.index(
                self.suspicious_class)
        else:
            # last resort: assume the documented order's suspicious index
            self._suspicious_idx = (self.classes.index(self.suspicious_class)
                                    if self.suspicious_class in self.classes
                                    else 0)
            log.warning("[specialist %s] suspicious class %r not in checkpoint "
                        "classes %s — defaulting to index %d; VERIFY this!",
                        self.name, self.suspicious_class,
                        self._resolved_classes, self._suspicious_idx)

    def probability(self, clip_frames: list[np.ndarray], *,
                    bgr: bool = True) -> float | None:
        """Softmax probability of the suspicious class for one clip, or None if
        the model is unavailable. Never raises on inference — logs and returns
        None so a model glitch cannot crash the pipeline."""
        if not self.available and not self.load():
            return None
        try:
            torch = self._torch
            arr = preprocess_clip(clip_frames, bgr=bgr)
            with torch.no_grad():
                x = torch.from_numpy(arr).to(self._device)
                logits = self._model(x)
                probs = torch.softmax(logits, dim=1)[0]
                return float(probs[self._suspicious_idx].item())
        except Exception:
            log.exception("[specialist %s] inference failed", self.name)
            return None


class SpecialistBank:
    """Holds both specialist models. Lazy, degrade-gracefully, config-driven.

    config (config.yaml ``hybrid.specialist``)::

        enabled: false                 # master switch (default off = free layer only)
        device: auto
        break_in:
          weights: models/visionguard_ucf_binary_v3.pt
          classes: [HOUSE_BREAK_IN, NORMAL]
          suspicious_class: HOUSE_BREAK_IN
        vehicle:
          weights: models/visionguard_vehicle_security_v2.pt
          classes: [NORMAL_VEHICLE_ACTIVITY, VEHICLE_THEFT_OR_TAMPERING]
          suspicious_class: VEHICLE_THEFT_OR_TAMPERING
    """

    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.enabled = bool(cfg.get("enabled", False))
        device = cfg.get("device", "auto")
        bi = cfg.get("break_in", {})
        ve = cfg.get("vehicle", {})
        self.break_in = SpecialistModel(
            name="break_in",
            weights_path=bi.get("weights",
                                "models/visionguard_ucf_binary_v3.pt"),
            classes=tuple(bi.get("classes", UCF_CLASSES)),
            suspicious_class=bi.get("suspicious_class", UCF_SUSPICIOUS),
            device=device)
        self.vehicle = SpecialistModel(
            name="vehicle",
            weights_path=ve.get("weights",
                                "models/visionguard_vehicle_security_v2.pt"),
            classes=tuple(ve.get("classes", VEHICLE_CLASSES)),
            suspicious_class=ve.get("suspicious_class", VEHICLE_SUSPICIOUS),
            device=device)

    def warmup(self) -> dict[str, bool]:
        """Try to load both models now (so failures surface at startup, not on
        the first incident). Returns {name: available}."""
        if not self.enabled:
            return {"break_in": False, "vehicle": False}
        return {"break_in": self.break_in.load(),
                "vehicle": self.vehicle.load()}

    def score(self, clip_frames: list[np.ndarray], *, which: str,
              bgr: bool = True) -> float | None:
        """Score a clip with one model. ``which`` is 'break_in' or 'vehicle'."""
        if not self.enabled:
            return None
        model = getattr(self, which, None)
        if model is None:
            raise ValueError(f"unknown specialist model: {which!r}")
        return model.probability(clip_frames, bgr=bgr)
