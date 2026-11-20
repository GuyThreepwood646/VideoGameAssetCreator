"""The four-view crop spread is recorded as numbers only (vgac/cropcheck.py, WP-65 step 3): no warning, no check."""
import numpy as np
from PIL import Image

from vgac import cropcheck


def _cutout(path, w, h):
    """A transparent 400 x 400 picture with an opaque w x h block at the centre."""
    a = np.zeros((400, 400, 4), dtype=np.uint8)
    x0, y0 = (400 - w) // 2, (400 - h) // 2
    a[y0: y0 + h, x0: x0 + w] = (120, 90, 60, 255)
    Image.fromarray(a, "RGBA").save(path)
    return path


def test_equal_extents_spread_is_zero(tmp_path):
    paths = {v: _cutout(tmp_path / f"{v}.png", 200, 300) for v in ("front", "left", "back", "right")}
    r = cropcheck.spread_check(paths)
    assert r["spread"] == 0.0 and r["above_threshold"] is False
    assert r["note"].startswith("recorded only")


def test_one_view_much_larger_is_recorded_as_above_threshold(tmp_path):
    paths = {"front": _cutout(tmp_path / "f.png", 100, 100), "left": _cutout(tmp_path / "l.png", 100, 100),
             "back": _cutout(tmp_path / "b.png", 100, 100), "right": _cutout(tmp_path / "r.png", 150, 100)}
    r = cropcheck.spread_check(paths)
    assert r["extents"] == {"front": 100, "left": 100, "back": 100, "right": 150}
    assert abs(r["spread"] - 0.5) < 1e-3 and r["above_threshold"] is True


def test_a_view_with_no_subject_is_left_out_of_the_spread(tmp_path):
    paths = {"front": _cutout(tmp_path / "f.png", 100, 100), "left": _cutout(tmp_path / "l.png", 0, 0)}
    r = cropcheck.spread_check(paths)
    assert r["extents"]["left"] == 0 and r["spread"] == 0.0 and r["above_threshold"] is False


def test_no_subject_at_all_is_reported_not_raised(tmp_path):
    r = cropcheck.spread_check({"front": _cutout(tmp_path / "f.png", 0, 0)})
    assert r["spread"] is None and r["above_threshold"] is False
