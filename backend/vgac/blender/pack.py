"""Blender headless: pack a prepped (optionally rigged and animated) GLB for the engine (second half of bakeoff/finish.py).

Run: blender -b --python pack.py -- <in.glb> <out_dir> <asset_name> [--lods 0.5,0.25,0.1] [--collider hull|parts|mesh|none]
     [--collider-obj collider_parts.obj] [--formats fbx,glb]
     [--base-color base_color.png] [--normal normal.png] [--orm T_<asset>_ORM.png] [--uv-source prep.glb]

Builds LOD meshes by decimation named <asset>_LOD0..n so Unity's importer creates the LODGroup automatically; the
collision shapes (G16, G35): one convex hull, the parts the backend made with vgac/colliders.py (CoACD hulls or a
primitive), or an exact-shape copy of LOD1, named <asset>_collider_00.._nn or <asset>_collider_mesh, each convex part
at most 250 triangles; for unrigged assets with convex parts also <asset>_unreal.fbx (LOD0 plus UCX_<asset>_NN);
pushes every action into its own NLA strip so the FBX
exporter emits one clip per action (Blender 5: strip.action_slot = action.slots[0]); exports FBX (armature and skin
kept, Y-up, forward -Z, baked NLA strips, every LOD and the collider, no material: Unity builds its own) and the GLB
files; writes report.json with triangle counts per LOD, collider vertex count, bone count, clip names, a manifold check
and the GLB files written.

GLB files (G10, docs/GREAT-ASSETS-PLAN.md 3.11): <asset>.glb holds LOD0 only, with the material (base colour, normal,
and the ORM pack as both metallicRoughness and occlusion), and no collider, so web viewers, Blender and Godot see one
model. <asset>_godot.glb adds the collision shapes as <part>-convcolonly (convex) or <part>-colonly (the exact shape),
which Godot turns into collision shapes on import.
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
collider_obj = _common.opt("--collider-obj", "")
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

# --- Collision shapes (G16, G35; docs/GREAT-ASSETS-PLAN.md 3.4) ---------------------------------------------------------
# hull: one convex hull of LOD0; parts: the shapes the backend made (vgac/colliders.py) in --collider-obj;
# mesh: a copy of LOD1 (LOD0 under 4,000 triangles) for a static, non-convex mesh collider; none. Every convex part is kept at or under
# MAX_HULL_TRIS triangles (Unity's convex cap is 255): a bigger hull is rebuilt from its support points along fewer and
# fewer directions. Names: <asset>_collider_00.._nn (convex) and <asset>_collider_mesh.
MAX_HULL_TRIS = 250
collider_objs = []  # (object, kind)


def _fib_dirs(n):
    out = []
    for i in range(n):
        z = 1 - 2 * (i + 0.5) / n
        r = math.sqrt(max(0.0, 1 - z * z))
        t = math.pi * (1 + 5 ** 0.5) * (i + 0.5)
        out.append((math.cos(t) * r, math.sin(t) * r, z))
    return out


def _hull_bm(points):
    bm = bmesh.new()
    for p in points:
        bm.verts.new(p)
    res = bmesh.ops.convex_hull(bm, input=bm.verts[:])
    kill = {g for g in list(res["geom_interior"]) + list(res["geom_unused"]) if isinstance(g, bmesh.types.BMVert) and g.is_valid}
    if kill:
        bmesh.ops.delete(bm, geom=list(kill), context="VERTS")
    loose = [v for v in bm.verts if not v.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return bm


def limited_hull(points, max_tris=MAX_HULL_TRIS):
    """bmesh of the convex hull of `points` with at most `max_tris` triangles."""
    bm = _hull_bm(points)
    if len(bm.faces) <= max_tris:
        return bm
    pts = [v.co.copy() for v in bm.verts]
    bm.free()
    k = (max_tris + 4) // 2
    while True:
        sel = {max(range(len(pts)), key=lambda i, d=d: pts[i].x * d[0] + pts[i].y * d[1] + pts[i].z * d[2]) for d in _fib_dirs(k)}
        bm = _hull_bm([pts[i] for i in sorted(sel)])
        if len(bm.faces) <= max_tris or k <= 8:
            return bm
        bm.free()
        k -= 6


def _new_collider(obj_name, bm, kind):
    me = bpy.data.meshes.new(obj_name)
    bm.to_mesh(me)
    bm.free()
    o = bpy.data.objects.new(obj_name, me)
    bpy.context.scene.collection.objects.link(o)
    o.display_type = "WIRE"
    collider_objs.append((o, kind))
    return o


def _world_points(o):
    return [o.matrix_world @ v.co for v in o.data.vertices]


def read_collider_obj(path):
    """[(name, kind, verts, faces)] and the source bounding box (lo, hi) from colliders.write_obj."""
    parts, bbox, cur = [], None, None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            t = line.split()
            if not t:
                continue
            if t[0] == "#" and len(t) >= 8 and t[1] == "vgac_bbox":
                vals = [float(x) for x in t[2:8]]
                bbox = (vals[:3], vals[3:])
            elif t[0] == "#" and len(t) >= 3 and t[1] == "vgac_kind" and cur is not None:
                cur[1] = t[2]
            elif t[0] == "o":
                cur = [t[1], "hull", [], []]
                parts.append(cur)
            elif t[0] == "v" and cur is not None:
                cur[2].append(tuple(float(x) for x in t[1:4]))
            elif t[0] == "f" and cur is not None:
                cur[3].append([int(x.split("/")[0]) for x in t[1:4]])
    out, base = [], 1
    for nm, kind, vs, fs in parts:
        out.append((nm, kind, vs, [[i - base for i in f] for f in fs]))
        base += len(vs)
    return out, bbox


report["collider_verts"] = 0
report["collider_mode"] = collider_mode
if collider_mode == "hull":
    # The hull of LOD0 (a decimated level can lose its corners), then cut down to the triangle cap.
    bm = limited_hull(_world_points(mesh))
    _new_collider(f"{name}_collider_00", bm, "hull")
elif collider_mode == "mesh":
    # LOD1 when LOD0 is big, so a static mesh collider stays cheap; small pieces keep their exact LOD0.
    src_lod = lod_objs[1] if len(lod_objs) > 1 and report["lods"]["LOD0"] > 4000 else lod_objs[0]
    bm = bmesh.new()
    bm.from_mesh(src_lod.data)
    bm.transform(src_lod.matrix_world)
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    _new_collider(f"{name}_collider_mesh", bm, "mesh")
elif collider_mode == "parts" and collider_obj and os.path.isfile(collider_obj):
    from mathutils import Vector
    parts, src_box = read_collider_obj(collider_obj)
    pts0 = _world_points(mesh)
    t_lo = [min(p[i] for p in pts0) for i in range(3)]
    t_hi = [max(p[i] for p in pts0) for i in range(3)]
    scale, shift = [1.0, 1.0, 1.0], [0.0, 0.0, 0.0]
    if src_box is not None:
        for i in range(3):
            span = src_box[1][i] - src_box[0][i]
            scale[i] = (t_hi[i] - t_lo[i]) / span if span > 1e-9 else 1.0
            shift[i] = t_lo[i] - src_box[0][i] * scale[i]
    report["collider_fit_scale"] = [round(s, 5) for s in scale]
    for nm, kind, vs, fs in parts:
        pts = [Vector((vs[j][0] * scale[0] + shift[0], vs[j][1] * scale[1] + shift[1], vs[j][2] * scale[2] + shift[2])) for j in range(len(vs))]
        if kind in ("box", "sphere", "capsule"):
            bm = bmesh.new()
            bverts = [bm.verts.new(p) for p in pts]
            for f in fs:
                try:
                    bm.faces.new([bverts[i] for i in f])
                except ValueError:
                    pass
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
            if len(bm.faces) > MAX_HULL_TRIS:
                bm.free()
                bm = limited_hull(pts)
        else:
            bm = limited_hull(pts)
        _new_collider(nm, bm, kind)

parts_report = []
for o, kind in collider_objs:
    o.data.materials.clear()
    parts_report.append({"name": o.name, "kind": kind, "triangles": tri_count(o), "vertices": len(o.data.vertices)})
report["collider_parts"] = parts_report
report["collider_count"] = len(parts_report)
report["collider_verts"] = sum(p["vertices"] for p in parts_report)
convex = [p for p in parts_report if p["kind"] != "mesh"]
report["collider_triangles_max"] = max((p["triangles"] for p in convex), default=0)
report["collider_triangles_total"] = sum(p["triangles"] for p in parts_report)

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
    # Unreal (UCX_ convention): a separate FBX with LOD0 named after the asset and each convex part as
    # UCX_<asset>_NN, which Unreal's FBX import turns into the static mesh's simple collision. Unrigged assets only;
    # not verified in Unreal (no Unreal in the stack).
    ucx = [o for o, kind in collider_objs if kind != "mesh"]
    if arm is None and ucx:
        lod0_name = mesh.name
        old = [(o, o.name) for o in ucx]
        mesh.name = name
        for i, o in enumerate(ucx):
            o.name = f"UCX_{name}_{i:02d}"
        bpy.ops.object.select_all(action="DESELECT")
        for o in [mesh] + ucx:
            o.select_set(True)
        bpy.context.view_layer.objects.active = mesh
        unreal = os.path.join(out_dir, name + "_unreal.fbx")
        bpy.ops.export_scene.fbx(filepath=unreal, use_selection=True, apply_unit_scale=True, apply_scale_options="FBX_SCALE_ALL",
                                 axis_forward="-Z", axis_up="Y", object_types={"MESH"}, use_mesh_modifiers=True,
                                 mesh_smooth_type="FACE", use_tspace=True, bake_anim=False, path_mode="COPY")
        report["fbx_unreal"] = unreal
        report["unreal_collision"] = [o.name for o in ucx]
        for o, nm in old:
            o.name = nm
        mesh.name = lod0_name


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
    if collider_objs:
        # Godot: a mesh whose name ends in -convcolonly becomes a convex collision shape on import and is not drawn;
        # -colonly makes a concave (trimesh) one, for the exact-shape collider of static scenery.
        names = [(o, o.name) for o, _ in collider_objs]
        for o, kind in collider_objs:
            o.name = o.name + ("-colonly" if kind == "mesh" else "-convcolonly")
        godot = os.path.join(out_dir, name + "_godot.glb")
        export_glb(godot, [mesh] + [o for o, _ in collider_objs] + rig)
        report["glb_godot"] = godot
        for o, nm in names:
            o.name = nm
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
