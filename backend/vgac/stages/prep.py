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

Light and colour (G07, G08): prep scores light painted into the base colour (imaging.light_score). The style decides
whether it is removed (styles.yaml delight_default; the delight knob overrides), and whether colours, metal and shine
are brought into physically based ranges (pbr_fix_default; the pbr_fix knob overrides). Roughness is always kept at
0.04 or more. A corrected base colour becomes base_color, and the one before is kept as base_color_original.
"""

from __future__ import annotations

import asyncio
import json
import math
import shutil
import time
from pathlib import Path
from typing import Any

from .. import imaging, quality, reference
from .base import StageContext

# The mesh stage's silhouette floor when the category does not set one (stages/mesh.py DEFAULT_SILHOUETTE_MIN).
DEFAULT_SILHOUETTE_MIN = 0.6

TEXTURE_KEYS = ("base_color", "normal", "metallic", "roughness", "ao")
# Thresholds (docs/GREAT-ASSETS-PLAN.md 3.5 and 3.9). The light score limit is ours: on the library's painted assets
# the score is 0 (wolf), 0.12 to 0.18 (knights, chest) and 0.25 (goblin, visibly lit from above in its concept).
LIGHT_SCORE_LIMIT = 0.2
ATLAS_USED_MIN = 0.5
BAKE_MATCH_MIN = 0.99
ALBEDO_OUT_MAX = 0.01
METAL_MIXED_MAX = 0.05
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
    pivot = params.get("pivot") or sc.category.get("pivot") or "base"
    smooth = float(params.get("smooth_angle") or 60.0)
    face_yaw = params.get("face_yaw")
    surface = resolve_surface(sc.style, params)
    sc.set_params(size_m=size["size_m"], size_axis=size["size_axis"], size_word=size["size_word"],
                  face_yaw=face_yaw if face_yaw not in (None, "") else "auto", symmetric=symmetric, pivot=pivot, smooth_angle=smooth,
                  delight=params.get("delight") or "auto", pbr_fix=surface["pbr_fix"], rebake=bool(params.get("rebake") or False))
    sc.set_metrics(size_source=size["size_source"], **facing)
    sc.use_model("blender")
    out = sc.dir / "prep.glb"
    args: list[Any] = [src, out, "mesh", "--size", size["size_m"], "--size-axis", size["size_axis"], "--yaw", facing["face_yaw_applied"],
                       "--pivot", pivot, "--smooth-angle", smooth]
    if sc.budget.get("triangles"):
        args += ["--budget", int(sc.budget["triangles"])]
    if symmetric:
        args.append("--symmetric")
    mesh_maps = {k: sc.lineage_file(k, ("mesh",)) for k in TEXTURE_KEYS}
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
        link_textures(sc)
    sc.set_metrics(triangles=report.get("triangles"), vertices=report.get("vertices"), dims=report.get("dims_m"),
                   size_extent_m=report.get("size_extent_m"),
                   non_manifold_edges=report.get("non_manifold_edges"), boundary_edges=report.get("boundary_edges"),
                   degenerate_faces=report.get("degenerate_faces"), uv_layers=report.get("uv_layers"), bones=report.get("bones"),
                   rebaked=rebaked, rebake_reason=report.get("rebake_reason"), rebake_seconds=report.get("rebake_seconds") or 0.0,
                   decimated=bool(report.get("decimated")), texture_size=tex)
    if maps.get("base_color"):
        sc.progress(0.7, "Checking the textures")
        bake_glb = src if not rebaked else out
        result = await asyncio.to_thread(texture_pass, out, bake_glb, maps, sc.dir, tex, surface["delight"], surface["pbr_fix"],
                                         rebaked)
        for key, p in result["files"].items():
            sc.register(key, p)
        sc.set_metrics(**result["metrics"], delight_mode=surface["delight"], delight_source=surface["delight_source"])
    await judge(sc)


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


def texture_size_of(maps: dict[str, Path], mesh_params: dict[str, Any], budget_texture: int | None) -> int:
    """The atlas size: the base colour's width, else the mesh version's texture_size, else the platform budget."""
    if maps.get("base_color"):
        return int(imaging.image_size(maps["base_color"])[0])
    return int(mesh_params.get("texture_size") or budget_texture or 2048)


def texture_pass(final_glb: Path, bake_glb: Path, maps: dict[str, Path], out_dir: Path, tex: int, delight_mode: str,
                 pbr_fix: bool, fill: bool = False) -> dict[str, Any]:
    """Measure and correct the final textures (pure CPU work, run in a thread).

    Measures on the final mesh's layout: atlas_used, texture_padding_px, bake_mesh_hash, export_mesh_hash and
    bake_match (share of the shipped UV triangles that the maps were baked on); the light score; the PBR ranges.
    Corrects: de-light when delight_mode is remove and the score is over the limit; with pbr_fix, colours into range and
    metallic toward 0 or 1; always, roughness up to 0.04. Changed maps are written to out_dir; a changed base colour
    keeps the previous one as base_color_original.png. With fill (maps re-baked in Blender, black past the bake
    margin), each map is first grown into the empty space and the far space filled (imaging.fill_empty), in place.
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
        if new_met is not None:
            new_met = imaging.sharpen_metallic(new_met)
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
    if files:
        imaging.glb_replace_images(final_glb, base_color=files.get("base_color"), metallic=files.get("metallic"),
                                   roughness=files.get("roughness"))
    after = imaging.light_score(new_base, mask, ao, ras["up"]) if new_base is not base else light
    metrics.update(light_score=after["score"], delight_applied=strength > 0, delight_strength=round(strength, 3),
                   pbr_fix_applied=bool(pbr_fix), roughness_clamped=new_rough is not rough,
                   pbr=imaging.pbr_stats(new_base, new_met, new_rough, mask) if files else stats,
                   texture_pass_seconds=round(time.time() - t0, 2))
    return {"metrics": metrics, "files": files}


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
    if pbr.get("albedo_out") is not None:
        out.append(quality.check("albedo_range", float(pbr["albedo_out"]) <= ALBEDO_OUT_MAX, float(pbr["albedo_out"]), ALBEDO_OUT_MAX,
                                 vars={"pbr_fix_on": fix_on, "black": pbr.get("albedo_black"), "white": pbr.get("albedo_white")}))
    if pbr.get("metal_mixed") is not None:
        out.append(quality.check("metal_mixed", float(pbr["metal_mixed"]) <= METAL_MIXED_MAX, float(pbr["metal_mixed"]), METAL_MIXED_MAX,
                                 vars={"pbr_fix_on": fix_on}))
    return out


