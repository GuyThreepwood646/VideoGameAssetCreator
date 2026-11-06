"""The face sentence (vgac/hairpic.face_sentence, docs/KNOWN-FAULTS-RESEARCH.md section 2): the Cel painter sometimes
leaves a character's face empty or paints the figure from behind when nothing in the prompt asks for a face. Tested in
runtime/tmp/celface. The looks that get it, where it sits in the prompt, the description winning, and the full stop
after the description."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from vgac import hairpic, reference
from vgac.stages import concept

HUMANOID = reference.category("humanoid")
MAGE = "a young mage with long flowing silver hair, in a dark violet robe with gold embroidery"
KNIGHT = "a sturdy young woman with short red hair, steel plate armour over a blue tabard"


def _sc(prompt, look="cel", cat="humanoid"):
    return SimpleNamespace(project=SimpleNamespace(style_lock={}),
                           asset=SimpleNamespace(prompt=prompt, parts=[], category=cat, equipment=None, held_item=None),
                           style=reference.style(look), category=reference.category(cat))


def face(text, look="cel", cat=HUMANOID):
    return hairpic.face_sentence(text, reference.style(look), cat)


CEL_FACE = reference.style("cel")["face"]["sentence"]


def test_only_cel_humanoids_get_the_sentence():
    assert CEL_FACE == "The face is clearly drawn, with both eyes and a mouth."
    assert face(MAGE) == CEL_FACE and face(KNIGHT) == CEL_FACE
    # The looks that do not have the fault get nothing (runtime/tmp/celface).
    for s in reference.styles():
        if s["id"] != "cel":
            assert face(MAGE, s["id"]) == "", s["id"]
    for cid in ("quadruped", "winged", "prop_rigid", "vehicle"):
        assert face("a grey wolf with a long face", cat=reference.category(cid)) == "", cid
    assert hairpic.face_sentence(MAGE, {"face": {"sentence": "Face."}}, HUMANOID) == ""  # no categories named
    assert hairpic.face_sentence(MAGE, None, HUMANOID) == ""


def test_the_sentence_comes_last():
    # Last in the prompt: after the style block the same request changed the character more (runtime/tmp/celface).
    p = concept.build_prompt(_sc(MAGE), {})
    style_block = reference.style("cel")["prompt_block"]
    assert p.startswith(MAGE + ". " + style_block + " ")
    assert p.endswith(" " + HUMANOID["pose_block"].strip() + " " + CEL_FACE)
    assert concept.build_prompt(_sc(MAGE), {"face_block": False}) + " " + CEL_FACE == p
    assert CEL_FACE not in concept.build_prompt(_sc(MAGE, "painted"), {})
    # A prompt override is what the decision is made from.
    assert CEL_FACE not in concept.build_prompt(_sc(MAGE), {"prompt_override": "a knight with the visor down"})
    # Separate parts still come before it.
    sc = _sc(KNIGHT)
    sc.asset.parts = ["shield"]
    assert concept.build_prompt(sc, {}).endswith("clearly separate pieces: shield. " + CEL_FACE)


@pytest.mark.parametrize("text,why", [
    ("a knight in plate armour with the visor down", "hidden"),
    ("a knight in a closed helmet", "hidden"),
    ("a crusader in a great helm and a white surcoat", "hidden"),
    ("a knight in a full helmet and plate armour", "hidden"),
    ("a masked rogue with black hair", "cover"),
    ("an assassin in a black mask", "cover"),
    ("a plague doctor with a beaked mask and a long coat", "cover"),
    ("a soldier wearing a gas mask", "hidden"),
    ("a prisoner with a blindfold and long hair", "cover"),
    ("a pirate with red hair and an eyepatch", "cover"),
    ("a pilot with goggles over her eyes", "cover"),
    ("a hooded monk whose face is in shadow", "hidden"),
    ("a cultist in a deep red robe, hood pulled low", "hidden"),
    ("a faceless hooded figure in grey robes", "hidden"),
    ("a mysterious stranger with his face hidden by a scarf", "hidden"),
    ("a rusty robot", "body"),
    ("a skeleton warrior with a round shield", "body"),
    ("a stone golem", "body"),
    ("a woman with long black hair, seen from behind", "back"),
    ("a wanderer in a long coat, back view", "back"),
    ("a ranger facing away from the viewer", "back"),
    ("a girl with a long fringe covering both eyes", "hair"),
    ("a cyclops in a loincloth", "hidden"),
])
def test_the_description_wins_when_it_hides_the_face(text, why):
    assert hairpic.face_hidden(text) == why, text
    assert face(text) == "", text
    assert CEL_FACE not in concept.build_prompt(_sc(text), {})


@pytest.mark.parametrize("text", [
    MAGE, KNIGHT, "a knight with no helmet and short brown hair", "a ranger in a green hooded cloak",
    "a ranger with her hood down", "a scholar with grey hair and round glasses", "a knight holding a mask",
    "a knight with the visor raised", "a knight with an open visor", "a jester with a mask pushed up",
    "a young swordswoman with long black hair, in a red coat over dark trousers and black boots",
    "a knight with short hair and a scar over his left eye", "a pirate with a patch over one eye",
    "a woman in a blue hijab", "a backpacker in hiking boots", "a girl with a face covered in freckles",
])
def test_a_visible_face_still_gets_the_sentence(text):
    assert hairpic.face_hidden(text) is None, text
    assert face(text) == CEL_FACE, text


def test_the_description_ends_in_a_full_stop():
    # The description used to run into the style block ("...gold embroidery Anime cel-shaded style...").
    for look in ("painted", "cel", "clay", "realistic_pbr"):
        p = concept.build_prompt(_sc(MAGE, look), {})
        assert p.startswith(MAGE + ". " + reference.style(look)["prompt_block"].strip()), look
    assert concept.full_stop("a goblin") == "a goblin."
    assert concept.full_stop("a goblin.") == "a goblin." and concept.full_stop("a goblin!") == "a goblin!"
    assert concept.full_stop("a goblin, ") == "a goblin."
    assert concept.full_stop('a sign saying "stop."') == 'a sign saying "stop."'
    assert concept.full_stop("") == ""


# --- the check: the face is drawn ------------------------------------------------------------------------------------

from vgac import guidance  # noqa: E402

CHECKED = {"face": {"sentence": "Face.", "categories": ["humanoid"], "check": True}}


def test_face_answer_is_read_from_the_reply():
    assert hairpic.parse_face_answer('```json {"eyes": false, "mouth": true, "back": false} ```') == {
        "eyes": False, "mouth": True, "back": False}
    assert hairpic.parse_face_answer('{"eyes": true}') == {"eyes": True, "mouth": False, "back": False}
    for text in (None, "", "I see a face.", '{"eyes": "yes"}', "{not json}"):
        assert hairpic.parse_face_answer(text) is None, text


def test_face_check_sentences_and_fix():
    ok = hairpic.face_check({"eyes": True, "mouth": True, "back": False}, 12.0)
    assert ok["name"] == "face_drawn" and ok["passed"] is True and ok["severity"] == "warn" and "fix" not in ok
    assert ok["help"].startswith("The face is drawn")
    blank = hairpic.face_check({"eyes": False, "mouth": False, "back": False})
    assert blank["passed"] is False and blank["help"].startswith("The character has no eyes drawn")
    assert blank["fix"] == {"stage": "concept", "op": "refine", "instruction": hairpic.FACE_FIX_INSTRUCTION, "params": {},
                            "label": "Draw the face"}
    back = hairpic.face_check({"eyes": True, "mouth": False, "back": True})
    assert back["passed"] is False and back["help"].startswith("The character is drawn from behind")
    assert hairpic.face_check(None) is None
    assert "same" in hairpic.FACE_FIX_INSTRUCTION and "eyes" in hairpic.FACE_FIX_INSTRUCTION
    c = guidance.describe_check({"name": "face_drawn", "passed": False, "value": 0, "limit": 1})
    assert c["help"].startswith("The face has no eyes drawn")


def test_face_check_runs_only_where_asked_and_the_description_allows():
    style = {**reference.style("cel"), **CHECKED}
    assert hairpic.face_check_applies(MAGE, HUMANOID, style)
    assert not hairpic.face_check_applies("a knight with the visor down", HUMANOID, style)
    assert not hairpic.face_check_applies("a woman seen from behind", HUMANOID, style)
    assert not hairpic.face_check_applies("a grey wolf", reference.category("quadruped"), style)
    for s in reference.styles():
        expect = bool((s.get("face") or {}).get("check"))
        assert hairpic.face_check_applies(MAGE, HUMANOID, s) is expect, s["id"]
    assert [s["id"] for s in reference.styles() if (s.get("face") or {}).get("check")] in ([], ["cel"])


def _figure():
    from PIL import Image, ImageDraw

    im = Image.new("RGBA", (600, 1000), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([250, 60, 350, 190], fill=(228, 186, 150, 255))
    d.rectangle([200, 200, 400, 950], fill=(40, 60, 140, 255))
    return im


def test_head_crop_is_the_top_of_the_figure():
    crop = hairpic.head_crop(_figure())
    assert crop.size == (448, 448)
    px = crop.getpixel((224, 150))
    assert px[0] > 200 and px[2] < 180  # skin in the middle of the crop
    from PIL import Image

    assert hairpic.head_crop(Image.new("RGBA", (100, 100), (0, 0, 0, 0))) is None


def test_readiness_asks_the_vision_model_and_adds_the_check(tmp_path, monkeypatch):
    import asyncio

    cut = tmp_path / "cut.png"
    _figure().save(cut)
    calls = []

    class FakeSc:
        dir = tmp_path
        asset = SimpleNamespace(prompt=MAGE)
        version = SimpleNamespace(params={}, id="abcdef1234")
        category = HUMANOID
        style = {**reference.style("cel"), **CHECKED}
        settings = SimpleNamespace(judge_instance="b")
        ctx = SimpleNamespace(instance="b", clients={"b": object()})

        def progress(self, *a):
            pass

        async def stage_input(self, path, name, instance=None):
            calls.append(("stage", name, instance))
            return name

        async def comfy(self, graph, **kw):
            calls.append(("comfy", graph["gen"]["inputs"]["prompt"], kw.get("instance")))
            return {"outputs": {"show": {"text": ['{"eyes": false, "mouth": false, "back": false}']}}, "elapsed_seconds": 9.5}

    sc = FakeSc()
    c = asyncio.run(concept._face_check(sc, cut))
    assert c["name"] == "face_drawn" and c["passed"] is False and c["vars"]["seconds"] == 9.5
    assert calls[1] == ("comfy", hairpic.FACE_CHECK_PROMPT, "b")
    # Not asked for, the description hides the face, or the engine fails: no check, never an error.
    sc.style = reference.style("painted")
    assert asyncio.run(concept._face_check(sc, cut)) is None
    sc.style = {**reference.style("cel"), **CHECKED}
    sc.version = SimpleNamespace(params={"prompt_override": "a knight with the visor down"}, id="abcdef1234")
    assert asyncio.run(concept._face_check(sc, cut)) is None
    sc.version = SimpleNamespace(params={}, id="abcdef1234")

    async def broken(graph, **kw):
        raise RuntimeError("engine down")

    sc.comfy = broken
    assert asyncio.run(concept._face_check(sc, cut)) is None
