# Reality check — what real footage actually showed

Everything before this was tested on synthetic motion (which proves the
machinery, not the product). This is the first run against **real CCTV** — the
public MEVA dataset (real 1920×1080 outdoor cameras, CC BY-4.0) — pulled and
scored live with the production detector, tracker and gate. It is a first cycle,
not a full validation, and it is written to be honest about both.

## What was proven

1. **The system runs end to end on real footage.** Fetch a real MEVA video →
   detect → track → the free-layer gate → the incident logic, no changes. The
   plumbing is real, not just green unit tests.

2. **Vehicle detection is strong.** On a real 1080p hospital scene the detector
   found a vehicle in **30 of 30** sampled frames. The "eyes" work on real cars.

3. **No obvious false alarms on ordinary footage** — on the (short) samples
   scored so far, the gate + incident logic produced **0 alerts** over ~5 minutes
   of ordinary hospital footage. Encouraging, but see the limits below before
   reading it as a rate.

## What was found — and fixed

**The default detector was effectively blind to people.** Measured on 40 real
1080p frames:

| detector | people found | vehicles found |
|---|---|---|
| `yolo11n` @ 640 (old default) | **5** | 43 |
| `yolo11n` @ 1280 | 5 | 132 |
| `yolo11s` @ 1280 (**new default**) | **19** | 64 |

The nano model missed ~75% of the people on a wide outdoor scene — the small,
distant figures a real car-park camera is full of. And the entire behaviour
layer depends on seeing the person: no person, no "loitering near a vehicle", no
alert. That almost certainly flatters the "0 false alarms" above, because a
detector that sees fewer people also raises fewer alarms.

**Fix (committed):** the default detection is now `yolo11s.pt` @ imgsz 1280 @
confidence 0.20 — the setting that recovered the people, with the measurement
written into `config.yaml` so the choice is auditable. The product's own
priority order puts person-recall above raw speed, so this is the right default;
the config note tells a CPU-bound, many-camera site how to trade back down.

## The honest limits of this cycle

- **Short footage.** Minutes, not hours. A real false-alarm *rate* is a property
  of long continuous footage; a few minutes can only say "nothing obvious fired".
- **CPU only here.** `yolo11s` @ 1280 runs at well under real time on this box
  (~0.5 s/frame), so scoring hours of footage is slow. A real evaluation wants
  the target GPU.
- **The wrong scenes for the hard case.** MEVA hospital/school cameras are not
  the adversarial car-park-at-night the product is for. The footage that matters
  — someone lingering by parked cars, a break-in — needs the parking-lot MEVA
  cameras (for negatives) and UCF-Crime (for real positives).
- **No recall number yet.** We have measured the false-alarm side only. Recall —
  does it catch a real break-in — needs real positive footage, which has not been
  scored here.

## What a real validation still needs (the pilot checklist)

1. **Hours of MEVA parking-lot footage** → a real false-alarms-per-hour number,
   day and night, with people actually detected.
2. **UCF-Crime positives** → the recall side (does it catch the real thing).
3. **The untouched holdout, run once** → the single number worth quoting.
4. **Night footage specifically** → the biggest unproven risk; the machinery
   (low-light enhancement, the day/night metric split) is in place, but no real
   dark clips have been scored.

None of these are code — they are footage and GPU time. The pipelines to consume
them are all built and tested; this cycle proved they run on real frames and
fixed the one thing that would have quietly crippled them.
