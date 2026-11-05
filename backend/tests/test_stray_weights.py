"""Stray skin weights: a vertex tied to a bone across the body is released from it."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vgac" / "blender"))
import rig_fix  # noqa: E402

SEGS = {
    "RightHand": ((-0.5, 0.0, 1.0), (-0.6, 0.0, 0.9)),
    "RightForeArm": ((-0.3, 0.0, 1.2), (-0.5, 0.0, 1.0)),
    "RightToeBase": ((-0.1, -0.1, 0.02), (-0.1, -0.2, 0.0)),
    "Spine": ((0.0, 0.0, 1.1), (0.0, 0.0, 1.3)),
}


def test_a_fingertip_tied_to_a_toe_bone_is_released():
    tip = (-0.62, 0.0, 0.88)
    assert rig_fix.stray_influences(tip, {"RightHand": 0.8, "RightToeBase": 0.2}, SEGS, 1.8) == ["RightToeBase"]
    # The neighbouring forearm is a normal blend and stays.
    assert rig_fix.stray_influences(tip, {"RightHand": 0.7, "RightForeArm": 0.3}, SEGS, 1.8) == []


def test_a_far_bone_stays_when_nothing_nearer_holds_the_vertex():
    cloak = (0.0, -0.45, 0.6)  # a long cloak hem held only by the spine
    assert rig_fix.stray_influences(cloak, {"Spine": 1.0}, SEGS, 1.8) == []
    assert rig_fix.stray_influences(cloak, {"Spine": 0.6, "RightForeArm": 0.4}, SEGS, 1.8) == []


def test_unknown_bones_and_zero_weights_are_left_alone():
    tip = (-0.62, 0.0, 0.88)
    assert rig_fix.stray_influences(tip, {"RightHand": 1.0, "Mystery": 0.5, "RightToeBase": 0.0}, SEGS, 1.8) == []
