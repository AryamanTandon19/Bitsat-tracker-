# Running VisionGuard on a real CCTV system

Everything here is what a real test needs and nothing else. The goal is: plug
the box into the DVR's network, start it, and walk away.

## What you need

* A computer (a laptop or a small box) on the **same network as your DVR/NVR**.
* Python 3.11, and the dependencies: `pip install -r requirements.txt`.
* The DVR reachable over the LAN with RTSP enabled (it is, by default, on
  every Hikvision / CP Plus / Dahua unit).

## Start it

```
python -m app.main
```

That is the whole command. On the first run it:

1. **finds your cameras by itself.** It scans the local network, tries the
   factory-default DVR logins, and connects every channel that streams. If
   your DVR's password was changed at install, it will not guess it — open the
   console (below), go to **Cameras**, and add it once; it is remembered
   after that.
2. **prints a one-time admin password** to the console, like:
   ```
   ==============================================================
     operator app: first-run account created
         username: admin
         password: ozwgmujLNRyotVcX
     Shown once. Change it: python -m app.users passwd admin
   ==============================================================
   ```
   Write it down. You log in to the dashboard with it.
3. starts the dashboard on **http://THIS-MACHINE-IP:8000**.

## Watch it work

Open `http://<the machine's IP>:8000` in a browser and log in with the admin
account above.

* **View** — the live camera wall, with people and vehicles boxed in real time.
* **Cameras** — what is connected, the manual "add a camera" flow for a DVR
  whose password is not a default, and two things worth setting per camera:
  * **Where it looks** — "B-Block Main Gate, facing main road", so alerts name
    the place instead of the device.
  * **Zones** — draw the **entry**, **parking** and **restricted** areas right
    on the camera picture. This is the biggest single accuracy gain: "a vehicle
    stopped in the entry zone" is a far sharper alert than "a vehicle appeared".
    Changes apply immediately — no restart.
* **Events** — anomalies, with clips.

## The resident app (owners see their own alerts on their phone)

Residents get a small phone app (a web app they can "Add to Home Screen") that
shows **only their own** vehicle's alerts, the evidence clip for each, their
gate entry/exit history, and one tap to confirm **real** or **false alarm** —
the same tap that trains the system and, on a false alarm, deletes the clip.

To give a resident access, mint their personal link (it is scoped to their
registered plate and carries a secret, so hand it to them privately):

```
curl -X POST http://<ip>:8000/api/registry/<PLATE>/owner-link
```

The response contains a `link`. The resident opens it once on their phone; from
then on the app remembers them. They can install it to the home screen and it
opens like a native app. One resident can never see another's data — every
request is checked against the plate behind their link.

To check it is healthy without logging in, open
`http://<ip>:8000/health` — it lists the connected cameras and whether each is
online.

## What it does on its own, with no further input

* **Learns what is permanently there.** A pole, a bin, a reflection or a fire
  hydrant that the detector mistakes for a person will fire an alert at first;
  within a couple of minutes the system learns it is background and goes quiet
  about it — per camera, automatically. Nothing to label.
* **Sends one alert per incident, not one every few seconds.** A continuing
  situation is one notification, plus one more only if it gets worse.
* **Deletes old footage** past the retention window (default 14 days), and
  frees space if the disk gets tight, so it can run for the whole test
  unattended.

## The few things you may want to set

All in `config.yaml`, all optional:

* `telegram:` — turn on and add a bot token + chat ids to actually receive
  alerts on a phone. Off by default (alerts are logged and shown in the
  console regardless).
* `retention.clip_days` — how long to keep clips (default 14).
* `autoconnect.credentials` — your DVR's real username/password, if it is not a
  factory default, so auto-connect finds it without the manual step.
* `ANTHROPIC_API_KEY` in the environment — enables the paid second-opinion AI
  review. Off without it; everything else works.

## If a camera does not connect

`http://<ip>:8000/health` shows what connected. If your camera is missing:

* it is almost always the DVR password. Open **Cameras**, enter the DVR's
  address and its real login, and press **Find cameras**.
* the RTSP path is in the DVR's own web interface under **Network → RTSP** if
  the automatic patterns do not match your brand.
