"""blender/palm_down.py: the hands turned from palm-forward to palm-down with forearm twist bones, and the rig step's call.

The numpy parts run anywhere; the Blender part builds a small palm-forward rigged arm, exports it, runs palm_down.py on it and
measures the result (skipped when Blender is not set up on this machine)."""
from __future__ import annotations

import asyncio
import json
import math
import subprocess
import textwrap

import numpy as np
import pytest

import machine_tools
from vgac.blender import palm_down as pd
from vgac.stages import rig

BLENDER = machine_tools.blender()
needs_blender = pytest.mark.skipif(BLENDER is None, reason="Blender is not set up on this machine")


# --- numpy parts -----------------------------------------------------------------------------------------------


def _hand(side, palm):
    """Wrist, four finger bases, thumb base and hand vertices of a flat hand along +x (left) or -x (right): the thumb toward the
    model's front when the palm is down (palm "down"), or up when the palm faces the front (palm "front")."""
    sx = 1.0 if side == "Left" else -1.0
    thumb_dir = np.array([0, -1.0, 0]) if palm == "down" else np.array([0, 0, 1.0])
    f = np.array([sx, 0, 0])
    normal = np.cross(f, thumb_dir)
    fingers = [f * 0.09 + thumb_dir * (0.03 - 0.02 * i) for i in range(4)]  # index next to the thumb, pinky far from it
    thumb = f * 0.02 + thumb_dir * 0.04
    rng = np.random.default_rng(3)
    pts = np.array([f * rng.uniform(0.0, 0.18) + thumb_dir * rng.uniform(-0.04, 0.04) + normal * rng.uniform(-0.005, 0.005) for _ in range(400)])
    return np.zeros(3), fingers, thumb, pts


@pytest.mark.parametrize("side", ["Left", "Right"])
def test_palm_forward_needs_a_quarter_turn_and_palm_down_none(side):
    sx = 1.0 if side == "Left" else -1.0
    a = np.array([sx, 0, 0])
    w, fingers, thumb, pts = _hand(side, "front")
    n = pd.palm_normal(w, fingers, thumb, side, pts)
    # whichever way round the finger names are, the palm normal is the thin axis of the hand: here the front (-y) or its
    # opposite is excluded by the anatomy of the side
    assert abs(n[1]) > 0.9
    ang = math.degrees(pd.roll_angle(a, n))
    assert abs(abs(ang) - 90) < 5
    w, fingers, thumb, pts = _hand(side, "down")
    n2 = pd.palm_normal(w, fingers, thumb, side, pts)
    assert abs(n2[2]) > 0.9 and abs(math.degrees(pd.roll_angle(a, n2))) < 5


def test_the_two_sides_turn_in_opposite_directions_and_palm_normals_mirror():
    wl, fl, tl, pl = _hand("Left", "front")
    wr, fr, tr, pr = _hand("Right", "front")
    nl = pd.palm_normal(wl, fl, tl, "Left", pl)
    nr = pd.palm_normal(wr, fr, tr, "Right", pr)
    assert nl[1] * nr[1] > 0  # both palms face the same way (the front, or both the back): they are mirror images


def test_finger_names_do_not_matter():
    w, fingers, thumb, pts = _hand("Left", "front")
    a = pd.palm_normal(w, fingers, thumb, "Left", pts)
    b = pd.palm_normal(w, fingers[::-1], thumb, "Left", pts)
    assert np.allclose(a, b)


def test_twist_nodes_share_the_forearm_without_gaps():
    nodes = pd.node_layout(3)
    assert [n for _, n in nodes] == ["forearm", "twist1", "twist2", "twist3", "hand"]
    assert [round(t, 3) for t, _ in nodes] == [0.0, 0.167, 0.5, 0.833, 1.0]
    for t in np.linspace(-0.2, 1.3, 40):
        w = pd.node_weights(t, nodes)
        assert abs(sum(w.values()) - 1) < 1e-9 and len(w) <= 2 and all(v >= 0 for v in w.values())
    assert pd.node_weights(0.0, nodes) == {"forearm": 1.0, "twist1": 0.0}
    assert pd.node_weights(1.0, nodes)["hand"] == 1.0
    # position equals the share of the roll the node takes: the blend turns a vertex by about its own position
    w = pd.node_weights(0.3, nodes)
    turn = sum(v * dict((n, t) for t, n in nodes)[k] for k, v in w.items())
    assert abs(turn - 0.3) < 1e-9


