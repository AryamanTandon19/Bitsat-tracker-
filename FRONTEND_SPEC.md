# VisionGuard / Society AI Watchdog — Front-End Specification

**Purpose of this document:** a complete, self-contained brief for a builder
(e.g. Emergent) to recreate and improve the front-end. It documents every
screen, component, data contract, state, and flow of the current dashboard, plus
guidance on what to improve. The back-end is a Python **FastAPI** server that
serves both the HTML page and JSON APIs; you can keep that back-end and rebuild
only the front-end against the documented API, or wrap it in a new SPA.

> Do not invent endpoints or fields. Everything below is the real contract. If
> you add features, add new endpoints; don't assume undocumented ones exist.

---

## 1. Product in one paragraph

VisionGuard is an on-premise AI security system for residential societies. It
watches CCTV, flags suspicious activity around vehicles/premises with a cheap
"free layer," and lets **Claude** review flagged clips and write a plain-English
verdict (theft / break-in / vandalism). The dashboard is the operator surface:
a live wall, a "Forensic Lab" to analyze uploaded footage, an event archive, a
vehicle registry, and an AI-spend meter. Primary users: **security guards,
society managers, admins**. Tone: serious, high-tech, trustworthy — a security
operations console, not a consumer app.

---

## 2. Design system (current — keep or refine)

**Aesthetic:** dark "glass" security console. Deep near-black background,
frosted translucent panels, neon cyan + purple accents, soft glows.

### Color tokens (CSS variables, current values)
```
--bg:        #050505   /* page background (near-black) */
--surface:   #101415   /* panel/glass base */
--text:      #e0e3e5   /* primary text */
--muted:     #b9cacb   /* secondary text */
--cyan:      #00f2ff   /* primary accent (actions, links, active nav) */
--cyan-dim:  #00dbe7   /* gradient partner */
--purple:    #6f00be   /* secondary accent */
--purple-lt: #ddb7ff   /* purple text/borders */
--red:       #ffb4ab   /* HIGH severity / threat */
--amber:     #ffd28a   /* MEDIUM severity */
--green:     #7ef0a2   /* OK / online */
```

### Typography
- Sans-serif UI (Inter / Segoe UI / system-ui). Monospace for plates & hashes
  (`ui-monospace, Menlo`).
- Headings bold (700–800), tight letter-spacing. Labels are small (10–11px),
  UPPERCASE, wide letter-spacing (`.12–.18em`), muted — a "HUD" feel.

### Core components (already defined; reuse the visual language)
- **Glass card** (`.glass`): translucent surface, 1px white-alpha border,
  16px radius.
- **Severity chip** (`.chip.HIGH/.MEDIUM/.LOW`, `.chip.ok/.bad`): pill with a
  glowing dot; red/amber/cyan by severity.
- **Threat banner**: red glass bar with a pulsing warning icon; used for the
  Claude verdict headline.
- **Incident card** (`.inc-card`): glass card with a severity-colored top border,
  "INCIDENT N" label + severity chip, plain-English headline, and a meta row
  (time span / culprit IDs / alert count). Hover lifts it. Click → seek video.
- **Stat card** (`.stat`): big number + label + sublabel, top accent line;
  cyan/purple/red variants. Used on the spend/live views.
- **Dropzone** (`.dropzone`): large dashed panel for drag-and-drop upload.
- **Buttons**: `.btn` (solid cyan), `.btn.grad` (cyan→purple gradient, primary
  CTA), `.btn.ghost` (subtle).
- **Toggle switch** (`.switch`): for "Smart AI Review" etc.
- **Tables** (`.tbl-card` + `.tblwrap`): glass header row, hover highlight,
  horizontal scroll on overflow. `a.jump` = cyan "jump to timestamp" links.

### Effects
- Soft box-shadow glows on accents; `@keyframes pulse` for live/threat dots.
- Everything must be **responsive** (grids collapse to 1 column on mobile) and
  render on both dark and light — but this product is **dark-first**; a light
  theme is optional.

