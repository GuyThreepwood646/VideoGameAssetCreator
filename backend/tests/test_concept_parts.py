"""The step-1 picture is asked to draw the declared parts apart, but not a category's default parts (F: default-parts).

A boat made with the vehicle category's default (wheels) used to be painted with wheels, because the prompt named them.
The default rows carry "from": "category"; build_prompt names only the other rows. Rigging reads the same rows as before.
"""

from __future__ import annotations

from types import SimpleNamespace

from vgac import declared_parts as dp
from vgac import reference
from vgac.stages import concept

SEPARATE = "The following parts are clearly separate pieces:"


def _sc(prompt, parts, cat="vehicle"):
    return SimpleNamespace(project=SimpleNamespace(style_lock={}),
                           asset=SimpleNamespace(prompt=prompt, parts=parts, category=cat, equipment=None, held_item=None),
                           style=reference.style("cel"), category=reference.category(cat))


def test_a_category_default_on_a_boat_is_not_asked_for_in_the_picture():
    parts = dp.item_defaults("vehicle", "Rowboat", "a wooden rowboat", ["wheels"])
    assert parts == [{"name": "wheels", "kind": "spin", "from": "category"}]
    p = concept.build_prompt(_sc("a wooden rowboat", parts), {})
    # The vehicle category's own pose_block still says "wheels straight, doors and hatches closed" for every vehicle;
    # that text is not the parts sentence and is not changed here (reported to the orchestrator).
    assert SEPARATE not in p


def test_a_keyword_part_is_still_asked_for_in_the_picture():
    parts = dp.item_defaults("vehicle", "Wheelbarrow", "A garden wheelbarrow", ["wheels"])
    assert parts == [{"name": "wheel", "kind": "spin"}]
    assert SEPARATE + " wheel." in concept.build_prompt(_sc("a garden wheelbarrow", parts), {})


def test_a_typed_part_is_asked_for_even_when_it_has_the_default_name():
    parts = dp.read_stored([{"name": "wheels", "kind": "spin"}])  # typed by the person: no mark
    assert SEPARATE + " wheels." in concept.build_prompt(_sc("a rowboat on wheels", parts), {})


def test_a_mix_names_only_the_unmarked_rows():
    parts = [{"name": "wheels", "kind": "spin", "from": "category"}, {"name": "sail", "kind": "stiff"}]
    assert SEPARATE + " sail." in concept.build_prompt(_sc("a sailing cart", parts), {})


def test_no_sentence_when_only_category_defaults_are_left():
    parts = [{"name": "wheels", "kind": "spin", "from": "category"}]
    assert SEPARATE not in concept.build_prompt(_sc("a sled", parts), {})


def test_read_stored_keeps_the_mark_and_leaves_other_rows_alone():
    rows = dp.read_stored([{"name": "wheels", "kind": "spin", "from": "category"},
                           {"name": "lid", "kind": "hinge", "from": "typed"}, {"name": "", "kind": "spin"}])
    assert rows == [{"name": "wheels", "kind": "spin", "from": "category"}, {"name": "lid", "kind": "hinge"}]


def test_rigging_reads_the_same_parts_with_or_without_the_mark():
    from vgac.stages import rig

    marked = SimpleNamespace(asset=SimpleNamespace(parts=[{"name": "wheels", "kind": "separate", "from": "category"}]))
    plain = SimpleNamespace(asset=SimpleNamespace(parts=[{"name": "wheels", "kind": "separate"}]))
    assert rig._moving_parts(marked) == rig._moving_parts(plain) == ["wheels"]
