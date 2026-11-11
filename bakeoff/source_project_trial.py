"""Texture fidelity lane B, stage 1: project the source picture (and the turnaround views) into the final atlas.

CPU only, numpy + Pillow (no scipy, no OpenCV in backend/.venv, so the flow and the solver are plain numpy).

For each subject (bake-off build under bakeoff/out/convert, its picture, its turnaround views):
1. cameras: orthographic, the model's bounds framed, yaw fitted per picture by silhouette IoU;
2. fit: picture cutout onto the render by scale, shift and small rotation (silhouette IoU); refuse under 0.85;
3. warp: dense multi-scale optical flow of the picture onto the base-colour render (the TRELLIS colour is the target),
   capped to a few percent of the frame and smoothed;
4. project through the existing UVs: depth test, weight = view weight x cos^4, cut off past ~75 degrees, feathered;
5. variants: V1 blend, V2 scaffold (turnaround detail only), V3 solve (sparse least squares, matrix-free CG);
6. fallback texels keep their colour with the low frequencies matched to the projected colour (3D grid), then dilated.

Usage: backend/.venv/Scripts/python.exe bakeoff/source_project_trial.py [subject ...] [--variants v1,v2a,v2b,v3] [--no-turn]
Output: bakeoff/out/source_project/<subject>/ (atlases, sheets, report.json) and results.md
"""
from __future__ import annotations

import json
import math
import os
import sys
import time

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
sys.path.insert(0, HERE)
from vgac import face, imaging  # noqa: E402

from repaint_trial import real_detail_db, uv_cover  # noqa: E402

OUT = os.path.join(HERE, "out", "source_project")
CONVERT = os.path.join(HERE, "out", "convert")
TURN = os.path.join(HERE, "out", "turnaround")
SUBJECTS = {
    "knight": ("humanoid_20000", "humanoid"),
    "wolf": ("quadruped_20000", "quadruped"),
    "chest": ("prop_5000", "prop"),
}
S = 2048                 # working render size (the picture is 1024: Catmull-Rom upsampled once, in the final resample)
FIT_S = 512              # size the fit and the flow run at (flow is upsampled)
MIN_IOU = 0.85
COS_CUT = math.cos(math.radians(75.0))
MAX_FLOW = 0.02          # of the frame
FLOW_ALPHA = 2.0         # Horn-Schunck smoothness weight (higher: stiffer field)
FLOW_SMOOTH = 8.0        # final Gaussian sigma of the field, in pixels at FIT_S
VIEW_WEIGHT = {"front": 1.0, "quarter": 0.5, "left": 0.5, "right": 0.5, "back": 0.5}
BG = 0.5
TAG = ""               # output folder suffix (--tag)


# ---------------------------------------------------------------------------------------------------------------
# image helpers

