"""The labelling workbench — where human judgement enters the system.

Every threshold in VisionGuard is tuned against someone's opinion of what was
actually happening in a piece of footage. Until now that opinion lived in a CSV
you had to edit by hand, which meant it did not exist. This page is that CSV,
with the video next to it.

Two kinds of mark, and they do different jobs. Be clear about which:

  **Timeline flags** — "from 0:12 to 0:41 a stranger was trying door handles;
  this is an incident". These are the labels the tuner uses directly. Mark
  enough of them and `validate_triggers.py` and `evaluate_alerts.py` stop
  reporting hypotheses and start reporting measurements. This is the one that
  matters most.

  **Object boxes** — drawing round a person or a car on a paused frame. This
  does *not* teach the detector what a car looks like; YOLO already knows, and
  changing its mind would take tens of thousands of boxes and a GPU. What it
  does is measure whether it sees them *on your camera* — your height, your
  lens, your light. If a site's boxes show the detector missing half the people
  at night, that is an argument for a bigger model or better lighting, and it
  is the kind of argument you can only make with numbers.

Served at /train, behind the registry permission: labelling changes what the
whole system believes, so it is not a guard-level action.
"""
from __future__ import annotations

import csv
import io

CLASSES = ("person", "car", "motorcycle", "truck", "bus", "other")

# What people are usually marking. Free text is still allowed — a site will
# have its own vocabulary and forcing it into ours would lose information.
SUGGESTED = (
    "trying door handles", "loitering by a vehicle", "breaking a window",
    "taking something from a vehicle", "climbing a wall", "tampering with camera",
    "resident parking", "resident leaving", "delivery", "visitor arriving",
    "children playing", "guard patrol", "stray animal", "nothing happening",
)


def to_labels_csv(db) -> str:
    """Turn the marks into the labels.csv the harnesses already read.

    One row per clip, because that is the unit the harnesses score. A clip
    with any incident mark is an incident, and its window spans the earliest
    start to the latest end — the harness asks "did the trigger fire during
    the incident", so a window that covers all of them is the honest input.
    """
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["filename", "type", "incident", "start_s", "end_s", "notes"])

    marks_by_clip: dict[int, list[dict]] = {}
    for m in db.training_marks():
        marks_by_clip.setdefault(m["clip_id"], []).append(m)

    for clip in sorted(db.list_training_clips(), key=lambda c: c["id"]):
        marks = marks_by_clip.get(clip["id"], [])
        incidents = [m for m in marks if m["verdict"] == "incident"]
        if incidents:
            start = min(m["start_s"] for m in incidents)
            end = max(m["end_s"] for m in incidents)
            kind = incidents[0]["label"] or "incident"
            notes = "; ".join(dict.fromkeys(m["label"] for m in incidents if m["label"]))
        elif marks:
            start = end = ""
            kind = "normal"
            notes = "; ".join(dict.fromkeys(m["label"] for m in marks if m["label"]))
        else:
            # Unmarked clips are deliberately still listed, with a note saying
            # so. Silently dropping them would make the test set look more
            # complete than it is.
            start = end = ""
            kind = "unmarked"
            notes = "NOT YET LABELLED — no mark has been made on this clip"
        w.writerow([clip["filename"], kind,
                    "1" if incidents else "0", start, end,
                    (clip["source"] + " — " if clip["source"] else "") + notes])
    return out.getvalue()


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#08050f">
<title>VisionGuard — Train</title>
<link rel="icon" href="/operator/icon-192.png">
<style>
@font-face{font-family:"Manrope";font-weight:200 800;font-display:swap;
  src:url(/operator/font.woff2) format("woff2")}
