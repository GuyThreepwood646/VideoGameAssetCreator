"""Blender headless: close-up renders of both hands through a set of clips (WP-76 hand preview and review sheets).

Run: blender -b --python hand_sheet.py -- <anim.glb> <out_dir> <clips comma list> [frames=4] [size=320]

Per clip, `frames` evenly spaced frames; per frame, one picture of each hand, taken along that hand's own curl axis
(the palm normal's rotation axis, so a finger's bend reads as movement in the picture). Bright studio lighting: a key
light 45 degrees front-left and above, a half-strength fill front-right, a rim light behind, a soft world, Standard
view transform, exposure 0, light-grey background.

Writes <out_dir>/<clip>_<i>_<side>.png and <out_dir>/hands.json ({side, size, clips: {name: {frames, frame_numbers}}}).
The camera framing is fixed per hand over the whole clip, so the hand moves within the picture.
"""
import json
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402  (shared helpers next to this script)
from mathutils import Vector  # noqa: E402

_raw = _common.after_double_dash()
src, out_dir = os.path.abspath(_raw[0]), os.path.abspath(_raw[1])
clip_names = [c.strip() for c in (_raw[2] if len(_raw) > 2 else "").split(",") if c.strip()]
nframes = int(_raw[3]) if len(_raw) > 3 else 4
size = int(_raw[4]) if len(_raw) > 4 else 320
os.makedirs(out_dir, exist_ok=True)

_common.open_glb(src)
scene = bpy.context.scene
arm = max([o for o in scene.objects if o.type == "ARMATURE"], key=lambda o: len(o.data.bones))
arm.animation_data_create()
for t in arm.animation_data.nla_tracks:
    t.mute = True
prefix = ""
for b in arm.data.bones:
    if b.name.endswith("LeftForeArm"):
        prefix = b.name[: -len("LeftForeArm")]
        break
SIDES = ("Left", "Right")
FINGERS = ("Thumb", "Index", "Middle", "Ring", "Pinky")


def hand_names(side):
    """(hand, [every finger bone of this hand that the rig has])."""
    hand = f"{prefix}{side}Hand"
    fb = [f"{prefix}{side}Hand{f}{k}" for f in FINGERS for k in (1, 2, 3)]
    return hand, [n for n in fb if n in arm.data.bones]


# Render settings: Cycles on the CPU, bright studio.
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = 48
scene.cycles.use_denoising = False
scene.render.resolution_x = size
scene.render.resolution_y = size
scene.render.resolution_percentage = 100
scene.render.film_transparent = False
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"
scene.view_settings.exposure = 0.0
scene.view_settings.gamma = 1.0
scene.render.image_settings.file_format = "PNG"
world = scene.world or bpy.data.worlds.new("studio")
scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes.get("Background")
if bg is not None:
    bg.inputs[0].default_value = (0.78, 0.78, 0.78, 1.0)
    bg.inputs[1].default_value = 1.0


def light(name, loc, energy, size_):
    d = bpy.data.lights.new(name, type="AREA")
    d.energy = energy
    d.size = size_
    o = bpy.data.objects.new(name, d)
    o.location = loc
    scene.collection.objects.link(o)
    return o


def clear_lights():
    for o in [o for o in scene.objects if o.type == "LIGHT"]:
        bpy.data.objects.remove(o, do_unlink=True)


def sample_frames(act):
    """nframes frames spread evenly over the action, both ends included."""
    f0, f1 = int(act.frame_range[0]), int(act.frame_range[1])
    if nframes <= 1 or f1 <= f0:
        return [f0]
    return sorted({int(round(f0 + (f1 - f0) * i / (nframes - 1))) for i in range(nframes)})


def curl_axis(side):
    """The hand's curl axis at rest (world): f x n with the palm normal as procanim takes it."""
    hand, _ = hand_names(side)
    W = arm.matrix_world

    def head(n):
        return W @ arm.data.bones[n].head_local

    wrist = head(hand)
    knuck = [head(f"{prefix}{side}Hand{f}1") for f in ("Index", "Middle", "Ring", "Pinky") if f"{prefix}{side}Hand{f}1" in arm.data.bones]
    if len(knuck) < 2:
        return (W.to_3x3() @ arm.data.bones[hand].y_axis).normalized()
    fdir = ((sum(knuck, Vector()) / len(knuck)) - wrist).normalized()
    thumb_b = f"{prefix}{side}HandThumb1"
    t = (head(thumb_b) - wrist) if thumb_b in arm.data.bones else (knuck[-1] - knuck[0])
    t = (t - fdir * t.dot(fdir)).normalized()
    n = fdir.cross(t).normalized()
    if side == "Right":
        n = -n
    return fdir.cross(n).normalized()


def hand_bounds(side, frames_):
    """World centre and size of the hand over the given frames (evaluated pose)."""
    hand, fingers = hand_names(side)
    W = arm.matrix_world
    pts = []
    for f in frames_:
        scene.frame_set(f)
        bpy.context.view_layer.update()
        pb = arm.evaluated_get(bpy.context.evaluated_depsgraph_get()).pose.bones
        for n in [hand] + fingers:
            if n in pb:
                pts.append(W @ pb[n].matrix.translation)
                pts.append(W @ (pb[n].matrix @ Vector((0, pb[n].bone.length, 0))))
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return (lo + hi) * 0.5, max((hi - lo).length, 0.05)


