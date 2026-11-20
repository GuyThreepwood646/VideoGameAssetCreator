"""WP-69: the numpy core of the stiff-part pass (blender/worn_region.py) and the stiff kind (declared_parts.py).
Runs without Blender."""
import numpy as np

from vgac import declared_parts as dp
from vgac.blender import worn_region as wr


def _chain(n):
    return np.array([[i, i + 1] for i in range(n - 1)], dtype=int)


def test_gltf_to_blender_swaps_up_axis():
    assert wr.gltf_to_blender([1.0, 2.0, 3.0]).tolist() == [1.0, -3.0, 2.0]


def _quad_pair(z_fold):
    # two triangles sharing an edge, first flat in XY, second folded by z_fold (a crease) about the shared edge
    co = np.array([[0.0, 0, 0], [1.0, 0, 0], [0.0, 1, 0], [1.0, 1, z_fold]])
    faces = np.array([[0, 1, 2], [1, 3, 2]])
    return co, faces


def test_grow_crosses_a_convex_ridge():
    # a convex ridge: the second face folds up (outward normals turn away), so growth continues across it
    co, faces = _quad_pair(-0.5)
    m = wr.grow_region(co, faces, [0.1, 0.1, 0.0], radius=5.0)
    assert m.all()


def test_grow_respects_radius_and_empty_when_far():
    co, faces = _quad_pair(-0.5)
    assert not wr.grow_region(co, faces, [9.0, 9.0, 9.0], radius=0.5).any()
    m = wr.grow_region(co, faces, [0.0, 0.0, 0.0], radius=0.6)
    assert m[0] and not m[3]


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
    rows = [{"name": "backpack", "kind": "stiff", "location": None, "locations": [[0.0, 1.1, -0.25], [0.1, 1.0, -0.2]]},
            {"name": "cape", "kind": "sway", "location": [0.0, 0.1, 1.0], "locations": []},
            {"name": "lid", "kind": "stiff", "location": None, "locations": []}]
    assert [r["name"] for r in dp.for_stiff(rows)] == ["backpack"]
    assert len(dp.for_stiff(rows)[0]["locations"]) == 2
    assert [r["name"] for r in dp.for_springs(rows)] == ["cape"]


def test_placed_stiff_takes_points_and_caps_them():
    declared = [{"name": "backpack", "kind": "stiff"}]
    rows = dp.placed([{"name": "backpack", "locations": [[0, 1, 0], [0.1, 1, 0]]}], declared, "humanoid")
    assert rows[0]["locations"] == [[0.0, 1.0, 0.0], [0.1, 1.0, 0.0]]
    too_many = [[0, 1, 0]] * (dp.MAX_POINTS + 1)
    try:
        dp.placed([{"name": "backpack", "locations": too_many}], declared, "humanoid")
        raise AssertionError("expected a PartError")
    except dp.PartError:
        pass
