"""Colour match of a built model's base colour to its picture (docs/TEXTURE-FIDELITY-PLAN.md, 24-step texture sampler).

TRELLIS's texture sampler at 24 steps gives a much richer texture than 12, but its overall tone can drift (the bake-off
knight came out about 7 percent brighter). After the build, a flat render of the model from the picture's side
(texfidelity) is compared with the picture, and ONE global correction is applied to the base colour atlas: a shift of
the mean in Lab (never a gain, which flattens contrast). It is capped so
no colour moves by more than MAX_MOVE_DE (about 8 dE), and only made when the silhouette fit is good (so the render and
the picture really show the same figure). Looks that change colour on purpose (crisp, palette) are not touched.

plan() and apply() are pure; match_base_colour() does the file work.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from . import face, texfidelity

MAX_MOVE_DE = 8.0     # the most any colour may be moved, in dE (Lab distance)
MIN_GAIN_DE = 1.0     # a correction that closes less than this is not worth making
STRENGTHS = (1.0, 0.75, 0.5, 0.25)  # tried in turn until the move fits under the cap
OVERLAP_MIN_PX = 2000


def _lab_to_linear(lab: np.ndarray) -> np.ndarray:
    fy = (lab[..., 0] + 16) / 116
    fx = fy + lab[..., 1] / 500
    fz = fy - lab[..., 2] / 200

    def inv(f):
        return np.where(f ** 3 > 0.008856, f ** 3, (f - 16 / 116) / 7.787)

    xyz = np.stack([inv(fx) * 0.9505, inv(fy), inv(fz) * 1.089], axis=-1)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    return xyz @ np.linalg.inv(m).T


def from_lab(lab: np.ndarray) -> np.ndarray:
    """CIE Lab (D65) -> sRGB 0..1 (the inverse of face.to_lab). A colour outside the sRGB gamut keeps its lightness and
    loses chroma until it fits (found by bisection), instead of being clipped channel by channel, which would flatten
    the texture's detail at the ends of the range."""
    lab = np.array(lab, dtype=np.float64)
    lab[..., 0] = np.clip(lab[..., 0], 0.0, 100.0)
    lin = _lab_to_linear(lab)
    bad = np.any((lin < -1e-4) | (lin > 1.0 + 1e-4), axis=-1)
    if bad.any():
        sub = lab[bad]
        lo, hi = np.zeros(len(sub)), np.ones(len(sub))
        for _ in range(12):
            mid = (lo + hi) / 2
            t = sub.copy()
            t[:, 1:] *= mid[:, None]
            l2 = _lab_to_linear(t)
            ok = np.all((l2 >= -1e-4) & (l2 <= 1.0 + 1e-4), axis=-1)
            lo = np.where(ok, mid, lo)
            hi = np.where(ok, hi, mid)
        sub[:, 1:] *= lo[:, None]
        lab[bad] = sub
        lin = _lab_to_linear(lab)
    c = np.clip(lin, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


TAPER = 10.0  # a lightness shift fades out over this many L units towards the end it moves towards


def shifted_lab(lab: np.ndarray, shift: np.ndarray, strength: float = 1.0) -> np.ndarray:
    """lab plus strength * shift. The lightness part is tapered near the end of the range it moves towards (a brightening
    fades out over the last TAPER units below 100, a darkening over the first TAPER above 0), so highlights and shadows
    are compressed smoothly instead of piling up at 0 or 100."""
    out = lab + strength * np.asarray(shift, dtype=np.float64) * np.array([0.0, 1.0, 1.0])
    dl = strength * float(shift[0])
    room = (100.0 - lab[..., 0]) if dl > 0 else lab[..., 0]
    out[..., 0] = lab[..., 0] + dl * np.clip(room / TAPER, 0.0, 1.0)
    return out


def apply(rgb: np.ndarray, corr: dict[str, Any] | None) -> np.ndarray:
    """The correction (from plan()) on an sRGB 0..1 image of any shape (..., 3). None or mode "none": unchanged."""
    if not corr or corr.get("mode") in (None, "none"):
        return rgb
    return from_lab(shifted_lab(face.to_lab(rgb), corr["shift"], float(corr.get("strength", 1.0))))


def _moved(render: np.ndarray, overlap: np.ndarray, corr: dict[str, Any]) -> float:
    """The mean dE the correction moves the render's colours by, over the overlap."""
    a = face.to_lab(render)[overlap]
    b = face.to_lab(apply(render, corr))[overlap]
    return float(np.sqrt(((a - b) ** 2).sum(axis=1)).mean())


def plan(render: np.ndarray, render_mask: np.ndarray, pic: np.ndarray, pic_mask: np.ndarray, cap: float = MAX_MOVE_DE) -> dict[str, Any]:
    """The Lab mean shift that brings the render's colour closest to the picture's, from images in one frame (S,S,3
    floats 0..1 and their masks). Only a shift (a constant added to every colour, which cannot blur or flatten detail);
    the strongest strength that moves colours by no more than `cap` dE is used.
    -> {mode: none|lab_shift, strength, shift, de_before, de_after, moved_de, reason}."""
    overlap = face.erode(render_mask & pic_mask, texfidelity.BORDER_PX)
    if overlap.sum() < OVERLAP_MIN_PX:
        return {"mode": "none", "reason": "overlap_too_small"}
    before = texfidelity.compare(render, render_mask, pic, pic_mask)["delta_e"]
    shift = (face.to_lab(pic)[overlap] - face.to_lab(render)[overlap]).mean(axis=0)
    best: dict[str, Any] = {"mode": "none", "de_before": before, "de_after": before, "reason": "no_gain"}
    for s in STRENGTHS:
        corr = {"mode": "lab_shift", "strength": s, "shift": [round(float(x), 3) for x in shift]}
        moved = _moved(render, overlap, corr)
        if moved > cap:
            continue
        after = texfidelity.compare(apply(render, corr), render_mask, pic, pic_mask)["delta_e"]
        if after is not None and before is not None and before - after >= MIN_GAIN_DE:
            best = {**corr, "de_before": before, "de_after": after, "moved_de": round(moved, 2), "reason": "applied"}
        break  # the strongest strength under the cap is the one used
    return best


def match_base_colour(glb: Path | str, picture: Path | str, base_png: Path | str, cap: float = MAX_MOVE_DE) -> dict[str, Any]:
    """Measure the model against its picture and, when the fit is good and a correction closes the gap, rewrite
    base_png with it. -> metrics {applied, mode, de_before, de_after, moved_de, iou, reason, ...}."""
    res = texfidelity.score(glb, picture, base_color=base_png, views=False, keep_images=True)
    if "error" in res:
        return {"applied": False, "reason": res["error"]}
    rec: dict[str, Any] = {"applied": False, "iou": res.get("silhouette_iou"), "yaw": res.get("yaw")}
    if not res.get("fit_ok"):
        return {**rec, "reason": "fit_too_loose"}
    im = res["_images"]
    corr = plan(im["render"], im["render_mask"], im["picture"], im["picture_mask"], cap)
    rec.update({k: corr.get(k) for k in ("mode", "strength", "shift", "de_before", "de_after", "moved_de")})
    rec["reason"] = corr.get("reason")
    if corr.get("mode") == "none":
        return rec
    tex = face.read_rgb(base_png)
    out = apply(tex, corr)
    Image.fromarray(face.to_uint8(out)).save(base_png)
    rec["applied"] = True
    return rec
