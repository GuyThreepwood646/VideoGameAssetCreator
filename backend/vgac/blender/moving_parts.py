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
ROUND_CELL = 0.05  # round_trim: the wheel's profile (distance from the axis, place along it) in cells this share of the radius
ROUND_BINS = 24  # round_trim: the turn in this many slices
ROUND_SHARE = 0.5  # round_trim: a profile cell with points in fewer than this share of the slices is not round


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


def find_pieces(co, faces, seeds, kind="hinge", size=None, toll=CREASE_TOLL, far_share=FAR_SHARE, info=False):
    """The part the seeds sit on, as a bool mask over the given vertices, and a list of pieces (bool masks), one per
    separate region. co: (V, 3); faces: (F, 3); seeds: points in the same frame (nearest surface point each). A spin
    part takes each seed as its own turning piece (spin_piece); hinge and slide parts take all seeds together.
    info: also return, per piece, {axis, centre, radius} for a turning piece (None for the others)."""
    co = np.asarray(co, dtype=float).reshape(-1, 3)
    inv, wco = weld(co)
    wf = inv[np.asarray(faces, dtype=int).reshape(-1, 3)]
    edges, length, _ = surface_graph(wco, wf)
    if size is None:
        size = float(np.ptp(wco, axis=0).max())
    adj = _adjacency(len(wco), edges, length)
    src = sorted({int(np.argmin(np.linalg.norm(wco - np.asarray(p, dtype=float), axis=1))) for p in seeds})
    found = []
    if kind == "spin":
        for s in src:
            if any(f[0][s] for f in found):
                continue
            piece, a, c, R = spin_piece(wco, wf, edges, s, size, toll, far_share)
            found.append((piece, None if a is None else {"axis": a, "centre": c, "radius": R}))
    else:
        found = [(p, None) for p in _split(_fronts(adj, wco, wf, edges, src, size, toll, far_share), edges, src)]
    found = [f for f in found if f[0].sum() >= MIN_PIECE]
    wmask = np.zeros(len(wco), dtype=bool)
    for p, _ in found:
        wmask |= p
    pieces = [p[inv] for p, _ in found]
    if info:
        return wmask[inv], pieces, [i for _, i in found]
    return wmask[inv], pieces


# --- Pivots (numpy; Blender frame: Z up, the object's front facing -Y) -------------------------------------------------

# The way each hinge side lies from the piece, in Blender's frame. Left and right are a person's facing the front.
SIDE_DIRS = {"back": (0.0, 1.0, 0.0), "front": (0.0, -1.0, 0.0), "left": (-1.0, 0.0, 0.0), "right": (1.0, 0.0, 0.0),
             "top": (0.0, 0.0, 1.0), "bottom": (0.0, 0.0, -1.0)}
DOOR_SIDE, LID_SIDE = "left", "back"  # the hinge side "auto" takes when the weld does not show it
STRIP = 3.0  # a weld ring this many times longer than it is wide is a hinge strip: the hinge lies along it
EDGE_SHARE = 0.12  # the hinge edge: the piece's points within this share of its extent of the chosen side
SQUARE_SIN = 0.26  # a hinge or slide line within about 15 degrees of the object's own axes is taken as on that axis


def gltf_to_blender(p):
    x, y, z = p
    return np.array([x, -z, y], dtype=float)


def blender_to_gltf(p):
    x, y, z = p
    return [float(x), float(z), float(-y)]


def principal(pts):
    """-> (centroid, directions by spread largest first (rows), spreads)."""
    pts = np.asarray(pts, dtype=float).reshape(-1, 3)
    c = pts.mean(axis=0)
    evals, evecs = np.linalg.eigh(np.cov((pts - c).T) if len(pts) > 1 else np.eye(3))
    order = np.argsort(evals)[::-1]
    return c, evecs[:, order].T, np.sqrt(np.maximum(evals[order], 0.0))


