"""Blender headless: a stiff part moves rigidly with one spine bone (WP-69).

Run: blender -b --python worn_region.py -- <rig.glb> <out.glb> --report <json> --points <json> [--seam 8]
     [--radius 0.25]

--points is the list of stiff parts placed on this rig version: [{"name", "location": [x, y, z]}], the location in the
rig GLB's own glTF frame (Y up, the character facing +Z), as declared_parts.py stores it.

For each point the region is grown from the mesh vertex nearest the point: the vertices connected to it along mesh
edges, within RADIUS metres of the point. The regions of all points are joined. The region's weights become one
spine bone: the chain bone (Spine, Spine1, Spine2, Chest) whose head is nearest the region's centre. Seam: the next
SEAM rings blend toward that bone (ring k takes (seam + 1 - k) / (seam + 1)), so the cut is not torn. Four
influences, normalised. Nothing else changes.

Everything above main() is plain numpy so the backend tests can run it without Blender.
"""
import json
import os
import sys

import numpy as np

MAX_INFLUENCES = 4
RADIUS = 0.6  # metres from the placed point; a region never grows past it (edges only, see the handover)
SEAM = 8  # rings of the seam (measured on The Heir: no tear, body no worse)
SPINE_WORDS = ("spine", "chest")


def gltf_to_blender(p):
    """glTF (Y up, facing +Z) to Blender's Z-up frame after the glTF import (Blender Y = -glTF Z)."""
    x, y, z = p
    return np.array([x, -z, y], dtype=float)


def _adjacency(V, edges):
    adj = [[] for _ in range(V)]
    for a, b in np.asarray(edges, dtype=int).reshape(-1, 2):
        adj[a].append(b)
        adj[b].append(a)
    return adj


def grow_region(co, edges, point, radius=RADIUS):
    """Vertices connected to the one nearest the point along edges, within radius of the point. A bool mask."""
    co = np.asarray(co, dtype=float).reshape(-1, 3)
    V = len(co)
    mask = np.zeros(V, dtype=bool)
    if V == 0:
        return mask
    dist = np.linalg.norm(co - np.asarray(point, dtype=float), axis=1)
    start = int(np.argmin(dist))
    if dist[start] > radius:
        return mask
    adj = _adjacency(V, edges)
    stack = [start]
    mask[start] = True
    while stack:
        u = stack.pop()
        for w in adj[u]:
            if not mask[w] and dist[w] <= radius:
                mask[w] = True
                stack.append(w)
    return mask


def spine_bone(names, heads, centre):
    """The chain bone (spine or chest) whose head is nearest the region's centre; None when there is none."""
    best, best_d = None, None
    for n, h in zip(names, heads):
        low = str(n).replace("mixamorig:", "").lower()
        if not any(w in low for w in SPINE_WORDS):
            continue
        d = float(np.linalg.norm(np.asarray(h, dtype=float) - np.asarray(centre, dtype=float)))
        if best_d is None or d < best_d:
            best, best_d = n, d
    return best


def ring_blend(edges, region, seam):
    """Per vertex blend toward the bone: 1.0 in the region, (seam + 1 - k) / (seam + 1) in ring k, 0 elsewhere."""
    V = len(region)
    t = np.where(region, 1.0, 0.0)
    if seam <= 0 or not len(edges):
        return t
    adj = _adjacency(V, edges)
    frontier = np.where(region)[0].tolist()
    seen = np.array(region, dtype=bool, copy=True)
    for k in range(1, seam + 1):
        nxt = []
        for v in frontier:
            for w in adj[v]:
                if not seen[w]:
                    seen[w] = True
                    nxt.append(w)
        for w in nxt:
            t[w] = (seam + 1 - k) / (seam + 1.0)
        frontier = nxt
    return t


def cap_and_normalise(W, rows):
    W = np.array(W, dtype=float, copy=True)
    if not np.any(rows):
        return W
    sub = W[rows]
    if sub.shape[1] > MAX_INFLUENCES:
        cut = -np.sort(-sub, axis=1)[:, MAX_INFLUENCES - 1:MAX_INFLUENCES]
        sub = np.where(sub >= cut, sub, 0.0)
    s = sub.sum(axis=1)
    sub = np.where(s[:, None] > 1e-12, sub / np.maximum(s[:, None], 1e-12), sub)
    W[rows] = sub
    return W


