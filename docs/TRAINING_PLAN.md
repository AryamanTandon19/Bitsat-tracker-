# VisionGuard — Upgraded Training Plan (RTX 4050 Laptop, 6 GB VRAM)

Written against an inspection of this repository on 2026-08-03, not from
memory of the other project. Everything in §1 was verified by reading the
code; anything I could not determine is marked so.

---

## 1. What is actually here

### 1.1 Present — inference and decision only

| Module | What it does | Keep? |
|---|---|---|
| `app/specialist.py` | R3D-18 **inference wrapper**. Builds torchvision `r3d_18` with a 2-logit head; accepts a pickled `nn.Module` or a state-dict. Resolves the suspicious class **by name** from checkpoint metadata, falling back to config, then a documented default — an inverted index would silently turn 0.92 "break-in" into 0.08. | **Keep — it is the contract** |
| `app/temporal.py` | Per-`(camera, event_type)` confirmation: 2 of the last 3 overlapping windows ≥ 0.70, plus a peak ≥ 0.85 strong gate. Isolated per camera. | Keep, **recalibrate** |
| `app/hybrid.py` | Rolling 4.0 s frame buffer, inference every 2.0 s, requires ≥3.6 s of span. Timestamp-driven, not wall-clock, so offline analysis is deterministic. | Keep |
| `app/fusion.py` | Six independent evidence groups → NORMAL / WATCH / AI_REVIEW / CONFIRMED_INCIDENT. Already encodes the rule you asked for. | **Keep — do not rebuild** |
| `app/trigger.py` | Candidate signals: `NEAR_VEHICLE`, `LINGERING`, `AT_NIGHT`, `AT_VEHICLE`, `DEPARTURE`, `BREAK_IN`, `DISTURBANCE`. Deliberately a wide net. | Keep as gate + feature source |
| `app/track.py`, `app/segment.py`, `app/tagging.py`, `app/annotations.py` | Tracking, masks, click-to-tag, annotation store with review states + COCO export. | Keep |
| `app/measure.py` | One matching rule shared by the harnesses. | Keep, extend |
| `app/analyze.py::_merge_incidents` | Merges events within `gap_s=20` — **offline only**. | Promote to live |

### 1.2 The exact preprocessing contract (do not change casually)

```
16 frames, sampled linspace(0, n-1, 16), last frame repeated if short
BGR -> RGB
resize 128x128, cv2.INTER_AREA
/255.0
Kinetics norm: mean (0.43216, 0.394666, 0.37645)
                std (0.22803, 0.22145, 0.216989)
-> float32 [1, 3, 16, 128, 128]
rolling clip 4.0 s, inference every 2.0 s, >= 3.6 s span required
```

This is implemented and unit-tested in `app/specialist.py` and
`tests/test_specialist.py`. **Any new model must either honour it exactly or
ship with a matching change to `preprocess_clip`.** Training-time and
inference-time preprocessing drifting apart is the most common silent killer
in this kind of project, and this repo has already paid to get it right.

### 1.3 Absent

* **No training code anywhere.** No optimizer, no DataLoader, no loss.
* **No checkpoints.** `models/` does not exist; `*.pt` is gitignored.
* **No video-clip dataset and no manifest.** Only `registry.csv` and
  `testset/labels.csv` (9 MEVA clips, no incidents).
* Live rising-edge incident logic (only the offline merger exists).

**Conclusion: the R3D-18 training lived in the separate Windows project. This
repo consumes its outputs and has never produced one.** So this plan builds a
training pipeline here for the first time — but it does *not* need to build a
decision architecture, because that already exists and is tested.

### 1.4 What I could not determine

**Current GPU memory usage on your laptop.** No GPU in this environment and
torch is not installed. Run `training/profile_gpu.py` (step 7) and paste the
output — the plan's batch sizes assume the analytic estimate below.

Analytic estimate for R3D-18 at `[B,3,16,128,128]`, AMP on:

| | fp32 | with AMP |
|---|---|---|
| params (33.4 M) + grads + Adam states | ~0.54 GB | ~0.54 GB |
| activations, batch 4 | ~0.6 GB | ~0.35 GB |
| activations, batch 8 | ~1.2 GB | ~0.7 GB |
| **total, batch 8** | ~1.8 GB | **~1.3 GB** |

