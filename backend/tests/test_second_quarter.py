"""The second three-quarter view (stages/turnaround.py wants_second_quarter, second_quarter_check, _add_views; docs
AFTER-TEST-CHANGES.md A2): drawn by default for every asset whose clean-up paints from the views, a plain warning with a
fix when it is missing, and the fix draws only that view."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from vgac import guidance, imaging
from vgac.stages import turnaround

from test_stage_offloop import FakeDB, FakeSC, _lineage, _rgba

STANDARD = ["front", "left", "back", "right", "quarter"]


@pytest.mark.parametrize("category, params, wanted", [
    ("humanoid", {}, True),
    ("humanoid", {"source_project": False}, True),   # the view is also for the face pass; the warning still applies
    ("quadruped", {}, False),
    ("prop_rigid", {}, False),
    ("vehicle", {}, False),
    ("quadruped", {"source_project": True}, True),   # the person turned the paint-from-the-views pass on
    (None, {}, False),
])
def test_which_assets_get_the_second_three_quarter_view(category, params, wanted):
    assert turnaround.wants_second_quarter(category, params) is wanted
    views = turnaround.with_second_quarter(list(STANDARD), category, params)
    assert ("quarter2" in views) is wanted
    if wanted:
        assert views == ["front", "left", "back", "right", "quarter", "quarter2"]


def test_switched_off_or_no_three_quarter_view_leaves_it_out():
    assert "quarter2" not in turnaround.with_second_quarter(list(STANDARD), "humanoid", {"second_quarter": False})
    assert "quarter2" not in turnaround.with_second_quarter(["front", "back"], "humanoid", {})


def test_the_check_is_none_when_the_view_is_there_or_not_needed():
    both = {"quarter": {}, "quarter2": {}}
    assert turnaround.second_quarter_check("humanoid", {}, both) is None
    assert turnaround.second_quarter_check("quadruped", {}, {"quarter": {}}) is None


@pytest.mark.parametrize("params, words", [({"second_quarter": False}, "was switched off"), ({}, "was not drawn for this version")])
def test_the_warning_in_plain_words_with_a_fix_that_draws_only_that_view(params, words):
    c = turnaround.second_quarter_check("humanoid", params, {"quarter": {}})
    assert c is not None and c["passed"] is False and c["severity"] == "warn"
    assert "far side of the face and body will be painted with less to go on" in c["help"] and words in c["help"]
    assert c["fix"]["stage"] == "turnaround" and c["fix"]["params"] == {"add_views": ["quarter2"], "second_quarter": True}
    assert c["fix_label"] == "Draw the other three-quarter view"
    ok = guidance.describe_check({"name": "second_quarter", "passed": True, "ok": True, "value": 1, "limit": 1})
    assert "fix" not in ok and "Both three-quarter views" in ok["help"]


class MetricsSC(FakeSC):
    """FakeSC whose metrics are the version's own (as library.update_metrics writes them) and with an asset."""

    def __init__(self, tmp, version, db, category="humanoid"):
        super().__init__(tmp, version, db)
        self.asset = SimpleNamespace(category=category, prompt="a knight")
        self.category = {}
        self.ctx = None
        self.copied: list[str] = []

    def set_metrics(self, **kw):
        super().set_metrics(**kw)
        self.version.metrics.update(kw)

    def prefix(self, name):
        return name


@pytest.fixture
def drawn(monkeypatch):
    monkeypatch.setattr(turnaround.library, "version_file", lambda v, key, settings: v.image if key == "image" else None)
    calls: list[tuple[str, int]] = []

    async def fake_generate(sc, src_name, view, seed, lora_strength, extra, base, span, name=None, instance=None):
        calls.append((view, seed))
        raw, cut = _rgba(sc.dir / f"{view}_raw.png"), _rgba(sc.dir / f"{view}.png")
        return {"raw": raw, "cutout": cut, "white": imaging.white_composite(cut), "stats": None}

    monkeypatch.setattr(turnaround, "_generate_view", fake_generate)
    return calls


def parent_without_it(tmp_path, params=None):
    concept, ta, new = _lineage(tmp_path)
    ta.params = {"views": list(STANDARD), "lora_strength": 0.8, **(params or {})}
    ta.metrics = {"views": {v: {"passed": True} for v in STANDARD}}
    new.params = dict(ta.params)
    return concept, ta, new


def test_a_character_made_without_it_warns_when_the_step_finishes(tmp_path, drawn):
    concept, ta, new = parent_without_it(tmp_path, {"second_quarter": False})
    sc = MetricsSC(tmp_path, new, FakeDB(concept, ta, new))
    asyncio.run(turnaround.adjust(sc, {"second_quarter": False}))
    assert "quarter2" not in {v for v, _ in drawn}
    checks = new.metrics["checks"]
    assert [c["name"] for c in checks] == ["second_quarter"] and new.metrics["gate_ok"] is False
    assert checks[0]["fix"]["params"]["add_views"] == ["quarter2"]


def test_the_fix_draws_only_the_missing_view_and_clears_the_warning(tmp_path, drawn):
    concept, ta, new = parent_without_it(tmp_path, {"second_quarter": False})
    ta.metrics["checks"] = [{"name": "second_quarter", "passed": False}]
    sc = MetricsSC(tmp_path, new, FakeDB(concept, ta, new))
    asyncio.run(turnaround.adjust(sc, {"add_views": ["quarter2"], "second_quarter": True}))
    assert [v for v, _ in drawn] == ["quarter2"]
    assert set(new.metrics["views"]) == set(STANDARD) | {"quarter2"}
    assert new.params["views"] == ["front", "left", "back", "right", "quarter", "quarter2"]
    assert new.params["second_quarter"] is True and "add_views" not in new.params
    assert not any(c["name"] == "second_quarter" for c in new.metrics.get("checks") or [])


def test_adding_a_view_that_is_there_draws_nothing(tmp_path, drawn):
    concept, ta, new = parent_without_it(tmp_path)
    ta.metrics["views"]["quarter2"] = {"passed": True}
    ta.params["views"] = STANDARD + ["quarter2"]
    new.params = dict(ta.params)
    sc = MetricsSC(tmp_path, new, FakeDB(concept, ta, new))
    asyncio.run(turnaround.adjust(sc, {"add_views": ["quarter2"]}))
    assert drawn == [] and "checks" not in new.metrics


def test_a_character_run_draws_it_without_a_warning(tmp_path, drawn):
    concept, ta, new = parent_without_it(tmp_path)
    sc = MetricsSC(tmp_path, new, FakeDB(concept, ta, new))
    asyncio.run(turnaround._run_views(sc, turnaround.with_second_quarter(list(STANDARD), "humanoid", {}), {"seed": 1}, concept))
    assert "quarter2" in {v for v, _ in drawn} and "checks" not in new.metrics


def test_a_prop_never_warns(tmp_path, drawn):
    concept, ta, new = parent_without_it(tmp_path)
    sc = MetricsSC(tmp_path, new, FakeDB(concept, ta, new), category="prop_rigid")
    asyncio.run(turnaround._run_views(sc, list(STANDARD), {"seed": 1}, concept))
    assert "checks" not in new.metrics
