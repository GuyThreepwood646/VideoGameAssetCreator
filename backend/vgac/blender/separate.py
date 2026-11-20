"""Blender headless: separate a limb the 3D build grew into the body (an upper arm into the torso, a sleeve into a coat).

Run: blender -b --python separate.py -- <step4.glb> <labels_rig.glb> <out.glb> --report <json>
         [--side L|R|both] [--gap 0.006] [--start 0.3] [--rigged-out <rig_out.glb>] [--debug]

WP-25, proved on the stored blacksmith (upper arms grown into the torso) and mage (sleeves grown into the robe); WP-27
added the weights, the cap colour and the rest pose (evidence in bakeoff/wp25 and bakeoff/wp27, local). Steps, per arm:
0. Labels. Each step 4 point takes the skin weights of its rig point, found by face corners (the rig keeps the step 4
   model's triangles in order; passes after the rig such as palm-down moved some points, so the nearest point can be
   the wrong one), or the nearest rig point when the corners do not line up.
1. The weight line. Each point's arm share is the summed skin weight of that side's upper arm bone and every bone below
   it (finger bones the app added included), smoothed. Where the build fused the arm to the body the share falls from
   1 to 0 across the fused strip; the line where it crosses one half, beyond `start` (a share of the upper arm from the
   shoulder joint, so the shoulder stays whole), is found per triangle.
2. A flat cut. The plane that best fits each weight-line piece cuts the triangles within BAND of it. A cut along the
   weight line itself is not flat, and its two caps crossed each other and the surface on every real model tried.
3. The true section. Every other place the plane crosses the surface inside the cut's outline (a robe's inner layer, an
   air pocket) is cut too and becomes a hole in the cap, so the cap is exactly the plane's section of the solid. A cut
   piece whose inner cut does not close is refused (reason `inner_cut_not_closed`; for example a flat wall lying in the
   plane), and so is a closed cut that would only fence off a small patch.
4. The gap. Inside the outline, every surface point within gap/2 of the plane, the cut's own copies included, moves
   straight off the plane to its own side (at most gap/2), so a cloth layer lying along the plane cannot poke through a
   cap. The gap narrows to nothing over RAMP at the cut's ends, which stay put: they are the ends of the new armpit.
5. Weights. Each side of the fused strip lets go of the other side's weights (fading in past `start`).
6. Caps. One constrained Delaunay triangulation of the outline with inner points gives both caps, parallel copies a
   gap apart, joined at the new armpit edge; inner points blend the weights of the nearest edge points.
7. Weights again (WP-27, _rederive). The arm's own surface is flooded from the arm side of its cuts; on it only the
   arm's bones stay, everywhere else past the shoulder this arm's bones go (hair or robe the build skinned to the arm),
   points left with nothing are filled from their own side, and each side is smoothed on its own with a volume limit.
8. The raise. Each arm is raised 90 degrees out to the side, before and after, and the edges near its cut (and over the
   whole model) stretched past STRETCH_RATIO are counted. An arm whose near-cut count rose is put back as it was
   (reason `stretch_rose`).
9. Texture. Caps get texture space of their own (uvfit.place_islands in the atlas's free space, at the surface's texel
   density, smaller if short of room). Their colour comes from the surface round their ring: each ring point's colour
   is read just inside the surface triangles beside it (only texels the surface covers), cap pixels blend those by
   inverse distance, and the surface's own detail is mirrored across the ring within a small swing. Every picture of
   the material is filled so; normal maps get a flat normal; cap faces are shaded flat. Nothing else in any picture
   changes. The report lists each cap's texture triangles (the paint tools find the caps there) and its colour match.
10. Checks against the input: open edges, edges with three or more faces, pieces, and triangle pairs crossing that did
   not cross before. `ok` is false when any of them got worse.
11. The rigged copy (--rigged-out) is put in the rig's rest pose: each point moves by the offset of the nearest place on
   the input surface between the step 4 model and its rig (palm-down hands), so rig.carry can use it as the new rig.
Refused, with the model unchanged: a weight-line piece that starts past the upper arm (`not_upper_arm`: the low-poly
dwarf, whose arm weights begin on the forearm). When no cut is left the script writes no model (`changed` false).
No booleans and no remesh: the rest of the surface keeps its points, triangles and texture coordinates. Not handled:
arms whose weight line never leaves the shoulder (the low-poly dwarf). Runs are repeatable: every set is walked in an
order fixed by position.

Everything above main() is plain numpy so the backend tests can run it without Blender.
"""
import json
import math
import os
import sys
from collections import deque

import numpy as np

_FACE_FAIL = {}  # why cap triangles could not be made, by reason (reported with --debug)
ISO = 0.5  # arm share at which the cut runs
SMOOTH_PASSES = 6  # passes of neighbour averaging of the arm share before the cut (removes speckle)
MIN_CUT_LEN = 0.06  # m: shorter weight-line pieces are speckle, left uncut
PATCH_SHARE = 0.05  # a closed cut that fences off less than this share of the surface is a patch, left uncut
RAMP = 0.04  # m along the cut from each end over which the gap opens from nothing to full
BAND = 0.05  # m: the flat cut is applied to triangles this close to the weight line (more when the line is not flat)
RELEASE_RINGS = 3  # rings beside the cut that always let go of the other side's weights
RELEASE_RAMP = 0.15  # share of the upper arm past `start` over which the letting go fades in
RELEASE_NEAR = 0.06  # m: within this of the cut a point's side is its side of the plane, further out its arm share
SLAB_MARGIN = 0.0005  # m kept between a pushed point and the cap
CAP_SPACING_MIN = 0.02  # m: the least spacing of a cap's inner points (twice the cut's edge length otherwise)
NOT_UPPER_ARM = 0.35  # a weight-line piece starting this far past `start` (in upper-arm lengths) is not an upper-arm join
RAISE_DEG = 90.0  # the test raise: the upper arm swung out to the side, about the front axis at the shoulder joint
STRETCH_RATIO = 1.6  # an edge longer than this times its rest length in the raise is stretched
STRETCH_NEAR = 0.08  # m: edges with a point this close to the side's cut count as near the cut
CORNER_TOL = 1e-4  # m: a step 4 face corner and the rig's are the same point when this close
CORNER_SHARE = 0.5  # the corners line up when at least this share of them are the same point
WEIGHT_PASSES = 2  # neighbour averaging passes over the points whose weights were re-derived (each side on its own)
WEIGHT_RINGS = 2  # rings round the re-derived points that are smoothed with them
WEIGHT_LIMIT = 0.3  # the volume limit: a point's weights may move at most this far in the smoothing
RING_INSET = 0.35  # an edge point's colour is read this far from it toward the middle of each surface triangle beside it
SAMPLE_RADIUS_PX = 2  # texels round that read point averaged (only those the surface covers)
IDW_NEAREST = 12  # a cap pixel blends the colours of this many nearest edge points
MIRROR_REACH = 0.1  # m: the surface a cap mirrors from lies within the cap's size plus this of its middle
MIRROR_SWING = 0.08  # the most the mirrored detail moves a cap pixel off the blended ring colour (of 1)
MIRROR_LAND = 0.03  # m: a mirrored point further than this from that surface keeps the blended ring colour
CAP_PAD_PX = 2  # pixels a cap's colour is grown past its edge in the picture
ARM_SURFACE_SHARE = 0.1  # away from a cut, the arm's own surface is where its arm share is over this
LEAK_SHARE = 0.4  # an arm's own surface reaching more than this share of the model has leaked into the body


def arm_share(weights, groups, arm_names):
    """(n,) share of each point's skin weight on the arm's bones (`arm_names`: the upper arm bone and every bone
    below it, fingers and added finger bones included). weights: list of {group_index: weight}."""
    arm = {i for i, g in enumerate(groups) if g in arm_names}
    out = np.zeros(len(weights))
    for k, d in enumerate(weights):
        tot = sum(d.values())
        if tot > 1e-9:
            out[k] = sum(w for g, w in d.items() if g in arm) / tot
    return out


def smooth_field(a, nbrs, passes=SMOOTH_PASSES):
    """Neighbour averaging (half own value, half the neighbours' mean), `passes` times. nbrs: list of index arrays."""
    a = np.asarray(a, dtype=float).copy()
    for _ in range(passes):
        m = np.array([a[n].mean() if len(n) else a[i] for i, n in enumerate(nbrs)])
        a = 0.5 * a + 0.5 * m
    return a


def chain_param(P, chain):
    """Distance along a bone chain (list of joint points, shoulder first) of each point's nearest place on it, in
    bone lengths: 0 at the shoulder joint, 1 at the elbow, 2 at the wrist; beyond the last joint it runs on along the
    last bone. -> (t (n,), dist (n,))."""
    P = np.asarray(P, dtype=float)
    best_d = np.full(len(P), np.inf)
    best_t = np.zeros(len(P))
    for k in range(len(chain) - 1):
        A, B = np.asarray(chain[k], float), np.asarray(chain[k + 1], float)
        AB = B - A
        L2 = float(AB @ AB) or 1e-12
        s = (P - A) @ AB / L2
        last = k == len(chain) - 2
        s_c = np.clip(s, 0.0 if k else -np.inf, np.inf if last else 1.0)
        Q = A + s_c[:, None] * AB
        d = np.linalg.norm(P - Q, axis=1)
        upd = d < best_d
        best_d[upd] = d[upd]
        best_t[upd] = k + s_c[upd]
    return best_t, best_d


