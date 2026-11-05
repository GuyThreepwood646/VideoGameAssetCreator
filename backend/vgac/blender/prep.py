"""Blender headless: game-ready prep of a textured low-poly GLB (the first half of bakeoff/finish.py).

Run: blender -b --python prep.py -- <in.glb> <out.glb> <asset_name> [--size 1.8] [--size-axis height|horizontal|longest]
     [--yaw 0] [--symmetric] [--pivot base|center] [--smooth-angle 60] [--budget N]
     (--height H is the old form of --size H --size-axis height)

Does, in order: import; join meshes; apply transforms; turn about the up axis by --yaw degrees (counter-clockwise seen
from above, so the concept's front ends up facing Blender -Y, which is +Z in glTF and Unity); scale uniformly so the
bounding-box extent on the size axis equals --size (height: Z; horizontal: the larger of X and Y; longest: the largest
of all three); move the pivot (base centre or bounding-box centre) to the origin; weld vertices split along UV chart
borders; optional symmetrize across X; smooth shading with auto-smooth at the crease angle; export GLB; write
<out>_report.json with triangle count, dimensions, the measured size, bone count and a manifold check.
"""
import json
import math
import os
import sys

import bmesh
import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402  (shared helpers next to this script)
from mathutils import Vector

argv = _common.after_double_dash()
src, dst, name = os.path.abspath(argv[0]), os.path.abspath(argv[1]), argv[2]

legacy_height = _common.opt("--height", None)
size_m = float(_common.opt("--size", legacy_height if legacy_height is not None else 1.8))
size_axis = _common.opt("--size-axis", "height")
if size_axis not in ("height", "horizontal", "longest"):
    size_axis = "height"
yaw = float(_common.opt("--yaw", 0.0)) % 360
symmetric = _common.flag("--symmetric")
pivot = _common.opt("--pivot", "base")
smooth_angle = float(_common.opt("--smooth-angle", 60.0))
os.makedirs(os.path.dirname(dst), exist_ok=True)

_common.open_glb(src)
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
if not meshes:
    raise SystemExit("no mesh")
mesh = meshes[0]
if len(meshes) > 1:
    bpy.ops.object.select_all(action="DESELECT")
    for m in meshes:
        m.select_set(True)
    bpy.context.view_layer.objects.active = mesh
    bpy.ops.object.join()
arm = arms[0] if arms else None
root = arm if arm else mesh

bpy.ops.object.select_all(action="DESELECT")
root.select_set(True)
mesh.select_set(True)
bpy.context.view_layer.objects.active = root
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


def bounds():
    pts = [mesh.matrix_world @ Vector(c) for c in mesh.bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return lo, hi


def extent(lo, hi):
    d = hi - lo
    if size_axis == "horizontal":
        return max(d.x, d.y)
    if size_axis == "longest":
        return max(d.x, d.y, d.z)
    return d.z


if yaw:
    root.rotation_mode = "XYZ"  # glTF imports use quaternions, which ignore rotation_euler
    root.rotation_euler = (0.0, 0.0, math.radians(yaw))
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)

lo, hi = bounds()
s = size_m / max(1e-6, extent(lo, hi))
root.scale = (s, s, s)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
lo, hi = bounds()
if pivot == "center":
    base = (lo + hi) / 2
else:
    base = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
root.location -= base
if arm is not None and mesh.parent is None:
    mesh.location -= base
bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)
lo, hi = bounds()

mesh.name = name
mesh.data.name = name

# Weld vertices split along UV chart borders by the unwrapper (UVs are per-loop, so they survive the merge).
bpy.ops.object.select_all(action="DESELECT")
mesh.select_set(True)
bpy.context.view_layer.objects.active = mesh
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.mesh.remove_doubles(threshold=0.00001)
bpy.ops.object.mode_set(mode="OBJECT")

if symmetric:
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.symmetrize(direction="POSITIVE_X", threshold=0.001)
    # Symmetrize leaves a few non-manifold edges and tiny gaps on the mirror plane: weld them and fill small holes.
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=0.0005)
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.fill_holes(sides=8)
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode="OBJECT")

# Triangle budget: symmetrize and welding can push the count over the platform budget; decimate back to it.
budget = int(_common.opt("--budget", 0))
mesh.data.calc_loop_triangles()
if budget and len(mesh.data.loop_triangles) > budget:
    mod = mesh.modifiers.new("budget", "DECIMATE")
    mod.ratio = budget / len(mesh.data.loop_triangles) * 0.995
    mod.use_collapse_triangulate = True
    bpy.ops.object.modifier_apply(modifier="budget")
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=0.00001)
    bpy.ops.object.mode_set(mode="OBJECT")

# Final manifold cleanup: weld the remaining non-manifold vertices a little more aggressively, fill small holes,
# drop loose geometry. Done last so it also covers artefacts from the budget decimation.
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="DESELECT")
bpy.ops.mesh.select_mode(type="VERT")
bpy.ops.mesh.select_non_manifold()
bpy.ops.mesh.remove_doubles(threshold=0.002, use_unselected=False)
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.mesh.fill_holes(sides=12)
bpy.ops.mesh.delete_loose()
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.mesh.normals_make_consistent(inside=False)
bpy.ops.object.mode_set(mode="OBJECT")

# Smooth shading, split only at hard edges above the crease angle.
bpy.ops.object.shade_smooth()
try:
    bpy.ops.object.shade_auto_smooth(angle=math.radians(smooth_angle))
except Exception:
    pass

mesh.data.calc_loop_triangles()
bm = bmesh.new()
bm.from_mesh(mesh.data)
non_manifold = sum(1 for e in bm.edges if not e.is_manifold)
boundary = sum(1 for e in bm.edges if e.is_boundary)
degenerate = sum(1 for f in bm.faces if f.calc_area() < 1e-12)
bm.free()
report = {
    "asset": name,
    "size_m": size_m,
    "size_axis": size_axis,
    "size_extent_m": extent(lo, hi),
    "yaw_deg": yaw,
    "height_m": hi.z - lo.z,
    "symmetric": symmetric,
    "pivot": pivot,
    "smooth_angle": smooth_angle,
    "triangles": len(mesh.data.loop_triangles),
    "vertices": len(mesh.data.vertices),
    "uv_layers": len(mesh.data.uv_layers),
    "materials": len(mesh.data.materials),
    "bones": len(arm.data.bones) if arm else 0,
    "non_manifold_edges": non_manifold,
    "boundary_edges": boundary,
    "degenerate_faces": degenerate,
    "dims_m": [hi.x - lo.x, hi.y - lo.y, hi.z - lo.z],
    "min_m": [lo.x, lo.y, lo.z],
    "max_m": [hi.x, hi.y, hi.z],
}

bpy.ops.object.select_all(action="SELECT")
bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", use_selection=True, export_apply=True)
report["glb"] = dst
with open(os.path.splitext(dst)[0] + "_report.json", "w") as f:
    json.dump(report, f, indent=1)
print("PREP_OK", json.dumps(report))
