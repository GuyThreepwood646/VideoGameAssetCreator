"""vgac/colourmatch.py: one global, capped colour correction of the base colour towards the picture."""
import numpy as np

from vgac import colourmatch, face


def _scene(brighten=1.0, shift_rgb=(0, 0, 0), n=160):
    rng = np.random.default_rng(1)
    base = np.clip(0.45 + 0.15 * np.sin(np.linspace(0, 6, n))[None, :, None] + rng.normal(0, 0.02, (n, n, 3)), 0, 1)
    mask = np.zeros((n, n), bool)
    mask[20:140, 30:130] = True
    pic = np.where(mask[..., None], base, 0.0)
    render = np.where(mask[..., None], np.clip(base * brighten + np.array(shift_rgb), 0, 1), 0.0)
    return render, mask, pic, mask


def test_lab_round_trip():
    rgb = np.random.default_rng(0).uniform(0.02, 0.98, (50, 3))
    assert np.abs(colourmatch.from_lab(face.to_lab(rgb)) - rgb).max() < 0.01


def test_a_brighter_render_is_pulled_back_to_the_picture():
    render, rm, pic, pm = _scene(brighten=1.12)
    p = colourmatch.plan(render, rm, pic, pm)
    assert p["mode"] == "lab_shift" and p["reason"] == "applied"
    assert p["de_after"] < p["de_before"] * 0.6 and p["moved_de"] <= colourmatch.MAX_MOVE_DE
    out = colourmatch.apply(render, p)
    assert abs(out[rm].mean() - pic[pm].mean()) < abs(render[rm].mean() - pic[pm].mean()) * 0.4


def test_a_matching_render_is_left_alone():
    render, rm, pic, pm = _scene()
    p = colourmatch.plan(render, rm, pic, pm)
    assert p["mode"] == "none"
    assert np.array_equal(colourmatch.apply(render, p), render)


def test_the_move_is_capped():
    render, rm, pic, pm = _scene(shift_rgb=(0.35, -0.2, 0.0))  # far off: a full correction would move colours a lot
    p = colourmatch.plan(render, rm, pic, pm, cap=8.0)
    if p["mode"] != "none":
        assert p["moved_de"] <= 8.0 + 1e-6
    assert colourmatch.plan(render, rm, pic, pm, cap=0.5)["mode"] == "none"


def test_too_small_an_overlap_gives_no_correction():
    render, rm, pic, pm = _scene(brighten=1.2)
    pm2 = np.zeros_like(pm)
    pm2[0:5, 0:5] = True
    assert colourmatch.plan(render, rm, pic, pm2)["reason"] == "overlap_too_small"


def test_a_shift_does_not_flatten_detail_at_the_ends_of_the_range():
    rng = np.random.default_rng(5)
    tex = np.clip(0.9 + rng.normal(0, 0.04, (64, 64, 3)), 0, 1)  # bright and detailed: a plain clip would flatten it
    out = colourmatch.apply(tex, {"mode": "lab_shift", "strength": 1.0, "shift": [6.0, 0.0, 0.0]})
    assert out.std() > 0.6 * tex.std()
    dark = np.clip(0.06 + rng.normal(0, 0.03, (64, 64, 3)), 0, 1)
    out = colourmatch.apply(dark, {"mode": "lab_shift", "strength": 1.0, "shift": [-6.0, 0.0, 0.0]})
    assert out.std() > 0.6 * dark.std() and out.min() >= 0.0 and out.max() <= 1.0