def inside_polygon(pts, poly):
    """Even-odd test: which 2D points `pts` (n, 2) lie inside the closed polygon `poly` (m, 2)."""
    pts, poly = np.asarray(pts, dtype=float), np.asarray(poly, dtype=float)
    x, y = pts[:, 0][:, None], pts[:, 1][:, None]
    x0, y0 = poly[:, 0][None, :], poly[:, 1][None, :]
    x1, y1 = np.roll(poly[:, 0], -1)[None, :], np.roll(poly[:, 1], -1)[None, :]
    straddle = (y0 > y) != (y1 > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        xc = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
    return (straddle & (x < xc)).sum(axis=1) % 2 == 1


def signed_area(poly):
    """Signed area of a closed 2D polygon (n, 2): positive when it runs anticlockwise."""
    P = np.asarray(poly, dtype=float)
    x, y = P[:, 0], P[:, 1]
    return 0.5 * float((x * np.roll(y, -1) - np.roll(x, -1) * y).sum())


def seg_distance(P, A, B):
    """Distance of 2D points P (n, 2) to the nearest of segments A[i]-B[i] (m, 2)."""
    P, A, B = (np.asarray(x, dtype=float) for x in (P, A, B))
    AB = B - A
    L2 = np.maximum((AB ** 2).sum(axis=1), 1e-18)
    s_ = np.clip(((P[:, None, :] - A[None]) * AB[None]).sum(axis=2) / L2[None], 0, 1)
    Q = A[None] + s_[..., None] * AB[None]
    return np.sqrt(((P[:, None, :] - Q) ** 2).sum(axis=2)).min(axis=1)


def closest_on_segments(P, A, B, chunk=2048):
    """For 3D points P (n, 3), the nearest point on any of the segments A[i]-B[i] (m, 3) -> (n, 3)."""
    P, A, B = (np.asarray(x, dtype=float) for x in (P, A, B))
    AB = B - A
    L2 = np.maximum((AB ** 2).sum(axis=1), 1e-18)
    out = np.empty_like(P)
    for s in range(0, len(P), chunk):
        Q = P[s:s + chunk]
        t = np.clip(((Q[:, None, :] - A[None]) * AB[None]).sum(axis=2) / L2[None], 0, 1)
        C = A[None] + t[..., None] * AB[None]
        k = np.argmin(((Q[:, None, :] - C) ** 2).sum(axis=2), axis=1)
        out[s:s + chunk] = C[np.arange(len(Q)), k]
    return out


def raise_arm(P, share, pivot, axis, deg=RAISE_DEG):
    """Linear blend skinning of one pose: every bone of the arm turned by `deg` about `axis` through `pivot`, the rest
    still. A point moves by its arm share of the turn."""
    k = np.asarray(axis, float) / np.linalg.norm(axis)
    a = math.radians(deg)
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    Rm = np.eye(3) + math.sin(a) * K + (1 - math.cos(a)) * K @ K
    Q = P - pivot
    return P + np.asarray(share, float)[:, None] * (Q @ Rm.T - Q)


def raise_stretch(P, edges, share, pivot, axis, near, ratio=STRETCH_RATIO):
    """Edges with a point flagged `near` -> (how many, how many stretched past `ratio` in the raise)."""
    E = np.asarray(edges, int).reshape(-1, 2)
    E = E[np.asarray(near, bool)[E].any(axis=1)]
    if not len(E):
        return 0, 0
    Q = raise_arm(P, share, pivot, axis)
    rest = np.linalg.norm(P[E[:, 0]] - P[E[:, 1]], axis=1)
    now = np.linalg.norm(Q[E[:, 0]] - Q[E[:, 1]], axis=1)
    ok = rest > 1e-6
    return int(len(E)), int((now[ok] > ratio * rest[ok]).sum())


def corner_map(s_corners, s_pos, r_corners, r_pos, tol=CORNER_TOL, min_share=CORNER_SHARE):
    """Which rig point each step 4 point is, from the two meshes' face corners in the same order (the rig is made from
    the step 4 model and keeps its triangles; passes after the rig such as palm-down move some points, so position alone
    can name the wrong point). s_corners, r_corners: the point index of every face corner, flattened in face order.
    -> (rig index per step 4 point (-1 where no corner names one), rig rest position minus step 4 position (n, 3)), or
    None when the corners do not line up (different counts, or fewer than min_share of them in the same place)."""
    s_corners, r_corners = np.asarray(s_corners, int), np.asarray(r_corners, int)
    s_pos, r_pos = np.asarray(s_pos, float), np.asarray(r_pos, float)
    if s_corners.shape != r_corners.shape or not len(s_corners):
        return None
    d = np.linalg.norm(s_pos[s_corners] - r_pos[r_corners], axis=1)
    if float((d < tol).mean()) < min_share:
        return None
    rig_of = np.full(len(s_pos), -1, dtype=int)
    rig_of[s_corners] = r_corners
    off = np.zeros_like(s_pos)
    ok = rig_of >= 0
    off[ok] = r_pos[rig_of[ok]] - s_pos[ok]
    return rig_of, off


def flood(seeds, nbrs, allowed):
    """Points reached from `seeds` stepping only onto points where `allowed` is true -> (n,) bool."""
    reached = np.zeros(len(nbrs), dtype=bool)
    stack = [int(s) for s in seeds if allowed[s]]
    reached[stack] = True
    while stack:
        i = stack.pop()
        for j in nbrs[i]:
            if not reached[j] and allowed[j]:
                reached[j] = True
                stack.append(j)
    return reached


def keep_side(weights, is_arm, in_arm, t, start, ramp=RELEASE_RAMP):
    """Each point past `start` keeps only its own side's bones: on the arm's own surface (`in_arm`) the arm's bones,
    elsewhere every other bone. The change fades in over `ramp` past `start` so the shoulder keeps its blend.
    -> (new weights, indices changed, indices left with nothing of their side (to fill from neighbours))."""
    out = [dict(d) for d in weights]
    changed, need = set(), set()
    for i, d in enumerate(weights):
        if t[i] <= start or not d:
            continue
        tot = sum(d.values())
        mine = {g: w for g, w in d.items() if bool(is_arm[g]) == bool(in_arm[i])}
        if len(mine) == len(d) or tot <= 1e-9:
            continue
        kt = sum(mine.values())
        s = min(1.0, max(0.0, (t[i] - start) / ramp))
        if kt <= 1e-6:
            if s >= 1.0:
                need.add(i)
            continue
        out[i] = {g: (1 - s) * d.get(g, 0.0) / tot + s * mine.get(g, 0.0) / kt for g in set(d) | set(mine)}
        out[i] = {g: w for g, w in out[i].items() if w > 1e-6}
        changed.add(i)
    return out, changed, need


def fill_from_neighbours(weights, need, nbrs, cls, max_iter=200):
    """Points in `need` take the mean weights of their neighbours of the same class (`cls`) that have weights, ring by
    ring inward. -> (weights, how many were left unfilled (they keep what they had))."""
    out = [dict(d) for d in weights]
    todo = set(need)
    for _ in range(max_iter):
        if not todo:
            break
        done = {}
        for i in sorted(todo):
            src = [j for j in nbrs[i] if j not in todo and cls[j] == cls[i] and out[j]]
            if not src:
                continue
            acc = {}
            for j in src:
                for g, w in out[j].items():
                    acc[g] = acc.get(g, 0.0) + w / len(src)
            done[i] = acc
        if not done:
            break
        for i, acc in done.items():
            out[i] = acc
        todo -= set(done)
    return out, len(todo)


def smooth_weights(weights, idx, nbrs, cls, passes=WEIGHT_PASSES, limit=WEIGHT_LIMIT):
    """Neighbour averaging of the weights of points `idx` over neighbours of the same class only (so the two sides of a
    cut never blend), `passes` times. The volume limit: a point whose weights would move by more than `limit` (half the
    summed absolute change) from where they started keeps them as they were.
    -> (weights, how many points the limit held)."""
    out = [dict(d) for d in weights]
    idx = sorted(set(idx))
    start_w = {i: dict(weights[i]) for i in idx}
    for _ in range(passes):
        new = {}
        for i in idx:
            src = [j for j in nbrs[i] if cls[j] == cls[i]]
            if not src:
                continue
            ti = sum(out[i].values()) or 1.0
            acc = {g: 0.5 * w / ti for g, w in out[i].items()}
            for j in src:
                tj = sum(out[j].values()) or 1.0
                for g, w in out[j].items():
                    acc[g] = acc.get(g, 0.0) + 0.5 * w / tj / len(src)
            new[i] = acc
        for i, acc in new.items():
            out[i] = acc
    held = 0
    for i in idx:
        a, b = start_w[i], out[i]
        ta, tb = sum(a.values()) or 1.0, sum(b.values()) or 1.0
        move = 0.5 * sum(abs(a.get(g, 0.0) / ta - b.get(g, 0.0) / tb) for g in set(a) | set(b))
        if move > limit:
            out[i] = a
            held += 1
    return out, held


def top_influences(d, k=4):
    """The k largest weights of a point, summing to one."""
    items = sorted(((w, g) for g, w in d.items() if w > 1e-6), reverse=True)[:k]
    tot = sum(w for w, _ in items)
    return {g: w / tot for w, g in items} if tot > 1e-9 else {}


def cap_grid(loops, spacing):
    """Inner points for a flat cap: a staggered grid at `spacing` inside the first loop (the outline) and outside the
    others (holes), at least half a spacing from every loop edge. loops: list of (n, 2) closed polylines."""
    outer = np.asarray(loops[0], dtype=float)
    lo, hi = outer.min(axis=0), outer.max(axis=0)
    rows = []
    dy = spacing * math.sqrt(3) / 2
    for k, y in enumerate(np.arange(lo[1] + dy / 2, hi[1], dy)):
        xs = np.arange(lo[0] + (spacing / 2 if k % 2 else spacing / 4), hi[0], spacing)
        rows.append(np.c_[xs, np.full(len(xs), y)])
    if not rows:
        return np.zeros((0, 2))
    G = np.vstack(rows)
    if not len(G):
        return G
    keep = inside_polygon(G, outer)
    for h in loops[1:]:
        keep &= ~inside_polygon(G, h)
    G = G[keep]
    if not len(G):
        return G
    A = np.vstack([np.asarray(lp, dtype=float) for lp in loops])
    B = np.vstack([np.roll(np.asarray(lp, dtype=float), -1, axis=0) for lp in loops])
    return G[seg_distance(G, A, B) > spacing / 2]


def main(skip=None):  # pragma: no cover - runs inside Blender
    """skip: sides already put back by an earlier pass (their report parts), when a cut made the raise worse."""
    import bmesh
    import bpy
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    from mathutils.kdtree import KDTree

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _common
    import uvfit

    argv = _common.after_double_dash()
    src, lab_src, dst = (os.path.abspath(p) for p in argv[:3])
    report_path = _common.opt("--report", None)
    sides = {"L": ["L"], "R": ["R"], "both": ["L", "R"]}[_common.opt("--side", "both")]
    gap = float(_common.opt("--gap", "0.006"))
    start = float(_common.opt("--start", "0.3"))
    rigged_out = _common.opt("--rigged-out", None)
    report = {"ok": False, "changed": False, "sides": {}, "reason": None}

    # --- the step 4 model and the rig that labels it
    _common.open_glb(src)
    mo = next(o for o in bpy.context.scene.objects if o.type == "MESH")
    _common.import_glb(lab_src)
    arm_obj = next((o for o in bpy.context.scene.objects if o.type == "ARMATURE"), None)
    rig_mesh = max((o for o in bpy.context.scene.objects if o.type == "MESH" and o.vertex_groups),
                   key=lambda o: len(o.data.vertices), default=None)
    if arm_obj is None or rig_mesh is None:
        report["reason"] = "no_rig"
        return _finish(report, report_path)

    me = mo.data
    me.transform(mo.matrix_world)
    mo.matrix_world.identity()
    # which rig point each step 4 point is: by face corners when the two meshes line up (WP-27), else the nearest point
    rmw = rig_mesh.matrix_world
    corr = None
    if [p.loop_total for p in me.polygons] == [p.loop_total for p in rig_mesh.data.polygons]:
        corr = corner_map([lp.vertex_index for lp in me.loops], [v.co[:] for v in me.vertices],
                          [lp.vertex_index for lp in rig_mesh.data.loops],
                          [(rmw @ v.co)[:] for v in rig_mesh.data.vertices])
    bm = bmesh.new()
    bm.from_mesh(me)
    rest_off = bm.verts.layers.float_vector.new("rest_off")
    rig_ix = bm.verts.layers.int.new("rig_ix")
    bm.verts.ensure_lookup_table()
    for v in bm.verts:
        if corr is not None and corr[0][v.index] >= 0:
            v[rig_ix] = int(corr[0][v.index])
            v[rest_off] = Vector(corr[1][v.index])
        else:
            v[rig_ix] = -1
    n_in = len(bm.verts)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
    bm.verts.ensure_lookup_table()
    report["input"] = {"points": n_in, "points_joined": len(bm.verts), "triangles": len(bm.faces)}
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    chk0 = _check(bm)
    report["input"]["check"] = {k: v for k, v in chk0.items() if k not in ("crossing_faces", "_pairs")}
    bm.faces.ensure_lookup_table()
    in_cross = [bm.faces[i].calc_center_median().copy() for i in chk0["crossing_faces"]]
    bm.verts.ensure_lookup_table()

    # labels: the rig point by face corners (else the nearest), weights copied into a deform layer (interpolated by the
    # cuts below)
    kd = KDTree(len(rig_mesh.data.vertices))
    for v in rig_mesh.data.vertices:
        kd.insert(rmw @ v.co, v.index)
    kd.balance()
    groups = [g.name for g in rig_mesh.vertex_groups]
    for g in groups:
        mo.vertex_groups.new(name=g)
    dl = bm.verts.layers.deform.verify()
    far = 0
    for v in bm.verts:
        _, j, d = kd.find(v.co)
        far += d > 1e-4
        if v[rig_ix] >= 0:
            j = v[rig_ix]
        dv = v[dl]
        for ge in rig_mesh.data.vertices[j].groups:
            if ge.weight > 0:
                dv[ge.group] = ge.weight
    report["input"]["points_off_rig"] = int(far)
    report["input"]["labels"] = "corners" if corr is not None else "nearest"
    report["input"]["rest_moved_points"] = int((np.linalg.norm(corr[1], axis=1) > 1e-4).sum()) if corr is not None else 0
    # the input surface with each corner's rest offset, so any point of the result can be put in the rig's rest pose
    rest_src = _rest_source(bm, rest_off) if corr is not None else None

    def joints(prefix):
        b = arm_obj.data.bones
        mw = arm_obj.matrix_world
        names = [prefix + "Arm", prefix + "ForeArm", prefix + "Hand"]
        found = []
        for nm in names:
            bone = b.get("mixamorig:" + nm) or b.get(nm)
            if bone is None:
                return None
            found.append(np.array(mw @ bone.head_local))
        hand = b.get("mixamorig:" + prefix + "Hand") or b.get(prefix + "Hand")
        found.append(np.array(mw @ hand.tail_local))
        return found

    ash = bm.verts.layers.float.new("arm_share")
    bm.verts.index_update()
    bm.edges.ensure_lookup_table()
    before = (np.array([v.co[:] for v in bm.verts]), np.array([[e.verts[0].index, e.verts[1].index] for e in bm.edges]),
              [dict(v[dl].items()) for v in bm.verts])

    def raised(side_name, arm_names, P, E, weights, cap_pts):
        """(near-cut edges, stretched) with this side's arm raised."""
        kdc = KDTree(len(cap_pts))
        for i, c in enumerate(cap_pts):
            kdc.insert(c, i)
        kdc.balance()
        near = np.array([kdc.find(Vector(p_))[2] < STRETCH_NEAR for p_ in P])
        bone = arm_obj.data.bones.get("mixamorig:" + side_name + "Arm") or arm_obj.data.bones.get(side_name + "Arm")
        mw = arm_obj.matrix_world
        head, tail = mw @ bone.head_local, mw @ bone.tail_local
        axis = (0.0, -1.0, 0.0) if tail.x > head.x else (0.0, 1.0, 0.0)  # out and up, the character facing -Y
        share = arm_share(weights, groups, arm_names)
        n_near, s_near = raise_stretch(P, E, share, np.array(head[:]), axis, near)
        # the whole model too: hair or robe that took arm weight stretches far from the cut
        n_all, s_all = raise_stretch(P, E, share, np.array(head[:]), axis, np.ones(len(P), dtype=bool))
        return n_near, s_near, n_all, s_all

    for side in sides:
        if skip and side in skip:  # an earlier run found this side's cut made the raise worse: left as it was
            report["sides"][side] = skip[side]
            continue
        rep = report["sides"].setdefault(side, {})
        chain = joints("Left" if side == "L" else "Right")
        if chain is None:
            rep["reason"] = "no_arm_bones"
            continue
        bm.verts.index_update()
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        weights = [dict(v[dl].items()) for v in bm.verts]
        arm_names = _arm_bones(arm_obj, "Left" if side == "L" else "Right")
        rep["arm_bones"] = len(arm_names)
        a = arm_share(weights, groups, arm_names)
        nbrs = [np.array([e.other_vert(v).index for e in v.link_edges], dtype=int) for v in bm.verts]
        a = smooth_field(a, nbrs)
        P = np.array([v.co[:] for v in bm.verts])
        t, _ = chain_param(P, chain)
        for v in bm.verts:
            v[ash] = float(a[v.index])
            if abs(v[ash] - ISO) < 1e-4:
                v[ash] = ISO + (1e-4 if v[ash] >= ISO else -1e-4)
        tv = {v: float(t[v.index]) for v in bm.verts}

        # 1. the weight line: where the arm share crosses one half, below `start` (positions only, mesh untouched)
        segs = _iso_segments(bm, ash, tv, start)
        pieces = [pc for pc in _seg_pieces(segs) if pc["length"] >= MIN_CUT_LEN]
        rep["weight_line_pieces"] = len(pieces)
        rep["weight_line_m"] = round(sum(pc["length"] for pc in pieces), 4)
        caps = []
        for pc in pieces:
            # a piece starting past the upper arm is not a join of the upper arm into the body (the dwarf's forearm)
            t_min = float(chain_param(pc["points"], chain)[0].min())
            if t_min > start + NOT_UPPER_ARM:
                caps.append({"ok": False, "reason": "not_upper_arm", "t_min": round(t_min, 3),
                             "weight_line_m": round(pc["length"], 4)})
                continue
            # 2. a flat cut: the plane that best fits the weight line, applied only to the fused band round it
            pts = pc["points"]
            c = pts.mean(axis=0)
            _, _, vt = np.linalg.svd(pts - c, full_matrices=False)
            n = vt[2]
            arm_pts = np.array([v.co[:] for v in bm.verts if v[ash] > 0.9 and tv[v] > start])
            if len(arm_pts) and float((arm_pts.mean(axis=0) - c) @ n) < 0:
                n = -n
            flat = float(np.abs((pts - c) @ n).max())
            reach = max(BAND, 2 * flat + 0.01)
            kdp = KDTree(len(pts))
            for i, p_ in enumerate(pts):
                kdp.insert(Vector(p_), i)
            kdp.balance()
            region = [f for f in bm.faces if all(tv.get(v, 0.0) > start for v in f.verts)
                      and min(kdp.find(v.co)[2] for v in f.verts) < reach]
            geom = list({x for f in region for x in (f, *f.edges, *f.verts)})
            res = bmesh.ops.bisect_plane(bm, geom=geom, dist=1e-6, plane_co=Vector(c), plane_no=Vector(n))
            cut_edges = [e for e in res["geom_cut"] if isinstance(e, bmesh.types.BMEdge)]
            for v in bm.verts:
                if v not in tv:
                    nb = [tv[w] for w in (e.other_vert(v) for e in v.link_edges) if w in tv]
                    tv[v] = float(np.mean(nb)) if nb else start
            comps = [cc for cc in _components(cut_edges) if sum(e.calc_length() for e in cc) >= MIN_CUT_LEN]
            if not comps:
                caps.append({"ok": False, "reason": "plane_missed"})
                continue
            comp = min(comps, key=lambda cc: np.mean([kdp.find(v.co)[2] for e in cc for v in e.verts]))
            # 3. the cap must be the plane's true section of the solid: every other place the plane crosses the
            # surface inside the cut's outline (a robe's inner layer, an air pocket) is cut too and becomes a hole
            holes, why = _inner_loops(bm, comp, Vector(c), Vector(n), tv, start)
            if why:
                caps.append({"ok": False, "reason": why, "plane_flatness_m": round(flat, 4)})
                continue
            info = _cut_and_cap(bm, comp, Vector(c), Vector(n), gap, tv, dl, groups, arm_names, start, holes, ash)
            info.update(plane_flatness_m=round(flat, 4), band_m=round(reach, 4), plane_pieces=len(comps),
                        weight_line_m=round(pc["length"], 4))
            caps.append(info)
        # weights derived again round the cut and off the arm's own surface (WP-27)
        if any(cp.get("ok") for cp in caps):
            rep["weights"] = _rederive(bm, dl, groups, arm_names, chain, caps, start)
        # 9. the raise, before and after, near this side's new cut
        cap_pts =sorted({v.co.copy().freeze() for cp in caps for f in cp.get("faces", []) if f and f.is_valid
                          for v in f.verts}, key=tuple)
        if cap_pts:
            bm.verts.index_update()
            bm.edges.ensure_lookup_table()
            P1 = np.array([v.co[:] for v in bm.verts])
            E1 = np.array([[e.verts[0].index, e.verts[1].index] for e in bm.edges])
            name = "Left" if side == "L" else "Right"
            nb, sb, ab, wb = raised(name, arm_names, *before, cap_pts)
            na, sa, aa, wa = raised(name, arm_names, P1, E1, [dict(v[dl].items()) for v in bm.verts], cap_pts)
            rep["stretch"] = {"raise_deg": RAISE_DEG, "near_edges_before": nb, "stretched_before": sb,
                              "near_edges_after": na, "stretched_after": sa,
                              "edges_before": ab, "stretched_all_before": wb, "edges_after": aa, "stretched_all_after": wa}
            if sa > sb:  # the cut made the raise worse: start again from the files with this side left as it was
                rep["caps"] = [{**{k: v for k, v in cp.items() if k != "faces" and not k.startswith("_")},
                                **({"ok": False, "reason": "stretch_rose"} if cp.get("ok") else {})} for cp in caps]
                bm.free()
                return main(skip={**(skip or {}), side: rep})
        for cp in caps:
            cp["_side"] = side
        rep["caps"] = [{k: v for k, v in cp.items() if k != "faces" and not k.startswith("_")} for cp in caps]
        rep.setdefault("_caps", caps)

    if not any(cp.get("ok") for sd in report["sides"].values() for cp in sd.get("caps", [])):
        report["changed"] = False  # nothing cut: no model is written, the caller keeps the one it has
        report["reason"] = report["reason"] or "nothing_cut"
        bm.free()
        return _finish(report, report_path)
    report["changed"] = True
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    report["check"] = _check(bm)
    report["check"]["crossing_faces_n"] = len(report["check"]["crossing_faces"])
    bm.faces.ensure_lookup_table()
    xf = {bm.faces[i] for i in report["check"]["crossing_faces"]}
    on_caps = 0
    for sd in report["sides"].values():
        for cp, cpr in zip(sd.get("_caps", []), sd.get("caps", [])):
            k = sum(1 for f in cp.get("faces", []) if f.is_valid and f in xf)
            cpr["cap_faces_crossing"] = k
            on_caps += k
    report["check"]["crossing_faces_on_caps"] = on_caps
    capset = {f for sd in report["sides"].values() for cp in sd.get("_caps", []) for f in cp.get("faces", []) if f.is_valid}
    kinds = {"cap_cap": 0, "cap_surface": 0, "surface_surface": 0, "surface_surface_new": 0}
    for i, j in report["check"].pop("_pairs"):
        k = (bm.faces[i] in capset) + (bm.faces[j] in capset)
        kinds[("surface_surface", "cap_surface", "cap_cap")[k]] += 1
        if k == 0:
            cs = [bm.faces[x].calc_center_median() for x in (i, j)]
            if not all(any((c - q).length < 0.01 for q in in_cross) for c in cs):
                kinds["surface_surface_new"] += 1
    report["check"]["crossing_kinds"] = kinds
    report["check"]["new_crossings"] = kinds["cap_cap"] + kinds["cap_surface"] + kinds["surface_surface_new"]
    report["check"].pop("crossing_faces")
    if _common.flag("--debug"):
        # for looking at a run: cap faces red and crossing faces yellow in the outputs, cap points for the stretch test
        report["debug_face_fail"] = _FACE_FAIL
        report["debug_cap_points"] = [[round(c, 4) for c in v.co] for v in sorted({v for f in capset for v in f.verts},
                                                                                   key=lambda v: tuple(v.co))][::3]
        report["debug_crossing_xyz"] = [[round(c, 3) for c in f.calc_center_median()] for f in list(xf)[:400]]
        _debug_colours(bm, me, report, xf)
    all_caps = [cp for s in report["sides"].values() for cp in s.pop("_caps", [])]
    report["uv"] = _cap_uvs(bm, me, mo, all_caps)
    for cp in all_caps:
        cp.pop("_plane", None)
        cp.pop("faces", None)
        cp.pop("edge_verts", None)
    bm.to_mesh(me)
    me.update()
    bm.free()

    # step 4 output: the mesh alone
    bpy.ops.object.select_all(action="DESELECT")
    mo.select_set(True)
    bpy.context.view_layer.objects.active = mo
    groups_saved = [g.name for g in mo.vertex_groups]
    if rigged_out:
        rm = rig_mesh
        # the rigged copy sits in the rig's rest pose (palm-down and other passes baked after the rig moved some points)
        rest = None
        if rest_src is not None:
            P_now = np.array([v.co[:] for v in me.vertices])
            rest = rest_offsets(P_now, rest_src)
            report["rest_offset_points"] = int((np.linalg.norm(rest, axis=1) > 1e-4).sum())
            for v, o in zip(me.vertices, rest):
                v.co += Vector(o)
        mod = mo.modifiers.new("Armature", "ARMATURE")
        mod.object = arm_obj
        mo.parent = arm_obj
        bpy.ops.object.select_all(action="DESELECT")
        mo.select_set(True)
        arm_obj.select_set(True)
        bpy.context.view_layer.objects.active = arm_obj
        bpy.ops.export_scene.gltf(filepath=os.path.abspath(rigged_out), export_format="GLB", use_selection=True,
                                  export_apply=False, export_animations=False)
        mo.modifiers.remove(mod)
        mo.parent = None
        mo.matrix_world.identity()
        if rest is not None:
            for v, o in zip(me.vertices, rest):
                v.co -= Vector(o)
        del rm
    mo.vertex_groups.clear()
    bpy.ops.object.select_all(action="DESELECT")
    mo.select_set(True)
    bpy.context.view_layer.objects.active = mo
    bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", use_selection=True, export_apply=False)
    report["groups"] = len(groups_saved)
    ck, ik = report["check"], report["input"]["check"]
    report["ok"] = (ck["open_edges"] <= ik["open_edges"] and ck["bad_edges"] <= ik["bad_edges"]
                    and ck["new_crossings"] == 0 and ck["shells"] <= ik["shells"])
    return _finish(report, report_path)


def _finish(report, path):  # pragma: no cover
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
    print("SEPARATE_OK", json.dumps({k: report.get(k) for k in ("ok", "reason", "check")}))


def _iso_segments(bm, ash, tv, start):  # pragma: no cover
    """One segment per triangle the weight line crosses (all corners below `start`): (p, q, key_p, key_q), the keys
    naming the edges the ends sit on, so segments can be chained."""
    segs = []
    for f in bm.faces:
        vs = f.verts
        if any(tv[v] <= start for v in vs):
            continue
        ends = []
        for i in range(len(vs)):
            v0, v1 = vs[i], vs[(i + 1) % len(vs)]
            a0, a1 = v0[ash], v1[ash]
            if (a0 - ISO) * (a1 - ISO) < 0:
                u = (ISO - a0) / (a1 - a0)
                ends.append((np.array(v0.co.lerp(v1.co, u)[:]), frozenset((v0.index, v1.index))))
        if len(ends) == 2:
            segs.append((ends[0][0], ends[1][0], ends[0][1], ends[1][1]))
    return segs


def _seg_pieces(segs):  # pragma: no cover
    """Chain segments sharing an edge into pieces -> [{points (n, 3), length}]."""
    by_key = {}
    for i, (_, _, k0, k1) in enumerate(segs):
        by_key.setdefault(k0, []).append(i)
        by_key.setdefault(k1, []).append(i)
    seen, out = set(), []
    for i in range(len(segs)):
        if i in seen:
            continue
        stack, comp = [i], []
        seen.add(i)
        while stack:
            j = stack.pop()
            comp.append(j)
            for k in segs[j][2:]:
                for m in by_key[k]:
                    if m not in seen:
                        seen.add(m)
                        stack.append(m)
        pts = np.array([segs[j][0] for j in comp] + [segs[j][1] for j in comp])
        length = float(sum(np.linalg.norm(segs[j][0] - segs[j][1]) for j in comp))
        out.append({"points": pts, "length": length})
    return out


def _edge_key(e):  # pragma: no cover
    return tuple(sorted((tuple(e.verts[0].co), tuple(e.verts[1].co))))


def _components(edges):  # pragma: no cover
    """Connected pieces of an edge list, in a fixed order (by position), so a run repeats exactly."""
    edges = sorted(edges, key=_edge_key)
    left = set(edges)
    out = []
    for e in edges:
        if e not in left:
            continue
        left.discard(e)
        comp, stack = [e], [e]
        while stack:
            x = stack.pop()
            for v in x.verts:
                for y in v.link_edges:
                    if y in left:
                        left.discard(y)
                        comp.append(y)
                        stack.append(y)
        out.append(comp)
    return out


def _order(edges):  # pragma: no cover
    """The cut piece as an ordered list of points, and whether it closes on itself."""
    adj = {}
    for e in edges:
        a, b = e.verts
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    ends = sorted((v for v, n in adj.items() if len(n) == 1), key=lambda v: tuple(v.co))
    if any(len(n) > 2 for n in adj.values()):
        return None, None
    closed = not ends
    v0 = min(adj, key=lambda v: tuple(v.co)) if closed else ends[0]
    path, prev = [v0], None
    while True:
        nx = [w for w in adj[path[-1]] if w is not prev]
        if not nx or (closed and nx[0] is v0):
            break
        prev = path[-1]
        path.append(nx[0])
        if len(path) > len(adj) + 1:
            return None, None
    return path, closed


def _arm_bones(arm_obj, prefix):  # pragma: no cover
    b = arm_obj.data.bones
    top = b.get("mixamorig:" + prefix + "Arm") or b.get(prefix + "Arm")
    if top is None:
        return set()
    return {top.name} | {c.name for c in top.children_recursive}


def _cut_and_cap(bm, comp, pc, pn, gap, tv, dl, groups, arm_names, start, holes=(), ash=None):  # pragma: no cover
    """Split the mesh along one flat cut piece (plane point pc, normal pn toward the arm), open the gap, let each side
    go of the other's weights, and close each side with a flat cap."""
    import bmesh
    from mathutils import geometry

    path, closed = _order(comp)
    if path is None:
        return {"ok": False, "reason": "branching_cut"}
    length = sum(e.calc_length() for e in comp)
    arc = [0.0]
    for i in range(1, len(path)):
        arc.append(arc[-1] + (path[i].co - path[i - 1].co).length)
    total = arc[-1]
    ramp = {v: (1.0 if closed else min(1.0, min(arc[i], total - arc[i]) / RAMP)) for i, v in enumerate(path)}
    if closed and _fences_patch(bm, comp):
        return {"ok": False, "reason": "patch", "length_m": round(length, 4)}
    hole_paths = [_order(h)[0] for h in holes]
    for hp in hole_paths:
        for v in hp:
            ramp[v] = 1.0
    every = list(path) + [v for hp in hole_paths for v in hp]
    t_path = {v: tv.get(v, start) for v in every}
    on_path = set(every)
    bmesh.ops.split_edges(bm, edges=list(comp) + [e for h in holes for e in h])
    # every boundary point now is a copy of a path point; its side is the side of the faces round it
    pos_key = {tuple(round(c, 7) for c in v.co): v for v in every}
    arm_side, body_side = {}, {}
    for v in bm.verts:
        if not v.is_boundary:
            continue
        src = pos_key.get(tuple(round(c, 7) for c in v.co))
        if src is None:
            continue
        other = [x.co for f in v.link_faces for x in f.verts if not x.is_boundary]
        if not other:
            other = [f.calc_center_median() for f in v.link_faces]
        side = sum(((o - pc).dot(pn) for o in other)) / len(other)
        (arm_side if side > 0 else body_side)[src] = v
    ends = [] if closed else [path[0], path[-1]]
    for v in ends:  # an end of an open cut is not doubled: it belongs to both caps
        arm_side[v] = body_side[v] = v
    missing = [v for v in every if v not in arm_side or v not in body_side]
    if missing:
        return {"ok": False, "reason": "unsorted_copies", "count": len(missing)}
    for copies in (arm_side, body_side):
        for src, v in copies.items():
            tv[v] = t_path[src]
    # open the gap: clear a slab of half-width gap/2 round the plane inside the cut's outline. Every copy, and every
    # other surface point inside the slab there (a cloth layer lying along the plane, a grazing fold), moves straight
    # off the plane to its own side; the slab narrows to nothing at the cut's ends, which stay put.
    moved = _clear_slab(bm, path, hole_paths, arm_side, body_side, ends, ramp, pc, pn, gap, tv, start)
    released = _release(bm, arm_side, body_side, dl, groups, arm_names, set(ends), pc, pn, tv, start, ash)
    # flat caps
    faces = []
    info = {"ok": True, "closed": bool(closed), "points": len(path), "length_m": round(length, 4), "released": released,
            "moved_points": moved,
            "t_range": [round(min(t_path.values()), 3), round(max(t_path.values()), 3)]}
    if not closed:
        info["new_armpit_m"] = round((path[0].co - path[-1].co).length, 4)
    info["holes"] = len(hole_paths)
    faces, uv2d, why = _flat_caps(bm, path, hole_paths, arm_side, body_side, ramp, pc, pn, gap, dl)
    if why:
        info["cap_fill"] = why
    info["_uv2d"] = uv2d
    info["_arm_copies"] = [v for v in arm_side.values() if v not in ends]
    info["_body_copies"] = [v for v in body_side.values() if v not in ends]
    info["_ends"] = list(ends)
    faces = [f for f in faces if f is not None]
    info["cap_triangles"] = len(faces)
    info["faces"] = faces
    info["_plane"] = (pc.copy(), pn.copy())
    del on_path
    return info


def _flat_caps(bm, path, hole_paths, arm_side, body_side, ramp, pc, pn, gap, dl):  # pragma: no cover
    """Both caps from one constrained Delaunay triangulation of the cut's outline (holes left open) with inner points
    at the cut's own edge spacing, so the two caps are parallel copies and their triangles stay small enough to bend
    near the new armpit. Inner points take their weights from the nearest edge points of their own side.
    -> (faces, {vert: (u, v) metres on the plane}, reason or None)."""
    from mathutils import Vector, geometry

    to2, _ = _plane_frame(path, pc, pn)
    c0, ax_u, ax_v, nn = to2.frame
    loops_src = [list(path)] + [list(hp) for hp in hole_paths]
    L2 = [to2([v.co[:] for v in lp]) for lp in loops_src]
    seg = np.concatenate([np.linalg.norm(np.diff(np.vstack([l_, l_[:1]]), axis=0), axis=1) for l_ in L2])
    spacing = float(min(0.05, max(CAP_SPACING_MIN, 2.0 * np.median(seg))))
    G = cap_grid(L2, spacing)
    pts = np.vstack(L2 + [G]) if len(G) else np.vstack(L2)
    edges, off = [], 0
    for l_ in L2:
        n = len(l_)
        edges += [(off + i, off + (i + 1) % n) for i in range(n)]
        off += n
    loop_faces, off = [], 0
    for l_ in L2:
        ids = list(range(off, off + len(l_)))
        # every loop goes in anticlockwise: the triangulator counts a clockwise face as covering the outside
        loop_faces.append(ids if signed_area(l_) > 0 else ids[::-1])
        off += len(l_)
    out = geometry.delaunay_2d_cdt([Vector(p) for p in pts], edges, loop_faces, 0, 1e-9, True)
    o_verts, o_faces, o_orig, o_face_src = out[0], out[2], out[3], out[5]
    flat_src = [v for lp in loops_src for v in lp]
    faces = []
    uv2d = {}
    if any(len(o) == 0 for o in o_orig) or len(o_verts) != len(pts):
        # the outline crosses itself: fall back to a plain fill
        for copies in (arm_side, body_side):
            loops = [[copies[v] for v in lp] for lp in loops_src]
            flat_v = [v for lp in loops for v in lp]
            for i, j, k in geometry.tessellate_polygon([[v.co for v in lp] for lp in loops]):
                faces.append(_face(bm, (flat_v[i], flat_v[j], flat_v[k])))
            for v, p in zip(flat_v, np.vstack(L2)):
                uv2d[v] = (float(p[0]), float(p[1]))
        return [f for f in faces if f is not None], uv2d, "plain_fill"
    idx = [o[0] for o in o_orig]  # output vertex -> input index
    # inside the outline and outside every hole: the triangle lies in an odd number of the input loops
    tri = [[idx[k] for k in f] for f, src in zip(o_faces, o_face_src) if len(src) % 2 == 1]
    B2 = np.vstack(L2)
    for copies, sign in ((arm_side, 1.0), (body_side, -1.0)):
        bverts = [copies[v] for v in flat_src]
        rmp = [ramp.get(v, 1.0) for v in flat_src]
        allv = list(bverts)
        for p in G:
            d = np.linalg.norm(B2 - p, axis=1)
            near = np.argsort(d)[:4]
            r = float(rmp[near[0]])
            co = c0 + ax_u * p[0] + ax_v * p[1] + nn * (sign * gap / 2 * r)
            nv = bm.verts.new(Vector(co))
            w = 1.0 / np.maximum(d[near], 1e-6)
            w /= w.sum()
            acc = {}
            for wk, k in zip(w, near):
                for g, x in bverts[k][dl].items():
                    acc[g] = acc.get(g, 0.0) + wk * x
            for g, x in acc.items():
                nv[dl][g] = x
            allv.append(nv)
        for i, j, k in tri:
            faces.append(_face(bm, (allv[i], allv[j], allv[k])))
        for v, p in zip(allv, pts):
            uv2d[v] = (float(p[0]), float(p[1]))
    return [f for f in faces if f is not None], uv2d, None


def _plane_frame(path, pc, pn):  # pragma: no cover
    """2D coordinates on the cut plane: -> (to2(P) -> (n, 2), outline polygon (m, 2))."""
    c0 = np.array(pc[:])
    nn = np.array(pn[:])
    P = np.array([v.co[:] for v in path]) - c0
    _, _, vt = np.linalg.svd(P, full_matrices=False)
    ax_u = vt[0] - nn * (vt[0] @ nn)
    ax_u /= np.linalg.norm(ax_u)
    ax_v = np.cross(nn, ax_u)

    def to2(Q):
        Q = np.asarray(Q, dtype=float) - c0
        return np.c_[Q @ ax_u, Q @ ax_v]

    to2.frame = (c0, ax_u, ax_v, nn)
    return to2, to2(np.array([v.co[:] for v in path]))


def _clear_slab(bm, path, hole_paths, arm_side, body_side, ends, ramp, pc, pn, gap, tv, start):  # pragma: no cover
    from mathutils.kdtree import KDTree

    to2, poly = _plane_frame(path, pc, pn)
    every = list(path) + [v for hp in hole_paths for v in hp]
    kd = KDTree(len(every))
    for i, v in enumerate(every):
        kd.insert(v.co, i)
    kd.balance()
    half = gap / 2
    side_of = {}
    for src, v in arm_side.items():
        side_of[v] = (1.0, src)
    for src, v in body_side.items():
        side_of[v] = (-1.0, src)
    moved = 0
    cand = [v for v in bm.verts if v in side_of or (tv.get(v, 0.0) > start and abs((v.co - pc).dot(pn)) < half + SLAB_MARGIN)]
    if not cand:
        return 0
    near_out = [kd.find(v.co)[2] < 0.01 for v in cand]
    inside = inside_polygon(to2([v.co[:] for v in cand]), poly)
    for v, ins, near in zip(cand, inside, near_out):
        if v in side_of:
            sign, src = side_of[v]
            if src in ends:
                continue
            h = half * ramp[src]
            d = (v.co - pc).dot(pn)
            v.co += pn * (sign * h - d)
            moved += 1
            continue
        if not (ins or near):
            continue
        _, j, _ = kd.find(v.co)
        h = half * ramp[every[j]]
        if h <= 0:
            continue
        d = (v.co - pc).dot(pn)
        if abs(d) >= h + SLAB_MARGIN:
            continue
        sign = 1.0 if d > 0 else -1.0
        v.co += pn * (sign * (h + SLAB_MARGIN) - d)
        moved += 1
    return moved


def _inner_loops(bm, comp, pc, pn, tv, start):  # pragma: no cover
    """Cut the plane through every face below `start` that crosses it inside the outline of the cut piece `comp`,
    and return the closed loops found inside (holes of the cap), or (None, reason) when one does not close."""
    import bmesh

    path, closed = _order(comp)
    if path is None:
        return None, "branching_cut"
    _, _, vt = np.linalg.svd(np.array([v.co[:] for v in path]) - np.array(pc[:]), full_matrices=False)
    ax_u, ax_v = vt[0], vt[1]
    nn = np.array(pn[:])
    ax_u = ax_u - nn * (ax_u @ nn)
    ax_u /= np.linalg.norm(ax_u)
    ax_v = np.cross(nn, ax_u)
    c0 = np.array(pc[:])

    def to2(P):
        P = np.asarray(P, dtype=float) - c0
        return np.c_[P @ ax_u, P @ ax_v]

    poly = to2([v.co[:] for v in path])
    outer = set(comp)
    on_plane = 1e-5
    cand = []
    for f in bm.faces:
        ds = [(v.co - pc).dot(pn) for v in f.verts]
        if min(ds) > on_plane or max(ds) < -on_plane:
            continue
        if any(tv.get(v, 0.0) <= start for v in f.verts):
            continue
        cand.append(f)
    if cand:
        cen = to2([f.calc_center_median()[:] for f in cand])
        cand = [f for f, ok in zip(cand, inside_polygon(cen, poly)) if ok]
    if cand:
        geom = list({x for f in cand for x in (f, *f.edges, *f.verts)})
        bmesh.ops.bisect_plane(bm, geom=geom, dist=1e-6, plane_co=pc, plane_no=pn)
        for v in bm.verts:
            if v not in tv:
                nb = [tv[w] for w in (e.other_vert(v) for e in v.link_edges) if w in tv]
                tv[v] = float(np.mean(nb)) if nb else start
    plane_edges = [e for e in bm.edges if e not in outer and all(abs((v.co - pc).dot(pn)) < on_plane for v in e.verts)
                   and len(e.link_faces) == 2 and all(tv.get(v, 0.0) > start for v in e.verts)]
    if plane_edges:
        mid = to2([((e.verts[0].co + e.verts[1].co) / 2)[:] for e in plane_edges])
        plane_edges = [e for e, ok in zip(plane_edges, inside_polygon(mid, poly)) if ok]
    holes = []
    for cc in _components(plane_edges):
        hp, hclosed = _order(cc)
        if hp is None or not hclosed:
            return None, "inner_cut_not_closed"
        if len(hp) >= 3:
            holes.append(cc)
    return holes, None


def _fences_patch(bm, comp):  # pragma: no cover
    """True when a closed cut line fences off a small patch (it would come away as a loose piece), not a neck between
    two parts (a hand grown onto a hip: cutting round the neck leaves both sides joined to the body)."""
    wall = set(comp)
    f0 = next(iter(comp[0].link_faces))
    seen, stack = {f0}, [f0]
    while stack:
        f = stack.pop()
        for e in f.edges:
            if e in wall:
                continue
            for g in e.link_faces:
                if g not in seen:
                    seen.add(g)
                    stack.append(g)
    n = len(bm.faces)
    if len(seen) == n:
        return False  # both sides still meet elsewhere: a neck
    return min(len(seen), n - len(seen)) < PATCH_SHARE * n


def _face(bm, vs):  # pragma: no cover
    if len(set(vs)) < 3:
        _FACE_FAIL["same_point"] = _FACE_FAIL.get("same_point", 0) + 1
        return None
    try:
        return bm.faces.new(vs)
    except ValueError as e:
        k = str(e)[:60]
        _FACE_FAIL[k] = _FACE_FAIL.get(k, 0) + 1
        return None


def _release(bm, arm_side, body_side, dl, groups, arm_names, ends, pc, pn, tv, start, ash):  # pragma: no cover
    """Each side of the cut lets go of the other side's weights over the whole fused strip: from the cut copies,
    flood over points that still carry some of the other side's weight (or lie within RELEASE_RINGS of the cut), never
    through the cut's ends. Near the new armpit (t from `start` to start + RELEASE_RAMP) the change fades in, so the
    shoulder keeps its blend. Runs before the caps exist, so each side only reaches its own surface."""
    from mathutils.kdtree import KDTree

    is_arm = [g in arm_names for g in groups]
    cut_pts = [v.co.copy() for v in set(arm_side.values())]
    kd = KDTree(len(cut_pts))
    for i, c in enumerate(cut_pts):
        kd.insert(c, i)
    kd.balance()

    def on_arm_side(w):
        if kd.find(w.co)[2] < RELEASE_NEAR:
            return (w.co - pc).dot(pn) > 0
        return w[ash] > ISO

    out = {}
    for copies, keep_arm in ((arm_side, True), (body_side, False)):
        seeds = sorted({v for v in copies.values() if v not in ends}, key=lambda v: tuple(v.co))
        ring = {v: 0 for v in seeds}
        stack = deque(seeds)  # breadth first, so `ring` is the true number of rings from the cut
        while stack:
            v = stack.popleft()
            for e in v.link_edges:
                w = e.other_vert(v)
                if w in ring or w in ends or tv.get(w, 0.0) <= start or on_arm_side(w) != keep_arm:
                    continue
                d = w[dl]
                tot = sum(d.values()) or 1.0
                other = sum(x for g, x in d.items() if is_arm[g] != keep_arm) / tot
                if other < 0.02 and ring[v] + 1 > RELEASE_RINGS:
                    continue
                ring[w] = ring[v] + 1
                stack.append(w)
        n = 0
        for v in ring:
            d = v[dl]
            keep = {g: w for g, w in d.items() if is_arm[g] == keep_arm}
            tot = sum(keep.values())
            if tot <= 1e-6:
                continue
            s = min(1.0, max(0.0, (tv.get(v, start) - start) / RELEASE_RAMP))
            old = dict(d.items())
            new = {g: (1 - s) * old.get(g, 0.0) + s * keep.get(g, 0.0) / tot for g in set(old) | set(keep)}
            for g in list(d.keys()):
                del d[g]
            for g, w in new.items():
                if w > 1e-6:
                    d[g] = w
            n += 1
        out["arm" if keep_arm else "body"] = n
    return out


def _rederive(bm, dl, groups, arm_names, chain, caps, start):  # pragma: no cover
    """WP-27: the weights of a cut arm, derived again. The arm's own surface is every point reached from the arm side of
    its cuts without crossing a cut (near a cut a point's side is its side of the plane), the shoulder (t below `start`)
    or a cut's end. On it only the arm's bones stay; everywhere else this arm's bones go (hair or robe that took arm
    weight from the 3D build's fusing). Points left with nothing of their side take their neighbours' weights. Then
    each side is smoothed on its own (the volume limit holds any point that would move too far). The cut's ends are on
    neither surface and keep their blend. Blending both sides round the ends as well made the raise stretch more on the
    WP-25 cases (mage left 32 against 28 without), so it is not done. Four influences, summing to one."""
    from mathutils import Vector
    from mathutils.kdtree import KDTree

    cut = [cp for cp in caps if cp.get("ok") and cp.get("_arm_copies")]
    if not cut:
        return {}
    bm.verts.index_update()
    bm.verts.ensure_lookup_table()
    n = len(bm.verts)
    P = np.array([v.co[:] for v in bm.verts])
    t, _ = chain_param(P, chain)
    nbrs = [[e.other_vert(v).index for e in v.link_edges] for v in bm.verts]
    is_arm = np.array([g in arm_names for g in groups], dtype=bool)
    ends = sorted({v.index for cp in cut for v in cp["_ends"] if v.is_valid})
    pts, planes = [], []
    for k, cp in enumerate(cut):
        for v in cp["_arm_copies"] + cp["_body_copies"]:
            if v.is_valid:
                pts.append(v.co.copy())
                planes.append(k)
    kd = KDTree(len(pts))
    for i, c in enumerate(pts):
        kd.insert(c, i)
    kd.balance()
    side = np.zeros(n)
    near = np.zeros(n, dtype=bool)
    for v in bm.verts:
        _, j, d = kd.find(v.co)
        pc, pn = cut[planes[j]]["_plane"]
        side[v.index] = (v.co - pc).dot(pn)
        near[v.index] = d < RELEASE_NEAR
    W0 = [dict(v[dl].items()) for v in bm.verts]
    share0 = arm_share(W0, groups, arm_names)
    # near a cut a point's side is its side of the plane; further out the arm's surface ends where its weights do
    allowed = (t > start) & np.where(near, side > 0, share0 > ARM_SURFACE_SHARE)
    allowed[ends] = False
    seeds = [v.index for cp in cut for v in cp["_arm_copies"] if v.is_valid]
    seeds += [v.index for cp in cut for f in cp.get("faces", []) if f.is_valid for v in f.verts
              if (v.co - cp["_plane"][0]).dot(cp["_plane"][1]) > 0]
    in_arm = flood(sorted(set(seeds)), nbrs, allowed)
    rep = {"arm_surface_points": int(in_arm.sum())}
    if in_arm.sum() > LEAK_SHARE * n:  # the arm's surface ran into the body: leave the weights as the cut made them
        rep["reason"] = "arm_surface_leaked"
        return rep
    W, share_before = W0, share0
    W, changed, need = keep_side(W, is_arm, in_arm, t, start)
    W, unfilled = fill_from_neighbours(W, need, nbrs, in_arm)
    region = set(changed) | set(need)
    for _ in range(WEIGHT_RINGS):
        region |= {j for i in region for j in nbrs[i] if t[j] > start}
    W, held = smooth_weights(W, region, nbrs, in_arm)
    for v in bm.verts:
        d = v[dl]
        new = top_influences(W[v.index])
        if not new:
            continue
        for g in list(d.keys()):
            del d[g]
        for g, w in new.items():
            d[g] = w
    share_after = arm_share([dict(v[dl].items()) for v in bm.verts], groups, arm_names)
    off_arm = ~in_arm & (t > start + RELEASE_RAMP)
    rep.update(released=len(changed), filled=len(need) - unfilled, unfilled=unfilled, smoothed=len(region),
               held_by_limit=held,
               arm_weight_off_arm_before=int((share_before[off_arm] > 0.02).sum()),
               arm_weight_off_arm_after=int((share_after[off_arm] > 0.02).sum()))
    return rep


def barycentric(p, a, b, c):
    """Barycentric weights (3,) of point p's projection on triangle abc."""
    p, a, b, c = (np.asarray(x, float) for x in (p, a, b, c))
    v0, v1, v2 = b - a, c - a, p - a
    d00, d01, d11 = v0 @ v0, v0 @ v1, v1 @ v1
    d20, d21 = v2 @ v0, v2 @ v1
    den = d00 * d11 - d01 * d01
    if abs(den) < 1e-18:
        return np.array([1.0, 0.0, 0.0])
    v = (d11 * d20 - d01 * d21) / den
    w = (d00 * d21 - d01 * d20) / den
    bc = np.clip(np.array([1.0 - v - w, v, w]), 0.0, 1.0)
    return bc / bc.sum()


def _rest_source(bm, rest_off):  # pragma: no cover
    """(BVH tree of the input triangles, their corner points (n, 3, 3), their corner rest offsets (n, 3, 3))."""
    from mathutils.bvhtree import BVHTree

    bm.verts.index_update()
    tris = [[v.index for v in f.verts] for f in bm.faces]
    co = [v.co.copy() for v in bm.verts]
    off = np.array([v[rest_off][:] for v in bm.verts])
    P = np.array([c[:] for c in co])
    T = np.array(tris)
    return BVHTree.FromPolygons(co, tris, all_triangles=True), P[T], off[T]


def rest_offsets(points, rest_src):  # pragma: no cover
    """Each point's offset into the rig's rest pose: that of the nearest place on the input surface."""
    tree, corners, offs = rest_src
    out = np.zeros((len(points), 3))
    from mathutils import Vector

    for i, p in enumerate(points):
        hit = tree.find_nearest(Vector(p))
        if hit[2] is None:
            continue
        k = hit[2]
        bc = barycentric(hit[0][:], *corners[k])
        out[i] = bc @ offs[k]
    return out


def _check(bm):  # pragma: no cover
    import bmesh
    from mathutils.bvhtree import BVHTree

    c = bm
    c.verts.ensure_lookup_table()
    c.faces.ensure_lookup_table()
    open_e = sum(1 for e in c.edges if len(e.link_faces) == 1)
    bad_e = sum(1 for e in c.edges if len(e.link_faces) > 2)
    loose = sum(1 for v in c.verts if not v.link_faces)
    seen, shells = set(), 0
    for v in c.verts:
        if v in seen:
            continue
        shells += 1
        st = [v]
        seen.add(v)
        while st:
            x = st.pop()
            for e in x.link_edges:
                w = e.other_vert(x)
                if w not in seen:
                    seen.add(w)
                    st.append(w)
    tree = BVHTree.FromBMesh(c, epsilon=0.0)
    pairs = tree.overlap(tree)
    cross = set()
    for i, j in pairs:
        if i >= j:
            continue
        fi, fj = c.faces[i], c.faces[j]
        if set(fi.verts) & set(fj.verts):
            continue
        cross.add((i, j))
    sliver = 0
    for f in c.faces:
        a = f.calc_area()
        s = sum(e.calc_length() ** 2 for e in f.edges)
        if s > 0 and 4 * math.sqrt(3) * a / s < 0.05:
            sliver += 1
    out = {"open_edges": open_e, "bad_edges": bad_e, "loose_points": loose, "shells": shells,
           "crossing_pairs": len(cross), "slivers": sliver, "triangles": len(c.faces),
           "crossing_faces": sorted({k for p in cross for k in p}), "_pairs": sorted(cross)}
    return out


def _debug_colours(bm, me, report, crossing):  # pragma: no cover
    """Material 1 (red) on cap faces, 2 (yellow) on faces crossing others: for looking at a run."""
    import bpy

    for name, col in (("dbg_cap", (0.9, 0.1, 0.1, 1)), ("dbg_cross", (1.0, 0.9, 0.0, 1))):
        m = bpy.data.materials.new(name)
        m.diffuse_color = col
        m.use_nodes = True
        m.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = col
        me.materials.append(m)
    k = len(me.materials)
    for f in (f for s in report["sides"].values() for cp in s.get("_caps", []) for f in cp.get("faces", [])):
        if f.is_valid:
            f.material_index = k - 2
    for f in crossing:
        if f.is_valid:
            f.material_index = k - 1


def texel_density(uv_tris, xyz_tris):
    """Median texture units per metre over triangles: sqrt(UV area / surface area). uv_tris (n, 3, 2), xyz (n, 3, 3)."""
    uv_tris, xyz_tris = np.asarray(uv_tris, dtype=float), np.asarray(xyz_tris, dtype=float)
    e1, e2 = uv_tris[:, 1] - uv_tris[:, 0], uv_tris[:, 2] - uv_tris[:, 0]
    ua = 0.5 * np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0])
    xa = 0.5 * np.linalg.norm(np.cross(xyz_tris[:, 1] - xyz_tris[:, 0], xyz_tris[:, 2] - xyz_tris[:, 0]), axis=1)
    ok = (xa > 1e-10) & (ua > 1e-14)
    return float(np.median(np.sqrt(ua[ok] / xa[ok]))) if ok.any() else 0.0


