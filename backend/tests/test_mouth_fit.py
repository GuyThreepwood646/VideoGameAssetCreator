"""The mouth corners in face.fit_landmarks (a sideways scale of its own) and face.add_mouth_corners (docs/MOUTH-PLAN.md phase 1)."""
from __future__ import annotations

import numpy as np

from vgac import face, lipmarks
from tests.test_lipmarks import mesh_points


def lm(eyes, mouth, corners=None):
    d = {"eyes": eyes, "mouth": mouth}
    if corners:
        d["mouth_corners"] = corners
    return d


def test_fit_without_corners_is_as_before_and_with_corners_scales_the_width_alone():
    src = lm([(40.0, 40.0), (80.0, 40.0)], (60.0, 80.0), [(45.0, 80.0), (75.0, 80.0)])
    dst = lm([(40.0, 40.0), (80.0, 40.0)], (60.0, 80.0), [(43.5, 80.0), (76.5, 80.0)])  # a mouth 1.1 wider, same line
    old = face.fit_landmarks(lm(src["eyes"], src["mouth"]), lm(dst["eyes"], dst["mouth"]), (60.0, 60.0))
    assert old["sx"] == old["sy"] == 1.0 and "corner_residual_px" not in old
    new = face.fit_landmarks(src, dst, (60.0, 60.0))
    assert abs(new["sx"] - 1.1) < 1e-3 and new["sy"] == 1.0 and new["corner_residual_px"] < 0.05
    # one side without corners: the old fit
    half = face.fit_landmarks(src, lm(dst["eyes"], dst["mouth"]), (60.0, 60.0))
    assert half["sx"] == half["sy"] and "corner_residual_px" not in half


def test_fit_keeps_the_sideways_scale_near_the_upright_one_and_refuses_nonsense():
    eyes, mouth = [(40.0, 40.0), (80.0, 40.0)], (60.0, 80.0)
    src = lm(eyes, mouth, [(40.0, 80.0), (80.0, 80.0)])
    wide = lm(eyes, mouth, [(36.4, 80.0), (83.6, 80.0)])  # 1.18 wide: held to 1.15 of the upright 1.0
    assert abs(face.fit_landmarks(src, wide, (60.0, 60.0))["sx"] - 1.15) < 1e-3
    absurd = lm(eyes, mouth, [(0.0, 80.0), (120.0, 80.0)])  # 3 times wider: not the same mouth, scale stays upright
    assert face.fit_landmarks(src, absurd, (60.0, 60.0))["sx"] == 1.0


def test_fit_shifts_sideways_by_the_mouth():
    src = lm([(40.0, 40.0), (80.0, 40.0)], (60.0, 80.0), [(45.0, 80.0), (75.0, 80.0)])
    dst = lm([(40.0, 40.0), (80.0, 40.0)], (66.0, 80.0), [(51.0, 80.0), (81.0, 80.0)])  # mouth 6 px to the right
    fit = face.fit_landmarks(src, dst, (60.0, 60.0))
    assert abs(fit["tx"] - 6.0) < 1e-6 and fit["corner_residual_px"] < 0.05


def test_add_mouth_corners_keeps_the_old_landmarks_when_nothing_is_found(monkeypatch):
    info = {"width_px": 300.0}
    base = {"eyes": [(1.0, 1.0), (2.0, 2.0)], "mouth": (350.0, 505.0), "how": "mediapipe"}
    monkeypatch.setattr(lipmarks, "available", lambda: False)
    assert face.add_mouth_corners(base, np.zeros((8, 8, 3)), info) is base
    monkeypatch.setattr(lipmarks, "available", lambda: True)
    monkeypatch.setattr(lipmarks, "find", lambda img: None)
    assert face.add_mouth_corners(base, np.zeros((8, 8, 3)), info) is base
    lp = lipmarks.from_mesh_points(mesh_points())
    monkeypatch.setattr(lipmarks, "find", lambda img: lp)
    out = face.add_mouth_corners(base, np.zeros((8, 8, 3)), info)
    assert out is not base and "mouth_corners" not in base  # the finder's cached dict is not touched
    assert out["mouth_corners"] == [lp["left"], lp["right"]]
    far = {**base, "mouth": (350.0, 700.0)}  # other lips than the ones found
    assert face.add_mouth_corners(far, np.zeros((8, 8, 3)), info) is far
