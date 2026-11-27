"""Lightness match of a built model's base colour to its picture (bakeoff/render-tone/PLAN.md).

TRELLIS.2's base colour comes out up to about 17 L* lighter or darker than the picture it was built from (the tone is
set by the builder; the clean-up and the export keep it). After the build, lightness_plan() compares the whole-silhouette
mean L* of a flat render at the build view's own turn (the turn nearest it whose outline fits best, searched only around
that view and its mirror) with the mean L* over the picture's cut-out, borders left out on both sides. No pixel fit is
needed, so it runs down to an outline IoU of L_IOU_MIN; the move is capped at L_CAP and is a shift of L* only (hue and
chroma kept, so a neutral grey stays neutral), tapered near black and white so detail is not flattened.

When the build-view picture's mean lightness differs from the step-1 picture's by more than DRIFT_MAX, the target
becomes the step-1 picture itself: its mean L* over its own cut-out, measured at the front turn that fits step 1. The
two pictures can show different sides, so the gap (called drift here) is often the shading of the side the build view
turned to, not a tone change by the step-2 redraw. The same global L*-only shift is applied, so the model lands on
step 1's lightness, not the build view's.

The corrected base colour is written to base_color.png AND into the GLB's own base colour image, because the clean-up
re-bakes from the GLB (a correction in the PNG alone never reached a shipped model).

lightness_plan() and apply() are pure; match_base_colour() does the file work.

Roof lift (buildings only): a building's roof often bakes far darker than the picture's roof, which one global shift
cannot fix without moving the walls. After the global shift, roof_lift_plan() finds the up-facing UV islands that sit high
on the model, measures the picture's roof (the picture's pixels under the model's own roof pixels in a tilted render
fitted to the picture's outline) and lifts the islands that are clearly darker than it by one capped L* shift, on those
islands' up-facing texels only. Other routes, walls and a roof that already matches are untouched.

Roof colour move (buildings only): the roof's hue can be wrong too. roof_colour_plan() looks for a view whose outline and
inner layout (edges and colours) both agree with the picture and that sees the roof's top. When the roof's hue there
differs enough from the picture's, the roof's same-coloured up-facing texels move in L*, a* and b* in place of the lift.
With no such view the lift alone applies.
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
L_COARSE = tuple(float(t) for t in range(0, 360, 45))  # coarse turns: a picture may show any side
FINE_STEP = 15.0      # the fine turns are this far either side of each of the best coarse turns
FINE_TOP = 2          # how many coarse turns are refined
TURN_IOU_NEAR = 0.02  # turns whose outline fit is within this of the best are told apart by the picture's colours
DRIFT_MAX = 4.0       # build view against step-1 picture, gap in mean L* over each cut-out: past this the target is step 1
C_SCALE = (0.75, 1.4)  # the chroma stage scales a* and b* by the picture's mean chroma over the render's, within this range
C_ROT_MAX = 15.0       # and turns their hue by the gap between the two mean hue directions, at most this many degrees
C_MIN_SCALE = 0.05     # a chroma gap under this share and a hue gap under C_MIN_ROT degrees are left alone
C_MIN_ROT = 2.0
ROOF_ROUTE = "buildings"  # the only route the roof lift runs for
ROOF_UP_MIN = 0.35        # an island whose UV-area-weighted face normal points up by at least this much is a roof
ROOF_TEXEL_UP = 0.2       # inside a roof island only texels of faces that point up by at least this much are lifted
ROOF_MARGIN = 8.0         # an island is lifted only when its mean L* is this far below the picture's roof
ROOF_CAP = 25.0           # the most the roof lift moves, in L* units
ROOF_MIN_HEIGHT = 0.6     # a roof island's up-facing surface averages at least this share of the model's height (awnings, ledges, ground sit lower)
ROOF_TILTS = (20.0,)  # the roof is looked at from above: the render is tilted by this much (a render with no tilt sees a roof edge-on)
ROOF_SEARCH_SIZE = 256    # render size while the roof's turn and tilt are searched
ROOF_MIN_PX = 200         # roof pixels in the render, and picture pixels under them, needed to trust the measure
ROOF_COLOUR_TILTS = (0.0, 10.0, 20.0)  # tilts tried for the roof colour move (a level picture sees the roof edge-on)
ROOF_COLOUR_IOU = 0.9     # the colour move needs a view whose outline overlaps the picture's at least this much
ROOF_COLOUR_LAYOUT = 0.3  # and whose edges and colours inside the outline agree with the picture's at least this much
ROOF_COLOUR_MIN_AB = 10.0  # a roof whose (a*, b*) lies this close to the picture's roof keeps its colour (the lift handles lightness)
ROOF_AB_CAP = 18.0        # the most the roof colour move shifts (a*, b*), as one length
ROOF_CROWN_BAND = 0.2     # the roof's top: its faces within this share of the model's height below its highest face
ROOF_CROWN_SEEN = 0.2     # the colour move needs a view that sees at least this share of the top's area (an awning or a
#                           ledge sampled while the roof itself is edge-on says nothing about the roof)
ROOF_SAME_AB = 12.0       # texels this close in (a*, b*) to the sampled roof take the whole move, fading to none at twice
ROOF_FLOOR_PCT = 5.0      # the colour move reaches up-facing faces down to this percentile of the roof faces' height
ROOF_EDGE_BLUR = 2        # blur (pixels at ROOF_SEARCH_SIZE) of the edge strength the two layouts are compared by
ROOF_LAYOUT_BORDER = 4    # pixels left out along the outline when the layouts are compared
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
    """Build-view picture's mean L* minus the step-1 picture's (positive: the build view is lighter). The cut-outs may
    show different sides, so a gap is often shading, not a tone change by the redraw. None when there is no step-1
    picture, it is the build picture itself, or either cut-out is empty."""
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
    L_COARSE (every 45 degrees, all round, then 15 degrees either side of the best two: a picture may show a side other than the view's own turn). Turns
    whose outline fit is within TURN_IOU_NEAR of the best are told apart by the picture's colours (fidelity delta E). -> {mode: none|l_shift, shift, gap, capped, iou, yaw, render_px, picture_px,
    render_L, picture_L, reason}."""
    base = VIEW_TURN.get(view or "front", 0.0)
    ys, xs = np.nonzero(pic_mask)
    if len(ys) < 100:
        return {"mode": "none", "reason": "no_cutout"}
    extent = float(max(np.ptp(xs), np.ptp(ys)) + 1) * L_SIZE / max(pic.shape[:2])
    pm_sq = texfidelity._pad_square(pic_mask)
    pm_work = np.asarray(Image.fromarray((pm_sq * 255).astype(np.uint8)).resize((L_SIZE, L_SIZE), Image.BILINEAR)) >= 128
    cands: list[dict[str, Any]] = []
    seen_turns: set[float] = set()

    def probe(y: float) -> None:
        y = round(y % 360.0, 3)
        if y in seen_turns:
            return
        seen_turns.add(y)
        cam = texfidelity._frame_camera(mesh, y, extent, L_SIZE)
        r = face.render_view(mesh, tex, cam, background=0.0)
        iou = texfidelity.fit_silhouette(pm_work, r["mask"])["iou"]
        cands.append({"key": (round(iou, 2), -_turn_gap(y, base)), "yaw": y, "iou": iou, "render": r})

    # coarse to fine: every COARSE_STEP turns, then FINE_STEP either side of the best FINE_TOP coarse turns
    for y in L_COARSE:
        probe(y)
    for c in sorted(cands, key=lambda c: c["iou"], reverse=True)[:FINE_TOP]:
        for d in (-FINE_STEP, FINE_STEP):
            probe(c["yaw"] + d)
    top = max(c["iou"] for c in cands)
    near = sorted((c for c in cands if c["iou"] >= top - TURN_IOU_NEAR), key=lambda c: c["key"], reverse=True)
    best = near[0]
    if len(near) > 1:
        # Turns whose outlines fit alike (a box, a symmetric prop): the picture's own colours pick the turn, as the
        # fidelity check does, so the lightness is measured on the side the picture shows.
        scored = []
        for c in near[:FINE_TOP + 2]:
            de = texfidelity._front_candidate(mesh, tex, pic, pic_mask, c["yaw"], L_SIZE, False).get("delta_e")
            if de is not None:
                scored.append((de, c))
        if scored:
            best = min(scored, key=lambda x: x[0])[1]
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


