"""Mark the body (vgac/bodymarks.py, bodymarks_api.py, the step 2 hook): the rule that says which automatic marks are sure,
the two layers (the app's marks and the person's moves) and how they carry over a redraw, the stop that is off by default,
the endpoints, and the 3D lift and the finger_build file for the rig. No real finder runs: pictures are described by hand."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from vgac import autopilot, bodymarks as BM, face, guidance, library
from vgac.stages import turnaround

from test_second_quarter import MetricsSC
from test_stage_offloop import FakeDB

TOP, BOTTOM, SIZE = 100.0, 900.0, [1024, 1024]
H = BOTTOM - TOP


def _front(legs_split=0.45, groin_how="outline", chin_how="face", swap=False, found=True):
    """A front picture of a figure standing square: the template's places, all seen; the person's left on the right."""
    if not found:
        return {"size": SIZE, "file": "front_cutout", "found": False, "figure": [TOP, BOTTOM], "legs_split": legs_split,
                "marks": {}, "hands": {}}
    m = BM.template("front", SIZE, [TOP, BOTTOM])
    for j in m.values():
        j.update(score=0.99, how="pose")
    m["chin"]["how"] = chin_how
    m["head_top"]["how"] = "outline"
    m["groin"].update(how=groin_how, score=0.99 if groin_how == "outline" else 0.3)
    m["nose"] = {"x": 512.0, "y": TOP + 0.08 * H, "score": 0.99, "how": "pose"}
    m["ear_L"] = {"x": 540.0, "y": TOP + 0.07 * H, "score": 0.9, "how": "pose"}
    m["ear_R"] = {"x": 484.0, "y": TOP + 0.07 * H, "score": 0.9, "how": "pose"}
    for f, dx in zip(BM.FINGERS, (-8, -4, 0, 4, 8)):
        m[f"tip_{f}_L"] = {"x": m["wrist_L"]["x"] + 20 + dx, "y": m["wrist_L"]["y"] + 60, "score": 0.95, "how": "hand"}
        m[f"tip_{f}_R"] = {"x": m["wrist_R"]["x"] - 20 + dx, "y": m["wrist_R"]["y"] + 60, "score": 0.95, "how": "hand"}
    if swap:
        for j in list(m):
            if j.endswith("_L"):
                o = j[:-2] + "_R"
                m[j]["x"], m[o]["x"] = m[o]["x"], m[j]["x"]
    return {"size": SIZE, "file": "front_cutout", "found": True, "figure": [TOP, BOTTOM], "legs_split": legs_split,
            "marks": m, "hands": {"left": {"found": True, "score": 0.95}, "right": {"found": True, "score": 0.95}}}


def _side(view, faces=None, knee_dy=0.0):
    """A side picture: the near side seen, the far side barely; the face and toes pointing the way `faces` says (left:
    the picture's left, as the left view should)."""
    faces = faces or view
    near, far = ("L", "R") if view == "left" else ("R", "L")
    d = -1 if faces == "left" else 1
    f = _front()["marks"]
    m = {}
    for j, mk in f.items():
        m[j] = {"x": 512.0, "y": mk["y"], "score": 0.99, "how": mk["how"]}
    for p in ("elbow", "wrist", "knee", "ankle", "heel", "toe", "shoulder", "hip"):
        m[f"{p}_{far}"]["score"] = 0.1
    m[f"knee_{near}"]["y"] += knee_dy * H
    m[f"toe_{near}"]["x"], m[f"heel_{near}"]["x"] = 512 + 40 * d, 512 - 15 * d
    m["nose"]["x"], m[f"ear_{near}"]["x"] = 512 + 30 * d, 512 - 10 * d
    m["chin"]["how"] = "mouth"
    return {"size": SIZE, "file": f"{view}_cutout", "found": True, "figure": [TOP, BOTTOM], "legs_split": 0.0, "marks": m,
            "hands": {"left": {"found": view == "left"}, "right": {"found": view == "right"}}}


def _quarter(view, swap=False):
    """A three-quarter picture: turned under 90 degrees, so the person's left is still on the picture's right; the far
    side's elbow barely seen (it goes behind the body)."""
    q = _front(swap=swap)
    q["file"] = f"{view}_cutout"
    far = "R" if view == "quarter" else "L"
    q["marks"][f"elbow_{far}"]["score"] = 0.3
    return q


def _back(swap=False):
    """The back picture: the front mirrored, so the person's left is on the picture's left; no face."""
    b = _front()
    b["file"] = "back_cutout"
    for j, m in b["marks"].items():
        m["x"] = 1024 - m["x"]
    b["marks"]["chin"]["how"] = "mouth"
    if swap:
        for j in [j for j in b["marks"] if j.endswith("_L")]:
            o = j[:-2] + "_R"
            b["marks"][j]["x"], b["marks"][o]["x"] = b["marks"][o]["x"], b["marks"][j]["x"]
    return b


def _doc(views):
    return {"format": BM.FORMAT, "views": views, "rule": BM.judge(views), "edited": {}, "accepted": None, "redrawn": {}}


def _views(**kw):
    return {"front": _front(**kw), "left": _side("left"), "right": _side("right")}


def _all_views(**kw):
    """Every picture with marks, as find_view gives them (the not-seen rule applied off the front)."""
    v = {"front": _front(**kw), "quarter": _quarter("quarter"), "quarter2": _quarter("quarter2"),
         "left": _side("left"), "right": _side("right"), "back": _back()}
    return {name: BM.hide_unseen(name, d) for name, d in v.items()}


# --- the rule ----------------------------------------------------------------------------------------------------------


def test_a_square_figure_with_both_sides_is_sure_and_its_check_passes():
    doc = _doc(_views())
    assert BM.state(doc) == "sure" and BM.to_check(doc) == []
    assert all(doc["rule"]["tests"].values())
    c = BM.check(doc, stop=False)
    assert c["passed"] and c["severity"] == "warn" and "confident" in c["help"]


def test_a_robe_hides_the_knees_groin_and_hips_and_they_are_shown_as_guessed():
    """The pose finder scores knees under a robe as seen; the outline says the legs are not apart (the Mage)."""
    doc = _doc(_views(legs_split=0.09, groin_how="hips"))
    asked = [c["joint"] for c in BM.to_check(doc)]
    assert BM.state(doc) == "check" and sorted(asked) == ["groin", "knee_L", "knee_R"]
    guessed = {j for j, r in doc["rule"]["views"]["front"].items() if r["guessed"]}
    assert {"groin", "knee_L", "knee_R", "hip_L", "hip_R"} <= guessed
    assert "guessed" in BM.to_check(doc)[0]["words"]
    c = BM.check(doc, stop=False)
    assert not c["passed"] and c["value"] == 3 and "left knee in the front picture" in c["help"]
    assert c["fix_kind"] == "by_hand" and "Mark the body" in c["fix_note"]


def test_the_right_picture_handed_in_as_the_left_one_is_caught_by_two_tests():
    views = _views()
    views["left"] = _side("left", faces="right")
    views["left"]["marks"] = {j: m for j, m in _side("right")["marks"].items()}
    doc = _doc(views)
    t = doc["rule"]["tests"]
    assert t["left_near"] is False and t["left_facing"] is False and t["right_near"] and t["right_facing"]
    words = [c["words"] for c in BM.to_check(doc)]
    assert BM.state(doc) == "check" and any("may show the other side" in w for w in words)


def test_left_and_right_swapped_on_the_front_is_caught():
    doc = _doc({"front": _front(swap=True), "left": _side("left"), "right": _side("right")})
    assert doc["rule"]["tests"]["front_left_right"] is False and BM.state(doc) == "check"


def test_a_knee_at_another_height_in_the_side_picture_is_unsure():
    views = _views()
    views["left"] = _side("left", knee_dy=0.08)
    doc = _doc(views)
    assert doc["rule"]["views"]["front"]["knee_L"]["why"] == ["height"]
    assert doc["rule"]["views"]["front"]["knee_R"]["sure"]


def test_no_face_on_the_front_makes_the_chin_unsure():
    doc = _doc(_views(chin_how="mouth"))
    assert doc["rule"]["tests"]["front_face"] is False
    assert doc["rule"]["views"]["front"]["chin"]["why"] == ["not_seen"]


def test_a_picture_with_no_person_gets_placeholder_marks_that_are_all_asked_about(tmp_path):
    def finder(path, key):
        return _front(found=False) if key == "front_cutout" else _side(key.split("_")[0])
    doc = BM.compute({"front": tmp_path / "f.png", "left": tmp_path / "l.png"}, finder)
    front = doc["rule"]["views"]["front"]
    assert all(r["why"] == ["not_found"] and r["guessed"] for r in front.values())
    assert doc["rule"]["tests"]["front_found"] is False and BM.state(doc) == "check"
    assert {c["joint"] for c in BM.to_check(doc) if c["joint"]} == set(BM.REQUIRED)


def test_marks_the_app_cannot_see_off_the_front_have_no_place_and_are_never_asked_about():
    """The far side of a side picture (behind the body), a far elbow seen under 0.5 on a three-quarter picture, the chin
    on the back and on a side picture with no face: "not seen", x and y null, for the person to place."""
    doc = _doc(_all_views())
    eff = BM.effective(doc)
    assert eff["left"]["elbow_R"]["x"] is None and not eff["left"]["elbow_R"]["seen"]
    assert eff["left"]["elbow_L"]["seen"] and eff["left"]["chin"]["x"] is None
    assert eff["quarter"]["elbow_R"]["x"] is None and eff["quarter"]["elbow_L"]["seen"]
    assert eff["quarter2"]["elbow_L"]["x"] is None and eff["quarter2"]["elbow_R"]["seen"]
    assert eff["back"]["chin"]["x"] is None and eff["back"]["shoulder_L"]["seen"]
    assert eff["front"]["chin"]["seen"], "the front keeps every mark"
    assert doc["rule"]["views"]["back"]["chin"] == {"sure": False, "guessed": False, "why": ["not_seen_here"], "seen": False}
    assert BM.state(doc) == "sure" and BM.to_check(doc) == [], "nothing off the front is asked about"
    ph = BM.placeholders(doc)
    assert set(ph["back"]) == {"chin"} and ph["left"]["elbow_R"]["y"] > ph["left"]["shoulder_R"]["y"]


def test_the_back_picture_names_the_persons_own_left_which_is_on_the_pictures_left():
    views = _all_views()
    assert views["back"]["marks"]["shoulder_L"]["x"] < 512 < views["back"]["marks"]["shoulder_R"]["x"]
    doc = _doc(views)
    assert doc["rule"]["tests"]["back_left_right"] is True
    views["back"] = BM.hide_unseen("back", _back(swap=True))
    doc = _doc(views)
    assert doc["rule"]["tests"]["back_left_right"] is False and BM.state(doc) == "check"
    assert any("back picture" in c["words"] for c in BM.to_check(doc))
    t = BM.template("back", SIZE, [TOP, BOTTOM])
    assert t["wrist_L"]["x"] < 512 < t["wrist_R"]["x"], "a mark placed by hand on the back starts on the person's own side"


def test_the_three_quarter_pictures_keep_the_persons_left_on_the_pictures_right():
    views = _all_views()
    assert _doc(views)["rule"]["tests"]["quarter_left_right"] and _doc(views)["rule"]["tests"]["quarter2_left_right"]
    views["quarter2"] = BM.hide_unseen("quarter2", _quarter("quarter2", swap=True))
    doc = _doc(views)
    assert doc["rule"]["tests"]["quarter2_left_right"] is False and BM.state(doc) == "check"


def test_a_three_quarter_or_back_picture_with_no_person_has_every_mark_to_place(tmp_path):
    def finder(path, key):
        return {"size": SIZE, "file": key, "found": False, "figure": [TOP, BOTTOM], "legs_split": None, "marks": {}, "hands": {}}
    v = BM.find_view("back", tmp_path / "b.png", finder)
    assert all(m["x"] is None and m["how"] == "not_seen" for m in v["marks"].values()) and "knee_L" in v["marks"]


def test_moves_on_the_new_pictures_work_like_the_others():
    doc = _doc(_all_views())
    BM.apply_moves(doc, [{"view": "back", "joint": "chin", "x": 512, "y": 200}, {"view": "quarter", "joint": "elbow_R", "x": 400, "y": 380}])
    eff = BM.effective(doc)
    assert eff["back"]["chin"] == {"x": 512.0, "y": 200.0, "source": "you", "sure": True, "guessed": False, "stale": False, "seen": True}
    assert "chin" not in BM.placeholders(doc).get("back", {})
    BM.reset(doc, "back")
    assert BM.effective(doc)["back"]["chin"]["x"] is None and BM.effective(doc)["quarter"]["elbow_R"]["source"] == "you"


def test_an_older_document_gets_the_new_pictures_once_and_keeps_the_moves(tmp_path):
    doc = _doc(_views())
    BM.apply_moves(doc, [{"view": "front", "joint": "knee_L", "x": 600, "y": 700}])
    calls: list[str] = []
    files = {v: tmp_path / f"{v}.png" for v in BM.MARK_VIEWS}
    assert BM.complete(doc, files, _counting_finder(calls)) is True
    assert sorted(calls) == ["back_cutout", "quarter2_cutout", "quarter_cutout"]
    assert set(doc["views"]) == set(BM.MARK_VIEWS) and doc["edited"]["front"]["knee_L"]["x"] == 600.0
    assert "back_left_right" in doc["rule"]["tests"]
    assert BM.complete(doc, files, _counting_finder(calls)) is False and len(calls) == 3


def test_template_puts_the_persons_left_on_the_pictures_right_and_side_toes_forward():
    t = BM.template("front", SIZE, [TOP, BOTTOM])
    assert t["wrist_L"]["x"] > 512 > t["wrist_R"]["x"]
    s = BM.template("left", SIZE, [TOP, BOTTOM])
    assert s["toe_L"]["x"] < s["heel_L"]["x"]


def test_plain_names():
    assert BM.plain_joint("elbow_L") == "left elbow" and BM.plain_joint("tip_index_R") == "right index fingertip"
    assert BM.plain_joint("head_top") == "top of the head" and BM.plain_joint("tip_thumb_L") == "left thumb tip"


# --- the person's layer and redraws ------------------------------------------------------------------------------------


def test_moves_go_in_their_own_layer_and_win_and_reset_takes_them_back():
    doc = _doc(_views(legs_split=0.09, groin_how="hips"))
    auto = copy.deepcopy(doc["views"])
    BM.apply_moves(doc, [{"view": "front", "joint": "knee_L", "x": 600, "y": 700}])
    assert doc["views"] == auto, "the app's layer is never written over"
    eff = BM.effective(doc)["front"]["knee_L"]
    assert (eff["x"], eff["y"], eff["source"]) == (600.0, 700.0, "you")
    assert [c["joint"] for c in BM.to_check(doc)] == ["groin", "knee_R"]
    BM.apply_moves(doc, [{"view": "front", "joint": "knee_R", "x": 420, "y": 700},
                         {"view": "front", "joint": "groin", "x": 512, "y": 520}])
    assert BM.state(doc) == "edited" and BM.check(doc, stop=False)["passed"]
    BM.reset(doc, "front")
    assert BM.state(doc) == "check" and doc["edited"] == {}


def test_these_look_right_accepts_the_set():
    doc = _doc(_views(legs_split=0.09, groin_how="hips"))
    BM.accept(doc)
    assert BM.state(doc) == "edited" and BM.summary(doc)["accepted"]
    c = BM.check(doc, stop=False)
    assert c["passed"] and "You moved or checked" in c["help"]
    BM.reset(doc)
    assert doc["accepted"] is None and BM.state(doc) == "check"


@pytest.mark.parametrize("move, words", [
    ({"view": "back", "joint": "knee_L", "x": 1, "y": 1}, "no 'back' picture"),
    ({"view": "front", "joint": "nose", "x": 1, "y": 1}, "is not a mark"),
    ({"view": "front", "joint": "knee_L", "x": 2000, "y": 1}, "moved off the picture"),
    ({"view": "front", "joint": "knee_L", "x": "a", "y": 1}, "x and y numbers"),
])
def test_bad_moves_are_refused(move, words):
    doc = _doc(_views())
    with pytest.raises(BM.MarkError, match=words):
        BM.apply_moves(doc, [move])


def _counting_finder(calls):
    def finder(path, key):
        calls.append(key)
        view = key.split("_")[0]
        if view == "front":
            return _front()
        if view in ("quarter", "quarter2"):
            return _quarter(view)
        return _back() if view == "back" else _side(view)
    return finder


def test_a_redraw_finds_only_the_redrawn_views_marks_and_keeps_moves_on_it_as_stale(tmp_path):
    files = {v: tmp_path / f"{v}.png" for v in BM.MARK_VIEWS}
    parent = _doc(_views())
    parent["views"]["front"]["marks"]["knee_L"]["x"] = 555.0   # tell the carried layer from a new find
    BM.apply_moves(parent, [{"view": "left", "joint": "knee_L", "x": 500, "y": 700},
                            {"view": "front", "joint": "elbow_R", "x": 380, "y": 360}])
    BM.accept(parent)
    calls: list[str] = []
    doc = BM.carry(parent, files, ["left", "back"], "v2", _counting_finder(calls))
    assert calls == ["quarter_cutout", "quarter2_cutout", "left_cutout", "back_cutout"], \
        "the redrawn views with marks, and the pictures the parent had no marks for yet"
    assert doc["views"]["front"]["marks"]["knee_L"]["x"] == 555.0
    assert doc["edited"]["left"]["knee_L"]["stale"] is True, "kept, flagged, never dropped"
    assert doc["edited"]["front"]["elbow_R"]["stale"] is False
    assert doc["accepted"] is None and doc["redrawn"] == {"left": "v2", "back": "v2"}
    assert BM.state(doc) == "check" and BM.stale(doc) == [{"view": "left", "joint": "knee_L", "name": "left knee"}]
    assert "drawn again" in BM.check(doc, stop=False)["help"]
    BM.apply_moves(doc, [{"view": "left", "joint": "knee_L", "x": 505, "y": 700}])
    assert BM.stale(doc) == [] and BM.state(doc) == "edited"


def test_a_redraw_of_a_view_without_marks_keeps_everything(tmp_path):
    files = {v: tmp_path / f"{v}.png" for v in BM.MARK_VIEWS}
    parent = BM.accept(_doc(_views()))
    calls: list[str] = []
    doc = BM.carry(parent, files, ["back"], "v2", _counting_finder(calls))
    assert calls == ["quarter_cutout", "quarter2_cutout", "back_cutout"], "only the pictures the parent had no marks for"
    assert doc["accepted"] is None, "the back was drawn again"
    calls.clear()
    doc2 = BM.carry(BM.accept(doc), files, ["top"], "v3", _counting_finder(calls))
    assert calls == [] and doc2["accepted"] == doc["accepted"] and doc2["views"] == doc["views"]


def test_moves_on_a_view_the_version_no_longer_has_are_kept_as_stale(tmp_path):
    parent = _doc(_views())
    BM.apply_moves(parent, [{"view": "right", "joint": "wrist_R", "x": 500, "y": 400}])
    files = {v: tmp_path / f"{v}.png" for v in ("front", "left")}
    doc = BM.carry(parent, files, [], "v2", _counting_finder([]))
    assert "right" not in doc["views"] and doc["edited"]["right"]["wrist_R"]["stale"] is True


def test_no_parent_marks_means_a_fresh_find(tmp_path):
    calls: list[str] = []
    doc = BM.carry(None, {v: tmp_path / f"{v}.png" for v in BM.MARK_VIEWS}, [], "v", _counting_finder(calls))
    assert sorted(calls) == sorted(f"{v}_cutout" for v in BM.MARK_VIEWS) and doc["format"] == BM.FORMAT


def test_the_file_round_trips_and_an_unknown_format_is_ignored(tmp_path):
    doc = _doc(_views())
    p = BM.write(doc, tmp_path / "marks.json")
    assert BM.read(p) == json.loads(json.dumps(doc))
    p.write_text(json.dumps({**doc, "format": 99}), encoding="utf-8")
    assert BM.read(p) is None and BM.read(tmp_path / "none.json") is None


# --- the stop: off until something reads the marks ---------------------------------------------------------------------


def _unsure_version(params=None):
    doc = _doc(_views(legs_split=0.09, groin_how="hips"))
    c = BM.check(doc, stop=BM.stop_on(params or {}))
    return SimpleNamespace(stage="turnaround", params=params or {}, metrics={"checks": [c]})


def test_unsure_marks_do_not_stop_autopilot_by_default():
    assert BM.STOP_WHEN_UNSURE is False and BM.stop_on({}) is False
    v = _unsure_version()
    assert v.metrics["checks"][0]["vars"]["stop"] is False
    assert autopilot.attention_reason(v, "block") is None
    assert autopilot._passes(v, "block")[0] is True, "a warning does not hold the version back"


def test_with_the_stop_on_autopilot_stops_and_asks(monkeypatch):
    v = _unsure_version({"body_marks_stop": True})
    reason = autopilot.attention_reason(v, "block")
    assert reason and "Mark the body" in reason and "Resume" in reason
    monkeypatch.setattr(BM, "STOP_WHEN_UNSURE", True)
    assert BM.stop_on({}) is True and BM.stop_on({"body_marks_stop": False}) is False
    assert autopilot.attention_reason(_unsure_version(), "block")


def test_sure_marks_never_stop_even_with_the_stop_on():
    doc = _doc(_views())
    v = SimpleNamespace(stage="turnaround", params={}, metrics={"checks": [BM.check(doc, stop=True)]})
    assert autopilot.attention_reason(v, "block") is None


def test_the_check_has_a_plain_guide_and_a_line_for_what_to_do():
    assert guidance.CHECK_GUIDE["body_marks"]["severity"] == "warn"
    c = BM.check(_doc(_views(legs_split=0.09, groin_how="hips")), stop=False)
    assert c["fix_note"] and "fix" not in c


# --- the step 2 hook ---------------------------------------------------------------------------------------------------


def _png(path: Path) -> Path:
    a = np.zeros((1024, 1024, 4), np.uint8)
    a[90:910, 200:824] = 255   # every hand-made mark lies on the figure
    Image.fromarray(a).save(path)
    return path


@pytest.fixture
def fake_finder(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(BM, "find", _counting_finder(calls))
    monkeypatch.setattr(turnaround, "BODY_MARKS", True)
    return calls


def _sc(tmp_path, version, *others, category="humanoid"):
    sc = MetricsSC(tmp_path, version, FakeDB(*others, version), category=category)
    sc.settings = None
    for v in BM.MARK_VIEWS + ("back",):
        sc.files[f"{v}_cutout"] = _png(tmp_path / f"{version.id[:2]}_{v}.png")
    return sc


def test_step_2_writes_the_marks_the_summary_and_the_check(tmp_path, fake_finder):
    concept = SimpleNamespace(id="c" * 32, parent_version_id=None, stage="concept", params={}, metrics={})
    v = SimpleNamespace(id="n" * 32, parent_version_id=concept.id, stage="turnaround", params={}, metrics={})
    sc = _sc(tmp_path, v, concept)
    turnaround.body_marks(sc, list(BM.MARK_VIEWS) + ["back"])
    assert sorted(fake_finder) == sorted(f"{v}_cutout" for v in BM.MARK_VIEWS)
    doc = BM.read(sc.files["marks"])
    assert doc and v.metrics["body_marks"]["state"] == "sure"
    assert [c["name"] for c in v.metrics["checks"]] == ["body_marks"] and v.metrics["gate_ok"]


def test_step_2_carries_a_parents_marks_through_a_redraw(tmp_path, fake_finder, monkeypatch):
    concept = SimpleNamespace(id="c" * 32, parent_version_id=None, stage="concept", params={}, metrics={})
    parent = SimpleNamespace(id="p" * 32, parent_version_id=concept.id, stage="turnaround", params={}, metrics={})
    pdoc = _doc(_views())
    BM.apply_moves(pdoc, [{"view": "right", "joint": "ankle_R", "x": 520, "y": 860}])
    ppath = BM.write(pdoc, tmp_path / "parent_marks.json")
    monkeypatch.setattr(turnaround.library, "version_file", lambda ver, key, settings: ppath if (ver is parent and key == "marks") else None)
    v = SimpleNamespace(id="n" * 32, parent_version_id=parent.id, stage="turnaround", params={}, metrics={})
    sc = _sc(tmp_path, v, concept, parent)
    turnaround.body_marks(sc, ["right"])
    assert fake_finder == ["quarter_cutout", "quarter2_cutout", "right_cutout", "back_cutout"]
    assert v.metrics["body_marks"]["stale"] == [{"view": "right", "joint": "ankle_R", "name": "right ankle"}]
    assert v.metrics["body_marks"]["state"] == "check" and not v.metrics["gate_ok"]


def test_step_2_marks_only_characters_with_a_human_body(tmp_path, fake_finder):
    v = SimpleNamespace(id="n" * 32, parent_version_id=None, stage="turnaround", params={}, metrics={})
    sc = _sc(tmp_path, v, category="quadruped")
    turnaround.body_marks(sc, ["front"])
    assert "skipped" in v.metrics["body_marks"] and fake_finder == [] and "checks" not in v.metrics


def test_a_fault_in_the_marks_never_fails_the_step(tmp_path, monkeypatch):
    def boom(path, key):
        raise RuntimeError("finder broke")
    monkeypatch.setattr(BM, "find", boom)
    v = SimpleNamespace(id="n" * 32, parent_version_id=None, stage="turnaround", params={}, metrics={})
    turnaround.body_marks(_sc(tmp_path, v), ["front"])
    assert "finder broke" in v.metrics["body_marks"]["error"]


# --- the endpoints -----------------------------------------------------------------------------------------------------


@pytest.fixture
def api(settings, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(BM, "find", _counting_finder(calls))
    from vgac.db import Asset, Project, Version, session_factory
    from vgac.main import app

    with TestClient(app) as client:
        with session_factory()() as db:
            p = Project(name="p")
            db.add(p)
            db.flush()
            ids = {}
            for cat in ("humanoid", "quadruped"):
                a = Asset(project_id=p.id, name=cat, category=cat)
                db.add(a)
                db.flush()
                for status in ("done", "running"):
                    v = Version(asset_id=a.id, stage="turnaround", status=status)
                    db.add(v)
                    db.flush()
                    d = library.version_dir(a, v, settings)
                    for view in BM.MARK_VIEWS:
                        library.register_file(v, f"{view}_cutout", _png(d / f"{view}.png"), settings)
                    ids[(cat, status)] = v.id
            db.commit()
        yield client, ids, calls


def test_get_finds_marks_for_an_older_version_once_and_saves_them(api):
    client, ids, calls = api
    vid = ids[("humanoid", "done")]
    r = client.get(f"/api/versions/{vid}/marks")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "sure" and set(body["pictures"]) == set(BM.MARK_VIEWS) and body["stop"] is False
    assert body["marks"]["front"]["knee_L"]["source"] == "app" and body["names"]["knee_L"] == "left knee"
    assert body["pictures"]["front"]["path"].endswith("front.png")
    client.get(f"/api/versions/{vid}/marks")
    assert len(calls) == len(BM.MARK_VIEWS), "found once, then read from marks.json"
    v = client.get(f"/api/versions/{vid}").json()
    assert v["files"]["marks"].endswith("marks.json") and v["metrics"]["body_marks"]["state"] == "sure"
    assert any(c["name"] == "body_marks" and c["passed"] for c in v["metrics"]["checks"])


def test_put_moves_accept_and_reset(api):
    client, ids, _ = api
    vid = ids[("humanoid", "done")]
    r = client.put(f"/api/versions/{vid}/marks", json={"moves": [{"view": "front", "joint": "knee_L", "x": 600, "y": 700}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "edited" and body["marks"]["front"]["knee_L"] == {"x": 600.0, "y": 700.0, "source": "you", "sure": True, "guessed": False, "stale": False, "seen": True}
    assert body["automatic"]["front"]["knee_L"]["x"] != 600.0
    r = client.put(f"/api/versions/{vid}/marks", json={"accept": True})
    assert r.json()["accepted"] is True
    r = client.post(f"/api/versions/{vid}/marks/reset", json={"view": "front"})
    assert r.json()["marks"]["front"]["knee_L"]["source"] == "app" and r.json()["accepted"] is False
    assert client.post(f"/api/versions/{vid}/marks/reset").status_code == 200
    v = client.get(f"/api/versions/{vid}").json()
    assert v["metrics"]["body_marks"]["edited"] == 0


@pytest.mark.parametrize("payload, status", [
    ({"moves": [{"view": "front", "joint": "knee_L", "x": 5000, "y": 1}]}, 400),
    ({"moves": [{"view": "front", "joint": "liver", "x": 1, "y": 1}]}, 400),
    ({}, 400),
])
def test_put_refuses_bad_moves(api, payload, status):
    client, ids, _ = api
    assert client.put(f"/api/versions/{ids[('humanoid', 'done')]}/marks", json=payload).status_code == status


def test_marks_only_on_finished_step_2_versions_of_characters(api):
    client, ids, _ = api
    assert client.get(f"/api/versions/{ids[('humanoid', 'running')]}/marks").status_code == 409
    assert client.get(f"/api/versions/{ids[('quadruped', 'done')]}/marks").status_code == 400
    assert client.post(f"/api/versions/{ids[('humanoid', 'done')]}/marks/reset", json={"view": "top"}).status_code == 400
    assert client.get("/api/versions/nope/marks").status_code == 404


# --- for the rig and the painter ---------------------------------------------------------------------------------------


def test_lift_puts_joints_back_where_they_were_and_skips_the_far_side():
    cams = {"front": face.Camera(target=np.array([0.0, 0.9, 0.0]), yaw_deg=0.0, scale=2.0, size=1024),
            "left": face.Camera(target=np.array([0.0, 0.9, 0.0]), yaw_deg=90.0, scale=2.0, size=1024)}
    world = {"knee_L": np.array([0.12, 0.5, 0.03]), "knee_R": np.array([-0.12, 0.5, 0.01]), "tip_index_L": np.array([0.4, 0.8, 0.05])}
    views = {}
    for name, cam in cams.items():
        m = {}
        for j, p in world.items():
            (xy,), _ = cam.project(p[None])
            x, y = xy
            m[j] = {"x": float(x), "y": float(y), "score": 0.99, "how": "pose"}
        views[name] = {"size": SIZE, "found": True, "figure": [TOP, BOTTOM], "marks": m, "hands": {}}
    doc = {"format": BM.FORMAT, "views": views, "edited": {}, "accepted": None,
           "rule": {"views": {v: {j: {"sure": True, "guessed": False, "why": []} for j in world} for v in views}, "tests": {}}}
    got = BM.lift(doc, lambda view, x, y: (cams[view], x, y))
    for j in ("knee_L", "tip_index_L"):
        assert np.allclose(got[j]["xyz"], world[j], atol=1e-3) and sorted(got[j]["views"]) == ["front", "left"]
    assert got["knee_R"]["views"] == ["front"], "a side picture gives only its own side"
    doc["edited"] = {"front": {"knee_L": {"x": 0, "y": 0, "at": "", "stale": True}}}
    assert BM.lift(doc, lambda view, x, y: (cams[view], x, y))["knee_L"]["views"] == ["left"], "a stale move is not used"


def test_finger_build_file_is_in_blenders_frame_and_says_which_hand_is_missing():
    j3 = {f"tip_{f}_L": {"xyz": [0.4, 0.8, 0.05 + i * 0.01]} for i, f in enumerate(("index", "middle", "ring", "little"))}
    out = BM.finger_build_marks(j3)
    assert out["Left"]["fingertips"][0] == [0.4, -0.05, 0.8] and len(out["Left"]["fingertips"]) == 4
    assert "Right" not in out and "Right" in out["missing"]


def test_a_picture_pixel_is_found_in_the_fitted_cameras_frame():
    ys, xs = np.mgrid[0:200, 0:200] + 0.5
    qx, qy = 0.5 * xs + 10.0, 0.5 * ys + 20.0     # the camera pixel (c, r) reads picture pixel (0.5c + 10, 0.5r + 20)
    c, r = BM.render_from_picture(qx, qy, 60.0, 70.0)
    assert abs(c - 100.0) < 0.6 and abs(r - 100.0) < 0.6
    assert BM.render_from_picture(qx, qy, 500.0, 500.0) is None


def test_a_mark_is_found_in_the_padded_square_picture_the_clean_up_fits():
    assert BM.picture_to_prepared(100, 50, [1024, 1024], 2048) == (200.0, 100.0)
    # a tall picture is centred on a square of its height: 600 x 1000 gets 200 px of border on each side
    x, y = BM.picture_to_prepared(0, 0, [600, 1000], 1000)
    assert (x, y) == (200.0, 0.0)


def test_get_completes_marks_made_before_the_three_quarter_and_back_pictures_had_them(api, settings):
    client, ids, calls = api
    vid = ids[("humanoid", "done")]
    from vgac.db import Asset, Version, session_factory

    old = _doc(_views())
    BM.apply_moves(old, [{"view": "front", "joint": "elbow_L", "x": 640, "y": 360}])
    with session_factory()() as db:
        v = db.get(Version, vid)
        a = db.get(Asset, v.asset_id)
        library.register_file(v, "marks", BM.write(old, library.version_dir(a, v, settings) / "marks.json"), settings)
        db.commit()
    body = client.get(f"/api/versions/{vid}/marks").json()
    assert sorted(calls) == ["back_cutout", "quarter2_cutout", "quarter_cutout"]
    assert list(body["pictures"]) == list(BM.MARK_VIEWS), "in the order the panel shows them"
    assert body["marks"]["front"]["elbow_L"]["source"] == "you" and body["marks"]["back"]["chin"]["x"] is None
    assert body["placeholders"]["back"]["chin"]["y"] > 0
    client.get(f"/api/versions/{vid}/marks")
    assert len(calls) == 3


def test_a_knee_is_guessed_when_its_own_row_shows_one_shape_and_the_three_quarter_pictures_follow_the_front():
    """The legs are tested at each knee's own height (a foot set forward cut the old scan short on a three-quarter
    picture). Only the front and the back are tested: on a three-quarter picture one knee is often behind the other, so
    it takes the front's answer, the clothing being the same."""
    views = _all_views()
    views["front"]["knees_apart"] = {"knee_L": False, "knee_R": True}
    views["front"]["legs_split"] = 0.0     # ignored when knees_apart is there
    views["quarter"]["knees_apart"] = {"knee_L": False, "knee_R": False}   # overlapping legs: not read on a three-quarter
    doc = _doc(views)
    front, quarter = doc["rule"]["views"]["front"], doc["rule"]["views"]["quarter"]
    assert front["knee_L"]["guessed"] and front["knee_R"]["sure"]
    assert quarter["knee_L"]["guessed"] and quarter["knee_R"]["sure"]
    views["back"]["knees_apart"] = {"knee_L": True, "knee_R": False}
    assert _doc(views)["rule"]["views"]["back"]["knee_R"]["guessed"]
