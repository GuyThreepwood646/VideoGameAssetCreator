"""Runs inside Blender (tests/test_faceuv_pack.py starts it): builds a small mesh of separate texture pieces for the
face relayout test, or reads a relaid mesh back and reports its pieces.

  make    <out.glb> <points.json> <maps.json> <tex>   24 pieces (small rectangles) on about 35 percent of the sheet; 4 of
                                                      them are the face (their centres go to points.json); writes one
                                                      flat map per kind to maps.json
  inspect <in.glb> <out.json> <points.json>          islands, overlapping pixels between islands (2048 grid), the share of
                                                      the sheet used and the face's area and the total area in UV
"""
import json
import math
import os
import sys

import bmesh
import bpy
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vgac", "blender"))
import _common  # noqa: E402
import uvfit  # noqa: E402

args = _common.after_double_dash()
mode = args[0]


def island_of(bm, uv):
    def key(loop):
        return (round(loop[uv].uv.x, 6), round(loop[uv].uv.y, 6))

    par = list(range(len(bm.faces)))

    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x

    for e in bm.edges:
        lf = e.link_faces
        if len(lf) != 2:
            continue
        a, b = lf
        la = {loop.vert.index: key(loop) for loop in a.loops if loop.vert in e.verts}
        lb = {loop.vert.index: key(loop) for loop in b.loops if loop.vert in e.verts}
        if la == lb:
            ra, rb = find(a.index), find(b.index)
            if ra != rb:
                par[rb] = ra
    return [find(f.index) for f in bm.faces]


def tri_area(p0, p1, p2):
    return abs((p1[0] - p0[0]) * (p2[1] - p0[1]) - (p2[0] - p0[0]) * (p1[1] - p0[1])) / 2


if mode == "make":
    out, points_path, maps_path, tex = args[1], args[2], args[3], int(args[4])
    bpy.ops.wm.read_factory_settings(use_empty=True)
    me = bpy.data.meshes.new("fixture")
    ob = bpy.data.objects.new("fixture", me)
    bpy.context.scene.collection.objects.link(ob)
    bm = bmesh.new()
    uv = bm.loops.layers.uv.new("UVMap")
    rng = np.random.default_rng(3)
    face_ids = {7, 8, 13, 14}
    centres = []
    for i in range(24):
        r, c = divmod(i, 6)
        # a 6 x 4 grid of cells 0.16 wide and 0.24 high; the piece fills part of its cell
        w, h = float(rng.uniform(0.05, 0.13)), float(rng.uniform(0.06, 0.15))
        u0, v0 = 0.02 + c * 0.16, 0.02 + r * 0.24
        x0, y0 = c * 1.2, r * 1.2
        vs = [bm.verts.new((x0, y0, 0)), bm.verts.new((x0 + 1, y0, 0)), bm.verts.new((x0 + 1, y0 + 1, 0)), bm.verts.new((x0, y0 + 1, 0))]
        f = bm.faces.new(vs)
        for loop, (du, dv) in zip(f.loops, ((0, 0), (1, 0), (1, 1), (0, 1))):
            loop[uv].uv = (u0 + du * w, v0 + dv * h)
    # triangles, as the importer gives them; the face points are the centres of the face pieces' triangles
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    for f in bm.faces:
        c = f.calc_center_median()
        if (int(c.y // 1.2) * 6 + int(c.x // 1.2)) in face_ids:
            centres.append((c.x, c.y, c.z))
    bm.to_mesh(me)
    bm.free()
    # glTF coordinates (Y up): (x, z, -y)
    with open(points_path, "w") as fh:
        json.dump([[x, z, -y] for x, y, z in centres], fh)
    d = os.path.dirname(os.path.abspath(maps_path))
    maps = {}
    for k, val in (("base_color", (180, 140, 110)), ("metallic", (0, 0, 0)), ("roughness", (200, 200, 200)),
                   ("ao", (255, 255, 255)), ("normal", (128, 128, 255))):
        p = os.path.join(d, k + ".png")
        img = bpy.data.images.new(k, tex, tex)
        img.pixels = [val[0] / 255.0, val[1] / 255.0, val[2] / 255.0, 1.0] * (tex * tex)
        img.filepath_raw = p
        img.file_format = "PNG"
        img.save()
        maps[k] = p
    with open(maps_path, "w") as fh:
        json.dump(maps, fh)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", use_selection=True)
else:
    src, out, points_path = args[1], args[2], args[3]
    _common.open_glb(src)
    ob = [o for o in bpy.context.scene.objects if o.type == "MESH"][0]
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bm.faces.ensure_lookup_table()
    uv = bm.loops.layers.uv.active
    isl = island_of(bm, uv)
    tris = {}
    for f in bm.faces:
        pts = [(loop[uv].uv.x, loop[uv].uv.y) for loop in f.loops]
        for i in range(1, len(pts) - 1):
            tris.setdefault(isl[f.index], []).append((pts[0], pts[i], pts[i + 1]))
    res = 2048
    count = np.zeros((res, res), dtype=np.uint8)
    for t in tris.values():
        count += uvfit.rasterise_tris(np.array(t, dtype=np.float64), res)
    with open(points_path) as fh:
        face_pts = json.load(fh)
    face_area = 0.0
    total_area = 0.0
    for f in bm.faces:
        c = ob.matrix_world @ f.calc_center_median()
        gl = (c.x, c.z, -c.y)
        pts = [(loop[uv].uv.x, loop[uv].uv.y) for loop in f.loops]
        area = sum(tri_area(pts[0], pts[i], pts[i + 1]) for i in range(1, len(pts) - 1))
        total_area += area
        if any(math.dist(gl, p) < 1e-3 for p in face_pts):
            face_area += area
    with open(out, "w") as fh:
        json.dump({"islands": len(tris), "overlap_px": int((count > 1).sum()), "used_share": float((count > 0).mean()),
                   "face_area": face_area, "total_area": total_area}, fh)
