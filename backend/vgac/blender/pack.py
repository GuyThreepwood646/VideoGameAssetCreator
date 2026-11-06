"""Blender headless: pack a prepped (optionally rigged and animated) GLB for the engine (second half of bakeoff/finish.py).

Run: blender -b --python pack.py -- <in.glb> <out_dir> <asset_name> [--lods 0.5,0.25,0.1] [--collider hull|parts|mesh|none]
     [--collider-obj collider_parts.obj] [--formats fbx,glb]
     [--base-color base_color.png] [--normal normal.png] [--orm T_<asset>_ORM.png] [--uv-source prep.glb]
     [--shading smooth|flat] [--texture-filter smooth|crisp] [--sockets humanoid|quadruped|multileg|winged|serpent|none]
     [--hair-layout hair_layout.json --hair-base-color hair_base_color.png [--hair-normal ...] [--hair-orm ...]]
     [--hair-object VGAC_Hair --hair-mesh hair_mesh.npz --hair-base-color ... [--hair-kind cards] [--hair-alpha cutout]
      [--hair-two-sided]]

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

Recipes (reference.recipe): --shading flat (low-poly) makes every face flat after the weld, so every LOD and both
formats keep the facets whatever the input's normals were; --texture-filter crisp (pixel art) samples the GLB's
textures nearest-neighbour (Image Texture interpolation Closest, which the glTF exporter writes as a NEAREST sampler).
A look without surface bumps simply gets no --normal, so its GLB has no normal texture.

Sockets (--sockets <category>, rigged assets only): vgac/sockets.py places the attachment points (Socket_Hand_R,
Socket_Back and so on) from the rest pose; each becomes an empty (no geometry) parented to its bone in the FBX and in
every GLB (the clean, Godot and LOD files), so every engine sees a named transform that follows the animation.
sockets.json (glTF model space) is written to <out_dir>; report.json lists the names in `sockets`.

Hair as its own material (--hair-layout, vgac/hairmat.py): the clean-up step laid the hair triangles out on a texture
of their own (hair_layout.json: each hair triangle's centre, corners and coordinates on the hair texture, with the
prep model's bounding box). Each face of LOD0 whose centre matches a hair triangle (both normalised to their bounding
boxes, as for --uv-source) takes material slot 1 and, at each corner, the hair texture's coordinates of the nearest
corner; every other face keeps slot 0. The slots are named <asset>_Body and <asset>_Hair in the FBX (names only; the
Unity importer builds both materials) and the GLB files get two materials: the body's textures and the hair's own
(--hair-base-color, --hair-normal, --hair-orm). LODs are decimated after this, so every level keeps both slots;
report.json gives hair_material: {faces, lod_triangles, slots}.

Hair from the library (--hair-object VGAC_Hair --hair-mesh hair_mesh.npz [--hair-kind shell|cards] [--hair-alpha cutout]
[--hair-two-sided], docs/HAIR-LIBRARY-PLAN.md section 6): the rig step bound the hairstyle as its own object. It is
joined to the body as slot 1 (<name>_Hair; the body is slot 0, <name>_Body), the scalp cap marked by matching positions
to the canonical npz (role 1). LODs: shells decimate with the body; cards keep every hair face at LOD1 (only the body is
decimated) and from LOD2 keep only the cap. Measured on the body faces alone: manifold, watertight (with "edges" for
the tolerance), dims_m, lods_body, the collider points and the sockets. Every GLB's hair material gets alphaMode MASK
with alphaCutoff 0.5 for cut-out hair, doubleSided from the flag, and extras {"vgac_hair": "library"}. Without
--hair-object everything above is as before.
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
shading = _common.opt("--shading", "smooth")
texture_filter = _common.opt("--texture-filter", "smooth")
socket_category = _common.opt("--sockets", "none")
hair_layout = _common.opt("--hair-layout", "")
hair_base = _common.opt("--hair-base-color", "")
hair_normal = _common.opt("--hair-normal", "")
hair_orm = _common.opt("--hair-orm", "")
# Hair from the library (docs/HAIR-LIBRARY-PLAN.md section 6): the rig step's VGAC_Hair object, joined as slot 1.
hair_object = _common.opt("--hair-object", "")
hair_mesh_npz = _common.opt("--hair-mesh", "")
hair_alpha = _common.opt("--hair-alpha", "opaque")
hair_two_sided = _common.flag("--hair-two-sided")
hair_kind = _common.opt("--hair-kind", "shell")
# vgac/sockets.py (pure maths, shared with the backend) sits one folder up.
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.makedirs(out_dir, exist_ok=True)

_common.open_glb(src)
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
if not meshes:
    raise SystemExit("no mesh")
hair_src = None
if hair_object:
    hair_src = next((o for o in meshes if o.name == hair_object or o.name.startswith(hair_object + ".")), None)
    if hair_src is not None and len(meshes) > 1:
        meshes = [o for o in meshes if o is not hair_src]  # the body; the hair joins after the UVs are back
    else:
        hair_src = None
library_hair = hair_src is not None
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


def join_library_hair(body, hair, npz_path):
    """Join the library hair into the body as material slot 1 (<name>_Hair; the body keeps slot 0, <name>_Body), with
    the hair's UV layer under the body's UV name and a face attribute vgac_cap marking the scalp cap (npz role 1,
    found by matching positions to the canonical npz). -> hair report."""
    import numpy as np
    import hairbind

    global body_slot, hair_slot
    hd = hair.data
    HV = np.array([tuple(hair.matrix_world @ v.co) for v in hd.vertices])
    role = np.zeros(len(HV), np.int64)
    matched = None
    if npz_path and os.path.isfile(npz_path):
        nz = np.load(npz_path)
        nrole = np.asarray(nz["role"]) if "role" in nz.files else np.zeros(len(nz["verts"]), np.int64)
        role, _worst = hairbind.roles_by_position(HV, hairbind.gltf_to_blender(nz["verts"]), nrole)
        matched = round(float(np.mean(role >= 0)), 4) if len(role) else 0.0
    cap = [all(role[i] == 1 for i in p.vertices) for p in hd.polygons]
    attr = hd.attributes.get("vgac_cap") or hd.attributes.new("vgac_cap", "BOOLEAN", "FACE")
    attr.data.foreach_set("value", cap)
    body_slot = next((m for m in body.data.materials if m is not None), None) or bpy.data.materials.new(name + "_Body")
    body_slot.name = name + "_Body"
    hair_slot = bpy.data.materials.new(name + "_Hair")
    for o, idx in ((body, 0), (hair, 1)):
        o.data.materials.clear()
        o.data.materials.append(body_slot)
        o.data.materials.append(hair_slot)
        for p in o.data.polygons:
            p.material_index = idx
    if body.data.uv_layers and hd.uv_layers:
        keep = hd.uv_layers.active
        for lay in [lay for lay in hd.uv_layers if lay != keep]:
            hd.uv_layers.remove(lay)
        hd.uv_layers[0].name = body.data.uv_layers.active.name
    faces = len(hd.polygons)
    bpy.ops.object.select_all(action="DESELECT")
    hair.select_set(True)
    body.select_set(True)
    bpy.context.view_layer.objects.active = body
    bpy.ops.object.join()
    return {"ok": faces > 0, "source": "library", "faces": faces, "cap_faces": int(sum(cap)), "matched": matched,
            "kind": hair_kind, "alpha": hair_alpha, "two_sided": bool(hair_two_sided)}


hair_report = None
if library_hair:
    hair_report = join_library_hair(mesh, hair_src, hair_mesh_npz)

# The glTF importer splits vertices along UV seams and sharp edges; weld them back (UVs and vertex groups
# survive the merge) so decimation and the manifold check see one connected surface, as bakeoff/finish.py did.
bpy.ops.object.select_all(action="DESELECT")
mesh.select_set(True)
bpy.context.view_layer.objects.active = mesh
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.mesh.remove_doubles(threshold=0.00001)
bpy.ops.object.mode_set(mode="OBJECT")
if shading == "flat":
    if mesh.data.has_custom_normals:
        bpy.ops.mesh.customdata_custom_splitnormals_clear()
    bpy.ops.object.shade_flat()


def tri_count(o):
    o.data.calc_loop_triangles()
    return len(o.data.loop_triangles)


def assign_hair(target, path):
    """Slot 1 and the hair texture's coordinates for the faces in hair_layout.json (see the module docstring)."""
    from mathutils import Vector

    with open(path) as fh:
        doc = json.load(fh)
    rows = doc.get("faces") or []
    if not rows:
        return {"ok": False, "reason": "empty layout"}
    lo, hi = [Vector(v) for v in doc["box"]]
    sc_ = (lo + hi) / 2
    ss = max(hi[i] - lo[i] for i in range(3)) or 1.0
    tc, ts = _box(target)
    kd = KDTree(len(rows))
    for i, r in enumerate(rows):
        kd.insert((Vector(r[0:3]) - sc_) / ss, i)
    kd.balance()
    td = target.data
    tw = target.matrix_world
    tvert = [(tw @ v.co - tc) / ts for v in td.vertices]
    uvl = td.uv_layers.active.data
    tol = 2e-4
    hits, worst = 0, 0.0
    for poly in td.polygons:
        _, i, dist = kd.find((tw @ poly.center - tc) / ts)
        if dist is None or dist > tol:
            poly.material_index = 0
            continue
        worst = max(worst, dist)
        r = rows[i]
        corners = [(Vector(r[3 + 3 * k: 6 + 3 * k]) - sc_) / ss for k in range(3)]
        uvs = [r[12 + 2 * k: 14 + 2 * k] for k in range(3)]
        for li in poly.loop_indices:
            vp = tvert[td.loops[li].vertex_index]
            k = min(range(3), key=lambda kk: (corners[kk] - vp).length_squared)
            uvl[li].uv = uvs[k]
        poly.material_index = 1
        hits += 1
    return {"ok": hits > 0, "faces": hits, "layout_faces": len(rows), "max_face_offset": round(worst, 6)}


