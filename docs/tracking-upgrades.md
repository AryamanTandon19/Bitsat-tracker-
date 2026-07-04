# Tracking upgrades — what we took from the surveyed open-source systems

Three open-source tracking systems were reviewed for making culprit tracking
stronger: **Ultralytics YOLO**, **OpenMMLab MMTracking**, and **AI-Camera
(YOLOv8 + DeepSORT + TensorRT)**. This note records what was adopted, what was
deliberately *not* vendored in, and why — so the decision is auditable later.

## The common thread

All three do the same thing at their core: **YOLO-style object detection +
appearance-based re-identification (ReID) tracking**. ReID is the capability we
were missing — it keeps the *same* track ID on a person/vehicle across brief
occlusions and short exits from frame, instead of assigning a new ID (which
ByteTrack, being motion-only, can do). For following a suspect, persistent
identity is the single most valuable upgrade.

## What we adopted

**Ultralytics BoT-SORT with ReID**, enabled by config
(`detection.tracker: "botsort_reid"`, shipped at `app/trackers/botsort_reid.yaml`).

Rationale:
- We already depend on Ultralytics for detection + ByteTrack, so this adds
  **zero new dependencies**.
- `model: auto` reuses the detector's own features for ReID, so there is **no
  separate ReID weights download** and it stays CPU/Windows-portable.
- It delivers the same detection+ReID pipeline that AI-Camera's DeepSORT
  provides, through a config switch rather than a second codebase.

## What we did NOT vendor in, and why

**AI-Camera (DeepSORT + TensorRT).** A solid implementation, but:
- Its speed advantage comes from **NVIDIA TensorRT** (`src/trt_utils/`,
  `trt_engine.py`) — GPU-specific code that would break the CPU-only,
  Windows+Linux portability the prototype requires.
- Its DeepSORT (custom Kalman filter, matching, ReID model) **duplicates** what
  Ultralytics BoT-SORT already gives us, and needs a separate ReID checkpoint.
- Net: it would add ~13 MB and GPU coupling to re-implement a capability we
  already have. Its ideas (appearance ReID, cosine-distance matching) are
  exactly what BoT-SORT does.

**OpenMMLab MMTracking.** A comprehensive research toolbox (SOT/MOT/VIS,
638 files), but:
- Heavy **PyTorch + MMCV** stack; the repo is effectively superseded (its work
  moved into MMDetection). Pinning to it is a maintenance liability.
- Built for research flexibility and benchmarking, not lightweight edge
  deployment on a society PC. It would multiply install size and fragility for
  no capability we can't get from Ultralytics.

## Guiding principle

For a demo/edge prototype, **fewer, well-integrated dependencies beat more
frameworks.** We took the capability (appearance ReID) via the dependency we
already have, and left the heavyweight/GPU-locked codebases out. If phase-2
needs go beyond this — e.g. true action recognition ("this looks like theft"),
face re-identification across cameras, or cross-camera hand-off — those are
dedicated model-training efforts, not a matter of importing one of these repos
wholesale.

## Phase-2 tracking roadmap (when the product graduates from prototype)

1. **Dedicated ReID checkpoint** (e.g. OSNet) instead of `model: auto`, for
   better cross-occlusion identity on crowded scenes.
2. **Cross-camera re-identification** — hand a culprit's identity from the gate
   camera to the parking camera. Needs a shared ReID embedding store.
3. **Action recognition** — a temporal model (e.g. SlowFast / video
   transformer) fine-tuned on labelled incident clips to score "theft-like"
   behaviour, replacing/augmenting the current hand-written rules.
4. **GPU acceleration** (TensorRT / ONNX Runtime) once a GPU box is in place —
   AI-Camera is a good reference for that specific step.
