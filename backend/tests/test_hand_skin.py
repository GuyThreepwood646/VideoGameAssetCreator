"""blender/hand_skin.py (PL-126): the hand skin pass, its numpy parts, and the rig step's call."""
from __future__ import annotations

import asyncio
import inspect
import json

import numpy as np
import pytest

from vgac.blender import hand_skin as hs
from vgac.stages import rig

NAMES = ["mixamorig:Hips", "mixamorig:LeftArm", "mixamorig:LeftForeArm", "mixamorig:Extra_ForeArmTwist_L1", "mixamorig:LeftHand",
         "mixamorig:LeftHandIndex1", "mixamorig:RightHand", "mixamorig:RightForeArm", "mixamorig:Spring_hair_1"]


def test_bones_are_sorted_by_role_and_side():
    assert [hs.bone_role(n) for n in NAMES] == [None, None, "fore", "fore", "hand", "finger", "hand", "fore", None]
    assert [hs.side_of(n) for n in NAMES[2:8]] == ["L", "L", "L", "L", "R", "R"]
    cols = hs.side_columns(NAMES, "L")
    assert cols == {"hand": 4, "finger": [5], "fore": [2, 3]}


def test_region_is_the_skin_near_the_hand_from_a_little_behind_the_wrist():
    P = np.array([[0, 0, 0.5], [0, 0, 0.64], [0, 0, 0.95], [0.5, 0, 0.5], [0, 0, 0.7], [0.05, 0, 0.5]])
    mask, a, hl = hs.hand_region(P, (0, 0, 0.6), (0, 0, 0.5), (0, 0, 0.9))
    assert mask.tolist() == [True, True, False, False, False, False]
    assert hl == pytest.approx(0.1)
    assert a[0] == pytest.approx(0.1) and a[1] == pytest.approx(-0.04)


def test_ramp_is_smooth_half_at_its_centre_and_whole_from_the_joint_on():
    s = hs.target_share(np.array([-0.1, -0.06, -0.03, 0.0, 0.1]), 0.1)
    assert s[0] == 0.0 and s[1] == 0.0 and s[2] == pytest.approx(0.5) and s[3] == 1.0 and s[4] == 1.0
    ramp = hs.target_share(np.linspace(-0.06, 0.0, 20), 0.1)
    assert np.all(np.diff(ramp) >= 0) and np.max(np.abs(np.diff(ramp))) < 0.2


def test_cloth_round_the_wrist_with_no_hand_weight_is_not_hand_skin():
    cols = hs.side_columns(NAMES, "L")
    W = np.zeros((3, len(NAMES)))
    W[0, [2, 4]] = [0.6, 0.4]
    W[1, [2, 4]] = [0.98, 0.02]  # cuff
    W[2, [2, 4]] = [0.5, 0.5]
    mask = np.array([True, True, False])
    assert hs.hand_skin_rows(W, mask, cols).tolist() == [0]


def test_skin_colour_decides_hand_skin_when_the_picture_is_known():
    cols = hs.side_columns(NAMES, "L")
    W = np.zeros((3, len(NAMES)))
    W[:, 2] = 1.0
    mask = np.array([True, True, True])
    skin = np.array([True, False, False])
    assert hs.hand_skin_rows(W, mask, cols, skin).tolist() == [0]
    colours = np.array([[0.9, 0.7, 0.6], [0.35, 0.3, 0.5], [0.6, 0.6, 0.2]])  # skin, purple cloth, olive trim
    assert hs.skin_coloured(colours).tolist() == [True, False, False]


