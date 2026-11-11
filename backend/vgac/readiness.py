"""Is this picture ready for 3D? Measurements on a concept picture's cutout (uploaded or generated), a material
photo, and the colours of any picture. Pillow and numpy only, no graphics card.

Concept readiness (each a quality.check, severity warn, sentences in guidance.CHECK_GUIDE):
  one_subject   connected areas of the cutout: one subject, no second figure or loose pieces of any size
  in_frame      a margin on every side, so nothing is cut off at the edge
  big_enough    the subject's height (its longest side for anything but a person) in pixels
  arms_away     people only: rows between the shoulders and the hips where an arm stands clear of the body,
                or a T-pose (arms straight out, far wider than the waist)
A failing check carries the redraw instruction (vars.redraw) its Fix sends to the concept refine.

Material photos: even_lighting (brightness change across the photo, from a fitted plane) and straight_on (how much
finer the detail is on one side than the other, the mark of a photo taken at an angle).

Colours: palette() is a median cut on a downsampled copy, ignoring near-transparent pixels and, when the border is
one plain colour, the background.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from . import quality

ANALYSE_SIDE = 256  # the cutout is measured at this size (longest side)
MIN_PART_SHARE = 0.03  # a separate area smaller than this share of the subject is a speck, not a second subject
MARGIN_MIN = 0.01  # at least 1 percent of the picture clear on every side
MIN_SUBJECT_PX = 400  # the 3D builder reads about 500 px; smaller loses detail
# The size the later steps are tuned for: every character that built well here had a subject 868 to 980 px tall (the
# painted pictures are 1024 x 1024; uploads 591 to 1832). Under this the face and small parts come out soft, and the
# check offers "Upscale it" (stages/concept.upscale) to UPSCALE_TARGET_PX.
DETAIL_SUBJECT_PX = 860
UPSCALE_TARGET_PX = 930
ARMS_GAP_SHARE = 0.2  # share of the shoulder-to-hip rows with an arm clear of the body
TPOSE_RATIO = 2.5  # arm span over waist width that counts as arms held out
EVEN_LIGHT_MAX = 0.25  # brightness change across a material photo, 0 to 1
DETAIL_RATIO_MAX = 2.0  # finer detail on one side than the other

HUMANOID_REDRAW = ("Redraw this same character with the same design, colours and outfit, standing in a relaxed A-pose, "
                   "whole body in frame, plain light grey background, even lighting")
SUBJECT_WORDS = {"quadruped": "animal", "multileg": "creature", "winged": "creature", "serpent": "creature",
                 "vehicle": "vehicle", "prop_rigid": "object", "prop_articulated": "object", "environment": "structure",
                 "foliage": "plant"}


def redraw_instruction(category: dict[str, Any] | None) -> str:
    """The fixed instruction "Redraw it ready for 3D" sends to the concept refine: the humanoid sentence, else the same
    lead-in with the category's own pose block from data/categories."""
    cat = category or {}
    cid = cat.get("id") or "humanoid"
    if cid == "humanoid":
        return HUMANOID_REDRAW
    noun = SUBJECT_WORDS.get(cid, "subject")
    pose = (cat.get("pose_block") or "").strip()
    lead = f"Redraw this same {noun} with the same design, colours and materials."
    return f"{lead} {pose}".strip() if pose else f"{lead} Whole subject in frame, plain light grey background, even lighting."


# --- the cutout mask ---------------------------------------------------------------------------------------------


def _mask(cutout: Path | str | Image.Image, side: int = ANALYSE_SIDE) -> tuple[np.ndarray, tuple[int, int]]:
    """The subject mask at `side` px on the longest side, and the full picture size."""
    im = cutout if isinstance(cutout, Image.Image) else Image.open(cutout)
    full = im.size
    a = im.getchannel("A") if im.mode in ("RGBA", "LA") else im.convert("RGBA").getchannel("A")
    scale = side / max(full)
    if scale < 1:
        a = a.resize((max(1, round(full[0] * scale)), max(1, round(full[1] * scale))), Image.Resampling.BOX)
    return np.asarray(a) >= 128, full


def components(mask: np.ndarray) -> list[int]:
    """Areas (in pixels) of the 8-connected parts of a boolean mask, largest first."""
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    areas: list[int] = []
    ys, xs = np.nonzero(mask)
    for y0, x0 in zip(ys.tolist(), xs.tolist()):
        if seen[y0, x0]:
            continue
        seen[y0, x0] = True
        q = deque([(y0, x0)])
        n = 0
        while q:
            y, x = q.popleft()
            n += 1
            for dy in (-1, 0, 1):
                yy = y + dy
                if yy < 0 or yy >= h:
                    continue
                for dx in (-1, 0, 1):
                    xx = x + dx
                    if 0 <= xx < w and mask[yy, xx] and not seen[yy, xx]:
                        seen[yy, xx] = True
                        q.append((yy, xx))
        areas.append(n)
    return sorted(areas, reverse=True)


