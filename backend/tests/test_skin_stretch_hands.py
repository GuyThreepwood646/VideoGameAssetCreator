"""blender/skin_stretch.py: hand and finger edges are their own 'hands' region, not part of 'arms'.

The planted case moves a hand's finger weights onto the forearm in a tiny Blender mesh: the finger edges must leave
the hands region and join arms, while hips, legs and torso must not change."""
from __future__ import annotations

import json
import subprocess
import textwrap

import numpy as np
import pytest

import machine_tools
from vgac.blender import skin_stretch as ss

BLENDER = machine_tools.blender()


def test_hands_are_not_arms_in_the_edge_stats():
    # Edge 0 forearm to forearm (arms), edge 1 forearm to hand (the wrist, hands), edge 2 finger to finger (hands).
    rest = np.array([[0.0, 0, 0], [1.0, 0, 0], [2.0, 0, 0], [3.0, 0, 0]])
    edges = np.array([[0, 1], [1, 2], [2, 3]])
    regions = ["arms", "hands", "hands"]
    stretched = rest.copy()
    stretched[2] = [1.6, 0, 0]  # moves the wrist edge (edge 1) past 1.3 times its rest length
    stats = ss.edge_stats(rest, [stretched], edges, regions)
    assert stats["hands"]["edges"] == 2 and stats["hands"]["over_share"] == 0.5
    assert stats["arms"]["edges"] == 1 and stats["arms"]["over_share"] == 0.0
    assert "other" not in stats


def test_region_order_lists_hands_between_legs_and_arms():
    assert ss.REGION_PRIORITY.index("legs") < ss.REGION_PRIORITY.index("hands") < ss.REGION_PRIORITY.index("arms")


_MAKE = textwrap.dedent('''
    import bpy, sys
    out, planted = sys.argv[sys.argv.index("--") + 1], sys.argv[sys.argv.index("--") + 2] == "1"
    bpy.ops.wm.read_factory_settings(use_empty=True)
    ad = bpy.data.armatures.new("A"); arm = bpy.data.objects.new("Armature", ad); bpy.context.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm; bpy.ops.object.mode_set(mode="EDIT")
    for name, z0, z1 in (("mixamorig:LeftHips", 0.0, 0.5), ("mixamorig:LeftUpLeg", 0.5, 0.8),
                         ("mixamorig:LeftForeArm", 0.9, 1.2), ("mixamorig:LeftHand", 1.2, 1.3),
                         ("mixamorig:LeftHandIndex1", 1.3, 1.4)):
        b = ad.edit_bones.new(name); b.head = (0, 0, z0); b.tail = (0, 0, z1)
    bpy.ops.object.mode_set(mode="OBJECT")
    # A strip up the arm: v0 and v1 forearm, v2 hand, v3 and v4 finger. The faces give six edges.
    verts = [(0.0, 0, 0.95), (0.05, 0, 0.95), (0.0, 0, 1.25), (0.0, 0, 1.35), (0.05, 0, 1.35)]
    me = bpy.data.meshes.new("M"); me.from_pydata(verts, [], [(0, 1, 2), (2, 3, 4)]); me.update()
    o = bpy.data.objects.new("Body", me); bpy.context.collection.objects.link(o); o.parent = arm
    weights = {"mixamorig:LeftForeArm": [0, 1], "mixamorig:LeftHand": [2],
               "mixamorig:LeftHandIndex1": [3, 4]}
    if planted:  # the planted defect: the finger weights go to the forearm
        weights["mixamorig:LeftForeArm"] = [0, 1, 3, 4]
        del weights["mixamorig:LeftHandIndex1"]
    for name, idx in weights.items():
        g = o.vertex_groups.new(name=name); g.add(idx, 1.0, "REPLACE")
    mod = o.modifiers.new("Armature", "ARMATURE"); mod.object = arm
    arm.animation_data_create(); act = bpy.data.actions.new("walk")
    arm.animation_data.action = act
    pb = arm.pose.bones["mixamorig:LeftHandIndex1"]; pb.rotation_mode = "XYZ"
    for f, ang in ((1, 0.0), (12, 1.0), (24, 0.0)):
        pb.rotation_euler = (ang, 0, 0); pb.keyframe_insert("rotation_euler", frame=f)
    tr = arm.animation_data.nla_tracks.new(); tr.name = "walk"; tr.strips.new("walk", 1, act)
    arm.animation_data.action = None
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", use_selection=True, export_animations=True, export_nla_strips=True)
''')


def _measure(tmp_path, planted: bool) -> dict:
    make = tmp_path / f"make_{int(planted)}.py"
    make.write_text(_MAKE, encoding="utf-8")
    glb, out = tmp_path / f"arm_{int(planted)}.glb", tmp_path / f"s_{int(planted)}.json"
    run = lambda *a: subprocess.run([str(BLENDER), "-b", "--threads", "2", *a], capture_output=True, text=True, timeout=300)  # noqa: E731
    r = run("--python", str(make), "--", str(glb), "1" if planted else "0")
    assert glb.is_file(), r.stdout[-600:] + r.stderr[-600:]
    r = run("--python", ss.__file__, "--", str(glb), str(out), "--clips", "walk", "--samples", "4")
    assert out.is_file(), r.stdout[-600:]
    return json.loads(out.read_text(encoding="utf-8"))["regions"]


@pytest.mark.skipif(BLENDER is None, reason="Blender is not set up on this machine (VGAC_TEST_BLENDER)")
def test_planted_finger_weights_on_the_forearm_move_only_hands_and_arms(tmp_path):
    clean = _measure(tmp_path, planted=False)
    planted = _measure(tmp_path, planted=True)
    # The faces give six edges. Clean: v0-v1 is arms; v1-v2, v0-v2, v2-v3, v2-v4 and v3-v4 touch a hand or finger.
    assert clean["hands"] == 5 and clean["arms"] == 1
    # Planted: v3 and v4 are forearm-dominant. v0-v1 and v3-v4 are arms; the four edges to v2 stay hands.
    assert planted["hands"] == 4 and planted["arms"] == 2
    for region in ("hips", "legs", "torso"):
        assert clean.get(region, 0) == planted.get(region, 0)
