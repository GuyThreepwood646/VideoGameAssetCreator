"""Your own hairstyles: people add a hair mesh from another source to the hair library.

Same shape as the own animation files (moves.py) and the own add-ons (user_addons.py): a streamed upload with a size cap
and a header check, a registry in the library folder, ids that never shadow built-ins, and a "Where it came from" line
with a licence that travels into the export credits.

Files:  <library_path>/my_hair/my_hair.json            the registry ({"styles": {id: catalogue-style entry}})
        <library_path>/my_hair/<id>/<id>.npz, _lite.npz, grey.png, thumbs/front.png, thumbs/quarter.png

An entry has the same fields as a built-in catalogue entry (vgac/hairstyles.py), plus own=True and added_at, so the
picker, the ranking, the fit and the credits read it without knowing the difference. Its file paths start with "mine/"
and are resolved by `resolve()` (the hair library's file route and hairstyles.paths use it), never by joining a
user-supplied string to a folder. Blender reads the mesh once (blender/hairimport.py): it must be one hair mesh, and it
is put on the library's reference head (data/hairlib/reference_head.npz, a CC0 Quaternius figure) and baked like the
packs' styles.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .config import Settings, get_settings

router = APIRouter()

MY_DIR = "my_hair"
REGISTRY_FILE = "my_hair.json"
PREFIX = "mine_"
URL_PREFIX = "mine/"
MAX_FILE_BYTES = 80 * 1024 ** 2
KINDS = (".glb", ".gltf", ".fbx")
FAMILIES = ("bob", "short", "long", "ponytail", "bun", "braid", "afro")
FAMILY_LABELS = {"bob": "Bob", "short": "Short", "long": "Long", "ponytail": "Ponytail", "bun": "Bun", "braid": "Braid", "afro": "Afro"}
MAX_NAME = 60
MAX_SOURCE = 300
LICENCES = ("CC0", "CC-BY", "My own work", "Paid or licensed asset", "Not sure")
ID_RE = re.compile(r"^mine_[a-z0-9_]{1,40}$")
WRITE_CHUNK = 1024 * 1024
REFERENCE = Path(__file__).resolve().parent / "data" / "hairlib" / "reference_head.npz"
_lock = threading.Lock()

RESPONSIBILITY = ("Only add hair you made or have the right to use. The file stays on this computer, and the name, "
                  "source and licence you give here are written into the credits of every character that uses it.")

# The plain sentence for each reason the reader refuses a file (blender/hairimport.py `Refuse` codes).
REFUSALS = {
    "no_mesh": "This file has no mesh we can use as hair.",
    "whole_character": "This file looks like a whole character or a rigged model, not just hair. Export only the hair mesh and try again.",
    "too_small": "This mesh has too few triangles to be a hairstyle.",
    "too_big": "This hair has far too many triangles (over 300,000). Reduce it in your 3D program and try again.",
    "flat": "This mesh is flat or empty in one direction, so it cannot be fitted as hair.",
    "unreadable": "The app could not read this file. It may be damaged, or saved in a way Blender cannot open.",
    "failed": "The app could not turn this file into a hairstyle. Try exporting it again as a GLB.",
}


class HairError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


# --- registry ----------------------------------------------------------------------------------------------------------


def root(settings: Settings | None = None) -> Path:
    return Path((settings or get_settings()).library_path) / MY_DIR


def registry_path(settings: Settings | None = None) -> Path:
    return root(settings) / REGISTRY_FILE


_cache: dict[str, tuple[tuple[int, int], dict[str, dict[str, Any]]]] = {}


def _read(settings: Settings | None = None) -> dict[str, dict[str, Any]]:
    p = registry_path(settings)
    try:
        st = p.stat()
    except OSError:
        return {}
    stamp = (st.st_mtime_ns, st.st_size)
    hit = _cache.get(str(p))
    if hit and hit[0] == stamp:
        return dict(hit[1])
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    styles = data.get("styles") if isinstance(data, dict) else None
    out = {k: v for k, v in (styles or {}).items() if isinstance(v, dict) and ID_RE.match(str(k)) and v.get("id") == k}
    _cache[str(p)] = (stamp, out)
    return dict(out)


def _write(styles: dict[str, dict[str, Any]], settings: Settings | None = None) -> None:
    p = registry_path(settings)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps({"version": 1, "styles": styles}, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    _cache.pop(str(p), None)


def entries(settings: Settings | None = None) -> dict[str, dict[str, Any]]:
    """The registered styles as catalogue entries (merged into hairstyles.catalogue())."""
    return _read(settings)


def resolve(rel: str, settings: Settings | None = None) -> Path:
    """A stored "mine/<id>/..." path as a file inside my_hair/<id>/, never anywhere else."""
    if not rel.startswith(URL_PREFIX):
        raise ValueError("not an own-hair path")
    parts = rel[len(URL_PREFIX):].split("/")
    if len(parts) < 2 or not ID_RE.match(parts[0]) or any(p in ("", ".", "..") or "\\" in p or ":" in p for p in parts):
        raise ValueError("path outside your hairstyles")
    base = root(settings).resolve()
    p = (base / Path(*parts)).resolve()
    if base not in p.parents:
        raise ValueError("path outside your hairstyles")
    return p


# --- reading the reference and the library's crown ---------------------------------------------------------------------


def crown(q: np.ndarray) -> tuple[float, float, float]:
    """(width, y centre, top) of the crown: the top 40 percent of the hair's own height, in the skull frame. The same
    measure runs inside blender/hairimport.py on the user's mesh."""
    zmax, zmin = float(q[:, 2].max()), float(q[:, 2].min())
    P = q[q[:, 2] >= zmax - 0.4 * (zmax - zmin)]
    return float(P[:, 0].max() - P[:, 0].min()), float((P[:, 1].max() + P[:, 1].min()) / 2), zmax