def run_numpy(W, edges, co, names, heads, region, seam=SEAM):
    """The pass on arrays. region: bool mask over vertices. names and heads: one bone per column of W (heads are the
    bone head positions). Returns (W, report). W unchanged when there is no region or no spine bone."""
    W0 = np.array(W, dtype=float, copy=True)
    region = np.asarray(region, dtype=bool)
    if not np.any(region):
        return W0, {"ok": False, "reason": "the stiff part region is empty"}
    centre = np.asarray(co, dtype=float)[region].mean(axis=0)
    bone = spine_bone(names, heads, centre)
    if bone is None:
        return W0, {"ok": False, "reason": "no spine or chest bone on this rig"}
    col = list(names).index(bone)
    t = ring_blend(edges, region, seam)
    target = np.zeros_like(W0)
    target[:, col] = 1.0
    blended = (1.0 - t[:, None]) * W0 + t[:, None] * target
    rows = t > 1e-9
    out = cap_and_normalise(blended, rows)
    err = float(np.abs(out[rows].sum(axis=1) - 1.0).max()) if np.any(rows) else 0.0
    report = {"ok": True, "bone": bone, "region_vertices": int(region.sum()),
              "seam_vertices": int((rows & ~region).sum()), "seam_rings": int(seam), "row_sum_error": err}
    return out, report


# ---- Blender side -------------------------------------------------------------------------------------------------


def main():  # pragma: no cover - runs inside Blender
    import bpy

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _common  # noqa: E402

    argv = _common.after_double_dash()
    src, dst = os.path.abspath(argv[0]), os.path.abspath(argv[1])
    report_path = _common.opt("--report", None)
    points_path = _common.opt("--points", None)
    seam = int(_common.opt("--seam", str(SEAM)))
    radius = float(_common.opt("--radius", str(RADIUS)))
    points = []
    if points_path:
        with open(points_path, encoding="utf-8") as fh:
            data = json.load(fh)
        points = data.get("parts", []) if isinstance(data, dict) else data
    _common.open_glb(src)
    scene = bpy.context.scene
    arm = next((o for o in scene.objects if o.type == "ARMATURE"), None)
    meshes = [o for o in scene.objects if o.type == "MESH" and o.vertex_groups]
    report = {"ok": False, "points": [], "meshes": []}
    if not meshes or arm is None:
        report["reason"] = "no armature or no skinned mesh"
    elif not points:
        report["reason"] = "no stiff parts placed"
    else:
        bone_heads = {b.name: list(arm.matrix_world @ b.head_local) for b in arm.data.bones}
        for o in meshes:
            vg_names = [g.name for g in o.vertex_groups]
            V = len(o.data.vertices)
            W = np.zeros((V, len(vg_names)))
            for v in o.data.vertices:
                for g in v.groups:
                    W[v.index, g.group] = g.weight
            co = np.array([tuple(o.matrix_world @ v.co) for v in o.data.vertices], dtype=float).reshape(-1, 3)
            edges = np.array([[e.vertices[0], e.vertices[1]] for e in o.data.edges], dtype=int).reshape(-1, 2)
            region = np.zeros(V, dtype=bool)
            for p in points:
                m = grow_region(co, edges, gltf_to_blender(p["location"]), radius)
                report["points"].append({"name": p.get("name"), "vertices": int(m.sum())})
                region |= m
            heads = [bone_heads.get(n, [0.0, 0.0, 0.0]) for n in vg_names]
            W2, rep = run_numpy(W, edges, co, vg_names, heads, region, seam=seam)
            rep["mesh"] = o.name
            report["meshes"].append(rep)
            if rep.get("ok"):
                for g in o.vertex_groups:
                    g.remove([v.index for v in o.data.vertices])
                for gi, g in enumerate(o.vertex_groups):
                    for vi in np.where(W2[:, gi] > 1e-6)[0]:
                        g.add([int(vi)], float(W2[vi, gi]), "REPLACE")
        report["ok"] = any(m.get("ok") for m in report["meshes"])
    if report_path:
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
    if report["ok"]:
        bpy.ops.object.select_all(action="DESELECT")
        keep = [o for o in scene.objects if o.type == "ARMATURE" or (o.type == "MESH" and o.vertex_groups)]
        for o in keep:
            o.select_set(True)
        bpy.context.view_layer.objects.active = keep[0]
        bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", use_selection=True, export_apply=False,
                                  export_animations=True)
    print("WORN_REGION_OK" if report["ok"] else "WORN_REGION_SKIPPED", report.get("reason", ""))


if __name__ == "__main__":  # pragma: no cover
    main()
