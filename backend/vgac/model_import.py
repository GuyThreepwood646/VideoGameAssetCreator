"""Your own 3D model on step 3 ("Use my own 3D model"; docs/PROMPT-AND-IMPORT-SPEC.md, item 3 phase A).

    POST /api/assets/{aid}/stages/mesh/upload?name=<file name>&turn=0     raw file as the body (GLB, glTF, FBX, OBJ, or
                                                                       a zip holding one of them with its pictures)
    POST /api/versions/{vid}/turn-model  {"turn": 90 | 180 | 270}       the same file again, turned on the spot

Both make a step 3 version (params.source = "upload") and queue a `mesh.upload` job on the host (Blender on this
computer's processor; neither graphics card is used). The job (stages/mesh.upload -> run_job):

1. blender/modelimport.py makes the file one mesh with one set of maps: model.glb, base_color/metallic/roughness/
   normal.png (only those the model has), import_report.json.
2. blender/render_views.py renders it from eight sides at 1024 px on a see-through background.
3. Steps 1 and 2 get a version each made from those renders (params.source = "from_model"): step 1 the front, step 2
   front, left, back, right and both three-quarter views, each as the picture, its cut-out and on white, exactly the
   files a drawn turnaround has. The model is the truth now, so the earlier pictures would describe something else;
   they stay in the history.
4. The step 3 version is put on top of them (its parent is the new step 2 version) and the usual 3D audit and checks
   run, so the silhouette match, the facing and the checks of step 4 work as for a built model.
5. All three are kept: they are one result.

The upload is streamed to a part file in the version folder (never held in memory), its first bytes are checked
against the kind its name gives, and a zip is unpacked with every name checked (no folders that climb out, no links,
size and count caps). The original file stays in the version folder under source/, so "Turn" and a later rebuild can
read it again.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from PIL import Image
from pydantic import BaseModel

from . import library, pipeline
from .config import get_settings
from .db import Asset, Version, job_to_dict, session_factory, version_to_dict

router = APIRouter()

MAX_FILE_BYTES = 400 * 1024 ** 2
MAX_UNPACKED_BYTES = 1024 ** 3
MAX_ZIP_FILES = 400
MAX_TRIANGLES = 2_000_000
WRITE_CHUNK = 1024 * 1024
MODEL_KINDS = (".glb", ".gltf", ".fbx", ".obj")
KINDS = MODEL_KINDS + (".zip",)
TURNS = (0, 90, 180, 270)
SOURCE_DIR = "source"
RENDER_SIZE = 1024
GREY = (200, 200, 200)
# render_views.py camera yaw (degrees, counter-clockwise seen from above, 0 = Blender front) for each named view.
# Checked against FACING_TEXT in the frontend: left = its left side toward you, quarter = it faces you and to your right.
VIEW_RENDER_YAW = {"front": 0, "left": 90, "back": 180, "right": 270, "quarter": 315, "quarter2": 45}
TURN_VIEWS = ("front", "left", "back", "right", "quarter", "quarter2")
SOURCE = "from_model"

REFUSALS = {
    "unreadable": "Blender could not read this file. Export it again as GLB (or FBX) and try that.",
    "no_mesh": "There is no 3D model in this file (no mesh was found).",
    "too_many_triangles": "This model has more than 2 million triangles. Make a lighter copy first (decimate it), then add that.",
    "too_few_triangles": "This model has almost no triangles, so there is nothing to build on.",
    "failed": "The model could not be read.",
}
WARNINGS = {
    "skeleton_set_aside": "It had a skeleton; the model is used in its rest pose and step 5 rigs it again.",
    "no_colour": "It has no colours or pictures, so it stays grey.",
    "no_texture_layout": "It has no texture layout.",
    "very_large": "It is very large (over 50 m); step 4 scales it to the size you choose.",
    "very_small": "It is very small (under 5 cm); step 4 scales it to the size you choose.",
}


class ModelError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


# --- names and file kinds --------------------------------------------------------------------------------------------


def safe_name(raw: str | None) -> str:
    name = str(raw or "").strip()
    if not name:
        raise ModelError("The file has no name.")
    if any(c in name for c in "/\\:") or ".." in name or "\x00" in name:
        raise ModelError("The file name must be a plain file name, without folders.")
    stem, ext = os.path.splitext(name)
    if ext.lower() not in KINDS:
        raise ModelError("Use a GLB, glTF, FBX or OBJ file, or a zip with the model and its pictures.")
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", stem).strip(" .")
    stem = re.sub(r"_+", "_", stem)[:80] or "model"
    if stem.split(".")[0].lower() in {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}:
        stem = "model_" + stem
    return stem + ext.lower()


def sniff(first: bytes, ext: str) -> None:
    """The first bytes must match the kind the name gives, else ModelError."""
    head = first.lstrip(b"\xef\xbb\xbf").lstrip()
    ok = {
        ".glb": first[:4] == b"glTF",
        ".gltf": head.startswith(b"{"),
        ".fbx": first.startswith(b"Kaydara FBX Binary") or head.startswith(b";") or b"FBXHeaderExtension" in first,
        ".obj": _looks_like_obj(head),
        ".zip": first[:4] == b"PK\x03\x04",
    }.get(ext, False)
    if not ok:
        raise ModelError(f"This file is not a readable {ext.lstrip('.').upper()} file.")


def _looks_like_obj(head: bytes) -> bool:
    try:
        text = head.decode("utf-8", "strict")
    except UnicodeDecodeError:
        return False
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        return line.split(" ", 1)[0] in {"v", "vt", "vn", "f", "o", "g", "mtllib", "usemtl", "s"}
    return text.strip().startswith("#")


def gltf_points_outside(path: Path) -> bool:
    """A .gltf that names other files (a .bin, pictures) can only be used from a zip that brings them."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    uris = [b.get("uri") for b in doc.get("buffers", [])] + [i.get("uri") for i in doc.get("images", [])]
    return any(u is not None and not str(u).startswith("data:") for u in uris)


