"""WP-18: the numpy cores of the leg skin pass (blender/leg_skin.py) and the stretch measure (blender/skin_stretch.py).
These run without Blender; the Blender-driven checks at the end need VGAC_TEST_BLENDER."""
import os
import subprocess
from pathlib import Path

import numpy as np
import pytest

from vgac.blender import leg_skin as ls
from vgac.blender import skin_stretch as ss

NAMES = ["mixamorig:Hips", "mixamorig:LeftUpLeg", "mixamorig:LeftLeg", "mixamorig:LeftFoot",
         "mixamorig:RightUpLeg", "mixamorig:RightLeg", "mixamorig:RightFoot", "mixamorig:Spine"]


def test_leg_group_kinds():
    assert ls.leg_group_kind("mixamorig:LeftUpLeg") == ("thigh", "left")
    assert ls.leg_group_kind("mixamorig:RightLeg") == ("calf", "right")
    assert ls.leg_group_kind("mixamorig:LeftFoot") == ("foot", "left")
    assert ls.leg_group_kind("mixamorig:Spine") is None
    assert ls.leg_group_kind("mixamorig:LeftHand") is None


def test_cap_influences_keeps_four_and_normalises():
    rng = np.random.default_rng(3)
    W = rng.random((50, 9))
    W2, over = ls.cap_influences(W)
    assert over == 50
    assert ls.max_influences(W2) <= 4
    assert np.allclose(W2.sum(axis=1), 1.0)
    # The kept four are the largest ones.
    top = np.argsort(-W, axis=1)[:, :4]
    for r in range(50):
        assert set(np.where(W2[r] > 0)[0]) == set(top[r])


def test_smoothing_keeps_row_sums_and_leaves_zone_edge_alone():
    # A chain of 6 vertices; the first two are leg zone, the rest are not.
    edges = np.array([[i, i + 1] for i in range(5)])
    W = np.zeros((6, 2))
    W[:, 0] = [1, 1, 1, 0, 0, 0]
    W[:, 1] = [0, 0, 0, 1, 1, 1]
    zone = np.array([True, True, True, False, False, False])
    W2, n = ls.smooth_zone(W, edges, zone, passes=2, alpha=0.5)
    # Vertex 2 has a neighbour outside the zone, so it does not move.
    assert np.allclose(W2[2], W[2])
    assert n >= 1
    assert np.allclose(W2[:3].sum(axis=1), 1.0)


def test_narrow_knee_splits_by_height_and_conserves_leg_weight():
    z = np.array([0.80, 0.50, 0.49, 0.45, 0.30, 0.90])
    W = np.zeros((6, 2))
    W[:, 0] = 0.6  # thigh
    W[:, 1] = 0.4  # calf
    zone = np.array([True, True, True, True, True, False])
    W2, n = ls.narrow_knee(W, z, zone, [0], [1], knee_z=0.5, height=1.0, band=0.02, half=0.2)
    assert n == 4  # 0.80 is outside half * height (0.2), 0.90 is outside the zone
    assert np.allclose(W2[0], W[0])  # 0.80 is outside the near band: unchanged
    assert W2[1, 1] < W2[3, 1]  # the calf share rises going down the leg
    assert np.allclose(W2[:, 0] + W2[:, 1], W.sum(axis=1))
    assert np.allclose(W2[5], W[5])


def test_run_numpy_end_to_end_normalised_and_four_max():
    rng = np.random.default_rng(7)
    V, G = 120, len(NAMES)
    W = rng.random((V, G)) * (rng.random((V, G)) > 0.5)
    W[W.sum(axis=1) == 0, 0] = 1.0
    W /= W.sum(axis=1, keepdims=True)
    # A line of vertices joined to their neighbours.
    edges = np.array([[i, i + 1] for i in range(V - 1)])
    z = np.linspace(0.0, 1.8, V)
    out, rep = ls.run_numpy(W, edges, z, NAMES, knee_z=0.5, height=1.8)
    assert rep["max_influences_after"] <= 4
    assert rep["row_sum_error"] < 1e-9
    assert rep["zone_vertices"] > 0
    assert np.allclose(out.sum(axis=1), 1.0)


def test_run_numpy_without_a_leg_pair_only_smooths_and_caps():
    W = np.full((4, 2), 0.5)
    edges = np.array([[0, 1], [1, 2], [2, 3]])
    z = np.zeros(4)
    out, rep = ls.run_numpy(W, edges, z, ["mixamorig:Spine", "mixamorig:Head"], knee_z=None, height=1.0)
    assert rep["narrowed_vertices"] == 0
    assert rep["zone_vertices"] == 0
    assert np.allclose(out, W)


