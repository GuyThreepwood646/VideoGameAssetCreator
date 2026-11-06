"""The close-up guard, second version (vgac/facecloseup.py): features found by matching at the painting's resolution,
small moves put back by a local warp, identity still judged."""

from __future__ import annotations

import numpy as np
from PIL import Image

from vgac import face, facecloseup as fc
from vgac.stages import face_detail as fd

S = 512
SRC_PX = 96.0  # the painting's own pixels across the crop


def _face(seed: int, eyes=((0.38, 0.42), (0.62, 0.42)), mouth=0.68, eye_r=0.035) -> np.ndarray:
    """A face-like picture: skin, two dark eyes with a light iris ring, brows, a mouth line and random blobs that stand
    for its shading (a different seed: a different face with the eyes and mouth in the same places)."""
    rng = np.random.default_rng(seed)
    img = np.ones((S, S, 3)) * np.array([0.85, 0.7, 0.6])
    Y, X = np.mgrid[0:S, 0:S] / S
    for _ in range(14):
        cx, cy, r, d = rng.uniform(0.2, 0.8), rng.uniform(0.2, 0.8), rng.uniform(0.03, 0.12), rng.uniform(-0.25, 0.25)
        img += d * np.exp(-((X - cx) ** 2 + (Y - cy) ** 2) / (2 * r * r))[..., None]
    for ex, ey in eyes:
        img[np.hypot(X - ex, Y - ey) < eye_r] = 0.1
        img[(np.abs(Y - ey + 0.06) < 0.008) & (np.abs(X - ex) < 0.06)] = 0.35  # a brow
    img[(np.abs(Y - mouth) < 0.008) & (np.abs(X - 0.5) < 0.09)] = 0.3
    return np.clip(img, 0, 1)


def _painting(img: np.ndarray) -> np.ndarray:
    """The picture as the painting has it: SRC_PX pixels across, enlarged again."""
    small = face.resize(img, int(SRC_PX))
    return np.asarray(Image.fromarray(face.to_uint8(small)).resize((S, S), Image.BILINEAR), dtype=np.float64) / 255.0


