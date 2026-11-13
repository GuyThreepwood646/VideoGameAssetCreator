"""A picture combined from the person's own pictures keeps their clothes and hair (step 0.5):
the compose prompt no longer carries the look's palette or hair rule and says to keep clothes and hair, and the
check `compose_match` (stages/concept.compose_check) warns when the combined picture's clothes or hair colour differ.
"""
from __future__ import annotations

from PIL import Image, ImageDraw

from vgac import compose, guidance
from vgac.stages import concept

BG = (225, 225, 228)


def person(hair=(25, 20, 20), top=(190, 150, 80), legs=(50, 90, 160), cutout: bool = False) -> Image.Image:
    """A 400 x 800 figure on a plain grey background (or transparent): hair, a jacket, trousers."""
    im = Image.new("RGBA", (400, 800), (0, 0, 0, 0) if cutout else BG + (255,))
    d = ImageDraw.Draw(im)
    d.ellipse((160, 40, 240, 140), fill=hair + (255,))
    d.ellipse((172, 70, 228, 140), fill=(215, 170, 140, 255))
    d.rectangle((130, 150, 270, 420), fill=top + (255,))
    d.rectangle((140, 420, 260, 740), fill=legs + (255,))
    return im


def test_compose_prompt_keeps_clothes_and_hair_and_the_run_drops_the_palette_and_hair_rule():
    roles = [{"role": "face"}, {"role": "body"}]
    text = compose.compose_prompt(roles, compose.plan_inputs(roles), "A woman. Realistic look.")
    assert "Keep the hair" in text and "clothes exactly as the pictures show" in text
    assert "not in the look of the pictures" not in text
    # build_prompt leaves the palette and the hair rule out when asked to
    import inspect

    src = inspect.getsource(compose.run)
    assert '"palette_block": False' in src and '"hair_block": False' in src


def test_same_clothes_and_hair_pass_even_in_another_picture():
    inputs = [("face", person()), ("body", person(top=(185, 148, 84)))]
    c = concept.compose_check(inputs, person(cutout=True, top=(195, 152, 78)))
    assert c is not None and c["passed"] and c["severity"] == "warn", c


def test_repainted_clothes_warn_with_plain_help_and_a_fix():
    inputs = [("face", person()), ("body", person())]
    c = concept.compose_check(inputs, person(cutout=True, top=(60, 62, 60), legs=(40, 40, 42)))
    assert c is not None and not c["passed"] and c["name"] == "compose_match"
    first = c["help"].split(":")[0]
    assert "the clothes" in first and "the hair" not in first and "carry on" in c["help"] and "{" not in c["help"]
    assert c["fix_label"] == "Combine them again" and c["fix"]["stage"] == "concept" and c["fix"]["op"] == "adjust"
    assert guidance.CHECK_GUIDE["compose_match"]["severity"] == "warn"


def test_changed_hair_colour_warns():
    inputs = [("face", person(hair=(30, 22, 20)))]
    c = concept.compose_check(inputs, person(cutout=True, hair=(210, 180, 110)))
    assert c is not None and not c["passed"] and "keep the hair" in c["help"]


def test_no_verdict_for_a_busy_photo_or_nothing_to_compare():
    busy = Image.effect_noise((400, 800), 80).convert("RGB")
    assert concept.compose_check([("body", busy)], person(cutout=True)) is None
    assert concept.compose_check([("other", person())], person(cutout=True)) is None
    assert concept.compose_check([("body", person())], Image.new("RGBA", (50, 50), (0, 0, 0, 0))) is None
