"""vgac/facing.py: the toes test, the decision between it and the outline test, and the facing_tests check. Synthetic
legs and feet, no Blender."""

from __future__ import annotations

import math

import numpy as np
import pytest

from vgac import facing, quality
from vgac.stages import prep


def box(x0, x1, y0, y1, z0, z1, n=6):
    xs, ys, zs = np.linspace(x0, x1, n), np.linspace(y0, y1, n), np.linspace(z0, z1, n)
    return np.array([[x, y, z] for x in xs for y in ys for z in zs])


def person(yaw_deg: float = 0.0, feet: bool = True, legs_gap: bool = True) -> np.ndarray:
    """A 1.8 m figure whose toes point to camera yaw `yaw_deg` (0: +Z): two legs 0.2 m apart, feet 0.25 m long sticking out
    in front, a torso on top."""
    parts = []
    for x in ((-0.1, 0.1) if legs_gap else (-0.02, 0.02)):
        parts.append(box(x - 0.05, x + 0.05, 0.08, 0.9, -0.05, 0.05))          # leg
        if feet:
            parts.append(box(x - 0.05, x + 0.05, 0.0, 0.05, -0.05, 0.2))       # foot, toes toward +Z
    parts.append(box(-0.2, 0.2, 0.9, 1.8, -0.1, 0.1))                          # body and head
    v = np.concatenate(parts)
    a = math.radians(yaw_deg)
    c, s = math.cos(a), math.sin(a)
    return np.stack([c * v[:, 0] + s * v[:, 2], v[:, 1], -s * v[:, 0] + c * v[:, 2]], axis=1)


@pytest.mark.parametrize("yaw", [0.0, 30.0, 90.0, 180.0, 250.0])
def test_toes_point_where_the_feet_stick_out(yaw):
    t = facing.toes(person(yaw))
    assert t["found"], t
    assert facing.angle_diff(t["yaw"], yaw) < 8
    assert facing.angle_diff(t["turn"], -yaw) < 8


def test_no_feet_no_answer():
    t = facing.toes(person(feet=False))
    assert not t["found"] and "stick out" in t["reason"]


def test_legs_together_give_no_answer():
    t = facing.toes(person(legs_gap=False))
    assert not t["found"] and "legs" in t["reason"]


def test_a_model_without_a_figure_gives_no_answer():
    assert not facing.toes(np.zeros((10, 3)))["found"]
    assert not facing.toes_of_file("nowhere.glb", "humanoid")["found"]
    assert "two-legged" in facing.toes_of_file("nowhere.glb", "prop_rigid")["reason"]


SCORES = {0: 0.7665, 180: 0.7772, 345: 0.59}    # M1-shorts: 180 beat 0 by 0.011
TOES_FRONT = {"found": True, "turn": 1.2}


def test_a_close_outline_is_settled_by_the_toes():
    d = facing.decide(180, SCORES, TOES_FRONT)
    assert d["turn"] == 0 and d["source"] == "toes" and not d["agree"] and d["outline_margin"] == pytest.approx(0.0107)
    assert d["differ_deg"] > 170


def test_a_clear_outline_with_agreeing_toes_stands():
    d = facing.decide(0, {0: 0.8, 180: 0.65, 15: 0.7}, {"found": True, "turn": 10.0})
    assert d["turn"] == 0 and d["source"] == "outline" and d["agree"]


def test_a_clear_outline_the_toes_contradict_is_overruled_and_flagged():
    d = facing.decide(0, {0: 0.8, 180: 0.6, 270: 0.5}, {"found": True, "turn": 175.0})
    assert d["turn"] == 180 and not d["agree"] and d["differ_deg"] == pytest.approx(175.0)


def test_without_toes_a_clear_outline_decides_as_before():
    d = facing.decide(180, {0: 0.6, 180: 0.8}, {"found": False, "reason": "the legs cannot be told apart"})
    assert d["turn"] == 180 and d["source"] == "outline" and d["agree"] and not d["toes_found"] and d["sure"]
    assert d["toes_reason"].startswith("the legs")
    assert facing.decide(None, None, TOES_FRONT)["turn"] is None   # no trusted outline: left as built, as before


