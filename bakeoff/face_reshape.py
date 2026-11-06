"""Face reshape prototype: move the model's face to the picture's proportions (instead of stretching the picture).

1. Keystones from the side outline (the profile), the same way on the picture and on the model's side render: the top
   of the head, the nose tip (most forward point in the face band), then down the profile the dips and bumps: under the
   nose, upper lip, between the lips, lower lip, the fold under the lip, chin front, chin bottom (where the outline
   turns back toward the neck).
2. Picture rows are put on the model's scale by two anchors both agree on: the top of the head and the nose tip.
3. The model's face vertices are moved up or down (and the chin forward or back) so each keystone lands on the
   picture's row: a piecewise-linear map of height between the keystones, applied only below the nose tip and only to
   the front of the head (weight by how far forward the vertex is), fading back to no move across the neck so the body
   join does not move. The texture moves with the vertices (UVs unchanged).
4. Measures before and after: keystone misses in pixels, and the side outline's match (IoU) against the picture's
   (head region, after the anchors). Sheet: side and front, shape and colour, before and after, picture outline drawn.

Usage: backend/.venv/Scripts/python.exe bakeoff/face_reshape.py <build_dir> <side_picture.png> <share> [--depth]
(share: how much of the model's height, from the top, the head and neck take: about 0.2 for a full figure, 0.7 for a head-and-shoulders build)
Output: <build_dir>/reshape/{model.glb, keystones.json, sheet.png}. Bake-off files only.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
from vgac import face  # noqa: E402

from keystone_fit import shape_image  # noqa: E402

KEYS = ("subnasale", "chin_front", "chin_bottom")
MAX_MOVE = 0.35  # of the top-to-nose-tip distance: a keystone further off than this is not trusted


def smooth1(a: np.ndarray, r: int) -> np.ndarray:
    k = np.ones(2 * r + 1) / (2 * r + 1)
    return np.convolve(np.pad(a, r, mode="edge"), k, mode="valid")


def outline(mask: np.ndarray, faces_left: bool) -> np.ndarray:
    """Per row, how far forward the outline reaches, as a coordinate that grows forward (nan where empty)."""
    H, W = mask.shape
    out = np.full(H, np.nan)
    for y in range(H):
        xs = np.where(mask[y])[0]
        if len(xs):
            out[y] = (W - xs.min()) if faces_left else xs.max()
    return out


def profile_keys(mask: np.ndarray, faces_left: bool, top_row: int | None = None) -> dict:
    """Keystone rows on a side silhouette (head and shoulders, face to one side): the top of the head, the nose tip
    (most forward point between a quarter and 70 percent of the way from the top to the neck), under the nose (the
    first dip below the tip), the neck (the furthest-back point of the outline in the 0.8 tip-heights under the tip,
    before a collar comes forward again), the chin bottom (between the chin front and the neck, the first row back 60
    percent of the way from the chin front to the neck) and the chin front (the most forward point between under the
    nose and the neck, skipping the lips: the lower half of that range)."""
    f = outline(mask, faces_left)
    rows = np.where(np.isfinite(f))[0]
    top = int(rows[0]) if top_row is None else int(top_row)  # top_row: the head's top when mask is the face's skin only
    g = smooth1(np.nan_to_num(f, nan=0.0), 3)
    H = mask.shape[0]
    # a first guess of the tip: the most forward point in the upper 60 percent of the figure's rows
    a, b = top + int(0.2 * (rows[-1] - top)), top + int(0.6 * (rows[-1] - top))
    tip = a + int(np.argmax(g[a:b]))
    D = tip - top
    neck = tip + int(np.argmin(g[tip: tip + int(0.8 * D)]))
    # under the nose: the first local dip below the tip
    sub = None
    for i in range(tip + 2, neck):
        if g[i] <= g[i - 1] and g[i] < g[i + 1] and g[tip] - g[i] > 0.01 * mask.shape[1]:
            sub = i
            break
    mid = (sub or tip) + (neck - (sub or tip)) // 2
    chin_front = mid + int(np.argmax(g[mid:neck])) if neck > mid + 2 else None
    chin_bottom = None
    if chin_front is not None:
        thr = g[chin_front] - 0.6 * (g[chin_front] - g[neck])
        w = np.where(g[chin_front: neck + 1] <= thr)[0]
        chin_bottom = int(chin_front + w[0]) if len(w) else None
    out = {"top": top, "tip": tip, "subnasale": sub, "chin_front": chin_front, "chin_bottom": chin_bottom, "neck": neck}
    out["forward_at"] = {k: float(g[out[k]]) for k in ("tip", "subnasale", "chin_front", "chin_bottom") if out.get(k) is not None}
    return out


def picture_mask(path: str, share: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """The picture and its figure mask; with share, cropped like the model's camera: from the figure's top down
    share x 1.1 of its height, square, centred on the head, enlarged to 1024."""
    rgb = face.read_rgb(path)
    m = face.fill_holes(rgb.max(axis=2) > 18 / 255)
    if share is not None and share < 0.5:
        rows = np.where(m.any(axis=1))[0]
        top, bot = int(rows[0]), int(rows[-1])
        span = share * (bot - top) * 1.1
        y0 = top - 0.04 * span
        xs = np.where(m[top: top + int(0.05 * (bot - top))].any(axis=0))[0]
        cx = 0.5 * (xs[0] + xs[-1])
        box = (cx - span / 2, y0, cx + span / 2, y0 + span)
        im = Image.fromarray(face.to_uint8(rgb)).crop(tuple(int(round(v)) for v in box)).resize((1024, 1024), Image.LANCZOS)
        rgb = np.asarray(im, dtype=np.float64) / 255.0
        m = face.fill_holes(rgb.max(axis=2) > 18 / 255)
    else:
        rgb = face.read_rgb(path, 1024)
        m = face.fill_holes(rgb.max(axis=2) > 18 / 255)
    return rgb, m


def main() -> None:
    build = Path(sys.argv[1]).resolve()
    side_pic = sys.argv[2]
    share = float(sys.argv[3])
    depth = "--depth" in sys.argv
    out = build / ("reshape_depth" if depth else "reshape")
    out.mkdir(exist_ok=True)
    mesh = face.load_mesh(build / "model.glb")
    tex = face.read_rgb(build / "base_color.png")
    head = face.find_head(mesh["verts"], share)
    # framed from the top of the model down `share` of its height (the head, neck and collar), with a little room
    span = share * head.height * 1.1
    tgt = np.array([head.centre[0], head.top - 0.5 * span + 0.04 * span, head.centre[2]])
    cam_s = face.Camera(target=tgt, yaw_deg=90, scale=span, size=1024)
    cam_f = face.Camera(target=tgt, yaw_deg=0, scale=span, size=1024)
    S, sc = 1024, cam_s.scale
    view_s = face.render_view(mesh, tex, cam_s)
    # which way the face points in the model's side render: the nose is the nearest point to the front camera
    vf = face.render_view(mesh, tex, cam_f)
    zf = np.where(vf["mask"], vf["raster"]["z"], np.inf)
    iy, ix = np.unravel_index(int(np.argmin(zf[200:800, 300:724])), zf[200:800, 300:724].shape)
    nose_world_z = float(face.interpolate(mesh["verts"], mesh["faces"], vf["raster"])[iy + 200, ix + 300, 2])
    # in the side camera, project a point in front of the head to see which side it lands on
    p, _ = cam_s.project(np.array([[head.centre[0], head.centre[1], head.centre[2] + 0.1]]))
    model_left = bool(p[0, 0] < S / 2)
    mk = profile_keys(view_s["mask"], model_left)

    pic_rgb, pm = picture_mask(side_pic, share)
    # the picture's face direction: compare the outline's reach on each side at the nose band
    rows = np.where(pm.any(axis=1))[0]
    mid = rows[0] + int(0.25 * (rows[-1] - rows[0]))
    xs = np.where(pm[mid])[0]
    cx = np.where(pm[rows[0]: rows[0] + 40].any(axis=0))[0].mean()
    pic_left = bool((cx - xs.min()) > (xs.max() - cx))
    pk = profile_keys(pm, pic_left)

    # anchors: top of the head and nose tip; picture rows -> model rows
    k = (mk["tip"] - mk["top"]) / (pk["tip"] - pk["top"])
    to_model = lambda r: mk["top"] + (r - pk["top"]) * k  # noqa: E731
    target = {n: to_model(pk[n]) for n in KEYS if pk.get(n) is not None}
    D = mk["tip"] - mk["top"]
    miss_before = {n: round(mk[n] - target[n], 1) for n in KEYS if mk.get(n) is not None and n in target}
    trusted = {n: v for n, v in target.items() if mk.get(n) is not None and abs(mk[n] - v) <= MAX_MOVE * D}
    print("model keys", {n: mk.get(n) for n in ("top", "tip", *KEYS)})
    print("picture keys (model scale)", {n: round(v, 1) for n, v in target.items()})
    print("miss before (px, model minus picture)", miss_before)

    # rows -> world height on the side camera (same target and scale as the front camera)
    def row_to_y(r: float) -> float:
        return float(cam_s.target[1] + (S / 2 - r) * sc / S)

    knots_m = [mk["tip"]] + [mk[n] for n in KEYS if n in trusted]
    knots_t = [mk["tip"]] + [trusted[n] for n in KEYS if n in trusted]
    order = np.argsort(knots_m)
    knots_m = list(np.array(knots_m)[order])
    knots_t = list(np.array(knots_t)[order])
    # below the last keystone: the move fades back to nothing over the neck (to the neck's deepest point plus a bit)
    fade_end = max(mk.get("neck") or knots_m[-1] + 0.3 * D, knots_m[-1] + 0.15 * D)
    knots_m.append(fade_end)
    knots_t.append(fade_end)
    ym = np.array([row_to_y(r) for r in knots_m])
    yt = np.array([row_to_y(r) for r in knots_t])

    v = mesh["verts"].copy()
    # which vertices: the front of the head below the nose tip; weight by forwardness so cheeks follow less, the ears
    # and the back of the head not at all
    fwd = (v[:, 2] - head.centre[2]) / max(nose_world_z - head.centre[2], 1e-6)
    w = np.clip((fwd - 0.0) / 0.5, 0.0, 1.0)
    inside = (v[:, 1] <= ym[0]) & (v[:, 1] >= ym[-1]) & (np.abs(v[:, 0] - head.centre[0]) < 0.75 * head.width)
    dy = np.interp(-v[:, 1], -ym, yt - ym)  # np.interp needs increasing x: use -y (rows grow downward)
    new = v.copy()
    new[:, 1] = v[:, 1] + np.where(inside, dy * w, 0.0)
    if depth:
        # the chin's forwardness from the picture, relative to the nose tip, on the model's scale
        def fwd_px(keys: dict, name: str) -> float | None:
            fa = keys["forward_at"]
            return fa.get(name) - fa["tip"] if name in fa else None
        dz_px = []
        for n in ("chin_front", "chin_bottom"):
            a_, b_ = fwd_px(mk, n), fwd_px(pk, n)
            if a_ is not None and b_ is not None and n in trusted:
                dz_px.append((trusted[n], b_ * k - a_))
        if dz_px:
            rws = np.array([r for r, _ in dz_px])
            dzs = np.array([d_ for _, d_ in dz_px]) * sc / S
            o = np.argsort(rws)
            yr = np.array([row_to_y(r) for r in rws[o]])
            dz = np.interp(-new[:, 1], -yr, dzs[o], left=0.0, right=0.0)
            new[:, 2] = new[:, 2] + np.where(inside, dz * w, 0.0)
    mesh2 = dict(mesh)
    mesh2["verts"] = new
    after_s = face.render_view(mesh2, tex, cam_s)
    mk2 = profile_keys(after_s["mask"], model_left)
    miss_after = {n: round(mk2[n] - target[n], 1) for n in KEYS if mk2.get(n) is not None and n in target}
    print("miss after", miss_after)

    # outline match: the picture's silhouette laid on the model by the anchors (scale k about the top row, and the nose
    # tip's forward position), head region only (top to the chin bottom target)
    def placed_picture_mask() -> np.ndarray:
        H = pm.shape[0]
        Y, X = np.mgrid[0:S, 0:S]
        src_y = pk["top"] + (Y - mk["top"]) / k
        # forward coordinate: model forward f_m = picture forward * k + offset (nose tips equal)
        fm_tip = mk["forward_at"]["tip"]
        fp_tip = pk["forward_at"]["tip"]
        fwd_m = (S - X) if model_left else X
        fwd_p = (fwd_m - fm_tip) / k + fp_tip
        src_x = (pm.shape[1] - fwd_p) if pic_left else fwd_p
        ok = (src_y >= 0) & (src_y < H - 1) & (src_x >= 0) & (src_x < pm.shape[1] - 1)
        mm = np.zeros((S, S), dtype=bool)
        mm[ok] = pm[src_y[ok].astype(int), src_x[ok].astype(int)]
        return mm

    placed = placed_picture_mask()
    cb = int(target.get("chin_bottom") or target.get("chin_front") or mk["tip"] + D)
    region = np.zeros((S, S), dtype=bool)
    region[mk["top"]: cb + 1] = True

    def iou(a: np.ndarray) -> float:
        a, b = a & region, placed & region
        return round(float((a & b).sum() / max(1, (a | b).sum())), 4)

    rec = {"model_before": {n: mk.get(n) for n in ("top", "tip", *KEYS, "neck")}, "model_after": {n: mk2.get(n) for n in ("top", "tip", *KEYS)},
           "picture_on_model_scale": {n: round(v_, 1) for n, v_ in target.items()}, "scale_picture_to_model": round(k, 4),
           "miss_before_px": miss_before, "miss_after_px": miss_after, "trusted": sorted(trusted),
           "side_iou_before": iou(view_s["mask"]), "side_iou_after": iou(after_s["mask"]), "depth": depth}
    print("side outline match", rec["side_iou_before"], "->", rec["side_iou_after"])
    with open(out / "keystones.json", "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1, default=float)

    # Save the reshaped model (only the vertex positions change).
    np.save(out / "verts.npy", new)

    # Sheet: side shape before | side shape after | side colour after | front colour before | front colour after,
    # the picture's outline in red and its keystones as ticks.
    edge = placed & ~face.erode(placed, 2)
    after_f = face.render_view(mesh2, tex, cam_f)

    def tile(img: np.ndarray, keys: dict | None, draw_edge: bool) -> Image.Image:
        a = img.copy()
        if draw_edge:
            a[edge] = (0.9, 0.15, 0.15)
        im = Image.fromarray(face.to_uint8(a))
        d = ImageDraw.Draw(im)
        for n, r in target.items():
            d.line([(0, r), (60, r)], fill=(220, 40, 40), width=3)
        if keys:
            for n in KEYS:
                if keys.get(n) is not None:
                    d.line([(S - 60, keys[n]), (S, keys[n])], fill=(40, 200, 40), width=3)
        return im.crop((128, max(0, mk["top"] - 40), 896, min(S, cb + 200))).resize((384, 384 * (min(S, cb + 200) - max(0, mk["top"] - 40)) // 768))

    tiles = [tile(shape_image(mesh, view_s), mk, True), tile(shape_image(mesh2, after_s), mk2, True), tile(after_s["image"], None, False),
             tile(vf["image"], None, False), tile(after_f["image"], None, False)]
    hh = max(t.height for t in tiles)
    sheet = Image.new("RGB", (384 * len(tiles), hh), (40, 40, 40))
    for i, t in enumerate(tiles):
        sheet.paste(t, (384 * i, 0))
    sheet.save(out / "sheet.png")


if __name__ == "__main__":
    main()
