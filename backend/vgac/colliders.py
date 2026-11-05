"""Collision shapes for the export stage (G16 with G35, docs/GREAT-ASSETS-PLAN.md 3.4). Runs in the backend process
(CoACD is in the backend venv, not in Blender's Python); blender/pack.py turns what this writes into collider meshes.

All geometry here is in Blender's frame (Z up, metres): the input GLB is read with trimesh and turned from glTF's
Y up the way Blender's importer does it, (x, y, z) -> (x, -z, y). pack.py also maps the parts from the bounding box
written in the OBJ header onto its own LOD0 box, so a node transform the two readers treat differently cannot shift
the shapes.

What is measured on a voxel grid (64 cells along the longest side, no scipy):
- solid: the cells inside the surface (flood fill from outside);
- hull: the cells inside the convex hull, approximated by a 128-direction k-DOP (slightly larger than the true hull);
- openings: rays front to back and side to side through the bounding box that miss the visible surface and are
  closed in by it on the left, right and top (a doorway, an arch, the gap under a cart; rays past a sloping edge are
  open to the outside and do not count). The bottom is left open, so a doorway that reaches the floor counts.

The choice for `collider: auto` by category (plan 3.4 A):
- environment: the exact shape (a static mesh collider);
- prop_rigid and prop_articulated: several shapes when the object is hollow (solid under 0.7 of its hull) or when one
  hull would fill an opening; else the first of box, upright cylinder, capsule and sphere whose volume is at most 1.3
  times the hull's while covering at least 0.85 of it; else one hull. Hollowness and openings come first because a
  primitive is compared with the hull, and the hull of an arch is a box;
- vehicle: several shapes (at most 6);
- foliage: a capsule around the trunk (the bottom 40 percent of the height);
- rigged categories: one hull, which the Unity importer replaces with the body shape for moving (G34).
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

import numpy as np

MAX_HULL_TRIANGLES = 250   # Unity's convex MeshCollider cap is 255 triangles [S2]; the plan keeps a margin (G35)
HULL_VERTEX_CAP = 120      # a convex hull with V points has 2V - 4 triangles, so 120 points keeps it at 236
HOLLOW_RATIO = 0.7
PRIMITIVE_COVER = 0.85
PRIMITIVE_MAX_VOLUME = 1.3
OPENING_BLOCKED_MAX = 0.10
MIN_OPENING_M = 0.3        # a gap only counts as an opening when something could pass: at least this wide and this tall
GRID = 64

RIGGED = ("humanoid", "quadruped", "multileg", "winged", "serpent")
OPENINGS_CATEGORIES = ("prop_rigid", "environment", "vehicle")
MODES = ("auto", "hull", "decompose", "mesh", "none")

# Most shapes per kind of asset, per platform (plan 3.4 A: 8 for props, 16 for scenery, 6 for a vehicle); a phone
# gets half so physics stays cheap there.
PART_CAPS = {"prop": 8, "scenery": 16, "vehicle": 6}
PHONE_PLATFORMS = ("mobile_low",)


def part_group(category: str | None) -> str:
    if category == "environment":
        return "scenery"
    if category == "vehicle":
        return "vehicle"
    return "prop"


def max_parts(category: str | None, platform: str | None, override: Any = None) -> int:
    """The most convex shapes for this category and platform; `override` (collider_max_parts, 1 to 32) wins."""
    try:
        if override is not None and override != "":
            return max(1, min(32, int(override)))
    except (TypeError, ValueError):
        pass
    cap = PART_CAPS[part_group(category)]
    if platform in PHONE_PLATFORMS:
        cap = max(2, cap // 2)
    return cap


def normalise_mode(value: Any) -> str:
    """The export knob as one of MODES; empty means auto."""
    v = str(value or "auto").strip().lower()
    return v if v in MODES else "auto"


# --- Loading -----------------------------------------------------------------------------------------------------------

def load_glb_zup(path: Path | str) -> tuple[np.ndarray, np.ndarray]:
    """(vertices n x 3 float64, faces m x 3 int64) of every mesh in the GLB with its node transforms, in Blender's
    frame, with vertices split along texture seams welded back together."""
    import trimesh

    scene = trimesh.load(str(path), force="scene")
    geoms = []
    for node in scene.graph.nodes_geometry:
        transform, gname = scene.graph[node]
        g = scene.geometry.get(gname)
        if g is None or not hasattr(g, "faces") or len(g.faces) == 0:
            continue
        v = np.asarray(g.vertices, dtype=np.float64)
        v = v @ np.asarray(transform)[:3, :3].T + np.asarray(transform)[:3, 3]
        geoms.append((v, np.asarray(g.faces, dtype=np.int64)))
    if not geoms:
        raise ValueError(f"no triangles in {path}")
    verts, faces, off = [], [], 0
    for v, f in geoms:
        verts.append(v)
        faces.append(f + off)
        off += len(v)
    v = np.concatenate(verts)
    f = np.concatenate(faces)
    v = np.stack([v[:, 0], -v[:, 2], v[:, 1]], axis=1)
    return weld(v, f)


def weld(v: np.ndarray, f: np.ndarray, tol: float = 1e-6) -> tuple[np.ndarray, np.ndarray]:
    key = np.round(v / tol).astype(np.int64)
    _, first, inverse = np.unique(key, axis=0, return_index=True, return_inverse=True)
    f2 = inverse.reshape(-1)[f]
    keep = (f2[:, 0] != f2[:, 1]) & (f2[:, 1] != f2[:, 2]) & (f2[:, 0] != f2[:, 2])
    return v[first], f2[keep]


# --- Voxels ------------------------------------------------------------------------------------------------------------

class Grid:
    """A voxel grid over the mesh's bounding box with one empty cell of padding on every side."""

    def __init__(self, lo: np.ndarray, hi: np.ndarray, res: int = GRID):
        ext = np.maximum(hi - lo, 1e-6)
        self.pitch = float(ext.max() / res)
        self.lo = lo - self.pitch  # padding
        self.shape = tuple(int(math.ceil(e / self.pitch)) + 2 for e in ext)
        idx = np.indices(self.shape).reshape(3, -1).T
        self.centers = self.lo + (idx + 0.5) * self.pitch

    def cell(self, p: np.ndarray) -> np.ndarray:
        c = np.floor((p - self.lo) / self.pitch).astype(np.int64)
        return np.clip(c, 0, np.array(self.shape) - 1)

    def mask(self, inside: np.ndarray) -> np.ndarray:
        return inside.reshape(self.shape)


