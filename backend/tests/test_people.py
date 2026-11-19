"""The people in a feature photo (vgac/people.py): the main person, the suggested box and its limits, the API for one
person, several, none and a detector that fails or is missing, and the crop and warning recorded on the version
(vgac/compose.py). The detector is replaced by a stand-in; the graphics-card calls are the stand-ins of
test_own_images_api.py."""

from __future__ import annotations

from PIL import Image

from own_images_helpers import b64, cut, figure
from test_own_images_api import _asset, _project, client, engine, wait_job  # noqa: F401  (fixtures)
from vgac import people

W, H = 400, 800


def person(x0, y0, x1, y1, score=0.9):
    return {"box": [x0, y0, x1, y1], "score": score}


# --- the main person ---------------------------------------------------------------------------------------------------


def test_the_main_person_is_the_largest_when_both_are_central():
    small_mid = person(0.40, 0.20, 0.60, 0.80)
    big_mid = person(0.30, 0.10, 0.70, 0.95)
    assert people.main_person([small_mid, big_mid]) == 1


def test_a_middle_person_beats_a_smaller_one_at_the_edge_when_the_difference_is_large_enough():
    at_edge = person(0.00, 0.20, 0.25, 0.70)  # area 0.125, centre 0.125: weighted down to 0.625 (score 0.078)
    central = person(0.40, 0.20, 0.60, 0.80)  # area 0.12, centre 0.50: full weight (score 0.12)
    assert people.main_person([at_edge, central]) == 1


def test_no_people_has_no_main_person():
    assert people.main_person([]) is None


# --- box limits --------------------------------------------------------------------------------------------------------


def test_a_box_is_whole_pixels_inside_the_picture_and_at_least_the_minimum_side():
    assert people.clamp_box({"x": 10.4, "y": 20.6, "w": 100.2, "h": 120}, W, H) == {"x": 10, "y": 21, "w": 100, "h": 120}
    assert people.clamp_box({"x": -1, "y": 0, "w": 100, "h": 100}, W, H) is None  # outside on the left
    assert people.clamp_box({"x": 350, "y": 0, "w": 100, "h": 100}, W, H) is None  # past the right edge
    assert people.clamp_box({"x": 0, "y": 0, "w": 20, "h": 100}, W, H) is None  # too narrow
    assert people.clamp_box({"x": 0, "y": 0, "w": 0, "h": 100}, W, H) is None  # empty


def test_a_small_picture_may_be_boxed_whole():
    tiny = (40, 50)
    assert people.clamp_box({"x": 0, "y": 0, "w": 40, "h": 50}, *tiny) == {"x": 0, "y": 0, "w": 40, "h": 50}


def test_the_suggested_box_has_a_margin_stays_in_the_picture_and_is_usable():
    centre = person(0.40, 0.20, 0.60, 0.80)
    b = people.suggest_box(centre, W, H)
    own = people.box_px(centre, W, H)
    assert b["x"] < own["x"] and b["x"] + b["w"] > own["x"] + own["w"]
    assert b["y"] < own["y"] and b["y"] + b["h"] > own["y"] + own["h"]
    assert people.clamp_box(b, W, H) == b
    edge = person(0.00, 0.00, 0.50, 1.00)  # touching the top, left and bottom
    e = people.suggest_box(edge, W, H)
    assert e["x"] == 0 and e["y"] == 0 and e["y"] + e["h"] <= H
    tiny = person(0.50, 0.50, 0.505, 0.51)  # a few pixels: still a usable box
    t = people.suggest_box(tiny, W, H)
    assert t["w"] >= people.MIN_SIDE and t["h"] >= people.MIN_SIDE


# --- the API ------------------------------------------------------------------------------------------------------------


def _detect(client, monkeypatch, result):
    monkeypatch.setattr(people, "find", lambda img: result)
    img = b64(Image.new("RGB", (W, H), (120, 120, 120)))
    return client.post("/api/people", json={"image": img})


def test_one_person_gets_a_suggested_box(client, monkeypatch):
    r = _detect(client, monkeypatch, {"status": "ok", "people": [person(0.40, 0.20, 0.60, 0.80)]})
    body = r.json()
    assert r.status_code == 200 and body["status"] == "ok" and body["main"] == 0
    assert body["picture"] == {"width": W, "height": H} and len(body["people"]) == 1
    assert people.clamp_box(body["suggested"], W, H) == body["suggested"]


def test_several_people_pick_one_main_person_and_suggest_its_box(client, monkeypatch):
    r = _detect(client, monkeypatch, {"status": "ok", "people": [person(0.00, 0.20, 0.25, 0.70, 0.8), person(0.40, 0.20, 0.60, 0.80, 0.9)]})
    body = r.json()
    assert body["main"] == 1 and len(body["people"]) == 2
    assert body["suggested"]["x"] > W * 0.3  # the central person, not the one at the left


def test_no_person_has_no_box_and_no_error(client, monkeypatch):
    body = _detect(client, monkeypatch, {"status": "none", "people": []}).json()
    assert body["status"] == "none" and body["main"] is None and body["suggested"] is None and body["people"] == []


def test_a_detector_that_fails_or_is_missing_gives_no_box_and_no_error(client, monkeypatch):
    for status in ("failed", "unavailable"):
        r = _detect(client, monkeypatch, {"status": status, "people": []})
        assert r.status_code == 200 and r.json()["status"] == status and r.json()["suggested"] is None


def test_the_missing_pose_model_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(people, "_detector", None)
    monkeypatch.setattr(people, "_failed", False)
    monkeypatch.setenv("VGAC_POSE_LANDMARKER", "C:/no/such/model.task")
    out = people.find(Image.new("RGB", (64, 64), (0, 0, 0)))
    assert out["status"] in ("unavailable", "failed") and out["people"] == []


# --- the crop and the warning on the version ----------------------------------------------------------------------------


def test_a_cropped_photo_is_cut_to_its_box_and_the_version_records_it(client, engine):  # noqa: F811
    p = _project(client)
    feats = [{"role": "body", "image": b64(cut(figure("wide", legs="apart"))), "people_found": 2, "crop": {"x": 10, "y": 20, "w": 120, "h": 200}},
             {"role": "outfit", "image": b64(Image.new("RGB", (300, 300), (30, 30, 200))), "people_found": 2}]
    a = _asset(client, p["id"], prompt="a tall ranger", features=feats)
    roles = a["compose"]["version"]["params"]["compose_roles"]
    assert roles[0]["cropped"] is True and roles[0]["crop"] == [10, 20, 120, 200]
    assert roles[1]["cropped"] is False and roles[1]["whole_with_others"] is True


def test_a_box_outside_the_picture_is_refused(client, engine):  # noqa: F811
    p = _project(client)
    feats = [{"role": "body", "image": b64(Image.new("RGB", (200, 260), (30, 30, 200))), "crop": {"x": 150, "y": 0, "w": 200, "h": 100}}]
    r = client.post(f"/api/projects/{p['id']}/assets", json={"name": "knight", "category": "humanoid", "style_preset": "painted", "prompt": "a knight", "features": feats})
    assert r.status_code == 400 and "outside the picture" in r.text