6 GB is not the constraint here. **Your constraint is dataset size and
labelling time, not VRAM** — which changes what you should train.

---

## 2. Architecture decision

### 2.1 The diagnosis, from your own code

`app/specialist.py`'s docstring records the failure:

> *"the models were observed to drift up to ~0.72-0.78 on unrelated live scenes"*

A full-frame R3D-18 sees a 128×128 thumbnail of the entire camera view. At
that resolution a person at a car door is a dozen pixels. The model cannot
be learning the interaction — it is learning **scene appearance**, which is
exactly why it drifts on an unfamiliar camera and why it collapsed toward a
few classes.

**Full-frame classification is the root cause. Fix that before touching
architecture.**

### 2.2 Three candidates, in the order you should try them

**Tier 0 — engineered track features + gradient-boosted trees. No GPU.**

Build a feature vector per candidate window from what `trigger.py` and
`track.py` already compute: distance from person to nearest vehicle
(normalised by vehicle size), dwell time, approach speed, path curvature,
number of direction reversals, revisits to the same vehicle, time of day,
zone, pose signals, whether the vehicle is registered, prior false-alarm rate
on this camera. Train LightGBM or sklearn's `HistGradientBoostingClassifier`.

Trains in **seconds**, needs a few hundred examples, is **completely
explainable** (feature importances map to sentences a guard understands), and
cannot drift on scene appearance because it never sees pixels.

**This is your baseline and every later model must beat it.** Given priority
#1 is false alarms and #4 is explainability, there is a real chance this is
the answer and you never need a video model at all.

**Tier 1 — frozen image encoder + small temporal head. ~1 GB VRAM.**

Run a frozen ResNet-18 or MobileNetV3 over the 16 frames of a **crop** around
the person↔vehicle pair, then a 2-layer GRU or temporal conv + linear head
over the 16 feature vectors. Only the head trains: ~0.2 M parameters against
R3D-18's 33 M.

With a few hundred clips this will beat a fine-tuned R3D-18, because there is
nothing to overfit. Feature extraction can be cached to disk once, after which
epochs take seconds.

**Tier 2 — fine-tune R3D-18 on crops, honouring the existing contract.**

Freeze `stem`, `layer1`, `layer2`; train `layer3`, `layer4`, `fc`. Batch 8 with
AMP. Only worth it once you have **≥1,500 clips per specialist across ≥8
source videos**. Below that it will overfit exactly as before.

Do **not** use a video transformer. Even as a frozen extractor, VideoMAE/TimeSformer
on 6 GB buys nothing a frozen ResNet-18 doesn't at a tenth of the cost.

### 2.3 The change that matters most: crop, don't classify the frame

Whichever tier, feed the model a **crop around the person–object pair**, not
the full frame:

* union of the person box and the vehicle box, expanded 40%, square-padded,
  resized to 128×128
* the interaction now fills the frame instead of occupying 12 pixels
* the model stops being able to memorise scene backgrounds, which is what
  makes it transfer to an unseen camera
* it also grounds the score: "this score is about *this* person and *that*
  car" is exactly what `fusion.py`'s `relationship` group needs

This single change is worth more than any architecture swap.

---

## 3. Data strategy

### 3.1 Sources

| Class | Source | Status |
|---|---|---|
| Positives — break-in, vehicle tampering | UCF-Crime (Stealing / Burglary / Vandalism / Robbery) | Manual download; you used it before |
| **Normal + hard negatives** | MEVA — **329 hours verified reachable**, free, CC BY-4.0 | `fetch_testset.py` already pulls it |
| Site-specific hard negatives | Your own pilot footage | Later, and worth more than all of the above |

MEVA is the asset here. Your stated lesson — false alarms are more dangerous
than misses — means the *negative* class is what you are short of, and MEVA is
329 hours of exactly that: people walking to cars, opening doors, loading
boots, deliveries, maintenance, reversing, standing around.

### 3.2 Storage-safe cycle (your spec, made concrete)

```
training/clipmine.py --source ucf --batch-gb 2
  1. download <= 2 GB of raw video
  2. read annotations / temporal bounds
  3. cut 4-8 s clips at 128x128 (and crops), CRF 28
  4. verify: decodes, right length, non-black, has the labelled object
  5. append to manifest.jsonl  (one row per clip)
  6. DELETE the raw video
  7. next batch
```