@lru_cache(maxsize=2)
def _stats(key: str) -> dict[str, float]:
    from . import hairstyles

    cat = hairstyles._load(str(hairstyles.DATA_DIR))
    ws, ys, zs = [], [], []
    for sid, e in (cat.get("styles") or {}).items():
        if e.get("kind") != "shell":
            continue
        f = hairstyles.DATA_DIR / "styles" / f"{sid}.npz"
        if not f.is_file():
            continue
        q = np.load(f)["q"]
        w, y, z = crown(q)
        ws.append(w); ys.append(y); zs.append(z)
    if not ws:
        return {"w": 2.2, "ycen": 0.0, "ztop": 1.2}
    return {"w": float(np.median(ws)), "ycen": float(np.median(ys)), "ztop": float(np.median(zs))}


def reference_stats() -> dict[str, float]:
    from . import hairstyles

    return _stats(str(hairstyles.DATA_DIR))


# --- upload checks -----------------------------------------------------------------------------------------------------


def safe_upload_name(raw: str | None) -> str:
    name = str(raw or "").strip()
    if not name:
        raise HairError("The file has no name.")
    if any(c in name for c in "/\\:") or ".." in name or "\x00" in name:
        raise HairError("The file name must be a plain file name, without folders.")
    stem, ext = os.path.splitext(name)
    if ext.lower() not in KINDS:
        raise HairError("Use a GLB, glTF or FBX hair mesh.")
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", stem).strip(" .")
    stem = re.sub(r"_+", "_", stem)[:80] or "hair"
    if stem.split(".")[0].lower() in {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}:
        stem = "hair_" + stem
    return stem + ext.lower()


def sniff_kind(first: bytes, ext: str) -> str:
    """The first bytes must match the kind the name says. -> 'glb' | 'gltf' | 'fbx', else HairError."""
    head = first.lstrip(b"\xef\xbb\xbf")
    if ext == ".glb" and first[:4] == b"glTF":
        return "glb"
    if ext == ".gltf" and head.lstrip().startswith(b"{") and b'"asset"' in first + b"":
        return "gltf"
    if ext == ".fbx" and (first.startswith(b"Kaydara FBX Binary") or head.lstrip().startswith(b";") or b"FBXHeaderExtension" in first):
        return "fbx"
    raise HairError("This file is not a readable " + ext.lstrip(".").upper() + " file. Use a GLB, glTF or FBX hair mesh.")


