"""Place enlarged texture pieces (UV islands) into the free space of an existing layout without moving anything else.

Pure numpy (no Blender, no PIL), so blender/faceuv.py uses it inside Blender and the tests import it directly, like
gridfit.py. Used by the face detail pass: the face's islands are scaled up and put where the atlas is empty, and
every other island stays exactly where it was (so only the face changes; the rest keeps all its texels).

Coordinates: UV in 0..1, v up. The occupancy grid has row 0 at v = 1 (as images do).
"""
from __future__ import annotations

import math

import numpy as np


def rasterise_tris(tris: np.ndarray, res: int, grid: np.ndarray | None = None) -> np.ndarray:
    """Mark the cells of a res x res grid that triangles (n, 3, 2) in UV touch: every cell whose centre is inside a
    triangle, plus the cells holding its corners (so thin slivers are never missed)."""
    g = np.zeros((res, res), dtype=bool) if grid is None else grid
    if len(tris) == 0:
        return g
    px = np.empty_like(tris, dtype=np.float64)
    px[..., 0] = tris[..., 0] * res
    px[..., 1] = (1.0 - tris[..., 1]) * res
    for (ax, ay), (bx, by), (cx, cy) in px:
        x0 = max(0, int(math.floor(min(ax, bx, cx))))
        x1 = min(res, int(math.ceil(max(ax, bx, cx))) + 1)
        y0 = max(0, int(math.floor(min(ay, by, cy))))
        y1 = min(res, int(math.ceil(max(ay, by, cy))) + 1)
        if x1 <= x0 or y1 <= y0:
            continue
        for x, y in ((ax, ay), (bx, by), (cx, cy)):
            xi, yi = min(res - 1, max(0, int(x))), min(res - 1, max(0, int(y)))
            g[yi, xi] = True
        area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        if abs(area) < 1e-12:
            continue
        X, Y = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
        w0 = ((bx - X) * (cy - Y) - (by - Y) * (cx - X)) / area
        w1 = ((cx - X) * (ay - Y) - (cy - Y) * (ax - X)) / area
        w2 = 1.0 - w0 - w1
        g[y0:y1, x0:x1] |= (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
    return g


def grow(mask: np.ndarray, r: int) -> np.ndarray:
    """mask grown by r cells (8-neighbour)."""
    out = mask.copy()
    for _ in range(int(r)):
        m = out.copy()
        m[1:] |= out[:-1]
        m[:-1] |= out[1:]
        m[:, 1:] |= out[:, :-1]
        m[:, :-1] |= out[:, 1:]
        m[1:, 1:] |= out[:-1, :-1]
        m[:-1, :-1] |= out[1:, 1:]
        m[1:, :-1] |= out[:-1, 1:]
        m[:-1, 1:] |= out[1:, :-1]
        out = m
    return out


def rotate_uv(uv: np.ndarray, quarter: int, centre: np.ndarray) -> np.ndarray:
    """uv (..., 2) turned by quarter * 90 degrees about centre."""
    a = (quarter % 4) * math.pi / 2
    c, s = round(math.cos(a)), round(math.sin(a))
    d = uv - centre
    return np.stack([c * d[..., 0] - s * d[..., 1], s * d[..., 0] + c * d[..., 1]], axis=-1) + centre


def _free_positions(occ: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """For every placement of mask's top-left corner (rows, cols such that it stays inside occ), whether it overlaps
    nothing: (H - h + 1, W - w + 1) bool."""
    H, W = occ.shape
    h, w = mask.shape
    if h > H or w > W:
        return np.zeros((0, 0), dtype=bool)
    FH, FW = H + h, W + w
    a = np.fft.rfft2(occ.astype(np.float64), (FH, FW))
    b = np.fft.rfft2(mask[::-1, ::-1].astype(np.float64), (FH, FW))
    full = np.fft.irfft2(a * b, (FH, FW))
    # full[y + h - 1, x + w - 1] = overlap with mask placed at (y, x)
    overlap = full[h - 1:H, w - 1:W]
    return overlap < 0.5


def place_islands(fixed_tris: np.ndarray, islands: list[np.ndarray], res: int = 512, pad_cells: int = 2,
                  keep_near: bool = True) -> list[dict] | None:
    """Find a spot for each island (a (n, 3, 2) array of UV triangles, already at its new size) in the space the
    fixed triangles leave free, with pad_cells of empty cells around everything. Larger islands are placed first, each
    in any quarter turn, at the free spot nearest where it was (keep_near) or the first one found. Returns one
    {quarter, centre_from, centre_to} per island (in the input order): new_uv = rotate_uv(uv, quarter, centre_from) -
    centre_from + centre_to; or None when one does not fit."""
    occ = grow(rasterise_tris(np.asarray(fixed_tris, dtype=np.float64), res), pad_cells)
    order = sorted(range(len(islands)), key=lambda i: -float(np.ptp(islands[i][..., 0]) * np.ptp(islands[i][..., 1])))
    result: list[dict | None] = [None] * len(islands)
    for i in order:
        tris = np.asarray(islands[i], dtype=np.float64)
        c_from = tris.reshape(-1, 2).mean(axis=0)
        best = None
        for q in range(4):
            rt = rotate_uv(tris, q, c_from)
            lo, hi = rt.reshape(-1, 2).min(axis=0), rt.reshape(-1, 2).max(axis=0)
            # the island's own small grid, in cells of the atlas grid, anchored at its UV bounding box
            w = int(math.ceil((hi[0] - lo[0]) * res)) + 2
            h = int(math.ceil((hi[1] - lo[1]) * res)) + 2
            if w > res or h > res:
                continue
            # rasterise at the atlas grid's cell size into a square of the larger side (the box one cell in from its
            # corner), then crop to h x w
            side = max(w, h)
            sq = rt.copy()
            sq[..., 0] = (rt[..., 0] - lo[0]) * res / side + 1.0 / side
            sq[..., 1] = 1.0 - ((hi[1] - rt[..., 1]) * res / side + 1.0 / side)
            # the fixed islands carry the padding already (occ is grown), so the island itself is not grown here
            m = np.pad(rasterise_tris(sq, side)[:h, :w], pad_cells)
            free = _free_positions(occ, m)
            if free.size == 0 or not free.any():
                continue
            ys, xs = np.nonzero(free)
            # where the island's bounding box top-left would be originally (rows from v = 1)
            ox = lo[0] * res - pad_cells - 1
            oy = (1.0 - hi[1]) * res - pad_cells - 1
            d = (xs - ox) ** 2 + (ys - oy) ** 2 if keep_near else ys * res + xs
            j = int(np.argmin(d))
            cand = (float(d[j]), q, int(ys[j]), int(xs[j]), m, lo, hi)
            if best is None or cand[0] < best[0]:
                best = cand
        if best is None:
            return None
        _, q, y, x, m, lo, hi = best
        # a placed island keeps the padding from the ones placed after it
        occ[y:y + m.shape[0], x:x + m.shape[1]] |= grow(m, pad_cells)
        # new bounding box: left at x + pad + 1 cells, top at y + pad + 1 cells
        new_lo_u = (x + pad_cells + 1) / res
        new_hi_v = 1.0 - (y + pad_cells + 1) / res
        rt = rotate_uv(tris, q, c_from).reshape(-1, 2)
        lo_r, hi_r = rt.min(axis=0), rt.max(axis=0)
        shift = np.array([new_lo_u - lo_r[0], new_hi_v - hi_r[1]])
        result[i] = {"quarter": q, "centre_from": c_from.tolist(), "centre_to": (c_from + shift).tolist()}
    return result  # type: ignore[return-value]


def apply_placement(uv: np.ndarray, p: dict) -> np.ndarray:
    c_from = np.asarray(p["centre_from"])
    return rotate_uv(uv, p["quarter"], c_from) - c_from + np.asarray(p["centre_to"])