def surface_cells(grid: Grid, v: np.ndarray, f: np.ndarray) -> np.ndarray:
    """Cells the surface passes through: each triangle sampled on a barycentric lattice finer than half a cell."""
    occ = np.zeros(grid.shape, dtype=bool)
    tri = v[f]
    edge = np.max(np.linalg.norm(tri - np.roll(tri, 1, axis=1), axis=2), axis=1)
    steps = np.maximum(1, np.ceil(edge / (grid.pitch * 0.5))).astype(np.int64)
    for n in np.unique(steps):
        sel = tri[steps == n]
        i, j = np.meshgrid(np.arange(n + 1), np.arange(n + 1), indexing="ij")
        ok = (i + j) <= n
        a = (i[ok] / n)[None, :, None]
        b = (j[ok] / n)[None, :, None]
        pts = sel[:, None, 0] * (1 - a - b) + sel[:, None, 1] * a + sel[:, None, 2] * b
        c = grid.cell(pts.reshape(-1, 3))
        occ[c[:, 0], c[:, 1], c[:, 2]] = True
    return occ


def _dilate(m: np.ndarray) -> np.ndarray:
    out = m.copy()
    out[1:] |= m[:-1]
    out[:-1] |= m[1:]
    out[:, 1:] |= m[:, :-1]
    out[:, :-1] |= m[:, 1:]
    if m.ndim == 3:
        out[:, :, 1:] |= m[:, :, :-1]
        out[:, :, :-1] |= m[:, :, 1:]
    return out


