"""Triangle floor curve (PL-131): one descent of the app's own reduction, measuring how far the surface moves.

Run headless by stages/prep.py before the budget reduction:  blender -b -P trifloor.py -- <model.glb> <out.json> [--min N]

The model is welded as prep welds it, then reduced with the same Decimate (collapse) modifier prep uses, step by step
down to a minimum (each step starts from the last, so the whole run costs about one reduction to the minimum). After
each step the surface of the welded original is measured against the reduced one with shapeloss's sampling (seeded
points, nearest-surface distance). Writes {start_tris, diag, steps: [{tris, mean, p95, max}]} with every distance as a
share of the bounding diagonal. The backend reads the floor off it (vgac/trifloor.py). Nothing in the model is kept.
"""
import json
import os
import sys
import time

import bpy
import numpy as np
from mathutils.bvhtree import BVHTree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402
import shapeloss  # noqa: E402

STEP = 0.72  # each step keeps this share of the triangles
SAMPLES = 2000


def arrays(obj):
    me = obj.data
    me.calc_loop_triangles()
    co = np.empty(len(me.vertices) * 3, dtype=np.float32)
    me.vertices.foreach_get("co", co)
    t = np.empty(len(me.loop_triangles) * 3, dtype=np.int64)
    me.loop_triangles.foreach_get("vertices", t)
    m = np.array(obj.matrix_world, dtype=float)
    return co.reshape(-1, 3).astype(float) @ m[:3, :3].T + m[:3, 3], t.reshape(-1, 3)


def main():
    argv = _common.after_double_dash()
    glb, out = argv[0], argv[1]
    floor_n = int(_common.opt("--min", 500))
    t0 = time.perf_counter()
    _common.open_glb(glb)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    ob = bpy.context.view_layer.objects.active
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=0.00001)
    bpy.ops.object.mode_set(mode="OBJECT")
    pre_v, pre_t = arrays(ob)
    diag = float(np.linalg.norm(pre_v.max(axis=0) - pre_v.min(axis=0)))
    points = shapeloss.sample_surface(pre_v, pre_t, SAMPLES)
    steps = []
    current = len(pre_t)
    while diag > 0 and len(points):
        current = int(current * STEP)
        if current < floor_n:
            break
        ob.data.calc_loop_triangles()
        mod = ob.modifiers.new("budget", "DECIMATE")
        mod.ratio = current / len(ob.data.loop_triangles) * 0.995
        mod.use_collapse_triangulate = True
        bpy.ops.object.modifier_apply(modifier="budget")
        v, t = arrays(ob)
        tree = BVHTree.FromPolygons(v.tolist(), t.tolist(), all_triangles=True)
        d = np.array([tree.find_nearest(p)[3] for p in points.tolist()]) / diag
        steps.append({"tris": int(len(t)), "mean": float(d.mean()), "p95": float(np.percentile(d, 95)), "max": float(d.max())})
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"start_tris": int(len(pre_t)), "diag": diag, "seconds": round(time.perf_counter() - t0, 2), "steps": steps}, fh)


main()