# --- the rig step's call -----------------------------------------------------------------------------------------


def test_palm_down_runs_after_the_finger_build_and_before_everything_that_reads_the_skeleton():
    import inspect

    src = inspect.getsource(rig._rig)
    at = src.index("_palm_down(sc, rig_glb, params)")
    assert src.index("_fingers(sc, rig_glb, params)") < at
    for later in ("sc.register(\"glb\"", "_inspect(sc", "fit_hitboxes(sc", "_animate(sc"):
        assert at < src.index(later), later


class _FakeSC:
    def __init__(self, tmp_path, report):
        self.dir, self.report = tmp_path, report
        self.metrics, self.params, self.registered = {}, {}, {}
        self.settings = type("S", (), {"blender_timeout_seconds": 5})()

    async def blender(self, script, args, log_name=None, timeout=None):
        assert script == "palm_down.py"
        out, rep = args[1], args[args.index("--report") + 1]
        out.write_bytes(b"NEW")
        rep.write_text(json.dumps(self.report), encoding="utf-8")

    def set_metrics(self, **kw):
        self.metrics.update(kw)

    def set_params(self, **kw):
        self.params.update(kw)

    def register(self, key, path):
        self.registered[key] = path


def _run_step(tmp_path, report, params=None):
    glb = tmp_path / "rig.glb"
    glb.write_bytes(b"OLD")
    sc = _FakeSC(tmp_path, report)
    asyncio.run(rig._palm_down(sc, glb, params or {}))
    return sc, glb


def test_a_turned_hand_replaces_the_rig_and_records_the_degrees(tmp_path):
    sc, glb = _run_step(tmp_path, {"Left": {"path": "turned", "roll_deg": 80.3, "twist_bones": 3}, "Right": {"path": "skipped", "reason": "no thumb"}})
    assert glb.read_bytes() == b"NEW" and (tmp_path / "rig_before_palm_down.glb").read_bytes() == b"OLD"
    pdm = sc.metrics["palm_down"]
    assert pdm["Left"] == {"path": "turned", "roll_deg": 80.3, "twist_bones": 3} and pdm["Right"]["path"] == "skipped" and "palm_down" in sc.registered


def test_nothing_turned_leaves_the_rig_alone(tmp_path):
    sc, glb = _run_step(tmp_path, {"Left": {"path": "skipped", "reason": "the palm is already down", "roll_deg": 0.0}, "Right": {"path": "skipped"}})
    assert glb.read_bytes() == b"OLD" and sc.metrics["palm_down"]["Left"]["roll_deg"] == 0.0


def test_the_named_setting_turns_it_off(tmp_path):
    sc, glb = _run_step(tmp_path, {"Left": {"path": "turned"}}, {"palm_down": False})
    assert glb.read_bytes() == b"OLD" and "palm_down" not in sc.metrics and sc.params["palm_down"] is False


# --- Blender: a palm-forward arm ---------------------------------------------------------------------------------

