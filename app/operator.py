"""The operator app — the phone-sized VisionGuard for guards and committee.

Separate from the console on purpose. The console is for sitting down and
reviewing footage; this is for someone standing at a gate holding a cheap
Android in one hand. So: bottom navigation within thumb reach, tap targets you
can hit without looking, and three jobs only — triage an alert, check the gate
register, tell residents something.

The look: a soft grey-white ground with the content sitting on it as physical
slabs. Depth does the work colour usually does — a card is raised, a statistic
is pressed into the surface, a button gives under the thumb. That leaves the
three strong hues to mean one thing each (needs attention, watch, settled) so
a guard can read the screen at arm's length without decoding a legend.

Served as a PWA (installable, works from the home screen) rather than a native
app: no app-store review between a fix and the guard having it.
"""
from __future__ import annotations

import io
import json

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#e8ebf0">
<title>VisionGuard Operator</title>
<link rel="manifest" href="/operator/manifest.webmanifest">
<link rel="icon" href="/operator/icon-192.png">
<link rel="apple-touch-icon" href="/operator/icon-192.png">
<style>
:root{
  /* A cool grey-white, not a cream: it stays neutral under the yellow light
     of a gate lamp, where a warm ground goes muddy. */
  --ground:#e8ebf0;
  --slab-top:#fdfdfe; --slab-bottom:#eff1f5;
  --ink:#171a20; --ink-soft:#3c424e; --muted:#767e8c;
  --hairline:rgba(23,26,32,.09);

  /* One hue per meaning. Nothing else in the app is coloured. */
  --alert:#c8394a; --caution:#b8721c; --calm:#1f8a6d; --action:#3b4ba8;
  --alert-wash:#fdf2f3; --caution-wash:#fdf7ee; --calm-wash:#eff8f4;

  /* Light falls from above, so every raised thing has a white top edge and
     drops a shadow; every recessed thing does the exact opposite. */
  --raise:0 1px 1.5px rgba(20,25,40,.05), 0 10px 22px -14px rgba(20,25,40,.42),
          inset 0 1px 0 rgba(255,255,255,.95);
  --raise-lift:0 2px 3px rgba(20,25,40,.06), 0 18px 34px -16px rgba(20,25,40,.5),
          inset 0 1px 0 rgba(255,255,255,.95);
  --recess:inset 0 2px 5px rgba(20,25,40,.11), inset 0 -1px 0 rgba(255,255,255,.9);
  --press:inset 0 3px 7px rgba(20,25,40,.16);

  --r-card:22px; --r-field:15px; --tap:56px;
}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;padding:0}
body{
  background:var(--ground); color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text",system-ui,
              "Segoe UI",Roboto,sans-serif;
  font-size:16px; line-height:1.55; letter-spacing:-.011em;
  -webkit-font-smoothing:antialiased;
  padding-bottom:calc(94px + env(safe-area-inset-bottom));
}
:focus-visible{outline:2px solid var(--action); outline-offset:2px; border-radius:8px}

/* --- header: sits on the ground, no slab, so the content floats above it --- */
header{
  position:sticky; top:0; z-index:10;
  background:linear-gradient(var(--ground) 68%, rgba(232,235,240,0));
  padding:calc(20px + env(safe-area-inset-top)) 20px 14px;
  display:flex; align-items:center; justify-content:space-between; gap:12px;
}
.wordmark{font-size:15px; font-weight:680; letter-spacing:.02em; color:var(--ink)}
.wordmark span{color:var(--muted); font-weight:500}
.who{
  font:inherit; font-size:13px; font-weight:560; color:var(--ink-soft);
  background:linear-gradient(var(--slab-top),var(--slab-bottom));
  border:1px solid var(--hairline); box-shadow:var(--raise);
  border-radius:999px; padding:7px 15px; cursor:pointer;
}
.who:active{box-shadow:var(--press); background:var(--slab-bottom)}

main{padding:4px 20px 20px; max-width:560px; margin:0 auto}
.view{display:none} .view.on{display:block; animation:rise .3s cubic-bezier(.2,.7,.3,1)}
@keyframes rise{from{opacity:0; transform:translateY(6px)} to{opacity:1; transform:none}}

h1{font-size:27px; font-weight:700; letter-spacing:-.028em; margin:8px 0 6px;
   text-wrap:balance}
.hint{color:var(--muted); font-size:14.5px; margin:0 0 22px; max-width:44ch}
h2{font-size:11px; font-weight:700; letter-spacing:.11em; text-transform:uppercase;
   color:var(--muted); margin:32px 0 12px}