def sample_covered(px, covered, uv, radius=SAMPLE_RADIUS_PX):
    """The mean colour of the texels round texture point uv (0..1, v up) that the surface covers (`covered`, (h, w)
    bool, row 0 at v 0), so a read at an island's border never takes the empty atlas beside it. With none in reach, the
    texel under the point."""
    h, w = covered.shape
    xi, yi = int(math.floor(float(uv[0]) * w)), int(math.floor(float(uv[1]) * h))
    for r in (radius, 2 * radius, 4 * radius):
        y0, y1, x0, x1 = max(0, yi - r), min(h, yi + r + 1), max(0, xi - r), min(w, xi + r + 1)
        if y1 <= y0 or x1 <= x0:
            continue
        m = covered[y0:y1, x0:x1]
        if m.any():
            return px[y0:y1, x0:x1][m].mean(axis=0)
    return px[min(h - 1, max(0, yi)), min(w - 1, max(0, xi))]


def sample_many(px, covered, uvs):
    """The texel under each texture point (n, 2), or where that texel is not on the surface, sample_covered's read."""
    h, w = covered.shape
    xi = np.clip(np.floor(uvs[:, 0] * w).astype(int), 0, w - 1)
    yi = np.clip(np.floor(uvs[:, 1] * h).astype(int), 0, h - 1)
    out = px[yi, xi].astype(np.float64)
    for k in np.nonzero(~covered[yi, xi])[0]:
        out[k] = sample_covered(px, covered, uvs[k], radius=1)
    return out


