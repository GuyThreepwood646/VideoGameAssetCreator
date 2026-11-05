"""Blender headless: repair skin weights on a rigged GLB.

Run: blender -b --python rig_fix.py -- <in.glb> <out.glb> [--reference <prep.glb>] [--report <json>]
Vertices with no bone influence copy the groups of the nearest weighted vertex (KD-tree); every vertex is then
limited to 4 influences and normalised. Prints RIGFIX_OK <fixed_unweighted> <verts>.

With --reference (the cleaned-up mesh the rigger was given) it also restores what the rigger drops:
- texture coordinates and the material (the rigger writes neither, so the model would be untextured);
- the real-world size and position (the rigger normalises every model to a 2 m box).
"""
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402  (shared helpers next to this script)
import json

from mathutils import Vector, kdtree

argv = _common.after_double_dash()
src, dst = os.path.abspath(argv[0]), os.path.abspath(argv[1])
reference = _common.opt("--reference", "")
report_path = _common.opt("--report", "")
_common.open_glb(src)
fixed = total = 0
# Drop helper meshes the rigger's exporter leaves behind (no vertex groups, not skinned): they would count as
# unweighted geometry and ship into the engine.
removed = []
for arm in [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]:
    for pb in arm.pose.bones:
        pb.custom_shape = None
for o in [o for o in bpy.context.scene.objects if o.type == "MESH" and not o.vertex_groups]:
    removed.append(o.name)
    data = o.data
    bpy.data.objects.remove(o, do_unlink=True)
    if data.users == 0:
        bpy.data.meshes.remove(data)
unnormalised = 0
for obj in [o for o in bpy.context.scene.objects if o.type == "MESH" and o.vertex_groups]:
    verts = obj.data.vertices
    total += len(verts)
    groups = obj.vertex_groups
    weighted = [v for v in verts if sum(g.weight for g in v.groups) > 1e-6]
    unweighted = [v for v in verts if sum(g.weight for g in v.groups) <= 1e-6]
    if unweighted and weighted:
        tree = kdtree.KDTree(len(weighted))
        for i, v in enumerate(weighted):
            tree.insert(v.co, i)
        tree.balance()
        for v in unweighted:
            _, idx, _ = tree.find(v.co)
            for g in weighted[idx].groups:
                groups[g.group].add([v.index], g.weight, "REPLACE")
            fixed += 1
    # Limit to 4 influences and normalise, in plain Python so it works headless and deterministically.
    for v in verts:
        entries = sorted(((g.group, g.weight) for g in v.groups if g.weight > 0), key=lambda t: -t[1])
        drop = entries[4:]
        entries = entries[:4]
        for gi, _ in drop:
            groups[gi].remove([v.index])
        total_w = sum(w for _, w in entries)
        if total_w <= 0:
            continue
        if abs(total_w - 1.0) > 1e-4:
            unnormalised += 1
            for gi, w in entries:
                groups[gi].add([v.index], w / total_w, "REPLACE")


def _world_box(objs):
    pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    lo = Vector([min(q[i] for q in pts) for i in range(3)])
    hi = Vector([max(q[i] for q in pts) for i in range(3)])
    return lo, hi


