"""Blender headless: separate a limb the 3D build grew into the body (an upper arm into the torso, a sleeve into a coat).

Run: blender -b --python separate.py -- <step4.glb> <labels_rig.glb> <out.glb> --report <json>
         [--side L|R|both] [--gap 0.006] [--start 0.3] [--rigged-out <rig_out.glb>] [--debug]

WP-25, proved on the stored blacksmith (upper arms grown into the torso) and mage (sleeves grown into the robe); the
evidence is in bakeoff/wp25 (local). Steps, per arm:
1. The weight line. Each point's arm share is the summed skin weight of that side's upper arm bone and every bone below
   it (finger bones the app added included), copied from the nearest rig point and smoothed. Where the build fused the
   arm to the body the share falls from 1 to 0 across the fused strip; the line where it crosses one half, beyond
   `start` (a share of the upper arm from the shoulder joint, so the shoulder stays whole), is found per triangle.
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
7. Texture. Caps get texture space of their own (uvfit.place_islands in the atlas's free space, at the surface's texel
   density, smaller if short of room), coloured from the surface along their edge; normal maps get a flat normal.
8. Checks against the input: open edges, edges with three or more faces, pieces, and triangle pairs crossing that did
   not cross before. `ok` is false when any of them got worse.
No booleans and no remesh: the rest of the surface keeps its points, triangles and texture coordinates. Not handled:
hair that carries arm weight (a weights fault, WP-27), and arms whose weight line never leaves the shoulder (the
low-poly dwarf). Runs are repeatable: every set is walked in an order fixed by position.

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


def main():  # pragma: no cover - runs inside Blender
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
    report = {"ok": False, "sides": {}, "reason": None}

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
    bm = bmesh.new()
    bm.from_mesh(me)
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

    # labels: nearest rig point, weights copied into a deform layer (interpolated by the cuts below)
    rmw = rig_mesh.matrix_world
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
        dv = v[dl]
        for ge in rig_mesh.data.vertices[j].groups:
            if ge.weight > 0:
                dv[ge.group] = ge.weight
    report["input"]["points_off_rig"] = int(far)

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
    for side in sides:
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
        rep["caps"] = [{k: v for k, v in cp.items() if k != "faces" and not k.startswith("_")} for cp in caps]
        rep.setdefault("_caps", caps)

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
    the surface's own texel density (smaller if the free space is short), and their colour from the surface along
    their edge: each cap pixel takes the mean colour of the three nearest edge points of its cap, sampled where the
    surface meets the cap. Normal maps get a flat normal there."""
    import uvfit

    uvl = bm.loops.layers.uv.active
    if uvl is None or not caps:
        return {"placed": 0, "reason": "no_uv" if uvl is None else "no_caps"}
    capset = set()
    islands = []  # (faces, {vert: (u, v) metres})
    for cp in caps:
        fs = [f for f in cp.get("faces", []) if f.is_valid]
        if not fs or "_plane" not in cp:
            continue
        pc, pn = cp["_plane"]
        uv2d = cp.get("_uv2d", {})
        for sign in (1, -1):
            side = [f for f in fs if ((f.calc_center_median() - pc).dot(pn) > 0) == (sign > 0)]
            if side:
                islands.append((side, uv2d))
        capset.update(fs)
    surf = [f for f in bm.faces if f not in capset]
    uv_t = np.array([[l[uvl].uv[:] for l in f.loops] for f in surf])
    xyz_t = np.array([[l.vert.co[:] for l in f.loops] for f in surf])
    k = texel_density(uv_t, xyz_t)
    out = {"islands": len(islands), "density_uv_per_m": round(k, 3)}
    scale, placed = 1.0, None
    while scale >= 0.2:
        isl_tris = []
        for fs, uv2d in islands:
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
    for (fs, _), tris, pl in zip(islands, isl_tris, placed):
        moved = uvfit.apply_placement(tris, pl)
        for f, tuv in zip(fs, moved):
            for l, uv in zip(f.loops, tuv):
                l[uvl].uv = (float(uv[0]), float(uv[1]))
        new_uv.append(moved)
    # colour: every image the material uses
    imgs = _material_images(mo)
    out["images"] = {}
    for img, is_normal in imgs:
        w, h = img.size
        if not w or not h:
            continue
        px = np.empty(w * h * img.channels, dtype=np.float32)
        img.pixels.foreach_get(px)
        px = px.reshape(h, w, img.channels)  # row 0 = v 0 (bottom)
        filled = 0
        for (fs, _), muv in zip(islands, new_uv):
            # edge points of this cap: vertices with a surface face, their surface UV and colour
            edge = {}
            for f in fs:
                for v in f.verts:
                    if v in edge:
                        continue
                    for l in v.link_loops:
                        if l.face not in capset:
                            edge[v] = l[uvl].uv.copy()
                            break
            if not edge:
                continue
            ev = list(edge)
            e_xyz = np.array([v.co[:] for v in ev])
            e_uv = np.array([edge[v][:] for v in ev])
            e_col = px[np.clip((e_uv[:, 1] * h).astype(int), 0, h - 1), np.clip((e_uv[:, 0] * w).astype(int), 0, w - 1)]
            tris_px = np.stack([muv[..., 0] * w, muv[..., 1] * h], axis=-1)
            rows, cols, tid, bary = raster_cover(tris_px, w, h)
            if not len(rows):
                continue
            # grow by two pixels so filtering does not pull in the neighbouring atlas colour
            P3 = np.array([[l.vert.co[:] for l in f.loops] for f in fs])
            pts = (bary[:, :, None] * P3[tid]).sum(axis=1)
            mask = np.zeros((h, w), bool)
            mask[rows, cols] = True
            grown = uvfit.grow(mask, 2) & ~mask
            gr, gc = np.nonzero(grown)
            if is_normal:
                col = np.tile(np.array([0.5, 0.5, 1.0, 1.0][:img.channels], dtype=np.float32), (len(rows), 1))
                gcol = np.tile(col[:1], (len(gr), 1))
            else:
                d = np.linalg.norm(pts[:, None, :] - e_xyz[None, :, :], axis=2)
                nn3 = np.argsort(d, axis=1)[:, :3]
                col = e_col[nn3].mean(axis=1)
                # grown pixels copy the nearest filled pixel
                fr, fc = rows, cols
                j = np.argmin((gr[:, None] - fr[None, :]) ** 2 + (gc[:, None] - fc[None, :]) ** 2, axis=1) if len(gr) else []
                gcol = col[j] if len(gr) else np.zeros((0, img.channels))
            px[rows, cols] = col
            if len(gr):
                px[gr, gc] = gcol
            filled += len(rows)
        img.pixels.foreach_set(px.ravel())
        img.update()
        try:
            img.pack()
        except RuntimeError:
            pass
        out["images"][img.name] = {"normal": is_normal, "pixels_filled": int(filled), "size": [w, h]}
    out["placed"] = len(placed)
    return out


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