def idw_colour(pts, ring_xyz, ring_col, k=IDW_NEAREST, power=2.0, chunk=4096):
    """Colours for points (n, 3) blended from the ring's colours by inverse distance over the k nearest ring points."""
    pts, ring_xyz, ring_col = (np.asarray(x, float) for x in (pts, ring_xyz, ring_col))
    k = min(k, len(ring_xyz))
    out = np.empty((len(pts), ring_col.shape[1]))
    for s in range(0, len(pts), chunk):
        d = np.linalg.norm(pts[s:s + chunk, None, :] - ring_xyz[None, :, :], axis=2)
        nn = np.argpartition(d, k - 1, axis=1)[:, :k]
        dn = np.take_along_axis(d, nn, axis=1)
        wt = 1.0 / np.maximum(dn, 1e-5) ** power
        wt /= wt.sum(axis=1, keepdims=True)
        out[s:s + chunk] = (wt[..., None] * ring_col[nn]).sum(axis=1)
    return out


def dilate_fill(px, mask, rings):
    """Grow the pixels of `mask` outward by `rings` pixels, each new pixel the mean of its filled 8-neighbours, so
    texture filtering at a cap's edge does not pull in the atlas round it. px (h, w, c) in place -> the grown mask."""
    H, W = mask.shape
    ys_, xs_ = np.nonzero(mask)
    if not len(ys_):
        return mask.copy()
    y0, y1 = max(0, ys_.min() - rings - 1), min(H, ys_.max() + rings + 2)
    x0, x1 = max(0, xs_.min() - rings - 1), min(W, xs_.max() + rings + 2)
    full = px
    px = full[y0:y1, x0:x1]  # a view: writes land in the picture
    done = mask[y0:y1, x0:x1].copy()
    h, w = done.shape
    for _ in range(int(rings)):
        acc = np.zeros(px.shape, dtype=np.float64)
        cnt = np.zeros((h, w))
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if not dy and not dx:
                    continue
                # neighbour at (y + dy, x + dx) seen from (y, x)
                ys, yd = (slice(dy, h), slice(0, h - dy)) if dy >= 0 else (slice(0, h + dy), slice(-dy, h))
                xs, xd = (slice(dx, w), slice(0, w - dx)) if dx >= 0 else (slice(0, w + dx), slice(-dx, w))
                m = done[ys, xs]
                acc[yd, xd] += np.where(m[..., None], px[ys, xs], 0.0)
                cnt[yd, xd] += m
        new = (cnt > 0) & ~done
        px[new] = (acc[new] / cnt[new][:, None]).astype(px.dtype)
        done |= new
    out = mask.copy()
    out[y0:y1, x0:x1] |= done
    return out


