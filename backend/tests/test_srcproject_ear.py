"""vgac/srcproject.py, a three-quarter head picture's own ear moved onto the model's (ear_onto_model, EAR_MOVE_*), on
synthetic pictures with the finders stood in (no MediaPipe, no card)."""
from __future__ import annotations

import types

import numpy as np
import pytest

from vgac import face, srcproject


def pts(nose_x: float, ears: dict[str, tuple[float, float]]) -> dict[str, tuple[float, float, float]]:
    """Pose points: eyes at x 100 and 140, the nose at nose_x, ears as given (x, visibility)."""
    p = {"nose": (nose_x, 100.0, 1.0), "eye_a": (100.0, 90.0, 1.0), "eye_b": (140.0, 90.0, 1.0),
         "ear_L": (0.0, 100.0, 0.0), "ear_R": (0.0, 100.0, 0.0)}
    for n, (x, vis) in ears.items():
        p[n] = (x, 100.0, vis)
    return p


def test_the_near_ear_is_the_one_away_from_where_the_nose_points() -> None:
    # nose to the right of the eyes: the face looks right, its near ear is on the left
    assert srcproject.near_ear(pts(150.0, {"ear_L": (40.0, 0.9), "ear_R": (190.0, 0.9)})) == "ear_L"
    assert srcproject.near_ear(pts(90.0, {"ear_L": (40.0, 0.9), "ear_R": (190.0, 0.9)})) == "ear_R"
    # an ear the finder is unsure of does not count, and with none seen there is no near ear
    assert srcproject.near_ear(pts(150.0, {"ear_L": (40.0, 0.3), "ear_R": (190.0, 0.9)})) is None
    # both on the near side: the further out
    assert srcproject.near_ear(pts(150.0, {"ear_L": (40.0, 0.9), "ear_R": (70.0, 0.9)})) == "ear_L"


def test_ears_closer_than_the_least_move_are_left_alone() -> None:
    px_cm = 10.0
    mx, my, rec = srcproject.ear_move_maps(200, np.array([50.0, 100.0]), np.array([55.0, 100.0]),
                                           np.array([[150.0, 90.0]]), px_cm)
    assert mx is None and rec["moved"] is False and rec["offset_cm"] == pytest.approx(0.5)


def test_the_ear_lands_on_the_models_and_the_face_does_not_move() -> None:
    S, px_cm = 300, 10.0
    ear_from, ear_to = np.array([80.0, 150.0]), np.array([50.0, 150.0])  # 3 cm back
    pins = np.array([[200.0, 130.0], [240.0, 130.0], [220.0, 190.0]])  # eyes and mouth
    mx, my, rec = srcproject.ear_move_maps(S, ear_from, ear_to, pins, px_cm)
    assert rec["moved"] and rec["offset_cm"] == pytest.approx(3.0)
    # the pixel at the model's ear takes its colour from the picture's ear
    i, j = int(ear_to[1]), int(ear_to[0])
    assert (mx[i, j] + 0.5, my[i, j] + 0.5) == pytest.approx(tuple(ear_from), abs=0.6)
    # the eyes and mouth take their colour from where they are
    for x, y in pins.astype(int):
        assert (mx[y, x], my[y, x]) == pytest.approx((x, y), abs=1e-3)
    assert rec["stretch"] > 1.0 and rec["fade_cm"] == pytest.approx(srcproject.EAR_MOVE_FADE * np.hypot(150.0, 20.0) / px_cm, abs=0.01)


def test_no_move_when_the_face_is_as_close_as_the_ear() -> None:
    mx, _, rec = srcproject.ear_move_maps(200, np.array([80.0, 100.0]), np.array([50.0, 100.0]),
                                          np.array([[55.0, 100.0]]), 10.0)
    assert mx is None and rec["moved"] is False


def _view(S: int = 300) -> srcproject.View:
    v = srcproject.View("head_quarter", 1.0)
    v.cam = types.SimpleNamespace(size=S, scale=S / 1000.0)  # 10 pixels a centimetre
    v.raster = types.SimpleNamespace(mask=np.ones((S, S), bool))
    v.size = S
    img = np.full((S, S, 3), 0.6)
    yy, xx = np.mgrid[0:S, 0:S]
    img[np.hypot(xx - 80, yy - 150) < 4] = (0.1, 0.1, 0.1)  # the picture's ear: skin round a dark hollow
    v.pic_aligned = v.pic_affine = img
    v.alpha_img = np.ones((S, S))
    v.render = np.full((S, S, 3), 0.5)
    v.disc = np.zeros((S, S), bool)
    v.valid = np.ones((S, S), bool)
    return v


