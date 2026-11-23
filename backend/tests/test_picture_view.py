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


def test_every_painted_category_asks_for_a_level_square_on_front():
    # Step 2 turns its views from the step 1 picture as if it were the front (golden set 2026-11-22), so step 1 asks every
    # category with a picture for a level, square-on front, and says so with picture_view.
    for c in reference.categories():
        if c["id"] == "material":
            continue
        block = c["pose_block"].lower()
        assert c.get("picture_view") == "front", c["id"]
        assert not any(w in block for w in ("three-quarter", "slightly above", "from above", "high angle")), c["id"]
        assert "front" in block and ("eye level" in block or "facing the camera directly" in block), c["id"]


LEVEL_ALREADY = ("humanoid", "foliage")  # these already came out square-on with their wording last (golden set)


def test_the_view_words_come_first_in_the_step_1_prompt():
    # Z-Image Turbo ignored square-on wording at the end of the prompt (step1-view trial 2026-11-22: 0 of 8 square-on) and
    # followed it at the start (8 of 8), so every painted category but these two leads with its view words.
    for c in reference.categories():
        if c["id"] in ("material",) + LEVEL_ALREADY:
            continue
        lead = c.get("view_lead") or ""
        assert lead.startswith("Straight-on front view") and "eye level" in lead, c["id"]
        prompt, view = concept.prompt_and_view(_sc(c["id"]), {})
        assert prompt.startswith(lead + " a sturdy knight"), c["id"]
        assert view == "front", c["id"]
        assert prompt.count(lead) == 1, c["id"]


def test_no_view_lead_without_the_category_wording(monkeypatch):
    lead = reference.category("prop_rigid")["view_lead"]
    assert lead not in concept.prompt_and_view(_sc("prop_rigid"), {"readiness_block": False})[0]
    from vgac import items

    monkeypatch.setattr(items, "concept_prompt_parts", lambda _a: {"pose_block": "The sword is shown alone, upright."})
    assert lead not in concept.prompt_and_view(_sc("prop_rigid"), {})[0]
