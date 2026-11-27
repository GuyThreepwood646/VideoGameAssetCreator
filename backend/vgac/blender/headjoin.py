"""Blender headless: one closed, smooth surface and one texture for a model joined from two builds (the head built on
its own, vgac/headgraft.py).

Run: blender -b --python headjoin.py -- <graft.glb> <out.glb> --maps maps.json [--tex 2048] [--padding 8] [--margin 16]
     [--strip N]

Input: the joined mesh with its UVs on a square sheet twice the texture size (the body's texture top-left, the head's
top-right), and the sheets (maps.json: {key: png}). headgraft cut the body and the head at two rings one triangle's
height apart, already matched in shape and skin tone, and closed the gap with a strip of triangles (the last N faces,
--strip), which read the body's texture. Here:
1. Vertices are welded into one closed surface; head faces nobody can see (the inner wall of a head built as a thin
   shell) are deleted with any loose bits they leave; what is still open, a small tear, is filled, and the result is
   repaired until it is one piece with no open or folded edge; the strip's vertices
   and their neighbours are smoothed a little so the two necks meet without a crease, and the whole model is shaded
   smooth.
2. Faces made here (fills) take texture positions their corners already have.
3. Every texture piece is packed again to fill one square texture (Blender's packer, no rotation, relative sizes kept,
   so the head keeps the share headgraft gave it and the normal map's directions stay valid).
4. Every map is copied texel by texel from the sheet through each triangle's old and new texture positions (the mesh
   does not change, so nothing is cast or baked: no ray picks up a collar's inside or a neighbouring surface), then
   grown past the pieces' edges.
One material, one atlas, the usual size.

Writes <out.glb>, the maps next to it (<key>.png) and <out>_report.json: closing, faces, copy seconds.
"""
import json
import os
import sys
import time

import bpy
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402
import rebake  # noqa: E402

