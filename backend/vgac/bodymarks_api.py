"""Mark the body: the endpoints (docs/API.md "Mark the body"). The marks themselves: bodymarks.py.

  GET  /api/versions/{vid}/marks          the marks of a finished step 2 version, found now when it has none yet (an
                                          older version)
  PUT  /api/versions/{vid}/marks          {moves?: [{view, joint, x, y}], accept?: bool}: the person's moves (kept apart
                                          from the app's) and/or "these look right"
  POST /api/versions/{vid}/marks/reset    {view?: str, joint?: str}: the app's marks back (one mark, one picture, or
                                          all); one mark the app did not see comes off the picture

Each answers the panel's state (MarksOut) and writes marks.json, metrics.body_marks and the body_marks check on the
version itself: marks are a note on the pictures, not a new result, so no new version is made.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import bodymarks, library, pipeline, quality
from .config import get_settings
from .db import Asset, Version, get_session, version_to_dict
from .events import bus

router = APIRouter()


class Move(BaseModel):
    view: str = Field(max_length=16)
    joint: str = Field(max_length=32)
    x: float
    y: float


class MarksIn(BaseModel):
    moves: list[Move] = Field(default_factory=list, max_length=64)
    accept: bool = False


class AcceptedIn(BaseModel):
    accepted: bool


class ResetIn(BaseModel):
    view: str | None = Field(default=None, max_length=16)
    joint: str | None = Field(default=None, max_length=32)


def _turnaround(db: Session, vid: str) -> Version:
    v = pipeline.get_version(db, vid)
    if v.stage != "turnaround":
        raise HTTPException(400, "marks are on the side views step's versions")
    if v.status != "done":
        raise HTTPException(409, f"version is {v.status}, not done")
    a = db.get(Asset, v.asset_id)
    if a is None or a.category not in bodymarks.CATEGORIES:
        raise HTTPException(400, "marks are made for characters with a human body only")
    return v


def _cutouts(v: Version) -> dict[str, Any]:
    s = get_settings()
    return {view: p for view in bodymarks.MARK_VIEWS if (p := library.version_file(v, f"{view}_cutout", s)) is not None}


def publish(db: Session, v: Version, doc: dict[str, Any]) -> None:
    """marks.json, metrics.body_marks and the body_marks check on the version, then the version to the page."""
    s = get_settings()
    a = db.get(Asset, v.asset_id)
    path = bodymarks.write(doc, library.version_dir(a, v, s) / "marks.json")
    library.register_file(v, "marks", path, s)
    checks = [c for c in (v.metrics.get("checks") or []) if c.get("name") != "body_marks"]
    checks.append(bodymarks.check(doc, bodymarks.stop_on(v.params)))
    library.update_metrics(v, body_marks=bodymarks.summary(doc), **quality.summarise(checks))
    db.commit()
    db.refresh(v)
    bus.publish_threadsafe({"type": "version", "version": version_to_dict(v)})  # plain def endpoints run in a worker thread


def load(db: Session, v: Version) -> dict[str, Any]:
    """The version's marks, found and saved now when it has none (a version from before marks, or a failed find), and
    completed when it was made before a picture of it had marks (the three-quarter and back pictures)."""
    doc = bodymarks.read(library.version_file(v, "marks", get_settings()))
    files = _cutouts(v)
    try:
        if doc is None:
            if "front" not in files:
                raise HTTPException(409, "this version has no front view to mark")
            doc = bodymarks.compute(files)
            publish(db, v, doc)
        elif bodymarks.complete(doc, files):
            publish(db, v, doc)
    except HTTPException:
        raise
    except library.LibraryPathError as e:
        raise HTTPException(500, str(e))  # already one plain sentence with the fix first: no prefix stacked on it
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"The marks could not be found: {e}")
    return doc


def out(v: Version, doc: dict[str, Any]) -> dict[str, Any]:
    """What the panel draws: the pictures (library paths and sizes), the marks to use (yours over the app's), the app's
    own, what to check, the moved marks that may be out of date, the tests, which hands were found, and plain names."""
    eff = bodymarks.effective(doc)
    return {
        "version_id": v.id,
        "state": bodymarks.state(doc),
        "pictures": {view: {"path": v.files.get(f"{view}_cutout"), "size": d["size"], "found": d.get("found", False)}
                     for view, d in sorted((doc.get("views") or {}).items(), key=lambda kv: bodymarks.MARK_VIEWS.index(kv[0])
                                           if kv[0] in bodymarks.MARK_VIEWS else 99)},
        "placeholders": bodymarks.placeholders(doc),
        "marks": eff,
        "automatic": {view: {j: {"x": m.get("x"), "y": m.get("y")} for j, m in d.get("marks", {}).items() if j not in bodymarks.EXTRA}
                      for view, d in (doc.get("views") or {}).items()},
        "to_check": bodymarks.to_check(doc),
        "stale": bodymarks.stale(doc),
        "tests": (doc.get("rule") or {}).get("tests") or {},
        "hands": {view: d.get("hands") or {} for view, d in (doc.get("views") or {}).items()},
        "names": {j: bodymarks.plain_joint(j) for j in bodymarks.JOINTS},
        "required": list(bodymarks.REQUIRED),
        "accepted": bool(doc.get("accepted")),
        "stop": bodymarks.stop_on(v.params),
    }


@router.get("/api/versions/{vid}/marks")
def get_marks(vid: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    v = _turnaround(db, vid)
    return out(v, load(db, v))


@router.put("/api/versions/{vid}/marks")
def put_marks(vid: str, body: MarksIn, db: Session = Depends(get_session)) -> dict[str, Any]:
    v = _turnaround(db, vid)
    doc = load(db, v)
    if not body.moves and not body.accept:
        raise HTTPException(400, "nothing to change: move a mark or say they look right")
    try:
        bodymarks.apply_moves(doc, [m.model_dump() for m in body.moves])
    except bodymarks.MarkError as e:
        raise HTTPException(400, str(e))
    if body.accept:
        bodymarks.accept(doc)
    publish(db, v, doc)
    return out(v, doc)


@router.put("/api/versions/{vid}/marks/accepted")
def put_marks_accepted(vid: str, body: AcceptedIn, db: Session = Depends(get_session)) -> dict[str, Any]:
    """Set or clear "These look right" as it was: used by undo and redo of that step (and nothing else)."""
    v = _turnaround(db, vid)
    doc = load(db, v)
    bodymarks.set_accepted(doc, body.accepted)
    publish(db, v, doc)
    return out(v, doc)


@router.post("/api/versions/{vid}/marks/reset")
def reset_marks(vid: str, body: ResetIn | None = None, db: Session = Depends(get_session)) -> dict[str, Any]:
    v = _turnaround(db, vid)
    doc = load(db, v)
    view = body.view if body else None
    joint = body.joint if body else None
    if view is not None and view not in (doc.get("views") or {}):
        raise HTTPException(400, f"there is no {view!r} picture with marks on this version")
    if joint is not None and (view is None or joint not in bodymarks.JOINTS):
        raise HTTPException(400, "one mark is put back on one picture: give its view and a known mark")
    bodymarks.reset(doc, view, joint)
    publish(db, v, doc)
    return out(v, doc)