def raster_cover(tris_px, w, h):
    """Pixels whose centre lies in any triangle (n, 3, 2) in pixel units (x right, y down) -> (rows, cols, tri,
    barycentric (k, 3))."""
    rows, cols, tid, bary = [], [], [], []
    for t, ((ax, ay), (bx, by), (cx, cy)) in enumerate(np.asarray(tris_px, dtype=float)):
        x0, x1 = max(0, int(math.floor(min(ax, bx, cx)))), min(w, int(math.ceil(max(ax, bx, cx))) + 1)
        y0, y1 = max(0, int(math.floor(min(ay, by, cy)))), min(h, int(math.ceil(max(ay, by, cy))) + 1)
        den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if x1 <= x0 or y1 <= y0 or abs(den) < 1e-12:
            continue
        X, Y = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
        l0 = ((by - cy) * (X - cx) + (cx - bx) * (Y - cy)) / den
        l1 = ((cy - ay) * (X - cx) + (ax - cx) * (Y - cy)) / den
        l2 = 1 - l0 - l1
        m = (l0 >= -1e-6) & (l1 >= -1e-6) & (l2 >= -1e-6)
        rows.append(Y[m].astype(int))
        cols.append(X[m].astype(int))
        tid.append(np.full(int(m.sum()), t))
        bary.append(np.c_[l0[m], l1[m], l2[m]])
    if not rows:
        return np.zeros(0, int), np.zeros(0, int), np.zeros(0, int), np.zeros((0, 3))
    return np.concatenate(rows), np.concatenate(cols), np.concatenate(tid), np.vstack(bary)