NO_TOES = {"found": False, "reason": "the legs cannot be told apart"}


def test_a_near_tie_without_toes_keeps_the_side_built_as_the_front_and_is_not_sure():
    # R1 of 2026-11-16: the outline put the back forward by 0.014
    d = facing.decide(180, SCORES, NO_TOES)
    assert d["turn"] == 0 and d["source"] == "as built" and not d["sure"] and "nearly a tie" in d["why"]
    assert facing.metrics_of(d)["face_decision_sure"] is False


def test_a_near_tie_the_colour_confirms_is_sure():
    asked = []

    def colour(t):
        asked.append(t)
        return {"turn": t, "head": 11.2, "all": 3.2, "views": {}}

    d = facing.decide(180, SCORES, NO_TOES, colour)
    assert d["turn"] == 0 and d["sure"] and asked == [0] and d["colour"]["head"] == 11.2
    assert "confirms" in d["why"]


def test_a_near_tie_the_colour_clearly_contradicts_turns_the_other_way():
    d = facing.decide(0, {0: 0.80, 180: 0.79, 345: 0.7}, NO_TOES, lambda t: {"turn": t, "head": -6.0, "all": -2.0, "views": {}})
    assert d["turn"] == 180 and d["source"] == "colour" and d["sure"]
    assert d["colour"]["turn"] == 180 and d["colour"]["head"] == 6.0   # the evidence as seen from the turn taken


@pytest.mark.parametrize("head,whole", [(3.3, 1.0), (-3.3, -1.0), (5.0, 0.5), (-5.0, -1.0), (0.0, 4.6)])
def test_weak_colour_evidence_settles_nothing(head, whole):
    # the largest wrong-way readings over the library's clearly settled builds: head 1.8, whole figure 1.2; with the
    # haired drawn front against a bald model the head read 3.3 the wrong way
    d = facing.decide(180, SCORES, NO_TOES, lambda t: {"turn": t, "head": head, "all": whole, "views": {}})
    assert d["turn"] == 0 and not d["sure"]


def test_a_clear_outline_is_not_asked_about_colour():
    def colour(t):
        raise AssertionError("not asked")

    d = facing.decide(0, {0: 0.8, 180: 0.6}, NO_TOES, colour)
    assert d["turn"] == 0 and d["sure"] and d["colour"] is None


def card(yaw_deg: float = 0.0) -> np.ndarray:
    """A flat 1.8 m figure 1 cm thick: the 3D step sometimes builds a flat card from a flat-shaded picture."""
    parts = [box(x - 0.05, x + 0.05, 0.0, 0.9, -0.005, 0.005) for x in (-0.1, 0.1)] + [box(-0.2, 0.2, 0.9, 1.8, -0.005, 0.005)]
    v = np.concatenate(parts)
    a = math.radians(yaw_deg)
    c, s = math.cos(a), math.sin(a)
    return np.stack([c * v[:, 0] + s * v[:, 2], v[:, 1], -s * v[:, 0] + c * v[:, 2]], axis=1)


@pytest.mark.parametrize("yaw", [0.0, 37.0])
def test_a_flat_model_has_no_toes_and_is_never_sure(yaw):
    assert facing.depth(card(yaw)) < facing.FLAT_DEPTH < facing.depth(person())
    t = facing.toes(card(yaw))
    assert not t["found"] and t["flat"] and "flat" in t["reason"]
    # the outline puts it 30 degrees round by a hair; a flat model keeps the turn it was built at, and warns
    d = facing.decide(30, {30: 0.80, 210: 0.7994, 0: 0.79}, t, lambda t_: {"turn": t_, "head": 9.0, "all": 3.0, "views": {}})
    assert d["turn"] == 0 and not d["sure"] and d["flat"] and "flat" in d["why"]


