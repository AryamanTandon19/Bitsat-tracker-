"""Minimal FastAPI dashboard: live annotated MJPEG, events, registry, clip
deletion (name + reason, audited). One page, no build step."""
from __future__ import annotations

import asyncio
import logging
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from . import clips as clips_mod
from .plates import normalize_plate

log = logging.getLogger(__name__)


def create_app(ctx) -> FastAPI:
    """ctx: object with .db, .config, .workers {name: CameraWorker},
    .pipelines {name: CameraPipeline}."""
    auth_cfg = (ctx.config.get("dashboard") or {}).get("auth") or {}
    dependencies = []
    if auth_cfg.get("enabled", True):
        user = str(auth_cfg.get("username", "admin"))
        password = str(auth_cfg.get("password", ""))
        if not password:
            log.warning("dashboard auth enabled but no password set — "
                        "using 'changeme'. Set dashboard.auth.password!")
            password = "changeme"
        basic = HTTPBasic()

        def check_auth(creds: HTTPBasicCredentials = Depends(basic)):
            ok = (secrets.compare_digest(creds.username.encode(), user.encode())
                  and secrets.compare_digest(creds.password.encode(),
                                             password.encode()))
            if not ok:
                raise HTTPException(401, "invalid credentials",
                                    headers={"WWW-Authenticate": "Basic"})

        dependencies = [Depends(check_auth)]

    app = FastAPI(title="Society AI Watchdog", dependencies=dependencies)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return PAGE.replace("__CAMERAS__",
                            ",".join(f'"{n}"' for n in ctx.workers))

    # ---------------------------------------------------------- live view
    async def mjpeg_gen(camera: str):
        boundary = b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
        while True:
            jpg = None
            pipe = ctx.pipelines.get(camera)
            if pipe is not None:
                jpg = pipe.annotated_jpeg
            if jpg is None:
                worker = ctx.workers.get(camera)
                if worker:
                    snap = worker.buffer_snapshot()
                    if snap:
                        jpg = snap[-1][1]
            if jpg:
                yield boundary + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n"
            await asyncio.sleep(0.15)

    @app.get("/stream/{camera}")
    def stream(camera: str):
        if camera not in ctx.workers:
            raise HTTPException(404, "unknown camera")
        return StreamingResponse(mjpeg_gen(camera),
                                 media_type="multipart/x-mixed-replace; boundary=frame")

    # -------------------------------------------------------------- events
    @app.get("/api/events")
    def events(limit: int = 100):
        return ctx.db.recent_events(limit)

    @app.get("/api/status")
    def status():
        return {n: {"online": w.online,
                    "last_frame_age_s": None if not w.last_frame_ts else
                    round(__import__("time").time() - w.last_frame_ts, 1)}
                for n, w in ctx.workers.items()}

    # ------------------------------------------------------------ registry
    @app.get("/api/registry")
    def registry():
        return ctx.db.list_vehicles()

    @app.post("/api/registry")
    def registry_add(plate: str = Form(...), owner_name: str = Form(""),
                     owner_phone: str = Form(""), flat_number: str = Form(""),
                     telegram_chat_id: str = Form("")):
        p = normalize_plate(plate)
        if not p:
            raise HTTPException(400, "invalid plate")
        ctx.db.add_vehicle(p, owner_name, owner_phone, flat_number,
                           telegram_chat_id, actor="dashboard")
        return {"ok": True, "plate": p}

    @app.delete("/api/registry/{plate}")
    def registry_remove(plate: str):
        if not ctx.db.remove_vehicle(normalize_plate(plate), actor="dashboard"):
            raise HTTPException(404, "plate not found")
        return {"ok": True}

    # --------------------------------------------------------------- clips
    @app.get("/clips/{clip_id}")
    def clip_file(clip_id: int):
        clip = ctx.db.get_clip(clip_id)
        if not clip or clip["deleted"] or not Path(clip["path"]).exists():
            raise HTTPException(404, "clip not available")
        return FileResponse(clip["path"], media_type="video/mp4")

    @app.post("/api/clips/{clip_id}/delete")
    def clip_delete(clip_id: int, name: str = Form(...), reason: str = Form(...)):
        if not name.strip() or not reason.strip():
            raise HTTPException(400, "name and reason are required")
        if not clips_mod.delete_clip_file(ctx.db, clip_id, name.strip(), reason.strip()):
            raise HTTPException(404, "clip not found or already deleted")
        return {"ok": True}

    # ------------------------------------------------------------ audit
    @app.get("/api/audit")
    def audit(limit: int = 200):
        rows = ctx.db.audit_rows()
        return rows[-limit:]

    return app


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Society AI Watchdog</title>
<style>
 body{font-family:system-ui,sans-serif;background:#111;color:#ddd;margin:0;padding:16px}
 h1{font-size:20px} h2{font-size:15px;color:#9ac;margin:18px 0 6px}
 .cams{display:flex;gap:12px;flex-wrap:wrap}
 .cam img{max-width:480px;width:100%;border:1px solid #333;border-radius:6px}
 table{border-collapse:collapse;width:100%;font-size:13px}
 td,th{border-bottom:1px solid #2a2a2a;padding:4px 8px;text-align:left}
 tr.HIGH td:first-child{color:#f66} tr.MEDIUM td:first-child{color:#fc6}
 tr.LOW td:first-child{color:#9ad}
 a{color:#8cf} button{background:#334;color:#ddd;border:1px solid #556;
 border-radius:4px;padding:2px 8px;cursor:pointer}
 input{background:#222;color:#ddd;border:1px solid #444;border-radius:4px;padding:4px}
 .row{display:flex;gap:8px;flex-wrap:wrap;margin:6px 0}
</style></head><body>
<h1>&#128680; Society AI Watchdog</h1>
<div class="cams" id="cams"></div>
<h2>Recent events</h2>
<table id="events"><thead><tr><th>Severity</th><th>Time</th><th>Camera</th>
<th>Type</th><th>Plate</th><th>Description</th><th>Clip</th></tr></thead>
<tbody></tbody></table>
<h2>Vehicle registry</h2>
<div class="row">
 <input id="r_plate" placeholder="Plate (WB02AB1234)">
 <input id="r_owner" placeholder="Owner name">
 <input id="r_phone" placeholder="Phone">
 <input id="r_flat" placeholder="Flat">
 <input id="r_chat" placeholder="Telegram chat id (optional)">
 <button onclick="addPlate()">Add</button>
</div>
<table id="registry"><thead><tr><th>Plate</th><th>Owner</th><th>Phone</th>
<th>Flat</th><th></th></tr></thead><tbody></tbody></table>
<script>
const cams=[__CAMERAS__];
document.getElementById('cams').innerHTML=cams.map(c=>
 `<div class="cam"><div>${c}</div><img src="/stream/${c}"></div>`).join('');
function esc(s){return (s??'').toString().replace(/[&<>"]/g,
 c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
async function refresh(){
 const evs=await (await fetch('/api/events?limit=100')).json();
 document.querySelector('#events tbody').innerHTML=evs.map(e=>{
  const t=new Date(e.ts*1000).toLocaleString();
  let clip='—';
  if(e.clip_id&&!e.clip_deleted) clip=`<a href="/clips/${e.clip_id}" target="_blank">view</a>
   <button onclick="delClip(${e.clip_id})">delete</button>`;
  else if(e.clip_deleted) clip='deleted';
  return `<tr class="${e.severity}"><td>${e.severity}</td><td>${t}</td>
  <td>${esc(e.camera)}</td><td>${esc(e.event_type)}</td><td>${esc(e.plate)||'—'}</td>
  <td>${esc(e.description)}</td><td>${clip}</td></tr>`;}).join('');
 const reg=await (await fetch('/api/registry')).json();
 document.querySelector('#registry tbody').innerHTML=reg.map(v=>
  `<tr><td>${esc(v.plate_number)}</td><td>${esc(v.owner_name)}</td>
   <td>${esc(v.owner_phone)}</td><td>${esc(v.flat_number)}</td>
   <td><button onclick="rmPlate('${esc(v.plate_number)}')">remove</button></td></tr>`).join('');
}
async function addPlate(){
 const f=new FormData();
 f.append('plate',document.getElementById('r_plate').value);
 f.append('owner_name',document.getElementById('r_owner').value);
 f.append('owner_phone',document.getElementById('r_phone').value);
 f.append('flat_number',document.getElementById('r_flat').value);
 f.append('telegram_chat_id',document.getElementById('r_chat').value);
 await fetch('/api/registry',{method:'POST',body:f}); refresh();
}
async function rmPlate(p){
 if(!confirm('Remove '+p+' from registry?'))return;
 await fetch('/api/registry/'+p,{method:'DELETE'}); refresh();
}
async function delClip(id){
 const name=prompt('Your name (required for the audit log):'); if(!name)return;
 const reason=prompt('Reason for deletion:'); if(!reason)return;
 const f=new FormData(); f.append('name',name); f.append('reason',reason);
 const r=await fetch('/api/clips/'+id+'/delete',{method:'POST',body:f});
 if(!r.ok)alert('delete failed'); refresh();
}
refresh(); setInterval(refresh,5000);
</script></body></html>"""
