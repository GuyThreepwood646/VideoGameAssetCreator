"""WP-58: the AI upscaler runs for wood only (bakeoff/wp58/trialC/VERDICT.md, 2026-11-21). Wood is decided from the
material's description (whole words) and a realistic preset. Every other material keeps the seamless 1024 path. The
upscaler is skipped, not faked, when the model is not on the card that runs the job. The helpers keep the wrap-around
pad and the crop exact, so the upscaled tile stays seamless.
"""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from vgac.graphs.material import (UPSCALE_MODEL, UPSCALE_OUT, crop_margin, is_wood, upscale_graph, upscale_plan, wrap_pad)
from vgac.stages.material import upscale_model_ready

# The prompts the trial called wood, and the ones it called other categories (bakeoff/wp58/trialC, 10 prompts).
TRIAL_WOOD = ["oak planks with straight grain and a worn finish", "pine boards with a few small knots and a light varnish"]
TRIAL_OTHER = [
    "weathered grey granite cobblestones, flat paving",
    "cracked sandstone slab with fine pitting",
    "red clay brick wall with dark mortar joints",
    "old yellow brick with chipped edges and pale mortar",
    "brushed steel plate with fine parallel scratches",
    "dense green lawn grass, short blades, even coverage",
    "hand-painted stylised paint strokes, soft colour, cloud-like shapes",
    "cel-shaded mossy rock surface, bold simple shapes",
]


def test_a_realistic_oak_planks_takes_the_upscaler():
    plan = upscale_plan("old oak planks", "realistic_pbr")
    assert plan is not None
    assert plan["model"] == UPSCALE_MODEL
    assert plan["size"] == UPSCALE_OUT == 2048
    graph = upscale_graph("in.png", "wp58_wood")
    assert graph["2"]["class_type"] == "UpscaleModelLoader"
    assert graph["3"]["class_type"] == "ImageUpscaleWithModel"


@pytest.mark.parametrize("preset", ["painted", "cel", "retro64", "lowpoly_flat", "clay"])
def test_the_same_wood_words_with_a_stylised_preset_do_not(preset):
    assert upscale_plan("old oak planks", preset) is None


def test_red_brick_wall_does_not():
    assert upscale_plan("red brick wall", "realistic_pbr") is None


def test_woodland_moss_does_not_match_whole_words_only():
    assert upscale_plan("woodland moss", "realistic_pbr") is None
    assert is_wood("woodland moss", "realistic_pbr") is False


@pytest.mark.parametrize("prompt", [None, "", "stone wall"])
def test_missing_or_other_prompts_keep_today_path(prompt):
    assert upscale_plan(prompt, "realistic_pbr") is None


def test_missing_preset_keeps_today_path():
    assert upscale_plan("old oak planks", None) is None


def test_trial_wood_and_other_prompts_split_exactly():
    wood_hits = [p for p in TRIAL_WOOD if is_wood(p, "realistic_pbr")]
    other_hits = [p for p in TRIAL_OTHER if is_wood(p, "realistic_pbr")]
    print(f"trial wood prompts matched: {len(wood_hits)} of {len(TRIAL_WOOD)}; "
          f"non-wood trial prompts matched: {len(other_hits)} of {len(TRIAL_OTHER)}")
    assert wood_hits == TRIAL_WOOD
    assert other_hits == []


def test_the_upscaler_runs_at_the_tested_2048_size():
    assert upscale_plan("old oak planks", "realistic_pbr", texture_budget=2048)["size"] == 2048
    assert upscale_plan("old oak planks", "realistic_pbr", texture_budget=4096)["size"] == 2048


def test_mobile_low_1024_budget_skips_the_upscaler():
    # mobile_low is 1024 px for materials (data/categories/material.yaml); the upscaler is only used at 2048.
    assert upscale_plan("old oak planks", "realistic_pbr", texture_budget=1024) is None
    assert upscale_plan("old oak planks", "realistic_pbr", texture_budget=512) is None


class _FakeClient:
    def __init__(self, info=None, fail=False):
        self._info = info
        self._fail = fail

    async def object_info(self, node_class):
        if self._fail:
            raise ConnectionError("card is down")
        assert node_class == "UpscaleModelLoader"
        return self._info


def _info(names):
    # object_info returns the node's own description (the same shape stages/concept.py reads with combo_choices).
    return {"input": {"required": {"model_name": [list(names), {}]}}}


def test_model_on_the_card_is_ready():
    assert asyncio.run(upscale_model_ready(_FakeClient(_info([UPSCALE_MODEL, "4xLSDIR.pth"])), UPSCALE_MODEL)) is True


def test_model_missing_on_the_card_skips_the_upscaler():
    assert asyncio.run(upscale_model_ready(_FakeClient(_info(["4xLSDIR.pth"])), UPSCALE_MODEL)) is False


def test_card_that_cannot_answer_skips_the_upscaler():
    assert asyncio.run(upscale_model_ready(_FakeClient(fail=True), UPSCALE_MODEL)) is False
    assert asyncio.run(upscale_model_ready(_FakeClient(info=None), UPSCALE_MODEL)) is False


def test_wrap_pad_then_crop_returns_the_tile_exactly():
    rng = np.random.default_rng(3)
    tile = rng.integers(0, 255, size=(64, 64, 3), dtype=np.uint8)
    padded = wrap_pad(tile, pad=8)
    assert padded.shape == (80, 80, 3)
    # Stand-in for an exact 4x upscale of the padded tile: nearest-neighbour repeat, so the crop is checkable pixel by pixel.
    up = np.repeat(np.repeat(padded, 4, axis=0), 4, axis=1)
    back = crop_margin(up, pad=8, scale=4)
    assert back.shape == (256, 256, 3)
    assert np.array_equal(back, np.repeat(np.repeat(tile, 4, axis=0), 4, axis=1))


def test_wrap_pad_keeps_the_seam_closed():
    tile = np.arange(16 * 16 * 3, dtype=np.uint8).reshape(16, 16, 3)
    padded = wrap_pad(tile, pad=4)
    assert np.array_equal(padded[:4, 4:-4], tile[-4:, :])
    assert np.array_equal(padded[-4:, 4:-4], tile[:4, :])