def outward(face_normals, face_areas):
    """The piece's facing: its faces' area-weighted mean normal (the front of a drawer, the top of a lid)."""
    n = (np.asarray(face_normals) * np.asarray(face_areas)[:, None]).sum(axis=0)
    return n / max(np.linalg.norm(n), 1e-12)


def hinge_line(ring, piece_pts, facing, side="auto", name=""):
    """Where a hinged piece turns. -> (pivot, axis, how). ring: the points where it was welded to the body; piece_pts:
    its own points; facing: its outward normal (outward()).
    auto: a weld ring that is a strip (STRIP times longer than wide) is the hinge itself (a lid modelled open, welded
    only at its hinge); otherwise the hinge goes on the door side (DOOR_SIDE for a door or a gate, LID_SIDE for
    anything else). A chosen side: the hinge is the piece's edge on that side (EDGE_SHARE of its extent), on its
    body-facing face (against facing), and runs along that edge."""
    ring = np.asarray(ring, dtype=float).reshape(-1, 3)
    pts = np.asarray(piece_pts, dtype=float).reshape(-1, 3)
    facing = np.asarray(facing, dtype=float)
    if side == "auto":
        if len(ring) >= 3:
            c, dirs, spread = principal(ring)
            if spread[1] < 1e-9 or spread[0] / spread[1] >= STRIP:
                return c, square(dirs[0]), "weld strip"
        words = "".join(ch.lower() if ch.isalnum() else " " for ch in name).split()
        side = DOOR_SIDE if any(w in ("door", "doors", "gate") for w in words) else LID_SIDE
    d = np.asarray(SIDE_DIRS[side])
    proj = pts @ d
    edge = pts[proj >= proj.max() - EDGE_SHARE * max(np.ptp(proj), 1e-9)]
    depth = edge @ -facing
    back = edge[depth >= depth.max() - EDGE_SHARE * max(np.ptp(pts @ facing), 1e-9)]
    if len(back) < 2:
        back = edge
    c, dirs, spread = principal(back)
    axis = dirs[0]
    if spread[0] < 1e-9:
        axis = np.cross(d, facing)
        axis /= max(np.linalg.norm(axis), 1e-12)
    # the axis lies across the chosen side: keep only its part square to d
    axis = axis - d * (axis @ d)
    axis /= max(np.linalg.norm(axis), 1e-12)
    return c, square(axis), f"{side} edge"


def square(axis):
    """axis snapped onto the nearest of the object's own axes when it is within SQUARE_SIN of it (models are made
    upright and facing front, so a hinge or a drawer runs along one of them; a few degrees off is the mesh's noise)."""
    a = np.asarray(axis, dtype=float)
    a = a / max(np.linalg.norm(a), 1e-12)
    i = int(np.argmax(np.abs(a)))
    if np.sqrt(max(1.0 - a[i] ** 2, 0.0)) <= SQUARE_SIN:
        out = np.zeros(3)
        out[i] = np.sign(a[i])
        return out
    return a


def roll_axis(axis):
    """A wheel's axis pointed so that turning it the positive way (right hand) rolls the object toward its front
    (-Y): every wheel of a cart then spins the same way. On the ground a wheel rolling toward the front turns about
    up x front = +X; an axis that is not across the object keeps the side of its largest part positive."""
    a = np.asarray(axis, dtype=float)
    a = a / max(np.linalg.norm(a), 1e-12)
    i = int(np.argmax(np.abs(a)))
    return a if a[i] >= 0 else -a


def turn(pts, pivot, axis, deg):
    """pts turned by deg degrees about the line through pivot along axis (right hand)."""
    a = np.asarray(axis, dtype=float)
    a = a / np.linalg.norm(a)
    t = np.radians(deg)
    p = np.asarray(pts, dtype=float) - pivot
    return pivot + p * np.cos(t) + np.cross(a, p) * np.sin(t) + np.outer(p @ a, a) * (1 - np.cos(t))


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
    if best[1] is not None:
        best = (round_trim(wco, edges, best[0], best[1], best[2], best[3], start),) + tuple(best[1:])
    return best


