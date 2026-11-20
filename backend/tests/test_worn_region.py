"""WP-69: the numpy core of the worn region pass (blender/worn_region.py). Runs without Blender."""
import numpy as np

from vgac.blender import worn_region as wr


def _chain(n):
    # a line of n vertices, edges between neighbours
    edges = np.array([[i, i + 1] for i in range(n - 1)], dtype=int)
    return edges


def test_region_mask_is_a_box():
    co = np.array([[0.0, 0.2, 1.0], [0.0, 0.0, 1.0], [0.5, 0.2, 1.0], [0.0, 0.2, 2.0]])
    m = wr.region_mask(co, 0.10, 0.42, (0.65, 1.60))
    assert m.tolist() == [True, False, False, False]


def test_ring_blend_fades_out_from_region():
    edges = _chain(6)
    region = np.array([True, True, False, False, False, False])
    t = wr.ring_blend(edges, region, seam=2)
    assert t[0] == 1.0 and t[1] == 1.0
    assert abs(t[2] - 2 / 3) < 1e-9
    assert abs(t[3] - 1 / 3) < 1e-9
    assert t[4] == 0.0 and t[5] == 0.0


def test_run_moves_region_to_one_bone_and_keeps_rows_normalised():
    names = ["mixamorig:Spine2", "mixamorig:Hips"]
    co = np.array([[0, 0.2, 1.0], [0, 0.0, 1.0], [0, -0.2, 1.0]])
    edges = _chain(3)
    W = np.array([[0.2, 0.8], [0.5, 0.5], [0.9, 0.1]])
    out, rep = wr.run_numpy(W, edges, co, names, "mixamorig:Spine2", 0.10, 0.42, (0.65, 1.60), seam=0, max_change=0)
    assert rep["ok"] is True
    assert out[0].tolist() == [1.0, 0.0]
    assert np.allclose(out[1], W[1])  # outside the region, no seam: unchanged
    assert np.allclose(out.sum(axis=1), 1.0)


def test_missing_bone_changes_nothing():
    names = ["mixamorig:Hips"]
    co = np.zeros((2, 3))
    W = np.ones((2, 1))
    out, rep = wr.run_numpy(W, _chain(2), co, names, "mixamorig:Spine2", 0.0, 1.0, (-1, 1))
    assert rep["ok"] is False
    assert np.array_equal(out, W)


def test_influences_capped_at_four():
    names = [f"b{i}" for i in range(6)] + ["mixamorig:Spine2"]
    rng = np.random.default_rng(1)
    W = rng.random((4, 7))
    W /= W.sum(axis=1, keepdims=True)
    co = np.array([[0, 0.2, 1.0], [0, 0.2, 1.0], [0, 0.0, 1.0], [0, 0.0, 1.0]])
    edges = _chain(4)
    out, rep = wr.run_numpy(W, edges, co, names, "mixamorig:Spine2", 0.10, 0.42, (0.65, 1.60), seam=2, max_change=0)
    assert ((out > 1e-6).sum(axis=1) <= 4).all()
    assert np.allclose(out.sum(axis=1), 1.0)
