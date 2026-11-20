"""Blender helper for test_separate.py (not a test module): a box torso with a left arm grown into its side.

Run: blender -b --python blender_fused_arm.py -- <out_dir> [--wall | --forearm] [--palm] [--stray]
Writes <out_dir>/step4.glb (the model alone, textured) and <out_dir>/rig.glb (the same model on a Mixamo-named
skeleton, its left arm weighted to the arm bones from 2 cm inside the arm, the rest to the spine). With --wall the
weight boundary lies on the torso's flat side wall instead, which puts the cut plane in that wall: the script must
refuse that cut. With --forearm the arm's weights begin at the elbow (the upper arm is weighted to the spine), as on
the low-poly dwarf: the weight line is not an upper-arm join and the script must leave the model unchanged. The torso and arm boxes overlap by 1 cm and are made one closed surface by a voxel rebuild, as the 3D
builder would fuse them. With --palm the rig's hand points sit 5 cm forward of the step 4 model's (a pass after the
rig, like palm-down, baked a new rest pose): the rig's points must be found by face corners, and the rigged copy of the
result must sit in the rig's rest pose. With --stray the back of the torso below the arm carries a third arm weight (hair
or a robe the 3D build skinned to the arm): it is not the arm's own surface, so the cut must take that weight off.
"""
import os
import sys

import bmesh
import bpy
from mathutils import Vector

args = sys.argv[sys.argv.index("--") + 1:]
out = os.path.abspath(args[0])
edge_x = 0.15 if "--wall" in args else 0.17  # --wall: the weight boundary on the torso's flat side wall
os.makedirs(out, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)


def box(lo, hi):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co = Vector([lo[i] + (v.co[i] + 0.5) * (hi[i] - lo[i]) for i in range(3)])
    return bm


bm = box((-0.15, -0.1, 0.9), (0.15, 0.1, 1.7))
arm = box((0.14, -0.05, 1.0), (0.24, 0.05, 1.68))
me_arm = bpy.data.meshes.new("a")
arm.to_mesh(me_arm)
bm.from_mesh(me_arm)
me = bpy.data.meshes.new("body")
bm.to_mesh(me)
ob = bpy.data.objects.new("body", me)
bpy.context.scene.collection.objects.link(ob)
bpy.context.view_layer.objects.active = ob
ob.select_set(True)
mod = ob.modifiers.new("r", "REMESH")
mod.mode = "VOXEL"
mod.voxel_size = 0.012
bpy.ops.object.modifier_apply(modifier="r")
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.mesh.quads_convert_to_tris()
bpy.ops.uv.smart_project(island_margin=0.02)
bpy.ops.object.mode_set(mode="OBJECT")

img = bpy.data.images.new("base_color", 256, 256)
img.generated_color = (0.6, 0.4, 0.3, 1.0)
img.pack()
mat = bpy.data.materials.new("skin")
mat.use_nodes = True
tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
tex.image = img
mat.node_tree.links.new(tex.outputs["Color"], mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
me.materials.append(mat)

bpy.ops.export_scene.gltf(filepath=os.path.join(out, "step4.glb"), export_format="GLB", use_selection=True)

arm_data = bpy.data.armatures.new("rig")
rig = bpy.data.objects.new("rig", arm_data)
bpy.context.scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode="EDIT")
bones = {}
for name, h, t, parent in (
        ("Hips", (0, 0, 0.9), (0, 0, 1.1), None), ("Spine", (0, 0, 1.1), (0, 0, 1.6), "Hips"),
        ("LeftShoulder", (0, 0, 1.6), (0.19, 0, 1.66), "Spine"), ("LeftArm", (0.19, 0, 1.66), (0.19, 0, 1.33), "LeftShoulder"),
        ("LeftForeArm", (0.19, 0, 1.33), (0.19, 0, 1.05), "LeftArm"), ("LeftHand", (0.19, 0, 1.05), (0.19, 0, 0.99), "LeftForeArm"),
        ("RightShoulder", (0, 0, 1.6), (-0.19, 0, 1.66), "Spine"), ("RightArm", (-0.19, 0, 1.66), (-0.19, 0, 1.33), "RightShoulder"),
        ("RightForeArm", (-0.19, 0, 1.33), (-0.19, 0, 1.05), "RightArm"), ("RightHand", (-0.19, 0, 1.05), (-0.19, 0, 0.99), "RightForeArm")):
    b = arm_data.edit_bones.new("mixamorig:" + name)
    b.head, b.tail = Vector(h), Vector(t)
    if parent:
        b.parent = bones[parent]
    bones[name] = b
bpy.ops.object.mode_set(mode="OBJECT")
groups = {n: ob.vertex_groups.new(name="mixamorig:" + n) for n in ("Spine", "LeftArm", "LeftForeArm")}
for v in me.vertices:
    x, z = v.co.x, v.co.z
    if "--forearm" in args:
        groups["LeftForeArm" if x > edge_x and z < 1.33 else "Spine"].add([v.index], 1.0, "REPLACE")
    elif x > edge_x and z < 1.66:
        groups["LeftArm" if z > 1.33 else "LeftForeArm"].add([v.index], 1.0, "REPLACE")
    elif "--stray" in args and v.co.y > 0.09 and 0.0 < x < 0.12 and z < 1.3:
        groups["Spine"].add([v.index], 0.65, "REPLACE")
        groups["LeftForeArm"].add([v.index], 0.35, "REPLACE")
    else:
        groups["Spine"].add([v.index], 1.0, "REPLACE")
if "--palm" in args:
    for v in me.vertices:
        if v.co.x > 0.14 and v.co.z < 1.08:
            v.co.y -= 0.05
ob.parent = rig
ob.modifiers.new("Armature", "ARMATURE").object = rig
bpy.ops.object.select_all(action="DESELECT")
ob.select_set(True)
rig.select_set(True)
bpy.context.view_layer.objects.active = rig
bpy.ops.export_scene.gltf(filepath=os.path.join(out, "rig.glb"), export_format="GLB", use_selection=True)
print("FUSED_ARM_OK")
