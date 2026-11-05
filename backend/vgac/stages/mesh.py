"""Stage 3, mesh and texture (instance A).

trellis (default): TRELLIS.2 shape + texture + native post chain from one RGBA cutout, output model.glb with
base_color, metallic, roughness, normal and ao PNGs, decimated to the category/platform triangle budget.
hy3d: Hunyuan3D-2mv from the four turnaround views over white (shape only, Tencent licence recorded).
The default triangle target sits 2 percent under the budget so prep need not decimate again (G05 a). UnwrapMesh
padding is max(4, texture / 256) texels and every map is grown max(8, texture / 128) texels into the empty space
after the bake (G03); metrics record the atlas share used, the measured gutter and the UV hash of the baked mesh.
After the job: silhouette audit at 15-degree steps against the turnaround cutouts (Blender on the host).
refine has no instruction engine for meshes: it re-runs with a new seed and records the instruction. adjust
re-runs with merged params.

Recipes (reference.recipe): when the version does not choose them, the triangle target is the recipe's share of the
budget (a quarter for low-poly, 40 percent for Retro 64) and the texture size the recipe's cap (never over the
platform's). A look without surface bumps (mesh.normal_map false) never registers the normal map the builder bakes,
and its model.glb loses its copy. The checks measure against the recipe's targets, and say so.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from .. import imaging, library, quality, reference
from ..comfy import ComfyClient
from ..graphs.hy3d import HY3D_VIEWS, hy3d_graph
from ..graphs.trellis import IMAGE_NODES, UNWRAP_PADDING_MAX, convert_graph
from ..imaging import (VIEW_YAW, atlas_used, best_alignment, contact_sheet, dilate_png, dilation_px, gutter_px, image_size,
                       load_uv_mesh, rasterise_uv, silhouette, unwrap_padding, uv_hash)
from .base import StageContext, random_seed
from .concept import stage_recipe

TEXTURE_KEYS = ("base_color", "metallic", "roughness", "normal", "ao")
# G05 (a): the mesh stage aims this far under the platform budget so prep does not have to decimate again.
BUDGET_HEADROOM = 0.98


def _silhouette_match(views_dir: Path, yaws, refs: dict[str, Path]) -> dict[str, Any]:
    """Rendered silhouettes against the reference cutouts, best yaw alignment. Pure CPU work, run in a thread."""
    rendered = {yaw: silhouette(views_dir / f"shade_{yaw}.png") for yaw in yaws}
    ref_sils = {v: silhouette(p) for v, p in refs.items()}
    return best_alignment(ref_sils, rendered)


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


def default_target_faces(budget_triangles: int | None) -> int:
    """The triangle target when the version does not set one: the platform budget less a little headroom, so the weld
    and hole filling in prep do not push the model over the budget and force a second decimation (and a re-bake)."""
    if not budget_triangles:
        return 20000
    return max(500, int(int(budget_triangles) * BUDGET_HEADROOM))


# The fewest triangles a recipe's target may ask the builder for.
RECIPE_FACES_MIN = 200


def recipe_target_faces(rec: dict[str, Any], budget_triangles: int | None) -> int:
    """The triangle target from the recipe: its share of the budget (reference.recipe mesh.triangles) less the same
    headroom as the budget default; the budget default itself when the recipe keeps the full budget."""
    t = (rec.get("mesh") or {}).get("triangles")
    if not t or not budget_triangles or int(t) >= int(budget_triangles):
        return default_target_faces(budget_triangles)
    return max(RECIPE_FACES_MIN, int(int(t) * BUDGET_HEADROOM))


def resolve_targets(rec: dict[str, Any], params: dict[str, Any], budget: dict[str, Any]) -> dict[str, Any]:
    """{target_faces, target_faces_source, texture_size, texture_source}: the version's choice ("chosen"), else the
    recipe's ("recipe"; the platform budget for looks that keep it)."""
    m = rec.get("mesh") or {}
    if params.get("target_faces"):
        faces, fsrc = int(params["target_faces"]), "chosen"
    else:
        faces, fsrc = recipe_target_faces(rec, budget.get("triangles")), "recipe"
    if params.get("texture_size"):
        tex, tsrc = int(params["texture_size"]), "chosen"
    else:
        tex, tsrc = int(m.get("texture") or budget.get("texture") or 2048), "recipe"
    return {"target_faces": faces, "target_faces_source": fsrc, "texture_size": tex, "texture_source": tsrc}


def resolve_padding(params: dict[str, Any], tex: int) -> int:
    """UnwrapMesh padding in texels: the version's padding, else max(4, texture / 256), within the node's 1 to 16."""
    p = params.get("padding")
    # The unwrap node leaves about half its padding as real space between pieces (measured live: 8 asked, 4 found),
    # so ask for double the target; the node's own limit is 16.
    value = int(p) if p not in (None, "") else 2 * unwrap_padding(tex)
    return max(1, min(UNWRAP_PADDING_MAX, value))