def link_textures(sc: StageContext) -> None:
    """Point the texture keys at the mesh stage's PNGs so viewers find them without copying."""
    from .. import library

    for key in ("base_color", "normal", "metallic", "roughness", "ao"):
        p = sc.lineage_file(key, ("mesh",))
        if p is not None:
            files = sc.version.files
            files[key] = library.rel_path(p, sc.settings)
            sc.version.files = files
    sc.save()


def prep_checks(report: dict[str, Any], params: dict[str, Any], metrics: dict[str, Any], category_id: str,
                triangle_budget: int | None) -> list[dict[str, Any]]:
    """Geometry, size along the version's axis, and facing when the mesh audit gave a trusted fit."""
    spec = reference.size_spec(category_id)
    size = params.get("size_m") or params.get("height_m")
    axis = params.get("size_axis") or ("height" if not params.get("size_m") and params.get("height_m") else spec["axis"])
    word = params.get("size_word") or (spec["word"] if axis == spec["axis"] else reference.AXIS_WORD_DEFAULT.get(axis, "tall"))
    checks = quality.geometry_checks(report, triangle_budget or None, float(size) if size else None, size_axis=axis, axis_word=word)
    best = metrics.get("face_best_turn")
    if best is not None:
        checks.append(quality.facing_check(best, metrics.get("face_yaw_applied") or 0))
    return checks


async def judge(sc: StageContext) -> None:
    rp = sc.file("report")
    if rp is None:
        return
    report = json.loads(Path(rp).read_text())
    checks = prep_checks(report, sc.version.params, sc.version.metrics, sc.asset.category, sc.budget["triangles"])
    checks += surface_checks(sc.version.metrics, sc.version.params)
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
