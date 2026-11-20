"""Declared parts on a rig version: the endpoints (docs/API.md "Declared parts"). The rules: declared_parts.py.

  GET /api/versions/{vid}/parts   the asset's declared parts, each with this version's location (or none), and the
                                  placed list as stored
  PUT /api/versions/{vid}/parts   {parts: [{name, kind, location?}]}: replaces this version's placed parts. A location
                                  is three numbers in metres in the rig GLB's rest pose (y up). Each name must be a
                                  declared part of the asset with the same kind.

Only rig versions take placed parts: the rig step reads parts.json when it runs the spring bones
(stages/rig.py _springs). Writing it does not re-run a finished rig; `applies_to_rig` says whether the step reads it.
"""
from __future__ import annotations

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
