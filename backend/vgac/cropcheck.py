"""Warn-only check of the four-view crop (WP-65 phase 2, step 3). Records how much today's per-view crop would rescale
each view, computed from the four cut-outs before the Pixal3D build. It never switches the crop.

Today's crop cuts each view to a square of its own extent x 1.1 (graphs/pixal3d.py, ImageCropToMask). When the four
views have very different extents, one view is shown at a much larger scale than another and the model comes out with
the wrong depth. The spread (largest side / smallest side - 1) says how much. On the WP-65 library (12 subjects, phase
2 step 2) the spread separated the subjects where a shared crop side gave the better proportions on seed 42; on seeds
43 and 44 it did not separate cleanly (arch and songbird flipped), so the check is a warning, not a switch.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

ALPHA_MIN = 204  # the crop's subject: alpha above this (as graphs/pixal3d.py's mask)
PAD = 1.1  # today's crop side = the view's own extent x PAD
WARN_SPREAD = 0.20  # warn when the views' crop sides differ by more than this (between the phase 2 data points 0.18 and 0.24)


def _extent(path: Path | str) -> int:
    a = np.asarray(Image.open(path).convert("RGBA"))[..., 3]
    ys, xs = np.nonzero(a > ALPHA_MIN)
    if len(xs) == 0:
        return 0
    return int(max(xs.max() - xs.min() + 1, ys.max() - ys.min() + 1))


def spread_check(paths: dict[str, Path | str]) -> dict[str, Any]:
    """{extents, today_side, spread, warn, threshold}: the four views' own extents, today's crop sides and their spread.
    A view with no subject counts as extent 0 and is left out of the spread."""
    extents = {v: _extent(p) for v, p in paths.items()}
    sides = {v: round(e * PAD, 1) for v, e in extents.items() if e > 0}
    if not sides:
        return {"extents": extents, "today_side": {}, "spread": None, "warn": False, "threshold": WARN_SPREAD,
                "note": "no subject found in the cut-outs"}
    spread = max(sides.values()) / min(sides.values()) - 1
    return {"extents": extents, "today_side": sides, "spread": round(spread, 4), "warn": bool(spread > WARN_SPREAD),
            "threshold": WARN_SPREAD, "note": "warn only: the crop is not switched"}
