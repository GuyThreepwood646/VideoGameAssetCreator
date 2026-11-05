"""Stage 4, game-ready prep (Blender on the host): face forward, scale on the category's size axis, pivot, weld,
optional symmetrize, smooth shading.

Input is the mesh version's model.glb; output prep.glb plus prep_report.json. Quality checks from the report.
The material category records a skip.

Size (G12, light G13): size_m along the category's size_axis (data/categories/*.yaml). When the version does not set
size_m, a size written in the description ("two metres wide") is used, with its axis word; otherwise the category
size. height_m, the old knob, is still read from older versions and means the height.

Facing (G14): face_yaw "auto" turns the model by the nearest quarter turn to the mesh audit's best fit when the fit
is trusted (silhouette match at or above the category floor) and within 20 degrees of a quarter turn; a number turns
it by that many degrees. The facing check compares the turn applied with the audit's best turn.

Textures on the final model (G05, G03): symmetrize is off unless the version turns it on. When prep changes the
surface anyway (symmetrize, or a decimation back to the budget) or the version asks (rebake), blender/rebake.py bakes
every map again from the mesh stage's model onto the final mesh, and prep registers those maps instead of the mesh
stage's. Metrics record whether that happened and how long it took, the UV hashes of the baked and the shipped mesh,
the share of the atlas used and the gutter between texture pieces.

Scenery on the grid (G15): for the grid categories (environment) prep sizes the piece as above, then blender/prep.py
rounds width and depth to whole grid steps and the height to half steps (blender/gridfit.py: thin sides left alone, at
most 15 percent per side), puts the pivot on the minimum corner and renders "How it tiles" (tiling.png). The knobs are
grid_m (the step: the version's choice, else the category's grid_m, else 1 m; per asset, no project setting in v1),
grid_footprint and grid_height. The size knob does not apply to scenery; a size in the description still sets the
starting size. Metrics record the grid, the footprint in squares, the snapped size and the tiling gap; the checks are
grid_fit and tiles_cleanly instead of size_m.

Light and colour (G07, G08): prep scores light painted into the base colour (imaging.light_score). The style decides
whether it is removed (styles.yaml delight_default; the delight knob overrides), and whether colours, metal and shine
are brought into physically based ranges (pbr_fix_default; the pbr_fix knob overrides). Roughness is always kept at
0.04 or more. A corrected base colour becomes base_color, and the one before is kept as base_color_original.

Recipes (reference.recipe): the shading is the recipe's (flat for low-poly: every face its own flat normal), unless
the shading knob chooses; the triangle limit is the recipe's target (the platform budget when the mesh version chose
a larger count). The texture pass then gives the maps the recipe's look: no normal map when the look has no surface
bumps; every map shrunk to the recipe's texture size (whole pixels, nearest neighbour, for crisp looks; an even box
average otherwise); for crisp looks the base colour limited to the project palette (style lock colours) or to the
recipe's number of colours, the original kept as base_color_original; and the prep GLB sampled nearest-neighbour.
The look_applied check says in plain words what the recipe did and whether the shipped files match it.
"""

from __future__ import annotations

import asyncio
import json
import math
import shutil
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .. import imaging, quality, reference
from ..blender import gridfit
from . import face_detail
from .base import StageContext
from .concept import stage_recipe

# The mesh stage's silhouette floor when the category does not set one (stages/mesh.py DEFAULT_SILHOUETTE_MIN).
DEFAULT_SILHOUETTE_MIN = 0.6

TEXTURE_KEYS = ("base_color", "normal", "metallic", "roughness", "ao")
# Thresholds (docs/GREAT-ASSETS-PLAN.md 3.5 and 3.9). The light score limit is ours: on the library's painted assets
# the score is 0 (wolf), 0.12 to 0.18 (knights, chest) and 0.25 (goblin, visibly lit from above in its concept).
LIGHT_SCORE_LIMIT = 0.2
ATLAS_USED_MIN = 0.35  # the unwrap normally lands between 0.45 and 0.6; a mirrored, un-rebaked model sat at 0.28
BAKE_MATCH_MIN = 0.99
ALBEDO_OUT_MAX = 0.01
METAL_MIXED_MAX = 0.05
METAL_MIXED_AFTER_FIX_MAX = 0.2  # leftover edge texels after the automatic sharpening (ranger: 0.51 before, 0.11 after)
# The measured gutter passes at three quarters of the padding asked for (rasterising the pieces costs a texel or so).
PADDING_PASS_FRACTION = 0.75
DELIGHT_MODES = ("auto", "remove", "keep")


