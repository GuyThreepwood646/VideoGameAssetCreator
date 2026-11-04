"""Stage 4, game-ready prep (Blender on the host): scale to height, pivot, weld, optional symmetrize, smooth shading.

Input is the mesh version's model.glb; output prep.glb plus prep_report.json. Quality checks from the report.
The material category records a skip.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import quality
from .base import StageContext


async def _prep(sc: StageContext, params: dict[str, Any]) -> None:
    if sc.asset.category == "material":
        sc.set_metrics(skipped=True, reason="material category has no mesh")
        return
    src = sc.lineage_file("glb", ("mesh",))
    if src is None:
        raise FileNotFoundError("no mesh glb in lineage")
    height = float(params.get("height_m") or sc.category.get("size_m") or 1.0)
    symmetric = params.get("symmetric")
    if symmetric is None:
        symmetric = bool(sc.category.get("symmetric", False))
    pivot = params.get("pivot") or "base"
    smooth = float(params.get("smooth_angle") or 60.0)
    sc.set_params(height_m=height, symmetric=bool(symmetric), pivot=pivot, smooth_angle=smooth)
    sc.use_model("blender")
    out = sc.dir / "prep.glb"
    args: list[Any] = [src, out, "mesh", "--height", height, "--pivot", pivot, "--smooth-angle", smooth]
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


async def judge(sc: StageContext) -> None:
    rp = sc.file("report")
    if rp is None:
        return
    report = json.loads(Path(rp).read_text())
    checks = quality.geometry_checks(report, sc.budget["triangles"] or None, float(sc.version.params.get("height_m") or 0) or None)
    sc.set_metrics(**quality.summarise(checks))


async def run(sc: StageContext, params: dict[str, Any]) -> None:
    await _prep(sc, params)


async def refine(sc: StageContext, instruction: str, view: str | None, mask_png: Path | None, params: dict[str, Any]) -> None:
    parent = sc.parent
    merged = {**(parent.params if parent else {}), **params, "instruction": instruction}
    await _prep(sc, merged)


async def adjust(sc: StageContext, params: dict[str, Any]) -> None:
    parent = sc.parent
    merged = {**(parent.params if parent else {}), **params}
    await _prep(sc, merged)
