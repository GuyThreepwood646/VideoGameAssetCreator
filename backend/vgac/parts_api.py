"""Declared parts on a rig version: the endpoints (docs/API.md "Declared parts"). The rules: declared_parts.py.

  GET /api/versions/{vid}/parts   the asset's declared parts, each with this version's location (or none), and the
                                  placed list as stored
  PUT /api/versions/{vid}/parts   {parts: [{name, kind, location?, locations?, side?, open_at_rest?, angle?,
                                  distance?}]}: replaces this version's placed parts. A location (sway) or locations
                                  (stiff, hinge, spin, slide: up to 8 points) is three numbers in metres in the rig
                                  GLB's rest pose (y up). A moving part (hinge, spin, slide, WP-29) also keeps its
                                  hinge side, whether the model shows it open, its opening angle (hinge) and sliding
                                  distance (slide). Each name must be a declared part of the asset with the same kind.

Only rig versions take placed parts: the rig step reads parts.json when it runs the spring bones
(stages/rig.py _springs). Writing it does not re-run a finished rig; `applies_to_rig` says whether the step reads it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import declared_parts, library, pipeline
from .config import get_settings
from .db import Asset, Version, get_session, version_to_dict
from .events import bus

router = APIRouter()


class PlacedIn(BaseModel):
    name: str = Field(max_length=200)
    kind: str | None = Field(default=None, max_length=20)
    location: list[float] | None = Field(default=None, max_length=3)
    locations: list[list[float]] | None = Field(default=None, max_length=8)
    side: str | None = Field(default=None, max_length=10)  # hinge side for a moving part (WP-29)
    open_at_rest: bool | None = None
    angle: float | None = None
    distance: float | None = None


class PartsIn(BaseModel):
    parts: list[PlacedIn] = Field(default_factory=list, max_length=32)


def _version(db: Session, vid: str) -> tuple[Version, Asset]:
    v = pipeline.get_version(db, vid)
    a = db.get(Asset, v.asset_id)
    if a is None:
        raise HTTPException(404, "the character of this version is gone")
    return v, a


def _answer(v: Version, a: Asset) -> dict[str, Any]:
    path = library.version_file(v, "parts", get_settings())
    rows = declared_parts.read_placed(path)
    return {"version_id": v.id, "category": a.category, **declared_parts.view(a.parts, rows, v.stage == "rig")}


@router.get("/api/versions/{vid}/parts")
def get_parts(vid: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    v, a = _version(db, vid)
    return _answer(v, a)


@router.put("/api/versions/{vid}/parts")
def put_parts(vid: str, body: PartsIn, db: Session = Depends(get_session)) -> dict[str, Any]:
    v, a = _version(db, vid)
    if v.stage != "rig":
        raise HTTPException(400, "parts are placed on a rig version: the rig step reads them for the spring bones")
    if v.status != "done":
        raise HTTPException(409, f"version is {v.status}, not done")
    try:
        rows = declared_parts.placed([p.model_dump() for p in body.parts], a.parts, a.category)
    except declared_parts.PartError as e:
        raise HTTPException(400, str(e))
    path = declared_parts.write_placed(library.version_dir(a, v, get_settings()) / declared_parts.FILE, rows)
    library.register_file(v, "parts", path, get_settings())
    db.commit()
    db.refresh(v)
    bus.publish_threadsafe({"type": "version", "version": version_to_dict(v)})  # plain def endpoints run in a worker thread
    return _answer(v, a)


# --- Suggested worn items (item_suggest.py, WP-69 follow-up) --------------------------------------------------------
# A suggestion is offered only for a humanoid with a kept back-worn item on a finished rig, and only when the finder's
# models are installed (otherwise the answer says installed false and the page says nothing). Dismiss and keep stop
# further suggestions for that rig version. Keeping saves the stiff part through the parts endpoint above.


def _suggest_answer(v: Version, a: Asset) -> dict[str, Any]:
    from . import item_suggest

    settings = get_settings()
    version_dir = library.version_dir(a, v, settings, create=False)
    return {
        "version_id": v.id,
        "installed": item_suggest.installed(settings),
        "eligible": item_suggest.eligible(a, v),
        "state": item_suggest.public(item_suggest.current_state(version_dir)),
    }


@router.get("/api/versions/{vid}/suggestion")
def get_suggestion(vid: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    v, a = _version(db, vid)
    return _suggest_answer(v, a)


@router.post("/api/versions/{vid}/suggestion")
def start_suggestion(vid: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    """The "Suggest" button: queues the suggestion job (one at a time on the host). Nothing is queued when the finder
    is not installed or the item is not eligible; a live or answered suggestion is returned as it stands."""
    from . import item_suggest

    v, a = _version(db, vid)
    settings = get_settings()
    version_dir = library.version_dir(a, v, settings)
    item_suggest.request(settings, a, v, version_dir)
    return _suggest_answer(v, a)


@router.post("/api/versions/{vid}/suggestion/{state}")
def mark_suggestion(vid: str, state: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    from . import item_suggest

    if state not in ("dismissed", "kept"):
        raise HTTPException(400, "a suggestion is dismissed or kept")
    v, a = _version(db, vid)
    item_suggest.set_state(library.version_dir(a, v, get_settings(), create=False), state)
    return _suggest_answer(v, a)
