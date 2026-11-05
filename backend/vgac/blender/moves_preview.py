"""Blender headless: read a file of moves and render a small moving preview of each clip.

Run: blender -b --python moves_preview.py -- <moves file .glb|.fbx|.bvh> <out_dir> [--frames 8] [--size 112]
         [--actions A,B] [--no-render]

Used for the hand-made library packs (once per pack; the backend caches the strips) and for a person's own
animation file (moves.py, "Use my own animation file"). Writes <out_dir>/preview.json:
  {"skeleton": {recognised, total, found, missing, missing_core, usable, sentence, bones: [names], armatures},
   "fps": file rate, "mesh": true when the file has its own body (else a stand-in figure is drawn),
   "clips": [{"action", "frame_start", "frame_end", "seconds", "moves_bones": bool, "travel_m": metres the hips
              travel, "frames": [png names]}]}
and <out_dir>/<n>_<i>.png per clip n (its index in "clips") and frame i. The backend joins each clip's frames into
one strip. A file without a body (Mixamo "without skin", BVH) gets a simple figure of boxes on its bones. The
camera looks at the skeleton's front from a three-quarter angle, framed on the whole clip so a move that travels
(climbing up a ledge) stays in view, over a ground plane.
"""
import json
import math
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402
import gait  # noqa: E402
import motionfile  # noqa: E402
from mathutils import Vector  # noqa: E402

_VALUE_FLAGS = ("--frames", "--size", "--actions")
_raw = _common.after_double_dash()
_pos = [a for i, a in enumerate(_raw) if not a.startswith("--") and (i == 0 or _raw[i - 1] not in _VALUE_FLAGS)]
src, out_dir = os.path.abspath(_pos[0]), os.path.abspath(_pos[1])
nframes = max(1, int(_common.opt("--frames", "8")))
size = int(_common.opt("--size", "112"))
only = [a for a in _common.opt("--actions", "").split(",") if a]
render = not _common.flag("--no-render")
os.makedirs(out_dir, exist_ok=True)
out = {"skeleton": {}, "clips": [], "fps": None, "mesh": False}


