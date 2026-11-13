"""face.find_face_checked: the nose tip the shape gave is checked against MediaPipe's eyes and mouth read off the head's
shape, and moved onto the face when it is not between them (stored runs W2-clothed, W2-underwear, P1-photo). The finders
are faked; the logic and its fall-backs are what is tested."""

from __future__ import annotations

import numpy as np

from vgac import face

S = 200
MARKS = {"eyes": [(70.0, 70.0), (130.0, 70.0)], "mouth": (100.0, 130.0)}  # eyes 60 apart: a face about 133 px wide


def _view(z_nose=(100, 112)):
    z = np.full((S, S), 5.0)
    z[z_nose[1] - 2: z_nose[1] + 3, z_nose[0] - 2: z_nose[0] + 3] = 1.0  # the nearest point near the 60 percent mark
    return {"mask": np.ones((S, S), dtype=bool), "raster": {"z": z}, "facing": np.ones((S, S)), "image": np.zeros((S, S, 3))}


class _Cam:
    size = S


def _fake(monkeypatch, marks, first_nose, again_found=True):
    calls = []

    def find_face(view, mesh, cam, head, skin_tolerance=16.0, nose=None):
        calls.append(nose)
        n = nose or first_nose
        return {"found": again_found or nose is None, "nose": n, "width_px": 40.0, "reason": None}

    monkeypatch.setattr(face, "find_face", find_face)
    monkeypatch.setattr(face, "shape_marks", lambda view: marks)
    return calls


def test_a_nose_between_the_eyes_and_mouth_stands(monkeypatch):
    calls = _fake(monkeypatch, MARKS, (100, 108))
    info = face.find_face_checked(_view(), {}, _Cam(), None)
    assert info["nose"] == (100, 108) and "nose_from" not in info and calls == [None]


def test_a_nose_on_the_chin_is_moved_to_the_face_and_both_are_kept(monkeypatch):
    calls = _fake(monkeypatch, MARKS, (100, 180))  # far below the mouth
    info = face.find_face_checked(_view(), {}, _Cam(), None)
    assert info["nose_from"] == "mediapipe" and info["nose_by_shape"] == (100, 180)
    assert abs(info["nose"][0] - 100) <= 6 and abs(info["nose"][1] - 112) <= 6  # the nearest point around the 60 percent mark
    assert len(calls) == 2


def test_a_nose_off_to_the_side_on_curls_is_moved(monkeypatch):
    _fake(monkeypatch, MARKS, (20, 60))  # beside the eyes
    info = face.find_face_checked(_view(), {}, _Cam(), None)
    assert info["nose_from"] == "mediapipe"


def test_the_width_comes_from_the_eyes_so_a_sliver_of_skin_does_not_decide(monkeypatch):
    # the grown skin region was 40 px wide (width_px above) yet the nose is right: it must stand
    _fake(monkeypatch, MARKS, (100, 112))
    assert "nose_from" not in face.find_face_checked(_view(), {}, _Cam(), None)


def test_without_a_finder_or_a_face_the_first_finding_stands(monkeypatch):
    _fake(monkeypatch, None, (100, 180))
    info = face.find_face_checked(_view(), {}, _Cam(), None)
    assert info["nose"] == (100, 180) and "nose_from" not in info


def test_a_corrected_nose_that_does_not_hold_either_is_not_used(monkeypatch):
    marks = {"eyes": [(70.0, 70.0), (130.0, 70.0)], "mouth": (100.0, 130.0)}
    calls = _fake(monkeypatch, marks, (100, 180))
    # the nearest point around the 60 percent mark is far to the side: not between the eyes
    view = _view(z_nose=(100, 112))
    view["raster"]["z"][:] = 5.0
    view["raster"]["z"][108:117, 112:121] = 1.0  # still inside the search window but...
    real = face.find_face

    def find_face(view, mesh, cam, head, skin_tolerance=16.0, nose=None):
        out = real(view, mesh, cam, head, skin_tolerance, nose)
        if nose is not None:
            out["nose"] = (100, 190)  # the re-found face says its nose is on the chin again
        return out

    monkeypatch.setattr(face, "find_face", find_face)
    info = face.find_face_checked(view, {}, _Cam(), None)
    assert info["nose"] == (100, 180) and "nose_from" not in info
    assert len(calls) == 2


def test_a_failing_finder_changes_nothing(monkeypatch):
    _fake(monkeypatch, MARKS, (100, 180))

    def boom(view):
        raise RuntimeError("model missing")

    monkeypatch.setattr(face, "shape_marks", boom)
    info = face.find_face_checked(_view(), {}, _Cam(), None)
    assert info["nose"] == (100, 180)


def test_the_finder_is_asked_once_per_head(monkeypatch):
    from vgac import lipmarks, facemarks

    asked = []
    monkeypatch.setattr(lipmarks, "available", lambda: True)
    monkeypatch.setattr(facemarks, "local", lambda img: asked.append(1) or MARKS)
    face._SHAPE_MARKS.clear()
    v = _view()
    assert face.shape_marks(v) == MARKS and face.shape_marks(v) == MARKS
    assert len(asked) == 1
    monkeypatch.setattr(lipmarks, "available", lambda: False)
    assert face.shape_marks(_view()) is None
    face._SHAPE_MARKS.clear()