def unpack_zip(zpath: Path, dest: Path) -> None:
    """Unpack a zip into dest with every member checked: plain relative names only, no links, caps on count and size."""
    dest = dest.resolve()
    try:
        zf = zipfile.ZipFile(zpath)
    except zipfile.BadZipFile as e:
        raise ModelError("This zip file cannot be opened.") from e
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_ZIP_FILES:
            raise ModelError(f"This zip holds more than {MAX_ZIP_FILES} files. Put only the model and its pictures in it.")
        if sum(i.file_size for i in infos) > MAX_UNPACKED_BYTES:
            raise ModelError("This zip unpacks to more than 1 GB.")
        for i in infos:
            name = i.filename.replace("\\", "/")
            if name.startswith("/") or ":" in name or any(part in ("", "..") for part in name.split("/")):
                raise ModelError("This zip has a file with a name that points outside it, so it was not opened.")
            if (i.external_attr >> 16) & 0o170000 == 0o120000:
                raise ModelError("This zip holds a link, so it was not opened.")
            target = (dest / name).resolve()
            target.relative_to(dest)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(i) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, WRITE_CHUNK)


def find_model(folder: Path) -> Path:
    """The one model file in an unpacked zip (GLB first, then glTF, FBX, OBJ). ModelError when there is none or more
    than one of the first kind found."""
    files = [p for p in folder.rglob("*") if p.is_file() and not p.name.startswith((".", "__MACOSX"))
             and "__MACOSX" not in p.parts]
    for ext in MODEL_KINDS:
        found = [p for p in files if p.suffix.lower() == ext]
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            names = ", ".join(sorted(p.name for p in found)[:4])
            raise ModelError(f"This zip holds more than one {ext.lstrip('.').upper()} model ({names}). Put one model in it.")
    raise ModelError("There is no GLB, glTF, FBX or OBJ model in this zip.")


def source_file(version_folder: Path) -> Path:
    """The model file kept under source/ in a step 3 version folder."""
    src = version_folder / SOURCE_DIR
    if not src.is_dir():
        raise FileNotFoundError("the model file for this version is missing")
    top = [p for p in src.iterdir() if p.is_file() and p.suffix.lower() in MODEL_KINDS]
    if len(top) == 1:
        return top[0]
    return find_model(src)


