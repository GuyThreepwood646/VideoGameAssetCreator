"""Start from your own images: the endpoints and helpers (docs/API.md "Your own pictures").

Every picture goes through uploads.py (base64 in JSON, 25 MB, PNG / JPEG / WebP sniffed, pixel cap, EXIF orientation
applied then all metadata dropped, at most 2048 px, written as PNG under a fixed name in the version folder).

  POST /api/assets/{aid}/stages/concept/upload       {image, note?} -> {job, version}: a concept version marked
                                                     params.source = "upload", cut out and checked by a job
  POST /api/versions/{vid}/views/{view}/upload       {image} -> {job, version}: a new turnaround version with your
                                                     own picture for one view, every other view copied forward
  POST /api/palette-from-image                       {image, colors: 8|16|32, ignore_background?} -> {palette, swatches}
  POST /api/projects/{pid}/style-from-image          {image} -> {palette, swatches, job | null, needs_review_engine,
                                                     message}; the description arrives as GET /api/jobs/{id} result

main.py calls start_concept_from_picture (POST /api/projects/{pid}/assets with `image`), provided_views_prepare
(run params provided_views on the turnaround) and write_reference (refine reference_image).
"""

from __future__ import annotations

import uuid
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import library, pipeline, readiness, reference, uploads
from .config import get_settings
from .db import Asset, Project, Version, get_session, job_to_dict, version_to_dict
from .jobs import JUDGE, active_queue

router = APIRouter()

IMAGE_FIELD_MAX = uploads.MAX_B64_CHARS


def image_field(**kw: Any) -> Any:
    return Field(max_length=IMAGE_FIELD_MAX, **kw)


def decode_or_400(data: str, field: str = "picture") -> Image.Image:
    try:
        return uploads.decode_image(data, field)
    except uploads.UploadError as e:
        raise HTTPException(400, str(e))


def _register(db: Session, v: Version, key: str, path) -> None:
    library.register_file(v, key, path, get_settings())
    db.add(v)
    db.commit()


# --- start an asset from a picture ---------------------------------------------------------------------------------


def start_concept_from_picture(db: Session, a: Asset, img: Image.Image, note: str | None = None) -> dict[str, Any]:
    """A concept version from the person's picture: files.image (on plain light grey when it has see-through parts;
    the see-through original is kept as files.original), params.source = "upload", then a job that cuts it out like
    a painted picture and checks it is ready for 3D. The note fills an empty asset description."""
    note = (note or "").strip()[:1000] or None
    if note and not (a.prompt or "").strip():
        a.prompt = note
        db.commit()
    v = pipeline.new_version(db, a, "concept", None, {"source": "upload", "note": note, "upload_size": list(img.size),
                                                       "readiness_block": None, "model": None, "width": None, "height": None})
    folder = library.version_dir(a, v, get_settings())
    if img.mode == "RGBA":
        _register(db, v, "original", uploads.save_png(img, folder, "original.png"))
    _register(db, v, "image", uploads.save_png(uploads.flatten(img), folder, "image.png"))
    job = pipeline.submit(db, a, v, "upload", {"params": {}})
    db.refresh(v)
    return {"job": job_to_dict(job), "version": version_to_dict(v)}


class ConceptUploadIn(BaseModel):
    image: str = image_field(min_length=1)
    note: str | None = Field(default=None, max_length=1000)


@router.post("/api/assets/{aid}/stages/concept/upload")
def upload_concept(aid: str, body: ConceptUploadIn, db: Session = Depends(get_session)) -> dict[str, Any]:
    a = db.get(Asset, aid)
    if a is None:
        raise HTTPException(404, "asset not found")
    img = decode_or_400(body.image)
    return start_concept_from_picture(db, a, img, body.note)


# --- your own side and back views ----------------------------------------------------------------------------------


def _check_view(view: str) -> None:
    if view not in reference.TURNAROUND_VIEWS:
        raise HTTPException(400, f"unknown view {view!r}; views: {list(reference.TURNAROUND_VIEWS)}")


def _write_view(db: Session, a: Asset, v: Version, view: str, img: Image.Image) -> None:
    folder = library.version_dir(a, v, get_settings())
    _register(db, v, f"supplied_{view}", uploads.save_png(uploads.flatten(img), folder, f"supplied_{view}.png"))


