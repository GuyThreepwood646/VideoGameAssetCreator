"""Blender headless: inspect a rigged GLB and render it in rest pose and in one test pose.

Run: blender -b --python rig_info.py -- <rigged.glb> <out_dir>
Prints bone count and names, vertex-weight coverage, and writes rest.png and bend.png (front view, bones drawn).
The bend test rotates every bone whose name contains one of the test keywords by 35 degrees, which shows whether
the weights follow the skeleton without any knowledge of the naming scheme.
"""
import json
import math
import os
import sys

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
src, out_dir = os.path.abspath(argv[0]), os.path.abspath(argv[1])
os.makedirs(out_dir, exist_ok=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
info = {"armatures": len(arms), "meshes": len(meshes), "bones": 0, "bone_names": [], "verts": 0, "weighted_verts": 0}
if arms:
    arm = arms[0]
    info["bones"] = len(arm.data.bones)
    info["bone_names"] = [b.name for b in arm.data.bones]
    info["roots"] = [b.name for b in arm.data.bones if b.parent is None]
    info["bone_segments"] = [{"name": b.name, "parent": b.parent.name if b.parent else None,
                              "head": list(arm.matrix_world @ b.head_local), "tail": list(arm.matrix_world @ b.tail_local)}
                             for b in arm.data.bones]
for m in meshes:
    info["verts"] += len(m.data.vertices)
    info["weighted_verts"] += sum(1 for v in m.data.vertices if any(g.weight > 0.01 for g in v.groups))

# Camera: front, orthographic, framed on the whole scene.
pts = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
centre, extent = (lo + hi) / 2, max(hi - lo)
scene = bpy.context.scene
scene.render.resolution_x = scene.render.resolution_y = 768
scene.render.engine = "BLENDER_WORKBENCH"
scene.render.film_transparent = True
scene.display.shading.light = "STUDIO"
scene.display.shading.color_type = "SINGLE"
scene.display.shading.show_xray = True
scene.display.shading.xray_alpha = 0.6
scene.view_settings.view_transform = "Standard"
cam_data = bpy.data.cameras.new("cam")
cam_data.type = "ORTHO"
cam_data.ortho_scale = extent * 1.15
cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
cam.location = centre + Vector((0, -extent * 4, 0))
cam.rotation_euler = (math.radians(90), 0, 0)
if arms:
    arm.show_in_front = True
    arm.data.display_type = "OCTAHEDRAL"

scene.render.filepath = os.path.join(out_dir, "rest.png")
bpy.ops.render.render(write_still=True)

if arms:
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="POSE")
    keys = ("arm", "leg", "thigh", "shoulder", "upper", "neck", "tail", "spine", "hip", "knee", "elbow", "lid")
    bent = []
    for pb in arm.pose.bones:
        n = pb.name.lower()
        if any(k in n for k in keys) and pb.parent is not None:
            pb.rotation_mode = "XYZ"
            pb.rotation_euler = (math.radians(35), 0, 0)
            bent.append(pb.name)
    if not bent:
        for pb in list(arm.pose.bones)[1:6]:
            pb.rotation_mode = "XYZ"
            pb.rotation_euler = (math.radians(35), 0, 0)
            bent.append(pb.name)
    info["bent"] = bent
    bpy.ops.object.mode_set(mode="OBJECT")
    scene.render.filepath = os.path.join(out_dir, "bend.png")
    bpy.ops.render.render(write_still=True)

info["camera"] = {"centre": list(centre), "extent": extent, "ortho_scale": cam_data.ortho_scale, "res": 768}
json.dump(info, open(os.path.join(out_dir, "rig_info.json"), "w"), indent=1)
print("RIG_OK", info["bones"], "bones,", info["weighted_verts"], "/", info["verts"], "weighted verts")