if not library_hair and hair_layout and os.path.isfile(hair_layout) and mesh.data.uv_layers:
    # the model's own material (the rig step's, with its base colour, which the FBX embeds) stays as the body's slot
    body_slot = next((m for m in mesh.data.materials if m is not None), None) or bpy.data.materials.new(name + "_Body")
    body_slot.name = name + "_Body"
    hair_slot = bpy.data.materials.new(name + "_Hair")
    mesh.data.materials.clear()
    mesh.data.materials.append(body_slot)
    mesh.data.materials.append(hair_slot)
    hair_report = assign_hair(mesh, hair_layout)
    if not hair_report.get("ok"):
        mesh.data.materials.clear()


report = {"asset": name, "lods": {}, "lod_ratios": lods, "bones": len(arm.data.bones) if arm else 0, "clips": [],
          "uvs": bool(mesh.data.uv_layers), "uv_transfer": uv_report, "shading": shading, "texture_filter": texture_filter,
          "normal_map": bool(tex_normal and os.path.isfile(tex_normal))}
report["lods"]["LOD0"] = tri_count(mesh)
report["hair_material"] = hair_report

def decimate_body_keep_cards(lod, ratio, drop_cards):
    """Library hair cards: decimating thin cards tears them, so the hair (slot 1) is split off, only the body is
    decimated, and the hair joins again whole (LOD1) or as its scalp cap alone (LOD2 on: every non-cap hair face is
    deleted)."""
    bpy.ops.object.select_all(action="DESELECT")
    lod.select_set(True)
    bpy.context.view_layer.objects.active = lod
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")
    lod.active_material_index = 1
    bpy.ops.object.material_slot_select()
    bpy.ops.mesh.separate(type="SELECTED")
    bpy.ops.object.mode_set(mode="OBJECT")
    parts = [o for o in bpy.context.selected_objects if o is not lod]
    mod = lod.modifiers.new("dec", "DECIMATE")
    mod.ratio = ratio
    bpy.context.view_layer.objects.active = lod
    bpy.ops.object.modifier_apply(modifier="dec")
    for hp in parts:
        if drop_cards:
            a = hp.data.attributes.get("vgac_cap")
            keep = [bool(d.value) for d in a.data] if a is not None and a.domain == "FACE" else [False] * len(hp.data.polygons)
            bm = bmesh.new()
            bm.from_mesh(hp.data)
            bm.faces.ensure_lookup_table()
            kill = [f for f in bm.faces if not keep[f.index]]
            bmesh.ops.delete(bm, geom=kill, context="FACES")
            loose = [v for v in bm.verts if not v.link_faces]
            if loose:
                bmesh.ops.delete(bm, geom=loose, context="VERTS")
            bm.to_mesh(hp.data)
            bm.free()
        if not hp.data.polygons:
            bpy.data.objects.remove(hp, do_unlink=True)
            continue
        bpy.ops.object.select_all(action="DESELECT")
        hp.select_set(True)
        lod.select_set(True)
        bpy.context.view_layer.objects.active = lod
        bpy.ops.object.join()