def test_stretch_edge_stats_threshold_and_regions():
    rest = np.array([[0.0, 0, 0], [1.0, 0, 0], [2.0, 0, 0], [0.0, 1, 0]])
    edges = np.array([[0, 1], [1, 2], [0, 3]])
    regions = ["legs", "hips", "arms"]
    # Frame 1 stretches edge 0 to 1.5 (over 1.3); frame 2 stretches edge 2 to 1.2 (under).
    f1 = rest.copy()
    f1[1] = [1.5, 0, 0]
    f2 = rest.copy()
    f2[3] = [0.0, 1.2, 0]
    stats = ss.edge_stats(rest, [f1, f2], edges, regions)
    assert stats["all"]["edges"] == 3
    assert stats["all"]["over_share"] == pytest.approx(1 / 3, abs=1e-3)
    assert stats["legs"]["over_share"] == 1.0
    assert stats["hips"]["over_share"] == 0.0  # edge 1 keeps its rest length in both frames
    assert stats["arms"]["over_share"] == 0.0  # edge 2 reaches 1.2, under 1.3


def test_stretch_skips_zero_length_edges_and_empty_input():
    rest = np.array([[0.0, 0, 0], [0.0, 0, 0]])
    stats = ss.edge_stats(rest, [rest], np.array([[0, 1]]), ["legs"])
    assert stats["all"]["edges"] == 0
    assert stats["all"]["over_share"] is None
    assert ss.edge_stats(rest, [], np.zeros((0, 2), dtype=int), [])["all"]["edges"] == 0


@pytest.mark.parametrize("bone,region", [
    ("mixamorig:Hips", "hips"), ("mixamorig:LeftUpLeg", "legs"), ("mixamorig:RightFoot", "legs"),
    ("mixamorig:LeftArm", "arms"), ("mixamorig:LeftHand", "arms"), ("mixamorig:Spine1", "torso"),
    ("mixamorig:Head", "torso"), ("Cape_03", "other"),
])
def test_region_of_bone(bone, region):
    assert ss.region_of_bone(bone) == region


def test_edge_region_priority():
    assert ss.edge_region("legs", "hips") == "hips"
    assert ss.edge_region("arms", "torso") == "arms"
    assert ss.edge_region("other", "legs") == "legs"


_BLENDER = os.environ.get("VGAC_TEST_BLENDER")


@pytest.mark.skipif(not _BLENDER, reason="needs VGAC_TEST_BLENDER (path to blender.exe) and a rigged GLB in VGAC_TEST_GLB")
def test_leg_skin_blender_keeps_weights_normalised(tmp_path):
    src = os.environ.get("VGAC_TEST_GLB")
    if not src or not Path(src).is_file():
        pytest.skip("VGAC_TEST_GLB not set")
    out = tmp_path / "leg_skin.glb"
    rep = tmp_path / "leg_skin.json"
    scripts = Path(ls.__file__).parent
    subprocess.run([_BLENDER, "-b", "--threads", "2", "--python", str(scripts / "leg_skin.py"), "--",
                    src, str(out), "--report", str(rep)], check=True, timeout=600)
    import json
    data = json.loads(rep.read_text(encoding="utf-8"))
    assert data["ok"]
    for m in data["meshes"]:
        assert m["max_influences_after"] <= 4
        assert m["row_sum_error"] < 1e-5
    assert out.is_file()


def test_smoothing_guards_keep_dominant_bone_and_limit_change():
    # Vertex 1 sits between a strong leg vertex (0) and a hips vertex (2): smoothing would hand it to the hips bone.
    edges = np.array([[0, 1], [1, 2]])
    W = np.array([[0.9, 0.1], [0.6, 0.4], [0.0, 1.0]])
    zone = np.array([True, True, False])
    W_guard, _ = ls.smooth_zone(W, edges, zone, passes=4, alpha=0.5, keep_dominant=True, max_change=0.0)
    assert np.argmax(W_guard[1]) == np.argmax(W[1])
    # With a tight change limit a row that moves a lot keeps its original weights.
    W_lim, _ = ls.smooth_zone(W, edges, zone, passes=4, alpha=0.5, keep_dominant=False, max_change=0.01)
    assert np.allclose(W_lim[1], W[1])
