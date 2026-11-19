"""WP-09: the hand redraw and the whole-figure redraw run through the character guard (reroll, then warn)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import test_buildpose as tb
from vgac import buildpose, readiness
from vgac.stages import concept, mesh as mesh_stage

OK = {"ok": True, "reasons": [], "not_compared": [], "measures": {}}
BAD = {"ok": False, "reasons": ["footwear was added to bare feet"], "not_compared": [], "measures": {}}


def _sc(tmp_path):
    d = tmp_path / "v"
    d.mkdir()
    sc = tb._SC(d, None)
    sc.version.metrics = {}
    return sc, tb._png(tmp_path / "in.png")


def test_the_three_wordings_carry_the_same_clothing_sentence_and_name_no_garment():
    for text in (buildpose.pose_prompt(), mesh_stage.HAND_EDIT, readiness.HUMANOID_REDRAW):
        assert buildpose.KEEP_WORN in text
    assert readiness.is_humanoid_redraw(readiness.HUMANOID_REDRAW) and not readiness.is_humanoid_redraw("Make it blue.")


def test_hand_edit_guard_rerolls_then_refuses(tmp_path, monkeypatch):
    sc, src = _sc(tmp_path)
    monkeypatch.setattr(buildpose, "guard", lambda a, b: BAD)
    out = asyncio.run(mesh_stage._edit_picture(sc, mesh_stage.HAND_EDIT, src, None, guarded=True))
    assert out is None and len(sc.graphs) == 3
    assert set(sc.registered) == {"hand_refused_picture", "hand_refused_cutout"} and not sc.version.params.get("picture_edit")
    assert sc.version.metrics["hand_guard"]["tries"] == 3 and not sc.version.metrics["hand_guard"]["ok"]


def test_hand_edit_guard_keeps_the_first_pass_and_one_reroll(tmp_path, monkeypatch):
    sc, src = _sc(tmp_path)
    monkeypatch.setattr(buildpose, "guard", lambda a, b: OK)
    out = asyncio.run(mesh_stage._edit_picture(sc, mesh_stage.HAND_EDIT, src, None, guarded=True))
    assert out is not None and len(sc.graphs) == 1 and sc.version.params["picture_edit"] and mesh_stage.EDITED_KEY in sc.registered
    other = tmp_path / "second"
    other.mkdir()
    sc2, src2 = _sc(other)
    verdicts = iter([BAD, OK])
    monkeypatch.setattr(buildpose, "guard", lambda a, b: next(verdicts))
    assert asyncio.run(mesh_stage._edit_picture(sc2, mesh_stage.HAND_EDIT, src2, None, guarded=True)) is not None
    assert len(sc2.graphs) == 2


def test_an_unguarded_edit_is_one_pass_with_no_guard(tmp_path, monkeypatch):
    sc, src = _sc(tmp_path)
    monkeypatch.setattr(buildpose, "guard", lambda a, b: pytest.fail("the guard must not run"))
    assert asyncio.run(mesh_stage._edit_picture(sc, "Make the cape red.", src, None)) is not None and len(sc.graphs) == 1


def test_the_hand_guard_check_reaches_the_card():
    c = buildpose.redraw_guard_check("hand_guard", "The hand redraw", {**BAD, "tries": 3}, True,
                                     "It was not used; the 3D model was built from your own picture.")
    from vgac import guidance

    assert guidance.CHECK_GUIDE["hand_guard"]["severity"] == "warn" and guidance.CHECK_GUIDE["redraw_guard"]["severity"] == "warn"
    assert not c["passed"] and "was not used" in c["help"]


def _refine_sc(tmp_path):
    d = tmp_path / "c"
    d.mkdir()
    sc = tb._SC(d, None)
    sc.parent = SimpleNamespace(id="parentver1", files={"image": "x"}, params={"source": "generate"})
    sc.version.metrics = {}
    sc.version.stage = "concept"
    sc.file = lambda key: None
    return sc


def _run_refine(sc, monkeypatch, instruction, verdicts):
    async def no_loras(*a, **k):
        return None

    async def no_cutout(*a, **k):
        return None
    person = tb._png(sc.dir / "person.png")
    monkeypatch.setattr(concept, "edit_loras", no_loras)
    monkeypatch.setattr(concept, "_cutout", no_cutout)
    monkeypatch.setattr("vgac.library.version_file", lambda v, key, _s: person)
    sc.settings = SimpleNamespace()
    monkeypatch.setattr(buildpose, "guard", lambda a, b: next(verdicts))
    asyncio.run(concept.refine(sc, instruction, None, None, {}))


def test_the_whole_figure_redraw_rerolls_then_keeps_the_last_with_a_warning(tmp_path, monkeypatch):
    sc = _refine_sc(tmp_path)
    _run_refine(sc, monkeypatch, readiness.HUMANOID_REDRAW, iter([BAD, BAD, BAD]))
    assert len(sc.graphs) == 3
    rg = sc.version.metrics["redraw_guard"]
    assert rg["tries"] == 3 and not rg["ok"] and rg["reasons"] == BAD["reasons"]
    assert sc.registered["image"].name == "image.png"  # the picture is still kept: the person asked for it


def test_the_whole_figure_redraw_stops_at_the_first_good_try(tmp_path, monkeypatch):
    sc = _refine_sc(tmp_path)
    _run_refine(sc, monkeypatch, readiness.HUMANOID_REDRAW, iter([BAD, OK]))
    assert len(sc.graphs) == 2 and sc.version.metrics["redraw_guard"]["ok"]


def test_other_refines_are_not_guarded(tmp_path, monkeypatch):
    sc = _refine_sc(tmp_path)
    _run_refine(sc, monkeypatch, "Make the cape red.", iter([]))
    assert len(sc.graphs) == 1 and "redraw_guard" not in sc.version.metrics


def test_bare_hands_sentence_needs_both_hands_measured():
    one = buildpose.pose_prompt({"hands": {"left": {"skin_gap": 3.0}, "right": {"state": "not_found"}}})
    assert buildpose.BARE_HANDS.strip() not in one
    both = buildpose.pose_prompt({"hands": {"left": {"skin_gap": 3.0}, "right": {"skin_gap": 4.0}}})
    assert buildpose.BARE_HANDS.strip() in both


def test_a_failed_reroll_falls_back_instead_of_failing_the_step(tmp_path, monkeypatch):
    sc, src = _sc(tmp_path)
    monkeypatch.setattr(buildpose, "guard", lambda a, b: BAD)
    real = mesh_stage._edit_once
    calls = []

    async def flaky(*a, **k):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("the edited picture came out without the character")
        return await real(*a, **k)
    monkeypatch.setattr(mesh_stage, "_edit_once", flaky)
    assert asyncio.run(mesh_stage._edit_picture(sc, mesh_stage.HAND_EDIT, src, None, guarded=True)) is None
    assert sc.version.metrics["hand_guard"]["tries"] == 1 and not sc.version.metrics["hand_guard"]["ok"]
