"""Every category's route through the six steps, with the engines mocked (WP-40).

Each category is run through the autopilot from concept to export. The job execution is the fake from test_api
(stages.execute is replaced, so no ComfyUI, Blender or GPU is used). The tests check which steps ran, in order, and
which were skipped by the category, and that the run reaches export. The rig step is still run for categories whose
rig_engine is "none": it passes through inside the stage (rig.py `_skip`), which the autopilot cannot see; that part
is covered by the recorded plan in docs/research/asset-types/, not here.
"""

from __future__ import annotations

import time

import pytest

from vgac import autopilot, pipeline, reference
from vgac.reference import STAGE_ORDER

from test_api import _project_asset, client, fake_execute, wait_job  # noqa: F401  (fixtures and helpers)

ALL_CATEGORIES = [
    "humanoid", "quadruped", "winged", "multileg", "serpent", "prop_rigid", "prop_articulated",
    "vehicle", "environment", "foliage", "material",
]
# What a finished run of each category must have built (the order matters; exports come last).
FULL_ROUTE = ["concept", "turnaround", "mesh", "prep", "rig", "export"]
EXPECTED_ROUTE = {c: FULL_ROUTE for c in ALL_CATEGORIES}
# 5A: the material category has no turnaround. prep and rig still run as pass-through stages that record a skip
# (stages/prep.py and stages/rig.py), so the intended order is concept, mesh, prep (skipped), rig (skipped), export.
EXPECTED_ROUTE["material"] = ["concept", "mesh", "prep", "rig", "export"]
# Rig engine per family (5A table): creatures and humanoids rig; hard objects, buildings, plants and materials do not.
RIG_ENGINE_RIGS = {"humanoid", "quadruped", "winged", "multileg", "serpent"}


@pytest.fixture(autouse=True)
def _fast_polling(monkeypatch):
    monkeypatch.setattr(autopilot, "POLL_SECONDS", 0.02)


def _wait(client, aid, timeout=60.0):
    t0 = time.time()
    st = {}
    while time.time() - t0 < timeout:
        st = client.get(f"/api/assets/{aid}/autopilot").json()
        if st["status"] in ("done", "error", "paused"):
            return st
        time.sleep(0.05)
    raise AssertionError(f"autopilot did not finish: {st}")


def _steps_run(calls):
    """The stages that ran, in order, with each stage once (concept makes two candidates)."""
    seen: list[str] = []
    for c in calls:
        kind = c["kind"]
        parts = kind.split(".")
        stage = parts[1] if parts[0] == "material" else parts[0]
        if parts[-1] == "judge" or stage not in STAGE_ORDER:
            continue
        if stage not in seen:
            seen.append(stage)
    return seen


@pytest.mark.parametrize("category", ALL_CATEGORIES)
def test_each_category_reaches_export_and_runs_its_steps_in_order(client, fake_execute, category):
    p, a = _project_asset(client, category=category)
    fake_execute.clear()
    client.post(f"/api/assets/{a['id']}/autopilot", json={"until_stage": "export", "stop_on": "never"})
    st = _wait(client, a["id"])
    assert st["status"] == "done", st
    assert _steps_run(fake_execute) == EXPECTED_ROUTE[category]


def test_material_skips_turnaround_and_builds_on_concept(client, fake_execute):
    p, a = _project_asset(client, category="material")
    fake_execute.clear()
    client.post(f"/api/assets/{a['id']}/autopilot", json={"until_stage": "export", "stop_on": "never"})
    assert _wait(client, a["id"])["status"] == "done"
    assert "turnaround" not in _steps_run(fake_execute)
    # The 3D step builds on the kept picture, not on a turnaround that was never made.
    assert pipeline.previous_stage("material", "mesh") == "concept"
    assert pipeline.previous_stage("material", "export") == "rig"  # prep and rig record their skips and stay in the chain


def test_until_stage_stops_before_export(client, fake_execute):
    p, a = _project_asset(client, category="quadruped")
    fake_execute.clear()
    client.post(f"/api/assets/{a['id']}/autopilot", json={"until_stage": "rig", "stop_on": "never"})
    st = _wait(client, a["id"])
    assert st["status"] == "done"
    assert "export" not in _steps_run(fake_execute)


def test_category_table_matches_section_5a():
    """The category files declare the skips and rig engines the route table relies on."""
    for cid in ALL_CATEGORIES:
        cat = reference.category(cid)
        assert cat["id"] == cid
        skips = set(cat.get("skips") or [])
        if cid == "material":
            assert skips == {"turnaround"}
        else:
            assert skips == set(), cid
        rig = cat.get("rig_engine")
        if cid in RIG_ENGINE_RIGS:
            assert rig != "none", cid
        else:
            assert rig == "none", cid


def test_previous_stage_walks_back_over_skips_only():
    assert pipeline.previous_stage("humanoid", "turnaround") == "concept"
    assert pipeline.previous_stage("quadruped", "mesh") == "turnaround"
    assert pipeline.previous_stage("prop_rigid", "rig") == "prep"
    assert pipeline.previous_stage("material", "mesh") == "concept"
