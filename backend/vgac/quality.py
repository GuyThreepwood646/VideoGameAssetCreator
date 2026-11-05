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
# The size check measures the category's axis with this tolerance (docs/GREAT-ASSETS-PLAN.md 3.2 A3).
SIZE_TOLERANCE = 0.03
# Facing (G14): prep turns automatically only when the audit's best turn is this close to a quarter turn; the facing
# check fails only when a different quarter turn is closer to the best turn than the one applied (more than 45
# degrees off). The plan's first draft failed at 20 degrees, but the wolf, which faces forward as built, has a best
# audit offset 30 degrees off, so 20 would warn on a correct model.
AUTO_TURN_WINDOW_DEG = 20
FACING_TOLERANCE_DEG = 45


def check(id_: str, ok: bool, value: Any, limit: Any, vars: dict[str, Any] | None = None) -> Check:
    """{name, passed, message} as the frontend renders them, plus {id, ok, value, limit} as ARCHITECTURE.md names them.
    vars are extra values for the check sentence (guidance.CHECK_GUIDE), kept on the check."""
    from . import guidance
    c = {"name": id_, "passed": bool(ok), "message": f"{value} (limit {limit})", "id": id_, "ok": bool(ok), "value": value, "limit": limit}
    if vars:
        c["vars"] = dict(vars)
    return guidance.describe_check(c)


def axis_extent(dims_m: list[float] | tuple[float, ...], axis: str = "height") -> float:
    """The extent the size is measured on, from Blender dimensions [x, y, z] (z up): height is z; horizontal the larger
    of x and y (the ground plane; X and Z in a Y-up engine); longest the largest of all three."""
    x, y, z = (float(d) for d in dims_m[:3])
    if axis == "horizontal":
        return max(x, y)
    if axis == "longest":
        return max(x, y, z)
    return z


def angle_diff(a: float, b: float) -> float:
    d = abs(float(a) - float(b)) % 360
    return min(d, 360 - d)


def nearest_quarter(turn: float) -> int:
    return int(round(float(turn) / 90.0) * 90) % 360


def best_turn(offset: float, sign: int = 1) -> int:
    """The turn (degrees counter-clockwise seen from above) that brings the concept's front to face forward, from the
    mesh audit's best alignment (imaging.best_alignment). offset is the render yaw that matched the front picture;
    camera yaw 0 looks at the Blender front (-Y, which is +Z in Unity and glTF). A sign of -1 is the mirrored twin of
    the same fit (front and back silhouettes are mirror images), so it is read as the sign +1 fit at offset + 180."""
    off = float(offset) + (180.0 if int(sign) < 0 else 0.0)
    return int(round(-off)) % 360


def auto_turn(best: float | None) -> int:
    """The turn prep applies on Automatic: the nearest quarter turn when the best turn is within the window, else none."""
    if best is None:
        return 0
    q = nearest_quarter(best)
    return q if angle_diff(best, q) <= AUTO_TURN_WINDOW_DEG else 0


def facing_check(best: float, applied: float) -> Check:
    """facing: passes when the applied turn is the quarter turn nearest the audit's best turn. The fix turns to the
    nearest quarter turn, and is left out when that is the turn already applied."""
    q = nearest_quarter(best)
    ok = angle_diff(best, applied) <= FACING_TOLERANCE_DEG
    fix_yaw = q if angle_diff(q, applied) > 0 else None
    return check("facing", ok, int(round(best)) % 360, int(round(applied)) % 360, vars={"fix_yaw": fix_yaw})


def geometry_checks(report: dict[str, Any], triangle_budget: int | None = None, size_m: float | None = None,
                    size_tolerance: float = SIZE_TOLERANCE, size_axis: str = "height", axis_word: str | None = None,
                    *, height_m: float | None = None, height_tolerance: float | None = None) -> list[Check]:
    """From a Blender prep or pack report: manifold, degenerate faces, triangle budget, and size along the category's
    axis (size_m; height_m and height_tolerance are the old names and mean the height axis)."""
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
    if size_m is None and height_m:
        size_m, size_axis = height_m, "height"
    if height_tolerance is not None:
        size_tolerance = height_tolerance
    dims = report.get("dims_m")
    if dims and size_m:
        from .reference import AXIS_WORD_DEFAULT
        extent = axis_extent(dims, size_axis)
        ok = abs(extent - size_m) <= size_tolerance * size_m
        word = axis_word or AXIS_WORD_DEFAULT.get(size_axis, "tall")
        out.append(check("size_m", ok, round(extent, 4), size_m, vars={"axis_word": word, "size_axis": size_axis}))
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
