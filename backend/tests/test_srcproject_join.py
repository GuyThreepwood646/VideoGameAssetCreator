"""WP-50 trial B: the join blend between views (srcproject.tone_adjust, JOIN_BLEND).

A synthetic flat atlas is painted by two views, a first one (the person's picture) and a second (a side view) whose colour is
a known amount lighter. Where they meet the second view's colour must come to the first's, and with the join blend only a
few centimetres from the overlap may change."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from vgac import srcproject

STEP = 0.12      # how much lighter the second view paints than the first
PITCH = 0.002    # metres between texels


def flat_atlas(nx: int = 400, ny: int = 60) -> SimpleNamespace:
    """A strip of surface, nx by ny texels, standing in for the atlas (position and the gutter flag are all that is read)."""
    x, y = np.meshgrid(np.arange(nx) * PITCH, np.arange(ny) * PITCH)
    pos = np.stack([x.ravel(), y.ravel(), np.zeros(x.size)], 1)
    return SimpleNamespace(pos=pos, nrm=np.tile([0.0, 0.0, 1.0], (x.size, 1)), is_gut=np.zeros(x.size, bool),
                           idx=np.arange(x.size), x=x.ravel())


def two_views(atlas: SimpleNamespace, lo: float = 0.40, hi: float = 0.60):
    """The first view paints x below `hi`, the second x above `lo`: they overlap between. Colour is a smooth ramp plus STEP
    for the second, which also paints a bit more weakly at the edge of the overlap."""
    x = atlas.x
    base = np.stack([0.3 + 0.2 * x, 0.4 + 0.1 * x, 0.5 - 0.1 * x], 1)
    views = [SimpleNamespace(name="picture"), SimpleNamespace(name="left")]
    s0 = np.flatnonzero(x < hi)
    s1 = np.flatnonzero(x > lo)
    projs = [{"sel": s0, "w": np.where(x[s0] < lo, 1.0, np.clip((hi - x[s0]) / (hi - lo), 0.05, 1.0))},
             {"sel": s1, "w": np.where(x[s1] > hi, 1.0, np.clip((x[s1] - lo) / (hi - lo), 0.05, 1.0))}]
    layers = [{"sel": s0, "w": projs[0]["w"], "col": base[s0]}, {"sel": s1, "w": projs[1]["w"], "col": np.clip(base[s1] + STEP, 0, 1)}]
    return views, projs, layers, base


def blended(atlas, projs, cols) -> np.ndarray:
    num, den = np.zeros((atlas.pos.shape[0], 3)), np.zeros(atlas.pos.shape[0])
    for p, c in zip(projs, cols):
        ww = p["w"] ** srcproject.BLEND_POW
        num[p["sel"]] += c * ww[:, None]
        den[p["sel"]] += ww
    return num / np.maximum(den, 1e-12)[:, None]


def step_between(atlas, projs, cols, at: float) -> float:
    """The colour step across x = at: what the second view's own colour is against the first's, where each paints alone."""
    x = atlas.x
    a = cols[0][np.abs(x[projs[0]["sel"]] - at) < 0.02].mean(0)
    b = cols[1][np.abs(x[projs[1]["sel"]] - at) < 0.02].mean(0)
    return float(np.abs(a - b).max())


@pytest.fixture(autouse=True)
def reset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(srcproject, "JOIN_BLEND", False)


def test_the_blend_with_the_off_switches_is_what_it_was() -> None:
    """compose calls tone_adjust only when JOIN_BLEND is on; with it off the blend takes no layers and no colours."""
    assert srcproject.JOIN_BLEND is False
    a = flat_atlas(60, 10)
    views, projs, layers, base = two_views(a, 0.04, 0.08)
    img = np.full((8, 8, 3), 0.4)
    for p in projs:
        p["sp"] = np.full((p["sel"].size, 2), 3.5)
    plain = srcproject.blend(a, projs, [img, img], 1)
    seen: list = []
    with_layers = srcproject.blend(a, projs, [img, img], 1, layers=seen)
    for x, y in zip(plain, with_layers):
        np.testing.assert_array_equal(x, y)
    assert len(seen) == 2
    again = srcproject.blend(a, projs, [img, img], 1, colours=[l["col"] for l in seen])
    for x, y in zip(plain, again):
        np.testing.assert_array_equal(x, y)


def test_a_view_that_shares_too_little_with_the_others_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(srcproject, "JOIN_BLEND", True)
    a = flat_atlas()
    views, projs, layers, base = two_views(a, lo=0.78, hi=0.789)   # under 1 cm of overlap: fewer texels than TONE_MIN
    cols, rec = srcproject.tone_adjust(a, views, projs, layers)
    assert not rec["left"]["matched"]
    np.testing.assert_array_equal(cols[1], layers[1]["col"])


def test_different_material_in_the_overlap_is_not_read_as_a_difference_of_tone(monkeypatch: pytest.MonkeyPatch) -> None:
    """A texel where the second view shows a dark edge the first does not is dropped by the guard, so the field still levels the rest."""
    monkeypatch.setattr(srcproject, "JOIN_BLEND", True)
    a = flat_atlas()
    views, projs, layers, base = two_views(a)
    col = layers[1]["col"].copy()
    x = a.x[projs[1]["sel"]]
    col[(x > 0.45) & (x < 0.48)] = 0.0    # a black stripe in the overlap, in the second view only
    layers[1]["col"] = col
    cols, rec = srcproject.tone_adjust(a, views, projs, layers)
    ok = (x > 0.52) & (x < 0.58)
    assert (cols[1][ok] - col[ok]).mean() == pytest.approx(-STEP, abs=0.03)


def test_the_join_blend_levels_the_step_and_leaves_texels_away_from_the_join_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(srcproject, "JOIN_BLEND", True)
    a = flat_atlas()
    views, projs, layers, base = two_views(a)
    before = step_between(a, projs, [l["col"] for l in layers], 0.5)
    cols, rec = srcproject.tone_adjust(a, views, projs, layers)
    after = step_between(a, projs, cols, 0.5)
    assert after < 0.25 * before
    far = a.x[projs[1]["sel"]] > 0.95   # more than 30 cm from the overlap
    np.testing.assert_array_equal(cols[1][far], layers[1]["col"][far])      # byte-identical away from the join
    np.testing.assert_array_equal(cols[0], layers[0]["col"])
    assert not np.array_equal(cols[1], layers[1]["col"])
    assert rec["left"]["field_texels"] > 0
