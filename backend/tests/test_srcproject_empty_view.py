"""vgac/srcproject.py, a view with no sample points (PL-161): a rigid prop seen face-on can give a projection whose texels
are none, so its sample array is empty. catmull must return an empty (0, c) result instead of failing on the reshape, and
blend must treat such a view as painting nothing, leaving the other views' paint exactly as it was."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from vgac import srcproject

from tests.test_srcproject_join import flat_atlas


def test_catmull_of_no_points_is_an_empty_result() -> None:
    img = np.full((8, 8, 3), 0.4)
    out = srcproject.catmull(img, np.zeros((0, 1)), np.zeros((0, 1)))[:, 0]
    assert out.shape == (0, 3)


def test_a_view_with_no_sample_points_paints_nothing_and_changes_nothing() -> None:
    atlas = flat_atlas(60, 10)
    x = atlas.x
    img = np.full((8, 8, 3), 0.4)
    good_sel = np.flatnonzero(x < 0.1)
    good = {"sel": good_sel, "w": np.ones(good_sel.size),
            "sp": np.full((good_sel.size, 2), 3.5)}
    empty = {"sel": np.zeros(0, dtype=int), "w": np.zeros(0),
             "sp": np.zeros((0, 2))}
    alone = srcproject.blend(atlas, [good], [img], 1)
    with_empty = srcproject.blend(atlas, [good, empty], [img, img], 1)
    for a, b in zip(alone, with_empty):
        np.testing.assert_array_equal(a, b)
