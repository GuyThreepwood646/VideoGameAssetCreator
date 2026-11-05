"""FastAPI application for Video Game Asset Creator: every endpoint in docs/API.md.

Run with: python -m uvicorn vgac.main:app --port 8765 --reload (from the backend folder).
"""

from __future__ import annotations

import asyncio
import logging
import re
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
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from . import __version__, autopilot, library, pipeline, reference
from .comfy import ComfyClient
from .config import BACKEND_DIR, get_settings
from .db import (JOB_STATUSES, STAGES, VERSION_STATUSES, Asset, Job, Project, Version, asset_to_dict, get_session, init_db, utcnow,
                 job_to_dict, project_to_dict, version_to_dict, _iso)
from .events import bus
from .jobs import HOST, JUDGE, JobQueue, active_queue, free_ram_gb, job_kind
from .pipeline import PipelineError

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

LOCAL_ORIGIN = re.compile(r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$", re.IGNORECASE)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=LOCAL_ORIGIN.pattern,
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
    if origin and request.method not in ("GET", "HEAD", "OPTIONS") and not LOCAL_ORIGIN.match(origin):
        return JSONResponse({"detail": "cross-site requests are not allowed"}, status_code=403)
    return await call_next(request)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class StyleLock(BaseModel):
    preset: str | None = None
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


class AssetPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    prompt: str | None = None
    category: str | None = None
    parts: list[str] | None = None
    platform_target: str | None = None
    style_preset: str | None = None


class RunIn(BaseModel):
    parent_version_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    count: int = Field(default=1, ge=1, le=4)


class RefineIn(BaseModel):
    instruction: str = Field(min_length=1, max_length=4000)
    view: str | None = None
    mask_png: str | None = Field(default=None, max_length=6_000_000)
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
    return [project_to_dict(p) for p in db.scalars(select(Project).order_by(Project.created_at.desc())).all()]


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
    a = Asset(project_id=pid, name=body.name, category=body.category, style_preset=body.style_preset, platform_target=platform, prompt=body.prompt)
    a.concept_model = body.concept_model
    a.parts = body.parts if body.parts is not None else list(cat.get("parts_default") or [])
    db.add(a)
    db.commit()
    db.refresh(a)
    return asset_to_dict(a)


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
    pairs = pipeline.run_stage(db, a, stage, count=body.count, params=body.params, parent_version_id=body.parent_version_id)
    return pairs if len(pairs) > 1 else pairs[0]


class AutopilotIn(BaseModel):
    until_stage: str = "export"
    stop_on: str = "block"
    fix: dict[str, Any] | None = None


@app.post("/api/assets/{aid}/autopilot")
async def start_autopilot(aid: str, body: AutopilotIn | None = None, db: Session = Depends(get_session)) -> dict[str, Any]:
    a = _asset(db, aid)
    body = body or AutopilotIn()
    if body.fix and body.fix.get("stage") in STAGES:
        body.fix = {**body.fix, "params": _params(body.fix["stage"], body.fix.get("params"), a.category)}
    try:
        return autopilot.start(a.id, until_stage=body.until_stage, stop_on=body.stop_on, fix=body.fix)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/assets/{aid}/autopilot")
def get_autopilot(aid: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    return autopilot.get_state(_asset(db, aid))


@app.post("/api/assets/{aid}/autopilot/stop")
async def stop_autopilot(aid: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    _asset(db, aid)
    return autopilot.stop(aid)


@app.post("/api/versions/{vid}/refine")
def refine_version(vid: str, body: RefineIn, db: Session = Depends(get_session)) -> dict[str, Any]:
    parent = _version(db, vid)
    if parent.status != "done":
        raise HTTPException(409, f"version is {parent.status}, not done")
    a = _asset(db, parent.asset_id)
    if body.view and body.view not in reference.TURNAROUND_VIEWS:
        raise HTTPException(400, f"unknown view {body.view!r}")
    _check_mask(body.mask_png)
    rparams = _params(parent.stage, body.params, a.category)
    v = pipeline.new_version(db, a, parent.stage, parent, {**parent.params, **rparams})
    job = pipeline.submit(db, a, v, "refine", {"instruction": body.instruction, "view": body.view, "mask_png": body.mask_png, "params": rparams})
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


@app.get("/api/versions/{vid}")
def get_version(vid: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    return version_to_dict(_version(db, vid))


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
    if origin and not LOCAL_ORIGIN.match(origin):
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
