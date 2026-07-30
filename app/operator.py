"""The operator app — the phone-sized VisionGuard for guards and committee.

Separate from the console on purpose. The console is for sitting down and
reviewing footage; this is for someone standing at a gate at 2am holding a
cheap Android in one hand. So: bottom navigation within thumb reach, tap
targets you can hit without looking, a dark ground that does not blind at
night, and three jobs only — triage an alert, check the gate register, tell
residents something.

Served as a PWA (installable, works from the home screen) rather than a native
app: no app-store review between a fix and the guard having it.
"""
from __future__ import annotations

import io
import json

# Amber is the accent because that is what a gate barrier and a high-vis vest
# already are — it reads as "security" on a screen without explanation.
PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#12151a">
<title>VisionGuard Operator</title>
<link rel="manifest" href="/operator/manifest.webmanifest">
<link rel="icon" href="/operator/icon-192.png">
<link rel="apple-touch-icon" href="/operator/icon-192.png">
<style>
:root{
  --ground:#12151a; --surface:#1b1f27; --raised:#232833; --line:#2f3542;
  --text:#e9ecf1; --muted:#98a1b0;
  --amber:#f2a33c; --red:#e5484d; --green:#30a46c;
  --tap:56px;
}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;padding:0}
body{
  background:var(--ground); color:var(--text);
  font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  font-size:16px; line-height:1.5;
  padding-bottom:calc(76px + env(safe-area-inset-bottom));
}
header{
  position:sticky; top:0; z-index:10; background:var(--ground);
  border-bottom:1px solid var(--line);
  padding:calc(14px + env(safe-area-inset-top)) 16px 12px;
  display:flex; align-items:baseline; justify-content:space-between; gap:12px;
}
.wordmark{font-size:15px; font-weight:700; letter-spacing:.14em; text-transform:uppercase}
.wordmark span{color:var(--amber)}
.who{font-size:13px; color:var(--muted); text-decoration:underline; cursor:pointer;
     background:none; border:0; font-family:inherit; padding:4px}
main{padding:16px; max-width:640px; margin:0 auto}
.view{display:none} .view.on{display:block}
h2{font-size:13px; letter-spacing:.12em; text-transform:uppercase; color:var(--muted);
   margin:0 0 10px; font-weight:600}
.hint{color:var(--muted); font-size:14px; margin:0 0 16px}

/* --- summary strip: the answer before the detail --- */
.strip{display:grid; grid-template-columns:repeat(3,1fr); gap:8px; margin-bottom:20px}
.stat{background:var(--surface); border:1px solid var(--line); border-radius:12px;
      padding:12px 10px; text-align:center}
.stat b{display:block; font-size:26px; font-variant-numeric:tabular-nums; line-height:1.1}
.stat small{color:var(--muted); font-size:11px; letter-spacing:.06em; text-transform:uppercase}
.stat.warn b{color:var(--amber)} .stat.bad b{color:var(--red)}

/* --- cards --- */
.card{background:var(--surface); border:1px solid var(--line); border-radius:14px;
      padding:14px; margin-bottom:12px; position:relative; overflow:hidden}
.card::before{content:""; position:absolute; left:0; top:0; bottom:0; width:4px;
              background:var(--muted)}
.card.sev-HIGH::before{background:var(--red)}
.card.sev-MEDIUM::before{background:var(--amber)}
.card.sev-LOW::before{background:var(--muted)}
.card.done::before{background:var(--green)}
.row{display:flex; justify-content:space-between; align-items:baseline; gap:10px}
.kind{font-weight:650; letter-spacing:.02em}
.when{color:var(--muted); font-size:13px; font-variant-numeric:tabular-nums;
      white-space:nowrap}
.desc{margin:6px 0 0; font-size:15px}
.meta{margin-top:6px; color:var(--muted); font-size:13px}
.pill{display:inline-block; padding:2px 8px; border-radius:999px; font-size:11px;
      font-weight:650; letter-spacing:.08em; text-transform:uppercase;
      border:1px solid currentColor}
.pill.high{color:var(--red)} .pill.medium{color:var(--amber)}
.pill.low{color:var(--muted)} .pill.ok{color:var(--green)}

