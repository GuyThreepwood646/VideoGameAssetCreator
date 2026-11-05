"""Grid fit maths for scenery (G15, docs/GREAT-ASSETS-PLAN.md 3.8), kept free of bpy and mathutils.

prep.py runs inside Blender and imports this module from the same folder (sys.path insert, like _common); the
backend and the tests import it as vgac.blender.gridfit. Numbers in, numbers out. Sizes are Blender dimensions
[x, y, z] with z up: x is the width (left to right as seen from the front), y the depth (front to back), z the
height. The model has already been turned to face forward (G14) and scaled to its size (G12).

The rules (plan 3.8 A2):

1. Width and depth go to the nearest whole number of grid steps, when that side is at least 0.75 of a step. Thinner
   sides, such as a wall's thickness, are left alone.
2. Height goes to the nearest half step. A height under 0.75 of a half step (a thin slab) is left alone too: rounding
   it to nothing, or doubling it, is not a fit.
3. Each side may stretch or shrink by at most 15 percent. A side that needs more keeps its size, and the piece does
   not fit the grid (the grid_fit check fails).
4. A chosen footprint ("2x1": two squares wide, one deep) or a chosen height scales the whole piece evenly first, so
   the longer ground side (or the height) lands exactly on the choice without changing the proportions; the other
   sides then follow rules 1 to 3. The longer count goes on the longer ground side.

The pivot goes on the minimum corner (plan 3.8 A3): lowest X, lowest Z, Y = 0 in glTF and Unity terms, which is
Blender's lowest x, highest y (the back) and lowest z.
"""
from __future__ import annotations

import math

# The grid sizes offered (metres), and the default (plan 3.8 B: 1 m, standard).
GRID_OPTIONS = (0.5, 1, 2, 4)
DEFAULT_GRID_M = 1.0
# Footprint choices: squares wide by squares deep.
FOOTPRINTS = {"1x1": (1, 1), "2x1": (2, 1), "2x2": (2, 2), "4x1": (4, 1), "4x4": (4, 4)}
FOOTPRINT_OPTIONS = ("auto",) + tuple(FOOTPRINTS)
# Height choices in metres.
HEIGHT_OPTIONS = (0.5, 1, 3, 4)
GRID_HEIGHT_OPTIONS = ("auto",) + HEIGHT_OPTIONS
THIN_FRACTION = 0.75
MAX_STRETCH = 0.15
HEIGHT_STEP = 0.5  # of a grid step
# A piece is a floor when its height is under this fraction of its smaller footprint side (plan 3.8 A4).
FLOOR_RATIO = 0.25
# The final bounds count as whole grid steps within this much (plan 3.8 E: within 1 mm).
FIT_TOLERANCE_M = 0.001
# The final true-up after cleanup may move a snapped side by at most this fraction.
TRUE_UP_MAX = 0.02

SIDE_WORDS = ("wide", "deep", "tall")


def grid_value(grid_m) -> float:
    """The grid step in metres: one of GRID_OPTIONS, else the default."""
    try:
        g = float(grid_m)
    except (TypeError, ValueError):
        return DEFAULT_GRID_M
    return g if any(abs(g - o) < 1e-9 for o in GRID_OPTIONS) else DEFAULT_GRID_M


def height_choice(value):
    """A chosen height in metres, or None for automatic."""
    if value in (None, "", "auto"):
        return None
    try:
        h = float(value)
    except (TypeError, ValueError):
        return None
    return h if h > 0 else None


def _round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def snap_side(size: float, step: float, target: float | None = None, thin_ok: bool = True) -> dict:
    """One side: {size, target, steps, factor, state}. state is "snapped" (factor scales it onto target), "thin" (left
    alone: under 0.75 of a step and no target asked for) or "too_far" (needs more than 15 percent; left alone). steps
    is target / step for a snapped or too_far side."""
    size = float(size)
    if target is None:
        if size <= 0 or (thin_ok and size < THIN_FRACTION * step):
            return {"size": size, "target": size, "steps": None, "factor": 1.0, "state": "thin"}
        target = max(1, _round_half_up(size / step)) * step
    steps = round(target / step, 4)
    if size <= 0:
        return {"size": size, "target": size, "steps": steps, "factor": 1.0, "state": "too_far", "wanted": target}
    factor = target / size
    if abs(factor - 1.0) > MAX_STRETCH + 1e-9:
        return {"size": size, "target": size, "steps": steps, "factor": 1.0, "state": "too_far", "wanted": target}
    return {"size": size, "target": target, "steps": steps, "factor": factor, "state": "snapped"}


