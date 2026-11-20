"""WP-69: the numpy core of the stiff-part pass (blender/worn_region.py) and the stiff kind (declared_parts.py).
Runs without Blender."""
import numpy as np

from vgac import declared_parts as dp
from vgac.blender import worn_region as wr


def _chain(n):
    return np.array([[i, i + 1] for i in range(n - 1)], dtype=int)


def test_gltf_to_blender_swaps_up_axis():
    assert wr.gltf_to_blender([1.0, 2.0, 3.0]).tolist() == [1.0, -3.0, 2.0]


def test_grow_region_stops_at_radius_and_follows_edges():
    co = np.array([[0.0, 0, 0], [0.1, 0, 0], [0.2, 0, 0], [0.9, 0, 0], [0.1, 0.9, 0]])
    edges = _chain(4)  # 0-1-2-3, and vertex 4 is not connected
    m = wr.grow_region(co, edges, [0.0, 0.0, 0.0], radius=0.25)
    assert m.tolist() == [True, True, True, False, False]


def test_grow_region_empty_when_point_is_far():
    co = np.zeros((3, 3))
    assert not wr.grow_region(co, _chain(3), [5.0, 5.0, 5.0], radius=0.25).any()


def test_spine_bone_is_nearest_chain_bone():
    names = ["mixamorig:Hips", "mixamorig:Spine", "mixamorig:Spine2", "mixamorig:LeftArm"]
    heads = [[0, 0, 0.9], [0, 0, 1.0], [0, 0, 1.2], [0.3, 0, 1.2]]
    assert wr.spine_bone(names, heads, [0, 0, 1.15]) == "mixamorig:Spine2"
    assert wr.spine_bone(["mixamorig:Hips"], [[0, 0, 0]], [0, 0, 0]) is None


def test_run_moves_region_to_one_spine_bone_and_normalises():
    names = ["mixamorig:Spine2", "mixamorig:Hips"]
    heads = [[0, 0, 1.2], [0, 0, 0.9]]
    co = np.array([[0, 0.2, 1.0], [0, 0.0, 1.0], [0, -0.2, 1.0]])
    W = np.array([[0.2, 0.8], [0.5, 0.5], [0.9, 0.1]])
    region = np.array([True, False, False])
    out, rep = wr.run_numpy(W, _chain(3), co, names, heads, region, seam=0)
    assert rep["ok"] is True and rep["bone"] == "mixamorig:Spine2"
    assert out[0].tolist() == [1.0, 0.0]
    assert np.allclose(out[1], W[1])
    assert np.allclose(out.sum(axis=1), 1.0)


def test_run_reports_empty_region_and_leaves_weights():
    W = np.ones((2, 1))
    out, rep = wr.run_numpy(W, _chain(2), np.zeros((2, 3)), ["mixamorig:Spine"], [[0, 0, 1]], np.zeros(2, bool))
    assert rep["ok"] is False and np.array_equal(out, W)


def test_influences_capped_at_four():
    names = [f"b{i}" for i in range(6)] + ["mixamorig:Spine2"]
    heads = [[0, 0, 0.5]] * 6 + [[0, 0, 1.2]]
    rng = np.random.default_rng(1)
    W = rng.random((4, 7))
    W /= W.sum(axis=1, keepdims=True)
    co = np.array([[0, 0.2, 1.0], [0, 0.2, 1.0], [0, 0.0, 1.0], [0, 0.0, 1.0]])
    region = np.array([True, True, False, False])
    out, rep = wr.run_numpy(W, _chain(4), co, names, heads, region, seam=2)
    assert ((out > 1e-6).sum(axis=1) <= 4).all()
    assert np.allclose(out.sum(axis=1), 1.0)


def test_stiff_kind_is_allowed_for_humanoids_and_selects_for_rig():
    assert "stiff" in dp.KINDS
    assert dp.clean_kind("stiff", "humanoid") == "stiff"
    rows = [{"name": "backpack", "kind": "stiff", "location": [0.0, 0.1, 1.1]},
            {"name": "cape", "kind": "sway", "location": [0.0, 0.1, 1.0]},
            {"name": "lid", "kind": "stiff", "location": None}]
    assert [r["name"] for r in dp.for_stiff(rows)] == ["backpack"]
    assert [r["name"] for r in dp.for_springs(rows)] == ["cape"]
