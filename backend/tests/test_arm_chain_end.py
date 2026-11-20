"""Arm IK end (WP-77): the palm-down pass puts Extra_ForeArmTwist bones between ForeArm and Hand, all starting at the elbow.
procanim's arm chain must end at the Hand (the wrist), so the forearm length l2 is elbow to wrist, not zero. One Blender
run builds the same humanoid with and without the twist bones and compares the two; skipped without Blender."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.machine_tools import blender as machine_blender
from tests.test_arm_pose import _BUILD

ROOT = Path(__file__).resolve().parents[2]
BLENDER = machine_blender() or Path("blender-not-found")
PROCANIM = ROOT / "backend" / "vgac" / "blender" / "procanim.py"

# The twist bones go in while the armature is in edit mode, between ForeArm and Hand, as palm_down.py does.
MARK = 'bpy.ops.object.mode_set(mode="OBJECT")\n# A body box'
TWIST = '''
if sys.argv[sys.argv.index("--") + 3] == "1":
    for s in ("Left", "Right"):
        E_, H_ = J[s + "ForeArm"], J[s + "Hand"]
        parent = eb[s + "ForeArm"]
        for k in range(3):
            t = ad.edit_bones.new("mixamorig:Extra_ForeArmTwist_%s%d" % (s[0], k + 1))
            t.head = tuple(E_[i] + (H_[i] - E_[i]) * k / 3 for i in range(3))
            t.tail = tuple(E_[i] + (H_[i] - E_[i]) * (k + 1) / 3 for i in range(3))
            t.parent = parent
            parent = t
        eb[s + "Hand"].parent = parent
'''
assert _BUILD.count(MARK) == 1
BUILD = _BUILD.replace(MARK, TWIST + MARK)


def _build(tmp_path, twist: bool, ang: int = 30) -> Path:
    script = tmp_path / f"build_{int(twist)}.py"
    script.write_text(BUILD, encoding="utf-8")
    out = tmp_path / f"rig_{int(twist)}.glb"
    subprocess.run([str(BLENDER), "-b", "--python", str(script), "--", str(out), str(ang), "1" if twist else "0"],
                   check=True, capture_output=True, timeout=300)
    assert out.is_file()
    return out


def _moves(tmp_path, rig: Path, tag: str) -> dict:
    out = tmp_path / f"anim_{tag}.glb"
    subprocess.run([str(BLENDER), "-b", "--python", str(PROCANIM), "--", str(rig), str(out), "24", "--motion", "biped",
                    "--clips", "idle,walk", "--arm-pose", "relaxed"], check=True, capture_output=True, timeout=600)
    return json.loads((tmp_path / f"anim_{tag}_anim.json").read_text(encoding="utf-8"))


@pytest.mark.skipif(not (BLENDER.is_file() and PROCANIM.is_file()), reason="needs Blender (VGAC_TEST_BLENDER)")
def test_the_arm_ik_ends_at_the_wrist_with_or_without_twist_bones(tmp_path):
    plain = _moves(tmp_path, _build(tmp_path, False), "plain")
    twisted = _moves(tmp_path, _build(tmp_path, True), "twisted")
    assert len(twisted["arms"]) == 2
    for a_plain, a_twist in zip(plain["arms"], twisted["arms"]):
        ik_p, ik_t = a_plain["ik"], a_twist["ik"]
        # the end of the chain is the Hand, not the first twist bone that starts at the elbow
        assert ik_t["end_bone"] == "mixamorig:" + ("LeftHand" if a_twist["side"] == "left" else "RightHand")
        assert ik_t["end_bone"] == ik_p["end_bone"]
        # the forearm is elbow to wrist: 0.25 m in the built humanoid, with or without the twist bones
        assert ik_p["l2"] == pytest.approx(0.25, abs=0.005)
        assert ik_t["l2"] == pytest.approx(ik_p["l2"], abs=1e-4)
        assert ik_t["l1"] == pytest.approx(ik_p["l1"], abs=1e-4)
        assert ik_t["rest_reach"] == pytest.approx(ik_p["rest_reach"], abs=1e-4)
