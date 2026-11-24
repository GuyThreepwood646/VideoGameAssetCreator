"""find_head's neck cap: a top band spread over HEAD_WIDTH_CAP_M is narrowed to a band within it."""
import numpy as np

from vgac import face


def _figure(arm_y=None, arm_x=0.5, arm_count=40, head_width=0.2):
    """A standing column 1.8 m tall: a body, a head of head_width at the top, and optionally an arm point pair at arm_y."""
    rng = np.random.default_rng(3)
    body = np.column_stack([rng.uniform(-0.1, 0.1, 400), rng.uniform(0.0, 1.5, 400), rng.uniform(-0.05, 0.05, 400)])
    head = np.column_stack([rng.uniform(-head_width / 2, head_width / 2, 300), rng.uniform(1.6, 1.8, 300),
                            rng.uniform(-0.1, 0.1, 300)])
    parts = [body, head]
    if arm_y is not None:
        side = np.linspace(0.0, 0.01, arm_count)
        parts.append(np.column_stack([np.full(arm_count, arm_x) + side, np.full(arm_count, arm_y), np.zeros(arm_count)]))
        parts.append(np.column_stack([np.full(arm_count, -arm_x) - side, np.full(arm_count, arm_y), np.zeros(arm_count)]))
    return np.vstack(parts)


def test_a_raised_arm_in_the_top_band_does_not_widen_the_head():
    verts = _figure(arm_y=1.58)  # inside the top 13 percent (from 1.566), outside the 11 percent band (from 1.602)
    top_band = face._head_band(verts, 0.13)
    assert top_band.width > face.HEAD_WIDTH_CAP_M
    head = face.find_head(verts)
    assert head.width <= face.HEAD_WIDTH_CAP_M
    assert abs(float(head.centre[0])) < 0.05  # the middle of the head, not of the arms


def test_a_normal_head_is_unchanged():
    verts = _figure()
    band = face._head_band(verts, 0.13)
    head = face.find_head(verts)
    assert band.width <= face.HEAD_WIDTH_CAP_M
    assert head.width == band.width
    assert np.allclose(head.centre, band.centre)


def test_when_no_narrower_band_is_within_the_cap_the_top_band_stays():
    verts = _figure(arm_y=1.79, arm_count=40)  # the arm sits in every band down to 5 percent
    head = face.find_head(verts)
    assert head.width == face._head_band(verts, 0.13).width
