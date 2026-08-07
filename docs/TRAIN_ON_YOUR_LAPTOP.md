# Train VisionGuard on your own laptop (Windows + RTX 4050)

Written for a beginner. You do not need to understand machine learning. Run one
command at a time, read what it prints, move to the next.

Your laptop's GPU does the heavy part (running the detector over real video).
That is why this takes minutes on your machine and hours on a plain server.

---

## One-time setup

**1. Install Python 3.11** from python.org. During install, tick **"Add Python
to PATH"**.

**2. Get the project and its dependencies.** Open **PowerShell** in the project
folder and run:

```
pip install -r requirements.txt
```

**3. Install the GPU version of PyTorch.** This is the step people miss — the
default install is CPU-only, which is slow. Install the CUDA build:

```
pip install --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cu121
```

**4. Check the GPU is actually seen:**

```
python -c "import torch; print('GPU:', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"
```

You want to see `GPU: True  NVIDIA GeForce RTX 4050 ...`. If it says `False`,
redo step 3 — everything below will be painfully slow until this says True.

---

## Step 1 — Train on real footage (one command)

This mines real CCTV (the public MEVA dataset), runs the detector on your GPU,
and trains the brain. It cleans up after itself — it never keeps more than a few
GB of video at a time.

```
python -m training.pilot_train
```

It prints each step as it runs. It will take a while the first time (it is
downloading and watching real video). When it finishes you will see a **test**
block with numbers, and `Done. The brain is trained on real footage`.

> Want to see exactly what it will do first, without running anything?
> `python -m training.pilot_train --dry-run`

**Read this number:** the line under `test (...)` that says
`false alarms/hour`. That is the real thing this product lives or dies by — how
often it would wake somebody for nothing on ordinary footage. Lower is better.

If it says the detector will run on **CPU (slow!)**, stop and fix step 3 above.

---

## Step 2 — Get a bigger, better number (optional but recommended)

More footage = a more trustworthy number. Mine more by raising the amount per
camera, and add a dedicated night pass (night is the hard case):

```
python -m training.pilot_train --sources 16 --night
```

`--night` matters: dark footage is where cheap cameras struggle, and the brain
only learns to handle it if it has seen it.

---

## Step 3 — Add real crime footage, so it also measures *catching* things

Everything so far measures **false alarms only** (MEVA is all ordinary
activity). To measure whether it **catches** a real break-in, you need real
incident clips. The standard free source is **UCF-Crime**:

1. Download UCF-Crime (search "UCF-Crime dataset download" — the University of
   Central Florida page). It is large; you only need the Burglary / Stealing /
   Robbery / Vandalism categories.
2. Put the clips in a folder, e.g. `D:\ucf_crime`.
3. Train with them included:

```
python -m training.pilot_train --ucf D:\ucf_crime --sources 16 --night
```

Now the `test` block will also show **recall** — of the real incidents, how many
it caught. You want recall high **and** false-alarms/hour low.

> UCF-Crime is published for research. Check its terms before any commercial use.

---

## Step 4 — Use the trained brain

`pilot_train` saves the brain to `models\brain.joblib`. The live system loads it
automatically:

```
python -m app.main
```

On startup it logs `behaviour brain loaded: models/brain.joblib`. From then on
every camera is judged by the model you just trained on real footage. And it
keeps getting better on its own — see the self-improve loop in
[THE_BRAIN.md](THE_BRAIN.md).

---

## When something goes wrong

* **"CPU (slow!)" / it's crawling** → the GPU PyTorch install didn't take. Redo
  setup step 3, confirm with step 4 of setup.
* **"CUDA out of memory"** → the detector is too big for 6 GB at this resolution.
  Lower it in `config.yaml`: `detection.imgsz: 960` (or `1024`). Still far better
  than the old nano default.
* **"No space left on device"** → lower how much video is kept at once:
  `python -m training.pilot_train --budget-gb 1.5 --sources 6`.
* **A step failed partway** → each step is an ordinary command (it printed the
  exact line). Fix the problem, run that one line by hand, then continue with
  `python -m training.pilot_train --skip-mine` to pick up from the split without
  re-downloading.
* **Very few "feature rows"** → the cameras you mined don't have people near
  cars. Try different ones: `--cameras G474,G475,G476,G505,G506,G508`. The
  cameras where people walk *through* a car park give the most to learn from.

---

## What "done" looks like

You are ready to talk about a pilot when, on footage the brain never trained on:

* **false alarms per hour is low** (ideally well under one per camera per hour),
* **recall is high** (it catches the real incidents), measured with `--ucf`,
* and both hold up **at night**, not just in daylight.

Those three numbers — not a general "accuracy" — are the honest measure of this
product. Everything to produce them is one command away.