argv = _common.after_double_dash()
src, dst = os.path.abspath(argv[0]), os.path.abspath(argv[1])
maps_json = _common.opt("--maps", None)
tex_size = int(_common.opt("--tex", 2048))
padding_px = int(_common.opt("--padding", max(4, tex_size // 256)))
strip_faces = int(_common.opt("--strip", 0))
SMOOTH_FACTOR = 0.5
SMOOTH_STEPS = 3
REPAIR_ROUNDS = 4
NEAR_UV = 0.02  # of the sheet: a filled face whose corners' texture positions are this close keeps them
margin_px = int(_common.opt("--margin", max(8, tex_size // 128)))
with open(maps_json) as f:
    maps = {k: v for k, v in json.load(f).items() if v and os.path.isfile(v)}

t0 = time.time()
_common.open_glb(src)
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
if not meshes:
    raise SystemExit("no mesh")
mesh = meshes[0]
bpy.ops.object.select_all(action="DESELECT")
mesh.select_set(True)
bpy.context.view_layer.objects.active = mesh


def _boundary_loops(bm):
    """Open edges grouped into connected loops, largest first: [(edges, verts)]."""
    open_e = [e for e in bm.edges if e.is_boundary]
    seen, loops = set(), []
    for e0 in open_e:
        if e0.index in seen:
            continue
        stack, edges, verts = [e0], [], set()
        seen.add(e0.index)
        while stack:
            e = stack.pop()
            edges.append(e)
            for v in e.verts:
                verts.add(v)
                for e2 in v.link_edges:
                    if e2.is_boundary and e2.index not in seen:
                        seen.add(e2.index)
                        stack.append(e2)
        loops.append((edges, verts))
    loops.sort(key=lambda lv: -len(lv[0]))
    return loops


def _body_face(f, old, uv):
    """The body's triangle (texture on the left half of the sheet) a new face rests on: one sharing an edge with it,
    else one sharing a corner; None when it touches no body surface."""
    best = None
    for e in f.edges:
        for g in e.link_faces:
            if g is not f and g in old and g.is_valid and all(l[uv].uv.x < 0.5 for l in g.loops):
                return g
    for v in f.verts:
        for g in v.link_faces:
            if g in old and g.is_valid and all(l[uv].uv.x < 0.5 for l in g.loops):
                best = best or g
    return best


def _uv_on(g, uv, p):
    """The texture position of point p under the triangle g's own mapping."""
    a, b, c = (l.vert.co for l in g.loops[:3])
    ua, ub, uc = (l[uv].uv for l in g.loops[:3])
    v0, v1, v2 = b - a, c - a, p - a
    d00, d01, d11 = v0.dot(v0), v0.dot(v1), v1.dot(v1)
    d20, d21 = v2.dot(v0), v2.dot(v1)
    den = d00 * d11 - d01 * d01
    if abs(den) < 1e-20:
        return ua.copy()
    s_ = (d11 * d20 - d01 * d21) / den
    t_ = (d00 * d21 - d01 * d20) / den
    # held inside the triangle (past its edge the texture can be another piece or padding): the strip repeats the
    # colour along the body's edge, one triangle's height
    w = [max(0.0, 1.0 - s_ - t_), max(0.0, s_), max(0.0, t_)]
    n = sum(w) or 1.0
    return (ua * w[0] + ub * w[1] + uc * w[2]) / n


def drop_hidden(bm, uv):
    """The head's faces nobody can see, deleted: a head build can come out as a thin shell whose inner wall lines
    the head and neck. headgraft keeps only the outer skin at the ring, so the inner wall would be left with an open
    edge over the ring for the filling below to cap. A head face (texture on the right half of the sheet) is hidden
    when the rays from just in front of it, along its normal and along each of the six axes, all hit the model.
    Returns how many were deleted."""
    import bmesh
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    bm.faces.ensure_lookup_table()
    tree = BVHTree.FromBMesh(bm)
    lo = Vector((min(v.co.x for v in bm.verts), min(v.co.y for v in bm.verts), min(v.co.z for v in bm.verts)))
    hi = Vector((max(v.co.x for v in bm.verts), max(v.co.y for v in bm.verts), max(v.co.z for v in bm.verts)))
    eps = 1e-4 * (hi - lo).length
    axes = [Vector(d) for d in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))]
    hidden = []
    for f in bm.faces:
        if f.normal.length < 0.5 or not all(l[uv].uv.x > 0.5 for l in f.loops):
            continue
        c = f.calc_center_median() + f.normal * eps
        if all(tree.ray_cast(c, d)[0] is not None for d in (f.normal, *axes)):
            hidden.append(f)
    if hidden:
        bmesh.ops.delete(bm, geom=hidden, context="FACES_ONLY")
        loose = [v for v in bm.verts if not v.link_faces]
        if loose:
            bmesh.ops.delete(bm, geom=loose, context="VERTS")
    return len(hidden)


def _pieces(bm):
    """The faces in connected pieces (across shared edges), largest first."""
    seen, out = set(), []
    for f in bm.faces:
        if f in seen:
            continue
        comp, stack = [f], [f]
        seen.add(f)
        while stack:
            g = stack.pop()
            for e in g.edges:
                for h in e.link_faces:
                    if h not in seen:
                        seen.add(h)
                        comp.append(h)
                        stack.append(h)
        out.append(comp)
    out.sort(key=len, reverse=True)
    return out


def keep_largest(bm):
    """All but the largest piece deleted (bits of skin left between deleted hidden faces). Returns faces deleted."""
    import bmesh
    extra = [f for comp in _pieces(bm)[1:] for f in comp]
    if extra:
        bmesh.ops.delete(bm, geom=extra, context="FACES_ONLY")
        loose = [v for v in bm.verts if not v.link_faces]
        if loose:
            bmesh.ops.delete(bm, geom=loose, context="VERTS")
    return len(extra)


def fill_open(bm, rep):
    """Every open loop filled: Blender's hole fill, else a fan from a new middle point."""
    import bmesh
    bm.edges.ensure_lookup_table()
    bm.edges.index_update()
    for edges, _ in _boundary_loops(bm):
        try:
            bmesh.ops.holes_fill(bm, edges=edges, sides=0)
        except Exception as ex:
            rep.setdefault("fill_errors", []).append(str(ex)[:80])
    for edges, verts in _boundary_loops(bm):
        # what holes_fill leaves (a hole whose edge touches itself): a fan from a new middle point
        if len(edges) > 200:
            continue
        try:
            c = sum((v.co for v in verts), verts.__iter__().__next__().co * 0) / len(verts)
            mid = bm.verts.new(c)
            for e in edges:
                a_, b_ = e.verts
                try:
                    bm.faces.new((a_, b_, mid))
                except ValueError:
                    pass
        except Exception as ex:
            rep.setdefault("fill_errors", []).append(str(ex)[:80])
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])


def repair(bm, old, rep):
    """One closed surface in one piece: an edge with more than two faces loses the faces made here on it (else its
    smallest, after the first round every face on it), a face repeated with the same corners goes, the hole that
    leaves is filled again, and loose pieces are dropped; up to REPAIR_ROUNDS times."""
    import bmesh
    for n in range(REPAIR_ROUNDS):
        bm.verts.index_update()
        bad = [e for e in bm.edges if len(e.link_faces) > 2]
        if not bad and not any(e.is_boundary for e in bm.edges) and len(_pieces(bm)) == 1:
            break
        drop = set()
        for e in bad:
            # first the faces made here on it (else its smallest); if that did not settle it, every face on it
            made = [f for f in e.link_faces if f not in old]
            drop |= set(e.link_faces) if n else set(made or [min(e.link_faces, key=lambda f: f.calc_area())])
        corners: dict = {}
        for f in bm.faces:  # a face twice over (the same corners) folds the surface onto itself
            k = frozenset(v.index for v in f.verts)
            if k in corners:
                drop.add(f)
            corners[k] = f
        if drop:
            bmesh.ops.delete(bm, geom=list(drop), context="FACES_ONLY")
        keep_largest(bm)
        fill_open(bm, rep)
        rep["repair_rounds"] = n + 1


def close_shell(obj):
    """One closed surface: the body's ring and the head's ring are bridged, and small tears are filled. All faces are
    triangles on return and the new ones are selected. Returns a report."""
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bridge_faces = list(bm.faces[len(bm.faces) - strip_faces:]) if 0 < strip_faces <= len(bm.faces) else None
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)  # glTF splits vertices at texture seams
    uv = bm.loops.layers.uv.active
    bm.normal_update()
    hidden = drop_hidden(bm, uv)
    loose_faces = keep_largest(bm)
    bm.edges.ensure_lookup_table()
    before = sum(1 for e in bm.edges if e.is_boundary)
    for f in bm.faces:
        f.select = False
    old = set(bm.faces)
    loops = _boundary_loops(bm)

    def is_head(edges):
        # the graft put the head's texture on the right half of the sheet (u above 0.5), the body's on the left
        us = [l[uv].uv.x for e in edges for f in e.link_faces for l in f.loops]
        return bool(us) and sum(us) / len(us) > 0.5

    heads = [l for l in loops if is_head(l[0])]
    bodies = [l for l in loops if not is_head(l[0])]
    rep = {"hidden_faces_dropped": hidden, "loose_faces_dropped": loose_faces, "open_edges_before": before, "head_loops": [len(l[0]) for l in heads][:8],
           "body_loops": [len(l[0]) for l in bodies][:8], "bridged": False}
    if heads and bodies:
        try:
            res = bmesh.ops.bridge_loops(bm, edges=heads[0][0] + bodies[0][0], use_pairs=False, twist_offset=0)
            rep["bridged"] = bool(res.get("faces"))
        except Exception as ex:  # a loop that touches itself cannot be bridged
            rep["bridge_error"] = str(ex)
    fill_open(bm, rep)
    repair(bm, old, rep)
    if strip_faces and bridge_faces is not None:
        # the strip and the ring of vertices round it, smoothed a little: the two necks meet at slightly different
        # slopes, a crease a grazing light shows
        band = {v for f in bridge_faces if f.is_valid for v in f.verts}
        band |= {e.other_vert(v) for v in list(band) for e in v.link_edges}
        for _ in range(SMOOTH_STEPS):
            bmesh.ops.smooth_vert(bm, verts=list(band), factor=SMOOTH_FACTOR, use_axis_x=True, use_axis_y=True,
                                  use_axis_z=True)
        rep["smoothed_vertices"] = len(band)
    new = [f for f in bm.faces if f not in old]
    # the new faces read the body's texture: each strip face takes the texture mapping of the body triangle at the
    # ring it rests on, so the strip shows the body's neck along the ring and runs on from it without a break. A face
    # with no body triangle under it (a filled hole) keeps its corners' own positions when they lie close together (one
    # texture piece), else takes one corner's position for all (spread over the sheet it would read everything between).
    # Either way it shares positions with its piece and is packed with it.
    at = {}
    for f in old:
        if f.is_valid:
            for l in f.loops:
                at.setdefault(l.vert, l[uv].uv.copy())
    mapped = 0
    for f in new:
        f.select = True
        base = _body_face(f, old, uv)
        if base is not None:
            for l in f.loops:
                l[uv].uv = _uv_on(base, uv, l.vert.co)
            mapped += 1
            continue
        corners = [at[v] for v in f.verts if v in at]
        if not corners:
            continue
        if len(corners) == len(f.verts) and max((a - b).length for a in corners for b in corners) < NEAR_UV:
            for l in f.loops:  # corners on one texture piece: each its own position
                l[uv].uv = at[l.vert]
            continue
        body_side = [c for c in corners if c.x < 0.5]
        pick = (body_side or corners)[0]
        for l in f.loops:
            l[uv].uv = pick
    rep["strip_faces_mapped"] = mapped
    for f in bm.faces:
        f.smooth = True
    rep["new_faces"] = len(new)
    rep["open_edges_after"] = sum(1 for e in bm.edges if e.is_boundary)
    rep["non_manifold_edges_after"] = sum(1 for e in bm.edges if len(e.link_faces) > 2)
    rep["pieces_after"] = len(_pieces(bm))
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return rep


def _uvs(me):
    a = np.empty(len(me.loops) * 2, dtype=np.float32)
    me.uv_layers.active.data.foreach_get("uv", a)
    return a.reshape(-1, 2).astype(np.float64)


def _tris(me):
    n = len(me.polygons)
    start = np.empty(n, dtype=np.int64)
    total = np.empty(n, dtype=np.int64)
    me.polygons.foreach_get("loop_start", start)
    me.polygons.foreach_get("loop_total", total)
    if (total != 3).any():
        raise SystemExit("not all triangles")
    return start[:, None] + np.arange(3)[None, :]


def raster(dst_uv, src_uv, tris, size):
    """Every texel of the new layout that a triangle covers: (row, column, source u, source v), rows from the bottom
    (Blender's image order). A texel whose centre is within a small margin of a triangle counts, so neighbouring
    triangles leave no gaps."""
    rows, cols, su, sv, tri_of = [], [], [], [], []
    P = dst_uv * size - 0.5
    for t, lp in enumerate(tris):
        x, y = P[lp, 0], P[lp, 1]
        x0, x1 = max(0, int(np.floor(x.min()))), min(size - 1, int(np.ceil(x.max())))
        y0, y1 = max(0, int(np.floor(y.min()))), min(size - 1, int(np.ceil(y.max())))
        if x1 < x0 or y1 < y0:
            continue
        d = (y[1] - y[2]) * (x[0] - x[2]) + (x[2] - x[1]) * (y[0] - y[2])
        if abs(d) < 1e-12:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
        l0 = ((y[1] - y[2]) * (gx - x[2]) + (x[2] - x[1]) * (gy - y[2])) / d
        l1 = ((y[2] - y[0]) * (gx - x[2]) + (x[0] - x[2]) * (gy - y[2])) / d
        l2 = 1.0 - l0 - l1
        m = (l0 >= -0.05) & (l1 >= -0.05) & (l2 >= -0.05)
        if not m.any():
            continue
        b = np.stack([l0[m], l1[m], l2[m]], axis=1)
        b = np.clip(b, 0.0, None)
        b /= b.sum(axis=1, keepdims=True)
        s = src_uv[lp]
        rows.append(gy[m])
        cols.append(gx[m])
        su.append(b @ s[:, 0])
        sv.append(b @ s[:, 1])
        tri_of.append(np.full(int(m.sum()), t))
    cat = np.concatenate
    return cat(rows), cat(cols), cat(su), cat(sv), cat(tri_of)


def _read(path):
    img = bpy.data.images.load(path, check_existing=False)
    img.colorspace_settings.name = "Non-Color"  # raw values: copied as stored
    w, h = img.size[:]
    a = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(a)
    bpy.data.images.remove(img)
    return a.reshape(h, w, 4)


def _sample(img, u, v):
    """Bilinear reads of img (rows from the bottom) at texture positions u, v."""
    h, w = img.shape[:2]
    x = np.clip(u * w - 0.5, 0, w - 1)
    y = np.clip(v * h - 0.5, 0, h - 1)
    x0, y0 = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    top = img[y0, x0] * (1 - fx) + img[y0, x1] * fx
    bot = img[y1, x0] * (1 - fx) + img[y1, x1] * fx
    return top * (1 - fy) + bot * fy


def _grow(img, filled, steps):
    """Texels outside the pieces take the mean of their filled neighbours, `steps` texels out; the rest takes the
    map's mean (so smaller mip levels do not darken toward the edges)."""
    img = img.copy()
    filled = filled.copy()
    for _ in range(steps):
        acc = np.zeros_like(img)
        cnt = np.zeros(filled.shape, dtype=np.float32)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            f = np.roll(np.roll(filled, dy, axis=0), dx, axis=1)
            acc += np.roll(np.roll(img, dy, axis=0), dx, axis=1) * f[..., None]
            cnt += f
        new = ~filled & (cnt > 0)
        if not new.any():
            break
        img[new] = acc[new] / cnt[new][:, None]
        filled |= new
    if filled.any():
        img[~filled] = img[filled].mean(axis=0)
    return img


def _write(arr, path):
    h, w = arr.shape[:2]
    img = bpy.data.images.new(os.path.basename(path), w, h, alpha=False, float_buffer=False)
    img.colorspace_settings.name = "Non-Color"
    out = arr.copy()
    out[..., 3] = 1.0
    img.pixels.foreach_set(out.reshape(-1).astype(np.float32))
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    return img


closing = close_shell(mesh)
print("HEADJOIN close", json.dumps(closing))
me = mesh.data
tris = _tris(me)
src_uv = _uvs(me)
sel = np.zeros(len(me.polygons), dtype=bool)
me.polygons.foreach_get("select", sel)
margin = max(1.0, padding_px / 2.0) / float(max(1, tex_size))
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.select_all(action="SELECT")
try:
    bpy.ops.uv.pack_islands(rotate=False, scale=True, margin_method="FRACTION", margin=margin, shape_method="CONCAVE")
except (TypeError, RuntimeError) as e:
    print("HEADJOIN concave packing unavailable:", e)
    bpy.ops.uv.pack_islands(rotate=False, scale=True, margin=margin)
bpy.ops.object.mode_set(mode="OBJECT")
if mesh.data.has_custom_normals:
    bpy.ops.mesh.customdata_custom_splitnormals_clear()
dst_uv = _uvs(me)

t1 = time.time()
out_dir = os.path.dirname(dst)
written, images, sizes = {}, {}, {}
cache = {}
for key in ("base_color", "metallic", "roughness", "ao", "normal"):
    if key not in maps:
        continue
    sheet = _read(maps[key])
    # AO keeps its own resolution (half the sheet, as the body's AO is half the texture size)
    size = int(tex_size) if key != "ao" else max(64, min(int(tex_size), sheet.shape[0] // 2))
    if size not in cache:
        cache[size] = raster(dst_uv, src_uv, tris, size)
    rows, cols, su, sv, tri_of = cache[size]
    vals = _sample(sheet, su, sv)
    if key == "normal":
        vals[sel[tri_of]] = (0.5, 0.5, 1.0, 1.0)  # the bridge: no detail of its own
    out = np.zeros((size, size, 4), dtype=np.float32)
    filled = np.zeros((size, size), dtype=bool)
    out[rows, cols] = vals
    filled[rows, cols] = True
    out = _grow(out, filled, max(2, int(margin_px)))
    path = os.path.join(out_dir, f"{key}.png")
    images[key] = _write(out, path)
    images[key].colorspace_settings.name = rebake.MAPS[key][0]
    written[key] = path
    sizes[key] = size
    del sheet
copy_seconds = round(time.time() - t1, 2)
me.materials.clear()
me.materials.append(rebake._final_material(images))
bpy.ops.object.select_all(action="DESELECT")
mesh.select_set(True)
bpy.context.view_layer.objects.active = mesh
bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", use_selection=True, export_apply=True)
report = {"faces": len(mesh.data.polygons), "tex": tex_size, "padding_px": padding_px, "margin_px": margin_px,
          "copy": {"maps": written, "sizes": sizes, "seconds": copy_seconds}, "closing": closing,
          "seconds": round(time.time() - t0, 1)}
with open(os.path.splitext(dst)[0] + "_report.json", "w") as f:
    json.dump(report, f, indent=1, default=str)
print("HEADJOIN done", json.dumps({k: report[k] for k in ("faces", "seconds")}))
