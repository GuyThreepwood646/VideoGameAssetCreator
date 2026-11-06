"""Stage 6, export (host): engine texture packs, Blender pack (LODs, collider, FBX with clips, a clean GLB plus a Godot
GLB and per-LOD GLBs), Unity sandbox import with clips and snapshot strip, optional sprite sheets, manifest.json,
quality gate.

Input mesh: the rig stage's animated GLB when present, else its (rigged or pass-through) GLB, else the prep GLB.
Textures come from the prep stage in the lineage (the mesh stage when prep has none).

Recipes (reference.recipe): a look without surface bumps ships no normal map (none is copied, packed or put in the
GLB material, and the importer leaves the slot empty); pack.py gets the prep stage's shading (flat for low-poly) and
the texture filter (crisp: nearest-neighbour samplers in the GLB); the Unity importer gets the engine settings
(-vgacFilter point|bilinear, -vgacMipmaps, -vgacCompress, -vgacNormalMap). The manifest and metrics.recipe record the
recipe that ran.

Sockets (vgac/sockets.py): a rigged character gets attachment points (Socket_Hand_R, Socket_Back and so on). pack.py
places them as empties parented to the bones in the FBX and every GLB and writes sockets.json; this stage fills each
socket's joint-local transform from the clean GLB (and checks the GLB node sits where the file says), registers
sockets.json, sets metrics.sockets = {count, names}, passes -vgacSockets to the Unity importer and writes a `sockets`
block with one plain sentence per socket into the manifest.

Hair as its own material (vgac/hairmat.py): when the clean-up step gave the hair its own texture (metrics
hair_material.use), its maps (hair_base_color, hair_normal, hair_roughness, hair_ao, hair_flow) and hair_layout.json
come in from the prep version; the hair packs T_<name>_Hair_MetallicSmoothness and T_<name>_Hair_ORM are written;
pack.py gives the hair triangles the second material slot (FBX slots <name>_Body and <name>_Hair, two textured
materials in every GLB); every GLB's hair material gets KHR_materials_anisotropy with the flow map; the Unity importer
gets -vgacHair on and builds the second material; the manifest gets a hair_material block and README.txt the plain
lines for Unreal and Godot. The check hair_material_shipped says whether the files carry it.

Hair from the library (docs/HAIR-LIBRARY-PLAN.md section 6; library_hair: the clean-up step attached a hairstyle and the
rig step bound it): the same hair maps come in with the canonical hair_mesh.npz instead of a layout, pack.py joins the
VGAC_Hair object as the second slot (cards keep their faces at LOD1 and only the scalp cap from LOD2; body-only
measures and lods_body), the Unity importer also gets -vgacHairCutout and -vgacHairTwoSided as the hairstyle needs
(VGAC/HairCutout, importer Version 17, the shader travels with the importer), the manifest's hair_material block says
source library with the style's credit, and README.txt gets the credit lines. LOD ratios are checked on the body.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .. import colliders, guidance, hairmat, items, library, quality, reference, sockets
from .. import springs as springs_mod
from ..beauty import render_beauty, showcase_sky
from ..db import _iso, utcnow
from ..imaging import contact_sheet, image_size
from ..sprites import pack_sprites
from .base import StageContext, manifest_provenance, slug

TEXTURE_KEYS = ("base_color", "normal", "metallic", "roughness", "ao")
PACK_KEYS = ("metallic_smoothness", "orm")
MOVEMENT_SHAPES = ("auto", "upright", "along", "box", "none")
HITBOX_NOTE = ("Hit boxes are trigger colliders on the bones; Unity sends trigger events only when one of the two "
               "objects has a Rigidbody (a kinematic one is enough).")
# Channel layouts written to the manifest (G02).
PACK_LAYOUTS = {
    "metallic_smoothness": "RGB metallic, A smoothness (1 - roughness); linear; Unity URP Lit and Standard _MetallicGlossMap",
    "orm": "R occlusion, G roughness, B metallic; linear; glTF metallicRoughness and occlusion, Unreal and Godot ORM",
}
# Defaults for a missing input map, as 8-bit values: no metal, middling roughness, no extra shadowing.
_PACK_DEFAULTS = {"metallic": 0, "roughness": 128, "ao": 255}


LOD_SCREEN_HEIGHTS = [0.6, 0.3, 0.12, 0.03]

# G17 (plan 3.8 A5): the Unity importer generates lightmap UVs (UV1) for these categories; environment pieces also get
# the static flags Contribute GI and Batching Static. The settings are the ones VgacImport.cs uses (the plan gives
# none; these are Unity's defaults with the margin calculated from the lightmap resolution, which avoids bleeding).
LIGHTMAP_CATEGORIES = ("environment", "prop_rigid")
LIGHTMAP_SETTINGS = {"margin_method": "Calculate", "min_lightmap_resolution": 40, "min_object_scale": 1, "hard_angle": 88,
                     "angle_distortion": 8, "area_distortion": 15}
# G20 part one (plan 3.13): foliage LODGroups cross-fade instead of popping.
CROSSFADE_CATEGORIES = ("foliage",)
CROSSFADE_WIDTH = 0.25
CROSSFADE_NOTE = ("LODGroup fadeMode CrossFade, each level fading over the last quarter of its range; URP needs "
                  "'LOD Cross Fade' on in the pipeline asset for the dither to show.")
# Collision checks (G16, G35): Unity's convex MeshCollider cap.
UNITY_CONVEX_TRIANGLES = 255


def parse_lods(value: Any) -> list[float] | None:
    """Accept a list of numbers or a string like "1.0, 0.5, 0.25, 0.1"; a leading 1.0 (LOD0) is dropped."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        items = [x.strip() for x in value.replace(";", ",").split(",") if x.strip()]
    else:
        items = list(value)
    out = []
    for x in items:
        try:
            out.append(float(x))
        except (TypeError, ValueError):
            raise ValueError(f"bad LOD ratio {x!r}")
    if out and abs(out[0] - 1.0) < 1e-9:
        out = out[1:]
    return out


def lod_list(lods: dict[str, int]) -> list[dict[str, Any]]:
    out = []
    for i in range(len(lods)):
        key = f"LOD{i}"
        if key not in lods:
            continue
        out.append({"level": i, "triangles": lods[key], "screen_height": LOD_SCREEN_HEIGHTS[i] if i < len(LOD_SCREEN_HEIGHTS) else 0.01})
    return out


def asset_export_name(sc: StageContext) -> str:
    return f"{slug(sc.asset.name)}_{sc.asset.id[:8]}"


# --- Engine texture packs (G01, G02; docs/GREAT-ASSETS-PLAN.md 3.1) ---------------------------------------------------

def _channel(path: Path | str | None, size: tuple[int, int]) -> np.ndarray | None:
    """One 8-bit channel (height x width) of a grey map, resized to `size` (width, height). 16-bit and float images
    are scaled to 8 bits; colour images are read as luminance (the maps are grey)."""
    if path is None or not Path(path).is_file():
        return None
    with Image.open(path) as im:
        if im.mode in ("I", "I;16", "I;16B", "I;16L", "F"):
            arr = np.asarray(im, dtype=np.float64)
            peak = 1.0 if im.mode == "F" else (65535.0 if arr.max() > 255 else 255.0)
            g = Image.fromarray(np.clip(np.rint(arr / peak * 255.0), 0, 255).astype(np.uint8), "L")
        else:
            g = im.convert("L")
        if g.size != size:
            g = g.resize(size, Image.BILINEAR)
        return np.asarray(g, dtype=np.uint8).copy()


def pack_texture_maps(textures: dict[str, Path | str | None], out_dir: Path | str, name: str) -> dict[str, Path]:
    """Write T_<name>_MetallicSmoothness.png (RGB metallic, A 1 - roughness; Unity) and T_<name>_ORM.png (R occlusion,
    G roughness, B metallic; glTF, Godot, Unreal) into `out_dir` at the size of the largest of the maps they are made
    of (not the base colour's: "High" face detail ships the base colour alone at 4096, and enlarging the data maps to
    match would only cost memory). A missing input takes the defaults above. The metal-and-shine pack needs a metallic or roughness map;
    the ORM pack any of ao, roughness and metallic. -> {"metallic_smoothness": path, "orm": path} for what was written.

    The tileable material category writes both packs too: stages/material.py calls this with its own maps."""
    present = {k: Path(v) for k, v in textures.items() if v and Path(v).is_file()}
    if not any(k in present for k in ("metallic", "roughness", "ao")):
        return {}
    size: tuple[int, int] | None = None
    for k in ("metallic", "roughness", "ao"):
        if k in present:
            with Image.open(present[k]) as im:
                if size is None or im.size[0] * im.size[1] > size[0] * size[1]:
                    size = im.size
    assert size is not None
    w, h = size
    ch: dict[str, np.ndarray] = {}
    for k in ("metallic", "roughness", "ao"):
        arr = _channel(present.get(k), size)
        ch[k] = arr if arr is not None else np.full((h, w), _PACK_DEFAULTS[k], dtype=np.uint8)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    if "metallic" in present or "roughness" in present:
        ms = np.dstack([ch["metallic"], ch["metallic"], ch["metallic"], 255 - ch["roughness"]])
        p = out / f"T_{name}_MetallicSmoothness.png"
        Image.fromarray(ms, "RGBA").save(p)
        written["metallic_smoothness"] = p
    orm = np.dstack([ch["ao"], ch["roughness"], ch["metallic"]])
    p = out / f"T_{name}_ORM.png"
    Image.fromarray(orm, "RGB").save(p)
    written["orm"] = p
    return written


# --- The Unity importer copy in a user's project (docs/GREAT-ASSETS-PLAN.md 2.6) -------------------------------------

_IMPORTER_VERSION_RE = re.compile(r'public\s+const\s+string\s+Version\s*=\s*"(\d+)"')


def importer_version(path: Path | str) -> int | None:
    """The `public const string Version = "N";` of a VgacImport.cs; None when the file or the constant is missing."""
    p = Path(path)
    if not p.is_file():
        return None
    m = _IMPORTER_VERSION_RE.search(p.read_text(encoding="utf-8", errors="replace"))
    return int(m.group(1)) if m else None


def sync_importer(src: Path | str, editor_dir: Path | str) -> dict[str, Any]:
    """Copy the sandbox's VgacImport.cs into a project's Assets/Editor when the copy there is missing, has no Version,
    or has a lower one; a higher version there is left alone. -> {action, version, previous} with action one of
    copied, updated, current, kept_newer, no_source."""
    src_p, dest = Path(src), Path(editor_dir) / "VgacImport.cs"
    if not src_p.is_file():
        return {"action": "no_source", "version": None, "previous": importer_version(dest)}
    new = importer_version(src_p)
    if not dest.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_p, dest)
        return {"action": "copied", "version": new, "previous": None}
    old = importer_version(dest)
    if old is None or (new is not None and old < new):
        shutil.copy2(src_p, dest)
        return {"action": "updated", "version": new, "previous": old}
    return {"action": "current" if old == new else "kept_newer", "version": old, "previous": old}


SHADER_REL = Path("Assets") / "VgacShaders" / "VgacHair.shader"
_SHADER_VERSION_RE = re.compile(r"VgacHair\s+Version\s+(\d+)")


def shader_version(path: Path | str) -> int | None:
    """The "// VgacHair Version N" line of VgacHair.shader; None when the file or the line is missing."""
    p = Path(path)
    if not p.is_file():
        return None
    m = _SHADER_VERSION_RE.search(p.read_text(encoding="utf-8", errors="replace"))
    return int(m.group(1)) if m else None


