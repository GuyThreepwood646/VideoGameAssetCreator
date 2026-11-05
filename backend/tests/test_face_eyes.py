"""Face detail pass, each eye (the second knight: her right eye stayed a dark smudge while the check passed).

The cause: the face region is the skin joined to the nose with its holes filled; her right eye's smudge ran into a lock
of hair, so it was no hole and the eye was left out of the face and never painted (face.close_eye_gaps puts it back).
The picture's eyes sat above the band the eye search looks in, so the eye-and-mouth fit failed (fit_picture now tries
it again after lining the picture up by the whole face). The check now measures each eye (eye_measures, eye_state)."""

from __future__ import annotations

import numpy as np

from vgac import face, guidance
from vgac.stages import face_detail as fd

SKIN, HAIR, SMUDGE = (0.85, 0.65, 0.55), (0.75, 0.2, 0.08), (0.2, 0.14, 0.12)


def _head(S: int = 160, smudge_under_hair: bool = False):
    """A front render: hair over the top, a lock hanging down the left of the forehead, two eye smudges. The left
    smudge (the character's right eye) touches the lock; with smudge_under_hair the lock covers that eye."""
    img = np.empty((S, S, 3))
    img[:] = SKIN
    img[:40] = HAIR
    img[:, :20] = HAIR
    img[:, 140:] = HAIR
    img[40:72, 36:52] = HAIR  # the lock, down to the eye
    img[72:80, 34:58] = SMUDGE  # the right eye, joined to the lock
    img[72:80, 96:120] = SMUDGE  # the left eye, inside the skin
    if smudge_under_hair:
        img[40:84, 30:62] = HAIR  # the lock covers the whole eye
    mask = np.ones((S, S), dtype=bool)
    return img, mask


def _region(img: np.ndarray, mask: np.ndarray, skin_lab: np.ndarray) -> np.ndarray:
    """find_face's region without the eye-gap step: the skin joined to the nose, its holes filled."""
    lab = face.to_lab(img)
    dist = np.linalg.norm((lab - skin_lab) * np.array([0.5, 1.0, 1.0]), axis=-1)
    seed = np.zeros_like(mask)
    seed[100, 80] = True
    region = face.grow(seed, mask & (dist < 16))
    return face.fill_holes(region) & mask


def _gaps(img, mask):
    skin_lab = face.to_lab(np.array([[SKIN]]))[0, 0]
    region = _region(img, mask, skin_lab)
    ys, xs = np.where(region)
    box = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
    nose = (80, 110)
    closed, added = face.close_eye_gaps(region, mask, face.to_lab(img), skin_lab, nose, box)
    return region, closed, added


def test_an_eye_smudge_joined_to_a_lock_of_hair_is_put_back_into_the_face():
    img, mask = _head()
    region, closed, added = _gaps(img, mask)
    assert region[76, 108] and not region[76, 45]  # the cause: the right eye was not in the face, the left one was
    assert closed[76, 45] and added > 0  # now it is
    assert not closed[60, 44] and not closed[20, 80]  # the lock and the hair above stay out


def test_hair_that_covers_the_eye_on_the_model_stays_out_of_the_face():
    img, mask = _head(smudge_under_hair=True)
    region, closed, _ = _gaps(img, mask)
    assert not closed[76, 45] and not closed[60, 44]
    assert closed[76, 108]


def test_without_a_hair_colour_the_region_is_left_as_it_was():
    img, mask = _head()
    img[img[..., 0] == HAIR[0]] = SKIN  # no hair at all
    region, closed, added = _gaps(img, mask)
    assert added == 0 and (closed == region).all()


def test_compose_alignment_is_the_two_moves_in_one():
    rng = np.random.default_rng(4)
    img = face.blur(rng.random((96, 96, 3)), 2.0)
    first = {"sx": 1.05, "sy": 0.98, "tx": -6.0, "ty": 9.0, "centre": (50.0, 44.0)}
    then = {"sx": 1.06, "sy": 1.06, "tx": 2.5, "ty": -3.0, "centre": (47.0, 52.0)}
    twice = face.apply_alignment(face.apply_alignment(img, first), then)
    once = face.apply_alignment(img, face.compose_alignment(first, then))
    inner = (slice(15, 80), slice(15, 80))
    assert np.abs(twice[inner] - once[inner]).max() < 0.03


def _face_picture(size: int = 256, up: int = 0) -> np.ndarray:
    """Skin with soft shading, two dark eyes and a mouth line; `up` moves the features up by that many pixels."""
    rng = np.random.default_rng(7)
    img = np.ones((size, size, 3)) * np.array(SKIN)
    Y, X = np.mgrid[0:size, 0:size]
    for _ in range(10):
        cx, cy, r, d = rng.uniform(60, 200), rng.uniform(60, 200), rng.uniform(8, 30), rng.uniform(-0.15, 0.15)
        img += d * np.exp(-((X - cx) ** 2 + (Y - cy - (-up)) ** 2) / (2 * r * r))[..., None]
    for ex in (97, 159):
        img[np.hypot(X - ex, Y - (107 - up)) < 9] = 0.1
    img[(np.abs(Y - (174 - up)) < 2) & (np.abs(X - 128) < 23)] = 0.3
    return np.clip(img, 0, 1)


def _info(size: int = 256) -> dict:
    region = np.zeros((size, size), dtype=bool)
    region[30:226, 40:216] = True
    return {"region": region, "nose": (128, 160), "width_px": 176.0, "box": (40, 30, 216, 226)}


