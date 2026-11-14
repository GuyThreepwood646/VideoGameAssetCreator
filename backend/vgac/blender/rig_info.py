"""Blender headless: inspect a rigged GLB and render it in rest pose and in bend-test poses (bakeoff/rig_info.py).

Run: blender -b --python rig_info.py -- <rigged.glb> <out_dir> [angles=35,90]
Writes rig_info.json (bone count and names, hierarchy, weight coverage, influences per vertex, weight normalisation)
and rest.png plus bend_<angle>.png (front view, bones drawn). The bend test rotates every bone whose name contains one
of the test keywords by the angle, which shows whether the weights follow the skeleton without knowledge of the naming.
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
angles = [float(a) for a in (argv[2] if len(argv) > 2 else "35,90").split(",") if a.strip()]
os.makedirs(out_dir, exist_ok=True)

_common.open_glb(src)
arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
info = {"armatures": len(arms), "meshes": len(meshes), "bones": 0, "bone_names": [], "verts": 0, "weighted_verts": 0,
        "max_influences": 0, "over_4_influences": 0, "unnormalised_verts": 0, "roots": [], "bone_segments": []}
if arms:
    arm = arms[0]
    info["bones"] = len(arm.data.bones)
    info["bone_names"] = [b.name for b in arm.data.bones]
    info["roots"] = [b.name for b in arm.data.bones if b.parent is None]
    info["bone_segments"] = [{"name": b.name, "parent": b.parent.name if b.parent else None,
                              "head": list(arm.matrix_world @ b.head_local), "tail": list(arm.matrix_world @ b.tail_local)}
                             for b in arm.data.bones]
info["unskinned_meshes"] = [m.name for m in meshes if not m.vertex_groups]
for m in [m for m in meshes if m.vertex_groups] or meshes:
    info["verts"] += len(m.data.vertices)
    for v in m.data.vertices:
        ws = [g.weight for g in v.groups if g.weight > 0.01]
        if ws:
            info["weighted_verts"] += 1
        info["max_influences"] = max(info["max_influences"], len(ws))
        if len(ws) > 4:
            info["over_4_influences"] += 1
        total_w = sum(g.weight for g in v.groups)
        if ws and abs(total_w - 1.0) > 0.02:
            info["unnormalised_verts"] += 1
info["weight_coverage"] = (info["weighted_verts"] / info["verts"]) if info["verts"] else 0.0
# How far each finger chain lies from the hand's skin: for every joint of a finger's three bones (and the last bone's end), the distance to the
# nearest skin vertex within 28 cm of the wrist; the worst joint, in cm. A chain that lies in the hand has joints within 3 cm of skin (thumb tips
# reach 2.8 cm); one outside it is not on the hand. {side: {finger: cm}}; the rig check `fingers` reads it.
info["finger_chains"] = {}
if arms and any(m.vertex_groups for m in meshes):
    import numpy as _np

    _pts = _np.array([list(m.matrix_world @ v.co) for m in meshes if m.vertex_groups for v in m.data.vertices])
    for _side in ("Left", "Right"):
        _hand = arm.data.bones.get("mixamorig:" + _side + "Hand")
        if _hand is None:
            continue
        _w = _np.array(list(arm.matrix_world @ _hand.head_local))
        _near = _pts[_np.linalg.norm(_pts - _w, axis=1) < 0.28]
        if not len(_near):
            continue
        for _f in ("Thumb", "Index", "Middle", "Ring", "Pinky"):
            _ch = [arm.data.bones.get("mixamorig:" + _side + "Hand" + _f + str(_i)) for _i in (1, 2, 3)]
            if not all(_ch):
                continue
            _joints = [_np.array(list(arm.matrix_world @ b.head_local)) for b in _ch] + [_np.array(list(arm.matrix_world @ _ch[2].tail_local))]
            _d = max(float(_np.min(_np.linalg.norm(_near - j, axis=1))) for j in _joints)
            info["finger_chains"].setdefault(_side, {})[_f] = round(_d * 100, 1)
# Same segments in glTF space (Y up, -Z forward) for the web viewer: Blender (x, y, z) -> (x, z, -y).
info["bone_segments_gltf"] = [{"name": b["name"], "parent": b["parent"],
                               "head": [b["head"][0], b["head"][2], -b["head"][1]],
                               "tail": [b["tail"][0], b["tail"][2], -b["tail"][1]]} for b in info["bone_segments"]]

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
info["renders"] = {"rest": "rest.png"}

if arms:
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="POSE")
    keys = ("arm", "leg", "thigh", "shoulder", "upper", "neck", "tail", "spine", "hip", "knee", "elbow", "lid")
    bent = [pb.name for pb in arm.pose.bones if any(k in pb.name.lower() for k in keys) and pb.parent is not None
            and "extra_" not in pb.name.lower()]  # Extra_ bones (forearm twist) turn with their parent, not as forearms
    if not bent:
        bent = [pb.name for pb in list(arm.pose.bones)[1:6]]
    info["bent"] = bent
    for angle in angles:
        for pb in arm.pose.bones:
            if pb.name in bent:
                pb.rotation_mode = "XYZ"
                pb.rotation_euler = (math.radians(angle), 0, 0)
        bpy.ops.object.mode_set(mode="OBJECT")
        key = f"bend_{int(angle)}"
        scene.render.filepath = os.path.join(out_dir, key + ".png")
        bpy.ops.render.render(write_still=True)
        info["renders"][key] = key + ".png"
        bpy.ops.object.mode_set(mode="POSE")
    bpy.ops.object.mode_set(mode="OBJECT")

info["camera"] = {"centre": list(centre), "extent": extent, "ortho_scale": cam_data.ortho_scale, "res": 768}
with open(os.path.join(out_dir, "rig_info.json"), "w") as f:
    json.dump(info, f, indent=1)
print("RIG_OK", info["bones"], "bones,", info["weighted_verts"], "/", info["verts"], "weighted verts")
