"""Facing beyond the quarter-turn window (bakeoff/facing): a sure facing decision turns the model to the nearest quarter
turn however far the audit's best turn is from it, and a model left more than 45 degrees from its decided front is
warned about, whatever kind of model it is. The golden-set wheelbarrow: audit best turn 60 (sure, margin 0.068), left
side-on by the old 20-degree window with nothing said."""

from __future__ import annotations

from vgac import guidance, quality
from vgac.stages import prep

NO_TOES = {"found": False, "reason": "not a two-legged character"}


def _audit(best: int, margin: float = 0.08, top: float = 0.70) -> dict:
    """Mesh audit metrics whose outline test decides `best` (sign +1), beating the opposite turn by `margin`."""
    scores = {t: round(top - 0.2 - 0.001 * quality.angle_diff(t, best), 4) for t in range(0, 360, 15)}
    scores[best % 360] = top
    scores[(best + 180) % 360] = round(top - margin, 4)
    return {"silhouette_offset": (-best) % 360, "silhouette_sign": 1, "silhouette_iou": top, "silhouette_turn_scores": scores}


def test_a_sure_decision_outside_the_window_turns_to_the_nearest_quarter():
    r = prep.resolve_facing({"silhouette_min": 0.55}, "auto", _audit(60), NO_TOES, None)
    assert r["face_best_turn"] == 60 and r["face_decision_sure"] is True
    assert r["face_yaw_applied"] == 90  # was 0: the wheelbarrow stayed side-on


def test_planted_turns_all_end_within_45_of_forward():
    for built in (60, 90, 120, 180, 300, 15, 330):
        r = prep.resolve_facing({"silhouette_min": 0.55}, "auto", _audit(built), NO_TOES, None)
        assert quality.angle_diff(r["face_best_turn"], r["face_yaw_applied"]) <= 45, built


def test_an_unsure_decision_keeps_the_window():
    r = prep.resolve_facing({"silhouette_min": 0.55}, "auto", _audit(60, margin=0.01), NO_TOES, None)
    assert r["face_decision_sure"] is False and r["face_yaw_applied"] == quality.auto_turn(r["face_best_turn"])
    assert quality.auto_turn(60) == 0 and quality.auto_turn(60, sure=True) == 90 and quality.auto_turn(30, sure=True) == 0


def test_a_model_left_far_from_its_decided_front_is_warned_about():
    m = {"face_best_turn": 60, "face_yaw_applied": 0, "facing_source": "audit", "face_decision_sure": False}
    c = prep.facing_check(m, "vehicle")
    assert c is not None and c["name"] == "facing_tests" and not c["passed"]
    assert c["vars"]["fix_yaw"] == 90 and c["value"] == 60
    d = guidance.describe_check(c)
    assert "60 degrees" in d["help"] and d["fix"]["params"] == {"face_yaw": 90}
    # within the tolerance, or chosen by hand: no new warning for a model with no second test
    assert prep.facing_check({**m, "face_best_turn": 30}, "vehicle") is None
    assert prep.facing_check({**m, "facing_source": "chosen"}, "vehicle") is None
