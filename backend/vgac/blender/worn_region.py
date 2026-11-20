"""Blender headless: a worn item that must not bend with the body moves rigidly with one bone (WP-69).

Run: blender -b --python worn_region.py -- <rig.glb> <out.glb> --report <json> --bone mixamorig:Spine2
     --region-y 0.10 --region-x 0.42 --region-z 0.65,1.60 [--seam 2] [--max-change 0.3]

The region is the set of mesh vertices in a box on the rest pose: Y at least region-y (behind the body's back surface,
about Y 0 on the built-in rigs), |X| at most region-x, Z between the two region-z values. The region's weights become
the one bone. Seam: the next `seam` rings of vertices blend toward the bone (ring k takes (seam + 1 - k) / (seam + 1)
of the bone), so there is no tear. Everything else is unchanged. Four influences, normalised.

Everything above main() is plain numpy so the backend tests can run it without Blender.
"""
import json
import os
import sys

import numpy as np

MAX_INFLUENCES = 4


def region_mask(co, region_y, region_x, region_z):
    """Boolean per vertex for the worn-item box on rest coordinates co (V, 3)."""
    x, y, z = co[:, 0], co[:, 1], co[:, 2]
    zlo, zhi = region_z
    return (y >= region_y) & (np.abs(x) <= region_x) & (z >= zlo) & (z <= zhi)


def ring_blend(edges, region, seam):
    """Per vertex, the blend toward the bone: 1.0 in the region, (seam + 1 - k) / (seam + 1) in ring k (k = 1 .. seam
    rings out from the region), 0 elsewhere. Rings grow along mesh edges."""
    V = len(region)
    t = np.where(region, 1.0, 0.0)
    if seam <= 0 or not len(edges):
        return t
    adj = [[] for _ in range(V)]
    for a, b in edges:
        adj[a].append(b)
        adj[b].append(a)
    frontier = np.where(region)[0].tolist()
    seen = region.copy()
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


def run_numpy(W, edges, co, names, bone, region_y, region_x, region_z, seam=2, max_change=0.3):
    """The whole pass on arrays. names: one bone name per column of W. Returns (W, report dict).
    If the bone is missing, W is returned unchanged with a reason."""
    if bone not in names:
        return np.array(W, dtype=float, copy=True), {"ok": False, "reason": f"bone {bone} not on the mesh"}
    col = list(names).index(bone)
    region = region_mask(co, region_y, region_x, region_z)
    report = {"ok": True, "bone": bone, "region_vertices": int(region.sum()), "seam_rings": seam}
    if not np.any(region):
        report.update(ok=False, reason="empty region")
        return np.array(W, dtype=float, copy=True), report
    t = ring_blend(edges, region, seam)
    W0 = np.array(W, dtype=float, copy=True)
    target = np.zeros_like(W0)
    target[:, col] = 1.0
    blended = (1.0 - t[:, None]) * W0 + t[:, None] * target
    rows = t > 1e-9
    moved = np.abs(blended - W0).sum(axis=1)
    # a seam row whose weights would move by more than max_change keeps its own weights (as leg_skin does)
    far = rows & ~region & (moved > max_change) if max_change > 0 else np.zeros(len(rows), dtype=bool)
    blended[far] = W0[far]
    rows = rows & ~far
    out = cap_and_normalise(blended, rows | region)
    report.update(seam_vertices=int((rows & ~region).sum()), kept_seam_vertices=int(far.sum()),
                  row_sum_error=float(np.abs(out[rows | region].sum(axis=1) - 1.0).max()) if np.any(rows | region) else 0.0)
    return out, report


# ---- Blender side -------------------------------------------------------------------------------------------------


def main():  # pragma: no cover - runs inside Blender
    import bpy

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _common  # noqa: E402

    argv = _common.after_double_dash()
    src, dst = os.path.abspath(argv[0]), os.path.abspath(argv[1])
    report_path = _common.opt("--report", None)
    bone = _common.opt("--bone", "mixamorig:Spine2")
    ry = float(_common.opt("--region-y", "0.10"))
    rx = float(_common.opt("--region-x", "0.42"))
    rz = [float(v) for v in _common.opt("--region-z", "0.65,1.60").split(",")]
    seam = int(_common.opt("--seam", "2"))
    max_change = float(_common.opt("--max-change", "0.3"))
    _common.open_glb(src)
    scene = bpy.context.scene
    meshes = [o for o in scene.objects if o.type == "MESH" and o.vertex_groups]
    report = {"ok": False, "meshes": []}
    if not meshes:
        report["reason"] = "no skinned mesh"
    else:
        for o in meshes:
            vg_names = [g.name for g in o.vertex_groups]
            V = len(o.data.vertices)
            W = np.zeros((V, len(vg_names)))
            for v in o.data.vertices:
                for g in v.groups:
                    W[v.index, g.group] = g.weight
            co = np.array([tuple(v.co) for v in o.data.vertices], dtype=float).reshape(-1, 3)
            edges = np.array([[e.vertices[0], e.vertices[1]] for e in o.data.edges], dtype=int).reshape(-1, 2)
            W2, rep = run_numpy(W, edges, co, vg_names, bone, ry, rx, rz, seam=seam, max_change=max_change)
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