def sync_shader(src: Path | str, dest: Path | str) -> dict[str, Any]:
    """Copy the sandbox's VgacHair.shader to the same place in a user's project when the copy there is missing or
    older, as sync_importer does for VgacImport.cs. -> {action, version, previous}."""
    src_p, dest_p = Path(src), Path(dest)
    if not src_p.is_file():
        return {"action": "no_source", "version": None, "previous": shader_version(dest_p)}
    new, old = shader_version(src_p), shader_version(dest_p)
    existed = dest_p.is_file()
    if not existed or old is None or (new is not None and old < new):
        dest_p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_p, dest_p)
        return {"action": "updated" if existed else "copied", "version": new, "previous": old}
    return {"action": "current" if old == new else "kept_newer", "version": old, "previous": old}


CUTOUT_SHADER_REL = Path("Assets") / "VgacShaders" / "VgacHairCutout.shader"
_CUTOUT_VERSION_RE = re.compile(r"VgacHairCutout\s+Version\s+(\d+)")


def cutout_shader_version(path: Path | str) -> int | None:
    """The "// VgacHairCutout Version N" line of VgacHairCutout.shader; None when the file or the line is missing."""
    p = Path(path)
    if not p.is_file():
        return None
    m = _CUTOUT_VERSION_RE.search(p.read_text(encoding="utf-8", errors="replace"))
    return int(m.group(1)) if m else None


def sync_cutout_shader(src: Path | str, dest: Path | str) -> dict[str, Any]:
    """Copy VgacHairCutout.shader into a user's project when the copy there is missing or older (as sync_shader)."""
    src_p, dest_p = Path(src), Path(dest)
    if not src_p.is_file():
        return {"action": "no_source", "version": None, "previous": cutout_shader_version(dest_p)}
    new, old = cutout_shader_version(src_p), cutout_shader_version(dest_p)
    existed = dest_p.is_file()
    if not existed or old is None or (new is not None and old < new):
        dest_p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_p, dest_p)
        return {"action": "updated" if existed else "copied", "version": new, "previous": old}
    return {"action": "current" if old == new else "kept_newer", "version": old, "previous": old}


# Spring bones: the runtime script the importer adds to the prefab travels with it the same way.
SPRING_SCRIPT_REL = Path("Assets") / "VgacScripts" / "VgacSpringBones.cs"
_SPRING_VERSION_RE = re.compile(r"VgacSpringBones\s+Version\s+(\d+)")


def spring_script_version(path: Path | str) -> int | None:
    """The "// VgacSpringBones Version N" line of VgacSpringBones.cs; None when the file or the line is missing."""
    p = Path(path)
    if not p.is_file():
        return None
    m = _SPRING_VERSION_RE.search(p.read_text(encoding="utf-8", errors="replace"))
    return int(m.group(1)) if m else None


def sync_spring_script(src: Path | str, dest: Path | str) -> dict[str, Any]:
    """Copy the sandbox's VgacSpringBones.cs to the same place in a user's project when the copy there is missing or
    older, as sync_shader does for the hair shader. -> {action, version, previous}."""
    src_p, dest_p = Path(src), Path(dest)
    if not src_p.is_file():
        return {"action": "no_source", "version": None, "previous": spring_script_version(dest_p)}
    new, old = spring_script_version(src_p), spring_script_version(dest_p)
    existed = dest_p.is_file()
    if not existed or old is None or (new is not None and old < new):
        dest_p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_p, dest_p)
        return {"action": "updated" if existed else "copied", "version": new, "previous": old}
    return {"action": "current" if old == new else "kept_newer", "version": old, "previous": old}


# --- Hand-off to an open Unity Editor ---------------------------------------------------------------------------------
# Unity refuses to open a project in batch mode while the Editor has it open, which is the usual case for a game
# maker's own project. Then the export leaves the importer's inputs and a request file in the project and the Editor
# (VgacImportWatcher in VgacImport.cs, Version 6 and later) runs the same import when it next refreshes.

HANDOFF_MIN_IMPORTER = 6
HANDOFF_REQUEST = "vgac_import.json"
HANDOFF_DONE = "vgac_import.done.json"
HANDOFF_NOTE = "Sent to your open Unity project. It finishes importing when you switch to Unity."
# The importer's own texture inputs (VgacImport.Import copies these from its input folder).
HANDOFF_TEXTURES = ("base_color.png", "normal.png", "metallic.png", "roughness.png", "ao.png",
                    "hair_base_color.png", "hair_normal.png", "hair_ao.png", "hair_flow.png")


def _open_exclusive(path: Path):
    """Open a file for writing the way another process would have to; fails while Unity holds it (Windows opens it
    without sharing; on macOS and Linux Unity holds an flock on it)."""
    f = open(path, "r+b")
    try:
        import fcntl  # noqa: PLC0415  (POSIX only)
    except ImportError:
        return f
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError:
        f.close()
        raise
    return f


def unity_project_open(path: Path | str, opener=None) -> bool:
    """True when a Unity Editor has this project open: <project>/Temp/UnityLockfile exists and cannot be opened for
    exclusive write. A lock file left behind by a crashed Editor opens fine, so it reads as closed. `opener` is for
    tests (a function path -> file handle that may raise)."""
    lock = Path(path) / "Temp" / "UnityLockfile"
    if not lock.is_file():
        return False
    try:
        f = (opener or _open_exclusive)(lock)
    except FileNotFoundError:
        return False
    except OSError:  # PermissionError on Windows, BlockingIOError from flock
        return True
    try:
        f.close()
    except Exception:  # noqa: BLE001
        pass
    return False


def handoff_folder(project: Path | str, name: str) -> Path:
    """<project>/Assets/VGAC/<name>/_incoming: the importer builds the prefab in Assets/VGAC/<name>/."""
    return Path(project) / "Assets" / "VGAC" / name / "_incoming"


def _remove_with_meta(p: Path) -> None:
    for q in (p, p.with_name(p.name + ".meta")):
        if q.is_file():
            q.unlink()


def handoff_to_editor(src_dir: Path | str, project: Path | str, name: str, args: dict[str, Any], flags: list[str],
                      importer: dict[str, Any] | None = None, version_id: str | None = None) -> dict[str, Any]:
    """Copy the importer's inputs (the FBX, the textures and packed maps, hitboxes.json, colliders.json, the clip
    sidecar) into <project>/Assets/VGAC/<name>/_incoming/ and write vgac_import.json there with every argument the
    batch call would have passed. File arguments become names in that folder; the shared clip library keeps its own
    path. -> the metrics.unity_project_import value (state "sent", or "failed" when the project's importer is too old
    to take a hand-off)."""
    import uuid  # noqa: PLC0415

    src, proj = Path(src_dir), Path(project)
    folder = handoff_folder(proj, name)
    imp = importer or {}
    have = imp.get("version") if imp.get("version") is not None else imp.get("previous")
    base = {"project": str(proj), "folder": str(folder), "importer": importer}
    if have is None or int(have) < HANDOFF_MIN_IMPORTER:
        return {**base, "ok": False, "state": "failed", "request_id": None,
                "error": f"the importer script in your project (Assets/Editor/VgacImport.cs) is version {have}; "
                         f"importing into an open project needs version {HANDOFF_MIN_IMPORTER} or later"}
    folder.mkdir(parents=True, exist_ok=True)
    # A report or done marker from an earlier request must not be read as this one's result.
    for stale in ("unity_report.json", HANDOFF_DONE):
        _remove_with_meta(folder / stale)
    copied: list[str] = []

    def put(p: Path) -> str:
        shutil.copy2(p, folder / p.name)
        copied.append(p.name)
        return p.name

    fbx = src / f"{name}.fbx"
    if not fbx.is_file():
        raise FileNotFoundError(f"no FBX to hand off: {fbx}")
    put(fbx)
    for t in (*HANDOFF_TEXTURES, f"T_{name}_MetallicSmoothness.png", f"T_{name}_ORM.png", f"T_{name}_Hair_MetallicSmoothness.png"):
        if (src / t).is_file():
            put(src / t)
        else:
            _remove_with_meta(folder / t)  # a map an earlier request carried (a normal map the new look drops)
    argv: list[str] = [f for f in flags if f != "-nographics"]  # -nographics is a batch-mode switch
    arguments: dict[str, str] = {}
    rel_folder = folder.relative_to(proj).as_posix()
    for k, v in args.items():
        if k == "-vgacInput":
            value = rel_folder  # the Editor reads its inputs from the request's own folder
        elif isinstance(v, Path) or (isinstance(v, str) and k in ("-vgacHitboxes", "-vgacColliders", "-vgacClipInfo", "-vgacClips", "-vgacSockets", "-vgacAttachments", "-vgacSprings")):
            p = Path(v)
            # Inputs from this export's folder travel with the request; anything else (the shared clip library)
            # is read where it is.
            value = put(p) if p.is_file() and p.parent.resolve() == src.resolve() else str(p)
        else:
            value = str(v)
        arguments[k] = value
        argv += [k, value]
    request_id = uuid.uuid4().hex
    req = {"request_id": request_id, "importer_version": str(have), "name": name, "args": argv, "arguments": arguments,
           "flags": [f for f in flags if f != "-nographics"], "files": copied, "source_version": version_id,
           "created_at": _iso(utcnow()), "prefab": f"Assets/VGAC/{name}/{name}.prefab"}
    tmp = folder / (HANDOFF_REQUEST + ".tmp~")  # Unity ignores names ending in ~, so it never sees a half-written file
    tmp.write_text(json.dumps(req, indent=1), encoding="utf-8")
    tmp.replace(folder / HANDOFF_REQUEST)
    return {**base, "ok": None, "state": "sent", "request_id": request_id, "note": HANDOFF_NOTE, "prefab": "", "error": "",
            "importer_version": str(have)}


