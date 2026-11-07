"""Blender headless: one texture for a model joined from two builds (the head built on its own, vgac/headgraft.py).

Run: blender -b --python headjoin.py -- <graft.glb> <out.glb> --maps maps.json [--tex 2048] [--padding 8] [--margin 16]

Input: the joined mesh with its UVs on a square sheet twice the texture size (the body's texture top-left, the head's
top-right), and the sheets (maps.json: {key: png}). Every texture piece is packed again to fill one square texture
(Blender's packer, any rotation, relative sizes kept, so the head keeps the share headgraft gave it), then every map
is baked again from a copy of the mesh with the sheet layout (blender/rebake.py). One material, one atlas, the usual
size.

Writes <out.glb>, the maps next to it (<key>.png) and <out>_report.json: islands, faces, rebake_seconds.
"""
import json
import os
import sys
import time

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402
import rebake  # noqa: E402

argv = _common.after_double_dash()
src, dst = os.path.abspath(argv[0]), os.path.abspath(argv[1])
maps_json = _common.opt("--maps", None)
tex_size = int(_common.opt("--tex", 2048))
padding_px = int(_common.opt("--padding", max(4, tex_size // 256)))
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


def close_shell(obj):
    """One closed surface: the body (old neck cut out) and the new head (open at the bottom of its neck) are stitched
    with a short tube inside the collar, and small tears are filled. New faces are selected on return (they need
    their own texture space). Returns a report."""
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)  # glTF splits vertices at texture seams
    bm.edges.ensure_lookup_table()
    before = sum(1 for e in bm.edges if e.is_boundary)
    for f in bm.faces:
        f.select = False
    old = set(bm.faces)
    loops = _boundary_loops(bm)
    uv = bm.loops.layers.uv.active

    def is_head(edges):
        # the graft put the head's texture on the right half of the sheet (u above 0.5), the body's on the left
        us = [l[uv].uv.x for e in edges for f in e.link_faces for l in f.loops]
        return bool(us) and sum(us) / len(us) > 0.5

    heads = [l for l in loops if is_head(l[0])]
    bodies = [l for l in loops if not is_head(l[0])]
    rep = {"open_edges_before": before, "head_loops": [len(l[0]) for l in heads][:8],
           "body_loops": [len(l[0]) for l in bodies][:8], "bridged": False}
    if heads and bodies:
        try:
            res = bmesh.ops.bridge_loops(bm, edges=heads[0][0] + bodies[0][0], use_pairs=False, twist_offset=0)
            rep["bridged"] = bool(res.get("faces"))
        except Exception as ex:  # a loop that touches itself cannot be bridged
            rep["bridge_error"] = str(ex)
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
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if f not in old and len(f.verts) > 3])
    new = [f for f in bm.faces if f not in old]
    # the new faces take the texture position each corner already has on the old surface (the neck edges they join),
    # so the copy the bake reads from shows them neck-coloured; they get their own texture pieces after the copy
    at = {}
    for f in old:
        if f.is_valid:
            for l in f.loops:
                at.setdefault(l.vert, l[uv].uv.copy())
    for f in new:
        f.select = True
        for l in f.loops:
            if l.vert in at:
                l[uv].uv = at[l.vert]
    rep["new_faces"] = len(new)
    rep["open_edges_after"] = sum(1 for e in bm.edges if e.is_boundary)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return rep


closing = close_shell(mesh)
print("HEADJOIN close", json.dumps(closing))
source = rebake.copy_object(mesh, "vgac_bake_source")
margin = max(1.0, padding_px / 2.0) / float(max(1, tex_size))
bpy.ops.object.select_all(action="DESELECT")
mesh.select_set(True)
bpy.context.view_layer.objects.active = mesh
bpy.ops.object.mode_set(mode="EDIT")
if closing["new_faces"]:
    # the stitched and filled faces: their own small texture pieces (baked from the nearest surface like the rest)
    bpy.ops.uv.smart_project(angle_limit=1.15, island_margin=0.0, scale_to_bounds=False)
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.select_all(action="SELECT")
try:
    bpy.ops.uv.pack_islands(rotate=True, rotate_method="ANY", scale=True, margin_method="FRACTION", margin=margin,
                            shape_method="CONCAVE")
except (TypeError, RuntimeError) as e:
    print("HEADJOIN concave packing unavailable:", e)
    bpy.ops.uv.pack_islands(rotate=True, scale=True, margin=margin)
bpy.ops.object.mode_set(mode="OBJECT")

rebake.triangulate(mesh)
bake = rebake.rebake(source, mesh, maps, os.path.dirname(dst), tex_size, padding_px, margin_px, repack=False)
bpy.ops.object.select_all(action="DESELECT")
mesh.select_set(True)
bpy.context.view_layer.objects.active = mesh
bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", use_selection=True, export_apply=True)
report = {"faces": len(mesh.data.polygons), "tex": tex_size, "padding_px": padding_px, "margin_px": margin_px,
          "rebake": bake, "closing": closing, "seconds": round(time.time() - t0, 1)}
with open(os.path.splitext(dst)[0] + "_report.json", "w") as f:
    json.dump(report, f, indent=1, default=str)
print("HEADJOIN done", json.dumps({k: report[k] for k in ("faces", "seconds")}))