def restore_from_reference(meshes, path):
    """Copy UVs and the material from the reference mesh and scale and place the rig to match it. -> report."""
    out = {"reference": os.path.basename(path)}
    before = set(bpy.data.objects)
    _common.import_glb(path)
    new = [o for o in bpy.data.objects if o not in before]
    try:
        srcs = [o for o in new if o.type == "MESH"]
        if not srcs:
            return {**out, "ok": False, "reason": "reference has no mesh"}
        src_o = max(srcs, key=lambda o: len(o.data.polygons))
        target = max(meshes, key=lambda o: len(o.data.polygons))
        sd, td = src_o.data, target.data
        s_lo, s_hi = _world_box([src_o])
        t_lo, t_hi = _world_box([target])
        s_size, t_size = max(s_hi - s_lo) or 1.0, max(t_hi - t_lo) or 1.0
        s_mid, t_mid = (s_lo + s_hi) / 2, (t_lo + t_hi) / 2
        sw, tw = src_o.matrix_world, target.matrix_world
        # Texture coordinates: match each face to the nearest reference face in a size-independent frame.
        if sd.uv_layers and not td.uv_layers:
            tree = kdtree.KDTree(len(sd.polygons))
            for poly in sd.polygons:
                tree.insert((sw @ poly.center - s_mid) / s_size, poly.index)
            tree.balance()
            svert = [(sw @ v.co - s_mid) / s_size for v in sd.vertices]
            tvert = [(tw @ v.co - t_mid) / t_size for v in td.vertices]
            suv = sd.uv_layers.active.data
            layer = td.uv_layers.new(name="UVMap")
            worst = 0.0
            for poly in td.polygons:
                _, si, dist = tree.find((tw @ poly.center - t_mid) / t_size)
                worst = max(worst, dist)
                sp = sd.polygons[si]
                for li in poly.loop_indices:
                    vp = tvert[td.loops[li].vertex_index]
                    best = min(sp.loop_indices, key=lambda sl: (svert[sd.loops[sl].vertex_index] - vp).length_squared)
                    layer.data[li].uv = suv[best].uv
            out["uvs"] = {"faces": len(td.polygons), "source_faces": len(sd.polygons), "max_face_offset": round(worst, 5)}
        else:
            out["uvs"] = {"skipped": "target already has UVs" if td.uv_layers else "reference has no UVs"}
        # Material: the rigger writes none, so the model would be untextured in the viewer and in GLB exports.
        if sd.materials and not [m for m in td.materials if m is not None]:
            td.materials.clear()
            for m in sd.materials:
                td.materials.append(m)
            out["materials"] = [m.name for m in sd.materials if m is not None]
        # Size and place: the rigger normalises to a 2 m box; put the model back at the size cleanup gave it,
        # standing on the same spot.
        factor = s_size / t_size
        out["scale_factor"] = round(factor, 5)
        out["size_before_m"] = [round(v, 4) for v in (t_hi - t_lo)]
        roots = [o for o in bpy.context.scene.objects if o.parent is None and o not in new]
        if abs(factor - 1.0) > 1e-4:
            for o in roots:
                o.scale = [c * factor for c in o.scale]
                o.location = [c * factor for c in o.location]
            bpy.context.view_layer.update()
        t_lo, t_hi = _world_box([target])
        shift = Vector(((s_lo.x + s_hi.x) / 2 - (t_lo.x + t_hi.x) / 2, (s_lo.y + s_hi.y) / 2 - (t_lo.y + t_hi.y) / 2, s_lo.z - t_lo.z))
        if shift.length > 1e-5:
            for o in roots:
                o.location = Vector(o.location) + shift
            bpy.context.view_layer.update()
        mine = [o for o in bpy.context.scene.objects if o not in new and (o.type == "ARMATURE" or o.type == "MESH")]
        bpy.ops.object.select_all(action="DESELECT")
        for o in mine:
            o.select_set(True)
        bpy.context.view_layer.objects.active = mine[0]
        bpy.ops.object.transform_apply(location=True, rotation=False, scale=True)
        t_lo, t_hi = _world_box([target])
        out["size_after_m"] = [round(v, 4) for v in (t_hi - t_lo)]
        out["reference_size_m"] = [round(v, 4) for v in (s_hi - s_lo)]
        out["ok"] = True
        return out
    finally:
        for o in new:
            bpy.data.objects.remove(o, do_unlink=True)


ref_report = None
skinned = [o for o in bpy.context.scene.objects if o.type == "MESH" and o.vertex_groups]
if reference and os.path.isfile(reference) and skinned:
    try:
        ref_report = restore_from_reference(skinned, reference)
    except Exception as e:  # keep the repaired rig even when the restore fails
        ref_report = {"ok": False, "reason": str(e)[:300]}
if report_path:
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump({"fixed_unweighted": fixed, "verts": total, "normalised": unnormalised, "removed": removed, "reference": ref_report}, fh, indent=1)

# Export only the armature(s) and skinned meshes, so nothing stray can come along.
bpy.ops.object.select_all(action="DESELECT")
keep = [o for o in bpy.context.scene.objects if o.type == "ARMATURE" or (o.type == "MESH" and o.vertex_groups)]
for o in keep:
    o.select_set(True)
bpy.context.view_layer.objects.active = keep[0]
bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", use_selection=True, export_apply=False, export_animations=True)
print("RIGFIX_OK", fixed, total, "normalised", unnormalised, "removed", removed, "reference", ref_report)