SCENE = textwrap.dedent('''
    import bpy, bmesh, math, sys
    from mathutils import Vector, Matrix
    bpy.ops.wm.read_factory_settings(use_empty=True)
    arm_d = bpy.data.armatures.new("A"); arm = bpy.data.objects.new("A", arm_d)
    bpy.context.scene.collection.objects.link(arm); bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm_d.edit_bones
    def bone(name, head, tail, parent=None):
        b = eb.new("mixamorig:" + name); b.head = head; b.tail = tail
        if parent: b.parent = eb["mixamorig:" + parent]
    bone("Hips", (0, 0, -0.9), (0, 0, -0.6)); bone("LeftArm", (-0.6, 0, 0), (-0.3, 0, 0), "Hips")
    bone("LeftForeArm", (-0.3, 0, 0), (0, 0, 0), "LeftArm"); bone("LeftHand", (0, 0, 0), (0.09, 0, 0), "LeftForeArm")
    for i in range(3):
        bone("LeftHandThumb%d" % (i + 1), (0.02 + 0.015 * i, 0, 0.04 + 0.01 * i), (0.035 + 0.015 * i, 0, 0.05 + 0.01 * i), "LeftHand" if i == 0 else "LeftHandThumb%d" % i)
    for f, z, ln in (("Index", 0.03, 0.17), ("Middle", 0.01, 0.19), ("Ring", -0.01, 0.18), ("Pinky", -0.03, 0.15)):
        for i in range(3):
            x0 = 0.09 + (ln - 0.09) * i / 3
            bone("LeftHand%s%d" % (f, i + 1), (x0, 0, z), (x0 + (ln - 0.09) / 3, 0, z), "LeftHand" if i == 0 else "LeftHand%s%d" % (f, i))
    bpy.ops.object.mode_set(mode="OBJECT")
    bm = bmesh.new()
    def box(x0, x1, z0, z1, y=0.01):
        r = bmesh.ops.create_cube(bm, size=1.0)
        vs = r["verts"]
        for v in vs:
            v.co = Vector(((x1 + x0) / 2 + v.co.x * (x1 - x0), v.co.y * 2 * y, (z1 + z0) / 2 + v.co.z * (z1 - z0)))
        bmesh.ops.subdivide_edges(bm, edges=list({{e for v in vs for e in v.link_edges}}), cuts=3, use_grid_fill=True)
    box(0, 0.09, -0.04, 0.04)
    for c, ln in zip((0.03, 0.01, -0.01, -0.03), (0.17, 0.19, 0.18, 0.15)): box(0.09, ln, c - 0.007, c + 0.007)
    box(0.01, 0.06, 0.04, 0.06)
    hand_n = len(bm.verts)
    r = bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=0.04, radius2=0.04, depth=0.3)
    for v in r["verts"]:
        v.co = Vector((-0.15 + v.co.z, v.co.x, v.co.y))   # the cone's axis (z) lies along x from -0.3 to 0
    bmesh.ops.subdivide_edges(bm, edges=list({{e for v in r["verts"] for e in v.link_edges}}), cuts=5)
    me = bpy.data.meshes.new("M"); bm.to_mesh(me); bm.free()
    mesh = bpy.data.objects.new("M", me); bpy.context.scene.collection.objects.link(mesh)
    for b in arm_d.bones: mesh.vertex_groups.new(name=b.name)
    for v in me.vertices:
        if v.co.x < -0.005: grp = "mixamorig:LeftForeArm"
        elif v.co.z > 0.045 and 0.0 < v.co.x < 0.07: grp = "mixamorig:LeftHandThumb1"
        else: grp = "mixamorig:LeftHand"
        mesh.vertex_groups[grp].add([v.index], 1.0, "REPLACE")
    mod = mesh.modifiers.new("Armature", "ARMATURE"); mod.object = arm
    mesh.parent = arm
    bpy.ops.object.select_all(action="DESELECT"); arm.select_set(True); mesh.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.export_scene.gltf(filepath={out!r}, export_format="GLB", use_selection=True, export_animations=False)
''')

MEASURE = textwrap.dedent('''
    import bpy, json, sys, os
    import numpy as np
    sys.path.insert(0, {blender_dir!r})
    import _common
    _common.open_glb({glb!r})
    arm = next(o for o in bpy.context.scene.objects if o.type == "ARMATURE")
    mesh = max((o for o in bpy.context.scene.objects if o.type == "MESH"), key=lambda o: len(o.data.vertices))
    W = mesh.matrix_world
    V = np.array([W @ v.co for v in mesh.data.vertices])
    P = "mixamorig:"
    hand_h = np.array(arm.matrix_world @ arm.data.bones[P + "LeftHand"].head_local)
    fa_h = np.array(arm.matrix_world @ arm.data.bones[P + "LeftForeArm"].head_local)
    t = (V - fa_h)[:, 0] / (hand_h - fa_h)[0]
    r = np.linalg.norm((V - fa_h)[:, 1:], axis=1)
    mid = (t > 0.35) & (t < 0.65) & (r < 0.08)
    palm = V[(V[:, 0] > hand_h[0] + 0.02) & (V[:, 0] < hand_h[0] + 0.08)]
    wts = []
    for v in mesh.data.vertices:
        ws = [g.weight for g in v.groups if g.weight > 0]
        wts.append((len(ws), sum(ws)))
    out = {{"mid_radius": float(np.percentile(r[mid], 90)), "palm_z": float(palm[:, 2].max() - palm[:, 2].min()),
           "palm_y": float(palm[:, 1].max() - palm[:, 1].min()),
           "max_infl": max(w[0] for w in wts), "sum_min": min(w[1] for w in wts), "sum_max": max(w[1] for w in wts),
           "twist": {{b.name: b.parent.name for b in arm.data.bones if "Extra_ForeArmTwist" in b.name}},
           "bones": len(arm.data.bones), "hand_parent": arm.data.bones[P + "LeftHand"].parent.name,
           "forearm_children": len(arm.data.bones[P + "LeftForeArm"].children)}}
    open({out!r}, "w").write(json.dumps(out))
''')


