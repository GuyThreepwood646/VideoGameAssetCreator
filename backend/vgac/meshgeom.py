"""Is the 3D model whole? The geometry measures for step 3 and the model_whole check built on them.

Blender (blender/mesh_health.py) joins the seam duplicates glTF makes at texture seams, then counts open edges, bad joins,
folded edges, sliver triangles and separate pieces, and draws the model grey with the texture off. This module reads that
report, judges it per route (the families of plan section 5A) and makes the check (severity warn).

Why not the silhouette score: a model shattered into shards can still fill the drawn outline (the Nutra Pak building scored
0.838). The folded-edge share is what separated it: folded edges are edges whose two faces face away from each other by
more than 120 degrees, the look of torn paper.

Calibration (docs are local; bakeoff/wp43): the limits below are the owner's Nutra Pak versions and the 20 WP-40 models.
Buildings have bad examples to calibrate on; the other routes have good examples only, so their limits are provisional,
set well above the good results and well below the damage seen on buildings.

A failed check never stops an automatic build (decision D23). On a route in REBUILD_ROUTES the 3D step builds again with the
next seeds (at most REBUILD_MAX more builds), keeps the best (pick_best: among builds whose base colour passes, lowest folded
share, then lowest seed) and, when the best still fails, goes on with it and shows the check as "likely to show in the game". Buildings are the first route:
on the WP-44 rerun 2 the 6 percent folded limit had no false block and one miss on 37 blind or checked buildings
(a local fold the whole-model share cannot see; a local measure was tried and could not be calibrated, so none ships). The other routes have good examples only, so they are not rebuilt yet.
"""
from __future__ import annotations

from typing import Any

from . import quality

# Category -> route family (plan section 5A). A category not listed here has no limits and is not judged.
ROUTE_OF = {
    "humanoid": "characters",
    "quadruped": "creatures", "winged": "creatures", "multileg": "creatures", "serpent": "creatures",
    "prop_rigid": "hard_objects", "prop_articulated": "hard_objects", "vehicle": "hard_objects",
    "environment": "buildings",
    "foliage": "plants",
}
ROUTE_WORDS = {"characters": "character", "creatures": "creature", "hard_objects": "hard object",
               "buildings": "building or scenery piece", "plants": "plant"}

# Per route: the largest share of edges folded back, of open edges, of bad joins, of sliver triangles that still counts
# as whole, and the smallest share of the surface the biggest piece must have. None: the route may have it on purpose
# (plants and open scenery have open edges and separate pieces by design, so only folds and slivers count there).
LIMITS: dict[str, dict[str, float | None]] = {
    "characters":   {"folded": 0.03, "open": 0.05, "bad_joins": 0.02, "slivers": 0.05, "largest_piece": 0.5},
    "creatures":    {"folded": 0.05, "open": 0.05, "bad_joins": 0.02, "slivers": 0.05, "largest_piece": 0.5},
    "hard_objects": {"folded": 0.03, "open": 0.05, "bad_joins": 0.02, "slivers": 0.05, "largest_piece": 0.5},
    "buildings":    {"folded": 0.06, "open": 0.05, "bad_joins": 0.02, "slivers": 0.05, "largest_piece": 0.5},
    "plants":       {"folded": 0.20, "open": None, "bad_joins": None, "slivers": 0.05, "largest_piece": None},
}

# Routes whose shape gate rebuilds the model on a fail (D23), and how many extra builds at most (builds take about 4 minutes).
REBUILD_ROUTES: frozenset[str] = frozenset({"buildings"})
REBUILD_MAX = 2

PROBLEM_WORDS = {
    "folded": "surface folded back on itself along {share} of its edges (up to {limit} is normal)",
    "open": "holes or torn seams along {share} of its edges (up to {limit} is normal)",
    "bad_joins": "places where three or more surfaces meet on one edge, along {share} of its edges (up to {limit} is normal)",
    "slivers": "very thin, stretched triangles, {share} of them (up to {limit} is normal)",
    "largest_piece": "split into pieces: the biggest piece is only {share} of the surface, it should be at least {limit}",
}


