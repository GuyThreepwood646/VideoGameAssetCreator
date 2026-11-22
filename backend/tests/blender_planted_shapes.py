"""Run inside Blender (tests/test_outline_check.py): planted-defect meshes for the shape_loss and outline measures.

    blender -b --factory-startup --python blender_planted_shapes.py -- OUT.json [case ...]

Each case is a small model built from primitives (Z up, about 1 to 1.8 units tall): the intact "before" mesh, and a "final"
mesh that is the same model reduced with Blender's collapse decimator. A control case reduces the intact model; a defect
case reduces a model with one defect planted (a collapsed blade, a closed handle hole, a missing finger, a dented corner).
For every case the script writes today's shape_loss numbers (vgac/blender/shapeloss.py, driven the way prep.py drives it)
and the outline numbers (vgac/blender/outline.py).
"""
import json
import math
import os
import sys
import time

import bmesh
import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "vgac", "blender"))
import outline  # noqa: E402
import shapeloss  # noqa: E402


def _reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _prim(kind, loc=(0, 0, 0), scale=(1, 1, 1), rot=(0, 0, 0), cuts=0, **kw):
    if kind == "cube":
        bpy.ops.mesh.primitive_cube_add(size=1.0)
    elif kind == "cyl":
        bpy.ops.mesh.primitive_cylinder_add(vertices=kw.get("verts", 24), radius=0.5, depth=1.0)
    elif kind == "sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12, radius=0.5)
    elif kind == "torus":
        bpy.ops.mesh.primitive_torus_add(major_radius=kw["major"], minor_radius=kw["minor"], major_segments=32, minor_segments=12)
    ob = bpy.context.active_object
    ob.scale = scale
    ob.rotation_euler = [math.radians(a) for a in rot]
    ob.location = loc
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    if cuts:
        bm = bmesh.new()
        bm.from_mesh(ob.data)
        bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=cuts, use_grid_fill=True)
        bm.to_mesh(ob.data)
        bm.free()
    return ob


def _join(objs, name):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.join()
    ob = bpy.context.active_object
    ob.name = name
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    bm.to_mesh(ob.data)
    bm.free()
    return ob


# --- the models ---------------------------------------------------------------------------------


def sword(blade_w=0.12, blade_t=0.03):
    parts = [_prim("sphere", (0, 0, 0.03), (0.06, 0.06, 0.06)),
             _prim("cyl", (0, 0, 0.14), (0.04, 0.04, 0.2)),
             _prim("cube", (0, 0, 0.26), (0.30, 0.05, 0.04), cuts=4),
             _prim("cube", (0, 0, 0.635), (blade_w, blade_t, 0.73), cuts=12)]
    return _join(parts, "sword")


def mug(close_hole=False):
    parts = [_prim("cyl", (0, 0, 0.5), (0.7, 0.7, 1.0), verts=40, cuts=3),
             _prim("torus", (0.55, 0, 0.5), rot=(90, 0, 0), major=0.22, minor=0.05)]
    if close_hole:
        parts.append(_prim("cyl", (0.55, 0, 0.5), (0.44, 0.44, 0.08), rot=(90, 0, 0), verts=32))
    return _join(parts, "mug")


def figure(missing_finger=False):
    parts = [_prim("cyl", (-0.1, 0, 0.425), (0.12, 0.12, 0.85), cuts=4),
             _prim("cyl", (0.1, 0, 0.425), (0.12, 0.12, 0.85), cuts=4),
             _prim("cube", (0, 0, 1.15), (0.36, 0.20, 0.60), cuts=6),
             _prim("cyl", (0, 0, 1.52), (0.10, 0.10, 0.14)),
             _prim("sphere", (0, 0, 1.685), (0.23, 0.23, 0.23)),
             _prim("cyl", (-0.48, 0, 1.38), (0.08, 0.08, 0.60), rot=(0, 90, 0), cuts=4),
             _prim("cyl", (0.48, 0, 1.38), (0.08, 0.08, 0.60), rot=(0, 90, 0), cuts=4),
             _prim("cube", (-0.83, 0, 1.38), (0.10, 0.03, 0.09)),
             _prim("cube", (0.83, 0, 1.38), (0.10, 0.03, 0.09))]
    for side in (-1, 1):
        for k, ang in enumerate((-30, -15, 0, 15, 30)):
            if missing_finger and side == 1 and ang == 30:
                continue
            a = math.radians(ang)
            length, r = 0.07, 0.009
            cx = side * (0.88 + 0.5 * length * math.cos(a))
            cz = 1.38 + 0.5 * length * math.sin(a)
            # a cylinder's axis is Z; turn it to the finger direction (cos a, 0, sin a) with the arm out along +-X
            rot_y = 90 - ang if side == 1 else -(90 - ang)
            parts.append(_prim("cyl", (cx, 0, cz), (2 * r, 2 * r, length), rot=(0, rot_y, 0), verts=8))
    return _join(parts, "figure")


