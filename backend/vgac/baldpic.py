"""Picture maths for "Hair from the library" (docs/HAIR-LIBRARY-PLAN.md section 2): the hair outline of the concept
picture, found by comparing it with the bald picture drawn from it, plus the measures the fit needs. numpy and PIL only.

Port of the wig trial's fitprep.py and the hair-library trial's prep_pic.py (runtime/tmp/wigtrial, runtime/tmp/hairlib):
 - measure_target: from the bald picture's cut-out: figure height, top, the neck (narrowest row 8 to 25 percent down),
   head centre, head width and height, all as the trial's fit_target.json (`H_px, top_px, neck_px, head_cx_px, head_w,
   head_h`, head_w and head_h as shares of the figure height).
 - outline_mask: where the concept and the bald picture differ near the head (kept when connected to the head box),
   refined by colour: pixels near the hair's median colour (Lab) inside a grown copy that also differ from the bald
   picture; small holes closed; specks dropped.
 - hair_colour: median of the hair pixels and the medians of their darkest 15 and lightest 12 percent (sRGB).
 - eye_boxes: the two darkest blobs in the eye band of the bald picture's head; eye_cover: the hair's share of each box.
 - kept_measure: how much of the picture outside the hair the bald edit left unchanged, and how far the face moved.
All radii were tuned at 1024 px and scale with the picture size."""
from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def _arr(img: Any, mode: str) -> np.ndarray:
    if isinstance(img, np.ndarray):
        return img
    if isinstance(img, Image.Image):
        return np.asarray(img.convert(mode))
    return np.asarray(Image.open(img).convert(mode))


def _rgb(img: Any) -> np.ndarray:
    a = _arr(img, "RGB")
    return a[..., :3].astype(float) if a.ndim == 3 else np.repeat(a[..., None], 3, -1).astype(float)


def _alpha(img: Any) -> np.ndarray:
    a = _arr(img, "RGBA")
    if a.ndim == 3 and a.shape[2] == 4:
        return a[..., 3] > 127
    return np.ones(a.shape[:2], bool)


def _r(n: float, size: int) -> int:
    return int(round(n * size / 1024))


def morph(m: np.ndarray, r: int, op: str) -> np.ndarray:
    if r <= 0:
        return m.copy()
    im = Image.fromarray(m.astype(np.uint8) * 255)
    f = ImageFilter.MaxFilter if op == "dilate" else ImageFilter.MinFilter
    for _ in range(r):
        im = im.filter(f(3))
    return np.asarray(im) > 127


