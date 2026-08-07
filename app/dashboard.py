"""FastAPI dashboard: live annotated MJPEG, events, registry, clip
deletion (name + reason, audited). One page, no build step.

Frontend: AEGIS glass theme (from the user's Stitch design), rebuilt as
self-contained CSS — no Tailwind CDN, so the dashboard still renders when
the internet is down. Only the Google font is fetched (graceful fallback).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import time
from pathlib import Path

# Bump when shipping a fix that must be verifiable in production. /health
# echoes it, so "is my change actually live?" has a definite answer.
BUILD = "2026-07-26-ai-review-sdk"

from fastapi import (Depends, FastAPI, File, Form, HTTPException, Request,
                     UploadFile)
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               Response, StreamingResponse)
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from . import annotations as ann_mod
from . import assistant as assistant_mod
from . import auth
from . import clips as clips_mod
from . import damage as damage_mod
from . import db as db_mod
from . import discovery
from . import train as train_mod
from . import operator as operator_mod
from . import owner as owner_mod
from . import segment as segment_mod
from . import tagging
from . import track as track_mod
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

    def _console_html():
        return PAGE.replace("__CAMERAS__",
                            ",".join(f'"{n}"' for n in ctx.workers))

    # The real console is the only thing this server serves. It used to fall
    # back to a bundled static demo page, which meant a stale copy could shadow
    # the working app and show canned results — never again.
    # no-store keeps a browser from holding on to an old copy of the shell.
    _NO_CACHE = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}

    @app.get("/", response_class=HTMLResponse)
    def index():
        return HTMLResponse(_console_html(), headers=_NO_CACHE)

    @app.get("/console", response_class=HTMLResponse)
    def console():
        return HTMLResponse(_console_html(), headers=_NO_CACHE)

    # ---------------------------------------------- operator identity
    # Everything the operator app can change is attributable: a verdict is the
    # label the detection layer gets tuned against, and a notice reaches every
    # resident. Both need a real account behind them, not a typed-in name.
    def current_user(request: Request) -> dict | None:
        token = request.cookies.get(auth.SESSION_COOKIE)
        if token:
            th = auth.token_hash(token)
            user = ctx.db.session_user(th)
            if user is not None:
                # slide the expiry so a shift is not cut off mid-way
                ctx.db.touch_session(th, auth.session_expiry())
                return user
        # HTTP Basic fallback: when the legacy shared-password gate is enabled,
        # the app-level dependency has already authenticated the request before
        # it reaches any endpoint, so a request that got here IS the configured
        # admin. Treating it as an admin session lets the same require() guard
        # protect every endpoint whether the deployment uses Basic or the real
        # session system — and it is what keeps the resident registry, live
        # video and recorded clips closed on a hosted install that turned the
        # session login off.
        if auth_cfg.get("enabled", True):
            return {"username": str(auth_cfg.get("username", "admin")),
                    "role": "admin"}
        return None

    def require(permission: str):
        def dep(request: Request) -> dict:
            user = current_user(request)
            if user is None:
                raise HTTPException(401, "sign in to continue")
            if not auth.can(user["role"], permission):
                raise HTTPException(
                    403, f"a {user['role']} account cannot do this")
            return user
        return dep

    @app.post("/api/login")
    def login(request: Request, response: Response,
              username: str = Form(...), password: str = Form(...)):
        user = ctx.db.authenticate(username, password)
        if user is None:
            # one message for both wrong-user and wrong-password: saying which
            # was wrong confirms whether an account exists
            raise HTTPException(401, "wrong username or password")
        token, th = auth.new_token()
        ctx.db.create_session(user["id"], th, auth.session_expiry(),
                              request.headers.get("user-agent", ""))
        ctx.db.purge_expired_sessions()
        response.set_cookie(
            auth.SESSION_COOKIE, token, httponly=True, samesite="lax",
            max_age=auth.SESSION_TTL_S,
            # only send the cookie over TLS once the app is actually on TLS;
            # forcing it on plain http would silently break a LAN install
            secure=request.url.scheme == "https", path="/")
        ctx.db.append_audit(user["username"], "LOGIN", {"role": user["role"]})
        return {"ok": True, **_me(user)}

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        token = request.cookies.get(auth.SESSION_COOKIE)
        if token:
            ctx.db.drop_session(auth.token_hash(token))
        response.delete_cookie(auth.SESSION_COOKIE, path="/")
        return {"ok": True}

    # ==================== resident owner app (/owner) ====================
    # A separate, magic-link identity from the operator console: a resident is
    # not a staff account. Everything it can reach is scoped to ONE plate, and
    # every action re-checks ownership, so one resident can never see another's.
    OWNER_COOKIE = "vg_owner"

    def current_owner(request: Request) -> dict | None:
        token = request.cookies.get(OWNER_COOKIE)
        return ctx.db.owner_for_token(token) if token else None

    def require_owner(request: Request) -> dict:
        owner = current_owner(request)
        if owner is None:
            raise HTTPException(401, "open your access link to sign in")
        return owner

    def _keep_only_confirmed() -> bool:
        return bool((ctx.config.get("clips") or {}).get("keep_only_confirmed", True))

    @app.get("/owner", response_class=HTMLResponse)
    def owner_page():
        return HTMLResponse(owner_mod.page(), headers=_NO_CACHE)

    @app.get("/owner/manifest.webmanifest")
    def owner_manifest():
        return JSONResponse(owner_mod.MANIFEST,
                            media_type="application/manifest+json")

    @app.get("/owner/sw.js")
    def owner_sw():
        return Response(owner_mod.SW, media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})

    @app.get("/owner/icon.svg")
    def owner_icon():
        return Response(owner_mod.ICON_SVG, media_type="image/svg+xml")

    @app.post("/api/owner/login")
    def owner_login(request: Request, response: Response, token: str = Form(...)):
        owner = ctx.db.owner_for_token(token)
        if owner is None:
            raise HTTPException(401, "invalid or expired link")
        response.set_cookie(
            OWNER_COOKIE, token, httponly=True, samesite="lax",
            secure=request.url.scheme == "https", path="/",
            max_age=60 * 60 * 24 * 90)
        return {"ok": True, **owner}

    @app.post("/api/owner/logout")
    def owner_logout(response: Response):
        response.delete_cookie(OWNER_COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/owner/me")
    def owner_me(owner: dict = Depends(require_owner)):
        return owner

    @app.get("/api/owner/alerts")
    def owner_alerts(owner: dict = Depends(require_owner)):
        rows = ctx.db.events_for_plate(owner["plate"], 50)
        verdicts = ctx.db.event_verdicts([r["id"] for r in rows])
        out = []
        for r in rows:
            v = verdicts.get(r["id"])
            out.append({
                "id": r["id"], "ts": r["ts"], "camera": r["camera"],
                "location": ctx.db.describe_camera(r["camera"]),
                "event_type": r["event_type"], "severity": r["severity"],
                "description": r["description"], "ai_summary": r.get("ai_summary"),
                "has_clip": bool(r.get("clip_id") and not r.get("clip_deleted")),
                "verdict": v["verdict"] if v else None})
        return out

    @app.get("/api/owner/visits")
    def owner_visits(owner: dict = Depends(require_owner)):
        return ctx.db.recent_visits(limit=50, plate=owner["plate"])

    @app.get("/owner/clip/{event_id}")
    def owner_clip(event_id: int, owner: dict = Depends(require_owner)):
        # ownership is the gate: the clip is served only if this event is about
        # this resident's own vehicle.
        if not ctx.db.event_belongs_to_plate(event_id, owner["plate"]):
            raise HTTPException(404, "not found")
        clip = ctx.db.clip_for_event(event_id)
        if not clip or not clip.get("path") or not os.path.exists(clip["path"]):
            raise HTTPException(404, "no clip")
        return FileResponse(clip["path"], media_type="video/mp4")

    @app.post("/api/owner/alerts/{event_id}/feedback")
    def owner_alert_feedback(event_id: int, verdict: str = Form(...),
                             owner: dict = Depends(require_owner)):
        if verdict not in ("real", "false_alarm"):
            raise HTTPException(400, "verdict must be 'real' or 'false_alarm'")
        if not ctx.db.event_belongs_to_plate(event_id, owner["plate"]):
            raise HTTPException(404, "not found")
        who = f"owner:{owner['plate']}"
        ctx.db.insert_feedback(event_id, verdict, who)
        discarded = False
        if verdict == "false_alarm" and _keep_only_confirmed() and \
                hasattr(ctx, "discard_event_clip"):
            discarded = ctx.discard_event_clip(
                event_id, "owner marked false alarm", who)
        return {"ok": True, "verdict": verdict, "clip_discarded": discarded}

    def _me(user: dict) -> dict:
        return {"username": user["username"], "name": user["display_name"],
                "role": user["role"],
                "can": sorted(auth.PERMISSIONS.get(user["role"], ()))}

    @app.get("/api/me")
    def me(request: Request):
        user = current_user(request)
        if user is None:
            raise HTTPException(401, "not signed in")
        return _me(user)

    # ------------------------------------------- operator app (guards' PWA)
    @app.get("/operator", response_class=HTMLResponse)
    def operator():
        # the page itself is the login screen, so it is always served; every
        # endpoint behind it checks the session
        return HTMLResponse(operator_mod.PAGE, headers=_NO_CACHE)

    @app.get("/operator/manifest.webmanifest")
    def operator_manifest():
        return Response(operator_mod.manifest_json(),
                        media_type="application/manifest+json")

    @app.get("/operator/sw.js")
    def operator_sw():
        # never cached: the service worker is how a stale app gets replaced
        return Response(operator_mod.SW, media_type="application/javascript",
                        headers=_NO_CACHE)

    @app.get("/operator/font.woff2")
    def operator_font():
        """Manrope, served from disk rather than a font CDN — a guard at a gate
        with no signal must not fall back to the system font mid-shift."""
        path = Path(__file__).parent / "static" / "manrope-latin.woff2"
        if not path.exists():
            raise HTTPException(404, "font not bundled")
        return FileResponse(path, media_type="font/woff2",
                            headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.get("/operator/icon-{size}.png")
    def operator_icon(size: int):
        if size not in (192, 512):
            raise HTTPException(404, "no such icon")
        return Response(operator_mod.icon_png(size), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/health")
    def health():
        """Liveness + build check. Reports whether Smart AI Review can actually
        run, so a stale image or a missing key is visible without uploading a
        clip — the two failures look identical from the UI otherwise."""
        try:
            import anthropic
            sdk = getattr(anthropic, "__version__", "installed")
        except ImportError:
            sdk = None
        key_env = (ctx.config.get("vlm") or {}).get("api_key_env",
                                                    "ANTHROPIC_API_KEY")
        from .vlm import VLMDescriber
        reviewer = VLMDescriber({**(ctx.config.get("vlm") or {}), "enabled": True})
        # Plate OCR is a hard dependency for the registry, visitor log and
        # journey tracking, and it fails silently — so report it here too.
        from .plates import PlateReader
        _pr = PlateReader({**(ctx.config.get("plates") or {}), "enabled": True})
        # camera health, so "is it connected and seeing anything" is answerable
        # from one URL — the first thing to check on a fresh install
        cams = []
        for name, worker in ctx.workers.items():
            cams.append({"name": name, "online": bool(worker.online)})
        return {
            "ok": True,
            "ui": "console",
            "build": BUILD,
            "cameras": list(ctx.workers),
            "camera_health": cams,
            "autoconnect": getattr(ctx, "autoconnect_result", None),
            "plate_ocr": {
                "available": _pr._ocr is not None,
                "backend": _pr._ocr_kind or None,
            },
            "ai_review": {
                "available": reviewer.available,
                "anthropic_sdk": sdk,
                "key_env": key_env,
                "key_present": bool(os.environ.get(key_env, "").strip()),
                "off_reason": reviewer.off_reason or None,
            },
        }

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
    def stream(camera: str, user: dict = Depends(require("triage"))):
        if camera not in ctx.workers:
            raise HTTPException(404, "unknown camera")
        return StreamingResponse(mjpeg_gen(camera),
                                 media_type="multipart/x-mixed-replace; boundary=frame")

    @app.get("/snapshot/{camera}")
    def snapshot(camera: str, user: dict = Depends(require("triage"))):
        """One still frame — the background the zone editor draws on. Same
        pixels the pipeline sees, so a polygon drawn here lines up with what
        the rules test against."""
        jpg = None
        pipe = ctx.pipelines.get(camera)
        if pipe is not None:
            jpg = pipe.annotated_jpeg
        if jpg is None:
            worker = ctx.workers.get(camera)
            if worker is not None:
                snap = worker.buffer_snapshot()
                if snap:
                    jpg = snap[-1][1]
        if jpg is None:
            raise HTTPException(404, "no frame yet")
        return Response(content=jpg, media_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})

    # -------------------------------------------------------------- events
    @app.get("/api/events")
    def events(limit: int = 100, user: dict = Depends(require("triage"))):
        rows = ctx.db.recent_events(limit)
        # attach the triage verdict so the operator app can show what a
        # colleague has already dealt with instead of asking twice
        verdicts = ctx.db.event_verdicts([r["id"] for r in rows])
        for r in rows:
            v = verdicts.get(r["id"])
            r["verdict"] = v["verdict"] if v else None
            r["verdict_by"] = v["user_name"] if v else None
        return rows

    @app.post("/api/events/{event_id}/feedback")
    def event_feedback(event_id: int, verdict: str = Form(...),
                       user: dict = Depends(require("triage"))):
        """A guard marking an alert real or a false alarm. This is the label
        the detection layer is tuned against, so it is worth one tap — and it
        is signed by whoever is holding the phone."""
        if verdict not in ("real", "false_alarm"):
            raise HTTPException(400, "verdict must be 'real' or 'false_alarm'")
        if ctx.db.get_event(event_id) is None:
            raise HTTPException(404, "event not found")
        ctx.db.insert_feedback(event_id, verdict, user["display_name"])
        # a false alarm is not evidence: drop its clip if the deployment keeps
        # only confirmed clips (and the context supports it).
        discarded = False
        if verdict == "false_alarm" and \
                (ctx.config.get("clips") or {}).get("keep_only_confirmed", True) \
                and hasattr(ctx, "discard_event_clip"):
            discarded = ctx.discard_event_clip(
                event_id, "operator marked false alarm", user["display_name"])
        return {"ok": True, "event_id": event_id, "verdict": verdict,
                "clip_discarded": discarded}

    # ------------------------------------------- notices (to residents)
    @app.get("/api/notices")
    def notices(limit: int = 50, user: dict = Depends(require("notices"))):
        return ctx.db.recent_notices(min(limit, 200))

    @app.post("/api/notices")
    def notice_add(title: str = Form(...), body: str = Form(...),
                   audience: str = Form("all"), flat_number: str = Form(""),
                   user: dict = Depends(require("notices"))):
        if not title.strip() or not body.strip():
            raise HTTPException(400, "title and body are required")
        if audience not in ("all", "flat"):
            raise HTTPException(400, "audience must be 'all' or 'flat'")
        if audience == "flat" and not flat_number.strip():
            raise HTTPException(400, "flat_number is required for audience=flat")
        nid = ctx.db.add_notice(title.strip(), body.strip(),
                                user["display_name"], audience,
                                flat_number.strip())
        sent = 0
        notifier = getattr(ctx, "notifier", None)
        if notifier is not None:
            try:
                sent = notifier.broadcast_notice(title.strip(), body.strip(),
                                                 audience, flat_number.strip())
            except Exception:
                log.exception("notice broadcast failed")
        ctx.db.mark_notice_sent(nid, sent)
        # sent=0 is normal when Telegram is off — the notice is still recorded
        return {"ok": True, "id": nid, "recipients": sent}

    @app.get("/api/status")
    def status(user: dict = Depends(require("triage"))):
        contexts = ctx.db.list_camera_context()
        return {n: {"online": w.online,
                    "last_frame_age_s": None if not w.last_frame_ts else
                    round(__import__("time").time() - w.last_frame_ts, 1),
                    "location": db_mod.describe_camera_context(
                        n, contexts.get(n))}
                for n, w in ctx.workers.items()}

    # ------------------------------------------------------------ registry
    @app.get("/api/registry")
    def registry(user: dict = Depends(require("triage"))):
        return ctx.db.list_vehicles()

    @app.post("/api/registry")
    def registry_add(plate: str = Form(...), owner_name: str = Form(""),
                     owner_phone: str = Form(""), flat_number: str = Form(""),
                     telegram_chat_id: str = Form(""),
                     user: dict = Depends(require("registry"))):
        p = normalize_plate(plate)
        if not p:
            raise HTTPException(400, "invalid plate")
        ctx.db.add_vehicle(p, owner_name, owner_phone, flat_number,
                           telegram_chat_id, actor="dashboard")
        return {"ok": True, "plate": p}

    @app.delete("/api/registry/{plate}")
    def registry_remove(plate: str, user: dict = Depends(require("registry"))):
        if not ctx.db.remove_vehicle(normalize_plate(plate), actor="dashboard"):
            raise HTTPException(404, "plate not found")
        return {"ok": True}

    @app.post("/api/registry/{plate}/owner-link")
    def registry_owner_link(plate: str, request: Request,
                            user: dict = Depends(require("registry"))):
        """Mint a resident's access link for their vehicle. Hand this to the
        owner once (it carries a secret) — they open it and see only their own
        alerts in the owner app."""
        p = normalize_plate(plate)
        if ctx.db.vehicle_by_plate(p) is None:
            raise HTTPException(404, "no such vehicle in the registry")
        token = ctx.db.issue_owner_token(p, label=user["username"])
        base = str(request.base_url).rstrip("/")
        return {"plate": p, "path": f"/owner?token={token}",
                "link": f"{base}/owner?token={token}"}

    # ------------------------------------------------------------ training
    # The labelling workbench. Every threshold in this system is tuned against
    # human judgement, and this is where that judgement is entered.
    TRAIN_DIR = Path((ctx.config.get("storage") or {}).get(
        "training_dir", "testset/clips"))

    @app.get("/train", response_class=HTMLResponse)
    def train_page():
        return HTMLResponse(train_mod.PAGE, headers=_NO_CACHE)

    @app.get("/api/train/clips")
    def train_clips(user: dict = Depends(require("registry"))):
        return ctx.db.list_training_clips()

    @app.post("/api/train/clips")
    async def train_upload(file: UploadFile = File(...), source: str = Form(""),
                           user: dict = Depends(require("registry"))):
        suffix = Path(file.filename or "clip.mp4").suffix.lower() or ".mp4"
        if suffix not in ALLOWED_VIDEO_EXT:
            raise HTTPException(400, f"unsupported video type {suffix}")
        TRAIN_DIR.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(file.filename or "clip").name)
        dest = TRAIN_DIR / safe
        n = 1
        while dest.exists():                       # never silently overwrite
            dest = TRAIN_DIR / f"{Path(safe).stem}_{n}{suffix}"
            n += 1
        size = 0
        with dest.open("wb") as out:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                out.write(chunk)
        if not size:
            dest.unlink(missing_ok=True)
            raise HTTPException(400, "empty file")

        duration = 0.0
        try:
            import cv2
            cap = cv2.VideoCapture(str(dest))
            fps = cap.get(cv2.CAP_PROP_FPS) or 0
            frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            cap.release()
            duration = frames / fps if fps else 0.0
        except Exception:
            log.exception("could not read duration of %s", dest)

        cid = ctx.db.add_training_clip(dest.name, str(dest), duration,
                                       user["username"], source.strip())
        return {"ok": True, "id": cid, "filename": dest.name,
                "duration_s": round(duration, 2)}

    @app.get("/api/train/clips/{clip_id}/video")
    def train_video(clip_id: int, user: dict = Depends(require("registry"))):
        clip = ctx.db.get_training_clip(clip_id)
        if not clip or not Path(clip["path"]).exists():
            raise HTTPException(404, "clip not available")
        return FileResponse(clip["path"], media_type="video/mp4")

    @app.delete("/api/train/clips/{clip_id}")
    def train_clip_delete(clip_id: int,
                          user: dict = Depends(require("registry"))):
        clip = ctx.db.get_training_clip(clip_id)
        if not ctx.db.delete_training_clip(clip_id, user["username"]):
            raise HTTPException(404, "no such clip")
        # the file stays on disk: labels are cheap to redo, footage is not
        return {"ok": True, "file_kept": clip["path"] if clip else None}

    @app.get("/api/train/marks")
    def train_marks(clip_id: int = 0,
                    user: dict = Depends(require("registry"))):
        return ctx.db.training_marks(clip_id or None)

    @app.post("/api/train/marks")
    def train_mark_add(clip_id: int = Form(...), start_s: float = Form(...),
                       end_s: float = Form(...), label: str = Form(...),
                       verdict: str = Form(...), note: str = Form(""),
                       user: dict = Depends(require("registry"))):
        if ctx.db.get_training_clip(clip_id) is None:
            raise HTTPException(404, "no such clip")
        try:
            mid = ctx.db.add_training_mark(clip_id, start_s, end_s,
                                           label.strip(), verdict, note.strip(),
                                           user["display_name"])
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "id": mid}

    @app.delete("/api/train/marks/{mark_id}")
    def train_mark_delete(mark_id: int,
                          user: dict = Depends(require("registry"))):
        if not ctx.db.delete_training_mark(mark_id):
            raise HTTPException(404, "no such mark")
        return {"ok": True}

    @app.get("/api/train/boxes")
    def train_boxes(clip_id: int = 0,
                    user: dict = Depends(require("registry"))):
        return ctx.db.training_boxes(clip_id or None)

    @app.post("/api/train/boxes")
    def train_box_add(clip_id: int = Form(...), t_s: float = Form(...),
                      cls: str = Form(...), x1: float = Form(...),
                      y1: float = Form(...), x2: float = Form(...),
                      y2: float = Form(...),
                      user: dict = Depends(require("registry"))):
        if ctx.db.get_training_clip(clip_id) is None:
            raise HTTPException(404, "no such clip")
        if cls not in train_mod.CLASSES:
            raise HTTPException(400,
                                f"class must be one of {', '.join(train_mod.CLASSES)}")
        try:
            bid = ctx.db.add_training_box(clip_id, t_s, cls, (x1, y1, x2, y2),
                                          user["display_name"])
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "id": bid}

    @app.delete("/api/train/boxes/{box_id}")
    def train_box_delete(box_id: int,
                         user: dict = Depends(require("registry"))):
        if not ctx.db.delete_training_box(box_id):
            raise HTTPException(404, "no such box")
        return {"ok": True}

    # ---- segmentation: outlines for one frame, and clicking one -----------
    _seg_cache = segment_mod.SegmentationCache(
        int((ctx.config.get("train") or {}).get("cache_frames", 64)))
    _seg_holder: dict = {}

    def _segmenter():
        """Built once, on first use. Loading a model per request would make
        every click pay for it."""
        if "s" not in _seg_holder:
            _seg_holder["s"] = segment_mod.build_segmenter(
                ctx.config.get("train") or {})
        return _seg_holder["s"]

    def _segment_frame(clip_id: int, timestamp_ms: float, force: bool = False):
        clip = ctx.db.get_training_clip(clip_id)
        if clip is None or not Path(clip["path"]).exists():
            raise HTTPException(404, "clip not available")
        if timestamp_ms < 0:
            raise HTTPException(400, "timestamp cannot be negative")
        try:
            frame, idx, fps, w, h = segment_mod.read_frame(clip["path"],
                                                           timestamp_ms)
        except ValueError as e:
            raise HTTPException(400, str(e))

        key = (clip_id, idx)
        if not force:
            cached = _seg_cache.get(key)
            if cached is not None:
                return cached
        seg = _segmenter()
        try:
            objects = seg.segment(frame, idx)
        except Exception as e:
            log.exception("segmentation failed on clip %s frame %s", clip_id, idx)
            raise HTTPException(503, f"segmentation is unavailable: {e}")
        result = segment_mod.FrameSegmentation(
            clip_id=clip_id, frame_index=idx,
            timestamp_ms=int(round(timestamp_ms)),
            frame_width=w, frame_height=h, model=seg.name, objects=objects)
        _seg_cache.put(key, result)
        return result

    @app.post("/api/train/clips/{clip_id}/segment-frame")
    def train_segment_frame(clip_id: int, timestamp_ms: float = Form(...),
                            force_refresh: bool = Form(False),
                            user: dict = Depends(require("registry"))):
        """Outline every object on one frame. Cached per (clip, frame)."""
        return _segment_frame(clip_id, timestamp_ms, force_refresh).public()

    @app.post("/api/train/clips/{clip_id}/select-object")
    def train_select_object(clip_id: int, timestamp_ms: float = Form(...),
                            display_x: float = Form(...),
                            display_y: float = Form(...),
                            display_width: float = Form(...),
                            display_height: float = Form(...),
                            radius: float = Form(60.0),
                            user: dict = Depends(require("registry"))):
        """What did they click?

        The conversion happens here rather than in the browser so there is one
        implementation of it, and it is the tested one.
        """
        result = _segment_frame(clip_id, timestamp_ms)
        if min(display_width, display_height) <= 0:
            raise HTTPException(400, "display size must be positive")
        fx, fy = tagging.to_frame_coords(display_x, display_y,
                                         display_width, display_height,
                                         result.frame_width, result.frame_height)
        on_picture = tagging.in_letterbox(display_x, display_y,
                                          display_width, display_height,
                                          result.frame_width, result.frame_height)
        # objects carry .polygons, so select() prefers outlines over boxes
        out = tagging.select(result.objects, fx, fy, radius)
        chosen = out["selected"]
        return {
            "clip_id": clip_id, "frame_index": result.frame_index,
            "frame_point": {"x": round(fx, 1), "y": round(fy, 1)},
            "clicked_on_video": on_picture,
            "selection_method": out["method"],
            "recommended_object_id":
                chosen.temporary_object_id if chosen else None,
            "recommended": chosen.public() if chosen else None,
            "overlapping_candidates":
                ([chosen.temporary_object_id] if chosen else [])
                + [o.temporary_object_id for o in out["alternatives"]],
            "objects": [o.public() for o in result.objects],
            "model": result.model,
        }

    @app.get("/api/train/segment-cache")
    def train_segment_cache(user: dict = Depends(require("registry"))):
        return {"entries": len(_seg_cache), "hits": _seg_cache.hits,
                "misses": _seg_cache.misses, "max": _seg_cache.max_entries}

    @app.delete("/api/train/segment-cache")
    def train_segment_cache_clear(user: dict = Depends(require("registry"))):
        """Cached AI results only. Saved annotations are in the database and
        are not touched by this."""
        _seg_cache.clear()
        return {"ok": True}

    # ---- tagged objects: the outline plus somebody's judgement ------------
    @app.get("/api/train/annotations")
    def train_annotations(clip_id: int = 0, frame_index: int = -1,
                          review_status: str = "",
                          user: dict = Depends(require("registry"))):
        rows = ctx.db.object_annotations(
            clip_id or None, frame_index if frame_index >= 0 else None,
            review_status)
        return [ann_mod.Annotation.from_row(r).public() for r in rows]

    @app.post("/api/train/annotations")
    def train_annotation_add(
            clip_id: int = Form(...), frame_index: int = Form(...),
            timestamp_ms: float = Form(...), category: str = Form(...),
            source: str = Form(...), frame_width: int = Form(...),
            frame_height: int = Form(...),
            original_polygon: str = Form(""), corrected_polygon: str = Form(""),
            x1: float = Form(None), y1: float = Form(None),
            x2: float = Form(None), y2: float = Form(None),
            custom_label: str = Form(""), tags: str = Form(""),
            notes: str = Form(""), detection_confidence: str = Form(""),
            user_confidence: str = Form(""), model: str = Form(""),
            track_id: str = Form(""), temporary_object_id: str = Form(""),
            user: dict = Depends(require("registry"))):
        """Save one tagged object.

        Everything arrives as a form because that is what the workbench sends
        and it keeps the page free of a JSON body builder. Validation happens
        in annotations.build(), which raises sentences rather than codes.
        """
        if ctx.db.get_training_clip(clip_id) is None:
            raise HTTPException(404, "no such clip")
        payload = {
            "clip_id": clip_id, "frame_index": frame_index,
            "timestamp_ms": timestamp_ms, "category": category,
            "source": source, "frame_width": frame_width,
            "frame_height": frame_height,
            "original_polygon": original_polygon,
            "corrected_polygon": corrected_polygon,
            "custom_label": custom_label, "tags": tags, "notes": notes,
            "detection_confidence": detection_confidence,
            "user_confidence": user_confidence, "model": model,
            "track_id": track_id, "temporary_object_id": temporary_object_id,
        }
        if None not in (x1, y1, x2, y2):
            payload["bbox"] = (x1, y1, x2, y2)
        try:
            ann = ann_mod.build(payload, user["username"])
        except ValueError as e:
            raise HTTPException(400, str(e))
        ann.id = ctx.db.add_object_annotation(ann.row())
        return ann.public()

    @app.post("/api/train/annotations/batch")
    def train_annotation_batch(
            clip_id: int = Form(...), frame_index: int = Form(...),
            timestamp_ms: float = Form(...), objects: str = Form(...),
            category: str = Form(""), keep_own_class: bool = Form(True),
            custom_label: str = Form(""), tags: str = Form(""),
            notes: str = Form(""), user_confidence: str = Form(""),
            user: dict = Depends(require("registry"))):
        """Tag everything that is selected, in one go.

        Selecting six cars and pressing save once is the difference between
        this being usable on a busy frame and not. `keep_own_class` is on by
        default because a mixed selection is the normal case — five cars and
        the person walking between them — and forcing them all to one category
        would silently mislabel the odd one out.
        """
        if ctx.db.get_training_clip(clip_id) is None:
            raise HTTPException(404, "no such clip")
        try:
            items = json.loads(objects)
        except ValueError as e:
            raise HTTPException(400, f"objects is not valid JSON: {e}")
        if not isinstance(items, list) or not items:
            raise HTTPException(400, "no objects were selected")
        if len(items) > 100:
            raise HTTPException(400, "that is more objects than one frame has")
        if not keep_own_class and not category:
            raise HTTPException(400, "choose a category, or keep each own class")

        built = []
        for i, o in enumerate(items):
            if not isinstance(o, dict):
                raise HTTPException(400, f"object {i + 1} is not an object")
            cls = (o.get("class_name") if keep_own_class else category) or category
            if cls not in ann_mod.CLASSES:
                cls = "unknown"
            polys = o.get("polygons") or []
            box = o.get("bbox")
            if isinstance(box, dict):
                box = (box.get("x_min"), box.get("y_min"),
                       box.get("x_max"), box.get("y_max"))
            payload = {
                "clip_id": clip_id, "frame_index": frame_index,
                "timestamp_ms": timestamp_ms, "category": cls,
                "source": ("yolo_segmentation" if polys
                           else "yolo_detection_fallback"),
                "frame_width": o.get("frame_width"),
                "frame_height": o.get("frame_height"),
                "original_polygon": polys, "bbox": box,
                "custom_label": custom_label, "tags": tags, "notes": notes,
                "detection_confidence": o.get("confidence"),
                "user_confidence": user_confidence,
                "model": o.get("model", ""), "track_id": o.get("track_id"),
                "temporary_object_id": o.get("temporary_object_id", ""),
            }
            try:
                built.append(ann_mod.build(payload, user["username"]))
            except ValueError as e:
                # named rather than numbered: "object 3" means nothing to
                # someone looking at a frame full of cars
                what = o.get("class_name") or f"object {i + 1}"
                raise HTTPException(400, f"{what}: {e}")

        ids = ctx.db.add_object_annotations([a.row() for a in built])
        for a, i in zip(built, ids):
            a.id = i
        return {"saved": len(ids), "annotations": [a.public() for a in built]}

    @app.patch("/api/train/annotations/{ann_id}")
    async def train_annotation_edit(ann_id: int, request: Request,
                                    user: dict = Depends(require("registry"))):
        """Edit what a person said, and where they moved the outline to.

        Only the fields actually present in the request are touched, so the
        page can save a dragged polygon without resending the tags, and the
        model's own polygon is unreachable from here by construction.
        """
        row = ctx.db.get_object_annotation(ann_id)
        if row is None:
            raise HTTPException(404, "no such annotation")
        form = await request.form()
        payload = {k: form[k] for k in
                   ("category", "custom_label", "corrected_polygon", "tags",
                    "notes", "user_confidence") if k in form}
        if not payload:
            raise HTTPException(400, "nothing to change")
        ann = ann_mod.Annotation.from_row(row)
        try:
            ann_mod.apply_edit(ann, payload, user["username"])
        except ValueError as e:
            raise HTTPException(400, str(e))
        ctx.db.update_object_annotation(ann_id, ann.row())
        return ann.public()

    @app.post("/api/train/annotations/{ann_id}/review")
    def train_annotation_review(ann_id: int, review_status: str = Form(...),
                                user: dict = Depends(require("registry"))):
        """draft -> submitted -> approved | rejected, and back to draft.

        Approving and rejecting need the account permission that also governs
        user management: signing off on a label is saying the system may be
        tuned against it, which is a different act from writing one.
        """
        row = ctx.db.get_object_annotation(ann_id)
        if row is None:
            raise HTTPException(404, "no such annotation")
        if review_status in ("approved", "rejected") and \
                not auth.can(user["role"], "users"):
            raise HTTPException(403, "only an admin can approve or reject a label")
        if not ann_mod.can_transition(row["review_status"], review_status):
            raise HTTPException(
                400, f"cannot go from {row['review_status']} to {review_status}")
        ctx.db.set_annotation_status(ann_id, review_status, user["username"])
        return ann_mod.Annotation.from_row(
            ctx.db.get_object_annotation(ann_id)).public()

    @app.delete("/api/train/annotations/{ann_id}")
    def train_annotation_delete(ann_id: int,
                                user: dict = Depends(require("registry"))):
        if not ctx.db.delete_object_annotation(ann_id):
            raise HTTPException(404, "no such annotation")
        ctx.db.append_audit(user["username"], "TRAINING_CHANGE",
                            {"op": "delete_annotation", "id": ann_id})
        return {"ok": True}

    # ---- following an object through the video ---------------------------
    _jobs: dict = {}
    _jobs_lock = threading.Lock()

    def _track_config() -> track_mod.TrackConfig:
        cfg = (ctx.config.get("train") or {}).get("tracking") or {}
        base = track_mod.TrackConfig()
        for k, v in cfg.items():
            if hasattr(base, k):
                setattr(base, k, type(getattr(base, k))(v))
        return base

    def _run_track(job: track_mod.TrackJob, clip: dict, anchor,
                   cfg: track_mod.TrackConfig, frame_w: int, frame_h: int,
                   label: str, tags: str, notes: str):
        """The whole job, on a worker thread. Never touches the request."""
        try:
            job.state = "running"
            seg = _segmenter()

            def frames():
                for idx, ts, img in segment_mod.iter_frames(
                        clip["path"], anchor.frame_index, cfg.stride,
                        cfg.max_frames + 1):
                    if job.cancel:
                        return
                    if idx == anchor.frame_index:
                        continue
                    yield idx, ts, seg.segment(img, idx)

            def progress(n, frame_index, _f):
                job.processed = n
                job.at_frame = frame_index

            tracklet = track_mod.follow(anchor, frames(), cfg,
                                        model=seg.name, on_progress=progress)
            job.tracklet = tracklet
            if job.cancel:
                job.state = "cancelled"
                return

            track_row = {
                "clip_id": clip["id"], "category": tracklet.category,
                "custom_label": label,
                "start_frame": tracklet.span[0], "end_frame": tracklet.span[1],
                "stride": cfg.stride, "frames": tracklet.frames,
                "observed": tracklet.observed,
                "reconstructed": tracklet.reconstructed,
                "flagged": tracklet.flagged, "lost_at": tracklet.lost_at,
                "lost_why": tracklet.lost_why, "model": tracklet.model,
                "tags": json.dumps(ann_mod.clean_tags(tags)), "notes": notes,
                "review_status": "draft", "created_by": job.requested_by,
                "created_at": time.time(),
            }
            track_id = ctx.db.add_object_track(track_row)
            job.track_id = track_id

            rows = []
            for o in tracklet.observations:
                a = ann_mod.Annotation(
                    clip_id=clip["id"], frame_index=o.frame_index,
                    timestamp_ms=o.timestamp_ms,
                    category=(tracklet.category
                              if tracklet.category in ann_mod.CLASSES
                              else "unknown"),
                    source=("interpolated" if o.kind == "interpolated"
                            else "tracked"),
                    bbox=o.bbox, frame_width=frame_w, frame_height=frame_h,
                    original_polygon=o.polygons, custom_label=label,
                    detection_confidence=(round(o.confidence, 3)
                                          if o.confidence else None),
                    model=tracklet.model, track_ref=track_id,
                    mask_source=o.kind, needs_review=o.needs_review,
                    review_note=o.why, created_by=job.requested_by,
                    created_at=time.time(), updated_by=job.requested_by,
                    updated_at=time.time())
                rows.append(a.row())
            job.saved = ctx.db.add_object_annotations(rows)
            job.state = "done"
        except Exception as e:                       # noqa: BLE001
            log.exception("tracking job %s failed", job.id)
            job.state = "failed"
            job.error = str(e)
        finally:
            job.finished = time.time()

    @app.post("/api/train/clips/{clip_id}/track")
    def train_track_start(clip_id: int, timestamp_ms: float = Form(...),
                          temporary_object_id: str = Form(""),
                          display_x: float = Form(None),
                          display_y: float = Form(None),
                          display_width: float = Form(None),
                          display_height: float = Form(None),
                          custom_label: str = Form(""), tags: str = Form(""),
                          notes: str = Form(""), stride: int = Form(0),
                          max_frames: int = Form(0),
                          user: dict = Depends(require("registry"))):
        """Follow one object forward from the frame it was picked on.

        Identify it either by the id from a previous segment-frame call, or by
        where it was clicked. The click form exists so following something is
        one action rather than two, which matters when there are forty objects
        on the frame and only one of them is walking away with a bicycle.
        """
        clip = ctx.db.get_training_clip(clip_id)
        if clip is None or not Path(clip["path"]).exists():
            raise HTTPException(404, "clip not available")

        result = _segment_frame(clip_id, timestamp_ms)
        chosen = None
        if temporary_object_id:
            chosen = next((o for o in result.objects
                           if o.temporary_object_id == temporary_object_id), None)
        elif None not in (display_x, display_y, display_width, display_height):
            if min(display_width, display_height) <= 0:
                raise HTTPException(400, "display size must be positive")
            fx, fy = tagging.to_frame_coords(display_x, display_y,
                                             display_width, display_height,
                                             result.frame_width,
                                             result.frame_height)
            chosen = tagging.select(result.objects, fx, fy)["selected"]
        if chosen is None:
            raise HTTPException(404, "there is no object there to follow")
        if not chosen.polygons:
            raise HTTPException(
                400, "that object has no outline, only a box — following it "
                     "would have nothing to match on")

        cfg = _track_config()
        if stride:
            cfg.stride = max(1, min(10, stride))
        if max_frames:
            cfg.max_frames = max(1, min(2000, max_frames))

        anchor = track_mod.observation_from(chosen, result.frame_index,
                                            result.timestamp_ms)
        job = track_mod.TrackJob(secrets.token_hex(8), clip_id,
                                 result.frame_index, user["username"])
        # how many processed frames are actually left in the clip
        try:
            _, total_frames, _, _ = segment_mod.clip_shape(clip["path"])
        except ValueError:
            total_frames = 0
        remaining = max(0, total_frames - result.frame_index - 1)
        job.total = min(cfg.max_frames, remaining // max(1, cfg.stride))

        with _jobs_lock:
            _jobs[job.id] = job
            # a workbench is used by one person at a time; keeping the last
            # few dozen jobs is plenty and stops this growing without bound
            if len(_jobs) > 40:
                for old in sorted(_jobs.values(),
                                  key=lambda j: j.started)[:len(_jobs) - 40]:
                    _jobs.pop(old.id, None)

        threading.Thread(
            target=_run_track, daemon=True,
            args=(job, clip, anchor, cfg, result.frame_width,
                  result.frame_height, custom_label, tags, notes)).start()
        return job.public()

    @app.get("/api/train/track-jobs/{job_id}")
    def train_track_job(job_id: str, user: dict = Depends(require("registry"))):
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        return job.public()

    @app.delete("/api/train/track-jobs/{job_id}")
    def train_track_cancel(job_id: str,
                           user: dict = Depends(require("registry"))):
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        job.cancel = True
        return job.public()

    @app.get("/api/train/tracks")
    def train_tracks(clip_id: int = 0,
                     user: dict = Depends(require("registry"))):
        return ctx.db.object_tracks(clip_id or None)

    @app.get("/api/train/tracks/{track_id}")
    def train_track(track_id: int, user: dict = Depends(require("registry"))):
        row = ctx.db.get_object_track(track_id)
        if row is None:
            raise HTTPException(404, "no such track")
        rows = ctx.db.object_annotations(track_ref=track_id)
        return {"track": row,
                "frames": [ann_mod.Annotation.from_row(r).public()
                           for r in rows]}

    @app.patch("/api/train/tracks/{track_id}")
    async def train_track_edit(track_id: int, request: Request,
                               user: dict = Depends(require("registry"))):
        if ctx.db.get_object_track(track_id) is None:
            raise HTTPException(404, "no such track")
        form = await request.form()
        fields = {k: form[k] for k in ("category", "custom_label", "notes")
                  if k in form}
        if "tags" in form:
            try:
                fields["tags"] = json.dumps(ann_mod.clean_tags(form["tags"]))
            except ValueError as e:
                raise HTTPException(400, str(e))
        if "category" in fields and fields["category"] not in ann_mod.CLASSES:
            raise HTTPException(
                400, f"category must be one of {', '.join(ann_mod.CLASSES)}")
        if not fields:
            raise HTTPException(400, "nothing to change")
        ctx.db.update_object_track(track_id, **fields)
        # the frames carry the label too, so they stay in step
        for r in ctx.db.object_annotations(track_ref=track_id):
            a = ann_mod.Annotation.from_row(r)
            if "category" in fields:
                a.category = fields["category"]
            if "custom_label" in fields:
                a.custom_label = fields["custom_label"]
            a.updated_by, a.updated_at = user["username"], time.time()
            ctx.db.update_object_annotation(a.id, a.row())
        return ctx.db.get_object_track(track_id)

    @app.post("/api/train/tracks/{track_id}/review")
    def train_track_review(track_id: int, review_status: str = Form(...),
                           user: dict = Depends(require("registry"))):
        """One judgement about the whole path, applied to every frame in it."""
        row = ctx.db.get_object_track(track_id)
        if row is None:
            raise HTTPException(404, "no such track")
        if review_status in ("approved", "rejected") and \
                not auth.can(user["role"], "users"):
            raise HTTPException(403, "only an admin can approve or reject")
        if not ann_mod.can_transition(row["review_status"], review_status):
            raise HTTPException(
                400, f"cannot go from {row['review_status']} to {review_status}")
        n = ctx.db.set_track_status(track_id, review_status, user["username"])
        return {"track_id": track_id, "review_status": review_status,
                "frames_changed": n}

    @app.delete("/api/train/tracks/{track_id}")
    def train_track_delete(track_id: int,
                           user: dict = Depends(require("registry"))):
        if not ctx.db.delete_object_track(track_id):
            raise HTTPException(404, "no such track")
        ctx.db.append_audit(user["username"], "TRAINING_CHANGE",
                            {"op": "delete_track", "id": track_id})
        return {"ok": True}

    # ---- connecting cameras, without editing a file or restarting ---------
    # Every one of these is behind `require("registry")` — the real
    # server-side session, not the HTTP Basic gate that config.yaml disables.
    # They scan the local network and store DVR passwords, so an open endpoint
    # here would be considerably worse than an open one anywhere else.
    _scan_lock = threading.Lock()

    @app.get("/api/cameras/list")
    def cameras_list(user: dict = Depends(require("registry"))):
        """Configured cameras and their live state. Passwords masked."""
        contexts = ctx.db.list_camera_context()

        def _ctx(name):
            c = contexts.get(name, {})
            return {"label": c.get("label", ""), "block": c.get("block", ""),
                    "facing": c.get("facing", ""), "notes": c.get("notes", ""),
                    "location": db_mod.describe_camera_context(name, c or None)}

        out = []
        for cam in ctx.db.list_cameras():
            worker = ctx.workers.get(cam["name"])
            out.append({**cam, "url": discovery.mask(cam["url"]),
                        "running": worker is not None,
                        "online": bool(worker and worker.online),
                        "from": "database", **_ctx(cam["name"])})
        for cam in ctx.config.get("cameras", []):
            worker = ctx.workers.get(cam["name"])
            out.append({"name": cam["name"], "url": discovery.mask(cam["url"]),
                        "enabled": 1, "running": worker is not None,
                        "online": bool(worker and worker.online),
                        "from": "config.yaml", **_ctx(cam["name"])})
        return out

    @app.post("/api/cameras/context")
    def cameras_context(name: str = Form(...), label: str = Form(""),
                        block: str = Form(""), facing: str = Form(""),
                        notes: str = Form(""),
                        user: dict = Depends(require("registry"))):
        """Tell the system where a camera is and what it faces.

        Keyed by name, so it works the same for a camera added here and one
        defined in config.yaml. This is what makes an alert say "B-Block gate,
        facing main road" instead of the bare device name.
        """
        name = name.strip()
        if not name:
            raise HTTPException(400, "which camera?")
        ctx.db.set_camera_context(name, label=label, block=block,
                                  facing=facing, notes=notes,
                                  actor=user["username"])
        return {"ok": True, "name": name,
                "location": ctx.db.describe_camera(name)}

    @app.get("/api/cameras/{name}/zones")
    def cameras_zones_get(name: str,
                          user: dict = Depends(require("registry"))):
        """The drawn zones for a camera, plus its frame size so the editor can
        map clicks to source pixels."""
        zones = ctx.db.get_camera_zones(name) or db_mod.clean_zones({})
        cam = ctx.db.camera_by_name(name)
        w = (cam or {}).get("width") or 0
        h = (cam or {}).get("height") or 0
        return {"name": name, "zones": zones, "width": w, "height": h}

    @app.post("/api/cameras/zones")
    def cameras_zones_set(name: str = Form(...), zones_json: str = Form(...),
                          user: dict = Depends(require("registry"))):
        """Save the polygons a guard drew, and apply them to the running camera
        at once. Keyed by name, so a config.yaml camera can carry zones too."""
        name = name.strip()
        if not name:
            raise HTTPException(400, "which camera?")
        try:
            zones = json.loads(zones_json)
        except (ValueError, TypeError):
            raise HTTPException(400, "zones_json is not valid JSON")
        if not isinstance(zones, dict):
            raise HTTPException(400, "zones must be an object of {kind: polygon}")
        setter = getattr(ctx, "set_camera_zones", None)
        clean = setter(name, zones, user["username"]) if setter \
            else ctx.db.set_camera_zones(name, zones, user["username"])
        return {"ok": True, "name": name, "zones": clean,
                "counts": {k: len(v) for k, v in clean.items()}}

    @app.post("/api/cameras/scan")
    def cameras_scan(network: str = Form(...),
                     user: dict = Depends(require("registry"))):
        """Which hosts on this network have an RTSP port open."""
        if not _scan_lock.acquire(blocking=False):
            raise HTTPException(429, "a scan is already running")
        try:
            found = discovery.scan(network)
        except ValueError as e:
            raise HTTPException(400, str(e))
        finally:
            _scan_lock.release()
        return {"network": network, "found": [d.public() for d in found],
                "hint": ("Nothing answered on port 554. Check you are on the "
                         "same network as the DVR, and that the range is "
                         "right — it is usually a /24 like 192.168.1.0/24."
                         if not found else "")}

    @app.post("/api/cameras/probe")
    def cameras_probe(host: str = Form(...), username: str = Form("admin"),
                      password: str = Form(""), channels: int = Form(4),
                      port: int = Form(discovery.RTSP_PORT),
                      user: dict = Depends(require("registry"))):
        """Try the known RTSP paths for each channel until one gives a frame.

        Substreams are tried before main streams: a 4K main stream saturates a
        cheap box and buys nothing, because detection runs at 640px.
        """
        if channels < 1 or channels > 32:
            raise HTTPException(400, "channels must be between 1 and 32")
        dev = discovery.probe_device(host, username, password, channels, port)
        ok = discovery.working(dev)
        return {"host": host, "channels": [c.public() for c in dev.channels],
                "working": len(ok), "advice": discovery.advice(dev)}

    @app.post("/api/cameras/add")
    def cameras_add(name: str = Form(...), host: str = Form(...),
                    channel: int = Form(1), username: str = Form("admin"),
                    password: str = Form(""),
                    port: int = Form(discovery.RTSP_PORT),
                    vendor: str = Form(""),
                    user: dict = Depends(require("registry"))):
        """Prove the stream works, store it, and start it — no restart.

        The URL is rebuilt here from host/channel/credentials rather than
        accepted from the browser, so the password never has to make a round
        trip through a page where it could be logged or cached.
        """
        name = name.strip()
        if not name:
            raise HTTPException(400, "the camera needs a name")
        if ctx.db.camera_by_name(name) or name in ctx.workers:
            raise HTTPException(409, f"a camera called {name!r} already exists")

        found = discovery.probe_channel(host, username, password, channel, port)
        if found is None or not found.ok:
            raise HTTPException(
                400, f"no working stream on channel {channel}: "
                     f"{found.error if found else 'nothing tried'}")

        cam_id = ctx.db.add_camera(name, found.url, vendor or found.vendor,
                                   channel, found.width, found.height,
                                   added_by=user["username"])
        ctx.db.append_audit(user["username"], "CAMERA_CHANGE",
                            {"op": "add", "name": name,
                             "url": discovery.mask(found.url)})
        started = False
        if hasattr(ctx, "start_camera"):
            try:
                started = ctx.start_camera(name, found.url)
            except Exception as e:                       # noqa: BLE001
                log.exception("could not start camera %s", name)
                raise HTTPException(
                    500, f"stored, but it would not start: {e}")
        return {"id": cam_id, "name": name, "url": found.safe_url,
                "vendor": found.vendor, "channel": channel,
                "width": found.width, "height": found.height,
                "running": started,
                "note": "" if started else
                        "stored — it will start when the system next runs"}

    @app.delete("/api/cameras/{camera_id}")
    def cameras_delete(camera_id: int,
                       user: dict = Depends(require("registry"))):
        cam = ctx.db.get_camera(camera_id)
        if cam is None:
            raise HTTPException(404, "no such camera")
        if hasattr(ctx, "stop_camera"):
            ctx.stop_camera(cam["name"])
        ctx.db.delete_camera(camera_id, user["username"])
        return {"ok": True, "name": cam["name"]}

    @app.post("/api/cameras/{camera_id}/enabled")
    def cameras_enabled(camera_id: int, enabled: bool = Form(...),
                        user: dict = Depends(require("registry"))):
        cam = ctx.db.get_camera(camera_id)
        if cam is None:
            raise HTTPException(404, "no such camera")
        ctx.db.set_camera_enabled(camera_id, enabled)
        if hasattr(ctx, "start_camera"):
            if enabled:
                ctx.start_camera(cam["name"], cam["url"])
            else:
                ctx.stop_camera(cam["name"])
        return {"ok": True, "name": cam["name"], "enabled": enabled}

    @app.get("/api/train/annotations/stats")
    def train_annotation_stats(clip_id: int = 0,
                               user: dict = Depends(require("registry"))):
        return ann_mod.summarise(ctx.db.object_annotations(clip_id or None))

    @app.get("/api/train/annotations/export")
    def train_annotation_export(user: dict = Depends(require("registry"))):
        """COCO instance segmentation — the format training pipelines read.

        Rejected labels are left out; everything else carries its review
        status so a consumer can decide how much unfinished work to trust.
        """
        clips = {c["id"]: c for c in ctx.db.list_training_clips()}
        doc = ann_mod.to_coco(ctx.db.object_annotations(), clips)
        return JSONResponse(doc, headers={
            "Content-Disposition": 'attachment; filename="annotations.json"'})

    @app.get("/api/train/export")
    def train_export(user: dict = Depends(require("registry"))):
        """Hand the labels to the harnesses in the format they already read.

        This is the whole point of the page: what someone marks here becomes
        `labels.csv`, which is what validate_triggers.py and evaluate_alerts.py
        measure against.
        """
        rows = train_mod.to_labels_csv(ctx.db)
        return Response(rows, media_type="text/csv", headers={
            "Content-Disposition": 'attachment; filename="labels.csv"'})

    # ------------------------------------------------------- parking slots
    @app.get("/api/slots")
    def slots(camera: str = "", user: dict = Depends(require("gate"))):
        rows = ctx.db.list_slots(camera or None)
        occupancy = {}
        for name, pipe in ctx.pipelines.items():
            tracker = getattr(pipe, "slots", None)
            if tracker is not None:
                occupancy.update(tracker.occupancy())
        for r in rows:
            r.pop("polygon_json", None)
            r["occupant"] = occupancy.get(r["id"])
        return rows

    @app.post("/api/slots")
    def slot_add(camera: str = Form(...), label: str = Form(...),
                 polygon: str = Form(...), plate: str = Form(""),
                 flat_number: str = Form(""),
                 user: dict = Depends(require("registry"))):
        """polygon is a JSON array of [x, y] pairs in source-frame pixels."""
        import json as _json
        try:
            points = _json.loads(polygon)
        except ValueError:
            raise HTTPException(400, "polygon must be JSON")
        if not isinstance(points, list) or len(points) < 3:
            raise HTTPException(400, "a slot needs at least three points")
        if not label.strip():
            raise HTTPException(400, "label is required")
        sid = ctx.db.add_slot(camera, label.strip(), points,
                              normalize_plate(plate) or None,
                              flat_number.strip(), actor=user["username"])
        return {"ok": True, "id": sid}

    @app.delete("/api/slots/{slot_id}")
    def slot_remove(slot_id: int, user: dict = Depends(require("registry"))):
        if not ctx.db.remove_slot(slot_id, actor=user["username"]):
            raise HTTPException(404, "no such slot")
        return {"ok": True}

    @app.get("/api/slots/activity")
    def slot_activity(limit: int = 200, plate: str = "",
                      user: dict = Depends(require("gate"))):
        """Arrivals and departures — the answer to 'when did my car leave?'"""
        return ctx.db.slot_activity(min(limit, 1000),
                                    plate=normalize_plate(plate) or None)

    @app.get("/api/damage")
    def damage_lookup(plate: str = "", slot_id: int = 0, since: float = 0,
                      until: float = 0, user: dict = Depends(require("gate"))):
        """What could have marked this vehicle while it was parked.

        Narrowed to the periods the slot map says it was actually in its space,
        on the camera that watches that space — which is the difference between
        a searchable question and eleven hours of footage nobody will watch.
        """
        if not plate and not slot_id:
            raise HTTPException(400, "give a plate or a slot_id")
        return damage_mod.search(ctx.db, normalize_plate(plate) or None,
                                 slot_id or None, since or None, until or None)

    # --------------------------------------------------- visitor log (gate)
    @app.get("/api/visits")
    def visits(limit: int = 200, plate: str = "", registered: str = "",
               user: dict = Depends(require("gate"))):
        """The gate register, newest first. `registered` filters residents
        (1/true) from visitors (0/false); blank returns both."""
        reg = None
        if registered != "":
            reg = registered.lower() in ("1", "true", "yes")
        return ctx.db.recent_visits(min(limit, 1000),
                                    normalize_plate(plate) or None, reg)

    @app.get("/api/visits/open")
    def visits_open(user: dict = Depends(require("gate"))):
        """Vehicles currently inside — 'who is in the society right now'."""
        return ctx.db.open_visits()

    @app.get("/api/visits/overstays")
    def visits_overstays(hours: float = 0,
                         user: dict = Depends(require("gate"))):
        """Unregistered vehicles still inside past the overstay threshold."""
        cfg = ctx.config.get("visitor_log") or {}
        return ctx.db.overstaying_visits(
            hours or float(cfg.get("overstay_hours", 12)))

    # --------------------------------------------------------------- clips
    @app.get("/clips/{clip_id}")
    def clip_file(clip_id: int, user: dict = Depends(require("triage"))):
        clip = ctx.db.get_clip(clip_id)
        if not clip or clip["deleted"] or not Path(clip["path"]).exists():
            raise HTTPException(404, "clip not available")
        return FileResponse(clip["path"], media_type="video/mp4")

    @app.post("/api/clips/{clip_id}/delete")
    def clip_delete(clip_id: int, name: str = Form(...), reason: str = Form(...), user: dict = Depends(require("registry"))):
        if not name.strip() or not reason.strip():
            raise HTTPException(400, "name and reason are required")
        if not clips_mod.delete_clip_file(ctx.db, clip_id, name.strip(), reason.strip()):
            raise HTTPException(404, "clip not found or already deleted")
        return {"ok": True}

    # --------------------------------------------- upload & analyze video
    @app.post("/api/analyze")
    async def analyze_upload(file: UploadFile = File(...),
                             zones_from: str = Form(""),
                             ai_review: str = Form(""),
                             user: dict = Depends(require("triage"))):
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
    def analyze_status(job_id: str, user: dict = Depends(require("triage"))):
        analyzer = getattr(ctx, "analyzer", None)
        job = analyzer.get(job_id) if analyzer else None
        if job is None:
            raise HTTPException(404, "job not found")
        return job.public()

    @app.get("/api/analyze/{job_id}/video")
    def analyze_video_file(job_id: str, user: dict = Depends(require("triage"))):
        analyzer = getattr(ctx, "analyzer", None)
        job = analyzer.get(job_id) if analyzer else None
        if job is None or not job.annotated_path or \
                not Path(job.annotated_path).exists():
            raise HTTPException(404, "annotated video not available")
        return FileResponse(job.annotated_path, media_type="video/mp4")

    @app.get("/api/cameras")
    def cameras(user: dict = Depends(require("triage"))):
        return [c.get("name") for c in ctx.config.get("cameras", [])]

    # ------------------------------------------------ Claude tuning chatbot
    @app.post("/api/assistant")
    async def assistant_chat(request: Request,
                             user: dict = Depends(require("users"))):
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
    async def assistant_apply(request: Request,
                              user: dict = Depends(require("users"))):
        asst = getattr(ctx, "assistant", None)
        body = await request.json()
        patch = body.get("patch") or {}
        result = assistant_mod.apply_patch(
            ctx.config_path, ctx.config, patch, db=ctx.db, actor="dashboard")
        return result

    # -------------------------------------------------------- cost meter
    @app.get("/api/costs")
    def costs(user: dict = Depends(require("users"))):
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
    def audit(limit: int = 200, user: dict = Depends(require("users"))):
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
::-webkit-scrollbar{width:8px;height:8px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:rgba(0,0,0,.18);border-radius:10px}
::-webkit-scrollbar-thumb:hover{background:rgba(0,0,0,.32)}
::selection{background:color-mix(in oklab,var(--cyan) 35%,transparent);color:var(--text)}
:focus-visible{outline:2px solid color-mix(in oklab,var(--cyan) 65%,transparent);outline-offset:2px;border-radius:4px}

.glass{background:var(--surface);border:1px solid var(--border);border-radius:16px;box-shadow:var(--shadow)}

/* top bar */
header{position:fixed;top:0;left:0;right:0;z-index:50;height:56px;
 display:flex;align-items:center;justify-content:space-between;padding:0 20px;
 background:color-mix(in oklab,var(--bg) 85%,transparent);backdrop-filter:blur(10px);
 border-bottom:1px solid var(--border)}
.brand b{color:var(--text);font-family:'Outfit',sans-serif;font-size:21px;font-weight:800;letter-spacing:-.02em}
.top-right{display:flex;align-items:center;gap:12px;font-size:12px;color:var(--muted)}
#clock{font-variant-numeric:tabular-nums}
.user-chip{color:var(--muted);font-size:12px}
.signout{background:none;border:none;color:var(--muted);font-family:inherit;font-size:12px;
 font-weight:600;padding:4px 10px;border-radius:999px;cursor:pointer;transition:all .2s}
.signout:hover{background:rgba(0,0,0,.05);color:var(--text)}

/* sidebar */
aside{position:fixed;left:8px;top:64px;bottom:8px;width:220px;z-index:40;
 display:flex;flex-direction:column;border-radius:14px;background:var(--surface);
 border:1px solid var(--border);box-shadow:var(--shadow);overflow:hidden}
nav{flex:1;padding:16px 10px;overflow-y:auto}
nav a{display:block;padding:11px 14px;margin:2px 0;border-radius:10px;color:var(--muted);
 text-decoration:none;font-size:13.5px;font-weight:600;letter-spacing:.01em;cursor:pointer;transition:all .2s}
nav a:hover{background:rgba(0,0,0,.04);color:var(--text)}
nav a.active{background:color-mix(in oklab,var(--cyan) 14%,transparent);color:var(--text)}
.aside-foot{padding:14px 18px;border-top:1px solid var(--border);
 font-size:10px;color:var(--muted);display:flex;align-items:center;gap:8px;
 letter-spacing:.1em;text-transform:uppercase}
.pulse-dot{width:8px;height:8px;border-radius:50%;background:var(--green);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:.55}50%{opacity:1}}

/* main */
main{margin-left:240px;padding:76px 20px 40px}
@media(max-width:860px){aside{display:none}main{margin-left:0}}
body:not(.authed) .app-chrome{display:none!important}
.view{display:none}
.view.active{display:block;animation:fadein .3s ease}
@keyframes fadein{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.page-head{display:flex;align-items:flex-end;justify-content:space-between;
 flex-wrap:wrap;gap:12px;margin-bottom:20px}
.page-head h1{font-size:26px;font-weight:800;letter-spacing:-.01em;color:var(--text)}
.page-head p{color:var(--muted);font-size:13px;margin-top:4px;max-width:600px;line-height:1.55}
.live-pill{display:inline-flex;align-items:center;gap:6px;padding:3px 12px;border-radius:999px;
 background:color-mix(in oklab,var(--cyan) 12%,transparent);
 border:1px solid color-mix(in oklab,var(--cyan) 30%,transparent);color:var(--cyan-dim);
 font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;margin-bottom:6px}
.live-pill i{width:7px;height:7px;border-radius:50%;background:var(--cyan);animation:pulse 2s infinite}

/* camera wall */
.cam-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}
.cam-card{padding:12px;position:relative;cursor:pointer;transition:transform .15s,box-shadow .15s}
.cam-card:hover{transform:translateY(-2px);box-shadow:var(--shadow)}
.cam-card img{width:100%;aspect-ratio:16/9;object-fit:cover;border-radius:11px;
 background:#111;border:1px solid var(--border);display:block}
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
.intel-head{padding:14px 16px;border-bottom:1px solid var(--border);background:var(--surface-2)}
.intel-head b{font-size:15px;font-family:'Outfit',sans-serif;font-weight:700}
.intel-body{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:14px}
.intel-item{display:flex;gap:10px}
.intel-ic{width:10px;height:10px;border-radius:50%;margin-top:4px;flex-shrink:0;background:var(--muted-soft)}
.intel-ic.HIGH{background:var(--red)}
.intel-ic.MEDIUM{background:var(--amber)}
.intel-ic.LOW{background:var(--cyan)}
.intel-item .t{font-size:12px;line-height:1.4;color:var(--text)}
.intel-item .m{font-size:10px;color:var(--muted);margin-top:2px;display:flex;gap:6px;flex-wrap:wrap}
.tag{padding:1px 7px;background:var(--surface-2);border-radius:5px;font-size:9px;color:var(--muted)}

/* enlarged camera modal */
/* camera onboarding */
.cam-row{display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap;
  margin-bottom:12px}
.cam-row label{display:flex;flex-direction:column;gap:4px;font-size:11px;
  letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.cam-row input{font:inherit;padding:8px 10px;border-radius:8px;
  border:1px solid rgba(0,0,0,.14);background:#fff;min-width:150px}
.cam-msg{font-size:12.5px;color:var(--muted);align-self:center}
.cam-msg .ok,td .ok{color:#0a7c3f;font-weight:650}
.cam-modal{position:fixed;inset:0;z-index:150;display:none;align-items:center;justify-content:center;
 padding:24px;background:rgba(0,0,0,.55);backdrop-filter:blur(4px)}
.cam-modal-inner{width:100%;max-width:1100px;padding:12px}
.cam-modal-inner img{width:100%;max-height:78vh;object-fit:contain;border-radius:10px;
 background:#111;border:1px solid var(--border);display:block}
.cam-modal-bar{display:flex;align-items:center;justify-content:space-between;padding:12px 6px 4px}

/* forensic lab */
.dropzone{position:relative;border-radius:16px;border:1px dashed var(--border-strong);
 background:var(--surface);padding:44px 20px;text-align:center;transition:all .25s;cursor:pointer}
.dropzone:hover{border-color:var(--cyan)}
.dropzone.drag{background:color-mix(in oklab,var(--cyan) 8%,transparent);border-color:var(--cyan)}
.drop-orb{width:92px;height:92px;margin:0 auto 18px;border-radius:50%;
 background:color-mix(in oklab,var(--cyan) 12%,white);border:1px solid var(--border);
 display:flex;align-items:center;justify-content:center;color:var(--cyan-dim)}
.dropzone h2{font-size:19px;margin-bottom:6px}
.dropzone p{color:var(--muted);font-size:12px;margin-bottom:16px}
.btn{display:inline-flex;align-items:center;gap:8px;padding:10px 22px;border:none;
 border-radius:999px;font-family:inherit;font-size:12px;font-weight:700;letter-spacing:.08em;
 text-transform:uppercase;cursor:pointer;background:var(--cyan);color:#fff;transition:all .2s}
.btn:hover{background:var(--cyan-dim)}
.btn.grad{background:var(--cyan);color:#fff}
.btn.grad:hover{background:var(--cyan-dim)}
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

/* AI verdict banner — red when Claude finds something, green when it clears
   the clip. An all-clear is a real result, so it gets the same weight as an
   alert rather than a footnote. */
.threat-banner{display:none;margin:14px 0;padding:16px 20px;border-radius:14px;
 background:color-mix(in oklab,var(--red) 8%,white);
 border:1px solid color-mix(in oklab,var(--red) 30%,transparent);
 border-left:4px solid var(--red)}
.threat-banner h3{color:var(--red-deep);font-size:16px;text-transform:uppercase;letter-spacing:.04em}
.threat-banner p{color:var(--muted);font-size:12px;margin-top:4px;line-height:1.5}
.threat-banner.clear{background:color-mix(in oklab,var(--green) 9%,white);
 border-color:color-mix(in oklab,var(--green) 32%,transparent);
 border-left-color:var(--green)}
.threat-banner.clear h3{color:var(--green)}
.threat-banner .vhead{display:flex;align-items:center;gap:10px}
.threat-banner .vdot{width:11px;height:11px;border-radius:50%;background:var(--red);flex-shrink:0}
.threat-banner.clear .vdot{background:var(--green)}

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
 letter-spacing:-.02em;color:var(--text)}
.login-sub{font-size:11px;color:var(--muted-soft);letter-spacing:.06em;margin-top:2px}
.login-lead{margin-top:16px;font-size:14px;color:var(--muted)}
.login-card .lbl{display:block;margin-top:18px;margin-bottom:6px;font-size:10px;
 text-transform:uppercase;letter-spacing:.14em;color:var(--muted);font-weight:600}
.login-card input{width:100%}
.login-err{margin-top:12px;font-size:13px;color:var(--red-deep)}
.login-note{margin-top:16px;padding:10px 12px;border-radius:10px;
 background:var(--surface-2);font-size:11px;line-height:1.55;color:var(--muted)}
.login-foot{margin-top:12px;text-align:center;font-size:12px;color:var(--muted-soft)}

@media(prefers-reduced-motion:reduce){
 *,*::before,*::after{animation-duration:.001ms!important;animation-iteration-count:1!important;
  transition-duration:.001ms!important;scroll-behavior:auto!important}
}
</style></head><body>

<div id="login-gate" class="login-gate">
 <form class="glass login-card" onsubmit="return doLogin(event)">
  <div class="login-brand">VisionGuard</div>
  <div class="login-sub">SOCIETY WATCH</div>
  <p class="login-lead">Private investor preview — please sign in.</p>
  <label class="lbl" for="lg_user">Username</label>
  <input id="lg_user" type="text" autocomplete="username" autofocus>
  <label class="lbl" for="lg_pass">Password</label>
  <input id="lg_pass" type="password" autocomplete="current-password">
  <div id="lg_err" class="login-err"></div>
  <button class="btn grad" type="submit" style="width:100%;margin-top:20px">Sign in</button>
  <p class="login-note">This is a working prototype under active development, so
   some steps may be slow or behave unexpectedly.</p>
  <p class="login-foot">Access is by invitation.</p>
 </form>
</div>

<header class="app-chrome">
 <div class="brand"><b>VisionGuard</b></div>
 <div class="top-right">
  <span id="clock"></span>
  <span id="app-user" class="user-chip"></span>
  <button class="signout" type="button" onclick="signOut()">Sign out</button>
 </div>
</header>

<aside class="app-chrome">
 <nav>
  <a data-v="view" class="active" onclick="show('view')">View</a>
  <a data-v="lab" onclick="show('lab')">Forensic Lab</a>
  <a data-v="events" onclick="show('events')">Events</a>
  <a data-v="cameras" onclick="show('cameras')">Cameras</a>
  <a href="/train">Train</a>
  <a href="/operator">Operator</a>
 </nav>
 <div class="aside-foot"><span class="pulse-dot"></span> System vigilant</div>
</aside>

<main class="app-chrome">

<!-- ======================= VIEW / CAMERA WALL ======================= -->
<section class="view active" id="view-view">
 <div class="page-head">
  <div>
   <span class="live-pill"><i></i> Live</span>
   <h1>Camera Wall</h1>
   <p>Annotated live feeds — green boxes mark flagged people. Click any camera to enlarge.</p>
  </div>
 </div>
 <div class="live-wrap">
  <div class="cam-grid" id="cams"></div>
  <div class="glass intel">
   <div class="intel-head"><b>Intelligence</b></div>
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
  <div class="drop-orb"><svg viewBox="0 0 24 24" width="34" height="34" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 16V4M6 10l6-6 6 6M4 20h16"/></svg></div>
  <h2>Ingest CCTV Footage</h2>
  <p>Drag &amp; drop a video here, or browse. mp4 / avi / mov / mkv / dav — deleted after analysis.</p>
  <button class="btn" type="button">Browse Local Files</button>
  <input type="file" id="up_file" accept="video/*" style="display:none">
 </div>

 <div class="lab-controls">
  <label class="switch"><input type="checkbox" id="up_ai"><span class="track"></span>
   Smart AI Review (Claude watches the video — needs API key)</label>
  <label style="font-size:13px;color:var(--muted)">Zones from:
   <select id="up_zones"><option value="">(none — tamper/contact only)</option></select>
  </label>
  <button class="btn grad" onclick="analyze()">Analyze</button>
  <span class="status-line" id="up_status"></span>
 </div>

 <div class="threat-banner" id="threat_banner">
  <div class="vhead"><span class="vdot"></span><h3 id="threat_title"></h3></div>
  <p id="threat_sub"></p>
 </div>

 <div id="incidents_box">
  <div class="tbl-head" style="background:none;border:none;padding:16px 2px 4px">
   Incidents detected</div>
  <div class="inc-grid" id="incidents_grid"></div>
 </div>

 <video id="up_player" controls></video>

 <div class="glass tbl-card" id="ai_box" style="display:none">
  <div class="tbl-head">AI Scene Review — what Claude sees</div>
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

<!-- ===================== CAMERAS / CONNECT A DVR ===================== -->
<section class="view" id="view-cameras">
 <div class="page-head">
  <div><h1>Cameras</h1>
  <p>Connect a DVR without editing a file or restarting anything. Scan the
     network, enter the DVR's username and password once, and the working
     channels are found for you. Passwords are stored on this machine and are
     never shown again.</p></div>
 </div>

 <div class="glass tbl-card">
  <div class="tbl-head">Connected</div>
  <div class="tblwrap"><table id="cams-table"><thead><tr>
   <th>Name</th><th>Where it looks</th><th>Stream</th><th>Size</th>
   <th>State</th><th>Source</th><th></th>
  </tr></thead><tbody></tbody></table></div>
 </div>

 <div id="cx-loc-panel" class="glass tbl-card" style="margin-top:16px;display:none">
  <div class="tbl-head">Where is <span id="cx-loc-cam"></span>?</div>
  <div style="padding:14px 16px">
   <p class="cam-msg" style="margin-top:0">Tell the system where this camera
    sits and what it faces. Alerts then read
    "<i>B-Block Main Gate, facing main road</i>" instead of the device name, so
    a guard knows where to go without opening the app.</p>
   <div class="cam-row">
    <label>Name it<input id="cx-loc-label" placeholder="B-Block Main Gate"></label>
    <label>Block / side<input id="cx-loc-block" placeholder="B-Block"></label>
    <label>Facing<input id="cx-loc-facing" placeholder="main road"></label>
   </div>
   <div class="cam-row">
    <label style="flex:1">Notes<input id="cx-loc-notes" placeholder="covers the visitor lane too"></label>
    <button class="mini-btn" onclick="camSaveCtx()">Save location</button>
    <button class="mini-btn" onclick="camCloseCtx()">Cancel</button>
    <span id="cx-loc-msg" class="cam-msg"></span>
   </div>
  </div>
 </div>

 <div id="zn-panel" class="glass tbl-card" style="margin-top:16px;display:none">
  <div class="tbl-head">Zones — <span id="zn-cam"></span></div>
  <div style="padding:14px 16px">
   <p class="cam-msg" style="margin-top:0">Draw where things matter, so alerts
    get sharper: a vehicle stopped in the <b>entry</b> zone, a person lingering
    in <b>parking</b>, anyone in a <b>restricted</b> area after hours. Click on
    the picture to drop points; finish a shape when it encloses the area.</p>
   <div class="cam-row">
    <span>Drawing:</span>
    <button class="mini-btn zn-kind" data-kind="entry" onclick="znSetKind('entry')">Entry</button>
    <button class="mini-btn zn-kind" data-kind="parking" onclick="znSetKind('parking')">Parking</button>
    <button class="mini-btn zn-kind" data-kind="restricted" onclick="znSetKind('restricted')">Restricted</button>
    <span style="flex:1"></span>
    <button class="mini-btn" onclick="znFinish()">Finish shape</button>
    <button class="mini-btn" onclick="znUndo()">Undo point</button>
    <button class="mini-btn" onclick="znClearKind()">Clear this kind</button>
   </div>
   <div id="zn-stage" style="position:relative;display:inline-block;max-width:100%;margin-top:10px">
    <img id="zn-img" alt="camera view" style="display:block;max-width:100%;height:auto;border-radius:8px">
    <canvas id="zn-canvas" style="position:absolute;left:0;top:0;cursor:crosshair"></canvas>
   </div>
   <div class="cam-row" style="margin-top:10px">
    <button class="mini-btn" onclick="znSave()">Save zones</button>
    <button class="mini-btn" onclick="znClose()">Cancel</button>
    <span id="zn-msg" class="cam-msg"></span>
   </div>
  </div>
 </div>

 <div class="glass tbl-card" style="margin-top:16px">
  <div class="tbl-head">Add a camera</div>
  <div style="padding:14px 16px">
   <div class="cam-row">
    <label>Network<input id="cx-net" value="192.168.1.0/24"></label>
    <button class="mini-btn" onclick="camScan()">Scan for DVRs</button>
    <span id="cx-scan-msg" class="cam-msg"></span>
   </div>
   <div class="cam-row">
    <label>DVR address<input id="cx-host" placeholder="192.168.1.108"></label>
    <label>Username<input id="cx-user" value="admin"></label>
    <label>Password<input id="cx-pass" type="password" autocomplete="off"></label>
    <label>Channels<input id="cx-chans" type="number" value="4" min="1" max="32"></label>
    <button class="mini-btn" onclick="camProbe()">Find cameras</button>
   </div>
   <div id="cx-results"></div>
   <p id="cx-msg" class="cam-msg"></p>
  </div>
 </div>
</section>

</main>

<div id="cam-modal" class="cam-modal" onclick="closeCam()">
 <div class="cam-modal-inner glass" onclick="event.stopPropagation()">
  <img id="cam-modal-img" alt="">
  <div class="cam-modal-bar">
   <span id="cam-modal-name" class="cam-name"></span>
   <button class="mini-btn" onclick="closeCam()">Close</button>
  </div>
 </div>
</div>

<footer class="app-chrome">All processing on this machine · only Telegram alerts leave it</footer>

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
 if(v==='cameras') camList();
}

// ---- cameras: scan, probe, add ----------------------------------------
const cxEsc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;',
 '>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function cxPost(url,data){
 const r=await fetch(url,{method:'POST',body:new URLSearchParams(data)});
 const body=await r.json().catch(()=>({}));
 if(!r.ok) throw new Error(body.detail||('failed ('+r.status+')'));
 return body;}

async function camList(){
 const tb=document.querySelector('#cams-table tbody');
 if(!tb) return;
 let rows=[];
 try{ rows=await (await fetch('/api/cameras/list')).json(); }
 catch(e){ tb.innerHTML='<tr><td colspan="7">sign in to manage cameras</td></tr>'; return; }
 if(!Array.isArray(rows)||!rows.length){
  tb.innerHTML='<tr><td colspan="7">No cameras yet — add one below.</td></tr>';return;}
 tb.innerHTML=rows.map(c=>{
  const set=(c.label||c.block||c.facing);
  const loc=set?cxEsc(c.location):'<span style="opacity:.55">not set</span>';
  const j=cxEsc(JSON.stringify(c));
  return `<tr>
  <td>${cxEsc(c.name)}</td>
  <td>${loc} <button class="mini-btn" onclick='camLocate(${j})'>${set?'Edit':'Set'}</button></td>
  <td class="mono" style="font-size:11px">${cxEsc(c.url)}</td>
  <td>${c.width?c.width+'x'+c.height:'—'}</td>
  <td>${c.online?'<span class="ok">live</span>':c.running?'connecting':'stopped'}</td>
  <td>${cxEsc(c.from)}</td>
  <td><button class="mini-btn" onclick='camZones(${j})'>Zones</button>${c.id?`<button class="mini-btn" onclick="camDrop(${c.id},'${cxEsc(c.name)}')">Remove</button>`:''}</td>
 </tr>`;}).join('');}

let _cxLocName='';
function camLocate(c){
 _cxLocName=c.name;
 document.getElementById('cx-loc-cam').textContent=c.name;
 document.getElementById('cx-loc-label').value=c.label||'';
 document.getElementById('cx-loc-block').value=c.block||'';
 document.getElementById('cx-loc-facing').value=c.facing||'';
 document.getElementById('cx-loc-notes').value=c.notes||'';
 document.getElementById('cx-loc-msg').textContent='';
 const p=document.getElementById('cx-loc-panel');
 p.style.display='block'; p.scrollIntoView({behavior:'smooth',block:'nearest'});
}
function camCloseCtx(){document.getElementById('cx-loc-panel').style.display='none';}
async function camSaveCtx(){
 const msg=document.getElementById('cx-loc-msg'); msg.textContent='saving…';
 try{
  await cxPost('/api/cameras/context',{
   name:_cxLocName,
   label:document.getElementById('cx-loc-label').value,
   block:document.getElementById('cx-loc-block').value,
   facing:document.getElementById('cx-loc-facing').value,
   notes:document.getElementById('cx-loc-notes').value});
  camCloseCtx(); camList();
 }catch(e){ msg.textContent=e.message; }}

// ---- zones: draw entry / parking / restricted on a still frame -----------
const ZN={name:'',kind:'entry',zones:{},current:[],natW:0,natH:0,
 colors:{entry:'#38bdf8',parking:'#f59e0b',restricted:'#ef4444'}};
async function camZones(c){
 ZN.name=c.name; ZN.kind='entry'; ZN.current=[];
 document.getElementById('zn-cam').textContent=c.name;
 document.getElementById('zn-msg').textContent='';
 let data={zones:{},width:c.width||0,height:c.height||0};
 try{ data=await (await fetch('/api/cameras/'+encodeURIComponent(c.name)+'/zones')).json(); }
 catch(e){}
 ZN.zones=Object.assign({entry:[],parking:[],restricted:[]},data.zones||{});
 ZN.natW=data.width||0; ZN.natH=data.height||0;
 ZN.current=(ZN.zones[ZN.kind]||[]).slice();
 znHighlight();
 const img=document.getElementById('zn-img');
 img.onload=()=>{ if(!ZN.natW){ZN.natW=img.naturalWidth;ZN.natH=img.naturalHeight;} znLayout(); };
 img.onerror=()=>{ document.getElementById('zn-msg').textContent=
   'no live frame yet — start the camera, then reopen to draw on it'; znLayout(); };
 img.src='/snapshot/'+encodeURIComponent(c.name)+'?t='+Date.now();
 const p=document.getElementById('zn-panel');
 p.style.display='block'; p.scrollIntoView({behavior:'smooth',block:'nearest'});
}
function znClose(){document.getElementById('zn-panel').style.display='none';}
function znHighlight(){
 document.querySelectorAll('.zn-kind').forEach(b=>
  b.style.outline=b.dataset.kind===ZN.kind?('2px solid '+ZN.colors[ZN.kind]):'none');}
function znLayout(){
 const img=document.getElementById('zn-img'), cv=document.getElementById('zn-canvas');
 const w=img.clientWidth||640, h=img.clientHeight||360;
 cv.width=w; cv.height=h; cv.style.width=w+'px'; cv.style.height=h+'px';
 if(!ZN.natW){ZN.natW=w; ZN.natH=h;}
 znDraw();
}
function znS2D(p){const cv=document.getElementById('zn-canvas');
 return [p[0]*cv.width/ZN.natW, p[1]*cv.height/ZN.natH];}
function znD2S(x,y){const cv=document.getElementById('zn-canvas');
 return [x*ZN.natW/cv.width, y*ZN.natH/cv.height];}
function znPoly(ctx,pts,color,active){
 if(!pts.length) return;
 ctx.beginPath();
 pts.forEach((p,i)=>{const d=znS2D(p); i?ctx.lineTo(d[0],d[1]):ctx.moveTo(d[0],d[1]);});
 if(!active) ctx.closePath();
 ctx.lineWidth=2; ctx.strokeStyle=color; ctx.stroke();
 if(!active){ctx.globalAlpha=0.18; ctx.fillStyle=color; ctx.fill(); ctx.globalAlpha=1;}
 pts.forEach(p=>{const d=znS2D(p); ctx.beginPath();
  ctx.arc(d[0],d[1],4,0,7); ctx.fillStyle=color; ctx.fill();});
}
function znDraw(){
 const cv=document.getElementById('zn-canvas'), ctx=cv.getContext('2d');
 ctx.clearRect(0,0,cv.width,cv.height);
 for(const k of ['entry','parking','restricted'])
  if(k!==ZN.kind) znPoly(ctx,ZN.zones[k]||[],ZN.colors[k],false);
 znPoly(ctx,ZN.current,ZN.colors[ZN.kind],true);   // the one being edited
}
document.getElementById('zn-canvas').addEventListener('click',e=>{
 const cv=document.getElementById('zn-canvas'), r=cv.getBoundingClientRect();
 ZN.current.push(znD2S(e.clientX-r.left, e.clientY-r.top)); znDraw();
});
window.addEventListener('resize',()=>{
 if(document.getElementById('zn-panel').style.display!=='none') znLayout();});
function znCommit(){ZN.zones[ZN.kind]=ZN.current.length>=3?ZN.current.slice():[];}
function znSetKind(k){znCommit(); ZN.kind=k; ZN.current=(ZN.zones[k]||[]).slice();
 znHighlight(); znDraw();}
function znFinish(){
 const m=document.getElementById('zn-msg');
 if(ZN.current.length<3){m.textContent='a shape needs at least 3 points'; return;}
 znCommit(); m.textContent=ZN.kind+' zone set ('+ZN.current.length+' points)';}
function znUndo(){ZN.current.pop(); znDraw();}
function znClearKind(){ZN.current=[]; ZN.zones[ZN.kind]=[]; znDraw();}
async function znSave(){
 znCommit();
 const m=document.getElementById('zn-msg'); m.textContent='saving…';
 try{
  const r=await cxPost('/api/cameras/zones',
    {name:ZN.name, zones_json:JSON.stringify(ZN.zones)});
  const c=r.counts||{};
  m.innerHTML='<span class="ok">saved</span> — entry '+(c.entry||0)+
   ', parking '+(c.parking||0)+', restricted '+(c.restricted||0)+' points';
 }catch(e){ m.textContent=e.message; }}

async function camScan(){
 const msg=document.getElementById('cx-scan-msg');
 msg.textContent='scanning…';
 try{
  const r=await cxPost('/api/cameras/scan',{network:document.getElementById('cx-net').value});
  if(!r.found.length){ msg.textContent=r.hint||'nothing found'; return; }
  msg.innerHTML=r.found.map(d=>`<a href="#" onclick="document.getElementById('cx-host').value='${d.ip}';return false">${d.ip}</a>`).join(' · ')
   +' &nbsp;— click one, then enter the password';
 }catch(e){ msg.textContent=e.message; }}

async function camProbe(){
 const msg=document.getElementById('cx-msg'), out=document.getElementById('cx-results');
 msg.textContent='trying the usual RTSP paths…'; out.innerHTML='';
 try{
  const r=await cxPost('/api/cameras/probe',{
   host:document.getElementById('cx-host').value,
   username:document.getElementById('cx-user').value,
   password:document.getElementById('cx-pass').value,
   channels:document.getElementById('cx-chans').value});
  const ok=r.channels.filter(c=>c.ok);
  if(!ok.length){ msg.textContent=r.advice||'no working stream found'; return; }
  msg.textContent=`${ok.length} camera${ok.length>1?'s':''} found.`;
  out.innerHTML=ok.map(c=>`<div class="cam-row">
    <b>Channel ${c.channel}</b> <span class="mono" style="font-size:11px">${cxEsc(c.vendor)} · ${c.width}x${c.height}</span>
    <label>Call it<input id="cx-name-${c.channel}" value="cam${c.channel}"></label>
    <button class="mini-btn" onclick="camAdd(${c.channel},'${cxEsc(c.vendor)}')">Add</button>
    <span id="cx-add-${c.channel}" class="cam-msg"></span></div>`).join('');
 }catch(e){ msg.textContent=e.message; }}

async function camAdd(channel,vendor){
 const el=document.getElementById('cx-add-'+channel);
 el.textContent='connecting…';
 try{
  const r=await cxPost('/api/cameras/add',{
   name:document.getElementById('cx-name-'+channel).value,
   host:document.getElementById('cx-host').value,
   username:document.getElementById('cx-user').value,
   password:document.getElementById('cx-pass').value,
   channel:channel, vendor:vendor});
  el.innerHTML='<span class="ok">added'+(r.running?' and live':'')+'</span>';
  camList();
 }catch(e){ el.textContent=e.message; }}

async function camDrop(id,name){
 if(!confirm('Remove '+name+'? Recorded clips and events are kept.')) return;
 await fetch('/api/cameras/'+id,{method:'DELETE'});
 camList();}

// ---- clock ------------------------------------------------------------
setInterval(()=>{document.getElementById('clock').textContent=
 new Date().toLocaleTimeString('en-IN',{hour12:false});},1000);

// ---- camera wall ------------------------------------------------------
document.getElementById('cams').innerHTML=cams.length?cams.map(c=>
 `<div class="glass cam-card" onclick="openCam('${esc(c)}')">
   <img src="/stream/${encodeURIComponent(c)}" alt="${esc(c)}">
   <div class="cam-meta">
    <span class="cam-name">${esc(c)}</span>
    <span class="chip ok" id="st_${esc(c)}"><i></i>LIVE</span>
   </div>
  </div>`).join('')
 :'<div class="glass cam-card empty" style="padding:28px;line-height:1.6">'+
  '<b style="color:var(--text)">No cameras connected to this preview.</b><br>'+
  'VisionGuard installs on-site and reads the existing CCTV in a building, so '+
  'there '+
  'are no live feeds on a hosted demo.<br><br>'+
  'Open <b style="color:var(--text)">Forensic Lab</b> to upload a clip and watch '+
  'the AI analyse real footage.</div>';

// With no cameras, drop the "Live" badge and the live-feed wording so the page
// never claims something it is not showing.
if(!cams.length){
 const lp=document.querySelector('#view-view .live-pill'); if(lp)lp.style.display='none';
 const sub=document.querySelector('#view-view .page-head p');
 if(sub)sub.textContent='Live feeds appear here once cameras are connected on-site.';
}

function openCam(name){
 document.getElementById('cam-modal-img').src='/stream/'+encodeURIComponent(name);
 document.getElementById('cam-modal-name').textContent=name;
 document.getElementById('cam-modal').style.display='flex';
}
function closeCam(){
 document.getElementById('cam-modal').style.display='none';
 document.getElementById('cam-modal-img').src='';
}
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeCam();});

async function pollStatus(){
 try{
  const st=await (await fetch('/api/status')).json();
  for(const [name,s] of Object.entries(st)){
   const el=document.getElementById('st_'+name);
   if(el){el.className='chip '+(s.online?'ok':'bad');
    el.innerHTML='<i></i>'+(s.online?'LIVE':'OFFLINE');}
  }
 }catch(e){}
}

// ---- events + intel ---------------------------------------------------
async function refresh(){
 try{
  const evs=await (await fetch('/api/events?limit=100')).json();
  document.getElementById('intel').innerHTML=evs.slice(0,10).map(e=>{
   const t=new Date(e.ts*1000).toLocaleTimeString('en-IN',{hour12:false});
   return `<div class="intel-item">
    <div class="intel-ic ${e.severity}"></div>
    <div><div class="t">${esc(e.description)}</div>
     <div class="m"><span class="tag">${esc(e.camera)}</span>
      ${e.incident_id?`<span class="tag">Incident #${e.incident_id}</span>`:''}
      <span class="tag">${t}</span></div></div></div>`;
  }).join('')||'<div class="empty">No events yet — all quiet.</div>';
  document.querySelector('#events tbody').innerHTML=evs.map(e=>{
   const t=new Date(e.ts*1000).toLocaleString('en-IN',{hour12:false});
   let clip='—';
   if(e.clip_id&&!e.clip_deleted) clip=`<a class="jump" href="/clips/${e.clip_id}" target="_blank">view</a>
    <button class="mini-btn danger" onclick="delClip(${e.clip_id})">delete</button>`;
   else if(e.clip_deleted) clip='<span style="opacity:.5">deleted</span>';
   return `<tr><td>${sevChip(e.severity)}</td>
   <td class="mono">${e.incident_id?'#'+e.incident_id:'—'}</td>
   <td class="mono">${t}</td><td>${esc(e.camera)}</td><td>${esc(e.event_type)}</td>
   <td class="mono">${esc(e.plate)||'—'}</td><td>${esc(e.description)}</td>
   <td>${esc(e.ai_summary)||'—'}</td><td>${clip}</td></tr>`;}).join('')
   ||'<tr><td colspan="9" class="empty">No events yet.</td></tr>';
 }catch(e){}
 pollStatus();
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
 const st=document.getElementById('up_status'); st.textContent='uploading…';
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
  st.textContent=`${j.status} — ${Math.round(j.progress*100)}% (${j.events.length} found)`;
  document.querySelector('#up_results tbody').innerHTML=j.events.map(e=>
   `<tr><td>${sevChip(e.severity)}</td>
   <td><a class="jump" onclick="seekTo(${e.video_time_s})">${e.video_time_s}s</a></td>
   <td>${esc(e.event_type)}</td><td class="mono">${esc(e.plate)||'—'}</td>
   <td>${esc(e.description)}</td>
   <td><a class="jump" onclick="seekTo(${e.video_time_s})">jump</a></td></tr>`
  ).join('')||'<tr><td colspan="6" class="empty">Nothing flagged yet…</td></tr>';
  const incs=j.incidents||[];
  if(incs.length){
   document.getElementById('incidents_box').style.display='block';
   document.getElementById('incidents_grid').innerHTML=incs.map(c=>{
    const span=(c.start_s===c.end_s)?`${c.start_s}s`:`${c.start_s}–${c.end_s}s`;
    const ids=(c.track_ids&&c.track_ids.length)?c.track_ids.map(t=>'#'+t).join(', '):'—';
    return `<div class="inc-card ${esc(c.severity)}" onclick="seekTo(${c.start_s})">
     <div class="top"><span class="no">Incident ${c.index+1}</span>${sevChip(c.severity)}</div>
     <div class="headline">${esc(c.summary)||esc(c.event_type)}</div>
     <div class="meta"><span>Span <b>${span}</b></span>
      <span>Culprit <b>${ids}</b></span>
      <span>${c.count} alert(s)</span></div></div>`;
   }).join('');
  }
  const aiBox=document.getElementById('ai_box');
  if((j.ai_findings&&j.ai_findings.length)||j.ai_note){
   aiBox.style.display='block';
   document.getElementById('ai_note').textContent=j.ai_note||'';
   const finds=j.ai_findings||[];
   document.querySelector('#ai_results tbody').innerHTML=finds.map(x=>
    `<tr><td>${sevChip(x.severity)}</td>
    <td><a class="jump" onclick="seekTo(${x.time_s})">${x.time_s}s</a></td>
    <td>${esc(x.activity)}</td>
    <td><a class="jump" onclick="seekTo(${x.time_s})">jump</a></td></tr>`
   ).join('');
   // Headline the verdict either way. A cleared clip is a real answer — it is
   // what rules out a false alarm — so it gets the same banner, in green.
   const b=document.getElementById('threat_banner');
   const t=document.getElementById('threat_title');
   const sub=document.getElementById('threat_sub');
   if(j.ai_status==='threat'||j.ai_verdict){
    b.style.display='block'; b.classList.remove('clear');
    t.textContent='Threat confirmed — '+j.ai_verdict;
    sub.textContent=finds.length+' finding(s) — timestamps listed below.';
   }else if(j.ai_status==='clear'){
    b.style.display='block'; b.classList.add('clear');
    t.textContent='All clear — no threat found';
    const n=(j.incidents||[]).length;
    sub.textContent='Claude watched the footage and found no theft, break-in '+
     'or vandalism.'+(n?' The '+n+' rule-based alert(s) below look like a false alarm.':'');
   }else{
    b.style.display='none'; b.classList.remove('clear');
   }
  }
  if(j.status==='done'||j.status==='error'){clearInterval(poll);
   if(j.status==='error'){st.textContent='error: '+j.error;return;}
   if(j.video_ready){
    player.src='/api/analyze/'+job_id+'/video';
    player.style.display='block';
    st.textContent='done — green CULPRIT boxes in the video; click a time to jump.';
   }
  }
 },1200);
}
function seekTo(t){
 const p=document.getElementById('up_player');
 if(p.src){p.currentTime=Math.max(0,t-2);p.play();
  p.scrollIntoView({behavior:'smooth',block:'center'});}
}

// ---- investor login gate (demo-grade, client-side) --------------------
// Real server-side login. The old version compared a password in JavaScript
// with the credentials sitting in the page source — anyone could read them in
// view-source, and curl never saw the check at all. Every data endpoint on
// this console now requires a real session, so the login has to create one.
async function doLogin(e){e.preventDefault();
 const u=(document.getElementById('lg_user').value||'').trim();
 const p=document.getElementById('lg_pass').value||'';
 document.getElementById('lg_err').textContent='';
 try{
  const r=await fetch('/api/login',{method:'POST',
    body:new URLSearchParams({username:u,password:p})});
  if(!r.ok){document.getElementById('lg_err').textContent=
    'Invalid username or password.';return false;}
  const me=await r.json();
  showApp(me.name||u);
 }catch(_){document.getElementById('lg_err').textContent=
   'Could not reach the server.';}
 return false;}
async function signOut(){try{await fetch('/api/logout',{method:'POST'});}catch(_){}
 location.reload();}
function showApp(name){document.getElementById('login-gate').style.display='none';
 document.body.classList.add('authed');
 const el=document.getElementById('app-user');if(el)el.textContent=name;}
// On load, ask the server whether we already have a session. The httpOnly
// cookie is invisible to JS, so the server is the only source of truth.
(async function(){
 try{
  const r=await fetch('/api/me',{cache:'no-store'});
  if(r.ok){const me=await r.json();showApp(me.name||me.username||'');return;}
 }catch(_){}
 document.getElementById('login-gate').style.display='flex';
})();

loadCams();
refresh(); setInterval(refresh,5000);
</script></body></html>"""
