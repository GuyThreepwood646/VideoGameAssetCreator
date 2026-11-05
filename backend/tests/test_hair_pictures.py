"""Hair-friendly pictures (vgac/hairpic.py, docs/HAIR-RESEARCH.md stage 1 option h): the hair sentence per look,
the description winning over it, no sentence for bald, helmeted or hooded characters, fur for creatures, the notes
for the asset form (and their copy in frontend/src/hairNotes.ts), and the "hair over the eyes" check."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from vgac import guidance, hairpic, reference
from vgac.stages import concept

ROOT = Path(__file__).resolve().parents[2]
HUMANOID = reference.category("humanoid")
QUADRUPED = reference.category("quadruped")
KNIGHT = "a sturdy young woman with short red hair, steel plate armour over a blue tabard"
MAGE = "a young mage with long flowing silver hair, in a dark violet robe with gold embroidery"


def sentence(text, look="painted", cat=HUMANOID):
    return hairpic.hair_sentence(text, reference.style(look), cat)


def negative(text, look="painted", cat=HUMANOID):
    return hairpic.hair_negative(text, reference.style(look), cat)


# --- the sentence per look ---------------------------------------------------------------------------------------


def test_painted_asks_for_one_smooth_shape_and_clear_eyes():
    # Round 2 (runtime/tmp/hairpics): "a few large, clearly separated clumps" gave saw-tooth lock ends in 3D and changed
    # a cel character, so the painted look asks for a smooth simple shape.
    assert sentence(KNIGHT) == "Hair as one smooth, simple shape with a clean outline and no loose strands, both eyes clearly visible."
    assert negative(KNIGHT) == "wispy hair, flyaway strands, hair over the eyes, transparent hair"


@pytest.mark.parametrize("look", ["cel", "clay"])
def test_cel_and_clay_characters_get_no_sentence_but_creatures_get_fur(look):
    # Every wording tried changed a cel or clay character on at least one test picture.
    assert sentence(KNIGHT, look) == "" and negative(KNIGHT, look) == ""
    assert sentence("a grey wolf", look, QUADRUPED).startswith("Short dense fur")


def test_photoreal_asks_for_tidy_hair_and_has_a_negative():
    # "short red hair": the description wins, so not "tied back or braided" (which turned her hair into braids).
    assert sentence(KNIGHT, "realistic_pbr") == "Hair cut short and combed in clear locks, no flyaway hairs, nothing over the face."
    assert sentence("a knight in plate armour with red hair", "realistic_pbr") == (
        "Hair neatly tied back, braided or cut short and combed in clear locks, no flyaway hairs, nothing over the face.")
    assert "short" in hairpic.wishes("a girl with a pixie cut") and "short" not in hairpic.wishes("a short man with long hair")
    assert negative(KNIGHT, "realistic_pbr") == ("wispy hair, flyaway strands, hair over the eyes, messy loose hair, "
                                                  "transparent hair, hair covering the collar")


@pytest.mark.parametrize("look", ["pixel", "retro64", "lowpoly_flat"])
def test_plain_looks_ask_for_one_simple_shape(look):
    assert sentence(KNIGHT, look) == "Hair as one simple solid shape."
    assert sentence("a grey wolf", look, QUADRUPED) == "Fur as one simple solid shape."


def test_every_style_has_a_hair_block_with_head_fur_and_negative():
    for s in reference.styles():
        hb = s.get("hair") or {}
        assert "head" in hb and hb.get("fur"), s["id"]
        assert set(hb.get("negative") or {}) == {"head", "fur"}, s["id"]


def test_unknown_style_and_categories_without_hair_get_nothing():
    assert hairpic.hair_sentence(KNIGHT, {"id": "mine", "prompt_block": ""}, HUMANOID) == ""
    for cid in ("prop_rigid", "vehicle", "environment", "foliage", "material", "prop_articulated"):
        assert sentence("a chest with a fur lining and long hair", cat=reference.category(cid)) == "", cid


# --- the description wins -----------------------------------------------------------------------------------------


def test_long_loose_hair_gets_no_painted_sentence_and_photoreal_drops_tied_back():
    # Any hair words blew long loose hair out to the side in the painted look (round 2), so it gets none.
    assert sentence(MAGE) == "" and negative(MAGE) == ""
    s = sentence(MAGE, "realistic_pbr")
    assert "tied back" not in s and "cut short" not in s and s.startswith("Hair combed in clear locks")
    assert "messy loose hair" not in negative(MAGE, "realistic_pbr") and "collar" not in negative(MAGE, "realistic_pbr")
    for text in ("a princess with waist-length golden hair", "a bard whose black hair falls loose to his shoulders",
                 "an elf with shoulder-length hair", "a witch with long, wavy red hair"):
        assert "long_loose" in hairpic.wishes(text), text
        assert sentence(text) == "", text


def test_long_hair_that_is_tied_is_not_loose():
    for text in ("a woman with long hair tied in a ponytail", "a warrior with a long braid", "a monk with long hair in a bun"):
        assert "long_loose" not in hairpic.wishes(text), text
        assert sentence(text) == "Hair as one smooth, simple shape with a clean outline and no loose strands, both eyes clearly visible.", text
    assert "long_loose" not in hairpic.wishes("a knight with a long sword and red hair")


def test_fringe_keeps_the_eyes_clear():
    assert sentence("a girl with short black hair and a heavy fringe").endswith("both eyes clearly visible.")
    assert sentence("a boy with bangs", "realistic_pbr").endswith("nothing over the eyes.")


def test_hair_over_an_eye_drops_every_eye_clause():
    for text in ("a girl with a fringe over one eye", "a thin man with messy dark hair falling over one eye",
                 "a rogue whose long bangs cover both eyes", "a peek-a-boo hairstyle"):
        assert "over_eye" in hairpic.wishes(text), text
        for look in ("painted", "realistic_pbr"):
            s, n = sentence(text, look), negative(text, look)
            assert "eyes" not in s and "face" not in s and "over the eyes" not in n, (text, look)
    # A scar or an eyepatch over an eye is not hair over the eye.
    assert "over_eye" not in hairpic.wishes("a knight with short hair and a scar over his left eye")
    assert "over_eye" not in hairpic.wishes("a pirate with red hair and a patch over one eye")


def test_dreadlocks_curls_mohawk_and_wild_hair():
    # A smooth simple shape contradicts locs, curls and wild hair: only the eye clause is left.
    for text in ("a warrior with dreadlocks", "a dancer with curly hair", "a hermit with wild, messy hair"):
        assert sentence(text) == "Both eyes clearly visible.", text
    assert "wispy hair" not in negative("a hermit with wild, messy hair")
    assert sentence("a warrior with dreadlocks", "realistic_pbr").startswith("Hair in thick, clearly separated locks")
    p = sentence("a punk with a green mohawk", "realistic_pbr")
    assert "tied back" not in p and "cut short" not in p
    assert "flyaway" not in sentence("a hermit with wild, messy hair", "realistic_pbr")
    assert "wispy hair" not in negative("a hermit with wild, messy hair", "realistic_pbr")


# --- no hair, no sentence -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "a bald monk in orange robes", "a warrior with a shaved head", "a knight in a full helmet and plate armour",
    "a ranger in a green hooded cloak", "an assassin with her hood up", "a rusty robot", "a skeleton warrior",
    "a woman in a blue hijab"])
def test_bald_helmeted_hooded_or_hairless_characters_get_no_sentence(text):
    for look in ("painted", "realistic_pbr", "pixel"):
        assert sentence(text, look) == "" and negative(text, look) == "", (text, look)


@pytest.mark.parametrize("text", [
    "a knight with no helmet and short brown hair", "a ranger with her hood down", "a squire carrying a helmet",
    "a hooded ranger with a long red braid", "a goblin in leather armour"])
def test_visible_hair_still_gets_the_sentence(text):
    assert sentence(text).startswith("Hair as one smooth, simple shape"), text


# --- creatures ----------------------------------------------------------------------------------------------------


def test_quadrupeds_get_fur_unless_they_have_none():
    assert sentence("a grey wolf", cat=QUADRUPED) == ("Short dense fur lying flat in clear clumps, the mane and tail as a "
                                                       "few thick locks.")
    assert sentence("a shaggy mountain goat", cat=QUADRUPED).startswith("Long fur in a few thick, clearly separated clumps")
    for text in ("a green lizard", "a stone golem hound", "a robot dog", "a scaly dragon"):
        assert sentence(text, cat=QUADRUPED) == "", text
    assert "wispy fur" in negative("a grey wolf", "realistic_pbr", QUADRUPED)


def test_other_creatures_get_fur_only_when_the_description_names_it():
    winged = reference.category("winged")
    assert sentence("a red dragon", cat=winged) == "" and sentence("a crow", cat=winged) == ""
    assert sentence("a griffin with a furry lion body", cat=winged).startswith("Short dense fur")
    assert sentence("a furry giant spider", cat=reference.category("multileg")).startswith("Short dense fur")


# --- the concept prompt ------------------------------------------------------------------------------------------


def _sc(prompt, look="painted", cat="humanoid", lock=None):
    return SimpleNamespace(project=SimpleNamespace(style_lock=lock or {}),
                           asset=SimpleNamespace(prompt=prompt, parts=[], category=cat, equipment=None, held_item=None),
                           style=reference.style(look), category=reference.category(cat))


def test_build_prompt_puts_the_sentence_after_the_style_block():
    sc = _sc(KNIGHT)
    p = concept.build_prompt(sc, {})
    style_block = reference.style("painted")["prompt_block"]
    hair = sentence(KNIGHT)
    assert p.index(style_block) < p.index(hair) < p.index(reference.category("humanoid")["pose_block"])
    assert hair not in concept.build_prompt(sc, {"hair_block": False})
    # A prompt override is what the sentence is worked out from.
    assert "collar" not in concept.build_prompt(sc, {"prompt_override": MAGE})
    assert concept.hair_negative(_sc(MAGE, "realistic_pbr"), {}) == negative(MAGE, "realistic_pbr")
    assert concept.hair_negative(_sc(MAGE, "realistic_pbr"), {"hair_block": False}) == ""
    assert concept.build_prompt(_sc("a bald monk"), {}) == concept.build_prompt(_sc("a bald monk"), {"hair_block": False})


# --- notes for the asset form -------------------------------------------------------------------------------------


def ids(text, look, cat=HUMANOID):
    return [n["id"] for n in hairpic.notes(text, look, cat)]


def test_note_triggers():
    assert ids(KNIGHT, "realistic_pbr") == ["photoreal_hair"]
    assert ids(KNIGHT, "painted") == []
    assert ids(MAGE, "painted") == ["long_loose"]
    assert ids(MAGE, "realistic_pbr") == ["photoreal_hair", "long_loose"]
    assert ids("a goblin in leather armour", "realistic_pbr") == []  # no hair words
    assert ids("a bald monk", "realistic_pbr") == []
    assert ids("a grey wolf", "painted", QUADRUPED) == ["fur"]
    assert ids("a green lizard", "painted", QUADRUPED) == []
    assert ids("a crow", "painted", reference.category("winged")) == []


def test_notes_never_promise_movement():
    for text in hairpic.NOTES.values():
        low = text.lower()
        assert "will swing" not in low and "can move" not in low and "spring" not in low
        assert re.search(r"[\U0001F300-\U0001FAFF]", text) is None


def _ts_list(src, name):
    m = re.search(rf"export const {name} = \[(.*?)\]", src, re.S)
    assert m, name
    return re.findall(r"'([^']*)'", m.group(1))


def test_frontend_copy_matches_the_backend():
    src = (ROOT / "frontend" / "src" / "hairNotes.ts").read_text(encoding="utf-8")
    for name in ("HAIR_WORDS", "BALD_WORDS", "COVER_WORDS", "COVER_NEGATORS", "COVER_AFTER", "NO_HAIR_BODIES", "LONG_WORDS",
                 "LONG_NOUNS", "LONG_AFTER", "TIED_WORDS", "FUR_WORDS", "NO_FUR_WORDS"):
        assert _ts_list(src, name) == getattr(hairpic, name), name
    for nid, text in hairpic.NOTES.items():
        assert f"'{text}'" in src, nid
    for c in reference.categories():
        if c.get("hair"):
            assert re.search(rf"\b{c['id']}: '{c['hair']}'", src), c["id"]


# --- the check: hair over the eyes ---------------------------------------------------------------------------------


def test_check_sentences_and_fix():
    c = guidance.describe_check({"name": "hair_off_eyes", "id": "hair_off_eyes", "passed": False, "ok": False, "value": 0.72,
                                 "limit": 0.75, "vars": {"redraw": hairpic.FIX_INSTRUCTION, "side_text": "her left eye"}})
    assert c["severity"] == "warn"
    assert c["help"].startswith("Hair covers her left eye (72%")
    assert c["fix"] == {"stage": "concept", "op": "refine", "instruction": hairpic.FIX_INSTRUCTION, "params": {},
                        "label": "Move the hair off the face"}
    ok = guidance.describe_check({"name": "hair_off_eyes", "passed": True, "value": 0.02, "limit": 0.4})
    assert ok["help"].startswith("Both eyes are clear of hair") and "fix" not in ok
    assert "same" in hairpic.FIX_INSTRUCTION and "eyes" in hairpic.FIX_INSTRUCTION


def _character(fringe: str | None = None):
    """A standing figure on transparent ground: dark hair around a skin-coloured face with two dark eyes, a blue body.
    fringe: None, "both" (hair down over both eyes) or "left" (over the eye on the left of the picture)."""
    from PIL import Image, ImageDraw

    im = Image.new("RGBA", (600, 1000), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    hair, skin = (60, 35, 25, 255), (228, 186, 150, 255)
    d.rectangle([282, 175, 318, 205], fill=skin)  # neck
    d.rectangle([200, 200, 400, 600], fill=(40, 60, 140, 255))  # body
    d.rectangle([215, 600, 290, 950], fill=(50, 50, 60, 255))
    d.rectangle([310, 600, 385, 950], fill=(50, 50, 60, 255))
    d.ellipse([240, 50, 360, 190], fill=hair)  # hair behind and on top
    d.ellipse([255, 80, 345, 185], fill=skin)  # face
    d.rectangle([255, 70, 345, 100], fill=hair)  # hairline
    for ex in (282, 318):
        d.ellipse([ex - 5, 128, ex + 5, 136], fill=(30, 30, 30, 255))
    if fringe == "both":
        d.rectangle([252, 70, 348, 152], fill=hair)
    elif fringe == "left":
        d.polygon([(250, 70), (305, 70), (300, 156), (250, 156)], fill=hair)
    return im


def test_eye_cover_finds_hair_over_the_eyes():
    clear = hairpic.eye_cover(_character())
    both = hairpic.eye_cover(_character("both"))
    left = hairpic.eye_cover(_character("left"))
    assert clear is not None and clear["covered"] == []
    assert both is not None and sorted(both["covered"]) == ["left", "right"]
    assert left is not None and left["covered"] == ["left"]


def test_eyes_check_applies_only_when_the_description_allows():
    c = hairpic.eyes_check(_character("both"), KNIGHT, HUMANOID, reference.style("painted"))
    assert c["name"] == "hair_off_eyes" and c["passed"] is False and c["fix"]["instruction"] == hairpic.FIX_INSTRUCTION
    assert c["help"].startswith("Hair covers both eyes")
    assert hairpic.eyes_check(_character(), KNIGHT, HUMANOID)["passed"] is True
    # The description wins; no hair; not a character; a look switched off.
    assert hairpic.eyes_check(_character("both"), "a girl with a long fringe covering both eyes", HUMANOID) is None
    assert hairpic.eyes_check(_character("both"), "a knight in a full helmet", HUMANOID) is None
    assert hairpic.eyes_check(_character("both"), "a grey wolf", QUADRUPED) is None
    assert hairpic.eyes_check(_character("both"), KNIGHT, HUMANOID, {"hair_eye_check": False}) is None


def test_every_clause_is_text_and_names_known_wishes():
    for s in reference.styles():
        for kind in ("head", "fur"):
            for c in s["hair"][kind]:
                if isinstance(c, dict):
                    assert isinstance(c["text"], str) and all(isinstance(v, str) for v in (c.get("swap") or {}).values()), s["id"]
                    assert set(c.get("swap") or {}) | set(c.get("drop") or []) <= {"long_loose", "short", "fringe", "over_eye", "locs", "wild", "curly", "styled", "long_fur"}, s["id"]


@pytest.mark.parametrize("text", ["a pirate with red hair and an eyepatch", "a thief with short hair and a black eye patch",
                                  "a prisoner with a blindfold and long hair", "a knight with short hair and a visor",
                                  "a scholar with grey hair and round glasses", "a masked rogue with black hair"])
def test_eyes_check_is_skipped_when_something_else_covers_the_eyes(text):
    assert hairpic.eyes_check(_character("both"), text, HUMANOID) is None
    assert hairpic.eyes_check(_character("both"), "a rogue with short black hair", HUMANOID)["passed"] is False
