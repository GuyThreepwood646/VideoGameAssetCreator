"""Photographic hair is judged tone by tone, and an eye is under hair only when something stands in front of it."""

from __future__ import annotations

import numpy as np

from vgac import face
from vgac.stages import face_detail as fd


def _two_tone(frac_grey: float, n=40000, seed=1):
    rng = np.random.default_rng(seed)
    img = np.zeros((200, 200, 3))
    grey = rng.random((200, 200)) < frac_grey
    noise = rng.normal(0, 0.03, (200, 200, 1))
    img[:] = np.array([0.45, 0.22, 0.12])  # auburn
    img[grey] = np.array([0.55, 0.55, 0.55])  # grey
    return np.clip(img + noise, 0, 1)


def test_the_same_two_tones_in_other_proportions_are_close_and_another_colour_is_far():
    mask = np.ones((200, 200), dtype=bool)
    a, b = _two_tone(0.25), _two_tone(0.45, seed=2)
    assert face.tone_distance(a, b, mask) < 6.0
    assert face.colour_distance(a, b, mask) > face.tone_distance(a, b, mask)  # the middle colour moves more
    blond = np.clip(a * 0.4 + np.array([0.6, 0.5, 0.2]) * 0.6, 0, 1)
    assert face.tone_distance(a, blond, mask) > 20.0
    assert face.tone_distance(a, b, np.zeros((200, 200), dtype=bool)) is None


def _m(**kw):
    base = {"likeness": 0.97, "feature_scores": [0.9], "feature_score_min": 0.9, "feature_move": 0.0, "skin_texture": 10.0,
            "eye_colour_shift": 0.0, "skin_shift": 0.0, "registered": "x", "hair_shift": 15.0}
    return {**base, **kw}


def test_photographic_hair_is_judged_by_its_tones_and_other_looks_by_the_middle_colour():
    assert fd.closeup_verdict(_m(hair_tone_shift=9.0), True) is None  # the bake-off's same-hair draws: median 12 to 18, tones 7 to 11
    assert fd.closeup_verdict(_m(hair_tone_shift=30.0), True) == "hair_colour"
    assert fd.closeup_verdict(_m(hair_tone_shift=9.0), False) == "hair_colour"  # a painted look: unchanged
    assert fd.closeup_verdict(_m(hair_shift=15.0), True) == "hair_colour"  # no tone measure (an older record): the old way
    assert fd.closeup_verdict(_m(hair_shift=4.0, hair_tone_shift=None), True) is None


def test_an_eye_is_under_hair_only_with_a_step_in_depth_in_its_box():
    e = {"painted": 0.0, "hair": 0.7, "side": "right"}
    assert fd.eye_state({**e, "step": 0.0003}) == "eye_missing"  # the shadowed cheek of the live M1-clothed
    assert fd.eye_state({**e, "step": 0.01}) == "eye_under_hair"  # a real flap
    assert fd.eye_state(e) == "eye_under_hair"  # measured before the step was recorded
    assert fd.eye_state({**e, "hair": 0.1, "step": 0.01}) == "eye_missing"
    assert fd.eyes_verdict([{**e, "step": 0.0003}])[0] == "eye_missing"


def test_depth_step_is_the_biggest_jump_between_neighbouring_pixels_in_the_box():
    z = np.zeros((50, 50))
    z[:, 25:] = 0.012
    mask = np.ones((50, 50), dtype=bool)
    box = np.zeros((50, 50), dtype=bool)
    box[10:20, 20:30] = True
    assert abs(fd.depth_step(z, mask, box) - 0.012) < 1e-6
    box[:] = False
    box[10:20, 2:10] = True
    assert fd.depth_step(z, mask, box) == 0.0
    assert fd.depth_step(None, None, box) is None
