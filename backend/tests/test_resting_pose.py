"""The Resting pose group on the rig step (WP-76): the settings, their checks, that a change redoes only the moves, and
the preview's state and picture plumbing. The Blender runs are not here (bakeoff/wp76 has the measured evidence)."""
from __future__ import annotations

import pytest

from vgac import guidance, reference
from vgac.blender import gait
from vgac.stages import rig
from vgac import pose_preview


def _rig_params() -> list[dict]:
    return reference.STAGE_SCHEMAS["rig"]


def test_hand_pose_choices_and_default():
    assert gait.hand_pose_choice(None) == "relaxed"
    assert gait.hand_pose_choice("") == "relaxed"
    for choice in ("open", "relaxed", "fist"):
        assert gait.hand_pose_choice(choice) == choice
    with pytest.raises(ValueError):
        gait.hand_pose_choice("claw")


def test_finger_curl_amount_range_and_default():
    assert gait.finger_curl_amount(None) == 1.0
    assert gait.finger_curl_amount("") == 1.0
    assert gait.finger_curl_amount(0) == 0.0
    assert gait.finger_curl_amount("1.5") == 1.5
    for bad in (-0.1, 1.6, "lots"):
        with pytest.raises(ValueError):
            gait.finger_curl_amount(bad)


def test_resting_pose_settings_are_on_the_rig_step_in_one_group():
    by_name = {p["name"]: p for p in _rig_params()}
    for name in ("arm_pose", "hand_pose", "finger_curl"):
        assert by_name[name]["group"] == "Resting pose"
        assert by_name[name]["categories"] == ["humanoid"]
    assert by_name["hand_pose"]["default"] == "relaxed"
    assert by_name["hand_pose"]["options"] == ["open", "relaxed", "fist"]
    assert by_name["finger_curl"]["default"] == 1.0
    assert by_name["finger_curl"]["min"] == 0 and by_name["finger_curl"]["max"] == 1.5


def test_guidance_covers_the_new_settings_in_plain_words():
    for name in ("hand_pose", "finger_curl"):
        entry = guidance.PARAM_HELP[name]
        assert entry["simple"] is True
        assert entry["help"]["meaning"]


def test_a_change_to_the_resting_pose_redoes_only_the_moves():
    for name in ("hand_pose", "finger_curl", "arm_pose"):
        assert name in rig.MOVES_PARAMS
    parent = {"hand_pose": "relaxed", "finger_curl": 1.0}
    assert rig.reuse_change(parent, {"hand_pose": "fist", "finger_curl": 1.0}) == {"moves"}
    assert rig.reuse_change(parent, {"hand_pose": "relaxed", "finger_curl": 0.5}) == {"moves"}


def test_rig_args_carry_the_hand_settings():
    assert rig.gait_hand_pose({}) == "relaxed"
    assert rig.gait_hand_pose({"hand_pose": "open"}) == "open"
    assert rig.gait_finger_curl({"finger_curl": 0.5}) == 0.5
    with pytest.raises(ValueError):
        rig.gait_hand_pose({"hand_pose": "paw"})


def test_preview_settings_are_checked_like_the_rig_step():
    assert pose_preview.settings_of({}) == {"arm_pose": "relaxed", "hand_pose": "relaxed", "finger_curl": 1.0}
    with pytest.raises(ValueError):
        pose_preview.settings_of({"finger_curl": 9})


def test_preview_state_is_public_without_the_file_path(tmp_path):
    assert pose_preview.public(None) == {"state": "none"}
    pose_preview.write_state(tmp_path, {"state": "done", "settings": {"hand_pose": "fist"}, "job_id": "j1"})
    out = pose_preview.public(pose_preview.current_state(tmp_path))
    assert out["state"] == "done" and out["picture"] is True and out["settings"] == {"hand_pose": "fist"}
    assert "reason" not in out
