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


# --- the fit choice and the hair mask release (face_detail) --------------------------------------------------------


def test_an_eyes_and_mouth_fit_far_behind_the_whole_face_fit_is_not_taken(monkeypatch):
    import numpy as np
    from vgac.stages import face_detail as fd

    info = {"width_px": 400.0, "nose": (2, 2)}
    scores = {"whole": 0.95, "lm": 0.69}

    monkeypatch.setattr(fd.face, "landmark_pair", lambda s, r, i: ({"how": "mediapipe", "eyes": []}, {"how": "mediapipe", "eyes": []}))
    monkeypatch.setattr(fd.face, "align", lambda s, r, inner: {"sx": 1.0, "sy": 1.0, "tx": 0.0, "ty": 0.0})
    monkeypatch.setattr(fd.face, "fit_landmarks", lambda a, b, nose: {"sx": 1.0, "sy": 1.0, "tx": 5.0, "ty": 0.0, "residual_px": 20.0})
    monkeypatch.setattr(fd.face, "apply_alignment", lambda src, a: a)
    monkeypatch.setattr(fd.face, "compose_alignment", lambda first, then: dict(then))
    monkeypatch.setattr(fd.face, "feature_match", lambda picture, render, inner, lo, hi: scores["whole"] if picture.get("tx") == 0.0 else scores["lm"])
    monkeypatch.setattr(fd.face, "landmarks", lambda *a: None)  # the whole fit does not place the eyes better
    a = fd.fit_picture(np.zeros((4, 4, 3)), np.zeros((4, 4, 3)), info, np.ones((4, 4), dtype=bool))
    assert a["how"] == "whole face" and a["score"] == 0.95  # 0.69 is 0.26 behind and under the hold line
    scores["lm"] = 0.90  # within the margin: the eyes-and-mouth fit stands, as before
    a = fd.fit_picture(np.zeros((4, 4, 3)), np.zeros((4, 4, 3)), info, np.ones((4, 4), dtype=bool))
    assert a["how"] == "eyes and mouth" and a["score"] == 0.90
    scores.update(whole=0.60, lm=0.55)  # whole is no better than the hold line: the old rule, only the margin applies
    a = fd.fit_picture(np.zeros((4, 4, 3)), np.zeros((4, 4, 3)), info, np.ones((4, 4), dtype=bool))
    assert a["how"] == "eyes and mouth"


def test_flat_hair_in_the_features_area_is_released_and_a_flap_keeps_its_hair(monkeypatch):
    import numpy as np
    from vgac.stages import face_detail as fd

    S = 200
    region = np.ones((S, S), dtype=bool)
    info = {"region": region, "nose": (100, 100), "width_px": 100.0}
    hair = np.zeros((S, S), dtype=bool)
    hair[80:120, 60:140] = True  # inside the features area
    hair[5:20, 5:20] = True  # outside it: never touched
    z = np.zeros((S, S))
    view = {"mask": np.ones((S, S), dtype=bool), "raster": {"z": z}}
    out, n = fd.release_flat_hair(hair, view, info)
    assert n == int((hair & fd.face.features_box(info)).sum()) and not (out & fd.face.features_box(info)).any()
    assert out[5:20, 5:20].all()
    # a flap: a step in depth at the edge of the hair keeps the hair next to it
    z[:, 100:] = 0.01
    out, n = fd.release_flat_hair(hair, view, info)
    assert out[80:120, 96:104].all() and n < int((hair & fd.face.features_box(info)).sum())


def test_feature_surrounds_are_paintable_only_for_points_found_by_the_landmark_model():
    import numpy as np
    from vgac.stages import face_detail as fd

    S = 400
    mask = np.ones((S, S), dtype=bool)
    mask[:, 300:] = False  # off the model
    info = {"width_px": 200.0}
    eyes = [(150.0, 200.0), (260.0, 200.0)]
    around = fd.feature_surrounds({"how": "mediapipe", "eyes": eyes}, info, mask)
    assert around[200, 150] and around[200, 215] and not around[:, 300:].any() and not around[300, 150]
    assert around[200, 150 - 40]  # well past the box itself
    for lm in (None, {"how": "darkest", "eyes": eyes}):
        assert not fd.feature_surrounds(lm, info, mask).any()


def test_the_surroundings_reach_up_in_every_column_even_when_the_two_points_differ_in_height():
    import numpy as np
    from vgac.stages import face_detail as fd

    S = 600
    mask = np.ones((S, S), dtype=bool)
    info = {"width_px": 200.0}
    eyes = [(200.0, 320.0), (400.0, 300.0)]
    around = fd.feature_surrounds({"how": "mediapipe", "eyes": eyes}, info, mask)
    top = int(310.0 - fd.BROW_BAND[0] * 200.0)
    for x in (200, 400):
        column = around[top:300, x]
        assert column.all(), x  # no unpainted row between the band's top and the box
    wide = fd.face.eye_boxes(mask.shape, eyes, 200.0, grow=fd.SURROUND_GROW)
    columns = np.nonzero(wide.any(axis=0))[0]
    assert not around[top, columns[0]] and not around[top, columns[-1]]  # the outer columns do not reach up
    assert not around[:top - 60, 200].any()


def test_extra_pixels_given_to_the_hair_release_are_released_too():
    import numpy as np
    from vgac.stages import face_detail as fd

    S = 200
    info = {"region": np.ones((S, S), dtype=bool), "nose": (100, 110), "width_px": 60.0}
    view = {"mask": np.ones((S, S), dtype=bool), "raster": {"z": np.zeros((S, S))}}
    hair = np.zeros((S, S), dtype=bool)
    hair[5:15, 5:15] = True  # far outside the features area
    also = np.zeros((S, S), dtype=bool)
    also[0:20, 0:20] = True
    assert fd.release_flat_hair(hair, view, info)[0][10, 10]
    out, n = fd.release_flat_hair(hair, view, info, also)
    assert not out.any() and n == 100


def test_the_full_coat_is_full_over_the_features_and_never_outside_what_may_be_painted():
    import numpy as np
    from vgac.stages import face_detail as fd

    S = 400
    info = {"region": np.ones((S, S), dtype=bool), "nose": (200, 220), "width_px": 200.0}
    paint = np.zeros((S, S), dtype=bool)
    paint[60:340, 60:340] = True
    around = np.zeros((S, S), dtype=bool)
    w = fd.features_coat_weight(paint, around, info)
    assert w[220, 200] > 0.99 and w[180, 200] > 0.99
    assert w[~paint].max() == 0.0 and w[80, 80] == 0.0 and w[320, 200] == 0.0  # inside paint, outside the features
    around[70:110, 150:250] = True  # feature surrounds add to it
    assert fd.features_coat_weight(paint, around, info)[90, 200] > 0.8