def apply_chroma(rgb: np.ndarray, corr: dict[str, Any] | None) -> np.ndarray:
    """The chroma move (from chroma_plan()) on an sRGB 0..1 image (..., 3): a* and b* scaled by corr["scale"] and turned
    by corr["rotate"] degrees, L* untouched. A neutral texel stays neutral. None or mode "none": unchanged."""
    if not corr or corr.get("mode") in (None, "none"):
        return rgb
    lab = face.to_lab(rgb)
    ab = (lab[..., 1] + 1j * lab[..., 2]) * float(corr["scale"]) * np.exp(1j * np.radians(float(corr["rotate"])))
    lab = np.array(lab, dtype=np.float64)
    lab[..., 1], lab[..., 2] = ab.real, ab.imag
    return from_lab(lab)


def chroma_plan(mesh: dict[str, np.ndarray], tex: np.ndarray, pic: np.ndarray, pic_mask: np.ndarray,
                yaw: float, iou: float) -> dict[str, Any]:
    """The chroma and hue stage, run after the lightness stage on its texture `tex`. The model's flat render at the
    lightness stage's turn `yaw` and the picture's cut-out are each reduced to their mean a*b* vector (its length is the
    mean chroma, its direction the mean hue, so a colour cast survives the mean and a neutral picture stays neutral), with
    the same masks as the lightness stage. The move scales chroma by the picture's over the render's (C_SCALE) and
    turns the hue by the angle between the two (C_ROT_MAX). -> {mode: none|chroma, scale, rotate, render_chroma,
    picture_chroma, reason}."""
    if iou < L_IOU_MIN:
        return {"mode": "none", "reason": "fit_too_loose"}
    ys, xs = np.nonzero(pic_mask)
    if len(ys) < 100:
        return {"mode": "none", "reason": "no_cutout"}
    extent = float(max(np.ptp(xs), np.ptp(ys)) + 1) * L_SIZE / max(pic.shape[:2])
    r = face.render_view(mesh, tex, texfidelity._frame_camera(mesh, yaw, extent, L_SIZE), background=0.0)
    border = max(1, int(round(texfidelity.BORDER_PX * max(pic.shape[:2]) / L_SIZE)))
    rm, pm = face.erode(r["mask"], texfidelity.BORDER_PX), face.erode(pic_mask, border)
    if rm.sum() < L_MASK_MIN_PX or pm.sum() < L_MASK_MIN_PX * (max(pic.shape[:2]) / L_SIZE) ** 2:
        return {"mode": "none", "reason": "mask_too_small"}
    rl = face.to_lab(face.to_uint8(r["image"]).astype(np.float64) / 255.0)[rm]
    pl = face.to_lab(pic)[pm]
    rv, pv = complex((rl[:, 1] + 1j * rl[:, 2]).mean()), complex((pl[:, 1] + 1j * pl[:, 2]).mean())
    rc, pc = float(np.mean(np.hypot(rl[:, 1], rl[:, 2]))), float(np.mean(np.hypot(pl[:, 1], pl[:, 2])))
    out: dict[str, Any] = {"render_chroma": round(rc, 2), "picture_chroma": round(pc, 2)}
    if rc < 1.0 or pc < 1.0 or abs(rv) < 0.5 or abs(pv) < 0.5:
        return {**out, "mode": "none", "reason": "neutral"}
    scale = float(np.clip(pc / rc, *C_SCALE))
    rot = float(np.clip(np.degrees(np.angle(pv / rv)), -C_ROT_MAX, C_ROT_MAX))
    out.update({"scale": round(scale, 3), "rotate": round(rot, 2), "hue_gap": round(float(np.degrees(np.angle(pv / rv))), 2)})
    if abs(scale - 1.0) < C_MIN_SCALE and abs(rot) < C_MIN_ROT:
        return {**out, "mode": "none", "reason": "no_gain"}
    return {**out, "mode": "chroma", "reason": "applied"}


