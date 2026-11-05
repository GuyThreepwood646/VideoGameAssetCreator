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
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import quality, reference
from .base import StageContext

# The mesh stage's silhouette floor when the category does not set one (stages/mesh.py DEFAULT_SILHOUETTE_MIN).
DEFAULT_SILHOUETTE_MIN = 0.6


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
    symmetric = params.get("symmetric")
    if symmetric is None:
        symmetric = bool(sc.category.get("symmetric", False))
    pivot = params.get("pivot") or sc.category.get("pivot") or "base"
    smooth = float(params.get("smooth_angle") or 60.0)
    face_yaw = params.get("face_yaw")
    sc.set_params(size_m=size["size_m"], size_axis=size["size_axis"], size_word=size["size_word"],
                  face_yaw=face_yaw if face_yaw not in (None, "") else "auto", symmetric=bool(symmetric), pivot=pivot, smooth_angle=smooth)
    sc.set_metrics(size_source=size["size_source"], **facing)
    sc.use_model("blender")
    out = sc.dir / "prep.glb"
    args: list[Any] = [src, out, "mesh", "--size", size["size_m"], "--size-axis", size["size_axis"], "--yaw", facing["face_yaw_applied"],
                       "--pivot", pivot, "--smooth-angle", smooth]
    if sc.budget.get("triangles"):
        args += ["--budget", int(sc.budget["triangles"])]
    if symmetric:
        args.append("--symmetric")
    sc.progress(0.1, "Blender prep")
    await sc.blender("prep.py", args, log_name="prep.log")
    report = json.loads((sc.dir / "prep_report.json").read_text())
    sc.register("glb", out)
    sc.register("report", sc.dir / "prep_report.json")
    link_textures(sc)
    sc.set_metrics(triangles=report.get("triangles"), vertices=report.get("vertices"), dims=report.get("dims_m"),
                   size_extent_m=report.get("size_extent_m"),
                   non_manifold_edges=report.get("non_manifold_edges"), boundary_edges=report.get("boundary_edges"),
                   degenerate_faces=report.get("degenerate_faces"), uv_layers=report.get("uv_layers"), bones=report.get("bones"))
    await judge(sc)


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
