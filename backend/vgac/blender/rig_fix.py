"""Blender headless: repair skin weights on a rigged GLB.

Run: blender -b --python rig_fix.py -- <in.glb> <out.glb>
Vertices with no bone influence copy the groups of the nearest weighted vertex (KD-tree); every vertex is then
limited to 4 influences and normalised. Prints RIGFIX_OK <fixed_unweighted> <verts>.
"""
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402  (shared helpers next to this script)
from mathutils import kdtree

argv = _common.after_double_dash()
src, dst = os.path.abspath(argv[0]), os.path.abspath(argv[1])
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
# Export only the armature(s) and skinned meshes, so nothing stray can come along.
bpy.ops.object.select_all(action="DESELECT")
keep = [o for o in bpy.context.scene.objects if o.type == "ARMATURE" or (o.type == "MESH" and o.vertex_groups)]
for o in keep:
    o.select_set(True)
bpy.context.view_layer.objects.active = keep[0]
bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", use_selection=True, export_apply=False, export_animations=True)
print("RIGFIX_OK", fixed, total, "normalised", unnormalised, "removed", removed)
