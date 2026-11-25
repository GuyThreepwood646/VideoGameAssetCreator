"""The texture sampler's default steps per category (stages/mesh.tex_steps_for): 12 for solid props, articulated props
and scenery, 24 for every other category, and a value the version holds (the person's choice or a saved one) always wins."""

from __future__ import annotations

import pytest

from vgac import reference
from vgac.stages import mesh

TWELVE = ["prop_rigid", "prop_articulated", "environment"]
TWENTY_FOUR = ["humanoid", "quadruped"]


@pytest.mark.parametrize("cid", TWELVE)
def test_default_is_12_for_props_and_scenery(cid):
    cat = reference.category(cid)
    assert cat["tex_steps_default"] == 12
    assert mesh.tex_steps_for({}, cat) == 12
    assert mesh.tex_steps_for({"tex_steps": None}, cat) == 12


@pytest.mark.parametrize("cid", TWENTY_FOUR)
def test_default_stays_24_for_other_categories(cid):
    cat = reference.category(cid)
    assert "tex_steps_default" not in cat
    assert mesh.tex_steps_for({}, cat) == 24
    assert mesh.tex_steps_for(None, None) == 24


@pytest.mark.parametrize("cid", TWELVE)
def test_chosen_value_wins_for_props_and_scenery(cid):
    cat = reference.category(cid)
    assert mesh.tex_steps_for({"tex_steps": 24}, cat) == 24
    assert mesh.tex_steps_for({"tex_steps": 12}, cat) == 12


@pytest.mark.parametrize("cid", TWENTY_FOUR)
def test_chosen_value_wins_for_other_categories(cid):
    cat = reference.category(cid)
    assert mesh.tex_steps_for({"tex_steps": 12}, cat) == 12
    assert mesh.tex_steps_for({"tex_steps": 24}, cat) == 24


def test_version_built_before_the_change_keeps_its_saved_value():
    # A version's params hold the steps it was built with, so a rebuild or a Resume does not move it to the new default.
    assert mesh.tex_steps_for({"tex_steps": 24}, reference.category("prop_rigid")) == 24
    assert mesh.tex_steps_for({"tex_steps": 24}, reference.category("environment")) == 24


def test_adjust_knob_defaults_to_the_category_when_empty():
    knob = next(k for k in reference.STAGE_SCHEMAS["mesh"] if k["name"] == "tex_steps")
    assert knob["default"] is None
    assert knob["options"] == [12, 24]