def finish(code=0, error=None):
    if error:
        out["error"] = error
    with open(os.path.join(out_dir, "preview.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print("MOVES_PREVIEW_OK" if not error else "MOVES_PREVIEW_FAIL", error or len(out["clips"]))
    if code:
        raise SystemExit(code)


bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.fps = 24
try:
    info = motionfile.load(src, keep_meshes=True)
except Exception as e:  # noqa: BLE001
    finish(3, f"unreadable: {e}")
arm = info["arm"]
out["fps"] = info["fps"]
if arm is None:
    out["skeleton"] = {"usable": False, "recognised": 0, "total": len(gait.BODY_BONES), "bones": [], "armatures": 0,
                       "sentence": "This file has no skeleton, so it has no moves we can use."}
    finish(0)
bones = [b.name for b in arm.data.bones]
sk = gait.recognise(bones)
sk.update({"bones": bones, "armatures": info["armatures"]})
out["skeleton"] = sk


def animates(act):
    """True when the action moves this armature's bones (not only an object or a shape key)."""
    names = set(bones)
    for fc in _fcurves(act):
        p = fc.data_path
        if p.startswith('pose.bones["'):
            n = p[len('pose.bones["'):].split('"]')[0]
            if n in names:
                return True
    return False


def _fcurves(act):
    if hasattr(act, "fcurves") and len(getattr(act, "fcurves", [])) > 0:
        return list(act.fcurves)
    out_ = []
    for layer in getattr(act, "layers", []):  # Blender 4.4+ layered actions
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", []):
                out_ += list(bag.fcurves)
    return out_


acts = [(n, a) for n, a in info["actions"].items() if not only or n in only]
shorts = gait.source_shorts(bones)
hips = next((n for n, s in shorts.items() if s == "Hips"), None)
fps = info["fps"] or 24.0

# The body: the file's own skinned meshes, else a stand-in of boxes on the bones.
meshes = info["meshes"]
out["mesh"] = bool(meshes)
if not meshes and render:
    import bmesh

    me = bpy.data.meshes.new("figure")
    ob = bpy.data.objects.new("figure", me)
    scene.collection.objects.link(ob)
    bm = bmesh.new()
    W = arm.matrix_world
    lens = [((W @ b.tail_local) - (W @ b.head_local)).length for b in arm.data.bones]
    big = max(lens) if lens else 1.0
    for b in arm.data.bones:
        h, t = W @ b.head_local, W @ b.tail_local
        L = (t - h).length
        if L < 0.02 * big:
            continue
        r = max(0.03 * big, 0.12 * L)
        d = (t - h).normalized()
        up = Vector((0, 0, 1)) if abs(d.z) < 0.9 else Vector((1, 0, 0))
        u = d.cross(up).normalized() * r
        v = d.cross(u).normalized() * r
        vs = [bm.verts.new(p) for p in (h + u + v, h + u - v, h - u - v, h - u + v, t + u + v, t + u - v, t - u - v, t - u + v)]
        for f in ((0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)):
            bm.faces.new([vs[i] for i in f])
    bm.to_mesh(me)
    bm.free()
    k = 0
    for b in arm.data.bones:
        h, t = W @ b.head_local, W @ b.tail_local
        if (t - h).length < 0.02 * big:
            continue
        g = ob.vertex_groups.new(name=b.name)
        g.add(list(range(k, k + 8)), 1.0, "REPLACE")
        k += 8
    mod = ob.modifiers.new("arm", "ARMATURE")
    mod.object = arm
    mat = bpy.data.materials.new("figure")
    mat.diffuse_color = (0.75, 0.62, 0.45, 1.0)
    me.materials.append(mat)
    meshes = [ob]

# Facing: the skeleton's front from its hips (left thigh minus right thigh, crossed with up).
facing = 0.0
lt = next((n for n, s in shorts.items() if s == "LeftUpLeg"), None)
rt = next((n for n, s in shorts.items() if s == "RightUpLeg"), None)
if lt and rt:
    W = arm.matrix_world
    lat = (W @ arm.data.bones[lt].head_local) - (W @ arm.data.bones[rt].head_local)
    lat.z = 0
    fwd = lat.cross(Vector((0, 0, 1)))
    if fwd.length > 1e-6:
        facing = math.atan2(fwd.x, -fwd.y)  # 0 when the front is -Y

scene.render.resolution_x = scene.render.resolution_y = size
scene.render.engine = "BLENDER_WORKBENCH"
scene.render.film_transparent = False
scene.display.shading.light = "STUDIO"
scene.display.shading.color_type = "MATERIAL"
scene.view_settings.view_transform = "Standard"
scene.world = bpy.data.worlds.new("w")
scene.world.color = (0.82, 0.83, 0.85)
cam_data = bpy.data.cameras.new("cam")
cam_data.type = "ORTHO"
cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
az = facing + math.radians(30.0)
ground_obj = None


def joint_points():
    return [arm.matrix_world @ pb.head for pb in arm.pose.bones] + [arm.matrix_world @ pb.tail for pb in arm.pose.bones]


def mesh_points():
    dg = bpy.context.evaluated_depsgraph_get()
    pts = []
    for o in meshes:
        ev = o.evaluated_get(dg)
        pts += [ev.matrix_world @ Vector(c) for c in ev.bound_box]
    return pts


for n, (name, act) in enumerate(acts):
    fr = act.frame_range
    start, end = float(fr[0]), float(fr[1])
    row = {"action": name, "frame_start": start, "frame_end": end, "seconds": round(max(0.0, end - start) / fps, 3),
           "moves_bones": animates(act), "frames": []}
    out["clips"].append(row)
    if not row["moves_bones"]:
        continue
    motionfile.use_action(arm, act)
    samples = [start + (end - start) * i / max(1, nframes - 1) for i in range(nframes)] if nframes > 1 else [start]
    pts, hips_track = [], []
    for f in samples:
        fi = int(math.floor(f))
        scene.frame_set(fi, subframe=f - fi)
        pts += joint_points() + (mesh_points() if render else [])
        if hips:
            hips_track.append(arm.matrix_world @ arm.pose.bones[hips].head)
    if hips_track:
        row["travel_m"] = round(max((p - hips_track[0]).length for p in hips_track), 3)
    if not render:
        continue
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    centre, extent = (lo + hi) / 2, max(hi - lo)
    if ground_obj is None:
        bpy.ops.mesh.primitive_plane_add(size=1.0, location=(0, 0, 0))
        ground_obj = bpy.context.active_object
        gm = bpy.data.materials.new("ground")
        gm.diffuse_color = (0.55, 0.57, 0.6, 1.0)
        ground_obj.data.materials.append(gm)
    ground_z = info["ground"] if info["ground"] is not None else lo.z
    ground_obj.location = (centre.x, centre.y, ground_z - 0.002 * extent)
    ground_obj.scale = (extent * 1.8, extent * 1.8, 1)
    cam_data.ortho_scale = extent * 1.25
    cam.location = centre + Vector((math.sin(az) * 4, -math.cos(az) * 4, 0.9)) * extent
    cam.rotation_euler = (math.radians(78), 0, az)
    for i, f in enumerate(samples):
        fi = int(math.floor(f))
        scene.frame_set(fi, subframe=f - fi)
        scene.render.filepath = os.path.join(out_dir, f"{n}_{i:02}.png")
        bpy.ops.render.render(write_still=True)
        row["frames"].append(os.path.basename(scene.render.filepath))
finish(0)