def body_tris(o):
    o.data.calc_loop_triangles()
    return sum(1 for t in o.data.loop_triangles if o.data.polygons[t.polygon_index].material_index == 0)


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
    if library_hair and hair_kind == "cards":
        decimate_body_keep_cards(lod, ratio, drop_cards=i >= 2)
    else:
        mod = lod.modifiers.new("dec", "DECIMATE")
        mod.ratio = ratio
        bpy.ops.object.modifier_apply(modifier="dec")
    lod.parent = mesh.parent
    report["lods"][f"LOD{i}"] = tri_count(lod)
    lod_objs.append(lod)


def hair_tris(o):
    o.data.calc_loop_triangles()
    return sum(1 for t in o.data.loop_triangles if o.data.polygons[t.polygon_index].material_index == 1)


if hair_report and hair_report.get("ok"):
    hair_report["lod_triangles"] = {f"LOD{i}": hair_tris(o) for i, o in enumerate(lod_objs)}
    hair_report["slots"] = [name + "_Body", name + "_Hair"]
if library_hair:
    # The LOD ratios are about the body: the hair cards keep their count at LOD1 and drop to the cap from LOD2.
    report["lods_body"] = {f"LOD{i}": body_tris(o) for i, o in enumerate(lod_objs)}


def body_points(o):
    """World positions of the vertices the body's faces use (slot 0); every vertex without library hair."""
    if not library_hair:
        return _world_points(o)
    used = {i for p in o.data.polygons if p.material_index == 0 for i in p.vertices}
    return [o.matrix_world @ o.data.vertices[i].co for i in sorted(used)]

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
    bm = limited_hull(body_points(mesh))
    _new_collider(f"{name}_collider_00", bm, "hull")
