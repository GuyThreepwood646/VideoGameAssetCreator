"""WP-66: the eyes and mouth on the model's render when the darkest-spot finder finds no band (vgac/face.py landmarks,
vgac/facemarks.py fallback). Synthetic pictures and stubs only: no library or owner picture is used here."""

from __future__ import annotations

import numpy as np

from vgac import face, facemarks, lipmarks

MP = {"eyes": [(400.0, 470.0), (640.0, 480.0)], "mouth": (512.0, 700.0), "how": "mediapipe", "finder": "local"}
DARK = {"eyes": [(405.0, 466.0), (636.0, 476.0)], "mouth": (510.0, 698.0)}


def _info(size: int = 1024, drop_left_eye_band: bool = False) -> dict:
    """A face info like face.find_face's: nose at (512, 560), width 500, a skin region over the face. With
    drop_left_eye_band the region leaves out the left half of the eye band (hair over the temple, as on the blacksmith)."""
    region = np.zeros((size, size), dtype=bool)
    region[300:900, 250:770] = True
    if drop_left_eye_band:
        region[300:560, 250:480] = False  # the whole left eye band (rows 380 to 530) is outside the region
    return {"nose": (512, 560), "width_px": 500.0, "region": region, "box": (250, 300, 770, 900)}


def test_fallback_is_used_only_where_the_darkest_spots_find_nothing(monkeypatch):
    img = np.zeros((1024, 1024, 3))
    monkeypatch.setattr(facemarks, "find", lambda i, info: None)  # no card or processor finder set
    monkeypatch.setattr(face, "darkest_landmarks", lambda i, info: DARK)
    monkeypatch.setattr(facemarks, "fallback", lambda i, info: (_ for _ in ()).throw(AssertionError("not asked")))
    assert face.landmarks(img, _info()) == DARK


def test_fallback_answers_when_the_darkest_spots_find_no_band(monkeypatch):
    img = np.zeros((1024, 1024, 3))
    monkeypatch.setattr(facemarks, "find", lambda i, info: None)
    monkeypatch.setattr(face, "darkest_landmarks", lambda i, info: None)
    monkeypatch.setattr(facemarks, "fallback", lambda i, info: MP)
    assert face.landmarks(img, _info()) == MP


def test_no_package_or_no_plausible_face_gives_nothing(monkeypatch):
    img = np.zeros((1024, 1024, 3))
    monkeypatch.setattr(facemarks, "find", lambda i, info: None)
    monkeypatch.setattr(face, "darkest_landmarks", lambda i, info: None)
    monkeypatch.setattr(facemarks, "local", lambda i: None)
    assert face.landmarks(img, _info()) is None
    # another face (eyes far too close, mouth above the nose) is not the model's and is not used
    bad = {**MP, "eyes": [(500.0, 470.0), (520.0, 470.0)]}
    monkeypatch.setattr(facemarks, "local", lambda i: bad)
    assert facemarks.fallback(img, _info()) is None


def test_a_missing_model_changes_nothing(monkeypatch):
    monkeypatch.setattr(lipmarks, "available", lambda: False)
    assert facemarks.fallback(np.zeros((8, 8, 3)), _info()) is None


def test_synthetic_case_where_the_darkest_spot_finder_has_no_left_eye_band(monkeypatch):
    """The blacksmith case in miniature: the region has no pixels in the left eye band, so darkest_landmarks is None
    for real (not stubbed), and the fallback answers with the MediaPipe eyes and mouth."""
    img = np.full((1024, 1024, 3), 0.6)
    info = _info(drop_left_eye_band=True)
    assert face.darkest_landmarks(img, info) is None
    monkeypatch.setattr(facemarks, "find", lambda i, info: None)
    monkeypatch.setattr(facemarks, "fallback", lambda i, info: MP)
    assert face.landmarks(img, info) == MP
    # the same picture with the full region is still read by the darkest spots (nothing changes for it)
    full = _info()
    monkeypatch.setattr(facemarks, "fallback", lambda i, info: (_ for _ in ()).throw(AssertionError("not asked")))
    assert face.darkest_landmarks(img, full) is not None
    assert face.landmarks(img, full) is not None