# --- the job ---------------------------------------------------------------------------------------------------------


def plain_refusal(report: dict[str, Any]) -> str:
    return REFUSALS.get(str(report.get("error")), REFUSALS["failed"])


def warning_lines(report: dict[str, Any]) -> list[str]:
    return [WARNINGS[w] for w in report.get("warnings") or [] if w in WARNINGS]


def picture_files(rgba_path: Path, out_dir: Path, stem: str) -> dict[str, Path]:
    """From one see-through render: the picture on light grey, the cut-out and the cut-out on white."""
    im = Image.open(rgba_path).convert("RGBA")
    cut = out_dir / f"{stem}_cutout.png"
    im.save(cut)
    grey = Image.new("RGBA", im.size, GREY + (255,))
    grey.alpha_composite(im)
    raw = out_dir / f"{stem}.png"
    grey.convert("RGB").save(raw)
    white = Image.new("RGBA", im.size, (255, 255, 255, 255))
    white.alpha_composite(im)
    wp = out_dir / f"{stem}_white.png"
    white.convert("RGB").save(wp)
    return {"raw": raw, "cutout": cut, "white": wp}


def coverage(rgba_path: Path) -> float:
    a = Image.open(rgba_path).getchannel("A")
    hist = a.histogram()
    return round(sum(hist[128:]) / float(a.size[0] * a.size[1]), 4)


async def run_job(sc: Any) -> None:
    """The mesh.upload job for StageContext `sc` (see the module docstring)."""
    from .stages import mesh as mesh_stage

    src = source_file(sc.dir)
    turn = int(sc.version.params.get("turn") or 0) % 360
    tex = int((sc.budget or {}).get("texture") or 2048)
    work = sc.dir / "import"
    work.mkdir(exist_ok=True)
    job = {"src": str(src), "out_dir": str(work), "tex": tex, "max_triangles": MAX_TRIANGLES, "turn": turn}
    jp = work / "job.json"
    jp.write_text(json.dumps(job), encoding="utf-8")
    sc.use_model("blender")
    sc.progress(0.05, "Reading your model in Blender")
    await sc.blender("modelimport.py", [jp], log_name="modelimport.log", timeout=sc.settings.blender_timeout_seconds)
    report_path = work / "import_report.json"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise RuntimeError(REFUSALS["failed"]) from e
    if report.get("error"):
        sc.set_metrics(import_error=report.get("error"), import_error_detail=str(report.get("detail") or "")[:300])
        raise RuntimeError(plain_refusal(report))
    glb = work / "model.glb"
    if not glb.is_file():
        raise RuntimeError(REFUSALS["failed"])
    shutil.move(str(glb), sc.dir / "model.glb")
    sc.register("glb", sc.dir / "model.glb")
    maps = {}
    for key in ("base_color", "metallic", "roughness", "normal"):
        p = work / f"{key}.png"
        if p.is_file():
            dest = sc.dir / f"{key}.png"
            shutil.move(str(p), dest)
            sc.register(key, dest)
            maps[key] = dest
    sc.register("import_report", report_path)
    sc.set_params(source="upload", engine="upload", hair_source="model", turn=turn, texture_size=report.get("texture_size"))
    sc.set_metrics(engine="upload", imported={k: report.get(k) for k in ("triangles", "vertices", "materials", "texture_mode",
                                                                      "texture_size", "uv_made", "bones", "armature", "dims",
                                                                      "warnings", "seconds", "turn")},
                   import_notes=warning_lines(report), triangles=report.get("triangles"), texture_size=report.get("texture_size"),
                   normal_map="normal" in maps, textured=bool(maps),
                   # The pictures of steps 1 and 2 are renders of this model: comparing the texture with them says nothing.
                   texture_fidelity={"skipped": "your model"}, texture_match_measured=False)

    sc.progress(0.45, "Taking pictures of your model for steps 1 and 2")
    renders = sc.dir / "renders"
    await sc.blender("render_views.py", [sc.dir / "model.glb", renders, RENDER_SIZE, 45, "view"], log_name="render_views_model.log",
                     timeout=sc.settings.blender_timeout_seconds)
    tv = make_picture_versions(sc, renders)
    sc.version.parent_version_id = tv.id
    sc.save()
    sc.progress(0.75, "Checking the model")
    await mesh_stage.audit(sc)
    pipeline.select(sc.db, sc.version)


