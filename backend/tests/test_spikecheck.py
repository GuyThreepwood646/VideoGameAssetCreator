"""Head spike check (vgac/spikecheck.py) on made-up faces: a smooth face with eyes, nose and brow, with and without thin
flaps. Metres on a figure 1.75 high, x across, y up, the face looking along +z."""
import numpy as np
import pytest

from vgac import quality, spikecheck

EYE_Y = 1.65
EYE_X = 0.032
STEP = 0.002


def _surface(x, y, brow_m=0.0):
    ax = np.abs(x)
    z = -0.02 * (x / 0.1) ** 2  # the face curves back toward the ears
    z = z - 0.008 * np.exp(-(((ax - EYE_X) / 0.014) ** 2 + ((y - EYE_Y) / 0.010) ** 2))  # eye sockets
    z = z + 0.004 * np.exp(-(((ax - EYE_X) / 0.008) ** 2 + ((y - EYE_Y) / 0.006) ** 2))  # the eyes in them
    z = z + 0.022 * np.exp(-((x / 0.008) ** 2 + ((y - EYE_Y + 0.025) / 0.020) ** 2))  # nose
    return z + brow_m / (1 + np.exp(-(y - EYE_Y - 0.013) / 0.0015))  # a shelf above the eyes


def _head(brow_m=0.0):
    xs = np.arange(-0.09, 0.09 + 1e-9, STEP)
    ys = np.arange(EYE_Y - 0.07, EYE_Y + 0.07 + 1e-9, STEP)
    gx, gy = np.meshgrid(xs, ys)
    verts = np.stack([gx, gy, _surface(gx, gy, brow_m)], -1).reshape(-1, 3)
    n = len(xs)
    faces = []
    for r in range(len(ys) - 1):
        for c in range(n - 1):
            a = r * n + c
            faces += [(a, a + 1, a + n + 1), (a, a + n + 1, a + n)]
    eyes = [np.array([s * EYE_X, EYE_Y, float(_surface(s * EYE_X, EYE_Y, brow_m))]) for s in (-1, 1)]
    return verts, np.array(faces), eyes


def _slab(head, x, y, height_mm, width=0.016, thick=0.0016, base_z=None):
    """The head with a thin sheet added: `width` across, `thick` high, standing `height_mm` out of the surface at (x, y)."""
    verts, faces, eyes = head
    base = float(_surface(x, y)) - 0.001 if base_z is None else base_z
    tip = (float(_surface(x, y)) if base_z is None else base_z) + height_mm / 1000.0
    corners = np.array([(x + sx * width / 2, y + sy * thick / 2, z) for z in (base, tip) for sy in (-1, 1) for sx in (-1, 1)])
    quads = [(4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)]
    n = len(verts)
    extra = [(n + a, n + b, n + c) for q in quads for a, b, c in ((q[0], q[1], q[2]), (q[0], q[2], q[3]))]
    return np.vstack([verts, corners]), np.vstack([faces, extra]), eyes


def _measure(head, yaw=0.0, height=1.75):
    verts, faces, eyes = head
    return spikecheck.protrusions(verts, faces, eyes, yaw, height)


def test_clean_face_passes():
    r = _measure(_head())
    assert r["max_mm"] < 1.0 and quality.eye_spike_check(r)["passed"]


def test_heavy_brow_is_not_a_flap():
    r = _measure(_head(brow_m=0.014))
    assert r["max_mm"] < spikecheck.LIMIT_MM


@pytest.mark.parametrize("planted", [3.0, 6.0])
def test_thin_flap_on_a_lid_is_found(planted):
    r = _measure(_slab(_head(), -EYE_X, EYE_Y + 0.007, planted))
    assert abs(r["max_mm"] - planted) < 1.0
    c = quality.eye_spike_check(r)
    assert not c["passed"] and "Thin flaps" in c["help"]


def test_flap_under_the_limit_passes():
    r = _measure(_slab(_head(), -EYE_X, EYE_Y + 0.007, 1.0))
    assert quality.eye_spike_check(r)["passed"]


def test_flap_on_the_side_of_the_nose_is_not_counted():
    r = _measure(_slab(_head(), -0.006, EYE_Y, 6.0, width=0.006))
    assert r["max_mm"] < spikecheck.LIMIT_MM


@pytest.mark.parametrize("ahead_m", [0.018, 0.03])
def test_strand_hanging_in_front_of_an_eye_is_not_counted(ahead_m):
    head = _head()
    r = _measure(_slab(head, -EYE_X, EYE_Y, 2.0, width=0.003, thick=0.02, base_z=float(head[2][0][2]) + ahead_m))
    assert r["max_mm"] < spikecheck.LIMIT_MM


def test_flap_is_found_when_the_eye_points_lie_deep():
    """A head with holes for eyes: the points the finder lifts onto the surface are far behind the lids."""
    verts, faces, eyes = _slab(_head(), -EYE_X, EYE_Y + 0.007, 6.0)
    r = spikecheck.protrusions(verts, faces, [e - np.array([0.0, 0.0, 0.018]) for e in eyes], 0.0, 1.75)
    assert r["max_mm"] > spikecheck.LIMIT_MM


def test_turned_and_resized_model_reads_the_same():
    verts, faces, eyes = _slab(_head(), -EYE_X, EYE_Y + 0.007, 6.0)
    front = spikecheck.protrusions(verts, faces, eyes, 0.0, 1.75)["max_mm"]
    turn = np.array([[0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0]])  # a quarter turn about the up axis
    side = spikecheck.protrusions(verts @ turn.T * 0.5, faces, [turn @ e * 0.5 for e in eyes], 90.0, 0.875)["max_mm"]
    assert abs(side - front) < 0.5


def test_eyes_off_the_model_give_no_measure():
    verts, faces, _ = _head()
    away = [np.array([-EYE_X, 0.4, 0.0]), np.array([EYE_X, 0.4, 0.0])]
    assert spikecheck.protrusions(verts, faces, away, 0.0, 1.75) is None


def test_no_measure_makes_no_check():
    assert quality.eye_spike_check(None) is None
    assert quality.eye_spike_check({}) is None