def box(dent=False):
    ob = _prim("cube", (0, 0, 0.5), (1, 1, 1), cuts=14)
    if dent:
        corner = Vector((0.5, 0.5, 1.0))
        inward = (Vector((0, 0, 0.5)) - corner).normalized()
        for v in ob.data.vertices:
            dist = (Vector(v.co) - corner).length
            if dist < 0.25:
                v.co = Vector(v.co) + inward * (0.12 * (1.0 - dist / 0.25))
    return _join([ob], "box")


# case name -> (model builder for the before mesh, builder for the mesh that gets reduced, reduction ratio)
CASES = {
    "sword_clean": (lambda: sword(), lambda: sword(), 0.35),
    "sword_clean_hard": (lambda: sword(), lambda: sword(), 0.08),
    "sword_blade_sliver": (lambda: sword(), lambda: sword(0.012, 0.006), 0.35),
    "sword_blade_flat": (lambda: sword(), lambda: sword(0.12, 0.004), 0.35),
    # a thin plate blade pressed flatter: every point stays within 0.5 percent of the height of its old surface
    "sword_thin_blade_flat": (lambda: sword(0.12, 0.012), lambda: sword(0.12, 0.002), 0.35),
    "mug_clean": (lambda: mug(), lambda: mug(), 0.35),
    "mug_clean_hard": (lambda: mug(), lambda: mug(), 0.08),
    "mug_handle_hole_closed": (lambda: mug(), lambda: mug(close_hole=True), 0.35),
    "figure_clean": (lambda: figure(), lambda: figure(), 0.35),
    "figure_clean_hard": (lambda: figure(), lambda: figure(), 0.08),
    "figure_missing_finger": (lambda: figure(), lambda: figure(missing_finger=True), 0.35),
    "box_clean": (lambda: box(), lambda: box(), 0.35),
    "box_clean_hard": (lambda: box(), lambda: box(), 0.08),
    "box_dented_corner": (lambda: box(), lambda: box(dent=True), 0.35),
}


# --- measuring ------------------------------------------------------------------------------------


def _arrays(ob):
    me = ob.data
    me.calc_loop_triangles()
    co = np.empty(len(me.vertices) * 3, dtype=np.float32)
    me.vertices.foreach_get("co", co)
    tris = np.empty(len(me.loop_triangles) * 3, dtype=np.int64)
    me.loop_triangles.foreach_get("vertices", tris)
    return co.reshape(-1, 3).astype(float), tris.reshape(-1, 3)


def _decimate(ob, ratio):
    mod = ob.modifiers.new("d", "DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = ratio
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(ob.evaluated_get(dg))
    out = bpy.data.objects.new(ob.name + "_reduced", me)
    bpy.context.scene.collection.objects.link(out)
    ob.modifiers.remove(mod)
    return out


def _nearest(tree, pts):
    return np.array([tree.find_nearest(p)[3] for p in pts.tolist()])


def run_case(name):
    _reset()
    build_pre, build_fin, ratio = CASES[name]
    pre = build_pre()
    pre_v, pre_t = _arrays(pre)
    _reset()
    fin_src = build_fin()
    fin = _decimate(fin_src, ratio)
    fin_v, fin_t = _arrays(fin)
    t_pre = BVHTree.FromPolygons(pre_v.tolist(), pre_t.tolist(), all_triangles=True)
    t_fin = BVHTree.FromPolygons(fin_v.tolist(), fin_t.tolist(), all_triangles=True)
    t0 = time.perf_counter()
    sl = shapeloss.measure(pre_v, pre_t, fin_v, fin_t, lambda p: _nearest(t_fin, p), lambda p: _nearest(t_pre, p))
    t1 = time.perf_counter()
    ol = outline.measure(t_pre, t_fin, pre_v, fin_v)
    t2 = time.perf_counter()
    return {"case": name, "triangles_before": len(pre_t), "triangles_after": len(fin_t), "ratio": ratio,
            "shape_loss": sl, "outline": ol, "shape_seconds": round(t1 - t0, 2), "outline_seconds": round(t2 - t1, 2)}


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    out_path, wanted = argv[0], argv[1:] or list(CASES)
    results = [run_case(n) for n in wanted]
    with open(out_path, "w") as f:
        json.dump(results, f, indent=1)


if __name__ == "__main__":
    main()