def resolve_size(category_id: str, params: dict[str, Any], description: str | None) -> dict[str, Any]:
    """{size_m, size_axis, size_word, size_source}: the version's size_m (or the old height_m, which means the
    height), else a size from the description, else the category size. An explicit size_axis in params wins (a Fix
    or an earlier description size carries one)."""
    spec = reference.size_spec(category_id)
    axis = params.get("size_axis") if params.get("size_axis") in reference.SIZE_AXES else None
    word = params.get("size_word") or None
    if params.get("size_m"):
        size, source = float(params["size_m"]), "chosen"
    elif params.get("height_m"):
        size, source = float(params["height_m"]), "chosen"
        axis = axis or "height"
    else:
        found = reference.parse_size(description, spec["default"])
        if found:
            size, source = float(found["size_m"]), "description"
            if found["axis"]:
                axis, word = found["axis"], found["word"]
        else:
            size, source = spec["default"], "category"
    axis = axis or spec["axis"]
    if not word or (axis != spec["axis"] and word == spec["word"] and source != "description"):
        word = spec["word"] if axis == spec["axis"] else reference.AXIS_WORD_DEFAULT[axis]
    return {"size_m": size, "size_axis": axis, "size_word": word, "size_source": source}


def resolve_grid(category: dict[str, Any], params: dict[str, Any]) -> dict[str, Any] | None:
    """{grid_m, grid_footprint, grid_height} for a grid category (G15), else None. grid_m: the version's choice, else
    the category's grid_m, else 1 m. Footprint and height are "auto" unless the version chose one."""
    if category.get("id") not in reference.GRID_CATEGORIES:
        return None
    grid = params.get("grid_m") if params.get("grid_m") not in (None, "") else category.get("grid_m")
    fp = params.get("grid_footprint")
    fp = fp if fp in gridfit.FOOTPRINTS else "auto"
    h = gridfit.height_choice(params.get("grid_height"))
    return {"grid_m": _num(gridfit.grid_value(grid)), "grid_footprint": fp, "grid_height": "auto" if h is None else _num(h)}


def _num(x: float) -> int | float:
    """1.0 -> 1, 0.5 -> 0.5: whole numbers as ints, so the stored params match the knob's options."""
    return int(x) if float(x).is_integer() else float(x)


def resolve_facing(category: dict[str, Any], face_yaw: Any, mesh_metrics: dict[str, Any] | None) -> dict[str, Any]:
    """{face_yaw_applied, face_best_turn, facing_source}. face_best_turn is None when the mesh audit has no fit or
    its silhouette match is below the category floor (then nothing is checked)."""
    m = mesh_metrics or {}
    best = None
    offset, iou = m.get("silhouette_offset"), m.get("silhouette_iou")
    floor = float(category.get("silhouette_min") or DEFAULT_SILHOUETTE_MIN)
    if offset is not None and iou is not None and float(iou) >= floor:
        best = quality.best_turn(offset, int(m.get("silhouette_sign") or 1))
    if face_yaw in (None, "", "auto"):
        applied = quality.auto_turn(best)
        source = "audit" if best is not None else "as built"
    else:
        applied = int(float(face_yaw)) % 360
        source = "chosen"
    return {"face_yaw_applied": applied, "face_best_turn": best, "facing_source": source}