def test_forearm_weight_moves_to_the_hand_bone_only_fingers_and_other_bones_stay_and_rows_stay_whole():
    cols = hs.side_columns(NAMES, "L")
    W = np.zeros((4, len(NAMES)))
    W[0, [2, 4]] = [0.7, 0.3]         # lump skin past the wrist: forearm 0.7 -> hand
    W[1, [2, 3, 5]] = [0.4, 0.2, 0.4]  # palm with finger weight: forearm and twist go to Hand, finger stays
    W[2, [1, 2]] = [0.5, 0.5]         # upper arm weight is never taken
    W[3, [2, 4]] = [0.7, 0.3]         # behind the wrist: target 0, nothing moves
    target = np.array([1.0, 1.0, 1.0, 0.0])
    W2, n = hs.move_to_hand(W, np.arange(4), target, cols)
    assert n == 3
    assert W2[0, 4] == pytest.approx(1.0) and W2[0, 2] == 0.0
    assert W2[1, 4] == pytest.approx(0.6) and W2[1, 5] == pytest.approx(0.4) and W2[1, 2] == 0.0 and W2[1, 3] == 0.0
    assert W2[2, 1] == pytest.approx(0.5) and W2[2, 4] == pytest.approx(0.5)
    assert np.allclose(W2[3], W[3]) and np.allclose(W2.sum(axis=1), W.sum(axis=1))
    assert np.allclose(W2[:, 5], W[:, 5])


def test_stats_and_influence_limit():
    cols = hs.side_columns(NAMES, "L")
    W = np.zeros((2, len(NAMES)))
    W[0, [2, 4]] = [0.6, 0.4]
    W[1, [2, 4]] = [0.1, 0.9]
    share, dominant = hs.hand_chain_stats(W, [0, 1], cols)
    assert share == pytest.approx(0.65) and dominant == pytest.approx(0.5)
    row = hs.limit_influences({0: 0.4, 1: 0.3, 2: 0.2, 3: 0.06, 4: 0.04})
    assert len(row) == 4 and sum(row.values()) == pytest.approx(1.0) and 4 not in row


def test_the_hand_pass_runs_after_the_palm_turn_before_the_sleeve_pass_and_the_clean_up_rerun_keeps_it():
    src = inspect.getsource(rig._rig)
    at = src.index("_hand_skin(sc, rig_glb)")
    assert src.index("_palm_down(sc, rig_glb, params)") < at < src.index("_sleeve_skin(sc, rig_glb)")
    again = inspect.getsource(rig._fix_weights_again)
    assert again.index("_hand_skin(sc, rig_glb)") < again.index("_sleeve_skin(sc, rig_glb)")


class _FakeSC:
    def __init__(self, tmp_path, report):
        self.dir, self.report = tmp_path, report
        self.metrics = {}
        self.settings = type("S", (), {"blender_timeout_seconds": 5})()

    async def blender(self, script, args, log_name=None, timeout=None):
        assert script == "hand_skin.py"
        args[1].write_bytes(b"NEW")
        args[args.index("--report") + 1].write_text(json.dumps(self.report), encoding="utf-8")

    def set_metrics(self, **kw):
        self.metrics.update(kw)


def _step(tmp_path, report):
    glb = tmp_path / "rig.glb"
    glb.write_bytes(b"OLD")
    sc = _FakeSC(tmp_path, report)
    asyncio.run(rig._hand_skin(sc, glb))
    return sc, glb


def test_a_changed_rig_replaces_the_old_one_and_the_old_one_is_kept(tmp_path):
    sc, glb = _step(tmp_path, {"ok": True, "changed": 1, "changed_vertices": 90, "sides": {"L": {"verts": 100}}})
    assert glb.read_bytes() == b"NEW" and (tmp_path / "rig_before_hand_skin.glb").read_bytes() == b"OLD"
    assert sc.metrics["hand_skin"] == {"ok": True, "changed": 1, "vertices": 90, "sides": {"L": {"verts": 100}}}


def test_nothing_changed_or_not_run_leaves_the_rig_as_it_was(tmp_path):
    sc, glb = _step(tmp_path, {"ok": True, "changed": 0, "changed_vertices": 0, "sides": {}})
    assert glb.read_bytes() == b"OLD" and not (tmp_path / "rig_before_hand_skin.glb").exists() and sc.metrics["hand_skin"]["ok"]
    sc2, glb2 = _step(tmp_path, {"ok": False, "reason": "no armature or no skinned mesh"})
    assert glb2.read_bytes() == b"OLD" and sc2.metrics["hand_skin"]["ok"] is False
