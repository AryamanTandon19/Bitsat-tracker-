"""FastAPI dashboard: live annotated MJPEG, events, registry, clip
deletion (name + reason, audited). One page, no build step.

Frontend: AEGIS glass theme (from the user's Stitch design), rebuilt as
self-contained CSS — no Tailwind CDN, so the dashboard still renders when
the internet is down. Only the Google font is fetched (graceful fallback).
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import tempfile
from pathlib import Path

from fastapi import (Depends, FastAPI, File, Form, HTTPException, Request,
                     UploadFile)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from . import assistant as assistant_mod
from . import clips as clips_mod
from .plates import normalize_plate

log = logging.getLogger(__name__)

ALLOWED_VIDEO_EXT = {".mp4", ".avi", ".mov", ".mkv", ".dav", ".m4v", ".webm"}


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

    # --------------------------------------------- upload & analyze video
    @app.post("/api/analyze")
    async def analyze_upload(file: UploadFile = File(...),
                             zones_from: str = Form(""),
                             ai_review: str = Form("")):
        analyzer = getattr(ctx, "analyzer", None)
        if analyzer is None:
            raise HTTPException(503, "video analysis is disabled")
        max_mb = int((ctx.config.get("analyze") or {}).get("max_upload_mb", 300))
        suffix = Path(file.filename or "upload.mp4").suffix.lower() or ".mp4"
        if suffix not in ALLOWED_VIDEO_EXT:
            raise HTTPException(400, f"unsupported file type '{suffix}' — "
                                     f"allowed: {', '.join(sorted(ALLOWED_VIDEO_EXT))}")
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        size = 0
        try:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > max_mb * 1024 * 1024:
                    tmp.close()
                    Path(tmp.name).unlink(missing_ok=True)
                    raise HTTPException(413, f"file exceeds {max_mb} MB")
                tmp.write(chunk)
        finally:
            tmp.close()
        # zones: reuse a configured camera's zones, if requested
        zones = {}
        for cam in ctx.config.get("cameras", []):
            if cam.get("name") == zones_from:
                zones = cam.get("zones", {})
                break
        registry = ctx.db.registry_plates()
        job = analyzer.submit(tmp.name, file.filename or "upload.mp4",
                              zones=zones, registry=registry,
                              delete_source=True,  # don't retain raw footage
                              ai_review=(ai_review in ("1", "true", "on", "yes")))
        return {"job_id": job.id}

    @app.get("/api/analyze/{job_id}")
    def analyze_status(job_id: str):
        analyzer = getattr(ctx, "analyzer", None)
        job = analyzer.get(job_id) if analyzer else None
        if job is None:
            raise HTTPException(404, "job not found")
        return job.public()

    @app.get("/api/analyze/{job_id}/video")
    def analyze_video_file(job_id: str):
        analyzer = getattr(ctx, "analyzer", None)
        job = analyzer.get(job_id) if analyzer else None
        if job is None or not job.annotated_path or \
                not Path(job.annotated_path).exists():
            raise HTTPException(404, "annotated video not available")
        return FileResponse(job.annotated_path, media_type="video/mp4")

    @app.get("/api/cameras")
    def cameras():
        return [c.get("name") for c in ctx.config.get("cameras", [])]

    # ------------------------------------------------ Claude tuning chatbot
    @app.post("/api/assistant")
    async def assistant_chat(request: Request):
        asst = getattr(ctx, "assistant", None)
        body = await request.json()
        message = (body.get("message") or "").strip()
        history = body.get("history") or []
        if asst is None or not asst.available:
            return JSONResponse({
                "reply": "Assistant is not configured. Set assistant.enabled: "
                         "true in config.yaml and export ANTHROPIC_API_KEY.",
                "patch": {}, "explanation": ""})
        if not message:
            raise HTTPException(400, "empty message")
        result = asst.chat(message, ctx.config, ctx.db.recent_events(30), history)
        # validate the proposed patch so the UI only offers safe changes
        clean, rejected = assistant_mod.validate_patch(ctx.config, result.get("patch"))
        result["patch"] = clean
        result["rejected"] = rejected
        return result

    @app.post("/api/assistant/apply")
    async def assistant_apply(request: Request):
        asst = getattr(ctx, "assistant", None)
        body = await request.json()
        patch = body.get("patch") or {}
        result = assistant_mod.apply_patch(
            ctx.config_path, ctx.config, patch, db=ctx.db, actor="dashboard")
        return result

    # -------------------------------------------------------- cost meter
    @app.get("/api/costs")
    def costs():
        usd_to_inr = float((ctx.config.get("ai_review") or {})
                           .get("usd_to_inr", 90.0))
        summary = ctx.db.ai_cost_summary()
        for key in ("last_24h", "last_30d"):
            summary[key]["cost_inr"] = round(summary[key]["cost_usd"] * usd_to_inr, 2)
        for row in summary["per_camera_30d"]:
            row["cost_inr"] = round(row["cost_usd"] * usd_to_inr, 2)
        summary["ai_review_enabled"] = bool(
            (ctx.config.get("ai_review") or {}).get("enabled"))
        return summary

    # ------------------------------------------------------------ audit
    @app.get("/api/audit")
    def audit(limit: int = 200):
        rows = ctx.db.audit_rows()
        return rows[-limit:]

    return app


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VisionGuard | Society AI Watchdog</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@500;600;700;800&family=Figtree:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{
 --bg:#f5f3ee; --surface:#ffffff; --surface-2:#efeae1;
 --text:#000000; --muted:#6b6660; --muted-soft:#8f8a83;
 --cyan:#b8654a; --cyan-dim:#a05541;
 --purple:#000000; --purple-lt:#3d3d3d;
 --red:#b3423a; --red-deep:#8f2f28; --amber:#c98a2b; --green:#4a7c59;
 --border:rgba(0,0,0,.08); --border-strong:rgba(0,0,0,.16);
 --shadow:0 1px 2px rgba(0,0,0,.04),0 8px 24px -12px rgba(0,0,0,.12);
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%}
body{
 font-family:'Figtree',system-ui,-apple-system,sans-serif;
 background-color:var(--bg); color:var(--text);
 font-feature-settings:"cv02","cv03","cv04","cv11";
 background-image:
  radial-gradient(1200px 600px at 10% -10%,color-mix(in oklab,#b8654a 8%,transparent),transparent 60%),
  radial-gradient(900px 500px at 100% 0%,color-mix(in oklab,#c98a2b 6%,transparent),transparent 60%);
 background-attachment:fixed; min-height:100vh; overflow-x:hidden;
}
h1,h2,h3,h4{font-family:'Outfit',system-ui,sans-serif;letter-spacing:-.01em;text-wrap:balance}
.mesh{display:none}
::-webkit-scrollbar{width:8px;height:8px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:rgba(0,0,0,.18);border-radius:10px}
::-webkit-scrollbar-thumb:hover{background:rgba(0,0,0,.32)}
::selection{background:color-mix(in oklab,var(--cyan) 35%,transparent);color:var(--text)}
:focus-visible{outline:2px solid color-mix(in oklab,var(--cyan) 65%,transparent);outline-offset:2px;border-radius:4px}

.glass{background:var(--surface);border:1px solid var(--border);border-radius:16px;box-shadow:var(--shadow)}
.glass-raised{background:var(--surface);border:1px solid var(--border-strong)}

/* top bar */
header{position:fixed;top:0;left:0;right:0;z-index:50;height:56px;
 display:flex;align-items:center;justify-content:space-between;padding:0 20px;
 background:color-mix(in oklab,var(--bg) 85%,transparent);backdrop-filter:blur(10px);
 border-bottom:1px solid var(--border)}
.brand{display:flex;align-items:baseline;gap:10px}
.brand b{color:var(--text);font-family:'Outfit',sans-serif;font-size:20px;font-weight:800;letter-spacing:-.01em}
.brand span{color:var(--muted-soft);font-size:10px;text-transform:uppercase;letter-spacing:.18em}
.top-right{display:flex;align-items:center;gap:12px;font-size:12px;color:var(--muted)}
.spend-chip{display:flex;align-items:center;gap:6px;padding:4px 12px;border-radius:999px;
 background:color-mix(in oklab,var(--cyan) 10%,transparent);
 border:1px solid color-mix(in oklab,var(--cyan) 30%,transparent);color:var(--cyan-dim);
 font-weight:600;cursor:pointer}
#clock{font-variant-numeric:tabular-nums}
.user-chip{color:var(--muted);font-size:12px}
.signout{background:none;border:none;color:var(--muted);font-family:inherit;font-size:12px;
 font-weight:600;padding:4px 10px;border-radius:999px;cursor:pointer;transition:all .2s}
.signout:hover{background:rgba(0,0,0,.05);color:var(--text)}

/* sidebar */
aside{position:fixed;left:8px;top:64px;bottom:8px;width:230px;z-index:40;
 display:flex;flex-direction:column;border-radius:14px;background:var(--surface);
 border:1px solid var(--border);box-shadow:var(--shadow);overflow:hidden}
.aside-head{padding:16px 18px;border-bottom:1px solid var(--border)}
.aside-head b{color:var(--text);font-family:'Outfit',sans-serif;font-size:15px;font-weight:800;display:block}
.aside-head span{font-size:10px;color:var(--muted-soft);text-transform:uppercase;letter-spacing:.2em}
nav{flex:1;padding:10px 8px;overflow-y:auto}
nav a{display:flex;align-items:center;gap:12px;padding:10px 14px;margin:3px 4px;
 border-radius:10px;color:var(--muted);text-decoration:none;font-size:13px;
 font-weight:600;letter-spacing:.01em;cursor:pointer;transition:all .2s}
nav a:hover{background:rgba(0,0,0,.04);color:var(--text)}
nav a.active{background:color-mix(in oklab,var(--cyan) 14%,transparent);color:var(--text)}
nav a .ic{width:20px;text-align:center;font-size:15px}
.aside-foot{padding:12px 16px;border-top:1px solid var(--border);
 font-size:10px;color:var(--muted);display:flex;align-items:center;gap:8px;letter-spacing:.06em}
.pulse-dot{width:8px;height:8px;border-radius:50%;background:var(--green);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:.55}50%{opacity:1}}

/* main */
main{margin-left:250px;padding:76px 20px 40px}
@media(max-width:860px){aside{display:none}main{margin-left:0}}
body:not(.authed) .app-chrome{display:none!important}
.view{display:none}
.view.active{display:block;animation:fadein .3s ease}
@keyframes fadein{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.page-head{display:flex;align-items:flex-end;justify-content:space-between;
 flex-wrap:wrap;gap:12px;margin-bottom:20px}
.page-head h1{font-size:26px;font-weight:800;letter-spacing:-.01em;color:var(--text)}
.page-head p{color:var(--muted);font-size:13px;margin-top:4px;max-width:560px;line-height:1.55}
.live-pill{display:inline-flex;align-items:center;gap:6px;padding:3px 12px;border-radius:999px;
 background:color-mix(in oklab,var(--cyan) 12%,transparent);
 border:1px solid color-mix(in oklab,var(--cyan) 30%,transparent);color:var(--cyan-dim);
 font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;margin-bottom:6px}
.live-pill i{width:7px;height:7px;border-radius:50%;background:var(--cyan);animation:pulse 2s infinite}

/* cameras */
.cam-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:18px}
.cam-card{padding:14px;position:relative}
.cam-card img{width:100%;aspect-ratio:16/9;object-fit:contain;border-radius:12px;
 background:#111;border:1px solid var(--border)}
.cam-meta{display:flex;align-items:center;justify-content:space-between;margin-top:10px}
.cam-name{font-weight:700;font-size:13px;color:var(--text)}
.chip{display:inline-flex;align-items:center;gap:6px;padding:2px 10px;border-radius:999px;
 font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;border:1px solid transparent}
.chip i{width:7px;height:7px;border-radius:50%}
.chip.ok{background:color-mix(in oklab,var(--green) 14%,transparent);color:var(--green);
 border-color:color-mix(in oklab,var(--green) 30%,transparent)}
.chip.ok i{background:var(--green)}
.chip.bad{background:color-mix(in oklab,var(--red) 12%,transparent);color:var(--red-deep);
 border-color:color-mix(in oklab,var(--red) 30%,transparent)}
.chip.bad i{background:var(--red)}
.chip.HIGH{background:color-mix(in oklab,var(--red) 12%,transparent);color:var(--red-deep);
 border-color:color-mix(in oklab,var(--red) 30%,transparent)}
.chip.MEDIUM{background:color-mix(in oklab,var(--amber) 14%,transparent);color:var(--amber);
 border-color:color-mix(in oklab,var(--amber) 30%,transparent)}
.chip.LOW{background:color-mix(in oklab,var(--cyan) 12%,transparent);color:var(--cyan-dim);
 border-color:color-mix(in oklab,var(--cyan) 28%,transparent)}

/* live layout with intel panel */
.live-wrap{display:grid;grid-template-columns:1fr 300px;gap:18px}
@media(max-width:1100px){.live-wrap{grid-template-columns:1fr}}
.intel{display:flex;flex-direction:column;overflow:hidden;max-height:calc(100vh - 150px);
 position:sticky;top:76px}
.intel-head{padding:14px 16px;border-bottom:1px solid var(--border);background:var(--surface-2);
 display:flex;justify-content:space-between;align-items:center}
.intel-head b{font-size:15px;font-family:'Outfit',sans-serif;font-weight:700}
.intel-body{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:14px}
.intel-item{display:flex;gap:10px}
.intel-ic{width:36px;height:36px;border-radius:10px;flex-shrink:0;display:flex;
 align-items:center;justify-content:center;font-size:16px;background:var(--surface-2);
 border:1px solid var(--border)}
.intel-ic.HIGH{background:color-mix(in oklab,var(--red) 12%,transparent);
 border-color:color-mix(in oklab,var(--red) 30%,transparent)}
.intel-ic.MEDIUM{background:color-mix(in oklab,var(--amber) 12%,transparent);
 border-color:color-mix(in oklab,var(--amber) 28%,transparent)}
.intel-item .t{font-size:12px;line-height:1.4;color:var(--text)}
.intel-item .m{font-size:10px;color:var(--muted);margin-top:2px;display:flex;gap:6px;flex-wrap:wrap}
.tag{padding:1px 7px;background:var(--surface-2);border-radius:5px;font-size:9px;color:var(--muted)}

/* forensic lab */
.dropzone{position:relative;border-radius:16px;border:1px dashed var(--border-strong);
 background:var(--surface);padding:44px 20px;text-align:center;transition:all .25s;cursor:pointer}
.dropzone:hover{border-color:var(--cyan)}
.dropzone.drag{background:color-mix(in oklab,var(--cyan) 8%,transparent);border-color:var(--cyan)}
.drop-orb{width:96px;height:96px;margin:0 auto 18px;border-radius:50%;
 background:color-mix(in oklab,var(--cyan) 12%,white);border:1px solid var(--border);
 display:flex;align-items:center;justify-content:center;font-size:40px}
.dropzone h2{font-size:19px;margin-bottom:6px}
.dropzone p{color:var(--muted);font-size:12px;margin-bottom:16px}
.btn{display:inline-flex;align-items:center;gap:8px;padding:10px 22px;border:none;
 border-radius:999px;font-family:inherit;font-size:12px;font-weight:700;letter-spacing:.08em;
 text-transform:uppercase;cursor:pointer;background:var(--cyan);color:#fff;transition:all .2s}
.btn:hover{background:var(--cyan-dim)}
.btn.grad{background:var(--cyan);color:#fff}
.btn.grad:hover{background:var(--cyan-dim)}
.btn.ghost{background:var(--surface-2);color:var(--muted);border:1px solid var(--border)}
.btn.ghost:hover{color:var(--text)}
.lab-controls{display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin:16px 0}
.switch{position:relative;display:inline-flex;align-items:center;gap:10px;cursor:pointer;
 font-size:13px;color:var(--muted);user-select:none}
.switch input{display:none}
.switch .track{width:40px;height:22px;border-radius:999px;background:var(--surface-2);
 border:1px solid var(--border-strong);position:relative;transition:background .3s}
.switch .track::after{content:"";position:absolute;top:2px;left:2px;width:16px;height:16px;
 border-radius:50%;background:#fff;box-shadow:0 1px 2px rgba(0,0,0,.25);transition:all .3s}
.switch input:checked+.track{background:color-mix(in oklab,var(--cyan) 30%,transparent)}
.switch input:checked+.track::after{left:20px;background:var(--cyan)}
select,input[type=text],input[type=password]{background:var(--surface);color:var(--text);
 border:1px solid var(--border-strong);border-radius:10px;padding:9px 12px;
 font-family:inherit;font-size:13px;outline:none;transition:border-color .2s}
select:focus,input[type=text]:focus,input[type=password]:focus{border-color:var(--cyan);
 box-shadow:0 0 0 2px color-mix(in oklab,var(--cyan) 18%,transparent)}
select option{background:#fff}
#up_player{width:100%;max-width:860px;border-radius:14px;border:1px solid var(--border-strong);
 box-shadow:var(--shadow);display:none;margin:14px 0;background:#000}
.status-line{font-size:12px;color:var(--muted)}

/* AI threat banner */
.threat-banner{display:none;margin:14px 0;padding:14px 18px;border-radius:14px;
 background:color-mix(in oklab,var(--red) 8%,white);
 border:1px solid color-mix(in oklab,var(--red) 30%,transparent);align-items:center;gap:14px}
.threat-banner .warn{width:42px;height:42px;border-radius:10px;flex-shrink:0;
 background:color-mix(in oklab,var(--red) 14%,white);display:flex;align-items:center;
 justify-content:center;font-size:20px;animation:pulse 1.6s infinite}
.threat-banner h3{color:var(--red-deep);font-size:15px;text-transform:uppercase;letter-spacing:.04em}
.threat-banner p{color:var(--muted);font-size:11px;margin-top:2px}

/* incident cards */
#incidents_box{display:none;margin-top:16px}
.inc-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.inc-card{padding:16px 18px;border-radius:14px;background:var(--surface);
 border:1px solid var(--border);position:relative;overflow:hidden;
 transition:transform .2s,box-shadow .2s;cursor:pointer}
.inc-card:hover{transform:translateY(-2px);box-shadow:var(--shadow)}
.inc-card::before{content:"";position:absolute;top:0;left:0;width:100%;height:3px;background:var(--cyan)}
.inc-card.HIGH::before{background:var(--red)}
.inc-card.MEDIUM::before{background:var(--amber)}
.inc-card .top{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.inc-card .no{font-size:11px;font-weight:800;letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}
.inc-card .headline{font-size:14px;font-weight:600;color:var(--text);line-height:1.4;margin-bottom:12px}
.inc-card .meta{display:flex;gap:16px;flex-wrap:wrap;font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums}
.inc-card .meta b{color:var(--cyan-dim);font-weight:700}

/* tables */
.tbl-card{overflow:hidden;margin-top:14px}
.tbl-head{padding:12px 16px;border-bottom:1px solid var(--border);background:var(--surface-2);
 font-size:11px;font-weight:700;color:var(--cyan-dim);letter-spacing:.12em;text-transform:uppercase}
table{border-collapse:collapse;width:100%;font-size:12px}
th{padding:10px 14px;text-align:left;font-size:10px;font-weight:600;color:var(--muted);
 text-transform:uppercase;letter-spacing:.1em;border-bottom:1px solid var(--border)}
td{padding:10px 14px;border-bottom:1px solid var(--border);vertical-align:middle;color:var(--text)}
tbody tr{transition:background .2s}
tbody tr:hover{background:rgba(0,0,0,.025)}
tbody tr:last-child td{border-bottom:none}
.mono{font-family:ui-monospace,Menlo,monospace;font-size:11px;font-variant-numeric:tabular-nums}
a.jump{color:var(--cyan-dim);text-decoration:none;cursor:pointer;font-weight:600}
a.jump:hover{text-decoration:underline}
.tblwrap{overflow-x:auto}

/* stat cards */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:16px;margin-bottom:20px}
.stat{padding:18px;position:relative;overflow:hidden}
.stat::before{content:"";position:absolute;top:0;left:0;width:100%;height:2px;background:var(--cyan);opacity:.6}
.stat.pur::before{background:var(--purple-lt)}
.stat.red::before{background:var(--red)}
.stat .lbl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.16em;
 margin-bottom:10px;display:flex;justify-content:space-between}
.stat .val{font-size:28px;font-weight:800;color:var(--text);font-family:'Outfit',sans-serif;
 font-variant-numeric:tabular-nums}
.stat.pur .val{color:var(--text)}
.stat.red .val{color:var(--red-deep)}
.stat .sub{font-size:10px;color:var(--muted);margin-top:6px}

/* registry rows */
.reg-form{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}
.reg-form input{flex:1;min-width:130px}

/* assistant chat */
.ai-wrap{display:grid;grid-template-columns:1fr 380px;gap:18px}
@media(max-width:1100px){.ai-wrap{grid-template-columns:1fr}}
.chatbox{display:flex;flex-direction:column;height:520px;overflow:hidden}
.chat-head{padding:14px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:12px}
.chat-head .orb{width:38px;height:38px;border-radius:50%;flex-shrink:0;
 background:color-mix(in oklab,var(--cyan) 12%,transparent);border:1px solid var(--border);
 display:flex;align-items:center;justify-content:center;font-size:17px}
.chat-head b{font-size:15px;display:block;font-family:'Outfit',sans-serif}
.chat-head span{font-size:10px;color:var(--muted)}
#chat{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:12px}
.bub{max-width:88%;padding:10px 14px;border-radius:16px;font-size:12.5px;line-height:1.5}
.bub.a{align-self:flex-start;border-top-left-radius:4px;background:var(--surface-2);
 border:1px solid var(--border);color:var(--text)}
.bub.u{align-self:flex-end;border-top-right-radius:4px;text-align:right;
 background:color-mix(in oklab,var(--cyan) 10%,transparent);
 border:1px solid color-mix(in oklab,var(--cyan) 25%,transparent);color:var(--text)}
.bub.p{align-self:flex-start;border-top-left-radius:4px;background:var(--surface-2);
 border:1px solid var(--border-strong);color:var(--text)}
.bub .who{font-size:9px;opacity:.55;margin-bottom:3px;text-transform:uppercase;letter-spacing:.1em}
.chat-in{padding:12px;display:flex;gap:8px;border-top:1px solid var(--border)}
.chat-in input{flex:1}
.send-btn{width:42px;height:42px;border:none;border-radius:12px;background:var(--cyan);
 color:#fff;font-size:16px;cursor:pointer;transition:transform .15s}
.send-btn:active{transform:scale(.93)}
.mini-btn{background:var(--surface-2);color:var(--text);border:1px solid var(--border);
 border-radius:8px;padding:4px 12px;font-size:11px;font-family:inherit;cursor:pointer}
.mini-btn:hover{background:color-mix(in oklab,var(--cyan) 10%,transparent);
 border-color:color-mix(in oklab,var(--cyan) 30%,transparent)}
.mini-btn.danger:hover{background:color-mix(in oklab,var(--red) 10%,transparent);
 border-color:color-mix(in oklab,var(--red) 30%,transparent);color:var(--red-deep)}

/* footer */
footer{position:fixed;bottom:0;right:0;z-index:30;padding:6px 16px;font-size:10px;
 color:var(--muted-soft);opacity:.85;letter-spacing:.14em;text-transform:uppercase;pointer-events:none}
.empty{color:var(--muted-soft);opacity:.85;font-size:12px;padding:20px;text-align:center}

/* login gate */
.login-gate{position:fixed;inset:0;z-index:200;display:none;align-items:center;
 justify-content:center;padding:20px;background:var(--bg)}
.login-card{width:100%;max-width:380px;padding:32px}
.login-brand{font-family:'Outfit',sans-serif;font-size:26px;font-weight:800;
 letter-spacing:-.01em;color:var(--text)}
.login-sub{font-size:11px;color:var(--muted-soft);letter-spacing:.06em;margin-top:2px}
.login-lead{margin-top:16px;font-size:14px;color:var(--muted)}
.login-card .lbl{display:block;margin-top:18px;margin-bottom:6px;font-size:10px;
 text-transform:uppercase;letter-spacing:.14em;color:var(--muted);font-weight:600}
.login-card input{width:100%}
.login-err{margin-top:12px;font-size:13px;color:var(--red-deep)}
.login-foot{margin-top:16px;text-align:center;font-size:12px;color:var(--muted-soft)}

@media(prefers-reduced-motion:reduce){
 *,*::before,*::after{animation-duration:.001ms!important;animation-iteration-count:1!important;
  transition-duration:.001ms!important;scroll-behavior:auto!important}
}
</style></head><body>
<div class="mesh"></div>
<div id="login-gate" class="login-gate">
 <form class="glass login-card" onsubmit="return doLogin(event)">
  <div class="login-brand">VisionGuard</div>
  <div class="login-sub">SOCIETY WATCH</div>
  <p class="login-lead">Private investor preview &mdash; please sign in.</p>
  <label class="lbl" for="lg_user">Username</label>
  <input id="lg_user" type="text" autocomplete="username" autofocus>
  <label class="lbl" for="lg_pass">Password</label>
  <input id="lg_pass" type="password" autocomplete="current-password">
  <div id="lg_err" class="login-err"></div>
  <button class="btn grad" type="submit" style="width:100%;margin-top:20px">Sign in</button>
  <p class="login-foot">Access is by invitation.</p>
 </form>
</div>


<header class="app-chrome">
 <div class="brand"><b>VisionGuard</b><span>society watch</span></div>
 <div class="top-right">
  <span id="clock"></span>
  <span class="spend-chip" onclick="show('spend')">&#8377; <b id="topSpend">—</b> / 24h</span>
  <span id="app-user" class="user-chip"></span>
  <button class="signout" type="button" onclick="signOut()">Sign out</button>
 </div>
</header>

<aside class="app-chrome">
 <div class="aside-head"><b>VisionGuard</b><span>Vigilance Active</span></div>
 <nav>
  <a data-v="live" class="active" onclick="show('live')"><span class="ic">&#128225;</span> Live Monitoring</a>
  <a data-v="lab" onclick="show('lab')"><span class="ic">&#128300;</span> Forensic Lab</a>
  <a data-v="events" onclick="show('events')"><span class="ic">&#128276;</span> Events</a>
  <a data-v="vehicles" onclick="show('vehicles')"><span class="ic">&#128663;</span> Vehicles</a>
  <a data-v="spend" onclick="show('spend')"><span class="ic">&#129504;</span> AI &amp; Spend</a>
 </nav>
 <div class="aside-foot"><span class="pulse-dot"></span> SYSTEM VIGILANT</div>
</aside>

<main class="app-chrome">

<!-- ======================= LIVE MONITORING ======================= -->
<section class="view active" id="view-live">
 <div class="page-head">
  <div>
   <span class="live-pill"><i></i> Live Monitoring</span>
   <h1>Society Cameras</h1>
   <p>Annotated live feeds — green boxes mark flagged people (culprit tracking).</p>
  </div>
 </div>
 <div class="live-wrap">
  <div class="cam-grid" id="cams"></div>
  <div class="glass intel">
   <div class="intel-head"><b>Intelligence</b><span style="font-size:16px">&#9889;</span></div>
   <div class="intel-body" id="intel"></div>
  </div>
 </div>
</section>

<!-- ======================= FORENSIC LAB ======================= -->
<section class="view" id="view-lab">
 <div class="page-head">
  <div>
   <h1>Forensic Lab</h1>
   <p>Upload CCTV footage — the free layer flags suspicious moments, and Smart AI
      Review makes Claude actually watch the video (theft / break-in / vandalism).</p>
  </div>
 </div>

 <div class="dropzone" id="dropzone" onclick="document.getElementById('up_file').click()">
  <div class="drop-orb">&#128228;</div>
  <h2>Ingest CCTV Footage</h2>
  <p>Drag &amp; drop a video here, or browse. mp4 / avi / mov / mkv / dav — deleted after analysis.</p>
  <button class="btn" type="button">Browse Local Files</button>
  <input type="file" id="up_file" accept="video/*" style="display:none">
 </div>

 <div class="lab-controls">
  <label class="switch"><input type="checkbox" id="up_ai"><span class="track"></span>
   &#129504; Smart AI Review (Claude watches the video — needs API key)</label>
  <label style="font-size:13px;color:var(--muted)">Zones from:
   <select id="up_zones"><option value="">(none — tamper/contact only)</option></select>
  </label>
  <button class="btn grad" onclick="analyze()">&#9654; Analyze</button>
  <span class="status-line" id="up_status"></span>
 </div>

 <div class="threat-banner" id="threat_banner">
  <div class="warn">&#9888;</div>
  <div><h3 id="threat_title">High threat detected</h3><p id="threat_sub"></p></div>
 </div>

 <div id="incidents_box">
  <div class="tbl-head" style="background:none;border:none;padding:16px 2px 4px">
   Incidents detected</div>
  <div class="inc-grid" id="incidents_grid"></div>
 </div>

 <video id="up_player" controls></video>

 <div class="glass tbl-card" id="ai_box" style="display:none">
  <div class="tbl-head">&#129504; AI Scene Review — what Claude sees</div>
  <div id="ai_note" class="status-line" style="padding:8px 16px"></div>
  <div class="tblwrap"><table id="ai_results"><thead><tr>
   <th>Severity</th><th>Time</th><th>Finding</th><th></th></tr></thead>
   <tbody></tbody></table></div>
 </div>

 <div class="glass tbl-card">
  <div class="tbl-head">Rule-based checks (free layer, geometry only)</div>
  <div class="tblwrap"><table id="up_results"><thead><tr>
   <th>Severity</th><th>Time</th><th>Type</th><th>Plate</th><th>Description</th><th></th>
  </tr></thead><tbody><tr><td colspan="6" class="empty">No analysis yet — upload a video above.</td></tr></tbody></table></div>
 </div>
</section>

<!-- ======================= EVENTS ======================= -->
<section class="view" id="view-events">
 <div class="page-head">
  <div><h1>Event Archive</h1>
  <p>Every anomaly with its clip, incident number and Claude's verdict. Deleting a
     clip requires your name + reason and is written to the tamper-proof audit log.</p></div>
 </div>
 <div class="glass tbl-card">
  <div class="tbl-head">Recent events</div>
  <div class="tblwrap"><table id="events"><thead><tr>
   <th>Severity</th><th>Incident</th><th>Time</th><th>Camera</th><th>Type</th>
   <th>Plate</th><th>Description</th><th>AI says</th><th>Clip</th>
  </tr></thead><tbody></tbody></table></div>
 </div>
</section>

<!-- ======================= VEHICLES ======================= -->
<section class="view" id="view-vehicles">
 <div class="page-head">
  <div><h1>Vehicle Registry</h1>
  <p>Plates registered here are "known" — anything else at the gate raises an
     unauthorized-vehicle event.</p></div>
 </div>
 <div class="stats">
  <div class="glass stat"><div class="lbl"><span>Registered</span><span>&#128663;</span></div>
   <div class="val" id="st_reg">—</div><div class="sub">plates in registry</div></div>
  <div class="glass stat"><div class="lbl"><span>Cameras online</span><span>&#128225;</span></div>
   <div class="val" id="st_cams">—</div><div class="sub">live feeds</div></div>
  <div class="glass stat pur"><div class="lbl"><span>Events 24h</span><span>&#128276;</span></div>
   <div class="val" id="st_ev">—</div><div class="sub">all severities</div></div>
  <div class="glass stat red"><div class="lbl"><span>High alerts 24h</span><span>&#9888;</span></div>
   <div class="val" id="st_high">—</div><div class="sub">needs attention</div></div>
 </div>
 <div class="reg-form">
  <input id="r_plate" type="text" placeholder="Plate (WB02AB1234)">
  <input id="r_owner" type="text" placeholder="Owner name">
  <input id="r_phone" type="text" placeholder="Phone">
  <input id="r_flat" type="text" placeholder="Flat">
  <input id="r_chat" type="text" placeholder="Telegram chat id (optional)">
  <button class="btn grad" onclick="addPlate()">&#43; Add Vehicle</button>
 </div>
 <div class="glass tbl-card">
  <div class="tbl-head">Registered vehicles</div>
  <div class="tblwrap"><table id="registry"><thead><tr>
   <th>Plate</th><th>Owner</th><th>Phone</th><th>Flat</th><th></th>
  </tr></thead><tbody></tbody></table></div>
 </div>
</section>

<!-- ======================= AI & SPEND ======================= -->
<section class="view" id="view-spend">
 <div class="page-head">
  <div><h1>AI Intelligence &amp; Cost</h1>
  <p>Real spend from the two-tier review (Haiku screen &#8594; Opus deep look) and the
     Tuning Assistant to correct the system in plain English.</p></div>
 </div>
 <div class="ai-wrap">
  <div>
   <div class="stats">
    <div class="glass stat pur"><div class="lbl"><span>Spend 24h</span><span>&#128176;</span></div>
     <div class="val" id="cost24">—</div><div class="sub">Claude API, all cameras</div></div>
    <div class="glass stat pur"><div class="lbl"><span>Spend 30d</span><span>&#128200;</span></div>
     <div class="val" id="cost30">—</div><div class="sub">rolling month</div></div>
    <div class="glass stat"><div class="lbl"><span>AI calls 30d</span><span>&#9889;</span></div>
     <div class="val" id="calls30">—</div><div class="sub">tier-1 + tier-2</div></div>
    <div class="glass stat"><div class="lbl"><span>Live review</span><span>&#129504;</span></div>
     <div class="val" id="rev_state" style="font-size:20px">—</div>
     <div class="sub" id="cost_note"></div></div>
   </div>
   <div class="glass tbl-card">
    <div class="tbl-head">Per-camera spend (30 days)</div>
    <div class="tblwrap"><table id="percam"><thead><tr>
     <th>Camera</th><th>Calls</th><th>Cost (&#8377;)</th>
    </tr></thead><tbody><tr><td colspan="3" class="empty">No AI calls yet.</td></tr></tbody></table></div>
   </div>
  </div>
  <div class="glass chatbox">
   <div class="chat-head">
    <div class="orb">&#9889;</div>
    <div><b>Tuning Assistant</b>
     <span>Claude — corrects thresholds in plain English</span></div>
   </div>
   <div id="chat"></div>
   <div class="chat-in">
    <input id="chat_in" type="text"
     placeholder="e.g. loitering alert fired too early — make it less sensitive"
     onkeydown="if(event.key==='Enter')sendChat()">
    <button class="send-btn" onclick="sendChat()">&#10148;</button>
   </div>
  </div>
 </div>
</section>

</main>
<footer class="app-chrome">All processing on this machine &middot; only Telegram alerts leave it</footer>

<script>
const cams=[__CAMERAS__];
function esc(s){return (s??'').toString().replace(/[&<>"]/g,
 c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function sevChip(s){return `<span class="chip ${esc(s)}"><i style="width:6px;height:6px;border-radius:50%;background:currentColor"></i>${esc(s)}</span>`;}

// ---- view switching ---------------------------------------------------
function show(v){
 document.querySelectorAll('.view').forEach(x=>x.classList.remove('active'));
 document.getElementById('view-'+v).classList.add('active');
 document.querySelectorAll('nav a').forEach(a=>
  a.classList.toggle('active',a.dataset.v===v));
}

// ---- clock ------------------------------------------------------------
setInterval(()=>{document.getElementById('clock').textContent=
 new Date().toLocaleTimeString('en-IN',{hour12:false});},1000);

// ---- camera cards -----------------------------------------------------
document.getElementById('cams').innerHTML=cams.length?cams.map(c=>
 `<div class="glass cam-card">
   <img src="/stream/${encodeURIComponent(c)}" alt="${esc(c)}">
   <div class="cam-meta">
    <span class="cam-name">&#128249; ${esc(c)}</span>
    <span class="chip ok" id="st_${esc(c)}"><i></i>LIVE</span>
   </div>
  </div>`).join('')
 :'<div class="glass cam-card empty">No cameras configured — add one in config.yaml</div>';

async function pollStatus(){
 try{
  const st=await (await fetch('/api/status')).json();
  let online=0;
  for(const [name,s] of Object.entries(st)){
   const el=document.getElementById('st_'+name);
   if(el){el.className='chip '+(s.online?'ok':'bad');
    el.innerHTML='<i></i>'+(s.online?'LIVE':'OFFLINE');}
   if(s.online)online++;
  }
  document.getElementById('st_cams').textContent=online+'/'+cams.length;
 }catch(e){}
}

// ---- events + intel + registry + costs --------------------------------
const TYPE_IC={suspicious_activity:'&#9888;',loitering:'&#128564;',
 unauthorized_vehicle:'&#128663;',vehicle_contact:'&#128165;',
 camera_tamper:'&#128247;',restricted_zone_night:'&#127769;'};
async function refresh(){
 try{
  const evs=await (await fetch('/api/events?limit=100')).json();
  // intel feed (latest 10)
  document.getElementById('intel').innerHTML=evs.slice(0,10).map(e=>{
   const t=new Date(e.ts*1000).toLocaleTimeString('en-IN',{hour12:false});
   return `<div class="intel-item">
    <div class="intel-ic ${e.severity}">${TYPE_IC[e.event_type]||'&#128276;'}</div>
    <div><div class="t">${esc(e.description)}</div>
     <div class="m"><span class="tag">${esc(e.camera)}</span>
      ${e.incident_id?`<span class="tag">Incident #${e.incident_id}</span>`:''}
      <span class="tag">${t}</span></div></div></div>`;
  }).join('')||'<div class="empty">No events yet — all quiet.</div>';
  // events table
  document.querySelector('#events tbody').innerHTML=evs.map(e=>{
   const t=new Date(e.ts*1000).toLocaleString('en-IN',{hour12:false});
   let clip='—';
   if(e.clip_id&&!e.clip_deleted) clip=`<a class="jump" href="/clips/${e.clip_id}" target="_blank">&#9654; view</a>
    <button class="mini-btn danger" onclick="delClip(${e.clip_id})">delete</button>`;
   else if(e.clip_deleted) clip='<span style="opacity:.5">deleted</span>';
   return `<tr><td>${sevChip(e.severity)}</td>
   <td class="mono">${e.incident_id?'#'+e.incident_id:'—'}</td>
   <td class="mono">${t}</td><td>${esc(e.camera)}</td><td>${esc(e.event_type)}</td>
   <td class="mono">${esc(e.plate)||'—'}</td><td>${esc(e.description)}</td>
   <td>${esc(e.ai_summary)||'—'}</td><td>${clip}</td></tr>`;}).join('')
   ||'<tr><td colspan="9" class="empty">No events yet.</td></tr>';
  // stats for vehicles view
  const day=Date.now()/1000-86400;
  const evs24=evs.filter(e=>e.ts>=day);
  document.getElementById('st_ev').textContent=evs24.length;
  document.getElementById('st_high').textContent=
   evs24.filter(e=>e.severity==='HIGH').length;
 }catch(e){}
 try{
  const c=await (await fetch('/api/costs')).json();
  document.getElementById('cost24').textContent='\\u20B9'+c.last_24h.cost_inr.toFixed(2);
  document.getElementById('cost30').textContent='\\u20B9'+c.last_30d.cost_inr.toFixed(2);
  document.getElementById('calls30').textContent=c.last_30d.calls;
  document.getElementById('topSpend').textContent=c.last_24h.cost_inr.toFixed(0);
  const on=c.ai_review_enabled;
  const rs=document.getElementById('rev_state');
  rs.textContent=on?'ON':'OFF';
  rs.style.color=on?'var(--cyan)':'var(--red)';
  document.getElementById('cost_note').textContent=on
   ?'two-tier review active':'set ai_review.enabled + API key';
  document.querySelector('#percam tbody').innerHTML=
   (c.per_camera_30d||[]).map(r=>`<tr><td>${esc(r.camera)}</td>
    <td class="mono">${r.calls}</td><td class="mono">\\u20B9${r.cost_inr.toFixed(2)}</td></tr>`)
   .join('')||'<tr><td colspan="3" class="empty">No AI calls yet.</td></tr>';
 }catch(e){}
 try{
  const reg=await (await fetch('/api/registry')).json();
  document.getElementById('st_reg').textContent=reg.length;
  document.querySelector('#registry tbody').innerHTML=reg.map(v=>
   `<tr><td class="mono" style="color:var(--cyan);font-weight:700">${esc(v.plate_number)}</td>
    <td>${esc(v.owner_name)}</td><td class="mono">${esc(v.owner_phone)}</td>
    <td>${esc(v.flat_number)}</td>
    <td><button class="mini-btn danger" onclick="rmPlate('${esc(v.plate_number)}')">remove</button></td></tr>`)
   .join('')||'<tr><td colspan="5" class="empty">No vehicles registered yet.</td></tr>';
 }catch(e){}
 pollStatus();
}

async function addPlate(){
 const f=new FormData();
 f.append('plate',document.getElementById('r_plate').value);
 f.append('owner_name',document.getElementById('r_owner').value);
 f.append('owner_phone',document.getElementById('r_phone').value);
 f.append('flat_number',document.getElementById('r_flat').value);
 f.append('telegram_chat_id',document.getElementById('r_chat').value);
 const r=await fetch('/api/registry',{method:'POST',body:f});
 if(!r.ok)alert('could not add — check the plate format');
 else ['r_plate','r_owner','r_phone','r_flat','r_chat'].forEach(i=>
  document.getElementById(i).value='');
 refresh();
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

// ---- forensic lab: upload & analyze -----------------------------------
const dz=document.getElementById('dropzone');
dz.addEventListener('dragover',e=>{e.preventDefault();dz.classList.add('drag');});
dz.addEventListener('dragleave',()=>dz.classList.remove('drag'));
dz.addEventListener('drop',e=>{
 e.preventDefault();dz.classList.remove('drag');
 if(e.dataTransfer.files.length){
  document.getElementById('up_file').files=e.dataTransfer.files;
  analyze();
 }});
document.getElementById('up_file').addEventListener('change',()=>{
 const f=document.getElementById('up_file').files[0];
 if(f)document.getElementById('up_status').textContent='ready: '+f.name;
});

async function loadCams(){
 try{const cs=await (await fetch('/api/cameras')).json();
  document.getElementById('up_zones').innerHTML=
   '<option value="">(none — tamper/contact only)</option>'+
   cs.map(c=>`<option value="${esc(c)}">${esc(c)}</option>`).join('');
 }catch(e){}
}

async function analyze(){
 const fi=document.getElementById('up_file');
 if(!fi.files.length){alert('choose a video first');return;}
 const st=document.getElementById('up_status'); st.textContent='uploading\\u2026';
 const f=new FormData(); f.append('file',fi.files[0]);
 f.append('zones_from',document.getElementById('up_zones').value);
 f.append('ai_review',document.getElementById('up_ai').checked?'1':'0');
 let r=await fetch('/api/analyze',{method:'POST',body:f});
 if(!r.ok){st.textContent='error: '+(await r.text());return;}
 const {job_id}=await r.json();
 const player=document.getElementById('up_player');
 player.style.display='none';
 document.getElementById('threat_banner').style.display='none';
 document.getElementById('incidents_box').style.display='none';
 const poll=setInterval(async()=>{
  const j=await (await fetch('/api/analyze/'+job_id)).json();
  st.textContent=`${j.status} \\u2014 ${Math.round(j.progress*100)}% (${j.events.length} found)`;
  document.querySelector('#up_results tbody').innerHTML=j.events.map(e=>
   `<tr><td>${sevChip(e.severity)}</td>
   <td><a class="jump" onclick="seekTo(${e.video_time_s})">${e.video_time_s}s</a></td>
   <td>${esc(e.event_type)}</td><td class="mono">${esc(e.plate)||'—'}</td>
   <td>${esc(e.description)}</td>
   <td><a class="jump" onclick="seekTo(${e.video_time_s})">&#9654; jump</a></td></tr>`
  ).join('')||'<tr><td colspan="6" class="empty">Nothing flagged yet\\u2026</td></tr>';
  // incident cards (merged) — the headline of the analysis
  const incs=j.incidents||[];
  if(incs.length){
   document.getElementById('incidents_box').style.display='block';
   document.getElementById('incidents_grid').innerHTML=incs.map(c=>{
    const span=(c.start_s===c.end_s)?`${c.start_s}s`:`${c.start_s}\\u2013${c.end_s}s`;
    const ids=(c.track_ids&&c.track_ids.length)?c.track_ids.map(t=>'#'+t).join(', '):'\\u2014';
    return `<div class="inc-card ${esc(c.severity)}" onclick="seekTo(${c.start_s})">
     <div class="top"><span class="no">Incident ${c.index+1}</span>${sevChip(c.severity)}</div>
     <div class="headline">${esc(c.summary)||esc(c.event_type)}</div>
     <div class="meta"><span>&#128337; <b>${span}</b></span>
      <span>&#128100; culprit <b>${ids}</b></span>
      <span>&#9654; ${c.count} alert(s)</span></div></div>`;
   }).join('');
  }
  // AI scene review findings
  const aiBox=document.getElementById('ai_box');
  if((j.ai_findings&&j.ai_findings.length)||j.ai_note){
   aiBox.style.display='block';
   document.getElementById('ai_note').textContent=j.ai_note||'';
   const finds=j.ai_findings||[];
   document.querySelector('#ai_results tbody').innerHTML=finds.map(x=>
    `<tr><td>${sevChip(x.severity)}</td>
    <td><a class="jump" onclick="seekTo(${x.time_s})">${x.time_s}s</a></td>
    <td>${esc(x.activity)}</td>
    <td><a class="jump" onclick="seekTo(${x.time_s})">&#9654; jump</a></td></tr>`
   ).join('');
   const high=finds.filter(x=>x.severity==='HIGH');
   if(j.ai_verdict){
    const b=document.getElementById('threat_banner');
    b.style.display='flex';
    document.getElementById('threat_title').textContent=
     'Claude verdict: '+j.ai_verdict;
    document.getElementById('threat_sub').textContent=
     finds.length+' finding(s) \\u2014 timestamps listed below';
   }
  }
  if(j.status==='done'||j.status==='error'){clearInterval(poll);
   if(j.status==='error'){st.textContent='error: '+j.error;return;}
   if(j.video_ready){
    player.src='/api/analyze/'+job_id+'/video';
    player.style.display='block';
    st.textContent='done \\u2014 green CULPRIT boxes in the video; click a time to jump.';
   }
  }
 },1200);
}
function seekTo(t){
 const p=document.getElementById('up_player');
 if(p.src){p.currentTime=Math.max(0,t-2);p.play();
  p.scrollIntoView({behavior:'smooth',block:'center'});}
}

// ---- Claude tuning chatbot -------------------------------------------
let chatHistory=[];
function addMsg(role,text){
 const d=document.getElementById('chat');
 const cls=role==='user'?'u':'a';
 const who=role==='user'?'You':'Assistant';
 d.innerHTML+=`<div class="bub ${cls}"><div class="who">${who}</div>${esc(text)}</div>`;
 d.scrollTop=d.scrollHeight;
}
async function sendChat(){
 const inp=document.getElementById('chat_in'); const msg=inp.value.trim();
 if(!msg)return; inp.value=''; addMsg('user',msg);
 chatHistory.push({role:'user',content:msg});
 const r=await fetch('/api/assistant',{method:'POST',
  headers:{'Content-Type':'application/json'},
  body:JSON.stringify({message:msg,history:chatHistory})});
 const res=await r.json();
 addMsg('assistant',res.reply);
 chatHistory.push({role:'assistant',content:res.reply});
 if(res.patch&&Object.keys(res.patch).length){
  const summary=Object.entries(res.patch).map(([k,v])=>`${k} \\u2192 ${v}`).join(', ');
  const d=document.getElementById('chat');
  d.innerHTML+=`<div class="bub p"><div class="who">Proposed change</div>
   <span class="mono">${esc(summary)}</span><br>
   <small>${esc(res.explanation||'')}</small><br><br>
   <button class="mini-btn" onclick='applyPatch(${JSON.stringify(res.patch)})'>&#10003; Apply</button></div>`;
  d.scrollTop=d.scrollHeight;
 }
}
async function applyPatch(patch){
 const r=await fetch('/api/assistant/apply',{method:'POST',
  headers:{'Content-Type':'application/json'},
  body:JSON.stringify({patch})});
 const res=await r.json();
 const n=Object.keys(res.applied||{}).length;
 addMsg('assistant',n?`Applied ${n} change(s). New thresholds are live.`:
  'Nothing applied'+(res.rejected&&res.rejected.length?
   ' (rejected: '+res.rejected.join(', ')+')':'')+'.');
}
addMsg('assistant','Namaste! Tell me what the system got wrong and I will '+
 'propose a safe settings change you can apply with one click.');
loadCams();
refresh(); setInterval(refresh,5000);


/* ---- investor login gate (demo-grade, client-side) ---- */
const AUTH_USERS={admin:"password1101",YC:"11012235"};
const AUTH_KEY="vg_auth_user";
function doLogin(e){e.preventDefault();
 const u=(document.getElementById('lg_user').value||'').trim();
 const p=document.getElementById('lg_pass').value||'';
 if(AUTH_USERS[u]&&AUTH_USERS[u]===p){try{localStorage.setItem(AUTH_KEY,u);}catch(_){}showApp(u);}
 else{document.getElementById('lg_err').textContent='Invalid username or password.';}
 return false;}
function signOut(){try{localStorage.removeItem(AUTH_KEY);}catch(_){}location.reload();}
function showApp(u){document.getElementById('login-gate').style.display='none';
 document.body.classList.add('authed');
 const el=document.getElementById('app-user');if(el)el.textContent=u;}
(function(){let u=null;try{u=localStorage.getItem(AUTH_KEY);}catch(_){}
 if(u){showApp(u);}else{document.getElementById('login-gate').style.display='flex';}})();
</script></body></html>"""
