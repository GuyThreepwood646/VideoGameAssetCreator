"""Bone names by geometry (blender/rig_fix.py): the pure role-finding maths on a synthetic skeleton built like
SkinToken's Mixamo-template output (names shifted along one arm, the real head an Extra bone), plus mirrored,
turned and broken variants. Plain numpy, no Blender."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np

from vgac.stages import rig

RF_PATH = Path(__file__).resolve().parent.parent / "vgac" / "blender" / "rig_fix.py"
_spec = importlib.util.spec_from_file_location("vgac_rig_fix_under_test", RF_PATH)
rf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rf)

P = "mixamorig:"
# (name, parent, head) as the rigger wrote them for the knight (Blender space, facing -Y, toes towards -Y). The bones
# named Left* sit at -X, the character's right; "Neck" and "Head" are the forearm and hand of the +X arm; the real
# neck and head are Extra_04 and Extra_05.
RAW = [
    ("Hips", None, (0.004, -0.512, 0.145)),
    ("LeftUpLeg", "Hips", (-0.145, -0.520, 0.082)),
    ("LeftLeg", "LeftUpLeg", (-0.176, -0.527, -0.426)),
    ("LeftFoot", "LeftLeg", (-0.207, -0.488, -0.863)),
    ("LeftToeBase", "LeftFoot", (-0.262, -0.652, -0.988)),
    ("RightUpLeg", "Hips", (0.152, -0.520, 0.082)),
    ("RightLeg", "RightUpLeg", (0.176, -0.527, -0.426)),
    ("RightFoot", "RightLeg", (0.215, -0.488, -0.863)),
    ("RightToeBase", "RightFoot", (0.270, -0.652, -0.988)),
    ("Spine", "Hips", (0.004, -0.520, 0.246)),
    ("Spine1", "Spine", (0.004, -0.512, 0.379)),
    ("Spine2", "Spine1", (0.004, -0.496, 0.543)),
    ("Extra_04", "Spine2", (0.004, -0.488, 0.723)),
    ("Extra_05", "Extra_04", (0.004, -0.504, 0.770)),
    ("Extra_06", "Spine2", (0.090, -0.488, 0.684)),
    ("Extra_07", "Extra_06", (0.254, -0.496, 0.598)),
    ("Neck", "Extra_07", (0.285, -0.481, 0.277)),
    ("Head", "Neck", (0.324, -0.606, 0.035)),
    ("Extra_10", "Head", (0.293, -0.629, 0.012)),
    ("Extra_11", "Extra_10", (0.270, -0.645, -0.012)),
    ("Extra_12", "Extra_11", (0.238, -0.652, -0.043)),
    ("Extra_13", "Head", (0.301, -0.660, -0.074)),
    ("Extra_14", "Extra_13", (0.285, -0.668, -0.105)),
    ("Extra_15", "Extra_14", (0.270, -0.668, -0.129)),
    ("RightShoulder", "Head", (0.316, -0.613, -0.090)),
    ("RightArm", "RightShoulder", (0.309, -0.613, -0.113)),
    ("RightForeArm", "RightArm", (0.293, -0.613, -0.137)),
    ("LeftShoulder", "Spine2", (-0.082, -0.488, 0.684)),
    ("LeftArm", "LeftShoulder", (-0.246, -0.488, 0.598)),
    ("LeftForeArm", "LeftArm", (-0.277, -0.481, 0.277)),
    ("LeftHand", "LeftForeArm", (-0.316, -0.606, 0.035)),
    ("LeftHandIndex1", "LeftHand", (-0.301, -0.660, -0.074)),
    ("LeftHandIndex2", "LeftHandIndex1", (-0.285, -0.668, -0.105)),
    ("LeftHandIndex3", "LeftHandIndex2", (-0.270, -0.668, -0.129)),
    ("LeftHandMiddle1", "LeftHand", (-0.324, -0.606, -0.074)),
    ("LeftHandMiddle2", "LeftHandMiddle1", (-0.316, -0.606, -0.098)),
    ("LeftHandMiddle3", "LeftHandMiddle2", (-0.309, -0.606, -0.121)),
    ("LeftHandThumb1", "LeftHand", (-0.285, -0.629, 0.012)),
    ("LeftHandThumb2", "LeftHandThumb1", (-0.262, -0.645, -0.012)),
    ("LeftHandThumb3", "LeftHandThumb2", (-0.238, -0.652, -0.043)),
]


def _bones(transform=lambda p: p, drop=()):
    out = []
    for n, par, h in RAW:
        if n in drop or par in drop:
            continue
        out.append({"name": P + n, "parent": P + par if par else None, "head": list(transform(np.array(h, float)))})
    return out


def _new_names(bones):
    found = rf.find_roles(bones)
    assert found["ok"], found["reason"]
    return found, rf.plan_names(bones, found["roles"])


def test_shifted_mixamo_names_are_corrected_by_geometry():
    bones = _bones()
    found, new = _new_names(bones)
    assert found["left_is"] == "+x" and found["forward_from"] == "toes" and found["face_agrees"] is True
    # The real neck and head are the Extra bones on top of the spine.
    assert new[P + "Extra_04"] == P + "Neck" and new[P + "Extra_05"] == P + "Head"
    # The +X arm is the character's left: the bones called Neck and Head there are the forearm and hand.
    assert [new[P + n] for n in ("Extra_06", "Extra_07", "Neck", "Head")] == \
        [P + "LeftShoulder", P + "LeftArm", P + "LeftForeArm", P + "LeftHand"]
    # The -X arm, named Left by the rigger, is the right one.
    assert [new[P + n] for n in ("LeftShoulder", "LeftArm", "LeftForeArm", "LeftHand")] == \
        [P + "RightShoulder", P + "RightArm", P + "RightForeArm", P + "RightHand"]
    assert new[P + "LeftUpLeg"] == P + "RightUpLeg" and new[P + "RightToeBase"] == P + "LeftToeBase"
    for n in ("Hips", "Spine", "Spine1", "Spine2"):
        assert new[P + n] == P + n
    # Fingers: thumb nearest the wrist, then index (frontmost) and middle.
    assert new[P + "Extra_10"] == P + "LeftHandThumb1" and new[P + "Extra_12"] == P + "LeftHandThumb3"
    assert new[P + "Extra_13"] == P + "LeftHandIndex1"
    assert new[P + "RightShoulder"] == P + "LeftHandMiddle1" and new[P + "RightForeArm"] == P + "LeftHandMiddle3"
    assert new[P + "LeftHandThumb1"] == P + "RightHandThumb1" and new[P + "LeftHandIndex2"] == P + "RightHandIndex2"
    # Every bone has exactly one new name.
    assert len(set(new.values())) == len(bones)


def test_mirrored_rig_gets_mirrored_names():
    mirror = lambda p: np.array([-p[0], p[1], p[2]])  # noqa: E731
    _, new = _new_names(_bones(mirror))
    assert new[P + "LeftArm"] == P + "LeftArm" and new[P + "Extra_07"] == P + "RightArm"
    assert new[P + "LeftUpLeg"] == P + "LeftUpLeg"
    assert new[P + "Extra_05"] == P + "Head"


def test_turned_rig_finds_its_left_from_the_toes():
    # Turned 90 degrees about Z: the character faces +X, so its left is +Y.
    turn = lambda p: np.array([-p[1], p[0], p[2]])  # noqa: E731
    found, new = _new_names(_bones(turn))
    assert found["left_is"] == "+y"
    assert new[P + "Extra_07"] == P + "LeftArm" and new[P + "LeftArm"] == P + "RightArm"


def test_fewer_fingers_and_five_finger_order():
    bones = _bones(drop=("Extra_13", "RightShoulder"))  # the +X hand keeps only a thumb
    found, new = _new_names(bones)
    # One chain past the hand reads as one long arm: the first four bones are the arm, the rest have no role.
    assert new[P + "Head"] == P + "LeftHand" and new[P + "Extra_10"].startswith(P + "Extra_")
    assert P + "LeftHandThumb1" not in new.values()
    # Five chains on a hand: thumb nearest the wrist, then index to pinky from front to back.
    hand = {"name": "h", "parent": None, "head": [0.0, 0.0, 0.0]}
    sk = [hand]
    bases = {"t": (0.02, -0.02, 0.0), "i": (0.08, -0.04, 0.0), "m": (0.09, -0.015, 0.0), "r": (0.085, 0.01, 0.0), "p": (0.075, 0.03, 0.0)}
    for k, b in bases.items():
        sk.append({"name": k, "parent": "h", "head": list(b)})
    roles, why = rf._arm_roles(rf.Bones([{"name": "a", "parent": None, "head": [0, 0, 0]}, {"name": "f", "parent": "a", "head": [0, 0, 0]},
                                         *[dict(b, parent=b["parent"] or "f") for b in sk]]),
                                "a", "Left", np.array([0.0, -1.0, 0.0]))
    assert why is None
    assert [roles[f"LeftHand{f}1"] for f in ("Thumb", "Index", "Middle", "Ring", "Pinky")] == ["t", "i", "m", "r", "p"]
    assert roles["LeftArm"] == "a" and roles["LeftForeArm"] == "f" and roles["LeftHand"] == "h"


def test_sanity_checks_leave_names_alone():
    one_arm = _bones(drop=("LeftShoulder",))
    found = rf.find_roles(one_arm)
    assert not found["ok"] and "arm" in found["reason"]
    lying = lambda p: np.array([p[0], -p[2], p[1]])  # noqa: E731  (rotated onto its back)
    assert not rf.find_roles(_bones(lying))["ok"]
    rep, mapping = rf.name_report(one_arm, "humanoid")
    assert rep["renamed"] == 0 and rep["skipped_reason"] and mapping == {}
    rep, mapping = rf.name_report(_bones(), "other")
    assert rep["skipped_reason"] == "not a humanoid skeleton" and mapping == {}


def test_report_and_extra_names():
    bones = _bones() + [{"name": P + "Cape", "parent": P + "Spine1", "head": [0.0, 0.1, 0.3]},
                        {"name": P + "Extra_40", "parent": P + "Spine", "head": [0.0, 0.1, 0.2]}]
    rep, mapping = rf.name_report(bones, "humanoid")
    assert rep["by"] == "geometry" and rep["left_is"] == "+x" and rep["skipped_reason"] is None
    assert rep["roles"]["Head"] == P + "Extra_05" and rep["roles"]["LeftArm"] == P + "Extra_07"
    assert mapping[P + "Extra_40"] == P + "Extra_40"  # a free Extra name is kept
    assert mapping[P + "Cape"].startswith(P + "Extra_") and mapping[P + "Cape"] != P + "Extra_40"
    assert rep["renamed"] == sum(1 for o, n in mapping.items() if o != n)


def test_rig_stage_passes_the_skeleton_and_reads_bone_names():
    src = Path(rig.__file__).read_text(encoding="utf-8")
    assert '"--skeleton", skeleton' in src and 'TEMPLATES["mixamo"]' in src
    assert '"bone_names": {"count"' in src