async def _prep(sc: StageContext, params: dict[str, Any]) -> None:
    if sc.asset.category == "material":
        sc.set_metrics(skipped=True, reason="material category has no mesh")
        return
    src = sc.lineage_file("glb", ("mesh",))
    if src is None:
        raise FileNotFoundError("no mesh glb in lineage")
    size = resolve_size(sc.asset.category, params, sc.asset.prompt)
    mesh_v = sc.stage_version("mesh")
    facing = resolve_facing(sc.category, params.get("face_yaw", "auto"), mesh_v.metrics if mesh_v else None)
    # G05 (b): mirroring halves the texture space the model uses and changes the surface the maps were painted for,
    # so it runs only when the version asks (the Fine-tune switch, or a Fix).
    symmetric = bool(params.get("symmetric") or False)
    grid = resolve_grid(sc.category, params)
    pivot = "corner" if grid else (params.get("pivot") or sc.category.get("pivot") or "base")
    smooth = float(params.get("smooth_angle") or 60.0)
    face_yaw = params.get("face_yaw")
    surface = resolve_surface(sc.style, params)
    rec = stage_recipe(sc)
    shading = resolve_shading(rec, params)
    mesh_v_params = sc.stage_version("mesh").params if sc.stage_version("mesh") else {}
    tri_limit = prep_triangle_limit(rec, mesh_v_params, sc.budget)
    sc.set_params(size_m=size["size_m"], size_axis=size["size_axis"], size_word=size["size_word"],
                  face_yaw=face_yaw if face_yaw not in (None, "") else "auto", symmetric=symmetric, pivot=pivot, smooth_angle=smooth,
                  delight=params.get("delight") or "auto", pbr_fix=surface["pbr_fix"], rebake=bool(params.get("rebake") or False),
                  shading=params.get("shading") if params.get("shading") in reference.SHADING_OPTIONS else "auto", **(grid or {}))
    sc.set_metrics(size_source=size["size_source"], shading_applied=shading["shading"], shading_source=shading["source"],
                   triangle_limit=tri_limit["limit"], triangle_limit_source=tri_limit["source"],
                   recipe=reference.recipe_record(rec, "mesh", "surface"), **facing)
    sc.use_model("blender")
    out = sc.dir / "prep.glb"
    args: list[Any] = [src, out, "mesh", "--size", size["size_m"], "--size-axis", size["size_axis"], "--yaw", facing["face_yaw_applied"],
                       "--pivot", pivot, "--smooth-angle", smooth, "--shading", shading["shading"]]
    if tri_limit["limit"]:
        args += ["--budget", int(tri_limit["limit"])]
    if symmetric:
        args.append("--symmetric")
    if grid:
        args += ["--grid", grid["grid_m"], "--grid-footprint", grid["grid_footprint"], "--grid-height", grid["grid_height"],
                 "--tiling", sc.dir / "tiling.png"]
    keep_normal = bool(rec["mesh"]["normal_map"])
    mesh_maps = {k: sc.lineage_file(k, ("mesh",)) for k in TEXTURE_KEYS if keep_normal or k != "normal"}
    mesh_maps = {k: p for k, p in mesh_maps.items() if p is not None}
    tex = texture_size_of(mesh_maps, mesh_v.params if mesh_v else {}, sc.budget.get("texture"))
    if mesh_maps:
        maps_json = sc.dir / "bake_maps.json"
        maps_json.write_text(json.dumps({k: str(p) for k, p in mesh_maps.items()}), encoding="utf-8")
        args += ["--maps", maps_json, "--tex", tex, "--padding", imaging.unwrap_padding(tex), "--margin", imaging.dilation_px(tex),
                 "--rebake", "always" if params.get("rebake") else "auto"]
    sc.progress(0.1, "Blender prep")
    await sc.blender("prep.py", args, log_name="prep.log")
    report = json.loads((sc.dir / "prep_report.json").read_text())
    sc.register("glb", out)
    sc.register("report", sc.dir / "prep_report.json")
    rebaked = bool(report.get("rebaked"))
    maps = {k: Path(p) for k, p in (report.get("rebake_maps") or {}).items() if Path(p).is_file()} if rebaked else dict(mesh_maps)
    if rebaked:
        for key, p in maps.items():
            sc.register(key, p)
    else:
        link_textures(sc, keep_normal=keep_normal)
    sc.set_metrics(triangles=report.get("triangles"), vertices=report.get("vertices"), dims=report.get("dims_m"),
                   size_extent_m=report.get("size_extent_m"),
                   non_manifold_edges=report.get("non_manifold_edges"), boundary_edges=report.get("boundary_edges"),
                   degenerate_faces=report.get("degenerate_faces"), uv_layers=report.get("uv_layers"), bones=report.get("bones"),
                   rebaked=rebaked, rebake_reason=report.get("rebake_reason"), rebake_seconds=report.get("rebake_seconds") or 0.0,
                   decimated=bool(report.get("decimated")), texture_size=tex)
    if report.get("grid"):
        sc.set_metrics(**grid_metrics(report["grid"]))
        tiling = report.get("tiling_png")
        if tiling and Path(tiling).is_file():
            sc.register("tiling", Path(tiling))
    if maps.get("base_color"):
        sc.progress(0.7, "Checking the textures")
        bake_glb = src if not rebaked else out
        look = texture_look(rec, (sc.project.style_lock or {}).get("palette"))
        result = await asyncio.to_thread(texture_pass, out, bake_glb, maps, sc.dir, tex, surface["delight"], surface["pbr_fix"],
                                         rebaked, look)
        for key, p in result["files"].items():
            sc.register(key, p)
        sc.set_metrics(**result["metrics"], delight_mode=surface["delight"], delight_source=surface["delight_source"])
        # The face detail pass (stages/face_detail.py) on the final maps: it may give the face more of the texture
        # (every map baked again onto the new layout, the GLB replaced) and paints the face into the base colour.
        current = {**maps, **{k: Path(p) for k, p in result["files"].items() if k in TEXTURE_KEYS}}
        sc.set_params(face_detail=params.get("face_detail") is not False, face_strength=face_detail.strength_of(params))
        fd = await face_detail.run(sc, out, current, rec, params)
        for key, p in fd["files"].items():
            sc.register(key, p)
        sc.set_metrics(**fd["metrics"])
        if fd["relayout"]:
            sc.set_metrics(**await asyncio.to_thread(layout_measures, out, tex))
    elif not keep_normal:
        await asyncio.to_thread(imaging.glb_replace_images, out, drop_normal=True)
    await judge(sc)


