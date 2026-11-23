"""Quality gate: the geometry, LOD, collider, texture, rig and facing criteria that are measurable from files.

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
# Facing (G14): a sure facing decision (facing.decide) turns the model to the nearest quarter turn, however far its
# best turn is from it; an unsure one only when the best turn is within AUTO_TURN_WINDOW_DEG of a quarter turn. The
# window alone left the golden-set wheelbarrow side-on (best turn 60, a sure decision, applied 0; bakeoff/facing).
# facing_off_check fails when the decided turn is more than FACING_TOLERANCE_DEG from the turn applied. Not 20: the
# wolf, which faces forward as built, has a best audit offset 30 degrees off, so 20 would warn on a correct model.
AUTO_TURN_WINDOW_DEG = 20
FACING_TOLERANCE_DEG = 45


def check(id_: str, ok: bool, value: Any, limit: Any, vars: dict[str, Any] | None = None) -> Check:
    """{name, passed, message} as the frontend renders them, plus {id, ok, value, limit}, the names the checks use everywhere else.
    vars are extra values for the check sentence (guidance.CHECK_GUIDE), kept on the check."""
    from . import guidance
    c = {"name": id_, "passed": bool(ok), "message": f"{value} (limit {limit})", "id": id_, "ok": bool(ok), "value": value, "limit": limit}
    if vars:
        c["vars"] = dict(vars)
    return guidance.describe_check(c)


def not_measured(id_: str, what: str, why: str, vars: dict[str, Any] | None = None) -> Check:
    """A check that could not be measured. It fails (never passes), so a missing measure is not read as a good one: the
    sentence is "Not measured: {what} was not checked, because {why}." (guidance.describe_check), with no fix.
    value is "not measured"; vars.measured is False and carries what and why."""
    c = check(id_, False, "not measured", None, vars={**(vars or {}), "measured": False, "what": what, "why": why})
    c["message"] = "not measured"
    return c


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


def auto_turn(best: float | None, sure: bool = False) -> int:
    """The turn prep applies on Automatic: the nearest quarter turn when the decision is sure (facing.decide), or when
    the best turn is within AUTO_TURN_WINDOW_DEG of it; else none."""
    if best is None:
        return 0
    q = nearest_quarter(best)
    return q if sure or angle_diff(best, q) <= AUTO_TURN_WINDOW_DEG else 0


def facing_off_check(decided: float, applied: float) -> Check:
    """facing_tests when the model was left more than FACING_TOLERANCE_DEG from the turn the facing decision found (an
    unsure decision outside the window): fails, value the angle between them, fix the decided quarter turn."""
    a = int(round(float(applied))) % 360
    d = int(round(float(decided))) % 360
    return check("facing_tests", False, round(angle_diff(d, a), 1), FACING_TOLERANCE_DEG,
                 vars={"state": "off", "off": True, "decided": d, "applied": a, "fix_yaw": nearest_quarter(d)})


def plain_reason(reason: str | None) -> str:
    """A test's reason as a plain clause for a check sentence: the bracketed examples dropped ("the legs cannot be told
    apart (a robe, ...)" -> "the legs cannot be told apart")."""
    import re

    return re.sub(r"\s*\([^)]*\)", "", str(reason or "")).strip().rstrip(".") or "they were not found"


def facing_tests_check(outline_turn: float, toes_turn: float, differ: float, decided: float | None, applied: float,
                       by: str | None = None) -> Check:
    """facing_tests: the two independent facing tests (the outline against the drawn views, and where the toes point)
    agree to within facing.DISAGREE_DEG. Fails when they do not: the model may face the wrong way and nothing else
    would say so (the outline test alone cannot fail, it is compared with itself). value is the angle between them;
    vars carry both turns, the turn applied, which test the decision went with (by: "outline" or "toes") and the fix,
    the turn that was decided on when it is not the one applied."""
    from .facing import DISAGREE_DEG

    q = nearest_quarter(decided) if decided is not None else None
    fix_yaw = q if q is not None and angle_diff(q, applied) > 0 else None
    return check("facing_tests", float(differ) <= DISAGREE_DEG, round(float(differ), 1), DISAGREE_DEG,
                 vars={"state": "tests", "outline_turn": int(round(outline_turn)) % 360, "toes_turn": int(round(toes_turn)) % 360,
                       "applied": int(round(float(applied))) % 360, "by": by if by in ("outline", "toes") else None,
                       "fix_yaw": fix_yaw})


