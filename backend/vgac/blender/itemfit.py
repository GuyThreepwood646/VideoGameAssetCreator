"""Fit an item to the motion: the small changes tried and the search over them ("Fit it for me").

Pure Python with numpy (no bpy), so the backend tests import it as vgac.blender.itemfit and equip.py (inside Blender)
loads it from this folder. The depth of a change (how far the item sinks into the body over the sampled frames of
every clip) is measured by the caller and passed in as a function, so the same search runs on the real skinned body in
Blender and on simple shapes in the tests.

Changes, in the attachment point's own glTF frame (+Z forward, +Y up):

    strapped or worn (not held)   a push out along the point's outward axis (PUSH_STEPS_CM, at most 5 cm) and tilts
                                  about the item's pivot about the two axes square to the outward one (TILT_STEPS)
    held                          the grip stays in the palm: a turn about the grip axis (+Z, TURN_STEPS) and small
                                  tilts about the palm (HELD_TILTS); every change turns about the point itself

Least change first: a centimetre of push costs 1, a degree of tilt DEG_COST_CM, a degree of turn TURN_COST_CM. The
first change (by cost) whose worst score is within the limit wins. When none clears, the change with the lowest worst
score wins (least cost on a tie); when even that is no better than no change, nothing changes.

How deep one frame is (the measure check item_clear_of_body and the search share). For every sampled item point
inside the body (a held item's points within the hold radius of the hand are left out: the handle belongs in the
fist), its depth is the larger of:

    nearest   the distance to the nearest body surface (the shortest way out, in any direction)
    escape    for a strapped or worn item, how far the point must travel along the attachment point's outward axis to
              be outside again (the way a push moves it). This is what catches an item sunk into or through a thin
              part: inside a forearm the nearest surface is never farther than half the arm's thickness, whichever
              side the item came in from, but the way back out to the outside of the arm is the full distance it went in.

A frame's depth is the MIN_POINTS-th deepest point (one stray vertex does not count) and its share is the part of the
counted points that is inside. Its score is the worse of the two against their limits, in metres:
max(depth, share x limit / share_limit), so a score within the depth limit means both are within theirs.
"""
from __future__ import annotations

import math
from typing import Any, Callable

import numpy as np

PUSH_STEPS_CM = (0, 0.5, 1, 1.5, 2, 3, 4, 5)
TILT_STEPS = (0, 5, -5, 10, -10, 15, -15)
TURN_STEPS = (0, 15, -15, 30, -30, 45, -45, 60, -60, 90, -90, 180)
HELD_TILTS = (0, 5, -5, 10, -10)
DEG_COST_CM = 0.25
TURN_COST_CM = 0.1
PUSH_MAX_CM = max(PUSH_STEPS_CM)
TILT_MAX_DEG = max(TILT_STEPS)
MIN_POINTS = 3
DEPTH_LIMIT_M = 0.03   # item_clear_of_body: the deepest a counted point may go
SHARE_LIMIT = 0.05     # item_clear_of_body: the most of the item's counted points that may be inside


LIBRARY_HAIR_MARK = ("vgac_hair", "library")


def is_library_hair(extras: Any) -> bool:
    """True for a material marked as hair from the library (pack.py writes extras {"vgac_hair": "library"} on it; the
    glTF importer turns extras into the material's custom properties). Such faces are a separate hairstyle, not the
    body, so the items' body tree leaves them out. Today's hair slot carries no mark and stays in the body."""
    try:
        return extras is not None and extras.get(LIBRARY_HAIR_MARK[0]) == LIBRARY_HAIR_MARK[1]
    except Exception:  # noqa: BLE001
        return False


def body_polygons(polys, material_extras) -> list:
    """The polygons of the body tree: polys [(vertex indices, material index)]; material_extras per slot (a mapping or
    None). Polygons on a library hair slot are left out."""
    skip = {i for i, e in enumerate(material_extras or []) if is_library_hair(e)}
    return [tuple(v) for v, mi in polys if mi not in skip]


def point_depth(nearest: float, escape: float | None = None) -> float:
    """One inside point's depth: the nearest way out, or the way out along the push direction when that is longer."""
    return max(float(nearest), float(escape) if escape is not None else 0.0)


def frame_score(depths, counted: int, limit_m: float = DEPTH_LIMIT_M, share_limit: float = SHARE_LIMIT) -> dict[str, float]:
    """depths: the depth of every counted point found inside; counted: how many points were counted (inside or not).
    -> {depth, share, score} for one frame (all 0 when fewer than MIN_POINTS are inside)."""
    d = sorted((float(x) for x in depths), reverse=True)
    if len(d) < MIN_POINTS or counted <= 0:
        return {"depth": 0.0, "share": 0.0, "score": 0.0}
    depth, share = d[MIN_POINTS - 1], len(d) / float(counted)
    return {"depth": depth, "share": share, "score": max(depth, share * limit_m / share_limit)}


