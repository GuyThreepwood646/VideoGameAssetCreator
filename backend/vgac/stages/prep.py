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

More triangles on the head (vgac/headtris.py, blender/headweight.py): when the 3D model was built over the budget on
purpose (mesh params head_room), the budget decimation is weighted so the head keeps about twice the triangles an even
decimation leaves it; the option head_triangles turns it off (then the decimation is even). The report's head counts
become metrics (head_weighted, head_triangles, head_triangles_even, head_weight_seconds) and the triangle_budget
check sentence says how many triangles the head has. A model built at the budget is not decimated, as before.

Hair from the library (vgac/hairlib.py, docs/HAIR-LIBRARY-PLAN.md sections 3, 5 and 6): when the 3D version was built
bald (its hair_source is "library"), the style is resolved (params.hair_style, then the asset's pick, then the 3D
step's best), the body is decimated to the budget less hair_allowance, the hair pass and the fused hair material stand
back, and after skin metal hairfit.attach fits the style to the final body as separate files (prep.glb stays the bald
body; body_glb_hash is its sha1). An adjust that changes only hair_style or hair_cover with a style that fits the
parent's allowance copies the parent's body files and attaches again (hairlib.only_hair_change). Checks hair_fit,
hair_skin_through and hair_triangles.
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

from .. import facing, guidance, hairlib, hairmat, headtris, imaging, normalfix, quality, reference, skinmetal, srcproject, texcolour
from ..blender import gridfit
from . import face_detail, hair_detail
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


def resolve_facing(category: dict[str, Any], face_yaw: Any, mesh_metrics: dict[str, Any] | None,
                   toes: dict[str, Any] | None = None) -> dict[str, Any]:
    """{face_yaw_applied, face_best_turn, facing_source, face_remainder_deg, and the two tests' numbers (facing.metrics_of)}.
    face_best_turn is None when the mesh audit has no fit or its silhouette match is below the category floor (then
    nothing is checked). The outline test (the mesh audit) decides, with the toes test (`toes`, facing.toes_of_file on
    the 3D model) as tie-break and second opinion; without toes (not a person, no feet found) it is as it always was.
    face_remainder_deg is how far the model still faces from straight ahead after the applied turn (the quarter-turn
    snap leaves up to 45 degrees), for the face pass."""
    m = mesh_metrics or {}
    outline = None
    offset, iou = m.get("silhouette_offset"), m.get("silhouette_iou")
    floor = float(category.get("silhouette_min") or DEFAULT_SILHOUETTE_MIN)
    if offset is not None and iou is not None and float(iou) >= floor:
        outline = quality.best_turn(offset, int(m.get("silhouette_sign") or 1))
    d = facing.decide(outline, m.get("silhouette_turn_scores"), toes)
    best = d["turn"]
    if face_yaw in (None, "", "auto"):
        applied = quality.auto_turn(best)
        source = ("toes" if d["source"] == "toes" else "audit") if best is not None else "as built"
    else:
        applied = int(float(face_yaw)) % 360
        source = "chosen"
    # The head's turn left over after the applied turn. The feet give a finer reading than the outline's 15 degree steps,
    # so they are used when they were found and agree with the decision; else the decided turn itself.
    basis = d["toes_turn"] if d["toes_found"] and d["agree"] else best
    remainder = None if basis is None else round(max(-45.0, min(45.0, facing.signed(basis - applied))), 1)
    return {"face_yaw_applied": applied, "facing_source": source, "face_remainder_deg": remainder, **facing.metrics_of(d)}


async def _prep(sc: StageContext, params: dict[str, Any]) -> None:
    if sc.asset.category == "material":
        sc.set_metrics(skipped=True, reason="material category has no mesh")
        return
    src = sc.lineage_file("glb", ("mesh",))
    if src is None:
        raise FileNotFoundError("no mesh glb in lineage")
    size = resolve_size(sc.asset.category, params, sc.asset.prompt)
    mesh_v = sc.stage_version("mesh")
    toes = await asyncio.to_thread(facing.toes_of_file, src, sc.asset.category)
    facing_ = resolve_facing(sc.category, params.get("face_yaw", "auto"), mesh_v.metrics if mesh_v else None, toes)
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
    hair = hair_plan(sc, params, mesh_v, tri_limit["limit"]) if hairlib.is_library(sc) else None
    if hair is not None:
        # The body is decimated to the budget less the hair's allowance; the hair pass sees the library and stands back.
        params = {**params, "hair_source": hairlib.LIBRARY}
        tri_limit = {**tri_limit, "limit": hair["body_limit"]}
    head = headtris.plan(rec, sc.asset.category, params, shading["shading"], mesh_v_params)
    sc.set_params(size_m=size["size_m"], size_axis=size["size_axis"], size_word=size["size_word"],
                  face_yaw=face_yaw if face_yaw not in (None, "") else "auto", symmetric=symmetric, pivot=pivot, smooth_angle=smooth,
                  delight=params.get("delight") or "auto", pbr_fix=surface["pbr_fix"], rebake=bool(params.get("rebake") or False),
                  shading=params.get("shading") if params.get("shading") in reference.SHADING_OPTIONS else "auto", **(grid or {}))
    if headtris.applies(rec, sc.asset.category):
        sc.set_params(**{headtris.PARAM: headtris.on(params), headtris.HAND_PARAM: headtris.hands_on(params)})
    if hair is not None:
        sc.set_params(hair_source=hairlib.LIBRARY, hair_style=hair["style"], hair_style_source=hair["style_source"],
                      hair_allowance=hair["allowance"], hair_cover=hair["cover"])
    sc.set_metrics(size_source=size["size_source"], shading_applied=shading["shading"], shading_source=shading["source"],
                   triangle_limit=tri_limit["limit"], triangle_limit_source=tri_limit["source"],
                   recipe=reference.recipe_record(rec, "mesh", "surface"), **facing_)
    sc.use_model("blender")
    out = sc.dir / "prep.glb"
    args: list[Any] = [src, out, "mesh", "--size", size["size_m"], "--size-axis", size["size_axis"], "--yaw", facing_["face_yaw_applied"],
                       "--pivot", pivot, "--smooth-angle", smooth, "--shading", shading["shading"]]
    if tri_limit["limit"]:
        args += ["--budget", int(tri_limit["limit"])]
        if head["weighted"]:
            args += ["--protect-head", headtris.HEAD_SHARE, "--head-gain", headtris.HEAD_GAIN]
            if headtris.hands_on(params):
                args += ["--hand-gain", headtris.HAND_GAIN]
    if symmetric:
        args.append("--symmetric")
    if grid:
        args += ["--grid", grid["grid_m"], "--grid-footprint", grid["grid_footprint"], "--grid-height", grid["grid_height"],
                 "--tiling", sc.dir / "tiling.png"]
    keep_normal = bool(rec["mesh"]["normal_map"])
    mesh_maps = {k: sc.lineage_file(k, ("mesh",)) for k in TEXTURE_KEYS if keep_normal or k != "normal"}
    mesh_maps = {k: p for k, p in mesh_maps.items() if p is not None}
    repaired = await repair_normal(sc, mesh_v, src, mesh_maps, keep_normal)
    if repaired:
        src, mesh_maps["normal"] = repaired["glb"], repaired["normal"]
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
        if repaired:
            sc.register("normal", repaired["normal"])  # the flipped copy, not the 3D step's own map
    sc.set_metrics(triangles=report.get("triangles"), vertices=report.get("vertices"), dims=report.get("dims_m"),
                   size_extent_m=report.get("size_extent_m"),
                   non_manifold_edges=report.get("non_manifold_edges"), boundary_edges=report.get("boundary_edges"),
                   degenerate_faces=report.get("degenerate_faces"), uv_layers=report.get("uv_layers"), bones=report.get("bones"),
                   rebaked=rebaked, rebake_reason=report.get("rebake_reason"), rebake_seconds=report.get("rebake_seconds") or 0.0,
                   decimated=bool(report.get("decimated")), texture_size=tex, triangles_before_budget=report.get("triangles_before_budget"),
                   **head_metrics(report, head))
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
        # Paint the picture's own pixels into the base colour (vgac/srcproject.py): by itself for a character on a
        # smooth look; any model when the version asks for it.
        body_views: dict[str, Any] = {}  # the fitted pictures, kept to paint again if the face pass packs the texture again
        # the camera yaw the face is seen straight on from: the picture projection finds the face region from there, and
        # the face pass paints from there
        head_yaw = face_detail.head_yaw_from_remainder(facing_.get("face_remainder_deg"))
        if srcproject.on(params, sc.asset.category, look):
            current = await srcproject.run_stage(sc, out, current, params, keep=body_views, head_yaw=head_yaw)

        async def paint_body_again(glb_: Path, maps_: dict[str, Path]) -> dict[str, Path]:
            return await srcproject.run_again(sc, glb_, maps_, body_views)

        # "The face matters" off on the asset: the pass is skipped unless this run turns it on
        face_matters = getattr(sc.asset, "face_matters", None) is not False
        if params.get("face_detail") is None and not face_matters:
            params = {**params, "_face_matters": False}
        sc.set_params(face_detail=params.get("face_detail") is not False and face_matters if params.get("face_detail") is None
                      else params.get("face_detail") is not False, face_strength=face_detail.strength_of(params),
                      face_density=face_detail.density_choice(params), face_closeup=face_detail.closeup_on(params),
                      face_sides=params.get("face_sides") is not False, face_closeup_guided=params.get("face_closeup_guided") is not False)
        # The hair detail pass (stages/hair_detail.py): the hair is found first, so the face pass's one relayout can give
        # it more texels too; it is painted after the face, on the final layout, and never writes the face's texels.
        sc.set_params(hair_detail=hair_detail.on(params), hair_strength=hair_detail.strength_of(params))
        if sc.asset.category == "humanoid":
            sc.set_params(**{hairmat.PARAM: hairmat.option(params)})  # hair as its own material (vgac/hairmat.py)
        hf = await hair_detail.find(sc, out, current, rec, params)
        face_detail_seed = params.get("face_detail_seed")
        fd = await face_detail.run(sc, out, current, rec, params, hair_plan=hf.get("plan"), face_detail_seed=face_detail_seed,
                                   after_relayout=paint_body_again if body_views.get("views") else None,
                                   body_painted=bool(body_views.get("views")),
                                   head_yaw=head_yaw)
        body_views.clear()  # several large pictures per view: let them go
        for key, p in fd["files"].items():
            sc.register(key, p)
        sc.set_metrics(**fd["metrics"])
        if fd["relayout"]:
            sc.set_metrics(**await asyncio.to_thread(layout_measures, out, tex))
        after_face = {**current, **{k: Path(p) for k, p in fd["files"].items() if k in TEXTURE_KEYS}}
        # Skin is never metal (vgac/skinmetal.py): the face and the skin of the head and neck joined to it lose the metal
        # the 3D builder spread onto them from armour and helmets, before the hair pass reads the maps.
        sm = await asyncio.to_thread(skinmetal.clear, out, after_face, sc.asset.category, sc.asset.prompt, sc.dir / "skin")
        if sm["files"].get("metallic") is not None:
            after_face["metallic"] = Path(sm["files"]["metallic"])
            sc.register("metallic", after_face["metallic"])
            await asyncio.to_thread(imaging.glb_replace_images, out, metallic=after_face["metallic"])
        sc.set_metrics(**sm["metrics"])
        hd = await hair_detail.run(sc, out, after_face, rec, params, hf, fd.get("layout"))
        for key, p in hd["files"].items():
            sc.register(key, p)
        sc.set_metrics(**hd["metrics"])
        if hd["relayout"]:
            sc.set_metrics(**await asyncio.to_thread(layout_measures, out, tex, "hair detail"))
        if int(fd.get("base_color_size") or 0) > tex:
            # "High" face detail on a 4096 platform: the base colour ships at 4096, the other maps stay at the atlas size
            sc.set_metrics(base_color_size=int(fd["base_color_size"]), texture_size_shipped=int(fd["base_color_size"]))
    elif not keep_normal:
        await asyncio.to_thread(imaging.glb_replace_images, out, drop_normal=True)
    if hair is not None:
        await attach_hair(sc, out, hair, mesh_v.metrics if mesh_v else {})
    await judge(sc)


HAIR_FILE_PREFIXES = ("hair_", "with_hair_")
HAIR_DIR = "hairlib"


def hair_plan(sc: StageContext, params: dict[str, Any], mesh_v: Any, limit: int | None) -> dict[str, Any]:
    """{style, style_source, allowance, cover, budget, body_limit} for a Clean up on a bald 3D model."""
    mm = (mesh_v.metrics if mesh_v is not None else None) or {}
    style, source = hairlib.resolve_style(params.get("hair_style"), sc.asset, mm)
    allow = params.get("hair_allowance")
    allow = int(allow) if allow not in (None, "") else hairlib.allowance(limit, mm, style)
    body = int(limit) - allow if limit else None
    return {"style": style, "style_source": source, "allowance": allow, "cover": hairlib.cover_of(params), "budget": limit,
            "body_limit": body}


async def attach_hair(sc: StageContext, body: Path, hair: dict[str, Any], mesh_metrics: dict[str, Any],
                      reused_body_of: str | None = None) -> None:
    """hairfit.attach on the final body: the hair as its own files, metrics.hair_library and body_glb_hash. A failure
    is recorded (attached false) and never fails the step."""
    from ..jobs import JobCancelled

    t0 = time.time()
    style = hair.get("style")
    hl_mesh = (mesh_metrics or {}).get("hair_library") or {}
    best = hairlib.candidate(mesh_metrics, hl_mesh.get("best"))
    record: dict[str, Any] = {"style": style, "style_source": hair.get("style_source"), "allowance": hair.get("allowance"),
                              "budget": hair.get("budget"), "cover": hair.get("cover"), "family_source": hl_mesh.get("family_source"),
                              "best_name": (best or {}).get("name"), "candidates_n": len(hl_mesh.get("candidates") or []),
                              "reused_body_of": reused_body_of}
    try:
        if not style:
            raise ValueError("no hairstyle to fit: the 3D step found none and none was picked")
        from .. import hairfit, hairstyles  # lane 1

        need = {k: sc.lineage_file(k, ("turnaround",)) for k in ("hair_picture", "bald_front_cutout", "hair_outline_front")}
        missing = [k for k, p in need.items() if p is None]
        if missing:
            raise FileNotFoundError("the bald pictures from Check all sides are missing: " + ", ".join(missing))
        info = hairstyles.style(style) or {}
        lib_tex = int(((info.get("textures") or {}).get("size")) or hairmat.hair_texture_size(sc.budget))
        tex = min(lib_tex, hairmat.hair_texture_size(sc.budget))
        cand = hairlib.candidate(mesh_metrics, style)
        sc.progress(0.95, "Fitting the hairstyle from the library")
        res = await hairfit.attach(sc, Path(body), style, (cand or {}).get("params"), need["hair_picture"], need["bald_front_cutout"],
                                   need["hair_outline_front"], hair.get("cover") or "normal", tex, sc.dir / HAIR_DIR)
        for key, path in ((res or {}).get("files") or {}).items():
            if path is not None and Path(path).is_file():
                sc.register(key, Path(path))
        record = {**record, "name": info.get("name"), **((res or {}).get("metrics") or {})}
        record.setdefault("attached", True)
        record.setdefault("tex", tex)
        if reused_body_of:
            record["reused_body_of"] = reused_body_of
    except JobCancelled:
        raise
    except Exception as e:  # noqa: BLE001  (the bald body stands; the check says the hair is missing)
        record.update(attached=False, error=str(e)[:300])
    record.setdefault("seconds", round(time.time() - t0, 1))
    sc.set_metrics(hair_library=record, body_glb_hash=hairlib.file_sha1(body))


async def hair_only(sc: StageContext, parent: Any, merged: dict[str, Any], explicit: dict[str, Any]) -> None:
    """A Clean up adjust that changes only the hairstyle or its cover: the parent's body files and measures are copied
    as they are (prep.glb byte for byte, so body_glb_hash stays the same) and only the hair is attached again."""
    from .. import library

    pdir = library.version_dir(sc.asset, parent, sc.settings, create=False).resolve()
    files = sc.version.files
    for key, rel in (parent.files or {}).items():
        if key.startswith(HAIR_FILE_PREFIXES):
            continue
        src = library.abs_path(rel, sc.settings)
        try:
            sub = src.resolve().relative_to(pdir)
        except ValueError:
            files[key] = rel  # a link to an earlier step's file (the 3D step's maps): linked again
            continue
        dest = sc.dir / sub
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        files[key] = library.rel_path(dest, sc.settings)
    sc.version.files = files
    sc.save()
    skip = ("hair_library", "body_glb_hash", "checks", "checks_passed", "checks_failed", "gate_ok")
    sc.set_metrics(**{k: v for k, v in (parent.metrics or {}).items() if k not in skip})
    mesh_v = sc.stage_version("mesh")
    mm = (mesh_v.metrics if mesh_v is not None else None) or {}
    style, source = hairlib.resolve_style(explicit.get("hair_style") or merged.get("hair_style"), sc.asset, mm)
    if not explicit.get("hair_style") and merged.get("hair_style"):
        source = (parent.params or {}).get("hair_style_source") or source
    hair = {"style": style, "style_source": source, "allowance": int(parent.params.get("hair_allowance") or 0),
            "cover": hairlib.cover_of(merged), "budget": ((parent.metrics or {}).get("hair_library") or {}).get("budget")}
    sc.set_params(**{**merged, "hair_source": hairlib.LIBRARY, "hair_style": style, "hair_style_source": source,
                     "hair_allowance": hair["allowance"], "hair_cover": hair["cover"], "hair_only_of": parent.id})
    body = sc.file("glb")
    if body is None:
        raise FileNotFoundError("the clean-up step this builds on has no model file")
    await attach_hair(sc, body, hair, mm, reused_body_of=parent.id)
    await judge(sc)


def _hair_merge(merged: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Under library hair a new Clean up resolves the style and allowance again: an inherited style stays only when
    the person chose it, and the allowance and source are worked out anew. Nothing changes for today's hair."""
    out = dict(merged)
    if "hair_style" not in params and out.get("hair_style_source") != "chosen":
        out.pop("hair_style", None)
    for key in ("hair_style_source", "hair_allowance", "hair_source", "hair_only_of"):
        if key not in params:
            out.pop(key, None)
    return out


NORMAL_FIX_DIR = "normal_fix"


async def repair_normal(sc: StageContext, mesh_v: Any, src: Path, mesh_maps: dict[str, Path], keep_normal: bool) -> dict[str, Any] | None:
    """A 3D-step version from before the normal map fix (vgac/normalfix.py: TRELLIS, a normal map, no marker) has its
    map's green channel upside down. Copies of its GLB and normal map with green flipped go into this version's
    normal_fix folder and the clean-up step bakes from them; the 3D step's own files are never changed, and a marked
    version is never flipped again. -> {glb, normal, glb_images} or None when nothing needed repair."""
    if mesh_v is None or not keep_normal or mesh_maps.get("normal") is None:
        return None
    if not normalfix.needs_fix(mesh_v.params, mesh_v.metrics, True):
        return None
    rep = await asyncio.to_thread(normalfix.repair_inputs, Path(src), Path(mesh_maps["normal"]), sc.dir / NORMAL_FIX_DIR)
    if rep.get("normal") is None:
        return None
    sc.set_metrics(normal_repaired=True, normal_repaired_glb_images=rep["glb_images"])
    return rep


def bake_inputs(sc: StageContext) -> tuple[Path | None, Path | None]:
    """The model and normal map this clean-up version baked from: the flipped copies when it repaired an older 3D
    step's map, else the 3D step's own."""
    d = sc.dir / NORMAL_FIX_DIR
    if (d / "normal.png").is_file() and (d / "model.glb").is_file():
        return d / "model.glb", d / "normal.png"
    return sc.lineage_file("glb", ("mesh",)), sc.lineage_file("normal", ("mesh",))


def head_metrics(report: dict[str, Any], head: dict[str, Any]) -> dict[str, Any]:
    """The prep metrics for the head's triangles from the Blender report: head_triangles always (the final model's top
    13 percent); head_weighted and, when the weighted decimation ran, what an even one would have left the head, what
    the 3D model had there, the factor and the seconds; head_weight_reason when it did not run (off, look, flat,
    no_room) and head_weight_error when it failed and the plain decimation ran instead."""
    out: dict[str, Any] = {"head_triangles": report.get("head_triangles"), "head_weighted": bool(report.get("head_weighted"))}
    if out["head_weighted"]:
        out.update(head_triangles_even=report.get("head_triangles_even"), head_triangles_source=report.get("head_triangles_source"),
                   head_weight_factor=report.get("head_weight_factor"), head_weight_seconds=report.get("head_weight_seconds"))
    else:
        out["head_weight_reason"] = head.get("reason") or ("error" if report.get("head_weight_error") else None)
    if report.get("head_weight_error"):
        out["head_weight_error"] = report["head_weight_error"]
    if report.get("hand_triangles_final") is not None:
        out.update(hand_triangles=report["hand_triangles_final"], hand_triangles_even=report.get("hand_triangles_even"),
                   hand_triangles_source=report.get("hand_triangles_source"))
    return out


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


def layout_measures(final_glb: Path, tex: int, reason: str = "face detail") -> dict[str, Any]:
    """The texture layout measures of texture_pass, again, after the face (or hair) detail pass laid the texture out
    anew and baked every map onto the final mesh (so the maps match it by construction)."""
    uvf = imaging.load_uv_mesh(final_glb)
    if uvf is None:
        return {}
    labels = imaging.rasterise_uv(uvf, tex)["labels"]
    pad = imaging.unwrap_padding(tex)
    h = imaging.uv_hash(uvf)
    return {"atlas_used": round(imaging.atlas_used(labels), 4), "texture_padding_px": imaging.gutter_px(labels, pad // 2 + 2),
            "texture_padding_target_px": pad, "bake_mesh_hash": h, "export_mesh_hash": h, "bake_match": 1.0, "rebaked": True,
            "rebake_reason": reason}


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
    # A look that keeps its painted shading also keeps its painted darks and lights: not a fault there, as long as the
    # base colour stays about as bright as the picture the 3D model was built from (texcolour.kept_look_excused, from
    # texture_match measured in judge). A near-black texture from a bright picture is not the look: it fails in every look.
    match = m.get("texture_match")
    kept_look = m.get("delight_mode") == "keep" and not fix_on and texcolour.kept_look_excused(match)
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
                c.update(guidance.describe_check(c))  # the sentence again, with the new values
    if on_grid:
        checks += quality.grid_checks(report["grid"], params)
    best = metrics.get("face_best_turn")
    if best is not None:
        checks.append(quality.facing_check(best, metrics.get("face_yaw_applied") or 0))
    if metrics.get("face_toes_found") and metrics.get("face_tests_differ_deg") is not None:
        checks.append(quality.facing_tests_check(metrics["face_outline_turn"], metrics["face_toes_turn"], metrics["face_tests_differ_deg"],
                                                 metrics.get("face_best_turn"), metrics.get("face_yaw_applied") or 0))
    up = upscale_check(metrics.get("srcproject_upscale"))
    if up is not None:
        checks.append(up)
    return checks


def upscale_check(up: dict[str, Any] | None) -> dict[str, Any] | None:
    """A warning when the upscaler failed on pictures painted onto the model (srcproject.upscale_sources); None when it
    did not, or when the pass did not run. No card to run it on is only noted in srcproject_upscale (a setup without
    a picture card, as the tests run), not warned about."""
    if not up or not up.get("failed"):
        return None
    note = up.get("note") or srcproject.upscale_note(up)
    failed = len(up["failed"])
    c = quality.check("pictures_sharpened", False, f"{failed} failed", "all sharpened", vars={"reason": note})
    c.setdefault("help", note)
    return c


async def picture_colour(sc: StageContext) -> dict[str, Any] | None:
    """The colour stats of the picture the 3D model was built from (texcolour.picture_stats): the 3D step's own record
    when it has one, else measured from the cut-out in the lineage. None without a picture or a cut-out."""
    mv = sc.stage_version("mesh")
    if mv is not None and (mv.metrics or {}).get("picture_colour"):
        return mv.metrics["picture_colour"]
    from .mesh import _input_image

    try:
        params = (mv.params if mv is not None else None) or {}
        view = params.get("input_view") or sc.category.get("input_view_default") or "concept"
        return await asyncio.to_thread(texcolour.picture_stats, _input_image(sc, view))
    except Exception:  # noqa: BLE001  (no picture: the look's darks stay excused, as before)
        return None


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
    hv = headtris.head_vars(m)
    if hv:
        tri_vars = {**(tri_vars or {}), **hv}
    if hairlib.is_library(sc):
        # The limit is the body's share: the sentence also names the triangles kept for the hair and the whole budget.
        hl = m.get("hair_library") or {}
        tri_vars = {**(tri_vars or {}), "hair_allowance": (sc.version.params or {}).get("hair_allowance"),
                    "platform_triangles": hl.get("budget") or sc.budget.get("triangles")}
    checks = prep_checks(report, sc.version.params, m, sc.asset.category, limit, tri_vars)
    if (m.get("pbr") or {}).get("albedo_out") is not None and "texture_match" not in m:
        # The shipped base colour against the picture, measured once: albedo_range excuses a kept look's darks only
        # while it passes (texcolour.kept_look_excused).
        pic = await picture_colour(sc)
        try:
            tex = await asyncio.to_thread(texcolour.texture_stats, sc.file("base_color"), sc.file("glb"))
        except Exception:  # noqa: BLE001  (no layout to measure: the look's darks stay excused, as before)
            tex = None
        sc.set_metrics(picture_colour=pic, texture_match=texcolour.compare(pic, tex))
        m = sc.version.metrics
    checks += surface_checks(m, sc.version.params)
    checks += look_checks(rec, report, m, {k: sc.file(k) for k in TEXTURE_KEYS}, sc.asset.category)
    checks += face_detail.face_checks(m)
    checks += hair_detail.hair_checks(m)
    from .mesh import fidelity_checks

    mv = sc.stage_version("mesh")  # the finished texture against the picture the 3D model was built from (report-only)
    checks += await fidelity_checks(sc, rec, ((mv.params if mv is not None else None) or {}).get("input_view"))
    if hairlib.is_library(sc):
        hl = m.get("hair_library") or {}
        checks += hairlib.prep_checks(m, hl.get("budget") or sc.budget.get("triangles"),
                                      picked=(sc.version.params or {}).get("hair_style_source") in ("picked", "chosen"))
    if (rec.get("mesh") or {}).get("normal_map", True) and sc.file("normal") is not None:
        from .mesh import upright_check

        nc = await upright_check(sc, "prep", *bake_inputs(sc))
        if nc:
            checks.append(nc)
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
    merged = {**_hair_merge(_merge(parent.params if parent else {}, params), params), "instruction": instruction}
    await _prep(sc, merged)


# "Try a different face" (owner, 2026-11-12): a clean-up again with the same settings but new seeds for the face pass
# only. Without it an adjust inherits the parent's face seeds and draws nearly the same face. A one-off marker: never
# kept on the version.
FACE_FRESH = "face_fresh"
FACE_SEED_KEYS = ("face_detail_seed", "face_seed", "face_closeup_seed", "face_closeup_seed_2")


def fresh_face(sc: StageContext, merged: dict[str, Any]) -> dict[str, Any]:
    """Drop the face pass's seeds (and the marker) from the run's params and from the new version's own params, so the
    pass picks new ones and writes them on the version."""
    out = {k: v for k, v in merged.items() if k not in FACE_SEED_KEYS and k != FACE_FRESH}
    own = dict(sc.version.params or {})
    for k in (*FACE_SEED_KEYS, FACE_FRESH):
        own.pop(k, None)
    sc.version.params = own
    sc.save()
    return out


async def adjust(sc: StageContext, params: dict[str, Any]) -> None:
    parent = sc.parent
    merged = _merge(parent.params if parent else {}, params)
    if params.get(FACE_FRESH):
        await _prep(sc, _hair_merge(fresh_face(sc, merged), params))
        return
    if parent is not None and parent.stage == "prep" and hairlib.is_library(sc):
        mesh_v = sc.stage_version("mesh")
        style = params.get("hair_style") or parent.params.get("hair_style")
        if hairlib.only_hair_change(parent.params, params, hairlib.style_triangles(style, mesh_v.metrics if mesh_v else None)):
            await hair_only(sc, parent, merged, params)
            return
    await _prep(sc, _hair_merge(merged, params))
