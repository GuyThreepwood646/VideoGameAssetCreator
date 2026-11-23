"""Blender script (not a test module): a small skinned creature rig with model-named bones (no Mixamo names), as the
skintoken "Keep model names" template leaves a beetle, dog or snake. One box per bone, weighted to that bone.

blender -b -t 2 --factory-startup --python blender_creature_rig.py -- <out.glb> <quad|hexapod|serpent>
"""
import sys

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
out, kind = argv[0], argv[1]
bpy.ops.wm.read_factory_settings(use_empty=True)

# name: (head, tail, parent). The body runs along -y (forward), z up, feet on the ground.
B = {}
if kind == "serpent":
    prev = None
    for i in range(8):
        y0, y1 = 0.9 - 0.25 * i, 0.9 - 0.25 * (i + 1)
        B[f"body_{i}"] = ((0, y0, 0.06), (0, y1, 0.06), prev)
        prev = f"body_{i}"
else:
    B["body"] = ((0, 0.45, 0.45), (0, -0.1, 0.45), None)
    B["chest"] = ((0, -0.1, 0.45), (0, -0.4, 0.5), "body")
    B["neck"] = ((0, -0.4, 0.5), (0, -0.55, 0.62), "chest")
    B["skull"] = ((0, -0.55, 0.62), (0, -0.75, 0.62), "neck")
    B["tail_0"] = ((0, 0.45, 0.45), (0, 0.75, 0.42), "body")
    rows = [(-0.35, "chest"), (0.25, "body")] if kind == "quad" else [(-0.4, "chest"), (0.0, "body"), (0.4, "body")]
    for r, (y, par) in enumerate(rows):
        for side, x in (("l", 0.15), ("r", -0.15)):
            n = f"leg_{r}{side}"
            B[n + "_a"] = ((x, y, 0.42), (x, y - 0.03, 0.22), par)
            B[n + "_b"] = ((x, y - 0.03, 0.22), (x, y, 0.02), n + "_a")
            # The foot starts on the ground: the glTF importer guesses a leaf's tail, so the leaf's head marks the foot.
            B[n + "_foot"] = ((x, y, 0.0), (x, y - 0.06, 0.03), n + "_b")

ad = bpy.data.armatures.new("Armature")
ob = bpy.data.objects.new("Armature", ad)
bpy.context.scene.collection.objects.link(ob)
bpy.context.view_layer.objects.active = ob
bpy.ops.object.mode_set(mode="EDIT")
eb = {}
for n, (h, t, _p) in B.items():
    b = ad.edit_bones.new(n)
    b.head, b.tail = h, t
    eb[n] = b
for n, (_h, _t, p) in B.items():
    if p:
        eb[n].parent = eb[p]
        eb[n].use_connect = False
bpy.ops.object.mode_set(mode="OBJECT")

# One mesh: a box around each bone, weighted fully to it.
verts, faces, groups = [], [], {}
for n, (h, t, _p) in B.items():
    h, t = Vector(h), Vector(t)
    axis = t - h
    side = axis.cross(Vector((0, 0, 1)))
    if side.length < 1e-6:
        side = Vector((1, 0, 0))
    side.normalize()
    upv = side.cross(axis).normalized()
    w = 0.05 if n.startswith("leg") else 0.09
    base = len(verts)
    for end in (h, t):
        for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            verts.append(end + side * (a * w) + upv * (b * w))
    faces += [tuple(base + i for i in f) for f in ((0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0))]
    groups[n] = list(range(base, base + 8))
me = bpy.data.meshes.new("body")
me.from_pydata([tuple(v) for v in verts], [], faces)
me.update()
mo = bpy.data.objects.new("body", me)
bpy.context.scene.collection.objects.link(mo)
mo.parent = ob
mod = mo.modifiers.new("arm", "ARMATURE")
mod.object = ob
for n, idx in groups.items():
    mo.vertex_groups.new(name=n).add(idx, 1.0, "REPLACE")
bpy.ops.object.select_all(action="SELECT")
bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", use_selection=True)
print("BUILD_OK", kind, len(B))
