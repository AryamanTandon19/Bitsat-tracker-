# The Brain — what it is, and how to run it

Written for someone who knows nothing about machine learning. No prior
knowledge assumed.

## What "the brain" is, in one paragraph

The cameras and the detector are the **eyes**: they see "a person is here, a car
is there." That is a snapshot — one frame. The brain is what watches those
snapshots *over a few seconds* and decides whether the behaviour is ordinary or
suspicious: "that person walked past" vs "that person has been circling the car
for thirty seconds and just crouched at the door." Crime is a *sequence*, not a
single picture, and the brain is the part that reads the sequence.

## Why it is built the way it is

We do **not** feed raw video into a big neural network. An earlier attempt did
exactly that, and it failed in a specific, documented way: it started raising
alarms on unrelated scenes because it had memorised what *those* videos looked
like, not what a break-in *is*. (See `app/specialist.py`.)

So the brain reads **geometry**, not pixels. For a person near a vehicle it
measures things a human would say out loud:

- how close did they get, and for how long?
- did they walk straight past, or mill around?
- did they change direction, come back, crouch, touch the car?
- did they actually move at all — or never move, like a fire hydrant a camera
  mistakes for a person?

These numbers cannot "memorise a scene," and they are **explainable**: every
alert comes with a sentence a guard can check against the video —
*"stayed within half a car-length for 34s, circled twice, never walked away."*
A raw-video model can only say "0.87", which no one can argue with.

This also means the brain runs on a **cheap laptop CPU** in seconds — no
expensive GPU needed.

## The two halves of the brain

The brain learns in two stages, because real data arrives in two stages.

1. **The anomaly half** learns what *ordinary* looks like, from ordinary
   footage alone. It needs **no examples of crime** — just normal days by the
   car park. It flags anything that sits far outside normal. This half works
   from day one, on the footage any building already has.

2. **The suspicion half** switches on automatically once you have real
   examples of actual incidents. Then it learns the difference directly. Until
   those exist, the anomaly half plus a plain-language rule ("lingered close and
   did not walk past") carry the decision.

Underneath both sits a safety rule that never changes: **no single score ever
pages a human by itself.** The brain refines the free layer's candidates — it
quietens the false ones and confirms the real ones — but a human alert always
needs corroboration from an independent signal (a pose, a sequence, the free
layer's own reading). That rule lives in `app/fusion.py` and the brain respects
it by design.

---

## How to run it — step by step

You need Python and the project installed (`pip install -r requirements.txt`).
Every command is one line. You do not need to understand what it prints.

### Step 1 — Prove the machinery works, right now (2 minutes)

Before any real footage, you can watch the whole brain train and score on
**synthetic motion** — made-up people walking, loitering, and breaking in, so
the pipeline can be checked end to end.

```
python -m training.brain_train --synth --out models/brain.joblib
```

You will see it train and print something like `recall 1.00 … FPR 0.000`,
clearly labelled **SYNTHETIC**. That number only means *the machine works* — it
is not a real-world accuracy figure, and the tool says so. What you have proven:
the brain trains, separates behaviours, and saves. There is now a working brain
at `models/brain.joblib`.

> The system will automatically load that file next time it starts. With the
> file absent, it simply runs on the free layer, exactly as before — nothing
> breaks either way.

### Step 2 — Get real footage (the one thing only you can do)

The brain is only as good as what it learns from. To make it real you need
ordinary CCTV clips from a car park / gate — **day and night**. Two sources:

- **Normal footage** (lots of it): any building's own recordings, or public
  CCTV datasets. This alone powers the anomaly half.
- **Incident footage** (a smaller amount): real break-ins / thefts, e.g. from
  the UCF-Crime dataset. This switches on the suspicion half.

Point the miner at a folder of clips and it cuts them into short pieces:

```
python -m training.clipmine --source local --dir /path/to/your/clips
```

### Step 3 — Turn footage into numbers

Run the production detector over the clips and write out the geometry features:

```
python -m training.extract --manifest training/data/manifest.jsonl
```

### Step 4 — Train the real brain

Same command as Step 1, but pointed at the real features instead of synthetic
motion:

```
python -m training.brain_train --features training/data/features.jsonl \
    --out models/brain.joblib
```

Now the printed number is a **real** measurement on footage the brain never
trained on — the headline is *false alarms per hour*, the thing this product
lives or dies by.

### Step 5 — Run the system

```
python -m app.main
```

On startup it logs `behaviour brain loaded: models/brain.joblib`. From then on,
every camera's candidates are refined by the brain: ordinary movement is
quietened, genuinely suspicious behaviour is corroborated and raised. Nothing
else about running the system changes.

---

## It improves itself — the self-improve loop

Once the system is running with a trained brain, it gets better on its own from
the ❌ taps guards and residents already make. One command does the whole cycle:

```
python -m training.self_improve
```

It pulls every "false alarm" verdict out of the database, adds them to the
training set as hard negatives, retrains the brain, and **deploys the new model
only if it did not get worse** on the held-out split (a wrong turn must never
reach the cameras unattended). The running system watches the model file and
loads the new brain within a minute — no restart. Put that one command on a
schedule (cron / Task Scheduler / a Routine, e.g. nightly) and the loop runs
itself: a guard taps ❌ today, every camera is a little better tomorrow.

Use `--dry-run` to see what it would do without deploying.

## The honest status

- **Built and proven on synthetic motion:** the whole chain — tracks → geometry
  → brain → calibrated score → the fusion gate — works today, with tests that
  fail if it ever stops working.
- **The one gap is real footage.** Every command above is ready; the brain is
  waiting for data, not for code. The moment you have clips, Steps 2-4 are the
  entire job.
- **A number you can quote to anyone comes only from real footage** held back
  and tested once (the "untouched holdout"). Any figure printed on `--synth` is
  a machine self-check, never a claim about the real world — and the tool
  labels it that way every time.

## Where each piece lives

| Piece | File |
|---|---|
| The brain (score + explain + save/load) | `app/brain.py` |
| Running it on a live camera stream | `app/brain_live.py` |
| Training it (one command) | `training/brain_train.py` |
| The geometry features it reads | `training/features.py` |
| Synthetic motion, for the self-check | `training/synth.py` |
| The safety gate it must not bypass | `app/fusion.py` |
| Turning it on / off, model path | `config.yaml` → `brain:` |
