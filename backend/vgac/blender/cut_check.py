"""Blender headless: does cutting a joined arm away from the body make raising it worse? (WP-75 part B)

Run: blender -b --python cut_check.py -- <rig before the cut.glb> <rig after the cut.glb> --report <json>

The rig step cuts an upper arm the joined-limb measure finds joined (separate.py). The cut is meant to stop the side
stretching like a web when the arm lifts, but on some models it can stretch the shoulder more, or let the skin fold
through itself once the collarbone lifts. This script raises each arm of both rigs to RAISE_DEGS from straight down,
with the collarbone lift the moves use (gait.clavicle_lift_deg, the upper arm keeping its direction), and measures on
each side, against the rig's own rest pose:
  shoulder  share of edges within 0.5 shoulder widths of the shoulder joint stretched over STRETCH (1.3) times
  side      the same for the side under the arm (0.10 to 1.10 widths below the joint, 0.30 in to 0.15 out)
  crossings triangles near the armpit passing through each other (not sharing a point)
The worst over RAISE_DEGS is kept. worse_sides() says which sides the cut made worse (STRETCH_TOL for stretch,
CROSS_TOL for crossings); the rig step keeps the cut only on the others.

Shoulder width W: upper-arm joint to upper-arm joint. Mixamo-named skeletons only (LeftArm, LeftShoulder,
LeftForeArm and the right ones). Everything above main() is plain numpy so the backend tests can run it without
Blender.
"""
import json
import math
import os
import sys

import numpy as np

RAISE_DEGS = (90.0, 120.0)
STRETCH = 1.3
STRETCH_TOL = 0.002  # a share of edges: the cut may not stretch more edges than this over the uncut rig
CROSS_TOL = 5  # armpit crossings the cut may add (the WP-75 pass mark: today plus 5)
MIN_EDGE_M = 0.001


def regions(co, joint, mid, lat, fwd, W, sign):
    """Masks over rest points (n, 3) for one side: shoulder sphere, side band, armpit ball, own side."""
    d = np.asarray(co, float) - np.asarray(joint, float)
    own = sign * ((np.asarray(co, float) - np.asarray(mid, float)) @ lat) > 0
    u = sign * (d @ lat)
    z = d[:, 2]
    f = d @ fwd
    shoulder = (np.linalg.norm(d, axis=1) < 0.5 * W) & own
    side = (u >= -0.30 * W) & (u <= 0.15 * W) & (z >= -1.10 * W) & (z <= -0.10 * W) & (np.abs(f) < 0.35 * W) & own
    pit = (np.linalg.norm(d - np.array([0.0, 0.0, -0.25 * W]), axis=1) < 0.40 * W) & own
    return {"shoulder": shoulder, "side": side, "pit": pit}


def stretch_share(rest, posed, edges, mask, ratio=STRETCH, min_len=MIN_EDGE_M):
    """Share of the edges with both ends in `mask` whose posed length is over `ratio` times their rest length."""
    E = np.asarray(edges, dtype=np.int64).reshape(-1, 2)
    m = np.asarray(mask, bool)
    E = E[m[E[:, 0]] & m[E[:, 1]]]
    if not len(E):
        return 0.0
    r = np.linalg.norm(rest[E[:, 0]] - rest[E[:, 1]], axis=1)
    p = np.linalg.norm(posed[E[:, 0]] - posed[E[:, 1]], axis=1)
    keep = r > min_len
    if not keep.any():
        return 0.0
    return float((p[keep] > ratio * r[keep]).mean())


def worse_sides(before, after, stretch_tol=STRETCH_TOL, cross_tol=CROSS_TOL):
    """{side: [what got worse]} for the sides where the cut rig is worse than the uncut one, from two reports'
    "sides" ({side: {shoulder, side, crossings}}). A side missing from either is not judged."""
    out = {}
    for s, b in (before or {}).items():
        a = (after or {}).get(s)
        if not a:
            continue
        why = []
        if a["shoulder"] > b["shoulder"] + stretch_tol:
            why.append("shoulder")
        if a["side"] > b["side"] + stretch_tol:
            why.append("side")
        if a["crossings"] > b["crossings"] + cross_tol:
            why.append("crossings")
        if why:
            out[s] = why
    return out


# ---- Blender side -------------------------------------------------------------------------------------------------


