"""Rig accuracy (rig-hands-wings, 2026-11-22): finger chains judged against the hand's skin, and a T-posed arm told from a wing.

Fingers: a joint is on the hand when it is inside the closed skin or within 1.5 cm of its surface (blender/finger_skin.py,
measured by rig_info.py, read by the rig check `fingers`). The old measure, distance to the nearest skin vertex over 3 cm,
failed joints deep in thick or low-poly fingers and passed fingers lying 2 to 3 cm outside the skin.
Wings: on a two-legged body a wing candidate whose weighted skin is a round tube (thickness over chord above 0.45) is an
arm (procanim.py WING_FLAT_MAX); a flat sheet (about 0.3) stays a wing.
The Blender cases build their rigs in pytest's temp folder; one Blender run each; skipped without Blender.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.machine_tools import blender as machine_blender
from tests.test_arm_pose import _BUILD
from vgac.blender import finger_skin as fs
from vgac.stages import rig

ROOT = Path(__file__).resolve().parents[2]
BLENDER = machine_blender() or Path("blender-not-found")
BL = ROOT / "backend" / "vgac" / "blender"
needs_blender = pytest.mark.skipif(not BLENDER.is_file(), reason="needs Blender (VGAC_TEST_BLENDER)")


def _env() -> dict:
    env = dict(os.environ)
    env["OMP_NUM_THREADS"] = "2"
    return env


def _run(script: Path, *args) -> None:
    subprocess.run([str(BLENDER), "-b", "--factory-startup", "-t", "2", "--python", str(script), "--", *map(str, args)],
                   check=True, capture_output=True, timeout=600, env=_env(), stdin=subprocess.DEVNULL)


# ---- the decision (no Blender) ----------------------------------------------------------------------------------------

def test_a_joint_deep_inside_a_closed_thick_finger_is_on_the_hand():
    assert fs.chain_outside_cm([1, 2, 3, 4], lambda j: True, lambda j: 0.057, closed=True) == 0.0


def test_a_joint_two_cm_outside_the_skin_is_off_the_hand():
    cm = fs.chain_outside_cm([1, 2, 3, 4], lambda j: j != 4, lambda j: 0.02 if j == 4 else 0.01, closed=True)
    assert cm == 2.0 and cm > rig.FINGER_OFF_SKIN_CM


def test_an_open_skin_falls_back_to_the_surface_distance_alone():
    # inside is undefined on an open skin: a joint 5.7 cm from the surface is then off the hand, one 1 cm away is on it
    assert fs.chain_outside_cm([1], lambda j: True, lambda j: 0.057, closed=False) == 5.7
    assert fs.chain_outside_cm([1], lambda j: True, lambda j: 0.01, closed=False) <= rig.FINGER_OFF_SKIN_CM


def test_closed_and_open_edges():
    quad_box = [[(0, 1), (1, 2), (2, 3), (0, 3)], [(0, 1), (1, 2), (2, 3), (0, 3)]]  # two faces sharing every edge
    assert fs.edges_closed(quad_box, lambda i: True)
    assert not fs.edges_closed(quad_box[:1], lambda i: True)  # one face: every edge is a border
    assert fs.edges_closed(quad_box[:1], lambda i: False)  # the open face is away from the hand


def _names(fingers=("Thumb", "Index", "Middle", "Ring", "Pinky")):
    out = ["mixamorig:LeftHand", "mixamorig:RightHand"]
    for s in ("Left", "Right"):
        out += [f"mixamorig:{s}Hand{f}{i}" for f in fingers for i in (1, 2, 3)]
    return out


def test_fingers_check_passes_a_thick_thumb_inside_the_skin_and_fails_a_finger_two_cm_outside():
    five = {f: 0.0 for f in fs_fingers()}
    ok = rig.fingers_check(_names(), None, None, {"Left": five, "Right": five})
    assert ok["passed"]
    off = rig.fingers_check(_names(), None, None, {"Left": {**five, "Pinky": 2.0}, "Right": five})
    assert not off["passed"] and off["vars"]["off_skin"] == {"left": {"Pinky": 2.0}}


def fs_fingers():
    return ("Thumb", "Index", "Middle", "Ring", "Pinky")


# ---- planted hand in Blender: closed and open skin --------------------------------------------------------------------

_HAND = r'''
import sys, bpy, bmesh
a = sys.argv[sys.argv.index("--") + 1:]
out, open_skin = a[0], a[1] == "open"
bpy.ops.wm.read_factory_settings(use_empty=True)
ad = bpy.data.armatures.new("Armature"); ob = bpy.data.objects.new("Armature", ad)
bpy.context.scene.collection.objects.link(ob); bpy.context.view_layer.objects.active = ob
bpy.ops.object.mode_set(mode="EDIT")
h = ad.edit_bones.new("mixamorig:LeftHand"); h.head = (0.0, 0, 1.0); h.tail = (0.08, 0, 1.0)
# a thick block hand 20 cm long, 10 cm thick: the fingers' chains run through its middle (5 cm from the surface),
# except the little finger, laid 2.5 cm above the top face
for k, f in enumerate(("Thumb", "Index", "Middle", "Ring", "Pinky")):
    y = -0.06 + 0.03 * k
    z = 1.075 if f == "Pinky" else 1.0
    prev = h
    for i in range(3):
        b = ad.edit_bones.new("mixamorig:LeftHand%s%d" % (f, i + 1))
        b.head = (0.08 + 0.035 * i, y, z); b.tail = (0.08 + 0.035 * (i + 1), y, z); b.parent = prev; prev = b
bpy.ops.object.mode_set(mode="OBJECT")
me = bpy.data.meshes.new("hand"); bm = bmesh.new()
bmesh.ops.create_cube(bm, size=1.0)
for v in bm.verts:
    v.co.x = 0.1 + v.co.x * 0.2; v.co.y = v.co.y * 0.16; v.co.z = 1.0 + v.co.z * 0.1
if open_skin:
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.normal.z > 0.9], context="FACES_ONLY")
bm.to_mesh(me); bm.free()
mo = bpy.data.objects.new("hand", me); bpy.context.scene.collection.objects.link(mo); mo.parent = ob
mo.modifiers.new("arm", "ARMATURE").object = ob
g = mo.vertex_groups.new(name="mixamorig:LeftHand"); g.add([v.index for v in me.vertices], 1.0, "REPLACE")
bpy.ops.object.select_all(action="SELECT")
bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", use_selection=True)
'''


def _hand_chains(tmp_path: Path, skin: str) -> dict:
    build = tmp_path / f"hand_{skin}.py"
    build.write_text(_HAND, encoding="utf-8")
    glb = tmp_path / f"hand_{skin}.glb"
    _run(build, glb, skin)
    info = tmp_path / f"info_{skin}"
    _run(BL / "rig_info.py", glb, info, "35")
    return json.loads((info / "rig_info.json").read_text(encoding="utf-8"))


@needs_blender
def test_planted_hand_closed_skin_keeps_deep_chains_and_flags_the_chain_outside(tmp_path):
    doc = _hand_chains(tmp_path, "closed")
    ch = doc["finger_chains"]["Left"]
    assert doc["finger_skin_closed"]["Left"] is True
    assert all(ch[f] <= rig.FINGER_OFF_SKIN_CM for f in ("Thumb", "Index", "Middle", "Ring"))  # 5 cm deep, inside
    assert ch["Pinky"] == pytest.approx(2.5, abs=0.2)  # 2.5 cm above the top face


@needs_blender
def test_planted_hand_open_skin_uses_the_surface_distance_alone(tmp_path):
    doc = _hand_chains(tmp_path, "open")
    ch = doc["finger_chains"]["Left"]
    assert doc["finger_skin_closed"]["Left"] is False
    assert ch["Index"] > rig.FINGER_OFF_SKIN_CM  # inside is undefined without the top face: 5 cm from the nearest face


# The goblin's left little finger lies wholly outside its skin (rig-hands-wings PLAN.md): it must still fail. Library rig of an
# agent-made asset; set VGAC_GOBLIN_RIG to its rig.glb, skipped otherwise.
GOBLIN = Path(os.environ["VGAC_GOBLIN_RIG"]) if os.environ.get("VGAC_GOBLIN_RIG") else None


@needs_blender
@pytest.mark.skipif(not (GOBLIN and GOBLIN.is_file()), reason="needs the cartoon goblin rig (VGAC_GOBLIN_RIG)")
def test_goblin_left_little_finger_outside_the_skin_still_fails(tmp_path):
    _run(BL / "rig_info.py", GOBLIN, tmp_path / "goblin", "35")
    ch = json.loads((tmp_path / "goblin" / "rig_info.json").read_text(encoding="utf-8"))["finger_chains"]["Left"]
    assert ch["Pinky"] > rig.FINGER_OFF_SKIN_CM
    assert ch["Index"] <= rig.FINGER_OFF_SKIN_CM  # 14 cm from the nearest vertex but inside the skin: no longer flagged


# ---- planted wings on a biped -----------------------------------------------------------------------------------------
# The synthetic arm rig (tests/test_arm_pose.py _BUILD) is the base: a biped with weighted feet. A pair of long sideways
# chains off Spine2 is planted at shoulder height, raised 33 degrees, its skin either a round tube (12 x 12 cm) or a flat sheet (40 cm chord,
# 12 cm thick: thickness over chord 0.3).

_EDIT_MARK = 'bpy.ops.object.mode_set(mode="OBJECT")\n# A body box'
_EXPORT_MARK = 'bpy.ops.object.select_all(action="SELECT")'
assert _BUILD.count(_EDIT_MARK) == 1 and _BUILD.count(_EXPORT_MARK) == 1

_LIMB_EDIT = '''
for s_, x_ in (("L", 1), ("R", -1)):
    prev_ = eb["Spine2"]
    for i_, (a_, b_) in enumerate((((0.04, 1.42), (0.2, 1.42)), ((0.2, 1.42), (0.5, 1.61)), ((0.5, 1.61), (0.85, 1.84)), ((0.85, 1.84), (0.9, 1.87)))):
        w_ = ad.edit_bones.new("mixamorig:Limb%d.%s" % (i_, s_))
        w_.head = (a_[0] * x_, 0.0, a_[1])
        w_.tail = (b_[0] * x_, 0.0, b_[1])
        w_.parent = prev_
        prev_ = w_
'''
# the skin: a box from 5 cm past the limb's root to its tip, along the limb (raised 33 degrees, so the body stays narrow
# enough for the two feet to read as two legs), CHORD_ wide front to back and 12 cm thick
_LIMB_OBJ = '''
import bmesh, math
for s_, x_ in (("L", 1), ("R", -1)):
    me_ = bpy.data.meshes.new("limb" + s_)
    bm_ = bmesh.new()
    bmesh.ops.create_cube(bm_, size=1.0)
    bmesh.ops.subdivide_edges(bm_, edges=bm_.edges[:], cuts=4, use_grid_fill=True)
    r_, t_ = (0.2, 1.42), (0.85, 1.84)
    L_ = math.hypot(t_[0] - r_[0], t_[1] - r_[1])
    d_ = ((t_[0] - r_[0]) / L_, (t_[1] - r_[1]) / L_)
    n_ = (-d_[1], d_[0])
    for v_ in bm_.verts:
        u_ = 0.05 + (v_.co.x + 0.5) * (L_ - 0.05)
        w_ = v_.co.z * 0.12
        v_.co.y = v_.co.y * CHORD_
        v_.co.x = (r_[0] + d_[0] * u_ + n_[0] * w_) * x_
        v_.co.z = r_[1] + d_[1] * u_ + n_[1] * w_
    bm_.to_mesh(me_)
    bm_.free()
    lo_ = bpy.data.objects.new("limb" + s_, me_)
    bpy.context.scene.collection.objects.link(lo_)
    lo_.parent = ob
    lo_.modifiers.new("arm", "ARMATURE").object = ob
    g1_ = lo_.vertex_groups.new(name="mixamorig:Limb1." + s_)
    g2_ = lo_.vertex_groups.new(name="mixamorig:Limb2." + s_)
    g1_.add([v_.index for v_ in me_.vertices if abs(v_.co.x) < 0.5], 1.0, "REPLACE")
    g2_.add([v_.index for v_ in me_.vertices if abs(v_.co.x) >= 0.5], 1.0, "REPLACE")
'''


def _limb_read(tmp_path: Path, tag: str, chord: float) -> dict:
    src = _BUILD.replace(_EDIT_MARK, _LIMB_EDIT + _EDIT_MARK, 1)
    src = src.replace(_EXPORT_MARK, _LIMB_OBJ.replace("CHORD_", repr(chord)) + _EXPORT_MARK, 1)
    build = tmp_path / f"{tag}_build.py"
    build.write_text(src, encoding="utf-8")
    rig_glb = tmp_path / f"{tag}.glb"
    _run(build, rig_glb, "102")
    _run(BL / "procanim.py", rig_glb, tmp_path / f"{tag}_out.glb", "24", "--clips", "idle")
    return json.loads((tmp_path / f"{tag}_out_anim.json").read_text(encoding="utf-8"))


@needs_blender
def test_a_biped_t_posed_arm_with_round_skin_is_an_arm_not_a_wing(tmp_path):
    # on master this pair reads as two wings (fails there); the chain ends in an unweighted Limb3 tip bone, since glTF
    # import guesses a leaf bone's tail
    r = _limb_read(tmp_path, "tube", 0.12)
    assert r["kind"] == "biped" and len(r["legs"]) == 2
    assert r["wings"] == [] and not r["wings_found"]
    assert len(r["arms"]) == 2  # the arms block now sees every arm-side chain, as on any biped


@needs_blender
def test_a_biped_wing_with_flat_skin_at_0_3_stays_a_wing(tmp_path):
    r = _limb_read(tmp_path, "sheet", 0.40)
    assert r["kind"] == "biped" and len(r["legs"]) == 2
    assert {w["leaf"] for w in r["wings"]} == {"mixamorig:Limb3.L", "mixamorig:Limb3.R"}
    assert all(0.2 < w["flat"] < 0.4 for w in r["wings"])