def provided_views_prepare(db: Session, a: Asset, params: dict[str, Any]) -> Callable[[Version], None] | None:
    """Run params provided_views {view: image}: every picture is checked first (400 naming the view), taken out of the
    params (only the list supplied_views stays), and written into the new version's folder before its job is queued."""
    provided = params.pop("provided_views", None)
    if not provided:
        return None
    if not isinstance(provided, dict):
        raise HTTPException(400, "provided_views must map each view to a picture")
    images: dict[str, Image.Image] = {}
    for view, data in provided.items():
        _check_view(view)
        if not isinstance(data, str):
            raise HTTPException(400, f"The {view} view picture could not be read. Try adding it again.")
        images[view] = decode_or_400(data, f"{view} view picture")
    params["supplied_views"] = sorted(images)
    views = list(params.get("views") or reference.category(a.category).get("views") or [])
    if views:
        params["views"] = views + [v for v in sorted(images) if v not in views]

    def prepare(v: Version) -> None:
        for view, img in images.items():
            _write_view(db, a, v, view, img)

    return prepare


class ViewUploadIn(BaseModel):
    image: str = image_field(min_length=1)


@router.post("/api/versions/{vid}/views/{view}/upload")
def upload_view(vid: str, view: str, body: ViewUploadIn, db: Session = Depends(get_session)) -> dict[str, Any]:
    parent = pipeline.get_version(db, vid)
    if parent.stage != "turnaround":
        raise HTTPException(400, "your own views go on a turnaround version")
    if parent.status != "done":
        raise HTTPException(409, f"version is {parent.status}, not done")
    _check_view(view)
    img = decode_or_400(body.image, f"{view} view picture")
    a = db.get(Asset, parent.asset_id)
    v = pipeline.new_version(db, a, "turnaround", parent, dict(parent.params))
    _write_view(db, a, v, view, img)
    job = pipeline.submit(db, a, v, "upload", {"params": {}, "view": view})
    db.refresh(v)
    return {"job": job_to_dict(job), "version": version_to_dict(v)}


# --- refine with a reference picture (experimental) -------------------------------------------------------------------


def check_reference(parent: Version, a: Asset, data: str | None) -> Image.Image | None:
    if not data:
        return None
    if parent.stage != "concept" or a.category == "material":
        raise HTTPException(400, "A reference picture works only when changing the first picture of a character, creature or prop.")
    return decode_or_400(data, "reference picture")


def write_reference(db: Session, a: Asset, v: Version, img: Image.Image) -> None:
    folder = library.version_dir(a, v, get_settings())
    _register(db, v, "reference", uploads.save_png(uploads.flatten(img), folder, "reference.png"))
    p = v.params
    p["reference_experimental"] = True
    v.params = p
    db.commit()


# --- colours and style from a picture -----------------------------------------------------------------------------------


class PaletteIn(BaseModel):
    image: str = image_field(min_length=1)
    colors: int = 8
    ignore_background: bool = True


def _palette(img: Image.Image, colors: int, ignore_background: bool) -> dict[str, Any]:
    if colors not in readiness.PALETTE_SIZES:
        raise HTTPException(400, f"colors must be one of {list(readiness.PALETTE_SIZES)}")
    try:
        return readiness.palette(img, colors, ignore_background)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/palette-from-image")
def palette_from_image(body: PaletteIn) -> dict[str, Any]:
    """The main colours of a picture, most used first. Nothing is stored."""
    return _palette(decode_or_400(body.image), body.colors, body.ignore_background)


class StyleIn(BaseModel):
    image: str = image_field(min_length=1)
    colors: int = 8


NEEDS_REVIEW_ENGINE = ("Describing the style needs the review engine (the local picture reader on a graphics card), "
                       "and none is on right now. The colours are ready; turn a card on and try again for the words.")


@router.post("/api/projects/{pid}/style-from-image")
def style_from_image(pid: str, body: StyleIn, db: Session = Depends(get_session)) -> dict[str, Any]:
    """The palette now, plus a review-engine job that writes a short style description. Nothing is applied: read the
    job's result, edit it, and save it through PUT /api/projects/{pid} style_lock (prompt_block, palette)."""
    if db.get(Project, pid) is None:
        raise HTTPException(404, "project not found")
    img = decode_or_400(body.image)
    pal = _palette(img, body.colors, True)
    out: dict[str, Any] = {**pal, "job": None, "needs_review_engine": False}
    q = active_queue()
    instance, waiting = q.place(JUDGE, "") if q is not None else (None, NEEDS_REVIEW_ENGINE)
    if q is None or waiting:
        out.update(needs_review_engine=True, message=NEEDS_REVIEW_ENGINE)
        return out
    folder = library.project_dir(pid, get_settings()) / "style_from_image" / uuid.uuid4().hex
    folder.mkdir(parents=True, exist_ok=True)
    path = uploads.save_png(uploads.flatten(img), folder, "reference.png")
    payload = {"task": "style_from_image", "project_id": pid, "image": library.rel_path(path, get_settings()),
               "palette": pal["palette"], "swatches": pal["swatches"]}
    job = q.submit(instance, "concept.judge", None, None, payload)
    out.update(job=job_to_dict(job), message="The review engine is describing the style. It takes about a minute.")
    return out
