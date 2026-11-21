"""Blender headless: moving parts on an object with no skeleton (WP-29): lids, doors, wheels, drawers.

Everything above main() is plain numpy so the backend tests can run it without Blender.

Finding a piece. The 3D build welds a lid at its hinge and a wheel into its arch, so a part is never its own island:
it has to be found on one closed surface. The person's points (up to 8 per part, in the rig GLB's glTF frame) are
the part's seeds. Two fronts spread over the welded surface at once: one from the seeds, one from the far side of the
object (the vertices furthest from the seeds over the surface, FAR_SHARE of them). Each step costs its length, plus a
toll for stepping onto a vertex in a crease (a concave edge, where the next face lies behind this one): CREASE_TOLL
times the asset's size times the crease's sharpness squared (its angle over a right angle, at most 1). Each vertex
goes to the front that reaches it cheaper, so the two meet in the crease that separates the part from the body (the
hinge strip of a lid, the arch round a tyre), and a groove inside the part (a band on a lid, a recessed hub) costs
the far front even more to reach. Seeds that land in separate pieces give separate pieces (four wheels). One point
on a chest's lid finds it (measured in bakeoff/wp29/trial, crease tolls 2 to 20 all pass).

A turning part (spin) is found by its shape instead (spin_piece): every normal of a surface of revolution meets its
axis, so the axis comes from the normals near the seed, and the piece is the cylinder the tread spans round it.
"""
import heapq
import json
import os
import sys

import numpy as np

CREASE_TOLL = 5.0  # times the asset's size, for a right-angle crease (2 to 20 passed the trial: bakeoff/wp29/trial)
FAR_SHARE = 0.3  # share of the surface, furthest from the seeds, that the far front starts from
WELD = 1e-5  # metres: seam copies closer than this are one surface point
MIN_PIECE = 12  # a found piece with fewer welded points is dropped
# Turning parts (spin_piece).
BALLS = (0.03, 0.06, 0.1, 0.15, 0.22, 0.3)  # first guesses: the surface within these shares of the asset's size from the seed
MISFIT = 3.0  # a piece point whose normal misses the axis counts this many times against the cylinder
MERIDIAN_SIN = 0.25  # a normal meets the axis when it is within about 15 degrees of the plane through the axis
TREAD_DOT = 0.8  # a tread point faces out from the axis within about 37 degrees
LEVEL_SIN = 0.17  # an axis within about 10 degrees of level
WIDTH = 1.2  # a wheel is at most this share of its radius wide, from the seed
SIDE = 0.09  # this share of the radius beyond the tread, each side (0.08 to 0.1 measured: bakeoff/wp29/trial)
BAND = 0.08  # the tread lies within this share of the radius
RIM = 0.03  # the cylinder reaches this share past the radius


def weld(co, tol=WELD):
    """-> (index of each vertex's welded point, welded positions). Seam copies (UV and normal splits) share a point."""
    co = np.asarray(co, dtype=float).reshape(-1, 3)
    keys = np.round(co / tol).astype(np.int64)
    _, first, inverse = np.unique(keys, axis=0, return_index=True, return_inverse=True)
    return inverse.reshape(-1), co[first]