def roof_islands(uvmesh: dict[str, np.ndarray], mesh: dict[str, np.ndarray] | None = None) -> list[int]:
    """Islands that face up: the UV-area-weighted mean of their faces' up component is at least ROOF_UP_MIN. With `mesh`
    (the same GLB's geometry) an island is also a roof only when its up-facing area averages at least ROOF_MIN_HEIGHT of
    the model's height, so an awning, a ledge or the ground slab is not taken for the roof."""
    uv, f = uvmesh["uv"], uvmesh["faces"]
    a, b, c = uv[f[:, 0]], uv[f[:, 1]], uv[f[:, 2]]
    area = np.abs((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1])) / 2
    isl = uvmesh["island"]
    h = None
    if mesh is not None:
        lo, hi = float(mesh["verts"][:, imaging.UP_AXIS].min()), float(mesh["verts"][:, imaging.UP_AXIS].max())
        tri = mesh["verts"][f]
        h = (tri[:, :, imaging.UP_AXIS].mean(axis=1) - lo) / max(hi - lo, 1e-12)
        sa = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)  # surface area
    out = []
    for i in np.unique(isl):
        m = isl == i
        w = area[m].sum()
        if w > 0 and float((uvmesh["up"][m] * area[m]).sum() / w) >= ROOF_UP_MIN:
            if h is not None:
                u = m & (uvmesh["up"] >= ROOF_TEXEL_UP)
                if sa[u].sum() <= 0 or float((h[u] * sa[u]).sum() / sa[u].sum()) < ROOF_MIN_HEIGHT:
                    continue
            out.append(int(i))
    return out


def _roof_view(mesh: dict[str, np.ndarray], uvmesh: dict[str, np.ndarray], roofs: list[int], tex: np.ndarray,
               pic_mask: np.ndarray, extent: float, yaw: float, tilt: float, size: int) -> dict[str, Any]:
    """The flat render at (yaw, tilt), its roof pixels and the outline fit of the picture's cut-out to it."""
    r = face.render_view(mesh, tex, texfidelity._frame_camera(mesh, yaw, extent * size / L_SIZE, size, tilt), background=0.0)
    face_roof = np.isin(uvmesh["island"], roofs) & (uvmesh["up"] >= ROOF_TEXEL_UP)
    roof_px = r["mask"] & face_roof[np.maximum(r["raster"]["tri"], 0)]
    iou = texfidelity.fit_silhouette(texfidelity._pad_to(pic_mask, size), r["mask"])["iou"]
    return {"render": r, "roof_px": roof_px, "iou": iou, "yaw": yaw, "tilt": tilt}


def roof_pose(mesh: dict[str, np.ndarray], uvmesh: dict[str, np.ndarray], roofs: list[int], tex: np.ndarray,
              pic_mask: np.ndarray, extent: float, yaw0: float) -> tuple[float, float]:
    """The (turn, tilt) at which the model's outline best fits the picture's cut-out, from every 45 degrees of turn and
    the ROOF_TILTS (a render with no tilt sees a roof edge-on; the pictures look down on the building). Turns that fit
    alike keep the one nearest yaw0, the turn the lightness stage found."""
    best = None
    for tilt in ROOF_TILTS:
        for yaw in L_COARSE:
            v = _roof_view(mesh, uvmesh, roofs, tex, pic_mask, extent, yaw, tilt, ROOF_SEARCH_SIZE)
            key = (round(v["iou"], 2), -_turn_gap(yaw, yaw0))
            if best is None or key > best[0]:
                best = (key, yaw, tilt)
    return best[1], best[2]


