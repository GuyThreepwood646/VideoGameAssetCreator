"""blender/finger_build.py: the finger chains built from the hand's geometry, and the rig check that reports them.

The numpy parts run anywhere; the Blender parts build a small synthetic rigged hand and run build_side on it
(skipped when Blender is not set up on this machine)."""
from __future__ import annotations

import json
import re
import subprocess
import textwrap

import numpy as np
import pytest

import machine_tools
from vgac.blender import finger_build as fb
from vgac.blender import gait
from vgac.stages import rig

BLENDER = machine_tools.blender()
needs_blender = pytest.mark.skipif(BLENDER is None, reason="Blender is not set up on this machine")
P = "mixamorig:"


# --- numpy parts -----------------------------------------------------------------------------------------------


def _hand_cloud(gaps: bool, rng=np.random.default_rng(1)):
    """A flat hand along +x from the wrist at the origin, thumb on +z, palm facing -y (the model's front)."""
    pts = []
    def box(x0, x1, z0, z1, n=500):
        pts.append(np.stack([rng.uniform(x0, x1, n), rng.uniform(-0.01, 0.01, n), rng.uniform(z0, z1, n)], 1))
    box(0, 0.09, -0.04, 0.04, 1500)
    w = 0.014 if gaps else 0.02
    for c, ln in zip((-0.03, -0.01, 0.01, 0.03), (0.17, 0.19, 0.18, 0.15)):
        box(0.09, ln, c - w / 2, c + w / 2)
    box(0.01, 0.06, 0.04, 0.06)
    return np.vstack(pts)


def _frame(pts):
    return fb.hand_frame(np.zeros(3), pts, thumb_head=np.array([0.02, 0.0, 0.04]))


def test_frame_orients_from_the_thumb_and_faces_the_palm_front():
    fr = _frame(_hand_cloud(True))
    assert fr["f"][0] > 0.95  # toward the fingertips
    assert fr["u"][2] < -0.9  # from the thumb (+z) across to the little finger
    assert fr["n"][1] < -0.9  # out of the palm, toward the front (-y)
    assert fb.hand_frame(np.zeros(3), _hand_cloud(True), None) is None


def test_separate_fingers_are_found_by_their_tips():
    pts = _hand_cloud(True)
    c = fb.coords(_frame(pts), pts)
    reach = c[:, 0].max()
    plan = fb.plan_chains(c, fb.outline_peaks(c, reach), reach)
    assert plan["path"] == "tips" and len(plan["centres"]) == 4
    assert plan["centres"] == sorted(plan["centres"])


def test_bunched_fingers_fall_back_to_the_knuckle_line():
    pts = _hand_cloud(False)
    pts[:, 0] = np.minimum(pts[:, 0], 0.17)  # the fingertips end level: no tips to find
    c = fb.coords(_frame(pts), pts)
    reach = c[:, 0].max()
    plan = fb.plan_chains(c, fb.outline_peaks(c, reach), reach)
    assert plan["path"] == "proportion" and len(plan["centres"]) == 4
    assert np.all(np.diff(plan["centres"]) > 0)


def test_chain_points_are_inside_the_hand_and_sensibly_long():
    pts = _hand_cloud(True)
    c = fb.coords(_frame(pts), pts)
    reach = c[:, 0].max()
    plan = fb.plan_chains(c, fb.outline_peaks(c, reach), reach)
    for k in range(4):
        jp = fb.chain_points(c, plan["centres"][k], plan["lane"][k], reach)
        assert len(jp) == 4
        a = [p[0] for p in jp]
        assert a == sorted(a) and 0.3 * reach < a[0] < 0.7 * reach and a[-1] <= reach + 1e-9
        lengths = [np.linalg.norm(jp[i + 1] - jp[i]) for i in range(3)]
        assert 0.4 * reach < sum(lengths) < 0.7 * reach  # finger about half the hand's reach
        assert lengths[0] > lengths[2]


