"""WP-96: step names come from one table, the frontend's fallback text agrees with guidance.py, categories sit in groups."""
from __future__ import annotations

import re
from pathlib import Path

from vgac import guidance, reference

HELP_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "help.ts"
QUOTED = r"'((?:[^'\\]|\\.)*)'"


def _un(x: str) -> str:
    return x.replace("\\'", "'")


def test_step_names_come_from_one_table():
    assert set(reference.STAGE_NAMES) == set(reference.STAGE_ORDER)
    for stage in reference.STAGE_ORDER:
        names = reference.STAGE_NAMES[stage]
        assert reference.STAGE_LABELS[stage] == names["plain"]
        g = guidance.stage_guide(stage)
        assert g["plain_name"] == names["plain"] and g["technical_short"] == names["technical"]
        assert names["technical"] and names["technical"] != names["plain"]


def test_stage_info_sends_the_industry_term():
    for s in reference.stages():
        assert s["plain_name"] == reference.STAGE_NAMES[s["id"]]["plain"]
        assert s["technical_short"] == reference.STAGE_NAMES[s["id"]]["technical"]


def test_help_ts_stage_text_agrees_with_guidance():
    src = HELP_TS.read_text(encoding="utf-8")
    for stage in reference.STAGE_ORDER:
        block = re.search(r"\n  " + stage + r": \{\n(.*?)\n  \},\n", src, re.S)
        assert block, stage
        text = block.group(1)
        plain = re.search(r"plain: " + QUOTED, text).group(1)
        tech = _un(re.search(r"tech: " + QUOTED, text).group(1))
        h = re.search(r"help: H\(" + ", ".join([QUOTED] * 4) + r"\)", text)
        g = guidance.stage_guide(stage)
        assert plain == g["plain_name"] and tech == g["technical_short"], stage
        assert _un(h.group(2)) == g["help"]["meaning"], stage
        assert _un(h.group(3)) == g["help"]["why"], stage
        assert _un(h.group(4)) == g["help"]["technical"] == g["technical_short"], stage


def test_every_category_has_a_picker_group():
    names = [n for n, _ids in reference.CATEGORY_GROUPS]
    listed = [i for _n, ids in reference.CATEGORY_GROUPS for i in ids]
    assert len(listed) == len(set(listed))
    for c in reference.categories():
        assert c["id"] in listed, c["id"]
        assert reference.category_group(c["id"]) in names


def test_one_name_for_the_stand_still_move():
    assert guidance.PARAM_HELP["moves"]["options"]["idle"] == "Stand still"
    src = HELP_TS.read_text(encoding="utf-8")
    assert "idle: 'Standing still'" not in src
    assert "Standing still (loops)" not in src


def test_category_list_comes_in_picker_order():
    order = [reference.category_order(c["id"]) for c in sorted(reference.categories(), key=lambda c: reference.category_order(c["id"]))]
    assert order == sorted(order)
    assert reference.category_order("humanoid") < reference.category_order("vehicle") < reference.category_order("environment") < reference.category_order("material")


def test_glossary_step_headings_come_from_the_step_table():
    src = (HELP_TS.parent / "pages" / "GlossaryPage.tsx").read_text(encoding="utf-8")
    assert "STAGE_TEXT[s].plain" in src and "STAGES.map" in src
    for stage in reference.STAGE_ORDER:
        assert guidance.stage_guide(stage)["help"]["technical"] == reference.STAGE_NAMES[stage]["technical"]


def test_one_name_per_move_everywhere():
    from vgac import moves

    assert moves.move_label("idle") == "Stand still" and moves.move_label("Idle_Loop") == "Stand still"
    assert moves.move_label("Hit_Chest") == "Get hit" == moves.move_label("hit")
    assert moves.move_label("Sword_Attack") == "Attack" == moves.move_label("attack")
    assert moves.move_label("Punch_Jab") == "Punch" and moves.move_label("Interact") == "Use or pick up"
    src = HELP_TS.read_text(encoding="utf-8")
    block = re.search(r"const CLIP_LABELS: Record<string, string> = \{\n(.*?)\n\}", src, re.S).group(1)
    for key, label in re.findall(r"^\s+(\w+): '([^']*)',?$", block, re.M):
        assert label == moves.move_label(key) or key in {"jump_start", "jump_loop", "jump_land", "crouch_idle", "crouch_walk"}, key


def test_category_help_names_match_the_category_labels():
    src = HELP_TS.read_text(encoding="utf-8")
    for c in reference.categories():
        m = re.search(r"\n  " + c["id"] + r": H\('([^']*)'", src)
        assert m and m.group(1) == c["label"], c["id"]
