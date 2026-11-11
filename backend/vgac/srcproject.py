"""Paint the model's own base colour from the source picture (and the turnaround views), through the existing UVs.

docs/TEXTURE-FIDELITY-PLAN.md lane B. Today no pixel of the concept picture reaches the body texture: the 3D step sees
only features of it and predicts a soft colour field. This pass projects the picture itself into the finished atlas,
with no new unwrap and no rebake, so a texel the picture sees squarely shows the picture's own pixel.

For each view (the picture first, then the turnaround views):
1. the model is drawn from an orthographic camera (the face pass's rasteriser) and the picture's cut-out is fitted to
   that drawing by silhouette (scale, shift, a few degrees of turn, and the camera's yaw when it is not known); a view
   whose outline matches under MIN_IOU is refused, and so is one whose turn the outline cannot settle (a box fits at any
   turn, and a wrong turn paints a feature twice);
2. the picture is warped onto the drawing with a dense optical flow (the soft TRELLIS colour is the target), limited to
   a few percent of the frame, smoothed, and scaled back if it would fold; the flow is dropped when it does not improve
   the structure match;
3. every texel (islands and a few texels of border) the view sees squarely takes the picture's colour with weight
   view weight x cos^4 of the angle to the surface, depth tested, zero past 75 degrees, feathered at the outline. The
   picture wins where it sees well: the turnaround views fade out where the picture's own weight is high;
4. turnaround views either give full colour ("full") or only fine detail on top of the TRELLIS colour ("detail": the
   TRELLIS colour below a few texels' worth of surface, the view's detail above it, so an AI side or back view cannot
   shift tone; this is the default);
5. texels no view reaches keep their colour with its low frequencies shifted, along the surface, to agree with the
   painted colour near them (no tone step), and island borders are grown into the gutter.

Pure numpy and Pillow. Coordinates are the glTF ones face.load_mesh gives. Measured on the bake-off knight, wolf and
chest (bakeoff/source_project_trial.py, bakeoff/out/source_project/results.md).
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image

from . import face, imaging

SIZE = 2048              # working render size; the picture is sampled bicubic (Catmull-Rom) once, at this size
FIT_N = 256              # size the camera and silhouette search runs at
FLOW_N = 512             # size the optical flow runs at
MIN_IOU = 0.85           # silhouette match below this refuses a view
COS_CUT = math.cos(math.radians(75.0))
MAX_FLOW = 0.02          # of the frame
FLOW_ALPHA = 2.0         # Horn-Schunck smoothness (higher: a stiffer field)
FLOW_SMOOTH = 8.0        # Gaussian sigma of the field in flow pixels
VIEW_WEIGHT = {"front": 1.0, "picture": 1.0, "quarter": 0.5, "left": 0.5, "right": 0.5, "back": 0.5}
BLEND_POW = 2.0          # the dominant view of a texel counts as weight squared, so views do not blur each other
FRONT_PRIORITY = 0.15    # where the picture's own weight reaches this, the turnaround views are switched off
ALPHA_RAMP = (0.004, 0.06)
GUTTER_PX = 3
DETAIL_TEXELS = 2.0      # "detail" mode: the TRELLIS colour is kept below this many texels of surface
BG = 0.5
TURN_YAWS = {
    "quarter": [y for y in range(20, 71, 10)] + [-y for y in range(20, 71, 10)],
    "left": [y for y in range(70, 111, 10)] + [-y for y in range(70, 111, 10)],
    "right": [y for y in range(70, 111, 10)] + [-y for y in range(70, 111, 10)],
    "back": list(range(140, 221, 10)),
}
PICTURE_YAWS = list(range(0, 360, 10))
TURN_VIEWS = ("quarter", "left", "right", "back")


# --- image helpers ----------------------------------------------------------------------------------------------


def catmull(img: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Bicubic (Catmull-Rom) sample of img (H,W,c) at pixel coordinates (centres at +0.5), edges clamped."""
    h, w = img.shape[:2]
    fx, fy = x - 0.5, y - 0.5
    x0, y0 = np.floor(fx).astype(int), np.floor(fy).astype(int)
    tx, ty = (fx - x0)[..., None], (fy - y0)[..., None]

    def wts(t: np.ndarray) -> list[np.ndarray]:
        t2, t3 = t * t, t * t * t
        return [(-t3 + 2 * t2 - t) / 2, (3 * t3 - 5 * t2 + 2) / 2, (-3 * t3 + 4 * t2 + t) / 2, (t3 - t2) / 2]

    wx, wy = wts(tx), wts(ty)
    out: Any = 0.0
    for j in range(4):
        yy = np.clip(y0 + j - 1, 0, h - 1)
        row: Any = 0.0
        for i in range(4):
            xx = np.clip(x0 + i - 1, 0, w - 1)
            row = row + wx[i] * img[yy, xx]
        out = out + wy[j] * row
    return out


def small(a: np.ndarray, n: int) -> np.ndarray:
    return face.resize(a, n, Image.BOX if a.shape[0] >= n else Image.BILINEAR)


def _rs(a: np.ndarray, h: int, w: int) -> np.ndarray:
    if a.shape == (h, w):
        return a
    return np.asarray(Image.fromarray(a.astype(np.float32), "F").resize((w, h), Image.BILINEAR), dtype=np.float64)