def facing_unsure_check(decided: float | None, applied: float, flat: bool = False, toes_reason: str = "") -> Check:
    """facing_tests when the front could not be told from the back (facing.decide's `sure` False): a warning, measured,
    whose fix turns the model half a circle from where it was put. value is the turn applied."""
    a = int(round(float(applied))) % 360
    return check("facing_tests", False, a, None, vars={"state": "unsure", "unsure": True, "flat": bool(flat),
                                                       "toes_reason": plain_reason(toes_reason),
                                                       "decided": None if decided is None else int(decided) % 360,
                                                       "fix_yaw": (a + 180) % 360})


def facing_settled_check(state: str, applied: float, toes_reason: str = "", decided: float | None = None) -> Check:
    """facing_tests when the turn was settled without the two tests agreeing, and rightly so (passes): "colour" (a
    near-tie the colours of the front and back pictures confirmed as built), "colour_overruled" (they showed the model
    was built facing backwards and it was turned round), "chosen" (the person chose the turn). value is the turn
    applied; decided is the turn the app's own decision would have made (for "chosen")."""
    a = int(round(float(applied))) % 360
    d = None if decided is None else int(round(float(decided))) % 360
    return check("facing_tests", True, a, None, vars={"state": state, "toes_reason": plain_reason(toes_reason),
                                                      "decided": d, "differs": d is not None and angle_diff(d, a) > 20})


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


# Scenery on the grid (G15): copies placed side by side may leave at most this gap or overlap, in centimetres.
TILE_GAP_MAX_CM = 0.5


