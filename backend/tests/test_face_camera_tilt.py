"""face.Camera with a tilt and an optional lens (WP-61 part B).

Tilt 0 with no lens must reproduce the orthographic camera exactly: the old formulas are written out here and
compared array-equal at several turns."""

from __future__ import annotations

import math

import numpy as np

from vgac import face


def _old_project(cam_target, yaw, scale, size, p):
    a = math.radians(yaw)
    back = np.array([math.sin(a), 0.0, math.cos(a)])
    right = np.array([math.cos(a), 0.0, -math.sin(a)])
    up = np.array([0.0, 1.0, 0.0])
    d = p - cam_target
    s = size / scale
    x = (d @ right) * s + size / 2
    y = -(d @ up) * s + size / 2
    return np.stack([x, y], axis=1), d @ -back


def _points(n=400, seed=3):
    rng = np.random.default_rng(seed)
    return rng.normal(size=(n, 3)) * 0.4 + np.array([0.0, 0.9, 0.0])


def test_tilt_zero_no_lens_equals_old_orthographic_projection():
    target = np.array([0.02, 1.1, -0.01])
    pts = _points()
    for yaw in (0.0, 10.0, -35.0, 90.0, 170.0):
        cam = face.Camera(target=target, yaw_deg=yaw, scale=0.5, size=256)
        new_xy, new_depth = cam.project(pts)
        old_xy, old_depth = _old_project(target, yaw, 0.5, 256, pts)
        assert np.array_equal(new_xy, old_xy), yaw
        assert np.array_equal(new_depth, old_depth), yaw


def test_tilt_zero_no_lens_cosine_equals_old_formula():
    target = np.zeros(3)
    cam = face.Camera(target=target, yaw_deg=25.0, scale=0.5, size=128)
    rng = np.random.default_rng(7)
    nrm = rng.normal(size=(300, 3))
    nrm /= np.linalg.norm(nrm, axis=1, keepdims=True)
    pos = rng.normal(size=(300, 3))
    assert np.array_equal(cam.cos_to_camera(nrm, pos), -(nrm @ cam.basis[2]))


def test_target_projects_to_picture_centre_with_lens_and_tilt():
    target = np.array([0.0, 1.0, 0.0])
    for tilt in (0.0, 30.0, 60.0):
        for lens in (85.0, 50.0, 35.0):
            cam = face.Camera(target=target, yaw_deg=20.0, scale=0.6, size=512, tilt_deg=tilt, lens_mm=lens)
            xy, depth = cam.project(target[None, :])
            assert np.allclose(xy[0], [256.0, 256.0], atol=1e-6)
            assert depth[0] > 0


def test_lens_divides_by_depth():
    # the same lateral offset, further from the eye, lands nearer the picture centre (points divide by depth)
    cam = face.Camera(target=np.zeros(3), yaw_deg=0.0, scale=1.0, size=512, lens_mm=35.0)
    near = np.array([[0.1, 0.0, 0.0]])
    far_pt = near + cam.basis[2] * 0.5  # forward is away from the eye
    x_near = cam.project(near)[0][0, 0] - 256.0
    x_far = cam.project(far_pt)[0][0, 0] - 256.0
    assert x_near > 0 and 0 < x_far < x_near


def test_lens_view_direction_differs_across_the_surface():
    cam = face.Camera(target=np.zeros(3), yaw_deg=0.0, scale=1.0, size=256, lens_mm=35.0)
    nrm = np.tile(np.array([[0.0, 0.0, 1.0]]), (2, 1))
    pos = np.array([[0.0, 0.0, 0.0], [0.5, 0.0, 0.0]])
    c = cam.cos_to_camera(nrm, pos)
    assert c[0] > c[1] > 0  # the surface point off-axis is seen at a more grazing angle


def test_tilt_moves_the_camera_above_the_target():
    cam = face.Camera(target=np.zeros(3), yaw_deg=0.0, scale=1.0, size=256, tilt_deg=45.0)
    right, up, fwd = cam.basis
    assert np.isclose(np.linalg.norm(right), 1.0) and np.isclose(np.dot(right, up), 0.0, atol=1e-12)
    assert -fwd[1] > 0.5  # the eye looks down from above