def test_shared_weights_sum_to_one():
    pts = np.random.default_rng(2).uniform(-0.05, 0.05, (200, 3))
    names, w = fb.share_weights(pts, {"a": (np.zeros(3), np.array([0.1, 0, 0])), "b": (np.array([0, 0.05, 0]), np.array([0.1, 0.05, 0]))}, 0.02)
    assert names == ["a", "b"] and np.allclose(w.sum(1), 1.0) and (w >= 0).all()


# --- the rig check -----------------------------------------------------------------------------------------------


BODY = [P + n for n in ("Hips", "LeftArm", "LeftForeArm", "LeftHand", "RightArm", "RightForeArm", "RightHand")]


def _names(per_hand):
    out = list(BODY)
    for side in ("Left", "Right"):
        for f, n in per_hand.items():
            out += [f"{P}{side}Hand{f}{k}" for k in range(1, n + 1)]
    return out


ALL = {"Thumb": 3, "Index": 3, "Middle": 3, "Ring": 3, "Pinky": 3}


def test_check_reports_fingers_built_from_the_hand_shape():
    build = {"Left": {"path": "proportion", "built": 9}, "Right": {"path": "tips", "built": 9}}
    c = rig.fingers_check(_names(ALL), None, build)
    assert c["passed"] and c["value"] == 30
    assert "Both hands have all five fingers" in c["help"] and "built from the shape of the hand" in c["help"]
    assert "estimates" in c["help"] and c["vars"]["finger_build"] == {"Left": "proportion", "Right": "tips"}


def test_check_complete_as_predicted_says_nothing_about_building():
    c = rig.fingers_check(_names(ALL), None, {"Left": {"path": "complete", "built": 0}, "Right": {"path": "complete", "built": 0}})
    assert c["passed"] and "built" not in c["help"]


def test_check_lump_keeps_the_model_hands_warning():
    names = _names({})
    c = rig.fingers_check(names, None, {"Left": {"path": "lump"}, "Right": {"path": "lump"}})
    assert not c["passed"] and "The cause is the model's hands, not the skeleton" in c["help"]
    assert c["fix"]["stage"] == "mesh"


def test_check_one_hand_built_one_lump():
    n = [x for x in _names(ALL) if not x.startswith(P + "RightHand" + "Index") and not x.startswith(P + "RightHand" + "Middle")
         and not x.startswith(P + "RightHandRing") and not x.startswith(P + "RightHandPinky")]
    c = rig.fingers_check(n, None, {"Left": {"path": "tips", "built": 9}, "Right": {"path": "lump"}})
    assert not c["passed"] and "right hand has only the thumb" in c["help"] and "model's hands" in c["help"]


# --- Blender parts: a synthetic rigged hand ----------------------------------------------------------------------

