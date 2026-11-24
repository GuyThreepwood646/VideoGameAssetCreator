"""Blender-side builder for the body-plan planted rigs (run by test_rig_body_plan.py; not a test module).

Writes one GLB per plant into the folder given after "--". Each rig is built from scratch: a tube per bone, weighted
to that bone with weight 1, under an armature. No library file, env var or picture is read.

Plants (name: what it tests):
  snake_long      one 10-bone chain, one leaf, path about 5 heights: automatic serpent (rule S).
  snake_short     one 4-bone chain, path about 8 heights but fewer than 8 bones: stays unknown (rule S must not fire).
  quad_long_tail  four legs and a long 8-bone tail on the back, its tip off the ground: stays quadruped (guard).
  quad_wing_pair  four legs and a sideways pair of two-bone wings: keeps two wings (rule L must not fire).
  quad_one_chain  four legs and one sideways two-bone antler-like chain on the head: the lone candidate is dropped (rule L).
"""
import math
import os
import sys

import bpy
import bmesh
from mathutils import Vector

out_dir = os.path.abspath(sys.argv[sys.argv.index("--") + 1])
RADIUS = 0.06
SIDES = 6


def new_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def add_armature(name, bones):
    """bones: list of (name, head, tail, parent_name or None). Returns the armature object."""
    data = bpy.data.armatures.new(name + "_arm")
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    eb = {}
    for bname, h, t, parent in bones:
        e = data.edit_bones.new(bname)
        e.head = Vector(h)
        e.tail = Vector(t)
        if parent:
            e.parent = eb[parent]
            e.use_connect = False
        eb[bname] = e
    bpy.ops.object.mode_set(mode="OBJECT")
    return obj


def add_skin(name, arm_obj, bones):
    """One open tube per bone (SIDES-gon rings at head and tail), weight 1 to that bone, skinned to the armature."""
    bm = bmesh.new()
    group_of = []
    for bname, h, t, _ in bones:
        h, t = Vector(h), Vector(t)
        d = (t - h).normalized()
        ref = Vector((0, 0, 1)) if abs(d.z) < 0.9 else Vector((1, 0, 0))
        u = d.cross(ref).normalized()
        v = d.cross(u).normalized()
        rings = []
        for end in (h, t):
            ring = []
            for k in range(SIDES):
                a = 2 * math.pi * k / SIDES
                ring.append(bm.verts.new(end + RADIUS * (math.cos(a) * u + math.sin(a) * v)))
            rings.append(ring)
        for k in range(SIDES):
            k2 = (k + 1) % SIDES
            bm.faces.new((rings[0][k], rings[0][k2], rings[1][k2], rings[1][k]))
        for vert in rings[0] + rings[1]:
            group_of.append((vert, bname))
    bm.verts.index_update()
    assignments = [(v.index, b) for v, b in group_of]
    mesh = bpy.data.meshes.new(name + "_mesh")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    for bname, _, _, _ in bones:
        obj.vertex_groups.new(name=bname)
    for vert_index, bname in assignments:
        obj.vertex_groups[bname].add([vert_index], 1.0, "REPLACE")
    mod = obj.modifiers.new("arm", "ARMATURE")
    mod.object = arm_obj
    obj.parent = arm_obj
    return obj


def export(path, arm_obj, mesh_obj):
    bpy.ops.object.select_all(action="DESELECT")
    arm_obj.select_set(True)
    mesh_obj.select_set(True)
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True, export_skins=True,
                              export_animations=False)


def build(name, bones):
    new_scene()
    arm = add_armature(name, bones)
    mesh = add_skin(name, arm, bones)
    export(os.path.join(out_dir, name + ".glb"), arm, mesh)
    print("PLANT_OK", name, len(bones))


def legs_and_body(prefix=""):
    """Four legs (two front from the chest, two back from the hips) and a body with a neck and head."""
    b = [
        ("Hips", (0, 0.0, 1.0), (0, 0.4, 1.02), None),
        ("Chest", (0, 0.4, 1.02), (0, 0.9, 1.1), "Hips"),
        ("Neck", (0, 0.9, 1.1), (0, 1.15, 1.45), "Chest"),
        ("Head", (0, 1.15, 1.45), (0, 1.35, 1.5), "Neck"),
    ]
    for side, x in (("L", 0.18), ("R", -0.18)):
        for parent, y in (("Hips", 0.05), ("Chest", 0.8)):
            up, lo = "Up%s%s" % (parent, side), "Low%s%s" % (parent, side)
            b.append((up, (x, y, 0.98), (x, y, 0.55), parent))
            b.append((lo, (x, y, 0.55), (x, y, 0.04), up))
    return b


def snake_long():
    b = []
    for i in range(10):
        h = (0.1 * i, 0.0, 0.1)
        t = (0.1 * (i + 1), 0.0, 0.1)
        b.append(("Seg%d" % i, h, t, "Seg%d" % (i - 1) if i else None))
    build("snake_long", b)


def snake_short():
    b = []
    for i in range(4):
        h = (0.4 * i, 0.0, 0.1)
        t = (0.4 * (i + 1), 0.0, 0.1)
        b.append(("Seg%d" % i, h, t, "Seg%d" % (i - 1) if i else None))
    build("snake_short", b)


def quad_long_tail():
    b = legs_and_body()
    prev = "Hips"
    for i in range(8):
        h = (0.0, -0.1 * i, 1.0) if i == 0 else (0.0, -0.1 * i, 1.0)
        t = (0.0, -0.1 * (i + 1), 1.0)
        name = "Tail%d" % i
        b.append((name, h, t, prev))
        prev = name
    build("quad_long_tail", b)


def quad_wing_pair():
    b = legs_and_body()
    for side, x in (("L", 1), ("R", -1)):
        # Span kept under 2 x 1.0 so the legs of one row still form separate clusters (procanim clusters legs by 0.12 x hmax).
        b.append(("Wing%sA" % side, (0.15 * x, 0.6, 1.1), (0.6 * x, 0.6, 1.5), "Chest"))
        b.append(("Wing%sB" % side, (0.6 * x, 0.6, 1.5), (1.0 * x, 0.6, 1.4), "Wing%sA" % side))
    build("quad_wing_pair", b)


def quad_one_chain():
    b = legs_and_body()
    b.append(("AntlerA", (0.04, 1.25, 1.47), (0.5, 1.25, 1.7), "Head"))
    b.append(("AntlerB", (0.5, 1.25, 1.7), (0.9, 1.25, 1.8), "AntlerA"))
    build("quad_one_chain", b)


for fn in (snake_long, snake_short, quad_long_tail, quad_wing_pair, quad_one_chain):
    new_scene()
    fn()