def _runs(row: np.ndarray, min_len: int) -> int:
    """How many stretches of True a row has, ignoring ones shorter than min_len."""
    padded = np.concatenate(([False], row, [False]))
    d = np.diff(padded.astype(np.int8))
    starts, ends = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
    return int(np.sum((ends - starts) >= min_len))


def arms_measure(mask: np.ndarray) -> dict[str, float]:
    """For a standing person's mask: gap_share, the share of rows between the shoulders and the hips (22 to 55
    percent down from the top of the head) where something stands clear on both sides of the body (three or more
    stretches across the row), and span_ratio, the widest row near the shoulders over the median waist width."""
    rows = np.nonzero(mask.any(axis=1))[0]
    cols = np.nonzero(mask.any(axis=0))[0]
    if rows.size == 0:
        return {"gap_share": 0.0, "span_ratio": 0.0}
    sub = mask[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
    h, w = sub.shape
    min_len = max(1, round(w * 0.01))
    lo, hi = int(h * 0.22), max(int(h * 0.22) + 1, int(h * 0.55))
    band = sub[lo:hi]
    gap_rows = sum(1 for r in band if _runs(r, min_len) >= 3)
    widths = sub.sum(axis=1)
    shoulders = widths[int(h * 0.15):max(int(h * 0.15) + 1, int(h * 0.35))]
    waist = widths[int(h * 0.40):max(int(h * 0.40) + 1, int(h * 0.55))]
    span = float(shoulders.max()) / max(1.0, float(np.median(waist))) if shoulders.size and waist.size else 0.0
    return {"gap_share": round(gap_rows / max(1, band.shape[0]), 3), "span_ratio": round(span, 2)}


def concept_checks(cutout: Path | str | Image.Image, category: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Readiness checks for a concept picture's cutout (RGBA). Every check is severity warn; failing ones carry the
    redraw instruction their Fix sends."""
    cat = category or {}
    cid = cat.get("id") or "humanoid"
    redraw = {"redraw": redraw_instruction(cat)}
    mask, (fw, fh) = _mask(cutout)
    h, w = mask.shape
    areas = components(mask)
    total = sum(areas)
    if total == 0:
        return [quality.check("one_subject", False, 0, 1, vars={**redraw, "empty": True})]
    parts = [a for a in areas if a >= max(4, MIN_PART_SHARE * total)]
    out = [quality.check("one_subject", len(parts) == 1, len(parts), 1, vars=dict(redraw))]
    ys, xs = np.nonzero(mask)
    margins = {"top": ys.min() / h, "bottom": (h - 1 - ys.max()) / h, "left": xs.min() / w, "right": (w - 1 - xs.max()) / w}
    touching = [side for side, m in margins.items() if m < MARGIN_MIN]
    out.append(quality.check("in_frame", not touching, round(min(margins.values()), 3), MARGIN_MIN,
                             vars={**redraw, "sides": touching, "sides_text": _join(touching)}))
    sy, sx = fh / h, fw / w
    subject_h = (ys.max() - ys.min() + 1) * sy
    subject_w = (xs.max() - xs.min() + 1) * sx
    size_px = int(round(subject_h if cid == "humanoid" else max(subject_h, subject_w)))
    out.append(quality.check("big_enough", size_px >= MIN_SUBJECT_PX, size_px, MIN_SUBJECT_PX,
                             vars={**redraw, "measure": "tall" if cid == "humanoid" else "across"}))
    out.append(quality.check("detail_size", size_px >= DETAIL_SUBJECT_PX, size_px, DETAIL_SUBJECT_PX,
                             vars={"measure": "tall" if cid == "humanoid" else "across", "target": UPSCALE_TARGET_PX}))
    if cid == "humanoid":
        m = arms_measure(mask)
        ok = m["gap_share"] >= ARMS_GAP_SHARE or m["span_ratio"] >= TPOSE_RATIO
        out.append(quality.check("arms_away", ok, m["gap_share"], ARMS_GAP_SHARE, vars={**redraw, "span_ratio": m["span_ratio"]}))
    return out


def _join(items: list[str]) -> str:
    if not items:
        return ""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


# --- material photos -----------------------------------------------------------------------------------------------


def _grey(path: Path | str | Image.Image, side: int) -> np.ndarray:
    im = path if isinstance(path, Image.Image) else Image.open(path)
    g = im.convert("L").resize((side, side), Image.Resampling.BOX)
    return np.asarray(g, dtype=np.float64) / 255.0


def lighting_spread(img: Path | str | Image.Image) -> float:
    """How much the brightness changes from one side of the photo to the other (0 to 1), from a plane fitted to a
    64 x 64 copy, so texture detail does not count, only the overall slope of the light."""
    g = _grey(img, 64)
    yy, xx = np.mgrid[0:64, 0:64]
    x = (xx.ravel() / 63.0) - 0.5
    y = (yy.ravel() / 63.0) - 0.5
    A = np.stack([np.ones_like(x), x, y], axis=1)
    coef, *_ = np.linalg.lstsq(A, g.ravel(), rcond=None)
    corners = [coef[0] + coef[1] * cx + coef[2] * cy for cx in (-0.5, 0.5) for cy in (-0.5, 0.5)]
    return float(max(corners) - min(corners))


def detail_ratio(img: Path | str | Image.Image) -> float:
    """How much finer the detail is in one third of the photo than in the opposite third (top against bottom, left
    against right), from the local contrast of a 128 x 128 copy. A photo taken at an angle has much finer detail on
    its far side."""
    g = _grey(img, 128)
    lap = np.abs(4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:])
    n = lap.shape[0] // 3
    pairs = [(lap[:n].mean(), lap[-n:].mean()), (lap[:, :n].mean(), lap[:, -n:].mean())]
    worst = 1.0
    for a, b in pairs:
        lo = max(min(a, b), 1e-4)
        worst = max(worst, max(a, b) / lo)
    return float(worst)


def photo_checks(img: Path | str | Image.Image) -> list[dict[str, Any]]:
    """The material photo checks (warn): even lighting and taken straight on."""
    spread = lighting_spread(img)
    ratio = detail_ratio(img)
    return [quality.check("even_lighting", spread <= EVEN_LIGHT_MAX, round(spread, 3), EVEN_LIGHT_MAX),
            quality.check("straight_on", ratio <= DETAIL_RATIO_MAX, round(ratio, 2), DETAIL_RATIO_MAX)]


# --- colours ---------------------------------------------------------------------------------------------------------

PALETTE_SIZES = (8, 16, 32)
PALETTE_SIDE = 160
BACKGROUND_TOLERANCE = 40  # sum of channel differences that still counts as the background colour
PLAIN_BORDER_SHARE = 0.8


def _hex(rgb: Any) -> str:
    r, g, b = (int(round(float(c))) for c in rgb[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def palette(img: Image.Image, colors: int = 8, ignore_background: bool = True) -> dict[str, Any]:
    """The main colours of a picture, most used first: {palette: [hex], swatches: [{hex, share}], background: hex or
    None, pixels}. share is the part of the counted pixels (0 to 1) each colour covers. Near-transparent pixels never
    count; with ignore_background, a plain border colour (80 percent of the border within a small difference) and
    everything close to it does not count either."""
    if colors not in PALETTE_SIZES:
        raise ValueError(f"colors must be one of {list(PALETTE_SIZES)}")
    im = img.convert("RGBA")
    im.thumbnail((PALETTE_SIDE, PALETTE_SIDE), Image.Resampling.BOX)
    arr = np.asarray(im)
    rgb = arr[..., :3].reshape(-1, 3).astype(np.int32)
    alpha = arr[..., 3].reshape(-1)
    keep = alpha >= 128
    background = None
    if ignore_background:
        edge = np.concatenate([arr[0], arr[-1], arr[:, 0], arr[:, -1]])
        edge = edge[edge[:, 3] >= 128][:, :3].astype(np.int32)
        if len(edge) >= 8:
            med = np.median(edge, axis=0)
            if (np.abs(edge - med).sum(axis=1) <= BACKGROUND_TOLERANCE).mean() >= PLAIN_BORDER_SHARE:
                not_bg = np.abs(rgb - med).sum(axis=1) > BACKGROUND_TOLERANCE
                if (keep & not_bg).any():
                    background = _hex(med)
                    keep = keep & not_bg
    px = rgb[keep]
    if px.size == 0:
        raise ValueError("The picture has no visible colours to take.")
    strip = Image.fromarray(px.reshape(1, -1, 3).astype(np.uint8), "RGB")
    q = strip.quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    pal = q.getpalette() or []
    counts = np.bincount(np.asarray(q).ravel(), minlength=colors)
    merged: dict[str, int] = {}
    for idx in np.argsort(-counts, kind="stable"):
        n = int(counts[idx])
        if n == 0 or idx * 3 + 2 >= len(pal):
            continue
        hx = _hex(pal[idx * 3: idx * 3 + 3])
        merged[hx] = merged.get(hx, 0) + n
    total = sum(merged.values()) or 1
    swatches = [{"hex": hx, "share": round(n / total, 4)} for hx, n in sorted(merged.items(), key=lambda kv: -kv[1])]
    return {"palette": [s["hex"] for s in swatches], "swatches": swatches, "background": background, "pixels": int(px.shape[0])}
