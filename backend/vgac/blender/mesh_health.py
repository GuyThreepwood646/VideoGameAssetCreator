"""Blender headless: measure how whole a built 3D model is, and draw it grey with the texture off.

Run:  blender -b --python mesh_health.py -- <in.glb> <out_dir> [size=384]
Joins the seam duplicates glTF makes at texture seams (vertices closer than 1e-5 of the model's largest side), then
measures the surface: the share of faces that are level (see LEVEL_TOLERANCE), open edges (one face), bad joins (three or more faces on an edge), the share of edges folded back
on themselves, the share of sliver triangles, the number of separate pieces and the largest piece's share of the
surface area. Writes <out_dir>/health.json and grey_<yaw>.png for yaw 0, 90, 180, 270 (Workbench, one grey colour, studio
light, so a texture cannot hide damage). Distances are measured on the model scaled to a largest side of 1.
"""
import json
import math
import os
import sys

import bmesh
import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402
from mathutils import Vector

# Shares of edges whose two faces turn away from each other by more than each angle; the app uses the 120 degree one.
FOLD_ANGLES = (100, 120, 135, 150)
FOLD_DOTS = [math.cos(math.radians(a)) for a in FOLD_ANGLES]
SLIVER_QUALITY = 0.1  # 4*sqrt(3)*area / sum of squared side lengths: 1 for an equilateral triangle, near 0 for a sliver
JOIN_DISTANCE = 1e-5
LEVEL_TOLERANCE = 5  # degrees: a face is "level" when its normal is within this of horizontal (a wall) or of straight up or down

argv = _common.after_double_dash()
src, out_dir = os.path.abspath(argv[0]), os.path.abspath(argv[1])
size = int(argv[2]) if len(argv) > 2 else 384
os.makedirs(out_dir, exist_ok=True)

_common.open_glb(src)
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
if not meshes:
    raise SystemExit("no mesh in " + src)

# One bmesh in world space, scaled to a largest side of 1.
bm = bmesh.new()
for o in meshes:
    me = o.data.copy()
    me.transform(o.matrix_world)
    bm.from_mesh(me)
    bpy.data.meshes.remove(me)
pts = [v.co.copy() for v in bm.verts]
lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
extent = max(hi - lo) or 1.0
for v in bm.verts:
    v.co = (v.co - (lo + hi) / 2) / extent
bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=JOIN_DISTANCE)
bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
bm.edges.ensure_lookup_table()
bm.faces.ensure_lookup_table()

edges = len(bm.edges)
open_edges = bad_joins = 0
folded = [0] * len(FOLD_DOTS)
for e in bm.edges:
    n = len(e.link_faces)
    if n == 1:
        open_edges += 1
    elif n > 2:
        bad_joins += 1
    elif n == 2:
        a, b = e.link_faces
        d = a.normal.dot(b.normal)
        for i, t in enumerate(FOLD_DOTS):
            if d < t:
                folded[i] += 1

tris = slivers = 0
area_total = level_area = 0.0
LEVEL_SIN = math.sin(math.radians(LEVEL_TOLERANCE))
for f in bm.faces:
    area = f.calc_area()
    area_total += area
    ny = abs(f.normal.z)  # Blender's up axis
    if ny < LEVEL_SIN or ny > math.cos(math.radians(LEVEL_TOLERANCE)):
        level_area += area
    n = len(f.verts) - 2  # triangles this face makes
    tris += n
    if len(f.verts) == 3:
        sq = sum(e.calc_length() ** 2 for e in f.edges)
        if sq <= 0 or 4 * math.sqrt(3) * area / sq < SLIVER_QUALITY:
            slivers += 1

# Pieces: faces joined through shared vertices.
seen = set()
piece_areas = []
for f in bm.faces:
    if f.index in seen:
        continue
    stack, area = [f], 0.0
    seen.add(f.index)
    while stack:
        cur = stack.pop()
        area += cur.calc_area()
        for v in cur.verts:
            for nb in v.link_faces:
                if nb.index not in seen:
                    seen.add(nb.index)
                    stack.append(nb)
    piece_areas.append(area)
piece_areas.sort(reverse=True)

report = {
    "triangles": tris, "edges": edges, "open_edges": open_edges, "bad_join_edges": bad_joins,
    "folded": {str(a): round(folded[i] / max(1, edges), 5) for i, a in enumerate(FOLD_ANGLES)},
    "sliver_share": round(slivers / max(1, len(bm.faces)), 5),
    "level_share": round(level_area / area_total, 5) if area_total else 1.0,
    "pieces": len(piece_areas),
    "largest_piece_area": round(piece_areas[0] / area_total, 5) if piece_areas and area_total else 0.0,
}
bm.free()

# Grey render on the original objects, texture off.
scene = bpy.context.scene
root = bpy.data.objects.new("root", None)
scene.collection.objects.link(root)
for o in scene.objects:
    if o.parent is None and o is not root:
        o.parent = root
root.location = -(lo + hi) / 2
scale = bpy.data.objects.new("scale", None)
scene.collection.objects.link(scale)
root.parent = scale
scale.scale = (1 / extent,) * 3
scene.render.resolution_x = scene.render.resolution_y = size
scene.render.resolution_percentage = 100
scene.render.engine = "BLENDER_WORKBENCH"
scene.render.film_transparent = False
scene.display.shading.light = "STUDIO"
scene.display.shading.color_type = "SINGLE"
scene.display.shading.single_color = (0.62, 0.62, 0.62)
scene.display.shading.show_backface_culling = False
scene.view_settings.view_transform = "Standard"
world = bpy.data.worlds.new("w")
world.color = (0.9, 0.9, 0.92)
scene.world = world
cam_data = bpy.data.cameras.new("cam")
cam_data.type = "ORTHO"
cam_data.ortho_scale = 1.15
cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
yaws = [0, 90, 180, 270]
for yaw in yaws:
    a = math.radians(yaw)
    cam.location = (math.sin(a) * 3, -math.cos(a) * 3, 0.15)
    cam.rotation_euler = (math.radians(84), 0, a)
    scene.render.filepath = os.path.join(out_dir, f"grey_{yaw}.png")
    bpy.ops.render.render(write_still=True)
report["yaws"] = yaws
with open(os.path.join(out_dir, "health.json"), "w") as f:
    json.dump(report, f)
print("HEALTH_OK", json.dumps(report))