def test_colour_evidence_on_pictures(tmp_path):
    """The drawn front matches the render that shows a face-coloured head, the back the one that shows hair."""
    from PIL import Image

    def fig(path, head, body):
        a = np.zeros((200, 100, 4), np.uint8)
        a[:40, 30:70] = (*head, 255)
        a[40:, 20:80] = (*body, 255)
        Image.fromarray(a, "RGBA").save(path)
        return path

    skin, hair, shirt = (230, 190, 160), (60, 30, 10), (40, 120, 200)
    pics = {"front": fig(tmp_path / "f.png", skin, shirt), "back": fig(tmp_path / "b.png", hair, shirt)}
    renders = {y: fig(tmp_path / f"r{y}.png", skin if y == 0 else hair, shirt) for y in range(0, 360, 15)}
    renders[180] = fig(tmp_path / "r180.png", hair, shirt)
    ev = facing.colour_evidence(pics, renders, 0)
    assert ev["head"] > facing.COLOUR_HEAD_MIN and ev["turn"] == 0
    assert facing.colour_evidence(pics, renders, 180)["head"] < -facing.COLOUR_HEAD_MIN
    ask = facing.colour_of_files(pics, None)
    assert ask is None and facing.colour_evidence({}, renders, 0) is None


def test_turn_scores_and_margin():
    sq = np.zeros((16, 16), bool)
    sq[2:14, 4:12] = True
    wide = np.zeros((16, 16), bool)
    wide[2:14, 2:14] = True
    refs = {"front": sq, "left": wide, "back": sq, "right": wide}
    rendered = {y: (sq if y in (0, 180) else wide) for y in range(0, 360, 15)}
    sc = facing.turn_scores(refs, rendered)
    assert sc[0] == pytest.approx(1.0) and sc[180] == pytest.approx(1.0)
    assert facing.margin(sc, 0) == pytest.approx(0.0) and facing.margin({"0": 0.9, "180": 0.5}, 0) == pytest.approx(0.4)
    assert facing.margin(None, 0) is None


def test_the_check_fails_when_the_tests_disagree_and_says_why():
    c = quality.facing_tests_check(180, 1, 179.0, 0, 180)
    assert not c["passed"] and c["severity"] == "warn" and c["fix"]["params"] == {"face_yaw": 0}
    assert "180 degrees" in c["help"] and "1 degrees" in c["help"] and "179" in c["help"]
    ok = quality.facing_tests_check(0, 5, 5.0, 0, 0)
    assert ok["passed"] and "agree" in ok["help"]


CAT = {"id": "humanoid"}


def test_prep_decides_with_both_tests_and_records_them():
    m = {"silhouette_offset": 180, "silhouette_sign": 1, "silhouette_iou": 0.78, "silhouette_turn_scores": {str(k): v for k, v in SCORES.items()}}
    r = prep.resolve_facing(CAT, "auto", m, TOES_FRONT)
    assert r["face_yaw_applied"] == 0 and r["face_best_turn"] == 0 and r["facing_source"] == "toes"
    assert r["face_outline_turn"] == 180 and r["face_toes_turn"] == 1.2 and r["face_tests_agree"] is False
    assert r["face_decided_by"] == "toes" and r["face_outline_margin"] == pytest.approx(0.0107)
    assert r["face_remainder_deg"] == pytest.approx(0.0)   # they disagree: the decided turn is the basis
    checks = prep.prep_checks({}, {}, r, "humanoid", None)
    by = {c["name"]: c for c in checks}
    assert not by["facing_tests"]["passed"] and "facing" not in by  # the old circular check is gone
    # a chosen turn is applied as chosen, and the two tests are still recorded
    chosen = prep.resolve_facing(CAT, 90, m, TOES_FRONT)
    assert chosen["face_yaw_applied"] == 90 and chosen["facing_source"] == "chosen" and chosen["face_tests_agree"] is False


def test_prep_without_toes_is_unchanged_and_adds_no_check():
    m = {"silhouette_offset": 180, "silhouette_sign": 1, "silhouette_iou": 0.78}
    r = prep.resolve_facing(CAT, "auto", m, {"found": False, "reason": "no"})
    assert r["face_yaw_applied"] == 180 and r["facing_source"] == "audit" and r["face_toes_found"] is False
    # one test only: the check is there, as "not measured", never a pass; the outline's turn is named in it
    c = {c["name"]: c for c in prep.prep_checks({}, {}, r, "humanoid", None)}["facing_tests"]
    assert not c["passed"] and c["vars"]["measured"] is False and c["value"] == "not measured" and "fix" not in c
    assert c["help"].startswith("Not measured: ") and "toes" in c["help"] and "180 degrees" in c["help"] and "no" in c["help"]
    # a model with no toes test at all (not a person) has no facing check
    assert "facing_tests" not in {c["name"] for c in prep.prep_checks({}, {}, r, "quadruped", None)}


