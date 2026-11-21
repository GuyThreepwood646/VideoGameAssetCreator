"""The resting pose preview (WP-76): the hands of a finished rig in the hand shape and finger curl the rig step's
Resting pose group would give, shown before a rig run.

How: a job (kind rig.pose_preview on the host queue, so it runs one at a time with the other Blender work). It makes
two short moves (idle and attack) with procanim into the version's preview folder, renders each hand close-up with
blender/hand_sheet.py, and joins them into one picture (rows are the moves, columns the left and right hand at two
moments). The rig itself is not touched. State is in <version dir>/pose_preview/state.json; the picture is
<version dir>/pose_preview/preview.png.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from . import library  # noqa: E402

KIND = "rig.pose_preview"
DIR = "pose_preview"
STATE = "state.json"
PICTURE = "preview.png"
CLIPS = ("idle", "attack")
FRAMES = 2  # per move: the start and the middle of the move
CELL = 256
LIVE = ("queued", "running")


def _dir(version_dir: Path) -> Path:
    return version_dir / DIR


def current_state(version_dir: Path) -> dict[str, Any] | None:
    p = _dir(version_dir) / STATE
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_state(version_dir: Path, state: dict[str, Any]) -> None:
    d = _dir(version_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / STATE).write_text(json.dumps(state, indent=1), encoding="utf-8")


def public(state: dict[str, Any] | None) -> dict[str, Any]:
    """What the page reads: the state, and the picture's address only when it exists."""
    if not state:
        return {"state": "none"}
    out = {k: v for k, v in state.items() if k in ("state", "reason", "settings", "job_id")}
    if state.get("state") == "done":
        out["picture"] = True
    return out


def settings_of(params: dict[str, Any]) -> dict[str, Any]:
    """The three knobs the preview shows, checked the way the rig step checks them (ValueError when one is not valid)."""
    from .blender import gait

    return {
        "arm_pose": gait.arm_pose_choice(params.get("arm_pose")),
        "hand_pose": gait.hand_pose_choice(params.get("hand_pose")),
        "finger_curl": gait.finger_curl_amount(params.get("finger_curl")),
    }


def request(version_dir: Path, asset: Any, version: Any, params: dict[str, Any]) -> dict[str, Any]:
    """The Preview button: queue one preview on the host unless one is already live for the same settings."""
    from . import jobs, pipeline

    chosen = settings_of(params)
    prior = current_state(version_dir)
    if prior and prior.get("state") in LIVE and prior.get("settings") == chosen:
        return public(prior)
    # No asset id: a preview is not a step of the asset, so a failed preview does not mark the asset's steps as failed.
    job = pipeline.queue().submit(jobs.HOST, KIND, None, None, {"version_id": version.id, "settings": chosen},
                                  message="waiting to make the hand preview")
    write_state(version_dir, {"state": "queued", "settings": chosen, "job_id": job.id})
    return public(current_state(version_dir))


def _picture(sheet_dir: Path, out: Path) -> None:
    """Join the hand pictures: a row per move, then for each of two moments the left hand and the right hand."""
    import json as _json

    from PIL import Image, ImageDraw

    info = _json.loads((sheet_dir / "hands.json").read_text(encoding="utf-8"))
    cols = []  # (frame index, side label)
    for i in range(FRAMES):
        for side in ("Left", "Right"):
            cols.append((i, side))
    label_h = 24
    head_w = 120
    W = head_w + len(cols) * CELL
    H = label_h + len(CLIPS) * CELL
    img = Image.new("RGB", (W, H), (200, 200, 200))
    draw = ImageDraw.Draw(img)
    for j, (i, side) in enumerate(cols):
        moment = "start" if i == 0 else "middle"
        draw.text((head_w + j * CELL + 6, 6), f"{side} hand, {moment} of the move", fill=(20, 20, 20))
    for r, clip in enumerate(CLIPS):
        entry = info.get("clips", {}).get(clip)
        y = label_h + r * CELL
        name = {"idle": "Stand still", "attack": "Attack"}.get(clip, clip)
        draw.text((6, y + 6), name, fill=(20, 20, 20))
        if not entry:
            continue
        for j, (i, side) in enumerate(cols):
            match = next((f for f in entry["frames"] if f["side"] == side and f["file"].startswith(f"{clip}_{i}_")), None)
            if match is None:
                continue
            tile = Image.open(sheet_dir / match["file"]).convert("RGB").resize((CELL, CELL))
            img.paste(tile, (head_w + j * CELL, y))
    img.save(out, format="PNG")


