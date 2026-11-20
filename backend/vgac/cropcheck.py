"""The four-view crop spread, recorded as a number (WP-65 phase 2, step 3). Computed from the four cut-outs before the
Pixal3D build and written to the stage metrics only. It is not a check, it raises no warning, and it never switches the
crop: its usefulness is not measured yet.

Today's crop cuts each view to a square of its own extent x 1.1 (graphs/pixal3d.py, ImageCropToMask). When the four
views have very different extents, one view is shown at a much larger scale than another and the model comes out with
the wrong depth. The spread (largest side / smallest side - 1) says how much. On the WP-65 library (12 subjects, phase
2 step 2) the spread separated the subjects where a shared crop side gave the better proportions on seed 42; on seeds
43 and 44 it did not separate cleanly (arch and songbird flipped), so nothing acts on it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

ALPHA_MIN = 204  # the crop's subject: alpha above this (as graphs/pixal3d.py's mask)
PAD = 1.1  # today's crop side = the view's own extent x PAD
ABOVE_SPREAD = 0.20  # a flag in the recorded numbers only (between the phase 2 data points 0.18 and 0.24); nothing acts on it


def _extent(path: Path | str) -> int:
    a = np.asarray(Image.open(path).convert("RGBA"))[..., 3]
    ys, xs = np.nonzero(a > ALPHA_MIN)
    if len(xs) == 0:
        return 0
    return int(max(xs.max() - xs.min() + 1, ys.max() - ys.min() + 1))


def spread_check(paths: dict[str, Path | str]) -> dict[str, Any]:
    """{extents, today_side, spread, above_threshold, threshold, note}: the four views' own extents, today's crop sides
    and their spread. A view with no subject counts as extent 0 and is left out of the spread."""
    extents = {v: _extent(p) for v, p in paths.items()}
    sides = {v: round(e * PAD, 1) for v, e in extents.items() if e > 0}
    note = "recorded only: the crop is not switched and nothing is shown or checked"
    if not sides:
        return {"extents": extents, "today_side": {}, "spread": None, "above_threshold": False, "threshold": ABOVE_SPREAD,
                "note": "no subject found in the cut-outs; " + note}
    spread = max(sides.values()) / min(sides.values()) - 1
    return {"extents": extents, "today_side": sides, "spread": round(spread, 4),
            "above_threshold": bool(spread > ABOVE_SPREAD), "threshold": ABOVE_SPREAD, "note": note}
