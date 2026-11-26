"""Lightness match of a built model's base colour to its picture (bakeoff/render-tone/PLAN.md).

TRELLIS.2's base colour comes out up to about 17 L* lighter or darker than the picture it was built from (the tone is
set by the builder; the clean-up and the export keep it). After the build, lightness_plan() compares the whole-silhouette
mean L* of a flat render at the build view's own turn (the turn nearest it whose outline fits best, searched only around
that view and its mirror) with the mean L* over the picture's cut-out, borders left out on both sides. No pixel fit is
needed, so it runs down to an outline IoU of L_IOU_MIN; the move is capped at L_CAP and is a shift of L* only (hue and
chroma kept, so a neutral grey stays neutral), tapered near black and white so detail is not flattened.

When the build-view picture's own lightness differs from the step-1 picture's by more than DRIFT_MAX (the step-2 redraw
changed the tone), the target becomes the step-1 picture itself: its mean L* over its own cut-out, measured at the front
turn that fits step 1. The same global L*-only shift is applied, so the model lands on step 1, not on the drifted view.

The corrected base colour is written to base_color.png AND into the GLB's own base colour image, because the clean-up
re-bakes from the GLB (a correction in the PNG alone never reached a shipped model).

lightness_plan() and apply() are pure; match_base_colour() does the file work.

Roof lift (buildings only, PL-15): a building's roof often bakes far darker than the picture's roof, which one global
shift cannot fix without moving the walls. After the global shift, roof_lift_plan() finds the up-facing UV islands from
the mesh normals, measures the picture's roof (the picture's pixels under the model's own roof pixels at the build view,
mapped by the two silhouettes' bounding boxes) and lifts the islands that are clearly darker than it by one capped L*
shift, on those islands' up-facing texels only. Other routes, and a roof that already matches, are untouched.
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
DRIFT_MAX = 4.0       # build view against step-1 picture, mean L* over each cut-out: past this the target is step 1
C_SCALE = (0.75, 1.4)  # the chroma stage scales a* and b* by the picture's mean chroma over the render's, within this range
C_ROT_MAX = 15.0       # and turns their hue by the gap between the two mean hue directions, at most this many degrees
C_MIN_SCALE = 0.05     # a chroma gap under this share and a hue gap under C_MIN_ROT degrees are left alone
C_MIN_ROT = 2.0
ROOF_ROUTE = "buildings"  # the only route the roof lift runs for
ROOF_UP_MIN = 0.35        # an island whose UV-area-weighted face normal points up by at least this much is a roof
ROOF_TEXEL_UP = 0.2       # inside a roof island only texels of faces that point up by at least this much are lifted
ROOF_MARGIN = 8.0         # an island is lifted only when its mean L* is this far below the picture's roof
ROOF_CAP = 25.0           # the most the roof lift moves, in L* units
ROOF_MIN_PX = 200         # roof pixels in the render, and picture pixels under them, needed to trust the measure
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


def roof_islands(uvmesh: dict[str, np.ndarray]) -> list[int]:
    """Islands that face up: the UV-area-weighted mean of their faces' up component is at least ROOF_UP_MIN."""
    uv, f = uvmesh["uv"], uvmesh["faces"]
    a, b, c = uv[f[:, 0]], uv[f[:, 1]], uv[f[:, 2]]
    area = np.abs((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1])) / 2
    isl = uvmesh["island"]
    out = []
    for i in np.unique(isl):
        m = isl == i
        w = area[m].sum()
        if w > 0 and float((uvmesh["up"][m] * area[m]).sum() / w) >= ROOF_UP_MIN:
            out.append(int(i))
    return out