def test_the_eye_and_mouth_fit_is_tried_again_after_lining_up_by_the_whole_face():
    render = _face_picture()
    src = _face_picture(up=22)  # the picture's eyes and mouth sit above the bands searched around the model's nose
    info = _info()
    inner = face.inner_face(info)
    lm = face.landmarks(src, info)
    assert lm is None or abs(lm["eyes"][0][1] - 85) > 8  # as framed, the picture's eye is not found
    a = fd.fit_picture(src, render, info, inner)
    assert a["how"] == "eyes and mouth" and a.get("after_whole_face")
    moved = face.landmarks(face.apply_alignment(src, a), info)
    assert abs(moved["eyes"][0][1] - 107) < 4 and abs(moved["eyes"][1][1] - 107) < 4


def _eyes_case(paint_right: bool = True, hair_on_right: bool = False, soft_left: bool = False):
    S = 256
    pic = _face_picture()
    after = pic.copy()
    weight = np.ones((S, S))
    hair = np.zeros((S, S), dtype=bool)
    box = (slice(92, 122), slice(75, 120))
    if not paint_right:
        after[box] = face.blur(pic, 6.0)[box]  # the 3D step's smudge
        weight[box] = 0.0
    if hair_on_right:
        hair[box] = True
    if soft_left:
        b2 = (slice(92, 122), slice(137, 182))
        after[b2] = face.blur(pic, 2.5)[b2]
    lm = {"eyes": [(97.0, 107.0), (159.0, 107.0)], "mouth": (128.0, 174.0)}
    return fd.eye_measures(pic, after, weight, hair, lm, _info())


def test_each_eye_is_measured_and_a_missing_eye_is_named():
    good = _eyes_case()
    assert [e["side"] for e in good] == ["right", "left"]
    assert all(fd.eye_state(e) is None for e in good)
    missing = _eyes_case(paint_right=False)
    assert fd.eye_state(missing[0]) == "eye_missing" and fd.eye_state(missing[1]) is None
    assert fd.eyes_verdict(missing) == ("eye_missing", ["right"])
    assert fd.eye_state(_eyes_case(paint_right=False, hair_on_right=True)[0]) == "eye_under_hair"
    assert fd.eye_state(_eyes_case(soft_left=True)[1]) == "eye_blurry"


def _check(eyes, **extra):
    fdm = {"ran": True, "sharpness_before": 0.55, "sharpness_after": 1.22, "feature_match_after": 0.77,
           "eye_colour_shift": 0.26, "eyes": eyes, **extra}
    return fd.face_checks({"face_detail": fdm})[0]


def test_the_check_fails_softly_when_an_eye_was_not_painted():
    ok_eye = {"side": "left", "painted": 1.0, "hair": 0.0, "match": 0.99, "sharp": 0.73}
    smudge = {"side": "right", "painted": 0.004, "hair": 0.0, "match": 0.19, "sharp": 0.25}  # the second knight
    c = _check([smudge, ok_eye])
    assert not c["ok"] and c["severity"] == "warn" and c["vars"]["reason"] == "eye_missing"
    assert "the right eye (on the viewer's left) was not" in c["help"] and "smudge" in c["help"]
    assert c["fix"]["stage"] == "prep" and c["fix_label"] == "Paint the face again"
    assert _check([{**smudge, "side": "right"}, {**smudge, "side": "left"}])["help"].startswith("The face was painted, but both eyes were not")


def test_an_eye_under_the_models_hair_says_painting_cannot_help_and_offers_a_new_3d_model():
    under = {"side": "left", "painted": 0.1, "hair": 0.7, "match": 0.2, "sharp": 0.3}
    c = _check([{"side": "right", "painted": 1.0, "hair": 0.0, "match": 0.98, "sharp": 0.7}, under])
    assert not c["ok"] and c["vars"]["reason"] == "eye_under_hair"
    assert "the left eye (on the viewer's right)" in c["help"] and "hair covers that eye" in c["help"]
    assert "cannot change that" in c["help"]
    assert c["fix"]["stage"] == "mesh" and c["fix_label"] == c["fix"]["label"] == "Build the 3D model again"


def test_a_blurry_eye_and_the_passing_cases():
    soft = {"side": "left", "painted": 1.0, "hair": 0.0, "match": 0.9, "sharp": 0.2}
    c = _check([{"side": "right", "painted": 1.0, "hair": 0.0, "match": 0.98, "sharp": 0.7}, soft])
    assert not c["ok"] and "the left eye (on the viewer's right) came out blurry" in c["help"]
    fine = _check([{"side": "right", "painted": 0.99, "hair": 0.0, "match": 0.94, "sharp": 0.58},
                   {"side": "left", "painted": 1.0, "hair": 0.0, "match": 1.0, "sharp": 0.75}])
    assert fine["ok"] and "{" not in fine["help"] and "eye_words" not in fine["vars"]
    assert _check(None)["ok"]  # versions from before the per-eye measure
    # a blurry face is still named first
    assert _check([soft], sharpness_after=0.7)["vars"]["reason"] == "blurry"


def test_every_face_reason_has_a_sentence():
    texts = guidance.CHECK_GUIDE["face_matches_picture"]["fail"]
    for reason in ("eye_missing", "eye_under_hair", "eye_blurry"):
        sentence = texts({"vars": {"reason": reason}})
        assert sentence != "The face does not match the picture yet." and "{eye_words}" in sentence
