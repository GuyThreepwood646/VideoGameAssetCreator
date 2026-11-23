"""F18: a common object named in the description takes its real size (data/object_sizes.yaml), on object categories
only. A size written in the text still wins, the subject of the description wins over other named objects, and
creatures, people and buildings keep their category size."""

from __future__ import annotations

import pytest

from vgac import reference
from vgac.stages import prep as prep_stage


def test_wheelbarrow_is_its_own_size_not_the_vehicle_default():
    # Master: 4.5 m (the vehicle default, "long"). A real wheelbarrow is about 1.5 m long.
    size = prep_stage.resolve_size("vehicle", {}, "a wheelbarrow")
    assert size["size_m"] == pytest.approx(1.5)
    assert size["size_axis"] == "longest"
    assert size["size_source"] == "object"


def test_gate_is_its_own_size_not_the_moving_parts_default():
    # Master: 0.8 m (the prop_articulated default). A garden gate is about 1 m across.
    size = prep_stage.resolve_size("prop_articulated", {}, "an old wooden gate")
    assert size["size_m"] == pytest.approx(1.0)
    assert size["size_axis"] == "horizontal"
    assert size["size_source"] == "object"


def test_a_size_written_in_the_text_still_wins_over_the_table():
    size = prep_stage.resolve_size("vehicle", {}, "a wheelbarrow, 3 m long")
    assert size["size_m"] == pytest.approx(3.0)
    assert size["size_source"] == "description"


def test_the_subject_wins_when_two_table_items_are_named():
    # Named first: the wheelbarrow is the subject; the apples and barrels are what it carries or stands by.
    size = prep_stage.resolve_size("prop_rigid", {}, "a wheelbarrow full of barrels")
    assert size["size_m"] == pytest.approx(1.5)
    assert size["size_axis"] == "longest"
    size = prep_stage.resolve_size("prop_rigid", {}, "a barrel beside a gate")
    assert size["size_m"] == pytest.approx(0.9)
    assert size["size_axis"] == "height"


def test_a_whole_word_only():
    # "gateway" is not "gate" and "boxer" is not "box": both fall to the category default.
    assert prep_stage.resolve_size("prop_articulated", {}, "a gateway arch")["size_source"] == "category"
    assert prep_stage.resolve_size("prop_rigid", {}, "a boxer's cabinet")["size_source"] == "category"


@pytest.mark.parametrize("category, text, default", [
    ("humanoid", "a knight with a wooden chair", 1.8),  # a person keeps the humanoid size, whatever else is named
    ("environment", "a cottage with a gate and a barrel", 3.0),  # a building keeps its size
    ("serpent", "a dragon guarding a chest", 2.0),  # a creature keeps its size
    ("winged", "a dragon guarding a chest", 1.5),
])
def test_creatures_people_and_buildings_keep_their_category_size(category, text, default):
    size = prep_stage.resolve_size(category, {}, text)
    assert size["size_m"] == pytest.approx(default)
    assert size["size_source"] == "category"
    assert reference.object_size(category, text) is None


def test_the_table_only_names_categories_and_sizes_that_exist():
    table = reference._object_sizes()
    known = {c["id"] for c in reference.categories()}
    assert table["categories"] <= known
    for obj in table["objects"]:
        assert obj["axis"] in reference.SIZE_AXES, obj["label"]
        assert 0.01 <= obj["size_m"] <= 100, obj["label"]
