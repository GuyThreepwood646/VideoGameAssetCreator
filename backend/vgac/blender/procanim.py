"""Blender headless: procedural idle and walk cycles for any SkinToken-style skeleton (bakeoff/procanim.py).

Run: blender -b --python procanim.py -- <rigged.glb> <out.glb> [fps=24]
Classifies bone chains by geometry alone (no naming needed): leg chains end near the ground, the spine runs from
the root toward the head, the tail is the rearmost low chain. Generates two looping actions, "idle" and "walk",
pushes them to NLA strips so exporters emit them as clips (Blender 5: strip.action_slot = action.slots[0]), and
writes a GLB with animations. Writes <out>_anim.json next to the output with the classification.
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
from mathutils import Quaternion, Vector

argv = sys.argv[sys.argv.index("--") + 1:]
src, dst = os.path.abspath(argv[0]), os.path.abspath(argv[1])
fps = int(argv[2]) if len(argv) > 2 else 24

bpy.ops.wm.read_factory_settings(use_empty=True)
_import_glb(src)
arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
if not arms:
    raise SystemExit("no armature")
arm = arms[0]
scene = bpy.context.scene
scene.render.fps = fps

# World-space bone heads and tails.
W = arm.matrix_world
bones = list(arm.data.bones)
head = {b.name: W @ b.head_local for b in bones}
tail = {b.name: W @ b.tail_local for b in bones}
pts = [p for o in meshes for p in (o.matrix_world @ Vector(c) for c in o.bound_box)]
lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
height = hi.z - lo.z
centre = (lo + hi) / 2
dims = hi - lo
# Body axis: longest horizontal extent. Up is +Z after glTF import.
fwd_axis = Vector((1, 0, 0)) if dims.x >= dims.y else Vector((0, 1, 0))
up = Vector((0, 0, 1))

roots = [b for b in bones if b.parent is None]
leaves = [b for b in bones if not b.children]


def chain_to_root(b):
    out = []
    while b is not None:
        out.append(b)
        b = b.parent
    return out  # leaf first


# Leg chains: leaves whose head sits in the bottom 20 percent of the height.
leg_leaves = [b for b in leaves if (head[b.name].z - lo.z) < 0.2 * height]
# Reduce to the lowest leaf per limb cluster (cluster by horizontal distance).
leg_leaves.sort(key=lambda b: head[b.name].z)
clusters = []
for b in leg_leaves:
    p = head[b.name]
    if all((p - head[c.name]).xy.length > 0.15 * max(dims.x, dims.y) for c in clusters):
        clusters.append(b)
legs = clusters[:4]
# Orient forward: the head end is the end of the body axis farther from the tail; decide by where most non-leg
# leaves above mid-height sit (the head and ears), else by the root position.
upper_leaves = [b for b in leaves if b not in leg_leaves and (head[b.name].z - lo.z) > 0.5 * height]
if upper_leaves:
    mean_upper = sum((head[b.name] for b in upper_leaves), Vector()) / len(upper_leaves)
    forward = fwd_axis if (mean_upper - centre).dot(fwd_axis) >= 0 else -fwd_axis
else:
    forward = fwd_axis
lateral = up.cross(forward).normalized()

kind = "quadruped" if len(legs) >= 4 else ("biped" if len(legs) >= 2 else "unknown")
# Leg roles by position: front/back along forward, left/right along lateral.
leg_info = []
for b in legs:
    p = head[b.name] - centre
    f = p.dot(forward)
    l = p.dot(lateral)
    leg_info.append({"leaf": b.name, "front": f > 0, "left": l > 0, "chain": [x.name for x in chain_to_root(b)]})
# Each leg chain: from leaf up to (but excluding) the first bone shared with another leg or the spine.
all_chains = [set(li["chain"]) for li in leg_info]
for li in leg_info:
    own = []
    for name in li["chain"]:
        if sum(1 for c in all_chains if name in c) > 1:
            break
        own.append(name)
    li["bones"] = own[::-1]  # top of leg first
# Spine: root chain to the highest upper leaf (head). Tail: lowest rear leaf not in legs.
spine_leaf = max(upper_leaves, key=lambda b: head[b.name].z) if upper_leaves else None
spine = [x.name for x in chain_to_root(spine_leaf)][::-1] if spine_leaf else []
leg_bone_set = {n for li in leg_info for n in li["bones"]}
rear_leaves = [b for b in leaves if b.name not in leg_bone_set and b not in upper_leaves and (head[b.name] - centre).dot(forward) < -0.2 * dims.dot(forward.copy().normalized() if forward.length else forward)]
tail_leaf = min(rear_leaves, key=lambda b: (head[b.name] - centre).dot(forward)) if rear_leaves else None
tail_chain = []
if tail_leaf:
    for b in chain_to_root(tail_leaf):
        if b.name in spine or b.name in leg_bone_set:
            break
        tail_chain.append(b.name)
    tail_chain = tail_chain[::-1]
head_bones = spine[-2:] if len(spine) >= 2 else spine
torso = spine[:-2] if len(spine) > 3 else spine[:1]

report = {"kind": kind, "forward": list(forward), "lateral": list(lateral), "height": height,
          "legs": [{k: v for k, v in li.items() if k != "chain"} for li in leg_info], "spine": spine, "tail": tail_chain, "head": head_bones,
          "fps": fps, "clips": ["idle", "walk"]}


def local_axis(pb, world_axis):
    m = (W @ pb.bone.matrix_local).to_3x3()
    return (m.inverted() @ world_axis).normalized()


def set_rot(pb, world_axis, degrees, frame):
    pb.rotation_mode = "QUATERNION"
    pb.rotation_quaternion = Quaternion(local_axis(pb, world_axis), math.radians(degrees))
    pb.keyframe_insert("rotation_quaternion", frame=frame)


def set_loc(pb, world_offset, frame):
    m = (W @ pb.bone.matrix_local).to_3x3().inverted()
    pb.location = m @ world_offset
    pb.keyframe_insert("location", frame=frame)


def make_action(name, length_frames, fn):
    act = bpy.data.actions.new(name)
    arm.animation_data_create()
    arm.animation_data.action = act
    for f in range(length_frames + 1):
        t = f / length_frames  # 0..1 over the loop
        for pb in arm.pose.bones:
            pb.rotation_mode = "QUATERNION"
            pb.rotation_quaternion = Quaternion((1, 0, 0, 0))
            pb.location = Vector((0, 0, 0))
        fn(t, f)
    if hasattr(act, "use_cyclic"):
        act.use_cyclic = True
    return act


root_pb = arm.pose.bones[roots[0].name]


def idle(t, f):
    bob = 0.01 * height * math.sin(2 * math.pi * t)
    set_loc(root_pb, up * bob, f)
    for i, n in enumerate(torso):
        set_rot(arm.pose.bones[n], lateral, 1.5 * math.sin(2 * math.pi * t), f)
    for n in head_bones:
        set_rot(arm.pose.bones[n], lateral, -2.0 * math.sin(2 * math.pi * t), f)
    for i, n in enumerate(tail_chain):
        set_rot(arm.pose.bones[n], up, 6.0 * math.sin(2 * math.pi * t + i * 0.6), f)


def walk(t, f):
    bob = 0.02 * height * abs(math.sin(2 * math.pi * t))
    set_loc(root_pb, up * bob, f)
    for li in leg_info:
        # Diagonal gait for quadrupeds, alternating for bipeds.
        if kind == "quadruped":
            phase = 0.0 if (li["front"] == li["left"]) else math.pi
        else:
            phase = 0.0 if li["left"] else math.pi
        swing = math.sin(2 * math.pi * t + phase)
        lift = max(0.0, math.sin(2 * math.pi * t + phase + 0.5))
        bones_ = li["bones"]
        if not bones_:
            continue
        top_amp = 14.0 if kind == "quadruped" else 26.0
        set_rot(arm.pose.bones[bones_[0]], lateral, top_amp * swing, f)
        if len(bones_) > 1:
            # Second joint folds the lower leg back on the lift for both front and rear legs.
            set_rot(arm.pose.bones[bones_[1]], lateral, -18.0 * lift, f)
        if len(bones_) > 2:
            set_rot(arm.pose.bones[bones_[2]], lateral, 10.0 * lift, f)
    for i, n in enumerate(torso):
        set_rot(arm.pose.bones[n], up, 3.0 * math.sin(2 * math.pi * t), f)
    for n in head_bones:
        set_rot(arm.pose.bones[n], lateral, 3.0 * math.sin(4 * math.pi * t), f)
    for i, n in enumerate(tail_chain):
        set_rot(arm.pose.bones[n], up, 10.0 * math.sin(2 * math.pi * t + i * 0.5), f)
    # Biped arm swing: upper chains that are not spine, head or tail.
    if kind == "biped":
        arm_leaves = [b for b in leaves if b.name not in leg_bone_set and b.name not in spine and b.name not in tail_chain
                      and 0.3 * height < (head[b.name].z - lo.z) < 0.85 * height]
        for b in arm_leaves:
            chain = [x for x in chain_to_root(b) if x.name not in spine][::-1]
            if not chain:
                continue
            side = (head[b.name] - centre).dot(lateral) > 0
            phase = math.pi if side else 0.0  # opposite to the same-side leg
            set_rot(arm.pose.bones[chain[0].name], lateral, 20.0 * math.sin(2 * math.pi * t + phase), f)


acts = []
acts.append(make_action("idle", fps * 2, idle))
acts.append(make_action("walk", fps, walk))
arm.animation_data.action = None
for a in acts:
    track = arm.animation_data.nla_tracks.new()
    track.name = a.name
    strip = track.strips.new(a.name, 1, a)
    strip.name = a.name
    if hasattr(strip, "action_slot") and len(getattr(a, "slots", [])) > 0:
        strip.action_slot = a.slots[0]
scene.frame_start, scene.frame_end = 1, fps * 2
# Sanity: the walk strip must move a leg bone.
_probe = arm.pose.bones[leg_info[0]["bones"][0]] if leg_info and leg_info[0]["bones"] else root_pb
for t in arm.animation_data.nla_tracks:
    t.mute = (t.name != "walk")
scene.frame_set(1)
_m0 = _probe.matrix.copy()
scene.frame_set(1 + fps // 4)
_m1 = _probe.matrix.copy()
report["walk_probe"] = {"bone": _probe.name, "angle": round((_m1.to_quaternion().rotation_difference(_m0.to_quaternion())).angle, 3)}
print("WALK_PROBE", _probe.name, report["walk_probe"]["angle"])
for t in arm.animation_data.nla_tracks:
    t.mute = False

bpy.ops.object.select_all(action="SELECT")
bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", use_selection=True, export_animations=True,
                          export_animation_mode="NLA_TRACKS", export_force_sampling=True, export_apply=False)
with open(os.path.splitext(dst)[0] + "_anim.json", "w") as f:
    json.dump(report, f, indent=1)
print("PROCANIM_OK", kind, "legs", len(leg_info), "spine", len(spine), "tail", len(tail_chain))