A verified 6 s clip at 128×128 is **~150 KB**. 3,000 clips ≈ **450 MB**. The
whole training set fits in under a gigabyte; the raw footage never
accumulates.

### 3.3 Manifest — the contract everything else reads

```jsonc
{"clip_id": "ucf_burglary017_t0042",
 "path": "clips/ucf/burglary017_t0042.mp4",
 "source_video": "Burglary017_x264",      // THE SPLIT KEY
 "dataset": "ucf-crime",
 "label": "HOUSE_BREAK_IN",               // matches specialist.py class names
 "specialist": "break_in",
 "start_s": 42.0, "end_s": 48.0, "fps": 30,
 "camera_id": "ucf-unknown", "night": false,
 "hard_negative": false, "hn_reason": null,
 "crop": [x1, y1, x2, y2],                // person+object union, or null
 "verified_by": "prelabel+human", "verified_at": 1785...,
 "split": "train"}
```

### 3.4 Source separation — the rule that must not bend

Splits are assigned by hashing `source_video`, **never** per clip:

```python
split = "test" if sha1(source_video) % 10 < 2 else \
        "val"  if sha1(source_video) % 10 < 4 else "train"
```

Twelve clips cut from `Burglary017` are twelve views of one event. Split them
at random and your validation score measures memorisation. A test asserts this
invariant and fails the build if any `source_video` appears in two splits.

### 3.5 Hard-negative queue

`training/hard_negatives.jsonl`, appended to by:
1. every false positive found in continuous-video evaluation (§5),
2. every alert a guard marks "false alarm" in the operator app —
   `db.verdict_rates()` and the `feedback` table already record these.

Each version trains on the previous version's mistakes. This closes the loop
your priority #1 needs.

---

## 4. The 17 steps, adapted to what exists

| # | Step | Notes for this repo |
|---|---|---|
| 1 | Repo + hardware inspection | **Done — §1.** Run `training/profile_gpu.py` for the missing number. |
| 2 | Baseline benchmark | `evaluate_alerts.py` on MEVA gave **47 false alarms/hour, 0% suppressed**. That is the number to beat. Record it before changing anything. |
| 3 | Dataset + label audit | No video dataset exists. Start from zero — nothing to audit, everything to build. |
| 4 | Clip extraction + manifest | `training/clipmine.py` (§3.2). |
| 5 | Source-separated splits | `training/splits.py` + the invariant test (§3.4). Test set written once, then **untouched**. |
| 6 | Smoke test, 20–50 clips | Overfit 30 clips to ~100% train accuracy. If it cannot, the pipeline is broken — find out in 2 minutes, not 2 hours. |
| 7 | GPU profiling | `training/profile_gpu.py` — prints peak VRAM for batch 1/2/4/8. Pick the largest that stays under 4.5 GB. |
| 8 | Frozen-backbone training | Tier 1 (§2.2). `num_workers=0`, AMP, early stopping on **val false-positive rate at fixed recall**, not accuracy. |
| 9 | Controlled unfreezing | Only if Tier 1 plateaus *and* you have ≥1,500 clips. Unfreeze `layer4` only, LR ÷10, one run, keep the better checkpoint. |
| 10 | Clip-level metrics | Recall, FPR, precision, balanced accuracy, confusion matrix — **on the val split only**. |
| 11 | Sliding-window video test | Reuse `app/hybrid.py`'s exact 4 s / 2 s cadence so evaluation matches production. |
| 12 | Full normal videos | Run whole MEVA hours. **Any alert here is a false alarm by definition** and goes straight to the hard-negative queue. |
| 13 | Unseen cameras | MEVA has 28. Hold 6 out entirely. |
| 14 | Threshold calibration | On **val only**. Replaces the DEV constants 0.70/0.85 in `config.yaml`, which are already flagged in-file as "NOT production constants". |
| 15 | Integrate | Point `hybrid.specialist.*` at the new weights, set `enabled: true`. `fusion.py` and `temporal.py` need no changes. |
| 16 | Hard-negative loop | §3.5. |
| 17 | Untouched holdout | Run **once**. Whatever it says is the number you may quote. If you run it twice and tune in between, it is no longer a holdout. |

---

## 5. Evaluation — incident level, not clip level

Clip accuracy is nearly meaningless for this product. `evaluate_alerts.py`
already scores at incident level; extend it to report:

* **false alerts per hour** on continuous normal footage ← *the headline*
* incident recall (did we catch it at all)
* time-to-trigger from incident start
* **alerts per incident** (must be ~1, not 30)
* per-camera breakdown, unseen cameras separated
* stability across lighting / distance / angle

### Rising-edge logic — DONE (step 0, `app/incidents.py`)

*This section originally overstated the problem; corrected after reading
`notify.py` properly.* The live path already grouped events into incidents
(`db.insert_event`) and already labelled follow-ups "UPDATE — same incident".
What it did not do was **stop sending**, and the only brake was
`max_notifications_per_hour`.

That crude cap was the actual danger. Measured on a realistic four-minute
incident — 40 MEDIUM events as track ids churn, then the break-in:

| | messages | did the HIGH break-in arrive? |
|---|---|---|
| before | 10, then 39 events dropped by the cap | **No — silenced by the cap** |
| after | 2 (open, escalate) | **Yes** |

A rate limit that suppresses the most important alert because thirty
unimportant ones came first is worse than no rate limit. `app/incidents.py`
gates on the rising edge instead: open → alert, sustained → record quietly,
**worse → always alert**, long-running → remind rarely, quiet → close.
Grouping is by camera and time, deliberately *not* by track id, because the
tracker demonstrably loses and re-acquires people.

---

## 6. Incremental order — one testable change at a time

Every step: branch, change, `pytest`, commit. Rollback is `git checkout`.

| Step | Deliverable | Trains? | Est. |
|---|---|---|---|
| **0** | Live rising-edge incident logic + tests | no | 1 day |
| **1** | `training/` skeleton, manifest schema, split invariant test | no | 1 day |
| **2** | `clipmine.py` for MEVA normals — 500 verified clips | no | 2 days |
| **3** | `profile_gpu.py` on your laptop | no | 1 hour |
| **4** | Feature extractor: candidate window → track feature vector | no | 2 days |
| **5** | **Tier 0 GBM baseline + measured FP/hour** | CPU only | 1 day |
| **6** | `clipmine.py` for UCF positives — 400 clips | no | 2 days |
| **7** | Crop extractor (person∪vehicle, +40%, 128×128) | no | 1 day |
| **8** | **Tier 1: frozen encoder + GRU head on crops** | ~1 GB | 2 days |
| **9** | Continuous-video eval + hard-negative queue | no | 2 days |
| **10** | Threshold calibration on val, wire into `config.yaml` | no | 1 day |
| **11** | Tier 2 R3D-18 fine-tune — **only if 5 and 8 both fall short** | ~1.3 GB | 3 days |
| **12** | Untouched holdout, once | no | 1 day |

Roughly **three weeks**, and you have a measurable false-alarm number by the
end of step 5 — before any video model is trained.

### Windows notes

* `num_workers=0` everywhere. Persist decoded clips as `.npy` so the loader is
  a memory-mapped read, not a video decode.
* Never load a full video into RAM. `clipmine.py` streams with `cv2` and
  writes clips as it goes.
* `torch.backends.cudnn.benchmark = True` — fixed input shapes here.
* Set `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` against fragmentation.
* Checkpoint every epoch to `models/`. A laptop that sleeps mid-run should
  cost you one epoch, not one night.

---

## 7. Ranked against your priorities

1. **Reduce false alarms** — crop-based classification (§2.3), Tier 0 baseline,
   continuous-video evaluation, hard-negative loop, rising-edge logic.
2. **Preserve recall** — early-stop on FPR *at fixed recall*; `trigger.py`
   stays a wide net so the gate never becomes the bottleneck.
3. **Laptop-trainable** — Tier 0 is CPU-only, Tier 1 is ~1 GB, Tier 2 ~1.3 GB.
   VRAM is not your constraint; labelling time is.
4. **Explainable** — `fusion.py` already emits accepted and rejected reasons.
   Tier 0 adds named feature contributions. Nothing here is a black box.
5. **One specialist at a time** — vehicle tampering first: MEVA gives you the
   negatives for free, and it is the pitch's core claim.

## 8. What must not be claimed until step 12

The system currently has **no trained specialist at all** (`enabled: false`,
no weights on disk). Until the untouched holdout in step 12 has been run
exactly once, there is no accuracy number for the learned layer. The
thresholds in `config.yaml` say so in-file, and that honesty should survive
into any deck.