def rot(axis, deg) -> np.ndarray:
    a = np.asarray(axis, float)
    a = a / (np.linalg.norm(a) or 1.0)
    t = math.radians(deg)
    K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + math.sin(t) * K + (1 - math.cos(t)) * (K @ K)


def side_axes(outward) -> tuple[np.ndarray, np.ndarray]:
    """The two point axes most square to the outward one (the tilt axes)."""
    o = np.asarray(outward, float)
    o = o / (np.linalg.norm(o) or 1.0)
    ax = sorted((np.eye(3)[i] for i in range(3)), key=lambda e: abs(float(e @ o)))
    return ax[0], ax[1]


def candidates(held: bool, outward=(0.0, 0.0, 1.0)) -> list[dict[str, Any]]:
    """Every change tried, least change first: {cost, outward_cm, turn_deg, tilt_deg [a, b], R (3x3 turn applied
    before the placement's own), push (metres along outward), about ("point" or "pivot"), angle (radians, a bound on
    the turn), reach_push (metres)}."""
    out = []
    if held:
        for turn in TURN_STEPS:
            for tx in HELD_TILTS:
                for ty in HELD_TILTS:
                    R = rot((0, 0, 1), turn) @ rot((1, 0, 0), tx) @ rot((0, 1, 0), ty)
                    out.append({"cost": TURN_COST_CM * abs(turn) + DEG_COST_CM * (abs(tx) + abs(ty)), "outward_cm": 0.0,
                                "turn_deg": turn, "tilt_deg": [tx, ty], "R": R, "push": 0.0, "about": "point",
                                "angle": math.radians(abs(turn) + abs(tx) + abs(ty))})
    else:
        a1, a2 = side_axes(outward)
        for push in PUSH_STEPS_CM:
            for t1 in TILT_STEPS:
                for t2 in TILT_STEPS:
                    R = rot(a1, t1) @ rot(a2, t2)
                    out.append({"cost": push + DEG_COST_CM * (abs(t1) + abs(t2)), "outward_cm": float(push), "turn_deg": 0,
                                "tilt_deg": [t1, t2], "R": R, "push": push / 100.0, "about": "pivot",
                                "angle": math.radians(abs(t1) + abs(t2))})
    out.sort(key=lambda c: c["cost"])
    return out


def apply(cand: dict[str, Any], pos, R0, outward=(0.0, 0.0, 1.0)) -> tuple[np.ndarray, np.ndarray]:
    """The placement (position, 3x3 rotation) after one change, from the placement (pos, R0)."""
    p = np.asarray(pos, float)
    R = cand["R"] @ np.asarray(R0, float)
    if cand["about"] == "point":
        p = cand["R"] @ p
    else:
        o = np.asarray(outward, float)
        p = p + o / (np.linalg.norm(o) or 1.0) * cand["push"]
    return p, R


def search(cands: list[dict[str, Any]], depth: Callable[[dict[str, Any], float | None], float], limit: float) -> dict[str, Any]:
    """depth(cand, stop_above) -> the worst depth in metres over every sampled frame (it may stop early once past
    stop_above). -> {cand, depth, depth_before, cleared, changed, tried}."""
    none = cands[0] if cands and cands[0]["cost"] == 0 else None
    d0 = depth(none, None) if none is not None else float("inf")
    if d0 <= limit:
        return {"cand": none, "depth": d0, "depth_before": d0, "cleared": True, "changed": False, "tried": 1}
    tried = 1
    for c in cands:
        if c is none:
            continue
        tried += 1
        d = depth(c, limit)  # within the limit means every frame was measured, so it is the full worst depth
        if d <= limit:
            return {"cand": c, "depth": d, "depth_before": d0, "cleared": d <= limit, "changed": True, "tried": tried}
    scored = sorted(((depth(c, None), c["cost"], i) for i, c in enumerate(cands) if c is not none), key=lambda s: (round(s[0], 4), s[1]))
    if not scored or scored[0][0] >= d0:
        return {"cand": none, "depth": d0, "depth_before": d0, "cleared": False, "changed": False, "tried": tried}
    d, _, i = scored[0]
    return {"cand": cands[i], "depth": d, "depth_before": d0, "cleared": d <= limit, "changed": True, "tried": tried}
