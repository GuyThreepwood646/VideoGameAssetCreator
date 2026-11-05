"""Blender headless: render frames of every NLA clip on an animated GLB (bakeoff/render_anim.py).

Run: blender -b --python render_anim.py -- <anim.glb> <out_dir> [frames=6] [size=384]
         [--view three_quarter|front|side|back|<degrees>] [--clips walk,run] [--margin 1.3]

Writes <out_dir>/<clip>_<i>.png and <out_dir>/frames.json ({clips: {name: [files]}, frames, size, view, order,
loop}). The camera is fixed for every clip, framed on the rest pose, so turns, lunges and falls show as movement.
Clip order and loop flags come from procanim's <anim>_clips.json sidecar when it sits next to the GLB, otherwise
from the NLA tracks. animation_data.action must be None before the NLA tracks are evaluated, otherwise the
active action overrides every strip. Frames are spread from the first to the last frame of each strip, so a
death clip's last image is its held final pose.
"""
import json
import math
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402  (shared helpers next to this script)
from mathutils import Vector  # noqa: E402

_VALUE_FLAGS = ("--view", "--clips", "--margin")
_raw = _common.after_double_dash()
_pos = [a for i, a in enumerate(_raw) if not a.startswith("--") and (i == 0 or _raw[i - 1] not in _VALUE_FLAGS)]
src, out_dir = os.path.abspath(_pos[0]), os.path.abspath(_pos[1])
nframes = int(_pos[2]) if len(_pos) > 2 else 6
size = int(_pos[3]) if len(_pos) > 3 else 384
view = _common.opt("--view", "three_quarter")
only = [c.strip() for c in _common.opt("--clips", "").split(",") if c.strip()]
margin = float(_common.opt("--margin", "1.3"))
VIEWS = {"front": 0.0, "three_quarter": 35.0, "side": 90.0, "back": 180.0}
azimuth = VIEWS[view] if view in VIEWS else float(view)

os.makedirs(out_dir, exist_ok=True)
sidecar_path = os.path.splitext(src)[0] + "_clips.json"
sidecar = None
if os.path.isfile(sidecar_path):
    with open(sidecar_path) as f:
        sidecar = json.load(f)

_common.open_glb(src)
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
cam_data.ortho_scale = extent * margin
cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
a = math.radians(azimuth)
cam.location = centre + Vector((math.sin(a) * 4, -math.cos(a) * 4, 0.6)) * extent
cam.rotation_euler = (math.radians(82), 0, a)
# A ground line helps judge drops and lifts: a thin dark plane at the rest pose's lowest point.
bpy.ops.mesh.primitive_plane_add(size=extent * 6, location=(centre.x, centre.y, lo.z - 0.002 * extent))
ground = bpy.context.active_object
gmat = bpy.data.materials.new("ground")
gmat.diffuse_color = (0.3, 0.3, 0.3, 1.0)
ground.data.materials.append(gmat)

ad = arm.animation_data if arm else None
if ad:
    ad.action = None
tracks = list(ad.nla_tracks) if ad else []
by_name = {t.name: t for t in tracks}
order = [c["name"] for c in (sidecar or {}).get("clips", []) if c["name"] in by_name]
order += [t.name for t in tracks if t.name not in order]
if only:
    order = [n for n in order if n in only]
loop = {c["name"]: bool(c.get("loop")) for c in (sidecar or {}).get("clips", [])}
files = {}
for name in order:
    track = by_name[name]
    for t in tracks:
        t.mute = (t != track)
    strip = track.strips[0]
    files[name] = []
    for i in range(nframes):
        scene.frame_set(int(round(strip.frame_start + (strip.frame_end - strip.frame_start) * i / max(1, nframes - 1))))
        scene.render.filepath = os.path.join(out_dir, f"{name}_{i:02}.png")
        bpy.ops.render.render(write_still=True)
        files[name].append(os.path.basename(scene.render.filepath))
with open(os.path.join(out_dir, "frames.json"), "w") as f:
    json.dump({"clips": files, "frames": nframes, "size": size, "view": view, "order": list(files),
               "loop": {n: loop.get(n) for n in files}}, f, indent=1)
print("RENDER_ANIM_OK", sum(len(v) for v in files.values()), list(files))
