"""blender/stretch_skin.py (PL-127): the stretch skin pass, its numpy parts, and the rig step's call."""
from __future__ import annotations

import asyncio
import inspect
import json

import numpy as np

from vgac.blender import stretch_skin as st
from vgac.stages import rig

NAMES = ["mixamorig:Hips", "mixamorig:Spine2", "mixamorig:Head", "mixamorig:LeftHand", "mixamorig:LeftHandIndex1",
         "mixamorig:Spring_hair_1"]


def _strip(n, gap=0.01):
    """n vertices on a line, `gap` metres apart, joined in a chain."""
    P = np.zeros((n, 3))
    P[:, 0] = np.arange(n) * gap
    edges = np.array([[i, i + 1] for i in range(n - 1)])
    return P, edges


def test_hand_and_finger_bones_are_found_and_spring_bones_are_not():
    assert st.hand_columns(NAMES) == [3, 4]


def test_gradient_is_weight_change_per_metre_with_a_floor_on_tiny_edges():
    P, edges = _strip(2, 0.01)
    W = np.array([[1.0, 0, 0, 0, 0, 0], [0.5, 0.5, 0, 0, 0, 0]])
    assert st.edge_gradient(W, edges, P)[0] == np.float64(1.0 / 0.01)
    P2, _ = _strip(2, 0.0001)
    assert st.edge_gradient(W, edges, P2)[0] == np.float64(1.0 / st.MIN_EDGE_M)


def test_a_steep_edge_is_softened_and_a_gentle_slope_is_not_touched():
    P, edges = _strip(6, 0.01)
    W = np.zeros((6, 6))
    W[:3, 1] = 1.0
    W[3:, 2] = 1.0  # a jump between 2 and 3: 200 per metre
    W2, n, before, after = st.smooth_steep(W, edges, P)
    assert before == 1 and n > 0
    assert st.edge_gradient(W2, edges, P).max() < st.edge_gradient(W, edges, P).max() / 2  # the jump is spread over several edges
    assert np.allclose(W2.sum(axis=1), 1.0)
    P3, edges3 = _strip(6, 1.0)  # same weights a metre apart: gradient 2 per metre
    W3, n3, _, _ = st.smooth_steep(W, edges3, P3)
    assert n3 == 0 and np.array_equal(W3, W)


def test_every_vertex_keeps_its_strongest_bone_and_moves_at_most_the_cap():
    rng = np.random.default_rng(3)
    P, edges = _strip(40, 0.005)
    W = rng.random((40, 6)) ** 3
    W /= W.sum(axis=1, keepdims=True)
    W2, _, _, _ = st.smooth_steep(W, edges, P)
    assert np.array_equal(np.argmax(W2, axis=1), np.argmax(W, axis=1))
    assert np.allclose(W2.sum(axis=1), 1.0) and np.all((W2 > 1e-9).sum(axis=1) <= st.MAX_INFLUENCES)


def test_hand_skin_is_never_changed_and_still_counts_as_a_neighbour():
    P, edges = _strip(4, 0.01)
    W = np.zeros((4, 6))
    W[:, 1] = 1.0
    W[3] = [0, 0, 0, 0.6, 0.4, 0]  # a hand vertex at the end
    frozen = st.frozen_rows(W, NAMES)
    assert frozen.tolist() == [False, False, False, True]
    W2, _, _, _ = st.smooth_steep(W, edges, P, frozen)
    assert np.array_equal(W2[3], W[3])


def test_rows_that_are_not_on_a_steep_edge_are_byte_equal():
    P, edges = _strip(8, 0.01)
    W = np.zeros((8, 6))
    W[:, 1] = 1.0
    W[7] = [0, 0.4, 0.6, 0, 0, 0]  # steep only at the end
    W2, _, _, _ = st.smooth_steep(W, edges, P)
    assert np.array_equal(W2[:2], W[:2])


def test_vertices_at_the_same_place_move_together_so_a_seam_does_not_tear():
    P, edges = _strip(6, 0.01)
    P = np.vstack([P, P[2:3]])  # vertex 6 is a seam copy of vertex 2, joined to vertex 3 only
    edges = np.vstack([edges, [[6, 3]]])
    W = np.zeros((7, 6))
    W[:3, 1] = 1.0
    W[3:6, 2] = 1.0
    W[6, 1] = 1.0
    W2, n, _, _ = st.smooth_steep(W, edges, P)
    assert n > 0 and np.array_equal(W2[2], W2[6])
    g = st.coincident_groups(P, W)
    assert g[2] == g[6] and len(set(g.tolist())) == 6
    W[6] = [0, 0.5, 0.5, 0, 0, 0]  # a copy that already carries other weights stays apart
    g2 = st.coincident_groups(P, W)
    assert g2[2] != g2[6]


