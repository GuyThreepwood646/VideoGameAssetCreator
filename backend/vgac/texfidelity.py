"""Texture fidelity: how closely a textured model matches the picture it was built from (CPU only, numpy and Pillow).

docs/TEXTURE-FIDELITY-PLAN.md lane A. The model is drawn with its base colour only (no light, no shading, the colour
the texture holds, which is what the Standard view transform shows) from the front orthographic camera, using the same
rasteriser the face pass uses (face.py), so the render and the face pass agree. The picture's cut-out is then fitted to
the render by silhouette (scale and shift), and inside the overlap of both silhouettes, with a few pixels of border
left out, it reports:

- ssim: structural similarity of the lightness (1 is identical);
- delta_e: mean colour difference in Lab after a low-pass, so it measures tone and hue and not a pixel of
  misregistration (about 2 is barely visible, 10 is a clearly different colour);
- sharpness_ratio: Laplacian variance of the render over that of the picture at the same scale (1 is as sharp as the
  picture, 0.5 is clearly softer);
- real_detail_db: the base colour against itself shrunk to half and blown back up, inside the UV islands (lower means
  more real detail in the atlas; moved here from bakeoff/repaint_trial.py);
- view_sharpness: face.sharpness on 8 orthographic renders around the model (moved here from
  bakeoff/texture_measure.py). Only the front is compared with a picture; the other seven stand alone.

The fit camera: every turn level, then tilts 15 and 30 when that does not line up, then tilts up
to 90 and an 85 and 50 mm lens. A fit only those wider cameras reach also needs the model's inner edges to agree with
the picture's (posesearch's edge term, EDGE_MIN), since from above one box's outline fits another box's picture.

What it cannot see: the back and the sides (the picture shows one side), anything hidden behind a limb in the picture,
the normal, metal and roughness maps, and lighting. A picture that is itself shaded is compared as shaded.

Also here: texture_checks_for(), two report-only quality checks (severity warn, so they never block a run):
texture_sharp and texture_colour_matches_picture.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from . import face

YAWS = [0, 45, 90, 135, 180, 225, 270, 315]
VIEW_SIZE = 1024
WORK = 512            # size the silhouette fit runs at
BORDER_PX = 4         # pixels left out inside the overlap edge
LOWPASS_FRAC = 0.02   # colour low-pass sigma, as a share of the frame
ALPHA_OPAQUE = 128
FRONT_IOU_OK = 0.85  # a model whose front already fits the picture this well is not turned
IOU_FIT_MIN = 0.80    # below this the render and the picture do not line up well enough to trust the numbers
# Camera heights (degrees looking down) tried when the level camera does not fit: most step 1 pictures look down on the
# subject a little.
FIT_TILTS = (15.0, 30.0)
# Wider cameras tried only when those still do not fit: steeper tilts for pictures drawn from nearly overhead, and an 85
# and a 50 mm lens on the best of them. Shorter lenses are left out: _frame_camera would put the eye inside the model.
WIDE_TILTS = (45.0, 60.0, 75.0, 90.0)
WIDE_LENSES = (85.0, 50.0)
# A fit only a wider camera reaches counts only when the model's inner edges agree with the picture's (posesearch's edge
# term at that pose): seen from above, the outline of one boxy subject fits the picture of another, while their inner
# edges do not agree. The limit sits between the edge agreement of such wrong fits and that of true fits.
EDGE_MIN = 0.33
WIDE_EDGE_TRIES = 3   # the best wider fits whose edges are checked, best outline first

# Report-only limits for the two checks (set from the bake-off baseline, docs/TEXTURE-FIDELITY-PLAN.md).
SHARP_RATIO_MIN = 0.5
DELTA_E_MAX = 12.0


# --- shared atlas and view measures (moved from the bake-off scripts) -----------------------------------------


def uv_cover(mesh: dict[str, np.ndarray], size: int) -> np.ndarray:
    """Boolean (size, size) mask of the texels the UV islands cover."""
    ras = face.rasterise(face.uv_pixels(mesh["uv"], size), np.zeros(len(mesh["uv"])), mesh["faces"], size)
    return ras["tri"] >= 0


def real_detail_db(tex: np.ndarray, cover: np.ndarray) -> float:
    """PSNR of tex against itself shrunk to half and blown back up (bicubic), inside cover; lower = more real detail."""
    t = tex.shape[0]
    im = Image.fromarray(face.to_uint8(tex))
    back = np.asarray(im.resize((t // 2, t // 2), Image.BICUBIC).resize((t, t), Image.BICUBIC), dtype=np.float64) / 255.0
    m = face.erode(cover, 3)
    err = ((tex - back) ** 2)[m].mean()
    return round(float(10 * math.log10(1.0 / max(err, 1e-12))), 2)


def view_sharpness(mesh: dict[str, np.ndarray], tex: np.ndarray, size: int = VIEW_SIZE, yaws: list[int] | None = None) -> list[dict[str, Any]]:
    """Eight orthographic renders of the base colour, cameras framed on the model's own bounds so builds of one subject
    compare. [{yaw, image, mask, sharpness}]."""
    v = mesh["verts"]
    lo, hi = v.min(0), v.max(0)
    centre = (lo + hi) / 2
    scale = float(max(hi[1] - lo[1], np.hypot(hi[0] - lo[0], hi[2] - lo[2]))) * 1.06
    out = []
    for yaw in (YAWS if yaws is None else yaws):
        r = face.render_view(mesh, tex, face.Camera(target=centre, yaw_deg=yaw, scale=scale, size=size), background=0.5)
        out.append({"yaw": yaw, "image": r["image"], "mask": r["mask"], "sharpness": face.sharpness(r["image"], r["mask"])})
    return out


# --- inputs -------------------------------------------------------------------------------------------------


def load_picture(src: Path | str | np.ndarray | Image.Image) -> tuple[np.ndarray, np.ndarray]:
    """The source picture as (rgb float 0..1 (h,w,3), mask bool (h,w)). A picture with a cut-out uses its alpha (128 or
    more). One without (the 3D step's input is a figure on black) takes everything brighter than 10 of 255, holes
    filled, the way bakeoff/texture_trials.py makes its cut-outs."""
    if isinstance(src, np.ndarray):
        arr = src
        if arr.dtype != np.uint8:
            arr = face.to_uint8(arr)
        im = Image.fromarray(arr)
    elif isinstance(src, Image.Image):
        im = src
    else:
        im = Image.open(src)
    rgba = np.asarray(im.convert("RGBA"))
    alpha = rgba[..., 3]
    rgb = rgba[..., :3].astype(np.float64) / 255.0
    if alpha.min() < 255 and (alpha < ALPHA_OPAQUE).mean() > 0.005:
        mask = alpha >= ALPHA_OPAQUE
    else:
        mask = face.fill_holes(rgba[..., :3].max(axis=2) > 10)
    return rgb, mask


def load_texture(glb: Path | str, base_color: Path | str | None = None) -> np.ndarray | None:
    """The base colour map: base_color if given, else the image in the GLB's material."""
    if base_color:
        return face.read_rgb(base_color)
    import trimesh

    scene = trimesh.load(str(glb), force="mesh", process=False)
    mat = getattr(getattr(scene, "visual", None), "material", None)
    img = getattr(mat, "baseColorTexture", None) or getattr(mat, "image", None)
    if img is None:
        return None
    return np.asarray(img.convert("RGB"), dtype=np.float64) / 255.0


# --- fit ----------------------------------------------------------------------------------------------------


def _pad_square(a: np.ndarray) -> np.ndarray:
    h, w = a.shape[:2]
    s = max(h, w)
    pad = [((s - h) // 2, s - h - (s - h) // 2), ((s - w) // 2, s - w - (s - w) // 2)] + [(0, 0)] * (a.ndim - 2)
    return np.pad(a, pad)


def _affine(s: float, tx: float, ty: float, size: int) -> tuple[float, ...]:
    """PIL affine data (output -> input) for: scale s about the frame centre, then shift (tx, ty) output pixels."""
    c = size / 2.0
    return (1 / s, 0, c - (c + tx) / s, 0, 1 / s, c - (c + ty) / s)


def _warp(im: Image.Image, size: int, s: float, tx: float, ty: float, resample: int) -> Image.Image:
    return im.transform((size, size), Image.AFFINE, _affine(s, tx, ty, size), resample)


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    u = (a | b).sum()
    return float((a & b).sum() / u) if u else 0.0


def fit_silhouette(pic_mask: np.ndarray, render_mask: np.ndarray) -> dict[str, float]:
    """Scale and shift (in render-frame pixels, about the frame centre) that bring the picture's silhouette onto the
    render's: start from the centroids and areas, then a coordinate search on the silhouette IoU at WORK px. Returns
    {scale, tx, ty, iou} with iou measured at full size."""
    size = render_mask.shape[0]
    k = WORK / size
    mim = Image.fromarray((pic_mask * 255).astype(np.uint8))
    rm = np.asarray(Image.fromarray((render_mask * 255).astype(np.uint8)).resize((WORK, WORK), Image.BILINEAR)) >= 128
    pm = np.asarray(mim.resize((WORK, WORK), Image.BILINEAR))
    if rm.sum() < 20 or (pm >= 128).sum() < 20:
        return {"scale": 1.0, "tx": 0.0, "ty": 0.0, "iou": 0.0}
    pim = Image.fromarray(pm)
    ys, xs = np.nonzero(pm >= 128)
    ry, rx = np.nonzero(rm)
    s = math.sqrt(rm.sum() / (pm >= 128).sum())
    c = WORK / 2.0
    tx = (rx.mean() - c) - (xs.mean() - c) * s
    ty = (ry.mean() - c) - (ys.mean() - c) * s

    def score(s_: float, tx_: float, ty_: float) -> float:
        return _iou(np.asarray(_warp(pim, WORK, s_, tx_, ty_, Image.BILINEAR)) >= 128, rm)

    best = score(s, tx, ty)
    step_s, step_t = 0.04, 6.0
    while step_s > 0.002:
        improved = True
        while improved:
            improved = False
            for ds, dx, dy in ((step_s, 0, 0), (-step_s, 0, 0), (0, step_t, 0), (0, -step_t, 0), (0, 0, step_t), (0, 0, -step_t)):
                cand = (s * (1 + ds), tx + dx, ty + dy)
                v = score(*cand)
                if v > best + 1e-5:
                    best, s, tx, ty, improved = v, cand[0], cand[1], cand[2], True
        step_s, step_t = step_s / 2, step_t / 2
    tx, ty = tx / k, ty / k
    full = np.asarray(_warp(Image.fromarray((_pad_to(pic_mask, size) * 255).astype(np.uint8)), size, s, tx, ty, Image.BILINEAR)) >= 128
    return {"scale": float(s), "tx": float(tx), "ty": float(ty), "iou": _iou(full, render_mask)}


def _pad_to(mask: np.ndarray, size: int) -> np.ndarray:
    m = _pad_square(mask)
    if m.shape[0] != size:
        m = np.asarray(Image.fromarray((m * 255).astype(np.uint8)).resize((size, size), Image.BILINEAR)) >= 128
    return m


def _frame_camera(mesh: dict[str, np.ndarray], yaw: float, pic_extent_px: float, size: int, tilt: float = 0.0,
                  lens: float | None = None) -> face.Camera:
    """A camera whose render has the figure about as many pixels across as the picture does, so the picture is hardly
    resampled and sharpness compares at one scale. lens None is orthographic; a focal length (mm) a pinhole camera
    standing back by the distance that frames the scale (85 and 50 mm only: shorter puts the eye inside the model)."""
    v = mesh["verts"]
    lo, hi = v.min(0), v.max(0)
    centre = (lo + hi) / 2
    cam0 = face.Camera(target=centre, yaw_deg=yaw, scale=float(np.linalg.norm(hi - lo)) * 1.5, size=size, tilt_deg=tilt, lens_mm=lens)
    p, _ = cam0.project(v)
    ext = float(max(np.ptp(p[:, 0]), np.ptp(p[:, 1])))
    scale = cam0.scale * ext / max(pic_extent_px, 1.0)
    # never let the figure leave the frame
    scale = max(scale, cam0.scale * ext / (size * 0.96))
    return face.Camera(target=centre, yaw_deg=yaw, scale=scale, size=size, tilt_deg=tilt, lens_mm=lens)


# --- the measures --------------------------------------------------------------------------------------------


def _ssim(a: np.ndarray, b: np.ndarray, region: np.ndarray, sigma: float = 1.5) -> float:
    """Mean structural similarity of two lightness images (0..1) over region (Gaussian window, standard constants)."""
    c1, c2 = 0.01 ** 2, 0.03 ** 2
    mu_a, mu_b = face.blur(a, sigma), face.blur(b, sigma)
    saa = face.blur(a * a, sigma) - mu_a * mu_a
    sbb = face.blur(b * b, sigma) - mu_b * mu_b
    sab = face.blur(a * b, sigma) - mu_a * mu_b
    m = ((2 * mu_a * mu_b + c1) * (2 * sab + c2)) / ((mu_a ** 2 + mu_b ** 2 + c1) * (saa + sbb + c2))
    return float(m[region].mean())


def laplacian_variance(img: np.ndarray, region: np.ndarray) -> float:
    """Variance of the 4-neighbour Laplacian of the lightness (0..1) inside region."""
    lum = face.luminance(img)
    p = np.pad(lum, 1, mode="edge")
    lap = 4 * lum - p[:-2, 1:-1] - p[2:, 1:-1] - p[1:-1, :-2] - p[1:-1, 2:]
    return float(lap[region].var())


def compare(render: np.ndarray, render_mask: np.ndarray, pic: np.ndarray, pic_mask: np.ndarray, border: int = BORDER_PX) -> dict[str, Any]:
    """The picture-against-render measures over the overlap of both silhouettes, border pixels in. Both images are
    (S,S,3) floats 0..1 in one frame."""
    overlap = face.erode(render_mask & pic_mask, border)
    size = render.shape[0]
    out: dict[str, Any] = {"overlap_px": int(overlap.sum()), "overlap_share": round(float(overlap.sum() / max(render_mask.sum(), 1)), 3)}
    if overlap.sum() < 200:
        out.update({"ssim": None, "delta_e": None, "delta_l": None, "sharpness_ratio": None})
        return out
    out["ssim"] = round(_ssim(face.luminance(render), face.luminance(pic), overlap), 4)
    sigma = LOWPASS_FRAC * size
    a = face.masked_blur(face.to_lab(render), overlap, sigma)
    b = face.masked_blur(face.to_lab(pic), overlap, sigma)
    d = a - b
    out["delta_e"] = round(float(np.sqrt((d ** 2).sum(axis=2))[overlap].mean()), 2)
    out["delta_l"] = round(float(d[..., 0][overlap].mean()), 2)          # negative: the render is darker than the picture
    out["delta_chroma"] = round(float(np.hypot(d[..., 1], d[..., 2])[overlap].mean()), 2)
    vp = laplacian_variance(pic, overlap)
    vr = laplacian_variance(render, overlap)
    out["sharpness_ratio"] = round(vr / vp, 3) if vp > 1e-12 else None
    return out


# --- the score ----------------------------------------------------------------------------------------------


def _front_candidate(mesh: dict[str, np.ndarray], tex: np.ndarray, pic: np.ndarray, pic_mask: np.ndarray, yaw: float, size: int,
                     want_images: bool, tilt: float = 0.0, lens: float | None = None) -> dict[str, Any]:
    ys, xs = np.nonzero(pic_mask)
    extent = float(max(np.ptp(xs), np.ptp(ys)) + 1) * size / max(pic.shape[:2])
    cam = _frame_camera(mesh, yaw, extent, size, tilt, lens)
    r = face.render_view(mesh, tex, cam, background=0.5)
    pic_sq = _pad_square(pic)
    mask_sq = _pad_square(pic_mask)
    fit = fit_silhouette(_pad_to(mask_sq, size), r["mask"])
    img = Image.fromarray(face.to_uint8(pic_sq)).resize((size, size), Image.LANCZOS) if pic_sq.shape[0] != size else Image.fromarray(face.to_uint8(pic_sq))
    warped = np.asarray(_warp(img, size, fit["scale"], fit["tx"], fit["ty"], Image.BICUBIC), dtype=np.float64) / 255.0
    wmask = np.asarray(_warp(Image.fromarray((_pad_to(mask_sq, size) * 255).astype(np.uint8)), size, fit["scale"], fit["tx"], fit["ty"], Image.BILINEAR)) >= 128
    # Both sides at 8 bits, as a texture or a picture on disk is: float rendering would show less quantisation noise than the picture.
    r["image"] = face.to_uint8(r["image"]).astype(np.float64) / 255.0
    m = compare(r["image"], r["mask"], warped, wmask)
    res = {"yaw": yaw, "tilt": tilt, "lens": lens, "silhouette_iou": round(fit["iou"], 4), "fit_scale": round(fit["scale"], 3), "fit_shift_px": [round(fit["tx"], 1), round(fit["ty"], 1)], **m}
    if want_images:
        res["_images"] = {"render": r["image"], "picture": warped, "render_mask": r["mask"], "picture_mask": wmask}
    return res


def score(glb: Path | str, picture: Path | str | np.ndarray | Image.Image, base_color: Path | str | None = None,
          yaw: float | str = "auto", views: bool = True, size: int = VIEW_SIZE, keep_images: bool = False) -> dict[str, Any]:
    """The fidelity of a textured GLB against its picture. yaw "auto" keeps the front camera when its silhouette fits the picture and otherwise
    searches the turns for the best silhouette fit (a three-quarter picture, a model not turned yet); a number fixes it. Returns the numbers (see the module text) plus the atlas and eight-view measures when views is true; with
    keep_images the render and the fitted picture come back under "_images" for a contact sheet."""
    mesh = face.load_mesh(glb)
    if mesh is None:
        return {"error": "the model has no texture layout"}
    tex = load_texture(glb, base_color)
    if tex is None:
        return {"error": "the model has no base colour"}
    pic, pic_mask = load_picture(picture)
    if pic_mask.sum() < 100:
        return {"error": "the picture has no cut-out to compare against"}
    tried: dict[tuple[float, float, float | None], dict[str, Any]] = {}

    def run(y: float, t: float = 0.0, lens: float | None = None) -> dict[str, Any]:
        k = (float(y) % 360, float(t), lens)
        if k not in tried:
            tried[k] = _front_candidate(mesh, tex, pic, pic_mask, k[0], size, keep_images, k[1], lens)
        return tried[k]

    def best_iou() -> float:
        return max(r["silhouette_iou"] for r in tried.values())

    def two_tops(rows: list[dict[str, Any]]) -> list[tuple[float, float, float | None]]:
        rows = sorted(rows, key=lambda r: -r["silhouette_iou"])
        out = [rows[0]] + [r for r in rows[1:] if _yaw_apart(r["yaw"], rows[0]["yaw"]) >= 45][:1]
        return [(r["yaw"], r["tilt"], r["lens"]) for r in out]

    if yaw != "auto":
        run(float(yaw))
    else:
        # A model that already faces the camera the way the picture does (what prep makes) is taken as it is. Otherwise
        # the eight 45 degree turns, then 15 degree steps around the best (a three-quarter picture sits between them).
        if run(0)["silhouette_iou"] < FRONT_IOU_OK:
            for y in range(45, 360, 45):
                run(y)
            top = max(tried.values(), key=lambda r: r["silhouette_iou"])["yaw"]
            for dy in (-30, -15, 15, 30):
                run(top + dy)
        if best_iou() < IOU_FIT_MIN:
            # the level camera does not line up: a picture drawn from above. The two best turns at each camera height,
            # then 15 degree steps around the best of those (a level fit that already lines up is never re-searched)
            level = sorted(tried.values(), key=lambda r: -r["silhouette_iou"])
            tops = [level[0]["yaw"]] + [r["yaw"] for r in level[1:] if _yaw_apart(r["yaw"], level[0]["yaw"]) >= 45][:1]
            for t in FIT_TILTS:
                for y in tops:
                    run(y, t)
            b = max(tried.values(), key=lambda r: r["silhouette_iou"])
            if b["tilt"]:
                for dy in (-15, 15):
                    run(b["yaw"] + dy, b["tilt"])
        if best_iou() < IOU_FIT_MIN:
            # still no fit: a picture drawn from nearly overhead or with a near camera. Steeper tilts on the two best
            # turns so far and the turns opposite them (from overhead the front and back views differ mainly in which end
            # is at the top, and the level search does not tell them apart), then the two lenses on the two best of
            # those, then 15 degree steps around the best.
            tops = two_tops(list(tried.values()))
            for t in WIDE_TILTS:
                for y, _, _ in tops:
                    run(y, t)
                    run(y + 180, t)
            for y, t, _ in two_tops(list(tried.values())):
                for lens in WIDE_LENSES:
                    run(y, t, lens)
            b = max(tried.values(), key=lambda r: r["silhouette_iou"])
            if _is_wide(b):
                for dy in (-15, 15):
                    run(b["yaw"] + dy, b["tilt"], b["lens"])
    refused: dict[str, Any] | None = None
    pool_from = [r for r in tried.values() if not _is_wide(r)]
    wide_fits = sorted((r for r in tried.values() if _is_wide(r) and r["silhouette_iou"] >= IOU_FIT_MIN),
                       key=lambda r: -r["silhouette_iou"])[:WIDE_EDGE_TRIES]
    if wide_fits:
        target = _edge_target(pic, pic_mask)
        for r in wide_fits:
            r["edge_term"] = round(_edge_term(mesh, tex, target, r), 4)
            if r["edge_term"] >= EDGE_MIN:
                pool_from.append(r)
            elif refused is None:
                refused = {k: r[k] for k in ("yaw", "tilt", "lens", "silhouette_iou", "edge_term")}
        if any(_is_wide(r) for r in pool_from):
            refused = None
    top_iou = max(r["silhouette_iou"] for r in pool_from)
    # A boxy or round subject fits the silhouette at several turns: among the turns that fit about equally well, the one
    # whose low-pass colour agrees best with the picture wins.
    pool = [r for r in pool_from if r["silhouette_iou"] >= top_iou - 0.03]
    best = min(pool, key=lambda r: (r["delta_e"] if r.get("delta_e") is not None else 1e9))
    best = dict(best)
    best["fit_ok"] = best["silhouette_iou"] >= IOU_FIT_MIN
    if refused:
        best["wide_fit_refused"] = refused
    out: dict[str, Any] = {k: v for k, v in best.items() if k != "_images"}
    out["triangles"] = int(len(mesh["faces"]))
    out["texture_px"] = int(tex.shape[0])
    cover = uv_cover(mesh, tex.shape[0])
    out["atlas_used"] = round(float(cover.mean()), 3)
    out["real_detail_db"] = real_detail_db(tex, cover)
    out["brightness"] = round(float(face.luminance(tex)[cover].mean()), 4)
    if views:
        vs = view_sharpness(mesh, tex)
        out["view_sharpness"] = {str(v["yaw"]): v["sharpness"] for v in vs}
        out["mean_sharpness"] = round(float(np.mean([v["sharpness"] for v in vs])), 3)
    if keep_images and "_images" in best:
        out["_images"] = best["_images"]
    return out


def _is_wide(r: dict[str, Any]) -> bool:
    """Whether a candidate needed a wider camera than the level, 15 and 30 degree orthographic ones."""
    return r.get("lens") is not None or float(r.get("tilt") or 0.0) > max(FIT_TILTS)


def _edge_target(pic: np.ndarray, pic_mask: np.ndarray) -> Any:
    from . import posesearch

    return posesearch.target(_pad_square(pic_mask).astype(np.float64), posesearch.REFINE_N, _pad_square(pic))


def _edge_term(mesh: dict[str, np.ndarray], tex: np.ndarray, target: Any, r: dict[str, Any]) -> float:
    """posesearch's inner-edge term (model creases and colour edges against the picture's edges, 0..1) at a candidate's
    turn, tilt and lens, on posesearch's own framing (bounds centre, 1.2 x the bounds diagonal)."""
    from . import posesearch

    v = mesh["verts"]
    lo, hi = v.min(0), v.max(0)
    p = posesearch.score_pose(mesh, target, (lo + hi) / 2, float(np.linalg.norm(hi - lo)) * 1.2,
                              (float(r["yaw"]), float(r["tilt"]), r.get("lens")), tex)
    return float(p.terms.get("edge", 0.0))


def _yaw_apart(a: float, b: float) -> float:
    d = abs(float(a) - float(b)) % 360
    return min(d, 360 - d)


def save_comparison(result: dict[str, Any], dest: Path | str) -> Path | None:
    """Contact sheet: the render, the fitted picture, and a 1:1 crop of each around the middle of the figure."""
    im = result.get("_images")
    if not im:
        return None
    rm = im["render_mask"] | im["picture_mask"]
    ys, xs = np.nonzero(rm)
    cy, cx = int(np.median(ys)), int(np.median(xs))
    s = im["render"].shape[0]
    half = 160
    y0 = int(np.clip(cy - half, 0, s - 2 * half))
    x0 = int(np.clip(cx - half, 0, s - 2 * half))
    cell = 512
    tiles = []
    for key in ("render", "picture"):
        full = Image.fromarray(face.to_uint8(im[key])).resize((cell, cell), Image.LANCZOS)
        crop = Image.fromarray(face.to_uint8(im[key][y0:y0 + 2 * half, x0:x0 + 2 * half])).resize((cell, cell), Image.NEAREST)
        tiles.append((full, crop))
    sheet = Image.new("RGB", (cell * 2, cell * 2))
    for col, (full, crop) in enumerate(tiles):
        sheet.paste(full, (col * cell, 0))
        sheet.paste(crop, (col * cell, cell))
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dest)
    return Path(dest)


# --- whether step 2 redrew the picture ------------------------------------------------------------------------

# The step 1 picture's outline against step 2's drawn front, each cropped to its figure and scaled to one height: below
# this, step 2 redrew the subject (a changed pose or a changed view), and a model built from the redrawn views is
# compared with the built-from picture when the step 1 picture does not line up.
REDRAWN_IOU = 0.85
OUTLINE_N = 128


def _outline(path: Path | str) -> np.ndarray | None:
    a = np.asarray(Image.open(path).convert("RGBA"))[..., 3] > ALPHA_OPAQUE
    ys, xs = np.nonzero(a)
    if len(xs) < 20:
        return None
    c = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    w = min(2 * OUTLINE_N, max(1, int(round(c.shape[1] * OUTLINE_N / c.shape[0]))))
    r = np.asarray(Image.fromarray((c * 255).astype(np.uint8)).resize((w, OUTLINE_N), Image.BILINEAR)) >= 128
    out = np.zeros((OUTLINE_N, 2 * OUTLINE_N), bool)
    x0 = (2 * OUTLINE_N - w) // 2
    out[:, x0:x0 + w] = r
    return out


def outline_overlap(a: Path | str, b: Path | str) -> float | None:
    """IoU of two cut-outs' outlines, each cropped to its figure, scaled to one height and centred (scale and shift free).
    None when either has no cut-out."""
    oa, ob = _outline(a), _outline(b)
    return None if oa is None or ob is None else round(_iou(oa, ob), 4)


# --- report-only quality checks --------------------------------------------------------------------------------


def fidelity_checks(result: dict[str, Any]) -> list[dict[str, Any]]:
    """texture_sharp and texture_colour_matches_picture from a score() result. When the render did not line up with the
    picture (silhouette IoU under IOU_FIT_MIN), has no overlap, or could not be made, a wrong fit would blame the
    texture for a misregistration, so neither number is judged; both checks come back as "not measured"
    (quality.not_measured) with the reason, never silently missing. Both are severity warn, so they are shown and never
    block a run."""
    from . import quality

    names = (("texture_sharp", "how sharp the colour texture is"),
             ("texture_colour_matches_picture", "whether the colours match the picture"))
    if result.get("error") or not result.get("fit_ok"):
        iou = result.get("silhouette_iou")
        refused = result.get("wide_fit_refused") if not result.get("error") else None
        if result.get("error"):
            why = "the model could not be drawn from the picture's side to compare with it"
        elif refused:
            why = (f"the model's outline lined up with the picture only from a steep or close camera, and the shapes inside "
                   f"it did not match the picture's (edge agreement {float(refused['edge_term']):.2f}, it needs "
                   f"{EDGE_MIN:.2f}), so it may be a different subject or side, and any difference would be the fit's "
                   f"fault, not the texture's")
        else:
            why = ("the model drawn from the picture's side did not line up with the picture"
                   + (f" (outline overlap {float(iou):.2f}, it needs {IOU_FIT_MIN:.2f})" if iou is not None else "")
                   + ", so any difference would be the fit's fault, not the texture's")
        return [quality.not_measured(i, what, why) for i, what in names]
    out: list[dict[str, Any]] = []
    ratio = result.get("sharpness_ratio")
    if ratio is not None:
        out.append(quality.check("texture_sharp", ratio >= SHARP_RATIO_MIN, ratio, SHARP_RATIO_MIN,
                                 vars={"ssim": result.get("ssim"), "iou": result.get("silhouette_iou")}))
    else:
        out.append(quality.not_measured(names[0][0], names[0][1], "the picture and the model had no overlap to compare"))
    de = result.get("delta_e")
    if de is not None:
        out.append(quality.check("texture_colour_matches_picture", de <= DELTA_E_MAX, de, DELTA_E_MAX,
                                 vars={"delta_l": result.get("delta_l"), "delta_chroma": result.get("delta_chroma")}))
    else:
        out.append(quality.not_measured(names[1][0], names[1][1], "the picture and the model had no overlap to compare"))
    return out


def texture_checks_for(glb: Path | str, picture: Path | str | np.ndarray | Image.Image, base_color: Path | str | None = None) -> list[dict[str, Any]]:
    """The two report-only checks for a model and its picture, or [] when they cannot be measured (no picture cut-out, no
    texture, a poor silhouette fit, or any failure here: a measure must never break a stage)."""
    try:
        return fidelity_checks(score(glb, picture, base_color, views=False))
    except Exception:  # noqa: BLE001
        return []


STAGE_KEYS = ("yaw", "tilt", "lens", "edge_term", "wide_fit_refused", "silhouette_iou", "fit_ok", "ssim", "delta_e", "delta_l", "delta_chroma", "sharpness_ratio")


def measure_for_stage(glb: Path | str, picture: Path | str, base_color: Path | str | None = None) -> dict[str, Any]:
    """The few numbers a stage keeps in its metrics (texture_fidelity) and builds the two report-only checks from;
    {"error": ...} when nothing could be measured. Never raises."""
    try:
        r = score(glb, picture, base_color, views=False)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:200]}
    if r.get("error"):
        return {"error": r["error"]}
    return {k: r.get(k) for k in STAGE_KEYS}