def collect_unity_import(version: Any) -> dict[str, Any] | None:
    """Pick up the open Editor's result for a hand-off: when metrics.unity_project_import is "sent" and its folder
    holds a unity_report.json with the same request id, update the metrics to state "imported" or "failed" and return
    the new value; None when there is nothing new (no hand-off, already collected, or the Editor has not run yet)."""
    m = (getattr(version, "metrics", None) or {}).get("unity_project_import")
    if not isinstance(m, dict) or m.get("state") != "sent" or not m.get("folder") or not m.get("request_id"):
        return None
    rp = Path(m["folder"]) / "unity_report.json"
    if not rp.is_file():
        return None
    try:
        report = json.loads(rp.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None  # still being written
    if report.get("request_id") != m["request_id"]:
        return None
    ok = bool(report.get("ok"))
    new = {**m, "ok": ok, "state": "imported" if ok else "failed", "prefab": report.get("prefab", "") if ok else "",
           "error": "" if ok else (report.get("error") or "Unity reported a failure without a message")[:300],
           "importer_version": report.get("importer_version"), "report": str(rp)}
    new.pop("note", None)
    library.update_metrics(version, unity_project_import=new)
    return new


def _input_glb(sc: StageContext) -> tuple[Path, str]:
    # A held item exports the copy with its pivot at the grip (items.pivot_item).
    if getattr(sc, "item_input", None) is not None and Path(sc.item_input).is_file():
        return Path(sc.item_input), "prep.glb"
    for key, stages in (("anim_glb", ("rig",)), ("glb", ("rig",)), ("glb", ("prep",)), ("glb", ("mesh",))):
        p = sc.lineage_file(key, stages)
        if p is not None:
            return p, f"{stages[0]}.{key}"
    raise FileNotFoundError("no mesh to export in lineage")


def _is_humanoid(sc: StageContext) -> bool:
    rig_v = sc.stage_version("rig")
    bones = (rig_v.metrics.get("bones") if rig_v else 0) or 0
    return sc.category.get("rig_engine") == "skintoken_mixamo" and bones > 0


async def _colliders(sc: StageContext, params: dict[str, Any], name: str, src: Path) -> tuple[str, dict[str, Any]]:
    """G16 with G35: decide and build the collision shapes in the backend (vgac/colliders.py; CoACD runs here, not in
    Blender). -> (pack.py --collider value, the plan written to colliders.json). Any failure falls back to one hull."""
    mode = colliders.normalise_mode(params.get("collider"))
    category = sc.asset.category
    try:
        sc.progress(0.03, "collision shapes")
        plan = await asyncio.to_thread(colliders.build, src, sc.dir, name, category, mode, sc.platform, params.get("collider_max_parts"))
    except Exception as e:  # noqa: BLE001
        plan = {"mode": mode, "chosen": "none" if mode == "none" else ("mesh" if mode == "mesh" else "hull"), "parts": [], "obj": None,
                "error": str(e)[-400:], "cap": colliders.max_parts(category, sc.platform, params.get("collider_max_parts"))}
    chosen = plan.get("chosen") or "hull"
    pack_mode = "parts" if plan.get("obj") else (chosen if chosen in ("hull", "mesh", "none") else "hull")
    plan["pack_mode"] = pack_mode
    return pack_mode, plan


def _write_colliders(sc: StageContext, plan: dict[str, Any], report: dict[str, Any]) -> None:
    """colliders.json: the plan plus what pack.py built (the Unity importer reads part kinds from it)."""
    doc = {k: v for k, v in plan.items() if k != "obj"}
    doc["parts"] = report.get("collider_parts") or []
    doc["count"] = report.get("collider_count", len(doc["parts"]))
    doc["triangles_max"] = report.get("collider_triangles_max")
    doc["vertices_total"] = report.get("collider_verts")
    doc["fit_scale"] = report.get("collider_fit_scale")
    p = sc.dir / "colliders.json"
    p.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    sc.register("colliders", p)
    if plan.get("obj") and Path(plan["obj"]).is_file():
        sc.register("collider_parts", plan["obj"])


def export_recipe(sc: Any) -> dict[str, Any] | None:
    """The asset's recipe, or None when the context cannot name one (a test double without a style)."""
    try:
        from .concept import stage_recipe

        return stage_recipe(sc)
    except Exception:  # noqa: BLE001
        return None


def ships_normal(sc: Any) -> bool:
    rec = export_recipe(sc)
    return True if rec is None else bool(rec["mesh"]["normal_map"])


def pack_look(sc: Any) -> dict[str, str]:
    """pack.py's --shading and --texture-filter: the shading the prep stage applied (else the recipe's), and the
    recipe's texture filter."""
    rec = export_recipe(sc)
    prep_v = sc.stage_version("prep") if hasattr(sc, "stage_version") else None
    shading = (getattr(prep_v, "metrics", None) or {}).get("shading_applied") or ((rec or {}).get("surface") or {}).get("shading") or "smooth"
    filt = ((rec or {}).get("surface") or {}).get("texture_filter") or "smooth"
    return {"shading": shading if shading in ("smooth", "flat") else "smooth", "texture_filter": filt if filt in ("smooth", "crisp") else "smooth"}


def engine_args(sc: Any) -> dict[str, str]:
    """The importer's texture settings from the recipe (nothing when there is no recipe, so the importer keeps its
    own defaults)."""
    rec = export_recipe(sc)
    if rec is None:
        return {}
    e = rec["engine"]
    return {"-vgacFilter": e["filter_mode"], "-vgacMipmaps": "on" if e["mipmaps"] else "off",
            "-vgacCompress": "on" if e["compress"] else "off", "-vgacNormalMap": "on" if rec["mesh"]["normal_map"] else "off"}


def socket_category(sc: Any) -> str | None:
    """The category pack.py places sockets for: a rigged character (the rig stage made bones and did not keep it
    rigid) in one of sockets.SOCKET_CATEGORIES; None otherwise."""
    category = getattr(getattr(sc, "asset", None), "category", None)
    if category not in sockets.SOCKET_CATEGORIES:
        return None
    rig_v = sc.stage_version("rig") if hasattr(sc, "stage_version") else None
    m = getattr(rig_v, "metrics", None) or {}
    if not (m.get("bones") or 0) or m.get("rigid"):
        return None
    return category


def pack_args(src: Any, out_dir: Any, name: str, lods: list[float], pack_mode: str, formats: list[str], look: dict[str, str],
              collider_obj: Any = None, base_color: Any = None, normal: Any = None, orm: Any = None, uv_source: Any = None,
              socket_cat: str | None = None, hair: dict[str, Any] | None = None) -> list[Any]:
    """pack.py's command line. "none" for --lods asks for LOD0 only (leaving it out would fall back to pack.py's own
    default chain); --sockets only for a rigged character; the hair's own material when `hair` has its layout and
    base colour ({layout, base_color, normal, orm})."""
    args: list[Any] = [src, out_dir, name, "--lods", ",".join(str(x) for x in lods) if lods else "none", "--collider", pack_mode,
                       "--formats", ",".join(formats), "--shading", look["shading"], "--texture-filter", look["texture_filter"]]
    if pack_mode == "parts" and collider_obj:
        args += ["--collider-obj", collider_obj]
    for flag, p in (("--base-color", base_color), ("--normal", normal), ("--orm", orm), ("--uv-source", uv_source)):
        if p is not None:
            args += [flag, p]
    if socket_cat:
        args += ["--sockets", socket_cat]
    if hair and hair.get("library") and hair.get("mesh") and hair.get("base_color"):
        # hair from the library: the rig step's VGAC_Hair object joins as slot 1 (no layout)
        lib = hair["library"]
        args += ["--hair-object", HAIR_OBJECT, "--hair-mesh", hair["mesh"], "--hair-base-color", hair["base_color"]]
        for flag, key in (("--hair-normal", "normal"), ("--hair-orm", "orm")):
            if hair.get(key) is not None:
                args += [flag, hair[key]]
        args += ["--hair-kind", "cards" if lib.get("kind") == "cards" else "shell"]
        if lib.get("alpha") == "cutout":
            args += ["--hair-alpha", "cutout"]
        if lib.get("two_sided"):
            args.append("--hair-two-sided")
    elif hair and hair.get("layout") and hair.get("base_color"):
        args += ["--hair-layout", hair["layout"], "--hair-base-color", hair["base_color"]]
        for flag, key in (("--hair-normal", "normal"), ("--hair-orm", "orm")):
            if hair.get(key) is not None:
                args += [flag, hair[key]]
    return args


HAIR_KEYS = ("hair_base_color", "hair_normal", "hair_roughness", "hair_ao", "hair_flow")
HAIR_PACK_KEYS = ("hair_metallic_smoothness", "hair_orm")


HAIR_OBJECT = "VGAC_Hair"


def library_hair(sc: Any) -> dict[str, Any] | None:
    """The clean-up step's hair_library metrics when it attached a hairstyle from the library and the rig step bound
    it, else None: today's hair, and every branch below stays as it was."""
    prep_v = sc.stage_version("prep") if hasattr(sc, "stage_version") else None
    hl = ((getattr(prep_v, "metrics", None) or {}).get("hair_library") or {})
    if not hl.get("attached"):
        return None
    rig_v = sc.stage_version("rig") if hasattr(sc, "stage_version") else None
    if rig_v is None or not ((getattr(rig_v, "metrics", None) or {}).get("hair_bind") or {}).get("ok"):
        return None  # the hair never joined the skeleton: there is no hair object to hand off
    return hl


def hair_inputs(sc: Any, keep_normal: bool = True) -> dict[str, Path]:
    """The hair's own maps and layout from the clean-up step, copied in, when it gave the hair its own material
    (metrics hair_material.use); {} otherwise. Keys: HAIR_KEYS that exist, plus hair_layout. With hair from the
    library: the same HAIR_KEYS and hair_mesh (the canonical npz) instead of the layout."""
    prep_v = sc.stage_version("prep") if hasattr(sc, "stage_version") else None
    if library_hair(sc) is not None:
        npz = sc.lineage_file("hair_mesh", ("prep",))
        base = sc.lineage_file("hair_base_color", ("prep",))
        if npz is None or base is None:
            return {}
        out_l: dict[str, Path] = {"hair_mesh": sc.copy_in(npz, "hair_mesh.npz")}
        for key in HAIR_KEYS:
            if key == "hair_normal" and not keep_normal:
                continue
            p = sc.lineage_file(key, ("prep",))
            if p is not None:
                out_l[key] = sc.copy_in(p, f"{key}.png")
        return out_l
    hm = ((getattr(prep_v, "metrics", None) or {}).get("hair_material") or {})
    if not hm.get("use"):
        return {}
    layout = sc.lineage_file("hair_layout", ("prep",))
    base = sc.lineage_file("hair_base_color", ("prep",))
    if layout is None or base is None:
        return {}
    out: dict[str, Path] = {"hair_layout": sc.copy_in(layout, "hair_layout.json")}
    for key in HAIR_KEYS:
        if key == "hair_normal" and not keep_normal:
            continue
        p = sc.lineage_file(key, ("prep",))
        if p is not None:
            out[key] = sc.copy_in(p, f"{key}.png")
    return out


def hair_glb_finish(glb_paths: list[Path], flow_png: Path | None, work_dir: Path) -> list[str]:
    """KHR_materials_anisotropy on the hair material of each GLB, with the flow map in glTF's direction convention.
    -> the GLB names it was added to."""
    if flow_png is None or not Path(flow_png).is_file():
        return []
    rgba = np.asarray(Image.open(flow_png).convert("RGBA"))
    gl = work_dir / "hair_anisotropy_gltf.png"
    Image.fromarray(hairmat.gl_flow(rgba), "RGB").save(gl)
    done = []
    for g in glb_paths:
        if Path(g).is_file() and hairmat.glb_add_anisotropy(g, gl, rotation=hairmat.anisotropy_rotation()):
            done.append(Path(g).name)
    return done


def hair_readme(name: str, hm: dict[str, Any], files: dict[str, str]) -> str:
    """README.txt for the hand-off: the hair material lines (hairmat.readme_lines)."""
    lines = [f"{name}", ""] + hairmat.readme_lines(name, int(hm.get("tex") or 1024), files.get("hair_flow", "hair_flow.png"), files)
    return "\n".join(lines) + "\n"


def _write_sockets(sc: StageContext, name: str, report: dict[str, Any]) -> dict[str, Any] | None:
    """sockets.json from pack.py, with each socket's joint-local transform read back from the clean GLB; registered
    with metrics.sockets = {count, names}. -> the document, or None when pack.py wrote none."""
    p = Path(sc.dir) / "sockets.json"
    if not p.is_file():
        if report.get("sockets_error"):
            sc.set_metrics(sockets={"count": 0, "names": [], "error": report["sockets_error"]})
        return None
    doc = json.loads(p.read_text(encoding="utf-8"))
    glb = Path(sc.dir) / f"{name}.glb"
    if glb.is_file():
        try:
            sockets.attach_glb(doc, glb)
        except Exception as e:  # noqa: BLE001  (the model-space data still stands)
            doc["glb_error"] = str(e)[-300:]
    p.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    sc.register("sockets", p)
    sc.set_metrics(sockets=sockets.metrics(doc))
    return doc


async def _pack(sc: StageContext, params: dict[str, Any], name: str) -> dict[str, Any]:
    src, src_key = _input_glb(sc)
    lods = parse_lods(params.get("lods"))
    if lods is None:
        lods = list(sc.category.get("lod_ratios") or [0.5, 0.25])
    collider = colliders.normalise_mode(params.get("collider"))
    formats = list(params.get("formats") or ["fbx", "glb"])
    sc.set_params(lods=lods, collider=collider, formats=formats, input=src_key)
    pack_mode, col_plan = await _colliders(sc, params, name, src)
    sc.set_params(collider_shape=col_plan.get("chosen"))
    sc.use_model("blender")
    textures: dict[str, Path] = {}
    keep_normal = ships_normal(sc)
    look = pack_look(sc)
    rec = export_recipe(sc)
    if rec is not None:
        sc.set_metrics(recipe=reference.recipe_record(rec, "mesh", "surface", "engine"), shading=look["shading"],
                       texture_filter=look["texture_filter"], normal_map=keep_normal)
    for key in TEXTURE_KEYS:
        if key == "normal" and not keep_normal:
            stale = sc.dir / "normal.png"
            if stale.is_file():
                stale.unlink()
            continue
        p = sc.lineage_file(key, ("prep", "mesh"))
        if p is not None:
            textures[key] = sc.copy_in(p, f"{key}.png")
            sc.register(key, textures[key])
    hb = sc.lineage_file("hitboxes", ("rig",))
    if hb is not None:
        sc.register("hitboxes", sc.copy_in(hb, "hitboxes.json"))
    # The clip sidecar (loop flags do not survive in the GLB or FBX); only when the input is the animated GLB.
    ci = sc.lineage_file("anim_clips", ("rig",)) if src_key == "rig.anim_glb" else None
    if ci is not None:
        sc.register("anim_clips", sc.copy_in(ci, "anim_clips.json"))
    # Spring bones: the chains are in the animated GLB already; springs.json goes with the files.
    sp = sc.lineage_file("springs", ("rig",)) if src_key == "rig.anim_glb" else None
    if sp is not None:
        sc.register("springs", sc.copy_in(sp, "springs.json"))
    elif (sc.dir / "springs.json").is_file():
        (sc.dir / "springs.json").unlink()
    packs = pack_texture_maps(textures, sc.dir, name)
    for key, p in packs.items():
        sc.register(key, p)
    # Hair as its own material: its maps and layout from the clean-up step, and its own packs.
    for stale in sc.dir.glob("hair_*.png"):
        stale.unlink()
    hair_in = hair_inputs(sc, keep_normal)
    hair_packs: dict[str, Path] = {}
    if hair_in:
        for key, p in hair_in.items():
            sc.register(key, p)
        hp = pack_texture_maps({k[5:]: hair_in.get(k) for k in ("hair_roughness", "hair_ao")}, sc.dir, name + "_Hair")
        hair_packs = {f"hair_{k}": p for k, p in hp.items()}
        for key, p in hair_packs.items():
            sc.register(key, p)
    sc.progress(0.05, "Blender pack")
    # The rig stage's GLB has no UVs; pack.py copies them from the prep GLB (same faces) when the input lacks them.
    uv_src = sc.lineage_file("glb", ("prep",)) if src_key.startswith("rig.") else None
    sock_cat = socket_category(sc)
    stale = sc.dir / "sockets.json"
    if stale.is_file():
        stale.unlink()
    lib_hair = library_hair(sc) if hair_in.get("hair_mesh") else None
    hair_arg = None
    if hair_in:
        hair_arg = {"layout": hair_in.get("hair_layout"), "base_color": hair_in.get("hair_base_color"),
                    "normal": hair_in.get("hair_normal"), "orm": hair_packs.get("hair_orm")}
        if lib_hair is not None:
            hair_arg.update(library=lib_hair, mesh=hair_in.get("hair_mesh"))
    args = pack_args(src, sc.dir, name, lods, pack_mode, formats, look, col_plan.get("obj") if pack_mode == "parts" else None,
                     textures.get("base_color"), textures.get("normal"), packs.get("orm"), uv_src, sock_cat, hair_arg)
    await sc.blender("pack.py", args, log_name="pack.log")
    report = json.loads((sc.dir / "report.json").read_text())
    hr = report.get("hair_material") or None
    if hair_in:
        glbs = [sc.dir / f"{name}.glb", sc.dir / f"{name}_godot.glb"] + [Path(p) for p in (report.get("glb_lods") or {}).values()]
        aniso = hair_glb_finish(glbs, hair_in.get("hair_flow"), sc.dir) if hr and hr.get("ok") else []
        block = {"planned": True, "ok": bool(hr and hr.get("ok")), "faces": (hr or {}).get("faces"),
                 "lod_triangles": (hr or {}).get("lod_triangles"), "slots": (hr or {}).get("slots"),
                 "anisotropy_glbs": aniso, "draws_added": hairmat.DRAWS_ADDED,
                 "error": None if hr and hr.get("ok") else ((hr or {}).get("reason") or "pack.py did not split the hair")}
        if lib_hair is not None:
            block.update(source="library", alpha=lib_hair.get("alpha") or "opaque", two_sided=bool(lib_hair.get("two_sided")),
                         kind=lib_hair.get("kind"), cap_faces=(hr or {}).get("cap_faces"), lods_body=report.get("lods_body"))
            if not hr:
                block["error"] = "pack.py did not find the hair object"
        sc.set_metrics(hair_material=block)
    else:
        sc.set_metrics(hair_material=None)
    sc.register("pack_report", sc.dir / "report.json")
    if (sc.dir / f"{name}.fbx").is_file():
        sc.register("fbx", sc.dir / f"{name}.fbx")
    if (sc.dir / f"{name}.glb").is_file():
        sc.register("glb", sc.dir / f"{name}.glb")
    if (sc.dir / f"{name}_godot.glb").is_file():
        sc.register("glb_godot", sc.dir / f"{name}_godot.glb")
    if (sc.dir / f"{name}_unreal.fbx").is_file():
        sc.register("fbx_unreal", sc.dir / f"{name}_unreal.fbx")
    for level, p in sorted((report.get("glb_lods") or {}).items()):
        if Path(p).is_file():
            sc.register(f"glb_{level.lower()}", p)
    _write_colliders(sc, col_plan, report)
    _write_sockets(sc, name, report)
    sc.set_metrics(lods=lod_list(report.get("lods") or {}), collider_verts=report.get("collider_verts"), bones=report.get("bones"), clips=report.get("clips") or [],
                   non_manifold_edges=report.get("non_manifold_edges"), boundary_edges=report.get("boundary_edges"), dims=report.get("dims_m"),
                   collider_count=report.get("collider_count", 0), collider_shape=col_plan.get("chosen"),
                   collider_kinds=sorted({p.get("kind") for p in report.get("collider_parts") or []}),
                   collider_triangles_max=report.get("collider_triangles_max"), collider_seconds=col_plan.get("seconds"),
                   openings=col_plan.get("openings"), collider_error=col_plan.get("error"))
    return report


def _speed_scale(sc: StageContext, humanoid: bool) -> float | None:
    """G33: the locomotion blend tree puts walk at 1.4 m/s and run at 3.5 m/s for a 1.8 m person; a rigged creature's
    speeds are scaled by its size / 1.8. None for humanoids and unrigged assets (the importer then uses 1)."""
    if humanoid:
        return None
    rig_v = sc.stage_version("rig")
    if not ((getattr(rig_v, "metrics", None) or {}).get("bones") or 0):
        return None
    prep_v = sc.stage_version("prep")
    prep_params = getattr(prep_v, "params", None) or {}
    size = prep_params.get("size_m") or prep_params.get("height_m") or (getattr(sc, "category", None) or {}).get("size_m")
    try:
        size = float(size)
    except (TypeError, ValueError):
        return None
    return size / 1.8 if size > 0 else None


def _unity_args(sc: StageContext, params: dict[str, Any], name: str) -> tuple[dict[str, Any], list[str], list[str]]:
    """Arguments and flags for VgacImport.Run, the same for the sandbox import and the project import (CODE-AUDIT
    D7): -vgacInput, -vgacName, -nographics, -vgacHumanoid for a rigged humanoid, -vgacClips when the humanoid's
    clip library exists, and -vgacSpeedScale for a rigged creature. -> (args, flags, warnings)."""
    humanoid = _is_humanoid(sc)
    flags = ["-nographics"] + (["-vgacHumanoid"] if humanoid else [])
    args: dict[str, Any] = {"-vgacInput": sc.dir, "-vgacName": name}
    # The category drives the lightmap UVs and static flags (G17) and the cross-fade (G20); colliders.json the kind of
    # each collision part (G16).
    category = getattr(getattr(sc, "asset", None), "category", None)
    if category:
        args["-vgacCategory"] = category
    cj = Path(sc.dir) / "colliders.json"
    if cj.is_file():
        args["-vgacColliders"] = cj
    scale = _speed_scale(sc, humanoid)
    if scale is not None:
        args["-vgacSpeedScale"] = f"{scale:.3f}"
    args.update(engine_args(sc))
    warnings: list[str] = []
    # Hit boxes (G34): the rig stage's hitboxes.json, copied next to the FBX by _pack; the body shape for moving is
    # the export knob movement_shape (auto takes the file's movement.default).
    hb = Path(sc.dir) / "hitboxes.json"
    if hb.is_file():
        args["-vgacHitboxes"] = hb
        shape = params.get("movement_shape") or "auto"
        args["-vgacMovementShape"] = shape if shape in MOVEMENT_SHAPES else "auto"
    ci = Path(sc.dir) / "anim_clips.json"
    if ci.is_file():
        args["-vgacClipInfo"] = ci
    sj = Path(sc.dir) / "sockets.json"
    if sj.is_file():
        args["-vgacSockets"] = sj
    hm = (sc.version.metrics.get("hair_material") or {}) if (Path(sc.dir) / "hair_base_color.png").is_file() else {}
    if hm.get("ok"):
        args["-vgacHair"] = "on"  # the second material from the hair's own textures (VgacImport.cs Version 13)
        if hm.get("source") == "library":
            # hair from the library: cut out and two-sided on VGAC/HairCutout (Version 17)
            if hm.get("alpha") == "cutout":
                args["-vgacHairCutout"] = "on"
            if hm.get("two_sided"):
                args["-vgacHairTwoSided"] = "on"
    spj = Path(sc.dir) / "springs.json"
    if spj.is_file() and sc.version.files.get("springs"):
        args["-vgacSprings"] = spj  # spring bones: the importer adds VgacSpringBones to the prefab (Version 15)
    aj = Path(sc.dir) / "attachments.json"
    if aj.is_file() and sc.version.files.get("attachments"):
        args["-vgacAttachments"] = aj  # items: the second prefab "<Name> (equipped)"
    clips_lib = params.get("clips_library", "ual1")
    if humanoid and clips_lib and clips_lib != "none":
        lib = sc.settings.clips_libraries.get(clips_lib)
        if lib and Path(lib).is_file():
            args["-vgacClips"] = lib
        else:
            warnings.append(f"clips library {clips_lib!r} not found")
    sky = showcase_sky()
    if sky is not None:
        args["-vgacHdri"] = sky  # the showcase scene's sky and light, sun clipped (beauty.py)
    return args, flags, warnings


def unity_metrics(report: dict[str, Any]) -> dict[str, Any]:
    """The Unity report as the version's metrics carry it (metrics.unity_report)."""
    return {"ok": bool(report.get("ok")), "prefab": report.get("prefab", ""), "avatar": report.get("avatar", ""),
            "warnings": list(report.get("warnings") or []), "errors": [report["error"]] if report.get("error") else [],
            "humanoidValid": report.get("humanoidValid"), "lodCount": report.get("lodCount"), "lodTriangles": report.get("lodTriangles"),
            "bones": report.get("bones"), "colliderVertices": report.get("colliderVertices"),
            "texturesAssigned": report.get("texturesAssigned"), "clipCount": report.get("clipCount"), "clips": report.get("clips"),
            "importer_version": report.get("importer_version"), "avatar_mapping": report.get("avatar_mapping"),
            "avatar_mapping_by": report.get("avatar_mapping_by"), "shader": report.get("shader"),
            "controller": report.get("controller"), "hitboxCount": report.get("hitboxCount"), "movementShape": report.get("movementShape"),
            "hitboxMaxBoneError": report.get("hitboxMaxBoneError"), "colliderCount": report.get("colliderCount"),
            "colliderKinds": report.get("colliderKinds"), "colliderTrianglesMax": report.get("colliderTrianglesMax"),
            "colliderBoundsError": report.get("colliderBoundsError"),
            "secondaryUV": report.get("secondaryUV"), "secondaryUVOnMesh": report.get("secondaryUVOnMesh"),
            "secondaryUVSettings": report.get("secondaryUVSettings"),
            "staticFlags": report.get("staticFlags"), "lodFadeMode": report.get("lodFadeMode"),
            "lodFadeWidth": report.get("lodFadeWidth"), "textureFilter": report.get("textureFilter"),
            "textureMipmaps": report.get("textureMipmaps"), "textureCompression": report.get("textureCompression"),
            "normalMap": report.get("normalMap"), "textureSettings": report.get("textureSettings"),
            "textureSizes": report.get("textureSizes"),
            "sockets": report.get("sockets"), "socketBones": report.get("socketBones"),
            "socketMaxError": report.get("socketMaxError"), "socketMaxAngle": report.get("socketMaxAngle"),
            "equippedPrefab": report.get("equippedPrefab"), "equippedItems": report.get("equippedItems"),
            "showcase": report.get("showcase"), "hairMaterial": report.get("hairMaterial"), "hairShader": report.get("hairShader"),
            "hairSubmeshes": report.get("hairSubmeshes"), "hairTriangles": report.get("hairTriangles"),
            "hairTexturesAssigned": report.get("hairTexturesAssigned"),
            "springChains": report.get("springChains"), "springBones": report.get("springBones"),
            "springWarning": report.get("springWarning"),
            "clipFacing": report.get("clipFacing"), "facingMeasured": report.get("facingMeasured"),
            "bindFacing": report.get("bindFacing"),
            # Version 17, only when the hair is cut out or two-sided (hair from the library)
            **{k: report[k] for k in ("hairCutout", "hairTwoSided") if report.get(k)}}


async def _unity(sc: StageContext, params: dict[str, Any], name: str) -> dict[str, Any] | None:
    if not params.get("unity", True):
        return None
    if not sc.settings.unity_exe or not Path(sc.settings.unity_exe).is_file():
        sc.set_metrics(unity_error="unity_exe not configured")
        return None
    if sc.file("fbx") is None:
        sc.set_metrics(unity_error="no FBX to import")
        return None
    args, flags, warnings = _unity_args(sc, params, name)
    if "-vgacClips" in args:
        sc.use_model(params.get("clips_library", "ual1"))
    for w in warnings:
        sc.set_metrics(clips_warning=w)
    sc.use_model("unity")
    sc.progress(0.5, "Unity sandbox import")
    report: dict[str, Any] | None = None
    try:
        await sc.unity("VgacImport.Run", args, flags, "unity_import.log")
    except Exception as e:
        sc.set_metrics(unity_error=str(e)[-800:])
    rp = sc.dir / "unity_report.json"
    if rp.is_file():
        report = json.loads(rp.read_text(encoding="utf-8"))
        sc.register("unity_report", rp)
        sc.set_metrics(unity_report=unity_metrics(report))
    if report and report.get("ok"):
        await _unity_snapshot(sc, params, name)
    return report


def snapshot_args(sc: Any, name: str, snap_dir: Path) -> dict[str, Any]:
    """VgacImport.Snapshot's arguments. -vgacHairLibrary on only for hair from the library (its close-ups are framed on
    the hairstyle's own bounds); otherwise the arguments are as they always were."""
    args: dict[str, Any] = {"-vgacName": name, "-vgacOut": snap_dir, "-vgacClipFilter": "walk,idle", "-vgacFrames": 4, "-vgacSize": 384,
                            **({"-vgacHdri": sky} if (sky := showcase_sky()) else {})}
    if ((getattr(sc, "version", None) and sc.version.metrics.get("hair_material")) or {}).get("source") == "library":
        args["-vgacHairLibrary"] = "on"
    return args


async def _unity_snapshot(sc: StageContext, params: dict[str, Any], name: str) -> None:
    snap_dir = sc.dir / "unity_snapshots"
    try:
        sc.progress(0.7, "Unity clip snapshots")
        await sc.unity("VgacImport.Snapshot", snapshot_args(sc, name, snap_dir), [], "unity_snapshot.log", timeout=1800)
    except Exception as e:
        sc.set_metrics(unity_snapshot_error=str(e)[-600:])
    rp = snap_dir / "snapshot_report.json"
    if rp.is_file():
        rep = json.loads(rp.read_text(encoding="utf-8"))
        files = [Path(f) for f in rep.get("files") or [] if Path(f).is_file()]
        if files:
            # Four frames per clip, then the loop probe at 1.5 times the clip's length.
            strip = contact_sheet(files, sc.dir / "unity_strip.png", cell=256, columns=5, labels=[f.stem for f in files])
            sc.register("unity_strip", strip)
            sc.set_metrics(unity_snapshot_frames=len(files), unity_loop_probes=list(rep.get("loopProbes") or []))


async def _sprites(sc: StageContext, params: dict[str, Any]) -> None:
    if not params.get("sprites", False):
        return
    src = sc.lineage_file("anim_glb", ("rig",)) or sc.lineage_file("glb", ("prep",)) or sc.file("glb")
    if src is None:
        return
    directions = int(params.get("sprite_directions") or 8)
    frames = int(params.get("sprite_frames") or 8)
    size = int(params.get("sprite_size") or 128)
    out = sc.dir / "sprites"
    args: list[Any] = [src, out, directions, frames, size]
    if sc.asset.style_preset == "pixel":
        args.append("--pixel")
    sc.progress(0.8, "sprite sheets")
    await sc.blender("sprites.py", args, log_name="sprites.log", timeout=3600)
    atlas = pack_sprites(out)
    sc.register("sprites_dir", out / "atlas.json")
    sc.register("sprite_atlas", out / "atlas.json")
    for i, (clip, info) in enumerate(atlas.get("clips", {}).items()):
        sc.register(f"sprite_{clip}", out / info["sheet"])
        if i == 0:
            sc.register("sprite_sheet", out / info["sheet"])
    shutil.rmtree(out / "_frames", ignore_errors=True)
    sc.set_metrics(sprites={"directions": directions, "frames": frames, "size": size, "clips": list(atlas.get("clips", {}))})


def _glb_files(sc: StageContext) -> dict[str, str]:
    out = {}
    for key in sorted(sc.version.files):
        if key in ("glb", "glb_godot") or re.fullmatch(r"glb_lod\d+", key):
            p = sc.file(key)
            if p is not None:
                out[key] = Path(p).name if key in ("glb", "glb_godot") else f"lods/{Path(p).name}"
    return out


def _hitbox_manifest(sc: StageContext, unity_report: dict[str, Any] | None) -> dict[str, Any] | None:
    """What the manifest says about hit boxes (G34): the file, what Unity built, and the Rigidbody note."""
    p = sc.file("hitboxes")
    if p is None:
        return None
    u = unity_report or {}
    return {"file": Path(p).name, "count": u.get("hitboxCount"), "movement_shape": u.get("movementShape"),
            "triggers": True, "note": HITBOX_NOTE}


def _collision_manifest(sc: StageContext, pack_report: dict[str, Any] | None, unity_report: dict[str, Any] | None) -> dict[str, Any]:
    """What the manifest says about collision (G16): the shape chosen, the parts and how each engine gets them."""
    pr = pack_report or {}
    plan = _read_json(sc.file("colliders"))
    u = unity_report or {}
    parts = pr.get("collider_parts") or []
    out: dict[str, Any] = {"mode": sc.version.params.get("collider"), "shape": plan.get("chosen"), "count": len(parts),
                           "parts": parts, "triangles_max": pr.get("collider_triangles_max"), "vertices_total": pr.get("collider_verts"),
                           "openings": plan.get("openings"), "unity": None, "godot": None, "unreal": None}
    if parts:
        out["godot"] = f"{len(parts)} -convcolonly or -colonly mesh(es) in the _godot.glb"
        if pr.get("unreal_collision"):
            out["unreal"] = {"file": Path(pr.get("fbx_unreal", "")).name, "ucx": pr.get("unreal_collision"), "verified": False}
    if u:
        out["unity"] = {"count": u.get("colliderCount"), "kinds": u.get("colliderKinds"), "triangles_max": u.get("colliderTrianglesMax"),
                        "where": "Colliders child of the prefab root"}
    return out


def _lighting_manifest(sc: StageContext, unity_report: dict[str, Any] | None) -> dict[str, Any] | None:
    """G17 and G20 in the manifest: lightmap UVs and static flags for scenery and still props, cross-fade for foliage."""
    cat = sc.asset.category
    u = unity_report or {}
    out: dict[str, Any] = {}
    if cat in LIGHTMAP_CATEGORIES:
        out["lightmap_uv"] = {"generated": bool(u.get("secondaryUV")) if u else None, "on_mesh": u.get("secondaryUVOnMesh"),
                              "by": "Unity ModelImporter.generateSecondaryUV (UV1)",
                              "settings": u.get("secondaryUVSettings") or LIGHTMAP_SETTINGS, "static_flags": u.get("staticFlags"),
                              "unreal": "Unreal generates lightmap UVs on import; none written for it"}
    if cat in CROSSFADE_CATEGORIES:
        out["lod_fade"] = {"mode": u.get("lodFadeMode") or "CrossFade", "transition_width": u.get("lodFadeWidth") or CROSSFADE_WIDTH,
                           "note": CROSSFADE_NOTE}
    return out or None


HAIR_FLOW_LAYOUT = ("RGBA; linear; R the strand direction along the texture's width, G along its height (up, OpenGL convention), "
                    "each 0.5 + 0.5 x; B 1 (strength); A strand noise for the highlight's shift (0.5 none)")


def _hair_manifest(sc: StageContext, unity_report: dict[str, Any] | None) -> dict[str, Any] | None:
    """What the manifest says about the hair's own material, or None when the hair stays in the body material."""
    m = sc.version.metrics.get("hair_material") or {}
    if not m.get("ok"):
        return None
    prep_m = (getattr(sc.stage_version("prep"), "metrics", None) or {}).get("hair_material") or {}
    files = {k: Path(p).name for k in HAIR_KEYS + HAIR_PACK_KEYS if (p := sc.file(k)) is not None}
    u = unity_report or {}
    if m.get("source") == "library":
        return _library_hair_manifest(sc, m, files, u)
    return {"materials": m.get("slots"), "texture_size": prep_m.get("tex"), "texels_per_cm": prep_m.get("texels_per_cm"),
            "texels_per_cm_in_body_texture": prep_m.get("texels_per_cm_body_texture"), "triangles": m.get("faces"),
            "lod_triangles": m.get("lod_triangles"), "draw_calls_added": m.get("draws_added"), "files": files,
            "flow_map": files.get("hair_flow"), "glb_anisotropy": {"extension": "KHR_materials_anisotropy", "files": m.get("anisotropy_glbs"),
                                                                   "strength": hairmat.ANISOTROPY_STRENGTH},
            "unity": {"material": u.get("hairMaterial"), "shader": u.get("hairShader")} if u else None,
            "readme": "README.txt", "unreal": "by hand: Shading Model Hair (README.txt); not verified",
            "godot": "by hand: Anisotropy on, Flowmap set to the flow map (README.txt); not verified"}


def hair_credit(hl: dict[str, Any] | None) -> dict[str, Any]:
    """The hairstyle's credit from the clean-up step's hair_library metrics: {id, name, author, licence, source_url}."""
    c = (hl or {}).get("credit") or {}
    return {k: c.get(k) for k in ("id", "name", "author", "licence", "source_url")}


def _library_hair_manifest(sc: Any, m: dict[str, Any], files: dict[str, str], u: dict[str, Any]) -> dict[str, Any]:
    """The manifest's hair_material block for hair from the library."""
    hl = (getattr(sc.stage_version("prep"), "metrics", None) or {}).get("hair_library") or {}
    rig_v = sc.stage_version("rig")
    sp = (getattr(rig_v, "metrics", None) or {}).get("springs") or {}
    return {"source": "library", "style": hair_credit(hl), "materials": m.get("slots"), "kind": hl.get("kind"),
            "alpha": m.get("alpha"), "two_sided": m.get("two_sided"), "texture_size": hl.get("tex"),
            "triangles": m.get("faces"), "cap_triangles": hl.get("cap_triangles") or m.get("cap_faces"), "lod_triangles": m.get("lod_triangles"),
            "lod_triangles_body": m.get("lods_body"), "draw_calls_added": m.get("draws_added"), "files": files,
            "flow_map": files.get("hair_flow"),
            "hanging_groups": [c.get("name") for c in sp.get("chains") or [] if c.get("library")],
            "glb_anisotropy": {"extension": "KHR_materials_anisotropy", "files": m.get("anisotropy_glbs"),
                               "strength": hairmat.ANISOTROPY_STRENGTH},
            "unity": {"material": u.get("hairMaterial"), "shader": u.get("hairShader"), "cutout": u.get("hairCutout"),
                      "two_sided": u.get("hairTwoSided")} if u else None,
            "readme": "README.txt",
            "unreal": "by hand: Shading Model Hair, Blend Mode Masked for cut-out hair (README.txt); not verified",
            "godot": "by hand: Transparency Alpha Scissor for cut-out hair (README.txt); not verified"}


def library_readme_lines(hl: dict[str, Any] | None, m: dict[str, Any]) -> list[str]:
    """README.txt lines for hair from the library: the credit and how cut-out hair is set up by hand elsewhere."""
    c = hair_credit(hl)
    where = f", from {c['source_url']}" if c.get("source_url") else ""
    lines = ["Hair from the library (ready-made hairstyles)",
             f"Hairstyle: {c.get('name') or c.get('id') or 'unknown'} by {c.get('author') or 'unknown'}, "
             f"licence {c.get('licence') or 'unknown'}{where}."]
    if m.get("alpha") == "cutout":
        lines.append("The hair is made of hair cards (flat strips with a see-through texture). Its material uses alpha cutout "
                     "(pixels under half opacity are not drawn): in Unity the importer puts it on VGAC/HairCutout; in Unreal "
                     "set Blend Mode to Masked; in Godot set Transparency to Alpha Scissor. Not tried in Unreal or Godot here.")
    if m.get("two_sided"):
        lines.append("The hair is drawn from both sides (two-sided).")
    return lines + [""]


def _read_json(p: Path | str | None) -> dict[str, Any]:
    if p is None or not Path(p).is_file():
        return {}
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def build_manifest(sc: StageContext, name: str, pack_report: dict[str, Any] | None, unity_report: dict[str, Any] | None) -> dict[str, Any]:
    prov = manifest_provenance(sc)
    models, seeds, lineage = prov["models"], prov["seeds"], prov["lineage"]
    rig_v = sc.stage_version("rig")
    textures = {}
    for key in TEXTURE_KEYS + PACK_KEYS + HAIR_KEYS + HAIR_PACK_KEYS:
        p = sc.file(key)
        if p is not None:
            textures[key] = {"file": p.name, "size": list(image_size(p))}
            if key in PACK_LAYOUTS or key[5:] in PACK_LAYOUTS:
                textures[key]["layout"] = PACK_LAYOUTS.get(key) or PACK_LAYOUTS[key[5:]]
            if key == "hair_flow":
                textures[key]["layout"] = HAIR_FLOW_LAYOUT
    return {
        "name": sc.asset.name,
        "export_name": name,
        "asset_id": sc.asset.id,
        "project_id": sc.project.id,
        "version_id": sc.version.id,
        "created_at": _iso(utcnow()),
        "category": sc.asset.category,
        "style_preset": sc.asset.style_preset,
        "style_lock": sc.project.style_lock,
        "platform_target": sc.platform,
        "budget": sc.budget,
        "recipe": sc.version.metrics.get("recipe"),
        "prompt": sc.asset.prompt,
        "parts": sc.asset.parts,
        "triangles_per_lod": (pack_report or {}).get("lods"),
        "lod_ratios": sc.version.params.get("lods"),
        "collider_vertices": (pack_report or {}).get("collider_verts"),
        "collision": _collision_manifest(sc, pack_report, unity_report),
        "lighting": _lighting_manifest(sc, unity_report),
        "lods": lod_list((pack_report or {}).get("lods") or {}),
        "textures": textures,
        "hair_material": _hair_manifest(sc, unity_report),
        "springs": springs_mod.manifest_block(springs_mod.read_doc(sc.file("springs")), unity_report),
        "glb_files": _glb_files(sc),
        "hitboxes": _hitbox_manifest(sc, unity_report),
        "sockets": sockets.manifest_block(_read_json(sc.file("sockets")), unity_report),
        "attachments": _read_json(sc.file("attachments")) or None,
        "held_item": getattr(sc.asset, "held_item", None),
        "grip": _read_json(sc.file("grip")) or None,
        "bones": (pack_report or {}).get("bones"),
        "bone_names": (rig_v.metrics.get("bone_names") if rig_v else None),
        "rigid": bool(rig_v.metrics.get("rigid")) if rig_v else True,
        "rig_template": (rig_v.params.get("template_label") if rig_v else None),
        "clips": (pack_report or {}).get("clips"),
        "unity": unity_report,
        "sprites": sc.version.metrics.get("sprites"),
        "files": sc.version.files,
        "models": list(models.values()),
        "licences": sorted({m.get("licence", "unknown") for m in models.values()}),
        "seeds": seeds,
        "lineage": lineage,
        "checks": sc.version.metrics.get("checks"),
    }


# --- Unity checks (G01, G26, G32, G33) -------------------------------------------------------------------------------

# Plain names of the maps the Unity material should use, in the order the materials_connected check reports them,
# and how the surface looks without each (docs/GREAT-ASSETS-PLAN.md 3.1 B).
UNITY_MAPS = (("base_color", "colour", "grey"), ("normal", "bump", "flat"), ("ao", "shadow", "a little flat"),
              ("metallic_smoothness", "metal and shine", "like plastic"))
LOCOMOTION_ROLES = ("Idle", "Walk", "Run")


def _check(id_: str, ok: bool, value: Any, limit: Any, vars: dict[str, Any] | None = None) -> dict[str, Any]:
    """quality.check plus the optional `vars` the check sentences fill in (plan 2.1)."""
    c = {"name": id_, "passed": bool(ok), "message": f"{value} (limit {limit})", "id": id_, "ok": bool(ok), "value": value, "limit": limit}
    if vars:
        c["vars"] = vars
    return guidance.describe_check(c)


def _role_clips(u: dict[str, Any]) -> dict[str, str]:
    """{"Idle": "Rig|Idle_Loop", ...} from the controller's roles list ("Idle=Rig|Idle_Loop")."""
    out = {}
    for r in (u.get("controller") or {}).get("roles") or []:
        role, _, clip = str(r).partition("=")
        out[role] = clip
    return out


# Hit boxes and attachment points may land this far from their fitted place in the Unity prefab, in metres.
PLACEMENT_MAX_M = 0.02


def unity_checks(u: dict[str, Any], humanoid: bool, rigged: bool, expect_normal: bool = True) -> list[dict[str, Any]]:
    """Checks from a Unity report that imported (u["ok"]): materials_connected always (without the bump map when the
    recipe's look has none); clips_loop and controller_ready when the asset has clips; unity_humanoid_avatar for
    humanoids."""
    checks: list[dict[str, Any]] = []
    assigned = set(u.get("texturesAssigned") or [])
    wanted = [m for m in UNITY_MAPS if expect_normal or m[0] != "normal"]
    missing = next(((plain, effect) for key, plain, effect in wanted if key not in assigned), None)
    checks.append(_check("materials_connected", missing is None, len(assigned & {k for k, _, _ in wanted}), len(wanted),
                         {"missing": missing[0] if missing else "", "effect": missing[1] if missing else "",
                          **({} if expect_normal else {"no_bumps": True})}))
    sizes = texture_import_sizes(u)
    if sizes:
        short = [(name, f, m) for name, f, m in sizes if m < f]
        top = max(sizes, key=lambda x: x[1])
        checks.append(_check("textures_full_size", not short, (short[0] if short else top)[2], (short[0] if short else top)[1],
                             {"texture": (short[0] if short else top)[0].replace(".png", "").replace("_", " "),
                              "file_px": str((short[0] if short else top)[1]), "import_px": str((short[0] if short else top)[2])}))
    clips = [c for c in (u.get("clips") or []) if isinstance(c, dict)]
    if rigged and clips:
        loops = {c.get("name"): bool(c.get("loop")) for c in clips}
        roles = _role_clips(u)
        cycle = [(role, roles[role]) for role in LOCOMOTION_ROLES if role in roles]
        broken = [(role, clip) for role, clip in cycle if not loops.get(clip, False)]
        value = broken[0][0].lower() if broken else ", ".join(r.lower() for r, _ in cycle)
        checks.append(_check("clips_loop", bool(cycle) and not broken, value, True,
                             {"clip": broken[0][1] if broken else ""}))
        ctrl = u.get("controller") or {}
        params = [str(p).split(":")[0] for p in ctrl.get("parameters") or []]
        ready = "Locomotion" in (ctrl.get("states") or []) and "Speed" in params
        checks.append(_check("controller_ready", ready, len(ctrl.get("states") or []), True,
                             {"states": ", ".join(ctrl.get("states") or []), "parameters": ", ".join(params)}))
    if humanoid:
        valid = bool(u.get("humanoidValid"))
        checks.append(_check("unity_humanoid_avatar", valid, u.get("clipCount") if valid else 0, True,
                             {"mapping": u.get("avatar_mapping") or ""}))
    facing = clip_facing(u)
    if facing is not None:
        worst = facing["worst"]
        checks.append(_check("clips_face_forward", not facing["over"], round(abs(worst["degrees"])), FACING_MAX_DEG,
                             {"clip": worst["plain"], "role": worst["role"], "side": worst["side"],
                              "others": max(0, len(facing["over"]) - 1), "moves": facing["count"]}))
    return checks


# A move may turn the body this far from the prefab's forward (averaged over the move) before the hand-off check
# fails. Authored stances turn up to about 30 degrees (a bladed sword stance); a move imported backwards is 125 to 180.
FACING_MAX_DEG = 60


def plain_clip_name(name: str) -> str:
    """"Rig|Idle_Loop" -> "idle loop"; "walk" -> "walk"."""
    short = str(name).rsplit("|", 1)[-1]
    return re.sub(r"[_\s]+", " ", short).strip().lower() or str(name)


def clip_facing(u: dict[str, Any]) -> dict[str, Any] | None:
    """The importer's clipFacing (Version 16) for the moves the controller plays: {worst, over, count}, where worst
    is the measured move turned furthest from the prefab's forward ({name, plain, degrees, role, side}) and over the
    moves past FACING_MAX_DEG, worst first. None when nothing was measured (Generic rigs, importers before 16)."""
    if not u.get("facingMeasured"):
        return None
    rows = [f for f in u.get("clipFacing") or [] if isinstance(f, dict) and f.get("measured") and f.get("inController")]
    if not rows:
        return None
    roles = {clip: role for role, clip in _role_clips(u).items()}

    def entry(f: dict[str, Any]) -> dict[str, Any]:
        deg = float(f.get("degrees") or 0.0)
        return {"name": f.get("name", ""), "plain": plain_clip_name(f.get("name", "")), "degrees": deg,
                "role": roles.get(f.get("name", ""), "").lower(), "side": "right" if deg > 0 else "left",
                "based_on": f.get("basedOn") or ""}

    ranked = sorted((entry(f) for f in rows), key=lambda e: -abs(e["degrees"]))
    return {"worst": ranked[0], "over": [e for e in ranked if abs(e["degrees"]) > FACING_MAX_DEG], "count": len(ranked)}


def texture_import_sizes(u: dict[str, Any]) -> list[tuple[str, int, int]]:
    """The importer's textureSizes ("base_color.png:4096:4096": the file's size, then the largest size Unity imports it
    at), parsed; [] from importers before Version 12."""
    out = []
    for e in u.get("textureSizes") or []:
        parts = str(e).rsplit(":", 2)
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            out.append((parts[0], int(parts[1]), int(parts[2])))
    return out


def unity_howto(prefab: str, controller: dict[str, Any] | None) -> list[str]:
    """The three "Using it in Unity" lines (plan 3.3 B) for an asset with an animation controller; [] without one.
    Only the one-off moves the controller has are named."""
    if not controller or "Locomotion" not in (controller.get("states") or []):
        return []
    added = [str(p) for p in controller.get("added") or []]  # moves the person added ("Add a move", moves.py)
    added_names = {p.split(":")[0] for p in added}
    triggers = [str(p).split(":")[0] for p in controller.get("parameters") or [] if str(p).endswith(":Trigger") and str(p).split(":")[0] not in added_names]
    lines = [f"Drag the prefab from {prefab} into your scene."]
    params = [str(p).split(":")[0] for p in controller.get("parameters") or []]
    if "Speed" in params:
        lines.append('In your movement script, set the speed every frame: animator.SetFloat("Speed", speed);')
    if triggers:
        first, rest = triggers[0], triggers[1:]
        extra = f' (or {", ".join(chr(34) + t + chr(34) for t in rest)})' if rest else ""
        lines.append(f'For one-off moves, call animator.SetTrigger("{first}");{extra}.')
    if "Flying" in params:
        lines.append('For flight, set animator.SetBool("Flying", true); set it back to false to land.')
    if added:
        one = [p.split(":")[0] for p in added if p.endswith(":Trigger")]
        held = [p.split(":")[0] for p in added if p.endswith(":Bool")]
        parts = ([f'animator.SetTrigger("{one[0]}") plays a move you added once'] if one else []) + \
                ([f'animator.SetBool("{held[0]}", true) plays a looping one until you set it to false'] if held else [])
        more = len(added) - len(parts)
        lines.append("For the moves you added, " + " and ".join(parts) + (f"; the other {more} work the same way, named after the move." if more > 0 else "."))
    return lines


def socket_howto(names: list[str], humanoid: bool) -> str | None:
    """The "Using it in Unity" line for attachment points: one line of code that parents an item to a socket (the
    right hand when there is one). None without sockets."""
    if not names:
        return None
    if humanoid and "Socket_Hand_R" in names:
        return ('To hold an item, parent it to an attachment point: '
                'sword.transform.SetParent(animator.GetBoneTransform(HumanBodyBones.RightHand).Find("Socket_Hand_R"), false);')
    first = "Socket_Back" if "Socket_Back" in names else names[0]
    return ('To attach an item, parent it to an attachment point (needs using System.Linq): '
            f'item.transform.SetParent(GetComponentsInChildren<Transform>().First(t => t.name == "{first}"), false);')


def collider_checks(report: dict[str, Any], plan: dict[str, Any], category: str | None, mode: Any = None) -> list[dict[str, Any]]:
    """Collision checks (plan 3.4 B; replaces quality.collider_checks for the export stage):
    collider_present (block) unless the shape is none; collider_triangles, the most triangles in one convex part
    against Unity's 255; collider_count, the number of shapes against the platform's most (with the vertex total);
    openings_clear for prop_rigid, environment and vehicle when the openings were measured."""
    if "none" in (plan.get("chosen"), report.get("collider_mode"), mode):
        return []
    parts = report.get("collider_parts")
    if parts is None:  # a pack report from before G16: one hull, vertices only
        verts = report.get("collider_verts", 0) or 0
        return [quality.check("collider_present", verts > 0, verts, ">0")]
    count = len(parts)
    checks = [quality.check("collider_present", count > 0, count, ">0")]
    convex = [p for p in parts if p.get("kind") != "mesh"]
    if convex:
        worst = max(int(p.get("triangles") or 0) for p in convex)
        checks.append(quality.check("collider_triangles", worst <= UNITY_CONVEX_TRIANGLES, worst, UNITY_CONVEX_TRIANGLES,
                                    {"parts": len(convex)}))
        cap = plan.get("cap") or colliders.max_parts(category, None)
        checks.append(quality.check("collider_count", len(convex) <= cap, len(convex), cap,
                                    {"vertices": sum(int(p.get("vertices") or 0) for p in convex)}))
    op = plan.get("openings")
    if category in colliders.OPENINGS_CATEGORIES and op:
        checks.append(quality.check("openings_clear", bool(op.get("ok")), int(op.get("blocked_openings") or 0), 0,
                                    {"openings": int(op.get("openings") or 0), "blocked": f"{round(100 * float(op.get('blocked_fraction') or 0))}%",
                                     "category": category}))
    return checks


def hair_shipped_checks(m: dict[str, Any], glb: Path | str | None) -> list[dict[str, Any]]:
    """hair_material_shipped (warn) when the clean-up step gave the hair its own material: the clean GLB has two
    materials, the FBX the two named slots, and (when Unity ran) Unity built the hair material. Nothing otherwise."""
    hm = (m or {}).get("hair_material") or {}
    if not hm.get("planned"):
        return []
    n = 0
    try:
        n = len(hairmat.glb_materials(glb)) if glb is not None and Path(glb).is_file() else 0
    except Exception:  # noqa: BLE001
        n = 0
    u = (m or {}).get("unity_report") or {}
    problem = ""
    if not hm.get("ok"):
        problem = str(hm.get("error") or "the hair triangles were not matched")
    elif u.get("ok") and not u.get("hairMaterial"):
        problem = "Unity did not build the hair material"
    ok = hm.get("ok") and n >= 2 and not problem
    vars_ = {"slots": ", ".join(hm.get("slots") or []), "shader": u.get("hairShader") or "", "problem": problem}
    if hm.get("source"):
        vars_["source"] = hm["source"]
    return [quality.check("hair_material_shipped", bool(ok), n, 2, vars=vars_)]


def glb_node_names(glb: Path | str | None) -> list[str]:
    """Node names in a GLB's JSON chunk (bones are nodes); [] when the file is missing or unreadable."""
    try:
        data = Path(glb).read_bytes() if glb is not None and Path(glb).is_file() else b""
        if len(data) < 20:
            return []
        n = int.from_bytes(data[12:16], "little")
        doc = json.loads(data[20:20 + n].decode("utf-8"))
        return [str(x.get("name") or "") for x in doc.get("nodes") or []]
    except Exception:  # noqa: BLE001
        return []


def springs_shipped_checks(sc: Any) -> list[dict[str, Any]]:
    """springs_shipped (warn) when the rig step made spring bones: every chain's bones are in the clean GLB and the FBX,
    springs.json is in the files, and (when Unity ran) the prefab has the spring script on its chains. Nothing
    otherwise."""
    rig_v = sc.stage_version("rig") if hasattr(sc, "stage_version") else None
    sm = ((getattr(rig_v, "metrics", None) or {}).get("springs") or {})
    if not sm.get("made"):
        return []
    doc = springs_mod.read_doc(sc.file("springs"))
    chains = doc.get("chains") or []
    want = [b for c in chains for b in c.get("bones") or []]
    names = set(glb_node_names(sc.file("glb")))
    fbx = sc.file("fbx")
    fbx_bytes = Path(fbx).read_bytes() if fbx is not None and Path(fbx).is_file() else b""
    problem = ""
    if not chains:
        problem = "springs.json is missing"
    elif any(b not in names for b in want):
        problem = "the GLB lacks some of the chain bones"
    elif fbx_bytes and any(b.encode() not in fbx_bytes for b in want):
        problem = "the FBX lacks some of the chain bones"
    u = sc.version.metrics.get("unity_report") or {}
    unity_ok = None
    if u.get("ok"):
        unity_ok = int(u.get("springChains") or 0) == len(chains)
        if not unity_ok and not problem:
            problem = "Unity did not put the spring script on the prefab" + (f": {u['springWarning']}" if u.get("springWarning") else "")
    n = len(chains) if not problem else 0
    return [quality.check("springs_shipped", not problem, n, len(chains) or 1, vars={"unity": bool(unity_ok), "problem": problem})]


async def judge(sc: StageContext) -> None:
    rp = sc.file("pack_report")
    report = json.loads(Path(rp).read_text()) if rp else {}
    checks: list[dict[str, Any]] = []
    rec = export_recipe(sc)
    from .mesh import look_vars, texture_target, with_vars

    prep_m = (getattr(sc.stage_version("prep"), "metrics", None) or {})
    tri_limit, tri_vars = sc.budget["triangles"] or None, None
    if rec is not None and prep_m.get("triangle_limit_source") == "recipe" and prep_m.get("triangle_limit"):
        tri_limit, tri_vars = int(prep_m["triangle_limit"]), {**look_vars(rec, sc.budget), "recipe": True}
    tex_limit, tex_vars = texture_target(rec, sc.budget) if rec is not None else (sc.budget["texture"], None)
    checks += with_vars(quality.geometry_checks(report, tri_limit), tri_vars, "triangle_budget")
    if report.get("lods_body"):
        # hair from the library: the LOD ratios are measured on the body (hair cards keep their count at LOD1)
        checks += quality.lod_checks({**report, "lods": report["lods_body"]}, sc.version.params.get("lods"))
    else:
        checks += quality.lod_checks(report, sc.version.params.get("lods"))
    checks += collider_checks(report, _read_json(sc.file("colliders")), sc.asset.category, sc.version.params.get("collider"))
    checks += with_vars(quality.texture_checks({k: library.version_file(sc.version, k, sc.settings) for k in TEXTURE_KEYS}, tex_limit),
                        tex_vars, "_budget")
    rig_v = sc.stage_version("rig")
    rigged = False
    if rig_v is not None:
        info_path = library.version_file(rig_v, "rig_info", sc.settings)
        if rig_v.metrics.get("rigid"):
            checks += quality.rig_checks({"bones": report.get("bones", 0)}, rig_expected=False)
        elif info_path is not None:
            checks += quality.rig_checks(json.loads(Path(info_path).read_text()), rig_expected=True,
                                         group_per_vertex=rig_v.params.get("group_per_vertex"))
        rigged = bool(report.get("bones")) and not rig_v.metrics.get("rigid")
    u = sc.version.metrics.get("unity_report")
    if u and "clipFacing" not in u:
        # Metrics written before the facing fields existed: take them from the report file, when it has them.
        rep = _read_json(sc.file("unity_report"))
        if rep.get("clipFacing") is not None:
            u = {**u, "clipFacing": rep.get("clipFacing"), "facingMeasured": rep.get("facingMeasured"), "bindFacing": rep.get("bindFacing")}
    if u:
        checks.append(quality.check("unity_import", bool(u.get("ok")), u.get("ok"), True))
        if u.get("ok"):
            checks += unity_checks(u, _is_humanoid(sc), rigged, expect_normal=ships_normal(sc))
            # Hit boxes and attachment points must sit where they were fitted: the importer measures how far each
            # landed from its place on the rest pose (30 cm off once, when the file's default pose was not the rest pose).
            off = [float(u[k]) for k in ("hitboxMaxBoneError", "socketMaxError") if isinstance(u.get(k), (int, float))]
            if off and (u.get("hitboxCount") or u.get("sockets")):
                worst = max(off)
                checks.append(quality.check("placement_exact", worst <= PLACEMENT_MAX_M, round(worst, 4), PLACEMENT_MAX_M,
                                            vars={"cm": round(worst * 100, 1)}))
    checks += hair_shipped_checks(sc.version.metrics, sc.file("glb"))
    checks += springs_shipped_checks(sc)
    checks += items.export_checks(sc)  # grip_found, item_clear_of_body
    sc.set_metrics(**quality.summarise(checks))
    # A hand-off to an open Unity Editor finishes later; POST /api/versions/{id}/judge (mode metrics) picks it up.
    picked = collect_unity_import(sc.version)
    if picked is not None:
        sc.set_metrics(unity_project_import=picked)
        try:
            dest = sc.dir / "unity_project_report.json"
            shutil.copy2(picked["report"], dest)
            sc.register("unity_project_report", dest)
        except Exception:  # noqa: BLE001  (the metrics are what matters; the copy is a convenience)
            pass


async def _export(sc: StageContext, params: dict[str, Any]) -> None:
    # The material category never reaches here: stages.module_for routes its export to stages/material.py.
    name = asset_export_name(sc)
    sc.set_params(export_name=name, unity=bool(params.get("unity", True)), clips_library=params.get("clips_library", "ual1"),
                  sprites=bool(params.get("sprites", False)), sprite_directions=int(params.get("sprite_directions") or 8),
                  sprite_size=int(params.get("sprite_size") or 128))
    if items.is_held_item(sc):
        await items.pivot_item(sc)  # the item's own files get their pivot at the grip
    pack_report = await _pack(sc, params, name)
    if not items.is_held_item(sc):
        await items.equip(sc, name)  # attachments.json, the item files and <name>_equipped.glb
    unity_report = await _unity(sc, params, name)
    if unity_report and unity_report.get("ok"):
        howto = unity_howto(unity_report.get("prefab", ""), unity_report.get("controller"))
        line = socket_howto(unity_report.get("sockets") or [], unity_report.get("humanoidValid") is True)
        equipped = items.unity_line(unity_report, name)
        sc.set_metrics(unity_howto=howto + ([line] if line else []) + ([equipped] if equipped else []))
    await _sprites(sc, params)
    await render_beauty(sc)  # the finish card's picture in realistic light (beauty.py)
    await judge(sc)
    finish = getattr(guidance, "finish_lines", None)  # plan 2.4, guidance lane
    if callable(finish):
        sc.set_metrics(finish_lines=finish(sc.asset.category, sc.version.metrics))
    hm_block = _hair_manifest(sc, unity_report)
    readme = sc.dir / "README.txt"
    spring_lines = springs_mod.readme_lines(name, springs_mod.read_doc(sc.file("springs")))
    if hm_block and hm_block.get("source") == "library":
        hl = (getattr(sc.stage_version("prep"), "metrics", None) or {}).get("hair_library") or {}
        spring_lines = library_readme_lines(hl, sc.version.metrics.get("hair_material") or {}) + spring_lines
    if hm_block or spring_lines:
        text = hair_readme(name, {"tex": hm_block.get("texture_size")}, hm_block["files"]) if hm_block else f"{name}\n\n"
        if hm_block and hm_block.get("source") == "library" and (hm_block.get("alpha") == "cutout" or hm_block.get("two_sided")):
            text = text.replace("the VGAC/Hair shader with a stretched highlight", "the VGAC/HairCutout shader, cut out and drawn from both "
                                "sides as the hairstyle needs, with a stretched highlight")
        readme.write_text(text + ("\n" if hm_block and spring_lines else "") + "\n".join(spring_lines), encoding="utf-8")
        sc.register("readme", readme)
    elif readme.is_file():
        readme.unlink()
    manifest = build_manifest(sc, name, pack_report, unity_report)
    if getattr(sc.asset, "base_asset_id", None):
        manifest["variant_of"] = {"asset_id": sc.asset.base_asset_id, "change": sc.asset.variant_change}
    mp = sc.dir / "manifest.json"
    mp.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    sc.register("manifest", mp)
    await _deliver(sc, name, params)
    items.keep_if_equipping(sc)  # a rebuild with the items on is kept
    sc.progress(1.0, "export done")


def delivery_dir(export_root: Path, project_name: str, asset_name: str) -> Path:
    """exports/<project>/<asset>, with both names reduced to [A-Za-z0-9_] so no name can leave the export root."""
    dest = (export_root / slug(project_name) / slug(asset_name)).resolve()
    dest.relative_to(export_root.resolve())  # ValueError if a name ever escaped
    return dest


DELIVER_KEYS = ("fbx", "fbx_unreal", "glb", "glb_godot", "manifest", "sockets", "springs", "attachments", "glb_equipped", "grip", "base_color", "normal", "metallic", "roughness", "ao", "metallic_smoothness", "orm",
                "hair_base_color", "hair_normal", "hair_roughness", "hair_ao", "hair_flow", "hair_metallic_smoothness", "hair_orm", "readme",
                "height", "tile_preview", "sprite_atlas", "unity_strip", "zip")


async def _deliver(sc: StageContext, name: str, params: dict[str, Any]) -> None:
    """Decision D12: always copy the deliverables to exports/<project>/<asset>/; also import into the project's own
    Unity project when one is set (never the sandbox's prefab, a fresh import there)."""
    export_root = (Path(sc.settings.export_path) if sc.settings.export_path else Path(sc.settings.library_path).parent / "exports").resolve()
    dest = delivery_dir(export_root, sc.project.name, sc.asset.name)
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    for key in DELIVER_KEYS:
        src = sc.file(key)
        if src is not None and Path(src).is_file():
            shutil.copy2(src, dest / Path(src).name)
            copied.append(Path(src).name)
    for key, rel in sc.version.files.items():
        if key.startswith("sprite_") and key != "sprite_atlas":
            src = sc.file(key)
            if src is not None and Path(src).is_file():
                shutil.copy2(src, dest / Path(src).name)
                copied.append(Path(src).name)
        elif re.fullmatch(r"glb_lod\d+", key):
            src = sc.file(key)
            if src is not None and Path(src).is_file():
                (dest / "lods").mkdir(exist_ok=True)
                shutil.copy2(src, dest / "lods" / Path(src).name)
                copied.append(f"lods/{Path(src).name}")
    # Items attached to a character: items/<item>/ with each item's own files (pivot at the grip).
    items_src = Path(sc.dir) / "items"
    if items_src.is_dir() and sc.version.files.get("attachments"):
        shutil.rmtree(dest / "items", ignore_errors=True)
        shutil.copytree(items_src, dest / "items")
        copied += [f"items/{p.relative_to(items_src).as_posix()}" for p in sorted(items_src.rglob("*")) if p.is_file()]
    sc.set_metrics(delivered_to=str(dest), delivered_files=copied)
    sc.progress(0.95, f"delivered to {dest}")
    proj_path = getattr(sc.project, "unity_project_path", None)
    if proj_path and params.get("unity", True) and sc.file("fbx") is not None:
        proj = Path(proj_path)
        if not (proj / "Assets").is_dir():
            sc.set_metrics(unity_project_import={"ok": False, "error": f"not a Unity project: {proj}"})
            return
        editor_dir = proj / "Assets" / "Editor"
        editor_dir.mkdir(parents=True, exist_ok=True)
        script_src = Path(sc.settings.unity_project) / "Assets" / "Editor" / "VgacImport.cs" if sc.settings.unity_project else None
        importer = sync_importer(script_src, editor_dir) if script_src else {"action": "no_source", "version": None, "previous": None}
        if script_src is not None and (sc.version.metrics.get("hair_material") or {}).get("ok"):
            # the hair shader travels with the importer (Assets/VgacShaders/VgacHair.shader), when the hair needs it
            importer["hair_shader"] = sync_shader(Path(sc.settings.unity_project) / SHADER_REL, proj / SHADER_REL)
            if (sc.version.metrics.get("hair_material") or {}).get("source") == "library":
                # cut-out and two-sided library hair needs VGAC/HairCutout (Assets/VgacShaders/VgacHairCutout.shader)
                importer["hair_cutout_shader"] = sync_cutout_shader(Path(sc.settings.unity_project) / CUTOUT_SHADER_REL,
                                                                    proj / CUTOUT_SHADER_REL)
        if script_src is not None and sc.version.files.get("springs"):
            # the spring script travels with the importer (Assets/VgacScripts/VgacSpringBones.cs), when there are chains
            importer["spring_script"] = sync_spring_script(Path(sc.settings.unity_project) / SPRING_SCRIPT_REL, proj / SPRING_SCRIPT_REL)
        args, flags, _ = _unity_args(sc, params, name)  # the sandbox import already recorded any clips warning
        unity_ok = bool(sc.settings.unity_exe) and Path(sc.settings.unity_exe).is_file()
        if unity_ok and unity_project_open(proj):
            # The Editor has the project open, so batch mode would be refused: hand the import to the open Editor.
            sc.progress(0.97, f"Unity hand-off to the open project {proj.name}")
            try:
                sc.set_metrics(unity_project_import=handoff_to_editor(sc.dir, proj, name, args, flags, importer, version_id=sc.version.id))
            except Exception as e:  # noqa: BLE001
                sc.set_metrics(unity_project_import={"ok": False, "state": "failed", "project": str(proj), "error": str(e)[-500:], "importer": importer})
            return
        sc.progress(0.97, f"Unity import into {proj.name}")
        # The importer always writes <input>/unity_report.json; the sandbox step already produced one, so move it
        # aside and read only a report this run wrote (B4 in docs/CODE-AUDIT.md).
        rp = sc.dir / "unity_report.json"
        sandbox_rp = sc.dir / "unity_sandbox_report.json"
        if rp.is_file():
            rp.replace(sandbox_rp)
            if sc.version.files.get("unity_report"):
                sc.register("unity_report", sandbox_rp)
        try:
            await sc.unity("VgacImport.Run", args, flags, "unity_project_import.log", project_path=proj)
            if rp.is_file():
                report = json.loads(rp.read_text(encoding="utf-8"))
                rp.replace(sc.dir / "unity_project_report.json")
                sc.register("unity_project_report", sc.dir / "unity_project_report.json")
            else:
                report = {"ok": False, "error": "Unity wrote no report; the project may not compile or the importer script is missing"}
            sc.set_metrics(unity_project_import={"ok": bool(report.get("ok")), "project": str(proj), "prefab": report.get("prefab", ""),
                                                 "error": (report.get("error") or "")[:300], "importer": importer,
                                                 "importer_version": report.get("importer_version")})
        except Exception as e:  # noqa: BLE001
            sc.set_metrics(unity_project_import={"ok": False, "project": str(proj), "error": str(e)[-500:], "importer": importer})


async def run(sc: StageContext, params: dict[str, Any]) -> None:
    await _export(sc, params)


async def refine(sc: StageContext, instruction: str, view: str | None, mask_png: Path | None, params: dict[str, Any]) -> None:
    parent = sc.parent
    merged = {**(parent.params if parent else {}), **params, "instruction": instruction}
    await _export(sc, merged)


async def adjust(sc: StageContext, params: dict[str, Any]) -> None:
    parent = sc.parent
    merged = {**(parent.params if parent else {}), **params}
    await _export(sc, merged)
