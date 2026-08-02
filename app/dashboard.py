"""FastAPI dashboard: live annotated MJPEG, events, registry, clip
deletion (name + reason, audited). One page, no build step.

Frontend: AEGIS glass theme (from the user's Stitch design), rebuilt as
self-contained CSS — no Tailwind CDN, so the dashboard still renders when
the internet is down. Only the Google font is fetched (graceful fallback).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import tempfile
from pathlib import Path

# Bump when shipping a fix that must be verifiable in production. /health
# echoes it, so "is my change actually live?" has a definite answer.
BUILD = "2026-07-26-ai-review-sdk"

from fastapi import (Depends, FastAPI, File, Form, HTTPException, Request,
                     UploadFile)
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               Response, StreamingResponse)
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from . import assistant as assistant_mod
from . import auth
from . import clips as clips_mod
from . import damage as damage_mod
from . import train as train_mod
from . import operator as operator_mod
from . import segment as segment_mod
from . import tagging
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
        if not token:
            return None
        th = auth.token_hash(token)
        user = ctx.db.session_user(th)
        if user is None:
            return None
        # slide the expiry so a working shift is not interrupted mid-way
        ctx.db.touch_session(th, auth.session_expiry())
        return user

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
        return {
            "ok": True,
            "ui": "console",
            "build": BUILD,
            "cameras": list(ctx.workers),
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
    def stream(camera: str):
        if camera not in ctx.workers:
            raise HTTPException(404, "unknown camera")
        return StreamingResponse(mjpeg_gen(camera),
                                 media_type="multipart/x-mixed-replace; boundary=frame")

    # -------------------------------------------------------------- events
    @app.get("/api/events")
    def events(limit: int = 100):
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
        return {"ok": True, "event_id": event_id, "verdict": verdict}

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
}

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

loadCams();
refresh(); setInterval(refresh,5000);
</script></body></html>"""