def round_trim(wco, edges, piece, a, c, R, start):
    """A wheel looks the same all the way round its axis; what the cylinder also caught (a bit of the tray the tyre
    touches, the fork's ends at the hub) does not. Each piece point's profile cell (distance from the axis, place along
    it; ROUND_CELL of the radius, with its eight neighbours) counts the slices of the turn (ROUND_BINS) that have
    points in it; points whose cell has fewer than ROUND_SHARE of them are not round. Not-round patches that touch the
    piece's edge go back to the body; not-round patches inside the wheel (spokes) stay. -> the trimmed piece."""
    idx = np.where(piece)[0]
    rel = wco[idx] - c
    t = rel @ a
    radial = rel - np.outer(t, a)
    r = np.linalg.norm(radial, axis=1)
    e1 = np.cross(a, (1.0, 0.0, 0.0) if abs(a[0]) < 0.9 else (0.0, 1.0, 0.0))
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(a, e1)
    ang = np.arctan2(radial @ e2, radial @ e1)
    sl = ((ang + np.pi) / (2 * np.pi) * ROUND_BINS).astype(int) % ROUND_BINS
    step = ROUND_CELL * R
    ci, cj = np.floor(r / step).astype(int), np.floor(t / step).astype(int)
    seen = {}
    for i, j, k in zip(ci.tolist(), cj.tolist(), sl.tolist()):
        seen.setdefault((i, j), set()).add(k)
    cover = np.empty(len(idx))
    for n_, (i, j) in enumerate(zip(ci.tolist(), cj.tolist())):
        got = set()
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                got |= seen.get((i + di, j + dj), set())
        cover[n_] = len(got) / ROUND_BINS
    flat = np.zeros(len(wco), dtype=bool)
    flat[idx[cover < ROUND_SHARE]] = True
    flat[start] = False
    edge = np.zeros(len(wco), dtype=bool)  # piece points with a neighbour outside the piece
    cross = piece[edges[:, 0]] != piece[edges[:, 1]]
    edge[edges[cross].ravel()] = True
    edge &= piece
    drop = _split(flat, edges, np.where(flat & edge)[0].tolist())
    out = piece.copy()
    for d in drop:
        out &= ~d
    return _split(out, edges, [start])[0]


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


FPS = 30  # clips: one second to open or close, one turn a second
INSIDE_LIMIT = 0.02  # pass mark 5: at most this share of a piece's points inside the body when it is open
CLOSE_STEP = 2.0  # degrees per step when a part modelled open is turned shut
HINGE_CLEAR = 0.15  # the inside test counts only a hinged piece's points farther from the hinge than this share of its reach


