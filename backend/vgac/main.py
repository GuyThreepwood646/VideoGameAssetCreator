"""FastAPI application for Video Game Asset Creator: every endpoint in docs/API.md.

Run with: python -m uvicorn vgac.main:app --port 8765 --reload (from the backend folder).
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import psutil
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from . import __version__, autopilot, library, own_images, pipeline, reference
from .comfy import ComfyClient
from .config import BACKEND_DIR, get_settings
from .db import (JOB_STATUSES, STAGES, VERSION_STATUSES, Asset, Job, Project, Version, asset_to_dict, get_session, init_db, utcnow,
                 job_to_dict, project_to_dict, session_factory, version_to_dict, _iso)
from .events import bus
from .jobs import HOST, JUDGE, JobQueue, active_queue, free_ram_gb, job_kind
from .pipeline import PipelineError
from . import credits as credits_mod, hairlib, items, my_hair, user_addons

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("vgac")

FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist"
HEALTH_STATS_MAX_AGE = 5.0  # seconds an instance's /system_stats answer is reused by /api/health (polled every 2 s)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db()
    q = JobQueue(settings)  # registers itself as jobs.active_queue() for the API and the autopilot
    await q.start()
    autopilot.resume_all()
    log.info("library at %s, db at %s", settings.library_path, settings.db_path)
    try:
        yield
    finally:
        await q.stop()


app = FastAPI(title="Video Game Asset Creator", version=__version__, lifespan=lifespan)
app.include_router(own_images.router)  # start from your own images (own_images.py)
app.include_router(__import__("vgac.compose", fromlist=["router"]).router)  # step 0.5: a character from several pictures (compose.py)
app.include_router(user_addons.router)  # your own add-ons (user_addons.py)
app.include_router(__import__("vgac.moves", fromlist=["router"]).router)  # "Add a move" and your own animation files (moves.py)
app.include_router(items.router)  # items: empty-handed characters and "Add an item" (items.py)
app.include_router(__import__("vgac.model_import", fromlist=["router"]).router)  # your own 3D model on step 3 (model_import.py)
app.include_router(my_hair.router)  # your own hairstyles: add, rename, remove (my_hair.py)
app.include_router(hairlib.router)  # hair from the library: the choice, the pick, the library's pictures (hairlib.py)

# Exact origins the app is served from: the backend itself (the launcher and the built UI) and the Vite dev server
# (frontend/vite.config.ts, port 5173, proxying /api to this backend). Any other local port is a foreign page.
BACKEND_PORT = 8765
DEV_UI_PORT = 5173
ALLOWED_ORIGINS = frozenset(
    f"http://{host}:{port}" for host in ("127.0.0.1", "localhost", "[::1]") for port in (BACKEND_PORT, DEV_UI_PORT)
)


def is_allowed_origin(origin: str) -> bool:
    return origin.lower() in ALLOWED_ORIGINS


app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(ALLOWED_ORIGINS),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# The server only ever listens on loopback; these two close the one remote route left, a web page in the
# user's browser (DNS rebinding and cross-site requests).
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"])


@app.exception_handler(PipelineError)
async def _pipeline_error(request: Request, exc: PipelineError) -> JSONResponse:
    return JSONResponse({"detail": exc.detail}, status_code=exc.status)


@app.middleware("http")
async def _block_foreign_origins(request: Request, call_next):
    origin = request.headers.get("origin")
    if origin and request.method not in ("GET", "HEAD", "OPTIONS") and not is_allowed_origin(origin):
        return JSONResponse({"detail": "cross-site requests are not allowed"}, status_code=403)
    return await call_next(request)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class StyleLock(BaseModel):
    preset: str | None = None
    based_on: str | None = None  # a custom look (no preset) takes its pipeline settings from this look
    prompt_block: str = ""
    model: str | None = None
    loras: list[str] = Field(default_factory=list)
    palette: list[str] = Field(default_factory=list)
    negative: str = ""


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    style_lock: StyleLock | None = None
    platform_target: str = "pc_mid"
    unity_project_path: str | None = None


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    style_lock: StyleLock | None = None
    platform_target: str | None = None
    unity_project_path: str | None = None


class AssetIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: str
    style_preset: str
    platform_target: str | None = None
    prompt: str = ""
    parts: list[str] | None = None
    concept_model: str | None = None
    # Start from a picture: the person's own concept picture (base64 or a data: URL); the description may stay empty.
    image: str | None = Field(default=None, max_length=own_images.IMAGE_FIELD_MAX)
    # Carried items found in the description (items.detect): "separate" (the default) makes the character
    # empty-handed and each item its own linked asset; "keep" leaves them part of the body.
    items: Literal["separate", "keep"] | None = None
    # Hair from the library (hairlib.py): "library" builds the model bald and fits a ready-made hairstyle; "model" or
    # nothing is today's hair.
    hair: Literal["model", "library"] | None = None
    # "The face matters": False skips the face detail pass in step 4 (db.Asset.face_matters).
    face_matters: bool | None = None


class AssetPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    prompt: str | None = None
    category: str | None = None
    parts: list[str] | None = None
    platform_target: str | None = None
    style_preset: str | None = None
    face_matters: bool | None = None


class RunIn(BaseModel):
    parent_version_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    count: int = Field(default=1, ge=1, le=4)


class RefineIn(BaseModel):
    instruction: str = Field(min_length=1, max_length=4000)
    view: str | None = None
    # Check all sides: several views redrawn together in one new version (instead of `view`).
    views: list[str] | None = Field(default=None, max_length=len(reference.TURNAROUND_VIEWS))
    mask_png: str | None = Field(default=None, max_length=6_000_000)
    # Experimental: a second picture the concept refine passes to the edit model (own_images.write_reference).
    reference_image: str | None = Field(default=None, max_length=own_images.IMAGE_FIELD_MAX)
    params: dict[str, Any] = Field(default_factory=dict)


class AdjustIn(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


class JudgeIn(BaseModel):
    mode: str = "metrics"


class FeedbackIn(BaseModel):
    thumbs: Literal["up", "down"] | None = None
    note: str | None = Field(default=None, max_length=1000)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_queue = pipeline.queue
_params = pipeline.validate_params
_version = pipeline.get_version


def _check_concept_model(field: str, mid: str | None) -> None:
    """A concept model id must be one of the concept models in data/models.yaml."""
    if mid and mid not in reference.concept_models():
        raise HTTPException(400, f"{field} must be one of {reference.concept_models()}")


def _check_mask(mask_b64: str | None) -> None:
    if mask_b64:
        from .stages import decode_mask

        try:
            decode_mask(mask_b64)
        except ValueError as e:
            raise HTTPException(400, str(e))


def _check_unity_project(path: str | None) -> str | None:
    """A Unity project the export may write into: an existing folder with Assets and ProjectSettings inside."""
    path = (path or "").strip()
    if not path:
        return None
    p = Path(path)
    if not p.is_absolute():
        raise HTTPException(400, "unity_project_path must be an absolute folder path")
    if not (p / "Assets").is_dir() or not (p / "ProjectSettings").is_dir():
        raise HTTPException(400, "unity_project_path is not a Unity project (no Assets and ProjectSettings folders)")
    return str(p.resolve())


def _project(db: Session, pid: str) -> Project:
    p = db.get(Project, pid)
    if p is None:
        raise HTTPException(404, "project not found")
    return p


def _asset(db: Session, aid: str) -> Asset:
    a = db.get(Asset, aid)
    if a is None:
        raise HTTPException(404, "asset not found")
    return a


def _validate_platform(pid: str) -> None:
    try:
        reference.platform(pid)
    except KeyError as e:
        raise HTTPException(400, str(e))


def _validate_style(sid: str) -> None:
    if sid not in {s["id"] for s in reference.styles()}:
        raise HTTPException(400, f"unknown style_preset {sid!r}; see GET /api/styles")


def _validate_category(cid: str) -> None:
    try:
        reference.category(cid)
    except KeyError as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------------------
# Health and reference data
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health() -> dict[str, Any]:
    """Polled every 2 s by the desktop launcher: it reuses the queue's ComfyUI clients and their cached /system_stats."""
    settings = get_settings()
    queue = active_queue()
    clients = {name: (queue.clients.get(name) if queue else None) or _health_client(name, inst.url) for name, inst in settings.instances.items()}
    summaries = await asyncio.gather(*(c.cached_vram_summary(HEALTH_STATS_MAX_AGE) for c in clients.values()))
    instances = {}
    for (name, inst), summary in zip(settings.instances.items(), summaries):
        instances[name] = {"url": clients[name].base_url, "gpu": inst.gpu, "role": inst.role, **summary,
                           "enabled": queue.is_enabled(name) if queue else True}
    instances[HOST] = {"role": "blender, unity", "reachable": True, "blender": str(settings.blender_exe), "unity": str(settings.unity_exe)}
    vm = psutil.virtual_memory()
    return {
        "ok": True,
        "version": __version__,
        "instances": instances,
        "host": {
            "ram_total_bytes": vm.total,
            "ram_free_bytes": vm.available,
            "ram_free_gb": round(free_ram_gb(), 1),
            "min_free_ram_gb": settings.min_free_ram_gb,
            "cpu_percent": psutil.cpu_percent(interval=None),
        },
        "queue": queue.status() if queue else {},
        "ws_subscribers": bus.subscribers,
        "stages": list(STAGES),
        "job_statuses": list(JOB_STATUSES),
        "version_statuses": list(VERSION_STATUSES),
    }