def route_of(category: str | None) -> str | None:
    return ROUTE_OF.get(category or "")


def shares(report: dict[str, Any]) -> dict[str, float]:
    """The measures as shares, from a mesh_health.py report."""
    edges = max(1, int(report.get("edges") or 0))
    return {
        "folded": float((report.get("folded") or {}).get("120", 0.0)),
        "open": float(report.get("open_edges") or 0) / edges,
        "bad_joins": float(report.get("bad_join_edges") or 0) / edges,
        "slivers": float(report.get("sliver_share") or 0.0),
        "largest_piece": float(report.get("largest_piece_area") if report.get("largest_piece_area") is not None else 1.0),
    }


def problems(route: str, report: dict[str, Any]) -> list[str]:
    """Plain phrases for each measure over its limit on this route (empty: the model is whole)."""
    lim, sh, out = LIMITS[route], shares(report), []
    for key, limit in lim.items():
        if limit is None:
            continue
        over = sh[key] < limit if key == "largest_piece" else sh[key] > limit
        if over:
            out.append(PROBLEM_WORDS[key].format(share=f"{sh[key] * 100:.1f}%", limit=f"{limit * 100:.0f}%"))
    return out


def summary(category: str | None, report: dict[str, Any] | None) -> dict[str, Any] | None:
    """What is kept in the version metrics (metrics.geometry): the raw counts, the shares, the route and the verdict."""
    if not report:
        return None
    route = route_of(category)
    out = {**{k: report.get(k) for k in ("triangles", "edges", "open_edges", "bad_join_edges", "pieces", "largest_piece_area",
                                         "sliver_share")},
           "folded_edge_share": (report.get("folded") or {}).get("120"), "route": route}
    if route:
        out["problems"] = problems(route, report)
    return out


def check(category: str | None, geometry: dict[str, Any] | None, rebuild: bool | None = None, rebuilds: int = 0) -> dict[str, Any] | None:
    """The model_whole check (warn), or None when there is nothing to judge: no measure (Blender failed), or a category
    with no 3D model (material). value is the folded-edge share; vars.problems has every measure over its limit;
    vars.rebuilds says how many extra builds were made (WP-83) so the sentence can say the best was kept."""
    if not geometry or not geometry.get("route"):
        return None
    route = geometry["route"]
    probs = geometry.get("problems") or []
    folded = geometry.get("folded_edge_share") or 0.0
    return quality.check("model_whole", not probs, round(float(folded), 4), LIMITS[route]["folded"],
                         vars={"route": route, "route_words": ROUTE_WORDS[route], "problem_list": "; ".join(probs),
                               "rebuild": rebuild, "rebuilds": int(rebuilds or 0)})


def passes(category: str | None, report: dict[str, Any] | None) -> bool | None:
    """The shape gate on one build's mesh_health report: True when no measure is over its limit, None when there is nothing to
    judge (no report, or a category with no limits)."""
    route = route_of(category)
    if not report or route is None:
        return None
    return not problems(route, report)


def rebuilds_wanted(category: str | None) -> bool:
    return route_of(category) in REBUILD_ROUTES


def pick_best(builds: list[dict[str, Any]]) -> int:
    """The build to keep out of the tries: each has "seed", "folded" (the folded-edge share; None when it could not be
    measured, which counts as worst) and "colour" (texcolour's result, {"ok": ...}; None when it was not measured, which
    counts as passing). Among the builds whose colour passes: lowest folded share, then lowest seed, then the earlier
    build. When none passes, the same order over all of them. Never random."""
    def key(i: int) -> tuple[float, int, int]:
        f = builds[i].get("folded")
        return (float("inf") if f is None else float(f), int(builds[i].get("seed") or 0), i)
    passing = [i for i in range(len(builds)) if (builds[i].get("colour") or {}).get("ok") is not False]
    return min(passing or range(len(builds)), key=key)
