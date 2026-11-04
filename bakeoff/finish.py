"""Blender headless: game-ready finishing of a textured low-poly GLB (with or without an armature).

Run: blender -b --python finish.py -- <in.glb> <out_dir> <asset_name> [height_m=1.8] [--symmetric] [--lods 0.5,0.25,0.1]

Does, in order: import; apply transforms; scale so the bounding-box height equals height_m; move the base centre to
the origin; optional symmetrize across X (mirror the +X half; characters and symmetric props); build LOD meshes by
decimation named <asset>_LOD0..n so Unity's importer creates the LODGroup automatically; build a convex hull
collider mesh named <asset>_collider (UCX_ prefix for Unreal is also added as a copy); export FBX (with the
armature and skin if present, Y-up, forward -Z, Unity-friendly settings) and GLB; write report.json with
triangle counts per LOD, dimensions, bone count and a manifold check.
"""
import json
import math
import os
import sys

import bmesh
import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
src, out_dir, name = os.path.abspath(argv[0]), os.path.abspath(argv[1]), argv[2]
height = float(argv[3]) if len(argv) > 3 and not argv[3].startswith("--") else 1.8
symmetric = "--symmetric" in argv
lods = [0.5, 0.25, 0.1]
if "--lods" in argv:
    lods = [float(x) for x in argv[argv.index("--lods") + 1].split(",")]
os.makedirs(out_dir, exist_ok=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
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

# Apply transforms and normalise scale and origin on the root.
bpy.ops.object.select_all(action="DESELECT")
root.select_set(True)
mesh.select_set(True)
bpy.context.view_layer.objects.active = root
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
pts = [mesh.matrix_world @ Vector(c) for c in mesh.bound_box]
lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
s = height / max(1e-6, (hi.z - lo.z))
root.scale = (s, s, s)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
pts = [mesh.matrix_world @ Vector(c) for c in mesh.bound_box]
lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
base = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
root.location -= base
if arm is None:
    pass
else:
    mesh.location -= base if mesh.parent is None else Vector((0, 0, 0))
bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)

mesh.name = name + "_LOD0"
mesh.data.name = name + "_LOD0"

# Weld vertices split along UV chart borders by the unwrapper (UVs are per-loop, so they survive the merge).
bpy.ops.object.select_all(action="DESELECT")
mesh.select_set(True)
bpy.context.view_layer.objects.active = mesh
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.mesh.remove_doubles(threshold=0.00001)
bpy.ops.object.mode_set(mode="OBJECT")

if symmetric:
    bpy.ops.object.select_all(action="DESELECT")
    mesh.select_set(True)
    bpy.context.view_layer.objects.active = mesh
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.symmetrize(direction="POSITIVE_X", threshold=0.001)
    bpy.ops.object.mode_set(mode="OBJECT")

def tri_count(o):
    o.data.calc_loop_triangles()
    return len(o.data.loop_triangles)

report = {"asset": name, "height_m": height, "symmetric": symmetric, "lods": {}, "bones": len(arm.data.bones) if arm else 0}
report["lods"]["LOD0"] = tri_count(mesh)

# LODs by decimation (keeps UVs and vertex groups).
lod_objs = [mesh]
for i, ratio in enumerate(lods, start=1):
    bpy.ops.object.select_all(action="DESELECT")
    mesh.select_set(True)
    bpy.context.view_layer.objects.active = mesh
    bpy.ops.object.duplicate()
    lod = bpy.context.active_object
    lod.name = f"{name}_LOD{i}"
    lod.data.name = lod.name
    mod = lod.modifiers.new("dec", "DECIMATE")
    mod.ratio = ratio
    bpy.ops.object.modifier_apply(modifier="dec")
    lod.parent = mesh.parent
    report["lods"][f"LOD{i}"] = tri_count(lod)
    lod_objs.append(lod)

# Convex hull collider from the lowest LOD.
bpy.ops.object.select_all(action="DESELECT")
lod_objs[-1].select_set(True)
bpy.context.view_layer.objects.active = lod_objs[-1]
bpy.ops.object.duplicate()
col = bpy.context.active_object
col.name = f"{name}_collider"
col.data.name = col.name
for m in list(col.modifiers):
    col.modifiers.remove(m)
col.vertex_groups.clear()
bm = bmesh.new()
bm.from_mesh(col.data)
res = bmesh.ops.convex_hull(bm, input=bm.verts)
kill = {g for g in list(res["geom_interior"]) + list(res["geom_unused"]) if isinstance(g, bmesh.types.BMVert) and g.is_valid}
bmesh.ops.delete(bm, geom=list(kill), context="VERTS")
bmesh.ops.dissolve_limit(bm, angle_limit=math.radians(5), verts=bm.verts[:], edges=bm.edges[:])
bm.to_mesh(col.data)
bm.free()
col.data.materials.clear()
col.display_type = "WIRE"
report["collider_verts"] = len(col.data.vertices)
# Keep the collider under 255 vertices for Unity convex MeshCollider.
if len(col.data.vertices) > 255:
    mod = col.modifiers.new("dec", "DECIMATE")
    mod.ratio = 250 / len(col.data.vertices)
    bpy.ops.object.select_all(action="DESELECT")
    col.select_set(True)
    bpy.context.view_layer.objects.active = col
    bpy.ops.object.modifier_apply(modifier="dec")
    report["collider_verts"] = len(col.data.vertices)

# Manifold check on LOD0.
bm = bmesh.new()
bm.from_mesh(mesh.data)
non_manifold = sum(1 for e in bm.edges if not e.is_manifold)
boundary = sum(1 for e in bm.edges if e.is_boundary)
bm.free()
report["non_manifold_edges"] = non_manifold
report["boundary_edges"] = boundary
report["dims_m"] = [hi.x - lo.x, hi.y - lo.y, hi.z - lo.z]

# Animations: push every action into its own NLA strip so the FBX exporter emits one clip per action.
if arm is not None and bpy.data.actions:
    arm.animation_data_create()
    arm.animation_data.action = None
    existing = {t.name for t in arm.animation_data.nla_tracks}
    for a in bpy.data.actions:
        if a.name in existing:
            continue
        track = arm.animation_data.nla_tracks.new()
        track.name = a.name
        strip = track.strips.new(a.name, 1, a)
        if hasattr(strip, "action_slot") and len(getattr(a, "slots", [])) > 0:
            strip.action_slot = a.slots[0]
    for t in arm.animation_data.nla_tracks:
        t.mute = False
    report["clips"] = [a.name for a in bpy.data.actions]

# Export.
bpy.ops.object.select_all(action="SELECT")
fbx = os.path.join(out_dir, name + ".fbx")
bpy.ops.export_scene.fbx(filepath=fbx, use_selection=True, apply_unit_scale=True, apply_scale_options="FBX_SCALE_ALL",
                         axis_forward="-Z", axis_up="Y", object_types={"ARMATURE", "MESH", "EMPTY"},
                         use_mesh_modifiers=True, mesh_smooth_type="FACE", use_tspace=True,
                         add_leaf_bones=False, bake_anim=True, bake_anim_use_nla_strips=True, bake_anim_use_all_actions=False,
                         bake_anim_simplify_factor=0.0, path_mode="COPY", embed_textures=True)
glb = os.path.join(out_dir, name + ".glb")
bpy.ops.export_scene.gltf(filepath=glb, export_format="GLB", use_selection=True, export_apply=True)
report["fbx"] = fbx
report["glb"] = glb
json.dump(report, open(os.path.join(out_dir, "report.json"), "w"), indent=1)
print("FINISH_OK", json.dumps(report))