def gltf_is_self_contained(path: Path) -> bool:
    """A .gltf that points at other files (a .bin, pictures) cannot be used from one upload; embedded data is fine."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    uris = [b.get("uri") for b in doc.get("buffers", [])] + [i.get("uri") for i in doc.get("images", [])]
    return all(u is None or str(u).startswith("data:") for u in uris)


def clean_text(value: str | None, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _new_id(label: str, taken: set[str]) -> str:
    base = PREFIX + (re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:30].strip("_") or "hair")
    fid, i = base, 2
    while fid in taken:
        fid, i = f"{base}_{i}", i + 1
    return fid


def _build_entry(sid: str, label: str, family: str, fringe: bool, curly: bool, source: str, licence: str, file_name: str,
                 res: dict[str, Any]) -> dict[str, Any]:
    cards = res.get("kind") == "cards"
    lite_ok = bool(res.get("lite_triangles") and res["lite_triangles"] <= 750)
    pre = f"{URL_PREFIX}{sid}/"
    licence_line = licence if licence else "Not stated"
    return {
        "id": sid, "name": label, "pack_name": label, "pack": "Your own hairstyles", "source_url": source if source.lower().startswith("http") else "",
        "author": "You" if licence == "My own work" else (source if source and not source.lower().startswith("http") else "Not stated"),
        "licence": licence_line, "licence_line": licence_line, "source_file": file_name, "source_note": source,
        "family": family, "fringe": fringe, "curly": curly, "parting": "centre", "length_rel_head": res["length_rel_head"],
        "kind": "cards" if cards else "shell", "alpha": "cutout" if cards else "opaque", "two_sided": bool(cards),
        "triangles": res["triangles"], "source_triangles": res["source_triangles"], "vertices": res["vertices"],
        "hang_groups": [{k: g[k] for k in ("k", "kind", "path_q", "length_q")} for g in res["hang_groups"]][:3],
        "textures": {"grey": pre + "grey.png", "normal": None, "size": 1024},
        "phone_ok": (not cards) and lite_ok, "lite": pre + f"{sid}_lite.npz" if res.get("lite_triangles") else None,
        "lite_triangles": res.get("lite_triangles"), "default_params": {"volume": 1.0, "dz_head": 0.0, "length": 1.0, "mirror": False},
        "thumbs": {"front": pre + "thumbs/front.png", "quarter": pre + "thumbs/quarter.png"},
        "self_check_mm": res["self_check_mm"], "notes": "your own hairstyle", "own": True, "has_texture": res.get("has_texture", False),
        "files": {"npz": pre + f"{sid}.npz"},
        "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def public(e: dict[str, Any]) -> dict[str, Any]:
    thumbs = {k: f"/api/hair-library/files/{p}" for k, p in (e.get("thumbs") or {}).items() if p}
    return {"id": e["id"], "name": e.get("name"), "family": e.get("family"), "family_label": FAMILY_LABELS.get(str(e.get("family")), ""),
            "fringe": bool(e.get("fringe")), "curly": bool(e.get("curly")), "kind": e.get("kind"), "triangles": e.get("triangles"),
            "phone_ok": bool(e.get("phone_ok")), "source": e.get("source_note", ""), "licence": e.get("licence", ""),
            "file_name": e.get("source_file", ""), "thumbs": thumbs, "added_at": e.get("added_at", ""),
            "has_texture": bool(e.get("has_texture")), "own": True}


# --- add, change, remove -----------------------------------------------------------------------------------------------


def _check_form(label: str, family: str, licence: str) -> tuple[str, str, str]:
    label = clean_text(label, MAX_NAME)
    if not label:
        raise HairError("Give the hairstyle a name.")
    if family not in FAMILIES:
        raise HairError("Pick the kind of hairstyle (short, long, bob, ponytail, bun, braid or afro).")
    licence = clean_text(licence, 40)
    if licence and licence not in LICENCES:
        raise HairError("Pick one of the licences in the list, or leave it unset.")
    return label, family, licence


def _builtin_ids() -> set[str]:
    from . import hairstyles

    return set(hairstyles._load(str(hairstyles.DATA_DIR)).get("styles", {}))


def _name_taken(label: str, settings: Settings, skip: str | None = None) -> bool:
    low = label.lower()
    return any(e.get("name", "").lower() == low and k != skip for k, e in _read(settings).items())


def run_import(settings: Settings, src: Path, sid: str, folder: Path) -> dict[str, Any]:
    """Run blender/hairimport.py on `src`; the files land in `folder`. -> result.json, or HairError in plain words."""
    from .tools import ToolError, run_blender

    if not REFERENCE.is_file():
        raise HairError("The hair library's reference head is missing from this copy of the app, so your own hairstyles cannot be added.", 500)
    work = Path(tempfile.mkdtemp(prefix="vgac_hair_", dir=str(Path(settings.library_path))))
    try:
        job = {"id": sid, "src": str(src), "out_dir": str(work), "reference": str(REFERENCE), "stats": reference_stats()}
        jp = work / "job.json"
        jp.write_text(json.dumps(job), encoding="utf-8")
        try:
            run_blender(settings, "hairimport.py", ["import", jp], log_path=work / "hairimport.log", timeout=600)
        except ToolError as e:
            doc = _result(work)
            if doc and doc.get("error") in REFUSALS:
                raise HairError(REFUSALS[doc["error"]])
            raise HairError(REFUSALS["failed"] + " (" + str(e)[-160:].replace("\n", " ") + ")", 422)
        doc = _result(work)
        if not doc:
            raise HairError(REFUSALS["failed"], 422)
        if doc.get("error"):
            raise HairError(REFUSALS.get(doc["error"], REFUSALS["failed"]))
        for need in (f"{sid}.npz", f"thumbs/{sid}_front.png"):
            if not (work / need).is_file():
                raise HairError(REFUSALS["failed"], 422)
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(work / f"{sid}.npz", folder / f"{sid}.npz")
        if (work / f"{sid}_lite.npz").is_file():
            shutil.copy2(work / f"{sid}_lite.npz", folder / f"{sid}_lite.npz")
        (folder / "thumbs").mkdir(exist_ok=True)
        for v in ("front", "quarter"):
            f = work / "thumbs" / f"{sid}_{v}.png"
            if f.is_file():
                shutil.copy2(f, folder / "thumbs" / f"{v}.png")
        _grey_png(work / "grey_rgba.png", folder / "grey.png", doc.get("kind") == "cards")
        return doc
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _result(work: Path) -> dict[str, Any] | None:
    p = work / "result.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
    except (OSError, ValueError):
        return None


def _grey_png(src: Path, dest: Path, cards: bool) -> None:
    """The library's grey texture is a luminance + alpha PNG; with no picture in the file, a flat mid grey."""
    from PIL import Image

    if src.is_file():
        im = Image.open(src).convert("RGBA")
        g = im.convert("L")
        a = im.getchannel("A") if cards else Image.new("L", im.size, 255)
        Image.merge("LA", (g, a)).save(dest, "PNG", optimize=True)
    else:
        Image.merge("LA", (Image.new("L", (64, 64), 128), Image.new("L", (64, 64), 255))).save(dest, "PNG")