def test_the_remainder_after_the_quarter_turn_comes_from_the_feet():
    # W1-clothed: outline turn 285, applied 270; the feet say 303
    m = {"silhouette_offset": 75, "silhouette_sign": 1, "silhouette_iou": 0.73}
    r = prep.resolve_facing(CAT, "auto", m, {"found": True, "turn": 303.4})
    assert r["face_yaw_applied"] == 270 and r["face_remainder_deg"] == pytest.approx(33.4)
    r = prep.resolve_facing(CAT, "auto", m, None)
    assert r["face_remainder_deg"] == pytest.approx(15.0)


def test_an_unsure_decision_warns_in_plain_words_with_a_turn_round_fix():
    m = {"silhouette_offset": 180, "silhouette_sign": 1, "silhouette_iou": 0.78, "silhouette_turn_scores": {str(k): v for k, v in SCORES.items()}}
    r = prep.resolve_facing(CAT, "auto", m, NO_TOES)
    assert r["face_yaw_applied"] == 0 and r["face_decision_sure"] is False and r["face_decided_by"] == "as built"
    c = {c["name"]: c for c in prep.prep_checks({}, {}, r, "humanoid", None)}["facing_tests"]
    assert not c["passed"] and c["severity"] == "warn" and c["vars"]["unsure"] and c["fix"]["params"] == {"face_yaw": 180}
    assert c["fix_label"] == "Turn it round" and "looks almost the same from the front and from behind" in c["help"]
    assert "the legs cannot be told apart" in c["help"] and "turned 0 degrees" in c["help"]
    flat = prep.resolve_facing(CAT, "auto", m, {"found": False, "reason": "the model is flat", "flat": True})
    c = {c["name"]: c for c in prep.prep_checks({}, {}, flat, "humanoid", None)}["facing_tests"]
    assert "flat, like a card" in c["help"]


def test_autopilot_stops_on_an_unsure_facing_in_plain_words():
    from vgac import autopilot

    c = quality.facing_unsure_check(0, 0, False, "no feet")
    text = [t for st, n, test, t in autopilot.STOP_AND_ASK if n == "facing_tests"][0]
    assert [test for st, n, test, t in autopilot.STOP_AND_ASK if n == "facing_tests"][0](c)
    assert "could not tell the character's front from its back" in text(c)
    old = quality.facing_tests_check(180, 1, 179.0, 0, 180)
    assert "disagree by about 179 degrees" in text(old)


# --- facing_tests says what happened, branch by branch -----------------------------------------------------------


def facing_help(m: dict) -> dict:
    return {c["name"]: c for c in prep.prep_checks({}, {}, m, "humanoid", None)}["facing_tests"]


ROBE = {"found": False, "reason": "the legs cannot be told apart (a robe, a long skirt, or legs together)"}
NEAR_TIE = {"silhouette_offset": 195, "silhouette_sign": 1, "silhouette_iou": 0.78,
            "silhouette_turn_scores": {"165": 0.80, "345": 0.786, "0": 0.785, "15": 0.787}}   # R1: outline 165 by 0.014


def test_branch_clear_outline_without_toes_names_the_turn_applied():
    m = {"silhouette_offset": 345, "silhouette_sign": 1, "silhouette_iou": 0.8, "silhouette_turn_scores": {"15": 0.8, "195": 0.6}}
    c = facing_help(prep.resolve_facing(CAT, "auto", m, ROBE))
    assert not c["passed"] and c["vars"]["measured"] is False
    h = c["help"]
    assert "alone clearly says turn 15 degrees" in h and "it was turned 0 degrees, as the clean-up turns by quarter turns only" in h
    assert "(" not in h.split("because", 1)[1].replace("(where the toes point)", "")   # no bracketed examples


