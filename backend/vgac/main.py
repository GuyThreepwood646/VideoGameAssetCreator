"""FastAPI application for Video Game Asset Creator: every endpoint in docs/API.md.

Run with: python -m uvicorn vgac.main:app --port 8765 --reload (from the backend folder).
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

import psutil
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import __version__, library, reference
from .comfy import ComfyClient
from .config import BACKEND_DIR, get_settings
from .db import (JOB_STATUSES, STAGES, VERSION_STATUSES, Asset, Job, Project, Version, asset_to_dict, get_session, init_db,
                 job_to_dict, project_to_dict, version_to_dict)
from .events import bus
from .jobs import HOST, JobQueue, free_ram_gb, instance_for, job_kind

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("vgac")

FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist"
STYLE_MODELS = ("zturbo", "zbase", "klein", "anima", "pixel", "chroma")

queue: JobQueue | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global queue
    settings = get_settings()
    init_db()
    queue = JobQueue(settings)
    await queue.start()
    log.info("library at %s, db at %s", settings.library_path, settings.db_path)
    try:
        yield
    finally:
        await queue.stop()
        queue = None


app = FastAPI(title="Video Game Asset Creator", version=__version__, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class StyleLock(BaseModel):
    prompt_block: str = ""
    model: str | None = None
    loras: list[str] = Field(default_factory=list)
    palette: list[str] = Field(default_factory=list)
    negative: str = ""


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    style_lock: StyleLock | None = None
    platform_target: str = "pc_mid"


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    style_lock: StyleLock | None = None
    platform_target: str | None = None


class AssetIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: str
    style_preset: str
    platform_target: str | None = None
    prompt: str = ""
    parts: list[str] | None = None
    concept_model: str | None = None


class AssetPatch(BaseModel):
    name: str | None = None
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
    instruction: str = Field(min_length=1)
    view: str | None = None
    mask_png: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class AdjustIn(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


class JudgeIn(BaseModel):
    mode: str = "metrics"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _queue() -> JobQueue:
    if queue is None:
        raise HTTPException(503, "job queue not running")
    return queue


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


def _version(db: Session, vid: str) -> Version:
    v = db.get(Version, vid)
    if v is None:
        raise HTTPException(404, "version not found")
    return v


def _validate_platform(pid: str) -> None:
    try:
        reference.platform(pid)
    except KeyError as e:
        raise HTTPException(400, str(e))


def _validate_category(cid: str) -> None:
    try:
        reference.category(cid)
    except KeyError as e:
        raise HTTPException(400, str(e))


def _selected(db: Session, asset_id: str, stage: str) -> Version | None:
    return db.scalar(select(Version).where(Version.asset_id == asset_id, Version.stage == stage, Version.selected == True))  # noqa: E712


def _select(db: Session, v: Version) -> None:
    for sib in db.scalars(select(Version).where(Version.asset_id == v.asset_id, Version.stage == v.stage)).all():
        sib.selected = sib.id == v.id
    db.commit()


def _new_version(db: Session, asset: Asset, stage: str, parent: Version | None, params: dict[str, Any]) -> Version:
    merged = {k: v for k, v in reference.stage_defaults(stage).items() if v is not None}
    merged.update(params or {})
    v = Version(asset_id=asset.id, stage=stage, parent_version_id=parent.id if parent else None, status="queued")
    v.params = merged
    db.add(v)
    db.commit()
    db.refresh(v)
    # First version of a stage is selected automatically so the next stage has a parent.
    if _selected(db, asset.id, stage) is None:
        v.selected = True
        db.commit()
    return v


def _submit(db: Session, asset: Asset, v: Version, op: str, payload: dict[str, Any]) -> Job:
    q = _queue()
    kind = job_kind(v.stage, op, asset.category)
    instance = instance_for(v.stage, asset.category)
    body = {"version_id": v.id, "op": op, **payload}
    job = q.submit(instance, kind, asset.id, v.id, body)
    db.refresh(v)
    return job


# ---------------------------------------------------------------------------
# Health and reference data
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health() -> dict[str, Any]:
    settings = get_settings()
    clients = {name: ComfyClient(inst.url, name=name) for name, inst in settings.instances.items()}
    summaries = await asyncio.gather(*(c.vram_summary() for c in clients.values()))
    instances = {}
    for (name, inst), summary in zip(settings.instances.items(), summaries):
        instances[name] = {"url": inst.url, "gpu": inst.gpu, "role": inst.role, **summary}
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


@app.get("/api/categories")
def categories() -> list[dict[str, Any]]:
    return [{"id": c["id"], "label": c["label"], "rig_engine": c["rig_engine"], "symmetric": bool(c["symmetric"]), "pose_block": c["pose_block"],
             "size_m": c["size_m"], "parts_default": c.get("parts_default", []), "budgets": c.get("budgets", {}),
             "views": c.get("views"), "lod_ratios": c.get("lod_ratios"), "topology": c.get("topology"), "input_view_default": c.get("input_view_default")}
            for c in reference.categories()]


@app.get("/api/styles")
def styles() -> list[dict[str, Any]]:
    return [{"id": s["id"], "label": s["label"], "model": s["model"], "prompt_block": s["prompt_block"]} for s in reference.styles()]


@app.get("/api/platforms")
def platforms() -> list[dict[str, Any]]:
    return reference.platforms()


@app.get("/api/stages")
def stages_() -> list[dict[str, Any]]:
    return reference.stages()


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
    if lock.get("model") and lock["model"] not in STYLE_MODELS:
        raise HTTPException(400, f"style_lock.model must be one of {STYLE_MODELS}")
    p = Project(name=body.name, platform_target=body.platform_target)
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
    if body.name is not None:
        p.name = body.name
    if body.platform_target is not None:
        _validate_platform(body.platform_target)
        p.platform_target = body.platform_target
    if body.style_lock is not None:
        lock = body.style_lock.model_dump()
        if lock.get("model") and lock["model"] not in STYLE_MODELS:
            raise HTTPException(400, f"style_lock.model must be one of {STYLE_MODELS}")
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
    rows = db.scalars(select(Asset).where(Asset.project_id == pid).order_by(Asset.created_at.desc())).all()
    return [asset_to_dict(a, with_stages=True) for a in rows]


@app.post("/api/projects/{pid}/assets", status_code=201)
def create_asset(pid: str, body: AssetIn, db: Session = Depends(get_session)) -> dict[str, Any]:
    p = _project(db, pid)
    _validate_category(body.category)
    platform = body.platform_target or p.platform_target
    _validate_platform(platform)
    cat = reference.category(body.category)
    if body.concept_model and body.concept_model not in STYLE_MODELS:
        raise HTTPException(400, f"concept_model must be one of {STYLE_MODELS}")
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
    src = _selected(db, base.id, "concept")
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
        except Exception:  # noqa: BLE001
            continue
    copy.files = files
    db.commit()
    # Refine with the change so the identity holds.
    v = _new_version(db, a, "concept", copy, {**copy.params, **(body.params or {})})
    job = _submit(db, a, v, "refine", {"instruction": change, "view": None, "mask_png": None, "params": body.params or {}})
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
    if stage not in STAGES:
        raise HTTPException(400, f"unknown stage {stage!r}; stages: {list(STAGES)}")
    count = body.count if stage == "concept" else 1
    parent: Version | None = None
    if stage != "concept":
        if body.parent_version_id:
            parent = _version(db, body.parent_version_id)
            if parent.asset_id != a.id:
                raise HTTPException(400, "parent version belongs to another asset")
        else:
            # Walk back to the nearest earlier stage with a selected version, so categories that skip stages
            # (material has no turnaround) still find their parent.
            idx = STAGES.index(stage) - 1
            prev = STAGES[idx]
            parent = None
            while idx >= 0 and parent is None:
                prev = STAGES[idx]
                parent = _selected(db, a.id, prev)
                idx -= 1
            if parent is None:
                raise HTTPException(409, f"no selected {prev} version to build on")
        if parent.status != "done":
            raise HTTPException(409, f"parent version is {parent.status}, not done")
    params = dict(body.params or {})
    if stage == "concept" and not params.get("model") and getattr(a, "concept_model", None):
        params["model"] = a.concept_model
    versions, jobs = [], []
    for i in range(count):
        params = dict(body.params or {})
        if count > 1 and params.get("seed") is not None:
            params["seed"] = int(params["seed"]) + i
        v = _new_version(db, a, stage, parent, params)
        job = _submit(db, a, v, "run", {"params": params})
        versions.append(v)
        jobs.append(job)
    pairs = [{"job": job_to_dict(j), "version": version_to_dict(v)} for j, v in zip(jobs, versions)]
    return pairs if count > 1 else pairs[0]


@app.post("/api/versions/{vid}/refine")
def refine_version(vid: str, body: RefineIn, db: Session = Depends(get_session)) -> dict[str, Any]:
    parent = _version(db, vid)
    if parent.status != "done":
        raise HTTPException(409, f"version is {parent.status}, not done")
    a = _asset(db, parent.asset_id)
    if body.view and body.view not in reference.TURNAROUND_VIEWS:
        raise HTTPException(400, f"unknown view {body.view!r}")
    v = _new_version(db, a, parent.stage, parent, {**parent.params, **(body.params or {})})
    job = _submit(db, a, v, "refine", {"instruction": body.instruction, "view": body.view, "mask_png": body.mask_png, "params": body.params or {}})
    return {"job": job_to_dict(job), "version": version_to_dict(v)}


@app.post("/api/versions/{vid}/adjust")
def adjust_version(vid: str, body: AdjustIn, db: Session = Depends(get_session)) -> dict[str, Any]:
    parent = _version(db, vid)
    if parent.status != "done":
        raise HTTPException(409, f"version is {parent.status}, not done")
    a = _asset(db, parent.asset_id)
    v = _new_version(db, a, parent.stage, parent, {**parent.params, **(body.params or {})})
    job = _submit(db, a, v, "adjust", {"params": body.params or {}})
    return {"job": job_to_dict(job), "version": version_to_dict(v)}


@app.post("/api/versions/{vid}/select")
def select_version(vid: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    v = _version(db, vid)
    _select(db, v)
    db.refresh(v)
    return version_to_dict(v)


@app.post("/api/versions/{vid}/reject")
def reject_version(vid: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    v = _version(db, vid)
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


@app.post("/api/versions/{vid}/judge")
async def judge_version(vid: str, body: JudgeIn, db: Session = Depends(get_session)) -> dict[str, Any]:
    v = _version(db, vid)
    a = _asset(db, v.asset_id)
    if body.mode == "metrics":
        from . import stages as stages_mod

        try:
            await stages_mod.judge_metrics(v.id, get_settings(), queue.clients if queue else {})
        except Exception as e:
            raise HTTPException(500, f"judge failed: {e}")
        db.expire_all()
        return version_to_dict(_version(db, vid))
    if body.mode == "local":
        if v.status != "done":
            raise HTTPException(409, f"version is {v.status}, not done")
        q = _queue()
        job = q.submit("b", job_kind(v.stage, "judge", a.category), a.id, v.id, {"version_id": v.id, "op": "judge", "mode": "local"})
        db.expire_all()
        out = version_to_dict(_version(db, vid))
        out["judge_job"] = job_to_dict(job)
        return out
    raise HTTPException(400, "mode must be metrics or local")


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


@app.websocket("/api/ws/events")
async def ws_events(ws: WebSocket) -> None:
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
    media_type = {"glb": "model/gltf-binary", "fbx": "application/octet-stream", "json": "application/json"}.get(target.suffix.lower().lstrip("."))
    return FileResponse(target, media_type=media_type)


# ---------------------------------------------------------------------------
# Frontend (built) at /
# ---------------------------------------------------------------------------

if FRONTEND_DIST.is_dir() and (FRONTEND_DIST / "index.html").is_file():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
else:

    @app.get("/")
    def root_placeholder() -> dict[str, Any]:
        return {
            "app": "Video Game Asset Creator",
            "version": __version__,
            "note": "frontend/dist not built; run npm run build in frontend, or use the Vite dev server",
            "health": "/api/health",
        }
