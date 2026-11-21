"""blender/skin_stretch.py must measure the clip's poses, not one frozen pose (WP-79: in Blender 5.2.3 an early set_tracks(None)
left every frame the same, so every stretch figure read a static pose)."""
from __future__ import annotations

import json
import subprocess
import textwrap

import numpy as np
import pytest

import machine_tools
from vgac.blender import skin_stretch as ss

BLENDER = machine_tools.blender()


def test_pose_spread_is_zero_for_identical_frames_and_the_largest_move_otherwise():
    base = np.zeros((3, 3))
    assert ss.pose_spread([base, base.copy(), base.copy()]) == 0.0
    moved = base.copy()
    moved[1] = [0.0, 0.3, 0.4]
    assert ss.pose_spread([base, base, moved]) == pytest.approx(0.5)
    assert ss.pose_spread([base]) == 0.0 and ss.pose_spread([]) == 0.0


_MAKE = textwrap.dedent('''
    import bpy, sys, math
    out = sys.argv[sys.argv.index("--") + 1]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    ad = bpy.data.armatures.new("A"); arm = bpy.data.objects.new("Armature", ad); bpy.context.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm; bpy.ops.object.mode_set(mode="EDIT")
    b = ad.edit_bones.new("mixamorig:Hips"); b.head = (0, 0, 0); b.tail = (0, 0, 0.5)
    bpy.ops.object.mode_set(mode="OBJECT")
    verts = [(0.0, 0, 0.1), (0.05, 0, 0.1), (0.0, 0, 0.4), (0.05, 0, 0.4)]
    me = bpy.data.meshes.new("M"); me.from_pydata(verts, [], [(0, 1, 2), (1, 3, 2)]); me.update()
    o = bpy.data.objects.new("Body", me); bpy.context.collection.objects.link(o); o.parent = arm
    g = o.vertex_groups.new(name="mixamorig:Hips"); g.add([0, 1, 2, 3], 1.0, "REPLACE")
    mod = o.modifiers.new("Armature", "ARMATURE"); mod.object = arm
    arm.animation_data_create(); act = bpy.data.actions.new("walk")
    arm.animation_data.action = act
    pb = arm.pose.bones["mixamorig:Hips"]; pb.rotation_mode = "XYZ"
    for f, ang in ((1, 0.0), (12, 1.0), (24, 0.0)):
        pb.rotation_euler = (0, ang, 0); pb.keyframe_insert("rotation_euler", frame=f)
    tr = arm.animation_data.nla_tracks.new(); tr.name = "walk"; tr.strips.new("walk", 1, act)
    arm.animation_data.action = None
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", use_selection=True, export_animations=True, export_nla_strips=True)
''')


@pytest.mark.skipif(BLENDER is None, reason="Blender is not set up on this machine")
def test_the_measure_sees_the_clip_move(tmp_path):
    glb, out = tmp_path / "a.glb", tmp_path / "s.json"
    (tmp_path / "make.py").write_text(_MAKE, encoding="utf-8")
    run = lambda *a: subprocess.run([str(BLENDER), "-b", "--threads", "2", *a], capture_output=True, text=True, timeout=300)  # noqa: E731
    r = run("--python", str(tmp_path / "make.py"), "--", str(glb))
    assert glb.is_file(), r.stdout[-600:] + r.stderr[-600:]
    r = run("--python", ss.__file__, "--", str(glb), str(out), "--clips", "walk", "--samples", "8")
    assert out.is_file(), r.stdout[-600:]
    clip = json.loads(out.read_text(encoding="utf-8"))["clips"]["walk"]
    assert not clip.get("missing"), clip
    assert clip["pose_spread_m"] > 0.01, "every sampled frame was the same pose"
