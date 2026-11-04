"""Quality gate: the ARCHITECTURE.md section 2.1 and 2.2 criteria that are measurable from files.

Each check is {id, ok, value, limit}. Stage modules call the functions for the data they have and attach the
list as metrics.checks. Criteria that need a renderer or a human (edge flow, seam placement, delit albedo)
are not checked here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .imaging import image_size, is_power_of_two

Check = dict[str, Any]

# The mesh stage's triangle_budget check allows this fraction over the budget (the decimator lands near, not on, the
# target); prep and export check the budget exactly. Named here so the tolerance lives in one place (CODE-AUDIT H3).
TRIANGLE_BUDGET_TOLERANCE = 0.05
# Bone influences per vertex when the rig version does not say (the rig stage's group_per_vertex default).
DEFAULT_MAX_INFLUENCES = 4


def check(id_: str, ok: bool, value: Any, limit: Any) -> Check:
    """{name, passed, message} as the frontend renders them, plus {id, ok, value, limit} as ARCHITECTURE.md names them."""
    from . import guidance
    return guidance.describe_check({"name": id_, "passed": bool(ok), "message": f"{value} (limit {limit})", "id": id_, "ok": bool(ok), "value": value, "limit": limit})


def geometry_checks(report: dict[str, Any], triangle_budget: int | None = None, height_m: float | None = None,
                    height_tolerance: float = 0.1) -> list[Check]:
    """From a Blender prep or pack report: manifold, degenerate faces, triangle budget, height."""
    out: list[Check] = []
    tris = report.get("triangles")
    if tris is None and isinstance(report.get("lods"), dict):
        tris = report["lods"].get("LOD0")
    if tris is not None and triangle_budget:
        out.append(check("triangle_budget", tris <= triangle_budget, tris, triangle_budget))
    # A handful of non-manifold or boundary edges on a game mesh is harmless to engines; the gate tolerates
    # 0.2 percent of the edge count (about 3 edges per 1000 triangles) and reports the count.
    edges = report.get("edges") or (int(tris) * 1.5 if tris else 0)
    tol = max(2, int(edges * 0.002))
    if "non_manifold_edges" in report:
        out.append(check("manifold", report["non_manifold_edges"] <= tol, report["non_manifold_edges"], tol))
    if "boundary_edges" in report:
        out.append(check("watertight", report["boundary_edges"] <= tol, report["boundary_edges"], tol))
    if "degenerate_faces" in report:
        out.append(check("degenerate_faces", report["degenerate_faces"] == 0, report["degenerate_faces"], 0))
    dims = report.get("dims_m")
    if dims and height_m:
        h = float(dims[2])
        ok = abs(h - height_m) <= height_tolerance * height_m
        out.append(check("height_m", ok, round(h, 4), height_m))
    if "uv_layers" in report:
        out.append(check("uv_set", report["uv_layers"] >= 1, report["uv_layers"], 1))
    return out


def lod_checks(report: dict[str, Any], expected_ratios: list[float] | None, tolerance: float = 0.25) -> list[Check]:
    """LOD chain from a pack report: count and each level's triangle ratio against LOD0."""
    out: list[Check] = []
    lods = report.get("lods") or {}
    lod0 = lods.get("LOD0")
    if not lod0:
        return out
    ratios = list(expected_ratios) if expected_ratios is not None else list(report.get("lod_ratios") or [])
    out.append(check("lod_count", len(lods) == len(ratios) + 1, len(lods), len(ratios) + 1))
    for i, r in enumerate(ratios, start=1):
        tris = lods.get(f"LOD{i}")
        if tris is None:
            out.append(check(f"lod{i}_ratio", False, None, r))
            continue
        actual = tris / lod0
        # Small meshes cannot hit tiny ratios exactly: allow an absolute floor of 300 triangles or a relative band.
        if tris <= max(300, lod0 * r * (1 + tolerance)) and tris >= lod0 * r * (1 - tolerance) * 0.5:
            out.append(check(f"lod{i}_ratio", True, round(actual, 3), r))
            continue
        out.append(check(f"lod{i}_ratio", abs(actual - r) <= tolerance * r, round(actual, 3), r))
    return out


def collider_checks(report: dict[str, Any], expected: bool = True) -> list[Check]:
    verts = report.get("collider_verts", 0)
    if not expected:
        return []
    return [check("collider_present", verts > 0, verts, ">0"), check("collider_vertices", 0 < verts <= 255, verts, 255)]


def texture_checks(files: dict[str, Path | str], texture_budget: int | None = None,
                   keys: tuple[str, ...] = ("base_color", "normal", "metallic", "roughness", "ao")) -> list[Check]:
    """Power-of-two square textures within the platform resolution budget."""
    out: list[Check] = []
    for key in keys:
        p = files.get(key)
        if not p or not Path(p).is_file():
            continue
        w, h = image_size(p)
        out.append(check(f"texture_{key}_pow2", is_power_of_two(w) and is_power_of_two(h), [w, h], "power of two"))
        out.append(check(f"texture_{key}_square", w == h, [w, h], "square"))
        if texture_budget:
            out.append(check(f"texture_{key}_budget", max(w, h) <= texture_budget, max(w, h), texture_budget))
    return out


def rig_checks(info: dict[str, Any], rig_expected: bool, min_coverage: float = 0.99,
               group_per_vertex: int | None = None) -> list[Check]:
    """From rig_info.json: bone count, single root, weight coverage, influences, normalisation. max_influences is
    compared with the rig's own group_per_vertex parameter (default 4)."""
    out: list[Check] = []
    bones = int(info.get("bones", 0))
    if not rig_expected:
        out.append(check("bones", bones == 0, bones, 0))
        return out
    out.append(check("bones", bones > 0, bones, ">0"))
    roots = info.get("roots") or []
    if roots:
        out.append(check("single_root", len(roots) == 1, len(roots), 1))
    if info.get("verts"):
        cov = float(info.get("weight_coverage", info.get("weighted_verts", 0) / info["verts"]))
        out.append(check("weight_coverage", cov >= min_coverage, round(cov, 4), min_coverage))
    if "max_influences" in info:
        limit = int(group_per_vertex or DEFAULT_MAX_INFLUENCES)
        out.append(check("max_influences", info["max_influences"] <= limit, info["max_influences"], limit))
    if "unnormalised_verts" in info:
        out.append(check("weights_normalised", info["unnormalised_verts"] == 0, info["unnormalised_verts"], 0))
    return out


def summarise(checks: list[Check]) -> dict[str, Any]:
    failed = [c["name"] for c in checks if not c["passed"]]
    return {"checks": checks, "checks_passed": len(checks) - len(failed), "checks_failed": failed, "gate_ok": not failed}