def _blender(tmp_path, script_text, name, *args):
    script = tmp_path / name
    script.write_text(script_text, encoding="utf-8")
    subprocess.run([str(BLENDER), "-b", "--threads", "2", "--python", str(script), "--", *map(str, args)], check=True, capture_output=True, timeout=300)


def _measure(tmp_path, glb, tag):
    out = tmp_path / f"m_{tag}.json"
    _blender(tmp_path, MEASURE.format(blender_dir=str(pd.__file__).rsplit("palm_down.py", 1)[0], glb=str(glb), out=str(out)), f"measure_{tag}.py")
    return json.loads(out.read_text())


@needs_blender
def test_a_palm_forward_arm_is_turned_palm_down_with_twist_bones_and_keeps_its_thickness(tmp_path):
    src = tmp_path / "arm.glb"
    _blender(tmp_path, SCENE.format(out=str(src)), "scene.py")
    before = _measure(tmp_path, src, "before")
    assert before["palm_y"] < 0.03 and before["palm_z"] > 0.06  # palm forward: thin in y
    out, rep = tmp_path / "turned.glb", tmp_path / "turned.json"
    pd_script = str(pd.__file__)
    subprocess.run([str(BLENDER), "-b", "--threads", "2", "--python", pd_script, "--", str(src), str(out), "--report", str(rep)],
                   check=True, capture_output=True, timeout=300)
    r = json.loads(rep.read_text())
    assert r["Left"]["path"] == "turned" and r["Left"]["twist_bones"] == 3 and 75 < abs(r["Left"]["roll_deg"]) < 105
    assert r["Right"]["path"] == "skipped"  # this scene has no right arm
    after = _measure(tmp_path, out, "after")
    assert after["palm_z"] < 0.03 and after["palm_y"] > 0.06  # palm down: thin in z
    # a chain between ForeArm and Hand: ForeArm keeps one child, so the hand is still the first bone where the fingers branch
    assert after["twist"] == {"mixamorig:Extra_ForeArmTwist_L1": "mixamorig:LeftForeArm", "mixamorig:Extra_ForeArmTwist_L2": "mixamorig:Extra_ForeArmTwist_L1",
                              "mixamorig:Extra_ForeArmTwist_L3": "mixamorig:Extra_ForeArmTwist_L2"}
    assert after["hand_parent"] == "mixamorig:Extra_ForeArmTwist_L3" and after["forearm_children"] == 1
    assert after["bones"] == before["bones"] + 3
    assert after["mid_radius"] > 0.9 * before["mid_radius"]  # the twist bones keep the forearm's thickness
    assert after["max_infl"] <= 4 and abs(after["sum_min"] - 1) < 1e-3 and abs(after["sum_max"] - 1) < 1e-3
    # run again: the palm is already down, nothing is turned
    out2, rep2 = tmp_path / "again.glb", tmp_path / "again.json"
    subprocess.run([str(BLENDER), "-b", "--threads", "2", "--python", pd_script, "--", str(out), str(out2), "--report", str(rep2)],
                   check=True, capture_output=True, timeout=300)
    assert json.loads(rep2.read_text())["Left"]["path"] == "skipped" and not out2.exists()
    # the rig inspection's bend test does not treat the twist bones as forearms
    info = tmp_path / "info"
    rig_info = str(pd.__file__).replace("palm_down.py", "rig_info.py")
    subprocess.run([str(BLENDER), "-b", "--threads", "2", "--python", rig_info, "--", str(out), str(info), "35"], check=True, capture_output=True, timeout=300)
    bent = json.loads((info / "rig_info.json").read_text())["bent"]
    assert "mixamorig:LeftForeArm" in bent and not [b for b in bent if "Extra_" in b]
