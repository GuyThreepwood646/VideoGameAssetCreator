"""Step 2 changes reach the views the person chose (WP-42): one view, several views and all views.

The API refuses a turnaround change that names no view (it used to fall back to the front view without saying so),
sends the chosen views and the typed words on to the job, and the stage redraws only those views and carries every
other view forward with the same file and the same seed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from vgac import reference
from vgac.db import Job, session_factory
from vgac.stages import turnaround

from test_api import _project_asset, client, fake_execute, wait_job  # noqa: F401  (fixtures)
from test_stage_offloop import FakeDB, FakeSC, _lineage, _patch_turnaround, _rgba


def _turnaround_version(client) -> tuple[str, str]:  # noqa: F811
    p, a = _project_asset(client)
    aid = a["id"]
    concept = client.post(f"/api/assets/{aid}/stages/concept/run", json={}).json()
    wait_job(client, concept["job"]["id"])
    client.post(f"/api/versions/{concept['version']['id']}/select")
    turn = client.post(f"/api/assets/{aid}/stages/turnaround/run", json={}).json()
    assert wait_job(client, turn["job"]["id"])["status"] == "done"
    return aid, turn["version"]["id"]


def _payload(job_id: str) -> dict:
    with session_factory()() as db:
        return dict(db.get(Job, job_id).payload)


def test_one_view_goes_to_the_job_with_the_typed_words(client):  # noqa: F811
    aid, tid = _turnaround_version(client)
    out = client.post(f"/api/versions/{tid}/refine", json={"instruction": "remove the people", "views": ["left"]}).json()
    assert wait_job(client, out["job"]["id"])["status"] == "done"
    pay = _payload(out["job"]["id"])
    assert pay["views"] == ["left"] and pay["instruction"] == "remove the people"


def test_a_single_view_field_is_sent_on_as_a_list(client):  # noqa: F811
    aid, tid = _turnaround_version(client)
    out = client.post(f"/api/versions/{tid}/refine", json={"instruction": "x", "view": "back"}).json()
    assert _payload(out["job"]["id"])["views"] == ["back"]


def test_several_views_go_to_the_job_once_each(client):  # noqa: F811
    aid, tid = _turnaround_version(client)
    out = client.post(f"/api/versions/{tid}/refine", json={"instruction": "remove the people", "views": ["left", "back", "left"]}).json()
    assert _payload(out["job"]["id"])["views"] == ["left", "back"]


def test_all_views_go_to_the_job(client):  # noqa: F811
    aid, tid = _turnaround_version(client)
    every = list(client.get(f"/api/versions/{tid}").json()["params"]["views"])
    out = client.post(f"/api/versions/{tid}/refine", json={"instruction": "remove the people", "views": every}).json()
    assert _payload(out["job"]["id"])["views"] == every and len(every) >= 3


def test_a_change_that_names_no_view_is_refused_and_makes_no_version(client):  # noqa: F811
    aid, tid = _turnaround_version(client)
    before = len(client.get(f"/api/assets/{aid}").json()["stages"]["turnaround"])
    for body in ({}, {"views": []}, {"view": ""}):
        r = client.post(f"/api/versions/{tid}/refine", json={"instruction": "remove the people", **body})
        assert r.status_code == 400 and "Choose which views" in r.json()["detail"]
    assert len(client.get(f"/api/assets/{aid}").json()["stages"]["turnaround"]) == before


def test_the_first_picture_refine_still_needs_no_view(client):  # noqa: F811
    p, a = _project_asset(client)
    concept = client.post(f"/api/assets/{a['id']}/stages/concept/run", json={}).json()
    wait_job(client, concept["job"]["id"])
    r = client.post(f"/api/versions/{concept['version']['id']}/refine", json={"instruction": "red cloak"})
    assert r.status_code == 200
    assert client.post(f"/api/versions/{concept['version']['id']}/refine", json={"instruction": "x", "views": ["left"]}).status_code == 400


# --- the stage: only the chosen views change ---------------------------------------------------


class CopySC(FakeSC):
    def copy_in(self, src, name):
        self.copied = getattr(self, "copied", [])
        self.copied.append(Path(src).name)
        return Path(src)


def _parent_with_files(tmp_path, monkeypatch):
    concept, ta, new = _lineage(tmp_path)
    ta.params["views"] = ["front", "left", "back", "right"]
    seeds = {"front": 11, "left": 12, "back": 13, "right": 14}
    ta.metrics["views"] = {v: {"passed": True, "seed": s} for v, s in seeds.items()}
    files = {}
    for v in seeds:
        for key in (v, f"{v}_cutout", f"{v}_white"):
            files[key] = _rgba(tmp_path / f"parent_{key}.png")
    files["image"] = concept.image
    monkeypatch.setattr(turnaround.library, "version_file", lambda ver, key, settings: files.get(key) if ver is ta else (concept.image if key == "image" else None))
    return concept, ta, new, seeds


def _drawn(monkeypatch) -> list[str]:
    drawn: list[str] = []
    real = turnaround._generate_view

    async def counting(sc, src_name, view, *a, **kw):
        drawn.append(view)
        return await real(sc, src_name, view, *a, **kw)

    monkeypatch.setattr(turnaround, "_generate_view", counting)
    return drawn


@pytest.mark.parametrize("targets", [["left"], ["left", "back"], ["front", "left", "back", "right"]])
def test_untouched_views_keep_their_files_and_seeds(tmp_path, monkeypatch, targets):
    _patch_turnaround(monkeypatch)
    concept, ta, new, seeds = _parent_with_files(tmp_path, monkeypatch)
    drawn = _drawn(monkeypatch)
    sc = CopySC(tmp_path, new, FakeDB(concept, ta, new))
    words = "remove the people" if len(targets) > 1 else "Redraw this view"  # one view with words is edited (below)
    asyncio.run(turnaround.refine(sc, words, None, None, {}, views=targets))
    untouched = [v for v in seeds if v not in targets]
    assert sorted(set(drawn)) == sorted(targets)  # only the chosen views were drawn
    copied = getattr(sc, "copied", [])
    for v in untouched:
        assert {f"parent_{v}.png", f"parent_{v}_cutout.png", f"parent_{v}_white.png"} <= set(copied)
        # The same seed and verdict (a redrawn side's mirror check may add its own measure to the opposite side's record).
        assert sc.metrics["views"][v]["seed"] == seeds[v] and sc.metrics["views"][v]["passed"] is True
    for v in targets:
        assert f"parent_{v}.png" not in copied
    assert new.params["refined_views"] == targets and new.params["instruction"] == words


def test_the_stage_refuses_to_redraw_with_no_view(tmp_path, monkeypatch):
    _patch_turnaround(monkeypatch)
    concept, ta, new = _lineage(tmp_path)
    drawn = _drawn(monkeypatch)
    sc = FakeSC(tmp_path, new, FakeDB(concept, ta, new))
    with pytest.raises(ValueError, match="no views were chosen"):
        asyncio.run(turnaround.refine(sc, "remove the people", None, None, {}))
    assert drawn == []


def test_the_view_names_match_the_reference():
    assert set(["front", "left", "back", "right"]) <= set(reference.TURNAROUND_VIEWS)


# --- one view with words is edited, not redrawn from the concept (the card trial) ----------------------------------


def test_one_view_with_words_is_edited_from_its_own_picture(tmp_path, monkeypatch):
    concept, ta, new, seeds = _parent_with_files(tmp_path, monkeypatch)
    seen: list[dict] = []

    async def fake_run_views(sc, views, params, source, keep=None, extra="", supplied=None, method=None, edit_views=None):
        seen.append({"views": views, "edit": edit_views, "method": method})

    monkeypatch.setattr(turnaround, "_run_views", fake_run_views)

    def refine(words, **kw):
        sc = CopySC(tmp_path, new, FakeDB(concept, ta, new))
        asyncio.run(turnaround.refine(sc, words, None, None, {}, **kw))
        return seen[-1]

    one = refine("remove the people", views=["left"])
    assert one["edit"] == {"left": tmp_path / "parent_left.png"} and one["method"] == "standard"
    assert refine("Redraw this view", views=["left"])["edit"] is None  # no words: a plain redraw from the concept
    assert refine("remove the people", views=["left", "back"])["edit"] is None  # several views stay alike: from the concept
    ta.params["supplied_views"] = ["left"]
    assert refine("remove the people", views=["left"])["edit"] is None  # the person's own picture is replaced, not edited


def test_a_humanoid_single_view_change_still_redraws_from_the_concept(tmp_path, monkeypatch):
    concept, ta, new, seeds = _parent_with_files(tmp_path, monkeypatch)
    seen: list = []

    async def fake_run_views(sc, views, params, source, keep=None, extra="", supplied=None, method=None, edit_views=None):
        seen.append(edit_views)

    monkeypatch.setattr(turnaround, "_run_views", fake_run_views)
    for category, expected in (("humanoid", None), ("environment", {"left": tmp_path / "parent_left.png"})):
        monkeypatch.setattr(turnaround, "_category", lambda sc, c=category: c)
        sc = CopySC(tmp_path, new, FakeDB(concept, ta, new))
        asyncio.run(turnaround.refine(sc, "remove the hat", None, None, {}, views=["left"]))
        assert seen[-1] == expected, category