def finish_maps(glb: Path, maps: dict[str, Path], tex: int) -> dict[str, Any]:
    """G03 after the bake: grow every map max(8, size / 128) texels into the empty texture space (nearest colour), and
    measure the layout: share of the atlas used, gutter between pieces, and the UV hash of the mesh the maps were
    baked on. Pure CPU work, run in a thread."""
    uvm = load_uv_mesh(glb)
    if uvm is None:
        return {}
    masks: dict[int, Any] = {}
    grown = {}
    for key, path in maps.items():
        size = image_size(path)[0]
        if size not in masks:
            masks[size] = rasterise_uv(uvm, size)["labels"]
        px = dilation_px(size)
        dilate_png(path, masks[size] > 0, px)
        grown[key] = px
    labels = masks.get(tex) if tex in masks else rasterise_uv(uvm, tex)["labels"]
    pad = unwrap_padding(tex)
    return {"atlas_used": round(atlas_used(labels), 4), "texture_padding_px": gutter_px(labels, pad // 2 + 2),
            "dilated_px": grown, "bake_mesh_hash": uv_hash(uvm)}


async def _trellis(sc: StageContext, params: dict[str, Any]) -> None:
    input_view = params.get("input_view") or sc.category.get("input_view_default") or "concept"
    rec = stage_recipe(sc)
    targets = resolve_targets(rec, params, sc.budget)
    target_faces, tex = targets["target_faces"], targets["texture_size"]
    keep_normal = bool(rec["mesh"]["normal_map"])
    crisp = rec["surface"]["texture_filter"] == "crisp"
    upsample = int(params.get("upsample") or 1024)
    seed = int(params.get("seed") or random_seed())
    remesh = bool(sc.category.get("remesh_default", True)) if params.get("remesh") is None else bool(params["remesh"])
    padding = resolve_padding(params, tex)
    src = _input_image(sc, input_view)
    sc.set_params(engine="trellis", input_view=input_view, target_faces=target_faces, texture_size=tex, upsample=upsample, seed=seed, remesh=remesh,
                  padding=padding, target_faces_source=targets["target_faces_source"], texture_source=targets["texture_source"])
    sc.use_model("trellis2")
    name = await sc.stage_input(src, f"vgac_{sc.version.id[:8]}_mesh_in.png")
    sc.register("input", sc.copy_in(src, "input_image.png"))
    entry = await sc.comfy(convert_graph(name, sc.prefix(), target_faces, tex, upsample, seed, remesh, padding=padding),
                           poll=5.0, message="TRELLIS.2 convert", base=0.05, span=0.7)
    glb = await sc.download_mesh(entry, sc.dir / "model.glb")
    if not keep_normal or crisp:
        # The recipe's look: no bump map (the GLB's own copy goes too); crisp textures sampled nearest-neighbour.
        imaging.glb_replace_images(glb, drop_normal=not keep_normal, nearest=crisp)
    sc.register("glb", glb)
    maps: dict[str, Path] = {}
    for node, key in IMAGE_NODES.items():
        if key == "normal" and not keep_normal:
            continue  # the builder always bakes one; a look without surface bumps never registers or ships it
        imgs = ComfyClient.images_of(entry, node)
        if imgs:
            p = await sc.client().download_output(imgs[0], sc.dir / f"{key}.png")
            sc.register(key, p)
            if key in TEXTURE_KEYS:
                maps[key] = Path(p)
    sc.set_metrics(engine="trellis", seed=seed, elapsed_seconds=entry.get("elapsed_seconds"), target_faces=target_faces, texture_size=tex,
                   unwrap_padding_px=padding, target_faces_source=targets["target_faces_source"], texture_source=targets["texture_source"],
                   normal_map=keep_normal, recipe=reference.recipe_record(rec, "mesh", "surface"))
    try:
        sc.progress(0.76, "Growing the textures into the seams")
        sc.set_metrics(**await asyncio.to_thread(finish_maps, Path(glb), maps, tex))
    except Exception as e:  # the maps stay as baked; the prep checks still measure the layout
        sc.set_metrics(dilate_error=str(e)[:300])


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
        await sc.blender("render_views.py", [glb, views_dir, 256, 15, "shade"], log_name="render_views.log", timeout=sc.settings.blender_timeout_seconds)
        info = json.loads((views_dir / "info.json").read_text())
        sc.set_metrics(triangles=info.get("triangles"), dims=info.get("dims"))
        sc.register("views_dir", views_dir / "info.json")
        if refs:
            best = await asyncio.to_thread(_silhouette_match, views_dir, info.get("yaws", range(0, 360, 15)), refs)
            sc.set_metrics(silhouette_iou=best["mean"], views={v: {"silhouette_iou": x} for v, x in best["views"].items()},
                           silhouette_offset=best["offset"], silhouette_sign=best["sign"])
        front = views_dir / "shade_0.png"
        if front.is_file():
            sc.register("preview", front)
        sheet_paths = [views_dir / f"shade_{y}.png" for y in range(0, 360, 45) if (views_dir / f"shade_{y}.png").is_file()]
        if sheet_paths:
            sheet = await asyncio.to_thread(contact_sheet, sheet_paths, sc.dir / "sheet.png", cell=256, columns=4, labels=[p.stem for p in sheet_paths])
            sc.register("sheet", sheet)
    except Exception as e:  # Blender missing or render failed: keep the mesh, record the problem
        sc.set_metrics(audit_error=str(e)[:500])
    await judge(sc)


DEFAULT_SILHOUETTE_MIN = 0.6


def look_vars(rec: dict[str, Any], budget: dict[str, Any]) -> dict[str, Any]:
    """Placeholder values for checks measured against the recipe's own target instead of the platform budget."""
    known = {s["id"]: s for s in reference.styles()}
    look = (known.get(rec.get("style")) or {}).get("look") or rec.get("style")
    return {"look": look, "platform_triangles": budget.get("triangles"), "platform_texture": budget.get("texture")}


def triangle_target(rec: dict[str, Any], params: dict[str, Any], budget: dict[str, Any]) -> tuple[int | None, dict[str, Any] | None]:
    """The triangle limit a check measures against, and the check's vars: the recipe's target when the look asks for
    fewer triangles than the platform allows (and the version did not choose more), else the platform budget and no
    vars."""
    platform = budget.get("triangles")
    target = (rec.get("mesh") or {}).get("triangles")
    chosen = params.get("target_faces_source") == "chosen" or (params.get("target_faces") and "target_faces_source" not in params)
    if chosen and int(params.get("target_faces") or 0) > int(target or 0):
        return platform, None
    if target and platform and int(target) < int(platform):
        return int(target), {**look_vars(rec, budget), "recipe": True}
    return platform, None


def texture_target(rec: dict[str, Any], budget: dict[str, Any]) -> tuple[int | None, dict[str, Any] | None]:
    """The texture size limit a check measures against: the recipe's when smaller than the platform's."""
    platform = budget.get("texture")
    tex = (rec.get("mesh") or {}).get("texture")
    if tex and platform and int(tex) < int(platform):
        return int(tex), {**look_vars(rec, budget), "recipe": True}
    return platform, None


def with_vars(checks: list[dict[str, Any]], vars: dict[str, Any] | None, suffix: str) -> list[dict[str, Any]]:
    """Add vars to the checks whose name ends with suffix."""
    if vars:
        for c in checks:
            if str(c.get("name") or c.get("id") or "").endswith(suffix):
                c["vars"] = {**(c.get("vars") or {}), **vars}
    return checks


async def judge(sc: StageContext) -> None:
    rec = stage_recipe(sc)
    tex_limit, tex_vars = texture_target(rec, sc.budget)
    checks = with_vars(quality.texture_checks({k: library.version_file(sc.version, k, sc.settings) for k in TEXTURE_KEYS}, tex_limit),
                       tex_vars, "_budget")
    tris = sc.version.metrics.get("triangles")
    limit, tvars = triangle_target(rec, sc.version.params, sc.budget)
    if tris is not None and limit:
        checks.append(quality.check("triangle_budget", tris <= limit * (1 + quality.TRIANGLE_BUDGET_TOLERANCE), tris, limit, vars=tvars))
    iou = sc.version.metrics.get("silhouette_iou")
    if iou is not None:
        # Long-bodied categories (quadruped, serpent, winged) never match a flat concept as closely as a biped does,
        # so each category may lower the floor (silhouette_min in its yaml); 0.6 is the biped default.
        floor = float(sc.category.get("silhouette_min") or DEFAULT_SILHOUETTE_MIN)
        checks.append(quality.check("silhouette_iou", iou >= floor, iou, floor))
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


def inherit(parent_params: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Parent params under the new ones, without the triangle target and texture size the parent took from the recipe
    (only a chosen value carries over, so a new run follows the recipe as it is now)."""
    base = dict(parent_params or {})
    for key, src in (("target_faces", "target_faces_source"), ("texture_size", "texture_source")):
        if base.get(src) == "recipe" and key not in params:
            base.pop(key, None)
        base.pop(src, None)
    return {**base, **params}


async def refine(sc: StageContext, instruction: str, view: str | None, mask_png: Path | None, params: dict[str, Any]) -> None:
    parent = sc.parent
    merged = {**inherit(parent.params if parent else {}, params), "seed": params.get("seed") or random_seed(), "instruction": instruction}
    sc.set_params(instruction=instruction, refine_of=parent.id if parent else None)
    await _rerun_from_mesh_parent(sc, merged)


async def adjust(sc: StageContext, params: dict[str, Any]) -> None:
    parent = sc.parent
    merged = inherit(parent.params if parent else {}, params)
    await _rerun_from_mesh_parent(sc, merged)


async def _rerun_from_mesh_parent(sc: StageContext, merged: dict[str, Any]) -> None:
    # Inputs come from the lineage above the mesh parent; lineage_file walks through it, so nothing to swap.
    await run(sc, merged)