---

## 3. Information architecture (5 views + top bar)

Left sidebar nav (icons + labels). One-page app; nav swaps `.view` sections.

| Nav item | Icon | View id | Purpose |
|----------|------|---------|---------|
| Live Monitoring | 📡 | `view-live` | Live camera wall (MJPEG streams) + status |
| Forensic Lab | 🔬 | `view-lab` | Upload a video → detect → Claude verdict (**demo centerpiece**) |
| Events | 🔔 | `view-events` | Archive of all anomalies + clips + AI verdicts |
| Vehicles | 🚗 | `view-vehicles` | Vehicle registry (plate → owner) |
| AI & Spend | 🧠 | `view-spend` | AI cost meter + config |

**Top bar:** brand mark (left), and a live spend chip `₹ <amount> / 24h` (right)
that links to the spend view. Add a login/user indicator if you build auth UI.

---

## 4. Page-by-page specification

### 4.1 Live Monitoring (`view-live`)
- **Purpose:** wall of live annotated camera feeds.
- **Data:** one `<img src="/stream/{camera}">` per camera (MJPEG multipart
  stream — just point an `<img>` at it). Camera list from `GET /api/cameras`.
  Per-camera health from `GET /api/status`.
- **Components:** grid of camera tiles; each tile shows the stream, camera name,
  and an online/offline chip (green = online, red = offline) with
  `last_frame_age_s`. A page-level "N cameras online" stat.
- **States:** offline camera → show a placeholder + red chip. No cameras → empty
  state ("No cameras configured").
- **Refresh:** stream is continuous; poll `GET /api/status` every ~5s for chips.

### 4.2 Forensic Lab (`view-lab`) — **the demo centerpiece**
Flow: **upload → analyze → live-poll → results**.

- **Upload:** a large `.dropzone` (drag-drop or click to browse). Accept
  `mp4/avi/mov/mkv/dav`. Below it, controls:
  - **Smart AI Review** toggle (`.switch`) — when on, Claude reviews the clip.
  - **Zones from** `<select>` — optionally reuse a configured camera's zones
    (options from `GET /api/cameras`).
  - **Analyze** button (`.btn.grad`) → `POST /api/analyze`.
  - A status line for progress text.
- **On submit:** `POST /api/analyze` returns `{ job_id }`. Then **poll**
  `GET /api/analyze/{job_id}` (~every 1s) until `status` is `done` or `error`,
  updating a progress bar/percentage.
- **Results (in priority order, top → bottom):**
  1. **Claude verdict banner** (threat banner) — `ai_verdict` string
     ("HIGH at 60s — car driven away … likely theft"). Show only if present.
  2. **Incidents detected** — grid of **incident cards** from `incidents[]`
     (the headline of the analysis). One theft = one card. Click a card →
     `seekTo(start_s)` on the video.
  3. **Annotated video player** — `GET /api/analyze/{job_id}/video`
     (H.264 mp4). This is the brightened, boxed, overlay-burned playback.
     `seekTo(t)` sets `player.currentTime = t` and plays.
  4. **AI Scene Review table** — `ai_findings[]` rows
     `{time_s, activity, severity}` with jump links; show `ai_note` when empty.
  5. **Rule-based checks table** — raw `events[]` (free-layer detail), with
     severity chip, time (jump), type, plate, description.
- **States:** queued / running (progress) / encoding / done / error (`error`
  string). If AI review requested but no API key: `ai_note` explains it.
- **Empty/first-run:** "No analysis yet — upload a video above."

### 4.3 Events (`view-events`)
- **Purpose:** archive of every anomaly across all cameras.
- **Data:** `GET /api/events?limit=100` → rows (see §5 data model).
- **Table columns:** Severity chip · Incident # · Time · Camera · Type · Plate ·
  Description · **AI says** (`ai_summary`) · Clip.
