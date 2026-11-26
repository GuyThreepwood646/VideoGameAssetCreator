"""Head spike check (vgac/spikecheck.py) on planted meshes: a flat face with and without a thin flap."""
import numpy as np

from vgac import quality, spikecheck


def _face(n=61, flap_mm=0.0):
    """A grid of points in x (right) and y (up) over a figure 1.75 high, the face looking along +z; one point near the left eye
    raised by flap_mm."""
    xs = np.linspace(-0.15, 0.15, n)
    ys = np.linspace(1.55, 1.75, n)
    g = np.array([(x, y, 0.0) for y in ys for x in xs])
    g = np.vstack([g, [[0.0, 0.0, 0.0], [0.0, 1.7, -0.1]]])  # feet and a back point give the figure its height
    eye = np.array([-0.03, 1.65, 0.0])
    if flap_mm:
        k = int(np.argmin(np.linalg.norm(g[:-2] - eye, axis=1)))
        g[k, 2] += flap_mm / 1000.0
    return g, [eye, np.array([0.03, 1.65, 0.0])]


def test_flat_face_passes():
    v, eyes = _face()
    r = spikecheck.protrusions(v, eyes, np.array([0, 0, 1.0]), 1.75)
    assert r["max_mm"] < 0.1 and r["flagged_verts"] == 0
    assert quality.eye_spike_check(r)["passed"]


def test_planted_flap_is_flagged():
    v, eyes = _face(flap_mm=6.0)
    r = spikecheck.protrusions(v, eyes, np.array([0, 0, 1.0]), 1.75)
    assert 5.0 < r["max_mm"] < 7.0 and r["flagged_verts"] >= 1
    c = quality.eye_spike_check(r)
    assert not c["passed"] and "Thin flaps" in c["help"]


def test_small_flap_under_the_limit_passes():
    v, eyes = _face(flap_mm=1.2)
    r = spikecheck.protrusions(v, eyes, np.array([0, 0, 1.0]), 1.75)
    assert quality.eye_spike_check(r)["passed"]


def test_flap_far_from_the_eyes_is_not_counted():
    v, eyes = _face()
    v[0, 2] += 0.02  # a corner of the grid, 20 cm from the eyes
    r = spikecheck.protrusions(v, eyes, np.array([0, 0, 1.0]), 1.75)
    assert r["max_mm"] < 0.1


def test_gentle_lid_fold_is_not_a_spike():
    v, eyes = _face()
    near = np.linalg.norm(v[:-2] - eyes[0], axis=1)
    v[:-2][:, 2] += 0.003 * np.exp(-(near / 0.02) ** 2)  # a 3 mm smooth bump 2 cm wide
    r = spikecheck.protrusions(v, eyes, np.array([0, 0, 1.0]), 1.75)
    assert r["max_mm"] < 2.0


def test_no_measure_makes_no_check():
    assert quality.eye_spike_check(None) is None
    assert quality.eye_spike_check({}) is None
