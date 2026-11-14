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


def test_without_toes_the_outline_decides_as_before():
    d = facing.decide(180, SCORES, {"found": False, "reason": "the legs cannot be told apart"})
    assert d["turn"] == 180 and d["source"] == "outline" and d["agree"] and not d["toes_found"]
    assert d["toes_reason"].startswith("the legs")
    assert facing.decide(None, None, TOES_FRONT)["turn"] is None   # no trusted outline: left as built, as before


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