def flood(seed: np.ndarray, free: np.ndarray, max_iter: int = 2000) -> np.ndarray:
    """Cells of `free` reachable from `seed` through face neighbours."""
    reach = seed & free
    for _ in range(max_iter):
        nxt = _dilate(reach) & free
        if nxt.sum() == reach.sum():
            return nxt
        reach = nxt
    return reach


def solid_cells(surf: np.ndarray) -> np.ndarray:
    border = np.zeros_like(surf)
    border[0] = border[-1] = True
    border[:, 0] = border[:, -1] = True
    border[:, :, 0] = border[:, :, -1] = True
    return ~flood(border, ~surf)


def fibonacci_dirs(n: int) -> np.ndarray:
    i = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * i / n)
    theta = math.pi * (1 + 5 ** 0.5) * i
    return np.stack([np.cos(theta) * np.sin(phi), np.sin(theta) * np.sin(phi), np.cos(phi)], axis=1)


def kdop(v: np.ndarray, n: int = 128) -> tuple[np.ndarray, np.ndarray]:
    """Planes (normals, offsets) of the k-DOP around the points: the convex hull, slightly enlarged."""
    d = np.concatenate([np.eye(3), -np.eye(3), fibonacci_dirs(n)])
    return d, (v @ d.T).max(axis=0)


def inside_planes(p: np.ndarray, normals: np.ndarray, offsets: np.ndarray, eps: float = 1e-9) -> np.ndarray:
    return np.all(p @ normals.T <= offsets + eps, axis=1)