:root{
  --night:#08050f; --glass:rgba(255,255,255,.045); --glass-2:rgba(255,255,255,.08);
  --edge:rgba(255,255,255,.085); --edge-lit:rgba(255,255,255,.19);
  --text:#f4f1ff; --soft:#c8bfe8; --muted:#8d84b0;
  --metal:linear-gradient(145deg,#dcd2ff 0%,#ab8dff 17%,#7f56f0 42%,
                          #5a2fd0 64%,#8b63f5 86%,#c3aeff 100%);
  --incident:#ff6183; --normal:#4ade9e; --accent:#8b63f5;
  --lift:0 18px 44px -20px rgba(88,44,208,.95), inset 0 1px 0 var(--edge-lit);
  --r:20px;
}
*{box-sizing:border-box}
body{margin:0;background:var(--night);color:var(--text);
  font-family:"Manrope",-apple-system,system-ui,sans-serif;font-size:15px;
  line-height:1.5;letter-spacing:-.012em;-webkit-font-smoothing:antialiased}
.aurora{position:fixed;inset:0;z-index:-1;background:var(--night);overflow:hidden}
.aurora span{position:absolute;border-radius:50%;filter:blur(80px);opacity:.26}
.aurora span:nth-child(1){width:60vw;height:60vw;top:-20vw;left:-14vw;
  background:radial-gradient(circle,#5f2fc4,transparent 68%)}
.aurora span:nth-child(2){width:50vw;height:50vw;bottom:-16vh;right:-14vw;
  background:radial-gradient(circle,#33208a,transparent 70%)}
:focus-visible{outline:2px solid #b49bff;outline-offset:2px;border-radius:8px}

header{display:flex;align-items:center;justify-content:space-between;gap:16px;
  padding:18px 22px;border-bottom:1px solid var(--edge);flex-wrap:wrap}
.wordmark{font-size:15px;font-weight:650}
.wordmark span{background:var(--metal);-webkit-background-clip:text;
  background-clip:text;-webkit-text-fill-color:transparent;font-weight:700}
.who{font-size:13px;color:var(--muted)}
main{max-width:1180px;margin:0 auto;padding:22px;display:grid;
  grid-template-columns:300px 1fr;gap:22px;align-items:start}
@media (max-width:900px){main{grid-template-columns:1fr}}

h1{font-size:26px;font-weight:700;letter-spacing:-.03em;margin:0 0 6px}
h1 em{font-style:normal;background:var(--metal);-webkit-background-clip:text;
  background-clip:text;-webkit-text-fill-color:transparent}
.lede{color:var(--soft);margin:0 0 18px;max-width:62ch}
h2{font-size:11px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;
  color:var(--muted);margin:0 0 12px}
.card{background:var(--glass);border:1px solid var(--edge);border-radius:var(--r);
  padding:16px;box-shadow:var(--lift);margin-bottom:16px}

/* clip list */
.clip{display:flex;justify-content:space-between;align-items:center;gap:10px;
  padding:10px 12px;border-radius:12px;cursor:pointer;border:1px solid transparent}
.clip:hover{background:var(--glass-2)}
.clip.on{background:var(--glass-2);border-color:rgba(139,99,245,.5)}
.clip b{font-weight:600;font-size:13.5px;word-break:break-all}
.clip small{color:var(--muted);font-size:11.5px;display:block;margin-top:2px}
.pip{flex:none;font-size:10px;font-weight:750;letter-spacing:.06em;
  text-transform:uppercase;padding:3px 8px;border-radius:999px;
  border:1px solid currentColor}
.pip.i{color:var(--incident)} .pip.n{color:var(--normal)} .pip.u{color:var(--muted)}

/* video + timeline */
.stage{position:relative;background:#000;border-radius:var(--r);overflow:hidden}
video{display:block;width:100%;background:#000}
canvas.overlay{position:absolute;inset:0;width:100%;height:100%;cursor:crosshair}
.timeline{position:relative;height:46px;margin-top:12px;border-radius:12px;
  background:rgba(255,255,255,.05);border:1px solid var(--edge);overflow:hidden;
  cursor:pointer}
.tl-mark{position:absolute;top:0;bottom:0;opacity:.8}
.tl-mark.incident{background:linear-gradient(rgba(255,97,131,.75),rgba(255,97,131,.35))}
.tl-mark.normal{background:linear-gradient(rgba(74,222,158,.55),rgba(74,222,158,.22))}
.tl-sel{position:absolute;top:0;bottom:0;background:rgba(139,99,245,.35);
  border-left:2px solid var(--accent);border-right:2px solid var(--accent)}
.tl-head{position:absolute;top:0;bottom:0;width:2px;background:#fff;
  box-shadow:0 0 8px #fff}
.tl-legend{display:flex;gap:16px;margin-top:8px;font-size:12px;color:var(--muted);
  flex-wrap:wrap}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px}

label{display:block;font-size:10.5px;font-weight:700;letter-spacing:.11em;
  text-transform:uppercase;color:var(--muted);margin:14px 0 7px}
input,select,textarea{width:100%;font:inherit;color:var(--text);appearance:none;
  background:rgba(255,255,255,.05);border:1px solid var(--edge);
  border-radius:12px;padding:11px 13px}
.row{display:flex;gap:10px;flex-wrap:wrap}
.row>*{flex:1;min-width:120px}
button{font:inherit;cursor:pointer;border-radius:12px;padding:11px 16px;
  font-weight:640;border:1px solid var(--edge);background:rgba(255,255,255,.07);
  color:var(--text);transition:transform .12s}
button:active{transform:scale(.97)}
button.primary{border:0;color:#fff;background:var(--metal);box-shadow:var(--lift)}
button.incident{border-color:rgba(255,97,131,.5);color:#ff8ba3}
button.normal{border-color:rgba(74,222,158,.45);color:#7ceab8}
button.ghost{background:none}
.marks{margin-top:6px}
.mark{display:flex;justify-content:space-between;align-items:center;gap:10px;
  padding:9px 12px;border-radius:12px;background:rgba(255,255,255,.04);
  border:1px solid var(--edge);margin-bottom:7px;font-size:13.5px}
.mark .t{color:var(--muted);font-variant-numeric:tabular-nums;font-size:12.5px}
.mark button{padding:4px 9px;font-size:12px}
.empty{color:var(--muted);text-align:center;padding:26px;font-size:14px}
.tick{display:flex;align-items:center;gap:7px;font-size:12.5px;font-weight:600;
  letter-spacing:0;text-transform:none;color:var(--soft);margin:0;
  background:rgba(255,255,255,.05);border:1px solid var(--edge);
  border-radius:12px;padding:10px 13px;cursor:pointer}
/* the global input rule sets appearance:none for the text fields, which also
   erases the tick — these were rendering as invisible dots */
.tick input{width:auto;padding:0;margin:0;appearance:auto;-webkit-appearance:auto;
  accent-color:#8b63f5;border:0;background:none;border-radius:0}
button.on{border-color:rgba(139,99,245,.7);color:#c3aeff;
  background:rgba(139,99,245,.16)}
.stage.selecting canvas.overlay{cursor:pointer}
.hint{color:var(--muted);font-size:13px;margin:10px 0 0}
.warn{background:linear-gradient(150deg,rgba(255,180,92,.14),rgba(255,180,92,.04));
  border:1px solid rgba(255,180,92,.3);border-radius:14px;padding:13px 15px;
  color:var(--soft);font-size:13.5px;margin-top:14px}
.toast{position:fixed;left:50%;transform:translateX(-50%) translateY(20px);
  bottom:26px;background:rgba(20,13,36,.95);border:1px solid var(--edge-lit);
  border-radius:14px;padding:13px 20px;box-shadow:var(--lift);z-index:50;
  opacity:0;visibility:hidden;transition:opacity .2s,transform .3s,visibility 0s .3s}
.toast.on{opacity:1;visibility:visible;transform:translateX(-50%);transition-delay:0s}
.gate{position:fixed;inset:0;display:none;align-items:center;justify-content:center;
  background:rgba(8,5,15,.85);z-index:60;padding:24px}
.gate.on{display:flex}
.gate .card{max-width:360px;width:100%;text-align:center}
</style>
</head>
<body>
<div class="aurora" aria-hidden="true"><span></span><span></span></div>

<header>
  <div class="wordmark">Vision<span>Guard</span> · Train</div>
  <div class="who" id="who"></div>
</header>

<main>
  <section>
    <div class="card">
      <h2>Clips</h2>
      <div id="clips"></div>
      <label for="up">Add footage</label>
      <input type="file" id="up" accept="video/*">
      <input id="source" placeholder="where it came from (optional)"
             style="margin-top:8px">
      <button class="primary" id="upload" style="width:100%;margin-top:10px">
        Upload</button>
      <p class="hint" id="upstat"></p>
    </div>
    <div class="card">
      <h2>Export</h2>
      <p class="hint" style="margin:0 0 10px">What you mark here becomes
        <code>labels.csv</code> — the file the measurement harnesses read.</p>
      <button id="export" style="width:100%">Download labels.csv</button>
    </div>
  </section>

  <section>
    <h1>Teach it what <em>actually happened</em></h1>
    <p class="lede">Play a clip, drag across the timeline to select a stretch,
      then say what was happening and whether it was a real incident. Those
      marks are what every threshold in the system gets tuned against.</p>

    <div class="card" id="workbench" hidden>
      <div class="stage">
        <video id="vid" controls preload="metadata"></video>
        <canvas class="overlay" id="ov"></canvas>
      </div>

      <div class="timeline" id="tl">
        <div class="tl-head" id="head" style="left:0"></div>
      </div>
      <div class="tl-legend">
        <span><i class="dot" style="background:#ff6183"></i>incident</span>
        <span><i class="dot" style="background:#4ade9e"></i>normal</span>
        <span id="selinfo">drag on the bar to select a stretch</span>
      </div>

      <label for="what">What is happening</label>
      <input id="what" list="suggested" placeholder="e.g. trying door handles">
      <datalist id="suggested"></datalist>
      <label for="note">Note (optional)</label>
      <input id="note" placeholder="anything the number will not capture">
      <div class="row" style="margin-top:16px">
        <button class="incident" id="mark-i">Mark as incident</button>
        <button class="normal" id="mark-n">Mark as normal</button>
      </div>

      <h2 style="margin-top:26px">Marks on this clip</h2>
      <div class="marks" id="marks"></div>

      <h2 style="margin-top:26px">Objects on this frame</h2>
      <p class="hint" style="margin:0 0 10px">Pause, then drag a box round a
        person or a vehicle. This does <b>not</b> teach the detector what a car
        looks like — it already knows. It measures whether it sees them on
        <b>this</b> camera, which is how you find out a site needs a bigger
        model or better lighting.</p>
      <div class="row">
        <button class="primary" id="seg">Find objects on this frame</button>
        <button id="selmode" class="ghost">Select Object: off</button>
      </div>
      <div class="row" style="margin-top:10px">
        <label class="tick"><input type="checkbox" id="t-mask" checked> Masks</label>
        <label class="tick"><input type="checkbox" id="t-box"> Boxes</label>
        <label class="tick"><input type="checkbox" id="t-label" checked> Labels</label>
        <button id="clearsel" class="ghost">Clear selection</button>
      </div>
      <p class="hint" id="segstat"></p>
      <div class="row" style="margin-top:12px">
        <select id="cls"></select>
        <button id="boxes-clear" class="ghost">Clear boxes on this frame</button>
      </div>
      <div class="marks" id="boxes"></div>
    </div>

    <div class="card empty" id="nothing">
      Select a clip on the left, or upload one.
      <div class="warn" style="text-align:left">
        No footage of your own yet? Run
        <code>python fetch_testset.py --camera G424</code> — it pulls real
        car-park CCTV (MEVA, CC BY-4.0) into this list. It is a stand-in for
        your site, not a substitute for it.
      </div>
    </div>
  </section>
</main>

<div class="gate" id="gate"><div class="card">
  <h2>Sign in</h2>
  <p class="hint">Labelling changes what the whole system believes, so it needs
    an account with registry permission.</p>
  <label for="u">Username</label><input id="u" autocapitalize="none">
  <label for="pw">Password</label><input id="pw" type="password">
  <button class="primary" id="signin" style="width:100%;margin-top:16px">
    Sign in</button>
  <p class="hint" id="gerr" style="color:#ff8ba3"></p>
</div></div>

<div class="toast" id="toast"></div>

<script>
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt = t => { t = Math.max(0, t||0);
  return `${Math.floor(t/60)}:${String(Math.floor(t%60)).padStart(2,"0")}`; };

let me = null, clips = [], current = null, marks = [], boxes = [];
let sel = null;                       // {start, end} in seconds

function toast(msg){ const t = $("#toast"); t.textContent = msg;
  t.classList.add("on"); clearTimeout(t._x);
  t._x = setTimeout(() => t.classList.remove("on"), 2600); }

async function api(url, opts){
  const r = await fetch(url, {cache:"no-store", ...opts});
  if(r.status === 401 || r.status === 403){ $("#gate").classList.add("on");
    throw new Error("not allowed"); }
  if(!r.ok) throw new Error((await r.json().catch(()=>({}))).detail || "failed");
  return r.headers.get("content-type","").includes("json") ? r.json() : r;
}
const form = (url, data, method) => api(url,
  {method: method || "POST", body: new URLSearchParams(data)});

/* ---------------------------------------------------------------- auth */
$("#signin").onclick = async () => {
  $("#gerr").textContent = "";
  try {
    me = await form("/api/login", {username:$("#u").value.trim(),
                                   password:$("#pw").value});
    $("#gate").classList.remove("on");
    boot();
  } catch(e){ $("#gerr").textContent = "Wrong username or password."; }
};

/* --------------------------------------------------------------- clips */
async function loadClips(){
  clips = await api("/api/train/clips");
  $("#clips").innerHTML = clips.length ? clips.map(c => {
    const cls = c.incidents ? "i" : c.marks ? "n" : "u";
    const tag = c.incidents ? "incident" : c.marks ? "marked" : "unmarked";
    return `<div class="clip ${current && current.id===c.id ? "on":""}"
                 data-id="${c.id}">
      <div><b>${esc(c.filename)}</b>
        <small>${c.duration_s ? fmt(c.duration_s) : "?"} ·
          ${c.marks} mark${c.marks===1?"":"s"} · ${c.boxes} box${c.boxes===1?"":"es"}</small>
      </div><span class="pip ${cls}">${tag}</span></div>`;
  }).join("") : '<p class="empty">No clips yet.</p>';
}

$("#clips").addEventListener("click", e => {
  const el = e.target.closest(".clip");
  if(el) select(clips.find(c => c.id === +el.dataset.id));
});

async function select(clip){
  current = clip;
  $("#workbench").hidden = false;
  $("#nothing").style.display = "none";
  $("#vid").src = `/api/train/clips/${clip.id}/video`;
  sel = null;
  await Promise.all([loadMarks(), loadBoxes()]);
  loadClips();
}

/* --------------------------------------------------------------- marks */
async function loadMarks(){
  marks = await api(`/api/train/marks?clip_id=${current.id}`);
  $("#marks").innerHTML = marks.length ? marks.map(m => `
    <div class="mark">
      <div><b style="color:${m.verdict==="incident"?"#ff8ba3":"#7ceab8"}">
        ${esc(m.label || m.verdict)}</b>
        <span class="t"> ${fmt(m.start_s)}–${fmt(m.end_s)}</span>
        ${m.note ? `<div class="t">${esc(m.note)}</div>` : ""}
        ${m.marked_by ? `<div class="t">by ${esc(m.marked_by)}</div>` : ""}
      </div>
      <button data-del="${m.id}">Remove</button>
    </div>`).join("")
    : '<p class="empty">Nothing marked on this clip yet.</p>';
  drawTimeline();
}

$("#marks").addEventListener("click", async e => {
  const id = e.target.dataset.del;
  if(!id) return;
  await api(`/api/train/marks/${id}`, {method:"DELETE"});
  await loadMarks(); loadClips(); toast("Mark removed.");
});

async function addMark(verdict){
  if(!sel){ toast("Drag across the bar to choose a stretch first."); return; }
  const label = $("#what").value.trim();
  if(!label){ toast("Say what is happening — that is the label."); return; }
  await form("/api/train/marks", {clip_id:current.id, start_s:sel.start,
    end_s:sel.end, label, verdict, note:$("#note").value.trim()});
  $("#what").value = ""; $("#note").value = ""; sel = null;
  $("#selinfo").textContent = "drag on the bar to select a stretch";
  await loadMarks(); loadClips();
  toast(verdict === "incident" ? "Marked as an incident." : "Marked as normal.");
}
$("#mark-i").onclick = () => addMark("incident");
$("#mark-n").onclick = () => addMark("normal");

/* ------------------------------------------------------------ timeline */
function dur(){ return $("#vid").duration || current?.duration_s || 0; }

function drawTimeline(){
  const tl = $("#tl"), d = dur();
  [...tl.querySelectorAll(".tl-mark,.tl-sel")].forEach(n => n.remove());
  if(!d) return;
  for(const m of marks){
    const el = document.createElement("div");
    el.className = `tl-mark ${m.verdict}`;
    el.style.left = (m.start_s / d * 100) + "%";
    el.style.width = Math.max(0.6, (m.end_s - m.start_s) / d * 100) + "%";
    el.title = `${m.label} (${fmt(m.start_s)}–${fmt(m.end_s)})`;
    tl.appendChild(el);
  }
  if(sel){
    const el = document.createElement("div");
    el.className = "tl-sel";
    el.style.left = (Math.min(sel.start, sel.end) / d * 100) + "%";
    el.style.width = Math.max(0.5,
      Math.abs(sel.end - sel.start) / d * 100) + "%";
    tl.appendChild(el);
  }
}

let dragging = false;
function tlTime(ev){
  const r = $("#tl").getBoundingClientRect();
  return Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width)) * dur();
}
$("#tl").addEventListener("pointerdown", ev => {
  if(!dur()) return;
  dragging = true; $("#tl").setPointerCapture(ev.pointerId);
  sel = {start: tlTime(ev), end: tlTime(ev)};
  drawTimeline();
});
$("#tl").addEventListener("pointermove", ev => {
  if(!dragging) return;
  sel.end = tlTime(ev); drawTimeline();
  $("#selinfo").textContent =
    `selected ${fmt(Math.min(sel.start,sel.end))}–${fmt(Math.max(sel.start,sel.end))}`;
});
$("#tl").addEventListener("pointerup", ev => {
  if(!dragging) return;
  dragging = false;
  const a = Math.min(sel.start, sel.end), b = Math.max(sel.start, sel.end);
  // A click rather than a drag: treat it as "seek here", not a zero-length
  // mark that could never be saved.
  if(b - a < 0.4){ sel = null; $("#vid").currentTime = a; drawTimeline();
    $("#selinfo").textContent = "drag on the bar to select a stretch"; return; }
  sel = {start: a, end: b};
  $("#vid").currentTime = a;
  drawTimeline();
});
$("#vid").addEventListener("timeupdate", () => {
  const d = dur();
  if(d) $("#head").style.left = ($("#vid").currentTime / d * 100) + "%";
  drawBoxes();
});
$("#vid").addEventListener("loadedmetadata", () => { drawTimeline(); sizeCanvas(); });

/* --------------------------------------------------------------- boxes */
const CLASSES = __CLASSES__;
$("#cls").innerHTML = CLASSES.map(c => `<option>${c}</option>`).join("");
$("#suggested").innerHTML = __SUGGESTED__.map(s => `<option value="${s}">`).join("");

function sizeCanvas(){
  const v = $("#vid"), c = $("#ov");
  c.width = v.clientWidth; c.height = v.clientHeight;
  drawBoxes();
}
addEventListener("resize", sizeCanvas);

async function loadBoxes(){
  boxes = await api(`/api/train/boxes?clip_id=${current.id}`);
  drawBoxes(); listBoxes();
}

function nearFrame(){
  const t = $("#vid").currentTime;
  return boxes.filter(b => Math.abs(b.t_s - t) < 0.6);
}

function drawBoxes(){
  const c = $("#ov"), g = c.getContext("2d");
  g.clearRect(0, 0, c.width, c.height);
  drawMasks(g, c);                       // AI outlines under the hand-drawn boxes
  g.lineWidth = 2; g.font = "600 12px Manrope, sans-serif";
  for(const b of nearFrame()){
    g.strokeStyle = "#8b63f5"; g.fillStyle = "rgba(139,99,245,.15)";
    const w = (b.x2-b.x1)*c.width, h = (b.y2-b.y1)*c.height;
    g.fillRect(b.x1*c.width, b.y1*c.height, w, h);
    g.strokeRect(b.x1*c.width, b.y1*c.height, w, h);
    g.fillStyle = "#dcd2ff";
    g.fillText(b.cls, b.x1*c.width + 4, b.y1*c.height - 5);
  }
  if(draw){
    g.strokeStyle = "#dcd2ff"; g.setLineDash([5,4]);
    g.strokeRect(draw.x1*c.width, draw.y1*c.height,
                 (draw.x2-draw.x1)*c.width, (draw.y2-draw.y1)*c.height);
    g.setLineDash([]);
  }
}

function listBoxes(){
  const here = nearFrame();
  $("#boxes").innerHTML = here.length ? here.map(b => `
    <div class="mark"><div><b>${esc(b.cls)}</b>
      <span class="t"> at ${fmt(b.t_s)}</span></div>
      <button data-box="${b.id}">Remove</button></div>`).join("")
    : '<p class="empty">No boxes on this frame. Pause and drag one.</p>';
}

$("#boxes").addEventListener("click", async e => {
  const id = e.target.dataset.box;
  if(!id) return;
  await api(`/api/train/boxes/${id}`, {method:"DELETE"});
  await loadBoxes(); loadClips();
});
$("#boxes-clear").onclick = async () => {
  for(const b of nearFrame()) await api(`/api/train/boxes/${b.id}`, {method:"DELETE"});
  await loadBoxes(); loadClips(); toast("Boxes cleared.");
};

/* ------------------------------------------------- segmentation overlay */
let seg = null;            // last FrameSegmentation for this clip
let segFrame = null;       // which frame it belongs to
let picked = null;         // temporary_object_id of the selected object
let hovered = null;
let selMode = false;

const CLASS_HUE = {person:"#4ade9e", car:"#8b63f5", bag:"#ffb45c",
                   motorcycle:"#63d2ff", bicycle:"#63d2ff", bus:"#c3aeff",
                   truck:"#c3aeff"};
const hueFor = c => CLASS_HUE[c] || "#c8bfe8";

$("#selmode").onclick = () => {
  selMode = !selMode;
  $("#selmode").classList.toggle("on", selMode);
  $("#selmode").textContent = "Select Object: " + (selMode ? "on" : "off");
  document.querySelector(".stage").classList.toggle("selecting", selMode);
  if(selMode && !seg) runSegment();
};
$("#clearsel").onclick = () => { picked = null; drawBoxes(); };
for(const id of ["t-mask","t-box","t-label"]) $("#"+id).onchange = drawBoxes;

async function runSegment(force){
  if(!current) return;
  const t = $("#vid").currentTime || 0;
  $("#segstat").textContent = "looking at this frame...";
  try {
    seg = await form(`/api/train/clips/${current.id}/segment-frame`,
                     {timestamp_ms: Math.round(t * 1000),
                      force_refresh: force ? "true" : "false"});
    segFrame = seg.frame_index;
    picked = null;
    $("#segstat").textContent = seg.objects.length
      ? `${seg.objects.length} object${seg.objects.length===1?"":"s"} outlined `
        + `on frame ${seg.frame_index} (${esc(seg.model)})`
      : `nothing found on frame ${seg.frame_index} (${esc(seg.model)})`;
    drawBoxes();
  } catch(e){ $("#segstat").textContent = "could not segment: " + e.message; }
}
$("#seg").onclick = () => runSegment(true);

// The video moved, so the outlines belong to a different picture. Drop them
// rather than draw last frame's shapes over this frame's objects.
$("#vid").addEventListener("seeked", () => {
  if(seg){ seg = null; picked = null; $("#segstat").textContent = ""; drawBoxes(); }
  if(selMode) runSegment();
});

function objAt(x, y){          // x,y in canvas pixels -> object under them
  if(!seg) return null;
  const c = $("#ov");
  const fx = x / c.width * seg.frame_width, fy = y / c.height * seg.frame_height;
  let best = null, bestArea = Infinity;
  for(const o of seg.objects){
    for(const poly of (o.polygons || [])){
      if(pointInPoly(fx, fy, poly)){
        const a = polyArea(poly);
        if(a < bestArea){ best = o; bestArea = a; }
      }
    }
  }
  return best;
}
function pointInPoly(x, y, poly){
  let inside = false;
  for(let i = 0, j = poly.length - 1; i < poly.length; j = i++){
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if((yi > y) !== (yj > y) &&
       x < (xj - xi) * (y - yi) / ((yj - yi) || 1e-12) + xi) inside = !inside;
  }
  return inside;
}
function polyArea(poly){
  let t = 0;
  for(let i = 0; i < poly.length; i++){
    const [x1, y1] = poly[i], [x2, y2] = poly[(i + 1) % poly.length];
    t += x1 * y2 - x2 * y1;
  }
  return Math.abs(t) / 2;
}

function drawMasks(g, c){
  if(!seg || !$("#t-mask").checked) return;
  const sx = c.width / seg.frame_width, sy = c.height / seg.frame_height;
  for(const o of seg.objects){
    const isPicked = o.temporary_object_id === picked;
    const isHover = o.temporary_object_id === hovered;
    const hue = hueFor(o.class_name);
    g.lineWidth = isPicked ? 3 : 2;
    g.strokeStyle = hue;
    g.fillStyle = hue + (isPicked ? "55" : isHover ? "3a" : "1f");
    for(const poly of (o.polygons || [])){
      if(poly.length < 3) continue;
      g.beginPath();
      g.moveTo(poly[0][0] * sx, poly[0][1] * sy);
      for(let i = 1; i < poly.length; i++) g.lineTo(poly[i][0] * sx, poly[i][1] * sy);
      g.closePath(); g.fill(); g.stroke();
    }
    if($("#t-box").checked){
      g.setLineDash([4, 3]); g.strokeStyle = hue + "99";
      g.strokeRect(o.bbox.x_min * sx, o.bbox.y_min * sy,
                   (o.bbox.x_max - o.bbox.x_min) * sx,
                   (o.bbox.y_max - o.bbox.y_min) * sy);
      g.setLineDash([]);
    }
    if($("#t-label").checked && (isPicked || isHover || !picked)){
      const text = `${o.class_name} ${Math.round(o.confidence * 100)}%`;
      g.font = "600 12px Manrope, sans-serif";
      const w = g.measureText(text).width + 10;
      const bx = o.bbox.x_min * sx, by = o.bbox.y_min * sy;
      g.fillStyle = "rgba(8,5,15,.8)";
      g.fillRect(bx, Math.max(0, by - 18), w, 17);
      g.fillStyle = hue;
      g.fillText(text, bx + 5, Math.max(11, by - 5));
    }
  }
}

let draw = null;
function canvasPt(ev){
  const r = $("#ov").getBoundingClientRect();
  return [Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width)),
          Math.max(0, Math.min(1, (ev.clientY - r.top) / r.height))];
}
$("#ov").addEventListener("pointermove", ev => {
  if(!selMode || draw) return;
  const r = $("#ov").getBoundingClientRect();
  const o = objAt((ev.clientX - r.left) / r.width * $("#ov").width,
                  (ev.clientY - r.top) / r.height * $("#ov").height);
  const id = o && o.temporary_object_id;
  if(id !== hovered){ hovered = id; drawBoxes(); }
});

$("#ov").addEventListener("pointerdown", async ev => {
  if(selMode){
    // Clicking an object is a selection, not the start of a box.
    ev.preventDefault();
    const v = $("#vid");
    if(!v.paused) v.pause();
    const r = $("#ov").getBoundingClientRect();
    try {
      const out = await form(`/api/train/clips/${current.id}/select-object`, {
        timestamp_ms: Math.round(v.currentTime * 1000),
        display_x: ev.clientX - r.left, display_y: ev.clientY - r.top,
        display_width: r.width, display_height: r.height});
      seg = {frame_index: out.frame_index, frame_width: out.recommended
               ? seg.frame_width : (seg && seg.frame_width),
             frame_height: seg && seg.frame_height,
             model: out.model, objects: out.objects};
      picked = out.recommended_object_id;
      if(picked){
        const o = out.recommended;
        $("#cls").value = CLASSES.includes(o.class_name) ? o.class_name : "other";
        $("#segstat").textContent =
          `${o.class_name} · ${Math.round(o.confidence * 100)}% · `
          + `${out.selection_method.replace("_", " ")}`
          + (out.overlapping_candidates.length > 1
             ? ` · ${out.overlapping_candidates.length - 1} other candidate(s)` : "");
      } else {
        $("#segstat").textContent = out.clicked_on_video
          ? "nothing there — draw a box instead"
          : "that was outside the picture";
      }
      drawBoxes();
    } catch(e){ $("#segstat").textContent = "selection failed: " + e.message; }
    return;
  }
  if(!$("#vid").paused){ toast("Pause the video first."); return; }
  const [x, y] = canvasPt(ev);
  draw = {x1:x, y1:y, x2:x, y2:y};
  $("#ov").setPointerCapture(ev.pointerId);
});
$("#ov").addEventListener("pointermove", ev => {
  if(!draw) return;
  [draw.x2, draw.y2] = canvasPt(ev); drawBoxes();
});
$("#ov").addEventListener("pointerup", async () => {
  if(!draw) return;
  const b = {x1:Math.min(draw.x1,draw.x2), y1:Math.min(draw.y1,draw.y2),
             x2:Math.max(draw.x1,draw.x2), y2:Math.max(draw.y1,draw.y2)};
  draw = null;
  if(b.x2-b.x1 < 0.01 || b.y2-b.y1 < 0.01){ drawBoxes(); return; }
  await form("/api/train/boxes", {clip_id:current.id,
    t_s:$("#vid").currentTime, cls:$("#cls").value, ...b});
  await loadBoxes(); loadClips();
});

/* -------------------------------------------------------------- upload */
$("#upload").onclick = async () => {
  const f = $("#up").files[0];
  if(!f){ toast("Choose a file first."); return; }
  const fd = new FormData();
  fd.append("file", f); fd.append("source", $("#source").value.trim());
  $("#upstat").textContent = "uploading...";
  try {
    const r = await api("/api/train/clips", {method:"POST", body:fd});
    $("#upstat").textContent = `added ${r.filename}`;
    $("#up").value = "";
    await loadClips();
    select(clips.find(c => c.id === r.id));
  } catch(e){ $("#upstat").textContent = "upload failed: " + e.message; }
};

$("#export").onclick = () => { location.href = "/api/train/export"; };

/* ---------------------------------------------------------------- boot */
async function boot(){
  try { me = await api("/api/me"); }
  catch(e){ $("#gate").classList.add("on"); return; }
  if(!me.can.includes("registry")){
    $("#gate").classList.add("on");
    $("#gerr").textContent = `A ${me.role} account cannot label footage.`;
    return;
  }
  $("#who").textContent = me.name;
  await loadClips();
}
boot();
</script>
</body>
</html>
"""

PAGE = (PAGE.replace("__CLASSES__", str(list(CLASSES)))
            .replace("__SUGGESTED__", str(list(SUGGESTED))))