def measure(path):  # pragma: no cover - runs inside Blender
    """{side: {shoulder, side, crossings}} for one rig, the worst over RAISE_DEGS; or {"reason"}."""
    import bpy
    from mathutils import Matrix, Vector
    from mathutils.bvhtree import BVHTree

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _common
    import gait

    _common.open_glb(path)
    scene = bpy.context.scene
    arm = next((o for o in scene.objects if o.type == "ARMATURE"), None)
    meshes = [o for o in scene.objects if o.type == "MESH" and o.vertex_groups]
    if arm is None or not meshes:
        return {"reason": "no armature or no skinned mesh"}
    MW = arm.matrix_world
    names = {s: {k: f"mixamorig:{s}{k}" for k in ("Shoulder", "Arm", "ForeArm")} for s in ("Left", "Right")}
    if not all(n in arm.data.bones for s in names.values() for n in s.values()):
        return {"reason": "no Mixamo arm bones"}
    UP = Vector((0, 0, 1))
    J = {s: MW @ arm.data.bones[names[s]["Arm"]].head_local for s in names}
    W = (J["Left"] - J["Right"]).length
    mid = (J["Left"] + J["Right"]) / 2
    lat = J["Left"] - J["Right"]
    lat.z = 0
    lat.normalize()
    fwd = UP.cross(lat).normalized()
    bpy.context.view_layer.update()

    def coords():
        dg = bpy.context.evaluated_depsgraph_get()
        out = []
        for o in meshes:
            eo = o.evaluated_get(dg)
            me = eo.to_mesh()
            c = np.empty(len(me.vertices) * 3)
            me.vertices.foreach_get("co", c)
            M = np.array(eo.matrix_world)
            out.append(c.reshape(-1, 3) @ M[:3, :3].T + M[:3, 3])
            eo.to_mesh_clear()
        return out

    rest = coords()
    topo = []
    for o in meshes:
        me = o.data
        E = np.empty(len(me.edges) * 2, dtype=np.int64)
        me.edges.foreach_get("vertices", E)
        me.calc_loop_triangles()
        T = np.empty(len(me.loop_triangles) * 3, dtype=np.int64)
        me.loop_triangles.foreach_get("vertices", T)
        topo.append((E.reshape(-1, 2), T.reshape(-1, 3)))
    rest_angle = {s: gait.angle_from_down(tuple(MW.to_3x3() @ (arm.data.bones[names[s]["ForeArm"]].head_local
                                                                - arm.data.bones[names[s]["Arm"]].head_local))) for s in names}

    def turn(pb, axis_w, deg, pivot_w):
        Minv = MW.inverted()
        ax = (Minv.to_3x3() @ axis_w).normalized()
        piv = Minv @ pivot_w
        pb.matrix = Matrix.Translation(piv) @ Matrix.Rotation(math.radians(deg), 4, ax) @ Matrix.Translation(-piv) @ pb.matrix
        bpy.context.view_layer.update()

    def pose(angle):
        for pb in arm.pose.bones:
            pb.matrix_basis = Matrix.Identity(4)
        bpy.context.view_layer.update()
        for s, sign in (("Left", 1), ("Right", -1)):
            out = lat * sign
            sh, ua, fa = (arm.pose.bones[names[s][k]] for k in ("Shoulder", "Arm", "ForeArm"))
            lift = gait.clavicle_lift_deg(angle, rest_angle[s])
            if lift > 1e-3:
                ax = out.cross(-UP).normalized()
                if (Matrix.Rotation(math.radians(10), 3, ax) @ out).z < 0:
                    ax = -ax
                turn(sh, ax, lift, MW @ sh.head)
            h, t = MW @ ua.head, MW @ fa.head
            a = math.radians(angle)
            q = (t - h).normalized().rotation_difference((out * math.sin(a) - UP * math.cos(a)).normalized())
            if abs(q.angle) > 1e-6:
                turn(ua, q.axis, math.degrees(q.angle), h)

    res = {}
    for s, sign in (("Left", 1), ("Right", -1)):
        res[s] = {"shoulder": 0.0, "side": 0.0, "crossings": 0}
    for angle in RAISE_DEGS:
        pose(angle)
        posed = coords()
        for s, sign in (("Left", 1), ("Right", -1)):
            r = res[s]
            for k, (o, R, P) in enumerate(zip(meshes, rest, posed)):
                E, T = topo[k]
                reg = regions(R, np.array(J[s]), np.array(mid), np.array(lat), np.array(fwd), W, sign)
                r["shoulder"] = max(r["shoulder"], round(stretch_share(R, P, E, reg["shoulder"]), 4))
                r["side"] = max(r["side"], round(stretch_share(R, P, E, reg["side"]), 4))
                tris = T[reg["pit"][T].any(axis=1)]
                if len(tris) < 2:
                    continue
                used = np.unique(tris)
                remap = {int(v): i for i, v in enumerate(used)}
                tree = BVHTree.FromPolygons([Vector(P[v]) for v in used],
                                            [[remap[int(a)], remap[int(b)], remap[int(c)]] for a, b, c in tris],
                                            all_triangles=True, epsilon=0.0)
                _, weld = np.unique(np.round(R, 5), axis=0, return_inverse=True)
                weld = weld.reshape(-1)
                n = sum(1 for i, j in tree.overlap(tree) if i < j and not (set(weld[tris[i]]) & set(weld[tris[j]])))
                r["crossings"] = max(r["crossings"], int(n))
    return {"sides": res, "W": round(W, 4)}


def main():  # pragma: no cover - runs inside Blender
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _common

    argv = _common.after_double_dash()
    before_glb, after_glb = os.path.abspath(argv[0]), os.path.abspath(argv[1])
    report_path = _common.opt("--report", None)
    before = measure(before_glb)
    after = measure(after_glb)
    rep = {"ok": "sides" in before and "sides" in after, "raise_deg": list(RAISE_DEGS), "before": before, "after": after}
    if rep["ok"]:
        rep["worse"] = worse_sides(before["sides"], after["sides"])
    if report_path:
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=1)
    print("CUT_CHECK_OK" if rep["ok"] else "CUT_CHECK_SKIPPED", json.dumps(rep.get("worse")))


if __name__ == "__main__":  # pragma: no cover
    main()