def convex_planes(v: np.ndarray, f: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Outward planes of a convex triangle mesh (orientation fixed against its centroid)."""
    tri = v[f]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    ln = np.linalg.norm(n, axis=1)
    ok = ln > 1e-12
    n, tri = n[ok] / ln[ok, None], tri[ok]
    c = v.mean(axis=0)
    off = np.einsum("ij,ij->i", n, tri[:, 0])
    flip = (n @ c) > off
    n[flip] *= -1
    off[flip] *= -1
    return n, off


# --- Openings ----------------------------------------------------------------------------------------------------------

def _components(mask: np.ndarray) -> list[np.ndarray]:
    """4-connected components of a 2D mask, as boolean masks."""
    seen = np.zeros_like(mask)
    out = []
    for start in zip(*np.nonzero(mask)):
        if seen[start]:
            continue
        seed = np.zeros_like(mask)
        seed[start] = True
        comp = flood(seed, mask)
        seen |= comp
        out.append(comp)
    return out


def openings(surf: np.ndarray, min_cells: int = 4, pitch: float | None = None,
             min_size_m: float = MIN_OPENING_M) -> tuple[list[tuple[int, np.ndarray]], tuple[list[int], list[int]]]:
    """[(axis, 2D mask)] for each opening seen along X (side to side) and Y (front to back); each 2D mask is over
    (the other horizontal axis, Z) of the surface's occupied range. See the module docstring.

    With `pitch` (metres per cell) a gap must be at least `min_size_m` wide and tall to count, so the spaces between
    wheel spokes or chair slats on a small prop are not reported as doorways (found on the first live cart)."""
    occ = np.nonzero(surf)
    lo = [int(a.min()) for a in occ]
    hi = [int(a.max()) + 1 for a in occ]
    crop = surf[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]]
    out = []
    for axis in (0, 1):
        miss = ~crop.any(axis=axis)  # 2D: (other horizontal, z)
        seed = np.zeros_like(miss)
        seed[0, :] = seed[-1, :] = True   # left and right
        seed[:, -1] = True                # top; the bottom (z = 0) is not open to the outside
        outside = flood(seed, miss)
        holes = miss & ~outside
        for comp in _components(holes):
            if comp.sum() < min_cells:
                continue
            if pitch:
                rows, cols = np.nonzero(comp)
                wide = (rows.max() - rows.min() + 1) * pitch
                tall = (cols.max() - cols.min() + 1) * pitch
                if wide < min_size_m or tall < min_size_m:
                    continue
            out.append((axis, comp))
    return out, (lo, hi)


def openings_blocked(surf: np.ndarray, collider: np.ndarray | None, found=None) -> dict[str, Any]:
    """How much of the openings a collider (3D cell mask, or None for the exact shape) fills.
    -> {openings, opening_cells, blocked_cells, blocked_fraction, blocked_openings, ok}."""
    holes, (lo, hi) = found if found is not None else openings(surf)
    total = sum(int(m.sum()) for _, m in holes)
    if collider is None or not holes:
        return {"openings": len(holes), "opening_cells": total, "blocked_cells": 0, "blocked_fraction": 0.0,
                "blocked_openings": 0, "ok": True}
    crop = collider[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]]
    proj = {0: crop.any(axis=0), 1: crop.any(axis=1)}
    blocked, bad = 0, 0
    for axis, m in holes:
        b = int((m & proj[axis]).sum())
        blocked += b
        if b > OPENING_BLOCKED_MAX * m.sum():
            bad += 1
    frac = blocked / total if total else 0.0
    return {"openings": len(holes), "opening_cells": total, "blocked_cells": blocked, "blocked_fraction": round(float(frac), 4),
            "blocked_openings": max(1, bad) if frac > OPENING_BLOCKED_MAX else 0, "ok": bool(frac <= OPENING_BLOCKED_MAX)}


# --- Primitives --------------------------------------------------------------------------------------------------------

def fit_primitives(v: np.ndarray, grid: Grid, hull_mask: np.ndarray) -> list[dict[str, Any]]:
    """Box, upright cylinder, capsule and sphere around the points, in that order, each with volume (m3), its volume
    over the hull's (`size`) and the part of the hull it covers (`cover`), measured on the grid."""
    lo, hi = v.min(axis=0), v.max(axis=0)
    c = (lo + hi) / 2
    ext = hi - lo
    cell_v = grid.pitch ** 3
    hull_n = max(1, int(hull_mask.sum()))
    hull_vol = hull_n * cell_v
    pts = grid.centers
    hm = hull_mask.reshape(-1)
    out = []

    def rec(kind, inside, vol, **shape):
        cover = float((inside & hm).sum()) / hull_n
        out.append({"kind": kind, "volume": float(vol), "size": float(vol / hull_vol), "cover": cover, **shape})

    rec("box", np.all((pts >= lo) & (pts <= hi), axis=1), float(np.prod(ext)), center=c.tolist(), size_m=ext.tolist())
    r = float(np.max(np.linalg.norm(v[:, :2] - c[:2], axis=1)))
    inside = (np.linalg.norm(pts[:, :2] - c[:2], axis=1) <= r) & (pts[:, 2] >= lo[2]) & (pts[:, 2] <= hi[2])
    rec("cylinder", inside, math.pi * r * r * ext[2], center=c.tolist(), radius=r, height=float(ext[2]))
    ax = int(np.argmax(ext))
    others = [i for i in range(3) if i != ax]
    rad = float(np.max(np.linalg.norm(v[:, others] - c[others], axis=1)))
    half = max(0.0, ext[ax] / 2 - rad)
    a = c.copy(); a[ax] -= half
    b = c.copy(); b[ax] += half
    t = np.clip((pts[:, ax] - a[ax]) / max(1e-9, 2 * half), 0, 1) if half > 0 else np.zeros(len(pts))
    nearest = a + (b - a) * t[:, None]
    inside = np.linalg.norm(pts - nearest, axis=1) <= rad
    rec("capsule", inside, math.pi * rad * rad * 2 * half + 4 / 3 * math.pi * rad ** 3, center=c.tolist(), radius=rad,
        height=float(2 * half + 2 * rad), axis=ax)
    rs = float(np.max(np.linalg.norm(v - c, axis=1)))
    rec("sphere", np.linalg.norm(pts - c, axis=1) <= rs, 4 / 3 * math.pi * rs ** 3, center=c.tolist(), radius=rs)
    for p in out:
        p["accepted"] = bool(p["cover"] >= PRIMITIVE_COVER and p["size"] <= PRIMITIVE_MAX_VOLUME)
    return out


def primitive_mesh(p: dict[str, Any], segments: int = 16) -> tuple[np.ndarray, np.ndarray]:
    """A small closed mesh for a primitive (box 12, cylinder 60, capsule and sphere under 250 triangles)."""
    c = np.asarray(p["center"], dtype=np.float64)
    kind = p["kind"]
    if kind == "box":
        e = np.asarray(p["size_m"]) / 2
        corners = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], dtype=np.float64) * e + c
        faces = np.array([[0, 2, 3], [0, 3, 1], [4, 5, 7], [4, 7, 6], [0, 1, 5], [0, 5, 4], [2, 6, 7], [2, 7, 3],
                          [0, 4, 6], [0, 6, 2], [1, 3, 7], [1, 7, 5]])
        return corners, faces
    if kind == "cylinder":
        r, h = p["radius"], p["height"]
        ang = np.linspace(0, 2 * math.pi, segments, endpoint=False)
        ring = np.stack([np.cos(ang) * r, np.sin(ang) * r], axis=1)
        v = np.concatenate([np.c_[ring, np.full(segments, -h / 2)], np.c_[ring, np.full(segments, h / 2)],
                            [[0, 0, -h / 2], [0, 0, h / 2]]]) + c
        f = []
        for i in range(segments):
            j = (i + 1) % segments
            f += [[i, j, segments + j], [i, segments + j, segments + i], [2 * segments, j, i], [2 * segments + 1, segments + i, segments + j]]
        return v, np.array(f)
    # capsule and sphere: a latitude-longitude sphere, the capsule's halves pulled apart along its axis
    r = p["radius"]
    half = (p.get("height", 2 * r) / 2 - r) if kind == "capsule" else 0.0
    ax = p.get("axis", 2) if kind == "capsule" else 2
    seg, rings = 12, 8
    verts = [[0, 0, -1]]
    for k in range(1, rings):
        t = math.pi * k / rings - math.pi / 2
        for i in range(seg):
            a = 2 * math.pi * i / seg
            verts.append([math.cos(a) * math.cos(t), math.sin(a) * math.cos(t), math.sin(t)])
    verts.append([0, 0, 1])
    v = np.asarray(verts, dtype=np.float64) * r
    v[:, 2] += np.where(v[:, 2] > 1e-9, half, np.where(v[:, 2] < -1e-9, -half, 0.0))
    f = []
    for i in range(seg):
        f.append([0, 1 + (i + 1) % seg, 1 + i])
    for k in range(rings - 2):
        a0, b0 = 1 + k * seg, 1 + (k + 1) * seg
        for i in range(seg):
            j = (i + 1) % seg
            f += [[a0 + i, a0 + j, b0 + j], [a0 + i, b0 + j, b0 + i]]
    top, last = len(v) - 1, 1 + (rings - 2) * seg
    for i in range(seg):
        f.append([last + i, last + (i + 1) % seg, top])
    if ax != 2:  # turn Z onto the capsule's axis
        order = {0: [2, 1, 0], 1: [0, 2, 1]}[ax]
        v = v[:, order]
    return v + c, np.array(f)


