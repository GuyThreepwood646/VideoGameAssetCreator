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


# --- which cases are which check (rig._cut_moving_parts) ----------------------------------------------------------------
# moving_parts (likely): a declared part with no points, or a point that found no piece when the Blender pass ran.
# moving_cut (look): the Blender pass itself failed, so the points were never tried.

import asyncio  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402

from vgac.stages import rig  # noqa: E402


def _sc(tmp: Path, parts, blender):
    return SimpleNamespace(asset=SimpleNamespace(parts=parts), set_params=lambda **k: None, set_metrics=lambda **k: None,
                           progress=lambda *a, **k: None, dir=tmp, register=lambda *a, **k: None,
                           settings=SimpleNamespace(blender_timeout_seconds=1), blender=blender)


def test_a_failed_blender_pass_is_a_look_check_not_a_likely_one(tmp_path, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("Blender crashed")

    monkeypatch.setattr(rig, "moving_points_for", lambda sc: [{"name": "gate leaf", "kind": "hinge", "locations": [[0, 0, 0]]}])
    checks = asyncio.run(rig._cut_moving_parts(_sc(tmp_path, [{"name": "gate leaf", "kind": "hinge"}], boom), tmp_path / "m.glb"))
    assert [c["name"] for c in checks] == ["moving_cut"]
    assert check_tiers.tier_of(checks[0]) == "look"


def test_a_point_that_finds_no_piece_is_still_likely(tmp_path, monkeypatch):
    async def ran_but_found_nothing(sc, args):
        report = Path(args[args.index("--report") + 1])
        report.write_text(json.dumps({"ok": True, "parts": [], "clips": []}), encoding="utf-8")

    async def blender(script, args, **kwargs):
        await ran_but_found_nothing(None, args)

    monkeypatch.setattr(rig, "moving_points_for", lambda sc: [{"name": "gate leaf", "kind": "hinge", "locations": [[0, 0, 0]]}])
    checks = asyncio.run(rig._cut_moving_parts(_sc(tmp_path, [{"name": "gate leaf", "kind": "hinge"}], blender), tmp_path / "m.glb"))
    assert [c["name"] for c in checks] == ["moving_parts"]
    assert checks[0]["passed"] is False and check_tiers.tier_of(checks[0]) == "likely"


def test_an_unmarked_part_alone_is_only_the_likely_check(tmp_path, monkeypatch):
    monkeypatch.setattr(rig, "moving_points_for", lambda sc: [])
    checks = asyncio.run(rig._cut_moving_parts(_sc(tmp_path, [{"name": "gate leaf", "kind": "hinge"}], None), tmp_path / "m.glb"))
    assert [c["name"] for c in checks] == ["moving_parts"] and check_tiers.tier_of(checks[0]) == "likely"
