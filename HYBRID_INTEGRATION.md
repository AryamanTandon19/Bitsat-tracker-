# VisionGuard Hybrid — Integration Handoff (Milestone 1)

This is the bridge between the two branches. The Claude free layer stays the
backbone; the ChatGPT specialist R3D-18 models plug in as **evidence**, and a
new fusion layer makes the final call so no single signal can raise a critical
alert.

## What Milestone 1 delivers (this commit)

Three new, isolated, unit-tested modules. **None of them touch the working
pipeline yet** — they are safe to merge and wire in incrementally.

| Module | Purpose | Tests | Needs torch? |
|--------|---------|-------|--------------|
| `app/specialist.py` | Load the two `.pt` models, reproduce the **exact** training preprocessing, return `P(suspicious)` | `tests/test_specialist.py` | only for inference; preprocessing + degradation tested without it |
| `app/temporal.py` | Per-`(camera_id, event_type)` sliding-window confirmation (2-of-3 @ 0.70 + peak ≥ 0.85) | `tests/test_temporal.py` | no |
| `app/fusion.py` | Fuse independent evidence groups → `NORMAL / WATCH / AI_REVIEW / CONFIRMED_INCIDENT` | `tests/test_fusion.py` | no |

Config lives under `hybrid:` in `config.yaml`, **disabled by default** — the
free layer runs exactly as before until you set `hybrid.specialist.enabled: true`
and the weights exist.

Run the whole new surface:
```bash
python -m pytest tests/test_specialist.py tests/test_temporal.py tests/test_fusion.py -q
```

## The exact model contract (locked down in code)

From the ChatGPT handoff — reproduced verbatim in `app/specialist.py` so it can
never drift:

```
clip:        ~4.0 s rolling window
frames:      16, evenly sampled  ->  linspace(0, n-1, 16).astype(int64)
             (repeat the last frame if fewer than 16 exist)
color:       BGR -> RGB
resize:      128 x 128, cv2.INTER_AREA
scale:       float / 255.0
normalize:   mean = [0.43216, 0.394666, 0.37645]
             std  = [0.22803, 0.22145, 0.216989]
tensor:      [C, T, H, W] = [3, 16, 128, 128]  ->  batch [1, 3, 16, 128, 128]
infer:       every ~2 s (windows overlap)
```

Models and their suspicious class (resolved **by name**, checkpoint-embedded
names win over config — an inverted index would silently flip 0.92 → 0.08):

```
models/visionguard_ucf_binary_v3.pt        classes [HOUSE_BREAK_IN, NORMAL]
                                           suspicious = HOUSE_BREAK_IN
models/visionguard_vehicle_security_v2.pt  classes [NORMAL_VEHICLE_ACTIVITY,
                                                     VEHICLE_THEFT_OR_TAMPERING]
                                           suspicious = VEHICLE_THEFT_OR_TAMPERING
```

> ⚠️ **Verify the class order against each model's training manifest.** The
> loader prefers class names stored in the checkpoint; if yours doesn't store
> them, the `config.yaml` order is used, so it must match training exactly.

## How to wire it into the live pipeline (Milestone 2 — next step)

The pipeline already computes free-layer signals per analyzed frame. Add a
rolling clip buffer, score the relevant model, confirm temporally, then fuse.
Sketch for `app/main.py` `CameraPipeline`:

```python
from collections import deque
from .specialist import SpecialistBank
from .temporal import TemporalConfirmer
from .fusion import Evidence, fuse, CONFIRMED_INCIDENT, AI_REVIEW

# in __init__:
hcfg = ctx.config.get("hybrid", {})
self.specialists = SpecialistBank(hcfg.get("specialist", {}))
self.specialists.warmup()                    # loads weights once, logs status
self.confirmer  = TemporalConfirmer(**hcfg.get("temporal", {}))
self._clip_buf  = deque(maxlen=self._clip_frames())   # ~4 s of raw frames
self._last_infer_ts = 0.0

# in the frame loop, AFTER free-layer trigger.is_candidate(...):
self._clip_buf.append(frame)                 # raw BGR frame
run_models = (self.specialists.enabled and
              ts - self._last_infer_ts >= infer_every_s and
              len(self._clip_buf) >= 8)

specialist_confirmed, specialist_scores = set(), {}
if run_models:
    self._last_infer_ts = ts
    clip = list(self._clip_buf)
    # ROUTE: only run the model the scene warrants (STEP 4 model routing)
    if person_near_vehicle:
        s = self.specialists.score(clip, which="vehicle")
        if s is not None:
            specialist_scores["vehicle"] = s
            if self.confirmer.update(self.cam_name, "vehicle", s).confirmed:
                specialist_confirmed.add("vehicle")
    if person_near_structure_or_entry:
        s = self.specialists.score(clip, which="break_in")
        if s is not None:
            specialist_scores["break_in"] = s
            if self.confirmer.update(self.cam_name, "break_in", s).confirmed:
                specialist_confirmed.add("break_in")

# build the evidence bundle from free-layer reasons + models + state:
ev = Evidence(
    camera_id=self.cam_name,
    context_signals={r for r in reasons if r in CONTEXT_REASONS},
    pose_motion_signals={r for r in reasons if r in ACTION_REASONS},
    specialist_confirmed=specialist_confirmed,
    specialist_scores=specialist_scores,
    state_chain=("vehicle_departure_after_activity"
                 if TRIG_DEPARTURE in reasons else None),
    relationship=bool(self.trigger.last_involved) and person_near_vehicle,
    contradictions=self._contradictions(plate_info),   # e.g. registered_plate
)
result = fuse(ev)

if result.decision == CONFIRMED_INCIDENT:
    sev = "HIGH"        # page a human
elif result.decision == AI_REVIEW:
    sev = "MEDIUM"      # send the evidence packet to Claude/VLM (STEP 12)
else:
    sev = None          # WATCH/NORMAL -> log only, no alert
```

Signal buckets to define once (map free-layer reasons to evidence groups):

```python
CONTEXT_REASONS = {"person_near_vehicle", "person_lingering", "person_at_night",
                   "person_in_parking", "person_in_entry", "person_in_restricted"}
ACTION_REASONS  = {"pose_crouching", "pose_reaching", "pose_arm_swing",
                   "vehicle_disturbance", "person_at_vehicle"}
```

### Reset lifecycle (do not skip)

Call `self.confirmer.reset(self.cam_name)` on **camera disconnect / reconnect /
scene reset**, and `self.confirmer.reset(self.cam_name, event_type)` when an
incident is closed so the next incident starts clean. This is what keeps
histories per-camera and prevents stale confirmation.

## Why the fusion gates look the way they do

Directly encodes the observed failures from the ChatGPT branch:

- **Model-only → WATCH.** A confirmed break-in/vehicle score with no
  person↔object interaction is the exact live domain-shift case (stationary
  person, unrelated webcam, ~0.75). `fuse()` refuses to escalate it.
- **Heuristic-only → WATCH/AI_REVIEW, never CONFIRMED.** Night, proximity,
  crouch, a single motion spike — none can page a human alone.
- **CONFIRMED needs a backbone + corroboration.** Either a temporally-confirmed
  model *or* a remembered state sequence, **plus** an independent grounding
  group (interaction / action / chain), **and** no strong contradiction.
- **Contradictions downgrade.** `recognized_owner`, `registered_plate`,
  `authorized_access` cap at WATCH; softer ones (`daytime_routine`) cap at
  AI_REVIEW. This is the owner-drives-away false alarm, handled.

Every `FusionResult` carries `accepted` / `rejected` reason lists and a
`calibration_note` — that is the explainability requirement (STEP 8).

## Do-not-do (carried over from the handoff)

- Do **not** re-enable `action_r3d18_balanced_baseline_v1.pt` / generic Kinetics
  labels for security decisions.
- Do **not** treat `0.70` / `0.85` as production constants — they are dev values
  in `config.yaml` for a reason.
- Do **not** share one `TemporalConfirmer` history across cameras (the API keys
  by `camera_id`; just pass the real camera name).
- Do **not** let `surface_change + high_motion` alone create a critical event —
  it enters fusion as one action signal, nothing more.

## Milestone checklist

- [x] **M1** Specialist wrapper + temporal confirmer + fusion, isolated & tested
- [ ] **M2** Wire into `app/main.py` live loop behind `hybrid.specialist.enabled`
- [ ] **M2** Wire into `app/analyze.py` offline analyzer (same fusion, per clip)
- [ ] **M3** Reality analyzer over `test_vd_2.mp4`: burn every score/gate/reason
      into the debug overlay (extend the existing overlay)
- [ ] **M4** Incident memory: merge fused decisions into one timeline (buffer +
      refractory) instead of per-frame alerts
- [ ] **M5** Evidence packet → Claude/VLM reviewer for `AI_REVIEW` decisions
- [ ] **M6** Untouched multi-camera holdout before any accuracy claim
```