def trunk_capsule(v: np.ndarray) -> dict[str, Any]:
    """Foliage (plan 3.4 A): an upright capsule around the bottom 40 percent of the height."""
    zmin, zmax = float(v[:, 2].min()), float(v[:, 2].max())
    top = zmin + 0.4 * (zmax - zmin)
    low = v[v[:, 2] <= top]
    if len(low) < 4:
        low = v
    xy = np.median(low[:, :2], axis=0)
    # Low vertices nearest the ground are the trunk; the 75th percentile ignores low leaves.
    base = low[low[:, 2] <= zmin + 0.15 * (zmax - zmin)]
    base = base if len(base) >= 4 else low
    r = float(np.percentile(np.linalg.norm(base[:, :2] - xy, axis=1), 75))
    r = max(r, 0.02 * (zmax - zmin))
    h = max(top - zmin, 2 * r)
    return {"kind": "capsule", "center": [float(xy[0]), float(xy[1]), zmin + h / 2], "radius": r, "height": h, "axis": 2}


# --- CoACD ---------------------------------------------------------------------------------------------------------------

# CoACD settings: threshold 0.08 (concavity allowed per part) and a lighter tree search than CoACD's defaults; on the
# 5,000-triangle test chest this takes about a quarter of the default's 72 s for the same number of parts.
COACD_SETTINGS = {"threshold": 0.08, "mcts_iterations": 60, "mcts_nodes": 10, "mcts_max_depth": 3, "resolution": 1000,
                  "preprocess_resolution": 40}


