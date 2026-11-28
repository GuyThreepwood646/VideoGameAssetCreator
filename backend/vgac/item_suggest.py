"""Suggested worn item (WP-69 follow-up): the app proposes where a backpack, quiver or similar sits on a humanoid, from
the description the person already gave. The person keeps it, adjusts it with points, or dismisses it. Nothing is
applied silently.

When: a humanoid whose description has a kept worn item that sits on the back (equipment.found with choice keep, and an
items.yaml worn type whose socket is Socket_Back), on a finished rig version. Otherwise no suggestion is offered.

How: the suggestion is a job (kind suggest.run) on the host's queue, so it runs one at a time with the other Blender
work and shows in the jobs list. It is queued automatically when a rig job finishes for an eligible humanoid with the
finder installed, and by the "Suggest" button on a done rig with no suggestion, or with a failed one. Opening the page
starts nothing.

The job: Blender renders the finished rig from six views (blender/suggest_render.py); a finder worker in its own Python
environment (finder_worker.py: Grounding DINO tiny, then SAM 2.1 small, both Apache-2.0) finds the item in each view;
the votes are kept per vertex (item_region.vote_vertices), inside the place where that item can be
(item_region.expected_place), tidied into the pieces that grow together, and turned into one stiff point that
reproduces the region (item_region.points_for_region).

Not installed: nothing is queued and nothing is said (no error). The models live under local/finder; a setup script
(setup/Setup-ItemFinder.ps1) makes that folder. data/models.yaml records their licence and source.

State: <version dir>/suggestion.json holds {state, item, label, job_id, ...} with state queued | running | done |
failed | dismissed | kept. A queued or running state whose job is no longer queued or running (the server stopped, or the
job was cancelled) reads as failed, so the button can retry it.
"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np

from . import config, item_region, items, library

log = logging.getLogger(__name__)

WORKER = Path(__file__).resolve().parent / "finder_worker.py"
FINDER_DIR = config.LOCAL_DIR / "finder"
MIN_VIEWS = 2  # a polygon is part of the item when at least this many views voted for it
STATE_FILE = "suggestion.json"
SUGGEST_KIND = "suggest.run"
PHRASE_WORDS = "a {item} on the back"  # the phrase the detector is asked for
VIEW_NAMES = ["back", "left", "right", "back_left", "back_right", "top_back"]
LIVE = ("queued", "running")  # job and suggestion states that are still in progress
ANSWERED = ("done", "dismissed", "kept")  # states that are not queued again by the page or the trigger


def finder_python(settings: Any) -> Path:
    return Path(getattr(settings, "finder_python", None) or (FINDER_DIR / "venv" / "Scripts" / "python.exe"))


def finder_models(settings: Any) -> Path:
    return Path(getattr(settings, "finder_models", None) or (FINDER_DIR / "hf"))


def installed(settings: Any) -> bool:
    """True when the finder's Python and both model folders are present. Never raises."""
    try:
        py = finder_python(settings)
        models = finder_models(settings)
        if not py.is_file():
            return False
        hub = models / "hub"
        names = [p.name for p in hub.iterdir()] if hub.is_dir() else []
        return any("grounding-dino-tiny" in n for n in names) and any("sam2.1-hiera-small" in n for n in names)
    except OSError:
        return False


def back_worn_items(equipment: dict[str, Any] | None) -> list[dict[str, str]]:
    """The kept items that are worn on the back: [{type, label}] from equipment.found (choice keep) and items.yaml
    (kind worn, socket Socket_Back)."""
    if not equipment or equipment.get("choice") != "keep":
        return []
    out = []
    for f in equipment.get("found") or []:
        tid = f.get("type")
        spec = items.types().get(tid) or {}
        if spec.get("kind") == "worn" and spec.get("socket") == "Socket_Back":
            out.append({"type": tid, "label": str(spec.get("label") or tid)})
    return out


def eligible(asset: Any, version: Any) -> list[dict[str, str]]:
    """The back-worn items to suggest for this rig version: a humanoid, a finished rig, a kept back item."""
    if getattr(asset, "category", None) != "humanoid":
        return []
    if getattr(version, "stage", None) != "rig" or getattr(version, "status", None) != "done":
        return []
    return back_worn_items(asset.equipment)


def read_state(version_dir: Path) -> dict[str, Any] | None:
    path = version_dir / STATE_FILE
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_state(version_dir: Path, state: dict[str, Any]) -> None:
    version_dir.mkdir(parents=True, exist_ok=True)
    (version_dir / STATE_FILE).write_text(json.dumps(state, indent=1), encoding="utf-8")


