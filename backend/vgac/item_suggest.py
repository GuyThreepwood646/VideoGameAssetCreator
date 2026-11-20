"""Suggested worn item (WP-69 follow-up): the app proposes where a backpack, quiver or similar sits on a humanoid, from
the description the person already gave. The person keeps it, adjusts it with points, or dismisses it. Nothing is
applied silently.

When: a humanoid whose description has a kept worn item that sits on the back (equipment.found with choice keep, and an
items.yaml worn type whose socket is Socket_Back), on a finished rig version. Otherwise no suggestion is offered.

How: Blender renders the finished rig from six views with a texture and a polygon id map (blender/suggest_render.py);
a finder worker in its own Python environment (finder_worker.py: Grounding DINO tiny, then SAM 2.1 small, both
Apache-2.0) finds the item in each view; the votes are kept per vertex (item_region.vote_vertices), inside the place
where that item can be (item_region.expected_place), tidied into the pieces that grow together, and turned into one
stiff point that reproduces the region (item_region.points_for_region).

Not installed: the suggestion is not offered and nothing is said (no error). The models live under local/finder; a
setup script (setup/Setup-ItemFinder.ps1) makes that folder. data/models.yaml records their licence and source.

Files: <version dir>/suggestion/ holds the renders and the worker output; <version dir>/suggestion.json holds the state:
{state: running | done | failed | dismissed | kept, item, phrase, ...}.
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
from pathlib import Path
from typing import Any

import numpy as np

from . import config, item_region, items, library

log = logging.getLogger(__name__)

WORKER = Path(__file__).resolve().parent / "finder_worker.py"
FINDER_DIR = config.LOCAL_DIR / "finder"
MIN_VIEWS = 2  # a polygon is part of the item when at least this many views voted for it
STATE_FILE = "suggestion.json"
PHRASE_WORDS = "a {item} on the back"  # the phrase the detector is asked for

_running: dict[str, threading.Thread] = {}
_lock = threading.Lock()


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


def work_root() -> Path:
    """Where the renders and the worker's output go: a short folder under the project's runtime/tmp. The library path
    can be long (a worktree, a deep library), and Blender's image writer fails past the Windows path limit."""
    return config.PROJECT_DIR / "runtime" / "tmp"


def compute(settings: Any, rig_glb: Path, version_dir: Path, item: dict[str, str]) -> dict[str, Any]:
    """The whole suggestion for one item: renders, detector, vote, placement, points. Raises on failure (the caller
    records it). Returns the state written to suggestion.json."""
    from .tools import run_blender, run_process

    work = work_root() / f"sg-{version_dir.name[:8]}"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    try:
        return _compute_in(settings, rig_glb, work, item)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _compute_in(settings: Any, rig_glb: Path, work: Path, item: dict[str, str]) -> dict[str, Any]:
    from .tools import run_blender, run_process

    run_blender(settings, "suggest_render.py", [rig_glb, work], work / "render.log", timeout=1800)
    phrase = PHRASE_WORDS.format(item=item["label"])
    cmd = [str(finder_python(settings)), str(WORKER), str(work), phrase, str(finder_models(settings))]
    run_process(cmd, log_path=work / "finder.log", timeout=1800)
    body = np.load(work / "body.npz", allow_pickle=True)
    verts = body["verts"]
    faces = body["faces"]
    polys = json.loads((work / "mesh_polys.json").read_text(encoding="utf-8"))["polygons"]
    nverts = len(verts)
    npoly = len(polys)
    votes = np.zeros(npoly, dtype=np.int64)
    views_used = {}
    for v in ["back", "left", "right", "back_left", "back_right", "top_back"]:
        ids_path = work / f"{v}_ids.npy"
        mask_path = work / f"{v}_mask.npy"
        if not ids_path.is_file() or not mask_path.is_file():
            continue
        ids = np.load(ids_path)
        mask = np.load(mask_path)
        if mask.shape != ids.shape:
            raise ValueError(f"the {v} picture and its id map differ in size")
        seen = np.unique(ids[mask & (ids >= 0)])
        votes[seen] += 1
        views_used[v] = int(mask.sum())
    det = json.loads((work / "detection.json").read_text(encoding="utf-8"))
    view_scores = {k: x.get("score") for k, x in (det.get("views") or {}).items()}
    voted = item_region.vote_vertices(polys, votes, nverts, MIN_VIEWS)
    fr = item_region.frame(body["bone_names"], body["bone_heads"], verts)
    if not fr.get("ok"):
        return {"state": "failed", "item": item["type"], "reason": fr.get("reason", "no skeleton frame")}
    allowed = voted & item_region.expected_place(verts, fr)
    region = item_region.tidy(verts, faces, allowed)
    share = float(region.sum()) / max(1, nverts)
    if region.sum() == 0:
        return {"state": "done", "item": item["type"], "label": item["label"], "phrase": phrase, "vertices": 0,
                "share": 0.0, "large": False, "points": [], "views": view_scores, "found": False}
    points, covered = item_region.points_for_region(verts, faces, region)
    gltf_points = [item_region.to_gltf(p) for p in points]
    keep_vertices = [int(i) for i in np.where(region)[0]]
    return {"state": "done", "item": item["type"], "label": item["label"], "phrase": phrase,
            "vertices": len(keep_vertices), "share": round(share, 4), "covered": round(covered, 4),
            "large": share > item_region.LARGE_SHARE, "points": gltf_points, "region": keep_vertices,
            "views": view_scores, "found": True}


def start(settings: Any, asset: Any, version: Any, version_dir: Path, rig_glb: Path) -> dict[str, Any]:
    """Start the suggestion in a background thread unless one is already done, running or dismissed. Returns the
    state (running when it started)."""
    key = str(version.id)
    items_ = eligible(asset, version)
    if not items_:
        return {"state": "none"}
    prior = read_state(version_dir)
    if prior and prior.get("state") in ("done", "dismissed", "kept", "failed") and prior.get("item") == items_[0]["type"]:
        return prior
    with _lock:
        t = _running.get(key)
        if t is not None and t.is_alive():
            return {"state": "running", "item": items_[0]["type"], "label": items_[0]["label"]}
        write_state(version_dir, {"state": "running", "item": items_[0]["type"], "label": items_[0]["label"]})

        def run():
            try:
                state = compute(settings, rig_glb, version_dir, items_[0])
            except Exception as e:  # noqa: BLE001 - recorded for the person's page, not raised
                log.warning("item suggestion failed: %s", e)
                state = {"state": "failed", "item": items_[0]["type"], "reason": f"{type(e).__name__}: {e}"}
            write_state(version_dir, state)

        th = threading.Thread(target=run, name=f"suggest-{key[:8]}", daemon=True)
        _running[key] = th
        th.start()
    return {"state": "running", "item": items_[0]["type"], "label": items_[0]["label"]}


def public(state: dict[str, Any] | None) -> dict[str, Any] | None:
    """What the rig page reads: the suggestion without the big region list."""
    if state is None:
        return None
    return {k: v for k, v in state.items() if k != "region"}
