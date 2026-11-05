"""Blender headless: pack a prepped (optionally rigged and animated) GLB for the engine (second half of bakeoff/finish.py).

Run: blender -b --python pack.py -- <in.glb> <out_dir> <asset_name> [--lods 0.5,0.25,0.1] [--collider hull|none] [--formats fbx,glb]
     [--base-color base_color.png] [--normal normal.png] [--orm T_<asset>_ORM.png] [--uv-source prep.glb]

Builds LOD meshes by decimation named <asset>_LOD0..n so Unity's importer creates the LODGroup automatically; a convex
hull collider mesh named <asset>_collider under 255 vertices; pushes every action into its own NLA strip so the FBX
exporter emits one clip per action (Blender 5: strip.action_slot = action.slots[0]); exports FBX (armature and skin
kept, Y-up, forward -Z, baked NLA strips, every LOD and the collider, no material: Unity builds its own) and the GLB
files; writes report.json with triangle counts per LOD, collider vertex count, bone count, clip names, a manifold check
and the GLB files written.

GLB files (G10, docs/GREAT-ASSETS-PLAN.md 3.11): <asset>.glb holds LOD0 only, with the material (base colour, normal,
and the ORM pack as both metallicRoughness and occlusion), and no collider, so web viewers, Blender and Godot see one
model. <asset>_godot.glb adds the collider as <asset>-colonly, which Godot turns into a collision shape on import.
lods/<asset>_LOD<n>.glb holds each lower detail level on its own.

UVs: the rig stage's GLB (SkinToken output) carries no texture coordinates, and its mesh is normalised to a 2 m box.
With --uv-source (the prep GLB, same faces), a mesh without UVs gets them back: both meshes are normalised to their
bounding boxes, each face takes the source face with the nearest centre, and each corner the UV of that face's nearest
corner. Without this every texture samples one texel on a rigged character.
"""
import json
import math
import os
import sys

import bmesh
import bpy
from mathutils.kdtree import KDTree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402  (shared helpers next to this script)

argv = _common.after_double_dash()
src, out_dir, name = os.path.abspath(argv[0]), os.path.abspath(argv[1]), argv[2]

_lods_raw = _common.opt("--lods", "0.5,0.25,0.1")
lods = [] if _lods_raw.strip().lower() in ("", "none") else [float(x) for x in _lods_raw.split(",") if x.strip()]
collider_mode = _common.opt("--collider", "hull")
formats = [f.strip() for f in _common.opt("--formats", "fbx,glb").split(",") if f.strip()]
tex_base = _common.opt("--base-color", "")
tex_normal = _common.opt("--normal", "")
tex_orm = _common.opt("--orm", "")
uv_source = _common.opt("--uv-source", "")
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


def _box(obj):
    pts = [obj.matrix_world @ v.co for v in obj.data.vertices]
    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    center = type(pts[0])([(lo[i] + hi[i]) / 2 for i in range(3)])
    return center, max(hi[i] - lo[i] for i in range(3)) or 1.0


def transfer_uvs(target, path):
    """Give `target` the UVs of the largest mesh in the GLB at `path` (see the module docstring). -> report dict."""
    before = set(bpy.data.objects)
    _common.import_glb(path)
    new = [o for o in bpy.data.objects if o not in before]
    try:
        srcs = [o for o in new if o.type == "MESH" and o.data.uv_layers]
        if not srcs:
            return {"ok": False, "reason": "source has no UVs"}
        src_o = max(srcs, key=lambda o: len(o.data.polygons))
        sd, td = src_o.data, target.data
        sc, ss = _box(src_o)
        tc, ts = _box(target)
        sw, tw = src_o.matrix_world, target.matrix_world
        kd = KDTree(len(sd.polygons))
        for poly in sd.polygons:
            kd.insert((sw @ poly.center - sc) / ss, poly.index)
        kd.balance()
        svert = [(sw @ v.co - sc) / ss for v in sd.vertices]
        tvert = [(tw @ v.co - tc) / ts for v in td.vertices]
        suv = sd.uv_layers.active.data
        layer = td.uv_layers.new(name="UVMap")
        worst = 0.0
        for poly in td.polygons:
            _, si, dist = kd.find((tw @ poly.center - tc) / ts)
            worst = max(worst, dist)
            sp = sd.polygons[si]
            for li in poly.loop_indices:
                vp = tvert[td.loops[li].vertex_index]
                best = min(sp.loop_indices, key=lambda sl: (svert[sd.loops[sl].vertex_index] - vp).length_squared)
                layer.data[li].uv = suv[best].uv
        return {"ok": True, "faces": len(td.polygons), "source_faces": len(sd.polygons), "max_face_offset": round(worst, 5)}
    finally:
        for o in new:
            bpy.data.objects.remove(o, do_unlink=True)