elif collider_mode == "mesh":
    # LOD1 when LOD0 is big, so a static mesh collider stays cheap; small pieces keep their exact LOD0.
    src_lod = lod_objs[1] if len(lod_objs) > 1 and report["lods"]["LOD0"] > 4000 else lod_objs[0]
    bm = bmesh.new()
    bm.from_mesh(src_lod.data)
    bm.transform(src_lod.matrix_world)
    if library_hair:
        bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.material_index == 1], context="FACES")
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    _new_collider(f"{name}_collider_mesh", bm, "mesh")
elif collider_mode == "parts" and collider_obj and os.path.isfile(collider_obj):
    from mathutils import Vector
    parts, src_box = read_collider_obj(collider_obj)
    pts0 = body_points(mesh)
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
if library_hair:
    # body faces only: hair cards are open sheets and the hair is a separate piece by design
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.material_index == 1], context="FACES")
    loose = [v for v in bm.verts if not v.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
    report["edges"] = len(bm.edges)
report["non_manifold_edges"] = sum(1 for e in bm.edges if not e.is_manifold)
report["boundary_edges"] = sum(1 for e in bm.edges if e.is_boundary)
bm.free()
pts = body_points(mesh)
if pts:
    report["dims_m"] = [max(p.x for p in pts) - min(p.x for p in pts), max(p.y for p in pts) - min(p.y for p in pts),
                        max(p.z for p in pts) - min(p.z for p in pts)]

# --- Attachment sockets (vgac/sockets.py) --------------------------------------------------------------------------------
# One empty per socket (no geometry), parented to its bone, placed from the rest pose: the FBX and every GLB carry a
# named transform that follows the animation. sockets.json (glTF model space) is written next to report.json.
socket_objs = []
report["sockets"] = []
if arm is not None and socket_category not in ("", "none"):
    import numpy as np
    import sockets as sockets_mod

    try:
        pose_was = arm.data.pose_position
        arm.data.pose_position = "REST"
        bpy.context.view_layer.update()
        W = arm.matrix_world
        bone_list = [{"name": b.name, "parent": b.parent.name if b.parent else None,
                      "head": list(W @ b.head_local), "tail": list(W @ b.tail_local)} for b in arm.data.bones]
        index = {b["name"]: i for i, b in enumerate(bone_list)}
        me = mesh.data
        n = len(me.vertices)
        co = np.empty(n * 3, dtype=np.float64)
        me.vertices.foreach_get("co", co)
        M = np.array(mesh.matrix_world)
        V = co.reshape(-1, 3) @ M[:3, :3].T + M[:3, 3]
        gname = {g.index: g.name for g in mesh.vertex_groups}
        owner = np.full(n, -1, dtype=np.int64)
        for v in me.vertices:
            best, bw = -1, 0.0
            for g in v.groups:
                if g.weight > bw and gname.get(g.group) in index:
                    best, bw = index[gname[g.group]], g.weight
            owner[v.index] = best
        me.calc_loop_triangles()
        tri = np.empty(len(me.loop_triangles) * 3, dtype=np.int64)
        me.loop_triangles.foreach_get("vertices", tri)
        tri = tri.reshape(-1, 3)
        if library_hair:
            # the sockets are placed on the body alone, as without library hair
            mi = np.empty(len(me.loop_triangles), dtype=np.int64)
            me.loop_triangles.foreach_get("material_index", mi)
            tri = tri[mi == 0]
            used = np.unique(tri)
            remap = np.full(n, -1, dtype=np.int64)
            remap[used] = np.arange(len(used))
            V, owner, tri = V[used], owner[used], remap[tri]
        result = sockets_mod.compute(bone_list, V, owner, tri, socket_category)
        from mathutils import Matrix, Vector
        for s in result["sockets"]:
            e = bpy.data.objects.new(s["name"], None)
            e.empty_display_type = "ARROWS"
            e.empty_display_size = 0.05
            bpy.context.scene.collection.objects.link(e)
            e.parent = arm
            if s["bone"] in arm.data.bones:
                e.parent_type = "BONE"
                e.parent_bone = s["bone"]
            bpy.context.view_layer.update()
            # Blender axes for the glTF frame (sockets.py): local -Y is forward (glTF +Z), local +Z is up (glTF +Y).
            r, u, f = (Vector([float(x) for x in s[k]]) for k in ("right", "up", "forward"))
            mw = Matrix((r, -f, u)).transposed().to_4x4()
            mw.translation = Vector([float(x) for x in s["position"]])
            e.matrix_world = mw
            socket_objs.append(e)
        bpy.context.view_layer.update()
        arm.data.pose_position = pose_was
        bpy.context.view_layer.update()
        doc = sockets_mod.to_json(result, bone_list)
        with open(os.path.join(out_dir, "sockets.json"), "w") as f:
            json.dump(doc, f, indent=1)
        report["sockets"] = doc["names"]
        report["sockets_skipped"] = doc["skipped"]
        report["sockets_file"] = os.path.join(out_dir, "sockets.json")
    except Exception as e:  # noqa: BLE001  (sockets never stop a pack)
        import traceback
        traceback.print_exc()
        report["sockets_error"] = str(e)[-400:]

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
        # A strip must not hold its first frame before it starts: see the rest-pose note below.
        for st in t.strips:
            st.extrapolation = "NOTHING"
    report["clips"] = [a.name for a in bpy.data.actions]
    # The file's default pose has to be the rest pose. Unity builds the prefab from it, and the hit boxes and
    # attachment points are placed against the rest pose; with every strip live, the default pose was the first
    # frame of whichever clip sat on top (arms down for the library clips), which put them up to 30 cm off.
    # No strip plays before frame 1, so stand at frame 0 with a clean pose.
    for pb in arm.pose.bones:
        pb.location = (0.0, 0.0, 0.0)
        pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        pb.rotation_euler = (0.0, 0.0, 0.0)
        pb.scale = (1.0, 1.0, 1.0)
    bpy.context.scene.frame_set(0)
    bpy.context.view_layer.update()
    report["default_pose"] = "rest"

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


def _tex_node(nt, path, colorspace):
    t = nt.nodes.new("ShaderNodeTexImage")
    t.image = _image(path, colorspace)
    if texture_filter == "crisp":
        t.interpolation = "Closest"
    return t


def build_material(tex_base=tex_base, tex_normal=tex_normal, tex_orm=tex_orm, mat_name=None, alpha=False):
    """Principled BSDF the glTF exporter reads: base colour, normal map, and one ORM image whose G drives roughness,
    B metallic and R occlusion (through the 'glTF Material Output' group), so the GLB references the ORM texture as
    both metallicRoughnessTexture and occlusionTexture. The hair's own material is built the same way from its maps."""
    have = [p for p in (tex_base, tex_normal, tex_orm) if p and os.path.isfile(p)]
    if not have:
        return None
    mat = bpy.data.materials.new(mat_name or (name + "_mat"))
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    if tex_base and os.path.isfile(tex_base):
        t = _tex_node(nt, tex_base, "sRGB")
        nt.links.new(t.outputs["Color"], bsdf.inputs["Base Color"])
        if alpha:  # library hair cards: the texture's alpha cuts the cards out (the GLB gets alphaMode MASK below)
            nt.links.new(t.outputs["Alpha"], bsdf.inputs["Alpha"])
    if tex_normal and os.path.isfile(tex_normal):
        t = _tex_node(nt, tex_normal, "Non-Color")
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nt.links.new(t.outputs["Color"], nm.inputs["Color"])
        nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    if tex_orm and os.path.isfile(tex_orm):
        t = _tex_node(nt, tex_orm, "Non-Color")
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
    two = bool(hair_report and hair_report.get("ok"))
    if two:
        for m in (body_slot, hair_slot):
            m.name = m.name + "_slot"  # the FBX already carries these names; the GLB's textured materials take them
    mat = build_material(mat_name=(name + "_Body") if two else None)
    hmat = build_material(hair_base, hair_normal, hair_orm, name + "_Hair", alpha=library_hair and hair_alpha == "cutout") if two else None
    if mat is not None and hmat is not None:
        # every level kept both slots through the decimation: the slots get the textured materials, the faces keep
        # their slot numbers
        for o in lod_objs:
            while len(o.data.materials) < 2:
                o.data.materials.append(None)
            o.data.materials[0] = mat
            o.data.materials[1] = hmat
    elif mat is not None:
        for o in lod_objs:
            o.data.materials.clear()
            o.data.materials.append(mat)
    rig = ([arm] if arm is not None else []) + socket_objs  # the sockets ride on the skeleton in every GLB
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


def patch_library_hair_glb(path):
    """The library hair material in a written GLB: alphaMode MASK with alphaCutoff 0.5 for cutout hair (OPAQUE
    otherwise), doubleSided from the catalogue flag, and extras {"vgac_hair": "library"} so later readers (the items'
    body tree in equip.py) can tell it from today's hair slot. -> True when the material was found."""
    import struct

    with open(path, "rb") as fh:
        data = fh.read()
    if data[:4] != b"glTF":
        return False
    n = struct.unpack_from("<I", data, 12)[0]
    doc = json.loads(data[20:20 + n].decode("utf-8"))
    rest = data[20 + n:]
    hit = False
    for m in doc.get("materials") or []:
        if str(m.get("name", "")).endswith("_Hair"):
            if hair_alpha == "cutout":
                m["alphaMode"] = "MASK"
                m["alphaCutoff"] = 0.5
            else:
                m.pop("alphaMode", None)
                m.pop("alphaCutoff", None)
            m["doubleSided"] = bool(hair_two_sided)
            m.setdefault("extras", {})["vgac_hair"] = "library"
            hit = True
    if not hit:
        return False
    js = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    js += b" " * ((4 - len(js) % 4) % 4)
    total = 12 + 8 + len(js) + len(rest)
    with open(path, "wb") as fh:
        fh.write(struct.pack("<III", 0x46546C67, 2, total))
        fh.write(struct.pack("<II", len(js), 0x4E4F534A))
        fh.write(js)
        fh.write(rest)
    return True


if library_hair and hair_report and hair_report.get("ok") and "glb" in formats:
    patched = []
    for p in [report.get("glb"), report.get("glb_godot")] + list((report.get("glb_lods") or {}).values()):
        if p and os.path.isfile(p) and patch_library_hair_glb(p):
            patched.append(os.path.basename(p))
    hair_report["glb_patched"] = patched
with open(os.path.join(out_dir, "report.json"), "w") as f:
    json.dump(report, f, indent=1)
print("PACK_OK", json.dumps(report))
