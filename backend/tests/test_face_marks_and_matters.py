"""MediaPipe's eyes and mouth (vgac/facemarks.py) and "the face matters" on the asset (db.Asset.face_matters)."""

from __future__ import annotations

import numpy as np

from vgac import face, facemarks
from vgac.stages import face_detail as fd

from test_api import _project_asset, client, fake_execute  # noqa: F401  (fixtures)

INFO = {"nose": (512.0, 560.0), "width_px": 500.0}


def _blob(cx: int, cy: int, rx: int, ry: int, size: int = 1024) -> np.ndarray:
    Y, X = np.mgrid[0:size, 0:size]
    return ((X - cx) / rx) ** 2 + ((Y - cy) / ry) ** 2 <= 1.0


def test_marks_from_masks_are_the_centres_left_to_right():
    m = facemarks.from_masks(_blob(640, 480, 40, 15), _blob(400, 470, 40, 15), _blob(512, 700, 70, 25))
    assert m["how"] == "mediapipe"
    (lx, ly), (rx, ry) = m["eyes"]
    assert abs(lx - 400.5) < 1 and abs(rx - 640.5) < 1 and abs(m["mouth"][1] - 700.5) < 1
    assert 75 <= m["eye_px"] <= 85
    assert facemarks.from_masks(np.zeros((8, 8), bool), _blob(4, 4, 2, 2, 8), _blob(4, 4, 2, 2, 8)) is None


def test_a_face_that_is_not_the_models_is_not_used():
    good = {"eyes": [(400.0, 470.0), (640.0, 480.0)], "mouth": (512.0, 700.0)}
    assert facemarks.plausible(good, INFO)
    assert not facemarks.plausible({**good, "mouth": (512.0, 300.0)}, INFO)  # mouth above the nose
    assert not facemarks.plausible({**good, "eyes": [(500.0, 470.0), (520.0, 470.0)]}, INFO)  # eyes far too close
    assert not facemarks.plausible({**good, "eyes": [(100.0, 200.0), (300.0, 200.0)]}, INFO)  # another face


def test_without_a_card_the_darkest_spot_finder_is_used(monkeypatch):
    assert not facemarks.active()
    assert facemarks.find(np.zeros((64, 64, 3)), INFO) is None
    seen = []
    monkeypatch.setattr(face, "darkest_landmarks", lambda img, info: seen.append(1) or {"eyes": [(1, 1), (2, 2)], "mouth": (3, 3)})
    assert face.landmarks(np.zeros((64, 64, 3)), INFO)["mouth"] == (3, 3) and seen


def test_a_pair_is_found_the_same_way(monkeypatch):
    mp = {"eyes": [(1, 1), (2, 2)], "mouth": (3, 3), "how": "mediapipe"}
    dark = {"eyes": [(5, 5), (6, 6)], "mouth": (7, 7)}
    answers = iter([mp, None])
    monkeypatch.setattr(facemarks, "find", lambda img, info: next(answers))
    monkeypatch.setattr(face, "darkest_landmarks", lambda img, info: dark)
    a, b = face.landmark_pair(np.zeros((4, 4, 3)), np.zeros((4, 4, 3)), INFO)
    assert a == dark and b == dark  # one by MediaPipe, one not: both by the darkest spots


def test_the_card_is_set_only_inside_use():
    tok = facemarks.use("http://127.0.0.1:1", "C:/nowhere")
    assert facemarks.active()
    facemarks.done(tok)
    assert not facemarks.active()
    assert facemarks.use(None, None) is None


def test_face_matters_off_skips_the_pass_unless_the_run_turns_it_on():
    rec = {"surface": {"texture_filter": "smooth", "shading": "smooth"}, "mesh": {"texture": 2048}}
    assert fd.skip_reason("humanoid", rec, {"_face_matters": False}, True) == "face_not_important"
    assert fd.skip_reason("humanoid", rec, {"_face_matters": False, "face_detail": True}, True) is None
    assert fd.face_checks({"face_detail": {"ran": False, "skipped": "face_not_important"}}) == []


def test_face_matters_is_on_by_default_and_can_be_turned_off(client):
    p, a = _project_asset(client)
    assert a["face_matters"] is True
    r = client.put(f"/api/assets/{a['id']}", json={"face_matters": False})
    assert r.status_code == 200 and r.json()["face_matters"] is False
    assert client.get(f"/api/assets/{a['id']}").json()["face_matters"] is False
    r = client.post(f"/api/projects/{p['id']}/assets", json={"name": "hooded", "category": "humanoid", "style_preset": a["style_preset"],
                                                             "prompt": "a hooded figure with glowing eyes", "face_matters": False})
    assert r.status_code == 201 and r.json()["face_matters"] is False
