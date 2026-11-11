"""Blender headless trial for Lane C item 5b of docs/TEXTURE-FIDELITY-PLAN.md: fewer, larger UV islands.

Run: blender -b --python bakeoff/unwrap_trial.py -- <model.glb> <out_dir> <mode> <maps.json> <tex>
modes: repack (the 3D step's islands, Average Islands Scale and a concave pack), pec (keep the 3D step's own layout, re-baked: the control), smart<deg> (Smart UV Project at that angle limit),
       seams<deg> (seams on edges sharper than that angle, then an angle-based unwrap).
Every mode except pec is followed by Average Islands Scale and a concave pack (margin = gutter in pixels, as the
pipeline's), then bakes every map from the original layout onto the new one with the pipeline's own blender/rebake.py.
Writes <out_dir>/model.glb, the maps, and <out_dir>/uv_report.json (islands, texel density, seconds).
"""
import json
import math
import os
import sys
import time

import bpy
import bmesh

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend", "vgac", "blender"))
import _common  # noqa: E402
import rebake  # noqa: E402

src, out_dir, mode, maps_json, tex = sys.argv[sys.argv.index("--") + 1:][:5]
tex = int(tex)
os.makedirs(out_dir, exist_ok=True)
maps = {k: v for k, v in json.load(open(maps_json)).items() if v and os.path.isfile(v)}
padding = max(4, tex // 256)
margin_px = max(8, tex // 128)

_common.open_glb(src)
mesh = [o for o in bpy.context.scene.objects if o.type == "MESH"][0]
bpy.ops.object.select_all(action="DESELECT")
mesh.select_set(True)
bpy.context.view_layer.objects.active = mesh
source = rebake.copy_object(mesh, "vgac_bake_source")


def stats(obj):
    """Islands (faces joined across edges whose two sides share UVs), texel density (area-weighted mean and 10th
    percentile of pixels per metre) and the share of the unit square the faces cover."""
    me = obj.data
    uv = me.uv_layers.active.data
    parent = list(range(len(me.polygons)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    seen = {}
    for p in me.polygons:
        for li in p.loop_indices:
            key = (me.loops[li].vertex_index, round(uv[li].uv.x, 5), round(uv[li].uv.y, 5))
            if key in seen:
                parent[find(p.index)] = find(seen[key])
            else:
                seen[key] = p.index
    islands = len({find(i) for i in range(len(me.polygons))})
    dens, w, cover = [], [], 0.0
    for p in me.polygons:
        pts = [uv[li].uv for li in p.loop_indices]
        a_uv = 0.0
        for i in range(1, len(pts) - 1):
            a_uv += abs((pts[i].x - pts[0].x) * (pts[i + 1].y - pts[0].y) - (pts[i + 1].x - pts[0].x) * (pts[i].y - pts[0].y)) / 2.0
        cover += a_uv
        if p.area > 1e-12:
            dens.append(math.sqrt(a_uv / p.area) * tex)
            w.append(p.area)
    order = sorted(range(len(dens)), key=lambda i: dens[i])
    tot, acc, p10 = sum(w), 0.0, 0.0
    for i in order:
        acc += w[i]
        if acc >= 0.1 * tot:
            p10 = dens[i]
            break
    mean = sum(d * x for d, x in zip(dens, w)) / tot
    return {"islands": islands, "uv_area_share": round(cover, 3), "texel_density_mean": round(mean, 1), "texel_density_p10": round(p10, 1)}


t0 = time.time()
before = stats(mesh)
if mode != "pec":
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    if mode == "repack":
        pass  # keep the 3D step's own islands (677 on the knight), only equalise their scale and pack again
    elif mode.startswith("smart"):
        bpy.ops.uv.smart_project(angle_limit=math.radians(float(mode[5:])), island_margin=0.0, scale_to_bounds=False)
    elif mode.startswith("seams"):
        bpy.ops.mesh.select_all(action="DESELECT")
        bpy.ops.mesh.select_mode(type="EDGE")
        bpy.ops.mesh.edges_select_sharp(sharpness=math.radians(float(mode[5:])))
        bpy.ops.mesh.mark_seam(clear=False)
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.unwrap(method="ANGLE_BASED", margin=0.0)
    else:
        raise SystemExit("unknown mode " + mode)
    bpy.ops.uv.select_all(action="SELECT")
    bpy.ops.uv.average_islands_scale()
    m = padding / float(tex)
    bpy.ops.uv.pack_islands(rotate=True, rotate_method="ANY", scale=True, margin_method="FRACTION", margin=m, shape_method="CONCAVE")
    bpy.ops.object.mode_set(mode="OBJECT")
after = stats(mesh)
rebake.triangulate(mesh)
bake = rebake.rebake(source, mesh, maps, out_dir, tex, padding, margin_px, repack=False)
bpy.ops.object.select_all(action="DESELECT")
mesh.select_set(True)
bpy.context.view_layer.objects.active = mesh
bpy.ops.export_scene.gltf(filepath=os.path.join(out_dir, "model.glb"), export_format="GLB", use_selection=True, export_apply=True)
json.dump({"mode": mode, "before": before, "after": after, "faces": len(mesh.data.polygons), "seconds": round(time.time() - t0, 1), "rebake_seconds": bake["rebake_seconds"]},
          open(os.path.join(out_dir, "uv_report.json"), "w"), indent=1)
print("UVTRIAL", json.dumps(after))