def test_the_pictures_ear_is_moved_onto_the_models(monkeypatch: pytest.MonkeyPatch) -> None:
    v = _view()
    picture_pts = pts(230.0, {"ear_L": (80.0, 0.95)})
    picture_pts["ear_L"] = (80.0, 150.0, 0.95)
    model_pts = dict(picture_pts, ear_L=(50.0, 150.0, 0.95))
    monkeypatch.setattr(srcproject, "pose_points", lambda img, mask: model_pts if img is v.render else picture_pts)
    monkeypatch.setattr(srcproject, "head_points", lambda img: np.array([[200.0, 130.0], [240.0, 130.0], [220.0, 190.0]]))
    rec = srcproject.ear_onto_model(v)
    assert rec["moved"] and rec["ear"] == "ear_L"
    assert v.pic_aligned[150, 50].max() < 0.2  # the dark ear now sits on the model's
    assert v.pic_aligned[150, 88].min() > 0.5  # and not where it was drawn (beyond its own width)
    assert v.pic_aligned[130, 220] == pytest.approx(np.full(3, 0.6))  # the face is untouched
    assert v.feather.shape == (300, 300)


def test_without_a_pose_finder_nothing_moves(monkeypatch: pytest.MonkeyPatch) -> None:
    v = _view()
    before = v.pic_aligned.copy()
    monkeypatch.setattr(srcproject, "pose_points", lambda img, mask: None)
    rec = srcproject.ear_onto_model(v)
    assert rec["moved"] is False
    assert np.array_equal(v.pic_aligned, before)


def test_the_model_ear_unseen_leaves_the_picture_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    v = _view()
    before = v.pic_aligned.copy()
    picture_pts = pts(230.0, {"ear_L": (80.0, 0.95)})
    model_pts = dict(picture_pts, ear_L=(50.0, 150.0, 0.2))
    monkeypatch.setattr(srcproject, "pose_points", lambda img, mask: model_pts if img is v.render else picture_pts)
    rec = srcproject.ear_onto_model(v)
    assert rec["moved"] is False and "model" in rec["why"]
    assert np.array_equal(v.pic_aligned, before)



def test_an_ear_under_hair_is_not_moved(monkeypatch: pytest.MonkeyPatch) -> None:
    v = _view()
    yy, xx = np.mgrid[0:300, 0:300]
    v.pic_aligned[np.hypot(xx - 80, yy - 150) < 25] = (0.45, 0.2, 0.05)  # hair over the ear's place
    before = v.pic_aligned.copy()
    picture_pts = pts(230.0, {"ear_L": (80.0, 1.0)})
    picture_pts["ear_L"] = (80.0, 150.0, 1.0)  # the finder still answers, with full visibility
    model_pts = dict(picture_pts, ear_L=(50.0, 150.0, 1.0))
    monkeypatch.setattr(srcproject, "pose_points", lambda img, mask: model_pts if img is v.render else picture_pts)
    monkeypatch.setattr(srcproject, "head_points", lambda img: np.array([[200.0, 130.0], [240.0, 130.0], [220.0, 190.0]]))
    rec = srcproject.ear_onto_model(v)
    assert rec["moved"] is False and rec["skin_share"]["picture"] < srcproject.EAR_SKIN_SHARE
    assert np.array_equal(v.pic_aligned, before)


def test_skin_share_is_measured_against_the_heads_own_skin() -> None:
    S, px_cm = 300, 10.0
    pins = np.array([[200.0, 130.0], [240.0, 130.0], [220.0, 190.0]])
    img = np.full((S, S, 3), 0.25)  # dark skin
    mask = np.ones((S, S), bool)
    assert srcproject.skin_share(img, mask, np.array([80.0, 150.0]), pins, px_cm) == 1.0
    img[100:200, 40:120] = (0.05, 0.03, 0.02)  # near-black hair at the ear's place
    assert srcproject.skin_share(img, mask, np.array([80.0, 150.0]), pins, px_cm) == 0.0
