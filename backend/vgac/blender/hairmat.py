"""Blender headless: lay the hair out on its own texture and bake the hair's maps there (hair as its own material,
docs/HAIR-RESEARCH.md 3.2 (b), stage 2).

Run: blender -b --python hairmat.py -- <in.glb> <out_dir> --points hair_material_points.json --maps maps.json
     [--tex 1024] [--padding 4] [--margin 8]

The hair pass (stages/hair_detail.py, vgac/hairmat.py) picks the hair triangles that get their own material: the
centres of those triangles (glTF coordinates, Y up) are --points. The model itself is not changed: the prep GLB keeps
one material and one texture layout, so the rig step and every preview read it as before. This script:
1. copies the hair triangles into an object of their own and packs their texture pieces (UV islands, as the main
   layout cut them) alone into the square, scaled up together so the hair fills its own texture;
2. bakes every map in --maps (the main layout's base colour, normal, roughness, occlusion) from the whole model onto
   that layout at --tex (blender/rebake.py), so the hair's colour, bumps and shading carry over;
3. writes <out_dir>/hair_layout.json: for each hair triangle its centre and corners (Blender coordinates, Z up) and
   its three texture coordinates on the hair texture, plus the model's bounding box, so blender/pack.py can give the
   same triangles the hair material and these coordinates in the files that ship (the rig step's GLB has no UVs of
   its own; pack.py matches faces by their centres in the bounding box, as it does for the main layout).

Writes <out_dir>/<key>.png for each map and <out_dir>/hairmat_report.json: faces, islands, fill (share of the square
the pieces cover), island_scale (how many times the pieces grew across), rebake_seconds.
"""
import json
import math
import os
import sys
import time

import bmesh
import bpy
from mathutils import Vector
from mathutils.kdtree import KDTree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402
import rebake  # noqa: E402

argv = _common.after_double_dash()
src, out_dir = os.path.abspath(argv[0]), os.path.abspath(argv[1])
points_json = _common.opt("--points", None)
maps_json = _common.opt("--maps", None)
tex_size = int(_common.opt("--tex", 1024))
padding_px = int(_common.opt("--padding", max(4, tex_size // 256)))
margin_px = int(_common.opt("--margin", max(8, tex_size // 128)))
pack_shape = _common.opt("--pack-shape", "AABB")  # boxes: 0.1 s and 0.49 filled; CONCAVE 0.59 but 12 s; CONVEX 47 s (fresh knight)
os.makedirs(out_dir, exist_ok=True)
with open(points_json) as f:
    points = json.load(f)
with open(maps_json) as f:
    maps = {k: v for k, v in json.load(f).items() if v and os.path.isfile(v)}

T_START = time.time()
_common.open_glb(src)
T_OPEN = time.time()
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
if not meshes:
    raise SystemExit("no mesh")
mesh = max(meshes, key=lambda o: len(o.data.polygons))
mw = mesh.matrix_world

kd = KDTree(max(1, len(points)))
for i, p in enumerate(points):
    kd.insert(Vector((p[0], -p[2], p[1])), i)  # glTF (x, y, z), Y up -> Blender (x, -z, y)
kd.balance()
hair_idx = set()
for poly in mesh.data.polygons:
    _, _, dist = kd.find(mw @ poly.center)
    if dist is not None and dist < 1e-4:
        hair_idx.add(poly.index)
if not hair_idx:
    raise SystemExit("no hair faces matched")

# the whole model with the main layout is the bake source
source = rebake.copy_object(mesh, "vgac_bake_source")

# the hair on its own: a copy with every other face removed
hair = rebake.copy_object(mesh, "vgac_hair")
hair.matrix_world = mw.copy()
bm = bmesh.new()
bm.from_mesh(hair.data)
bm.faces.ensure_lookup_table()
bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.index not in hair_idx], context="FACES")
bm.to_mesh(hair.data)
bm.free()
hair.data.update()


def uv_area(obj):
    total = 0.0
    lay = obj.data.uv_layers.active.data
    for poly in obj.data.polygons:
        li = list(poly.loop_indices)
        p0 = lay[li[0]].uv
        for i in range(1, len(li) - 1):
            p1, p2 = lay[li[i]].uv, lay[li[i + 1]].uv
            total += abs((p1.x - p0.x) * (p2.y - p0.y) - (p2.x - p0.x) * (p1.y - p0.y)) / 2
    return total


area_before = uv_area(hair)
T_PACK0 = time.time()
bpy.ops.object.select_all(action="DESELECT")
for o in list(bpy.context.scene.objects):
    o.select_set(False)
hair.select_set(True)
bpy.context.view_layer.objects.active = hair
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.select_all(action="SELECT")
# Blender's packer keeps the margin on every side of every piece, so half the padding gives the padding between pieces
margin = max(1.0, padding_px / 2.0) / float(max(1, tex_size))
try:
    bpy.ops.uv.pack_islands(rotate=True, rotate_method="ANY", scale=True, margin_method="FRACTION", margin=margin,
                            shape_method=pack_shape)
except (TypeError, RuntimeError) as e:
    print("HAIRMAT concave packing unavailable:", e)
    bpy.ops.uv.pack_islands(rotate=True, scale=True, margin=margin)
bpy.ops.object.mode_set(mode="OBJECT")
area_after = uv_area(hair)
T_PACK1 = time.time()

# the layout file, before the bake changes anything
lay = hair.data.uv_layers.active.data
hw = hair.matrix_world
faces_out = []
for poly in hair.data.polygons:
    li = list(poly.loop_indices)
    if len(li) != 3:
        continue  # the GLB is triangles; anything else is skipped rather than guessed
    c = hw @ poly.center
    corners = [hw @ hair.data.vertices[hair.data.loops[i].vertex_index].co for i in li]
    row = [c.x, c.y, c.z]
    for p in corners:
        row += [p.x, p.y, p.z]
    for i in li:
        row += [lay[i].uv.x, lay[i].uv.y]
    faces_out.append([round(v, 6) for v in row])
pts = [mw @ v.co for v in mesh.data.vertices]
lo = [min(p[i] for p in pts) for i in range(3)]
hi = [max(p[i] for p in pts) for i in range(3)]
with open(os.path.join(out_dir, "hair_layout.json"), "w") as f:
    json.dump({"tex": tex_size, "coords": "blender", "box": [lo, hi],
               "row": "centre xyz, corner xyz x3, uv x3 (v up)", "faces": faces_out}, f)

t0 = time.time()
bake = rebake.rebake(source, hair, maps, out_dir, tex_size, padding_px, margin_px, repack=False)
report = {"faces": len(faces_out), "faces_matched": len(hair_idx), "points": len(points), "tex": tex_size,
          "fill": round(area_after, 4), "uv_area_before": round(area_before, 6),
          "island_scale": round(math.sqrt(area_after / max(area_before, 1e-12)), 3),
          "padding_px": padding_px, "margin_px": margin_px, **bake, "hairmat_seconds": round(time.time() - t0, 2),
          "open_seconds": round(T_OPEN - T_START, 2), "pack_seconds": round(T_PACK1 - T_PACK0, 2),
          "script_seconds": round(time.time() - T_START, 2)}
with open(os.path.join(out_dir, "hairmat_report.json"), "w") as f:
    json.dump(report, f, indent=1)
print("HAIRMAT_OK", json.dumps(report))