def main():  # pragma: no cover - runs inside Blender
    """blender -b --python moving_parts.py -- <in.glb> <out_rig.glb> <out_anim.glb> --parts <json> --report <json>

    --parts: [{name, kind, locations (glTF frame), side, open_at_rest, angle | distance}] (declared_parts.for_moving).
    Writes out_rig.glb (the body, and each piece under its own pivot node, no animation) and out_anim.glb (the same
    with the clips) and the report. Every node is named for the engines: the pivot '<part>_<n>_Pivot' (still, its Z
    along the axis), its child '<part>_<n>' that the clips turn about Z or move along Z, and the piece's mesh
    '<part>_<n>_Mesh' under it. The body mesh keeps its name. Clips: hinge and slide '<part>_open' and '<part>_close'
    (FPS frames, eased), spin '<part>_spin' (one turn, linear, loops)."""
    import bmesh
    import bpy
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _common  # noqa: E402

    argv = _common.after_double_dash()
    src, out_rig, out_anim = (os.path.abspath(x) for x in argv[:3])
    report_path = _common.opt("--report", None)
    with open(_common.opt("--parts", ""), encoding="utf-8") as fh:
        parts = json.load(fh)
    report = {"ok": False, "fps": FPS, "parts": [], "clips": []}
    _common.open_glb(src)
    scene = bpy.context.scene
    meshes = [o for o in scene.objects if o.type == "MESH"]
    if not meshes:
        report["reason"] = "no mesh"
        return _write(report, report_path)
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    body = meshes[0]
    bpy.context.view_layer.objects.active = body
    if len(meshes) > 1:
        bpy.ops.object.join()
    mw = body.matrix_world.copy()
    body.parent = None
    body.matrix_world = mw
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    for o in [o for o in scene.objects if o is not body]:
        bpy.data.objects.remove(o, do_unlink=True)
    # Weld the seam copies (corner data such as UVs and normals stays on the corners).
    bm = bmesh.new()
    bm.from_mesh(body.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=WELD)
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    bm.to_mesh(body.data)
    bm.free()
    report["source"] = _edges(body)  # the model as it came: the pass adds no open or folded edges to these
    me = body.data
    co = np.array([v.co[:] for v in me.vertices], dtype=float)
    tri = np.array([p.vertices[:] for p in me.polygons], dtype=int)
    size = float(np.ptp(co, axis=0).max())
    owner = np.full(len(co), -1, dtype=int)  # which piece (index into pieces) claims each vertex
    pieces = []  # {part, n, mask, info}
    for part in parts:
        seeds = [gltf_to_blender(p) for p in part.get("locations") or []]
        if not seeds:
            continue
        _, masks, infos = find_pieces(co, tri, seeds, kind=part["kind"], size=size, info=True)
        for n, (m, inf) in enumerate(zip(masks, infos), start=1):
            m = m & (owner < 0)
            if m.sum() < MIN_PIECE:
                continue
            owner[m] = len(pieces)
            pieces.append({"part": part, "n": n, "mask": m, "info": inf})
    report["size_m"] = round(size, 4)
    if not pieces:
        report["reason"] = "no piece found at the points"
        return _write(report, report_path)
    rest_co = co.copy()
    # Faces: a face goes to the piece holding two or three of its corners. Mark them, then separate piece by piece.
    tag = me.attributes.get("vgac_piece") or me.attributes.new("vgac_piece", "INT", "FACE")
    fo = np.full(len(tri), -1, dtype=int)
    for i in range(len(pieces)):
        fo[(owner[tri] == i).sum(axis=1) >= 2] = i
    tag.data.foreach_set("value", fo.tolist())
    objs = []
    for i, pc in enumerate(pieces):
        vals = [0] * len(me.polygons)
        me.attributes["vgac_piece"].data.foreach_get("value", vals)
        if i not in vals:
            continue
        sel = [v == i for v in vals]
        me.polygons.foreach_set("select", sel)
        me.vertices.foreach_set("select", [False] * len(me.vertices))
        me.edges.foreach_set("select", [False] * len(me.edges))
        for p in me.polygons:
            if p.select:
                for vi in p.vertices:
                    me.vertices[vi].select = True
        me.update()
        bpy.ops.object.select_all(action="DESELECT")
        body.select_set(True)
        bpy.context.view_layer.objects.active = body
        before = set(scene.objects)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_mode(type="FACE")
        bpy.ops.mesh.separate(type="SELECTED")
        bpy.ops.object.mode_set(mode="OBJECT")
        new = [o for o in scene.objects if o not in before]
        if not new:
            continue
        ob = new[0]
        ob.name = f"{_node(pc['part']['name'])}_{pc['n']}_Mesh"
        ob.data.name = ob.name
        pc["obj"] = ob
        objs.append(ob)
        me = body.data
    # Close every opening the cut left, on the body and on each piece; note each piece's weld ring.
    for pc in pieces:
        if "obj" in pc:
            pc["ring"] = _cap(pc["obj"])
    _cap(body)
    body_pts = np.array([v.co[:] for v in body.data.vertices], dtype=float)
    centre_body = body_pts.mean(axis=0)
    bvh = BVHTree.FromObject(body, bpy.context.evaluated_depsgraph_get())

    def inside_share(pts):
        n_in = 0
        for p in pts:
            hit = bvh.find_nearest(Vector(p))
            if hit[0] is not None and (Vector(p) - hit[0]).dot(hit[1]) < -1e-5:
                n_in += 1
        return n_in / max(len(pts), 1)

    clip_rows = {}
    movers = []
    for pc in pieces:
        if "obj" not in pc:
            continue
        part, ob = pc["part"], pc["obj"]
        kind = part["kind"]
        pm = ob.data
        pts = np.array([v.co[:] for v in pm.vertices], dtype=float)
        sample = pts[np.linspace(0, len(pts) - 1, min(len(pts), 400)).astype(int)]
        capv = [0] * len(pm.polygons)
        if pm.attributes.get("vgac_cap"):
            pm.attributes["vgac_cap"].data.foreach_get("value", capv)
        own = [p for p, c in zip(pm.polygons, capv) if not c] or list(pm.polygons)
        facing = outward(np.array([p.normal[:] for p in own]), np.array([p.area for p in own]))
        node = f"{_node(part['name'])}_{pc['n']}"
        row = {"name": part["name"], "kind": kind, "piece": pc["n"], "node": node,
               "vertices": len(pm.vertices), "faces": len(pm.polygons), "open_at_rest": bool(part.get("open_at_rest"))}
        if kind == "spin":
            inf = pc["info"] or {}
            pivot = np.asarray(inf.get("centre", pts.mean(axis=0)), dtype=float)
            axis = roll_axis(inf.get("axis", (1.0, 0.0, 0.0)))
            row.update(how="axis from the normals", radius=round(float(inf.get("radius", 0.0)), 4))
            _round_caps(ob, axis, pivot)
            amount, sign = 360.0, 1.0
        elif kind == "hinge":
            pivot, axis, how = hinge_line(pc.get("ring", np.zeros((0, 3))), pts, facing, part.get("side", "auto"), part["name"])
            pivot = pivot + axis * float((pts.mean(axis=0) - pivot) @ axis)  # the point of the line level with the piece's middle
            row["how"] = how
            want = float(part.get("angle", 100.0))
            # the inside test leaves out the strip along the hinge, which touches the body at every angle
            off = np.linalg.norm(np.cross(sample - pivot, axis / np.linalg.norm(axis)), axis=1)
            sample = sample[off > HINGE_CLEAR * max(float(off.max()), 1e-9)]
            base = inside_share(sample)
            row["inside_rest"] = round(base, 4)
            if part.get("open_at_rest"):
                # turned shut: the way that brings it nearer the body, as far as it goes without passing into it
                d0 = np.linalg.norm(pts.mean(axis=0) - centre_body)
                sign = 1.0 if np.linalg.norm(turn(pts, pivot, axis, 10).mean(axis=0) - centre_body) < d0 else -1.0
                amount = 0.0
                while amount + CLOSE_STEP <= 180.0 and inside_share(turn(sample, pivot, axis, sign * (amount + CLOSE_STEP))) - base <= INSIDE_LIMIT:
                    amount += CLOSE_STEP
                row["inside_shut"] = round(inside_share(turn(sample, pivot, axis, sign * amount)), 4)
            else:
                tries = []
                for s_ in (1.0, -1.0):
                    moved = turn(sample, pivot, axis, s_ * want)
                    tries.append((round(inside_share(moved), 3), -float(np.linalg.norm(moved.mean(axis=0) - centre_body)), s_))
                tries.sort()
                sign, amount = tries[0][2], want
                row["inside_open"] = tries[0][0]
        else:  # slide
            axis = square(facing)
            pivot = pts.mean(axis=0)
            behind = float((body_pts @ -axis).max() - (pivot @ -axis))
            amount = float(part.get("distance", 0.8)) * max(behind, 0.0)
            sign = -1.0 if part.get("open_at_rest") else 1.0
            row.update(how="the piece's facing", depth_behind=round(behind, 4))
            if not part.get("open_at_rest"):
                _extrude_back(pm, axis, amount)  # the drawer's sides, hidden in the body when shut
                row["inside_open"] = round(inside_share(sample + axis * amount), 4)
        axis = np.asarray(axis, dtype=float) / max(np.linalg.norm(axis), 1e-12)
        row.update(pivot=blender_to_gltf(pivot), axis=blender_to_gltf(axis), sign=sign,
                   amount=round(float(amount), 4), unit="metres" if kind == "slide" else "degrees")
        # Nodes: the still pivot (its Z along the axis), the moving node, the piece's mesh.
        piv = bpy.data.objects.new(node + "_Pivot", None)
        scene.collection.objects.link(piv)
        m4 = Vector(axis.tolist()).to_track_quat("Z", "Y").to_matrix().to_4x4()
        m4.translation = Vector(pivot.tolist())
        piv.matrix_world = m4
        mov = bpy.data.objects.new(node, None)
        scene.collection.objects.link(mov)
        mov.parent = piv
        movers.append(mov)
        mov.rotation_mode = "XYZ"
        bpy.context.view_layer.update()
        ob.parent = mov
        ob.matrix_parent_inverse = mov.matrix_world.inverted()
        clips = _clips(mov, kind, sign, amount, bool(part.get("open_at_rest")), _node(part["name"]))
        for c in clips:
            clip_rows.setdefault(c["name"], c)
        row["clips"] = [c["name"] for c in clips]
        report["parts"].append(row)
    # The rest pose: no clip playing (the tracks would otherwise show each clip's first frame).
    tracks = [t for o in scene.objects if o.animation_data for t in o.animation_data.nla_tracks]
    for t in tracks:
        t.mute = True
    for o in movers:  # each scene update writes the playing tracks' values back into the nodes: set them still again
        o.location = (0.0, 0.0, 0.0)
        o.rotation_euler = (0.0, 0.0, 0.0)
    bpy.context.scene.frame_set(0)
    bpy.context.view_layer.update()
    # Closed and whole: every mesh at rest has no open and no folded edges.
    report["meshes"] = {o.name: _edges(o) for o in [body] + objs}
    report["added_open_edges"] = sum(m["open_edges"] for m in report["meshes"].values()) - report["source"]["open_edges"]
    report["added_folded_edges"] = sum(m["folded_edges"] for m in report["meshes"].values()) - report["source"]["folded_edges"]
    # Rest unchanged (pass mark 3): every original point is still on the model at rest; the caps add points.
    world = []
    for o in [body] + objs:
        world += [(o.matrix_world @ v.co)[:] for v in o.data.vertices]
    worst, moved = _nearest(rest_co, np.array(world))
    report["rest_offset_m"] = round(worst, 6)
    report["rest_moved_points"] = len(moved)
    if len(moved):
        report["rest_moved_where"] = [blender_to_gltf(q) for q in moved[:: max(1, len(moved) // 8)][:8]]
    report["clips"] = list(clip_rows.values())
    for o in scene.objects:
        o.select_set(o.type in ("MESH", "EMPTY"))
    bpy.context.view_layer.objects.active = body
    bpy.ops.export_scene.gltf(filepath=out_rig, export_format="GLB", use_selection=True, export_animations=False)
    for t in tracks:
        t.mute = False
    bpy.ops.export_scene.gltf(filepath=out_anim, export_format="GLB", use_selection=True, export_animations=True,
                              export_animation_mode="NLA_TRACKS", export_force_sampling=True)
    report["ok"] = bool(report["parts"])
    return _write(report, report_path)


def _write(report, path):  # pragma: no cover
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
    print("MOVING_PARTS_OK" if report.get("ok") else "MOVING_PARTS_SKIPPED", report.get("reason", ""))
    return report


def _node(name):
    """A part's name as a node name: letters, digits and underscores."""
    out = "".join(ch if ch.isalnum() else "_" for ch in str(name).strip())
    return "_".join(w for w in out.split("_") if w) or "part"


def _nearest(pts, ref):  # pragma: no cover
    """Largest distance from any of pts to the nearest of ref."""
    from mathutils.kdtree import KDTree
    kd = KDTree(len(ref))
    for i, p in enumerate(ref):
        kd.insert(p.tolist(), i)
    kd.balance()
    d = np.array([kd.find(p.tolist())[2] for p in pts])
    return float(d.max()), np.asarray(pts)[d > 1e-4]


def _cap(ob):  # pragma: no cover
    """Close every opening of ob's mesh: each boundary loop becomes a fan round its centre (faces marked vgac_cap). The
    whole cap takes one colour: the texture point in the middle of the largest face along its edge (the ring's own
    corners lie on several texture islands, so spreading them over the cap drags in the empty space between islands).
    -> the ring points before capping."""
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    uv = bm.loops.layers.uv.active
    cap = bm.faces.layers.int.get("vgac_cap") or bm.faces.layers.int.new("vgac_cap")
    ring_pts = [v.co[:] for v in bm.verts if v.is_boundary]
    for loop in _boundary_loops(bm):
        if len(loop) < 3:
            continue
        edge_faces = {f for v in loop for f in v.link_faces}
        big = max(edge_faces, key=lambda f: f.calc_area()) if edge_faces else None
        mat = big.material_index if big is not None else 0
        colour = None
        if uv is not None and big is not None:
            colour = sum((lp[uv].uv for lp in big.loops), big.loops[0][uv].uv * 0) / len(big.loops)
        try:
            f = bm.faces.new(loop)
        except ValueError:
            continue
        res = bmesh.ops.poke(bm, faces=[f])
        for g in res["faces"]:
            g[cap] = 1
            g.material_index = mat
            if colour is not None:
                for lp in g.loops:
                    lp[uv].uv = colour
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(ob.data)
    bm.free()
    ob.data.update()
    return np.array(ring_pts, dtype=float).reshape(-1, 3)


def _round_caps(ob, axis, centre):  # pragma: no cover
    """A wheel's caps follow its round: where the tyre was fused to the body (the tray it touched) the cut leaves a
    hole in the tread, and a flat cap there shows once the wheel turns. Each cap's middle point goes out to its ring's
    distance from the axis, and the cap takes the colour of the nearest face of the wheel's own surface."""
    import bmesh
    from mathutils import Vector
    a = Vector(np.asarray(axis, dtype=float).tolist()).normalized()
    c = Vector(np.asarray(centre, dtype=float).tolist())
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    uv = bm.loops.layers.uv.active
    cap = bm.faces.layers.int.get("vgac_cap")
    if cap is None:
        bm.free()
        return
    own = [f for f in bm.faces if not f[cap] and not any(g[cap] for v in f.verts for g in v.link_faces)]
    for v in bm.verts:
        if not v.link_faces or not all(f[cap] for f in v.link_faces):
            continue  # not a cap's middle point
        ring = [e.other_vert(v) for e in v.link_edges]
        def split(p):
            rel = p - c
            t = rel.dot(a)
            return t, rel - a * t
        t, rad = split(v.co)
        want = sum(split(w.co)[1].length for w in ring) / max(len(ring), 1)
        if rad.length > 1e-9:
            v.co = c + a * t + rad.normalized() * want
        if uv is not None and own:
            near = min(own, key=lambda f: (f.calc_center_median() - v.co).length)
            colour = sum((lp[uv].uv for lp in near.loops), near.loops[0][uv].uv * 0) / len(near.loops)
            for f in v.link_faces:
                for lp in f.loops:
                    lp[uv].uv = colour
    bm.to_mesh(ob.data)
    bm.free()
    ob.data.update()


def _boundary_loops(bm):  # pragma: no cover
    """Each opening's edge as an ordered list of vertices."""
    left = {e for e in bm.edges if e.is_boundary}
    loops = []
    while left:
        e = left.pop()
        loop = [e.verts[0], e.verts[1]]
        while True:
            v = loop[-1]
            nxt = next((x for x in v.link_edges if x in left), None)
            if nxt is None:
                break
            left.discard(nxt)
            w = nxt.other_vert(v)
            if w is loop[0]:
                break
            loop.append(w)
        loops.append(loop)
    return loops


def _extrude_back(me, axis, depth):  # pragma: no cover
    """A drawer's sides: its capped back moves back by depth (against axis), the sides follow it."""
    import bmesh
    from mathutils import Vector
    if depth <= 0:
        return
    bm = bmesh.new()
    bm.from_mesh(me)
    cap = bm.faces.layers.int.get("vgac_cap")
    faces = [f for f in bm.faces if cap is not None and f[cap]]
    if faces:
        res = bmesh.ops.extrude_face_region(bm, geom=faces)
        verts = [g for g in res["geom"] if isinstance(g, bmesh.types.BMVert)]
        bmesh.ops.translate(bm, verts=verts, vec=Vector((-np.asarray(axis) * depth).tolist()))
        bmesh.ops.delete(bm, geom=faces, context="FACES")
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(me)
    bm.free()


def _edges(ob):  # pragma: no cover
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    out = {"open_edges": sum(1 for e in bm.edges if e.is_boundary),
           "folded_edges": sum(1 for e in bm.edges if not e.is_manifold and not e.is_boundary),
           "faces": len(bm.faces)}
    bm.free()
    return out


def _clips(mov, kind, sign, amount, open_at_rest, part):  # pragma: no cover
    """The moving node's actions, each in its own NLA track named after the clip (the glTF export joins tracks of one
    name across nodes into one animation). -> [{name, loop, frame_start, frame_end, frames, seconds, hold_last,
    in_place, part, kind}]."""
    import bpy
    mov.animation_data_create()
    path, index = ("location", 2) if kind == "slide" else ("rotation_euler", 2)
    value = sign * (amount if kind == "slide" else float(np.radians(amount)))
    if kind == "spin":
        plan = [(f"{part}_spin", [(0, 0.0), (FPS, value)], True)]
    elif open_at_rest:  # the model shows it open: shut is the far end
        plan = [(f"{part}_close", [(0, 0.0), (FPS, value)], False), (f"{part}_open", [(0, value), (FPS, 0.0)], False)]
    else:
        plan = [(f"{part}_open", [(0, 0.0), (FPS, value)], False), (f"{part}_close", [(0, value), (FPS, 0.0)], False)]
    rows = []
    for clip, keys, loop in plan:
        act = bpy.data.actions.new(f"{clip}|{mov.name}")
        mov.animation_data.action = act
        for frame, v in keys:
            vec = list(getattr(mov, path))
            vec[index] = v
            setattr(mov, path, vec)
            mov.keyframe_insert(data_path=path, index=index, frame=frame)
        for fc in _fcurves(act):
            for kp in fc.keyframe_points:
                kp.interpolation = "LINEAR" if loop else "BEZIER"
        mov.animation_data.action = None
        track = mov.animation_data.nla_tracks.new()
        track.name = clip
        strip = track.strips.new(clip, 0, act)
        if hasattr(strip, "action_slot") and len(getattr(act, "slots", [])) > 0:
            strip.action_slot = act.slots[0]
        strip.extrapolation = "NOTHING"
        rows.append({"name": clip, "loop": loop, "frame_start": 0, "frame_end": FPS, "frames": FPS + 1,
                     "seconds": 1.0, "hold_last": not loop, "in_place": True, "part": part, "kind": kind})
    setattr(mov, path, [0.0, 0.0, 0.0])
    return rows


def _fcurves(act):  # pragma: no cover
    """An action's curves (Blender 5 keeps them in layered channel bags)."""
    if hasattr(act, "fcurves") and len(getattr(act, "fcurves", [])):
        return list(act.fcurves)
    out = []
    for layer in getattr(act, "layers", []):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", []):
                out.extend(bag.fcurves)
    return out


if __name__ == "__main__":  # pragma: no cover
    main()