def run_job(ctx: Any, payload: dict[str, Any]) -> None:
    """Runs the preview job (jobs.execute, kind rig.pose_preview). A failure is recorded and raised."""
    from .config import get_settings
    from .db import Asset, Version, session_factory
    from . import library
    from .tools import run_blender

    settings = get_settings()
    chosen = payload.get("settings") or {}
    with session_factory()() as db:
        version = db.get(Version, payload.get("version_id"))
        if version is None:
            raise ValueError(f"pose preview: version {payload.get('version_id')!r} not found")
        asset = db.get(Asset, version.asset_id)
        version_dir = library.version_dir(asset, version, settings)
    base = {"settings": chosen, "job_id": ctx.job_id}
    write_state(version_dir, {**base, "state": "running"})
    try:
        rig_glb = library.version_file(version, "glb", settings)
        if rig_glb is None:
            raise FileNotFoundError("this rig has no model file")
        work = _dir(version_dir)
        work.mkdir(parents=True, exist_ok=True)
        moves = work / "moves"
        if moves.exists():
            shutil.rmtree(moves)
        moves.mkdir(parents=True)
        anim = moves / "anim.glb"
        ctx.check_cancelled()
        run_blender(settings, "procanim.py", [Path(rig_glb), anim, 24, "--motion", "auto", "--clips", ",".join(CLIPS),
                                              "--arm-pose", chosen["arm_pose"], "--hand-pose", chosen["hand_pose"],
                                              "--finger-curl", f"{chosen['finger_curl']:g}"],
                    moves / "procanim.log", timeout=1200)
        ctx.check_cancelled()
        hands = moves / "hands"
        run_blender(settings, "hand_sheet.py", [anim, hands, ",".join(CLIPS), FRAMES, CELL], moves / "hand_sheet.log", timeout=1200)
        _picture(hands, work / PICTURE)
        write_state(version_dir, {**base, "state": "done"})
    except Exception as e:  # noqa: BLE001 - recorded on the state, then raised so the job shows it
        write_state(version_dir, {**base, "state": "failed", "reason": f"{type(e).__name__}: {e}"[:600]})
        raise


# --- HTTP (docs/API.md "Resting pose preview") ---------------------------------------------------------------------
from fastapi import APIRouter, Depends, HTTPException  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from .config import get_settings  # noqa: E402
from .db import Asset, Version, get_session  # noqa: E402

router = APIRouter()


class PreviewIn(BaseModel):
    settings: dict[str, Any] = {}


def _rig_version(db: Session, vid: str) -> tuple[Version, Asset, Path]:
    v = db.get(Version, vid)
    if v is None:
        raise HTTPException(404, "version not found")
    if v.stage != "rig":
        raise HTTPException(400, "the resting pose preview is for a rig version")
    a = db.get(Asset, v.asset_id)
    return v, a, library.version_dir(a, v, get_settings(), create=False)


@router.get("/api/versions/{vid}/pose-preview")
def get_pose_preview(vid: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    v, a, version_dir = _rig_version(db, vid)
    return {"version_id": v.id, "state": public(current_state(version_dir))}


@router.post("/api/versions/{vid}/pose-preview")
def start_pose_preview(vid: str, body: PreviewIn, db: Session = Depends(get_session)) -> dict[str, Any]:
    """The Preview button on the rig step: queues one hand preview with the form's Resting pose values."""
    v, a, version_dir = _rig_version(db, vid)
    try:
        request(version_dir, a, v, body.settings)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return {"version_id": v.id, "state": public(current_state(version_dir))}


@router.get("/api/versions/{vid}/pose-preview/picture")
def get_pose_preview_picture(vid: str, db: Session = Depends(get_session)) -> FileResponse:
    v, a, version_dir = _rig_version(db, vid)
    p = _dir(version_dir) / PICTURE
    if not p.is_file():
        raise HTTPException(404, "no preview yet")
    return FileResponse(p, media_type="image/png")
