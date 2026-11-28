"""Stage 4, game-ready prep (Blender on the host): face forward, scale on the category's size axis, pivot, weld,
optional symmetrize, smooth shading.

Input is the mesh version's model.glb; output prep.glb plus prep_report.json. Quality checks from the report.
The material category records a skip.

Size (G12, light G13): size_m along the category's size_axis (data/categories/*.yaml). When the version does not set
size_m, a size written in the description ("two metres wide") is used, with its axis word; otherwise the category
size. height_m, the old knob, is still read from older versions and means the height.

Facing (G14): face_yaw "auto" turns the model by the nearest quarter turn to the mesh audit's best fit when the fit
is trusted (silhouette match at or above the category floor) and within 20 degrees of a quarter turn; a number turns
it by that many degrees. The facing_tests check reports the outline test and the toes test and whether they agreed,
and warns when a near-tie could not be settled (facing.decide); the pictures are then painted on the decided front half
only (srcproject.FRONT_HALF).

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
When the pictures are painted on (srcproject), the de-light waits (delight_deferred): the projection would paint the
pictures' light back in. The light is measured on the finished colour after hair detail instead, and evened out when
strong (remove_light, vgac/lightfix.py; the colour before is kept as base_color_lit); baked_light then reads that.

Recipes (reference.recipe): the shading is the recipe's (flat for low-poly: every face its own flat normal), unless
the shading knob chooses; the triangle limit is the recipe's target (the platform budget when the mesh version chose
a larger count). The texture pass then gives the maps the recipe's look: no normal map when the look has no surface
bumps; every map shrunk to the recipe's texture size (an even box average), the original base colour kept as
base_color_original.
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
import re
import shutil
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .. import creaturemetal, eyes, face, facing, guidance, hairlib, hairmat, headtris, imaging, layoutspread, library, lightfix, meshchecks, normalfix, quality, reference, skinmetal, srcproject, steptime, texcolour, trifloor
from .. import hair as hairfind
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
OUTLINE_IOU_MIN = 0.92  # outline_kept: lowest outline overlap (IoU) over the views between the model before and after the budget reduction
OUTLINE_REGION_MAX = 0.02  # outline_kept: largest lost or filled patch of an outline, as a share of the outline's area
ALBEDO_CLIPPED_MAX = 0.05  # albedo_range: share of non-metal texels that are pure black (under 4 sRGB) or pure white (over 250)
ALBEDO_DARK_LOOK_FAULT = 0.5  # in a kept look, more than this share outside 30..240 while darker than the picture fails
ALBEDO_DARK_NOTE = 0.15  # and the share of dark texels (under 30) over which the passing sentence says so; black cloth and dark hair are not a fault
METAL_MIXED_MAX = 0.05
METAL_MIXED_AFTER_FIX_MAX = 0.2  # leftover edge texels after the automatic sharpening (ranger: 0.51 before, 0.11 after)
# The measured gutter passes at three quarters of the padding asked for (rasterising the pieces costs a texel or so).
PADDING_PASS_FRACTION = 0.75
DELIGHT_MODES = ("auto", "remove", "keep")


def resolve_size(category_id: str, params: dict[str, Any], description: str | None) -> dict[str, Any]:
    """{size_m, size_axis, size_word, size_source}: the version's size_m (or the old height_m, which means the
    height), else a size from the description, else a common object named in the description (object_sizes.yaml,
    object categories only), else the category size. An explicit size_axis in params wins (a Fix or an earlier
    description size carries one)."""
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
        obj = None if found else reference.object_size(category_id, description)
        if found:
            size, source = float(found["size_m"]), "description"
            if found["axis"]:
                axis, word = found["axis"], found["word"]
        elif obj:
            size, source = obj["size_m"], "object"
            axis, word = obj["axis"], None
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
                   toes: dict[str, Any] | None = None, colour: Any = None, picture: dict[str, Any] | None = None) -> dict[str, Any]:
    """{face_yaw_applied, face_best_turn, facing_source, face_remainder_deg, and the two tests' numbers (facing.metrics_of)}.
    face_best_turn is None when the mesh audit has no fit or its silhouette match is below the category floor (then
    nothing is checked). The outline test (the mesh audit) decides, with the toes test (`toes`, facing.toes_of_file on
    the 3D model) as tie-break and second opinion; without toes (not a person, no feet found) a clear outline stands and a
    near-tie keeps the side the 3D step built as the front unless the colour of the drawn views says otherwise, and says
    whether it is sure (facing.decide).
    face_remainder_deg is how far the model still faces from straight ahead after the applied turn (the quarter-turn
    snap leaves up to 45 degrees), for the face pass. colour: facing.colour_of_files over the drawn front and back views
    and the mesh audit's renders, for a near-tie the toes cannot settle. picture: facing.picture_of_files for the step 1
    picture (categories with facing_by_picture only); when decisive it sets the turn."""
    m = mesh_metrics or {}
    outline = None
    offset, iou = m.get("silhouette_offset"), m.get("silhouette_iou")
    floor = float(category.get("silhouette_min") or DEFAULT_SILHOUETTE_MIN)
    if offset is not None and iou is not None and float(iou) >= floor:
        outline = quality.best_turn(offset, int(m.get("silhouette_sign") or 1))
    d = facing.decide(outline, m.get("silhouette_turn_scores"), toes, colour,
                      picture if category.get("facing_by_picture") else None)
    best = d["turn"]
    # The feet give a finer reading than the outline's 15 degree steps, so the quarter turn is taken from them when they
    # were found and agree with the decision (a knight planted at 60: outline 45, which rounds to 0; toes 60, to 90).
    # (with no outline answer the toes alone do not turn it: facing.decide)
    basis = d["toes_turn"] if d["toes_found"] and d["agree"] else best
    if face_yaw in (None, "", "auto"):
        applied = quality.auto_turn(basis if best is not None else None, sure=bool(d.get("sure", True)))
        source = ("toes" if d["source"] == "toes" else "audit") if best is not None else "as built"
    else:
        applied = int(float(face_yaw)) % 360
        source = "chosen"
    # The head's turn left over after the applied turn, from the same basis.
    remainder = None if basis is None else round(max(-45.0, min(45.0, facing.signed(basis - applied))), 1)
    return {"face_yaw_applied": applied, "facing_source": source, "face_remainder_deg": remainder, **facing.metrics_of(d)}


def facing_colour(sc: StageContext) -> Any:
    """facing.colour_of_files for this asset: the drawn front and back views (the bald ones when the hair is the
    library's, which is what the model was built as) against the mesh audit's renders. None when either is missing."""
    try:
        info = sc.lineage_file("views_dir", ("mesh",))
        bald = hairlib.is_library(sc)
        pics = {v: (sc.lineage_file(f"bald_{v}_cutout", ("turnaround",)) if bald else None) or sc.lineage_file(f"{v}_cutout", ("turnaround",))
                for v in ("front", "back")}
        if bald:
            pics["back"] = None  # no bald back is drawn, and the drawn back's hair is not on the model
        return facing.colour_of_files(pics, Path(info).parent if info is not None else None)
    except Exception:  # noqa: BLE001  (no colour evidence: the facing stands on the outline and the toes)
        return None


def facing_picture(sc: StageContext, dims: Any = None) -> dict[str, Any] | None:
    """facing.picture_of_files for this asset: the step 1 cut-out against the mesh audit's renders (dims: the mesh audit's
    model size, for the square-footprint branch), for categories with facing_by_picture. None otherwise or when either
    is missing."""
    if not sc.category.get("facing_by_picture"):
        return None
    try:
        info = sc.lineage_file("views_dir", ("mesh",))
        pic = sc.lineage_file("cutout", ("concept",))
        return facing.picture_of_files(pic, Path(info).parent if info is not None else None, dims)
    except Exception:  # noqa: BLE001  (no picture evidence: the outline decision stands)
        return None


def paints_full_first(mesh_v: Any, own_maps: dict[str, Path]) -> bool:
    """A Hunyuan3D mesh (engine hy3d) has no texture of its own: it is painted from the pictures at full detail, then
    shrunk to the budget with the paint baked onto the shrunk model (shrink_after_paint). Every other mesh is as before."""
    return bool(mesh_v is not None and (mesh_v.params or {}).get("engine") == "hy3d" and not own_maps.get("base_color"))


def placeholder_maps(out_dir: Path, tex: int, keep_normal: bool) -> dict[str, Path]:
    """Neutral maps for a mesh with no texture of its own. They are not a colour of the model: the picture paint replaces
    the grey wherever a picture sees the surface; metallic is 0, roughness 0.5, ao 1, and the normal map (when the look
    keeps one) is flat."""
    out_dir.mkdir(parents=True, exist_ok=True)
    specs = {"base_color": (128, 128, 128), "metallic": (0, 0, 0), "roughness": (128, 128, 128), "ao": (255, 255, 255),
             "normal": (128, 128, 255)}
    files: dict[str, Path] = {}
    for key, colour in specs.items():
        if key == "normal" and not keep_normal:
            continue
        p = out_dir / f"{key}.png"
        Image.new("RGB", (int(tex), int(tex)), colour).save(p)
        files[key] = p
    return files


async def shrink_after_paint(sc: StageContext, out: Path, current: dict[str, Path], limit: int, size: dict[str, Any], pivot: str,
                             smooth: float, shading: str, tex: int, look: dict[str, Any], delight: str, pbr_fix: bool,
                             light_after: bool, keep_normal: bool, head_args: list[str] | None = None) -> dict[str, Path]:
    """The painted full-detail model is shrunk to the budget and the paint baked onto the shrunk model (the existing
    Blender prep: a copy of the painted model is the bake source, then the decimation and the rebake), then measured
    again on the shrunk model. The size and the pivot are as the first pass set them; no second turn (the yaw is already
    applied in the painted model)."""
    full = sc.dir / "painted_full.glb"
    shutil.copyfile(out, full)
    maps_json = sc.dir / "paint_maps.json"
    maps_json.write_text(json.dumps({k: str(p) for k, p in current.items() if k in TEXTURE_KEYS and (keep_normal or k != "normal")}),
                         encoding="utf-8")
    args = [full, out, "mesh", "--size", size["size_m"], "--size-axis", size["size_axis"], "--yaw", "0", "--pivot", pivot,
            "--smooth-angle", smooth, "--shading", shading, "--budget", int(limit), "--maps", maps_json, "--tex", tex,
            "--padding", imaging.unwrap_padding(tex), "--margin", imaging.dilation_px(tex), "--rebake", "auto",
            "--category", sc.asset.category, *(head_args or [])]
    sc.progress(0.8, "Shrinking the painted model")
    await sc.blender("prep.py", args, log_name="prep_shrink.log")
    report = json.loads((sc.dir / "prep_report.json").read_text())
    if not report.get("rebaked"):
        sc.set_metrics(shrink_rebaked=False, shrink_triangles=report.get("triangles"))
        return current
    maps = {k: Path(p) for k, p in (report.get("rebake_maps") or {}).items() if Path(p).is_file()}
    for key, p in maps.items():
        sc.register(key, p)
    sc.set_metrics(triangles=report.get("triangles"), vertices=report.get("vertices"), decimated=bool(report.get("decimated")),
                   triangles_before_budget=report.get("triangles_before_budget"), rebaked=True,
                   rebake_reason=report.get("rebake_reason"), rebake_seconds=report.get("rebake_seconds") or 0.0,
                   shrink_rebaked=True, texture_size=tex, rebake_unreached=report.get("rebake_unreached"))
    sc.register("glb", out)
    sc.register("report", sc.dir / "prep_report.json")
    # The maps were baked onto the shrunk model (out), so that is the bake reference the layout check compares against
    # (the same rule as the normal flow: bake_glb = out once rebaked). The painted full-detail model is the bake source only.
    result = await asyncio.to_thread(texture_pass, out, out, maps, sc.dir, tex, delight, pbr_fix, True, look, light_after)
    for key, p in result["files"].items():
        sc.register(key, p)
    sc.set_metrics(**result["metrics"])
    return {**maps, **{k: Path(p) for k, p in result["files"].items() if k in TEXTURE_KEYS}}


def own_mesh_glb(sc: Any) -> tuple[Any, Path | None]:
    """The mesh version this clean-up works on and its GLB. The walk is the prep's own lineage: its parent, or, for a
    clean-up made from an earlier clean-up, the mesh that one came from. A selected mesh elsewhere in the asset is
    never used: a model cleaned from another model's GLB is a different model. (None, None) when there is no mesh."""
    for v in sc.iter_lineage(include_self=False):
        if v.stage == "mesh":
            return v, library.version_file(v, "glb", sc.settings)
    return None, None


def own_texture_maps(mesh_v: Any, keys: list[str] | tuple[str, ...], settings: Any = None) -> tuple[dict[str, Path], list[str]]:
    """The texture maps of the mesh version prep works on, and the keys it does not have. Never taken from another
    mesh version: a map baked for another model would not match this model's UV layout (a Hunyuan3D mesh has none)."""
    if mesh_v is None:
        return {}, list(keys)
    found: dict[str, Path] = {}
    missing: list[str] = []
    for k in keys:
        p = library.version_file(mesh_v, k, settings)
        if p is None:
            missing.append(k)
        else:
            found[k] = p
    return found, missing


async def _prep(sc: StageContext, params: dict[str, Any]) -> None:
    if sc.asset.category == "material":
        sc.set_metrics(skipped=True, reason="material category has no mesh")
        return
    mesh_v, src = own_mesh_glb(sc)
    if src is None:
        raise FileNotFoundError("This model has no 3D mesh file to clean up. Build the 3D model again, then clean up.")
    size = resolve_size(sc.asset.category, params, sc.asset.prompt)
    toes = await asyncio.to_thread(facing.toes_of_file, src, sc.asset.category)
    facing_ = await asyncio.to_thread(resolve_facing, sc.category, params.get("face_yaw", "auto"), mesh_v.metrics if mesh_v else None,
                                      toes, facing_colour(sc), await asyncio.to_thread(facing_picture, sc, (mesh_v.metrics or {}).get("dims") if mesh_v else None))
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
    mesh_v_params = mesh_v.params if mesh_v is not None else {}
    tri_limit = prep_triangle_limit(rec, mesh_v_params, sc.budget)
    hair = hair_plan(sc, params, mesh_v, tri_limit["limit"]) if hairlib.is_library(sc) else None
    if hair is not None:
        # The body is decimated to the budget less the hair's allowance; the hair pass sees the library and stands back.
        params = {**params, "hair_source": hairlib.LIBRARY}
        tri_limit = {**tri_limit, "limit": hair["body_limit"]}
    floor_plan = await triangle_floor_plan(sc, rec, src, mesh_v, tri_limit) if hair is None else None
    if floor_plan is not None:
        tri_limit = {**tri_limit, "limit": floor_plan["limit"], "source": "floor" if floor_plan["state"] in ("raised", "over_max") else tri_limit["source"]}
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
    if floor_plan is not None:
        sc.set_metrics(triangle_floor=floor_plan)
    sc.use_model("blender")
    out = sc.dir / "prep.glb"
    args: list[Any] = [src, out, "mesh", "--size", size["size_m"], "--size-axis", size["size_axis"], "--yaw", facing_["face_yaw_applied"],
                       "--pivot", pivot, "--smooth-angle", smooth, "--shading", shading["shading"]]
    budget_now = None if paints_full_first(mesh_v, own_texture_maps(mesh_v, ["base_color"], sc.settings)[0]) else tri_limit["limit"]
    # The separate eyes are added after the paint. The reserve is released early only when the picture and the mesh settle
    # that they cannot run (eyes.early_reason, before the decimation); a later skip (no_room, or a reason that needs the
    # painted model) keeps the reserve, so the model ends that much under its budget: recorded as eye_reserve.
    wanted_eyes = eyes_wanted(sc, params)
    own_base = own_texture_maps(mesh_v, ["base_color"], sc.settings)[0].get("base_color") if wanted_eyes else None
    early_skip = (await asyncio.to_thread(eyes.early_reason, src, own_base, sc.lineage_file("cutout", ("concept",)),
                                          face_detail.head_yaw_from_remainder(facing_.get("face_remainder_deg")))) if own_base else None
    eye_room = eyes.reserve_for(early_skip, wanted_eyes)
    if wanted_eyes:
        sc.set_metrics(eye_reserve=eye_room, eye_early_skip=early_skip)
    if budget_now:
        budget_now = max(int(budget_now) - eye_room, 1)
    if budget_now:
        args += ["--budget", int(budget_now)]
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
    # Only the mesh version prep is working on supplies the maps: a map from another model (another engine, or a
    # selected version elsewhere in the asset) would be baked onto this model's UVs. Missing maps are said, not borrowed.
    mesh_maps, missing_maps = own_texture_maps(mesh_v, [k for k in TEXTURE_KEYS if keep_normal or k != "normal"], sc.settings)
    if missing_maps:
        sc.set_metrics(texture_maps_missing=missing_maps,
                       texture_maps_note=("The 3D model has no texture maps of its own, so prep leaves its surface as it is: nothing is painted or re-baked."
                                          if not mesh_maps else "Some texture maps are missing from the 3D model; prep uses only the ones it has."))
    repaired = await repair_normal(sc, mesh_v, src, mesh_maps, keep_normal)
    if repaired:
        src, mesh_maps["normal"] = repaired["glb"], repaired["normal"]
    paint_first = paints_full_first(mesh_v, mesh_maps)
    if paint_first:
        # the paint goes on at the mesh's UV size, at full detail; the budget is applied after the paint (below)
        mesh_maps = placeholder_maps(sc.dir / "placeholder", int((mesh_v.metrics or {}).get("unwrap_resolution") or 2048), keep_normal)
    tex = texture_size_of(mesh_maps, mesh_v.params if mesh_v else {}, sc.budget.get("texture"))
    if mesh_maps:
        maps_json = sc.dir / "bake_maps.json"
        maps_json.write_text(json.dumps({k: str(p) for k, p in mesh_maps.items()}), encoding="utf-8")
        args += ["--maps", maps_json, "--tex", tex, "--padding", imaging.unwrap_padding(tex), "--margin", imaging.dilation_px(tex),
                 "--rebake", "always" if params.get("rebake") else "auto", "--category", sc.asset.category]
    sc.progress(0.1, "Blender prep")
    with steptime.part(sc, "blender_prep"):   # waits for the host's Blender; the re-bake below is inside it
        await sc.blender("prep.py", args, log_name="prep.log")
    report = json.loads((sc.dir / "prep_report.json").read_text())
    steptime.add(sc, "rebake", report.get("rebake_seconds") or None)
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
    moved_layout = False
    if mesh_maps and not paint_first and await asyncio.to_thread(layout_gutter_short, out, tex):
        # The gutter is too small (a reduction that kept the islands' places shrinks it, and so does a re-bake onto that
        # layout): move the islands apart unchanged (no bake, no filtering)
        pad = imaging.unwrap_padding(tex)
        with steptime.part(sc, "layout_spread"):
            spread = await asyncio.to_thread(layoutspread.spread, out, maps, sc.dir, tex, math.ceil(PADDING_PASS_FRACTION * pad))
        sc.set_metrics(layout_spread={k: v for k, v in spread.items() if k in ("moved", "reason", "islands", "gutter_before", "gutter_after")})
        if spread["moved"]:
            maps = spread["maps"]
            for key, p in maps.items():
                sc.register(key, p)
            moved_layout = True  # the maps match the final layout by construction, as after a re-bake
    sc.set_metrics(triangles=report.get("triangles"), vertices=report.get("vertices"), dims=report.get("dims_m"),
                   size_extent_m=report.get("size_extent_m"),
                   non_manifold_edges=report.get("non_manifold_edges"), boundary_edges=report.get("boundary_edges"),
                   degenerate_faces=report.get("degenerate_faces"), uv_layers=report.get("uv_layers"), bones=report.get("bones"),
                   rebaked=rebaked, rebake_reason=report.get("rebake_reason"), rebake_seconds=report.get("rebake_seconds") or 0.0,
                   rebake_unreached=report.get("rebake_unreached") if rebaked else None, decimated=bool(report.get("decimated")), texture_size=tex, triangles_before_budget=report.get("triangles_before_budget"),
                   shape_loss=report.get("shape_loss"), outline=report.get("outline"), **head_metrics(report, head))
    if report.get("grid"):
        sc.set_metrics(**grid_metrics(report["grid"]))
        tiling = report.get("tiling_png")
        if tiling and Path(tiling).is_file():
            sc.register("tiling", Path(tiling))
    if maps.get("base_color"):
        sc.progress(0.7, "Checking the textures")
        bake_glb = out if rebaked or moved_layout else src
        look = texture_look(rec)
        # A look that paints at a larger size than it ships (Retro 64: recipe mesh.bake_texture) keeps the bake size
        # through the painting and is shrunk to its size at the end (ship_late); every other look as before.
        late_cap = late_cap_of(rec, look, tex)
        if late_cap:
            look = {**look, "cap": None}
        # Light is removed after the painting (vgac/lightfix.py) when the pictures are painted on: the projection paints
        # their light back in over a de-light done here. The de-light here stays for models nothing is painted on.
        light_after = surface["delight"] == "remove" and srcproject.on(params, sc.asset.category, look)
        result = await steptime.run(sc, "texture_pass", asyncio.to_thread(texture_pass, out, bake_glb, maps, sc.dir, tex, surface["delight"],
                                                                         surface["pbr_fix"], rebaked, look, light_after))
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
        if face_detail.layout_only_on(rec, sc.asset.category):
            # the look's face layout (Retro 64): the face gets more of the texture before the pictures are painted on
            fl = await face_detail.layout_only(sc, out, current, head_yaw, ship=late_cap)
            current = fl["maps"]
            sc.set_metrics(**fl["metrics"])
            if fl["relayout"]:
                sc.set_metrics(**await asyncio.to_thread(layout_measures, out, tex, "face layout"))
        if paint_first or srcproject.on(params, sc.asset.category, look):
            # the facing was decided (or chosen by hand): the front pictures are fitted on the front half only
            front_known = facing_.get("face_best_turn") is not None or facing_.get("facing_source") == "chosen"
            with steptime.part(sc, "projection"):   # the picture painted on: pose search, fits and the compose are inside it
                current = await srcproject.run_stage(sc, out, current, params, keep=body_views, head_yaw=head_yaw,
                                                     front_known=front_known)
            steptime.add(sc, "pose_search", pose_search_seconds(sc))   # summed over the views (fitted at the same time)
        if paint_first and tri_limit["limit"] and int(report.get("triangles") or 0) > int(tri_limit["limit"]):
            # the same head (and hands) protection as the budget pass of the normal path (only when the head is weighted)
            head_args = (["--protect-head", headtris.HEAD_SHARE, "--head-gain", headtris.HEAD_GAIN]
                         + (["--hand-gain", headtris.HAND_GAIN] if headtris.hands_on(params) else [])) if head["weighted"] else []
            current = await shrink_after_paint(sc, out, current, int(tri_limit["limit"]) - eye_room, size, pivot, smooth, shading["shading"], tex,
                                               look, surface["delight"], surface["pbr_fix"], light_after, keep_normal, head_args)

        async def paint_body_again(glb_: Path, maps_: dict[str, Path]) -> dict[str, Path]:
            with steptime.part(sc, "second_compose"):
                return await srcproject.run_again(sc, glb_, maps_, body_views)

        # "The face matters" off on the asset: the pass is skipped unless this run turns it on
        face_matters = getattr(sc.asset, "face_matters", None) is not False
        if params.get("face_detail") is None and not face_matters:
            params = {**params, "_face_matters": False}
        sc.set_params(face_detail=params.get("face_detail") is not False and face_matters if params.get("face_detail") is None
                      else params.get("face_detail") is not False, face_strength=face_detail.strength_of(params),
                      face_density=face_detail.density_choice(params), face_closeup=face_detail.closeup_on(params),
                      face_sides=params.get("face_sides") is not False, quarter_pictures=srcproject.quarters_on(params),
                      face_closeup_guided=params.get("face_closeup_guided") is not False)
        # The hair detail pass (stages/hair_detail.py): the hair is found first, so the face pass's one relayout can give
        # it more texels too; it is painted after the face, on the final layout, and never writes the face's texels.
        sc.set_params(hair_detail=hair_detail.on(params), hair_strength=hair_detail.strength_of(params))
        if sc.asset.category == "humanoid":
            sc.set_params(**{hairmat.PARAM: hairmat.option(params)})  # hair as its own material (vgac/hairmat.py)
        # Skin is never metal (vgac/skinmetal.py), first: the 3D builder spreads metal over the skin of the whole body, and the
        # hair find reads the metal map to tell a helmet from hair. The share of skin that was metal says whether metal on
        # the head means anything.
        current, sm = await clear_skin_metal(sc, out, current)
        sc.set_metrics(**sm["metrics"])
        hf = await hair_detail.find(sc, out, current, rec, params,
                                    skin_metal=(sm["metrics"]["skin_metal"].get("metal_share_before") if sm["metrics"]["skin_metal"].get("ran") else None))
        face_detail_seed = params.get("face_detail_seed")
        with steptime.part(sc, "face_detail"):   # the second compose and the sharpening redraw are inside it
            fd = await face_detail.run(sc, out, current, rec, params, hair_plan=hf.get("plan"), face_detail_seed=face_detail_seed,
                                       after_relayout=paint_body_again if body_views.get("views") else None,
                                       body_painted=bool(body_views.get("views")),
                                       head_yaw=head_yaw)
        fdm = (fd.get("metrics") or {}).get("face_detail") or {}
        steptime.add(sc, "sharpening", fdm.get("redraw_seconds") or None)
        steptime.add(sc, "face_rebake", (fdm.get("layout") or {}).get("rebake_seconds") or None)
        if not sc.category.get("emission_map"):
            body_views.clear()  # several large pictures per view: let them go (scenery keeps them for the glow map, below)
        for key, p in fd["files"].items():
            sc.register(key, p)
        sc.set_metrics(**fd["metrics"])
        if fd["relayout"]:
            sc.set_metrics(**await asyncio.to_thread(layout_measures, out, tex))
        after_face = {**current, **{k: Path(p) for k, p in fd["files"].items() if k in TEXTURE_KEYS}}
        if fd["relayout"]:
            # the face pass laid the texture out again: the cleared metal was baked across, and the skin is looked at once more on
            # the new layout (the face's own texels in particular)
            after_face, sm2 = await clear_skin_metal(sc, out, after_face)
            sc.set_metrics(skin_metal_again=sm2["metrics"]["skin_metal"])
        hd = await steptime.run(sc, "hair_detail", hair_detail.run(sc, out, after_face, rec, params, hf, fd.get("layout")))
        for key, p in hd["files"].items():
            sc.register(key, p)
        sc.set_metrics(**hd["metrics"])
        if hd["relayout"]:
            sc.set_metrics(**await asyncio.to_thread(layout_measures, out, tex, "hair detail"))
        final = {**after_face, **{k: Path(p) for k, p in hd["files"].items() if k in TEXTURE_KEYS}}
        if light_after and final.get("base_color"):
            final = await remove_light(sc, out, final, hf)
        if eyes_wanted(sc, params) and final.get("base_color"):
            final = await steptime.run(sc, "eyes", add_eyes(sc, out, final, head_yaw, tex, fdm))
        if (rec.get("surface") or {}).get("colour_per_face") and final.get("base_color"):
            final = await colour_faces(sc, out, final, rec, head_yaw)
        if late_cap:
            final = await ship_late(sc, out, final, {**look, "cap": late_cap}, tex)
        if sc.category.get("emission_map") and body_views.get("views") and final.get("base_color"):
            # last, so the glow map is made on the final layout at the final size (nothing after this changes either)
            await make_glow(sc, out, final, body_views)
        body_views.clear()
        if final.get("metallic") != maps.get("metallic") or final.get("base_color") != maps.get("base_color"):
            # metal was cleared (skin, hair) and colours painted after the texture pass measured them: measure again, so the
            # metal and colour-range checks read the finished maps
            after = await asyncio.to_thread(remeasure_pbr, out, final)
            if after is not None:
                sc.set_metrics(pbr=after, pbr_measured="finished")
        if int(fd.get("base_color_size") or 0) > tex:
            # "High" face detail on a 4096 platform: the base colour ships at 4096, the other maps stay at the atlas size
            sc.set_metrics(base_color_size=int(fd["base_color_size"]), texture_size_shipped=int(fd["base_color_size"]))
    elif not keep_normal:
        await asyncio.to_thread(imaging.glb_replace_images, out, drop_normal=True)
    if hair is not None:
        await attach_hair(sc, out, hair, mesh_v.metrics if mesh_v else {})
    await steptime.run(sc, "judge", judge(sc))


def pose_search_seconds(sc: StageContext) -> float | None:
    """The seconds the pose search took, summed over the views it ran for, from the record the projection pass keeps for
    each view (metrics.srcproject_views[view].pose_search.seconds: outline, terms, refine, settle, d2). The views are
    searched at the same time, so this can be longer than the time it held up the step. None: no search ran."""
    try:
        total, found = 0.0, False
        for info in ((sc.version.metrics or {}).get("srcproject_views") or {}).values():
            rec = info.get("pose_search") if isinstance(info, dict) else None
            secs = rec.get("seconds") if isinstance(rec, dict) else None
            if isinstance(secs, dict):
                for k, v in secs.items():
                    if k != "fit" and isinstance(v, (int, float)):
                        total, found = total + float(v), True
        return round(total, 1) if found else None
    except Exception:  # noqa: BLE001  (timing never fails a job)
        return None


def late_cap_of(rec: dict[str, Any], look: dict[str, Any], tex: int) -> int | None:
    """The size a look that bakes larger than it ships (recipe mesh.bake_texture) is shrunk to at the end of the
    clean-up, or None: shrunk in the texture pass as before (every look without a bake size, and a model whose maps are
    already at the shipped size)."""
    cap = look.get("cap")
    if not (rec.get("mesh") or {}).get("bake_texture") or not cap or int(tex) <= int(cap):
        return None
    return int(cap)


async def make_glow(sc: StageContext, glb: Path, current: dict[str, Path], kept: dict[str, Any]) -> None:
    """Scenery: the glow map (vgac/glow.py) from the views the picture paint fitted, registered as `emission` and noted in
    the metrics. Never fails the step."""
    from .. import glow

    def work() -> tuple[Path | None, dict[str, Any]]:
        size = int(imaging.read_map(current["base_color"]).shape[0])
        gains, how = glow.finder_gains(sc.settings, kept, sc.dir / "glow")   # the finder, when installed; else the colour rule
        path, rec = glow.write_emission(glb, kept, size, sc.dir / "emission.png", gains)
        return path, {**rec, **how}

    try:
        path, rec = await asyncio.to_thread(work)
    except Exception as e:  # noqa: BLE001
        path, rec = None, {"made": False, "reason": f"failed: {str(e)[:160]}"}
    stale = sc.dir / "emission.png"
    if path is None and stale.is_file():
        stale.unlink()
    if path is not None:
        sc.register("emission", Path(path))
    sc.set_metrics(emission_map=rec)


async def ship_late(sc: StageContext, glb: Path, maps: dict[str, Path], look: dict[str, Any], tex: int) -> dict[str, Path]:
    """The look's texture treatment (apply_look: shrink to look.cap) after everything was painted at the bake size; every
    map shrunk per texture piece (apply_look, imaging.shrink_by_island), so no piece takes in a neighbour's colour or the
    background. The GLB, the version's files and the metrics follow. -> the maps from here."""
    def work() -> tuple[dict[str, Any], dict[str, Path], int | None]:
        out_dir = sc.dir / "shipped"
        out_dir.mkdir(parents=True, exist_ok=True)
        uvf = imaging.load_uv_mesh(glb)
        base = imaging.read_map(maps["base_color"])
        files: dict[str, Path] = {}
        m, mr_size = apply_look(look, uvf, maps, files, base, int(base.shape[0]), out_dir)
        imaging.glb_replace_images(glb, base_color=files.get("base_color"), metallic=files.get("metallic"), roughness=files.get("roughness"),
                                   normal=files.get("normal"), mr_size=mr_size)
        return m, files, mr_size

    m, files, _ = await asyncio.to_thread(work)
    for key, p in files.items():
        sc.register(key, p)
    sc.set_metrics(**m, texture_painted_size=int(tex))
    return {**maps, **{k: Path(p) for k, p in files.items() if k in TEXTURE_KEYS}}


def eyes_wanted(sc: StageContext, params: dict[str, Any]) -> bool:
    """Whether this clean-up tries separate eyes (vgac/eyes.py)."""
    return eyes.wanted(sc.asset.category, getattr(sc.asset, "style_preset", None), params,
                       getattr(sc.asset, "face_matters", None) is not False)


async def add_eyes(sc: StageContext, glb: Path, maps: dict[str, Path], head_yaw: float, tex: int,
                   face_record: dict[str, Any]) -> dict[str, Path]:
    """Separate eyes (vgac/eyes.py, blender/eyes.py) on the finished clean-up: the lid openings cut into the head, a closed
    eye set behind each and painted from the picture into free texture space. metrics.eyes: {ran, rects, triangles_added}
    or {ran: False, reason, message}; a face it cannot use leaves the model as it was. -> the maps from here."""
    from ..jobs import JobCancelled

    def skip(reason: str, **more: Any) -> dict[str, Path]:
        sc.set_metrics(eyes={"ran": False, "reason": reason, "message": guidance.eyes_skipped(reason), **more})
        return maps

    sc.progress(0.96, "Setting the eyes")
    out_dir = sc.dir / "eyes"
    out_dir.mkdir(parents=True, exist_ok=True)
    closeup = sc.dir / "face" / "painted_head_closeup_merged.png"
    cutout = sc.lineage_file("cutout", ("concept",))

    def prepare() -> tuple[str | None, dict[str, Any]]:
        picture, source = None, "closeup"
        if (face_record.get("closeup") or {}).get("used") and closeup.is_file():
            picture = face.read_rgb(closeup, eyes.RENDER)
        elif cutout is not None and Path(cutout).is_file():
            picture, source = eyes.framed_picture(glb, cutout, head_yaw), "picture"
        if picture is None:
            return "no_face", {}
        planned = eyes.plan(glb, maps["base_color"], picture, head_yaw)
        if not planned["ok"]:
            return planned["reason"], {}
        uvmesh = imaging.load_uv_mesh(glb)
        rects = eyes.place(uvmesh, tex) if uvmesh is not None else None
        if rects is None:
            return "no_room", {}
        painted = eyes.paint_maps(maps, planned, picture, rects, out_dir)
        (out_dir / "job.json").write_text(json.dumps(eyes.mesh_job(planned, rects)), encoding="utf-8")
        return None, {"maps": painted, "rects": rects, "source": source}

    new_glb = out_dir / "eyes.glb"
    try:
        reason, work = await asyncio.to_thread(prepare)
        if reason:
            return skip(reason)
        report = out_dir / "report.json"
        await sc.blender("eyes.py", [glb, new_glb, "--job", out_dir / "job.json", "--report", report], log_name="eyes.log",
                         timeout=sc.settings.blender_timeout_seconds)
        rep = json.loads(report.read_text(encoding="utf-8"))
        if not rep.get("ok") or not new_glb.is_file():
            return skip(rep.get("reason") or "error")
        painted = work["maps"]
        await asyncio.to_thread(imaging.glb_replace_images, new_glb, base_color=painted.get("base_color"),
                                metallic=painted.get("metallic"), roughness=painted.get("roughness"), normal=painted.get("normal"))
        shutil.copy2(new_glb, glb)
    except (JobCancelled, asyncio.CancelledError):
        raise
    except Exception as e:  # noqa: BLE001  (never fails the step: the clean-up then stands as it was)
        return skip("error", error=str(e)[:300])
    finally:
        new_glb.unlink(missing_ok=True)
    for key, path in painted.items():
        sc.register(key, path)
    added = int(rep.get("triangles_added") or 0)
    sc.set_metrics(eyes={"ran": True, "rects": work["rects"], "triangles_added": added, "picture": work["source"],
                         "radius_mm": [e.get("radius_mm") for e in rep.get("eyes") or []]},
                   triangles=int((sc.version.metrics or {}).get("triangles") or 0) + added)
    return {**maps, **painted}


async def colour_faces(sc: StageContext, glb: Path, maps: dict[str, Path], rec: dict[str, Any], head_yaw: float) -> dict[str, Path]:
    """The look's one colour per triangle (recipe surface.colour_per_face, Low-poly; vgac/looktex.py), the front of a
    character's head left as painted when surface.face_keeps_texture, and every triangle that holds painted detail (a
    window, a sign, lettering: looktex.detailed_triangles) in every category. -> the maps from here."""
    from .. import looktex

    def work() -> tuple[Path, dict[str, Any]]:
        mesh = face.load_mesh(glb)
        base = face.read_rgb(maps["base_color"])
        keep, how = None, None
        if (rec.get("surface") or {}).get("face_keeps_texture") and sc.asset.category == "humanoid":
            # the finder's region together with the front of the head: the finder alone missed the eyes (looktex)
            found = looktex.front_of_head(mesh, base, head_yaw, whole_front=True)
            keep, how = found["tris"], found["how"]
        sign_rec = None
        if sc.category.get("emission_map"):
            # scenery: the triangles under a lettered sign stay as painted (vgac/signkeep.py)
            from .. import signkeep

            try:
                sign_tris, sign_rec = signkeep.sign_faces(sc.settings, glb, base, sc.dir / "signs")
            except Exception as e:  # noqa: BLE001  (never fails the step: the clean-up then runs as it always did)
                sign_tris, sign_rec = None, {"kept": 0, "reason": f"failed: {str(e)[:160]}"}
            if sign_tris is not None:
                keep = sign_tris if keep is None else np.union1d(np.asarray(keep, dtype=np.int64), sign_tris)
        r = looktex.colour_per_face(base, mesh, keep)
        out_dir = sc.dir / "faces"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = face.save_rgb(r["base_color"], out_dir / "base_color.png")
        imaging.glb_replace_images(glb, base_color=path)
        return path, {"ran": True, "faces": r["faces"], "kept_texels": r["kept"], "detail_faces": r["detailed_faces"],
                        "face_kept_by": how, **({"sign_keep": sign_rec} if sign_rec is not None else {})}

    path, record = await asyncio.to_thread(work)
    sc.register("base_color", path)
    sc.set_metrics(colour_per_face=record)
    return {**maps, "base_color": path}


HAIR_FILE_PREFIXES = ("hair_", "with_hair_")
HAIR_DIR = "hairlib"


async def clear_skin_metal(sc: StageContext, glb: Path, maps: dict[str, Path]) -> tuple[dict[str, Path], dict[str, Any]]:
    """skinmetal.clear on the current maps; the cleared metal map goes into the GLB and the version. -> (maps, result)."""
    sm = await asyncio.to_thread(skinmetal.clear, glb, maps, sc.asset.category, sc.asset.prompt, sc.dir / "skin",
                                 getattr(sc.asset, "wears_armour", None), getattr(sc.asset, "body_is_metal", None))
    if sm["files"].get("metallic") is not None:
        maps = {**maps, "metallic": Path(sm["files"]["metallic"])}
        sc.register("metallic", maps["metallic"])
        await asyncio.to_thread(imaging.glb_replace_images, glb, metallic=maps["metallic"])
    # Creatures are not metal unless the description says so (vgac/creaturemetal.py); a no-op for every other category.
    cm = await asyncio.to_thread(creaturemetal.clear, glb, maps, sc.asset.category, sc.asset.prompt, sc.dir / "creature")
    for key in ("metallic", "roughness"):
        if cm["files"].get(key) is not None:
            maps = {**maps, key: Path(cm["files"][key])}
            sc.register(key, maps[key])
            await asyncio.to_thread(imaging.glb_replace_images, glb, **{key: maps[key]})
    if creaturemetal.mode(sc.asset.category, sc.asset.prompt) is not None:
        sm["metrics"].update(cm["metrics"])  # recorded for creatures only; other categories keep their record
    return maps, sm


async def remove_light(sc: StageContext, glb: Path, maps: dict[str, Path], hf: dict[str, Any] | None) -> dict[str, Path]:
    """The light the pictures painted in, measured on the finished base colour and removed (vgac/lightfix.py), after the
    last pass that paints colour. The hair the hair pass found keeps its colour; when it stood back, black is still never
    lifted. The colour before is kept as base_color_lit. -> maps (base_color replaced when it corrected)."""
    sc.progress(0.95, "Removing the picture's light")
    hair_tris, hair_found = None, False
    points = ((hf or {}).get("plan") or {}).get("points")
    if points is not None and Path(points).is_file():
        try:
            mesh = await asyncio.to_thread(face.load_mesh, glb)
            hair_tris = hairfind.match_tris(mesh, json.loads(Path(points).read_text(encoding="utf-8")))
            hair_found = bool(hair_tris.any())
        except Exception:  # noqa: BLE001  (the black guard still holds the hair)
            hair_tris = None
    r = await asyncio.to_thread(lightfix.run, glb, maps["base_color"], sc.dir / "light", hair_tris, hair_found)
    sc.set_metrics(**r["metrics"])
    if r["files"].get("base_color"):
        await asyncio.to_thread(imaging.glb_replace_images, glb, base_color=r["files"]["base_color"])
        sc.register("base_color", r["files"]["base_color"])
        sc.register("base_color_lit", r["files"]["base_color_lit"])
        maps = {**maps, "base_color": Path(r["files"]["base_color"])}
    return maps


def remeasure_pbr(glb: Path, maps: dict[str, Path]) -> dict[str, Any] | None:
    """The PBR range figures (imaging.pbr_stats) on the finished maps. The texture pass measures them before the face,
    skin and hair passes, which clear metal; the checks read these, after."""
    uvf = imaging.load_uv_mesh(glb)
    if uvf is None or not maps.get("base_color"):
        return None
    base = imaging.read_map(maps["base_color"])
    size = base.shape[0]
    mask = imaging.rasterise_uv(uvf, size)["labels"] > 0
    return imaging.pbr_stats(base, imaging.read_map(maps.get("metallic"), size, grey=True),
                             imaging.read_map(maps.get("roughness"), size, grey=True), mask)


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
            "grid_fit": bool(g.get("fit")), "grid_chosen": gridfit.is_chosen(g), "grid_states": g.get("states"), "grid_scale": g.get("scale"),
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


async def triangle_floor_plan(sc: StageContext, rec: dict[str, Any], src: Path, mesh_v: Any, tri_limit: dict[str, Any]) -> dict[str, Any] | None:
    """The triangle floor (trifloor.py) for a look that sets one (recipe mesh.floor_error_px): one descent of the reduction
    on the model about to be cut, then the limit is the larger of the look's target and the floor, never over the platform's
    maximum. None when the look sets no floor, the model is already at or under the limit, or the descent fails (the
    limit then stays the look's target; the reason is recorded). {limit, floor, state, target, platform_max, error_px,
    picture_px, seconds, curve}."""
    mesh = rec.get("mesh") or {}
    error_px, picture_px = mesh.get("floor_error_px"), mesh.get("floor_picture_px")
    target, platform_max = int(tri_limit.get("limit") or 0), int(sc.budget.get("triangles") or 0)
    have = int(((mesh_v.metrics or {}).get("triangles") if mesh_v is not None else 0) or 0)
    if not error_px or not picture_px or not target or have <= target:
        return None
    out = sc.dir / "triangle_floor.json"
    try:
        await sc.blender("trifloor.py", [src, out, "--min", trifloor.MIN_TRIANGLES], log_name="trifloor.log")
        curve = json.loads(out.read_text(encoding="utf-8"))
    except Exception as err:  # noqa: BLE001  (the floor is advice: a failed descent leaves the look's target as it was)
        return {"limit": target, "floor": None, "state": "none", "target": target, "platform_max": platform_max, "error": str(err)[:200]}
    floor = trifloor.floor_from_steps(curve.get("steps") or [], int(picture_px), float(error_px))
    plan = trifloor.limit_with_floor(target, floor, platform_max)
    return {**plan, "target": target, "platform_max": platform_max, "error_px": float(error_px), "picture_px": int(picture_px),
            "seconds": curve.get("seconds"), "curve": [{"tris": st["tris"], "p95": round(st["p95"], 6)} for st in curve.get("steps") or []]}


def gutter_short(gap: int, pad: int) -> bool:
    """True when the gutter (empty texels between islands) is under what the texture_padding check asks for."""
    return int(gap) < math.ceil(PADDING_PASS_FRACTION * pad)


def layout_gutter_short(glb: Path, tex: int) -> bool:
    """Whether the model's texture layout at `tex` has a gutter the texture_padding check would fail (measured the way
    texture_pass measures it). A model whose layout cannot be read is left alone."""
    try:
        uv = imaging.load_uv_mesh(glb)
    except Exception:  # not a readable GLB (trimesh raises several kinds of error): nothing to measure, nothing to move
        return False
    if uv is None:
        return False
    pad = imaging.unwrap_padding(tex)
    return gutter_short(imaging.gutter_px(imaging.rasterise_uv(uv, tex)["labels"], pad // 2 + 2), pad)


def texture_look(rec: dict[str, Any]) -> dict[str, Any]:
    """What texture_pass does to the maps for the recipe: {cap, normal_map, style}."""
    m = rec.get("mesh") or {}
    return {"cap": m.get("texture"), "normal_map": bool(m.get("normal_map", True)), "style": rec.get("style")}


def texture_size_of(maps: dict[str, Path], mesh_params: dict[str, Any], budget_texture: int | None) -> int:
    """The atlas size: the base colour's width, else the mesh version's texture_size, else the platform budget."""
    if maps.get("base_color"):
        return int(imaging.image_size(maps["base_color"])[0])
    return int(mesh_params.get("texture_size") or budget_texture or 2048)


def texture_pass(final_glb: Path, bake_glb: Path, maps: dict[str, Path], out_dir: Path, tex: int, delight_mode: str,
                 pbr_fix: bool, fill: bool = False, look: dict[str, Any] | None = None, light_after: bool = False) -> dict[str, Any]:
    """Measure and correct the final textures (pure CPU work, run in a thread).

    Measures on the final mesh's layout: atlas_used, texture_padding_px, bake_mesh_hash, export_mesh_hash and
    bake_match (share of the shipped UV triangles that the maps were baked on); the light score; the PBR ranges.
    Corrects: de-light when delight_mode is remove and the score is over the limit, unless light_after (the pictures will
    be painted on, and the light is removed after that by lightfix: delight_deferred); with pbr_fix, colours into range and
    metallic toward 0 or 1; always, roughness up to 0.04. Changed maps are written to out_dir; a changed base colour
    keeps the previous one as base_color_original.png. With fill (maps re-baked in Blender, black past the bake
    margin), each map is first grown into the empty space and the far space filled (imaging.fill_empty), in place.
    With look (texture_look: the recipe), last: maps shrunk to look.cap and the normal map dropped when the look has
    none; the layout measures above stay those of the bake size.
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
    if delight_mode == "remove" and not light_after:
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
    if files or (look and not look.get("normal_map", True)):
        imaging.glb_replace_images(final_glb, base_color=files.get("base_color"), metallic=files.get("metallic"),
                                   roughness=files.get("roughness"), drop_normal=bool(look) and not look.get("normal_map", True),
                                   normal=files.get("normal"), mr_size=mr_size)
    metrics.update(look_metrics)
    after = imaging.light_score(new_base, mask, ao, ras["up"]) if new_base is not base else light
    metrics.update(light_score=after["score"], delight_applied=strength > 0, delight_deferred=bool(light_after and delight_mode == "remove"), delight_strength=round(strength, 3),
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
    m: dict[str, Any] = {"texture_size_shipped": target, "normal_map": bool(look.get("normal_map", True))}
    if target < size:
        if "base_color_original" not in files:
            original = out_dir / "base_color_original.png"
            if Path(maps["base_color"]).resolve() != original.resolve():
                shutil.copy2(maps["base_color"], original)
            files["base_color_original"] = original
        if uvf is not None:
            # per island: each texel is averaged from its own UV island only, then the islands are grown into the empty
            # space, so no island takes in a neighbour's colour or the background (the box filter blended them)
            base = np.clip(np.asarray(new_base, dtype=np.float32), 0.0, 1.0)
            files["base_color"] = imaging.write_map(imaging.shrink_by_island(base, uvf, target, linear=True), out_dir / "base_color.png")
            imaging.dilate_png(files["base_color"], imaging.rasterise_uv(uvf, target)["labels"] > 0, imaging.dilation_px(target))
        else:
            lin = imaging.srgb_to_linear(np.clip(new_base, 0, 1))
            chans = [np.asarray(Image.fromarray(lin[..., c]).resize((target, target), Image.BOX), dtype=np.float32) for c in range(3)]
            files["base_color"] = imaging.write_map(imaging.linear_to_srgb(np.stack(chans, axis=-1)), out_dir / "base_color.png")
    keys = ("metallic", "roughness", "ao") + (("normal",) if look.get("normal_map", True) else ())
    for key in keys:
        src = files.get(key) or maps.get(key)
        if src is None or not Path(src).is_file():
            continue
        if imaging.image_size(src)[0] > target:
            if uvf is not None and key == "normal":
                # per island, as the base colour: no blending across island borders
                files[key] = imaging.shrink_normal_islands(src, uvf, target, out_dir / f"{key}.png")
            elif uvf is not None:
                files[key] = imaging.shrink_map_islands(src, uvf, target, out_dir / f"{key}.png")
            elif key == "normal":
                files[key] = imaging.shrink_normal(src, target, out_dir / f"{key}.png")
            else:
                files[key] = imaging.shrink_image(src, target, out_dir / f"{key}.png")
    return m, (target if target < size else None)


def surface_checks(metrics: dict[str, Any], params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The texture checks from the metrics texture_pass wrote: bake_matches, texture_padding, atlas_used (G05, G03),
    baked_light, albedo_range, metal_mixed (G07, G08). Nothing when the pass did not run."""
    m = metrics or {}
    p = params or {}
    out: list[dict[str, Any]] = []
    if m.get("export_mesh_hash") is None:
        if m.get("texture_pass_error"):
            # the texture pass could not read the model's layout: these were not measured, which is not the same as fine
            why = str(m["texture_pass_error"])
            return [quality.not_measured("baked_light", "whether painted-in shadows are gone", why),
                    quality.not_measured("albedo_range", "whether the colours stay inside the range real materials have", why),
                    quality.not_measured("metal_mixed", "whether metal parts are clearly metal", why)]
        return out
    match = m.get("bake_match")
    if match is not None:
        out.append(quality.check("bake_matches", float(match) >= BAKE_MATCH_MIN, round(float(match), 4), BAKE_MATCH_MIN,
                                 vars={"rebaked": bool(m.get("rebaked"))}))
    pad = int(m.get("texture_padding_target_px") or imaging.unwrap_padding(int(m.get("texture_size") or 2048)))
    gap = m.get("texture_padding_px")
    if gap is not None:
        out.append(quality.check("texture_padding", not gutter_short(gap, pad), int(gap), pad))
    if m.get("atlas_used") is not None:
        out.append(quality.check("atlas_used", float(m["atlas_used"]) >= ATLAS_USED_MIN, round(float(m["atlas_used"]), 4), ATLAS_USED_MIN,
                                 vars={"symmetric": bool(p.get("symmetric"))}))
    lf = m.get("light_fix") or {}
    if m.get("delight_deferred") and lf:
        # the light was measured (and removed when strong) on the finished colour, after the pictures were painted on
        if not lf.get("measured"):
            why = lf.get("reason") or (f"the light pass failed: {lf['error']}" if lf.get("error") else "it could not be read")
            out.append(quality.not_measured("baked_light", "whether the picture's light is still painted into the colours", why))
        else:
            # judged on the light found in the painted colours: a correction evens the materials out only partly (one stop
            # at most, only what the texture and the fitted light agree on), so a hard-lit picture keeps the warning, with
            # how much more even the materials became
            found = float(lf.get("patch_before") or 0.0)
            ub, ua = lf.get("uneven_before"), lf.get("uneven_after")
            evened = round(1.0 - float(ua) / float(ub), 3) if lf.get("applied") and ub and ua is not None else None
            out.append(quality.check("baked_light", found < lightfix.CHECK_MAX, round(found, 3), lightfix.CHECK_MAX,
                                     vars={"kept": False, "removed": bool(lf.get("applied")), "after_painting": True,
                                           "rejected": bool(lf.get("rejected")), "evened": evened,
                                           "whole": lf.get("whole_before"), "left": list(lf.get("left") or [])}))
    elif m.get("light_score") is not None:
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
        # Only colour that is crushed to black or blown to white is wrong: nothing is left to light there. Dark cloth and dark hair
        # that match the picture are not (a game's light shades them less, which is said, not failed).
        clipped = float(pbr.get("albedo_black") or 0.0) + float(pbr.get("albedo_white") or 0.0)
        # A look that keeps its painted darks is excused only while its colour is about as bright as the picture's; a texture mostly
        # near-black from a bright picture (texture_match says it is far darker) is a fault there too, though not pure black.
        far_darker = m.get("delight_mode") == "keep" and not fix_on and not kept_look and float(pbr["albedo_out"]) > ALBEDO_DARK_LOOK_FAULT
        in_range = clipped <= ALBEDO_CLIPPED_MAX and not far_darker
        out.append(quality.check("albedo_range", in_range or kept_look, round(clipped, 4), ALBEDO_CLIPPED_MAX,
                                 vars={"kept": kept_look and not in_range, "black": pbr.get("albedo_black"),
                                       "white": pbr.get("albedo_white"), "dark": pbr.get("albedo_dark")}))
    if pbr.get("metal_mixed") is not None:
        # Once metal has been sharpened there is nothing more the person can do, so only a large leftover warns.
        metal_limit = METAL_MIXED_AFTER_FIX_MAX if (fix_on or m.get("metal_sharpened")) else METAL_MIXED_MAX
        out.append(quality.check("metal_mixed", float(pbr["metal_mixed"]) <= metal_limit, float(pbr["metal_mixed"]), metal_limit,
                                 vars={"pbr_fix_on": fix_on or bool(m.get("metal_sharpened"))}))
    return out


def look_checks(rec: dict[str, Any], report: dict[str, Any], metrics: dict[str, Any], files: dict[str, Path | None],
                category_id: str | None = None) -> list[dict[str, Any]]:
    """look_applied (warn): for a recipe that changes what good means (fewer triangles, flat shading, small textures,
    no normal map), whether the shipped model matches it, in one plain sentence. Nothing for looks that
    keep the full pipeline, or when the material category has no mesh."""
    if category_id == "material":
        return []
    mesh, srf = rec.get("mesh") or {}, rec.get("surface") or {}
    special = (mesh.get("triangle_scale", 1.0) < 1.0 or srf.get("shading") == "flat" or not mesh.get("normal_map", True)
               or (mesh.get("texture_cap") and mesh["texture_cap"] < (mesh.get("budget_texture") or 0)))
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
    parts = []
    if mesh.get("triangle_scale", 1.0) < 1.0 and mesh.get("triangles"):
        parts.append(f"about {int(mesh['triangles']):,} triangles")
    if (metrics.get("shading_applied") or srf.get("shading")) == "flat":
        parts.append("flat faces")
    if cap:
        parts.append(f"{int(size or cap)} px textures")
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
    fc = facing_check(metrics, category_id)
    if fc is not None:
        checks.append(fc)
    up = upscale_check(metrics.get("srcproject_upscale"))
    if up is not None:
        checks.append(up)
    oc = outline_check(report)
    if oc is not None:
        checks.append(oc)
    fl = floor_check(metrics.get("triangle_floor"), tri_vars)
    if fl is not None:
        checks.append(fl)
    ff = (metrics.get("srcproject_face_finder") or {}).get("missing")
    if ff:  # the processor finder was asked for and is not there (nothing asked for: nothing said)
        checks.append(quality.check("face_finder", False, ff, "found", vars={"reason": ff}))
    return checks


def floor_check(plan: dict[str, Any] | None, tri_vars: dict[str, Any] | None) -> dict[str, Any] | None:
    """triangle_floor (warning only): the shape needs more triangles than the look asks for (state raised: cut to the
    floor) or more than the platform allows (over_max: cut to the platform's maximum). None when the floor is at or under
    the look's target, or was not read."""
    if not isinstance(plan, dict) or plan.get("state") not in ("raised", "over_max"):
        return None
    vars_ = {k: plan.get(k) for k in ("state", "floor", "target", "platform_max")}
    vars_["look"] = (tri_vars or {}).get("look")
    return quality.check("triangle_floor", False, plan.get("limit"), plan.get("floor"), vars=vars_)


def outline_check(report: dict[str, Any]) -> dict[str, Any] | None:
    """outline_kept (WP-16 follow-up, warning only): the model before the budget reduction and the final model drawn as
    outlines from seven fixed views (vgac/blender/outline.py). Fails when the worst view overlaps less than
    OUTLINE_IOU_MIN, or a lost or filled-in patch is bigger than OUTLINE_REGION_MAX of the outline. None when prep did
    not try; not measured when it tried and could not."""
    o = report.get("outline")
    if not isinstance(o, dict):
        err = report.get("outline_error")
        return quality.not_measured("outline_kept", "whether the outline survived the clean-up", str(err)) if err else None
    iou, region = float(o["iou_min"]), float(o["region_max"])
    view = o.get("region_max_view") if region > OUTLINE_REGION_MAX else o.get("iou_min_view")
    return quality.check("outline_kept", iou >= OUTLINE_IOU_MIN and region <= OUTLINE_REGION_MAX, round(iou, 4), OUTLINE_IOU_MIN,
                         vars={"region": round(region, 4), "region_limit": OUTLINE_REGION_MAX, "kind": o.get("region_max_kind"),
                               "view": view})


def facing_check(metrics: dict[str, Any], category_id: str | None = None) -> dict[str, Any] | None:
    """facing_tests: what the two independent facing tests found and whether they agreed (the outline of the drawn views
    and the direction of the toes). Replaces the old `facing` check, which compared the turn applied with the outline
    test that had chosen it and could never fail. Only for two-legged characters, which is where the toes test exists:
    both tests agree (passes), they disagree (fails, with the fix to the decided turn), or the toes were not found
    (fails as "not measured": the outline test alone cannot be checked against anything, so it is not called good).
    Nothing when the outline test has no answer either (no trusted fit: nothing was decided)."""
    m = metrics or {}
    best, applied_ = m.get("face_best_turn"), int(m.get("face_yaw_applied") or 0) % 360
    # a person whose toes were read, or whose front could not be told from the back, has its own checks below
    person_checked = category_id in facing.TOES_CATEGORIES and (m.get("face_decision_sure") is False or bool(m.get("face_toes_found")))
    if (m.get("facing_source") != "chosen" and best is not None and not person_checked
            and quality.angle_diff(best, applied_) > quality.FACING_TOLERANCE_DEG):
        # any kind of model left further from its decided front than the tolerance
        return quality.facing_off_check(best, applied_)
    if m.get("face_decided_by") == "picture" and m.get("facing_source") != "chosen":
        return quality.facing_settled_check("picture", applied_)
    if category_id not in facing.TOES_CATEGORIES:
        return None  # no second test exists for this kind of model, and the outline test alone is not a check
    if m.get("face_outline_turn") is None and not m.get("face_toes_found"):
        return None
    applied = int(m.get("face_yaw_applied") or 0) % 360
    reason = quality.plain_reason(m.get("face_toes_reason") or "the feet were not found")
    if m.get("facing_source") == "chosen":
        # the person chose the turn: say so, and what the app's own tests would have done
        return quality.facing_settled_check("chosen", applied, decided=quality.auto_turn(m.get("face_best_turn"), sure=m.get("face_decision_sure") is not False)
                                            if m.get("face_best_turn") is not None else None)
    if m.get("face_decision_sure") is False:
        # a near-tie nothing settled (facing.decide): it kept the side the 3D step built as the front, and says so
        return quality.facing_unsure_check(m.get("face_best_turn"), applied, bool(m.get("face_model_flat")), reason)
    if m.get("face_toes_found") and m.get("face_tests_differ_deg") is not None:
        return quality.facing_tests_check(m["face_outline_turn"], m["face_toes_turn"], m["face_tests_differ_deg"],
                                          m.get("face_best_turn"), applied,
                                          by="toes" if m.get("face_decided_by") == "toes" else "outline")
    if m.get("face_colour") and m.get("face_decision_sure"):
        # a near-tie with no toes that the colours of the front and back pictures settled (facing.decide)
        return quality.facing_settled_check("colour_overruled" if m.get("face_decided_by") == "colour" else "colour",
                                            applied, reason)
    if m.get("face_toes_reason") is None and "face_toes_found" not in m:
        return None  # a version made before the toes test existed: nothing to report
    if m.get("face_toes_found"):  # the toes have an answer and the outline has none: the model was left as built
        toes = int(round(float(m["face_toes_turn"]))) % 360
        return quality.not_measured("facing_tests", "the outline test of which way it faces",
                                    f"the drawn views did not fit the model well enough to read it. The toes say turn {toes} "
                                    f"degrees, with nothing to compare them with, so it was left as the 3D step built it "
                                    f"(turned {applied} degrees)",
                                    vars={"toes_turn": toes, "applied": applied})
    outline = int(m["face_outline_turn"]) % 360
    margin = m.get("face_outline_margin")
    clearly = " clearly" if margin is not None and float(margin) >= facing.MARGIN_MIN else ""
    turned = (f"it was turned {applied} degrees" if facing.angle_diff(outline, applied) < 1 else
              f"it was turned {applied} degrees, as the clean-up turns by quarter turns only")
    return quality.not_measured("facing_tests", "the second test of which way it faces (where the toes point)",
                                f"{reason}. The outline of the drawn views alone{clearly} says turn {outline} degrees, and "
                                f"{turned}; nothing else confirms it",
                                vars={"outline_turn": outline, "applied": applied})


SKIN_METAL_LEFT_MAX = 0.05  # share of the skin-coloured surface still metal, on small separate pieces, that is not worth a warning


def skin_metal_check(m: dict[str, Any], category: str | None) -> dict[str, Any] | None:
    """skin_not_metal: measured after the clearing (skinmetal.clear): the share of the skin-coloured surface still marked
    metal, which is only ever on small separate pieces (jewellery) because the pass clears the rest. "Not measured" when the
    pass could not find the face. Nothing for other categories or a character that is metal by its description."""
    if category != "humanoid":
        return None
    rec = m.get("skin_metal")
    if not rec:
        return None
    skipped = rec.get("skipped")
    if skipped in ("not a humanoid", "the description makes the character metal", "no metal map"):
        return None
    if rec.get("error") or not rec.get("ran"):
        why = ("the face could not be found in the model" if skipped == "no face found" else
               "the model has no texture layout to read" if skipped == "no texture layout" else
               f"the check failed ({rec.get('error')})" if rec.get("error") else "it did not run")
        return quality.not_measured("skin_not_metal", "whether the skin is plain skin or metal", why)
    total = max(1, int(rec.get("skin_coloured_texels") or 0))
    left = int(rec.get("left_metal") or 0) / total
    return quality.check("skin_not_metal", left <= SKIN_METAL_LEFT_MAX, round(left, 4), SKIN_METAL_LEFT_MAX,
                         vars={"before": rec.get("metal_share_before"), "blanket": bool(rec.get("blanket")),
                               "kept": rec.get("kept_metal_texels"), "head_only": rec.get("body") is False,
                               "cleared": bool(rec.get("texels_changed"))})


def projection_reason_words(reason: Any) -> str:
    """srcproject's recorded reason (srcproject_reason) in words a person reads, as the end of "...because ...". The
    recorded text is kept after the plain words when it says something more."""
    r = str(reason or "").strip()
    m = re.match(r"silhouette match ([0-9.]+) is under ([0-9.]+)", r)
    if m:
        return (f"the picture did not line up with the model well enough (outline match {float(m.group(1)):.2f}, it needs "
                f"{float(m.group(2)):.2f})")
    if r.startswith("the result is no closer"):
        return "painting from the picture would not have made the model look more like it than the 3D builder's own colour"
    if r == "the picture does not show the front":
        return "the picture does not show the character from the front"
    if "mirrored" in r:
        return "the model's two halves share one piece of texture (it is mirrored), so a picture cannot be painted on one side"
    if "ambiguous" in r:
        return f"the picture's turn could not be told from the outline ({r})"
    if "contradicts" in r:
        return f"a view turned the opposite way to the front ({r})"
    if r.startswith("failed:"):
        return f"the painting failed ({r[7:].strip()})"
    if r.startswith("no base colour"):
        return "the model had no colour texture or there was no picture to paint from"
    return r or "no reason was recorded"


def projection_check(m: dict[str, Any], params: dict[str, Any], category: str, look: dict[str, Any] | None) -> dict[str, Any] | None:
    """picture_projection: whether the pictures' own pixels were painted onto the model (srcproject). It shows the
    reason in plain words when the pass was refused, skipped or failed, so a character left in the 3D builder's softer
    colour is never read as a painted one. Nothing when the pass is off for this look or by the person's choice."""
    if category == "material" or not srcproject.on(params, category, look):
        return None
    if m.get("srcproject_applied") is None:
        if m.get("export_mesh_hash") is None:
            return None  # no base colour to paint: nothing was attempted
        return quality.not_measured("picture_projection", "whether the pictures were painted onto the model",
                                    "the clean-up did not record what the picture painting did")
    if m.get("srcproject_applied"):
        share = m.get("srcproject_painted_share")
        c = quality.check("picture_projection", True, share if isinstance(share, (int, float)) else None, None,
                          vars={"share": share if isinstance(share, (int, float)) else None})
        c["message"] = f"{share:.0%} of the surface" if isinstance(share, (int, float)) else "painted"
        return c
    c = quality.check("picture_projection", False, "refused", None, vars={"reason": projection_reason_words(m.get("srcproject_reason"))})
    c["message"] = "refused"  # the check's own sentence says why; no "limit None" for a check that has no limit
    return c


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
    if m.get("triangle_limit_source") in ("recipe", "floor"):
        from .mesh import look_vars

        tri_vars = {**look_vars(rec, sc.budget), "recipe": True}
        if m.get("triangle_limit_source") == "floor":
            tri_vars.update(floor=True, target=(m.get("triangle_floor") or {}).get("target"))
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
    sk = skin_metal_check(m, sc.asset.category)
    if sk is not None:
        checks.append(sk)
    pc = projection_check(m, sc.version.params or {}, sc.asset.category, texture_look(rec))
    if pc is not None:
        checks.append(pc)
        if m.get("srcproject_applied"):
            checks += srcproject.neck_checks(m)
            ps = quality.painted_share_check(m.get("srcproject_painted_share"))  # WP-92: warning only
            if ps is not None:
                checks.append(ps)
    # WP-86 and WP-92 (warnings only): the re-bake's unreached texels, slivers, texel-density spread and flipped shells.
    uc = quality.unreached_check(m.get("rebake_unreached"))
    if uc is not None:
        checks.append(uc)
    if "mesh_measures" not in m and sc.file("glb") is not None:
        mm = await asyncio.to_thread(meshchecks.measure_glb, sc.file("glb"))
        if mm is not None:  # a GLB that cannot be read gives no measure and no check (never a pass)
            sc.set_metrics(mesh_measures=mm)
            m = sc.version.metrics
    checks += quality.mesh_measure_checks(m.get("mesh_measures"))
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
    _refuse_painted(sc)
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


def _refuse_painted(sc: StageContext) -> None:
    """Adjust and Refine rebuild the clean-up from the mesh, which would drop the Touch up paint (WP-23)."""
    from .. import paintsave

    why = paintsave.refusal(sc.parent)
    if why:
        raise ValueError(why)


async def adjust(sc: StageContext, params: dict[str, Any]) -> None:
    _refuse_painted(sc)
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