def _scene(crop: np.ndarray) -> tuple[dict, dict]:
    """A front 'render' that is the crop itself, and the face found on it (nose in the middle, the face half the
    picture wide), as face.find_face would describe it."""
    region = np.zeros((S, S), dtype=bool)
    region[int(0.22 * S):int(0.84 * S), int(0.24 * S):int(0.76 * S)] = True
    info = {"found": True, "nose": (S // 2, int(0.55 * S)), "width_px": 0.5 * S, "region": region,
            "box": (int(0.24 * S), int(0.22 * S), int(0.76 * S), int(0.84 * S)), "skin": [75.0, 10.0, 15.0]}
    view = {"image": crop, "mask": np.ones((S, S), dtype=bool)}
    return view, info


def _guard(crop: np.ndarray, closeup: np.ndarray) -> dict:
    view, info = _scene(crop)
    out = fc.guard(view, info, crop, closeup, SRC_PX, fd.LANDMARK_RESIDUAL_MAX, fd.ALIGN_MIN)
    out["reason"] = fd.closeup_verdict(out)
    return out


def test_match_shifts_finds_where_a_feature_went():
    a = _face(1)
    b = face.scale_image(a, 1.0, 1.0, 9.0, -4.0, (S / 2, S / 2))  # everything 9 px right, 4 px up
    found = fc.match_shifts(_painting(a), b, np.array([[0.38 * S, 0.42 * S], [0.62 * S, 0.42 * S], [0.5 * S, 0.68 * S]]),
                            0.5 * S, SRC_PX)
    for f in found:
        assert abs(f["shift"][0] - 9.0) < 2.5 and abs(f["shift"][1] + 4.0) < 2.5
        assert f["score"] > 0.9


def test_the_warp_moves_a_feature_and_leaves_the_outline():
    src = np.array([[200.0, 215.0], [318.0, 215.0], [256.0, 350.0]])
    dst = np.array([[194.0, 215.0], [318.0, 215.0], [256.0, 350.0]])
    field = fc.warp_field((S, S), src, dst, 0.5 * S)
    assert np.allclose(field[215, 194], [6.0, 0.0], atol=0.6)  # the corrected picture reads the close-up's eye there
    assert np.allclose(field[215, 318], 0.0, atol=0.6)
    assert np.abs(field[:20]).max() < 1e-6 and np.abs(field[:, :20]).max() < 1e-6  # far away nothing moves


def test_a_close_up_with_its_eyes_drawn_a_little_wider_is_corrected_and_used():
    """The fresh knight's case: the same face, the eyes drawn a little apart. The first guard's darkest-spot measure
    calls it moved; the second finds the eyes by matching, puts them back and uses the picture."""
    truth = _face(1)
    crop = _painting(truth)
    wide = _face(1, eyes=((0.365, 0.42), (0.635, 0.42)))  # 0.015 of the picture = 0.03 of the face's width each side
    sharp = np.clip(wide + np.random.default_rng(5).normal(0, 0.004, wide.shape), 0, 1)
    m = _guard(crop, sharp)
    assert m["reason"] is None, m
    assert m["feature_move_raw"] > 0.02 and m["feature_move"] < m["feature_move_raw"]
    assert m["correction"] is not None and m["registered"] is not None
    assert min(m["feature_scores"]) >= fc.FEATURE_MIN


def test_a_close_up_already_in_place_is_left_as_drawn():
    truth = _face(1)
    sharp = np.clip(truth + np.random.default_rng(5).normal(0, 0.004, truth.shape), 0, 1)
    m = _guard(_painting(truth), sharp)
    assert m["reason"] is None and m["correction"] is None
    assert np.array_equal(m["registered"], sharp)


def test_a_different_face_is_still_rejected_even_with_the_same_eyes_and_mouth_places():
    """Other shading and other-sized eyes, at the same places (the real wrong faces are in runtime/tmp/sharpface: the
    knights' hair and framing kept and only the face changed scored likeness 0.88 to 0.90)."""
    crop = _painting(_face(1))
    for seed, eye_r in ((2, 0.025), (3, 0.045), (4, 0.025)):
        m = _guard(crop, _face(seed, eye_r=eye_r))
        assert m["reason"] in ("likeness", "features"), m
        assert m["likeness"] < fd.LIKENESS_MIN or m["feature_score_min"] < fc.FEATURE_MIN


def test_features_too_far_away_are_not_corrected():
    crop = _painting(_face(1))
    far = _face(1, eyes=((0.30, 0.42), (0.70, 0.42)))  # 0.16 of the face's width each side
    m = _guard(crop, far)
    assert m["correction"] is None and m["reason"] is not None


def test_the_verdict_knows_the_feature_scores():
    ok = {"likeness": 0.98, "feature_move": 0.01, "feature_score_min": 0.95, "eye_colour_shift": 3.0}
    assert fd.closeup_verdict(ok) is None
    assert fd.closeup_verdict({**ok, "feature_score_min": 0.8}) == "features"
    assert fd.closeup_verdict({**ok, "feature_score_min": None}) is None  # not measured is not a fault
    assert "features" in fd.CLOSEUP_REASONS


def test_a_close_up_turned_photographic_is_rejected_as_another_style():
    """The same face with pores and creases drawn all over the skin (what the editor does to the Ranger's painted
    look): the features are the same, the fine skin texture is not."""
    truth = _face(1)
    crop = _painting(truth)
    rng = np.random.default_rng(9)
    grain = face.blur(rng.normal(0, 1, (S, S)), 0.7)
    photo = np.clip(truth + 0.06 * grain[..., None], 0, 1)
    smooth = np.clip(truth + np.random.default_rng(5).normal(0, 0.004, truth.shape), 0, 1)
    view, info = _scene(crop)
    assert fc.skin_texture(smooth, crop, info) < fc.SKIN_TEXTURE_MAX < fc.skin_texture(photo, crop, info)
    m = _guard(crop, photo)
    assert m["reason"] == "style", m
    assert "style" in fd.CLOSEUP_REASONS


def test_grey_inside_the_face_is_not_background():
    """Only grey joined to the picture's edge is the painted head's background: a dark grey pupil or brow inside the
    face stays (the silver-haired mage's close-up had both filled in boxes when the model's nose was found too low)."""
    old = _face(1)
    old[: int(0.2 * S)] = [0.7, 0.2, 0.1]  # the model's hair above the face
    pic = old.copy()
    pic[:, :12] = 0.25  # background at the picture's edge
    pic[300:312, 150:162] = 0.25  # a grey spot on the cheek
    view, info = _scene(old)
    seg = face.hair_and_skin(old, pic, info, view["mask"])
    assert seg["pic_background"][:, :12].all()
    assert not seg["pic_background"][300:312, 150:162].any()
