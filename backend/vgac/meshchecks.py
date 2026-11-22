"""Shape and texture-layout measures that nothing judged before (WP-92): slivers, texel-density spread, flipped shells.

Pure numpy on a triangle mesh (vertices, faces, per-vertex UVs), so a planted-defect test can build a tiny mesh and the
same measures run on a finished GLB. Every check built from these is a warning: nothing blocks shipping (docs WP-92).
`measure_glb` reads the finished clean-up GLB; it never raises (a mesh it cannot read gives no measures, and the check
is then not made, rather than passing).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from .imaging import face_islands

# A triangle whose smallest corner is under this many degrees is a sliver. degenerate_faces (area under 1e-12) stays as it was.
SLIVER_ANGLE_DEG = 5.0
# Islands smaller than this share of the surface are left out of the texel-density spread (a button's few texels is not a spread).
DENSITY_MIN_AREA_SHARE = 0.002
# A shell is judged inside out when its faces point inward by this much (area-weighted mean of the outward component).
OPEN_SHELL_INWARD = -0.5
# Shells with fewer faces than this are not judged open (too few to tell). A shell is mixed when faces walked the other way
# make up more than this share of its area.
OPEN_SHELL_MIN_FACES = 40
MIXED_SHARE = 0.05


def _tri(verts: np.ndarray, faces: np.ndarray):
    return verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]


def min_angles_deg(verts: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """The smallest corner of each triangle, in degrees (0 for a triangle with a zero-length side)."""
    a, b, c = _tri(np.asarray(verts, dtype=np.float64), np.asarray(faces))
    out = np.full(len(faces), 180.0)
    for p, q, r in ((a, b, c), (b, c, a), (c, a, b)):
        u, v = q - p, r - p
        nu, nv = np.linalg.norm(u, axis=1), np.linalg.norm(v, axis=1)
        den = nu * nv
        cos = np.where(den > 0, np.einsum("ij,ij->i", u, v) / np.where(den > 0, den, 1.0), 1.0)
        out = np.minimum(out, np.degrees(np.arccos(np.clip(cos, -1.0, 1.0))))
    return out


def sliver_share(verts: np.ndarray, faces: np.ndarray, angle_deg: float = SLIVER_ANGLE_DEG) -> dict[str, Any]:
    """{share, count, faces}: the share of triangles with a corner under angle_deg."""
    n = len(faces)
    if n == 0:
        return {"share": 0.0, "count": 0, "faces": 0}
    cnt = int((min_angles_deg(verts, faces) < angle_deg).sum())
    return {"share": cnt / n, "count": cnt, "faces": n}


def _weld_ids(rows: np.ndarray, scale: float) -> np.ndarray:
    key = np.round(np.asarray(rows, dtype=np.float64) * scale).astype(np.int64)
    _, ids = np.unique(key, axis=0, return_inverse=True)
    return ids.reshape(-1)


def texel_density_spread(verts: np.ndarray, faces: np.ndarray, uv: np.ndarray) -> dict[str, Any] | None:
    """How unevenly the texture is spread over the surface: the texel density (UV length per metre, from the square root of
    UV area over surface area) of each UV island, then p95 over p5 weighted by surface area. 1.0 is perfectly even; None
    when there are no islands big enough to measure. Islands are faces joined through shared UV positions."""
    verts, uv = np.asarray(verts, dtype=np.float64), np.asarray(uv, dtype=np.float64)
    if len(faces) == 0:
        return None
    a, b, c = _tri(verts, faces)
    area3 = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    ua, ub, uc = uv[faces[:, 0]], uv[faces[:, 1]], uv[faces[:, 2]]
    area2 = 0.5 * np.abs((ub[:, 0] - ua[:, 0]) * (uc[:, 1] - ua[:, 1]) - (uc[:, 0] - ua[:, 0]) * (ub[:, 1] - ua[:, 1]))
    uvid = _weld_ids(uv, 2**20)
    isl = face_islands(uvid[faces], int(uvid.max()) + 1)
    k = int(isl.max()) + 1
    s3 = np.bincount(isl, weights=area3, minlength=k)
    s2 = np.bincount(isl, weights=area2, minlength=k)
    total = float(s3.sum())
    if total <= 0:
        return None
    keep = (s3 >= DENSITY_MIN_AREA_SHARE * total) & (s2 > 0)
    if keep.sum() < 2:
        return None
    dens = np.sqrt(s2[keep] / s3[keep])
    w = s3[keep]
    order = np.argsort(dens)
    cum = np.cumsum(w[order]) / w.sum()
    p5 = float(dens[order][np.searchsorted(cum, 0.05)])
    p95 = float(dens[order][min(np.searchsorted(cum, 0.95), len(order) - 1)])
    return {"spread": p95 / p5 if p5 > 0 else float("inf"), "islands": int(keep.sum()), "p5": p5, "p95": p95}


def shells(verts: np.ndarray, faces: np.ndarray) -> dict[str, Any]:
    """Each connected piece of surface (faces joined through vertices at the same position, so UV and hard-edge splits do not
    separate them): {shell (per face), pos (per vertex welded id), edges info}."""
    verts = np.asarray(verts, dtype=np.float64)
    pos = _weld_ids(verts, 1e5)
    pf = pos[faces]
    return {"pos": pos, "pf": pf, "shell": face_islands(pf, int(pos.max()) + 1)}


def flipped_shells(verts: np.ndarray, faces: np.ndarray) -> dict[str, Any]:
    """Shells that look inside out, three ways, none needing a reference:
    - closed: a shell with no open edge whose signed volume is negative;
    - conflict: a shell whose faces split into patches walked opposite ways, the smaller ones making up more than MIXED_SHARE
      of its area (part of the shell was turned over, which is what a mistaken consistent-normals pass leaves on a leaf);
    - open: a shell of OPEN_SHELL_MIN_FACES or more faces whose normals, on average by area, point toward the middle of
      the whole model (a hair card or a piece of cloth with its front inward).
    Returns {shells, flipped, flipped_share (of surface area), kinds: {closed, conflict, open}}."""
    verts = np.asarray(verts, dtype=np.float64)
    faces = np.asarray(faces)
    n = len(faces)
    if n == 0:
        return {"shells": 0, "flipped": 0, "flipped_share": 0.0, "kinds": {"closed": 0, "conflict": 0, "open": 0}}
    sh = shells(verts, faces)
    shell, pf = sh["shell"], sh["pf"]
    k = int(shell.max()) + 1
    a, b, c = _tri(verts, faces)
    cr = np.cross(b - a, c - a)
    area = 0.5 * np.linalg.norm(cr, axis=1)
    cen = (a + b + c) / 3.0
    # directed edges per face, then undirected counts
    ea = np.concatenate([pf[:, 0], pf[:, 1], pf[:, 2]])
    eb = np.concatenate([pf[:, 1], pf[:, 2], pf[:, 0]])
    ef = np.tile(np.arange(n), 3)
    lo, hi = np.minimum(ea, eb), np.maximum(ea, eb)
    ids, inv, cnt = np.unique(np.stack([lo, hi], axis=1), axis=0, return_inverse=True, return_counts=True)
    inv = inv.reshape(-1)
    edge_shell = shell[ef]
    open_edges = np.bincount(edge_shell, weights=(cnt[inv] == 1).astype(float), minlength=k)
    # an edge shared by two faces walked the same way: both directed edges have the same (ea < eb) sign
    fwd = (ea < eb).astype(np.int64)
    sum_fwd = np.bincount(inv, weights=fwd, minlength=len(ids))
    pair = cnt == 2
    conflict_edge = pair & ((sum_fwd == 0) | (sum_fwd == 2))
    # every shared edge is seen from both of its faces, so the sums below count each twice
    # patches: faces joined across edges that two faces walk in opposite directions (a consistently wound surface)
    order = np.argsort(inv, kind="stable")
    first = np.searchsorted(inv[order], np.arange(len(ids)))
    pidx = np.where(pair & ~conflict_edge)[0]
    f1, f2 = ef[order][first[pidx]], ef[order][first[pidx] + 1]
    parent = np.arange(n)

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return int(x)

    for u, w in zip(f1.tolist(), f2.tolist()):
        ru, rw = find(u), find(w)
        if ru != rw:
            parent[rw] = ru
    patch = np.array([find(i) for i in range(n)])
    vol = np.bincount(shell, weights=np.einsum("ij,ij->i", a, np.cross(b, c)) / 6.0, minlength=k)
    faces_in = np.bincount(shell, minlength=k)
    area_in = np.bincount(shell, weights=area, minlength=k)
    _, pinv = np.unique(patch, return_inverse=True)
    pinv = pinv.reshape(-1)
    patch_area = np.bincount(pinv, weights=area)
    patch_shell = np.zeros(len(patch_area), dtype=np.int64)
    patch_shell[pinv] = shell
    biggest = np.zeros(k)
    np.maximum.at(biggest, patch_shell, patch_area)
    mixed = (area_in - biggest) > MIXED_SHARE * np.maximum(area_in, 1e-12)
    whole = (cen * area[:, None]).sum(axis=0) / max(float(area.sum()), 1e-12)
    out_dir = cen - whole
    nrm = np.linalg.norm(out_dir, axis=1)
    nn = np.linalg.norm(cr, axis=1)
    outward = np.where((nrm > 0) & (nn > 0), np.einsum("ij,ij->i", cr, out_dir) / np.where(nrm * nn > 0, nrm * nn, 1.0), 0.0)
    mean_out = np.bincount(shell, weights=outward * area, minlength=k) / np.maximum(area_in, 1e-12)
    closed = (open_edges == 0)
    kinds = {
        "closed": closed & (vol < 0) & ~mixed,
        "conflict": mixed,
        "open": (~closed) & (faces_in >= OPEN_SHELL_MIN_FACES) & (mean_out < OPEN_SHELL_INWARD) & ~mixed,
    }
    flipped = kinds["closed"] | kinds["conflict"] | kinds["open"]
    total = max(float(area.sum()), 1e-12)
    return {"shells": k, "flipped": int(flipped.sum()), "flipped_share": float(area_in[flipped].sum() / total),
            "kinds": {n_: int(v.sum()) for n_, v in kinds.items()}}


def measure_glb(glb: Path | str) -> dict[str, Any] | None:
    """{slivers, texel_density, flipped_shells} measured on a finished GLB; None when it cannot be read. Never raises."""
    try:
        import trimesh

        mesh = trimesh.load(str(glb), force="mesh", process=False)
        verts = np.asarray(mesh.vertices, dtype=np.float64)
        faces = np.asarray(mesh.faces, dtype=np.int64)
        if len(faces) == 0:
            return None
        out: dict[str, Any] = {"slivers": sliver_share(verts, faces), "flipped_shells": flipped_shells(verts, faces)}
        uv = getattr(getattr(mesh, "visual", None), "uv", None)
        if uv is not None and len(uv) == len(verts):
            out["texel_density"] = texel_density_spread(verts, faces, np.asarray(uv, dtype=np.float64))
        return out
    except Exception:  # noqa: BLE001  (a metric, never a reason to fail a stage)
        return None
