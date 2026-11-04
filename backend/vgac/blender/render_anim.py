"""Blender headless: render frames of every NLA clip on an animated GLB from a 3/4 view (bakeoff/render_anim.py).

Run: blender -b --python render_anim.py -- <anim.glb> <out_dir> [frames=6] [size=384]
Writes <out_dir>/<clip>_<i>.png and <out_dir>/frames.json. animation_data.action must be None before the NLA
tracks are evaluated, otherwise the active action overrides every strip.
"""
import json
import math
import os
import sys

import bpy


def _import_glb(path):
    """glTF import without the bone display-shape helper object the importer otherwise adds for skins."""
    try:
        bpy.ops.import_scene.gltf(filepath=path, bone_shape_type="NONE")
    except TypeError:
        bpy.ops.import_scene.gltf(filepath=path)
    for o in [o for o in bpy.context.scene.objects if o.type == "MESH" and not o.vertex_groups and o.name.lower().startswith("icosphere")]:
        bpy.data.objects.remove(o, do_unlink=True)
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
src, out_dir = os.path.abspath(argv[0]), os.path.abspath(argv[1])
nframes = int(argv[2]) if len(argv) > 2 else 6
size = int(argv[3]) if len(argv) > 3 else 384
os.makedirs(out_dir, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
_import_glb(src)
arm = next((o for o in bpy.context.scene.objects if o.type == "ARMATURE"), None)
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
pts = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
centre, extent = (lo + hi) / 2, max(hi - lo)
scene = bpy.context.scene
scene.render.resolution_x = scene.render.resolution_y = size
scene.render.engine = "BLENDER_WORKBENCH"
scene.render.film_transparent = False
scene.display.shading.light = "STUDIO"
scene.display.shading.color_type = "TEXTURE"
scene.view_settings.view_transform = "Standard"
scene.world = bpy.data.worlds.new("w")
scene.world.color = (0.45, 0.45, 0.45)
cam_data = bpy.data.cameras.new("cam")
cam_data.type = "ORTHO"
cam_data.ortho_scale = extent * 1.3
cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
a = math.radians(35)
cam.location = centre + Vector((math.sin(a) * 4, -math.cos(a) * 4, 0.6)) * extent
cam.rotation_euler = (math.radians(82), 0, a)
ad = arm.animation_data if arm else None
if ad:
    ad.action = None
tracks = list(ad.nla_tracks) if ad else []
files = {}
for track in tracks:
    for t in tracks:
        t.mute = (t != track)
    strip = track.strips[0]
    files[track.name] = []
    for i in range(nframes):
        scene.frame_set(int(strip.frame_start + (strip.frame_end - strip.frame_start) * i / max(1, nframes - 1)))
        scene.render.filepath = os.path.join(out_dir, f"{track.name}_{i:02}.png")
        bpy.ops.render.render(write_still=True)
        files[track.name].append(os.path.basename(scene.render.filepath))
with open(os.path.join(out_dir, "frames.json"), "w") as f:
    json.dump({"clips": files, "frames": nframes, "size": size}, f, indent=1)
print("RENDER_ANIM_OK", sum(len(v) for v in files.values()), list(files))
