"""Blender headless: sprite frames from an animated (or static) GLB (bakeoff/sprites.py).

Run: blender -b --python sprites.py -- <model.glb> <out_dir> [directions=8] [frames=8] [size=128] [--pixel]
For every NLA clip (or a single "static" row when there is none) and every direction (yaw steps around the model),
renders `frames` evenly spaced frames with an orthographic camera at a 30 degree elevation on a transparent
background into <out_dir>/_frames and writes atlas_frames.json with the frame rectangles. vgac.sprites.pack_sprites
packs them into <clip>.png (rows = directions, columns = frames) plus atlas.json. --pixel renders with no
anti-aliasing for pixel-art styles.
"""
import json
import math
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402  (shared helpers next to this script)
from mathutils import Vector

argv = _common.after_double_dash()
src, out_dir = os.path.abspath(argv[0]), os.path.abspath(argv[1])
nums = [a for a in argv[2:] if not a.startswith("--")]
directions = int(nums[0]) if len(nums) > 0 else 8
frames = int(nums[1]) if len(nums) > 1 else 8
size = int(nums[2]) if len(nums) > 2 else 128
pixel = _common.flag("--pixel")
os.makedirs(out_dir, exist_ok=True)

_common.open_glb(src)
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
pts = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
centre, extent = (lo + hi) / 2, max(hi - lo)

scene = bpy.context.scene
scene.render.resolution_x = scene.render.resolution_y = size
scene.render.engine = "BLENDER_WORKBENCH"
scene.render.film_transparent = True
scene.display.shading.light = "STUDIO"
scene.display.shading.color_type = "TEXTURE"
scene.view_settings.view_transform = "Standard"
scene.render.image_settings.color_mode = "RGBA"
if pixel:
    scene.render.filter_size = 0.0
    scene.display.render_aa = "OFF"
cam_data = bpy.data.cameras.new("cam")
cam_data.type = "ORTHO"
cam_data.ortho_scale = extent * 1.25
cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
elev = math.radians(30)

clips = []
arm = arms[0] if arms else None
if arm and arm.animation_data:
    arm.animation_data.action = None
    clips = [(t.name, t.strips[0].frame_start, t.strips[0].frame_end) for t in arm.animation_data.nla_tracks if t.strips]
if not clips:
    clips = [("static", 1, 1)]

atlas = {"size": size, "directions": directions, "frames": frames, "clips": {}}
tmp = os.path.join(out_dir, "_frames")
os.makedirs(tmp, exist_ok=True)
for name, f0, f1 in clips:
    if arm and arm.animation_data:
        for t in arm.animation_data.nla_tracks:
            t.mute = (t.name != name)
    entries = []
    nf = 1 if name == "static" else frames
    for d in range(directions):
        yaw = 2 * math.pi * d / directions
        dist = extent * 4
        cam.location = centre + Vector((math.sin(yaw) * math.cos(elev), -math.cos(yaw) * math.cos(elev), math.sin(elev))) * dist
        cam.rotation_euler = (math.pi / 2 - elev, 0, yaw)
        for i in range(nf):
            if name != "static":
                scene.frame_set(int(f0 + (f1 - f0) * i / max(1, nf)))  # loop: last frame excluded
            scene.render.filepath = os.path.join(tmp, f"{name}_{d}_{i}.png")
            bpy.ops.render.render(write_still=True)
            entries.append({"dir": d, "frame": i, "x": i * size, "y": d * size, "w": size, "h": size, "file": scene.render.filepath})
    atlas["clips"][name] = {"frames": nf, "rows": directions, "cols": nf, "entries": [{k: v for k, v in e.items() if k != "file"} for e in entries], "files": [e["file"] for e in entries]}

with open(os.path.join(out_dir, "atlas_frames.json"), "w") as f:
    json.dump(atlas, f)
print("SPRITES_OK", {k: v["frames"] for k, v in atlas["clips"].items()})