def test_branch_toes_agree_names_the_turn_applied():
    c = facing_help(prep.resolve_facing(CAT, "auto", {"silhouette_offset": 0, "silhouette_sign": 1, "silhouette_iou": 0.8,
                                                      "silhouette_turn_scores": {"0": 0.8, "180": 0.6}}, TOES_FRONT))
    assert c["passed"] and "agree. It was turned 0 degrees." in c["help"]


def test_branch_toes_decided_a_disagreement_says_which_it_went_with():
    m = {"silhouette_offset": 180, "silhouette_sign": 1, "silhouette_iou": 0.78, "silhouette_turn_scores": {str(k): v for k, v in SCORES.items()}}
    c = facing_help(prep.resolve_facing(CAT, "auto", m, TOES_FRONT))
    assert not c["passed"] and "It went with the toes and was turned 0 degrees." in c["help"]
    assert "says turn it 180 degrees, but its toes say 1 degrees" in c["help"]


def test_branch_colour_confirmed_passes_and_names_the_turn_applied_not_the_outlines():
    colour = lambda t: {"turn": t, "head": 11.2, "all": 3.2, "views": {}}   # noqa: E731
    r = prep.resolve_facing(CAT, "auto", NEAR_TIE, ROBE, colour)
    assert r["face_yaw_applied"] == 0 and r["face_decision_sure"]
    c = facing_help(r)
    assert c["passed"] and c["vars"]["state"] == "colour"
    h = c["help"]
    assert "confirm the side the 3D step built as its front. It was turned 0 degrees." in h
    assert "165" not in h and "because the legs cannot be told apart," in h and "(" not in h


def test_branch_colour_overruled_passes_and_says_it_was_turned_round():
    m = {"silhouette_offset": 0, "silhouette_sign": 1, "silhouette_iou": 0.78, "silhouette_turn_scores": {"0": 0.80, "180": 0.79}}
    r = prep.resolve_facing(CAT, "auto", m, ROBE, lambda t: {"turn": t, "head": -6.0, "all": -2.0, "views": {}})
    assert r["face_yaw_applied"] == 180 and r["face_decided_by"] == "colour"
    c = facing_help(r)
    assert c["passed"] and "built it facing backwards, so it was turned round: 180 degrees." in c["help"]


def test_branch_not_sure_is_plain_with_no_nested_brackets():
    c = facing_help(prep.resolve_facing(CAT, "auto", NEAR_TIE, ROBE))
    h = c["help"]
    assert not c["passed"] and c["fix"]["params"] == {"face_yaw": 180} and c["fix_label"] == "Turn it round"
    assert "its toes could not be read because the legs cannot be told apart, and the colours" in h
    assert "(" not in h and "It was turned 0 degrees" in h


def test_branch_flat_model_says_so():
    c = facing_help(prep.resolve_facing(CAT, "auto", NEAR_TIE, {"found": False, "reason": "the model is flat", "flat": True}))
    assert not c["passed"] and "flat, like a card" in c["help"] and "(" not in c["help"]


def test_branch_chosen_by_the_person_passes_and_says_what_the_app_would_have_done():
    c = facing_help(prep.resolve_facing(CAT, 180, NEAR_TIE, ROBE))
    assert c["passed"] and c["vars"]["state"] == "chosen"
    assert c["help"] == "It was turned 180 degrees, as you chose. The app's own tests would have turned it 0 degrees."
    same = facing_help(prep.resolve_facing(CAT, 0, NEAR_TIE, ROBE))
    assert same["help"] == "It was turned 0 degrees, as you chose."


def test_branch_toes_without_an_outline_says_it_was_left_as_built():
    c = facing_help(prep.resolve_facing(CAT, "auto", {"silhouette_offset": 0, "silhouette_iou": 0.2}, TOES_FRONT))
    assert not c["passed"] and "left as the 3D step built it" in c["help"] and "turned 0 degrees" in c["help"]


def test_plain_reason_drops_bracketed_examples():
    assert quality.plain_reason("the legs cannot be told apart (a robe, a long skirt, or legs together)") == "the legs cannot be told apart"
    assert quality.plain_reason(None) == "they were not found"
