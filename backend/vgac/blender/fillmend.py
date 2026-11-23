"""Texture corners for the faces a hole fill adds (pure maths, no bpy; prep.py uses it next to this script).

Blender's fill gives each new corner the texture position of a neighbouring corner at the same point. Around a hole
those neighbours belong to different texture pieces, so a new face can span the whole texture and lie on top of
everything else: on an open TRELLIS.2 cottage (remesh off) 813 such triangles, 6.7 percent of the surface, held 98
percent of the layout and the picture paint refused the model as mirrored (bakeoff/mirrored-layout).

A new face is wrong when any of its edges is much longer in the texture than the mesh's usual texture length per metre
(a thin sliver with far-apart corners has little texture area, so the edges are what tell). A wrong face is laid out
flat at its own shape (its best-fit plane, so a bent patch keeps its area) and placed in the texture on the far side of
an edge it shares with a face that is already right, at that face's texel size along the edge: the shared edge is no
seam and the patch never lies back over the face it was placed from. Wrong faces are placed outward from the right ones
until none is left or none can be reached.
"""

from __future__ import annotations

import numpy as np

STRETCH_MAX = 4.0  # an edge longer in the texture than this many times the mesh's median length per metre is wrong


def edge_ratios(pts: np.ndarray, uv: np.ndarray) -> np.ndarray:
    """Texture length per surface length of each edge of a polygon (corners in order); edges of no length left out."""
    p = np.asarray(pts, dtype=float)
    u = np.asarray(uv, dtype=float)
    lp = np.linalg.norm(np.roll(p, -1, axis=0) - p, axis=1)
    lu = np.linalg.norm(np.roll(u, -1, axis=0) - u, axis=1)
    ok = lp > 1e-9
    return lu[ok] / lp[ok]


def median_ratio(ratios: list[np.ndarray]) -> float:
    r = np.concatenate([np.asarray(x, dtype=float) for x in ratios]) if ratios else np.zeros(0)
    r = r[r > 0]
    return float(np.median(r)) if r.size else 0.0


def is_wrong(pts: np.ndarray, uv: np.ndarray, median: float, k: float = STRETCH_MAX) -> bool:
    r = edge_ratios(pts, uv)
    return median > 0 and r.size > 0 and bool((r > k * median).any())


def place_polygon(pts: np.ndarray, ia: int, ib: int, ua, ub, away) -> np.ndarray | None:
    """Texture positions for a polygon's corners pts (n, 3): laid flat in its best-fit plane, then placed so corners ia
    and ib land on ua and ub (the shape kept, scaled to that edge's texel size) and the rest of the polygon lies on the
    side of the edge away from `away` (a texture point of the face the edge is shared with). None when the edge has no
    length in 3D or in the texture, or the polygon has no plane."""
    p = np.asarray(pts, dtype=float)
    if len(p) < 3:
        return None
    o = p.mean(axis=0)
    _, s, vt = np.linalg.svd(p - o)
    if s[1] < 1e-12:
        return None
    q = (p - o) @ vt[:2].T
    z = q[:, 0] + 1j * q[:, 1]
    wa, wb, wx = complex(*ua), complex(*ub), complex(*away)
    d = wb - wa
    if abs(z[ib] - z[ia]) < 1e-12 or abs(d) < 1e-12:
        return None
    side_away = (np.conj(d) * (wx - wa)).imag
    for zz in (z, np.conj(z)):  # as laid out, and mirrored: the one whose body lies across the edge from `away`
        w = wa + d / (zz[ib] - zz[ia]) * (zz - zz[ia])
        if (np.conj(d) * (w.mean() - wa)).imag * side_away <= 0:
            return np.c_[w.real, w.imag]
    return None


class Coverage:
    """Which texels of an n x n grid over the texture square the layout already uses, so a placed patch can pick the
    placement that lies over the fewest of them."""

    def __init__(self, n: int = 1024):
        self.n = n
        self.used = np.zeros((n, n), dtype=bool)

    def _inside(self, uv: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        """(rows, cols, inside) for the texel centres in the polygon's box: inside marks the ones in the polygon (a fan
        of triangles from its first corner)."""
        u = np.asarray(uv, dtype=float) * self.n
        x0, y0 = np.clip(np.floor(u.min(axis=0)).astype(int), 0, self.n)
        x1, y1 = np.clip(np.ceil(u.max(axis=0)).astype(int) + 1, 0, self.n)
        if x1 <= x0 or y1 <= y0:
            return None
        X, Y = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
        ins = np.zeros(X.shape, dtype=bool)
        a = u[0]
        for b, c in zip(u[1:-1], u[2:]):
            den = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
            if abs(den) < 1e-12:
                continue
            w1 = ((X - a[0]) * (c[1] - a[1]) - (Y - a[1]) * (c[0] - a[0])) / den
            w2 = ((b[0] - a[0]) * (Y - a[1]) - (b[1] - a[1]) * (X - a[0])) / den
            ins |= (w1 >= 0) & (w2 >= 0) & (w1 + w2 <= 1)
        return slice(y0, y1), slice(x0, x1), ins

    def add(self, uv: np.ndarray) -> None:
        r = self._inside(uv)
        if r is not None:
            self.used[r[0], r[1]] |= r[2]

    def over(self, uv: np.ndarray) -> int:
        """How many already used texels the polygon lies over."""
        r = self._inside(uv)
        return 0 if r is None else int((self.used[r[0], r[1]] & r[2]).sum())