def prepare_picture(src: Path | str | Image.Image | np.ndarray, n: int = 1024) -> tuple[np.ndarray, np.ndarray]:
    """Colour (n,n,3) 0..1 and cut-out alpha (n,n) of a picture. The alpha is the picture's own when it has one; else the
    cut-out is whatever is not the backdrop (the colour of the corners), closed and with holes filled. Edges are
    defringed: colour is pulled outward from 2 px inside the cut-out."""
    if isinstance(src, np.ndarray):
        arr = np.asarray(src, dtype=np.float64)
        rgb = arr[..., :3]
        alpha = arr[..., 3] if arr.shape[-1] == 4 else None
    else:
        im = src if isinstance(src, Image.Image) else Image.open(src)
        rgba = np.asarray(im.convert("RGBA"), dtype=np.float64) / 255.0
        rgb, alpha = rgba[..., :3], rgba[..., 3]
    if alpha is not None and alpha.min() > 0.98:
        alpha = None
    if rgb.shape[0] != n:
        rgb = face.resize(rgb, n, Image.LANCZOS)
        alpha = face.resize(alpha, n, Image.BILINEAR) if alpha is not None else None
    if alpha is None:
        corners = np.array([rgb[2, 2], rgb[2, -3], rgb[-3, 2], rgb[-3, -3]])
        bg = np.median(corners, axis=0)
        nm = np.abs(rgb - bg).max(2) > 0.035
        alpha = face.fill_holes(face.erode(face.dilate_mask(nm, 3), 3)).astype(np.float64)
    core = face.erode(alpha > 0.5, 2)
    rgb = imaging.dilate(rgb, core, 8)
    alpha = np.clip(face.blur(face.dilate_mask(core, 3).astype(np.float64), 0.8), 0.0, 1.0)
    return rgb, alpha


# --- camera, raster ---------------------------------------------------------------------------------------------


def model_frame(mesh: dict[str, np.ndarray]) -> tuple[np.ndarray, float]:
    v = mesh["verts"]
    lo, hi = v.min(0), v.max(0)
    centre = (lo + hi) / 2
    scale = float(max(hi[1] - lo[1], np.hypot(hi[0] - lo[0], hi[2] - lo[2]))) * 1.06
    return centre, scale


class Raster:
    """One camera's view of the mesh: triangle map, depth, facing cosines, and the atlas position of every pixel."""

    def __init__(self, mesh: dict[str, np.ndarray], cam: face.Camera):
        self.cam = cam
        ras = face.view_raster(mesh, cam)
        self.mask = ras["tri"] >= 0
        self.z = ras["z"]
        m = self.mask
        n = face.interpolate(mesh["vnormals"], mesh["faces"], ras)
        n /= np.maximum(np.linalg.norm(n, axis=2, keepdims=True), 1e-12)
        self.cos = np.where(m, -(n @ cam.basis[2]), 0.0)
        v, f = mesh["verts"], mesh["faces"]
        fn = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
        fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
        self.fcos = np.where(m, -(fn[np.maximum(ras["tri"], 0)] @ cam.basis[2]), 0.0)
        self.uv = face.interpolate(face.uv_pixels(mesh["uv"], 1), mesh["faces"], ras)


def render_colour(ras: Raster, tex: np.ndarray, bg: float = BG) -> np.ndarray:
    T = tex.shape[0]
    img = np.full(ras.mask.shape + (3,), bg)
    uv = ras.uv[ras.mask] * T
    img[ras.mask] = face.sample(tex, uv[:, 0], uv[:, 1])
    return img


# --- fit: similarity transform of the picture onto the drawing -------------------------------------------------


def sim_matrix(s: float, rot: float, tx: float, ty: float, n: int) -> np.ndarray:
    """3x3 map from a drawing pixel p to a picture pixel q = c + R(rot)/s (p - c - t), c the frame centre."""
    c = n / 2.0
    cr, sr = math.cos(rot), math.sin(rot)
    R = np.array([[cr, -sr], [sr, cr]]) / s
    M = np.eye(3)
    M[:2, :2] = R
    M[:2, 2] = np.array([c, c]) - R @ np.array([c + tx, c + ty])
    return M


def warp_mask(pic: np.ndarray, M: np.ndarray, n: int) -> np.ndarray:
    ys, xs = np.mgrid[0:n, 0:n] + 0.5
    q = M @ np.stack([xs.ravel(), ys.ravel(), np.ones(n * n)])
    return face.sample(pic[..., None], q[0].reshape(n, n), q[1].reshape(n, n))[..., 0]


def iou(a: np.ndarray, b: np.ndarray) -> float:
    return float((a & b).sum()) / float(max((a | b).sum(), 1))


