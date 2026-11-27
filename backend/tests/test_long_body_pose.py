"""Snake-like descriptions are painted in a flat open S rest pose; fish and other creatures keep their wording."""
import asyncio
import math
from types import SimpleNamespace

import numpy as np
import pytest

from vgac import pose_control, reference
from vgac.stages import concept

SERPENT = reference.category("serpent")


def _prompt(category: str, description: str, **params) -> str:
    sc = SimpleNamespace(category=reference.category(category), project=SimpleNamespace(style_lock={}),
                         style=reference.style("painted"), asset=SimpleNamespace(prompt=description, parts=[], held_item=None,
                                                                                 equipment=None, style_preset="painted"))
    return concept.prompt_and_view(sc, params)[0]


@pytest.mark.parametrize("description", ["a green garden snake stretched straight", "a brown freshwater eel",
                                         "two Vipers", "an earthworm", "a sea serpent"])
def test_long_bodies_get_the_rest_pose_first(description):
    prompt = _prompt("serpent", description)
    assert prompt.startswith(f"{SERPENT['long_body_lead']} {description}")
    assert SERPENT["long_body_pose_block"] in prompt and SERPENT["pose_block"] not in prompt
    assert prompt.count(SERPENT["long_body_lead"]) == 1


@pytest.mark.parametrize("description", ["a shark", "a round goldfish with a fan tail", "a snakehead fish",
                                         "a seahorse", "a wormwood coloured carp"])
def test_fish_keep_the_category_wording(description):
    prompt = _prompt("serpent", description)
    assert prompt.startswith(description) and SERPENT["pose_block"] in prompt
    assert "long_body" not in prompt and SERPENT["long_body_lead"] not in prompt


def test_only_the_serpent_category_has_a_rest_pose():
    for c in reference.categories():
        assert concept.long_body(c, "a snake") == (c["id"] == "serpent"), c["id"]


def test_without_the_category_wording_there_is_no_rest_pose():
    prompt = _prompt("serpent", "a snake", readiness_block=False)
    assert SERPENT["long_body_lead"] not in prompt and SERPENT["long_body_pose_block"] not in prompt


def test_the_outline_is_one_open_s_that_stays_apart_from_itself():
    pts = [pose_control.s_line(i / 400) for i in range(401)]
    for i, (x, y, r) in enumerate(pts):
        assert 0.08 <= x - r and x + r <= 0.92 and 0.08 <= y - r and y + r <= 0.92
        for x2, y2, r2 in pts[i + 80:]:  # a fifth of the length or more away along the body
            assert math.hypot(x2 - x, y2 - y) > 1.5 * (r + r2)
    im = np.asarray(pose_control.s_outline(512, 384))
    assert im.shape == (384, 512, 3) and 0.002 < (im.max(axis=2) > 0).mean() < 0.08


def _stage(tmp_path, category, description):
    sent = []

    async def stage_input(_path, name):
        sent.append(name)
        return name

    sc = SimpleNamespace(category=reference.category(category), dir=tmp_path, stage_input=stage_input,
                         asset=SimpleNamespace(prompt=description, held_item=None, equipment=None, parts=[]),
                         version=SimpleNamespace(id="abcdef123456"), set_params=lambda **_k: None, use_model=lambda _m: None)
    return sc, sent


def test_only_long_bodies_in_the_serpent_category_are_drawn_to_the_outline(tmp_path):
    def run(category, description, model="zturbo"):
        sc, sent = _stage(tmp_path, category, description)
        return asyncio.run(concept._pose_control(sc, model, {}, 256, 256)), sent

    control, sent = run("serpent", "a brown freshwater eel")
    assert control["strength"] == 0.8 and control["end"] == 0.6 and len(sent) == 1
    assert run("serpent", "a shark")[0] is None
    assert run("serpent", "a snake", model="qwen_image")[0] is None
    assert run("quadruped", "a snake")[0] is None
