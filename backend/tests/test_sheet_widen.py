"""The sheet route's widening factor (WP-53): one factor per sheet from the step 1 cut-out and the sheet's own figures,
clamped to a range, with a fixed fallback when the measure is unreliable. Figures are widened sideways only.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from vgac import imaging
from vgac.graphs import qwen_edit
from vgac.stages import turnaround


def _cut(path: Path, size: tuple[int, int], boxes: list[tuple[int, int, int, int]]) -> Path:
    """An RGBA cut-out of `size` with opaque rectangles (x0, y0, x1, y1)."""
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    for x0, y0, x1, y1 in boxes:
        im.paste((200, 30, 30, 255), (x0, y0, x1, y1))
    im.save(path)
    return path


def _step1(tmp: Path, ratio: float) -> Path:
    """A single figure of width/height = ratio, 100 px tall."""
    h = 100
    w = round(ratio * h)
    return _cut(tmp / "step1.png", (300, 300), [(100, 100, 100 + w, 100 + h)])


def _sheet(tmp: Path, ratio_per_figure: list[float], step: int = 300) -> Path:
    """A sheet cut-out with one 100 px tall figure per entry, each of width/height = that entry, spaced `step` apart."""
    W, H = qwen_edit.SHEET_SIZE
    boxes = []
    for i, r in enumerate(ratio_per_figure):
        h = 100
        w = round(r * h)
        x0 = 60 + i * step
        boxes.append((x0, 500, x0 + w, 500 + h))
    return _cut(tmp / "sheet_cut.png", (W, H), boxes)


def _five(tmp: Path, front: float, back: float, side: float = 0.4) -> Path:
    # SHEET_ORDER is front, right, back, left, quarter: figure 0 is front, figure 2 is back.
    return _sheet(tmp, [front, side, back, side, side])


def test_factor_is_step1_ratio_over_the_mean_of_front_and_back(tmp_path):
    step1 = _step1(tmp_path, 0.5)
    sheet = _five(tmp_path, front=0.4, back=0.4)
    w = turnaround.sheet_widen(step1, sheet)
    assert w["factor"] == pytest.approx(1.25, abs=0.01)
    assert w["fallback"] is False and w["reason"] is None and w["clamped"] is False
    assert w["step1_ratio"] == pytest.approx(0.5, abs=0.01)
    assert w["front_ratio"] == pytest.approx(0.4, abs=0.01)
    assert w["back_ratio"] == pytest.approx(0.4, abs=0.01)
    assert w["figures"] == 5


def test_factor_is_clamped_to_the_top_of_its_range(tmp_path):
    w = turnaround.sheet_widen(_step1(tmp_path, 1.0), _five(tmp_path, front=0.4, back=0.4))
    assert w["factor"] == pytest.approx(1.6)
    assert w["clamped"] is True and w["fallback"] is False


def test_factor_is_clamped_to_the_bottom_of_its_range(tmp_path):
    w = turnaround.sheet_widen(_step1(tmp_path, 0.3), _five(tmp_path, front=0.4, back=0.4))
    assert w["factor"] == pytest.approx(1.0)
    assert w["clamped"] is True and w["fallback"] is False


def test_missing_step1_cutout_uses_the_fixed_fallback(tmp_path):
    w = turnaround.sheet_widen(None, _five(tmp_path, front=0.4, back=0.4))
    assert w["factor"] == pytest.approx(turnaround.SHEET_WIDEN_FALLBACK)
    assert w["fallback"] is True and w["reason"] == "no step 1 cut-out"


def test_front_and_back_disagreeing_uses_the_fixed_fallback(tmp_path):
    # 0.40 and 0.30 differ by more than 10 percent of their mean
    w = turnaround.sheet_widen(_step1(tmp_path, 0.5), _five(tmp_path, front=0.4, back=0.3))
    assert w["factor"] == pytest.approx(turnaround.SHEET_WIDEN_FALLBACK)
    assert w["fallback"] is True and w["reason"] == "front and back figures disagree"


def test_not_five_figures_uses_the_fixed_fallback(tmp_path):
    sheet = _sheet(tmp_path, [0.4, 0.4, 0.4, 0.4])
    w = turnaround.sheet_widen(_step1(tmp_path, 0.5), sheet)
    assert w["fallback"] is True and w["figures"] == 4


def test_split_widens_every_figure_sideways_by_the_factor(tmp_path):
    W, H = qwen_edit.SHEET_SIZE
    raw = Image.new("RGB", (W, H), (255, 255, 255))
    cut = _sheet(tmp_path, [0.4, 0.4, 0.4, 0.4, 0.4])
    raw.save(tmp_path / "raw.png")
    (tmp_path / "a").mkdir(exist_ok=True)
    base = turnaround.split_sheet(tmp_path / "raw.png", cut, tmp_path / "a", widen=1.0)
    base = turnaround.split_sheet(tmp_path / "raw.png", cut, tmp_path / "a", widen=1.0)
    (tmp_path / "b").mkdir(exist_ok=True)
    wide = turnaround.split_sheet(tmp_path / "raw.png", cut, tmp_path / "b", widen=1.5)
    assert base is not None and wide is not None
    for view in turnaround.SHEET_ORDER:
        w0 = imaging.alpha_bbox(base[view]["cutout"])["width"]
        w1 = imaging.alpha_bbox(wide[view]["cutout"])["width"]
        assert w1 / w0 == pytest.approx(1.5, rel=0.03)
        h0 = imaging.alpha_bbox(base[view]["cutout"])["height"]
        h1 = imaging.alpha_bbox(wide[view]["cutout"])["height"]
        assert h1 == pytest.approx(h0, rel=0.01)  # sideways only