SCENE = textwrap.dedent('''
    import bpy, bmesh, json, sys, os
    from mathutils import Vector
    sys.path.insert(0, {blender_dir!r})
    import finger_build as fb
    mode = {mode!r}
    bpy.ops.wm.read_factory_settings(use_empty=True)
    arm_d = bpy.data.armatures.new("A"); arm = bpy.data.objects.new("A", arm_d)
    bpy.context.scene.collection.objects.link(arm); bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm_d.edit_bones
    def bone(name, head, tail, parent=None):
        b = eb.new("mixamorig:" + name); b.head = head; b.tail = tail
        if parent: b.parent = eb["mixamorig:" + parent]
    bone("Hips", (0, 0, -0.9), (0, 0, -0.6)); bone("LeftArm", (-0.5, 0, 0), (-0.3, 0, 0), "Hips")
    bone("LeftForeArm", (-0.3, 0, 0), (0, 0, 0), "LeftArm"); bone("LeftHand", (0, 0, 0), (0.09, 0, 0), "LeftForeArm")
    if mode != "nothumb":
        for i in range(3):
            bone("LeftHandThumb%d" % (i + 1), (0.02 + 0.015 * i, 0, 0.04 + 0.01 * i), (0.035 + 0.015 * i, 0, 0.05 + 0.01 * i), "LeftHand" if i == 0 else "LeftHandThumb%d" % i)
    def chain(f, z, ln):
        for i in range(3):
            x0 = 0.09 + (ln - 0.09) * i / 3
            bone("LeftHand%s%d" % (f, i + 1), (x0, 0, z), (x0 + (ln - 0.09) / 3, 0, z), "LeftHand" if i == 0 else "LeftHand%s%d" % (f, i))
    if mode in ("one", "nothumb"): chain("Index", 0.03, 0.17)
    if mode == "one_mid": chain("Index", 0.01, 0.19)
    if mode == "complete":
        for f, z, ln in (("Index", 0.03, 0.17), ("Middle", 0.01, 0.19), ("Ring", -0.01, 0.18), ("Pinky", -0.03, 0.15)): chain(f, z, ln)
    bpy.ops.object.mode_set(mode="OBJECT")
    bm = bmesh.new()
    def box(x0, x1, z0, z1, y=0.01):
        r = bmesh.ops.create_cube(bm, size=1.0)
        vs = r["verts"]
        for v in vs:
            v.co = Vector(((x1 + x0) / 2 + v.co.x * (x1 - x0), v.co.y * 2 * y, (z1 + z0) / 2 + v.co.z * (z1 - z0)))
        bmesh.ops.subdivide_edges(bm, edges=list({{e for v in vs for e in v.link_edges}}), cuts=3, use_grid_fill=True)
    box(0, 0.09, -0.04, 0.04)
    for c, ln in zip((-0.03, -0.01, 0.01, 0.03), (0.17, 0.19, 0.18, 0.15)): box(0.09, ln, c - 0.007, c + 0.007)
    if mode != "nothumb": box(0.01, 0.06, 0.04, 0.06)
    me = bpy.data.meshes.new("M"); bm.to_mesh(me); bm.free()
    mesh = bpy.data.objects.new("M", me); bpy.context.scene.collection.objects.link(mesh)
    for b in arm_d.bones: mesh.vertex_groups.new(name=b.name)
    for v in me.vertices:
        grp = "mixamorig:LeftHandThumb1" if (mode != "nothumb" and v.co.z > 0.045 and v.co.x < 0.07 and v.co.x > 0.0) else "mixamorig:LeftHand"
        if mode == "one_mid" and v.co.x > 0.09 and abs(v.co.z - 0.01) < 0.01:
            grp = "mixamorig:LeftHandIndex1"  # the predicted chain holds the skin of its own finger
        mesh.vertex_groups[grp].add([v.index], 1.0, "REPLACE")
    def snapshot():
        return {{v.index: sorted((mesh.vertex_groups[g.group].name, round(g.weight, 5)) for g in v.groups) for v in me.vertices}}
    before = snapshot(); names_before = [b.name for b in arm_d.bones]
    report = {{}}
    if {marks!r}:
        fb.MARKS = {marks!r}
    fb.build_side(arm, mesh, "Left", report)
    after = snapshot()
    sums = [sum(w for _, w in after[i]) for i in after]
    out = {{"report": report["Left"], "names_before": names_before, "names_after": [b.name for b in arm_d.bones],
           "unchanged": before == after, "max_infl": max(len(after[i]) for i in after), "sum_min": min(sums), "sum_max": max(sums),
           "heads": {{b.name: list(b.head_local) for b in arm_d.bones if "Hand" in b.name and b.name.endswith(("1", "2", "3"))}},
           "tails": {{b.name: list(b.tail_local) for b in arm_d.bones if "Hand" in b.name and b.name.endswith("3")}},
           "new_groups": {{g.name: sum(1 for v in me.vertices for gg in v.groups if gg.group == g.index) for g in mesh.vertex_groups}}}}
    open({out!r}, "w").write(json.dumps(out))
''')


def _run(tmp_path, mode, marks=None):
    out = tmp_path / f"{mode}.json"
    script = tmp_path / f"scene_{mode}.py"
    script.write_text(SCENE.format(blender_dir=str(fb.__file__).rsplit("finger_build.py", 1)[0], mode=mode, out=str(out), marks=marks), encoding="utf-8")
    subprocess.run([str(BLENDER), "-b", "--threads", "2", "--python", str(script), "--", "x"], check=True, capture_output=True, timeout=300)
    return json.loads(out.read_text())