def _unused_add_camera(side, centre, reach, axis):
    cam_d = bpy.data.cameras.new(f"cam_{side}")
    cam_d.type = "ORTHO"
    cam_d.ortho_scale = reach * 1.6
    cam = bpy.data.objects.new(f"cam_{side}", cam_d)
    scene.collection.objects.link(cam)
    cam.location = centre + axis * reach * 3.0
    cam.rotation_mode = "QUATERNION"
    cam.rotation_quaternion = (centre - cam.location).normalized().to_track_quat("-Z", "Z")
    scene.camera = cam
    return cam


# One fixed camera on the whole figure, in front of it (the body faces -Y, as procanim's forward does on these rigs);
# each picture is a render region cut around the hand of that frame, so the hand is always in the picture.
from bpy_extras.object_utils import world_to_camera_view  # noqa: E402

FULL = 1024
REGION = 0.16  # the picture's width as a share of the full frame (the hand fills the cell)
W_arm = arm.matrix_world
_bb = [W_arm @ b.head_local for b in arm.data.bones]
_lo = Vector((min(p.x for p in _bb), min(p.y for p in _bb), min(p.z for p in _bb)))
_hi = Vector((max(p.x for p in _bb), max(p.y for p in _bb), max(p.z for p in _bb)))
BODY_C = (_lo + _hi) * 0.5
BODY_H = max((_hi - _lo).z, 0.5)
FORWARD = Vector((0.0, -1.0, 0.0))

cam_d = bpy.data.cameras.new("cam_body")
cam_d.type = "ORTHO"
cam_d.ortho_scale = BODY_H * 1.25
cam_d.clip_start = 0.01
cam_d.clip_end = 100.0
CAM = bpy.data.objects.new("cam_body", cam_d)
scene.collection.objects.link(CAM)
CAM.location = BODY_C + FORWARD * BODY_H * 3.0
CAM.rotation_mode = "QUATERNION"
CAM.rotation_quaternion = (BODY_C - CAM.location).normalized().to_track_quat("-Z", "Z")
scene.camera = CAM
scene.render.resolution_x = FULL
scene.render.resolution_y = FULL
scene.render.use_border = True
scene.render.use_crop_to_border = True
clear_lights()
key_pos = BODY_C + Vector((-1.0, -1.0, 1.0)) * BODY_H * 0.9 + Vector((0, 0, 0.6))
light("key", key_pos, 900.0, BODY_H * 0.8)
light("fill", BODY_C + Vector((1.0, -1.0, 0.3)) * BODY_H * 0.9, 300.0, BODY_H * 0.8)
light("rim", BODY_C + Vector((0.0, 1.0, 1.0)) * BODY_H * 0.9, 400.0, BODY_H * 0.8)


def hand_centre(side, f):
    """Centre of this hand in world space at frame f (the wrist and every finger bone the rig has)."""
    hand, fingers = hand_names(side)
    scene.frame_set(f)
    bpy.context.view_layer.update()
    pb = arm.evaluated_get(bpy.context.evaluated_depsgraph_get()).pose.bones
    pts = [W_arm @ pb[n].matrix.translation for n in [hand] + fingers if n in pb]
    return sum(pts, Vector()) / max(len(pts), 1)


result = {"size": size, "sides": {}, "clips": {}}
for clip in clip_names:
    act = bpy.data.actions.get(clip)
    if act is None:
        continue
    arm.animation_data.action = act
    frames_ = sample_frames(act)
    entry = {"frames": [], "frame_numbers": frames_}
    for side in SIDES:
        hand, _ = hand_names(side)
        if hand not in arm.data.bones:
            continue
        result["sides"][side] = {"axis": [round(c, 4) for c in curl_axis(side)]}
        for i, f in enumerate(frames_):
            c = hand_centre(side, f)
            px = world_to_camera_view(scene, CAM, c)
            half = REGION / 2.0
            # Keep the cut inside the frame, so a hand that leaves the frame still gets a picture (at the edge).
            cx = min(max(px.x, half), 1.0 - half)
            cy = min(max(px.y, half), 1.0 - half)
            x0, x1 = cx - half, cx + half
            y0, y1 = cy - half, cy + half
            scene.render.border_min_x, scene.render.border_max_x = x0, x1
            scene.render.border_min_y, scene.render.border_max_y = y0, y1
            scene.render.resolution_x = FULL
            scene.render.resolution_y = FULL
            scene.render.resolution_percentage = 100
            name = f"{clip}_{i}_{side.lower()}.png"
            scene.render.filepath = os.path.join(out_dir, name)
            bpy.ops.render.render(write_still=True)
            entry["frames"].append({"side": side, "file": name})
    result["clips"][clip] = entry
    arm.animation_data.action = None

clear_lights()
with open(os.path.join(out_dir, "hands.json"), "w") as fh:
    json.dump(result, fh, indent=1)
print("HANDSHEET_OK", sorted(result["clips"]))