def _cap_uvs(bm, me, mo, caps):  # pragma: no cover
    """Texture space for the caps in the atlas's free space (uvfit.place_islands, every existing piece stays put), at
    the surface's own texel density (smaller if the free space is short), and their colour from the surface round
    their edge (WP-27): each edge point's colour is read just inside the surface triangles beside it, from texels the
    surface really covers (never the empty atlas round an island), and each cap pixel blends the colours of the edge
    points nearest it by inverse distance, so a cap follows its ring where the ring changes colour. Every picture of
    the material is filled the same way; normal maps get a flat normal. Cap faces are shaded flat. The report lists
    each cap's texture triangles (so the paint tools can find the caps) and how far its colour at the ring is from
    the surface beside it."""
    import uvfit

    uvl = bm.loops.layers.uv.active
    if uvl is None or not caps:
        return {"placed": 0, "reason": "no_uv" if uvl is None else "no_caps"}
    capset = set()
    islands = []  # (faces, {vert: (u, v) metres}, side, "arm" or "body")
    planes = []  # each island's cut plane and which side of it the island faces
    for cp in caps:
        fs = [f for f in cp.get("faces", []) if f.is_valid]
        if not fs or "_plane" not in cp:
            continue
        pc, pn = cp["_plane"]
        uv2d = cp.get("_uv2d", {})
        for sign in (1, -1):
            side = [f for f in fs if ((f.calc_center_median() - pc).dot(pn) > 0) == (sign > 0)]
            if side:
                islands.append((side, uv2d, cp.get("_side"), "arm" if sign > 0 else "body"))
                planes.append((pc, pn, sign))
        capset.update(fs)
    for f in capset:
        f.smooth = False  # a cap's normal is its own: flat, not blended with the surface round it
    surf = [f for f in bm.faces if f not in capset]
    uv_t = np.array([[l[uvl].uv[:] for l in f.loops] for f in surf])
    xyz_t = np.array([[l.vert.co[:] for l in f.loops] for f in surf])
    k = texel_density(uv_t, xyz_t)
    out = {"islands": len(islands), "density_uv_per_m": round(k, 3)}
    scale, placed = 1.0, None
    while scale >= 0.2:
        isl_tris = []
        for fs, uv2d, _, _ in islands:
            isl_tris.append(np.array([[np.array(uv2d.get(l.vert, (0.0, 0.0))) * k * scale for l in f.loops]
                                      for f in fs]))
        placed = uvfit.place_islands(uv_t, isl_tris, res=1024, pad_cells=2)
        if placed is not None:
            break
        scale *= 0.7
    if placed is None:
        out["reason"] = "no_room"
        return out
    out["scale"] = round(scale, 3)
    new_uv = []
    for (fs, _, _, _), tris, pl in zip(islands, isl_tris, placed):
        moved = uvfit.apply_placement(tris, pl)
        for f, tuv in zip(fs, moved):
            for l, uv in zip(f.loops, tuv):
                l[uvl].uv = (float(uv[0]), float(uv[1]))
        new_uv.append(moved)
    out["cap_islands"] = [{"side": sd, "of": of, "triangles": len(fs), "uv_tris": np.round(muv, 6).tolist()}
                          for (fs, _, sd, of), muv in zip(islands, new_uv)]
    # each island's ring: its edge points, their position, where to read the surface colour beside each, and where
    # the point sits on the cap
    rings = []
    for fs, _, _, _ in islands:
        reads_of = {}
        for f in fs:
            for v in f.verts:
                if v in reads_of:
                    continue
                reads = []
                for l in v.link_loops:
                    if l.face in capset:
                        continue
                    cen = np.mean([x[uvl].uv[:] for x in l.face.loops], axis=0)
                    uv = np.array(l[uvl].uv[:])
                    reads.append(uv + RING_INSET * (cen - uv))
                if reads:
                    reads_of[v] = reads
        cap_uv = {}
        for f in fs:
            for l in f.loops:
                if l.vert in reads_of:
                    cap_uv[l.vert] = l[uvl].uv[:]
        rv = sorted(reads_of, key=lambda v: tuple(v.co))
        rings.append((np.array([v.co[:] for v in rv]).reshape(-1, 3), [reads_of[v] for v in rv],
                      np.array([cap_uv[v] for v in rv]).reshape(-1, 2)))
    mirrors = [_mirror_source(bm, fs, capset, surf, uvl, *pl) for (fs, _, _, _), pl in zip(islands, planes)]
    imgs = _material_images(mo)
    out["images"] = {}
    cover = {}
    mirror_uv = {}
    ring_report = [{"ring_points": len(r[1])} for r in rings]
    base_name = next((im.name for im, nrm in imgs if "base" in im.name.lower()), imgs[0][0].name if imgs else None)
    for img, is_normal in imgs:
        w, h = img.size
        if not w or not h:
            continue
        px = np.empty(w * h * img.channels, dtype=np.float32)
        img.pixels.foreach_get(px)
        px = px.reshape(h, w, img.channels)  # row 0 = v 0 (bottom)
        if (w, h) not in cover:
            r_, c_, _, _ = raster_cover(np.stack([uv_t[..., 0] * w, uv_t[..., 1] * h], axis=-1), w, h)
            m = np.zeros((h, w), bool)
            m[r_, c_] = True
            cover[(w, h)] = m
        covered = cover[(w, h)]
        filled = 0
        for n_isl, ((fs, _, _, _), muv, (r_xyz, r_reads, r_cap_uv)) in enumerate(zip(islands, new_uv, rings)):
            rows, cols, tid, bary = raster_cover(np.stack([muv[..., 0] * w, muv[..., 1] * h], axis=-1), w, h)
            if not len(rows) or not len(r_xyz):
                continue
            mask = np.zeros((h, w), bool)
            mask[rows, cols] = True
            if is_normal:
                px[rows, cols] = np.array([0.5, 0.5, 1.0, 1.0][:img.channels], dtype=np.float32)
            else:
                ring_col = np.array([np.mean([sample_covered(px, covered, uv) for uv in reads], axis=0)
                                     for reads in r_reads])
                P3 = np.array([[l.vert.co[:] for l in f.loops] for f in fs])
                pts = (bary[:, :, None] * P3[tid]).sum(axis=1)
                col = idw_colour(pts, r_xyz, ring_col)
                if (n_isl, w, h) not in mirror_uv:
                    mirror_uv[(n_isl, w, h)] = _mirror_uvs(pts, mirrors[n_isl])
                muv_s, ok = mirror_uv[(n_isl, w, h)]
                if ok.any():  # the surface's own detail, mirrored across the cut onto the cap, within a small swing
                    col[ok] += np.clip(sample_many(px, covered, muv_s[ok]) - col[ok], -MIRROR_SWING, MIRROR_SWING)
                    ring_report[n_isl]["mirrored_share"] = round(float(ok.mean()), 3)
                px[rows, cols] = col
            dilate_fill(px, mask, CAP_PAD_PX)
            if not is_normal and img.name == base_name:
                # the cap's colour at each edge point (the picture as it will be read there) against the surface's
                got = np.array([px[min(h - 1, int(v_ * h)), min(w - 1, int(u_ * w))] for u_, v_ in r_cap_uv])
                c3 = ring_col[:, :3]
                ring_report[n_isl].update(
                    ring_colour_diff=round(float(np.abs(got[:, :3] - c3).mean() * 255), 2),
                    ring_colour_spread=round(float(np.abs(c3 - c3.mean(axis=0)).mean() * 255), 2))
            filled += len(rows)
        img.pixels.foreach_set(px.ravel())
        img.update()
        try:
            img.pack()
        except RuntimeError:
            pass
        out["images"][img.name] = {"normal": is_normal, "pixels_filled": int(filled), "size": [w, h]}
    for isl, rr in zip(out["cap_islands"], ring_report):
        isl.update(rr)
    out["placed"] = len(placed)
    return out


