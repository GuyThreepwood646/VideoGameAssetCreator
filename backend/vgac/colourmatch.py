"""Colour match of a built model's base colour to its picture (docs/TEXTURE-FIDELITY-PLAN.md, 24-step texture sampler).

TRELLIS's texture sampler at 24 steps gives a much richer texture than 12, but its overall tone can drift (the bake-off
knight came out about 7 percent brighter). After the build, a flat render of the model from the picture's side
(texfidelity) is compared with the picture, and ONE global correction is applied to the base colour atlas: a shift of
the mean in Lab (never a gain, which flattens contrast). It is capped so
no colour moves by more than MAX_MOVE_DE (about 8 dE), and only made when the silhouette fit is good (so the render and
the picture really show the same figure).

Lightness comes first and on its own (bakeoff/render-tone/PLAN.md): the builder's tone was found 10 to 17 L off the
picture on some builds while the shared 8 dE cap and the 0.80 fit gate left them alone. lightness_plan() compares the
whole-silhouette mean L* of a flat render at the build view's own turn (the turn nearest it whose outline fits best,
searched only around that view and its mirror) with the mean L* over the picture's cut-out, borders left out on both
sides. No pixel fit is needed, so it runs down to an outline IoU of L_IOU_MIN, and it has its own cap, L_CAP. Then
plan() corrects the remaining colour only (lightness term off), as before.

lightness_plan(), plan() and apply() are pure; match_base_colour() does the file work.
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

L_CAP = 15.0          # the most the lightness stage moves the mean, in L* units
L_MIN_GAIN = 1.0      # a lightness gap smaller than this is left alone
L_IOU_MIN = 0.5       # below this outline IoU the render is not taken to show the picture's figure
L_SIZE = 512          # render size for the lightness stage
L_MASK_MIN_PX = 1500  # each side's mask, after the border is left out, at L_SIZE
L_SEARCH = (-30, -15, 0, 15, 30)  # turns tried around the view (and its mirror)
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


def plan(render: np.ndarray, render_mask: np.ndarray, pic: np.ndarray, pic_mask: np.ndarray, cap: float = MAX_MOVE_DE,
         lightness: bool = True) -> dict[str, Any]:
    """The Lab mean shift that brings the render's colour closest to the picture's, from images in one frame (S,S,3
    floats 0..1 and their masks). Only a shift (a constant added to every colour, which cannot blur or flatten detail);
    the strongest strength that moves colours by no more than `cap` dE is used. With lightness False the shift leaves
    L* alone (the lightness stage, lightness_plan(), has already set it).
    -> {mode: none|lab_shift, strength, shift, de_before, de_after, moved_de, reason}."""
    overlap = face.erode(render_mask & pic_mask, texfidelity.BORDER_PX)
    if overlap.sum() < OVERLAP_MIN_PX:
        return {"mode": "none", "reason": "overlap_too_small"}
    before = texfidelity.compare(render, render_mask, pic, pic_mask)["delta_e"]
    shift = (face.to_lab(pic)[overlap] - face.to_lab(render)[overlap]).mean(axis=0)
    if not lightness:
        shift[0] = 0.0
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


def _turn_gap(a: float, b: float) -> float:
    d = (a - b) % 360.0
    return min(d, 360.0 - d)


def lightness_plan(mesh: dict[str, np.ndarray], tex: np.ndarray, pic: np.ndarray, pic_mask: np.ndarray,
                   view: str | None = None, cap: float = L_CAP) -> dict[str, Any]:
    """The lightness stage: the L* shift that brings the mean of the model's flat render, at the build view's turn, to
    the mean over the picture's cut-out. Means are over each side's own mask (the picture's background and the render's
    empty pixels never count), with texfidelity.BORDER_PX left out at each edge. The turn is the best outline fit among
    L_SEARCH around the view's turn and around its mirror (ties to the view's own turn), so it is never far from the
    side the picture shows. -> {mode: none|l_shift, shift, strength, gap, capped, iou, yaw, render_px, picture_px,
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
    return {**out, "mode": "l_shift", "strength": 1.0, "shift": [round(shift, 3), 0.0, 0.0],
            "capped": abs(gap) > cap, "reason": "applied"}


def match_base_colour(glb: Path | str, picture: Path | str, base_png: Path | str, cap: float = MAX_MOVE_DE,
                      view: str | None = None) -> dict[str, Any]:
    """Measure the model against its picture and correct base_png: first its lightness (lightness_plan(), at the build
    view `view`'s turn), then, when the outline fit is good and a correction closes the gap, its colour without
    lightness (plan()). -> metrics {applied, lightness: {...}, mode, de_before, de_after, moved_de, iou, reason, ...}."""
    rec: dict[str, Any] = {"applied": False}
    mesh = face.load_mesh(glb)
    if mesh is not None:
        tex = face.read_rgb(base_png)
        pic, pic_mask = texfidelity.load_picture(picture)
        lp = lightness_plan(mesh, tex, pic, pic_mask, view)
        rec["lightness"] = lp
        if lp.get("mode") == "l_shift":
            Image.fromarray(face.to_uint8(apply(tex, lp))).save(base_png)
            rec["applied"] = True
    res = texfidelity.score(glb, picture, base_color=base_png, views=False, keep_images=True)
    if "error" in res:
        return {**rec, "reason": res["error"]}
    rec.update({"iou": res.get("silhouette_iou"), "yaw": res.get("yaw")})
    if not res.get("fit_ok"):
        return {**rec, "reason": "fit_too_loose"}
    im = res["_images"]
    corr = plan(im["render"], im["render_mask"], im["picture"], im["picture_mask"], cap, lightness=False)
    rec.update({k: corr.get(k) for k in ("mode", "strength", "shift", "de_before", "de_after", "moved_de")})
    rec["reason"] = corr.get("reason")
    if corr.get("mode") == "none":
        return rec
    tex = face.read_rgb(base_png)
    Image.fromarray(face.to_uint8(apply(tex, corr))).save(base_png)
    rec["applied"] = True
    return rec