@needs_blender
def test_a_complete_hand_is_left_untouched(tmp_path):
    r = _run(tmp_path, "complete")
    assert r["report"]["path"] == "complete" and r["report"]["built"] == 0
    assert r["unchanged"] and r["names_before"] == r["names_after"]


@needs_blender
def test_one_chain_plus_thumb_is_built_to_five_fingers(tmp_path):
    r = _run(tmp_path, "one")
    assert r["report"]["path"] in ("tips", "proportion") and r["report"]["built"] == 9
    fingers = sorted(n for n in r["names_after"] if re.search(r"LeftHand(Thumb|Index|Middle|Ring|Pinky)\d$", n))
    assert len(fingers) == 15
    # the bone names are the ones the animation library's retarget expects
    for n in fingers:
        assert n.split(":", 1)[1] in gait.MIXAMO_BONES
    # skin: every vertex normalised with at most four influences; every new bone holds some of the hand
    assert r["max_infl"] <= 4 and abs(r["sum_min"] - 1) < 1e-3 and abs(r["sum_max"] - 1) < 1e-3
    new = [n for n in fingers if n not in r["names_before"]]
    assert len(new) == 9 and all(r["new_groups"][n] > 0 for n in new)
    # the chains lie inside the hand, beyond the palm's start, and the finger bones step outward from the knuckle
    for n in new:
        x, y, z = r["heads"][n]
        assert 0.03 < x < 0.19 and abs(z) < 0.06


@needs_blender
def test_a_predicted_chain_in_the_middle_is_renamed_to_its_lane_and_keeps_its_skin(tmp_path):
    r = _run(tmp_path, "one_mid")
    assert r["report"]["built"] == 9 and r["report"]["renamed"]
    assert r["report"]["kept"] and list(r["report"]["kept"].values()) == ["Index"]
    kept_lane = list(r["report"]["kept"])[0]
    assert kept_lane != "Index"
    assert r["new_groups"][f"mixamorig:LeftHand{kept_lane}1"] > 0  # the renamed bone's group still holds the finger's own skin
    assert r["max_infl"] <= 4 and abs(r["sum_min"] - 1) < 1e-3 and abs(r["sum_max"] - 1) < 1e-3


@needs_blender
def test_no_thumb_means_no_building(tmp_path):
    r = _run(tmp_path, "nothumb")
    assert r["report"]["path"] == "lump" and not r["report"].get("built")
    assert r["unchanged"] and r["names_before"] == r["names_after"]


# --- the rig step's call ------------------------------------------------------------------------------------------


def test_the_build_runs_before_everything_that_reads_the_skeleton():
    import inspect

    src = inspect.getsource(rig._rig)
    at = src.index("_fingers(sc, rig_glb, params)")
    for later in ("sc.register(\"glb\"", "_inspect(sc", "fit_hitboxes(sc", "_animate(sc"):
        assert at < src.index(later), later
    assert src.index("rig_fix.py") < at  # after the bones carry their Mixamo names


class _FakeSC:
    def __init__(self, tmp_path, report, write_out=True):
        self.dir = tmp_path
        self.report, self.write_out = report, write_out
        self.metrics, self.params, self.registered = {}, {}, {}
        self.settings = type("S", (), {"blender_timeout_seconds": 5})()

    async def blender(self, script, args, log_name=None, timeout=None):
        assert script == "finger_build.py"
        out, rep = args[1], args[args.index("--report") + 1]
        if self.write_out:
            out.write_bytes(b"NEW")
        rep.write_text(json.dumps(self.report), encoding="utf-8")

    def set_metrics(self, **kw):
        self.metrics.update(kw)

    def set_params(self, **kw):
        self.params.update(kw)

    def register(self, key, path):
        self.registered[key] = path