/* --- statistics are pressed INTO the ground; cards sit on top of it --- */
.strip{display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin:0 0 26px}
.stat{
  background:var(--ground); box-shadow:var(--recess);
  border-radius:18px; padding:14px 8px 12px; text-align:center;
}
.stat b{display:block; font-size:30px; font-weight:680; line-height:1.05;
        letter-spacing:-.03em; font-variant-numeric:tabular-nums; color:var(--ink)}
.stat small{display:block; margin-top:3px; color:var(--muted); font-size:10.5px;
            font-weight:640; letter-spacing:.07em; text-transform:uppercase}
.stat.warn b{color:var(--caution)} .stat.bad b{color:var(--alert)}
.stat.zero b{color:var(--muted)}

/* --- cards: slabs, with a coloured tab clipped to the left edge --- */
.card{
  background:linear-gradient(var(--slab-top),var(--slab-bottom));
  border:1px solid var(--hairline); border-radius:var(--r-card);
  box-shadow:var(--raise); padding:17px 18px 18px 21px;
  margin-bottom:13px; position:relative;
}
.card::before{
  content:""; position:absolute; left:7px; top:16px; bottom:16px; width:4px;
  border-radius:4px; background:var(--muted); opacity:.55;
}
.card.sev-HIGH::before{background:var(--alert); opacity:1}
.card.sev-MEDIUM::before{background:var(--caution); opacity:1}
.card.sev-LOW::before{background:var(--muted)}
.card.sev-HIGH{background:linear-gradient(#fff,var(--alert-wash))}
.card.sev-MEDIUM{background:linear-gradient(#fff,var(--caution-wash))}
/* handled: settles back into the page instead of competing for attention */
.card.done{box-shadow:0 1px 1px rgba(20,25,40,.04), inset 0 1px 0 #fff;
           background:linear-gradient(var(--slab-top),var(--slab-bottom))}
.card.done::before{background:var(--calm); opacity:.85}

.row{display:flex; justify-content:space-between; align-items:baseline; gap:12px}
.kind{font-size:17px; font-weight:660; letter-spacing:-.02em}
.kind.plate{letter-spacing:.05em; font-variant-numeric:tabular-nums;
            text-transform:none}
.when{color:var(--muted); font-size:13px; font-weight:520; white-space:nowrap;
      font-variant-numeric:tabular-nums}
.desc{margin:5px 0 0; font-size:15.5px; color:var(--ink-soft); line-height:1.5}
.meta{margin:11px 0 0; color:var(--muted); font-size:13.5px;
      display:flex; align-items:center; gap:9px; flex-wrap:wrap}
.pill{
  display:inline-block; padding:3px 11px; border-radius:999px;
  font-size:10.5px; font-weight:720; letter-spacing:.07em; text-transform:uppercase;
  background:var(--ground); box-shadow:var(--recess); color:var(--muted);
}
.pill.high{color:var(--alert); background:var(--alert-wash)}
.pill.medium{color:var(--caution); background:var(--caution-wash)}
.pill.ok{color:var(--calm); background:var(--calm-wash)}

/* --- actions: two keys on a panel. They give when pressed. --- */
.actions{display:grid; grid-template-columns:1fr 1fr; gap:11px; margin-top:16px}
button.act{
  min-height:var(--tap); border-radius:16px; cursor:pointer;
  font:inherit; font-size:15.5px; font-weight:640; letter-spacing:-.01em;
  background:linear-gradient(var(--slab-top),var(--slab-bottom));
  border:1px solid var(--hairline); box-shadow:var(--raise); color:var(--ink-soft);
  transition:box-shadow .12s ease, background .12s ease;
}
button.act.real{color:var(--alert)}
button.act.false{color:var(--calm)}
button.act:active{box-shadow:var(--press); background:var(--slab-bottom)}
button.act[disabled]{opacity:.45; box-shadow:var(--press)}
.verdict{margin:14px 0 0; font-size:14.5px; font-weight:600; color:var(--calm);
         display:flex; align-items:center; gap:8px}
.verdict.real{color:var(--alert)}
.verdict::before{content:""; width:7px; height:7px; border-radius:50%;
                 background:currentColor; flex:none}
a.clip{display:inline-block; margin-top:13px; color:var(--action);
       font-size:14.5px; font-weight:560; text-decoration:none;
       border-bottom:1px solid rgba(59,75,168,.3); padding-bottom:1px}

/* --- fields are recessed: they are holes you put things into --- */
label{display:block; font-size:11px; font-weight:700; letter-spacing:.11em;
      text-transform:uppercase; color:var(--muted); margin:22px 0 8px}
input,textarea,select{
  width:100%; font:inherit; color:var(--ink);
  background:var(--ground); box-shadow:var(--recess);
  border:1px solid transparent; border-radius:var(--r-field); padding:14px 16px;
  appearance:none;
}
input::placeholder,textarea::placeholder{color:#a4abb8}
textarea{min-height:118px; resize:vertical; line-height:1.5}
select{background-image:linear-gradient(45deg,transparent 50%,var(--muted) 50%),
                       linear-gradient(135deg,var(--muted) 50%,transparent 50%);
       background-position:calc(100% - 21px) 24px, calc(100% - 16px) 24px;
       background-size:5px 5px, 5px 5px; background-repeat:no-repeat}
#q{margin-bottom:18px}
button.primary{
  width:100%; min-height:var(--tap); margin-top:24px; cursor:pointer;
  border-radius:17px; border:1px solid #33429a; font:inherit; font-size:16px;
  font-weight:640; letter-spacing:-.01em; color:#fff;
  background:linear-gradient(#5061c4,var(--action));
  box-shadow:0 1px 1px rgba(20,25,40,.08), 0 12px 24px -14px rgba(59,75,168,.9),
             inset 0 1px 0 rgba(255,255,255,.34);
}
button.primary:active{box-shadow:inset 0 3px 8px rgba(10,15,50,.4);
                      background:var(--action)}
button.primary[disabled]{opacity:.55}

.toast{
  position:fixed; left:20px; right:20px;
  bottom:calc(104px + env(safe-area-inset-bottom));
  max-width:520px; margin:0 auto; z-index:30;
  background:linear-gradient(var(--slab-top),var(--slab-bottom));
  border:1px solid var(--hairline); border-radius:16px; padding:14px 17px;
  font-size:14.5px; font-weight:540; color:var(--ink-soft);
  box-shadow:var(--raise-lift);
  display:flex; align-items:center; gap:11px;
  /* percentage transforms do not clear the screen on a one-line toast, so
     hide it outright rather than relying on it sliding far enough */
  opacity:0; visibility:hidden; transform:translateY(14px);
  transition:opacity .22s ease, transform .34s cubic-bezier(.2,.8,.25,1),
             visibility 0s linear .34s;
}
.toast::before{content:""; width:8px; height:8px; border-radius:50%; flex:none;
               background:var(--calm)}
.toast.err::before{background:var(--alert)}
.toast.on{opacity:1; visibility:visible; transform:none; transition-delay:0s}
.empty{color:var(--muted); text-align:center; padding:44px 20px; font-size:15px;
       background:var(--ground); box-shadow:var(--recess); border-radius:var(--r-card)}

/* --- bottom bar: frosted, so the list reads as sliding underneath it --- */
nav{
  position:fixed; left:0; right:0; bottom:0; z-index:20;
  display:grid; grid-template-columns:repeat(3,1fr);
  /* frosted, but opaque enough that a card scrolling under it does not stay
     readable through the glass */
  background:rgba(237,239,244,.93);
  -webkit-backdrop-filter:saturate(180%) blur(20px);
  backdrop-filter:saturate(180%) blur(20px);
  border-top:1px solid rgba(23,26,32,.07);
  padding:6px 8px calc(6px + env(safe-area-inset-bottom));
}
nav button{
  background:none; border:0; cursor:pointer; min-height:60px; border-radius:16px;
  font:inherit; font-size:12px; font-weight:640; letter-spacing:.02em;
  color:var(--muted); transition:color .16s ease;
  display:flex; align-items:center; justify-content:center; gap:7px;
}
nav button.on{
  color:var(--action);
  background:linear-gradient(var(--slab-top),var(--slab-bottom));
  box-shadow:var(--raise);
}
nav .badge{
  min-width:20px; height:20px; padding:0 6px; border-radius:999px; flex:none;
  background:var(--alert); color:#fff; font-size:11px; font-weight:700;
  line-height:20px; box-shadow:0 2px 6px rgba(200,57,74,.4);
}
@media (prefers-reduced-motion:reduce){
  .toast{transition:none} .view.on{animation:none}
}
</style>
</head>
<body>
<header>
  <div class="wordmark">VisionGuard <span>Operator</span></div>
  <button class="who" id="who">set name</button>
</header>

<main>
  <section class="view on" id="v-alerts">
    <h1>Detections</h1>
    <p class="hint">Mark each one once you have looked. Your answer teaches the
      system to stop raising the ones that were never anything.</p>
    <div class="strip">
      <div class="stat bad"><b id="s-untriaged">0</b><small>To check</small></div>
      <div class="stat"><b id="s-today">0</b><small>Today</small></div>
      <div class="stat"><b id="s-false">0</b><small>False</small></div>
    </div>
    <div id="alerts"></div>
  </section>

  <section class="view" id="v-gate">
    <h1>Gate register</h1>
    <p class="hint">Written by the camera, not by hand. Every plate that passes
      the gate opens a visit; the next pass closes it.</p>
    <div class="strip">
      <div class="stat"><b id="s-inside">0</b><small>Inside</small></div>
      <div class="stat warn"><b id="s-visitors">0</b><small>Visitors</small></div>
      <div class="stat bad"><b id="s-over">0</b><small>Overstay</small></div>
    </div>
    <input id="q" placeholder="Search a plate" autocomplete="off"
           inputmode="latin" aria-label="Search a plate">
    <div id="visits"></div>
  </section>

  <section class="view" id="v-notices">
    <h1>Tell the members</h1>
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
    <h2>Sent</h2>
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

// UNAUTHORIZED_VEHICLE -> "Unauthorized vehicle". Done here rather than with
// text-transform, which would also re-case anything a person typed.
const sentence = s => {
  const t = String(s || "").replace(/_/g, " ").toLowerCase();
  return t.charAt(0).toUpperCase() + t.slice(1);
};

// A zero is good news on every one of these tiles, so it should not be the
// colour that means "look at me".
function stat(sel, n){
  const el = $(sel);
  el.textContent = n;
  el.parentElement.classList.toggle("zero", !n);
}

/* ------------------------------------------------------------- alerts */
async function loadAlerts(){
  let rows = [];
  try { rows = await api("/api/events?limit=60"); }
  catch(e){ $("#alerts").innerHTML = '<p class="empty">Cannot reach the server.</p>';
            return; }
  const dayAgo = Date.now()/1000 - 86400;
  const untriaged = rows.filter(r => !r.verdict).length;
  stat("#s-untriaged", untriaged);
  stat("#s-today", rows.filter(r => r.ts > dayAgo).length);
  stat("#s-false", rows.filter(r => r.verdict === "false_alarm").length);
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
        <span class="kind">${esc(sentence(r.event_type))}</span>
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
  stat("#s-inside", open.length);
  stat("#s-visitors", open.filter(v => !v.registered).length);
  stat("#s-over", over.length);
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
    // Only what a guard might act on is tinted. A resident parked at home is
    // inside all night and must not look like a warning.
    const tone = late ? "sev-HIGH"
      : !inside ? "done" : v.registered ? "sev-LOW" : "sev-MEDIUM";
    return `<article class="card ${tone}">
      <div class="row">
        <span class="kind plate">${esc(v.plate)}</span>
        <span class="when">${inside ? "in " + clock(v.entry_ts)
                                    : clock(v.entry_ts) + " – " + clock(v.exit_ts)}</span>
      </div>
      <p class="meta">${tag} ${owner}</p>
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
    "background_color": "#e8ebf0",
    "theme_color": "#e8ebf0",
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

    # A home screen is a wall of bright squares, so the icon goes the other
    # way: deep indigo ground, one pale mark. Vertical gradient because the
    # app is lit from above everywhere else.
    top, bottom = np.array([164, 97, 80]), np.array([122, 66, 51])   # BGR
    ramp = np.linspace(0, 1, size)[:, None]
    img = (top * (1 - ramp) + bottom * ramp)[:, None, :]
    img = np.repeat(img, size, axis=1).astype(np.uint8)

    c, r = size // 2, int(size * 0.32)
    pale = (246, 244, 238)
    cv2.circle(img, (c, c), r, pale, max(2, size // 26), lineType=cv2.LINE_AA)
    # a chevron inside the ring — a watch mark, not a letter
    d = int(r * 0.46)
    pts = np.array([[c - d, c - int(d * 0.55)], [c, c + int(d * 0.75)],
                    [c + d, c - int(d * 0.55)]], dtype=np.int32)
    cv2.polylines(img, [pts], False, pale, max(2, size // 22),
                  lineType=cv2.LINE_AA)
    ok, buf = cv2.imencode(".png", img)
    if not ok:                                     # pragma: no cover
        raise RuntimeError("icon encode failed")
    return io.BytesIO(buf.tobytes()).getvalue()


def manifest_json() -> str:
    return json.dumps(MANIFEST)