def surface_graph(wco, wfaces):
    """Welded edges, their lengths and each welded point's crease sharpness (0 flat or convex, 1 a right angle or
    sharper). wfaces: (F, 3) welded indices; faces that collapsed in the weld are skipped."""
    f = np.asarray(wfaces, dtype=int)
    f = f[(f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])]
    a, b, c = wco[f[:, 0]], wco[f[:, 1]], wco[f[:, 2]]
    fn = np.cross(b - a, c - a)
    fn = fn / np.maximum(np.linalg.norm(fn, axis=1), 1e-12)[:, None]
    fc = (a + b + c) / 3.0
    e = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    fid = np.tile(np.arange(len(f)), 3)
    e = np.sort(e, axis=1)
    order = np.lexsort((e[:, 1], e[:, 0]))
    e, fid = e[order], fid[order]
    same = np.all(e[1:] == e[:-1], axis=1)
    sharp = np.zeros(len(wco))
    # an edge shared by exactly two faces: concave when either face's centre lies in front of the other face
    pair = np.where(same)[0]
    f1, f2 = fid[pair], fid[pair + 1]
    concave = (np.einsum("ij,ij->i", fn[f1], fc[f2] - fc[f1]) > 1e-9) | (np.einsum("ij,ij->i", fn[f2], fc[f1] - fc[f2]) > 1e-9)
    ang = np.arccos(np.clip(np.einsum("ij,ij->i", fn[f1], fn[f2]), -1.0, 1.0))
    s = np.where(concave, np.minimum(ang / (np.pi / 2), 1.0), 0.0)
    for k in (0, 1):
        np.maximum.at(sharp, e[pair, k], s)
    edges = np.unique(e, axis=0)
    length = np.linalg.norm(wco[edges[:, 0]] - wco[edges[:, 1]], axis=1)
    return edges, length, sharp


def _adjacency(n, edges, cost):
    adj = [[] for _ in range(n)]
    for (u, v), w in zip(edges.tolist(), cost.tolist()):
        adj[u].append((v, w))
        adj[v].append((u, w))
    return adj


def spread(adj, sources, toll=None):
    """Cheapest cost from any source to every point (Dijkstra). toll: extra cost for stepping onto each point."""
    n = len(adj)
    dist = np.full(n, np.inf)
    heap = []
    for s in sources:
        if dist[s] > 0:
            dist[s] = 0.0
            heap.append((0.0, int(s)))
    heapq.heapify(heap)
    while heap:
        d, u = heapq.heappop(heap)
        if d > dist[u]:
            continue
        for v, w in adj[u]:
            nd = d + w + (toll[v] if toll is not None else 0.0)
            if nd < dist[v]:
                dist[v] = nd
                heapq.heappush(heap, (nd, v))
    return dist


def find_pieces(co, faces, seeds, kind="hinge", size=None, toll=CREASE_TOLL, far_share=FAR_SHARE):
    """The part the seeds sit on, as a bool mask over the given vertices, and a list of pieces (bool masks), one per
    separate region. co: (V, 3); faces: (F, 3); seeds: points in the same frame (nearest surface point each). A spin
    part takes each seed as its own turning piece (spin_piece); hinge and slide parts take all seeds together."""
    co = np.asarray(co, dtype=float).reshape(-1, 3)
    inv, wco = weld(co)
    wf = inv[np.asarray(faces, dtype=int).reshape(-1, 3)]
    edges, length, _ = surface_graph(wco, wf)
    if size is None:
        size = float(np.ptp(wco, axis=0).max())
    adj = _adjacency(len(wco), edges, length)
    src = sorted({int(np.argmin(np.linalg.norm(wco - np.asarray(p, dtype=float), axis=1))) for p in seeds})
    if kind == "spin":
        pieces = []
        for s in src:
            if any(p[s] for p in pieces):
                continue
            pieces.append(spin_piece(wco, wf, edges, s, size, toll, far_share)[0])
    else:
        pieces = _split(_fronts(adj, wco, wf, edges, src, size, toll, far_share), edges, src)
    pieces = [p for p in pieces if p.sum() >= MIN_PIECE]
    wmask = np.zeros(len(wco), dtype=bool)
    for p in pieces:
        wmask |= p
    return wmask[inv], [p[inv] for p in pieces]


def point_normals(wco, wfaces):
    f = np.asarray(wfaces, dtype=int)
    fn = np.cross(wco[f[:, 1]] - wco[f[:, 0]], wco[f[:, 2]] - wco[f[:, 0]])  # area weighted
    vn = np.zeros_like(wco)
    for k in range(3):
        np.add.at(vn, f[:, k], fn)
    return vn / np.maximum(np.linalg.norm(vn, axis=1), 1e-12)[:, None]