def decompose(v: np.ndarray, f: np.ndarray, max_hulls: int, seed: int = 0, **settings: Any) -> list[tuple[np.ndarray, np.ndarray]]:
    """CoACD convex parts, at most `max_hulls`, each decimated to HULL_VERTEX_CAP points."""
    import coacd

    try:
        coacd.set_log_level("error")
    except Exception:  # noqa: BLE001
        pass
    mesh = coacd.Mesh(np.ascontiguousarray(v, dtype=np.float64), np.ascontiguousarray(f, dtype=np.int32))
    kw = {**COACD_SETTINGS, **settings}
    parts = coacd.run_coacd(mesh, max_convex_hull=max_hulls, preprocess_mode="auto", decimate=True,
                            max_ch_vertex=HULL_VERTEX_CAP, merge=True, seed=seed, **kw)
    return [(np.asarray(pv, dtype=np.float64), np.asarray(pf, dtype=np.int64)) for pv, pf in parts]


# --- The whole step ------------------------------------------------------------------------------------------------------

def analyse(v: np.ndarray, f: np.ndarray, res: int = GRID) -> dict[str, Any]:
    """Grid, surface, solid and hull masks, hollowness, openings and whether one hull fills them."""
    lo, hi = v.min(axis=0), v.max(axis=0)
    grid = Grid(lo, hi, res)
    surf = surface_cells(grid, v, f)
    solid = solid_cells(surf)
    normals, offsets = kdop(v)
    hull = grid.mask(inside_planes(grid.centers, normals, offsets))
    found = openings(surf, pitch=grid.pitch)
    return {"grid": grid, "surf": surf, "solid": solid, "hull": hull, "found": found,
            "solid_ratio": min(1.0, float((solid & hull).sum()) / max(1, int(hull.sum()))),
            "hull_openings": openings_blocked(surf, hull, found)}


def choose(category: str | None, mode: str, a: dict[str, Any] | None) -> str:
    """What `mode` means for this category: hull, decompose, mesh, primitive, trunk or none."""
    if mode != "auto":
        return mode
    if category in RIGGED:
        return "hull"
    if category == "environment":
        return "mesh"
    if category == "vehicle":
        return "decompose"
    if category == "foliage":
        return "trunk"
    if category in ("prop_rigid", "prop_articulated"):
        if a is None:
            return "hull"
        if a["solid_ratio"] < HOLLOW_RATIO or not a["hull_openings"]["ok"]:
            return "decompose"
        return "primitive"
    return "hull"


def write_obj(path: Path | str, parts: list[dict[str, Any]], bbox: tuple[np.ndarray, np.ndarray]) -> None:
    """One `o <name>` block per part; the header carries the source bounding box pack.py maps onto LOD0."""
    lo, hi = bbox
    lines = ["# vgac colliders, Blender frame (Z up), metres",
             "# vgac_bbox " + " ".join(f"{x:.6f}" for x in list(lo) + list(hi))]
    base = 1
    for p in parts:
        lines.append(f"o {p['name']}")
        lines.append(f"# vgac_kind {p['kind']}")
        lines += [f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in p["v"]]
        lines += [f"f {a + base} {b + base} {c + base}" for a, b, c in p["f"]]
        base += len(p["v"])
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _mask_of(grid: Grid, parts: list[dict[str, Any]]) -> np.ndarray:
    inside = np.zeros(len(grid.centers), dtype=bool)
    for p in parts:
        n, o = convex_planes(p["v"], p["f"])
        inside |= inside_planes(grid.centers, n, o, eps=grid.pitch * 0.25)
    return grid.mask(inside)


