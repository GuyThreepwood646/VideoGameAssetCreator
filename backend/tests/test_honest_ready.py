"""Honest "Ready to use": a failed check the app marks "likely to show in the game" is never ready and stops autopilot;
a check that was not measured is a note, never a problem; the two hit-box faults on one skeleton are one item with one
re-fit. Numbers come from the golden-set records in bakeoff/scorecard/app (tree 3ee3bd04: folded_edge_share 0.24114,
route plants, limit 0.20). The hit-box numbers are the audit's (FINDINGS F14, F17): the golden records keep no hit-box
values, so the pair in the combined test is one constructed skeleton with cover 68 percent and overreach 7.7 times."""

from __future__ import annotations

from types import SimpleNamespace

from vgac import autopilot, check_tiers, guidance, meshgeom, quality
from vgac.stages import rig

HITBOX_DOC = {"mode": "auto", "count": 11, "cover_min": 0.85, "overreach_max": 1.6}


def _v(stage: str = "export", checks=()):
    return SimpleNamespace(stage=stage, metrics={"checks": list(checks)})


def _failing(checks: list[dict]) -> list[dict]:
    return [c for c in checks if not c["passed"]]


def _described(c: dict) -> dict:
    return guidance.describe_check(c)


# --- the torn tree (F8) -----------------------------------------------------------------------------------------------


def _tree_model_whole() -> dict:
    geo = {"route": "plants", "folded_edge_share": 0.24114,
           "problems": ["surface folded back on itself along 24.1% of its edges (up to 20% is normal)"]}
    return _described(meshgeom.check("foliage", geo))


def test_the_torn_tree_is_likely_to_show_and_autopilot_stops_on_it():
    c = _tree_model_whole()
    assert c["passed"] is False and check_tiers.tier_of(c) == "likely"
    reason = autopilot.attention_reason(_v(stage="mesh", checks=[c]), "block")
    assert reason and "damaged" in reason and c["help"] in reason


def test_plants_have_no_rebuild_route_and_buildings_still_do():
    assert not meshgeom.rebuilds_wanted("foliage")
    assert meshgeom.rebuilds_wanted("environment")


# --- the hit boxes (F14, F17) -----------------------------------------------------------------------------------------


def test_hit_box_cover_and_overreach_are_likely_to_show_and_fit_is_not():
    assert check_tiers.tier_of({"name": "hitbox_cover", "passed": False}) == "likely"
    assert check_tiers.tier_of({"name": "hitbox_overreach", "passed": False}) == "likely"
    assert check_tiers.tier_of({"name": "hitbox_fit", "passed": False}) == "look"


def test_a_beetle_sized_overreach_stops_autopilot_at_the_rig_step():
    cs = _failing(rig.hitbox_checks(dict(HITBOX_DOC, cover=0.95, overreach=7.7)))
    assert [c["name"] for c in cs] == ["hitbox_overreach"]
    assert autopilot.attention_reason(_v(stage="rig", checks=[_described(cs[0])]), "block")


def test_both_hit_box_faults_on_one_skeleton_are_one_item_with_one_refit():
    cs = rig.hitbox_checks(dict(HITBOX_DOC, cover=0.68, overreach=7.7))
    assert len(cs) == 1, "one combined item, not a bigger and a smaller button"
    c = _described(cs[0])
    assert c["vars"]["both"] is True
    assert c["fix"]["params"] == {"hitbox_fit": "normal"} and c["fix"]["label"] == "Fit the hit boxes again"
    assert "wrong size in places" in c["help"] and "thin air" in c["help"] and "pass through" in c["help"]
    assert "bigger" not in c["fix"]["label"] and "smaller" not in c["fix"]["label"]
    assert "will fix" not in c["help"] and "will work" not in c["help"]


def test_one_hit_box_fault_keeps_its_own_fix():
    small = _described(_failing(rig.hitbox_checks(dict(HITBOX_DOC, cover=0.6, overreach=1.0)))[0])
    assert small["fix"]["params"] == {"hitbox_fit": "loose"} and small["fix"]["label"] == "Make the hit boxes bigger"
    big = _described(_failing(rig.hitbox_checks(dict(HITBOX_DOC, cover=0.95, overreach=3.0)))[0])
    assert big["fix"]["params"] == {"hitbox_fit": "snug"} and big["fix"]["label"] == "Make the hit boxes smaller"


# --- checks that were not measured (F9 condition) ---------------------------------------------------------------------


def test_a_check_that_was_not_measured_is_a_note_not_a_problem():
    c = _described(quality.not_measured("bones", "the skeleton", "the outline fit failed"))
    assert c["passed"] is False and c["help"].startswith("Not measured")
    assert not check_tiers.is_problem(c)
    # even a likely id that was not measured never stops autopilot
    assert autopilot.attention_reason(_v(stage="export", checks=[c]), "block") is None


def test_a_measured_failure_of_the_same_id_is_a_problem():
    c = _described(quality.check("bones", False, 0.4, 1.0))
    assert check_tiers.is_problem(c)
    assert autopilot.attention_reason(_v(stage="export", checks=[c]), "block")


# --- autopilot's rule for likely failures -----------------------------------------------------------------------------


def test_look_and_advice_failures_do_not_stop_autopilot():
    look = _described(quality.check("texture_sharp", False, 0.2, 0.5))
    assert check_tiers.tier_of(look) == "look"
    assert autopilot.attention_reason(_v(stage="export", checks=[look]), "block") is None


def test_likely_stop_follows_stop_on_as_before():
    c = _described(quality.check("size_m", False, 3.0, 1.8))
    assert autopilot.attention_reason(_v(stage="export", checks=[c]), "block")
    assert autopilot.attention_reason(_v(stage="export", checks=[c]), "never") is None
    assert autopilot.attention_reason(_v(stage="export", checks=[c]), "warn") is None  # "warn" stops through _passes


def test_the_combined_hit_box_item_has_its_own_headline_and_the_tier_is_likely():
    cs = [_described(c) for c in _failing(rig.hitbox_checks(dict(HITBOX_DOC, cover=0.68, overreach=7.7)))]
    metrics = check_tiers.annotate_metrics({"checks": cs})
    (c,) = metrics["checks"]
    assert c["tier"] == "likely" and c["headline"] == check_tiers.BOTH_HITBOX_HEADLINE


def test_the_unmeasured_check_is_served_without_a_tier_problem_flag():
    c = _described(quality.not_measured("texture_sharp", "how sharp the colour texture is", "the outline fit failed"))
    assert check_tiers.is_problem(c) is False
    assert "headline" not in check_tiers.annotate_metrics({"checks": [c]})["checks"][0]
