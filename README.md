# Society AI Watchdog — Live Demo Prototype

Connects to a society's **existing CCTV system** (Hikvision / CP Plus / Dahua
DVR or NVR), watches the live streams with AI, and:

- detects & tracks **cars, people and number plates** in real time (YOLO11 nano + ByteTrack)
- detects **5 anomalies**: unauthorized vehicle entry, loitering near parked
  cars, possible vehicle contact ("touch-and-flee"), restricted-zone entry at
  night, and camera tamper/offline
- sends a **Telegram alert within seconds**, with a video clip (10s before +
  20s after the event)
- **records nothing else** — everything lives in a 60s in-memory rolling
  buffer; only anomaly clips touch the disk
- keeps a **tamper-evident audit log** (SHA-256 hash chain) of every clip
  saved, notification sent, clip deleted and registry change

All processing is local. Only Telegram messages/clips leave the machine.
Runs on a normal Windows or Linux PC, CPU-only (GPU used automatically if
present). No changes to the DVR — it keeps doing its own recording.

---

## 1. Install

Python 3.11+ required. On the PC/laptop on the same LAN as the DVR:

```bash
git clone <this repo> && cd <repo>
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Optional but recommended: install **ffmpeg** and put it on PATH (much more
robust RTSP handling; the app falls back to OpenCV without it).

## 2. Find your camera streams (RTSP)

Nearly all DVR/NVRs expose one RTSP URL per channel. Common patterns:

| Vendor | URL (channel 1) |
|---|---|
| Hikvision | `rtsp://user:pass@DVR_IP:554/Streaming/Channels/102` (`102` = ch1 substream, `101` = ch1 main, `202` = ch2 substream…) |
| CP Plus / Dahua | `rtsp://user:pass@DVR_IP:554/cam/realmonitor?channel=1&subtype=1` (`subtype=1` = substream, `0` = main) |

**Use the substream** — lower resolution is lighter and plenty for detection.

Don't know the DVR's IP or which pattern works? Let the helper find it:

```bash
# scan the whole LAN for devices with RTSP open, then try patterns:
python discover.py --network 192.168.1.0/24 --user admin --password YOURPASS

# or probe a known DVR IP directly:
python discover.py --host 192.168.1.108 --user admin --password YOURPASS --channels 4
```

It prints working URLs ready to paste into `config.yaml` under `cameras:`.

