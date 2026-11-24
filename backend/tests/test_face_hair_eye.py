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


def test_an_unpainted_eye_is_under_hair_by_its_hair_share_not_by_the_depth_step():
    e = {"painted": 0.0, "hair": 0.7, "side": "right"}
    assert fd.eye_state({**e, "step": 0.0003}) == "eye_under_hair"  # the hair left in the box after the eye release is real
    assert fd.eye_state({**e, "step": 0.01}) == "eye_under_hair"
    assert fd.eye_state(e) == "eye_under_hair"
    assert fd.eye_state({**e, "hair": 0.1, "step": 0.01}) == "eye_missing"
    assert fd.eye_state({**e, "painted": 0.6, "match": 0.9, "sharp": 0.9}) is None  # painted: not under hair whatever the share


def _eye_scene(flap_mm=0.0):
    S = 200
    z = np.zeros((S, S))
    if flap_mm:
        z[:, :80] = flap_mm / 1000.0  # a step in depth right through the eye box
    view = {"mask": np.ones((S, S), dtype=bool), "raster": {"z": z}}
    info = {"region": np.ones((S, S), dtype=bool), "nose": (100, 120), "width_px": 100.0}
    lm = {"how": "mediapipe", "eyes": [(80.0, 90.0), (120.0, 90.0)]}
    hair = np.zeros((S, S), dtype=bool)
    hair[80:100, 60:140] = True  # a colour-only hair mask over both eyes and the brow
    hair[5:20, 5:20] = True  # elsewhere
    return view, info, lm, hair


def test_an_eye_under_a_colour_only_hair_mask_with_no_depth_step_is_painted():
    view, info, lm, hair = _eye_scene()
    out, free = fd.release_eye_hair(hair, view, info, lm)
    for x, y in lm["eyes"]:
        box = fd.face.eye_boxes(hair.shape, [(x, y)], 100.0)
        assert not (out & box).any() and free[box].all()  # the eye is no longer hair and is paintable
    paint = (info["region"] | free) & ~fd.face.dilate_mask(out, 2)
    assert paint[90, 80] and paint[90, 120]


def test_a_real_flap_in_front_of_an_eye_keeps_its_hair():
    view, info, lm, hair = _eye_scene(flap_mm=25.0)
    out, free = fd.release_eye_hair(hair, view, info, lm)
    left = fd.face.eye_boxes(hair.shape, [lm["eyes"][0]], 100.0)
    right = fd.face.eye_boxes(hair.shape, [lm["eyes"][1]], 100.0)
    assert out[left].all() and not free[left].any()  # the 25 mm lock still stands: that eye stays under hair
    assert not out[right].any() and free[right].all()  # the eye with no flap by it is released


def test_a_lock_beside_the_eye_is_not_a_flap_in_front_of_it():
    view, info, lm, hair = _eye_scene()
    view["raster"]["z"][:, :50] = 0.03  # a step 30 px left of the left eye: inside the brow box, outside the eye's own box
    out, free = fd.release_eye_hair(hair, view, info, lm)
    left = fd.face.eye_boxes(hair.shape, [lm["eyes"][0]], 100.0)
    assert not out[left].any() and free[left].all()


def test_nothing_outside_the_eye_boxes_changes_and_eyes_not_from_mediapipe_prove_nothing():
    view, info, lm, hair = _eye_scene()
    out, free = fd.release_eye_hair(hair, view, info, lm)
    boxes = fd.face.eye_boxes(hair.shape, lm["eyes"], 100.0, grow=fd.EYE_NOT_HAIR_GROW)
    assert (out[~boxes] == hair[~boxes]).all() and not free[~boxes].any()
    out2, free2 = fd.release_eye_hair(hair, view, info, {"how": None, "eyes": lm["eyes"]})
    assert (out2 == hair).all() and not free2.any()
    assert fd.release_eye_hair(hair, view, info, None)[0] is hair


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