def _run_fingers(tmp_path, report, params=None):
    import asyncio

    glb = tmp_path / "rig.glb"
    glb.write_bytes(b"OLD")
    sc = _FakeSC(tmp_path, report)
    asyncio.run(rig._fingers(sc, glb, params or {}))
    return sc, glb


def test_built_hands_replace_the_rig_and_are_recorded(tmp_path):
    sc, glb = _run_fingers(tmp_path, {"Left": {"path": "proportion", "built": 9, "peaks": 2}, "Right": {"path": "lump", "reason": "x"}})
    assert glb.read_bytes() == b"NEW" and (tmp_path / "rig_before_fingers.glb").read_bytes() == b"OLD"
    fb_m = sc.metrics["finger_build"]
    assert fb_m["Left"]["path"] == "proportion" and fb_m["Right"]["path"] == "lump" and "finger_build" in sc.registered


def test_complete_or_refused_hands_leave_the_rig_alone(tmp_path):
    sc, glb = _run_fingers(tmp_path, {"Left": {"path": "complete", "built": 0}, "Right": {"path": "lump"}})
    assert glb.read_bytes() == b"OLD" and sc.metrics["finger_build"]["Left"]["path"] == "complete"


def test_the_named_setting_turns_it_off(tmp_path):
    sc, glb = _run_fingers(tmp_path, {"Left": {"path": "proportion", "built": 9}}, {"build_fingers": False})
    assert glb.read_bytes() == b"OLD" and "finger_build" not in sc.metrics and sc.params["build_fingers"] is False


@needs_blender
def test_fingertip_marks_place_the_built_chains_on_the_marked_tips(tmp_path):
    # The person marks the four fingertips (index to little finger, world points). The predicted index chain stays; the other three lanes are
    # built straight from their knuckle to the marked tip instead of a guess by proportion.
    marks = {"Left": {"fingertips": [[0.17, 0.0, 0.03], [0.185, 0.0, 0.01], [0.175, 0.0, -0.01], [0.145, 0.0, -0.03]]}}
    r = _run(tmp_path, "one", marks)
    assert r["report"]["path"] == "marks" and r["report"]["built"] == 9
    for name, tip in (("Middle", marks["Left"]["fingertips"][1]), ("Ring", marks["Left"]["fingertips"][2]), ("Pinky", marks["Left"]["fingertips"][3])):
        tail = r["tails"][f"mixamorig:LeftHand{name}3"]
        assert max(abs(a - b) for a, b in zip(tail, tip)) < 0.003, (name, tail, tip)
    assert r["max_infl"] <= 4 and abs(r["sum_min"] - 1) < 1e-3 and abs(r["sum_max"] - 1) < 1e-3


# --- a finger chain that runs back toward the wrist ----------------------------------------------------------------


def test_turned_back_reads_the_first_bone_against_the_wrist():
    # the goblin's pinky as the rig had it (frame coordinates: a along the hand from the wrist): the first bone runs from
    # a 0.17 back to a 0.13, the three joints step back toward the wrist
    turned = np.array([[0.17, 0.02, 0.0], [0.13, 0.02, 0.0], [0.09, 0.01, 0.0], [0.05, 0.01, 0.0]])
    straight = np.array([[0.17, -0.1, 0.0], [0.22, -0.1, 0.0], [0.26, -0.1, 0.0], [0.30, -0.1, 0.0]])
    assert fb.turned_back(turned) is True
    assert fb.turned_back(straight) is False


def test_turned_back_leaves_a_sideways_first_bone_alone():
    # a first bone square to the hand (no a change) is not turned back; the chain's later bones going outward count
    square = np.array([[0.2, 0.0, 0.0], [0.2, 0.05, 0.0], [0.25, 0.1, 0.0], [0.3, 0.15, 0.0]])
    assert fb.turned_back(square) is True  # zero advance counts as not away from the wrist
    ok = np.array([[0.2, 0.0, 0.0], [0.21, 0.05, 0.0], [0.25, 0.1, 0.0], [0.3, 0.15, 0.0]])
    assert fb.turned_back(ok) is False