- **Clip cell:** if `clip_id` and not `clip_deleted`, a link to
  `/clips/{clip_id}` (plays inline / downloads). Plus a **Delete** action that
  opens a modal requiring **name + reason** → `POST /api/clips/{clip_id}/delete`
  (this is audited — surface that in the modal copy).
- **Refresh:** poll every ~5–10s or on demand.

### 4.4 Vehicles (`view-vehicles`)
- **Purpose:** registry mapping plate → owner (for "unauthorized vehicle" logic
  and alert routing).
- **Data:** `GET /api/registry` → vehicles.
- **Table:** Plate (mono) · Owner name · Phone · Flat · Telegram chat id · remove.
- **Add form:** plate (required) + owner_name/owner_phone/flat_number/
  telegram_chat_id → `POST /api/registry` (form-encoded).
- **Remove:** `DELETE /api/registry/{plate}` (audited).
- **Validation:** invalid plate → 400; show inline error.

### 4.5 AI & Spend (`view-spend`)
- **Purpose:** show exactly what the AI layer costs — the transparency selling
  point.
- **Data:** `GET /api/costs`.
- **Components:** stat cards for **last 24h** and **last 30d**
  (`calls`, `cost_inr`, tokens), a per-camera 30-day breakdown table
  (`per_camera_30d`), and an "AI review enabled" indicator
  (`ai_review_enabled`).
- Optionally: the **Claude tuning chatbot** (`POST /api/assistant`,
  `POST /api/assistant/apply`) — a chat box where an admin types plain-English
  tuning requests and Claude proposes a config patch to apply.

---

## 5. Data models (exact shapes the front-end consumes)

### Analyze job — `GET /api/analyze/{job_id}` → `job.public()`
```jsonc
{
  "id": "a1b2c3d4e5f6",
  "filename": "gate_night.mp4",
  "status": "queued|running|encoding|done|error",
  "progress": 0.0,                 // 0..1
  "message": "1 incident(s), 3 alerts, 2 AI findings",
  "events": [ /* Event-lite (see below) */ ],
  "incidents": [                   // merged incidents — the headline
    {
      "index": 0,
      "start_s": 11.0,
      "end_s": 60.0,
      "severity": "HIGH|MEDIUM|LOW",
      "event_type": "suspicious_activity",
      "track_ids": [7],            // culprit IDs
      "count": 4,                  // alerts merged into this incident
      "summary": "POSSIBLE VEHICLE THEFT: vehicle drove away 48s after activity"
    }
  ],
  "ai_findings": [                 // Claude's per-moment findings
    { "time_s": 60.0, "activity": "car driven away — likely theft", "severity": "HIGH" }
  ],
  "ai_verdict": "HIGH at 60s — car driven away … likely theft",  // headline, may be ""
  "ai_note": "",                   // message when AI review unavailable/empty
  "error": null,
  "video_ready": true              // annotated video available at .../video
}
```

### Event-lite (inside a job's `events[]`)
```jsonc
{ "index": 0, "event_type": "suspicious_activity", "severity": "MEDIUM",
  "video_time_s": 12.3, "plate": null, "track_ids": [7],
  "confidence": 0.5, "description": "Suspicious activity: person at vehicle" }
```

### Archive event — `GET /api/events` (per row)
```jsonc
{ "id": 42, "ts": 1737600000.0, "camera": "gate", "event_type": "suspicious_activity",
  "severity": "HIGH", "plate": "DL3CAB1234", "track_ids": "[7]", "confidence": 0.8,
  "description": "...", "suppressed": 0,
  "clip_id": 5, "clip_path": "...", "clip_deleted": 0,
  "ai_summary": "car driven away — likely theft" }   // newest AI-review summary, may be null
```
> `ts` is a Unix epoch (seconds, float) — format client-side. `track_ids` here is
> a JSON **string** (archive) but an **array** in job events — handle both.

