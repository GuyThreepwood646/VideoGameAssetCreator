"""Does the back view's hair match the side and front views'? (turnaround check hair_matches_sides)

Measured from the views' cut-outs, with a mask of the hair's own colour near the head: where the hair ends (as a share of the
head-to-shoulder height) and how wide it is just above its end, the back against the sides and the front.

Only for hair that is there to match: long enough to reach the shoulders in both side views, a natural hair colour, a mask
that stays near the head. Anything else (bald, short hair, a hat or hood, a mask that ran into clothes of the same colour)
gives no verdict, so the check stays quiet rather than guess."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

N = 512            # the figure is scaled to this many rows
ZONE = 0.42        # the hair is looked for in the top share of the figure's height
CROWN = (0.012, 0.035)
TOL_L = 55.0       # how far a pixel's lightness may differ from the hair's and still be hair (8-bit Lab)
TOL_AB = 16.0      # and its colour (a, b)


def load_figure(path: Path | str, rows: int = N) -> tuple[np.ndarray, np.ndarray] | None:
    """(rgb, opaque) of the cut-out cropped to the figure and scaled to `rows` rows high, or None when it is empty."""
    try:
        im = Image.open(path).convert("RGBA")
    except (OSError, ValueError):
        return None
    a = np.asarray(im)[..., 3] > 127
    ys, xs = np.where(a)
    if not len(ys):
        return None
    im = im.crop((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
    w = max(1, round(im.width * rows / im.height))
    im = im.resize((w, rows), Image.LANCZOS)
    arr = np.asarray(im)
    return arr[..., :3].copy(), arr[..., 3] > 127


def hair_mask(rgb: np.ndarray, opaque: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    """The hair near the head: opaque pixels in the top ZONE of the figure whose colour is near the hair's, joined to the
    crown. The hair's colour starts as the crown's median and is re-taken from the mask a few times (hair has highlights and
    shade, so the first pick is only its middle). Returns (mask, info) with the colour (Lab, 8-bit scale)."""
    h, w = opaque.shape
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    y0, y1 = int(CROWN[0] * h), max(int(CROWN[1] * h), int(CROWN[0] * h) + 2)
    band = opaque[y0:y1]
    cols = np.where(band.any(axis=0))[0]
    if not len(cols):
        return np.zeros_like(opaque), {"ok": False}
    mid = (cols.min() + cols.max()) / 2
    half = max(2.0, (cols.max() - cols.min()) * 0.3)
    sel = np.zeros_like(opaque)
    sel[y0:y1, int(mid - half):int(mid + half) + 1] = True
    sel &= opaque
    if sel.sum() < 12:
        return np.zeros_like(opaque), {"ok": False}
    med = np.median(lab[sel], axis=0)
    zone = np.zeros_like(opaque)
    zone[:int(ZONE * h)] = True
    zone &= opaque
    out = np.zeros_like(opaque)
    for _ in range(3):
        near = (np.abs(lab[..., 0] - med[0]) < TOL_L) & (np.linalg.norm(lab[..., 1:] - med[1:], axis=2) < TOL_AB) & zone
        near = cv2.morphologyEx(near.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        n, labels = cv2.connectedComponents(near, connectivity=8)
        keep = np.unique(labels[sel & (near > 0)])
        keep = keep[keep > 0]
        out = np.isin(labels, keep) if len(keep) else np.zeros_like(opaque)
        if out.sum() < 12:
            break
        med = np.median(lab[out], axis=0)
    return out, {"ok": True, "lab": [round(float(v), 1) for v in med]}


def shoulder_row(opaque: np.ndarray) -> int:
    """The row where the outline first widens to the shoulders, after the head and neck."""
    h = opaque.shape[0]
    width = opaque.sum(axis=1).astype(float)
    head = width[int(0.03 * h):int(0.09 * h)].max() if h else 0
    for y in range(int(0.09 * h), int(0.35 * h)):
        if width[y] >= 1.7 * head:
            return y
    return int(0.2 * h)


def _half_end(m: np.ndarray) -> float:
    """The row (share of the figure's height) where the hair's width first falls under half of its width higher up (the
    75th percentile over rows 5 to 16 percent). Unlike the lowest hair pixel it ignores single strands, so it says where the mass of the hair
    stops."""
    w = m.sum(axis=1).astype(float)
    h = len(w)
    plateau = float(np.percentile(w[int(.05 * h):int(.16 * h)], 75))
    for y in range(int(.08 * h), int(ZONE * h)):
        if w[y] < 0.5 * plateau:
            return y / h
    return ZONE


def measure(path: Path | str) -> dict[str, Any] | None:
    fig = load_figure(path)
    if fig is None:
        return None
    rgb, opaque = fig
    h, w = opaque.shape
    m, info = hair_mask(rgb, opaque)
    if not info["ok"]:
        return {"ok": False}
    ys = np.where(m.sum(axis=1) >= 3)[0]
    end = int(ys.max()) if len(ys) else 0
    sh = shoulder_row(opaque)
    rows = slice(sh, min(h, sh + int(0.04 * h)))
    cover = float(m[rows].sum(axis=1).mean()) / h
    sil = float(opaque[rows].sum(axis=1).mean()) / h
    lo, hi = max(0, end - int(0.06 * h)), max(1, end - int(0.02 * h))
    band = float(m[lo:hi].sum(axis=1).mean()) / h if hi > lo else 0.0
    return {"ok": True, "band": round(band, 4), "end": round(end / h, 4), "half_end": round(_half_end(m), 4), "shoulder": round(sh / h, 4), "end_below_shoulder": round((end - sh) / h, 4),
            "cover": round(cover, 4), "sil": round(sil, 4), "share": round(cover / max(1e-6, sil), 4),
            "area": round(float(m.sum()) / (h * h), 4), "hair": info, "_mask": m, "_rgb": rgb}


# Limits, set on the stored step 2 sets (see the validation table in the work notes).
# Loose hair (deep in both side views, so not a ponytail or a braid): where the mass of the hair stops (half_end) in the back
# against the side views' average, as a share of the head-to-shoulder height. Backs judged consistent (5 sets of 2 women) stop
# 0.045 to 0.092 LOWER than the sides'; the two backs the owner reported stop 0.065 and 0.075 HIGHER, so the limit sits between.
# Also, the hair's width just above its end in the back over its depth in the side views: from behind loose hair is at least
# as wide as it is deep; a narrow column down the back is much less.
# Other hair (a ponytail, a braid): only a gross shortfall counts, by the lowest hair pixel: 0.25.
LOOSE_END_GAP = 0.03
MAX_END_GAP = 0.25
MIN_WIDTH_RATIO = 0.8
LOOSE_DEPTH = 0.06         # a side view's hair this deep (share of the figure's height, just above its end) is loose hair
SHOULDER_MIN = 0.14        # a head-to-shoulder height below this share of the figure is not a head and shoulders
A_RANGE = (118.0, 150.0)   # natural hair colours in 8-bit Lab: neither green nor blue nor violet
B_RANGE = (118.0, 172.0)
L_MAX = 135.0              # lighter than this the 'hair' may be a bald scalp (skin) or a pale hat: no verdict


def _usable(m: dict[str, Any] | None) -> bool:
    if not m or not m.get("ok"):
        return False
    l, a, b = m["hair"]["lab"]
    return l <= L_MAX and A_RANGE[0] <= a <= A_RANGE[1] and B_RANGE[0] <= b <= B_RANGE[1] and m["end"] < ZONE - 0.03 and m["area"] < 0.05


def compare(back: Path | str, front: Path | str, left: Path | str, right: Path | str) -> dict[str, Any] | None:
    """The back view's hair against the front and side views', or None when there is nothing to compare (see the module
    note). The result: ok, end_gap (positive: the back's hair ends higher than the sides'), width_ratio (None when the hair
    is not loose) and the numbers behind them."""
    f, lf, rt, bk = (measure(p) for p in (front, left, right, back))
    if not all(_usable(m) for m in (f, lf, rt, bk)):
        return None
    sh = f["shoulder"]
    if sh < SHOULDER_MIN or min(lf["end"], rt["end"]) < sh - 0.015:
        return None   # short hair or none: nothing hangs to the shoulders
    depth = (lf["band"] + rt["band"]) / 2
    loose = min(lf["band"], rt["band"]) >= LOOSE_DEPTH
    if loose:
        gap, limit = ((lf["half_end"] + rt["half_end"]) / 2 - bk["half_end"]) / sh, LOOSE_END_GAP
    else:
        gap, limit = ((lf["end"] + rt["end"]) / 2 - bk["end"]) / sh, MAX_END_GAP
    ratio = bk["band"] / depth if loose else None
    ok = gap <= limit and (ratio is None or ratio >= MIN_WIDTH_RATIO)
    return {"ok": ok, "end_gap": round(gap, 4), "limit": limit, "width_ratio": None if ratio is None else round(ratio, 4),
            "loose": loose, "back_end": bk["end"], "back_half_end": bk["half_end"], "shoulder": sh, "back_width": bk["band"],
            "side_depth": round(depth, 4)}