def set_state(version_dir: Path, value: str) -> dict[str, Any] | None:
    """Mark the suggestion dismissed or kept (it is not offered again for this rig version)."""
    state = read_state(version_dir)
    if state is None:
        return None
    state["state"] = value
    write_state(version_dir, state)
    return state


def _job_live(job_id: str | None) -> bool:
    """True when the job is still queued or running in the app's queue."""
    if not job_id:
        return False
    from .db import Job, session_factory

    with session_factory()() as db:
        job = db.get(Job, job_id)
        return job is not None and job.status in LIVE


def current_state(version_dir: Path) -> dict[str, Any] | None:
    """The state as it stands: a queued or running state whose job is no longer queued or running (the server stopped,
    or the job was cancelled or failed) reads as failed, so the button can retry it."""
    state = read_state(version_dir)
    if state is None:
        return None
    if state.get("state") in LIVE and not _job_live(state.get("job_id")):
        state = {**state, "state": "failed", "reason": "the suggestion stopped before it finished; try again"}
    return state


def compute(settings: Any, rig_glb: Path, work: Path, item: dict[str, str], ctx: Any = None) -> dict[str, Any]:
    """The whole suggestion for one item in `work`: renders, detector, vote, placement, points. Raises on failure.
    ctx: the job context (cancellation and process registration), or None."""
    from .tools import run_blender, run_process

    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    try:
        on_start = ctx.register_process if ctx is not None else None
        if ctx is not None:
            ctx.check_cancelled()
        run_blender(settings, "suggest_render.py", [rig_glb, work], work / "render.log", timeout=1800, on_start=on_start)
        if ctx is not None:
            ctx.check_cancelled()
        phrase = PHRASE_WORDS.format(item=item["label"])
        cmd = [str(finder_python(settings)), str(WORKER), "hair", str(work), phrase, str(finder_models(settings))]
        run_process(cmd, log_path=work / "finder.log", timeout=1800, on_start=on_start)
        return _vote(work, item, phrase)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _vote(work: Path, item: dict[str, str], phrase: str) -> dict[str, Any]:
    body = np.load(work / "body.npz", allow_pickle=True)
    verts = body["verts"]
    faces = body["faces"]
    polys = json.loads((work / "mesh_polys.json").read_text(encoding="utf-8"))["polygons"]
    nverts = len(verts)
    votes = np.zeros(len(polys), dtype=np.int64)
    for v in VIEW_NAMES:
        ids_path = work / f"{v}_ids.npy"
        mask_path = work / f"{v}_mask.npy"
        if not ids_path.is_file() or not mask_path.is_file():
            continue
        ids = np.load(ids_path)
        mask = np.load(mask_path)
        if mask.shape != ids.shape:
            raise ValueError(f"the {v} picture and its id map differ in size")
        votes[np.unique(ids[mask & (ids >= 0)])] += 1
    det = json.loads((work / "detection.json").read_text(encoding="utf-8"))
    view_scores = {k: x.get("score") for k, x in (det.get("views") or {}).items()}
    voted = item_region.vote_vertices(polys, votes, nverts, MIN_VIEWS)
    fr = item_region.frame(body["bone_names"], body["bone_heads"], verts)
    base = {"item": item["type"], "label": item["label"], "phrase": phrase, "views": view_scores}
    if not fr.get("ok"):
        return {**base, "state": "failed", "reason": fr.get("reason", "no skeleton frame")}
    allowed = voted & item_region.expected_place(verts, fr)
    region = item_region.tidy(verts, faces, allowed)
    share = float(region.sum()) / max(1, nverts)
    if region.sum() == 0:
        return {**base, "state": "done", "vertices": 0, "share": 0.0, "large": False, "points": [], "found": False}
    points, covered = item_region.points_for_region(verts, faces, region)
    return {**base, "state": "done", "vertices": int(region.sum()), "share": round(share, 4), "covered": round(covered, 4),
            "large": share > item_region.LARGE_SHARE, "points": [item_region.to_gltf(p) for p in points],
            "region": [int(i) for i in np.where(region)[0]], "found": True}


def public(state: dict[str, Any] | None) -> dict[str, Any] | None:
    """What the rig page reads: the state without the big region list."""
    if state is None:
        return None
    return {k: v for k, v in state.items() if k != "region"}