def make_picture_versions(sc: Any, renders: Path) -> Version:
    """New kept step 1 and step 2 versions from the renders. -> the step 2 version."""
    db, asset, settings = sc.db, sc.asset, sc.settings
    front = renders / f"view_{VIEW_RENDER_YAW['front']}.png"
    if not front.is_file():
        raise RuntimeError("The pictures of your model could not be made.")
    common = {"source": SOURCE, "from_model_version": sc.version.id}
    cv = pipeline.new_version(db, asset, "concept", None, {**common, "model": None, "seed": None})
    cdir = library.version_dir(asset, cv, settings)
    pf = picture_files(front, cdir, "image")
    library.register_file(cv, "image", pf["raw"], settings)
    library.register_file(cv, "cutout", pf["cutout"], settings)
    library.register_file(cv, "cutout_white", pf["white"], settings)
    cv.metrics = {"source": SOURCE, "cutout": {"coverage": coverage(front), "empty": False}, "checks": [], "gate_ok": True,
                  "checks_passed": 0, "checks_failed": 0}
    cv.status = "done"
    db.commit()
    pipeline.select(db, cv)

    tv = pipeline.new_version(db, asset, "turnaround", cv, {**common, "views": list(TURN_VIEWS)})
    tdir = library.version_dir(asset, tv, settings)
    views: dict[str, Any] = {}
    for view in TURN_VIEWS:
        r = renders / f"view_{VIEW_RENDER_YAW[view]}.png"
        if not r.is_file():
            continue
        pf = picture_files(r, tdir, view)
        library.register_file(tv, view, pf["raw"], settings)
        library.register_file(tv, f"{view}_cutout", pf["cutout"], settings)
        library.register_file(tv, f"{view}_white", pf["white"], settings)
        views[view] = {"passed": True, "method": SOURCE, "seed": None, "checks": []}
    tv.metrics = {"views": views, "views_total": len(views), "views_passed": len(views), "checks": [], "gate_ok": True,
                  "checks_passed": 0, "checks_failed": 0, "source": SOURCE}
    tv.status = "done"
    db.commit()
    pipeline.select(db, tv)
    _publish(cv, tv)
    return tv


def _publish(*versions: Version) -> None:
    from .events import bus

    for v in versions:
        bus.publish_threadsafe({"type": "version", "version": version_to_dict(v)})


# --- the API ---------------------------------------------------------------------------------------------------------


def _asset_or_404(db: Any, aid: str) -> Asset:
    a = db.get(Asset, aid)
    if a is None:
        raise HTTPException(404, "asset not found")
    if a.category == "material":
        raise HTTPException(400, "A tileable material has no 3D model, so a model cannot be added to it.")
    return a


def _submit_host(db: Any, a: Asset, v: Version) -> Any:
    """The import runs on this computer (Blender), never on a graphics card."""
    from .jobs import HOST, job_kind

    q = pipeline.queue()
    return q.submit(HOST, job_kind("mesh", "upload", a.category), a.id, v.id, {"version_id": v.id, "op": "upload", "params": {}})


def _check_turn(turn: int) -> int:
    if turn not in TURNS:
        raise HTTPException(400, "turn must be 0, 90, 180 or 270")
    return turn


