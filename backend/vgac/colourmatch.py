"""Lightness match of a built model's base colour to its picture (bakeoff/render-tone/PLAN.md).

TRELLIS.2's base colour comes out up to about 17 L* lighter or darker than the picture it was built from (the tone is
set by the builder; the clean-up and the export keep it). After the build, lightness_plan() compares the whole-silhouette
mean L* of a flat render at the build view's own turn (the turn nearest it whose outline fits best, searched only around
that view and its mirror) with the mean L* over the picture's cut-out, borders left out on both sides. No pixel fit is
needed, so it runs down to an outline IoU of L_IOU_MIN; the move is capped at L_CAP and is a shift of L* only (hue and
chroma kept, so a neutral grey stays neutral), tapered near black and white so detail is not flattened.

When the build-view picture's own lightness differs from the step-1 picture's by more than DRIFT_MAX (the step-2 redraw
changed the tone), nothing is moved ("step2_tone_drift"): matching the build view would carry the redraw's drift into
the texture and move the model away from step 1.

The corrected base colour is written to base_color.png AND into the GLB's own base colour image, because the clean-up
re-bakes from the GLB (a correction in the PNG alone never reached a shipped model).

lightness_plan() and apply() are pure; match_base_colour() does the file work.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from . import face, imaging, texfidelity

L_CAP = 15.0          # the most the lightness stage moves the mean, in L* units
L_MIN_GAIN = 1.0      # a lightness gap smaller than this is left alone
L_IOU_MIN = 0.5       # below this outline IoU the render is not taken to show the picture's figure
L_SIZE = 512          # render size for the lightness stage
L_MASK_MIN_PX = 1500  # each side's mask, after the border is left out, at L_SIZE
L_SEARCH = (-30, -15, 0, 15, 30)  # turns tried around the view (and its mirror)
DRIFT_MAX = 4.0       # build view against step-1 picture, mean L* over each cut-out: past this the lightness stage is skipped
# The turn the build view's picture shows, in face.Camera yaw (as bakeoff/scorecard/sc_measure.INTENDED).
VIEW_TURN = {"concept": 0.0, "front": 0.0, "quarter": 45.0, "quarter2": -45.0, "left": 90.0, "right": -90.0, "back": 180.0}


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


def shifted_lab(lab: np.ndarray, dl: float) -> np.ndarray:
    """lab with dl added to L* only (a* and b* kept). The shift is tapered near the end of the range it moves towards
    (a brightening fades out over the last TAPER units below 100, a darkening over the first TAPER above 0), so
    highlights and shadows are compressed smoothly instead of piling up at 0 or 100."""
    out = np.array(lab, dtype=np.float64)
    room = (100.0 - out[..., 0]) if dl > 0 else out[..., 0]
    out[..., 0] = out[..., 0] + dl * np.clip(room / TAPER, 0.0, 1.0)
    return out


def apply(rgb: np.ndarray, corr: dict[str, Any] | None) -> np.ndarray:
    """The lightness move (from lightness_plan(), its shift[0] in L*) on an sRGB 0..1 image of any shape (..., 3).
    None or mode "none": unchanged."""
    if not corr or corr.get("mode") in (None, "none"):
        return rgb
    return from_lab(shifted_lab(face.to_lab(rgb), float(corr["shift"][0])))


def _turn_gap(a: float, b: float) -> float:
    d = (a - b) % 360.0
    return min(d, 360.0 - d)


def cutout_lightness(pic: np.ndarray, pic_mask: np.ndarray) -> float | None:
    """Mean L* over a picture's cut-out with the border left out (scaled from texfidelity.BORDER_PX at L_SIZE), the
    mask rule lightness_plan() uses. None for an empty cut-out."""
    border = max(1, int(round(texfidelity.BORDER_PX * max(pic.shape[:2]) / L_SIZE)))
    m = face.erode(pic_mask, border)
    if m.sum() < 100:
        return None
    return float(face.to_lab(pic)[m][:, 0].mean())


def step2_drift(build_picture: Path | str, step1_picture: Path | str | None) -> float | None:
    """Build-view picture's mean L* minus the step-1 picture's (positive: the redraw came out lighter). None when there
    is no step-1 picture, it is the build picture itself, or either cut-out is empty."""
    if step1_picture is None or Path(step1_picture).resolve() == Path(build_picture).resolve():
        return None
    a = cutout_lightness(*texfidelity.load_picture(build_picture))
    b = cutout_lightness(*texfidelity.load_picture(step1_picture))
    return None if a is None or b is None else round(a - b, 2)


def lightness_plan(mesh: dict[str, np.ndarray], tex: np.ndarray, pic: np.ndarray, pic_mask: np.ndarray,
                   view: str | None = None, cap: float = L_CAP) -> dict[str, Any]:
    """The lightness stage: the L* shift that brings the mean of the model's flat render, at the build view's turn, to
    the mean over the picture's cut-out. Means are over each side's own mask (the picture's background and the render's
    empty pixels never count), with texfidelity.BORDER_PX left out at each edge. The turn is the best outline fit among
    L_SEARCH around the view's turn and around its mirror (ties to the view's own turn), so it is never far from the
    side the picture shows. -> {mode: none|l_shift, shift, gap, capped, iou, yaw, render_px, picture_px,
    render_L, picture_L, reason}."""
    base = VIEW_TURN.get(view or "front", 0.0)
    ys, xs = np.nonzero(pic_mask)
    if len(ys) < 100:
        return {"mode": "none", "reason": "no_cutout"}
    extent = float(max(np.ptp(xs), np.ptp(ys)) + 1) * L_SIZE / max(pic.shape[:2])
    pm_sq = texfidelity._pad_square(pic_mask)
    pm_work = np.asarray(Image.fromarray((pm_sq * 255).astype(np.uint8)).resize((L_SIZE, L_SIZE), Image.BILINEAR)) >= 128
    turns = sorted({round((t + d) % 360.0, 3) for t in (base, -base) for d in L_SEARCH})
    best: dict[str, Any] | None = None
    for y in turns:
        cam = texfidelity._frame_camera(mesh, y, extent, L_SIZE)
        r = face.render_view(mesh, tex, cam, background=0.0)
        iou = texfidelity.fit_silhouette(pm_work, r["mask"])["iou"]
        key = (round(iou, 2), -_turn_gap(y, base))
        if best is None or key > best["key"]:
            best = {"key": key, "yaw": y, "iou": iou, "render": r}
    assert best is not None
    rm = face.erode(best["render"]["mask"], texfidelity.BORDER_PX)
    border = max(1, int(round(texfidelity.BORDER_PX * max(pic.shape[:2]) / L_SIZE)))
    pm = face.erode(pic_mask, border)
    out: dict[str, Any] = {"yaw": best["yaw"], "iou": round(float(best["iou"]), 4),
                           "render_px": int(rm.sum()), "picture_px": int(pm.sum())}
    if out["iou"] < L_IOU_MIN:
        return {**out, "mode": "none", "reason": "fit_too_loose"}
    if rm.sum() < L_MASK_MIN_PX or pm.sum() < L_MASK_MIN_PX * (max(pic.shape[:2]) / L_SIZE) ** 2:
        return {**out, "mode": "none", "reason": "mask_too_small"}
    img = face.to_uint8(best["render"]["image"]).astype(np.float64) / 255.0
    rl = float(face.to_lab(img)[rm][:, 0].mean())
    pl = float(face.to_lab(pic)[pm][:, 0].mean())
    gap = pl - rl
    out.update({"render_L": round(rl, 2), "picture_L": round(pl, 2), "gap": round(gap, 2)})
    if abs(gap) < L_MIN_GAIN:
        return {**out, "mode": "none", "reason": "no_gain"}
    shift = float(np.clip(gap, -cap, cap))
    return {**out, "mode": "l_shift", "shift": [round(shift, 3), 0.0, 0.0],
            "capped": abs(gap) > cap, "reason": "applied"}


def match_base_colour(glb: Path | str, picture: Path | str, base_png: Path | str, view: str | None = None,
                      step1: Path | str | None = None) -> dict[str, Any]:
    """Measure the model's lightness against its picture at the build view `view`'s turn and, when it is off, shift
    base_png and the GLB's own base colour image by the same move. Nothing is moved when the build view's tone drifted
    from the step-1 picture `step1` by more than DRIFT_MAX. -> metrics {applied, lightness: {...}, reason}."""
    rec: dict[str, Any] = {"applied": False}
    drift = step2_drift(picture, step1)
    if drift is not None and abs(drift) > DRIFT_MAX:
        rec["lightness"] = {"mode": "none", "reason": "step2_tone_drift", "drift": drift}
        rec["reason"] = "step2_tone_drift"
        return rec
    mesh = face.load_mesh(glb)
    if mesh is None:
        return {**rec, "reason": "no_texture_layout"}
    tex = face.read_rgb(base_png)
    pic, pic_mask = texfidelity.load_picture(picture)
    lp = lightness_plan(mesh, tex, pic, pic_mask, view)
    if drift is not None:
        lp["drift"] = drift
    rec["lightness"] = lp
    rec["reason"] = lp.get("reason")
    if lp.get("mode") != "l_shift":
        return rec
    Image.fromarray(face.to_uint8(apply(tex, lp))).save(base_png)
    imaging.glb_replace_images(glb, base_color=base_png)
    rec["applied"] = True
    return rec