uv_report = None
if not mesh.data.uv_layers and uv_source and os.path.isfile(uv_source):
    uv_report = transfer_uvs(mesh, uv_source)

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


report = {"asset": name, "lods": {}, "lod_ratios": lods, "bones": len(arm.data.bones) if arm else 0, "clips": [],
          "uvs": bool(mesh.data.uv_layers), "uv_transfer": uv_report}
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


def _image(path, colorspace):
    img = bpy.data.images.load(path, check_existing=True)
    img.colorspace_settings.name = colorspace
    return img


def build_material():
    """Principled BSDF the glTF exporter reads: base colour, normal map, and one ORM image whose G drives roughness,
    B metallic and R occlusion (through the 'glTF Material Output' group), so the GLB references the ORM texture as
    both metallicRoughnessTexture and occlusionTexture."""
    have = [p for p in (tex_base, tex_normal, tex_orm) if p and os.path.isfile(p)]
    if not have:
        return None
    mat = bpy.data.materials.new(name + "_mat")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    if tex_base and os.path.isfile(tex_base):
        t = nt.nodes.new("ShaderNodeTexImage")
        t.image = _image(tex_base, "sRGB")
        nt.links.new(t.outputs["Color"], bsdf.inputs["Base Color"])
    if tex_normal and os.path.isfile(tex_normal):
        t = nt.nodes.new("ShaderNodeTexImage")
        t.image = _image(tex_normal, "Non-Color")
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nt.links.new(t.outputs["Color"], nm.inputs["Color"])
        nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    if tex_orm and os.path.isfile(tex_orm):
        t = nt.nodes.new("ShaderNodeTexImage")
        t.image = _image(tex_orm, "Non-Color")
        sep = nt.nodes.new("ShaderNodeSeparateColor")
        nt.links.new(t.outputs["Color"], sep.inputs["Color"])
        nt.links.new(sep.outputs["Green"], bsdf.inputs["Roughness"])
        nt.links.new(sep.outputs["Blue"], bsdf.inputs["Metallic"])
        group = bpy.data.node_groups.get("glTF Material Output")
        if group is None:
            group = bpy.data.node_groups.new("glTF Material Output", "ShaderNodeTree")
            group.interface.new_socket(name="Occlusion", in_out="INPUT", socket_type="NodeSocketFloat")
        g = nt.nodes.new("ShaderNodeGroup")
        g.node_tree = group
        nt.links.new(sep.outputs["Red"], g.inputs["Occlusion"])
    return mat


def export_glb(path, objects, animations=True):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objects:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True, export_apply=True,
                              export_animations=animations, export_animation_mode="NLA_TRACKS", export_force_sampling=True)


if "glb" in formats:
    mat = build_material()
    if mat is not None:
        for o in lod_objs:
            o.data.materials.clear()
            o.data.materials.append(mat)
    rig = [arm] if arm is not None else []
    # The clean GLB: LOD0 only, named after the asset, no collider and no lower detail levels.
    lod0_name = mesh.name
    mesh.name = name
    glb = os.path.join(out_dir, name + ".glb")
    export_glb(glb, [mesh] + rig)
    report["glb"] = glb
    col_obj = bpy.data.objects.get(f"{name}_collider")
    if col_obj is not None:
        # Godot: a mesh whose name ends in -colonly becomes a collision shape on import and is not drawn.
        col_obj.name = f"{name}-colonly"
        godot = os.path.join(out_dir, name + "_godot.glb")
        export_glb(godot, [mesh, col_obj] + rig)
        report["glb_godot"] = godot
        col_obj.name = f"{name}_collider"
    mesh.name = lod0_name
    report["glb_lods"] = {}
    if len(lod_objs) > 1:
        os.makedirs(os.path.join(out_dir, "lods"), exist_ok=True)
        for i, o in enumerate(lod_objs[1:], start=1):
            p = os.path.join(out_dir, "lods", f"{name}_LOD{i}.glb")
            export_glb(p, [o] + rig, animations=False)
            report["glb_lods"][f"LOD{i}"] = p
with open(os.path.join(out_dir, "report.json"), "w") as f:
    json.dump(report, f, indent=1)
print("PACK_OK", json.dumps(report))