async def save_upload(request: Request, raw_name: str, label: str, family: str, fringe: bool, curly: bool, source: str, licence: str,
                      settings: Settings | None = None) -> dict[str, Any]:
    s = settings or get_settings()
    name = safe_upload_name(raw_name)
    ext = os.path.splitext(name)[1].lower()
    label, family, licence = _check_form(label or os.path.splitext(name)[0].replace("_", " "), family, licence)
    source = clean_text(source, MAX_SOURCE)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_FILE_BYTES:
        raise HairError(f"This file is larger than {MAX_FILE_BYTES // 1024 ** 2} MB. Hair meshes are much smaller.", 413)
    if _name_taken(label, s):
        raise HairError("You already have a hairstyle with that name. Choose another name.")
    base = root(s)
    base.mkdir(parents=True, exist_ok=True)
    with _lock:
        sid = _new_id(label, set(_read(s)) | _builtin_ids() | {p.name for p in base.iterdir()})
        folder = base / sid
        folder.mkdir()
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
                    raise HairError(f"This file is larger than {MAX_FILE_BYTES // 1024 ** 2} MB. Hair meshes are much smaller.", 413)
                if len(first) < 512:
                    first += chunk[: 512 - len(first)]
                buf.extend(chunk)
                if len(buf) >= WRITE_CHUNK:
                    data, buf = bytes(buf), bytearray()
                    await asyncio.to_thread(f.write, data)
            if buf:
                await asyncio.to_thread(f.write, bytes(buf))
        if total == 0:
            raise HairError("The file arrived empty. Try choosing it again.")
        sniff_kind(first, ext)
        src = folder / name
        os.rename(part, src)
        if ext == ".gltf" and not gltf_is_self_contained(src):
            raise HairError("This glTF points at other files (a .bin or pictures). Export it as one GLB file instead.")
        res = await asyncio.to_thread(run_import, s, src, sid, folder)
        entry = _build_entry(sid, label, family, fringe, curly, source, licence, name, res)
        # the source file is not kept: only the baked arrays, the picture and the thumbnails are used
        src.unlink(missing_ok=True)
        with _lock:
            styles = _read(s)
            styles[sid] = entry
            _write(styles, s)
        ok = True
        return public(entry)
    finally:
        if part.exists():
            try:
                part.unlink()
            except OSError:
                pass
        if not ok:
            shutil.rmtree(folder, ignore_errors=True)