SETUP_CACHE_SECONDS = 60.0
_setup_cache: dict[str, Any] = {"at": 0.0, "report": None}


@app.get("/api/setup")
async def setup_check(refresh: bool = False) -> dict[str, Any]:
    """Is everything the app needs in place? (preflight.py): {summary: {ok, fails, warns, unknown, affected}, items,
    groups, checked_at}. Kept for 60 s; ?refresh=1 checks again now. Read-only: it asks each graphics engine about
    one node at a time and reports only found or missing for each expected model file."""
    import time as _time

    from . import preflight

    now = _time.monotonic()
    if not refresh and _setup_cache["report"] is not None and now - _setup_cache["at"] < SETUP_CACHE_SECONDS:
        return _setup_cache["report"]
    settings = get_settings()
    queue = active_queue()
    clients = {name: (queue.clients.get(name) if queue else None) or _health_client(name, inst.url)
               for name, inst in settings.instances.items()}
    items = await preflight.run_checks(settings, clients, disabled=set(queue.disabled) if queue else set(),
                                       supervisor=queue.supervisor if queue else None)
    rep = preflight.report(items)
    _setup_cache.update(at=_time.monotonic(), report=rep)
    return rep


_health_clients: dict[str, ComfyClient] = {}


def _health_client(name: str, url: str) -> ComfyClient:
    """A client for health checks while no queue runs (kept, so its cache holds between polls)."""
    c = _health_clients.get(name)
    if c is None or c.base_url != url.rstrip("/"):
        c = _health_clients[name] = ComfyClient(url, name=name)
    return c


@app.get("/api/categories")
def categories() -> list[dict[str, Any]]:
    return [{"id": c["id"], "label": c["label"], "rig_engine": c["rig_engine"], "symmetric": bool(c["symmetric"]), "pose_block": c["pose_block"],
             "size_m": c["size_m"], "size_axis": c.get("size_axis"), "size_word": c.get("size_word"),
             "size_label": c.get("size_label"), "size_hint": c.get("size_hint"), "parts_default": c.get("parts_default", []), "budgets": c.get("budgets", {}),
             "views": c.get("views"), "lod_ratios": c.get("lod_ratios"), "topology": c.get("topology"), "input_view_default": c.get("input_view_default"),
             "skips": list(c.get("skips") or [])}
            for c in reference.categories()]


