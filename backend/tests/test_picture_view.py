"""The step 1 picture's known view (vgac/picture_view.py): recorded only on pictures the app painted from wording that asks
for a square-on front; never assumed for the person's own pictures, whose angle must be searched widely."""

from __future__ import annotations

from types import SimpleNamespace

from vgac import picture_view, reference
from vgac.stages import concept


def _sc(cat="humanoid", held=None):
    return SimpleNamespace(project=SimpleNamespace(style_lock={}),
                           asset=SimpleNamespace(prompt="a sturdy knight", parts=[], category=cat, equipment=None, held_item=held),
                           style=reference.style("painted"), category=reference.category(cat))


def test_a_picture_painted_from_front_wording_records_the_front():
    prompt, view = concept.prompt_and_view(_sc("humanoid"), {})
    assert view == "front" and reference.category("humanoid")["pose_block"] in prompt
    assert concept.build_prompt(_sc("humanoid"), {}) == prompt


def test_no_view_without_the_category_wording():
    # readiness_block off: the category's wording is not in the prompt, so nothing is known about the angle
    assert concept.prompt_and_view(_sc("humanoid"), {"readiness_block": False})[1] is None
    # a category with no picture_view (the material) records nothing
    assert concept.prompt_and_view(_sc("material"), {})[1] is None


def test_a_held_item_uses_its_own_wording_and_records_nothing(monkeypatch):
    from vgac import items

    monkeypatch.setattr(items, "concept_prompt_parts", lambda _a: {"pose_block": "The sword is shown alone, upright."})
    prompt, view = concept.prompt_and_view(_sc("prop_rigid"), {})
    assert "The sword is shown alone" in prompt and view is None


def test_assumed_front_only_for_app_pictures():
    assert picture_view.assumed({"picture_view": "front"}) == {"view": "front", "turn": 0.0, "tilt": 0.0}
    assert picture_view.assumed({"picture_view": None}) is None
    assert picture_view.assumed({}) is None
    assert picture_view.assumed(None) is None
    assert picture_view.assumed({"picture_view": "three-quarter"}) is None


def test_the_persons_own_pictures_are_never_assumed():
    # an upload (or an edit of one, or a picture combined from the person's photos) keeps a wide search, even if a
    # picture_view was carried over from an earlier painted version
    for source in picture_view.PERSON_SOURCES:
        assert picture_view.assumed({"source": source, "picture_view": "front"}) is None, source


# Categories whose step 1 wording asks for a level, square-on front. The others keep their three-quarter wording,
# record no picture_view and get no view words first.
SQUARE_ON = ("prop_rigid", "environment")
LEVEL_ALREADY = ("humanoid", "foliage")


def test_square_on_categories_ask_for_a_level_front():
    # Step 2 turns its views from the step 1 picture as if it were the front.
    for cid in SQUARE_ON + LEVEL_ALREADY:
        c = reference.category(cid)
        block = c["pose_block"].lower()
        assert c.get("picture_view") == "front", cid
        assert not any(w in block for w in ("three-quarter", "slightly above", "from above", "high angle")), cid
        assert "front" in block and ("eye level" in block or "facing the camera directly" in block), cid


def test_other_categories_keep_their_wording_and_record_no_view():
    for c in reference.categories():
        if c["id"] in SQUARE_ON + LEVEL_ALREADY + ("material",):
            continue
        assert not c.get("view_lead") and c.get("picture_view") is None, c["id"]
        assert concept.prompt_and_view(_sc(c["id"]), {})[1] is None, c["id"]


def test_the_view_words_come_first_in_the_step_1_prompt():
    # Z-Image Turbo follows view words at the start of the prompt and ignores them at the end.
    for cid in SQUARE_ON:
        lead = reference.category(cid).get("view_lead") or ""
        assert lead.startswith("Straight-on front view") and "eye level" in lead, cid
        prompt, view = concept.prompt_and_view(_sc(cid), {})
        assert prompt.startswith(lead + " a sturdy knight"), cid
        assert view == "front", cid
        assert prompt.count(lead) == 1, cid
    for cid in LEVEL_ALREADY:
        assert not reference.category(cid).get("view_lead"), cid


def test_no_view_lead_without_the_category_wording(monkeypatch):
    lead = reference.category("prop_rigid")["view_lead"]
    assert lead not in concept.prompt_and_view(_sc("prop_rigid"), {"readiness_block": False})[0]
    from vgac import items

    monkeypatch.setattr(items, "concept_prompt_parts", lambda _a: {"pose_block": "The sword is shown alone, upright."})
    assert lead not in concept.prompt_and_view(_sc("prop_rigid"), {})[0]
