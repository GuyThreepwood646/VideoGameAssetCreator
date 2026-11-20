"""Bone roles against geometry (WP-77): rig_fix.find_roles also checks that the arms mirror each other, the shoulders sit
beside the spine and below the head top, and the head starts above the chest. A refusal with the ROLE_FAULT prefix stops
the rig step (stages/rig.py `_role_fault`). Pure numpy on synthetic skeletons; no Blender."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

RF_PATH = Path(__file__).resolve().parent.parent / "vgac" / "blender" / "rig_fix.py"
_spec = importlib.util.spec_from_file_location("vgac_rig_fix_geometry", RF_PATH)
rf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rf)


def humanoid(forearm_stretch: float = 0.0, clavicle_rise: float = 0.0) -> list[dict]:
    """A raw SkinToken-style skeleton (Blender Z up, metres): the chest is the bone named Neck, the left clavicle is named
    Head, the right clavicle RightShoulder, as in the boy's raw rig (four bones per arm, the last one holding the fingers).
    forearm_stretch moves the left elbow and wrist out;
    clavicle_rise raises the left clavicle chain."""
    c = clavicle_rise
    pts = {
        "Hips": (0.0, 0.0, 0.95), "Spine": (0.0, 0.0, 1.05), "Spine1": (0.0, 0.0, 1.18), "Neck": (0.0, 0.0, 1.32),
        "Extra_04": (0.0, 0.0, 1.45), "Extra_05": (0.0, 0.0, 1.58),
        "LeftUpLeg": (0.12, 0.0, 0.90), "LeftLeg": (0.20, 0.0, 0.50), "LeftFoot": (0.25, 0.0, 0.10), "LeftToeBase": (0.30, -0.12, 0.02),
        "RightUpLeg": (-0.12, 0.0, 0.90), "RightLeg": (-0.20, 0.0, 0.50), "RightFoot": (-0.25, 0.0, 0.10), "RightToeBase": (-0.30, -0.12, 0.02),
        "Head": (0.09, 0.0, 1.30 + c), "LeftShoulder": (0.24, 0.0, 1.27 + c), "LeftArm": (0.47 + forearm_stretch, 0.0, 1.30 + c),
        "LeftForeArm": (0.73 + forearm_stretch, 0.0, 1.33 + c),
        "RightShoulder": (-0.08, 0.0, 1.30), "RightArm": (-0.22, 0.0, 1.27), "RightForeArm": (-0.45, 0.0, 1.30),
        "RightHand": (-0.72, 0.0, 1.33),
    }
    parent = {"Spine": "Hips", "Spine1": "Spine", "Neck": "Spine1", "Extra_04": "Neck", "Extra_05": "Extra_04",
              "Head": "Neck", "LeftShoulder": "Head", "LeftArm": "LeftShoulder", "LeftForeArm": "LeftArm",
              "RightShoulder": "Neck", "RightArm": "RightShoulder", "RightForeArm": "RightArm", "RightHand": "RightForeArm",
              "LeftUpLeg": "Hips", "LeftLeg": "LeftUpLeg", "LeftFoot": "LeftLeg", "LeftToeBase": "LeftFoot",
              "RightUpLeg": "Hips", "RightLeg": "RightUpLeg", "RightFoot": "RightLeg", "RightToeBase": "RightFoot"}
    bones = [{"name": "Hips", "parent": None, "head": pts["Hips"]}]
    bones += [{"name": n, "parent": parent[n], "head": pts[n]} for n in pts if n != "Hips"]
    return bones


def _roles(bones):
    return rf.find_roles(bones)


def test_a_symmetric_humanoid_passes_and_keeps_the_roles():
    res = _roles(humanoid())
    assert res["ok"] and res["reason"] is None
    assert res["roles"]["Spine2"] == "Neck" and res["roles"]["LeftShoulder"] == "Head"
    assert res["roles"]["LeftArm"] == "LeftShoulder" and res["roles"]["LeftHand"] == "LeftForeArm"
    assert res["roles"]["Head"] == "Extra_05" and res["roles"]["Neck"] == "Extra_04"


def test_a_left_upper_arm_much_longer_than_the_right_is_refused_with_the_role_fault_prefix():
    res = _roles(humanoid(forearm_stretch=0.45))
    assert not res["ok"]
    assert res["reason"].startswith(rf.ROLE_FAULT)
    assert "upper arm" in res["reason"] and "differ in length" in res["reason"]


def test_a_shoulder_raised_above_the_head_top_is_refused():
    res = _roles(humanoid(clavicle_rise=0.40))
    assert not res["ok"]
    assert res["reason"].startswith(rf.ROLE_FAULT)
    assert "below the top of the head" in res["reason"] or "not level" in res["reason"]


def test_the_geometry_check_lists_each_problem_in_plain_words():
    bones = humanoid(forearm_stretch=0.45, clavicle_rise=0.40)
    res = _roles(bones)
    assert not res["ok"] and res["reason"].startswith(rf.ROLE_FAULT)
    # every problem is a sentence; they are joined with semicolons after the prefix
    parts = res["reason"][len(rf.ROLE_FAULT):].split("; ")
    assert len(parts) >= 2 and all(p[:1].islower() for p in parts)


def test_the_rig_step_stops_only_on_the_role_fault(tmp_path):
    from vgac.stages import rig

    report = tmp_path / "rig_fix.json"
    assert rig._role_fault(report) is None                     # no report
    report.write_text(json.dumps({"bone_names": {"skipped_reason": "no two legs reaching the ground"}}), encoding="utf-8")
    assert rig._role_fault(report) is None                     # an ordinary refusal keeps the raw names, as before
    report.write_text(json.dumps({"bone_names": {"skipped_reason": rf.ROLE_FAULT + "the two shoulders are not level"}}), encoding="utf-8")
    msg = rig._role_fault(report)
    assert msg and msg.startswith("The rig stopped") and "the two shoulders are not level" in msg
    assert rig.ROLE_FAULT_PREFIX == rf.ROLE_FAULT              # the two copies of the prefix stay in step