# --- the job -----------------------------------------------------------------------------------------------------------


def _submit(asset_id: str, payload: dict[str, Any], message: str) -> Any:
    """Queue the suggest job on the host (one at a time with the other host work). Tests replace this."""
    from . import jobs, pipeline

    return pipeline.queue().submit(jobs.HOST, SUGGEST_KIND, asset_id, None, payload, message=message)


def enqueue(version_dir: Path, asset: Any, version: Any, item: dict[str, str]) -> Any:
    """Queue one suggestion for this rig version and record it as queued. Returns the job."""
    payload = {"version_id": version.id, "item": item}
    job = _submit(asset.id, payload, "waiting to look at the picture")
    write_state(version_dir, {"state": "queued", "item": item["type"], "label": item["label"], "job_id": job.id})
    return job


def request(settings: Any, asset: Any, version: Any, version_dir: Path) -> dict[str, Any]:
    """The button: queue a suggestion unless one is live or answered (done, kept, dismissed). A failed one is queued
    again. Returns the current state (none when nothing can be suggested)."""
    items_ = eligible(asset, version)
    if not items_ or not installed(settings):
        return {"state": "none"}
    prior = current_state(version_dir)
    if prior and prior.get("state") in LIVE + ANSWERED:
        return prior
    job = enqueue(version_dir, asset, version, items_[0])
    return {"state": "queued", "job_id": job.id, "item": items_[0]["type"], "label": items_[0]["label"]}


def after_rig(kind: str, version_id: str | None, asset_id: str | None) -> None:
    """The finished-job hook (jobs.FINISHED_HOOKS): a rig step that finished for an eligible humanoid with the finder
    installed queues the suggestion once. A failed one is not retried by itself (the button does that)."""
    if kind == "rig.pose_preview" or not kind.startswith("rig.") or not version_id or not asset_id:  # WP-76: a preview is no rig
        return
    from .config import get_settings
    from .db import Asset, Version, session_factory

    settings = get_settings()
    with session_factory()() as db:
        version = db.get(Version, version_id)
        asset = db.get(Asset, asset_id)
        if version is None or asset is None:
            return
        # The hook runs before the finished job marks its version done, so the version is read as done here.
        finished = SimpleNamespace(id=version.id, stage=version.stage, status="done")
        maybe_enqueue_after_rig(settings, asset, finished, library.version_dir(asset, version, settings, create=False))


def maybe_enqueue_after_rig(settings: Any, asset: Any, version: Any, version_dir: Path) -> Any | None:
    """Queue the suggestion once after a finished rig: only for an eligible humanoid with the finder installed, and only
    when no suggestion exists yet for this rig version. Returns the job queued, or None."""
    if not eligible(asset, version) or not installed(settings):
        return None
    if read_state(version_dir) is not None:
        return None
    return enqueue(version_dir, asset, version, eligible(asset, version)[0])


def work_dir(version_id: str) -> Path:
    """The renders' folder for one rig version: the full version id, so two suggestions never share a folder."""
    return config.PROJECT_DIR / "runtime" / "tmp" / f"sg-{version_id}"


def run_job(ctx: Any, payload: dict[str, Any]) -> None:
    """Runs the suggest job (jobs.execute, kind suggest.run). Writes the state as it goes; a failure is recorded as a
    failed state and raised, so the job shows the error."""
    from .db import Asset, Version, session_factory

    settings = config.get_settings()
    with session_factory()() as db:
        version = db.get(Version, payload.get("version_id"))
        if version is None:
            raise ValueError(f"suggestion: version {payload.get('version_id')!r} not found")
        asset = db.get(Asset, version.asset_id)
        version_dir = library.version_dir(asset, version, settings)
        item = dict(payload.get("item") or {})
        base = {"item": item.get("type"), "label": item.get("label"), "job_id": ctx.job_id}
        rig_glb = library.version_file(version, "glb", settings)
        write_state(version_dir, {**base, "state": "running"})
        if rig_glb is None:
            write_state(version_dir, {**base, "state": "failed", "reason": "this rig has no model file"})
            raise FileNotFoundError("this rig has no model file")
        try:
            state = compute(settings, Path(rig_glb), work_dir(version.id), item, ctx=ctx)
        except Exception as e:  # noqa: BLE001 - recorded on the state, then raised so the job shows it
            write_state(version_dir, {**base, "state": "failed", "reason": f"{type(e).__name__}: {e}"[:600]})
            raise
        write_state(version_dir, {**base, **state})