def _mirror_source(bm, fs, capset, surf, uvl, pc, pn, sign):  # pragma: no cover
    """What a cap's colour is mirrored from: its ring as segments, and the surface on its own side of the cut near it
    (a BVH over those triangles with their texture points)."""
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    fset = set(fs)
    seg = [e for f in fs for e in f.edges if sum(1 for g in e.link_faces if g in fset) == 1]
    if not seg:
        return None
    A = np.array([e.verts[0].co[:] for e in seg])
    B = np.array([e.verts[1].co[:] for e in seg])
    cv = np.array([v.co[:] for f in fs for v in f.verts])
    cen = cv.mean(axis=0)
    reach = float(np.linalg.norm(cv - cen, axis=1).max()) + MIRROR_REACH
    near = [f for f in surf if sign * (f.calc_center_median() - pc).dot(pn) > 0
            and (f.calc_center_median() - Vector(cen)).length < reach]
    if not near:
        return None
    co = [v.co.copy() for f in near for v in f.verts]
    polys = [(3 * i, 3 * i + 1, 3 * i + 2) for i in range(len(near))]
    tree = BVHTree.FromPolygons(co, polys, all_triangles=True)
    xyz = np.array([[v.co[:] for v in f.verts] for f in near])
    uv = np.array([[l[uvl].uv[:] for l in f.loops] for f in near])
    return A, B, tree, xyz, uv


