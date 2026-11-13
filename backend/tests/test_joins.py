"""Parts the 3D build joined: the leg-parting pass (blender/joins.py, stages/rig.py _joins)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vgac" / "blender"))
import joins as jn  # noqa: E402

from vgac import guidance  # noqa: E402
from vgac.stages import rig  # noqa: E402


def test_bone_classes():
    assert jn.bone_class("mixamorig:LeftUpLeg") == "Lleg" and jn.bone_class("mixamorig:RightToeBase") == "Rleg"
    assert jn.bone_class("mixamorig:LeftForeArm") == "Llowarm" and jn.bone_class("mixamorig:RightHandIndex2") == "Rlowarm"
    assert jn.bone_class("mixamorig:LeftArm") == "Luparm" and jn.bone_class("mixamorig:LeftShoulder") == "torso"
    assert jn.bone_class("mixamorig:Spine2") == "torso" and jn.bone_class("mixamorig:Extra_03") == "other"


def _strip(z: float):
    """Two quads side by side at height z: the left one on the left leg, the right one on the right leg, sharing an edge."""
    V = np.array([[0, 0, z], [1, 0, z], [2, 0, z], [0, 0, z + 0.01], [1, 0, z + 0.01], [2, 0, z + 0.01],
                  [5, 0, 0.0], [5, 0, 1.0]], dtype=float)  # the last two set the ground and the top
    F = np.array([[0, 1, 4], [0, 4, 3], [1, 2, 5], [1, 5, 4], [6, 7, 6]])
    vlab = np.array(["Lleg", "Lleg", "Rleg", "Lleg", "Lleg", "Rleg", "torso", "torso"])
    return V, F, vlab


def test_a_leg_to_leg_join_near_the_feet_is_split_and_one_at_the_crotch_is_not():
    low = jn.find_joins(*_strip(0.05), hips_z=0.5)
    assert low["leg_edges"] == [(1, 5)] and low["leg_edge_z"][0] < 0.1
    high = jn.find_joins(*_strip(0.4), hips_z=0.5)  # at the crotch (80 percent of the hips' height): left whole
    assert high["leg_edges"] == []


def test_forearm_to_body_triangles_are_counted_per_side():
    V = np.array([[0, 0, 1.0], [1, 0, 1.0], [0, 1, 1.0], [2, 0, 1.0], [0, 0, 0.0]], dtype=float)
    F = np.array([[0, 1, 2], [1, 3, 2]])
    vlab = np.array(["Llowarm", "torso", "torso", "Rlowarm", "torso"])
    assert jn.find_joins(V, F, vlab, hips_z=0.5)["arm_joins"] == {"L": 1, "R": 1}


def test_the_old_joins_check_is_no_longer_made():
    # Replaced by limb_joins (vgac/limbjoins.py, tests/test_limbjoins.py): it counted forearm and hand triangles only.
    assert not hasattr(rig, "joins_check") and "joins" not in guidance.CHECK_GUIDE
    assert "limb_joins" not in guidance.CHECK_GUIDE  # carries its own words, like fingers
