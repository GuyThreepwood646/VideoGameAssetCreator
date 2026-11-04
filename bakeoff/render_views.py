"""Blender headless: render silhouette masks and shaded previews of a GLB from 8 yaw angles.

Run:  blender -b --python render_views.py -- <in.glb> <out_dir> [size]
Writes <out_dir>/sil_<yaw>.png (white on black, orthographic) and <out_dir>/shade_<yaw>.png (Workbench),
plus <out_dir>/info.json with triangle count, bounds and dimensions. Camera yaw 0 looks down -Y at the object
(Blender front view); yaw increases counter-clockwise seen from above.
"""
import json
import math
import os
import sys

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
src, out_dir = os.path.abspath(argv[0]), os.path.abspath(argv[1])
size = int(argv[2]) if len(argv) > 2 else 512
os.makedirs(out_dir, exist_ok=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
if not meshes:
    raise SystemExit("no mesh in " + src)

# Bounds and normalisation: centre at origin, largest dimension = 1.
pts = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
centre = (lo + hi) / 2
extent = max(hi - lo)
root = bpy.data.objects.new("root", None)
bpy.context.scene.collection.objects.link(root)
for o in meshes:
    if o.parent is None:
        o.parent = root
root.location = -centre
scale = bpy.data.objects.new("scale", None)
bpy.context.scene.collection.objects.link(scale)
root.parent = scale
scale.scale = (1 / extent,) * 3

tris = 0
for o in meshes:
    m = o.data
    m.calc_loop_triangles()
    tris += len(m.loop_triangles)

scene = bpy.context.scene
scene.render.resolution_x = scene.render.resolution_y = size
scene.render.resolution_percentage = 100
scene.render.engine = "BLENDER_WORKBENCH"
scene.render.film_transparent = True
scene.display.shading.light = "STUDIO"
scene.display.shading.color_type = "TEXTURE"
scene.view_settings.view_transform = "Standard"

cam_data = bpy.data.cameras.new("cam")
cam_data.type = "ORTHO"
cam_data.ortho_scale = 1.15
cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam

for yaw in range(0, 360, 15):
    a = math.radians(yaw)
    cam.location = (math.sin(a) * 3, -math.cos(a) * 3, 0)
    cam.rotation_euler = (math.radians(90), 0, a)
    scene.render.filepath = os.path.join(out_dir, f"shade_{yaw}.png")
    bpy.ops.render.render(write_still=True)

# Silhouettes: re-read the shaded PNG alpha in a second pass without the viewer (done in metrics.py).
json.dump({"triangles": tris, "extent": extent, "dims": [hi.x - lo.x, hi.y - lo.y, hi.z - lo.z],
           "objects": len(meshes)}, open(os.path.join(out_dir, "info.json"), "w"))
print("RENDER_OK", tris, "tris")