async def save_upload(request: Request, aid: str, raw_name: str, turn: int) -> dict[str, Any]:
    settings = get_settings()
    name = safe_name(raw_name)
    ext = os.path.splitext(name)[1].lower()
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_FILE_BYTES:
        raise ModelError(f"This file is larger than {MAX_FILE_BYTES // 1024 ** 2} MB.", 413)
    with session_factory()() as db:
        a = _asset_or_404(db, aid)
        v = pipeline.new_version(db, a, "mesh", None, {"source": "upload", "engine": "upload", "file_name": name, "turn": turn,
                                                       "hair_source": "model"})
        folder = library.version_dir(a, v, settings) / SOURCE_DIR
        folder.mkdir(parents=True, exist_ok=True)
        vid = v.id
    part = folder / f".upload-{uuid.uuid4().hex}.part"
    total, first, ok = 0, b"", False
    try:
        with open(part, "xb") as f:
            buf = bytearray()
            async for chunk in request.stream():
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_FILE_BYTES:
                    raise ModelError(f"This file is larger than {MAX_FILE_BYTES // 1024 ** 2} MB.", 413)
                if len(first) < 512:
                    first += chunk[: 512 - len(first)]
                buf.extend(chunk)
                if len(buf) >= WRITE_CHUNK:
                    data, buf = bytes(buf), bytearray()
                    await asyncio.to_thread(f.write, data)
            if buf:
                await asyncio.to_thread(f.write, bytes(buf))
        if total == 0:
            raise ModelError("The file arrived empty. Try choosing it again.")
        sniff(first, ext)
        dest = folder / name
        os.replace(part, dest)
        if ext == ".zip":
            await asyncio.to_thread(unpack_zip, dest, folder)
            dest.unlink()
            find_model(folder)
        elif ext == ".gltf" and gltf_points_outside(dest):
            raise ModelError("This glTF points at other files (a .bin or pictures). Export it as one GLB, or put them all in a zip.")
        with session_factory()() as db:
            a = db.get(Asset, aid)
            v = db.get(Version, vid)
            job = _submit_host(db, a, v)
            db.refresh(v)
            ok = True
            return {"job": job_to_dict(job), "version": version_to_dict(v)}
    finally:
        if part.exists():
            try:
                part.unlink()
            except OSError:
                pass
        if not ok:
            _drop_version(vid)


def _drop_version(vid: str) -> None:
    """A version whose upload failed goes, with its folder (it never had a job)."""
    with session_factory()() as db:
        v = db.get(Version, vid)
        if v is None:
            return
        a = db.get(Asset, v.asset_id)
        if a is not None:
            shutil.rmtree(library.version_dir(a, v, get_settings(), create=False), ignore_errors=True)
        db.delete(v)
        db.commit()


@router.post("/api/assets/{aid}/stages/mesh/upload")
async def post_model_upload(aid: str, request: Request, name: str = Query(..., max_length=400), turn: int = Query(0)) -> dict[str, Any]:
    _check_turn(turn)
    try:
        return await save_upload(request, aid, name, turn)
    except ModelError as e:
        raise HTTPException(e.status, e.message) from e


class TurnIn(BaseModel):
    turn: int


@router.post("/api/versions/{vid}/turn-model")
def post_turn_model(vid: str, body: TurnIn) -> dict[str, Any]:
    """A new step 3 version from the same model file, turned `turn` degrees more (counter-clockwise seen from above)."""
    turn = _check_turn(body.turn)
    if turn == 0:
        raise HTTPException(400, "turn must be 90, 180 or 270")
    settings = get_settings()
    with session_factory()() as db:
        old = db.get(Version, vid)
        if old is None:
            raise HTTPException(404, "version not found")
        if old.stage != "mesh" or (old.params or {}).get("source") != "upload":
            raise HTTPException(400, "only a model you added can be turned this way")
        a = _asset_or_404(db, old.asset_id)
        src_dir = library.version_dir(a, old, settings, create=False) / SOURCE_DIR
        if not src_dir.is_dir():
            raise HTTPException(409, "the model file for this version is missing")
        params = {k: val for k, val in (old.params or {}).items() if k in ("source", "engine", "file_name", "hair_source")}
        params["turn"] = ((old.params or {}).get("turn", 0) + turn) % 360
        v = pipeline.new_version(db, a, "mesh", None, params)
        shutil.copytree(src_dir, library.version_dir(a, v, settings) / SOURCE_DIR)
        job = _submit_host(db, a, v)
        db.refresh(v)
        return {"job": job_to_dict(job), "version": version_to_dict(v)}