### Camera status — `GET /api/status`
```jsonc
{ "gate": { "online": true, "last_frame_age_s": 0.3 } }
```

### Vehicle — `GET /api/registry` (per row)
```jsonc
{ "id": 1, "plate_number": "DL3CAB1234", "owner_name": "…", "owner_phone": "…",
  "flat_number": "B-402", "telegram_chat_id": "…", "created_at": 1737600000.0 }
```

### Cost summary — `GET /api/costs`
```jsonc
{ "last_24h": { "calls": 12, "cost_usd": 0.34, "input_tokens": 40000,
                "output_tokens": 3000, "cost_inr": 30.6 },
  "last_30d": { "calls": 300, "cost_usd": 8.1, "input_tokens": 1000000,
                "output_tokens": 80000, "cost_inr": 729.0 },
  "per_camera_30d": [ { "camera": "gate", "calls": 120, "cost_usd": 3.2, "cost_inr": 288.0 } ],
  "ai_review_enabled": true }
```

### Audit row — `GET /api/audit`
```jsonc
{ "id": 10, "ts": 1737600000.0, "actor": "dashboard", "action": "DELETE_CLIP",
  "details_json": "{…}", "prev_hash": "…", "row_hash": "…" }
```
> The audit log is a **hash chain** (each row hashes the previous). If you build
> an audit view, show it as tamper-evident and allow a "verify chain" action.

---

## 6. Complete API reference

Base: same origin as the page. Auth: **HTTP Basic** (browser prompts;
`dashboard.auth` in server config). All JSON unless noted.

| Method | Path | Body / params | Returns |
|--------|------|---------------|---------|
| GET | `/` | — | The HTML dashboard page |
| GET | `/stream/{camera}` | — | `multipart/x-mixed-replace` MJPEG stream (use in `<img>`) |
| GET | `/api/cameras` | — | `["gate", …]` |
| GET | `/api/status` | — | `{ camera: { online, last_frame_age_s } }` |
| GET | `/api/events` | `?limit=100` | array of archive events (§5) |
| POST | `/api/analyze` | multipart: `file`, `zones_from`, `ai_review` | `{ job_id }` |
| GET | `/api/analyze/{job_id}` | — | job (§5) — **poll this** |
| GET | `/api/analyze/{job_id}/video` | — | annotated mp4 (H.264) |
| GET | `/api/registry` | — | array of vehicles (§5) |
| POST | `/api/registry` | form: `plate`(req), `owner_name`, `owner_phone`, `flat_number`, `telegram_chat_id` | `{ ok, plate }` |
| DELETE | `/api/registry/{plate}` | — | `{ ok }` |
| GET | `/clips/{clip_id}` | — | clip mp4 (404 if deleted/missing) |
| POST | `/api/clips/{clip_id}/delete` | form: `name`(req), `reason`(req) | `{ ok }` (audited) |
| GET | `/api/costs` | — | cost summary (§5) |
| GET | `/api/audit` | `?limit=200` | array of audit rows (§5) |
| POST | `/api/assistant` | json: `{ message, history }` | `{ reply, patch, explanation, rejected }` |
| POST | `/api/assistant/apply` | json: `{ patch }` | apply result |

**Error convention:** non-2xx with `{"detail": "..."}` (FastAPI). Common:
400 (bad input), 404 (not found), 413 (file too large), 503 (feature disabled).

---

## 7. Key flows