LEG_NAMES = ["mixamorig:Hips", "mixamorig:LeftUpLeg", "mixamorig:LeftLeg", "mixamorig:RightUpLeg", "mixamorig:LeftForeArm",
             "mixamorig:Spine"]


def test_leg_and_arm_chains_are_found():
    assert st.leg_columns(LEG_NAMES) == ([1, 2], [3]) and st.arm_columns(LEG_NAMES) == [4]


def test_the_split_between_the_legs_never_changes_and_a_row_without_leg_weight_gets_none():
    legs = st.leg_columns(LEG_NAMES)
    orig = np.array([[0.2, 0.8, 0, 0, 0, 0], [0.4, 0.3, 0, 0.3, 0, 0], [1.0, 0, 0, 0, 0, 0]])
    new = np.array([[0.2, 0.4, 0, 0.4, 0, 0], [0.2, 0.1, 0.2, 0.5, 0, 0], [0.6, 0.2, 0, 0.2, 0, 0]])
    out = st.lock_leg_split(new, orig, legs)
    assert np.allclose(out.sum(axis=1), 1.0)
    assert out[0, 3] == 0.0 and np.isclose(out[0, 1], 0.8)
    assert np.isclose(out[1, [1, 2]].sum(), out[1, 3])
    assert np.allclose(out[2], [1, 0, 0, 0, 0, 0])


def test_leg_skin_gains_no_arm_weight_and_other_skin_may():
    legs, arms = st.leg_columns(LEG_NAMES), st.arm_columns(LEG_NAMES)
    orig = np.array([[0.2, 0.8, 0, 0, 0, 0], [0.8, 0.2, 0, 0, 0, 0]])
    new = np.array([[0.1, 0.6, 0, 0, 0.3, 0], [0.5, 0.2, 0, 0, 0.3, 0]])
    out = st.cap_arm_gain(new, orig, legs, arms)
    assert out[0, 4] == 0.0 and np.isclose(out[0].sum(), 1.0)
    assert np.array_equal(out[1], new[1])


def test_the_pass_runs_after_the_sleeve_pass_and_the_clean_up_rerun_keeps_it():
    src = inspect.getsource(rig._rig)
    assert src.index("_sleeve_skin(sc, rig_glb)") < src.index("_stretch_skin(sc, rig_glb)")
    again = inspect.getsource(rig._fix_weights_again)
    assert again.index("_sleeve_skin(sc, rig_glb)") < again.index("_stretch_skin(sc, rig_glb)")


class _FakeSC:
    def __init__(self, tmp_path, report):
        self.dir, self.report = tmp_path, report
        self.metrics = {}
        self.settings = type("S", (), {"blender_timeout_seconds": 5})()

    async def blender(self, script, args, log_name=None, timeout=None):
        assert script == "stretch_skin.py"
        args[1].write_bytes(b"NEW")
        args[args.index("--report") + 1].write_text(json.dumps(self.report), encoding="utf-8")

    def set_metrics(self, **kw):
        self.metrics.update(kw)


def _step(tmp_path, report):
    glb = tmp_path / "rig.glb"
    glb.write_bytes(b"OLD")
    sc = _FakeSC(tmp_path, report)
    asyncio.run(rig._stretch_skin(sc, glb))
    return sc, glb


def test_a_changed_rig_replaces_the_old_one_and_the_old_one_is_kept(tmp_path):
    sc, glb = _step(tmp_path, {"ok": True, "changed": 1, "vertices": 90, "steep_edges_before": 500, "steep_edges_after": 120})
    assert glb.read_bytes() == b"NEW" and (tmp_path / "rig_before_stretch_skin.glb").read_bytes() == b"OLD"
    assert sc.metrics["stretch_skin"] == {"ok": True, "changed": 1, "vertices": 90, "steep_before": 500, "steep_after": 120}


def test_nothing_changed_or_not_run_leaves_the_rig_as_it_was(tmp_path):
    sc, glb = _step(tmp_path, {"ok": True, "changed": 0, "vertices": 0, "steep_edges_before": 0, "steep_edges_after": 0})
    assert glb.read_bytes() == b"OLD" and not (tmp_path / "rig_before_stretch_skin.glb").exists() and sc.metrics["stretch_skin"]["ok"]
    sc2, glb2 = _step(tmp_path, {"ok": False, "reason": "no armature or no skinned mesh"})
    assert glb2.read_bytes() == b"OLD" and sc2.metrics["stretch_skin"]["ok"] is False
