"""vgac/srcproject.py: the head pass (face_zone, fit_head) runs for a humanoid only.

Only a character has a head to fit; a prop or a building whose geometry happens to look like a face must not get a
head patch painted onto it. Both calls are wrapped to count them, the real results are kept."""
from __future__ import annotations

import pytest

from vgac import srcproject
from test_srcproject_objects import box, drawn, paint_as


@pytest.fixture
def head_calls(monkeypatch) -> dict[str, int]:
    counts = {"face_zone": 0, "fit_head": 0}
    real_zone, real_fit = srcproject.face_zone, srcproject.fit_head

    def zone(*a, **k):
        counts["face_zone"] += 1
        return real_zone(*a, **k)

    def fit(*a, **k):
        counts["fit_head"] += 1
        return real_fit(*a, **k)

    monkeypatch.setattr(srcproject, "face_zone", zone)
    monkeypatch.setattr(srcproject, "fit_head", fit)
    return counts


@pytest.mark.parametrize("category", ["prop_rigid", "prop_articulated", "environment", "vehicle", "quadruped"])
def test_head_pass_skipped_for_non_humanoid(head_calls, category):
    mesh, tex = box()
    paint_as(category, mesh, tex.copy(), drawn(mesh, tex, 0.0, 15.0), None, mode="off", picture_yaws=[0.0])
    assert head_calls == {"face_zone": 0, "fit_head": 0}, category


def test_head_pass_runs_for_humanoid(head_calls):
    mesh, tex = box()
    paint_as("humanoid", mesh, tex.copy(), drawn(mesh, tex, 0.0, 15.0), None, mode="off", picture_yaws=[0.0])
    assert head_calls["face_zone"] == 1 and head_calls["fit_head"] == 1