def components(m: np.ndarray) -> tuple[np.ndarray, list[int]]:
    """4-connected components (row runs joined with a union-find). Returns (labels, sizes) with labels 1..n."""
    m = np.asarray(m, bool)
    H, W = m.shape
    runs = []          # (row, start, end) end exclusive
    row_runs = []
    for y in range(H):
        r = m[y]
        if not r.any():
            row_runs.append([])
            continue
        d = np.diff(np.concatenate([[0], r.astype(np.int8), [0]]))
        st, en = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
        ids = []
        for s, e in zip(st, en):
            ids.append(len(runs)); runs.append((y, int(s), int(e)))
        row_runs.append(ids)
    parent = list(range(len(runs)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for y in range(1, H):
        prev, cur = row_runs[y - 1], row_runs[y]
        i = j = 0
        while i < len(prev) and j < len(cur):
            _, ps, pe = runs[prev[i]]
            _, cs, ce = runs[cur[j]]
            if ps < ce and cs < pe:
                a, b = find(prev[i]), find(cur[j])
                if a != b:
                    parent[b] = a
            if pe < ce:
                i += 1
            else:
                j += 1
    lab = np.zeros((H, W), np.int32)
    root_id: dict[int, int] = {}
    sizes = [0]
    for k, (y, s, e) in enumerate(runs):
        r = find(k)
        if r not in root_id:
            root_id[r] = len(sizes)
            sizes.append(0)
        n = root_id[r]
        lab[y, s:e] = n
        sizes[n] += e - s
    return lab, sizes


def fill_small_holes(m: np.ndarray, share: float = 0.01) -> np.ndarray:
    """Close holes smaller than `share` of the mask (the face inside a bob is not a hole)."""
    pad = np.pad(m, 1).astype(np.uint8)
    im = Image.fromarray(pad * 255)
    ImageDraw.floodfill(im, (0, 0), 128, thresh=0)
    outside = np.asarray(im)[1:-1, 1:-1] == 128
    holes = ~m & ~outside
    if not holes.any():
        return m
    lab, sizes = components(holes)
    out = m.copy()
    lim = share * max(1, int(m.sum()))
    for i in range(1, len(sizes)):
        if sizes[i] < lim:
            out |= lab == i
    return out


def to_lab(rgb: np.ndarray) -> np.ndarray:
    c = np.asarray(rgb, float) / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    M = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = c @ M.T / np.array([0.9505, 1.0, 1.089])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def measure_target(bald_rgba: Any) -> dict[str, Any]:
    """The trial's picture measures from the bald cut-out (see the module text)."""
    cut = _alpha(bald_rgba)
    ys, xs = np.nonzero(cut)
    if not len(ys):
        raise ValueError("the bald picture's cut-out is empty")
    top, bot = int(ys.min()), int(ys.max())
    H = max(1, bot - top)
    widths = cut[top:top + int(0.25 * H) + 1].sum(1)
    lo, hi = int(0.08 * H), max(int(0.08 * H) + 1, int(0.25 * H))
    neck = top + lo + int(np.argmin(widths[lo:hi]))
    head_rows = cut[top:neck]
    hx = np.nonzero(head_rows)[1]
    head_cx = float(np.median(hx))
    rw = [r.sum() for r in head_rows if r.sum()]
    head_w = float(np.percentile(rw, 90)) if rw else 0.1 * H
    return dict(H_px=int(H), top_px=top, neck_px=int(neck), head_cx_px=head_cx, head_w=head_w / H, head_h=(neck - top) / H,
                img_w=int(cut.shape[1]), img_h=int(cut.shape[0]))


def _resize_like(img: Any, shape: tuple[int, int], mode: str) -> np.ndarray:
    im = img if isinstance(img, Image.Image) else (Image.fromarray(img) if isinstance(img, np.ndarray) else Image.open(img))
    im = im.convert(mode)
    if im.size != (shape[1], shape[0]):
        im = im.resize((shape[1], shape[0]), Image.LANCZOS)
    return np.asarray(im)


def outline_mask(orig_rgba: Any, bald_rgba: Any) -> np.ndarray:
    """Hair outline of the concept picture (bool, the concept's size). bald_rgba: the bald picture's cut-out (RGBA);
    it is resized to the concept when the edit came back at another size."""
    orig = _rgb(orig_rgba)
    H, W = orig.shape[:2]
    size = max(H, W)
    bald_a = _resize_like(bald_rgba, (H, W), "RGBA")
    cut = bald_a[..., 3] > 127
    # the cut-out over the concept's own background colour (median of its border), so only the figure is compared
    border = np.concatenate([orig[0], orig[-1], orig[:, 0], orig[:, -1]])
    bg = np.median(border, 0)
    al = bald_a[..., 3:4].astype(float) / 255.0
    bald = bald_a[..., :3].astype(float) * al + bg * (1 - al)
    t = measure_target(Image.fromarray(bald_a, "RGBA"))
    top, neck, Hf = t["top_px"], t["neck_px"], t["H_px"]
    hw = t["head_w"] * Hf
    # 1. the trial's difference mask, kept where connected to the head box (fitprep.py)
    diff = np.abs(orig - bald).sum(-1) > 75
    diff = morph(morph(diff, _r(2, size), "erode"), _r(2, size), "dilate")
    box = np.zeros((H, W), bool)
    x0, x1 = int(t["head_cx_px"] - 1.2 * hw), int(t["head_cx_px"] + 1.2 * hw)
    box[max(0, top - int(0.08 * Hf)):neck, max(0, x0):max(0, x1)] = True
    lab, sizes = components(diff)
    ids = np.unique(lab[box & diff])
    m0 = np.isin(lab, ids[ids > 0])
    if m0.sum() < 20:
        return m0
    # 2. colour refinement (prep_pic.py)
    lab_img = to_lab(orig)
    core = morph(m0, _r(3, size), "erode")
    if core.sum() < 50:
        core = m0
    med = np.median(lab_img[core], 0)
    dist = np.linalg.norm(lab_img - med, axis=-1)
    spread = np.percentile(np.linalg.norm(lab_img[core] - med, axis=-1), 80)
    diff2 = np.abs(orig - bald).sum(-1) > 45
    grown = morph(m0, _r(14, size), "dilate")
    lim = max(14.0, 1.6 * spread)
    hair = (grown & (dist < lim) & diff2) | (m0 & (dist < 1.6 * lim))
    r2 = _r(2, size)
    hair = morph(morph(hair, r2, "dilate"), r2, "erode")
    hair = fill_small_holes(hair)
    hair = morph(morph(hair, r2, "erode"), r2, "dilate")
    lab2, sz2 = components(hair)
    if len(sz2) > 1:
        big = max(sz2[1:])
        keep = [i for i in range(1, len(sz2)) if sz2[i] >= max(150 * (size / 1024) ** 2, 0.04 * big)]
        hair = np.isin(lab2, keep)
    return hair


def hair_colour(orig: Any, mask: np.ndarray) -> dict[str, list[float]]:
    px = _rgb(orig)[np.asarray(mask, bool)]
    if not len(px):
        raise ValueError("no hair pixels")
    L = px.mean(-1)
    return dict(median=np.median(px, 0).round(1).tolist(), dark=np.median(px[L <= np.percentile(L, 15)], 0).round(1).tolist(),
                light=np.median(px[L >= np.percentile(L, 88)], 0).round(1).tolist())


def eye_boxes(bald: Any, target: dict[str, Any]) -> list[list[float]]:
    """Two boxes [x0, y0, x1, y1] on the eyes: the darkest blob on each side of the eye band of the head."""
    rgb = _rgb(bald)
    lum = rgb.mean(-1)
    top, Hp = target["top_px"], target["H_px"]
    hh = target["head_h"] * Hp
    cx = target["head_cx_px"]
    hw = target["head_w"] * Hp
    y0, y1 = int(top + 0.38 * hh), max(int(top + 0.38 * hh) + 2, int(top + 0.66 * hh))
    boxes = []
    for sgn in (-1, 1):
        xa, xb = int(cx + sgn * 0.06 * hw), int(cx + sgn * 0.42 * hw)
        xa, xb = max(0, min(xa, xb)), max(min(xa, xb) + 2, max(xa, xb))
        reg = lum[y0:y1, xa:xb]
        k = max(1, int(0.05 * hw))
        r = np.asarray(Image.fromarray(np.clip(reg, 0, 255).astype(np.uint8)).filter(ImageFilter.BoxBlur(k))).astype(float)
        yy, xx = np.unravel_index(int(np.argmin(r)), r.shape)
        ex, ey = float(xa + xx), float(y0 + yy)
        ew, eh = 0.20 * hw, 0.10 * hw
        boxes.append([ex - ew / 2, ey - eh / 2, ex + ew / 2, ey + eh / 2])
    return boxes


def eye_cover(mask: np.ndarray, boxes: list[list[float]]) -> list[float]:
    out = []
    for b in boxes:
        bb = [int(round(v)) for v in b]
        reg = np.asarray(mask)[max(0, bb[1]):bb[3], max(0, bb[0]):bb[2]]
        out.append(float(reg.mean()) if reg.size else 0.0)
    return out


def kept_measure(orig: Any, bald: Any, mask: np.ndarray) -> dict[str, float]:
    """kept_share: of the figure outside the (grown) hair, the share the edit left unchanged (colour sum difference
    under 60). face_delta: mean absolute colour difference (0..255) over the face (head box below the hair)."""
    o = _rgb(orig)
    H, W = o.shape[:2]
    b_a = _resize_like(bald, (H, W), "RGBA")
    b = b_a[..., :3].astype(float)
    cut = b_a[..., 3] > 127 if b_a.shape[2] == 4 else np.ones((H, W), bool)
    grown = morph(np.asarray(mask, bool), _r(10, max(H, W)), "dilate")
    region = cut & ~grown
    d = np.abs(o - b).sum(-1)
    kept = float((d[region] < 60).mean()) if region.any() else 0.0
    t = measure_target(Image.fromarray(b_a, "RGBA")) if b_a.shape[2] == 4 else None
    face_delta = 0.0
    if t:
        hw = t["head_w"] * t["H_px"]
        face = np.zeros((H, W), bool)
        face[int(t["top_px"] + 0.3 * (t["neck_px"] - t["top_px"])):t["neck_px"],
             max(0, int(t["head_cx_px"] - 0.4 * hw)):int(t["head_cx_px"] + 0.4 * hw)] = True
        face &= ~grown & cut
        if face.any():
            face_delta = float(np.abs(o - b)[face].mean())
    return dict(kept_share=round(kept, 4), face_delta=round(face_delta, 2))


def picture_record(orig: Any, bald_cutout: Any, bald_rgb: Any | None = None) -> tuple[np.ndarray, dict[str, Any]]:
    """Everything the `hair_picture` file holds, plus the outline mask: (mask, {target, eye_boxes, pic_eye_cover,
    colour, outline_px})."""
    mask = outline_mask(orig, bald_cutout)
    o = _rgb(orig)
    b_a = _resize_like(bald_cutout, o.shape[:2], "RGBA")
    target = measure_target(Image.fromarray(b_a, "RGBA"))
    ys, xs = np.nonzero(mask)
    if len(ys):
        Hf = target["H_px"]
        target.update(hair_x0=(xs.min() - target["head_cx_px"]) / Hf, hair_x1=(xs.max() - target["head_cx_px"]) / Hf,
                      hair_top=(target["top_px"] - ys.min()) / Hf, hair_bottom=(ys.max() - target["top_px"]) / Hf)
    boxes = eye_boxes(bald_rgb if bald_rgb is not None else b_a[..., :3], target)
    rec = dict(target=target, eye_boxes=boxes, pic_eye_cover=eye_cover(mask, boxes),
               colour=hair_colour(orig, mask) if mask.any() else None, outline_px=int(mask.sum()))
    return mask, rec


def target(bald_rgba: Any) -> dict[str, Any]:
    """Alias of measure_target (the name the turnaround step looks for)."""
    return measure_target(bald_rgba)