/* --- actions: two targets, both reachable, impossible to confuse --- */
.actions{display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-top:14px}
button.act{min-height:var(--tap); border-radius:12px; border:1px solid var(--line);
  background:var(--raised); color:var(--text); font:inherit; font-weight:650;
  cursor:pointer}
button.act.real{border-color:var(--red); color:#ff9a9d}
button.act.false{border-color:var(--green); color:#7ee2b8}
button.act:active{transform:translateY(1px)}
button.act[disabled]{opacity:.5}
.verdict{margin-top:12px; font-size:14px; color:var(--green); font-weight:600}
.verdict.real{color:var(--red)}
a.clip{display:inline-block; margin-top:10px; color:var(--amber); font-size:14px}

/* --- forms --- */
label{display:block; font-size:13px; color:var(--muted); margin:14px 0 6px}
input,textarea,select{width:100%; background:var(--raised); color:var(--text);
  border:1px solid var(--line); border-radius:10px; padding:12px; font:inherit}
textarea{min-height:110px; resize:vertical}
button.primary{width:100%; min-height:var(--tap); margin-top:18px; border-radius:12px;
  border:0; background:var(--amber); color:#17191d; font:inherit; font-weight:700;
  letter-spacing:.02em; cursor:pointer}
button.primary[disabled]{opacity:.6}
.toast{position:fixed; left:16px; right:16px; bottom:calc(88px + env(safe-area-inset-bottom));
  background:var(--raised); border:1px solid var(--line); border-left:4px solid var(--green);
  border-radius:10px; padding:12px 14px; font-size:14px; z-index:30;
  transform:translateY(140%); transition:transform .22s ease}
.toast.on{transform:none}
.toast.err{border-left-color:var(--red)}
.empty{color:var(--muted); text-align:center; padding:40px 20px; font-size:15px}

/* --- bottom nav: thumb territory --- */
nav{position:fixed; left:0; right:0; bottom:0; z-index:20; display:grid;
  grid-template-columns:repeat(3,1fr); background:var(--surface);
  border-top:1px solid var(--line); padding-bottom:env(safe-area-inset-bottom)}
nav button{background:none; border:0; color:var(--muted); font:inherit; font-size:12px;
  letter-spacing:.06em; text-transform:uppercase; font-weight:650;
  min-height:64px; cursor:pointer; position:relative}
nav button.on{color:var(--amber)}
nav button.on::after{content:""; position:absolute; top:0; left:22%; right:22%; height:2px;
  background:var(--amber)}
nav .badge{display:inline-block; min-width:18px; margin-left:5px; padding:0 5px;
  border-radius:9px; background:var(--red); color:#fff; font-size:11px; line-height:18px}
@media (prefers-reduced-motion:reduce){.toast{transition:none}}
</style>
</head>
<body>
<header>
  <div class="wordmark">Vision<span>Guard</span> Operator</div>
  <button class="who" id="who">set name</button>
</header>

<main>
  <section class="view on" id="v-alerts">
    <div class="strip">
      <div class="stat bad"><b id="s-untriaged">0</b><small>To check</small></div>
      <div class="stat"><b id="s-today">0</b><small>Today</small></div>
      <div class="stat"><b id="s-false">0</b><small>False</small></div>
    </div>
    <h2>Detections</h2>
    <p class="hint">Mark each one once you have looked. Your answer trains the
      system to stop raising the ones that were never anything.</p>
    <div id="alerts"></div>
  </section>

  <section class="view" id="v-gate">
    <div class="strip">
      <div class="stat"><b id="s-inside">0</b><small>Inside</small></div>
      <div class="stat warn"><b id="s-visitors">0</b><small>Visitors</small></div>
      <div class="stat bad"><b id="s-over">0</b><small>Overstay</small></div>
    </div>
    <h2>Gate register</h2>
    <p class="hint">Written by the camera, not by hand. Every plate that passes
      the gate opens a visit; the next pass closes it.</p>
    <input id="q" placeholder="Search a plate" autocomplete="off"
           inputmode="latin" aria-label="Search a plate">
    <div id="visits" style="margin-top:14px"></div>
  </section>

  <section class="view" id="v-notices">
    <h2>Tell the members</h2>
    <p class="hint">Goes to every resident who has connected Telegram. Anything
      you send is kept on record below.</p>
    <label for="n-title">Subject</label>
    <input id="n-title" maxlength="80" placeholder="Water supply cut">
    <label for="n-body">Message</label>
    <textarea id="n-body" maxlength="900" placeholder="Tomorrow, 10am to 1pm."></textarea>
    <label for="n-aud">Send to</label>
    <select id="n-aud">
      <option value="all">Everyone</option>
      <option value="flat">One flat</option>
    </select>
    <div id="flat-wrap" style="display:none">
      <label for="n-flat">Flat number</label>
      <input id="n-flat" placeholder="B-402">
    </div>
    <button class="primary" id="send">Send message</button>
    <h2 style="margin-top:30px">Sent</h2>
    <div id="notices"></div>
  </section>
</main>

<nav>
  <button class="on" data-view="alerts">Alerts<span class="badge" id="nav-badge"
    style="display:none">0</span></button>
  <button data-view="gate">Gate</button>
  <button data-view="notices">Messages</button>
</nav>
<div class="toast" id="toast"></div>

<script>
const $ = s => document.querySelector(s);
const api = p => fetch(p, {cache:"no-store"}).then(r => r.json());
const form = (p, d) => fetch(p, {method:"POST", body:new URLSearchParams(d)})
  .then(async r => { if(!r.ok) throw new Error((await r.json()).detail || r.status);
                     return r.json(); });

let who = localStorage.getItem("vg_operator") || "";
function renderWho(){ $("#who").textContent = who || "set name"; }
$("#who").onclick = () => {
  const n = prompt("Your name — it is recorded against what you mark.", who);
  if(n !== null){ who = n.trim(); localStorage.setItem("vg_operator", who); renderWho(); }
};
renderWho();

let toastTimer;
function toast(msg, bad){
  const t = $("#toast");
  t.textContent = msg; t.classList.toggle("err", !!bad); t.classList.add("on");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("on"), 3200);
}

const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

function ago(ts){
  const s = Math.max(0, Date.now()/1000 - ts);
  if(s < 60) return "just now";
  if(s < 3600) return Math.floor(s/60) + " min ago";
  if(s < 86400) return Math.floor(s/3600) + " hr ago";
  return new Date(ts*1000).toLocaleDateString([], {day:"numeric", month:"short"});
}
const clock = ts => new Date(ts*1000).toLocaleTimeString([],
  {hour:"2-digit", minute:"2-digit"});

/* ------------------------------------------------------------- alerts */
async function loadAlerts(){
  let rows = [];
  try { rows = await api("/api/events?limit=60"); }
  catch(e){ $("#alerts").innerHTML = '<p class="empty">Cannot reach the server.</p>';
            return; }
  const dayAgo = Date.now()/1000 - 86400;
  const untriaged = rows.filter(r => !r.verdict).length;
  $("#s-untriaged").textContent = untriaged;
  $("#s-today").textContent = rows.filter(r => r.ts > dayAgo).length;
  $("#s-false").textContent = rows.filter(r => r.verdict === "false_alarm").length;
  const badge = $("#nav-badge");
  badge.style.display = untriaged ? "inline-block" : "none";
  badge.textContent = untriaged;

  if(!rows.length){
    $("#alerts").innerHTML = '<p class="empty">Nothing has been detected yet.</p>';
    return;
  }
  // What still needs a person comes first; within that, newest first. The
  // server orders by row id, which is not the same thing once an old clip is
  // analysed after a live one.
  rows.sort((a, b) => (!!a.verdict - !!b.verdict) || (b.ts - a.ts));
  $("#alerts").innerHTML = rows.map(r => {
    const sev = (r.severity || "LOW").toUpperCase();
    const done = !!r.verdict;
    const clip = r.clip_id && !r.clip_deleted
      ? `<a class="clip" href="/clips/${r.clip_id}" target="_blank"
            rel="noopener">Watch the clip</a>` : "";
    const body = done
      ? `<p class="verdict ${r.verdict === "real" ? "real" : ""}">
           ${r.verdict === "real" ? "Confirmed real" : "Marked a false alarm"}
           ${r.verdict_by && r.verdict_by !== "operator"
              ? "by " + esc(r.verdict_by) : ""}</p>`
      : `<div class="actions">
           <button class="act real" data-id="${r.id}" data-v="real">It is real</button>
           <button class="act false" data-id="${r.id}" data-v="false_alarm">False alarm</button>
         </div>`;
    return `<article class="card sev-${sev} ${done ? "done" : ""}">
      <div class="row">
        <span class="kind">${esc((r.event_type || "").replace(/_/g, " ").toLowerCase())}</span>
        <span class="when">${ago(r.ts)}</span>
      </div>
      <p class="desc">${esc(r.description || "")}</p>
      <p class="meta"><span class="pill ${sev.toLowerCase()}">${sev}</span>
        &nbsp;${esc(r.camera || "")}${r.plate ? " &middot; " + esc(r.plate) : ""}</p>
      ${r.ai_summary ? `<p class="meta">AI: ${esc(r.ai_summary)}</p>` : ""}
      ${clip}${body}
    </article>`;
  }).join("");
}

$("#alerts").addEventListener("click", async e => {
  const b = e.target.closest("button.act");
  if(!b) return;
  const card = b.closest(".card");
  card.querySelectorAll("button.act").forEach(x => x.disabled = true);
  try {
    await form(`/api/events/${b.dataset.id}/feedback`,
               {verdict:b.dataset.v, user_name:who});
    toast(b.dataset.v === "real" ? "Marked real. Thank you."
                                 : "Marked a false alarm. Thank you.");
    loadAlerts();
  } catch(err){
    card.querySelectorAll("button.act").forEach(x => x.disabled = false);
    toast("Could not save that — try again.", true);
  }
});

/* --------------------------------------------------------------- gate */
async function loadGate(){
  let open = [], over = [], rows = [];
  const q = $("#q").value.trim();
  try {
    [open, over, rows] = await Promise.all([
      api("/api/visits/open"), api("/api/visits/overstays"),
      api("/api/visits?limit=100" + (q ? "&plate=" + encodeURIComponent(q) : ""))]);
  } catch(e){ $("#visits").innerHTML = '<p class="empty">Cannot reach the server.</p>';
              return; }
  $("#s-inside").textContent = open.length;
  $("#s-visitors").textContent = open.filter(v => !v.registered).length;
  $("#s-over").textContent = over.length;
  const overIds = new Set(over.map(v => v.id));

  if(!rows.length){
    $("#visits").innerHTML = `<p class="empty">${q ? "No visit for that plate."
      : "No vehicle has passed the gate yet."}</p>`;
    return;
  }
  $("#visits").innerHTML = rows.map(v => {
    const inside = !v.exit_ts, late = overIds.has(v.id);
    const tag = late ? '<span class="pill high">Overstaying</span>'
      : v.registered ? '<span class="pill ok">Resident</span>'
                     : '<span class="pill medium">Visitor</span>';
    const owner = v.owner_name
      ? esc(v.owner_name) + (v.flat_number ? " &middot; " + esc(v.flat_number) : "")
      : "not in the registry";
    return `<article class="card ${late ? "sev-HIGH" : inside ? "sev-MEDIUM" : "done"}">
      <div class="row">
        <span class="kind" style="letter-spacing:.06em">${esc(v.plate)}</span>
        <span class="when">${inside ? "in " + clock(v.entry_ts)
                                    : clock(v.entry_ts) + " – " + clock(v.exit_ts)}</span>
      </div>
      <p class="meta">${tag}&nbsp; ${owner}</p>
      <p class="meta">${inside ? "Still inside, since " + ago(v.entry_ts)
                               : "Left " + ago(v.exit_ts)}</p>
    </article>`;
  }).join("");
}
let qTimer;
$("#q").addEventListener("input", () => {
  clearTimeout(qTimer); qTimer = setTimeout(loadGate, 300);
});

/* ------------------------------------------------------------ notices */
$("#n-aud").onchange = e => {
  $("#flat-wrap").style.display = e.target.value === "flat" ? "block" : "none";
};
$("#send").onclick = async () => {
  const title = $("#n-title").value.trim(), body = $("#n-body").value.trim();
  if(!title || !body){ toast("A subject and a message are needed.", true); return; }
  const aud = $("#n-aud").value, flat = $("#n-flat").value.trim();
  if(aud === "flat" && !flat){ toast("Which flat?", true); return; }
  $("#send").disabled = true;
  try {
    const r = await form("/api/notices",
      {title, body, author:who || "committee", audience:aud, flat_number:flat});
    toast(r.recipients
      ? `Sent to ${r.recipients} ${r.recipients === 1 ? "resident" : "residents"}.`
      : "Saved. No resident has connected Telegram yet, so nothing was delivered.");
    $("#n-title").value = ""; $("#n-body").value = "";
    loadNotices();
  } catch(err){ toast("Could not send that — try again.", true); }
  $("#send").disabled = false;
};

async function loadNotices(){
  let rows = [];
  try { rows = await api("/api/notices?limit=30"); } catch(e){ return; }
  $("#notices").innerHTML = rows.length ? rows.map(n => `
    <article class="card done">
      <div class="row"><span class="kind">${esc(n.title)}</span>
        <span class="when">${ago(n.ts)}</span></div>
      <p class="desc">${esc(n.body)}</p>
      <p class="meta">${n.audience === "flat" ? "Flat " + esc(n.flat_number) : "Everyone"}
        &middot; ${n.recipients} delivered &middot; ${esc(n.author)}</p>
    </article>`).join("")
    : '<p class="empty">Nothing sent yet.</p>';
}

/* ------------------------------------------------------------- routing */
const loaders = {alerts:loadAlerts, gate:loadGate, notices:loadNotices};
let current = "alerts";
document.querySelectorAll("nav button").forEach(b => b.onclick = () => {
  current = b.dataset.view;
  document.querySelectorAll("nav button").forEach(x => x.classList.toggle("on", x === b));
  document.querySelectorAll(".view").forEach(v =>
    v.classList.toggle("on", v.id === "v-" + current));
  loaders[current]();
});

loadAlerts(); loadNotices();
// A guard leaves this open on a desk. Refresh the view they are looking at,
// but only while the phone is awake and showing it.
setInterval(() => { if(!document.hidden) loaders[current](); }, 15000);
document.addEventListener("visibilitychange", () => {
  if(!document.hidden) loaders[current]();
});

if("serviceWorker" in navigator){
  navigator.serviceWorker.register("/operator/sw.js").catch(() => {});
}
</script>
</body>
</html>
"""

MANIFEST = {
    "name": "VisionGuard Operator",
    "short_name": "Operator",
    "description": "Alerts, the gate register and messages to members.",
    "start_url": "/operator",
    "scope": "/operator",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#12151a",
    "theme_color": "#12151a",
    "icons": [
        {"src": "/operator/icon-192.png", "sizes": "192x192", "type": "image/png",
         "purpose": "any"},
        {"src": "/operator/icon-512.png", "sizes": "512x512", "type": "image/png",
         "purpose": "any maskable"},
    ],
}

# Network-first with a cached shell: a guard on a dead gate-side connection
# still gets the app open (and an honest "cannot reach the server"), rather
# than a browser error page. Alert data is never served from cache — stale
# alerts are worse than none.
SW = """
const SHELL = "vg-operator-shell-v1";
self.addEventListener("install", e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(["/operator"]))
              .then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys()
    .then(ks => Promise.all(ks.filter(k => k !== SHELL).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if(e.request.method !== "GET" || url.pathname.startsWith("/api/")) return;
  if(e.request.mode === "navigate"){
    e.respondWith(fetch(e.request)
      .then(r => { caches.open(SHELL).then(c => c.put("/operator", r.clone())); return r; })
      .catch(() => caches.match("/operator")));
  }
});
"""


def icon_png(size: int) -> bytes:
    """The home-screen icon, drawn rather than shipped as a binary blob so it
    stays editable and the repo stays free of build artifacts."""
    import cv2
    import numpy as np

    img = np.full((size, size, 3), (26, 21, 18), dtype=np.uint8)   # BGR ground
    c, r = size // 2, int(size * 0.34)
    cv2.circle(img, (c, c), r, (60, 163, 242), max(2, size // 24))  # amber ring
    # a shield-ish chevron inside the ring
    d = int(r * 0.52)
    pts = np.array([[c - d, c - d], [c, c + d], [c + d, c - d]], dtype=np.int32)
    cv2.polylines(img, [pts], False, (60, 163, 242), max(2, size // 20),
                  lineType=cv2.LINE_AA)
    ok, buf = cv2.imencode(".png", img)
    if not ok:                                     # pragma: no cover
        raise RuntimeError("icon encode failed")
    return io.BytesIO(buf.tobytes()).getvalue()


def manifest_json() -> str:
    return json.dumps(MANIFEST)
