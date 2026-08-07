"""The resident owner app — a small, installable mobile web app.

A resident opens a magic link (minted by the committee from the registry) and
sees only what concerns them: alerts about *their* vehicle, the evidence clip
for each, their gate entry/exit history, and one tap to confirm real or false.
That last tap is the same signal the whole system learns from.

This module is just the front end (one HTML page + a web-app manifest + a
pass-through service worker so it installs to a phone's home screen). Every byte
of data it shows comes from the strictly plate-scoped `/api/owner/*` endpoints
in app/dashboard.py — the page never has access to anything the token behind it
does not own.

Kept as a string constant, matching app/operator.py and app/train.py.
"""
from __future__ import annotations

ACCENT = "#2dd4bf"

MANIFEST = {
    "name": "VisionGuard — My Security",
    "short_name": "VisionGuard",
    "start_url": "/owner",
    "scope": "/owner",
    "display": "standalone",
    "background_color": "#0b1220",
    "theme_color": "#0b1220",
    "icons": [
        {"src": "/owner/icon.svg", "sizes": "any", "type": "image/svg+xml",
         "purpose": "any maskable"},
    ],
}

# A pass-through worker: it exists so the app is installable, and deliberately
# does NOT cache — resident data is private and per-token, and a shared cache is
# exactly how one resident would end up seeing another's alerts.
SW = """
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => self.clients.claim());
self.addEventListener('fetch', e => { return; });  // always hit the network
"""

ICON_SVG = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
<rect width="512" height="512" rx="112" fill="#0b1220"/>
<path d="M256 96l128 48v104c0 82-54 150-128 176-74-26-128-94-128-176V144z"
 fill="none" stroke="{ACCENT}" stroke-width="28" stroke-linejoin="round"/>
