"""Blender headless: give a character's face more of the texture, then bake every map again onto the new layout.

Run: blender -b --python faceuv.py -- <in.glb> <out.glb> --points face_points.json --maps maps.json --gain 2.4
     [--tex 2048] [--padding 8] [--margin 16] [--max-loss 0.12]

Why (the face detail pass, vgac/face.py): the 3D builder gives the face very few texels (about 90 to 130 across the
face in a 2048 atlas), too few to hold eyes and lips. The texture pieces (UV islands) that hold the face are made
`gain` times bigger, then:
1. fit: put into the free space with every other piece left exactly where it is (blender/uvfit.py); nothing else
   loses a texel. The unwrapper packs tightly, so this only works for small gains.
2. repack: otherwise every piece is packed again with Blender's packer (concave shapes, any rotation): on the knight
   the face gained 2.0 times its texels across and the rest of the body kept all of its (rest_scale 1.03; the share
   of the atlas used went from 0.37 to 0.41). The loss is capped by --max-loss; the island scale is corrected from the
   measured gain (at most four packs).
Then every map is baked again from a copy of the mesh with the old layout (blender/rebake.py). One material, one
atlas, same size.

--points: a JSON list of [x, y, z] in glTF coordinates (Y up), the centres of the face's triangles as vgac/face.py
found them; faces of the imported mesh whose centre lies within 0.1 mm of one are the face.
The gain is cut back so the rest of the texture never loses more than --max-loss of its texels across.

Writes <out.glb>, the maps next to it (<key>.png) and <out>_report.json: islands, face_share_before, island_scale,
pack_scale, gain (measured: the face's texels across after over before), rest_scale, rebake_seconds.
"""
import json
import math
import os
import sys
import time

import bmesh
import bpy
from mathutils import Vector
from mathutils.kdtree import KDTree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402
import numpy as np  # noqa: E402
import rebake  # noqa: E402
import uvfit  # noqa: E402  (pure maths next to this script)