def update(sid: str, label: str | None, family: str | None, fringe: bool | None, curly: bool | None, source: str | None,
           licence: str | None, settings: Settings | None = None) -> dict[str, Any]:
    s = settings or get_settings()
    with _lock:
        styles = _read(s)
        e = styles.get(sid)
        if e is None:
            raise HairError("That hairstyle is not on your list.", 404)
        if label is not None:
            label = clean_text(label, MAX_NAME)
            if not label:
                raise HairError("Give the hairstyle a name.")
            if _name_taken(label, s, skip=sid):
                raise HairError("You already have a hairstyle with that name. Choose another name.")
            e["name"] = e["pack_name"] = label
        if family is not None:
            if family not in FAMILIES:
                raise HairError("Pick the kind of hairstyle (short, long, bob, ponytail, bun, braid or afro).")
            e["family"] = family
        if fringe is not None:
            e["fringe"] = bool(fringe)
        if curly is not None:
            e["curly"] = bool(curly)
        if licence is not None:
            licence = clean_text(licence, 40)
            if licence and licence not in LICENCES:
                raise HairError("Pick one of the licences in the list, or leave it unset.")
            e["licence"] = e["licence_line"] = licence or "Not stated"
        if source is not None:
            e["source_note"] = clean_text(source, MAX_SOURCE)
            e["source_url"] = e["source_note"] if e["source_note"].lower().startswith("http") else ""
        e["author"] = "You" if e.get("licence") == "My own work" else (e.get("source_note") if e.get("source_note") and not e.get("source_url") else "Not stated")
        styles[sid] = e
        _write(styles, s)
        return public(e)


def remove(sid: str, in_use: list[str] | None = None, settings: Settings | None = None) -> dict[str, Any]:
    s = settings or get_settings()
    if in_use:
        raise HairError("Used by " + ", ".join(in_use[:3]) + ". Pick another hairstyle for it first, then remove this one.", 409)
    with _lock:
        styles = _read(s)
        if sid not in styles:
            raise HairError("That hairstyle is not on your list.", 404)
        del styles[sid]
        _write(styles, s)
    if ID_RE.match(sid):
        shutil.rmtree(root(s) / sid, ignore_errors=True)
    return {"removed": sid}


# --- routes ------------------------------------------------------------------------------------------------------------


def _http(e: HairError) -> HTTPException:
    return HTTPException(e.status, e.message)


def _users_of(sid: str) -> list[str]:
    from .db import Asset, get_session

    gen = get_session()
    db = next(gen)
    try:
        names = []
        for a in db.query(Asset).all():
            pick = (getattr(a, "hair", None) or {}).get("pick")
            if pick == sid:
                names.append(a.name)
        return names
    finally:
        gen.close()


@router.get("/api/my-hair")
def get_my_hair() -> dict[str, Any]:
    return {"styles": [public(e) for e in sorted(_read().values(), key=lambda x: x.get("added_at", ""))], "responsibility": RESPONSIBILITY,
            "max_mb": MAX_FILE_BYTES // 1024 ** 2, "kinds": list(KINDS), "families": [{"id": f, "label": FAMILY_LABELS[f]} for f in FAMILIES],
            "licences": list(LICENCES)}


@router.post("/api/my-hair/upload")
async def post_my_hair_upload(request: Request, name: str = Query(..., max_length=400), label: str = Query("", max_length=200),
                              family: str = Query("short", max_length=20), fringe: bool = Query(False), curly: bool = Query(False),
                              source: str = Query("", max_length=600), licence: str = Query("", max_length=60)) -> dict[str, Any]:
    try:
        return await save_upload(request, name, label, family, fringe, curly, source, licence)
    except HairError as e:
        raise _http(e)


class MyHairIn(BaseModel):
    label: str | None = Field(default=None, max_length=MAX_NAME * 2)
    family: str | None = Field(default=None, max_length=20)
    fringe: bool | None = None
    curly: bool | None = None
    source: str | None = Field(default=None, max_length=MAX_SOURCE * 2)
    licence: str | None = Field(default=None, max_length=60)


@router.put("/api/my-hair/{sid}")
def put_my_hair(sid: str, body: MyHairIn) -> dict[str, Any]:
    try:
        return update(sid, body.label, body.family, body.fringe, body.curly, body.source, body.licence)
    except HairError as e:
        raise _http(e)


@router.delete("/api/my-hair/{sid}")
def delete_my_hair(sid: str) -> dict[str, Any]:
    try:
        return remove(sid, _users_of(sid))
    except HairError as e:
        raise _http(e)