def build(glb: Path | str, out_dir: Path | str, name: str, category: str | None, mode: Any, platform: str | None,
          max_parts_override: Any = None, run_coacd: bool = True) -> dict[str, Any]:
    """Decide and build the collision shapes for one asset. Writes <out_dir>/collider_parts.obj when the shapes are
    made here (primitive, trunk, decompose) and returns the plan for pack.py and the checks:
    {mode, chosen, kind, cap, parts: [{name, kind, triangles, vertices}], obj, analysis, openings, seconds, primitive}.
    `chosen` is hull, mesh or none when pack.py builds the shape (or nothing) itself."""
    t0 = time.perf_counter()
    mode = normalise_mode(mode)
    cap = max_parts(category, platform, max_parts_override)
    out: dict[str, Any] = {"mode": mode, "cap": cap, "category": category, "platform": platform, "parts": [], "obj": None}
    # The measurements decide auto for props and feed the openings check; other cases only need the triangles.
    need = mode != "none" and (category in OPENINGS_CATEGORIES or (mode == "auto" and category == "prop_articulated"))
    a = None
    v = f = None
    if need:
        v, f = load_glb_zup(glb)
        a = analyse(v, f)
        out["analysis"] = {"solid_ratio": round(a["solid_ratio"], 4), "openings": a["hull_openings"]["openings"],
                           "one_hull_blocks": round(a["hull_openings"]["blocked_fraction"], 4), "grid_cell_m": round(a["grid"].pitch, 5),
                           "triangles_in": int(len(f))}
    chosen = choose(category, mode, a)
    if chosen in ("decompose", "primitive", "trunk") and v is None:
        v, f = load_glb_zup(glb)
    parts: list[dict[str, Any]] = []
    if chosen == "primitive":
        prims = fit_primitives(v, a["grid"], a["hull"])
        out["primitives"] = [{k: (round(x, 4) if isinstance(x, float) else x) for k, x in p.items() if k in ("kind", "size", "cover", "accepted")} for p in prims]
        pick = next((p for p in prims if p["accepted"]), None)
        if pick is None:
            chosen = "hull"
        else:
            pv, pf = primitive_mesh(pick)
            parts.append({"kind": pick["kind"], "v": pv, "f": pf, "shape": {k: pick[k] for k in ("center", "radius", "height", "size_m", "axis") if k in pick}})
    elif chosen == "trunk":
        cap_p = trunk_capsule(v)
        pv, pf = primitive_mesh(cap_p)
        parts.append({"kind": "capsule", "v": pv, "f": pf, "shape": {k: x for k, x in cap_p.items() if k != "kind"}})
    elif chosen == "decompose":
        if not run_coacd:
            chosen = "hull"
        else:
            t1 = time.perf_counter()
            hulls = decompose(v, f, cap)
            out["coacd_seconds"] = round(time.perf_counter() - t1, 2)
            for hv, hf in hulls:
                parts.append({"kind": "hull", "v": hv, "f": hf})
            if not parts:
                chosen = "hull"
    out["chosen"] = chosen
    if parts:
        for i, p in enumerate(parts):
            p["name"] = f"{name}_collider_{i:02d}"
        obj = Path(out_dir) / "collider_parts.obj"
        write_obj(obj, parts, (v.min(axis=0), v.max(axis=0)))
        out["obj"] = str(obj)
        out["parts"] = [{"name": p["name"], "kind": p["kind"], "triangles": int(len(p["f"])), "vertices": int(len(p["v"])),
                         **({"shape": {k: (np.asarray(x).round(5).tolist() if not isinstance(x, (int, float)) else round(float(x), 5))
                                       for k, x in p["shape"].items()}} if "shape" in p else {})} for p in parts]
    # Openings with the shapes the asset actually gets.
    if a is not None and category in OPENINGS_CATEGORIES and chosen != "none":
        if chosen == "mesh":
            out["openings"] = openings_blocked(a["surf"], None, a["found"])
        elif chosen == "hull":
            out["openings"] = a["hull_openings"]
        else:
            out["openings"] = openings_blocked(a["surf"], _mask_of(a["grid"], parts), a["found"])
    out["seconds"] = round(time.perf_counter() - t0, 2)
    return out