**No RTSP available?** Point `url:` at a video file instead (mp4/avi/dav
exported from the DVR software, or the DVR's recording folder if mounted).
Same pipeline, same detections, same clips. Set `loop_file: true` to loop it.

## 3. Create the Telegram bot

1. In Telegram, message **@BotFather** → `/newbot` → follow prompts → copy the
   **bot token**.
2. Create a group for the guards (and one for managers), add the bot to it.
3. Get the group chat id: add **@RawDataBot** to the group — it posts the chat
   id (a negative number like `-100123456789`) — then remove it.
4. In `config.yaml` set:

```yaml
telegram:
  enabled: true
  bot_token: "123456:ABC-your-token"
  chat_ids:
    guard: "-100123456789"
    manager: "-100987654321"
```

Owners of registered cars can also get direct alerts: put their personal chat
id in the registry (dashboard or `registry.csv`, column `telegram_chat_id`).

## 4. Register vehicles

Edit `registry.csv` (imported automatically on first start), or use the
dashboard's registry section to add/remove plates at runtime. Plates are
normalized (uppercase, no spaces/hyphens: `WB02AB1234`) and matched fuzzily
(1 character of OCR error tolerated).

## 5. Draw the zones

Each rule is anchored to a polygon you draw on the camera image:

```bash
python zones.py --camera gate
```

Left-click to add points, `n` to switch zone type (**entry** → **parking** →
**restricted**), `u` undo, `c` clear, **`s` to save into config.yaml**, `q` quit.

- `entry` — where vehicles enter (A1 unauthorized vehicle)
- `parking` — around parked cars (A2 loitering)
- `restricted` — no-go area at night (A4)

## 6. Run

```bash
python -m app.main --config config.yaml
```

Open the dashboard at **http://localhost:8000** — live annotated feeds,
recent events with clip playback, and registry management.

No cameras yet? Generate a synthetic test video and run against it:

```bash
python tests/make_sample_video.py     # writes tests/sample_gate.mp4
python -m app.main                    # default config points at it
```

## 7. Deploying on the DVR desktop + accessing it remotely

The best machine to run this on is **the existing desktop the cameras/DVR
already feed into** — it's on the right network and already runs 24/7.

**Recommended flow with a private GitHub repo:**

1. Keep this code in a **private** GitHub repository (free).
2. On the DVR desktop: install Python 3.11+ and Git, then
   `git clone https://github.com/<you>/<repo>.git` and follow section 1.
3. When you improve the code from anywhere, push to GitHub; on the desktop
   just run `git pull` to update. GitHub is your **code delivery + backup**
   channel — the software itself always runs locally on that desktop.

**Accessing the running dashboard from the internet** — do NOT port-forward
the dashboard on your router (that exposes your camera views to the whole
internet behind a single password). Two safe options:

- **Telegram only (simplest):** alerts + clips already reach your phone
  anywhere in the world. For most day-to-day use you never need the
  dashboard remotely.
- **Tailscale (recommended for full dashboard access):** install the free
  Tailscale app on the DVR desktop and on your phone/laptop. It creates a
  private encrypted network between your own devices — you open
  `http://<desktop-tailscale-ip>:8000` from anywhere, but nobody else on the
  internet can even see the port. No router changes, ~5 minutes to set up.

## 8. Verify the audit log

```bash
python verify_audit.py --db watchdog.db
```

Walks the SHA-256 hash chain and reports any edited or deleted rows. Clip
deletion is only possible from the dashboard and requires a name + reason;
the file is removed but the audit row is permanent.

---

## The five anomalies (all thresholds in `config.yaml → rules:`)

| # | Event | Trigger | Severity |
|---|---|---|---|
| A1 | Unauthorized vehicle | Vehicle in the **entry** zone with a plate not in the registry (after fuzzy match). If the plate can't be read within 5s: "Unidentified vehicle" (LOW). | HIGH |
| A2 | Loitering | Person stays in the **parking** zone > 45s (20s at night) with total movement < 120px. | MEDIUM |
| A3 | Possible vehicle contact | Two vehicle boxes overlap while one is moving, then one departs at speed (or vanishes) within 15s. Best-effort heuristic — expect false positives; tune with real footage. | MEDIUM |
| A4 | Restricted zone at night | Person inside the **restricted** zone during night hours (default 23:00–05:00). | HIGH |
| A5 | Camera tamper / offline | Frame near-black / near-white / extremely blurred for > 5s, or stream down > 30s. | HIGH |

Anti-spam: one event type per tracked object per 120s, and max 10
notifications/hour per camera (excess events are still logged and clipped,
just not sent).

## Number plates

- Plate region found with an optional dedicated YOLO model
  (`plates.detector_model` — e.g. a license-plate model from
  Roboflow/Ultralytics saved to `models/license_plate_yolo.pt`); without it,
  OCR runs on the lower half of the vehicle crop.
- OCR via **fast-plate-ocr** (default, light) or **easyocr** (fallback).
- Reads are throttled to ~1/s per tracked vehicle and stop once confident.
- Normalized to Indian format (`WB02AB1234`), common OCR confusions repaired
  (O↔0, B↔8, …), registry match tolerates Levenshtein distance ≤ 1.

## Test with a video (no cameras needed)

The dashboard has an **upload box**: pick any video file, optionally choose a
camera whose zones to reuse, click **Analyze**. The same detector + rules run
over the file in "video time" and the anomalies (with playable clips) appear
in a table. Tamper (A5) and vehicle-contact (A3) work with no zones; the
zone-based rules (A1/A2/A4) need zones, so either analyze against an existing
camera's zones or draw them first with `zones.py`.

This is the quickest way to try the system before any camera is wired up.

## Culprit tracking (green box)

When any anomaly fires, every person/vehicle involved is **flagged as a
culprit** and drawn with a bright-green box labelled `CULPRIT`, plus a green
motion trail, so a guard can follow and identify them across the frame. The
flag persists for `rules.flag_seconds` (default 60s) after the last anomaly,
then clears automatically. This shows in the live dashboard view **and** is
burned into the saved evidence clips (both live clips and upload-analysis
clips). Registered/normal objects keep their ordinary thin boxes.

## Tuning assistant (Claude chatbot)

When an alert is wrong — too sensitive, too slow, missing — you don't edit
YAML by hand. The dashboard has a **chat box powered by Claude**: type in plain
English ("the loitering alert fired too early, make it less sensitive") and the
assistant proposes a specific threshold change, shows you exactly what it will
change and why, and applies it on **Apply** — live, no restart for rule
thresholds. Every applied change is written to `config.yaml` and recorded in
the audit log.

Safety: the assistant can **only** touch tuning knobs (`rules`, `detection`,
`plates`, `clips`). It is structurally prevented — at both the suggestion and
the apply step — from editing your Telegram token, camera URLs, passwords or
any other secret.

Enable it in `config.yaml`:

```yaml
assistant:
  enabled: true
  model: "claude-opus-4-8"      # any Claude model id
  api_key_env: "ANTHROPIC_API_KEY"
```

and `export ANTHROPIC_API_KEY=sk-ant-...` before starting. Disabled by default
(so the prototype runs with zero cloud dependency); when off, the chat box
politely says it's not configured.

## Optional: AI incident descriptions (VLM)

Set `vlm.enabled: true` and export `ANTHROPIC_API_KEY` (also
`pip install anthropic`). On each anomaly, 6 keyframes from the clip are sent
to Claude (`claude-sonnet-4-6`) which returns a one-line incident description
appended to the Telegram alert. Fails silently if unavailable — this is the
**only** optional cloud feature and is off by default.

## Project layout

```
config.yaml          all settings (cameras, zones, thresholds, telegram, …)
registry.csv         seed registry (imported on first start)
discover.py          RTSP LAN scan + URL pattern probe
zones.py             polygon zone editor (writes back to config.yaml)
verify_audit.py      audit hash-chain verifier
app/
  main.py            entrypoint: camera workers + pipelines + dashboard
  camera.py          threaded reader, auto-reconnect, 60s rolling buffer
  detector.py        YOLO11n + ByteTrack wrapper
  plates.py          plate detect + OCR + normalize + fuzzy registry match
  rules.py           the 5 anomaly rules (pure Python, unit-tested)
  clips.py           pre/post-event clip extraction + sidecar JSON
  notify.py          Telegram alerts + clip upload + rate cap
  vlm.py             optional Claude keyframe description
  analyze.py         upload-a-video offline analysis (same detector + rules)
  assistant.py       Claude tuning chatbot (safe config-patch apply)
  db.py              SQLite + hash-chained audit log
  dashboard.py       FastAPI: MJPEG live view, events, registry, clip delete,
                     video upload/analyze, Claude tuning chat
clips/               anomaly clips only (created at runtime)
tests/               unit + integration tests, sample video generator
```

## Performance notes

- Nano model at 640px with frame skipping (default 6 inference FPS per
  camera) comfortably handles 2 cameras on a modern CPU; with CUDA it scales
  to 4+ automatically (`detection.device: auto`).
- Rolling buffers are JPEG-compressed and bounded (~30MB per camera at 60s);
  no unbounded growth over long runs.
- Every camera reader and pipeline is a supervised thread with exponential
  backoff — one bad camera never takes down the app.

## Security & privacy

- **All video stays on the LAN.** Frames are processed in memory on the local
  PC; only anomaly clips are written to disk, and only Telegram messages/
  clips ever leave the machine (that is the alert channel, over HTTPS).
- **Dashboard login:** HTTP Basic auth is on by default
  (`dashboard.auth` in `config.yaml`) — set a strong password before first
  run. Set `host: "127.0.0.1"` if only the DVR desktop itself needs the UI.
- **Telegram:** bot API calls go over TLS; only people in your guard/manager
  groups receive alerts. Keep the bot token secret (anyone with it can send
  as your bot).
- **Secrets:** the DVR password lives only in your local `config.yaml`, and
  `watchdog.db`, clips and model files are `.gitignore`d — so even in a
  private GitHub repo, no credentials or footage are ever committed.
  If you fork this publicly, strip `config.yaml` first.
- **Tamper-evident audit:** every clip save, alert, deletion and registry
  change is hash-chained; `verify_audit.py` proves nobody quietly edited or
  removed history.
- **Data minimization:** no continuous recording, no face recognition, a
  bounded 60s buffer, and clip deletion requires a name + reason that is
  permanently recorded.
- **Remote access:** prefer Tailscale/VPN over router port-forwarding (see
  section 7). Never expose port 8000 or the DVR's port 554 to the internet.

## Tests

```bash
pytest tests/ -q
```

Pure-Python tests (rules, plates, audit chain, dashboard API) run everywhere;
video/pipeline integration tests need OpenCV; the YOLO smoke test is skipped
unless `ultralytics` is installed.

## Demo checklist (acceptance test)

1. Registered car enters → **no alert** (silence is a feature)
2. Unregistered car enters → alert + plate + clip within 10s
3. Stand near parked cars ~1 min → loitering alert + clip
4. Cover a camera by hand → tamper alert after ~5s
5. Two toy cars nudge → "Possible vehicle contact" (best-effort)
6. Dashboard → delete one clip with name+reason → event row shows *deleted*,
   `python verify_audit.py` still passes, and the deletion is in the audit log
