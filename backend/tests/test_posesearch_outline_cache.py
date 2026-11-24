"""The pose search's outline cache belongs to one search call and one model: two models scored at the same pose with
separate caches get their own outlines, and a shared cache is read back only for the same pose and size."""
import numpy as np

from vgac import face, posesearch as ps, srcproject as sp


def _box(w: float) -> dict:
    v = np.array([[x, y, z] for x in (-w, w) for y in (-w, w) for z in (-1.0, 1.0)], dtype=np.float64)
    f = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1], [2, 3, 7], [2, 7, 6],
                  [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
    return {"verts": v, "faces": f}


def _target(n: int = 64) -> ps.Target:
    m = np.zeros((n, n), dtype=bool)
    m[16:48, 20:44] = True
    return ps.Target(n, m, ps._frame(m))


def test_separate_caches_give_each_model_its_own_outline():
    a, b = _box(0.3), _box(0.9)
    centre, scale, pose = np.zeros(3), 1.2, (0.0, 0.0, None)
    ca, cb = {}, {}
    pa = ps.score_pose(a, _target(), centre, scale, pose, cache=ca)
    pb = ps.score_pose(b, _target(), centre, scale, pose, cache=cb)
    (ma,), (mb,) = ca.values(), cb.values()
    assert not np.array_equal(ma, mb)  # each model's own drawing, not the other's at the same pose and scale
    assert ma.sum() != mb.sum()
    assert pa.iou == ps.score_pose(a, _target(), centre, scale, pose).iou
    assert pb.iou == ps.score_pose(b, _target(), centre, scale, pose).iou


def test_cache_is_read_back_for_the_same_pose_and_not_for_another():
    a = _box(0.5)
    centre, scale, cache = np.zeros(3), 1.2, {}
    t = _target()
    first = ps.score_pose(a, t, centre, scale, (0.0, 0.0, None), cache=cache)
    again = ps.score_pose(a, t, centre, scale, (0.0, 0.0, None), cache=cache)
    assert first.iou == again.iou and len(cache) == 1
    ps.score_pose(a, t, centre, scale, (30.0, 0.0, None), cache=cache)
    assert len(cache) == 2
