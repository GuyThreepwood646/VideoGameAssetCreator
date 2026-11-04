"""Stage 5, rig (SkinToken on instance B, then Blender on the host).

Template: category rig_engine skintoken_mixamo -> "Mixamo", skintoken -> "Keep model names", none -> skip with
metrics.rigid = true (the prep GLB passes through as files.glb). SkinToken asserting on an empty skinning
prediction is also "rigid, no rig", not an error. After rigging: rig_info (bones, weight coverage, rest and bend
renders at 35 and 90 degrees, skeleton overlay), then procedural idle and walk clips (procanim) and a rendered
strip of them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .. import quality
from ..comfy import ComfyClient, ComfyError
from ..graphs.skintoken import TEMPLATES, rig_graph
from ..imaging import contact_sheet
from .base import StageContext


def resolve_template(sc: StageContext, params: dict[str, Any]) -> str | None:
    """-> SkinToken template label, or None for no rig."""
    t = params.get("template") or "auto"
    if t == "none":
        return None
    if t in TEMPLATES:
        return TEMPLATES[t]
    engine = sc.category.get("rig_engine", "none")
    if engine == "skintoken_mixamo":
        return TEMPLATES["mixamo"]
    if engine == "skintoken":
        return TEMPLATES["generic"]
    return None


def overlay_bones(info_dir: Path) -> Path | None:
    """Draw the skeleton from rig_info.json over rest.png (bakeoff/overlay_bones.py)."""
    info = json.loads((info_dir / "rig_info.json").read_text())
    if not info.get("bone_segments") or not (info_dir / "rest.png").is_file():
        return None
    cam = info["camera"]
    cx, cy, cz = cam["centre"]
    S, R = cam["ortho_scale"], cam["res"]

    def proj(p):
        return ((p[0] - cx) / S + 0.5) * R, (0.5 - (p[2] - cz) / S) * R

    im = Image.open(info_dir / "rest.png").convert("RGBA")
    bg = Image.new("RGBA", im.size, (110, 110, 110, 255))
    bg.alpha_composite(im)
    dr = ImageDraw.Draw(bg)
    for b in info["bone_segments"]:
        h, t = proj(b["head"]), proj(b["tail"])
        dr.line([h, t], fill=(255, 200, 0, 255), width=3)
        dr.ellipse([h[0] - 4, h[1] - 4, h[0] + 4, h[1] + 4], fill=(255, 60, 60, 255))
    out = info_dir / "bones.png"
    bg.convert("RGB").save(out)
    return out


async def _skip(sc: StageContext, reason: str, src: Path | None) -> None:
    if src is not None:
        sc.register("glb", sc.copy_in(src, "rig.glb"))
    link_textures(sc)
    sc.set_metrics(rigid=True, bones=0, reason=reason)
    sc.set_metrics(**quality.summarise(quality.rig_checks({"bones": 0}, rig_expected=False)))


async def _rig(sc: StageContext, params: dict[str, Any]) -> None:
    if sc.asset.category == "material":
        sc.set_metrics(skipped=True, rigid=True, reason="material category has no mesh")
        return
    src = sc.lineage_file("glb", ("prep", "mesh"))
    if src is None:
        raise FileNotFoundError("no prep or mesh glb in lineage")
    template = resolve_template(sc, params)
    target_faces = int(params.get("target_faces") or 0)
    gpv = int(params.get("group_per_vertex") or 4)
    bco = bool(params.get("bottom_center_origin", True))
    bend_test = bool(params.get("bend_test", True))
    sc.set_params(template=params.get("template") or "auto", template_label=template, target_faces=target_faces, group_per_vertex=gpv,
                  bottom_center_origin=bco, bend_test=bend_test)
    if template is None:
        await _skip(sc, f"rig_engine {sc.category.get('rig_engine', 'none')}", src)
        return

    sc.use_model("skintoken")
    mesh_name = await sc.stage_input(src, f"vgac_{sc.version.id[:8]}_rig_in.glb")
    graph = rig_graph(mesh_name, sc.prefix("rig"), target_faces, template=template, group_per_vertex=gpv, bottom_center_origin=bco)
    try:
        entry = await sc.comfy(graph, poll=5.0, message="SkinToken rig", base=0.05, span=0.5)
    except ComfyError as e:
        if e.is_assertion() or any("skin" in m.lower() and "empty" in m.lower() for m in e.exception_messages):
            await _skip(sc, "SkinToken returned an empty skinning prediction (rigid object)", src)
            sc.set_metrics(skintoken_error=str(e)[:500])
            return
        raise
    faces_text = ComfyClient.texts_of(entry, "faces")
    paths = ComfyClient.texts_of(entry, "show")
    rigged = _locate_output(sc, paths)
    raw_glb = sc.copy_in(rigged, "rig_raw.glb")
    rig_glb = sc.dir / "rig.glb"
    try:
        # Repair weights: unweighted vertices inherit the nearest weighted vertex, 4 influences max, normalised.
        await sc.blender("rig_fix.py", [raw_glb, rig_glb], log_name="rig_fix.log", timeout=1800)
    except Exception as e:  # noqa: BLE001
        sc.set_metrics(rig_fix_error=str(e)[:500])
    if not rig_glb.is_file():
        rig_glb = raw_glb
    sc.register("glb", rig_glb)
    link_textures(sc)
    sc.set_metrics(rigid=False, elapsed_seconds=entry.get("elapsed_seconds"), faces_rigged=int(faces_text[0]) if faces_text and str(faces_text[0]).isdigit() else None,
                   template=template)
    await _inspect(sc, rig_glb, bend_test)
    await _animate(sc, rig_glb)
    await judge(sc)


def _locate_output(sc: StageContext, paths: list[str]) -> Path:
    client = sc.client()
    for p in paths:
        cand = Path(str(p).strip())
        if cand.is_file():
            return cand
        if client.output_dir is not None and (client.output_dir / cand).is_file():
            return client.output_dir / cand
    if client.output_dir is not None:
        matches = sorted(client.output_dir.glob(sc.prefix("rig") + "*.glb"), key=lambda q: q.stat().st_mtime)
        if matches:
            return matches[-1]
    raise FileNotFoundError(f"rigged GLB not found; SkinToken reported {paths!r}")


async def _inspect(sc: StageContext, rig_glb: Path, bend_test: bool) -> None:
    info_dir = sc.dir / "rig_info"
    try:
        sc.progress(0.6, "rig inspection render")
        await sc.blender("rig_info.py", [rig_glb, info_dir, "35,90" if bend_test else "35"], log_name="rig_info.log", timeout=1800)
        info = json.loads((info_dir / "rig_info.json").read_text())
        sc.register("rig_info", info_dir / "rig_info.json")
        for key, fname in (info.get("renders") or {}).items():
            sc.register(key, info_dir / fname)
        bones_png = overlay_bones(info_dir)
        if bones_png:
            sc.register("bones", bones_png)
        strip_paths = [info_dir / f for f in [bones_png.name if bones_png else "rest.png"] + [fn for k, fn in (info.get("renders") or {}).items() if k != "rest"] if (info_dir / f).is_file()]
        if strip_paths:
            sc.register("bend_strip", contact_sheet(strip_paths, sc.dir / "bend_strip.png", cell=384, columns=len(strip_paths), labels=[p.stem for p in strip_paths]))
        sc.set_metrics(bones=info.get("bones"), bone_names=info.get("bone_names"), roots=info.get("roots"),
                       weight_coverage=round(float(info.get("weight_coverage", 0.0)), 4), max_influences=info.get("max_influences"),
                       unnormalised_verts=info.get("unnormalised_verts"), bent=info.get("bent"))
    except Exception as e:
        sc.set_metrics(rig_info_error=str(e)[:500])


async def _animate(sc: StageContext, rig_glb: Path) -> None:
    """Procedural idle and walk clips for the rigged mesh, plus a rendered strip of both."""
    anim_glb = sc.dir / "anim.glb"
    try:
        sc.progress(0.75, "procedural idle and walk clips")
        await sc.blender("procanim.py", [rig_glb, anim_glb, 24], log_name="procanim.log", timeout=1800)
        report_path = sc.dir / "anim_anim.json"
        sc.register("anim_glb", anim_glb)
        if report_path.is_file():
            sc.register("anim_report", report_path)
            rep = json.loads(report_path.read_text())
            sc.set_metrics(anim_kind=rep.get("kind"), clips=rep.get("clips"), walk_probe=rep.get("walk_probe"))
        sc.progress(0.88, "render clip strip")
        frames_dir = sc.dir / "anim_frames"
        await sc.blender("render_anim.py", [anim_glb, frames_dir, 6, 256], log_name="render_anim.log", timeout=1800)
        frames = json.loads((frames_dir / "frames.json").read_text())
        paths, labels = [], []
        for clip, files in (frames.get("clips") or {}).items():
            for i, f in enumerate(files):
                paths.append(frames_dir / f)
                labels.append(f"{clip} {i}")
        if paths:
            strip = contact_sheet(paths, sc.dir / "anim_strip.png", cell=256, columns=frames.get("frames", 6), labels=labels)
            sc.register("anim_strip", strip)
    except Exception as e:
        sc.set_metrics(anim_error=str(e)[:500])


def link_textures(sc: StageContext) -> None:
    from .prep import link_textures as _lt

    _lt(sc)


async def judge(sc: StageContext) -> None:
    info_path = sc.file("rig_info")
    if sc.version.metrics.get("rigid"):
        checks = quality.rig_checks({"bones": sc.version.metrics.get("bones", 0)}, rig_expected=False)
    elif info_path is not None:
        checks = quality.rig_checks(json.loads(Path(info_path).read_text()), rig_expected=True)
    else:
        checks = quality.rig_checks({"bones": sc.version.metrics.get("bones", 0)}, rig_expected=True)
    if sc.version.metrics.get("anim_glb") or sc.file("anim_glb"):
        checks.append(quality.check("clips", bool(sc.version.metrics.get("clips")), sc.version.metrics.get("clips"), ["idle", "walk"]))
    sc.set_metrics(**quality.summarise(checks))


async def run(sc: StageContext, params: dict[str, Any]) -> None:
    await _rig(sc, params)


async def refine(sc: StageContext, instruction: str, view: str | None, mask_png: Path | None, params: dict[str, Any]) -> None:
    parent = sc.parent
    merged = {**(parent.params if parent else {}), **params, "instruction": instruction}
    await _rig(sc, merged)


async def adjust(sc: StageContext, params: dict[str, Any]) -> None:
    parent = sc.parent
    merged = {**(parent.params if parent else {}), **params}
    await _rig(sc, merged)
