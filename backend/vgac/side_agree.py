"""Side views agree with the front and back (WP-51 part A).

A side view of a humanoid or prop is checked against the front and back cut-outs of the same turnaround: the figure must
be about the same height, and its feet must sit at the same level. Both are measured on the cut-out alpha, in shares of
the front's height, so a picture drawn at another size compares. Object agreement (colour structure) is the existing
hist_dist check; this module adds the two measures a side view can get wrong while its colours still match.

UNTESTED ON A CARD: the limits were set on stored turnarounds on the processor only (bakeoff/wp51a), not on a card redraw.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from vgac import imaging

# Limits, set on the labelled set in bakeoff/wp51a (see its notes). Heights: a side view's figure height over the mean of the
# front and back heights. Feet: the side's lowest alpha row minus the mean of the front's and back's, over the front height.
HEIGHT_RANGE = (0.9, 1.1)
FEET_LIMIT = 0.05
SIDE_VIEWS = ("left", "right")


def _rows(path: Path | str) -> tuple[int, int, int, int]:
    """(top row, bottom row, height, picture height) of alpha > 127 in a cut-out. Pictures of one turnaround need not share
    a size (a side or back can be drawn taller), so the rows are read against each picture's own height."""
    a = imaging._alpha(path) > 127
    ys = np.where(a.any(axis=1))[0]
    if not len(ys):
        return 0, 0, 0, a.shape[0]
    return int(ys.min()), int(ys.max()), int(ys.max() - ys.min() + 1), a.shape[0]


def measures(side: Path | str, front: Path | str, back: Path | str) -> dict[str, float]:
    """height (side figure height over the mean of the front and back figure heights, each as a share of its own picture)
    and feet (the side's lowest row minus the mean of the front's and back's lowest rows, each as a share of its picture,
    over the front's figure height as a share of its picture). Zeros when a cut-out is empty."""
    st, sb, sh, sI = _rows(side)
    ft, fb, fh, fI = _rows(front)
    bt, bb, bh, bI = _rows(back)
    if not (sh and fh and bh):
        return {"height": 0.0, "feet": 0.0}
    fh_s, bh_s = fh / fI, bh / bI
    ref_h = (fh_s + bh_s) / 2
    feet_ref = (fb / fI + bb / bI) / 2
    return {"height": round((sh / sI) / ref_h, 4), "feet": round((sb / sI - feet_ref) / fh_s, 4)}


def words(view: str, m: dict[str, float]) -> str:
    """The check's sentence (plain words, the view named): what differs, or that it agrees."""
    name = {"left": "left side", "right": "right side"}.get(view, view)
    lo, hi = HEIGHT_RANGE
    what = []
    if not lo <= m["height"] <= hi:
        what.append(f"is {m['height']:.0%} as tall as the front and back")
    if abs(m["feet"]) > FEET_LIMIT:
        what.append("has its feet " + ("lower" if m["feet"] > 0 else "higher") + " than the front and back")
    if not what:
        return f"The {name} is the same height as the front and back, and its feet are at the same level."
    return (f"The {name} {' and '.join(what)}, so it does not show the same figure as the front and back. "
            "The model and its texture can come from a different size or pose. Draw this view again.")


def check(view: str, side: Path | str, front: Path | str, back: Path | str) -> dict[str, Any]:
    """side_agree for a left or right view: ok when the height is in HEIGHT_RANGE and the feet within FEET_LIMIT."""
    from vgac import quality

    m = measures(side, front, back)
    lo, hi = HEIGHT_RANGE
    ok = lo <= m["height"] <= hi and abs(m["feet"]) <= FEET_LIMIT
    return quality.check("side_agree", ok, m["height"], [lo, hi],
                         vars={"view": view, "words": words(view, m), "feet": m["feet"], "feet_limit": FEET_LIMIT})
