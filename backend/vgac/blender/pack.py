"""Blender headless: pack a prepped (optionally rigged and animated) GLB for the engine (second half of bakeoff/finish.py).

Run: blender -b --python pack.py -- <in.glb> <out_dir> <asset_name> [--lods 0.5,0.25,0.1] [--collider hull|none] [--formats fbx,glb]

Builds LOD meshes by decimation named <asset>_LOD0..n so Unity's importer creates the LODGroup automatically; a convex
hull collider mesh named <asset>_collider under 255 vertices; pushes every action into its own NLA strip so the FBX
exporter emits one clip per action (Blender 5: strip.action_slot = action.slots[0]); exports FBX (armature and skin
kept, Y-up, forward -Z, baked NLA strips) and GLB; writes report.json with triangle counts per LOD, collider vertex
count, bone count, clip names and a manifold check.
"""
import json
import math
import os
import sys

import bmesh
import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402  (shared helpers next to this script)

argv = _common.after_double_dash()
src, out_dir, name = os.path.abspath(argv[0]), os.path.abspath(argv[1]), argv[2]

_lods_raw = _common.opt("--lods", "0.5,0.25,0.1")
lods = [] if _lods_raw.strip().lower() in ("", "none") else [float(x) for x in _lods_raw.split(",") if x.strip()]
collider_mode = _common.opt("--collider", "hull")
formats = [f.strip() for f in _common.opt("--formats", "fbx,glb").split(",") if f.strip()]
os.makedirs(out_dir, exist_ok=True)

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

mesh.name = name + "_LOD0"
mesh.data.name = name + "_LOD0"

# The glTF importer splits vertices along UV seams and sharp edges; weld them back (UVs and vertex groups
# survive the merge) so decimation and the manifold check see one connected surface, as bakeoff/finish.py did.
bpy.ops.object.select_all(action="DESELECT")
mesh.select_set(True)
bpy.context.view_layer.objects.active = mesh
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.mesh.remove_doubles(threshold=0.00001)
bpy.ops.object.mode_set(mode="OBJECT")


def tri_count(o):
    o.data.calc_loop_triangles()
    return len(o.data.loop_triangles)


report = {"asset": name, "lods": {}, "lod_ratios": lods, "bones": len(arm.data.bones) if arm else 0, "clips": []}
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
report["collider_verts"] = 0
if collider_mode == "hull":
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
report["non_manifold_edges"] = sum(1 for e in bm.edges if not e.is_manifold)
report["boundary_edges"] = sum(1 for e in bm.edges if e.is_boundary)
bm.free()
pts = [mesh.matrix_world @ v.co for v in mesh.data.vertices]
if pts:
    report["dims_m"] = [max(p.x for p in pts) - min(p.x for p in pts), max(p.y for p in pts) - min(p.y for p in pts),
                        max(p.z for p in pts) - min(p.z for p in pts)]

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

bpy.ops.object.select_all(action="SELECT")
if "fbx" in formats:
    fbx = os.path.join(out_dir, name + ".fbx")
    bpy.ops.export_scene.fbx(filepath=fbx, use_selection=True, apply_unit_scale=True, apply_scale_options="FBX_SCALE_ALL",
                             axis_forward="-Z", axis_up="Y", object_types={"ARMATURE", "MESH", "EMPTY"},
                             use_mesh_modifiers=True, mesh_smooth_type="FACE", use_tspace=True,
                             add_leaf_bones=False, bake_anim=True, bake_anim_use_nla_strips=True, bake_anim_use_all_actions=False,
                             bake_anim_simplify_factor=0.0, path_mode="COPY", embed_textures=True)
    report["fbx"] = fbx
if "glb" in formats:
    glb = os.path.join(out_dir, name + ".glb")
    bpy.ops.export_scene.gltf(filepath=glb, export_format="GLB", use_selection=True, export_apply=True,
                              export_animations=True, export_animation_mode="NLA_TRACKS", export_force_sampling=True)
    report["glb"] = glb
with open(os.path.join(out_dir, "report.json"), "w") as f:
    json.dump(report, f, indent=1)
print("PACK_OK", json.dumps(report))