<circle cx="256" cy="238" r="34" fill="{ACCENT}"/>
<path d="M256 272v70" stroke="{ACCENT}" stroke-width="28" stroke-linecap="round"/>
</svg>"""

PAGE = """<!doctype html><html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0b1220">
<link rel="manifest" href="/owner/manifest.webmanifest">
<link rel="apple-touch-icon" href="/owner/icon.svg">
<title>VisionGuard — My Security</title>
<style>
 :root{--bg:#0b1220;--card:#131c2e;--line:#22304a;--txt:#e7edf6;--dim:#93a1b8;
  --accent:#2dd4bf;--high:#ef4444;--med:#f59e0b;--low:#38bdf8;--ok:#34d399}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--txt);
  font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  -webkit-font-smoothing:antialiased}
 .wrap{max-width:640px;margin:0 auto;padding:16px;padding-bottom:40px}
 header{display:flex;align-items:center;gap:12px;padding:8px 4px 16px}
 header .logo{width:34px;height:34px;flex:0 0 34px}
 header h1{font-size:16px;margin:0;letter-spacing:.2px}
 header .sub{color:var(--dim);font-size:12px;margin-top:2px}
 .id-card{background:linear-gradient(135deg,#16233b,#0e1626);border:1px solid var(--line);
  border-radius:16px;padding:16px;margin-bottom:16px}
 .plate{font-size:22px;font-weight:700;letter-spacing:2px;
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
 .id-card .who{color:var(--dim);font-size:13px;margin-top:4px}
 .tabs{display:flex;gap:8px;margin-bottom:14px}
 .tab{flex:1;text-align:center;padding:9px;border-radius:10px;border:1px solid var(--line);
  background:transparent;color:var(--dim);font-weight:600;cursor:pointer}
 .tab.on{background:var(--card);color:var(--txt);border-color:var(--accent)}
 .card{background:var(--card);border:1px solid var(--line);border-radius:14px;
  padding:14px;margin-bottom:12px}
 .row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
 .chip{font-size:11px;font-weight:700;padding:3px 8px;border-radius:999px;
  text-transform:uppercase;letter-spacing:.4px}
 .chip.HIGH{background:rgba(239,68,68,.16);color:#fca5a5}
 .chip.MEDIUM{background:rgba(245,158,11,.16);color:#fcd34d}
 .chip.LOW{background:rgba(56,189,248,.16);color:#7dd3fc}
 .title{font-weight:650;margin:8px 0 2px}
 .meta{color:var(--dim);font-size:13px}
 .ai{margin-top:8px;font-size:13px;color:#cbd5e1;border-left:2px solid var(--accent);
  padding-left:10px}
 video{width:100%;border-radius:10px;margin-top:10px;background:#000}
 .acts{display:flex;gap:8px;margin-top:12px}
 .btn{flex:1;padding:10px;border-radius:10px;border:1px solid var(--line);
  background:var(--card);color:var(--txt);font-weight:600;cursor:pointer}
 .btn.real{border-color:var(--ok);color:#a7f3d0}
 .btn.false{border-color:var(--high);color:#fca5a5}
 .verdict{margin-top:12px;font-size:13px;color:var(--dim)}
 .verdict b{color:var(--txt)}
 .visit{display:flex;justify-content:space-between;gap:10px;padding:11px 0;
  border-bottom:1px solid var(--line)}
 .visit:last-child{border-bottom:none}
 .visit .t{color:var(--dim);font-size:12px}
 .empty{color:var(--dim);text-align:center;padding:36px 12px}
 .login{max-width:420px;margin:8vh auto 0;padding:24px}
 .login .logo{width:56px;height:56px;margin:0 auto 14px;display:block}
 .login h2{text-align:center;margin:0 0 4px}
 .login p{text-align:center;color:var(--dim);margin:0 0 18px}
 .login input{width:100%;padding:12px;border-radius:10px;border:1px solid var(--line);
  background:#0e1626;color:var(--txt);font-size:15px}
 .login button{width:100%;margin-top:12px;padding:12px;border-radius:10px;border:none;
  background:var(--accent);color:#03201c;font-weight:700;font-size:15px;cursor:pointer}
 .msg{color:#fca5a5;text-align:center;margin-top:12px;font-size:13px;min-height:18px}
 a.link{color:var(--accent);text-decoration:none}
</style></head><body>

<div id="login" class="login" style="display:none">
 __ICON__
 <h2>My Security</h2>
 <p>Open the access link your society sent you, or paste it below.</p>
 <input id="tok" placeholder="paste your access link" autocomplete="off">
 <button onclick="doLogin()">Open</button>
 <div id="loginmsg" class="msg"></div>
</div>

<div id="app" class="wrap" style="display:none">
 <header>
  __ICON__
  <div><h1>VisionGuard</h1><div class="sub">your home &amp; vehicle</div></div>
  <span style="flex:1"></span>
  <a class="link" href="#" onclick="doLogout();return false">Sign out</a>
 </header>
 <div class="id-card">
  <div class="plate" id="p-plate">—</div>
  <div class="who" id="p-who"></div>
 </div>
 <div class="tabs">
  <button class="tab on" id="tab-alerts" onclick="show('alerts')">Alerts</button>
  <button class="tab" id="tab-visits" onclick="show('visits')">Gate history</button>
 </div>
 <div id="view-alerts"></div>
 <div id="view-visits" style="display:none"></div>
</div>

<script>
const ICON=`__ICONRAW__`;
function esc(s){return (s==null?'':String(s)).replace(/[&<>"]/g,
 c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function when(ts){try{return new Date(ts*1000).toLocaleString('en-IN',
 {day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',hour12:true});}
 catch(e){return '';}}
async function api(path,opts){const r=await fetch(path,opts||{});
 if(!r.ok) throw new Error((await r.json().catch(()=>({}))).detail||('failed '+r.status));
 return r.json();}
function form(o){return {method:'POST',body:new URLSearchParams(o)};}

function extractToken(s){
 s=(s||'').trim(); if(!s) return '';
 const m=s.match(/[?&]token=([^&\\s]+)/); if(m) return decodeURIComponent(m[1]);
 return s;  // they pasted the bare code
}
async function doLogin(){
 const msg=document.getElementById('loginmsg');
 const tok=extractToken(document.getElementById('tok').value);
 if(!tok){msg.textContent='paste your access link first';return;}
 try{ await api('/api/owner/login',form({token:tok})); location.replace('/owner'); }
 catch(e){ msg.textContent='that link is not valid or has expired'; }
}
async function doLogout(){ await fetch('/api/owner/logout',{method:'POST'});
 location.replace('/owner'); }

function show(which){
 document.getElementById('view-alerts').style.display=which==='alerts'?'':'none';
 document.getElementById('view-visits').style.display=which==='visits'?'':'none';
 document.getElementById('tab-alerts').classList.toggle('on',which==='alerts');
 document.getElementById('tab-visits').classList.toggle('on',which==='visits');
 if(which==='visits') loadVisits();
}

function alertCard(a){
 const sev=esc(a.severity||'LOW');
 const clip=a.has_clip?`<video controls preload="none" playsinline
   poster="" src="/owner/clip/${a.id}"></video>`:
   `<div class="meta" style="margin-top:8px">No clip stored for this alert.</div>`;
 let foot;
 if(a.verdict==='false_alarm') foot=`<div class="verdict">You marked this a
   <b>false alarm</b> — the clip was removed and the system is learning from it.</div>`;
 else if(a.verdict==='real'||a.verdict==='correct') foot=`<div class="verdict">
   You confirmed this was <b>real</b>.</div>`;
 else foot=`<div class="acts">
   <button class="btn real" onclick="verdict(${a.id},'real')">✓ Real</button>
   <button class="btn false" onclick="verdict(${a.id},'false_alarm')">✕ False alarm</button>
  </div>`;
 return `<div class="card">
  <div class="row"><span class="chip ${sev}">${sev}</span>
   <span class="meta">${esc(when(a.ts))}</span></div>
  <div class="title">${esc(a.description||a.event_type||'Alert')}</div>
  <div class="meta">📍 ${esc(a.location||a.camera||'')}</div>
  ${a.ai_summary?`<div class="ai">${esc(a.ai_summary)}</div>`:''}
  ${clip}${foot}</div>`;
}
async function loadAlerts(){
 const el=document.getElementById('view-alerts');
 try{ const rows=await api('/api/owner/alerts');
  el.innerHTML=rows.length?rows.map(alertCard).join(''):
   `<div class="empty">No alerts about your vehicle. That's good news.</div>`;
 }catch(e){ el.innerHTML=`<div class="empty">${esc(e.message)}</div>`; }
}
async function loadVisits(){
 const el=document.getElementById('view-visits');
 try{ const rows=await api('/api/owner/visits');
  el.innerHTML=rows.length?`<div class="card">`+rows.map(v=>`<div class="visit">
    <div><b>${v.exit_ts?'Visit':'Inside now'}</b>
     <div class="t">in ${esc(when(v.entry_ts))}${v.exit_ts?' · out '+esc(when(v.exit_ts)):''}</div></div>
   </div>`).join('')+`</div>`:
   `<div class="empty">No gate records for your vehicle yet.</div>`;
 }catch(e){ el.innerHTML=`<div class="empty">${esc(e.message)}</div>`; }
}
async function verdict(id,v){
 try{ await api('/api/owner/alerts/'+id+'/feedback',form({verdict:v})); loadAlerts(); }
 catch(e){ alert(e.message); }
}

async function boot(){
 document.querySelectorAll('.icon-slot').forEach(s=>s.innerHTML=ICON);
 const params=new URLSearchParams(location.search);
 const tok=params.get('token');
 if(tok){
  try{ await api('/api/owner/login',form({token:tok}));
   history.replaceState({},'', '/owner'); }   // keep the secret out of the address bar
  catch(e){}
 }
 let me=null; try{ me=await api('/api/owner/me'); }catch(e){}
 if(!me){ document.getElementById('login').style.display='block'; return; }
 document.getElementById('app').style.display='block';
 document.getElementById('p-plate').textContent=me.plate||'—';
 const bits=[]; if(me.owner_name) bits.push(me.owner_name);
 if(me.flat_number) bits.push('Flat '+me.flat_number);
 document.getElementById('p-who').textContent=bits.join(' · ');
 loadAlerts();
}
if('serviceWorker' in navigator){
 navigator.serviceWorker.register('/owner/sw.js').catch(()=>{}); }
boot();
</script></body></html>"""


def page() -> str:
    """The HTML with the inline icon substituted into its slots."""
    icon = f'<div class="icon-slot logo">{ICON_SVG}</div>'
    return (PAGE.replace("__ICON__", icon)
            .replace("__ICONRAW__", ICON_SVG.replace("`", "'")))