### 7.1 Analyze a video (the demo)
```
[Dropzone] --file--> POST /api/analyze {file, ai_review, zones_from}
      -> { job_id }
      -> poll GET /api/analyze/{job_id} every 1s
           status: queued -> running (progress %) -> encoding -> done
      -> render: verdict banner (ai_verdict)
                 incident cards (incidents[])
                 <video src=".../video">   (click card => seekTo)
                 AI findings table (ai_findings[])
                 rule-based table (events[])
```
Empty/negative result is a valid outcome: show `ai_note` ("nothing clearly
suspicious") — do **not** fabricate a threat.

### 7.2 Delete a clip (audited)
```
[Delete on event row] -> modal requires NAME + REASON
   -> POST /api/clips/{id}/delete {name, reason}
   -> success => row shows "deleted"; action is written to the audit chain
```
Copy must state that deletion is logged and irreversible.

### 7.3 Registry CRUD, 7.4 Assistant chat
Standard: add/remove vehicles; chat posts a message + prior history, gets a
`reply` plus a proposed config `patch` the admin can review and `apply`.

---

## 8. Behavior, real-time, and non-negotiables

- **Polling:** analyze job ~1s; status/events ~5–10s. MJPEG is push (just an
  `<img>`). No websockets today (you may add them).
- **Severity → color:** HIGH=red, MEDIUM=amber, LOW/OK=cyan/green. Consistent
  everywhere (chips, card borders, banners).
- **Privacy (must respect):** raw CCTV never leaves the box; only anomaly clips +
  metadata + selected keyframes do. Don't add features that upload continuous
  footage to third parties.
- **Explainability (must keep):** always show *why* — the evidence, timestamps,
  what Claude concluded, and let the user jump to the moment. Never present a
  verdict without the evidence behind it.
- **Human authority:** the UI presents review states/verdicts, never automated
  accusations or identity claims. Avoid naming/profiling individuals.
- **Honesty:** development metrics are not production accuracy; if you add a
  "stats" surface, label sources accordingly.

---

## 9. What to improve (brief for the builder)

The current UI is a functional single-page app with an established dark-glass
language. Priorities for a **better** front-end, in order:

1. **Make the Forensic Lab flow cinematic** — the upload→verdict→incident-card
   moment is the product's "wow." Smooth progress, a confident verdict reveal,
   incident cards that animate in, click-to-seek that feels instant.
2. **Incident-first Events view** — group archive events into incidents (the API
   already merges per-job; extend to the archive), each expandable to its clip +
   Claude verdict + timeline.
3. **A real video experience** — timeline markers on the player at each
   incident/finding time; scrub-to-evidence; side-by-side raw vs. annotated.
4. **Live wall polish** — responsive multi-camera grid, per-camera health,
   click-to-expand, recent-alert overlay per tile.
5. **Mobile** — guards use phones; make alerts + clip playback first-class on
   small screens (or a companion PWA).
6. **Auth & roles UI** — login, and role-scoped views (guard sees their
   building's cameras/alerts; manager/admin see more). The back-end auth is
   currently HTTP Basic; a proper session/JWT login is a planned upgrade.
7. **Accessibility & theming** — keyboard nav, focus states, ARIA on
   chips/banners; optional light theme.

Keep the color tokens and component vocabulary in §2 unless you deliberately
rebrand — consistency is part of the "trustworthy security console" feel.

---

## 10. Tech notes for the builder

- Back-end is **FastAPI** (Python) serving one HTML page + the JSON APIs above.
  You can (a) rebuild the front-end as a modern SPA (React/Vite/Tailwind or
  similar) that calls these same endpoints, or (b) restyle the existing inline
  page. Prefer (a) for a "better" front-end.
- No build step exists today; the current page is a single inline HTML/CSS/JS
  string. A real SPA with a component library is the recommended upgrade.
- CORS/same-origin: the SPA should be served from the same origin (or configure
  CORS on the FastAPI app) and pass through HTTP Basic auth.
- All media (MJPEG stream, clips, annotated video) are plain HTTP GETs returning
  image/video — no special player SDK required.

---

**Deliverable expectation for Emergent:** a responsive, dark-first security
console implementing the 5 views in §3–4 against the exact API in §6, with the
Forensic Lab flow (§7.1) as the hero experience, honoring the non-negotiables in
§8. Reuse the design tokens in §2 unless rebranding.