@app.get("/api/models")
def models_() -> list[dict[str, Any]]:
    """The concept painters from data/models.yaml: id, label, description, name, licence, version, default_steps, negative."""
    return reference.models_public()


@app.get("/api/credits")
def credits_() -> dict[str, Any]:
    """The Credits page: who made what the app builds on, with licences, in groups (vgac/credits.py)."""
    return credits_mod.credits()


@app.get("/api/styles")
def styles() -> list[dict[str, Any]]:
    return [{"id": s["id"], "label": s["label"], "model": s["model"], "prompt_block": s["prompt_block"]} for s in reference.styles()]


@app.get("/api/platforms")
def platforms() -> list[dict[str, Any]]:
    return reference.platforms()


@app.get("/api/stages")
def stages_() -> list[dict[str, Any]]:
    return reference.stages()


@app.get("/api/style-presets")
def style_presets_() -> list[dict[str, Any]]:
    return reference.style_presets()


@app.get("/api/recipe")
def recipe_(style: str = "painted", category: str = "humanoid", platform: str = "pc_mid") -> dict[str, Any]:
    """The resolved recipe for a style, category and platform, plus addon_entries: every add-on that works with the
    recipe's painter or the turnaround, with label, help, strengths, required, on and strength."""
    try:
        return reference.recipe_public(style, category, platform)
    except KeyError as e:
        raise HTTPException(400, str(e.args[0]))


@app.get("/api/suggest")
def suggest_(category: str = "humanoid", style: str = "painted", platform: str = "pc_mid") -> dict[str, Any]:
    from . import guidance
    return guidance.suggest(category, style, platform, reference.categories(), reference.styles(), reference.platforms())


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