def revolution_axis(p, n, rounds=8):
    """The axis of a surface of revolution through points p with normals n: every normal of such a surface meets the
    axis, which is linear in the axis' Plucker coordinates (a, m): (p x n) . a + n . m = 0 (Pottmann and Wallner).
    Reweighted least squares drops points that do not fit (the body round a wheel). -> (direction, a point on it,
    inlier weights)."""
    v = np.hstack([np.cross(p, n), n])
    w = np.ones(len(p))
    for _ in range(rounds):
        M = (v * w[:, None]).T @ v
        evals, evecs = np.linalg.eigh(M)
        x = evecs[:, 0]
        a, m = x[:3], x[3:]
        na = np.linalg.norm(a)
        if na < 1e-9:
            break
        a, m = a / na, m / na
        r = np.abs(v @ np.r_[a, m])  # the distance of each normal line from the axis, roughly, in metres
        s = max(np.median(r) * 3.0, 1e-4)
        w = 1.0 / (1.0 + (r / s) ** 2)
    c = np.cross(a, m)  # the axis point nearest the origin
    return a, c, w


def axis_through(p, n, a, rounds=8):
    """Where an axis of known direction a lies so that the normals meet it: c . (n x a) = n . (a x p) for each point,
    linear in c (solved across a, reweighted like revolution_axis). -> (a point on the axis, inlier weights)."""
    A = np.cross(n, a)
    b = np.einsum("ij,ij->i", n, np.cross(np.broadcast_to(a, p.shape), p))
    w = np.ones(len(p))
    c = p.mean(axis=0)
    for _ in range(rounds):
        sw = np.sqrt(w)[:, None]
        c = np.linalg.lstsq(A * sw, b * sw[:, 0], rcond=None)[0]
        res = np.abs(A @ c - b)
        sc = max(np.median(res) * 3.0, 1e-4)
        w = 1.0 / (1.0 + (res / sc) ** 2)
    c = c - a * (c @ a)
    return c, w


def axis_guesses(p, n):
    """Axis candidates for a patch of a turning part: the full solve (revolution_axis), and, for a patch that is
    mostly tread (where the full solve has no single answer), the direction the normals vary least in, placed by
    axis_through. -> [(direction, point)]."""
    a1, c1, _ = revolution_axis(p, n)
    out = [(a1, c1)]
    evals, evecs = np.linalg.eigh(n.T @ n)
    a2 = evecs[:, 0]
    c2, _ = axis_through(p, n, a2)
    out.append((a2, c2))
    return out


def spin_piece(wco, wfaces, edges, start, size, toll=CREASE_TOLL, far_share=FAR_SHARE, up=2):
    """A turning part (a wheel) from one seed point. -> (welded mask, axis, centre, radius).
    1. First guesses round the seed: the surface fronts from it, and the plain surface near it, each cut to BALLS
       shares of the asset's size.
    2. Each guess gives axes from its normals (axis_guesses) and each axis a cylinder (_cylinder).
    3. The cylinder kept is the one whose piece best fits a turning shape: points whose normals meet the axis count
       for it, the others MISFIT times against it."""
    adj = _adjacency(len(wco), edges, np.linalg.norm(wco[edges[:, 0]] - wco[edges[:, 1]], axis=1))
    first = _fronts(adj, wco, wfaces, edges, [start], size, toll, far_share)
    plain = spread(adj, [start])
    n = point_normals(wco, wfaces)
    best, best_score = (first, None, None, None), -np.inf
    tried = []
    for ball in BALLS:
        for guess in (first & (plain <= ball * size), plain <= ball * size):
            idx = np.where(guess)[0]
            if len(idx) < MIN_PIECE or any(np.array_equal(idx, t) for t in tried):
                continue
            tried.append(idx)
            for a, c in axis_guesses(wco[idx], n[idx]):
                got = _cylinder(wco, n, edges, start, a, c, up)
                if got is None:
                    continue
                piece, meets, centre, R = got
                score = float(meets[piece].sum()) - MISFIT * float((~meets[piece]).sum())
                if score > best_score:
                    best, best_score = (piece, a, centre, R), score
    return best