def grid_fix_params(g: dict[str, Any], params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The prep params that would put a piece that missed the grid onto it, or None. A chosen height that missed goes
    back to automatic; else a chosen footprint goes back to automatic (plan 3.8: Snap to the grid). When both are
    already automatic, the next finer grid on which every side fits (from the size before the grid), else None."""
    from .blender import gridfit
    p = params or {}
    off = g.get("off_side")
    height_chosen = p.get("grid_height") not in (None, "", "auto")
    footprint_chosen = p.get("grid_footprint") not in (None, "", "auto")
    if off == 2 and height_chosen:
        return {"grid_height": "auto"}
    if footprint_chosen:
        return {"grid_footprint": "auto"}
    if height_chosen:
        return {"grid_height": "auto"}
    before = g.get("dims_before_m")
    grid = gridfit.grid_value(g.get("grid_m"))
    if before:
        for finer in sorted((o for o in gridfit.GRID_OPTIONS if o < grid), reverse=True):
            if gridfit.plan_fit(before, finer)["fit"]:
                return {"grid_m": finer}
    return None


def grid_checks(g: dict[str, Any], params: dict[str, Any] | None = None) -> list[Check]:
    """grid_fit and tiles_cleanly from the Blender prep report's "grid" block (G15). grid_fit: every side the grid
    rounds is a whole number of grid steps (half steps for the height) within 1 mm; value is the size [width, depth,
    height] in metres, limit the grid step. tiles_cleanly: the largest gap or overlap between copies placed at the grid
    pitch, in centimetres."""
    from .blender import gridfit
    out: list[Check] = []
    dims = [float(v) for v in (g.get("dims_m") or [])]
    if len(dims) < 3:
        return out
    grid = gridfit.grid_value(g.get("grid_m"))
    judged = gridfit.judge_fit(dims, g.get("states") or ["snapped"] * 3, grid, g.get("targets_m"))
    off = judged["off_side"]
    smaller = all(s == "thin" for s in (g.get("states") or [])[:2])
    vars_: dict[str, Any] = {"w": round(dims[0], 3), "d": round(dims[1], 3), "h": round(dims[2], 3), "grid": grid,
                             "small": smaller}
    if off is not None:
        vars_.update(side=round(dims[off], 3), side_word=gridfit.SIDE_WORDS[off],
                     fix_params=grid_fix_params({**g, "off_side": off}, params))
    out.append(check("grid_fit", judged["fit"], [round(d, 3) for d in dims], grid, vars=vars_))
    gap = g.get("tile_gap_cm")
    if gap is not None:
        out.append(check("tiles_cleanly", float(gap) <= TILE_GAP_MAX_CM, round(float(gap), 1), TILE_GAP_MAX_CM,
                         vars={"kind": g.get("kind")}))
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


# LOD outline (step 6, warning only): each level's outline against LOD0, from the seven views of blender/outline.py
# (the same measure as step 4's outline_kept). The lowest IoU a category's level may show. Each mark is the lowest level
# of the reference subjects that shows no visible loss, minus 0.01, rounded down to 0.01; the foliage LOD2 levels that
# lost a branch mass are left out of that calibration. Environment is set to 0.90, one step above the rule, because
# a level just under it already shows broken edge detail while clean levels sit well above it. The region mark
# stays at the step 4 value.
LOD_OUTLINE_IOU_MIN = {"humanoid": 0.97, "quadruped": 0.96, "prop_rigid": 0.97, "vehicle": 0.98, "environment": 0.90,
                       "foliage": 0.90}
LOD_OUTLINE_REGION_MAX = 0.02


def lod_outline_checks(report: dict[str, Any], category_id: str | None) -> list[Check]:
    """One check per level that pack.py measured ("lod_outline" in its report): the lowest view IoU against the category's
    mark, and the largest lost or filled patch against LOD_OUTLINE_REGION_MAX. Nothing for a category with no mark or a
    pack that measured no outline (LOD0 only, or an older report)."""
    levels = report.get("lod_outline") or {}
    iou_min = LOD_OUTLINE_IOU_MIN.get(category_id or "")
    if not levels or iou_min is None:
        return []
    out: list[Check] = []
    for level, o in sorted(levels.items(), key=lambda kv: int(kv[0].removeprefix("LOD"))):
        iou, region = float(o["iou_min"]), float(o["region_max"])
        ok = iou >= iou_min and region <= LOD_OUTLINE_REGION_MAX
        out.append(check(f"lod_outline_kept_{level}", ok, round(iou, 4), iou_min,
                         vars={"level": level.removeprefix("LOD"), "region": round(region, 4),
                               "region_limit": LOD_OUTLINE_REGION_MAX, "kind": o.get("region_max_kind"),
                               "view": o.get("region_max_view") if region > LOD_OUTLINE_REGION_MAX else o.get("iou_min_view")}))
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
               group_per_vertex: int | None = None, moving_parts: list[str] | None = None) -> list[Check]:
    """From rig_info.json: bone count, single root, weight coverage, influences, normalisation. max_influences is
    compared with the rig's own group_per_vertex parameter (default 4). moving_parts: the names of the parts the
    asset declared to move (declared_parts kind separate); with no skeleton they are named as not rigged yet."""
    out: list[Check] = []
    bones = int(info.get("bones", 0))
    if not rig_expected:
        vars_ = {"moving": list(moving_parts)} if moving_parts and bones == 0 else None
        out.append(check("bones", bones == 0, bones, 0, vars=vars_))
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


# --- WP-86 and WP-92: warnings for things that were measured but never judged. None blocks shipping, and each limit is wide
# on purpose: the library has only humanoid (and three prop) samples, so none is calibrated per category yet. -----------------

# Painted share (stages/prep.py): of the surface, the share the picture painting painted. Lowest on 54 applied runs of
# humanoid characters: 0.29 (5th percentile 0.33, median 0.69).
PAINTED_SHARE_MIN = 0.20
# UV transfer (blender/rig_fix.py): the farthest a face's nearest reference face was, as a share of the model's size. Every
# one of 59 recorded humanoid transfers was 0.0.
UV_TRANSFER_OFFSET_MAX = 0.02
# Slivers: the share of triangles with a corner under meshchecks.SLIVER_ANGLE_DEG. Humanoid 0.1 to 7.3 percent, 3 props 0.1 to 0.7.
SLIVER_SHARE_MAX = 0.15
# Texel-density spread (meshchecks.texel_density_spread): p95 over p5 of the pieces' density. Humanoid 1.0 to 3.05 (the face
# and hair pieces are laid out deliberately denser); 3 props 1.0.
TEXEL_DENSITY_SPREAD_MAX = 4.0
# Flipped shells: the share of the surface in shells that look inside out. 0 on all 62 humanoid and 3 prop samples.
FLIPPED_SHELLS_MAX = 0.05
# Re-bake (blender/rebake.py measure_unreached): the share of covered texels no ray reached. Environments were recorded at 15 to 19 percent.
UNREACHED_TEXELS_MAX = 0.02


def painted_share_check(share: Any, limit: float = PAINTED_SHARE_MIN) -> Check | None:
    """painted_share: the picture painting ran (picture_projection passes) but painted little of the surface."""
    if not isinstance(share, (int, float)) or isinstance(share, bool):
        return None
    return check("painted_share", float(share) >= limit, round(float(share), 4), limit)


def uv_transfer_check(uvs: Any, limit: float = UV_TRANSFER_OFFSET_MAX) -> Check | None:
    """uv_transfer_offset: texture coordinates were copied from the reference model and the nearest reference face was far away."""
    off = (uvs or {}).get("max_face_offset") if isinstance(uvs, dict) else None
    if not isinstance(off, (int, float)) or isinstance(off, bool):
        return None
    return check("uv_transfer_offset", float(off) <= limit, round(float(off), 5), limit)


def mesh_measure_checks(mm: dict[str, Any] | None) -> list[Check]:
    """slivers, texel_density_spread and flipped_shells from meshchecks.measure_glb; a measure that is missing makes no check."""
    out: list[Check] = []
    if not mm:
        return out
    s = mm.get("slivers")
    if s and s.get("faces"):
        out.append(check("slivers", s["share"] <= SLIVER_SHARE_MAX, round(float(s["share"]), 4), SLIVER_SHARE_MAX,
                         vars={"count": s["count"], "faces": s["faces"]}))
    d = mm.get("texel_density")
    if d and isinstance(d.get("spread"), (int, float)):
        out.append(check("texel_density_spread", d["spread"] <= TEXEL_DENSITY_SPREAD_MAX, round(float(d["spread"]), 2),
                         TEXEL_DENSITY_SPREAD_MAX, vars={"islands": d["islands"]}))
    f = mm.get("flipped_shells")
    if f and f.get("shells"):
        out.append(check("flipped_shells", f["flipped_share"] <= FLIPPED_SHELLS_MAX, round(float(f["flipped_share"]), 4),
                         FLIPPED_SHELLS_MAX, vars={"flipped": f["flipped"], "shells": f["shells"]}))
    return out


def unreached_check(u: Any, limit: float = UNREACHED_TEXELS_MAX) -> Check | None:
    """unreached_texels: the share of covered texels the re-bake's rays never reached (blender/rebake.measure_unreached)."""
    share = (u or {}).get("share") if isinstance(u, dict) else None
    if not isinstance(share, (int, float)) or isinstance(share, bool):
        return None
    return check("unreached_texels", float(share) <= limit, round(float(share), 4), limit,
                 vars={"texels": u.get("texels"), "covered": u.get("covered")})