def grid_metrics(g: dict[str, Any]) -> dict[str, Any]:
    """The prep metrics for a piece on the grid, from the Blender report's "grid" block. grid_size_m is the snapped
    size as [width, depth, height] in metres (the same numbers as dims, which are Blender x, y, z)."""
    dims = [round(float(v), 4) for v in (g.get("dims_m") or [])]
    return {"grid_m": g.get("grid_m"), "grid_footprint_cells": g.get("footprint"), "grid_cells": g.get("cells"),
            "grid_size_m": dims, "grid_height_m": round(float(g.get("height_m") or 0), 4), "grid_kind": g.get("kind"),
            "grid_fit": bool(g.get("fit")), "grid_states": g.get("states"), "grid_scale": g.get("scale"),
            "grid_size_before_m": [round(float(v), 4) for v in (g.get("dims_before_m") or [])],
            "tile_gap_cm": g.get("tile_gap_cm"), "pivot_corner": True}


def resolve_surface(style: dict[str, Any] | None, params: dict[str, Any]) -> dict[str, Any]:
    """{delight: remove|keep, delight_source: chosen|style, pbr_fix: bool}. delight "auto" (or unset) follows the
    style's delight_default (keep when the style says nothing); pbr_fix unset follows the style's pbr_fix_default."""
    style = style or {}
    choice = params.get("delight") if params.get("delight") in DELIGHT_MODES else "auto"
    if choice == "auto":
        mode = "remove" if style.get("delight_default") == "remove" else "keep"
        source = "style"
    else:
        mode, source = choice, "chosen"
    pbr = params.get("pbr_fix")
    pbr_fix = bool(style.get("pbr_fix_default", False)) if pbr is None else bool(pbr)
    return {"delight": mode, "delight_source": source, "pbr_fix": pbr_fix}


def resolve_shading(rec: dict[str, Any], params: dict[str, Any]) -> dict[str, str]:
    """{shading: smooth | flat, source: chosen | recipe}: the shading knob, else the recipe's."""
    choice = params.get("shading")
    if choice in ("smooth", "flat"):
        return {"shading": choice, "source": "chosen"}
    return {"shading": (rec.get("surface") or {}).get("shading") or "smooth", "source": "recipe"}


def prep_triangle_limit(rec: dict[str, Any], mesh_params: dict[str, Any] | None, budget: dict[str, Any]) -> dict[str, Any]:
    """{limit, source}: the triangle count prep shrinks the model to. The recipe's target (the platform budget for
    looks that keep it), unless the mesh version chose a larger count itself; then the platform budget."""
    platform = budget.get("triangles")
    target = (rec.get("mesh") or {}).get("triangles") or platform
    mp = mesh_params or {}
    chosen = mp.get("target_faces_source") == "chosen" or (mp.get("target_faces") and "target_faces_source" not in mp
                                                                and int(mp.get("target_faces") or 0) != int(platform or 0))
    if chosen and target and int(mp.get("target_faces") or 0) > int(target):
        return {"limit": platform, "source": "platform"}
    if target and platform and int(target) < int(platform):
        return {"limit": int(target), "source": "recipe"}
    return {"limit": platform, "source": "platform"}


MIN_TEXTURE_PALETTE = 16


def texture_look(rec: dict[str, Any], palette: Any = None) -> dict[str, Any]:
    """What texture_pass does to the maps for the recipe: {cap, crisp, pixel_snap, palette (rgb list or None),
    palette_colors, normal_map, look}. The style lock's palette is the texture palette for crisp looks only."""
    m, srf = rec.get("mesh") or {}, rec.get("surface") or {}
    crisp = srf.get("texture_filter") == "crisp"
    pal = imaging.parse_palette(palette) if crisp else []
    # A handful of project colours guides the picture; forced onto the textures it repaints the model (an 8-colour
    # preset turned the goblin's skin purple). Only a palette with enough colours to hold skin, cloth and metal is used
    # as the texture palette; a smaller one stays a prompt hint and the textures keep their own limited colours.
    if len(pal) < MIN_TEXTURE_PALETTE:
        pal = []
    return {"cap": m.get("texture"), "crisp": crisp, "pixel_snap": bool(srf.get("pixel_snap", crisp)),
            "palette": pal or None, "palette_colors": srf.get("palette_colors") if crisp else None,
            "normal_map": bool(m.get("normal_map", True)), "style": rec.get("style")}