def fit_similarity(pic_mask: np.ndarray, render_mask: np.ndarray, n: int = FIT_N) -> dict[str, Any]:
    """Scale, shift and a few degrees of turn of the picture's cut-out onto the drawing's mask, by overlap (IoU), from
    the bounding boxes."""
    pm = small(pic_mask.astype(np.float64), n)
    rm = small(render_mask.astype(np.float64), n) > 0.5
    if not rm.any() or not (pm > 0.5).any():
        return {"s": 1.0, "rot": 0.0, "t": (0.0, 0.0), "iou": 0.0, "n": n}

    def bbox(m: np.ndarray) -> tuple[int, int, int, int]:
        ys, xs = np.where(m)
        return xs.min(), xs.max() + 1, ys.min(), ys.max() + 1

    px0, px1, py0, py1 = bbox(pm > 0.5)
    rx0, rx1, ry0, ry1 = bbox(rm)
    ratios = ((ry1 - ry0) / (py1 - py0), (rx1 - rx0) / (px1 - px0))
    s0 = 0.5 * (max(ratios) + min(ratios))
    c = n / 2.0
    tx0 = (rx0 + rx1) / 2 - c - s0 * ((px0 + px1) / 2 - c)
    ty0 = (ry0 + ry1) / 2 - c - s0 * ((py0 + py1) / 2 - c)

    def score(p: list[float]) -> float:
        return iou(warp_mask(pm, sim_matrix(p[0], p[1], p[2], p[3], n), n) > 0.5, rm)

    best = [s0, 0.0, tx0, ty0]
    bs = score(best)
    steps = [0.04 * s0, math.radians(2.0), 6.0, 6.0]
    for _ in range(40):
        improved = False
        for i in range(4):
            for d in (-1, 1):
                cand = list(best)
                cand[i] += d * steps[i]
                if (i == 0 and cand[0] <= 0.2) or abs(cand[1]) > math.radians(6):
                    continue
                sc = score(cand)
                if sc > bs + 1e-5:
                    best, bs, improved = cand, sc, True
        if not improved:
            steps = [x * 0.5 for x in steps]
            if steps[2] < 0.05:
                break
    return {"s": best[0], "rot": best[1], "t": (best[2], best[3]), "iou": bs, "n": n}


def sim_to_full(fit: dict[str, Any], pic_n: int, out_n: int) -> np.ndarray:
    n = fit["n"]
    M = sim_matrix(fit["s"], fit["rot"], fit["t"][0], fit["t"][1], n)
    return np.diag([pic_n / n, pic_n / n, 1.0]) @ M @ np.diag([n / out_n, n / out_n, 1.0])


# --- dense optical flow (multi-scale Horn-Schunck on structure channels) -----------------------------------------


def _grad(a: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    gx = np.zeros_like(a)
    gy = np.zeros_like(a)
    gx[:, 1:-1] = (a[:, 2:] - a[:, :-2]) / 2
    gy[1:-1] = (a[2:] - a[:-2]) / 2
    return gx, gy


def _avg(a: np.ndarray) -> np.ndarray:
    p = np.pad(a, 1, mode="edge")
    return (p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:]) / 4.0


def _hs_update(gx, gy, it, ubar, vbar, a2):  # noqa: ANN001
    sxx, sxy, syy = (gx * gx).sum(-1), (gx * gy).sum(-1), (gy * gy).sum(-1)
    b1 = a2 * ubar - (gx * it).sum(-1)
    b2 = a2 * vbar - (gy * it).sum(-1)
    det = (sxx + a2) * (syy + a2) - sxy * sxy
    return ((syy + a2) * b1 - sxy * b2) / det, ((sxx + a2) * b2 - sxy * b1) / det


def structure_channels(img: np.ndarray, mask: np.ndarray, s: float) -> np.ndarray:
    """Channels for matching a sharp picture with a soft, differently lit drawing: the luminance band between s and 4s
    over its local spread, and the smoothed silhouette."""
    lum = face.luminance(img)
    band = face.masked_blur(lum, mask, s) - face.masked_blur(lum, mask, 4 * s)
    spread = np.sqrt(np.maximum(face.masked_blur(band * band, mask, 6 * s), 1e-6))
    norm = np.clip(band / np.maximum(spread, 0.02), -3, 3) * 0.4 * mask
    return np.stack([norm, face.blur(mask.astype(np.float64), 1.5 * s)], axis=-1)


