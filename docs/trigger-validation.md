# Trigger validation — the pass/fail test for the system

The **candidate trigger** decides which moments get forwarded to Claude for
review. If it misses a real incident, the whole system fails silently. So we
don't guess whether it's good — we **measure** it on labelled footage.

This is a threshold-tuning + validation exercise, **not** model training. You
need **dozens** of well-labelled clips, not thousands, and no GPU.

## 1. Set up the test set

```
testset/
  clips/          <- put your .mp4 clips here
  labels.csv      <- one row per clip
```

`labels.csv` columns:

| column | meaning |
|---|---|
| `filename` | exact file name in `clips/` |
| `type` | `vehicle_theft` / `loitering` / `tampering` / `trespass_night` / `camera_tamper` / `stealing` / `burglary` / `robbery` / `vandalism` / `normal` |
| `incident` | `yes` or `no` |
| `start_s`, `end_s` | seconds when the incident happens (blank for normal) — **the most important field** |
| `notes` | one-line description |

## 2. Where to get clips

- **Best: UCF-Crime dataset** (real CCTV, already sorted by crime type, ~320×240
  like a society DVR). Grab a few files from the **Stealing / Burglary /
  Robbery / Vandalism** folders + some **Normal** clips.
  Project page: https://www.crcv.ucf.edu/projects/real-world/ ·
  Kaggle mirror: https://www.kaggle.com/datasets/odins0n/ucf-crime-dataset
  It ships a `Temporal_Anomaly_Annotation_for_Testing_Videos.txt` file with the
  exact incident frames — the harness reads it for you (see below).
- **Staged on your own cameras** (best for real deployment): have someone act
  out loitering / pulling door handles / riding a bike out at night, day and
  night. 5–10 per behaviour beats 100 random clips.
- **Quick bootstrap:** YouTube — "CCTV car theft caught on camera", "vehicle
  break-in CCTV", "suspicious loitering CCTV". Trim to ~30s–3min.

## 3. Run the harness

```bash
python validate_triggers.py
```

With UCF clips, let it auto-fill incident times from the annotation file:

```bash
python validate_triggers.py --ucf-annotations Temporal_Anomaly_Annotation_for_Testing_Videos.txt
```

## 4. Read the result

```
INCIDENTS: caught 18/18  ->  RECALL = 100%
NORMAL:    fired on 6/20  ->  false-trigger rate = 30%
COVERAGE:  12% of footage flagged for review (cost proxy)
```

- **RECALL must be ~100%** — every incident caught. A `MISS ✗` line is a
  failure: the trigger never forwarded the crime, so Claude never saw it.
- **False-trigger rate / coverage** = your cost. Claude filters these cheaply,
  so getting recall to 100% comes first; then reduce coverage to cut cost.

## 5. Tune, then re-run

All knobs are in `config.yaml` under `rules.trigger`:

| knob | effect |
|---|---|
| `near_vehicle_px` | bigger = more sensitive to "person near a car" (raise if a theft was missed) |
| `dwell_s` | seconds before "lingering" fires (lower = more sensitive) |
| `on_near_vehicle` / `on_loiter` / `on_zone` / `on_night_person` | turn each signal on/off |

**Recipe:** if RECALL < 100%, loosen (raise `near_vehicle_px`, lower `dwell_s`)
until every incident is caught. Then, if coverage is high, tighten the signals
that fire most on normal clips. Re-run after each change. Iterate.

## Important notes

- **Recall beats precision for the trigger.** A false trigger costs ~₹1 (Claude
  glances and dismisses it); a missed theft costs everything. Keep it loose.
- **Match the footage to your cameras.** Thresholds tuned on 320×240 clips may
  need adjusting for a 1080p camera (distances in pixels change). Validate again
  on clips from your actual cameras before trusting it in production.
- **The test set grows itself.** Every real event the live system flags becomes
  a new labelled clip — recall keeps improving over time.
- **Legal/privacy:** only use footage you have the right to (public datasets,
  your own cameras, staged clips). Real footage stays on your PC; the harness
  runs locally and uploads nothing.