@app.get("/api/projects")
def list_projects(db: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Every project, newest first, with a summary for the Projects page: asset_count, up to four cover
    pictures (the kept concept of the newest assets) and last_activity (newest asset or version)."""
    projects = db.scalars(select(Project).order_by(Project.created_at.desc())).all()
    assets = db.execute(select(Asset.id, Asset.project_id, Asset.created_at).order_by(Asset.created_at.desc())).all()
    covers: dict[str, str] = {}
    for v in db.scalars(select(Version).where(Version.stage == "concept", Version.selected == True)).all():  # noqa: E712
        pic = v.files.get("cutout") or v.files.get("image")
        if pic:
            covers[v.asset_id] = pic
    latest = dict(db.execute(select(Version.asset_id, func.max(Version.created_at)).group_by(Version.asset_id)).all())
    by_project: dict[str, list[Any]] = {}
    for row in assets:
        by_project.setdefault(row.project_id, []).append(row)
    out: list[dict[str, Any]] = []
    for p in projects:
        rows = by_project.get(p.id, [])
        times = [t for r in rows for t in (r.created_at, latest.get(r.id)) if t is not None]
        d = project_to_dict(p)
        d["asset_count"] = len(rows)
        d["covers"] = [covers[r.id] for r in rows if r.id in covers][:4]
        d["last_activity"] = _iso(max(times)) if times else d["created_at"]
        out.append(d)
    return out


@app.post("/api/projects", status_code=201)
def create_project(body: ProjectIn, db: Session = Depends(get_session)) -> dict[str, Any]:
    _validate_platform(body.platform_target)
    lock = (body.style_lock or StyleLock()).model_dump()
    _check_concept_model("style_lock.model", lock.get("model"))
    p = Project(name=body.name, platform_target=body.platform_target)
    p.unity_project_path = _check_unity_project(body.unity_project_path)
    p.style_lock = lock
    db.add(p)
    db.commit()
    db.refresh(p)
    return project_to_dict(p)


@app.get("/api/projects/{pid}")
def get_project(pid: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    return project_to_dict(_project(db, pid))


@app.put("/api/projects/{pid}")
def update_project(pid: str, body: ProjectPatch, db: Session = Depends(get_session)) -> dict[str, Any]:
    p = _project(db, pid)
    if body.unity_project_path is not None:
        p.unity_project_path = _check_unity_project(body.unity_project_path)
    if body.name is not None:
        p.name = body.name
    if body.platform_target is not None:
        _validate_platform(body.platform_target)
        p.platform_target = body.platform_target
    if body.style_lock is not None:
        lock = body.style_lock.model_dump()
        _check_concept_model("style_lock.model", lock.get("model"))
        p.style_lock = lock
    db.commit()
    db.refresh(p)
    return project_to_dict(p)


@app.delete("/api/projects/{pid}")
def delete_project(pid: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    p = _project(db, pid)
    db.delete(p)
    db.commit()
    library.remove_tree(library.project_dir(pid))
    return {"ok": True, "deleted": pid}


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------


@app.get("/api/projects/{pid}/assets")
def list_assets(pid: str, db: Session = Depends(get_session)) -> list[dict[str, Any]]:
    _project(db, pid)
    rows = db.scalars(select(Asset).where(Asset.project_id == pid).options(selectinload(Asset.versions))
                      .order_by(Asset.created_at.desc())).all()
    return [asset_to_dict(a, with_stages=True) for a in rows]


@app.post("/api/projects/{pid}/assets", status_code=201)
def create_asset(pid: str, body: AssetIn, db: Session = Depends(get_session)) -> dict[str, Any]:
    p = _project(db, pid)
    _validate_category(body.category)
    _validate_style(body.style_preset)
    platform = body.platform_target or p.platform_target
    _validate_platform(platform)
    cat = reference.category(body.category)
    _check_concept_model("concept_model", body.concept_model)
    picture = own_images.decode_or_400(body.image) if body.image else None  # start from a picture: checked first
    a = Asset(project_id=pid, name=body.name, category=body.category, style_preset=body.style_preset, platform_target=platform, prompt=body.prompt)
    a.concept_model = body.concept_model
    if body.face_matters is False:
        a.face_matters = False
    a.parts = body.parts if body.parts is not None else list(cat.get("parts_default") or [])
    db.add(a)
    db.commit()
    db.refresh(a)
    items.on_create(db, a, body.items)
    db.refresh(a)
    if body.hair is not None:
        hairlib.set_choice(db, a, body.hair, changed_by="form")
        db.refresh(a)
    if picture is None:
        return asset_to_dict(a)
    started = own_images.start_concept_from_picture(db, a, picture)
    db.refresh(a)
    return {**asset_to_dict(a), "upload": started}


class VariantIn(BaseModel):
    change: str
    name: str | None = None
    params: dict[str, Any] | None = None


@app.post("/api/assets/{aid}/variants", status_code=201)
def create_variant(aid: str, body: VariantIn, db: Session = Depends(get_session)) -> dict[str, Any]:
    """A variant is a sibling asset that keeps the base's identity: the base's selected concept is copied as the
    variant's first concept version and a refine with the change runs on it. Later stages run as usual."""
    base = _asset(db, aid)
    src = pipeline.selected(db, base.id, "concept")
    if src is None or src.status != "done":
        raise HTTPException(409, "the base asset needs a finished, selected concept first")
    change = body.change.strip()
    if not change:
        raise HTTPException(400, "change must not be empty")
    a = Asset(project_id=base.project_id, name=body.name or f"{base.name} - {change[:40]}", category=base.category,
              style_preset=base.style_preset, platform_target=base.platform_target,
              prompt=(base.prompt.rstrip(". ") + ". " + change) if base.prompt else change)
    settings = get_settings()
    a.parts = list(base.parts)
    a.base_asset_id = base.id
    a.variant_change = change
    a.hair_json = base.hair_json
    a.face_matters = base.face_matters
    db.add(a)
    db.commit()
    db.refresh(a)
    # Copy the base concept (files included) as a done, selected concept version of the variant.
    copy = Version(asset_id=a.id, stage="concept", parent_version_id=None, status="done", selected=True)
    copy.params = {**src.params, "copied_from_version": src.id, "copied_from_asset": base.id}
    copy.metrics = dict(src.metrics)
    db.add(copy)
    db.commit()
    db.refresh(copy)
    folder = library.version_dir(a, copy, settings)
    files: dict[str, str] = {}
    for key, rel in src.files.items():
        try:
            dst = library.import_file(folder, library.abs_path(rel, settings))
            files[key] = library.rel_path(dst, settings)
        except Exception as e:  # noqa: BLE001
            log.warning("variant of %s: could not copy %s (%s): %s", src.id[:8], key, rel, e)
            continue
    copy.files = files
    db.commit()
    # Refine with the change so the identity holds.
    vparams = _params("concept", body.params, a.category)
    v = pipeline.new_version(db, a, "concept", copy, {**copy.params, **vparams})
    job = pipeline.submit(db, a, v, "refine", {"instruction": change, "view": None, "mask_png": None, "params": vparams})
    return {"asset": asset_to_dict(a), "job": job_to_dict(job), "version": version_to_dict(v)}


@app.get("/api/assets/{aid}")
def get_asset(aid: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    return asset_to_dict(_asset(db, aid))


@app.put("/api/assets/{aid}")
def update_asset(aid: str, body: AssetPatch, db: Session = Depends(get_session)) -> dict[str, Any]:
    a = _asset(db, aid)
    if body.name is not None:
        a.name = body.name
    if body.prompt is not None:
        a.prompt = body.prompt
    if body.category is not None:
        _validate_category(body.category)
        a.category = body.category
    if body.parts is not None:
        a.parts = body.parts
    if body.platform_target is not None:
        _validate_platform(body.platform_target)
        a.platform_target = body.platform_target
    if body.style_preset is not None:
        _validate_style(body.style_preset)
        a.style_preset = body.style_preset
    if body.face_matters is not None:
        a.face_matters = body.face_matters
    db.commit()
    db.refresh(a)
    return asset_to_dict(a)


@app.delete("/api/assets/{aid}")
def delete_asset(aid: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    a = _asset(db, aid)
    pid = a.project_id
    db.delete(a)
    db.commit()
    library.remove_tree(library.asset_dir(pid, aid))
    return {"ok": True, "deleted": aid}


# ---------------------------------------------------------------------------
# Running stages
# ---------------------------------------------------------------------------


@app.post("/api/assets/{aid}/stages/{stage}/run")
def run_stage(aid: str, stage: str, body: RunIn, db: Session = Depends(get_session)) -> Any:
    a = _asset(db, aid)
    params = dict(body.params)
    # Your own side and back views (turnaround params provided_views {view: picture}): checked here, written before the job.
    prepare = own_images.provided_views_prepare(db, a, params) if stage == "turnaround" else None
    pairs = pipeline.run_stage(db, a, stage, count=body.count, params=params, parent_version_id=body.parent_version_id, prepare=prepare)
    return pairs if len(pairs) > 1 else pairs[0]


class AutopilotIn(BaseModel):
    until_stage: str = "export"
    stop_on: str = "block"
    fix: dict[str, Any] | None = None
    # Go back to this step: it is made again and every step after it is rebuilt (the old results are set aside).
    from_stage: str | None = None
    # Settings the person chose for a step's first build ({stage: params}); used when the run starts that step afresh.
    stage_params: dict[str, dict[str, Any]] | None = None


@app.post("/api/assets/{aid}/autopilot")
async def start_autopilot(aid: str, body: AutopilotIn | None = None, db: Session = Depends(get_session)) -> dict[str, Any]:
    a = _asset(db, aid)
    body = body or AutopilotIn()
    if body.fix and body.fix.get("stage") in STAGES:
        body.fix = {**body.fix, "params": _params(body.fix["stage"], body.fix.get("params"), a.category)}
    stage_params = None
    if body.stage_params:
        unknown = [k for k in body.stage_params if k not in STAGES]
        if unknown:
            raise HTTPException(400, f"unknown stage {unknown[0]!r} in stage_params")
        stage_params = {k: _params(k, v, a.category) for k, v in body.stage_params.items() if v}
    try:
        return autopilot.start(a.id, until_stage=body.until_stage, stop_on=body.stop_on, fix=body.fix, from_stage=body.from_stage,
                               stage_params=stage_params)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/assets/{aid}/autopilot")
def get_autopilot(aid: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    return autopilot.get_state(_asset(db, aid))


def _stop_asset(aid: str, stage: str | None) -> dict[str, Any]:
    """Stop now: the autopilot when it is running (then every step of the asset stops), else the one stage."""
    with session_factory()() as db:
        a = db.get(Asset, aid)
        if a is None:
            raise HTTPException(404, "asset not found")
        ap_running = autopilot.get_state(a).get("status") == "running"
    state = autopilot.stop(aid) if ap_running else None
    q = active_queue()
    stopped = q.stop_asset(aid, None if ap_running else stage) if q is not None else []
    if state is None:
        with session_factory()() as db:
            state = autopilot.get_state(db.get(Asset, aid))
    return {"stopped": stopped, "autopilot": state}


@app.post("/api/assets/{aid}/autopilot/stop")
async def stop_autopilot(aid: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    """Stops the autopilot and the step in progress now (the same as POST /api/assets/{aid}/stop)."""
    _asset(db, aid)
    return _stop_asset(aid, None)["autopilot"]


@app.post("/api/assets/{aid}/autopilot/dismiss")
def dismiss_autopilot(aid: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    """"Steer by hand instead": the autopilot goes idle for this asset and the steps it left stopped are set aside, so
    nothing offers to resume it when the person comes back. Everything made stays."""
    _asset(db, aid)
    state = autopilot.steer_by_hand(aid)
    q = active_queue()
    if state.get("status") == "idle" and q is not None:
        for s in STAGES:
            q.dismiss_stopped(aid, s)
    return state


class StageIn(BaseModel):
    stage: str | None = None


@app.post("/api/assets/{aid}/stop")
async def stop_asset(aid: str, body: StageIn | None = None) -> dict[str, Any]:
    """The Stop button. Stops what is queued or running for this asset now: one step when `stage` is given and the
    autopilot is not running, else everything, and the autopilot pauses. Nothing kept is lost."""
    stage = (body.stage if body else None) or None
    if stage and stage not in STAGES:
        raise HTTPException(400, f"unknown stage {stage!r}")
    return _stop_asset(aid, stage)


@app.post("/api/assets/{aid}/resume")
async def resume_asset(aid: str, body: StageIn | None = None, db: Session = Depends(get_session)) -> dict[str, Any]:
    """The Resume button. Puts the stopped versions back in the queue with the same settings (the step runs again
    from its start). When the autopilot was stopped by the person it starts again and carries on from that step."""
    a = _asset(db, aid)
    stage = (body.stage if body else None) or None
    if stage and stage not in STAGES:
        raise HTTPException(400, f"unknown stage {stage!r}")
    state = autopilot.get_state(a)
    resumed = _queue().resume_asset(aid, stage)
    if state.get("stopped_by_user") and state.get("status") == "paused":
        state = autopilot.start(aid, until_stage=state.get("until_stage", "export"), stop_on=state.get("stop_on", "block"))
    return {"resumed": resumed, "autopilot": state}


@app.post("/api/assets/{aid}/stopped/dismiss")
def dismiss_stopped(aid: str, body: StageIn, db: Session = Depends(get_session)) -> dict[str, Any]:
    """Set a step's stopped versions aside ("Leave it stopped"): they stay in the history, Resume is no longer offered."""
    _asset(db, aid)
    if body.stage not in STAGES:
        raise HTTPException(400, f"unknown stage {body.stage!r}")
    return {"dismissed": _queue().dismiss_stopped(aid, body.stage)}


def _refine_views(parent: Version, body: RefineIn) -> list[str] | None:
    """The views a turnaround redraw names (`views`, else `view`), checked before anything is made: a view the
    version does not have is a 400 here, not a failed version in the history."""
    if body.views is None:
        wanted = [body.view] if body.view and parent.stage == "turnaround" else []
    elif parent.stage != "turnaround":
        raise HTTPException(400, "only the turnaround step redraws views")
    elif not body.views:
        raise HTTPException(400, "no views were chosen to redraw")
    else:
        wanted = list(dict.fromkeys(body.views))
    if not wanted:
        return None
    from .stages.turnaround import turnaround_views  # late: the stage module loads the imaging code

    have = turnaround_views(parent)
    for view in wanted:
        if view not in reference.TURNAROUND_VIEWS:
            raise HTTPException(400, f"unknown view {view!r}")
        if view not in have:
            raise HTTPException(400, f"this version has no {view} view to redraw")
    return wanted if body.views is not None else None


@app.post("/api/versions/{vid}/refine")
def refine_version(vid: str, body: RefineIn, db: Session = Depends(get_session)) -> dict[str, Any]:
    parent = _version(db, vid)
    if parent.status != "done":
        raise HTTPException(409, f"version is {parent.status}, not done")
    a = _asset(db, parent.asset_id)
    if body.view and body.view not in reference.TURNAROUND_VIEWS:
        raise HTTPException(400, f"unknown view {body.view!r}")
    views = _refine_views(parent, body)
    _check_mask(body.mask_png)
    reference_img = own_images.check_reference(parent, a, body.reference_image)  # experimental, 400 before anything is made
    rparams = _params(parent.stage, body.params, a.category)
    inherited = {k: val for k, val in parent.params.items() if k != "reference_experimental"}
    v = pipeline.new_version(db, a, parent.stage, parent, {**inherited, **rparams})
    if reference_img is not None:
        own_images.write_reference(db, a, v, reference_img)
    job = pipeline.submit(db, a, v, "refine", {"instruction": body.instruction, "view": body.view, "views": views, "mask_png": body.mask_png,
                                               "params": rparams})
    return {"job": job_to_dict(job), "version": version_to_dict(v)}


@app.post("/api/versions/{vid}/adjust")
def adjust_version(vid: str, body: AdjustIn, db: Session = Depends(get_session)) -> dict[str, Any]:
    parent = _version(db, vid)
    if parent.status != "done":
        raise HTTPException(409, f"version is {parent.status}, not done")
    a = _asset(db, parent.asset_id)
    aparams = _params(parent.stage, body.params, a.category)
    v = pipeline.new_version(db, a, parent.stage, parent, {**parent.params, **aparams})
    job = pipeline.submit(db, a, v, "adjust", {"params": aparams})
    return {"job": job_to_dict(job), "version": version_to_dict(v)}


@app.post("/api/versions/{vid}/select")
def select_version(vid: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    v = _version(db, vid)
    pipeline.select(db, v)
    db.refresh(v)
    return version_to_dict(v)


@app.post("/api/versions/{vid}/reject")
def reject_version(vid: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    v = _version(db, vid)
    queue = active_queue()
    if v.status in ("queued", "running") and v.job_id and queue is not None:
        queue.cancel(v.job_id)
    v.status = "rejected"
    v.selected = False
    db.commit()
    db.refresh(v)
    return version_to_dict(v)


@app.delete("/api/versions/{vid}")
def delete_version(vid: str, with_later: bool = False, db: Session = Depends(get_session)) -> dict[str, Any]:
    """Remove a version with its files and its job log lines. A kept version, or one still in the queue, stays (409).
    When other versions were made from it (a later step built on it, or a change of it), they go too only with
    with_later=true; without it the answer is 409 and says how many."""
    v = _version(db, vid)
    if v.selected:
        raise HTTPException(409, "this version is kept; keep another version first, or reject it")
    # The version and everything made from it, at any step, children before their parents.
    tree: list[Version] = []
    todo = [v]
    while todo:
        cur = todo.pop()
        tree.append(cur)
        todo.extend(db.scalars(select(Version).where(Version.parent_version_id == cur.id)).all())
    later = tree[1:]
    if later and not with_later:
        raise HTTPException(409, f"{len(later)} other version(s) were made from this one; remove them too to go on")
    ids = [x.id for x in tree]
    if any(x.status in ("queued", "running") for x in tree):
        raise HTTPException(409, "this version or one made from it is still in the queue; stop it first")
    jobs = db.scalars(select(Job).where(Job.version_id.in_(ids))).all()
    if any(j.status in ("queued", "running") for j in jobs):
        raise HTTPException(409, "this version or one made from it still has work in the queue; stop it first")
    a = _asset(db, v.asset_id)
    folders = [library.version_dir(a, x, create=False) for x in tree]
    for j in jobs:
        db.delete(j)
    for x in reversed(tree):
        db.delete(x)
        db.flush()
    db.commit()
    for f in folders:
        library.remove_tree(f)
    return {"ok": True, "deleted": vid, "deleted_ids": ids}


@app.get("/api/versions/{vid}")
def get_version(vid: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    return version_to_dict(_version(db, vid))


def _version_files(v: Version) -> list[tuple[str, Path]]:
    """The version's files that exist inside the library, as (name to show, path). Nothing outside the library is listed."""
    root = get_settings().library_path.resolve()
    out: list[tuple[str, Path]] = []
    for rel in v.files.values():
        p = (root / rel).resolve()
        try:
            p.relative_to(root)
        except ValueError:
            continue
        if p.is_file() and p.name not in {x[0] for x in out}:
            out.append((p.name, p))
    return out


@app.get("/api/versions/{vid}/zip")
def version_zip(vid: str, db: Session = Depends(get_session)) -> FileResponse:
    """Every file of a version in one .zip ("Download everything" on the finish card)."""
    import tempfile
    import zipfile

    from starlette.background import BackgroundTask

    v = _version(db, vid)
    files = _version_files(v)
    if not files:
        raise HTTPException(404, "This version has no files to download.")
    fd, name = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    tmp = Path(name)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for name, path in files:
            z.write(path, name)
    return FileResponse(tmp, media_type="application/zip", filename=f"{vid[:8]}-files.zip", background=BackgroundTask(tmp.unlink, missing_ok=True))


@app.post("/api/versions/{vid}/open-folder")
def version_open_folder(vid: str, where: str = "files", db: Session = Depends(get_session)) -> dict[str, Any]:
    """Open the folder holding a version's files (the delivered folder when the export has one) in the file explorer."""
    v = _version(db, vid)
    delivered = v.metrics.get("delivered_to")
    if where == "unity":
        delivered = (v.metrics.get("unity_project_import") or {}).get("folder")
    folder: Path | None = Path(str(delivered)) if delivered else None
    if folder is None or not folder.is_dir():
        files = _version_files(v)
        folder = files[0][1].parent if files else None
    if folder is None:
        raise HTTPException(404, "This version has no folder yet.")
    try:
        user_addons._launch_explorer(folder)
    except OSError as e:
        raise HTTPException(500, f"The folder could not be opened: {e.strerror or e}")
    return {"opened": True, "folder": str(folder)}


@app.post("/api/versions/{vid}/feedback")
def feedback_version(vid: str, body: FeedbackIn, db: Session = Depends(get_session)) -> dict[str, Any]:
    """Thumbs up or down (or neither) plus an optional note, kept on metrics.feedback. It stays local."""
    v = _version(db, vid)
    if body.thumbs is None and not body.note:
        library.update_metrics(v, feedback=None)
    else:
        library.update_metrics(v, feedback={"thumbs": body.thumbs, "note": (body.note or "").strip() or None, "at": _iso(utcnow())})
    db.commit()
    db.refresh(v)
    out = version_to_dict(v)
    bus.publish_threadsafe({"type": "version", "version": out})  # plain def endpoints run in a worker thread
    return out


@app.post("/api/versions/{vid}/judge")
async def judge_version(vid: str, body: JudgeIn, db: Session = Depends(get_session)) -> dict[str, Any]:
    v = _version(db, vid)
    a = _asset(db, v.asset_id)
    if body.mode == "metrics":
        from . import stages as stages_mod

        try:
            queue = active_queue()
            await stages_mod.judge_metrics(v.id, get_settings(), queue.clients if queue else {})
        except Exception as e:
            raise HTTPException(500, f"judge failed: {e}")
        db.expire_all()
        return version_to_dict(_version(db, vid))
    if body.mode == "local":
        if v.status != "done":
            raise HTTPException(409, f"version is {v.status}, not done")
        q = _queue()
        instance, message = q.place(JUDGE, a.category)  # judge_instance, or the card that takes over while it is off
        job = q.submit(instance, job_kind(v.stage, "judge", a.category), a.id, v.id, {"version_id": v.id, "op": "judge", "mode": "local"},
                       message=message)
        db.expire_all()
        out = version_to_dict(_version(db, vid))
        out["judge_job"] = job_to_dict(job)
        return out
    raise HTTPException(400, "mode must be metrics or local")


# ---------------------------------------------------------------------------
# Gallery
# ---------------------------------------------------------------------------


@app.get("/api/gallery")
def gallery(db: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Every asset with a finished export, newest first: one card per asset with its thumbnail and deliverables."""
    exports = db.scalars(select(Version).where(Version.stage == "export", Version.status == "done")
                         .order_by(Version.created_at.desc(), Version.id)).all()
    if not exports:
        return []
    asset_ids = {ex.asset_id for ex in exports}
    assets = {a.id: a for a in db.scalars(select(Asset).where(Asset.id.in_(asset_ids))).all()}
    projects = {p.id: p for p in db.scalars(select(Project).where(Project.id.in_({a.project_id for a in assets.values()}))).all()}
    kept: dict[tuple[str, str], Version] = {(v.asset_id, v.stage): v for v in db.scalars(
        select(Version).where(Version.asset_id.in_(asset_ids), Version.selected == True)).all()}  # noqa: E712
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for ex in exports:
        a = assets.get(ex.asset_id)
        if a is None or a.id in seen:
            continue
        # The kept export wins over a newer unkept one.
        k = kept.get((a.id, "export"))
        if k is not None and k.status == "done":
            ex = k
        seen.add(a.id)
        pr = projects.get(a.project_id)
        concept = kept.get((a.id, "concept"))
        files = ex.files
        thumb = None
        if concept is not None:
            thumb = concept.files.get("image") or concept.files.get("cutout")
        preview = files.get("glb")
        if preview is None:
            mesh = kept.get((a.id, "prep")) or kept.get((a.id, "mesh"))
            preview = mesh.files.get("glb") if mesh is not None else None
        out.append({
            "asset_id": a.id, "asset_name": a.name, "project_id": a.project_id,
            "project_name": pr.name if pr is not None else None,
            "category": a.category, "style_preset": a.style_preset,
            "platform_target": a.platform_target or (pr.platform_target if pr is not None else None),
            "created_at": _iso(a.created_at), "finished_at": _iso(ex.created_at),
            "export_version_id": ex.id,
            "thumbnail": thumb, "preview": preview,
            "delivered_to": ex.metrics.get("delivered_to"),
            "fbx": files.get("fbx"), "glb": files.get("glb"),
        })
    return out


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


@app.get("/api/jobs")
def list_jobs(asset_id: str | None = None, status: str | None = None, limit: int = 200, db: Session = Depends(get_session)) -> list[dict[str, Any]]:
    q = select(Job)
    if asset_id:
        q = q.where(Job.asset_id == asset_id)
    if status:
        q = q.where(Job.status == status)
    rows = db.scalars(q.order_by(Job.created_at.desc()).limit(max(1, min(limit, 500)))).all()
    return [job_to_dict(j) for j in rows]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    j = db.get(Job, job_id)
    if j is None:
        raise HTTPException(404, "job not found")
    return job_to_dict(j)


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    j = _queue().cancel(job_id)
    if j is None:
        raise HTTPException(404, "job not found")
    return job_to_dict(j)


@app.post("/api/queue/{instance}/pause")
def pause_queue(instance: str, paused: bool = True) -> dict[str, Any]:
    q = _queue()
    if instance not in q.workers:
        raise HTTPException(404, "unknown instance")
    q.set_paused(instance, paused)
    return q.status()


class GpuIn(BaseModel):
    enabled: bool | None = None
    # Kinds of work this card may do: pictures, model, rigging, review (jobs.KIND_GROUPS).
    allowed_kinds: list[str] | None = None
    free_after_job: bool | None = None
    # Shut down an engine the app started after this many minutes without work; 0 = never.
    stop_when_idle_minutes: int | None = Field(default=None, ge=0, le=1440)


async def _gpu_list(q: JobQueue) -> list[dict[str, Any]]:
    names = list(q.settings.instances)
    summaries = await asyncio.gather(*((q.clients.get(n) or _health_client(n, q.settings.instances[n].url)).cached_vram_summary(HEALTH_STATS_MAX_AGE)
                                       for n in names))
    return q.gpus(dict(zip(names, summaries)))


@app.get("/api/gpus")
async def list_gpus() -> list[dict[str, Any]]:
    """The graphics cards: on or off in the app, reachable, memory, what each is doing and what it can run."""
    return await _gpu_list(_queue())


class GpuSharingIn(BaseModel):
    # The card work goes to first, for every step it can run; null: each step's usual card. Left out: unchanged.
    default_instance: str | None = None
    # A card with nothing to do takes waiting work from a busy card it can run.
    share_when_busy: bool | None = None


@app.put("/api/gpus")
async def set_gpu_sharing(body: GpuSharingIn) -> list[dict[str, Any]]:
    """The settings shared by the cards (saved in prefs.json): the default card and sharing work when one is busy.
    Each card entry of the answer carries `is_default` and `share_when_busy`."""
    q = _queue()
    kwargs: dict[str, Any] = {}
    if "default_instance" in body.model_fields_set:
        kwargs["default_instance"] = body.default_instance or None
    if body.share_when_busy is not None:
        kwargs["share_when_busy"] = body.share_when_busy
    try:
        q.set_sharing(**kwargs)
    except KeyError:
        raise HTTPException(404, f"unknown graphics card {body.default_instance!r}; known: {list(q.settings.instances)}")
    return await _gpu_list(q)


@app.put("/api/gpus/{instance}")
async def set_gpu(instance: str, body: GpuIn) -> list[dict[str, Any]]:
    """Turn a card on or off, set what it may do and whether it frees its memory after each job (saved in
    prefs.json; fields left out stay). Async on purpose: the change runs on the workers' loop, so a worker never
    starts a job in the middle of it moving."""
    q = _queue()
    if instance not in q.settings.instances:
        raise HTTPException(404, f"unknown graphics card {instance!r}; known: {list(q.settings.instances)}")
    try:
        q.configure(instance, enabled=body.enabled, allowed_kinds=body.allowed_kinds, free_after_job=body.free_after_job,
                    stop_when_idle_minutes=body.stop_when_idle_minutes)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return await _gpu_list(q)


@app.post("/api/gpus/{instance}/start")
async def start_gpu_engine(instance: str) -> list[dict[str, Any]]:
    """Start the card's ComfyUI now (the Start and Try again buttons). Returns at once with the card "starting"; the
    "gpus" event follows the start. 409 with a plain message when the card is off or has no start script."""
    q = _queue()
    if instance not in q.settings.instances:
        raise HTTPException(404, f"unknown graphics card {instance!r}; known: {list(q.settings.instances)}")
    try:
        q.start_engine(instance)
    except ValueError as e:
        raise HTTPException(409, str(e))
    await asyncio.sleep(0)  # let the start begin, so the answer already says "starting"
    return await _gpu_list(q)


@app.post("/api/gpus/{instance}/stop")
async def stop_gpu_engine(instance: str) -> list[dict[str, Any]]:
    """Stop the card's ComfyUI, only if the app started it. 409 with a plain message for an engine started by
    someone else (ComfyUI Desktop, the launcher) or one busy with a step."""
    q = _queue()
    if instance not in q.settings.instances:
        raise HTTPException(404, f"unknown graphics card {instance!r}; known: {list(q.settings.instances)}")
    try:
        q.stop_engine(instance)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return await _gpu_list(q)


@app.websocket("/api/ws/events")
async def ws_events(ws: WebSocket) -> None:
    origin = ws.headers.get("origin")
    if origin and not is_allowed_origin(origin):
        await ws.close(code=1008)  # policy violation: a foreign page must not read the event stream
        return
    await ws.accept()
    q = bus.subscribe()
    try:
        await ws.send_json({"type": "hello", "version": __version__})
        while True:
            event = await q.get()
            await ws.send_json(event)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bus.unsubscribe(q)


# ---------------------------------------------------------------------------
# Library files
# ---------------------------------------------------------------------------


@app.get("/api/library/{path:path}")
def library_file(path: str) -> FileResponse:
    root = get_settings().library_path.resolve()
    if not path or "\\" in path or "\0" in path:
        raise HTTPException(400, "bad path")
    target = (root / path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(403, "path outside library")
    if not target.is_file():
        raise HTTPException(404, "file not found")
    if target.suffix.lower().startswith(".sqlite") or target == Path(get_settings().db_path).resolve():
        raise HTTPException(403, "the database is not served")
    media_type = {"glb": "model/gltf-binary", "fbx": "application/octet-stream", "json": "application/json"}.get(target.suffix.lower().lstrip("."))
    return FileResponse(target, media_type=media_type)


# ---------------------------------------------------------------------------
# Frontend (built) at /
# ---------------------------------------------------------------------------

class SpaStaticFiles(StaticFiles):
    """The built app with client-side routes: any path that is not a real file gets index.html, so deep links
    such as /gallery or /assets/<id> and a browser refresh work (the API keeps its own 404s)."""

    async def get_response(self, path: str, scope):  # type: ignore[override]
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            # Starlette joins the path with the OS separator, so it is "api\x" on Windows.
            if e.status_code == 404 and not path.replace("\\", "/").lstrip("/").startswith("api/"):
                return await super().get_response("index.html", scope)
            raise


if FRONTEND_DIST.is_dir() and (FRONTEND_DIST / "index.html").is_file():
    app.mount("/", SpaStaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
else:

    @app.get("/")
    def root_placeholder() -> dict[str, Any]:
        return {
            "app": "Video Game Asset Creator",
            "version": __version__,
            "note": "frontend/dist not built; run npm run build in frontend, or use the Vite dev server",
            "health": "/api/health",
        }
