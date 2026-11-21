"""The tileable material blend (graphs/material.make_seamless) leaves no pure black dot at the middle of a join.

Where a tile border meets a middle line both blend weights are 0. The blend used to divide 0 by its 1e-6 floor there,
so those 8 texels came out pure black: a 2 x 2 dot at the middle of every join in a repeat.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from vgac.graphs.material import make_seamless


def _noise(n: int, seed: int) -> np.ndarray:
    """Smooth colour noise kept away from black (40 to 215)."""
    rng = np.random.default_rng(seed)
    small = rng.uniform(40, 215, size=(n // 8 + 2, n // 8 + 2, 3)).astype(np.uint8)
    return np.asarray(Image.fromarray(small).resize((n, n), Image.BICUBIC))


def test_no_black_dot_at_the_middle_of_a_join(tmp_path):
    raw = _noise(128, 1)
    Image.fromarray(raw).save(tmp_path / "raw.png")
    make_seamless(tmp_path / "raw.png", tmp_path / "base_color.png")
    tile = np.asarray(Image.open(tmp_path / "base_color.png").convert("RGB"))
    h, w, _ = tile.shape
    centres = [(0, w // 2 - 1), (0, w // 2), (h - 1, w // 2 - 1), (h - 1, w // 2),
               (h // 2 - 1, 0), (h // 2, 0), (h // 2 - 1, w - 1), (h // 2, w - 1)]
    assert all(tile[y, x].max() > 0 for y, x in centres)
    assert int((tile.max(axis=-1) == 0).sum()) == 0
    # There the tile is the plain average of the picture and its half-offset copy.
    shifted = np.roll(np.roll(raw.astype(np.float32), h // 2, axis=0), w // 2, axis=1)
    for y, x in centres:
        assert np.abs(tile[y, x].astype(np.float32) - (raw[y, x] + shifted[y, x]) / 2).max() <= 1
