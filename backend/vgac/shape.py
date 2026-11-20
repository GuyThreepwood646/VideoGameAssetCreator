"""The Shape tab (WP-26): separate a fused arm from the body, as a new step 4 version.

The browser sends parameters only (operation, side, gap, start, the rig version it was opened on). This module checks
them, reads the model files from the library, and runs the mesh-editing engine (separate.py, WP-25). Two calls:

- preview(): computes where the cut would start on each upper arm from the rig's skeleton. It runs no Blender job and
  writes no version. The page shades the band it names.
- run(): the job behind Apply. It makes a new step 4 (clean-up) version from the clean-up the rig was made from. The
  source version is never changed. A refusal (the engine's `changed` is false) raises, so the job fails with the plain
  reasons and the new version carries no model.

The new version is marked `shaped`, not `painted`: its geometry is new, so it must not claim the rig of the old
geometry (rig.retexture would reuse that rig's weights). Adjust and Refine are refused on it (paintsave.refusal), since
they rebuild the clean-up from the mesh and would drop the cut.

What the version holds besides the model (WP-27):
- Its pictures (base colour, normal, metallic, roughness, ambient occlusion) are the source's, with the caps' texels
  taken from the engine's model (the engine paints every picture under the caps); nothing outside the caps changes.
- shape_rig (rig.CARRY_FILE): the cut model on the rig's skeleton, in the rig's rest pose, with the weights derived
  again round the cut. The next rig run carries the rig over with it (rig.carry) instead of making the weights again.
- shape_caps: caps.json, each cap's texture triangles, so the paint tools can find the caps.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import library, separate
from .db import Version
from .tools import ToolError

OPERATIONS = {"separate": "Separate a fused arm"}
# Metrics a painted or polished version carries that a separated one must not inherit.
_NOT_CARRIED = ("painted", "painted_of", "geometry_of", "polished", "shaped", "shape_of", "shape_log", "elapsed_seconds",
                "checks", "checks_passed", "checks_failed", "gate_ok", "refit_of")
_NOT_CARRIED_PREFIX = ("paint_", "polish_")
REFUSAL_NOTE = "Nothing was changed: the model stays as it was."


class ShapeRefused(ValueError):
    """The engine left the model as it was: the message is the plain reason(s) for the person."""


def parse(params: dict[str, Any] | None) -> dict[str, Any]:
    """The checked parameters of a shape job or preview, or ValueError naming the bad one."""
    params = dict(params or {})
    op = params.get("operation", "separate")
    if op not in OPERATIONS:
        raise ValueError("choose an operation from the list")
    p = separate.check_params(params.get("side", "both"), params.get("gap", separate.DEFAULT_GAP),
                              params.get("start", separate.DEFAULT_START))
    return {"operation": op, **p}


def rig_source(db: Any, rig: Version) -> Version:
    """The step 4 version a rig was made from (the nearest clean-up up its parent chain). ValueError when there is none."""
    cur: Version | None = db.get(Version, rig.parent_version_id) if rig.parent_version_id else None
    seen: set[str] = set()
    while cur is not None and cur.id not in seen:
        if cur.stage == "prep":
            return cur
        seen.add(cur.id)
        cur = db.get(Version, cur.parent_version_id) if cur.parent_version_id else None
    raise ValueError("this rig has no clean-up step to shape")


def _joint(scene: Any, names: list[str]) -> list[float] | None:
    for n in names:
        if n in scene.graph.nodes_geometry or n in scene.graph.nodes:
            m = scene.graph.get(n)[0]
            return [float(x) for x in m[:3, 3]]
    return None


def preview(rig_glb: Path, *, start: float = separate.DEFAULT_START, gap: float = separate.DEFAULT_GAP) -> dict[str, Any]:
    """Where each arm's cut would start, from the rig's skeleton: a plane across the upper arm at `start` of its length
    from the shoulder joint. An estimate for the person to look at: the engine follows the weight line inside the fused
    strip, so the real cut sits near this plane, not exactly on it. Writes nothing."""
    import numpy as np
    import trimesh

    p = separate.check_params("both", gap, start)
    scene = trimesh.load(str(rig_glb))
    if not isinstance(scene, trimesh.Scene):
        return {"arms": {}, "messages": ["The rig has no skeleton to measure the arms from."]}
    arms: dict[str, Any] = {}
    for side, prefix in (("L", "Left"), ("R", "Right")):
        shoulder = _joint(scene, [f"mixamorig:{prefix}Arm", f"{prefix}Arm"])
        elbow = _joint(scene, [f"mixamorig:{prefix}ForeArm", f"{prefix}ForeArm"])
        if shoulder is None or elbow is None:
            continue
        s, e = np.asarray(shoulder), np.asarray(elbow)
        u = e - s
        length = float(np.linalg.norm(u))
        if length < 1e-6:
            continue
        axis = u / length
        at = s + p["start"] * u
        arms[side] = {"shoulder": shoulder, "elbow": elbow, "upper_arm_m": round(length, 4),
                      "cut_start": [float(x) for x in at], "axis": [float(x) for x in axis],
                      "half_band_m": round(max(p["gap"], 0.01) / 2, 4),
                      "radius_m": round(0.35 * length, 4)}
    messages = []
    for side, a in sorted(arms.items()):
        name = separate.SIDE_NAME[side]
        messages.append(f"The {name} cut would start {round(p['start'] * 100)} percent of the way down the upper arm.")
    if not arms:
        messages.append("The rig has no arm bones to show a cut on.")
    return {"arms": arms, "params": p, "messages": messages}


PICTURES = ("base_color", "normal", "metallic", "roughness", "ao")
CARRY_KEY = "shape_rig"  # rig.CARRY_FILE
CAPS_KEY = "shape_caps"
CAP_MARGIN_PX = 3  # texels round each cap taken from the engine's picture too (it grows the caps' colour 2 past them)


def glb_pictures(glb: Path) -> dict[str, Any]:
    """The pictures of a GLB's first material as PIL images by picture kind: base_color, normal, and metallic (blue),
    roughness (green) and ao (red of the occlusion image) as single channels."""
    import io
    import json
    import struct

    from PIL import Image

    data = Path(glb).read_bytes()
    if data[:4] != b"glTF":
        return {}
    jlen = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20:20 + jlen])
    bin_off = 20 + jlen
    bin_data = data[bin_off + 8: bin_off + 8 + struct.unpack_from("<I", data, bin_off)[0]]
    mats, images, views, textures = (gltf.get(k) or [] for k in ("materials", "images", "bufferViews", "textures"))
    if not mats:
        return {}

    def image(ref: dict[str, Any] | None) -> Any:
        if not ref or ref.get("index") is None or ref["index"] >= len(textures):
            return None
        src = textures[ref["index"]].get("source")
        if src is None or "bufferView" not in images[src]:
            return None
        bv = views[images[src]["bufferView"]]
        o = bv.get("byteOffset", 0)
        im = Image.open(io.BytesIO(bin_data[o:o + bv["byteLength"]]))
        im.load()
        return im

    m = mats[0]
    pbr = m.get("pbrMetallicRoughness") or {}
    out: dict[str, Any] = {}
    base = image(pbr.get("baseColorTexture"))
    if base is not None:
        out["base_color"] = base.convert("RGBA")
    nrm = image(m.get("normalTexture"))
    if nrm is not None:
        out["normal"] = nrm.convert("RGB")
    mr = image(pbr.get("metallicRoughnessTexture"))
    if mr is not None:
        _, g, b = mr.convert("RGB").split()
        out["roughness"], out["metallic"] = g, b
    ao = image(m.get("occlusionTexture"))
    if ao is not None:
        out["ao"] = ao.convert("RGB").split()[0]
    return out


def cap_mask(caps: list[dict[str, Any]], size: tuple[int, int], margin: int = CAP_MARGIN_PX) -> Any:
    """An L-mode mask (255 on the caps' texels, grown by `margin`) at `size` from caps.json's texture triangles."""
    from PIL import Image, ImageDraw, ImageFilter

    w, h = size
    mask = Image.new("L", (w, h), 0)
    dr = ImageDraw.Draw(mask)
    for cap in caps:
        for tri in cap.get("uv_tris") or []:
            dr.polygon([(u * w, (1.0 - v) * h) for u, v in tri], fill=255)
    if margin > 0:
        mask = mask.filter(ImageFilter.MaxFilter(2 * margin + 1))
    return mask


def refresh_pictures(glb: Path, caps: list[dict[str, Any]], sources: dict[str, Path | None], out_dir: Path) -> dict[str, Path]:
    """The version's pictures with the caps painted in: each source picture keeps every texel except the caps' (and
    CAP_MARGIN_PX round them), which come from the engine's model at the source's size. A kind with no source is
    written whole from the model for base colour only (so the clean-up always shows its caps). -> {kind: file}."""
    from PIL import Image

    got = glb_pictures(glb)
    out: dict[str, Path] = {}
    for kind in PICTURES:
        new = got.get(kind)
        if new is None:
            continue
        src = sources.get(kind)
        dest = out_dir / f"shape_{kind}.png"
        if src is None or not Path(src).is_file():
            if kind != "base_color":
                continue
            new.save(dest)
        else:
            with Image.open(src) as im:
                old = im.copy()
            if new.size != old.size:
                new = new.resize(old.size, Image.BILINEAR)
            old.paste(new.convert(old.mode), (0, 0), cap_mask(caps, old.size))
            old.save(dest)
        out[kind] = dest
    return out


def _metrics_carried(parent: Version) -> dict[str, Any]:
    return {k: v for k, v in (parent.metrics or {}).items()
            if k not in _NOT_CARRIED and not k.startswith(_NOT_CARRIED_PREFIX)}


async def run(sc: Any, params: dict[str, Any]) -> None:
    """The job behind Apply (stage prep, op shape). Runs on the host. Raises ShapeRefused when nothing was changed."""
    import asyncio
    import json
    import shutil

    from . import paintsave

    parent = sc.parent
    if parent is None or parent.stage != "prep" or not parent.files:
        raise ValueError("the arm can only be separated on a clean-up step")
    p = parse(params)
    rig = sc.db.get(Version, str(params.get("rig_version_id") or ""))
    if rig is None:
        raise ValueError("the rig this was opened on is missing")
    rig_glb = library.version_file(rig, "glb", sc.settings)
    step4 = library.version_file(parent, "glb", sc.settings)
    if rig_glb is None or step4 is None:
        raise ValueError("the rig or the clean-up has no model to separate")

    paintsave._copy_parent_files(sc, parent)
    work = sc.dir / "shape_work"
    out_glb = work / Path(step4).name
    rigged = work / f"{CARRY_KEY}.glb"
    sc.progress(0.1, "separating the arm in Blender")
    timeout = float(sc.settings.blender_timeout_seconds)

    def work_it() -> dict[str, Any]:
        return separate.separate(sc.settings, Path(step4), Path(rig_glb), out_glb, side=p["side"], gap=p["gap"],
                                 start=p["start"], rigged_out=rigged, timeout=timeout)

    report = await asyncio.to_thread(work_it)
    if not report.get("changed"):
        why = " ".join(report.get("messages") or ["The cut could not be made on this model."])
        raise ShapeRefused(f"{why} {REFUSAL_NOTE}")

    sc.progress(0.85, "saving the separated model and its pictures")
    final_glb = sc.dir / Path(step4).name
    shutil.copyfile(out_glb, final_glb)
    sc.register("glb", final_glb)
    caps_src = work / separate.CAPS_FILE
    caps = json.loads(caps_src.read_text(encoding="utf-8")).get("caps", []) if caps_src.is_file() else []
    if caps_src.is_file():
        caps_file = sc.dir / f"{CAPS_KEY}.json"
        shutil.copyfile(caps_src, caps_file)
        sc.register(CAPS_KEY, caps_file)
    sources = {}
    for kind in PICTURES:
        f = library.version_file(parent, kind, sc.settings)
        sources[kind] = Path(f) if f else None
    for kind, f in (await asyncio.to_thread(refresh_pictures, final_glb, caps, sources, sc.dir)).items():
        sc.register(kind, f)
    if rigged.is_file():  # the rig run carries the rig over with this (rig.carry)
        carry_file = sc.dir / f"{CARRY_KEY}.glb"
        shutil.copyfile(rigged, carry_file)
        sc.register(CARRY_KEY, carry_file)
    shutil.rmtree(work, ignore_errors=True)
    log = {"operation": OPERATIONS[p["operation"]], "parameters": {"side": p["side"], "gap": p["gap"], "start": p["start"]},
           "rig_version": rig.id, "engine": separate.summary(report), "messages": report.get("messages", [])}
    sc.set_metrics(**_metrics_carried(parent), shaped=True, shape_of=parent.id, shape_log=log)
    sc.set_params(shape_of=parent.id, shape_operation=p["operation"])