def _cylinder(wco, n, edges, start, a, c, up):
    """The piece inside the cylinder round axis (a, c) that holds the seed. -> (piece, meets, centre, radius) or None.
    The radius: a wheel with a level axis that stands on the ground (a tread, points facing out from the axis
    (TREAD_DOT) whose normals meet it, within BAND of the axis height above the asset's lowest point) takes the axis
    height; else the outermost tread joined to the seed over surface whose normals meet the axis. The width: the tread's axial range
    (within BAND of the radius), connected to the seed inside the radius and at most WIDTH times the radius from it,
    plus SIDE times the radius each side for the side walls."""
    rel = wco - c
    t = rel @ a
    radial = rel - np.outer(t, a)
    r = np.linalg.norm(radial, axis=1)
    u = radial / np.maximum(r, 1e-12)[:, None]
    meets = np.abs(np.einsum("ij,ij->i", n, np.cross(np.broadcast_to(a, u.shape), u))) < MERIDIAN_SIN
    outward = np.einsum("ij,ij->i", n, u) > TREAD_DOT
    ts = t[start]
    R = None
    if abs(a[up]) < LEVEL_SIN:  # a level axis: a wheel standing on the ground has a tread at the axis height
        h = float((c + a * ts)[up] - wco[:, up].min())
        if h > 0 and (meets & outward & (np.abs(r - h) <= BAND * h) & (np.abs(t - ts) <= WIDTH * h)).sum() >= 3:
            R = h
    if R is None:  # else the outermost tread joined to the seed over surface whose normals meet the axis
        seen = _split(meets | (np.arange(len(r)) == start), edges, [start])[0]
        tread = seen & outward & (np.abs(t - ts) <= WIDTH * max(r[seen].max(), 1e-6))
        if tread.sum() < 3:
            return None
        R = float(np.quantile(r[tread], 0.95))
    near_t = np.abs(t - ts) <= WIDTH * R
    reach = _split((r <= R * (1 + RIM)) & near_t | (np.arange(len(r)) == start), edges, [start])[0]
    band = reach & meets & outward & (np.abs(r - R) <= BAND * R)
    lo, hi = np.quantile(t[band], [0.01, 0.99]) if band.sum() >= 3 else (ts, ts)
    lo, hi = min(lo, ts) - SIDE * R, max(hi, ts) + SIDE * R
    inside = (r <= R * (1 + RIM)) & (t >= lo) & (t <= hi)
    inside[start] = True
    piece = _split(inside, edges, [start])[0]
    centre = c + a * (lo + hi) / 2.0
    return piece, meets, centre, R


def _fronts(adj, wco, wfaces, edges, src, size, toll, far_share):
    sharp = surface_graph(wco, wfaces)[2]
    plain = spread(adj, src)
    reach = np.isfinite(plain)
    cut = np.quantile(plain[reach], 1.0 - far_share)
    far = np.where(reach & (plain >= cut))[0]
    tolls = toll * size * sharp ** 2
    return reach & (spread(adj, src, tolls) < spread(adj, far, tolls))


def _split(mask, edges, seeds):
    """The connected parts of mask (over the welded edges) that hold a seed."""
    n = len(mask)
    parent = np.arange(n)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    keep = mask[edges[:, 0]] & mask[edges[:, 1]]
    for u, v in edges[keep].tolist():
        ru, rv = find(u), find(v)
        if ru != rv:
            parent[ru] = rv
    roots = {}
    for s in seeds:
        if mask[s]:
            roots.setdefault(find(s), None)
    out = []
    lab = np.array([find(i) for i in range(n)])
    for r in roots:
        out.append(mask & (lab == r))
    return out


def main():  # pragma: no cover - runs inside Blender
    raise SystemExit("moving_parts.py: the Blender pass is not written yet")


if __name__ == "__main__":  # pragma: no cover
    main()
