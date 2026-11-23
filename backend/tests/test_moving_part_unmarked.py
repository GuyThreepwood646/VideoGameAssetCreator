"""An unmarked declared moving part is a failed check the app shows as likely to show in the game (honest Ready), and its
sentence is the plain question that names the parts to mark. Autopilot stops on it once after step 5 through the likely
tier, not through a STOP_AND_ASK entry (F16)."""

from __future__ import annotations

from types import SimpleNamespace

from vgac import autopilot, check_headlines, check_tiers, guidance, quality


def _unmarked():
    return quality.check("moving_parts", False, 0, 1, vars={"moved": [], "unplaced": ["gate leaf"], "not_found": []})


def test_an_unmarked_moving_part_is_likely_to_show():
    assert check_tiers.tier_of({"name": "moving_parts", "passed": False}) == "likely"


def test_the_sentence_names_the_part_and_says_how_to_mark_it():
    text = guidance.describe_check(_unmarked())["help"]
    assert "gate leaf" in text
    assert "step 5" in text and "Make it move again" in text
    assert "not rigged yet" not in text


def test_the_headline_is_plain_and_has_no_numbers():
    assert check_headlines.headline_for("moving_parts")
    assert not any(ch.isdigit() for ch in check_headlines.headline_for("moving_parts"))


def test_autopilot_stops_once_after_step_5_on_an_unmarked_part():
    described = guidance.describe_check(_unmarked())
    reason = autopilot.attention_reason(SimpleNamespace(stage="rig", metrics={"checks": [described]}), "block")
    assert reason and "gate leaf" in reason


def test_a_marked_part_does_not_stop_autopilot():
    ok = quality.check("moving_parts", True, 1, 1, vars={"moved": ["gate leaf"], "unplaced": [], "not_found": []})
    assert autopilot.attention_reason(SimpleNamespace(stage="rig", metrics={"checks": [guidance.describe_check(ok)]}), "block") is None