def catmull(img: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Bicubic (Catmull-Rom) sample of img (H,W,c) at pixel coordinates (centres at +0.5); edge clamped."""
    h, w = img.shape[:2]
    fx, fy = x - 0.5, y - 0.5
    x0, y0 = np.floor(fx).astype(int), np.floor(fy).astype(int)
    tx, ty = (fx - x0)[..., None], (fy - y0)[..., None]

    def wts(t):
        t2, t3 = t * t, t * t * t
        return [(-t3 + 2 * t2 - t) / 2, (3 * t3 - 5 * t2 + 2) / 2, (-3 * t3 + 4 * t2 + t) / 2, (t3 - t2) / 2]

    wx, wy = wts(tx), wts(ty)
    out = 0.0
    for j in range(4):
        yy = np.clip(y0 + j - 1, 0, h - 1)
        row = 0.0
        for i in range(4):
            xx = np.clip(x0 + i - 1, 0, w - 1)
            row = row + wx[i] * img[yy, xx]
        out = out + wy[j] * row
    return out


def small(a: np.ndarray, n: int) -> np.ndarray:
    return face.resize(a, n, Image.BOX if a.shape[0] >= n else Image.BILINEAR)


def ssim(a: np.ndarray, b: np.ndarray, mask: np.ndarray, sigma: float = 1.5) -> float:
    """Mean SSIM of two luminance images inside mask (Gaussian-ish window by repeated box blur)."""
    c1, c2 = 0.01 ** 2, 0.03 ** 2
    mu_a, mu_b = face.blur(a, sigma), face.blur(b, sigma)
    saa = face.blur(a * a, sigma) - mu_a ** 2
    sbb = face.blur(b * b, sigma) - mu_b ** 2
    sab = face.blur(a * b, sigma) - mu_a * mu_b
    m = ((2 * mu_a * mu_b + c1) * (2 * sab + c2)) / ((mu_a ** 2 + mu_b ** 2 + c1) * (saa + sbb + c2))
    inner = face.erode(mask, 4)
    return float(m[inner].mean()) if inner.sum() > 50 else float("nan")


def lap_var(img: np.ndarray, mask: np.ndarray) -> float:
    lum = face.luminance(img)
    lap = (4 * lum - np.roll(lum, 1, 0) - np.roll(lum, -1, 0) - np.roll(lum, 1, 1) - np.roll(lum, -1, 1))
    inner = face.erode(mask, 4)
    return float(lap[inner].var()) if inner.sum() > 50 else 0.0


def lowpass_lab_diff(a: np.ndarray, b: np.ndarray, mask: np.ndarray, sigma_frac: float = 0.008) -> float:
    s = sigma_frac * a.shape[0]
    la = face.to_lab(np.clip(face.masked_blur(a, mask, s), 0, 1))
    lb = face.to_lab(np.clip(face.masked_blur(b, mask, s), 0, 1))
    inner = face.erode(mask, 6)
    return float(np.linalg.norm(la - lb, axis=-1)[inner].mean())


# ---------------------------------------------------------------------------------------------------------------
# camera, raster

def model_frame(mesh: dict) -> tuple[np.ndarray, float]:
    v = mesh["verts"]
    lo, hi = v.min(0), v.max(0)
    centre = (lo + hi) / 2
    scale = float(max(hi[1] - lo[1], np.hypot(hi[0] - lo[0], hi[2] - lo[2]))) * 1.06
    return centre, scale


class Raster:
    """One camera's view of the mesh: triangle map, barycentrics, depth, position, normal, facing cosine."""

    def __init__(self, mesh: dict, cam: face.Camera):
        self.cam = cam
        ras = face.view_raster(mesh, cam)
        self.ras = ras
        self.mask = ras["tri"] >= 0
        self.z = ras["z"]
        m = self.mask
        n = face.interpolate(mesh["vnormals"], mesh["faces"], ras)
        n /= np.maximum(np.linalg.norm(n, axis=2, keepdims=True), 1e-12)
        self.cos = np.where(m, -(n @ cam.basis[2]), 0.0)
        # face normals for grazing tests (smooth normals can hide a back-facing triangle at the silhouette)
        v, f = mesh["verts"], mesh["faces"]
        fn = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
        fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
        self.fcos = np.where(m, -(fn[np.maximum(ras["tri"], 0)] @ cam.basis[2]), 0.0)
        tex_px = face.uv_pixels(mesh["uv"], 1)  # uv in 0..1 with rows flipped; scaled per atlas later
        self.uv = face.interpolate(tex_px, mesh["faces"], ras)  # (S,S,2) in 0..1 of the atlas


def render_colour(mesh: dict, ras: Raster, tex: np.ndarray, bg: float = BG) -> np.ndarray:
    T = tex.shape[0]
    img = np.full(ras.mask.shape + (3,), bg)
    uv = ras.uv[ras.mask] * T
    img[ras.mask] = face.sample(tex, uv[:, 0], uv[:, 1])
    return img


# ---------------------------------------------------------------------------------------------------------------
# fit: similarity transform picture -> render frame by silhouette

def sim_matrix(s: float, rot: float, tx: float, ty: float, n: int) -> np.ndarray:
    """Map render-frame pixel p to picture pixel q: q = c + R(rot)/s (p - c - t), c = frame centre. 3x3."""
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


def fit_similarity(pic_mask: np.ndarray, render_mask: np.ndarray, n: int = 256) -> dict:
    """Scale, shift, small in-plane rotation (+-6 deg) of the picture's cutout onto the render mask, by IoU.
    pic_mask is in the picture frame (square, any size); both are reduced to n x n. Start from the bounding boxes."""
    pm = small(pic_mask.astype(np.float64), n)
    rm = render_mask_small = small(render_mask.astype(np.float64), n) > 0.5

    def bbox(m):
        ys, xs = np.where(m)
        return xs.min(), xs.max() + 1, ys.min(), ys.max() + 1

    px0, px1, py0, py1 = bbox(pm > 0.5)
    rx0, rx1, ry0, ry1 = bbox(rm)
    s0 = max((ry1 - ry0) / (py1 - py0), (rx1 - rx0) / (px1 - px0)) * 0.5 + min((ry1 - ry0) / (py1 - py0), (rx1 - rx0) / (px1 - px0)) * 0.5
    # picture bbox centre -> render bbox centre
    cx_p, cy_p = (px0 + px1) / 2, (py0 + py1) / 2
    cx_r, cy_r = (rx0 + rx1) / 2, (ry0 + ry1) / 2
    c = n / 2.0
    # q = c + (p - c - t)/s  with q = picture centre when p = render centre -> t = p_c - c - s (q_c - c)
    tx0 = cx_r - c - s0 * (cx_p - c)
    ty0 = cy_r - c - s0 * (cy_p - c)

    def score(p):
        s, rot, tx, ty = p
        w = warp_mask(pm, sim_matrix(s, rot, tx, ty, n), n) > 0.5
        return iou(w, rm)

    best = [s0, 0.0, tx0, ty0]
    bs = score(best)
    steps = [0.04 * s0, math.radians(2.0), 6.0, 6.0]
    for _ in range(40):
        improved = False
        for i in range(4):
            for d in (-1, 1):
                cand = list(best)
                cand[i] += d * steps[i]
                if i == 0 and cand[0] <= 0.2:
                    continue
                if abs(cand[1]) > math.radians(6):
                    continue
                sc = score(cand)
                if sc > bs + 1e-5:
                    best, bs, improved = cand, sc, True
        if not improved:
            steps = [x * 0.5 for x in steps]
            if steps[2] < 0.05:
                break
    k = 1.0
    return {"s": best[0], "rot": best[1], "t": (best[2], best[3]), "iou": bs, "n": n, "k": k}


def sim_to_full(fit: dict, pic_n: int, out_n: int) -> np.ndarray:
    """The fit was found at fit['n'] pixels (both frames reduced alike); give the 3x3 map render(out_n) -> picture(pic_n)."""
    n = fit["n"]
    M = sim_matrix(fit["s"], fit["rot"], fit["t"][0], fit["t"][1], n)  # fit-render px -> fit-picture px
    A = np.diag([n / out_n, n / out_n, 1.0])   # out px -> fit-render px
    B = np.diag([pic_n / n, pic_n / n, 1.0])   # fit-picture px -> picture px
    return B @ M @ A


# ---------------------------------------------------------------------------------------------------------------
# dense optical flow (multi-scale Horn-Schunck on structure channels)

def _grad(a: np.ndarray):
    gx = np.zeros_like(a)
    gy = np.zeros_like(a)
    gx[:, 1:-1] = (a[:, 2:] - a[:, :-2]) / 2
    gy[1:-1] = (a[2:] - a[:-2]) / 2
    return gx, gy


def _bilin(a: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return face.sample(a, x, y)


def structure_channels(img: np.ndarray, mask: np.ndarray, s: float) -> np.ndarray:
    """Illumination-insensitive channels for matching a sharp picture with a soft, differently lit render: the
    luminance band between s and 4s, divided by its local spread; plus the smoothed silhouette."""
    lum = face.luminance(img)
    band = face.masked_blur(lum, mask, s) - face.masked_blur(lum, mask, 4 * s)
    spread = np.sqrt(np.maximum(face.masked_blur(band * band, mask, 6 * s), 1e-6))
    norm = np.clip(band / np.maximum(spread, 0.02), -3, 3) * 0.4 * mask
    sil = face.blur(mask.astype(np.float64), 1.5 * s) * 1.0
    return np.stack([norm, sil], axis=-1)


def hs_flow(ref: np.ndarray, mov: np.ndarray, region: np.ndarray, max_px: float, levels: int = 5, iters: int = 60, alpha: float = 0.3) -> np.ndarray:
    """Flow u (H,W,2) so that mov(p + u(p)) matches ref(p). ref, mov (H,W,c). Coarse-to-fine Horn-Schunck with
    iterative warping; the displacement is clipped to max_px (at full size) and smoothed."""
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
            mw = _bilin(m, X + u[..., 0], Y + u[..., 1])
            gx = np.zeros_like(mw)
            gy = np.zeros_like(mw)
            for c in range(mw.shape[2]):
                gx[..., c], gy[..., c] = _grad(mw[..., c])
            it = mw - r
            du = np.zeros((h, w))
            dv = np.zeros((h, w))
            for _ in range(iters):
                du, dv = _hs_update(gx, gy, it, _avg(du), _avg(dv), a2)
            u = u + np.stack([du, dv], -1) * reg[..., None]
            lim = max_px * (w / W)
            u = np.clip(u, -lim, lim)
        u = np.stack([face.blur(u[..., 0], 1.0 + 0.5 * lv), face.blur(u[..., 1], 1.0 + 0.5 * lv)], -1)
    return u


def _rs(a: np.ndarray, h: int, w: int) -> np.ndarray:
    if a.shape == (h, w):
        return a
    return np.asarray(Image.fromarray(a.astype(np.float32), "F").resize((w, h), Image.BILINEAR), dtype=np.float64)


def _avg(a: np.ndarray) -> np.ndarray:
    p = np.pad(a, 1, mode="edge")
    return (p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:]) / 4.0


def _hs_update(gx, gy, it, ubar, vbar, a2):
    """Jacobi step of Horn-Schunck with channel-summed data term: minimise sum_c (gx du + gy dv + it)^2 + a2 |grad|^2
    per pixel given the neighbour mean (ubar, vbar)."""
    sxx = (gx * gx).sum(-1)
    sxy = (gx * gy).sum(-1)
    syy = (gy * gy).sum(-1)
    sxt = (gx * it).sum(-1)
    syt = (gy * it).sum(-1)
    # (sxx + a2) u + sxy v = a2 ubar - sxt ;  sxy u + (syy + a2) v = a2 vbar - syt
    b1 = a2 * ubar - sxt
    b2 = a2 * vbar - syt
    det = (sxx + a2) * (syy + a2) - sxy * sxy
    u = ((syy + a2) * b1 - sxy * b2) / det
    v = ((sxx + a2) * b2 - sxy * b1) / det
    return u, v


# ---------------------------------------------------------------------------------------------------------------
# views: fit, flow, aligned picture

class View:
    def __init__(self, name: str, weight: float):
        self.name, self.weight = name, weight
        self.ok = False
        self.info: dict = {}


def load_picture(rgb_path: str, alpha_path: str | None, n: int = 1024) -> tuple[np.ndarray, np.ndarray]:
    """Picture colour (n,n,3) and cutout alpha. With alpha_path None the cutout comes from the picture itself (anything
    that is not the black backdrop, closed and with holes filled): the bake-off's concept cutouts are not always the
    picture the 3D step was given. Edges are defringed: colour is pulled out from 2 px inside the cutout."""
    rgb = np.asarray(Image.open(rgb_path).convert("RGB"), dtype=np.float64) / 255.0
    if rgb.shape[0] != n:
        rgb = face.resize(rgb, n, Image.LANCZOS)
    if alpha_path is None:
        nm = rgb.max(2) > 0.035
        alpha = face.fill_holes(face.erode(face.dilate_mask(nm, 3), 3)).astype(np.float64)
    else:
        alpha = np.asarray(Image.open(alpha_path).convert("RGBA").split()[3], dtype=np.float64) / 255.0
        if alpha.shape[0] != n:
            alpha = face.resize(alpha, n, Image.BILINEAR)
    core = face.erode(alpha > 0.5, 2)
    rgb = imaging.dilate(rgb, core, 8)
    alpha = np.clip(face.blur(face.dilate_mask(core, 3).astype(np.float64), 0.8), 0.0, 1.0)
    return rgb, alpha


def fit_view(mesh: dict, tex: np.ndarray, pic: np.ndarray, alpha: np.ndarray, name: str, yaws: list[float],
             centre: np.ndarray, scale: float, flow: bool = True, log=print) -> View:
    v = View(name, VIEW_WEIGHT.get(name.split("_")[0], 0.5))
    pm = alpha > 0.5
    n = 256
    tried = []
    fits: dict[float, dict] = {}
    pic_s = small(pic, n)

    def try_yaw(yaw: float) -> dict:
        if yaw not in fits:
            cam = face.Camera(target=centre, yaw_deg=float(yaw), scale=scale, size=n)
            rm = face.view_raster(mesh, cam)["tri"] >= 0
            f = fit_similarity(pm, rm, n=n)
            f["rm"] = rm
            fits[yaw] = f
            tried.append((yaw, round(f["iou"], 3)))
        return fits[yaw]

    for yaw in yaws:
        try_yaw(float(yaw))
    top = max(fits, key=lambda y: fits[y]["iou"])
    for d in (-5.0, -2.5, 2.5, 5.0):
        try_yaw(top + d)
    top_iou = max(f["iou"] for f in fits.values())
    # a boxy or round subject fits the outline at several turns: among those within 0.03 of the best, the one whose
    # low-pass colour agrees best with the TRELLIS render wins
    pool = [y for y, f in fits.items() if f["iou"] >= top_iou - 0.03]
    best_y, best_d = None, 1e9
    table = []
    for y in pool:
        f = fits[y]
        cam = Raster(mesh, face.Camera(target=centre, yaw_deg=float(y), scale=scale, size=n))
        rc = render_colour(mesh, cam, tex)
        M = sim_matrix(f["s"], f["rot"], f["t"][0], f["t"][1], n)
        ys_, xs_ = np.mgrid[0:n, 0:n] + 0.5
        qx = M[0, 0] * xs_ + M[0, 1] * ys_ + M[0, 2]
        qy = M[1, 0] * xs_ + M[1, 1] * ys_ + M[1, 2]
        ap = catmull(pic_s, qx, qy)
        both = f["rm"] & (warp_mask(small(pm.astype(np.float64), n), M, n) > 0.5)
        if both.sum() > 200:
            ld = lowpass_lab_diff(rc, ap, both)
            corr = face.feature_match(ap, rc, both, lo=1.0, hi=4.0)
            d = ld - 40.0 * corr          # colour agreement and structure agreement together
        else:
            ld, corr, d = 1e9, -1.0, 1e9
        table.append((y, round(fits[y]["iou"], 3), round(ld, 2), round(corr, 3)))
        if d < best_d:
            best_y, best_d = y, d
    best = (best_y, fits[best_y])
    # ambiguity guard: when the outline fits at turns that are far apart (a box, a ball) and nothing in the colour or the
    # structure tells them apart, the turn is a guess and a wrong guess paints features twice; refuse the view
    ys_sorted = sorted(float(y) % 360 for y in pool)
    gaps = [(ys_sorted[(i + 1) % len(ys_sorted)] - ys_sorted[i]) % 360 for i in range(len(ys_sorted))]
    spread = 360.0 - max(gaps) if len(ys_sorted) > 1 else 0.0
    best_corr = max((r[3] for r in table), default=-1.0)
    ambiguous = spread > 90.0 and best_corr < 0.05
    yaw, fit = best
    v.info = {"yaw": float(yaw), "iou": round(fit["iou"], 4), "scale": round(fit["s"], 4), "rot_deg": round(math.degrees(fit["rot"]), 2),
              "shift": [round(fit["t"][0], 2), round(fit["t"][1], 2)], "sweep": tried, "pool": len(pool), "pool_spread_deg": round(spread, 1), "pool_best_corr": round(best_corr, 3), "pool_table": table, "colour_diff_pick": round(best_d, 2)}
    if ambiguous:
        v.info["refused"] = f"turn is ambiguous (outline fits over {spread:.0f} degrees, structure match {best_corr:.2f})"
        log(f"  view {name}: refused, {v.info['refused']}")
        return v
    if fit["iou"] < MIN_IOU:
        v.info["refused"] = f"silhouette match {fit['iou']:.3f} under {MIN_IOU}"
        log(f"  view {name}: refused, iou {fit['iou']:.3f} at yaw {yaw}")
        return v
    cam = face.Camera(target=centre, yaw_deg=float(yaw), scale=scale, size=S)
    v.cam = cam
    v.raster = Raster(mesh, cam)
    M = sim_to_full(fit, pic.shape[0], S)
    ys, xs = np.mgrid[0:S, 0:S] + 0.5
    qx = M[0, 0] * xs + M[0, 1] * ys + M[0, 2]
    qy = M[1, 0] * xs + M[1, 1] * ys + M[1, 2]
    a_aff = catmull(pic, qx, qy)
    al_aff = face.sample(alpha[..., None], qx, qy)[..., 0]
    v.pic_mask = (al_aff > 0.5)
    v.alpha_img = al_aff
    rm = v.raster.mask
    v.render = render_colour(mesh, v.raster, tex)
    # --- flow at FIT_S
    k = S // FIT_S
    r_s = small(v.render, FIT_S)
    a_s = small(a_aff, FIT_S)
    m_s = small(rm.astype(np.float64), FIT_S) > 0.5
    pm_s = small(v.pic_mask.astype(np.float64), FIT_S) > 0.5
    both = m_s & pm_s
    sg = FIT_S / 256.0 * 1.2
    ref = structure_channels(r_s, m_s, sg)
    mov = structure_channels(a_s, pm_s, sg)
    region = face.dilate_mask(m_s | pm_s, 6)
    max_px = MAX_FLOW * FIT_S
    t0 = time.time()
    u = hs_flow(ref, mov, region, max_px, alpha=FLOW_ALPHA)
    u = np.stack([face.blur(u[..., 0], FLOW_SMOOTH), face.blur(u[..., 1], FLOW_SMOOTH)], -1)
    u = np.stack([face.blur(u[..., 0] * region, 0.01), face.blur(u[..., 1] * region, 0.01)], -1) if False else u
    # no folding: scale the field down until the Jacobian determinant stays above 0.6 inside the figure
    fold = 1.0
    for _ in range(8):
        dux, duy = _grad(u[..., 0] * fold)
        dvx, dvy = _grad(u[..., 1] * fold)
        det = (1 + dux) * (1 + dvy) - duy * dvx
        bad = float((det[both] < 0.6).mean()) if both.any() else 0.0
        if bad < 0.002:
            break
        fold *= 0.8
    u = u * fold
    v.info["flow_fold_scale"] = round(fold, 3)
    mag = np.hypot(u[..., 0], u[..., 1])
    v.info["flow_max_px_512"] = round(float(mag[both].max()), 2) if both.any() else 0.0
    v.info["flow_mean_px_512"] = round(float(mag[both].mean()), 2) if both.any() else 0.0
    v.info["flow_seconds"] = round(time.time() - t0, 1)
    # upsample the field to S and resample the picture once (affine + flow in one Catmull-Rom lookup)
    uf = np.stack([face.resize(u[..., c], S, Image.BICUBIC) * k if False else _rs(u[..., c], S, S) * k for c in range(2)], -1)
    qx2 = M[0, 0] * (xs + uf[..., 0]) + M[0, 1] * (ys + uf[..., 1]) + M[0, 2]
    qy2 = M[1, 0] * (xs + uf[..., 0]) + M[1, 1] * (ys + uf[..., 1]) + M[1, 2]
    a_flow = catmull(pic, qx2, qy2)
    al_flow = face.sample(alpha[..., None], qx2, qy2)[..., 0]
    # guard: keep the flow only if structure agreement with the render improves
    inner = face.erode(small(rm.astype(np.float64), 1024) > 0.5, 3)
    fm0 = face.feature_match(small(a_aff, 1024), small(v.render, 1024), inner)
    fm1 = face.feature_match(small(a_flow, 1024), small(v.render, 1024), inner)
    v.info["feature_match_affine"], v.info["feature_match_flow"] = fm0, fm1
    if fm1 >= fm0 - 0.005 and flow:
        v.pic_aligned, v.alpha_img, v.info["flow_used"] = a_flow, al_flow, True
    else:
        v.pic_aligned, v.info["flow_used"] = a_aff, False
        v.alpha_img = al_aff
    v.pic_affine = a_aff
    v.alpha_aff = al_aff
    v.valid = rm & (v.alpha_img > 0.5)
    # feather: eroded valid area blurred over a few pixels at S (about 6 px at 2048)
    r = max(2, S // 340)
    v.feather = np.clip(face.blur(face.erode(v.valid, r).astype(np.float64), r * 0.8) / 0.9, 0.0, 1.0)
    v.ok = True
    log(f"  view {name}: yaw {yaw}, iou {fit['iou']:.3f}, flow {v.info['flow_used']} (match {fm0:.3f} -> {fm1:.3f}, max {v.info['flow_max_px_512']} px)")
    return v


# ---------------------------------------------------------------------------------------------------------------
# atlas side: texel positions, projection weights

GUTTER_PX = 3            # island borders are projected too, this many texels out (the plane of the border triangle continued)


class Atlas:
    def __init__(self, mesh: dict, T: int, gutter: int = GUTTER_PX):
        self.T = T
        t0 = time.time()
        uvp = face.uv_pixels(mesh["uv"], T)
        ras = face.rasterise(uvp, np.zeros(len(mesh["uv"])), mesh["faces"], T)
        self.cover = ras["tri"] >= 0
        isl = np.flatnonzero(self.cover.ravel())
        pos = face.interpolate(mesh["verts"], mesh["faces"], ras)[self.cover]
        n = face.interpolate(mesh["vnormals"], mesh["faces"], ras)[self.cover]
        nrm = n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
        v, f = mesh["verts"], mesh["faces"]
        a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
        area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1).sum()
        self.area_m2 = float(area)
        self.pitch_m = math.sqrt(area / max(isl.size, 1))   # surface metres per texel (islands only)
        self.n_island = isl.size
        self.is_gut = np.zeros(isl.size, dtype=bool)
        self.src = np.arange(isl.size)
        self.idx, self.pos, self.nrm = isl, pos, nrm
        self.cover_ext = self.cover
        if gutter > 0:
            tri = np.where(self.cover, ras["tri"], -1).astype(np.float64)
            Y, X = np.mgrid[0:T, 0:T].astype(np.float64)
            gtri = imaging.dilate(tri, self.cover, gutter)
            gx = imaging.dilate(X, self.cover, gutter)
            gy = imaging.dilate(Y, self.cover, gutter)
            gmask = (gtri >= 0) & ~self.cover
            gi = np.flatnonzero(gmask.ravel())
            t = gtri.ravel()[gi].astype(np.int64)
            p = np.stack([(gi % T) + 0.5, (gi // T) + 0.5], 1)
            tv = uvp[f[t]]                                   # (m,3,2)
            d = (tv[:, 1] - tv[:, 0], tv[:, 2] - tv[:, 0])
            det = d[0][:, 0] * d[1][:, 1] - d[0][:, 1] * d[1][:, 0]
            ok = np.abs(det) > 1e-9
            rel = p - tv[:, 0]
            b1 = (rel[:, 0] * d[1][:, 1] - rel[:, 1] * d[1][:, 0]) / np.where(ok, det, 1)
            b2 = (d[0][:, 0] * rel[:, 1] - d[0][:, 1] * rel[:, 0]) / np.where(ok, det, 1)
            b0 = 1 - b1 - b2
            gpos = b0[:, None] * v[f[t, 0]] + b1[:, None] * v[f[t, 1]] + b2[:, None] * v[f[t, 2]]
            gn = b0[:, None] * mesh["vnormals"][f[t, 0]] + b1[:, None] * mesh["vnormals"][f[t, 1]] + b2[:, None] * mesh["vnormals"][f[t, 2]]
            gn /= np.maximum(np.linalg.norm(gn, axis=1, keepdims=True), 1e-12)
            lut = np.full(T * T, -1, dtype=np.int64)
            lut[isl] = np.arange(isl.size)
            srcflat = (gy.ravel()[gi].astype(np.int64)) * T + gx.ravel()[gi].astype(np.int64)
            keep = ok & (lut[srcflat] >= 0)
            self.idx = np.concatenate([isl, gi[keep]])
            self.pos = np.concatenate([pos, gpos[keep]])
            self.nrm = np.concatenate([nrm, gn[keep]])
            self.is_gut = np.concatenate([self.is_gut, np.ones(int(keep.sum()), dtype=bool)])
            self.src = np.concatenate([self.src, lut[srcflat][keep]])
            ext = self.cover.copy().reshape(-1)
            ext[gi[keep]] = True
            self.cover_ext = ext.reshape(T, T)
        self.seconds = time.time() - t0


def cos_weight(cosv: np.ndarray) -> np.ndarray:
    """cos^4, zero below COS_CUT (about 75 degrees), feathered over the next 0.12 of cosine."""
    c = np.clip(cosv, 0.0, 1.0)
    return c ** 4 * face.smoothstep(COS_CUT, COS_CUT + 0.12, cosv)


def project_view(atlas: Atlas, v: View) -> dict:
    """Which atlas texels this view sees squarely: index into atlas.idx, screen position, weight."""
    cam = v.cam
    sp, sd = cam.project(atlas.pos)
    xi = np.clip(sp[:, 0].astype(int), 0, S - 1)
    yi = np.clip(sp[:, 1].astype(int), 0, S - 1)
    cosv = -(atlas.nrm @ cam.basis[2])
    px_m = cam.scale / S
    tol = 2.5 * px_m / np.maximum(np.abs(cosv), 0.15)
    inb = (sp[:, 0] >= 0) & (sp[:, 0] < S) & (sp[:, 1] >= 0) & (sp[:, 1] < S)
    z = v.raster.z[yi, xi]
    visible = inb & (sd <= z + tol)
    visible &= v.raster.fcos[yi, xi] > 0.05
    feather = face.sample(v.feather[..., None], sp[:, 0], sp[:, 1])[:, 0]
    w = v.weight * cos_weight(cosv) * feather * visible
    keep = np.flatnonzero(w > 1e-5)
    return {"sel": keep, "sp": sp[keep], "w": w[keep], "cos": cosv[keep]}


def view_colours(proj: dict, image: np.ndarray) -> np.ndarray:
    return catmull(image, proj["sp"][:, 0].reshape(-1, 1), proj["sp"][:, 1].reshape(-1, 1))[:, 0]


def detail_only(v: View, sigma_px: float) -> np.ndarray:
    """The view's picture with its broad colour and tone (below sigma_px at S) taken from the TRELLIS render:
    render low frequencies + picture high frequencies, masked so the background does not bleed in."""
    m = v.valid
    lo_r = face.masked_blur(v.render, m, sigma_px)
    lo_p = face.masked_blur(v.pic_aligned, m, sigma_px)
    return np.clip(lo_r + v.pic_aligned - lo_p, 0.0, 1.0)


# ---------------------------------------------------------------------------------------------------------------
# 3D low-frequency matching of the kept texture

def surface_correction(atlas: Atlas, delta: np.ndarray, alpha: np.ndarray, cell: float = 0.012, passes: int = 3, r: int = 2) -> tuple[np.ndarray, np.ndarray]:
    """Smooth, surface-following version of delta (colour offset of the painted texels), carried to every covered
    texel and fading out with distance from painted surface. Returns (correction (N,3), reach (N) 0..1)."""
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
        sa = face._box3(sa, r)
        sn = face._box3(sn, r)
    g = sd / np.maximum(sa, 1e-9)[..., None]
    frac = sa / np.maximum(sn, 1e-9)
    fc = f - 0.5
    corr = np.stack([face._trilinear(g[..., c], fc) for c in range(3)], -1)
    reach = face._trilinear(frac, fc)
    return corr, np.clip(face.smoothstep(0.03, 0.4, reach), 0, 1)


def finish(atlas: Atlas, base: np.ndarray, paint: np.ndarray, alpha: np.ndarray, tone_match: bool = True,
           gutter: np.ndarray | None = None, gutter_mask: np.ndarray | None = None) -> np.ndarray:
    """New atlas from the painted colour (N,3) and its alpha (N): painted texels take it; the others keep base with its
    low frequencies shifted to agree with the painted colour near them; then island borders are grown into the gutter."""
    T = atlas.T
    if atlas.is_gut.any():
        # a border texel the views do not reach takes its island neighbour's painted colour
        g = atlas.is_gut
        need = g & (alpha < 0.5) & (alpha[atlas.src] >= 0.5)
        paint = paint.copy()
        alpha = alpha.copy()
        paint[need] = paint[atlas.src[need]]
        alpha[need] = alpha[atlas.src[need]]
    kept = base.reshape(-1, 3)[atlas.idx]
    out = kept.copy()
    sel = alpha > 0.5
    if tone_match and sel.sum() > 100:
        delta = np.where(sel[:, None], paint - kept, 0.0)
        corr, reach = surface_correction(atlas, delta, np.where(sel, alpha, 0.0))
        out = np.clip(kept + corr * reach[:, None], 0, 1)
    out = out * (1 - alpha[:, None]) + paint * alpha[:, None]
    res = base.copy().reshape(-1, 3)
    res[atlas.idx] = np.clip(out, 0, 1)
    res = res.reshape(T, T, 3)
    filled = atlas.cover_ext
    if gutter is not None:
        take = gutter_mask & ~atlas.cover_ext
        res[take] = gutter[take]
        filled = atlas.cover_ext | take
    return imaging.dilate(res, filled, imaging.dilation_px(T))


# ---------------------------------------------------------------------------------------------------------------
# V1 blend, V3 solve

BLEND_POW = 2.0
FRONT_PRIORITY = 0.15   # where the picture's own weight reaches this, the turnaround views are switched off
A0, A1 = 0.004, 0.06     # coverage alpha ramp on the best single-view weight


def blend(atlas: Atlas, projs: list[dict], images: list[np.ndarray], pw: float | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Weighted blend. Returns (paint (N,3), alpha (N), dominant view index (N), -1 for none)."""
    N = atlas.idx.size
    num = np.zeros((N, 3))
    den = np.zeros(N)
    wmax = np.zeros(N)
    dom = np.full(N, -1)
    w0 = np.zeros(N)
    w0[projs[0]["sel"]] = projs[0]["w"]
    suppress = 1.0 - np.clip(w0 / FRONT_PRIORITY, 0.0, 1.0)
    for i, (p, img) in enumerate(zip(projs, images)):
        col = view_colours(p, img)
        if i > 0:
            p = dict(p, w=p["w"] * suppress[p["sel"]])
        w = p["w"] ** (BLEND_POW if pw is None else pw)
        num[p["sel"]] += col * w[:, None]
        den[p["sel"]] += w
        better = p["w"] > wmax[p["sel"]]
        dom[p["sel"][better]] = i
        wmax[p["sel"]] = np.maximum(wmax[p["sel"]], p["w"])
    paint = num / np.maximum(den, 1e-12)[:, None]
    alpha = face.smoothstep(A0, A1, wmax)
    return paint, alpha, dom


def solve(atlas: Atlas, base: np.ndarray, views: list[View], images: list[np.ndarray], front_map: np.ndarray | None = None, lam0: float = 0.01, lam1: float = 0.4,
          iters: int = 60, log=print) -> dict:
    """Atlas texels that best reproduce every view: minimise sum_rows w (a.x - pixel)^2 + sum_i lam_i (x_i - base_i)^2.
    Rows are view pixels, a holds the 4 bilinear taps of the pixel's atlas position. Matrix-free preconditioned CG."""
    T = atlas.T
    NT = T * T
    t0 = time.time()
    cols, vals, rows_b, rows_w = [], [], [], []
    for vi, (v, img) in enumerate(zip(views, images)):
        ras = v.raster
        ok = ras.mask & (ras.fcos > 0.05) & (v.feather > 0)
        w = v.weight * cos_weight(ras.cos) * v.feather * ok
        if vi > 0 and front_map is not None:
            tx_ = np.clip((ras.uv[..., 0] * T).astype(int), 0, T - 1)
            ty_ = np.clip((ras.uv[..., 1] * T).astype(int), 0, T - 1)
            w = w * (1.0 - np.clip(front_map[ty_, tx_] / FRONT_PRIORITY, 0.0, 1.0))
        sel = w > 1e-5
        uv = ras.uv[sel] * T
        fx, fy = uv[:, 0] - 0.5, uv[:, 1] - 0.5
        x0, y0 = np.floor(fx).astype(np.int64), np.floor(fy).astype(np.int64)
        tx, ty = fx - x0, fy - y0
        x0c, x1c = np.clip(x0, 0, T - 1), np.clip(x0 + 1, 0, T - 1)
        y0c, y1c = np.clip(y0, 0, T - 1), np.clip(y0 + 1, 0, T - 1)
        cols.append(np.stack([y0c * T + x0c, y0c * T + x1c, y1c * T + x0c, y1c * T + x1c], 1).astype(np.int32))
        vals.append(np.stack([(1 - tx) * (1 - ty), tx * (1 - ty), (1 - tx) * ty, tx * ty], 1).astype(np.float32))
        rows_b.append(img[sel].astype(np.float32))
        rows_w.append(w[sel].astype(np.float32))
    C = np.concatenate(cols)
    V = np.concatenate(vals)
    B = np.concatenate(rows_b)
    Wr = np.concatenate(rows_w)
    M = C.shape[0]
    log(f"    solve: {M} rows, {C.size} entries, build {time.time() - t0:.1f}s")
    cflat = C.ravel()
    wv = V * Wr[:, None]
    cov = np.bincount(cflat, weights=wv.ravel(), minlength=NT)
    diag_a = np.bincount(cflat, weights=(wv * V).ravel(), minlength=NT)
    lam = lam0 + lam1 * np.exp(-cov / 0.03)
    base_flat = base.reshape(-1, 3).astype(np.float64)
    diag = diag_a + lam
    out = np.empty((NT, 3))
    its = []
    for ch in range(3):
        b = np.bincount(cflat, weights=(wv * B[:, ch:ch + 1]).ravel(), minlength=NT) + lam * base_flat[:, ch]
        x = base_flat[:, ch].copy()

        def Aop(xx):
            y = (V * xx[C].astype(np.float32)).sum(1)
            return np.bincount(cflat, weights=(wv * y[:, None]).ravel(), minlength=NT) + lam * xx

        r = b - Aop(x)
        z = r / diag
        p = z.copy()
        rz = float(r @ z)
        r0 = math.sqrt(float(r @ r)) + 1e-12
        k = 0
        for k in range(iters):
            Ap = Aop(p)
            a = rz / max(float(p @ Ap), 1e-30)
            x += a * p
            r -= a * Ap
            if math.sqrt(float(r @ r)) / r0 < 1e-3:
                break
            z = r / diag
            rz2 = float(r @ z)
            p = z + (rz2 / rz) * p
            rz = rz2
        its.append(k + 1)
        out[:, ch] = x
    secs = time.time() - t0
    mem = (C.nbytes + V.nbytes + B.nbytes + Wr.nbytes + wv.nbytes) / 1e6
    log(f"    solve: CG iterations {its}, {secs:.1f}s, matrix memory {mem:.0f} MB")
    paint = np.clip(out[atlas.idx], 0, 1)
    alpha = face.smoothstep(A0 * 0.5, A1 * 0.5, cov[atlas.idx])
    return {"full": np.clip(out.reshape(T, T, 3), 0, 1), "full_mask": (cov > A1 * 0.5).reshape(T, T), "paint": paint, "alpha": alpha, "seconds": secs, "mem_mb": mem, "iters": its, "rows": int(M)}


# ---------------------------------------------------------------------------------------------------------------
# measures

YAWS8 = [0, 45, 90, 135, 180, 225, 270, 315]


class Measurer:
    def __init__(self, mesh: dict, atlas: Atlas, front: View):
        self.mesh, self.atlas, self.front = mesh, atlas, front
        c, s = model_frame(mesh)
        t0 = time.time()
        self.rasters = {y: Raster(mesh, face.Camera(target=c, yaw_deg=float(y), scale=s, size=1024)) for y in YAWS8}
        self.seam = self._seams()
        self.seconds_setup = time.time() - t0

    def _seams(self, max_edges: int = 6000) -> dict:
        v, f, uv = self.mesh["verts"], self.mesh["faces"], self.mesh["uv"]
        key = np.round(v * 1e5).astype(np.int64)
        _, pid = np.unique(key, axis=0, return_inverse=True)
        pid = pid.reshape(-1)
        e = {}
        for fi in range(len(f)):
            for k in range(3):
                a, b = f[fi, k], f[fi, (k + 1) % 3]
                pa, pb = pid[a], pid[b]
                kk = (min(pa, pb), max(pa, pb))
                e.setdefault(kk, []).append((a, b, pa, pb))
        pairs = []
        for kk, lst in e.items():
            if len(lst) != 2:
                continue
            (a1, b1, pa1, pb1), (a2, b2, pa2, pb2) = lst
            # orient the second edge so its start matches the first's start in 3D
            if pa1 != pa2:
                a2, b2 = b2, a2
            u1a, u1b, u2a, u2b = uv[a1], uv[b1], uv[a2], uv[b2]
            if np.abs(u1a - u2a).max() > 1e-5 or np.abs(u1b - u2b).max() > 1e-5:
                pairs.append((u1a, u1b, u2a, u2b))
        rng = np.random.default_rng(1)
        if len(pairs) > max_edges:
            pairs = [pairs[i] for i in rng.choice(len(pairs), max_edges, replace=False)]
        arr = np.array(pairs) if pairs else np.zeros((0, 4, 2))
        return {"edges": arr, "count": len(pairs)}

    def seam_step(self, tex: np.ndarray) -> float:
        e = self.seam["edges"]
        if len(e) == 0:
            return 0.0
        T = tex.shape[0]
        ts = np.linspace(0.1, 0.9, 9)[None, :, None]
        a = e[:, 0:1] * (1 - ts) + e[:, 1:2] * ts
        b = e[:, 2:3] * (1 - ts) + e[:, 3:4] * ts
        pa = face.uv_pixels(a.reshape(-1, 2), T)
        pb = face.uv_pixels(b.reshape(-1, 2), T)
        ca = face.sample(tex, pa[:, 0], pa[:, 1])
        cb = face.sample(tex, pb[:, 0], pb[:, 1])
        return float(np.abs(ca - cb).mean() * 255)

    def texel_step(self, tex: np.ndarray) -> float:
        """Mean absolute colour step between horizontal neighbour texels inside the islands (0..255)."""
        c = self.atlas.cover
        both = c[:, 1:] & c[:, :-1]
        return float(np.abs(tex[:, 1:] - tex[:, :-1]).mean(-1)[both].mean() * 255)

    def measure(self, tex: np.ndarray) -> dict:
        imgs = {}
        sharp = {}
        for y, r in self.rasters.items():
            im = render_colour(self.mesh, r, tex)
            imgs[y] = im
            sharp[y] = face.sharpness(im, r.mask)
        fr = self.front
        rend = render_colour(self.mesh, fr.raster, tex)
        m1024 = face.erode(small((fr.valid & fr.pic_mask).astype(np.float64), 1024) > 0.5, 6)
        r1, p1, pa1 = small(rend, 1024), small(fr.pic_affine, 1024), small(fr.pic_aligned, 1024)
        out = {
            "mean_sharp8": round(float(np.mean(list(sharp.values()))), 3),
            "sharp": {str(k): v for k, v in sharp.items()},
            "real_detail_db": real_detail_db(tex, self.atlas.cover),
            "seam_step": round(self.seam_step(tex), 3),
            "seam_ratio": round(self.seam_step(tex) / max(self.texel_step(tex), 1e-6), 3),
            "front_ssim": round(ssim(face.luminance(r1), face.luminance(p1), m1024), 4),
            "front_ssim_aligned": round(ssim(face.luminance(r1), face.luminance(pa1), m1024), 4),
            "front_lab_diff": round(lowpass_lab_diff(r1, p1, m1024), 3),
            "front_lapvar_ratio": round(lap_var(r1, m1024) / max(lap_var(p1, m1024), 1e-12), 4),
            "front_sharp_ratio": round(face.sharpness(r1, m1024) / max(face.sharpness(p1, m1024), 1e-9), 4),
        }
        return out, imgs, rend


# ---------------------------------------------------------------------------------------------------------------
# contact sheets

def crop(img: np.ndarray, cx: float, cy: float, n: int) -> Image.Image:
    h, w = img.shape[:2]
    x0 = int(min(max(cx - n / 2, 0), w - n))
    y0 = int(min(max(cy - n / 2, 0), h - n))
    return Image.fromarray(face.to_uint8(img[y0:y0 + n, x0:x0 + n]))


def label(img: Image.Image, text: str) -> Image.Image:
    from PIL import ImageDraw

    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 8 * len(text) + 8, 14), fill=(0, 0, 0))
    d.text((4, 2), text, fill=(255, 255, 255))
    return img


def sheets(dest: str, subject: str, rows: dict, front: View, pic_img: np.ndarray) -> None:
    """rows: name -> {"imgs": {yaw: 1024 render}, "front": S render}. Writes overview (1/8 size) and crops (1:1)."""
    names = list(rows)
    th = 256
    ov = Image.new("RGB", (th * 5, th * len(names)), (40, 40, 40))
    for i, n in enumerate(names):
        r = rows[n]
        tiles = [r["front"], r["imgs"][0], r["imgs"][45], r["imgs"][90], r["imgs"][180]]
        for j, t in enumerate(tiles):
            im = Image.fromarray(face.to_uint8(t)).resize((th, th), Image.LANCZOS)
            ov.paste(label(im, f"{n} {['pic cam', 'front', 'quarter', 'side', 'back'][j]}"), (j * th, i * th))
    ov.save(os.path.join(dest, "sheet_overview.png"))
    ys, xs = np.where(front.raster.mask)
    top = ys <= ys.min() + 0.12 * (ys.max() - ys.min())
    cx, cy = xs[top].mean(), ys.min() + 0.10 * (ys.max() - ys.min())
    cy2 = ys.mean()
    cx2 = xs.mean()
    cs = 320
    cr = Image.new("RGB", (cs * 4, cs * (len(names) + 1)), (40, 40, 40))
    # first row: the picture itself at the same crops
    cr.paste(label(crop(pic_img, cx, cy, cs), "picture head"), (0, 0))
    cr.paste(label(crop(pic_img, cx2, cy2, cs), "picture body"), (cs, 0))
    for i, n in enumerate(names):
        r = rows[n]
        cr.paste(label(crop(r["front"], cx, cy, cs), f"{n} head"), (0, (i + 1) * cs))
        cr.paste(label(crop(r["front"], cx2, cy2, cs), f"{n} body"), (cs, (i + 1) * cs))
        cr.paste(label(crop(r["imgs"][45], 512, 400, cs), f"{n} quarter"), (2 * cs, (i + 1) * cs))
        cr.paste(label(crop(r["imgs"][180], 512, 400, cs), f"{n} back"), (3 * cs, (i + 1) * cs))
    cr.save(os.path.join(dest, "sheet_crops.png"))


# ---------------------------------------------------------------------------------------------------------------
# subject runner

FRONT_YAWS = list(range(0, 360, 10))
TURN_YAWS = {
    "quarter": [y for y in range(20, 71, 10)] + [-y for y in range(20, 71, 10)],
    "left": [y for y in range(70, 111, 10)] + [-y for y in range(70, 111, 10)],
    "right": [y for y in range(70, 111, 10)] + [-y for y in range(70, 111, 10)],
    "back": list(range(140, 221, 10)),
}


def run_subject(key: str, variants: list[str], turn: bool, log=print) -> dict:
    build, tname = SUBJECTS[key]
    b = os.path.join(CONVERT, build)
    dest = os.path.join(OUT, key + TAG)
    os.makedirs(dest, exist_ok=True)
    t_all = time.time()
    mesh = face.load_mesh(os.path.join(b, "model.glb"))
    tex = face.read_rgb(os.path.join(b, "base.png"))
    T = tex.shape[0]
    centre, scale = model_frame(mesh)
    pic, alpha = load_picture(os.path.join(b, "input.png"), None)
    log(f"[{key}] mesh {len(mesh['faces'])} tris, atlas {T}")
    atlas = Atlas(mesh, T)
    pitch_px = atlas.pitch_m * S / scale
    log(f"[{key}] texel pitch {atlas.pitch_m * 1000:.2f} mm = {pitch_px:.2f} px at {S}; picture px = {S / 1024:.1f} of ours; atlas used {atlas.cover.mean():.3f} ({atlas.seconds:.0f}s)")
    t0 = time.time()
    views: list[View] = []
    front = fit_view(mesh, tex, pic, alpha, "front", FRONT_YAWS, centre, scale, log=log)
    if not front.ok:
        raise SystemExit(f"{key}: front picture refused: {front.info.get('refused')}")
    views.append(front)
    all_info = {"front": front.info}
    if turn:
        for name in ("quarter", "left", "right", "back"):
            p = os.path.join(TURN, tname, name + ".png")
            if not os.path.isfile(p):
                continue
            tp, ta = load_picture(p, p)
            vw = fit_view(mesh, tex, tp, ta, name, TURN_YAWS[name], centre, scale, log=log)
            all_info[name] = vw.info
            if vw.ok:
                views.append(vw)
    t_fit = time.time() - t0
    t0 = time.time()
    projs = [project_view(atlas, v) for v in views]
    t_proj = time.time() - t0
    meas = Measurer(mesh, atlas, front)
    results: dict[str, dict] = {}
    rows: dict[str, dict] = {}
    m0, imgs0, rend0 = meas.measure(tex)
    results["before"] = m0
    rows["before"] = {"imgs": imgs0, "front": rend0}
    log(f"[{key}] before: {json.dumps({k: v for k, v in m0.items() if k != 'sharp'})}")
    painted = {}
    imgs_aligned = [v.pic_aligned for v in views]
    sig = {"2": 2.0 * pitch_px, "4": 4.0 * pitch_px}
    detail_cache: dict[tuple[int, str], np.ndarray] = {}

    def det(i: int, s: str) -> np.ndarray:
        if (i, s) not in detail_cache:
            detail_cache[(i, s)] = detail_only(views[i], sig[s])
        return detail_cache[(i, s)]

    def images_for(variant: str) -> list[np.ndarray]:
        if variant.startswith("v1") or variant == "v3":
            return imgs_aligned
        if variant == "v1front":
            return [imgs_aligned[0]] + [imgs_aligned[i] for i in range(1, len(views))]
        s = {"v2a": "2", "v2b": "4", "v2c": "2"}[variant]
        out = []
        for i in range(len(views)):
            if i == 0 and variant != "v2c":
                out.append(imgs_aligned[0])
            else:
                out.append(det(i, s))
        return out

    for var in variants:
        t0 = time.time()
        extra = {}
        pw = None
        if var.startswith("v1p"):
            pw = float(var[3:])
        gut = gmask = None
        if var == "v3":
            fmap = np.zeros(atlas.T * atlas.T)
            fmap[atlas.idx[projs[0]['sel']]] = projs[0]['w']
            sol = solve(atlas, tex, views, images_for(var), front_map=fmap.reshape(atlas.T, atlas.T), log=log)
            paint, a = sol["paint"], sol["alpha"]
            gut, gmask = sol["full"], sol["full_mask"]
            extra = {k: sol[k] for k in ("seconds", "mem_mb", "iters", "rows")}
            dom = None
        else:
            paint, a, dom = blend(atlas, projs, images_for(var), pw)
        new = finish(atlas, tex, paint, a, gutter=gut, gutter_mask=gmask)
        secs = time.time() - t0
        m, imgs, rend = meas.measure(new)
        m["seconds"] = round(secs, 1)
        m.update(extra)
        m["painted_share"] = round(float((a > 0.5).mean()), 4)
        if dom is not None:
            m["share_by_view"] = {views[i].name: round(float(((dom == i) & (a > 0.5)).mean()), 4) for i in range(len(views))}
        results[var] = m
        rows[var] = {"imgs": imgs, "front": rend}
        face.save_rgb(new, os.path.join(dest, f"{var}_base.png"))
        painted[var] = new
        log(f"[{key}] {var}: {json.dumps({k: v for k, v in m.items() if k != 'sharp'})}")
    face.save_rgb(tex, os.path.join(dest, "before_base.png"))
    sheets(dest, key, rows, front, front.pic_affine)
    face.save_rgb(front.pic_affine, os.path.join(dest, "picture_affine.png"))
    face.save_rgb(front.pic_aligned, os.path.join(dest, "picture_aligned.png"))
    rep = {"subject": key, "build": build, "atlas": T, "texel_pitch_mm": round(atlas.pitch_m * 1000, 3), "pitch_px": round(pitch_px, 3),
           "atlas_used": round(float(atlas.cover.mean()), 3), "views": {k: {kk: vv for kk, vv in v.items() if kk != "sweep"} for k, v in all_info.items()},
           "seconds": {"fit_and_flow": round(t_fit, 1), "projection": round(t_proj, 1), "total": round(time.time() - t_all, 1)},
           "results": results}
    with open(os.path.join(dest, "report.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, default=float)
    return rep


def table(reports: list[dict]) -> str:
    cols = ["mean_sharp8", "real_detail_db", "front_ssim", "front_ssim_aligned", "front_lab_diff", "front_lapvar_ratio", "front_sharp_ratio", "seam_step", "seam_ratio", "painted_share", "seconds"]
    lines = ["| subject | variant | " + " | ".join(cols) + " |", "|" + "---|" * (len(cols) + 2)]
    for r in reports:
        for var, m in r["results"].items():
            lines.append(f"| {r['subject']} | {var} | " + " | ".join(str(m.get(c, "")) for c in cols) + " |")
    return "\n".join(lines)


def write_results() -> None:
    reps = []
    for p in sorted(os.listdir(OUT)):
        rp = os.path.join(OUT, p, "report.json")
        if os.path.isfile(rp):
            r = json.load(open(rp, encoding="utf-8"))
            r["subject"] = p
            reps.append(r)
    with open(os.path.join(OUT, "results.md"), "w", encoding="utf-8") as f:
        f.write(table(reps) + "\n")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    variants = ["v1", "v2a", "v2b", "v2c", "v3"]
    for i, a in enumerate(sys.argv):
        if a == "--variants" and i + 1 < len(sys.argv):
            variants = sys.argv[i + 1].split(",")
            args = [x for x in args if x != sys.argv[i + 1]]
    turn = "--no-turn" not in sys.argv
    global FRONT_YAWS
    for i, a in enumerate(sys.argv):
        if a == "--front-yaws":
            FRONT_YAWS = [float(x) for x in sys.argv[i + 1].split(",")]
            args = [x for x in args if x != sys.argv[i + 1]]
    global MIN_IOU, TAG
    for i, a in enumerate(sys.argv):
        if a == "--tag":
            TAG = sys.argv[i + 1]
            args = [x for x in args if x != sys.argv[i + 1]]
    for i, a in enumerate(sys.argv):
        if a == "--min-iou":
            MIN_IOU = float(sys.argv[i + 1])
            args = [x for x in args if x != sys.argv[i + 1]]
    subjects = args or list(SUBJECTS)
    os.makedirs(OUT, exist_ok=True)
    reports = []
    for k in subjects:
        reports.append(run_subject(k, variants, turn))
        with open(os.path.join(OUT, "results.md"), "w", encoding="utf-8") as f:
            f.write(table(reports) + "\n")
    print(table(reports))


if __name__ == "__main__":
    main()
