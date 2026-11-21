"""WP-58: the AI upscaler runs for wood only (bakeoff/wp58/trialC/VERDICT.md, 2026-11-21). Every other surface keeps the
seamless 1024 path. The helpers keep the wrap-around pad and the crop exact, so the upscaled tile stays seamless.
"""

from __future__ import annotations

import numpy as np
import pytest

from vgac.graphs.material import UPSCALE_MODEL, UPSCALE_OUT, crop_margin, upscale_graph, upscale_plan, wrap_pad


def test_a_wood_material_takes_the_upscaler_path():
    plan = upscale_plan({"surface": "wood"})
    assert plan is not None
    assert plan["model"] == UPSCALE_MODEL
    assert plan["size"] == UPSCALE_OUT == 2048
    graph = upscale_graph("in.png", "wp58_wood")
    assert graph["2"]["class_type"] == "UpscaleModelLoader"
    assert graph["3"]["class_type"] == "ImageUpscaleWithModel"
    assert graph["8"]["class_type"] == "SaveImage"


@pytest.mark.parametrize("surface", ["stone", "brick", "metal", "grass", "stylised", "", None])
def test_other_surfaces_keep_today_path(surface):
    params = {} if surface is None else {"surface": surface}
    assert upscale_plan(params) is None


def test_no_params_keeps_today_path():
    assert upscale_plan(None) is None
    assert upscale_plan({}) is None


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
    # The padded picture's border is the tile's opposite edge, so an upscaler that reads across the edge sees no seam.
    tile = np.arange(16 * 16 * 3, dtype=np.uint8).reshape(16, 16, 3)
    padded = wrap_pad(tile, pad=4)
    assert np.array_equal(padded[:4, 4:-4], tile[-4:, :])
    assert np.array_equal(padded[-4:, 4:-4], tile[:4, :])