def plan_fit(dims, grid_m=DEFAULT_GRID_M, footprint="auto", height="auto") -> dict:
    """How to scale a piece onto the grid. dims are Blender [x, y, z]. Returns:

    lead: the even scale applied first (1 unless a footprint or height was chosen);
    scale: [sx, sy, sz], the whole scale per side from dims (lead included);
    dims: the dimensions after it; sides: the snap_side result per side (sizes after the lead scale);
    cells: [squares wide, squares deep] (a thin side counts as one square); footprint: "WxD";
    height_m; kind: "floor" or "wall"; fit: every side snapped or thin; off_side: the first side that did not fit
    (0, 1 or 2), else None."""
    g = grid_value(grid_m)
    dims = [max(0.0, float(d)) for d in list(dims)[:3]]
    fp = FOOTPRINTS.get(str(footprint)) if footprint not in (None, "", "auto") else None
    h_choice = height_choice(height)
    long_i = 0 if dims[0] >= dims[1] else 1
    short_i = 1 - long_i
    lead = 1.0
    if fp and dims[long_i] > 0:
        lead = fp[0] * g / dims[long_i]
    elif h_choice and dims[2] > 0:
        lead = h_choice / dims[2]
    led = [d * lead for d in dims]
    sides: list[dict] = [{}, {}, {}]
    if fp:
        sides[long_i] = snap_side(led[long_i], g, fp[0] * g)
        thin = led[short_i] < THIN_FRACTION * g
        sides[short_i] = snap_side(led[short_i], g) if thin else snap_side(led[short_i], g, fp[1] * g)
    else:
        sides[long_i] = snap_side(led[long_i], g)
        sides[short_i] = snap_side(led[short_i], g)
    step_h = g * HEIGHT_STEP
    # A chosen height without a footprint was reached by the lead scale (factor 1 here); with a footprint it is a
    # stretch like any other side, within 15 percent.
    sides[2] = snap_side(led[2], step_h, h_choice) if h_choice else snap_side(led[2], step_h)
    final = [s["target"] for s in sides]
    scale = [lead * s["factor"] for s in sides]
    cells = [int(round(s["steps"])) if s["state"] != "thin" and s["steps"] else 1 for s in sides[:2]]
    off = next((i for i, s in enumerate(sides) if s["state"] == "too_far"), None)
    return {"grid_m": g, "lead": lead, "scale": scale, "dims": final, "sides": sides, "cells": cells,
            "footprint": f"{cells[0]}x{cells[1]}", "height_m": final[2], "kind": piece_kind(final),
            "fit": off is None, "off_side": off}


def piece_kind(dims) -> str:
    """"floor" when the height is under a quarter of the smaller footprint side, else "wall"."""
    x, y, z = (float(d) for d in list(dims)[:3])
    small = min(x, y)
    return "floor" if small > 0 and z < FLOOR_RATIO * small else "wall"


def is_multiple(value: float, step: float, tol: float = FIT_TOLERANCE_M) -> bool:
    """True when value is a whole number (at least one) of steps, within tol."""
    if step <= 0 or value <= 0:
        return False
    n = _round_half_up(value / step)
    return n >= 1 and abs(value - n * step) <= tol


def judge_fit(dims, sides_state, grid_m, targets=None, tol: float = FIT_TOLERANCE_M) -> dict:
    """The final bounds against the grid. dims are the measured Blender [x, y, z]; sides_state the state per side from
    plan_fit; targets the snapped sizes (the chosen height may be off the half-step grid). A snapped side passes when
    it is within tol of its target (else of a whole number of steps: grid steps for width and depth, half steps for
    height); a thin side always passes; a too_far side fails. -> {fit, off_side, off_size, errors}."""
    g = grid_value(grid_m)
    errors = []
    off = None
    for i, (d, state) in enumerate(zip(list(dims)[:3], list(sides_state)[:3])):
        step = g if i < 2 else g * HEIGHT_STEP
        if state == "thin":
            errors.append(0.0)
            continue
        if state == "too_far":
            n = max(1, _round_half_up(float(d) / step))
            errors.append(abs(float(d) - n * step))
            off = i if off is None else off
            continue
        t = (targets or [None, None, None])[i]
        if t is not None:
            err = abs(float(d) - float(t))
        else:
            n = max(1, _round_half_up(float(d) / step))
            err = abs(float(d) - n * step)
        errors.append(err)
        if err > tol and off is None:
            off = i
    return {"fit": off is None, "off_side": off, "off_size": None if off is None else float(list(dims)[off]), "errors": errors}


def tile_layout(kind: str, dims, sides_state, grid_m) -> dict:
    """Where the copies go in the "How it tiles" picture and the gap they leave. Walls: three in a row along the longer
    ground side. Floors: a 3 by 3 block. The pitch on a tiling side is its whole number of grid steps (so a side that
    missed the grid shows its gap or overlap). -> {offsets: [[dx, dy]] in Blender metres (dy negative, toward the
    back), axes, pitch, gap_cm (the largest gap or overlap between neighbours)}."""
    g = grid_value(grid_m)
    x, y = float(list(dims)[0]), float(list(dims)[1])
    pitch = []
    for d, state in ((x, sides_state[0]), (y, sides_state[1])):
        if state == "thin":
            pitch.append(d)
        else:
            pitch.append(max(1, _round_half_up(d / g)) * g)
    long_i = 0 if x >= y else 1
    axes = [0, 1] if kind == "floor" else [long_i]
    offsets = []
    if kind == "floor":
        for j in range(3):
            for i in range(3):
                offsets.append([i * pitch[0], -j * pitch[1]])
    elif long_i == 0:
        offsets = [[i * pitch[0], 0.0] for i in range(3)]
    else:
        offsets = [[0.0, -i * pitch[1]] for i in range(3)]
    gap = max(abs(pitch[a] - (x, y)[a]) for a in axes)
    return {"offsets": offsets, "axes": axes, "pitch": pitch, "gap_cm": round(gap * 100, 2)}