argv = _common.after_double_dash()
src, dst = os.path.abspath(argv[0]), os.path.abspath(argv[1])
points_json = _common.opt("--points", None)
maps_json = _common.opt("--maps", None)
gain_target = float(_common.opt("--gain", 2.0))
tex_size = int(_common.opt("--tex", 2048))
padding_px = int(_common.opt("--padding", max(4, tex_size // 256)))
margin_px = int(_common.opt("--margin", max(8, tex_size // 128)))
max_loss = float(_common.opt("--max-loss", 0.12))
with open(points_json) as f:
    points = json.load(f)
with open(maps_json) as f:
    maps = {k: v for k, v in json.load(f).items() if v and os.path.isfile(v)}

_common.open_glb(src)
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
if not meshes:
    raise SystemExit("no mesh")
mesh = meshes[0]
bpy.ops.object.select_all(action="DESELECT")
mesh.select_set(True)
bpy.context.view_layer.objects.active = mesh
source = rebake.copy_object(mesh, "vgac_bake_source")

# glTF (x, y, z) with Y up -> Blender (x, -z, y)
kd = KDTree(len(points))
for i, p in enumerate(points):
    kd.insert(Vector((p[0], -p[2], p[1])), i)
kd.balance()

bm = bmesh.new()
bm.from_mesh(mesh.data)
bm.faces.ensure_lookup_table()
uv = bm.loops.layers.uv.active
mw = mesh.matrix_world
face_set = set()
for f in bm.faces:
    c = mw @ f.calc_center_median()
    _, _, dist = kd.find(c)
    if dist is not None and dist < 1e-4:
        face_set.add(f.index)


def uv_key(loop):
    u = loop[uv].uv
    return (round(u.x, 6), round(u.y, 6))


# UV islands: faces joined across an edge whose two ends have the same UVs on both sides.
parent = list(range(len(bm.faces)))


def find(x):
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


for e in bm.edges:
    lf = e.link_faces
    if len(lf) != 2:
        continue
    a, b = lf
    la = {l.vert.index: uv_key(l) for l in a.loops if l.vert in e.verts}
    lb = {l.vert.index: uv_key(l) for l in b.loops if l.vert in e.verts}
    if la == lb:
        ra, rb = find(a.index), find(b.index)
        if ra != rb:
            parent[rb] = ra
island_of = [find(f.index) for f in bm.faces]
face_islands = {island_of[i] for i in face_set}


def uv_area(faces):
    total = 0.0
    for f in faces:
        ls = f.loops
        p0 = ls[0][uv].uv
        for i in range(1, len(ls) - 1):
            p1, p2 = ls[i][uv].uv, ls[i + 1][uv].uv
            total += abs((p1.x - p0.x) * (p2.y - p0.y) - (p2.x - p0.x) * (p1.y - p0.y)) / 2
    return total


all_area = uv_area(bm.faces)
isl_faces = [f for f in bm.faces if island_of[f.index] in face_islands]
isl_area = uv_area(isl_faces)
face_area_before = uv_area([bm.faces[i] for i in face_set])
share = isl_area / max(all_area, 1e-12)

original_uv = {(f.index, i): l[uv].uv.copy() for f in bm.faces for i, l in enumerate(f.loops)}
bm.to_mesh(mesh.data)
bm.free()
# Blender's packer keeps the margin on every side of every island, so two islands end up twice the margin apart:
# half the padding gives the padding asked for between islands (with the full padding the knight's layout lost 12
# percent of its texels across and its used share fell from 0.37 to 0.31; with half it kept them all, 0.41 used).
margin = max(1.0, padding_px / 2.0) / float(max(1, tex_size))


def set_layout(k, pin_rest):
    """Start from the original layout, scale every face island by k about its own centre, and pin (or unpin) the
    UVs of every other island."""
    b = bmesh.new()
    b.from_mesh(mesh.data)
    b.faces.ensure_lookup_table()
    lay = b.loops.layers.uv.active
    for f in b.faces:
        for i, l in enumerate(f.loops):
            l[lay].uv = original_uv[(f.index, i)]
            l[lay].pin_uv = bool(pin_rest) and island_of[f.index] not in face_islands
    for isl in face_islands:
        fs = [f for f in b.faces if island_of[f.index] == isl]
        loops = [l for f in fs for l in f.loops]
        cx = sum(l[lay].uv.x for l in loops) / len(loops)
        cy = sum(l[lay].uv.y for l in loops) / len(loops)
        for l in loops:
            u = l[lay].uv
            l[lay].uv = Vector((cx + (u.x - cx) * k, cy + (u.y - cy) * k))
    b.to_mesh(mesh.data)
    b.free()
    mesh.data.update()


def pack(**kw):
    bpy.ops.object.select_all(action="DESELECT")
    mesh.select_set(True)
    bpy.context.view_layer.objects.active = mesh
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.select_all(action="SELECT")
    try:
        bpy.ops.uv.pack_islands(rotate=True, rotate_method="ANY", margin_method="FRACTION", margin=margin, shape_method="CONCAVE", **kw)
    except (TypeError, RuntimeError) as e:
        print("FACEUV concave packing unavailable:", e)
        bpy.ops.uv.pack_islands(rotate=True, margin=margin, **{k: v for k, v in kw.items() if k == "scale"})
    bpy.ops.object.mode_set(mode="OBJECT")


def layout_ok():
    """Every UV inside the square, and the pinned islands exactly where they were."""
    b = bmesh.new()
    b.from_mesh(mesh.data)
    b.faces.ensure_lookup_table()
    lay = b.loops.layers.uv.active
    ok = True
    for f in b.faces:
        for i, l in enumerate(f.loops):
            u = l[lay].uv
            if not (-1e-6 <= u.x <= 1 + 1e-6 and -1e-6 <= u.y <= 1 + 1e-6):
                ok = False
            if island_of[f.index] not in face_islands and (u - original_uv[(f.index, i)]).length > 1e-6:
                ok = False
        if not ok:
            break
    b.free()
    return ok


def island_tris(k):
    """(fixed triangles of every other island, [triangles of each face island scaled by k about its centre]) in UV."""
    fixed, per = [], {isl: [] for isl in face_islands}
    for f in bm_tris.faces:
        loops = f.loops
        pts = [original_uv[(f.index, i)] for i in range(len(loops))]
        tris = [(pts[0], pts[i], pts[i + 1]) for i in range(1, len(pts) - 1)]
        if island_of[f.index] in face_islands:
            per[island_of[f.index]].extend(tris)
        else:
            fixed.extend(tris)
    out = []
    keys = sorted(per)
    for isl in keys:
        a = np.array([[[p.x, p.y] for p in t] for t in per[isl]], dtype=np.float64)
        c = a.reshape(-1, 2).mean(axis=0)
        out.append((a - c) * k + c)
    return np.array([[[p.x, p.y] for p in t] for t in fixed], dtype=np.float64), out, keys


def apply_fit(k, placements, keys):
    b = bmesh.new()
    b.from_mesh(mesh.data)
    b.faces.ensure_lookup_table()
    lay = b.loops.layers.uv.active
    centre = {}
    for isl in keys:
        pts = [original_uv[(f.index, i)] for f in bm_tris.faces if island_of[f.index] == isl for i in range(len(f.loops))]
        centre[isl] = np.array([sum(p.x for p in pts) / len(pts), sum(p.y for p in pts) / len(pts)])
    place = dict(zip(keys, placements))
    for f in b.faces:
        isl = island_of[f.index]
        for i, l in enumerate(f.loops):
            u = original_uv[(f.index, i)]
            if isl in place:
                c = centre[isl]
                p = (np.array([u.x, u.y]) - c) * k + c
                p = uvfit.apply_placement(p, place[isl])
                l[lay].uv = Vector((float(p[0]), float(p[1])))
            else:
                l[lay].uv = u
    b.to_mesh(mesh.data)
    b.free()
    mesh.data.update()


# 1. Fit: every other island stays exactly where it is; the bigger face islands go into the free space
#    (blender/uvfit.py). Only at the asked gain: the unwrapper packs tightly, so there is usually room for a small
#    gain only, and then the repack below gives more.
bm_tris = bmesh.new()
bm_tris.from_mesh(mesh.data)
bm_tris.faces.ensure_lookup_table()
mode, g, k = None, 1.0, 1.0
fit_res = 1024 if tex_size >= 2048 else 512
pad_cells = max(1, int(math.ceil(padding_px * fit_res / tex_size)))
if gain_target > 1.0:
    fixed, isl_t, keys = island_tris(gain_target)
    placements = uvfit.place_islands(fixed, isl_t, res=fit_res, pad_cells=pad_cells)
    if placements is not None:
        apply_fit(gain_target, placements, keys)
        if layout_ok():
            mode, g, k = "fit", gain_target, gain_target
bm_tris.free()
# 2. Otherwise repack everything with the face islands scaled up: island scale k for a wanted face gain g, when the
#    packing then scales everything by s: g = k * s and s^2 = 1 / (1 - share + share * k^2) (same packing
#    efficiency) -> k^2 = g^2 (1 - share) / (1 - share * g^2). The rest keeps s of its texels across, at least
#    1 - max_loss. Blender's packer is less tight than the unwrapper's, so the gain and the loss are measured after
#    each pack and k corrected (at most four packs); the best layout within the loss limit is kept.


def measure():
    b = bmesh.new()
    b.from_mesh(mesh.data)
    b.faces.ensure_lookup_table()
    lay = b.loops.layers.uv.active
    global uv
    uv = lay
    fa = uv_area([b.faces[i] for i in face_set])
    ra = uv_area([f for f in b.faces if island_of[f.index] not in face_islands])
    b.free()
    return math.sqrt(fa / max(face_area_before, 1e-12)), (math.sqrt(ra / max(all_area - isl_area, 1e-12)) if all_area > isl_area else 1.0)


if mode is None and gain_target > 1.0:
    g = gain_target
    while g > 1.0:
        if share * g * g < 0.9:
            s = g / math.sqrt(g * g * (1 - share) / (1 - share * g * g))
            if s >= 1 - max_loss:
                break
        g -= 0.05
    g = max(1.0, g)
    k = math.sqrt(g * g * (1 - share) / max(1 - share * g * g, 1e-6)) if g > 1.0 else 1.0
    best = None  # (gain, k)
    for _ in range(4):
        set_layout(k, pin_rest=False)
        pack(scale=True)
        got, rest = measure()
        print("FACEUV repack k", round(k, 3), "gain", round(got, 3), "rest", round(rest, 3))
        if rest >= 1 - max_loss and (best is None or got > best[0]):
            best = (got, k)
        if rest < 1 - max_loss:
            k = 1.0 + (k - 1.0) * 0.7
        elif got >= 0.97 * gain_target:
            break
        else:
            k *= gain_target / max(got, 1e-6)
    if best is None:
        k, mode = 1.0, "unchanged"
        set_layout(1.0, pin_rest=False)
    else:
        k, mode = best[1], "repack"
        set_layout(k, pin_rest=False)
        pack(scale=True)
if mode is None:
    k, mode = 1.0, "unchanged"
    set_layout(1.0, pin_rest=False)
uv = None
print("FACEUV islands", len(face_islands), "faces", len(face_set), "share", round(share, 5), "gain asked", gain_target,
      "mode", mode, "planned", round(g, 3), "k", round(k, 3))
# clear the pins
b = bmesh.new()
b.from_mesh(mesh.data)
lay = b.loops.layers.uv.active
for f in b.faces:
    for l in f.loops:
        l[lay].pin_uv = False
b.to_mesh(mesh.data)
b.free()
mesh.data.update()

bm = bmesh.new()
bm.from_mesh(mesh.data)
bm.faces.ensure_lookup_table()
uv = bm.loops.layers.uv.active
all_after = uv_area(bm.faces)
face_area_after = uv_area([bm.faces[i] for i in face_set])
rest_before = all_area - isl_area
rest_after = uv_area([f for f in bm.faces if island_of[f.index] not in face_islands])
bm.free()
gain = math.sqrt(face_area_after / max(face_area_before, 1e-12))
rest_scale = math.sqrt(rest_after / max(rest_before, 1e-12)) if rest_before > 0 else 1.0

t0 = time.time()
rebake.triangulate(mesh)
bake = rebake.rebake(source, mesh, maps, os.path.dirname(dst), tex_size, padding_px, margin_px, repack=False)

bpy.ops.object.select_all(action="SELECT")
bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", use_selection=True, export_apply=True)
report = {
    "faces": len(face_set), "islands": len(face_islands), "face_share_before": round(share, 5),
    "gain_asked": gain_target, "layout_mode": mode, "gain_planned": round(g, 3), "island_scale": round(k, 4), "gain": round(gain, 3),
    "rest_scale": round(rest_scale, 4), "uv_area_before": round(all_area, 5), "uv_area_after": round(all_after, 5),
    "glb": dst, **bake,
}
with open(os.path.splitext(dst)[0] + "_report.json", "w") as f:
    json.dump(report, f, indent=1)
print("FACEUV_OK", json.dumps(report))
