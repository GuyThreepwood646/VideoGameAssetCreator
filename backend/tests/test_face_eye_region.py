"""The face region keeps the dark eye sockets and brows inside it, and the picture's hair mask leaves out the eyes
MediaPipe finds (stored run M1 R4: the right eye was left out of the paint region and, once in, filled as hair)."""

from __future__ import annotations

import numpy as np

from vgac import face
from vgac.stages import face_detail as fd

S = 400
W = 200.0  # the face's width in the tests


def _face():
    region = np.zeros((S, S), dtype=bool)
    region[100:330, 100:300] = True  # a face 200 wide, nose tip at (200, 250)
    return region


def test_a_dark_socket_open_to_the_side_is_put_back_in_the_region():
    region = _face()
    region[170:190, 120:150] = False  # an eye socket 30 x 20 px (a closing of 12 px fills what is under 24) ...
    region[179:181, 100:120] = False  # ... joined to the edge by a thin dark channel, so fill_holes leaves it
    m = np.ones((S, S), dtype=bool)
    out, n = face.close_feature_holes(region, m, (200, 230), W)
    assert out[170:190, 120:150].all() and n >= 30 * 20


def test_a_gap_outside_the_eye_band_or_off_the_model_stays_out():
    region = _face()
    region[300:325, 150:250] = False  # under the nose: the mouth's business, not touched here
    m = np.ones((S, S), dtype=bool)
    m[170:200, 120:170] = False  # not on the model (a gap in the head itself)
    region[170:200, 120:170] = False
    out, _ = face.close_feature_holes(region, m, (200, 250), W)
    assert not out[300:325, 150:250].any() and not out[170:200, 120:170].any()


def test_a_piece_that_does_not_touch_the_face_is_not_added():
    region = _face()
    region[:, 100:] = False
    region[100:330, 150:300] = True
    region[:, :] = region
    m = np.ones((S, S), dtype=bool)
    out, _ = face.close_feature_holes(region, m, (200, 250), W)
    assert not out[170:200, 100:120].any()  # the empty strip left of the face is not a hole


def test_an_eye_found_by_mediapipe_is_not_picture_hair_but_one_found_by_the_dark_spots_is_unchanged(monkeypatch):
    hair = np.zeros((S, S), dtype=bool)
    hair[180:200, 150:190] = True  # dark eye and brow that joined the hair
    hair[20:60, 20:60] = True
    info = {"width_px": W}
    monkeypatch.setattr(fd.face, "landmarks", lambda img, info: {"how": "mediapipe", "eyes": [(170.0, 190.0), (260.0, 190.0)]})
    out = fd.pic_hair_without_eyes(hair, np.zeros((S, S, 3)), info, hair.shape)
    assert not out[180:200, 150:190].any() and out[20:60, 20:60].all()
    monkeypatch.setattr(fd.face, "landmarks", lambda img, info: {"how": "darkest", "eyes": [(170.0, 190.0), (260.0, 190.0)]})
    assert fd.pic_hair_without_eyes(hair, np.zeros((S, S, 3)), info, hair.shape).sum() == hair.sum()
    monkeypatch.setattr(fd.face, "landmarks", lambda img, info: None)
    assert fd.pic_hair_without_eyes(hair, np.zeros((S, S, 3)), info, hair.shape).sum() == hair.sum()


def test_a_gap_next_to_a_depth_step_a_flap_of_hair_is_left_out():
    region = _face()
    region[170:190, 120:150] = False
    region[179:181, 100:120] = False
    m = np.ones((S, S), dtype=bool)
    z = np.zeros((S, S))
    z[:, :135] = 0.01  # the hair stands 1 cm in front of the face, its edge through the gap
    out, n = face.close_feature_holes(region, m, (200, 230), W, z=z)
    assert not out[170:190, 120:150].any()
    out, n = face.close_feature_holes(region, m, (200, 230), W, z=np.zeros((S, S)))  # flat: a socket
    assert out[170:190, 120:150].all()
