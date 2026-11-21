"""Shoulder skin pass (blender/shoulder_skin.py, WP-75). Pure numpy: no Blender needed."""
import numpy as np
import pytest

from vgac.blender import shoulder_skin as ss


def test_keep_share_fades_out_far_above_and_below_only_on_a_separated_side():
    h = [0.0, ss.FADE_FROM, (ss.FADE_FROM + ss.FADE_TO) / 2, ss.FADE_TO, 0.6, -ss.BELOW_FROM,
         -(ss.BELOW_FROM + ss.BELOW_TO) / 2, -ss.BELOW_TO, -0.5]
    assert list(ss.keep_share(h, below=True)) == pytest.approx([1.0, 1.0, 0.5, 0.0, 0.0, 1.0, 0.5, 0.0, 0.0])
    assert list(ss.keep_share(h)) == pytest.approx([1.0, 1.0, 0.5, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0])  # a joined arm


def test_height_is_measured_square_to_the_collarbone_line():
    # A collarbone from the neck (0, 0, 1) out to the shoulder (0.2, 0, 1.05), shoulder width 0.4.
    co = [[0.1, 0.0, 1.025], [0.1, 0.0, 1.025 + 0.12], [0.1, 0.05, 1.025]]
    h = ss.heights_above_line(co, (0, 0, 1.0), (0.2, 0, 1.05), 0.4)
    assert h[0] == pytest.approx(0.0, abs=1e-9)
    assert h[1] == pytest.approx(0.12 / 0.4 * (0.2 / np.hypot(0.2, 0.05)) ** 2, rel=1e-6)
    assert h[2] == pytest.approx(0.0, abs=1e-9)


def test_hand_over_moves_only_collarbone_weight_on_the_side_and_keeps_rows_whole():
    # columns: chest, collarbone, upper arm
    W = np.array([[0.2, 0.8, 0.0], [0.0, 0.6, 0.4], [0.1, 0.9, 0.0]])
    keep = np.array([1.0, 0.25, 0.0])
    side = np.array([1.0, 1.0, 0.0])  # the third vertex is on the other side
    W2, n = ss.hand_over(W, 1, 0, keep, side)
    assert n == 1
    assert W2[0] == pytest.approx(W[0])
    assert W2[1] == pytest.approx([0.45, 0.15, 0.4])
    assert W2[2] == pytest.approx(W[2])
    assert W2.sum(axis=1) == pytest.approx(W.sum(axis=1))