def roof_lift_plan(mesh: dict[str, np.ndarray], uvmesh: dict[str, np.ndarray], tex: np.ndarray, pic: np.ndarray,
                   pic_mask: np.ndarray, view: str | None = None, yaw: float | None = None,
                   cap: float = ROOF_CAP) -> dict[str, Any]:
    """The roof lift: which islands to lift and by how much (L*). `tex` is the base colour after the global shift.
    The model's roof pixels are found in a flat render at `yaw` (default: the build view's turn); each pixel's place in
    the model's silhouette bounding box is carried to the picture's cut-out bounding box, and the picture's mean L*
    there is the target. Islands whose own mean L* is at least ROOF_MARGIN below the target are lifted by the gap
    between the target and the roof pixels' mean, capped. -> {mode: none|roof_lift, shift, islands, target_L, roof_L,
    island_L, reason}."""
    roofs = roof_islands(uvmesh)
    if not roofs:
        return {"mode": "none", "reason": "no_roof_island"}
    ys, xs = np.nonzero(pic_mask)
    if len(ys) < 100:
        return {"mode": "none", "reason": "no_cutout"}
    yaw = VIEW_TURN.get(view or "front", 0.0) if yaw is None else yaw
    extent = float(max(np.ptp(xs), np.ptp(ys)) + 1) * L_SIZE / max(pic.shape[:2])
    cam = texfidelity._frame_camera(mesh, yaw, extent, L_SIZE)
    r = face.render_view(mesh, tex, cam, background=0.0)
    face_roof = np.isin(uvmesh["island"], roofs) & (uvmesh["up"] >= ROOF_TEXEL_UP)
    rm = r["mask"]
    roof_px = rm & face_roof[np.maximum(r["raster"]["tri"], 0)]
    if roof_px.sum() < ROOF_MIN_PX:
        return {"mode": "none", "reason": "roof_not_visible"}
    ry, rx = np.nonzero(rm)
    py0, py1, px0, px1 = ys.min(), ys.max(), xs.min(), xs.max()
    qy, qx = np.nonzero(roof_px)
    u = (qx - rx.min() + 0.5) / max(np.ptp(rx) + 1, 1)
    v = (qy - ry.min() + 0.5) / max(np.ptp(ry) + 1, 1)
    sx = np.clip(np.round(px0 + u * (px1 - px0 + 1) - 0.5).astype(int), 0, pic.shape[1] - 1)
    sy = np.clip(np.round(py0 + v * (py1 - py0 + 1) - 0.5).astype(int), 0, pic.shape[0] - 1)
    inside = pic_mask[sy, sx]
    if inside.sum() < ROOF_MIN_PX:
        return {"mode": "none", "reason": "picture_roof_not_found"}
    target = float(face.to_lab(pic[sy[inside], sx[inside]])[:, 0].mean())
    rlab = face.to_lab(r["image"])[..., 0]
    roof_l = float(rlab[roof_px].mean())
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
           "island_L": {str(i): round(l, 2) for i, l in island_l.items()}, "yaw": yaw}
    if not lift or gap < ROOF_MARGIN:
        return {**out, "mode": "none", "reason": "roof_matches", "islands": []}
    shift = float(np.clip(gap, 0.0, cap))
    return {**out, "mode": "roof_lift", "shift": shift, "islands": lift, "capped": gap > cap, "reason": "applied"}


def apply_roof_lift(tex: np.ndarray, uvmesh: dict[str, np.ndarray], plan: dict[str, Any] | None) -> np.ndarray:
    """Add the plan's L* shift to the up-facing texels of its islands (and the gutter grown out of them)."""
    if not plan or plan.get("mode") != "roof_lift" or not plan.get("islands"):
        return tex
    size = tex.shape[0]
    ras = imaging.rasterise_uv(uvmesh, size)
    covered = ras["labels"] > 0
    sel = np.isin(ras["labels"], [i + 1 for i in plan["islands"]]) & (ras["up"] >= ROOF_TEXEL_UP)
    # a gutter texel the clean-up grew out of a lifted texel takes its move too, so no dark rim shows at the seams
    grown = imaging.dilate(sel[..., None].astype(np.uint8), covered, imaging.dilation_px(size))[..., 0].astype(bool)
    sel = sel | (grown & ~covered)
    out = np.array(tex, dtype=np.float64)
    out[sel] = from_lab(shifted_lab(face.to_lab(out[sel]), float(plan["shift"])))
    return out


def match_base_colour(glb: Path | str, picture: Path | str, base_png: Path | str, view: str | None = None,
                      step1: Path | str | None = None, route: str | None = None, chroma: bool = False,
                      regions: bool = False) -> dict[str, Any]:
    """Measure the model's lightness against its picture at the build view `view`'s turn and, when it is off, shift
    base_png and the GLB's own base colour image by the same move. When the build view's tone drifted from the step-1
    picture `step1` by more than DRIFT_MAX, the target is the step-1 picture instead, measured at the front turn.
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
            rec["roof_lift"] = rp
            out = apply_roof_lift(out, uvmesh, rp)
    if chroma and "yaw" in lp:
        cp = chroma_plan(mesh, out, pic, pic_mask, lp["yaw"], lp.get("iou", 0.0))
        rec["chroma"] = cp
        out = apply_chroma(out, cp)
        if regions:  # the four-view humanoid build only: per-region moves after the chroma stage
            gp = region_plan(mesh, out, pic, pic_mask, lp["yaw"], lp.get("iou", 0.0))
            rec["regions"] = gp
            out = apply_regions(out, gp)
    if lp.get("mode") != "l_shift" and rec.get("roof_lift", {}).get("mode") != "roof_lift" and rec.get("chroma", {}).get("mode") != "chroma" and rec.get("regions", {}).get("mode") != "regions":
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
