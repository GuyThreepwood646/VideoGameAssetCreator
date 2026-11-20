"""WP-47 item 4: a rigid asset with declared moving parts says they are not rigged yet, never a plain "as intended"
success (guidance bones check; quality.rig_checks moving_parts). The check still passes, so it does not block."""

from __future__ import annotations

from vgac import guidance, quality


def _bones(moving=None):
    checks = quality.rig_checks({"bones": 0}, rig_expected=False, moving_parts=moving)
    return checks[0]


def test_no_declared_moving_parts_keeps_the_old_sentence():
    described = guidance.describe_check(_bones())
    assert described["help"] == "No skeleton, as intended for this kind of asset."


def test_declared_moving_parts_are_named_as_not_rigged():
    described = guidance.describe_check(_bones(["lid", "handle"]))
    text = described["help"]
    assert "lid, handle" in text and "not rigged" in text
    assert "as intended" not in text
    assert described["passed"] is True  # a note, not a gate


def test_a_brace_in_a_part_name_does_not_break_the_sentence():
    text = guidance.describe_check(_bones(["{odd} lid"]))["help"]
    assert "{odd} lid" in text


def test_rigged_models_are_unchanged():
    checks = quality.rig_checks({"bones": 12, "roots": ["root"]}, rig_expected=True, moving_parts=["lid"])
    assert [c["name"] for c in checks][0] == "bones" and checks[0]["passed"]