def _place_picture(pic: np.ndarray, pic_mask: np.ndarray, render_mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The picture and its cut-out put into the render's frame by the silhouette fit."""
    size = render_mask.shape[0]
    mask_sq = texfidelity._pad_to(pic_mask, size)
    fit = texfidelity.fit_silhouette(mask_sq, render_mask)
    img = Image.fromarray(face.to_uint8(texfidelity._pad_square(pic))).resize((size, size), Image.LANCZOS)
    warped = np.asarray(texfidelity._warp(img, size, fit["scale"], fit["tx"], fit["ty"], Image.BICUBIC), dtype=np.float64) / 255.0
    wmask = np.asarray(texfidelity._warp(Image.fromarray((mask_sq * 255).astype(np.uint8)), size, fit["scale"], fit["tx"],
                                         fit["ty"], Image.BILINEAR)) >= 128
    return warped, wmask


def roof_picture_pixels(pic: np.ndarray, pic_mask: np.ndarray, render_mask: np.ndarray,
                        roof_px: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    """The picture's own pixels under the model's roof pixels. The picture is put onto the render by the silhouette fit
    (scale and shift that maximise the outline overlap, as the fidelity check does), so each roof pixel meets the picture
    pixel at the same place on the building, not at the same place in the bounding box. Only roof pixels with the cut-out
    under them, away from its edge, count. -> (picture in the render's frame (S,S,3), mask of roof pixels with picture
    under them), or None when fewer than ROOF_MIN_PX remain."""
    warped, wmask = _place_picture(pic, pic_mask, render_mask)
    inside = roof_px & face.erode(wmask, texfidelity.BORDER_PX)
    if inside.sum() < ROOF_MIN_PX:
        return None
    return warped, inside


def roof_lift_plan(mesh: dict[str, np.ndarray], uvmesh: dict[str, np.ndarray], tex: np.ndarray, pic: np.ndarray,
                   pic_mask: np.ndarray, view: str | None = None, yaw: float | None = None,
                   cap: float = ROOF_CAP) -> dict[str, Any]:
    """The roof lift: which islands to lift and by how much (L*). `tex` is the base colour after the global shift.
    The roof is the up-facing surface in the top of the model (roof_islands); its pixels are found in a flat render
    at the turn and tilt that fit the picture's outline best (roof_pose; `yaw`, default the build view's turn, breaks ties); the picture is put
    onto that render by the outline fit (roof_picture_pixels), and the picture's mean L* under the roof pixels is the
    target (the render's roof mean is taken over the same pixels). Islands whose own mean L* is at least ROOF_MARGIN below the target are lifted by the gap
    between the target and the roof pixels' mean, capped. -> {mode: none|roof_lift, shift, islands, target_L, roof_L,
    island_L, reason}."""
    roofs = roof_islands(uvmesh, mesh)
    if not roofs:
        return {"mode": "none", "reason": "no_roof_island"}
    ys, xs = np.nonzero(pic_mask)
    if len(ys) < 100:
        return {"mode": "none", "reason": "no_cutout"}
    yaw0 = VIEW_TURN.get(view or "front", 0.0) if yaw is None else yaw
    extent = float(max(np.ptp(xs), np.ptp(ys)) + 1) * L_SIZE / max(pic.shape[:2])
    yaw, tilt = roof_pose(mesh, uvmesh, roofs, tex, pic_mask, extent, yaw0)
    v = _roof_view(mesh, uvmesh, roofs, tex, pic_mask, extent, yaw, tilt, L_SIZE)
    r, roof_px = v["render"], v["roof_px"]
    if roof_px.sum() < ROOF_MIN_PX:
        return {"mode": "none", "reason": "roof_not_visible"}
    picked = roof_picture_pixels(pic, pic_mask, r["mask"], roof_px)
    if picked is None:
        return {"mode": "none", "reason": "picture_roof_not_found"}
    warped, inside = picked
    target = float(face.to_lab(warped)[..., 0][inside].mean())
    rlab = face.to_lab(r["image"])[..., 0]
    roof_l = float(rlab[inside].mean())
    # each roof island's own mean over its up-facing texels
    ras = imaging.rasterise_uv(uvmesh, tex.shape[0])
    tl = face.to_lab(tex)[..., 0]
    island_l: dict[int, float] = {}
    for i in roofs:
        m = (ras["labels"] == i + 1) & (ras["up"] >= ROOF_TEXEL_UP)
        if m.sum() > 0:
            island_l[i] = float(tl[m].mean())
    lift = [i for i, l in island_l.items() if l < target - ROOF_MARGIN]
    gap = target - roof_l
    out = {"target_L": round(target, 2), "roof_L": round(roof_l, 2), "gap": round(gap, 2),
           "island_L": {str(i): round(l, 2) for i, l in island_l.items()}, "yaw": yaw, "tilt": tilt,
           "fit_iou": round(v["iou"], 3)}
    if not lift or gap < ROOF_MARGIN:
        return {**out, "mode": "none", "reason": "roof_matches", "islands": []}
    shift = float(np.clip(gap, 0.0, cap))
    return {**out, "mode": "roof_lift", "shift": shift, "islands": lift, "capped": gap > cap, "reason": "applied"}


def _roof_texels(size: int, uvmesh: dict[str, np.ndarray], islands: list[int]) -> np.ndarray:
    """The up-facing texels of the islands, with the gutter grown out of them."""
    ras = imaging.rasterise_uv(uvmesh, size)
    covered = ras["labels"] > 0
    sel = np.isin(ras["labels"], [i + 1 for i in islands]) & (ras["up"] >= ROOF_TEXEL_UP)
    # a gutter texel the clean-up grew out of a lifted texel takes its move too, so no dark rim shows at the seams
    grown = imaging.dilate(sel[..., None].astype(np.uint8), covered, imaging.dilation_px(size))[..., 0].astype(bool)
    return sel | (grown & ~covered)


def _roof_colour_texels(size: int, uvmesh: dict[str, np.ndarray], mesh: dict[str, np.ndarray],
                        islands: list[int]) -> np.ndarray:
    """The texels the roof colour move may reach: the roof islands' up-facing faces, and every other face that points up
    by ROOF_UP_MIN or more at the same height (one roof surface is often cut into many pieces, only some of which count
    as roof islands, and a hue that stopped at their edges would show as patches). With the gutter grown out of them."""
    roof = np.isin(uvmesh["island"], islands) & (uvmesh["up"] >= ROOF_TEXEL_UP)
    if not roof.any():
        return np.zeros((size, size), bool)
    h = mesh["verts"][uvmesh["faces"]][:, :, imaging.UP_AXIS].mean(axis=1)
    faces = roof | ((uvmesh["up"] >= ROOF_UP_MIN) & (h >= np.percentile(h[roof], ROOF_FLOOR_PCT, method="lower")))
    labels = imaging.rasterise_uv({**uvmesh, "island": np.arange(len(faces))}, size)["labels"]  # label = face + 1
    covered = labels > 0
    sel = covered & faces[np.maximum(labels - 1, 0)]
    grown = imaging.dilate(sel[..., None].astype(np.uint8), covered, imaging.dilation_px(size))[..., 0].astype(bool)
    return sel | (grown & ~covered)


def apply_roof_lift(tex: np.ndarray, uvmesh: dict[str, np.ndarray], plan: dict[str, Any] | None) -> np.ndarray:
    """Add the plan's L* shift to the up-facing texels of its islands (and the gutter grown out of them)."""
    if not plan or plan.get("mode") != "roof_lift" or not plan.get("islands"):
        return tex
    sel = _roof_texels(tex.shape[0], uvmesh, plan["islands"])
    out = np.array(tex, dtype=np.float64)
    out[sel] = from_lab(shifted_lab(face.to_lab(out[sel]), float(plan["shift"])))
    return out


def _agreement(a: np.ndarray, b: np.ndarray) -> float:
    """Correlation of two (n, c) samples, each channel about its own mean, so an even light or tone gap does not count."""
    a, b = a - a.mean(axis=0), b - b.mean(axis=0)
    den = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / den) if den > 1e-9 else 0.0


def roof_layout_score(pic: np.ndarray, pic_mask: np.ndarray, render: np.ndarray, render_mask: np.ndarray) -> float:
    """How well the inside of the picture agrees with the inside of the render once their outlines are fitted: the mean
    of the correlations of edge strength and of (a*, b*). An outline alone cannot tell a building's side from the
    mirrored one; where the windows, trim and colours sit can."""
    warped, wmask = _place_picture(pic, pic_mask, render_mask)
    m = face.erode(wmask & render_mask, ROOF_LAYOUT_BORDER)
    if m.sum() < ROOF_MIN_PX:
        return 0.0
    a, b = face.to_lab(warped), face.to_lab(render)

    def edges(lab: np.ndarray) -> np.ndarray:
        return face.box_blur(np.hypot(*np.gradient(lab[..., 0])), ROOF_EDGE_BLUR)[m][:, None]

    return (_agreement(edges(a), edges(b)) + _agreement(a[m][:, 1:], b[m][:, 1:])) / 2.0


def roof_colour_pose(mesh: dict[str, np.ndarray], uvmesh: dict[str, np.ndarray], roofs: list[int], tex: np.ndarray,
                     pic: np.ndarray, pic_mask: np.ndarray, extent: float) -> dict[str, float] | None:
    """The view whose layout agrees best with the picture among those whose outline fits (every 45 degrees of turn,
    ROOF_COLOUR_TILTS). -> {yaw, tilt, iou, layout}, or None when no view reaches both floors."""
    best = None
    for tilt in ROOF_COLOUR_TILTS:
        for yaw in L_COARSE:
            v = _roof_view(mesh, uvmesh, roofs, tex, pic_mask, extent, yaw, tilt, ROOF_SEARCH_SIZE)
            if v["iou"] < ROOF_COLOUR_IOU:
                continue
            layout = roof_layout_score(pic, pic_mask, v["render"]["image"], v["render"]["mask"])
            if layout >= ROOF_COLOUR_LAYOUT and (best is None or layout > best["layout"]):
                best = {"yaw": yaw, "tilt": tilt, "iou": v["iou"], "layout": layout}
    return best


def roof_top_seen(mesh: dict[str, np.ndarray], uvmesh: dict[str, np.ndarray], roofs: list[int],
                  render: dict[str, Any], roof_px: np.ndarray) -> float:
    """The share (by area) of the top of the roof that the view sees: roof faces within ROOF_CROWN_BAND of the model's
    height below the highest roof face."""
    faces = np.nonzero(np.isin(uvmesh["island"], roofs) & (uvmesh["up"] >= ROOF_TEXEL_UP))[0]
    if not len(faces):
        return 0.0
    tri = mesh["verts"][mesh["faces"]]
    area = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    h = tri[:, :, imaging.UP_AXIS].mean(axis=1)
    tall = float(np.ptp(mesh["verts"][:, imaging.UP_AXIS]))
    top = faces[h[faces] >= h[faces].max() - ROOF_CROWN_BAND * tall]
    seen = np.intersect1d(np.unique(render["raster"]["tri"][roof_px]), top)
    return float(area[seen].sum() / max(area[top].sum(), 1e-12))


def roof_colour_plan(mesh: dict[str, np.ndarray], uvmesh: dict[str, np.ndarray], tex: np.ndarray, pic: np.ndarray,
                     pic_mask: np.ndarray) -> dict[str, Any]:
    """The roof colour move: one (L*, a*, b*) offset for the roof, from the median of the render's roof pixels to
    the median of the picture's pixels under them. Made only at a view roof_colour_pose trusts and when the two differ in
    (a*, b*) by ROOF_COLOUR_MIN_AB or more; L* is capped at ROOF_CAP, (a*, b*) at ROOF_AB_CAP. -> {mode: none|roof_colour, reason,
    yaw, tilt, fit_iou, layout, top_seen, gap_ab, move, roof_ab, islands}."""
    roofs = roof_islands(uvmesh, mesh)
    if not roofs:
        return {"mode": "none", "reason": "no_roof_island"}
    ys, xs = np.nonzero(pic_mask)
    if len(ys) < 100:
        return {"mode": "none", "reason": "no_cutout"}
    extent = float(max(np.ptp(xs), np.ptp(ys)) + 1) * L_SIZE / max(pic.shape[:2])
    pose = roof_colour_pose(mesh, uvmesh, roofs, tex, pic, pic_mask, extent)
    if pose is None:
        return {"mode": "none", "reason": "view_not_trusted"}
    out: dict[str, Any] = {"yaw": pose["yaw"], "tilt": pose["tilt"], "fit_iou": round(pose["iou"], 3),
                           "layout": round(pose["layout"], 3)}
    v = _roof_view(mesh, uvmesh, roofs, tex, pic_mask, extent, pose["yaw"], pose["tilt"], L_SIZE)
    r, roof_px = v["render"], v["roof_px"]
    if roof_px.sum() < ROOF_MIN_PX:
        return {**out, "mode": "none", "reason": "roof_not_visible"}
    out["top_seen"] = round(roof_top_seen(mesh, uvmesh, roofs, r, roof_px), 3)
    if out["top_seen"] < ROOF_CROWN_SEEN:
        return {**out, "mode": "none", "reason": "roof_top_not_seen"}
    picked = roof_picture_pixels(pic, pic_mask, r["mask"], roof_px)
    if picked is None:
        return {**out, "mode": "none", "reason": "picture_roof_not_found"}
    warped, inside = picked
    own = np.median(face.to_lab(r["image"])[inside], axis=0)
    move = np.median(face.to_lab(warped)[inside], axis=0) - own
    chroma = float(np.hypot(move[1], move[2]))
    out["gap_ab"] = round(chroma, 2)
    if chroma < ROOF_COLOUR_MIN_AB:
        return {**out, "mode": "none", "reason": "colour_matches"}
    move[0] = np.clip(move[0], -ROOF_CAP, ROOF_CAP)
    if chroma > ROOF_AB_CAP:
        move[1:] *= ROOF_AB_CAP / chroma
    return {**out, "mode": "roof_colour", "reason": "applied", "move": [round(float(x), 3) for x in move],
            "roof_ab": [round(float(own[1]), 3), round(float(own[2]), 3)], "islands": roofs}


def apply_roof_colour(tex: np.ndarray, uvmesh: dict[str, np.ndarray], mesh: dict[str, np.ndarray],
                      plan: dict[str, Any] | None) -> np.ndarray:
    """Add the plan's Lab offset to the roof's up-facing texels (_roof_colour_texels). A texel takes the move by how close
    its (a*, b*) is to the sampled roof's (ROOF_SAME_AB), so trim of another colour that sits on the roof keeps its own."""
    if not plan or plan.get("mode") != "roof_colour" or not plan.get("islands"):
        return tex
    sel = _roof_colour_texels(tex.shape[0], uvmesh, mesh, plan["islands"])
    out = np.array(tex, dtype=np.float64)
    lab = face.to_lab(out[sel])
    away = np.hypot(lab[..., 1] - plan["roof_ab"][0], lab[..., 2] - plan["roof_ab"][1])
    share = np.clip(2.0 - away / ROOF_SAME_AB, 0.0, 1.0)
    moved = shifted_lab(lab, float(plan["move"][0]))
    moved[..., 1] += plan["move"][1]
    moved[..., 2] += plan["move"][2]
    weighted = lab + share[..., None] * (moved - lab)
    new = out[sel]
    hit = share > 0  # a texel with no share is left byte for byte
    new[hit] = from_lab(weighted[hit])
    out[sel] = new
    return out


def match_base_colour(glb: Path | str, picture: Path | str, base_png: Path | str, view: str | None = None,
                      step1: Path | str | None = None, route: str | None = None, chroma: bool = False,
                      regions: bool = False) -> dict[str, Any]:
    """Measure the model's lightness against its picture at the build view `view`'s turn and, when it is off, shift
    base_png and the GLB's own base colour image by the same move. When the build view's mean lightness differs from the
    step-1 picture `step1`'s by more than DRIFT_MAX (often shading on the turned view), the target is the step-1 picture
    instead, measured at the front turn.
    `chroma` (the four-view humanoid build only; every other route is the lightness stage alone, as before) adds the
    chroma and hue stage after it. -> metrics {applied, lightness: {...}, chroma?: {...}, reason}."""
    rec: dict[str, Any] = {"applied": False}
    drift = step2_drift(picture, step1)
    drifted = drift is not None and abs(drift) > DRIFT_MAX
    mesh = face.load_mesh(glb)
    if mesh is None:
        return {**rec, "reason": "no_texture_layout"}
    tex = face.read_rgb(base_png)
    ref, ref_view = (step1, "front") if drifted else (picture, view)
    pic, pic_mask = texfidelity.load_picture(ref)
    lp = lightness_plan(mesh, tex, pic, pic_mask, ref_view)
    if drift is not None:
        lp["drift"] = drift
    if drifted:
        lp["target"] = "step1"
    rec["lightness"] = lp
    rec["reason"] = lp.get("reason")
    out = apply(tex, lp) if lp.get("mode") == "l_shift" else tex
    if route == ROOF_ROUTE:  # buildings only: any other route is the global shift alone, exactly as before
        uvmesh = imaging.load_uv_mesh(glb)
        if uvmesh is not None and lp.get("iou", 0.0) >= L_IOU_MIN:
            rp = roof_lift_plan(mesh, uvmesh, out, pic, pic_mask, ref_view, yaw=lp.get("yaw"))
            rc = roof_colour_plan(mesh, uvmesh, out, pic, pic_mask)
            if rc["mode"] == "roof_colour":  # its move carries the lightness too, so the lift is not added on top
                if rp["mode"] == "roof_lift":
                    rp = {**rp, "mode": "none", "reason": "colour_move", "islands": []}
                out = apply_roof_colour(out, uvmesh, mesh, rc)
            else:
                out = apply_roof_lift(out, uvmesh, rp)
            rec["roof_lift"], rec["roof_colour"] = rp, rc
    if chroma and "yaw" in lp:
        cp = chroma_plan(mesh, out, pic, pic_mask, lp["yaw"], lp.get("iou", 0.0))
        rec["chroma"] = cp
        out = apply_chroma(out, cp)
        if regions:  # the four-view humanoid build only: per-region moves after the chroma stage
            gp = region_plan(mesh, out, pic, pic_mask, lp["yaw"], lp.get("iou", 0.0))
            rec["regions"] = gp
            out = apply_regions(out, gp)
    if lp.get("mode") != "l_shift" and rec.get("roof_lift", {}).get("mode") != "roof_lift" and rec.get("roof_colour", {}).get("mode") != "roof_colour" and rec.get("chroma", {}).get("mode") != "chroma" and rec.get("regions", {}).get("mode") != "regions":
        return rec
    Image.fromarray(face.to_uint8(out)).save(base_png)
    imaging.glb_replace_images(glb, base_color=base_png)
    rec["applied"] = True
    return rec


# Region stage (PL-113): per-region lightness, chroma and hue moves after the chroma stage.
R_K = 6                # colour regions found in the render
R_MIN_SHARE = 0.015    # a region under this share of the masked render is never moved (small details stay as they are)
R_MIN_DE = 4.0         # a region already within this delta E of its target is left alone
R_L_CAP = 25.0         # most a region's mean L* moves
R_SCALE = (0.6, 1.6)   # chroma scale range
R_ROT_MAX = 25.0       # hue turn, degrees
R_CARRY_MIN = 0.6      # share of a region's pixels that must land inside the picture's cut-out
R_SPREAD_MAX = 20.0    # the picture's colours under a region must lie this close (mean delta E) to their own mean
R_MIN_CHROMA = 5.0     # hue is turned only when both chromas are at least this
R_FLAT = 1.5           # texels within this many region widths of a region's centre take its whole move
R_SAMPLE = 20000       # pixels used to find the regions


def _kmeans(x: np.ndarray, k: int, seed: int = 0, iters: int = 25) -> tuple[np.ndarray, np.ndarray]:
    """k-means on rows of x with a k-means++ start and a fixed seed -> (centres sorted by L*, labels of every row)."""
    rng = np.random.default_rng(seed)
    sub = x if len(x) <= R_SAMPLE else x[rng.choice(len(x), R_SAMPLE, replace=False)]
    c = [sub[rng.integers(len(sub))]]
    for _ in range(k - 1):
        d = np.min([((sub - ci) ** 2).sum(1) for ci in c], axis=0)
        c.append(sub[rng.choice(len(sub), p=d / d.sum())] if d.sum() > 0 else sub[rng.integers(len(sub))])
    c = np.array(c)
    for _ in range(iters):
        lab = np.argmin(((sub[:, None] - c[None]) ** 2).sum(2), axis=1)
        new = np.array([sub[lab == i].mean(0) if (lab == i).any() else c[i] for i in range(k)])
        if np.allclose(new, c, atol=1e-3):
            break
        c = new
    c = c[np.argsort(c[:, 0])]
    out = np.empty(len(x), dtype=int)
    for s in range(0, len(x), 50000):
        out[s:s + 50000] = np.argmin(((x[s:s + 50000, None] - c[None]) ** 2).sum(2), axis=1)
    return c, out


def _region_move(rc: np.ndarray, tc: np.ndarray) -> dict[str, float]:
    """The capped move taking a region's mean Lab rc to its target tc: L* shift, chroma scale, hue turn (degrees)."""
    dl = float(np.clip(tc[0] - rc[0], -R_L_CAP, R_L_CAP))
    rch, tch = float(np.hypot(*rc[1:])), float(np.hypot(*tc[1:]))
    scale, rot = 1.0, 0.0
    if rch >= 1.0 and tch >= 1.0:
        scale = float(np.clip(tch / rch, *R_SCALE))
        if rch >= R_MIN_CHROMA and tch >= R_MIN_CHROMA:
            gap = np.degrees(np.angle(complex(*tc[1:]) / complex(*rc[1:])))
            rot = float(np.clip(gap, -R_ROT_MAX, R_ROT_MAX))
    return {"dl": dl, "scale": scale, "rotate": rot}


def _region_name(centre: np.ndarray, y_frac: float) -> str:
    """Plain colour and place words for a region: lightness, hue family, and which third of the height it sits in."""
    l, a, b = centre
    if float(np.hypot(a, b)) < 8:
        col = "black" if l < 25 else "dark grey" if l < 45 else "grey" if l < 75 else "white"
    else:
        h = float(np.degrees(np.arctan2(b, a)) % 360)
        fam = "red" if h < 25 or h >= 340 else "orange" if h < 50 else "yellow" if h < 100 else "green" if h < 180 else "blue" if h < 270 else "purple"
        col = ("dark " if l < 40 else "light " if l > 70 else "") + fam
    return "%s, %s" % (col, "top third" if y_frac < 0.34 else "middle third" if y_frac < 0.67 else "bottom third")


def _skin_like(centre: np.ndarray) -> bool:
    l, a, b = centre
    h = float(np.degrees(np.arctan2(b, a)) % 360)
    return bool(l > 45 and a > 4 and b > 8 and 25 <= h <= 85)


def apply_regions(rgb: np.ndarray, plan: dict[str, Any] | None) -> np.ndarray:
    """The region moves of region_plan() on an sRGB 0..1 image (..., 3). Each texel is weighted towards each region by its
    Lab distance to the region's centre (Gaussian, the region's own width); the weights are summed without normalising
    below 1, so a texel far from every region stays put; the moves are blended by those weights. A region without a move
    still pulls its own texels, so they are not taken by a neighbour's move."""
    if not plan or plan.get("mode") != "regions":
        return rgb
    lab = face.to_lab(rgb).astype(np.float64)
    shape = lab.shape[:-1]
    flat = lab.reshape(-1, 3)
    ws, dls, fs = [], [], []
    for reg in plan["regions"]:
        d2 = ((flat - np.array(reg["centre"])) ** 2).sum(1)
        sg = float(reg["sigma"])
        # flat top inside R_FLAT widths, Gaussian fall-off beyond: the core of a region takes its whole move
        out_d = np.maximum(np.sqrt(d2) - R_FLAT * sg, 0.0)
        ws.append(np.exp(-out_d ** 2 / (2.0 * (0.7 * sg) ** 2)))
        mv = reg.get("move") or {"dl": 0.0, "scale": 1.0, "rotate": 0.0}
        dls.append(mv["dl"])
        fs.append(mv["scale"] * np.exp(1j * np.radians(mv["rotate"])))
    ws = np.array(ws)
    norm = np.maximum(ws.sum(0), 1.0)
    dl = (ws * np.array(dls)[:, None]).sum(0) / norm
    fac = 1.0 + (ws * (np.array(fs)[:, None] - 1.0)).sum(0) / norm
    out = flat.copy()
    room = np.where(dl > 0, 100.0 - out[:, 0], out[:, 0])
    out[:, 0] = out[:, 0] + dl * np.clip(room / TAPER, 0.0, 1.0)
    ab = (out[:, 1] + 1j * out[:, 2]) * fac
    out[:, 1], out[:, 2] = ab.real, ab.imag
    return from_lab(out.reshape(shape + (3,)))


def region_plan(mesh: dict[str, np.ndarray], tex: np.ndarray, pic: np.ndarray, pic_mask: np.ndarray,
                yaw: float, iou: float) -> dict[str, Any]:
    """The region stage, run after the chroma stage on its texture `tex`. The model's flat render at the lightness stage's
    turn `yaw` is split into R_K colour regions (k-means on Lab). Each pixel is carried to the picture by the two
    silhouettes' bounding boxes and the picture's mean Lab under a region is its target. A region is moved only when
    enough of it lands inside the cut-out (R_CARRY_MIN), the picture's colours under it agree (R_SPREAD_MAX), it holds at
    least R_MIN_SHARE of the render and is more than R_MIN_DE from the target. Moves are capped (R_L_CAP, R_SCALE,
    R_ROT_MAX). Every region is then checked on a re-render: one whose own delta E got worse goes back to no move, and
    when the share-weighted delta E over the regions did not drop the whole pass is dropped.
    -> {mode: none|regions, regions: [{name, share, centre, sigma, carried, target, spread, de_before, de_after, move,
    skin, reason}], de_before, de_after, reason}."""
    if iou < L_IOU_MIN:
        return {"mode": "none", "reason": "fit_too_loose"}
    ys, xs = np.nonzero(pic_mask)
    if len(ys) < 100:
        return {"mode": "none", "reason": "no_cutout"}
    extent = float(max(np.ptp(xs), np.ptp(ys)) + 1) * L_SIZE / max(pic.shape[:2])
    cam = texfidelity._frame_camera(mesh, yaw, extent, L_SIZE)
    r = face.render_view(mesh, tex, cam, background=0.0)
    border = max(1, int(round(texfidelity.BORDER_PX * max(pic.shape[:2]) / L_SIZE)))
    rm, pm = face.erode(r["mask"], texfidelity.BORDER_PX), face.erode(pic_mask, border)
    if rm.sum() < L_MASK_MIN_PX or pm.sum() < L_MASK_MIN_PX * (max(pic.shape[:2]) / L_SIZE) ** 2:
        return {"mode": "none", "reason": "mask_too_small"}
    ry, rx = np.nonzero(rm)
    full_y, full_x = np.nonzero(r["mask"])
    rlab = face.to_lab(face.to_uint8(r["image"]).astype(np.float64) / 255.0)[rm]
    centres, lab = _kmeans(rlab, R_K)
    py0, py1, px0, px1 = ys.min(), ys.max(), xs.min(), xs.max()
    u = (rx - full_x.min() + 0.5) / max(np.ptp(full_x) + 1, 1)
    v = (ry - full_y.min() + 0.5) / max(np.ptp(full_y) + 1, 1)
    sx = np.clip(np.round(px0 + u * (px1 - px0 + 1) - 0.5).astype(int), 0, pic.shape[1] - 1)
    sy = np.clip(np.round(py0 + v * (py1 - py0 + 1) - 0.5).astype(int), 0, pic.shape[0] - 1)
    inside = pm[sy, sx]
    plab = face.to_lab(pic[sy, sx])
    top, height = full_y.min(), max(np.ptp(full_y) + 1, 1)
    regions: list[dict[str, Any]] = []
    ids: list[int] = []
    for i in range(R_K):
        m = lab == i
        n = int(m.sum())
        if n == 0:
            continue
        share = n / len(lab)
        rc = rlab[m].mean(0)
        sigma = float(max(np.sqrt(((rlab[m] - rc) ** 2).sum(1).mean() / 3.0), 6.0))
        reg: dict[str, Any] = {"name": _region_name(rc, float((ry[m].mean() - top) / height)), "share": round(share, 4),
                               "centre": [round(float(x), 3) for x in rc], "sigma": round(sigma, 3), "skin": _skin_like(rc),
                               "carried": round(float(inside[m].mean()), 3)}
        if share < R_MIN_SHARE:
            reg["reason"] = "small"
        elif reg["carried"] < R_CARRY_MIN:
            reg["reason"] = "carry_unreliable"
        else:
            pl = plab[m & inside]
            tc = pl.mean(0)
            reg["target"] = [round(float(x), 3) for x in tc]
            reg["spread"] = round(float(np.sqrt(((pl - tc) ** 2).sum(1)).mean()), 2)
            reg["de_before"] = round(float(np.linalg.norm(rc - tc)), 2)
            if reg["spread"] > R_SPREAD_MAX:
                reg["reason"] = "picture_mixed"
            elif reg["de_before"] <= R_MIN_DE:
                reg["reason"] = "no_gain"
            else:
                reg["move"] = _region_move(rc, tc)
                reg["reason"] = "applied"
        regions.append(reg)
        ids.append(i)
    if not any(g.get("move") for g in regions):
        return {"mode": "none", "reason": "no_region_to_move", "regions": regions}
    plan: dict[str, Any] = {"mode": "regions", "regions": regions, "reason": "applied", "yaw": yaw}

    def remeasure() -> None:
        r2 = face.render_view(mesh, apply_regions(tex, plan), cam, background=0.0)
        l2 = face.to_lab(face.to_uint8(r2["image"]).astype(np.float64) / 255.0)[rm]
        for i, g in zip(ids, regions):
            if "target" in g:
                g["de_after"] = round(float(np.linalg.norm(l2[lab == i].mean(0) - np.array(g["target"]))), 2)

    remeasure()
    for g in regions:  # a region whose own delta E got worse goes back to no move
        if g.get("move") and g["de_after"] > g["de_before"]:
            g["move"], g["reason"] = None, "worse_dropped"
    if not any(g.get("move") for g in regions):
        return {"mode": "none", "reason": "all_worse", "regions": regions}
    remeasure()
    for g in regions:  # dropping a neighbour can change a region's own result: it is checked again, and set back if worse
        if g.get("move") and g["de_after"] > g["de_before"]:
            g["move"], g["reason"] = None, "worse_dropped"
    if not any(g.get("move") for g in regions):
        return {"mode": "none", "reason": "all_worse", "regions": regions}
    remeasure()
    scored = [g for g in regions if "target" in g]
    tw = sum(g["share"] for g in scored)
    plan["de_before"] = round(sum(g["share"] * g["de_before"] for g in scored) / tw, 2)
    plan["de_after"] = round(sum(g["share"] * g.get("de_after", g["de_before"]) for g in scored) / tw, 2)
    for g in scored:
        if not g.get("move"):
            g["de_after"] = g.get("de_after", g["de_before"])
    if plan["de_after"] >= plan["de_before"]:
        return {"mode": "none", "reason": "whole_not_better", "regions": regions,
                "de_before": plan["de_before"], "de_after": plan["de_after"]}
    return plan