def _mirror_uvs(pts, src):  # pragma: no cover
    """For each cap point, the texture point of the surface where its mirror image across the nearest place on the
    ring lands, and whether it landed on the surface (within MIRROR_LAND of it)."""
    from mathutils import Vector

    n = len(pts)
    if src is None or not n:
        return np.zeros((n, 2)), np.zeros(n, dtype=bool)
    A, B, tree, xyz, uv = src
    R = closest_on_segments(pts, A, B)
    M = 2 * R - pts
    out, ok = np.zeros((n, 2)), np.zeros(n, dtype=bool)
    for i, m in enumerate(M):
        hit = tree.find_nearest(Vector(m))
        if hit[2] is None or hit[3] > MIRROR_LAND:
            continue
        bc = barycentric(hit[0][:], *xyz[hit[2]])
        out[i] = bc @ uv[hit[2]]
        ok[i] = True
    return out, ok


def _material_images(mo):  # pragma: no cover
    """(image, is_normal_map) for every image texture in the object's materials."""
    seen, out = set(), []
    for m in mo.data.materials:
        if m is None or not m.use_nodes:
            continue
        for nd in m.node_tree.nodes:
            if nd.type != "TEX_IMAGE" or nd.image is None or nd.image.name in seen:
                continue
            seen.add(nd.image.name)
            is_normal = any(lk.to_node.type == "NORMAL_MAP" for o in nd.outputs for lk in o.links)
            out.append((nd.image, is_normal))
    return out


if __name__ == "__main__":  # pragma: no cover
    main()
