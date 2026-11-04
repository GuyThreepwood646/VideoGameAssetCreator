"""Stage 3, mesh and texture (instance A).

trellis (default): TRELLIS.2 shape + texture + native post chain from one RGBA cutout, output model.glb with
base_color, metallic, roughness, normal and ao PNGs, decimated to the category/platform triangle budget.
hy3d: Hunyuan3D-2mv from the four turnaround views over white (shape only, Tencent licence recorded).
After the job: silhouette audit at 15-degree steps against the turnaround cutouts (Blender on the host).
refine has no instruction engine for meshes: it re-runs with a new seed and records the instruction. adjust
re-runs with merged params.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import library, quality
from ..comfy import ComfyClient
from ..graphs.hy3d import HY3D_VIEWS, hy3d_graph
from ..graphs.trellis import IMAGE_NODES, convert_graph
from ..imaging import VIEW_YAW, best_alignment, silhouette
from .base import StageContext, random_seed

TEXTURE_KEYS = ("base_color", "metallic", "roughness", "normal", "ao")


def _input_image(sc: StageContext, input_view: str) -> Path:
    if input_view == "concept":
        p = sc.lineage_file("cutout", ("concept",))
        if p is None:
            raise FileNotFoundError("no concept cutout in lineage")
        return p
    p = sc.lineage_file(f"{input_view}_cutout", ("turnaround",))
    if p is None:
        raise FileNotFoundError(f"no turnaround view {input_view!r} in lineage")
    return p


async def _trellis(sc: StageContext, params: dict[str, Any]) -> None:
    input_view = params.get("input_view") or sc.category.get("input_view_default") or "concept"
    target_faces = int(params.get("target_faces") or sc.budget["triangles"] or 20000)
    tex = int(params.get("texture_size") or sc.budget["texture"] or 2048)
    upsample = int(params.get("upsample") or 1024)
    seed = int(params.get("seed") or random_seed())
    remesh = bool(params.get("remesh", True))
    src = _input_image(sc, input_view)
    sc.set_params(engine="trellis", input_view=input_view, target_faces=target_faces, texture_size=tex, upsample=upsample, seed=seed, remesh=remesh)
    sc.use_model("trellis2")
    name = await sc.stage_input(src, f"vgac_{sc.version.id[:8]}_mesh_in.png")
    sc.register("input", sc.copy_in(src, "input_image.png"))
    entry = await sc.comfy(convert_graph(name, sc.prefix(), target_faces, tex, upsample, seed, remesh), poll=5.0, message="TRELLIS.2 convert", base=0.05, span=0.7)
    glb = await sc.download_mesh(entry, sc.dir / "model.glb")
    sc.register("glb", glb)
    for node, key in IMAGE_NODES.items():
        imgs = ComfyClient.images_of(entry, node)
        if imgs:
            p = await sc.client().download_output(imgs[0], sc.dir / f"{key}.png")
            sc.register(key, p)
    sc.set_metrics(engine="trellis", seed=seed, elapsed_seconds=entry.get("elapsed_seconds"), target_faces=target_faces, texture_size=tex)


async def _hy3d(sc: StageContext, params: dict[str, Any]) -> None:
    seed = int(params.get("seed") or random_seed())
    names = {}
    for v in HY3D_VIEWS:
        p = sc.lineage_file(f"{v}_white", ("turnaround",))
        if p is None:
            raise FileNotFoundError(f"hy3d needs turnaround view {v} over white")
        names[v] = await sc.stage_input(p, f"vgac_{sc.version.id[:8]}_hy_{v}.png")
    sc.set_params(engine="hy3d", seed=seed, steps=int(params.get("steps") or 20), cfg=float(params.get("cfg") or 7.5))
    sc.use_model("hy3d_mv")
    entry = await sc.comfy(hy3d_graph(names, sc.prefix("model"), steps=int(params.get("steps") or 20), cfg=float(params.get("cfg") or 7.5), seed=seed),
                           poll=5.0, message="Hunyuan3D-2mv", base=0.05, span=0.7)
    glb = await sc.download_mesh(entry, sc.dir / "model.glb")
    sc.register("glb", glb)
    sc.set_metrics(engine="hy3d", seed=seed, elapsed_seconds=entry.get("elapsed_seconds"), textured=False)


async def audit(sc: StageContext) -> None:
    """Silhouette IoU against the turnaround cutouts; records metrics.silhouette_iou. Never fails the job."""
    glb = sc.file("glb")
    if glb is None:
        return
    refs = {}
    for v in VIEW_YAW:
        p = sc.lineage_file(f"{v}_cutout", ("turnaround",))
        if p is not None:
            refs[v] = p
    try:
        sc.progress(0.8, "silhouette audit render")
        views_dir = sc.dir / "views"
        await sc.blender("render_views.py", [glb, views_dir, 256, 15, "shade"], log_name="render_views.log", timeout=1800)
        info = json.loads((views_dir / "info.json").read_text())
        sc.set_metrics(triangles=info.get("triangles"), dims=info.get("dims"))
        sc.register("views_dir", views_dir / "info.json")
        if refs:
            rendered = {yaw: silhouette(views_dir / f"shade_{yaw}.png") for yaw in info.get("yaws", range(0, 360, 15))}
            ref_sils = {v: silhouette(p) for v, p in refs.items()}
            best = best_alignment(ref_sils, rendered)
            sc.set_metrics(silhouette_iou=best["mean"], views={v: {"silhouette_iou": x} for v, x in best["views"].items()},
                           silhouette_offset=best["offset"], silhouette_sign=best["sign"])
        front = views_dir / "shade_0.png"
        if front.is_file():
            sc.register("preview", front)
        sheet_paths = [views_dir / f"shade_{y}.png" for y in range(0, 360, 45) if (views_dir / f"shade_{y}.png").is_file()]
        if sheet_paths:
            from ..imaging import contact_sheet

            sc.register("sheet", contact_sheet(sheet_paths, sc.dir / "sheet.png", cell=256, columns=4, labels=[p.stem for p in sheet_paths]))
    except Exception as e:  # Blender missing or render failed: keep the mesh, record the problem
        sc.set_metrics(audit_error=str(e)[:500])
    await judge(sc)


async def judge(sc: StageContext) -> None:
    checks = quality.texture_checks({k: library.version_file(sc.version, k, sc.settings) for k in TEXTURE_KEYS}, sc.budget["texture"])
    tris = sc.version.metrics.get("triangles")
    if tris is not None and sc.budget["triangles"]:
        checks.append(quality.check("triangle_budget", tris <= sc.budget["triangles"] * 1.05, tris, sc.budget["triangles"]))
    iou = sc.version.metrics.get("silhouette_iou")
    if iou is not None:
        checks.append(quality.check("silhouette_iou", iou >= 0.6, iou, 0.6))
    sc.set_metrics(**quality.summarise(checks))


async def run(sc: StageContext, params: dict[str, Any]) -> None:
    engine = params.get("engine") or "trellis"
    if engine == "hy3d":
        await _hy3d(sc, params)
    elif engine == "trellis":
        await _trellis(sc, params)
    else:
        raise ValueError(f"unknown mesh engine {engine!r}")
    await audit(sc)


async def refine(sc: StageContext, instruction: str, view: str | None, mask_png: Path | None, params: dict[str, Any]) -> None:
    parent = sc.parent
    merged = {**(parent.params if parent else {}), **params, "seed": params.get("seed") or random_seed(), "instruction": instruction}
    sc.set_params(instruction=instruction, refine_of=parent.id if parent else None)
    await _rerun_from_mesh_parent(sc, merged)


async def adjust(sc: StageContext, params: dict[str, Any]) -> None:
    parent = sc.parent
    merged = {**(parent.params if parent else {}), **params}
    await _rerun_from_mesh_parent(sc, merged)


async def _rerun_from_mesh_parent(sc: StageContext, merged: dict[str, Any]) -> None:
    # Inputs come from the lineage above the mesh parent; lineage_file walks through it, so nothing to swap.
    await run(sc, merged)