def texture_size_of(maps: dict[str, Path], mesh_params: dict[str, Any], budget_texture: int | None) -> int:
    """The atlas size: the base colour's width, else the mesh version's texture_size, else the platform budget."""
    if maps.get("base_color"):
        return int(imaging.image_size(maps["base_color"])[0])
    return int(mesh_params.get("texture_size") or budget_texture or 2048)


def texture_pass(final_glb: Path, bake_glb: Path, maps: dict[str, Path], out_dir: Path, tex: int, delight_mode: str,
                 pbr_fix: bool, fill: bool = False, look: dict[str, Any] | None = None) -> dict[str, Any]:
    """Measure and correct the final textures (pure CPU work, run in a thread).

    Measures on the final mesh's layout: atlas_used, texture_padding_px, bake_mesh_hash, export_mesh_hash and
    bake_match (share of the shipped UV triangles that the maps were baked on); the light score; the PBR ranges.
    Corrects: de-light when delight_mode is remove and the score is over the limit; with pbr_fix, colours into range and
    metallic toward 0 or 1; always, roughness up to 0.04. Changed maps are written to out_dir; a changed base colour
    keeps the previous one as base_color_original.png. With fill (maps re-baked in Blender, black past the bake
    margin), each map is first grown into the empty space and the far space filled (imaging.fill_empty), in place.
    With look (texture_look: the recipe), last: maps shrunk to look.cap, the base colour limited to the palette for
    crisp looks, the normal map dropped when the look has none, and the GLB sampled nearest-neighbour for crisp looks;
    the layout measures above stay those of the bake size.
    Returns {metrics, files: {key: path}}."""
    t0 = time.time()
    metrics: dict[str, Any] = {}
    files: dict[str, Path] = {}
    uvf = imaging.load_uv_mesh(final_glb)
    if uvf is None:
        return {"metrics": {"texture_pass_error": "the final model has no texture layout"}, "files": files}
    uvb = uvf if Path(bake_glb) == Path(final_glb) else imaging.load_uv_mesh(bake_glb)
    if fill:
        masks: dict[int, Any] = {}
        for path in maps.values():
            s = imaging.image_size(path)[0]
            if s not in masks:
                masks[s] = imaging.rasterise_uv(uvf, s)["labels"] > 0
            imaging.dilate_png(path, masks[s], imaging.dilation_px(s), fill_rest=True)
    base = imaging.read_map(maps["base_color"])
    size = base.shape[0]
    ras = imaging.rasterise_uv(uvf, size)
    mask = ras["labels"] > 0
    labels_tex = ras["labels"] if size == tex else imaging.rasterise_uv(uvf, tex)["labels"]
    pad = imaging.unwrap_padding(tex)
    bake_hash, export_hash = imaging.uv_hash(uvb), imaging.uv_hash(uvf)
    match = 1.0 if bake_hash == export_hash else imaging.uv_match_fraction(uvb, uvf)
    metrics.update(atlas_used=round(imaging.atlas_used(labels_tex), 4), texture_padding_px=imaging.gutter_px(labels_tex, pad // 2 + 2),
                   texture_padding_target_px=pad, bake_mesh_hash=bake_hash, export_mesh_hash=export_hash,
                   bake_match=None if match is None else round(match, 4))

    ao = imaging.read_map(maps.get("ao"), size, grey=True)
    met = imaging.read_map(maps.get("metallic"), size, grey=True)
    rough = imaging.read_map(maps.get("roughness"), size, grey=True)
    light = imaging.light_score(base, mask, ao, ras["up"])
    stats = imaging.pbr_stats(base, met, rough, mask)
    metrics.update(light_score_before=light["score"], light_corr_ao=light["corr_ao"], light_corr_up=light["corr_up"],
                   pbr_before=stats)

    new_base, new_met, new_rough = base, met, rough
    strength = 0.0
    if delight_mode == "remove":
        strength = imaging.delight_strength(light["score"], LIGHT_SCORE_LIMIT)
        if strength > 0:
            new_base = imaging.delight(new_base, mask, ao, ras["up"], strength)
    if pbr_fix:
        new_base = imaging.clamp_albedo(new_base, new_met)
        new_base[~mask] = base[~mask]
    # Half-metal surfaces look dull and grey in every style (the mesh engine writes 0.2 to 0.5 on skin and leather),
    # so metal is made clearly metal or clearly not whenever a real share of the surface sits in between.
    metal_sharpened = False
    if new_met is not None and (pbr_fix or float(stats.get("metal_mixed") or 0.0) > METAL_MIXED_MAX):
        new_met = imaging.sharpen_metallic(new_met)
        metal_sharpened = True
    if new_rough is not None and stats.get("roughness_low"):
        new_rough = imaging.clamp_roughness(new_rough)

    out_dir = Path(out_dir)
    if new_base is not base:
        original = out_dir / "base_color_original.png"
        shutil.copy2(maps["base_color"], original)
        files["base_color_original"] = original
        files["base_color"] = imaging.write_map(new_base, out_dir / "base_color.png")
    for key, new, old in (("metallic", new_met, met), ("roughness", new_rough, rough)):
        if new is not None and new is not old:
            files[key] = imaging.write_map(new, out_dir / f"{key}.png")
    corrected = bool(files)
    look_metrics: dict[str, Any] = {}
    mr_size = None
    if look:
        look_metrics, mr_size = apply_look(look, uvf, maps, files, new_base, size, out_dir)
    if files or (look and (look.get("crisp") or not look.get("normal_map", True))):
        imaging.glb_replace_images(final_glb, base_color=files.get("base_color"), metallic=files.get("metallic"),
                                   roughness=files.get("roughness"), drop_normal=bool(look) and not look.get("normal_map", True),
                                   nearest=bool(look and look.get("crisp")), mr_size=mr_size)
    metrics.update(look_metrics)
    after = imaging.light_score(new_base, mask, ao, ras["up"]) if new_base is not base else light
    metrics.update(light_score=after["score"], delight_applied=strength > 0, delight_strength=round(strength, 3),
                   pbr_fix_applied=bool(pbr_fix), metal_sharpened=metal_sharpened, roughness_clamped=new_rough is not rough,
                   pbr=imaging.pbr_stats(new_base, new_met, new_rough, mask) if corrected else stats,
                   texture_pass_seconds=round(time.time() - t0, 2))
    return {"metrics": metrics, "files": files}


def layout_measures(final_glb: Path, tex: int) -> dict[str, Any]:
    """The texture layout measures of texture_pass, again, after the face detail pass laid the texture out anew and
    baked every map onto the final mesh (so the maps match it by construction)."""
    uvf = imaging.load_uv_mesh(final_glb)
    if uvf is None:
        return {}
    labels = imaging.rasterise_uv(uvf, tex)["labels"]
    pad = imaging.unwrap_padding(tex)
    h = imaging.uv_hash(uvf)
    return {"atlas_used": round(imaging.atlas_used(labels), 4), "texture_padding_px": imaging.gutter_px(labels, pad // 2 + 2),
            "texture_padding_target_px": pad, "bake_mesh_hash": h, "export_mesh_hash": h, "bake_match": 1.0, "rebaked": True,
            "rebake_reason": "face detail"}


def apply_look(look: dict[str, Any], uvf: dict[str, Any], maps: dict[str, Path], files: dict[str, Path], new_base: Any, size: int,
               out_dir: Path) -> tuple[dict[str, Any], int | None]:
    """The recipe's texture treatment inside texture_pass (files is updated in place). -> (metrics, the size the
    metallic-roughness image of the GLB should be shrunk to, or None)."""
    out_dir = Path(out_dir)
    cap = int(look.get("cap") or size)
    target = min(size, cap)
    crisp, snap = bool(look.get("crisp")), bool(look.get("pixel_snap"))
    palette = look.get("palette")
    colours = look.get("palette_colors")
    limit_colours = crisp and bool(palette or colours)
    m: dict[str, Any] = {"texture_size_shipped": target, "texture_crisp": crisp, "normal_map": bool(look.get("normal_map", True))}
    if target < size or limit_colours:
        if "base_color_original" not in files:
            original = out_dir / "base_color_original.png"
            if Path(maps["base_color"]).resolve() != original.resolve():
                shutil.copy2(maps["base_color"], original)
            files["base_color_original"] = original
        im = Image.fromarray((np.clip(new_base, 0, 1) * 255).round().astype("uint8"), "RGB")
        if target < size:
            im = im.resize((target, target), Image.NEAREST if snap else Image.BOX)
        arr = np.asarray(im, dtype="float32") / 255.0
        mask_t = imaging.rasterise_uv(uvf, target)["labels"] > 0
        if limit_colours:
            arr = imaging.quantise_colours(arr, mask_t, colours, palette)
        files["base_color"] = imaging.write_map(arr, out_dir / "base_color.png")
        m["texture_colours"] = imaging.colour_count(files["base_color"], mask_t)
        m["palette_source"] = "style lock" if palette else ("recipe" if limit_colours else None)
        m["palette_limit"] = len(palette) if palette else (int(colours) if limit_colours else None)
    keys = ("metallic", "roughness", "ao") + (("normal",) if look.get("normal_map", True) else ())
    for key in keys:
        src = files.get(key) or maps.get(key)
        if src is None or not Path(src).is_file():
            continue
        if imaging.image_size(src)[0] > target:
            files[key] = imaging.shrink_image(src, target, snap, out_dir / f"{key}.png")
    return m, (target if target < size else None)


def surface_checks(metrics: dict[str, Any], params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The texture checks from the metrics texture_pass wrote: bake_matches, texture_padding, atlas_used (G05, G03),
    baked_light, albedo_range, metal_mixed (G07, G08). Nothing when the pass did not run."""
    m = metrics or {}
    p = params or {}
    out: list[dict[str, Any]] = []
    if m.get("export_mesh_hash") is None:
        return out
    match = m.get("bake_match")
    if match is not None:
        out.append(quality.check("bake_matches", float(match) >= BAKE_MATCH_MIN, round(float(match), 4), BAKE_MATCH_MIN,
                                 vars={"rebaked": bool(m.get("rebaked"))}))
    pad = int(m.get("texture_padding_target_px") or imaging.unwrap_padding(int(m.get("texture_size") or 2048)))
    gap = m.get("texture_padding_px")
    if gap is not None:
        out.append(quality.check("texture_padding", int(gap) >= math.ceil(PADDING_PASS_FRACTION * pad), int(gap), pad))
    if m.get("atlas_used") is not None:
        out.append(quality.check("atlas_used", float(m["atlas_used"]) >= ATLAS_USED_MIN, round(float(m["atlas_used"]), 4), ATLAS_USED_MIN,
                                 vars={"symmetric": bool(p.get("symmetric"))}))
    if m.get("light_score") is not None:
        kept = m.get("delight_mode") == "keep"
        score = float(m["light_score"])
        out.append(quality.check("baked_light", kept or score <= LIGHT_SCORE_LIMIT, round(score, 4), LIGHT_SCORE_LIMIT,
                                 vars={"kept": kept, "removed": bool(m.get("delight_applied"))}))
    pbr = m.get("pbr") or {}
    fix_on = bool(m.get("pbr_fix_applied"))
    # A look that keeps its painted shading also keeps its painted darks and lights: not a fault there.
    kept_look = m.get("delight_mode") == "keep" and not fix_on
    if pbr.get("albedo_out") is not None:
        in_range = float(pbr["albedo_out"]) <= ALBEDO_OUT_MAX
        out.append(quality.check("albedo_range", in_range or kept_look, float(pbr["albedo_out"]), ALBEDO_OUT_MAX,
                                 vars={"pbr_fix_on": fix_on, "kept": kept_look and not in_range, "black": pbr.get("albedo_black"),
                                       "white": pbr.get("albedo_white")}))
    if pbr.get("metal_mixed") is not None:
        # Once metal has been sharpened there is nothing more the person can do, so only a large leftover warns.
        metal_limit = METAL_MIXED_AFTER_FIX_MAX if (fix_on or m.get("metal_sharpened")) else METAL_MIXED_MAX
        out.append(quality.check("metal_mixed", float(pbr["metal_mixed"]) <= metal_limit, float(pbr["metal_mixed"]), metal_limit,
                                 vars={"pbr_fix_on": fix_on or bool(m.get("metal_sharpened"))}))
    return out


def look_checks(rec: dict[str, Any], report: dict[str, Any], metrics: dict[str, Any], files: dict[str, Path | None],
                category_id: str | None = None) -> list[dict[str, Any]]:
    """look_applied (warn): for a recipe that changes what good means (fewer triangles, flat shading, small or crisp
    textures, no normal map), whether the shipped model matches it, in one plain sentence. Nothing for looks that
    keep the full pipeline, or when the material category has no mesh."""
    if category_id == "material":
        return []
    mesh, srf = rec.get("mesh") or {}, rec.get("surface") or {}
    special = (mesh.get("triangle_scale", 1.0) < 1.0 or srf.get("shading") == "flat" or not mesh.get("normal_map", True)
               or srf.get("texture_filter") == "crisp" or (mesh.get("texture_cap") and mesh["texture_cap"] < (mesh.get("budget_texture") or 0)))
    if not special:
        return []
    problems: list[str] = []
    base = files.get("base_color")
    size = imaging.image_size(base)[0] if base and Path(base).is_file() else None
    cap = mesh.get("texture")
    if size and cap and size > cap:
        problems.append(f"the textures are {size} px, not {cap} px")
    if not mesh.get("normal_map", True) and files.get("normal") is not None:
        problems.append("it still has a normal map")
    shading = metrics.get("shading_applied") or report.get("shading")
    if srf.get("shading") == "flat" and shading not in (None, "flat") and metrics.get("shading_source") != "chosen":
        problems.append("its shading is smooth")
    lim = metrics.get("palette_limit")
    used = metrics.get("texture_colours")
    if lim and used and int(used) > int(lim):
        problems.append(f"its colours number {used}, more than {lim}")
    parts = []
    if mesh.get("triangle_scale", 1.0) < 1.0 and mesh.get("triangles"):
        parts.append(f"about {int(mesh['triangles']):,} triangles")
    if (metrics.get("shading_applied") or srf.get("shading")) == "flat":
        parts.append("flat faces")
    if cap:
        parts.append(f"{int(size or cap)} px textures")
    if srf.get("texture_filter") == "crisp":
        parts.append("crisp pixels" + (f" in {used} colours" if used else ""))
    if not mesh.get("normal_map", True):
        parts.append("no surface bumps")
    known = {s["id"]: s for s in reference.styles()}
    look = (known.get(rec.get("style")) or {}).get("look") or rec.get("style")
    summary = ", ".join(parts)
    return [quality.check("look_applied", not problems, summary, rec.get("id"),
                          vars={"look": look, "summary": summary, "problem": "; ".join(problems)})]


def link_textures(sc: StageContext, keep_normal: bool = True, stages: tuple[str, ...] = ("mesh",)) -> None:
    """Point the texture keys at the PNGs of the nearest earlier version of `stages` (the mesh stage's for prep; the
    rig stage passes prep, whose maps are the ones that ship) so viewers find them without copying (not the normal
    map when the recipe's look has none)."""
    from .. import library

    for key in ("base_color", "normal", "metallic", "roughness", "ao"):
        if key == "normal" and not keep_normal:
            files = sc.version.files
            files.pop("normal", None)
            sc.version.files = files
            continue
        p = sc.lineage_file(key, stages)
        if p is not None:
            files = sc.version.files
            files[key] = library.rel_path(p, sc.settings)
            sc.version.files = files
    sc.save()


def prep_checks(report: dict[str, Any], params: dict[str, Any], metrics: dict[str, Any], category_id: str,
                triangle_budget: int | None, tri_vars: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Geometry, size along the version's axis (on the grid for scenery: grid_fit and tiles_cleanly instead), and
    facing when the mesh audit gave a trusted fit."""
    spec = reference.size_spec(category_id)
    on_grid = category_id in reference.GRID_CATEGORIES and bool(report.get("grid"))
    size = None if on_grid else (params.get("size_m") or params.get("height_m"))
    axis = params.get("size_axis") or ("height" if not params.get("size_m") and params.get("height_m") else spec["axis"])
    word = params.get("size_word") or (spec["word"] if axis == spec["axis"] else reference.AXIS_WORD_DEFAULT.get(axis, "tall"))
    checks = quality.geometry_checks(report, triangle_budget or None, float(size) if size else None, size_axis=axis, axis_word=word)
    if tri_vars:
        for c in checks:
            if c.get("name") == "triangle_budget":
                c["vars"] = {**(c.get("vars") or {}), **tri_vars}
    if on_grid:
        checks += quality.grid_checks(report["grid"], params)
    best = metrics.get("face_best_turn")
    if best is not None:
        checks.append(quality.facing_check(best, metrics.get("face_yaw_applied") or 0))
    return checks


async def judge(sc: StageContext) -> None:
    rp = sc.file("report")
    if rp is None:
        return
    report = json.loads(Path(rp).read_text())
    rec = stage_recipe(sc)
    m = sc.version.metrics
    limit = m.get("triangle_limit") or sc.budget["triangles"]
    tri_vars = None
    if m.get("triangle_limit_source") == "recipe":
        from .mesh import look_vars

        tri_vars = {**look_vars(rec, sc.budget), "recipe": True}
    checks = prep_checks(report, sc.version.params, m, sc.asset.category, limit, tri_vars)
    checks += surface_checks(m, sc.version.params)
    checks += look_checks(rec, report, m, {k: sc.file(k) for k in TEXTURE_KEYS}, sc.asset.category)
    checks += face_detail.face_checks(m)
    sc.set_metrics(**quality.summarise(checks))


def _merge(parent_params: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Parent params under the new ones. A new size_m without its own axis drops the inherited axis and word (the
    knob is labelled with the category's axis), and the old height_m never outlives a new size_m."""
    merged = {**parent_params, **params}
    if params.get("size_m") is not None:
        merged.pop("height_m", None)
        if "size_axis" not in params:
            merged.pop("size_axis", None)
            merged.pop("size_word", None)
    return merged


async def run(sc: StageContext, params: dict[str, Any]) -> None:
    await _prep(sc, params)


async def refine(sc: StageContext, instruction: str, view: str | None, mask_png: Path | None, params: dict[str, Any]) -> None:
    parent = sc.parent
    merged = {**_merge(parent.params if parent else {}, params), "instruction": instruction}
    await _prep(sc, merged)


async def adjust(sc: StageContext, params: dict[str, Any]) -> None:
    parent = sc.parent
    merged = _merge(parent.params if parent else {}, params)
    await _prep(sc, merged)