def hs_flow(ref: np.ndarray, mov: np.ndarray, region: np.ndarray, max_px: float, levels: int = 5, iters: int = 60,
            alpha: float = FLOW_ALPHA) -> np.ndarray:
    """Flow u (H,W,2) so that mov(p + u(p)) matches ref(p); coarse to fine, displacement clipped to max_px."""
    H, W = ref.shape[:2]
    u = None
    for lv in range(levels - 1, -1, -1):
        k = 2 ** lv
        h, w = max(H // k, 8), max(W // k, 8)
        m = np.stack([_rs(mov[..., c], h, w) for c in range(mov.shape[2])], -1)
        r = np.stack([_rs(ref[..., c], h, w) for c in range(ref.shape[2])], -1)
        reg = _rs(region.astype(np.float64), h, w)
        if u is None:
            u = np.zeros((h, w, 2))
        else:
            sc = w / u.shape[1]
            u = np.stack([_rs(u[..., 0], h, w), _rs(u[..., 1], h, w)], -1) * sc
        Y, X = np.mgrid[0:h, 0:w] + 0.5
        a2 = alpha ** 2
        for _outer in range(3):
            mw = face.sample(m, X + u[..., 0], Y + u[..., 1])
            gx, gy = np.zeros_like(mw), np.zeros_like(mw)
            for c in range(mw.shape[2]):
                gx[..., c], gy[..., c] = _grad(mw[..., c])
            it = mw - r
            du, dv = np.zeros((h, w)), np.zeros((h, w))
            for _ in range(iters):
                du, dv = _hs_update(gx, gy, it, _avg(du), _avg(dv), a2)
            u = u + np.stack([du, dv], -1) * reg[..., None]
            lim = max_px * (w / W)
            u = np.clip(u, -lim, lim)
        u = np.stack([face.blur(u[..., 0], 1.0 + 0.5 * lv), face.blur(u[..., 1], 1.0 + 0.5 * lv)], -1)
    return u


# --- one view ---------------------------------------------------------------------------------------------------


class View:
    def __init__(self, name: str, weight: float):
        self.name, self.weight = name, weight
        self.ok = False
        self.info: dict[str, Any] = {"name": name}


def fit_view(mesh: dict[str, np.ndarray], tex: np.ndarray, pic: np.ndarray, alpha: np.ndarray, name: str,
             yaws: list[float], centre: np.ndarray, scale: float, size: int = SIZE, min_iou: float = MIN_IOU,
             use_flow: bool = True, log: Callable[[str], None] | None = None) -> View:
    """Find the camera turn (among yaws), the picture's similarity fit and its flow warp; refuse what does not fit."""
    v = View(name, VIEW_WEIGHT.get(name, 0.5))
    n = min(FIT_N, size)
    pm = alpha > 0.5
    fits: dict[float, dict[str, Any]] = {}
    pic_s = small(pic, n)

    def try_yaw(yaw: float, nn: int = n) -> dict[str, Any]:
        if yaw not in fits or fits[yaw]["n"] != nn:
            rm = face.view_raster(mesh, face.Camera(target=centre, yaw_deg=float(yaw), scale=scale, size=nn))["tri"] >= 0
            f = fit_similarity(pm, rm, n=nn)
            f["rm"] = rm
            fits[yaw] = f
        return fits[yaw]

    if len(yaws) > 6:  # a wide search: a coarse pass at half size, then the best turns again at full size
        for yaw in yaws:
            try_yaw(float(yaw), n // 2)
        coarse_top = max(f["iou"] for f in fits.values())
        keep = sorted((y for y, f in fits.items() if f["iou"] >= coarse_top - 0.06), key=lambda y: -fits[y]["iou"])[:14]
        fits = {y: fits[y] for y in keep}
    for yaw in (fits if len(yaws) > 6 else yaws):
        try_yaw(float(yaw))
    if len(yaws) > 1:
        top = max(fits, key=lambda y: fits[y]["iou"])
        for d in (-5.0, -2.5, 2.5, 5.0):
            try_yaw(top + d)
    top_iou = max(f["iou"] for f in fits.values())
    pool = [y for y, f in fits.items() if f["iou"] >= top_iou - 0.03]
    best_y, best_d, best_corr = pool[0], 1e9, -1.0
    if len(pool) > 1:
        for y in pool:
            f = fits[y]
            rc = render_colour(Raster(mesh, face.Camera(target=centre, yaw_deg=float(y), scale=scale, size=n)), tex)
            M = sim_matrix(f["s"], f["rot"], f["t"][0], f["t"][1], n)
            ys_, xs_ = np.mgrid[0:n, 0:n] + 0.5
            ap = catmull(pic_s, M[0, 0] * xs_ + M[0, 1] * ys_ + M[0, 2], M[1, 0] * xs_ + M[1, 1] * ys_ + M[1, 2])
            both = f["rm"] & (warp_mask(small(pm.astype(np.float64), n), M, n) > 0.5)
            if both.sum() > 200:
                s_ = 0.008 * n
                la = face.to_lab(np.clip(face.masked_blur(rc, both, s_), 0, 1))
                lb = face.to_lab(np.clip(face.masked_blur(ap, both, s_), 0, 1))
                ld = float(np.linalg.norm(la - lb, axis=-1)[face.erode(both, 2)].mean())
                corr = face.feature_match(ap, rc, both, lo=1.0, hi=4.0)
                d = ld - 40.0 * corr  # colour and structure agreement together
            else:
                corr, d = -1.0, 1e9
            best_corr = max(best_corr, corr)
            if d < best_d:
                best_y, best_d = y, d
    yaw, fit = best_y, fits[best_y]
    ys_sorted = sorted(float(y) % 360 for y in pool)
    gaps = [(ys_sorted[(i + 1) % len(ys_sorted)] - ys_sorted[i]) % 360 for i in range(len(ys_sorted))]
    spread = 360.0 - max(gaps) if len(ys_sorted) > 1 else 0.0
    v.info.update(yaw=float(yaw), iou=round(float(fit["iou"]), 4), scale=round(float(fit["s"]), 4),
                  rot_deg=round(math.degrees(fit["rot"]), 2), pool_spread_deg=round(spread, 1))
    if spread > 90.0 and best_corr < 0.05:
        v.info["refused"] = f"the turn is ambiguous (the outline fits over {spread:.0f} degrees)"
    elif fit["iou"] < min_iou:
        v.info["refused"] = f"silhouette match {fit['iou']:.3f} is under {min_iou}"
    if "refused" in v.info:
        if log:
            log(f"view {name}: refused, {v.info['refused']}")
        return v
    cam = face.Camera(target=centre, yaw_deg=float(yaw), scale=scale, size=size)
    v.cam, v.raster = cam, Raster(mesh, cam)
    M = sim_to_full(fit, pic.shape[0], size)
    ys, xs = np.mgrid[0:size, 0:size] + 0.5
    qx, qy = M[0, 0] * xs + M[0, 1] * ys + M[0, 2], M[1, 0] * xs + M[1, 1] * ys + M[1, 2]
    a_aff = catmull(pic, qx, qy)
    al_aff = face.sample(alpha[..., None], qx, qy)[..., 0]
    rm = v.raster.mask
    v.render = render_colour(v.raster, tex)
    fn = min(FLOW_N, size)
    k = size // fn
    r_s, a_s = small(v.render, fn), small(a_aff, fn)
    m_s = small(rm.astype(np.float64), fn) > 0.5
    pm_s = small((al_aff > 0.5).astype(np.float64), fn) > 0.5
    both_s = m_s & pm_s
    sg = fn / 256.0 * 1.2
    flow_used, a_use, al_use = False, a_aff, al_aff
    if use_flow and both_s.any():
        u = hs_flow(structure_channels(r_s, m_s, sg), structure_channels(a_s, pm_s, sg), face.dilate_mask(m_s | pm_s, 6), MAX_FLOW * fn)
        u = np.stack([face.blur(u[..., 0], FLOW_SMOOTH), face.blur(u[..., 1], FLOW_SMOOTH)], -1)
        fold = 1.0  # no folding: scale the field down until its Jacobian determinant stays above 0.6 inside the figure
        for _ in range(8):
            dux, duy = _grad(u[..., 0] * fold)
            dvx, dvy = _grad(u[..., 1] * fold)
            if float((((1 + dux) * (1 + dvy) - duy * dvx)[both_s] < 0.6).mean()) < 0.002:
                break
            fold *= 0.8
        u = u * fold
        uf = np.stack([_rs(u[..., c], size, size) * k for c in range(2)], -1)
        qx2 = M[0, 0] * (xs + uf[..., 0]) + M[0, 1] * (ys + uf[..., 1]) + M[0, 2]
        qy2 = M[1, 0] * (xs + uf[..., 0]) + M[1, 1] * (ys + uf[..., 1]) + M[1, 2]
        a_flow = catmull(pic, qx2, qy2)
        inner = face.erode(small(rm.astype(np.float64), 1024) > 0.5, 3)
        fm0 = face.feature_match(small(a_aff, 1024), small(v.render, 1024), inner)
        fm1 = face.feature_match(small(a_flow, 1024), small(v.render, 1024), inner)
        v.info.update(feature_match_affine=fm0, feature_match_flow=fm1, flow_fold_scale=round(fold, 3))
        if fm1 >= fm0 - 0.005:
            flow_used, a_use = True, a_flow
            al_use = face.sample(alpha[..., None], qx2, qy2)[..., 0]
    v.info["flow_used"] = flow_used
    v.pic_aligned, v.pic_affine, v.alpha_img = a_use, a_aff, al_use
    v.valid = rm & (al_use > 0.5)
    r = max(2, size // 340)
    v.feather = np.clip(face.blur(face.erode(v.valid, r).astype(np.float64), r * 0.8) / 0.9, 0.0, 1.0)
    v.ok = True
    if log:
        log(f"view {name}: yaw {yaw}, iou {fit['iou']:.3f}, flow {flow_used}")
    return v


# --- the atlas side ---------------------------------------------------------------------------------------------


class Atlas:
    """Texels of the islands and GUTTER_PX texels around them, each with a surface position and normal (the border
    triangle's plane continued outward)."""

    def __init__(self, mesh: dict[str, np.ndarray], T: int, gutter: int = GUTTER_PX):
        self.T = T
        uvp = face.uv_pixels(mesh["uv"], T)
        ras = face.rasterise(uvp, np.zeros(len(mesh["uv"])), mesh["faces"], T)
        self.cover = ras["tri"] >= 0
        isl = np.flatnonzero(self.cover.ravel())
        pos = face.interpolate(mesh["verts"], mesh["faces"], ras)[self.cover]
        n = face.interpolate(mesh["vnormals"], mesh["faces"], ras)[self.cover]
        nrm = n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
        v, f = mesh["verts"], mesh["faces"]
        area = 0.5 * np.linalg.norm(np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]), axis=1).sum()
        self.pitch_m = math.sqrt(area / max(isl.size, 1))  # surface metres per texel
        self.is_gut = np.zeros(isl.size, dtype=bool)
        self.src = np.arange(isl.size)
        self.idx, self.pos, self.nrm = isl, pos, nrm
        self.cover_ext = self.cover
        if gutter > 0 and isl.size:
            tri = np.where(self.cover, ras["tri"], -1).astype(np.float64)
            Y, X = np.mgrid[0:T, 0:T].astype(np.float64)
            gtri = imaging.dilate(tri, self.cover, gutter)
            gx = imaging.dilate(X, self.cover, gutter)
            gy = imaging.dilate(Y, self.cover, gutter)
            gi = np.flatnonzero(((gtri >= 0) & ~self.cover).ravel())
            t = gtri.ravel()[gi].astype(np.int64)
            p = np.stack([(gi % T) + 0.5, (gi // T) + 0.5], 1)
            tv = uvp[f[t]]
            d1, d2 = tv[:, 1] - tv[:, 0], tv[:, 2] - tv[:, 0]
            det = d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]
            ok = np.abs(det) > 1e-9
            safe = np.where(ok, det, 1)
            rel = p - tv[:, 0]
            b1 = (rel[:, 0] * d2[:, 1] - rel[:, 1] * d2[:, 0]) / safe
            b2 = (d1[:, 0] * rel[:, 1] - d1[:, 1] * rel[:, 0]) / safe
            b0 = 1 - b1 - b2
            gpos = b0[:, None] * v[f[t, 0]] + b1[:, None] * v[f[t, 1]] + b2[:, None] * v[f[t, 2]]
            vn = mesh["vnormals"]
            gn = b0[:, None] * vn[f[t, 0]] + b1[:, None] * vn[f[t, 1]] + b2[:, None] * vn[f[t, 2]]
            gn /= np.maximum(np.linalg.norm(gn, axis=1, keepdims=True), 1e-12)
            lut = np.full(T * T, -1, dtype=np.int64)
            lut[isl] = np.arange(isl.size)
            srcflat = gy.ravel()[gi].astype(np.int64) * T + gx.ravel()[gi].astype(np.int64)
            keep = ok & (lut[srcflat] >= 0)
            self.idx = np.concatenate([isl, gi[keep]])
            self.pos = np.concatenate([pos, gpos[keep]])
            self.nrm = np.concatenate([nrm, gn[keep]])
            self.is_gut = np.concatenate([self.is_gut, np.ones(int(keep.sum()), dtype=bool)])
            self.src = np.concatenate([self.src, lut[srcflat][keep]])
            ext = self.cover.copy().reshape(-1)
            ext[gi[keep]] = True
            self.cover_ext = ext.reshape(T, T)


def cos_weight(cosv: np.ndarray) -> np.ndarray:
    """cos^4 of the angle between surface and view, zero past 75 degrees (feathered over the next 0.12 of cosine)."""
    return np.clip(cosv, 0.0, 1.0) ** 4 * face.smoothstep(COS_CUT, COS_CUT + 0.12, cosv)


def project_view(atlas: Atlas, v: View, size: int) -> dict[str, np.ndarray]:
    """The texels this view sees squarely: index into atlas.idx, screen position, weight."""
    cam = v.cam
    sp, sd = cam.project(atlas.pos)
    xi = np.clip(sp[:, 0].astype(int), 0, size - 1)
    yi = np.clip(sp[:, 1].astype(int), 0, size - 1)
    cosv = -(atlas.nrm @ cam.basis[2])
    tol = 2.5 * (cam.scale / size) / np.maximum(np.abs(cosv), 0.15)
    inb = (sp[:, 0] >= 0) & (sp[:, 0] < size) & (sp[:, 1] >= 0) & (sp[:, 1] < size)
    visible = inb & (sd <= v.raster.z[yi, xi] + tol) & (v.raster.fcos[yi, xi] > 0.05)
    feather = face.sample(v.feather[..., None], sp[:, 0], sp[:, 1])[:, 0]
    w = v.weight * cos_weight(cosv) * feather * visible
    keep = np.flatnonzero(w > 1e-5)
    return {"sel": keep, "sp": sp[keep], "w": w[keep]}


def detail_only(v: View, sigma_px: float) -> np.ndarray:
    """The view's picture with its broad colour and tone (below sigma_px) taken from the TRELLIS drawing: the drawing's
    low frequencies plus the picture's high frequencies, masked so the backdrop does not bleed in."""
    lo_r = face.masked_blur(v.render, v.valid, sigma_px)
    lo_p = face.masked_blur(v.pic_aligned, v.valid, sigma_px)
    return np.clip(lo_r + v.pic_aligned - lo_p, 0.0, 1.0)


def blend(atlas: Atlas, projs: list[dict[str, np.ndarray]], images: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Weighted blend of the views (the dominant one counts as weight^BLEND_POW; the turnaround views fade out where the
    picture sees well). Returns (paint (N,3), alpha (N), dominant view per texel, -1 for none)."""
    N = atlas.idx.size
    num, den, wmax, dom = np.zeros((N, 3)), np.zeros(N), np.zeros(N), np.full(N, -1)
    w0 = np.zeros(N)
    w0[projs[0]["sel"]] = projs[0]["w"]
    suppress = 1.0 - np.clip(w0 / FRONT_PRIORITY, 0.0, 1.0)
    for i, (p, img) in enumerate(zip(projs, images)):
        sel, w = p["sel"], p["w"]
        if i > 0:
            w = w * suppress[sel]
        col = catmull(img, p["sp"][:, 0].reshape(-1, 1), p["sp"][:, 1].reshape(-1, 1))[:, 0]
        ww = w ** BLEND_POW
        num[sel] += col * ww[:, None]
        den[sel] += ww
        better = w > wmax[sel]
        dom[sel[better]] = i
        wmax[sel] = np.maximum(wmax[sel], w)
    return num / np.maximum(den, 1e-12)[:, None], face.smoothstep(ALPHA_RAMP[0], ALPHA_RAMP[1], wmax), dom


def surface_correction(atlas: Atlas, delta: np.ndarray, alpha: np.ndarray, cell: float = 0.012, passes: int = 3,
                       r: int = 2) -> tuple[np.ndarray, np.ndarray]:
    """A smooth, surface-following version of delta (the colour offset of the painted texels), carried to every texel and
    fading out with distance from painted surface. Returns (correction (N,3), reach (N) 0..1)."""
    pos = atlas.pos
    lo = pos.min(0) - 3 * cell
    f = (pos - lo) / cell
    idx = np.floor(f).astype(int)
    dims = tuple(int(d) for d in idx.max(0) + 4)
    size = int(np.prod(dims))
    flat = np.ravel_multi_index(tuple(idx.T), dims)
    sd = np.stack([np.bincount(flat, weights=delta[:, c] * alpha, minlength=size).reshape(dims) for c in range(3)], -1)
    sa = np.bincount(flat, weights=alpha, minlength=size).reshape(dims)
    sn = np.bincount(flat, minlength=size).astype(np.float64).reshape(dims)
    for _ in range(passes):
        sd = np.stack([face._box3(sd[..., c], r) for c in range(3)], -1)
        sa, sn = face._box3(sa, r), face._box3(sn, r)
    g = sd / np.maximum(sa, 1e-9)[..., None]
    frac = sa / np.maximum(sn, 1e-9)
    fc = f - 0.5
    corr = np.stack([face._trilinear(g[..., c], fc) for c in range(3)], -1)
    return corr, np.clip(face.smoothstep(0.03, 0.4, face._trilinear(frac, fc)), 0, 1)


def finish(atlas: Atlas, base: np.ndarray, paint: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """The new atlas: painted texels take the painted colour; the others keep base with its low frequencies shifted to agree
    with the painted colour near them; the islands are then grown into the gutter."""
    T = atlas.T
    if atlas.is_gut.any():  # a border texel the views do not reach takes its island neighbour's painted colour
        need = atlas.is_gut & (alpha < 0.5) & (alpha[atlas.src] >= 0.5)
        paint, alpha = paint.copy(), alpha.copy()
        paint[need] = paint[atlas.src[need]]
        alpha[need] = alpha[atlas.src[need]]
    kept = base.reshape(-1, 3)[atlas.idx]
    out = kept
    sel = alpha > 0.5
    if sel.sum() > 100:
        corr, reach = surface_correction(atlas, np.where(sel[:, None], paint - kept, 0.0), np.where(sel, alpha, 0.0))
        out = np.clip(kept + corr * reach[:, None], 0, 1)
    out = out * (1 - alpha[:, None]) + paint * alpha[:, None]
    res = base.copy().reshape(-1, 3)
    res[atlas.idx] = np.clip(out, 0, 1)
    return imaging.dilate(res.reshape(T, T, 3), atlas.cover_ext, imaging.dilation_px(T))


# --- measures ---------------------------------------------------------------------------------------------------


def _lap_var(img: np.ndarray, mask: np.ndarray) -> float:
    lum = face.luminance(img)
    lap = 4 * lum - np.roll(lum, 1, 0) - np.roll(lum, -1, 0) - np.roll(lum, 1, 1) - np.roll(lum, -1, 1)
    inner = face.erode(mask, 4)
    return float(lap[inner].var()) if inner.sum() > 50 else 0.0


def front_measures(v: View, tex: np.ndarray) -> dict[str, float]:
    """The picture's view of the model against the picture (fitted, not warped): low-pass colour difference in Lab, and
    the Laplacian-variance sharpness of the drawing over the picture's (1 is as sharp as the picture)."""
    rend = render_colour(v.raster, tex)
    mask = face.erode(small((v.valid & (v.alpha_img > 0.5)).astype(np.float64), 1024) > 0.5, 6)
    r1, p1 = small(rend, 1024), small(v.pic_affine, 1024)
    if mask.sum() < 100:
        return {"lab_diff": float("nan"), "sharp_ratio": float("nan")}
    s = 0.008 * 1024
    la = face.to_lab(np.clip(face.masked_blur(r1, mask, s), 0, 1))
    lb = face.to_lab(np.clip(face.masked_blur(p1, mask, s), 0, 1))
    return {"lab_diff": round(float(np.linalg.norm(la - lb, axis=-1)[mask].mean()), 3),
            "sharp_ratio": round(_lap_var(r1, mask) / max(_lap_var(p1, mask), 1e-12), 4)}


# --- the pass ---------------------------------------------------------------------------------------------------


def paint(mesh: dict[str, np.ndarray], tex: np.ndarray, picture: Any, turnaround: dict[str, Any] | None = None, *,
          mode: str = "detail", size: int = SIZE, picture_yaws: list[float] | None = None, min_iou: float = MIN_IOU,
          log: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Paint the picture (and the turnaround views, mode "full" or "detail"; "off" leaves them out) into tex (T,T,3) through
    the mesh's UVs. Returns {applied, base_color (T,T,3), reason, metrics}. Nothing is applied when the picture does not
    fit the model, or when the result is no closer to the picture than what the model had."""
    t0 = time.time()
    res: dict[str, Any] = {"applied": False, "base_color": tex, "reason": None, "metrics": {}}
    centre, scale = model_frame(mesh)
    pic, alpha = prepare_picture(picture)
    front = fit_view(mesh, tex, pic, alpha, "picture", picture_yaws or PICTURE_YAWS, centre, scale, size, min_iou, log=log)
    views = [front]
    infos = {"picture": front.info}
    if not front.ok:
        res["reason"] = front.info.get("refused", "the picture was refused")
        res["metrics"] = {"srcproject_views": infos, "srcproject_seconds": round(time.time() - t0, 1)}
        return res
    if mode != "off":
        for name in TURN_VIEWS:
            src = (turnaround or {}).get(name)
            if src is None:
                continue
            tp, ta = prepare_picture(src)
            vw = fit_view(mesh, tex, tp, ta, name, TURN_YAWS[name], centre, scale, size, min_iou, log=log)
            infos[name] = vw.info
            if vw.ok:
                views.append(vw)
    atlas = Atlas(mesh, tex.shape[0])
    projs = [project_view(atlas, v, size) for v in views]
    pitch_px = atlas.pitch_m * size / scale
    images = [views[0].pic_aligned]
    for v in views[1:]:
        images.append(v.pic_aligned if mode == "full" else detail_only(v, DETAIL_TEXELS * pitch_px))
    paint_, alpha_, dom = blend(atlas, projs, images)
    new = finish(atlas, tex, paint_, alpha_)
    before, after = front_measures(front, tex), front_measures(front, new)
    painted = alpha_ > 0.5
    m = {"srcproject_views": infos, "srcproject_mode": mode, "srcproject_painted_share": round(float(painted[~atlas.is_gut].mean()), 4),
         "srcproject_share_by_view": {views[i].name: round(float(((dom == i) & painted)[~atlas.is_gut].mean()), 4) for i in range(len(views))},
         "srcproject_front_before": before, "srcproject_front_after": after,
         "srcproject_texel_pitch_mm": round(atlas.pitch_m * 1000, 3), "srcproject_seconds": round(time.time() - t0, 1)}
    res["metrics"] = m
    worse = after["lab_diff"] == after["lab_diff"] and before["lab_diff"] == before["lab_diff"] and after["lab_diff"] > before["lab_diff"] - 0.5
    if worse:
        res["reason"] = "the result is no closer to the picture than the model's own colour"
        return res
    res.update(applied=True, base_color=new)
    return res


def apply_files(glb: Path | str, base_color: Path | str, picture: Path | str, turnaround: dict[str, Path | None] | None,
                out_png: Path | str, *, mode: str = "detail", size: int = SIZE, log: Callable[[str], None] | None = None) -> dict[str, Any]:
    """paint() on files: reads the model and its base colour, writes the new base colour to out_png when it applies.
    Returns {applied, reason, base_color (the path to use), metrics}."""
    mesh = face.load_mesh(glb)
    if mesh is None:
        return {"applied": False, "reason": "the model has no texture layout", "base_color": Path(base_color), "metrics": {}}
    tex = face.read_rgb(base_color)
    r = paint(mesh, tex, picture, {k: p for k, p in (turnaround or {}).items() if p is not None and Path(p).is_file()},
              mode=mode, size=size, log=log)
    path = Path(base_color)
    if r["applied"]:
        path = face.save_rgb(r["base_color"], out_png)
    return {"applied": r["applied"], "reason": r["reason"], "base_color": path, "metrics": r["metrics"]}


# --- the prep stage's call ----------------------------------------------------------------------------------------

PARAM = "source_project"
MODES = ("detail", "full", "off")


def on(params: dict[str, Any]) -> bool:
    """The pass is off unless the version asks for it (it has not been turned on by default)."""
    return bool(params.get(PARAM))


def mode_of(params: dict[str, Any]) -> str:
    m = params.get("source_project_mode")
    return m if m in MODES else "detail"


async def run_stage(sc: Any, glb: Path, current: dict[str, Path], params: dict[str, Any]) -> dict[str, Path]:
    """The pass inside the prep stage (stages/prep.py), after the last rebake and before the face pass: paints the picture
    the 3D step was given, and the turnaround views, into the current base colour; replaces it in the GLB and in
    `current` when it applies, and records the before and after numbers in the stage metrics. Returns `current`."""
    import asyncio

    mode = mode_of(params)
    sc.set_params(**{PARAM: True, "source_project_mode": mode})
    base = current.get("base_color")
    picture = sc.lineage_file("input", ("mesh",)) or sc.lineage_file("cutout", ("concept",))
    if base is None or picture is None:
        sc.set_metrics(srcproject_applied=False, srcproject_reason="no base colour or no picture to paint from")
        return current
    turn = {v: sc.lineage_file(f"{v}_cutout", ("turnaround",)) for v in TURN_VIEWS}
    work = sc.dir / "srcproject"
    work.mkdir(parents=True, exist_ok=True)
    try:
        r = await asyncio.to_thread(apply_files, glb, base, picture, turn, work / "base_color.png", mode=mode)
    except Exception as e:  # noqa: BLE001  (the pass never fails a run: the colour stays as it was)
        sc.set_metrics(srcproject_applied=False, srcproject_reason=f"failed: {str(e)[:200]}")
        return current
    sc.set_metrics(**r["metrics"], srcproject_applied=bool(r["applied"]), srcproject_reason=r["reason"])
    if r["applied"]:
        await asyncio.to_thread(imaging.glb_replace_images, glb, base_color=r["base_color"])
        sc.register("base_color", r["base_color"])
        current = {**current, "base_color": r["base_color"]}
    return current
