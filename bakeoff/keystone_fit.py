"""Keystone fit prototype: lay the painted face on the model by its key features, each to its own place.

Today (stages/face_detail.fit_picture) the picture gets one scale and one shift, from the eye line and the mouth found
on the model's blurred texture; when those are not found it falls back to a whole-face match. The chin, the nose's
length and the jaw are never checked, so a model whose lower face is longer than the painting's gets the painted chin
too high (the bake-off knight at 4096: about 70 px of 1024 short).

Here the model's keystones come from its shape (the depth down the face's centre line, from face.render_view's
raster), not its texture:
- eye line: the deepest point between the nose and the forehead (the bridge of the nose);
- nose bottom: the steepest step back under the nose tip;
- mouth: half way between the nose bottom and the fold under the lower lip (the deepest point above the chin);
- chin bottom: where the surface turns under the chin toward the neck.
The picture's come from light and dark: the eyes (face.landmarks), the nostril line (the darkest row between the eyes
and the mouth), the mouth (face.landmarks), the chin bottom (the first strong drop from the lit chin into the shadow
under it). The picture is then stretched up and down piecewise between those rows (each band's stretch bounded), and
across by the eye-to-mouth stretch (the eye spacing stays the painting's), centred on the nose.

Usage: backend/.venv/Scripts/python.exe bakeoff/keystone_fit.py <build_dir>   (after face_closeup_trial.py on it)
Writes <build_dir>/face_trial/keystone/: overlay.png (shape, today's fit, keystone fit), keystones.json, and the app
close-up painted through the keystone fit (base_color_face.png, sheet.png with today's fit for comparison).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
from vgac import face  # noqa: E402
from vgac.stages import face_detail as fd  # noqa: E402

ACROSS = float(sys.argv[sys.argv.index("--across") + 1]) if "--across" in sys.argv else None  # fixed stretch across
BAND_STRETCH = (0.6, 1.7)  # a band stretched past this is not the same feature: its knot is dropped


def centre_profile(mesh: dict, view: dict, nx: int) -> np.ndarray:
    """Forward position (model z) down the face's centre line, averaged over a few columns; nan off the model."""
    pos = face.interpolate(mesh["verts"], mesh["faces"], view["raster"])
    z = pos[:, nx - 2: nx + 3, 2]
    ok = view["mask"][:, nx - 2: nx + 3]
    with np.errstate(invalid="ignore"):
        return np.where(ok.all(axis=1), z.mean(axis=1), np.nan)


def smooth1(a: np.ndarray, r: int) -> np.ndarray:
    k = np.ones(2 * r + 1) / (2 * r + 1)
    p = np.pad(a, r, mode="edge")
    return np.convolve(p, k, mode="valid")


def mesh_keystones(mesh: dict, view: dict, info: dict, face_width_m: float) -> dict:
    """The model's keystone rows. From the shape: the nose bottom (the steepest step back under the tip), the fold
    under the lower lip (the deepest point 0.3 to 0.7 face widths under the tip), the chin's front (the most forward
    point in the 0.3 face widths under the fold) and the chin bottom (the first row 0.15 face widths, in metres, behind
    the chin's front). The mouth is placed a third of the way from the nose bottom to the chin bottom (the usual face
    proportion; at this triangle count the lips are hardly modelled). The eye line comes from the texture's eye smudges
    (face.landmarks, as today's fit does): the shape models no eye sockets, and the bridge of the nose is too shallow
    to find."""
    nx, ny = info["nose"]
    w = float(info["width_px"])
    z = centre_profile(mesh, view, nx)
    zs = smooth1(np.nan_to_num(z, nan=np.nanmin(z)), max(1, int(w / 100)))
    g = np.gradient(zs)
    a, b = int(ny + 0.02 * w), int(ny + 0.22 * w)
    nose_b = a + int(np.argmin(g[a:b]))
    a, b = int(ny + 0.3 * w), int(ny + 0.7 * w)
    fold = a + int(np.argmin(zs[a:b]))
    front = fold + int(np.argmax(zs[fold: fold + int(0.3 * w)]))
    end = min(len(zs) - 1, int(ny + 1.2 * w))
    drop = np.where(zs[front:end] < zs[front] - 0.15 * face_width_m)[0]
    chin = front + int(drop[0]) if len(drop) else None
    mouth = int(round(nose_b + (chin - nose_b) / 3)) if chin else None
    lm = face.landmarks(view["image"], info)
    eye = float(np.mean([e[1] for e in lm["eyes"]])) if lm else None
    return {"eye": eye, "nose": nose_b, "mouth": mouth, "chin": chin, "fold": fold, "chin_front": front, "nose_tip": ny,
            "profile_every_10px": [None if np.isnan(v) else round(float(v - zs[ny]), 4) for v in z[ny: end: 10]]}


def picture_keystones(img: np.ndarray, info: dict) -> dict | None:
    lm = face.landmarks(img, info)
    if lm is None:
        return None
    w = float(info["width_px"])
    eye = float(np.mean([e[1] for e in lm["eyes"]]))
    mouth = float(lm["mouth"][1])
    cx = int(round(lm["mouth"][0]))
    lum = face.blur(face.luminance(img), max(1.0, w / 200))
    col = lum[:, cx - int(0.04 * w): cx + int(0.04 * w) + 1].mean(axis=1)
    # nostril line: the darkest row 0.45 to 0.85 of the way from the eyes to the mouth
    a, b = int(eye + 0.45 * (mouth - eye)), int(eye + 0.85 * (mouth - eye))
    nose = a + int(np.argmin(col[a:b]))
    # chin bottom: below the lower lip's shadow the lit chin, then the first row darker than 65 percent of it
    a = int(mouth + 0.12 * w)
    b = min(len(col) - 1, int(mouth + 0.7 * w))
    lit = a + int(np.argmax(col[a: int(mouth + 0.45 * w)]))
    dark = np.where(col[lit:b] < 0.65 * col[lit])[0]
    chin = lit + int(dark[0]) if len(dark) else None
    return {"eye": eye, "nose": float(nose), "mouth": mouth, "chin": None if chin is None else float(chin), "chin_front": float(lit),
            "nose_x": float(lm["mouth"][0]), "eyes": lm["eyes"]}


def knots(src: dict, dst: dict) -> tuple[list[float], list[float]]:
    """Matching rows (picture, model) in order, dropping a knot whose band would stretch out of BAND_STRETCH."""
    keys = [k for k in ("eye", "nose", "mouth", "chin") if src.get(k) is not None and dst.get(k) is not None]
    P = [float(src[k]) for k in keys]
    T = [float(dst[k]) for k in keys]
    changed = True
    while changed and len(P) > 2:
        changed = False
        for i in range(len(P) - 1):
            s = (T[i + 1] - T[i]) / max(P[i + 1] - P[i], 1e-6)
            if not (BAND_STRETCH[0] <= s <= BAND_STRETCH[1]):
                j = i + 1 if i + 1 < len(P) - 1 or i == 0 else i
                del P[j], T[j], keys[j]
                changed = True
                break
    return P, T


def keystone_warp(img: np.ndarray, src: dict, dst: dict, nose_x_dst: float) -> tuple[np.ndarray, dict]:
    P, T = knots(src, dst)
    H, W = img.shape[:2]
    rows = np.arange(H) + 0.5
    # output row y takes picture row interp(y): linear through the knots, the end bands carried on past them
    k0 = (P[1] - P[0]) / (T[1] - T[0])
    k1 = (P[-1] - P[-2]) / (T[-1] - T[-2])
    sy = np.interp(rows, T, P)
    sy = np.where(rows < T[0], P[0] + (rows - T[0]) * k0, sy)
    sy = np.where(rows > T[-1], P[-1] + (rows - T[-1]) * k1, sy)
    # across: the eye-to-mouth stretch, centred on the nose
    ie, im = (P.index(src["eye"]) if src["eye"] in P else 0), (len(P) - 1)
    kx = ACROSS if ACROSS is not None else float(np.clip((T[im] - T[ie]) / (P[im] - P[ie]), 0.85, 1.2))
    cols = np.arange(W) + 0.5
    sx = src["nose_x"] + (cols - nose_x_dst) / kx
    X, Y = np.meshgrid(sx, sy)
    return face.sample(img, X, Y), {"rows_picture": P, "rows_model": T, "stretch_across": round(kx, 4),
                                     "band_stretch": [round((T[i + 1] - T[i]) / (P[i + 1] - P[i]), 3) for i in range(len(P) - 1)]}


def shape_image(mesh: dict, view: dict) -> np.ndarray:
    nrm = face.interpolate(face.smooth_normals(mesh["verts"], mesh["faces"]), mesh["faces"], view["raster"])
    shade = np.clip(nrm[..., 2:3] * 0.8 + 0.2, 0, 1) * np.ones(3)
    return np.where(view["mask"][..., None], shade, 0.25)


def mark(img: np.ndarray, rows: dict, colour: tuple, x0: int, x1: int) -> Image.Image:
    im = Image.fromarray(face.to_uint8(img))
    d = ImageDraw.Draw(im)
    for k, y in rows.items():
        if y is not None and k in ("eye", "nose", "mouth", "chin"):
            d.line([(x0, y), (x1, y)], fill=colour, width=2)
    return im


def main() -> None:
    build = Path(sys.argv[1]).resolve()
    trial = build / "face_trial"
    out = trial / ("keystone" if ACROSS is None else f"keystone_across{ACROSS:g}")
    out.mkdir(exist_ok=True)
    glb, base = build / "model.glb", build / "base_color.png"
    mesh = face.load_mesh(glb)
    tex = face.read_rgb(base)
    head = face.find_head(mesh["verts"])
    cam = face.head_camera(head, 0, 1024)
    view = face.render_view(mesh, tex, cam)
    info = face.find_face(view, mesh, cam, head)
    dens = face.face_texels(mesh, cam, info["region"], tex.shape[0])
    crop = face.read_rgb(trial / "painted_head.png", 1024)
    picture = face.read_rgb(trial / "app_11" / "merged.png", 1024)  # the app's checked close-up, in the crop's framing

    today = fd.fit_picture(crop, view["image"], info, face.inner_face(info))
    fitted_crop = face.apply_alignment(crop, today)
    mk = mesh_keystones(mesh, view, info, float(dens["face_width_m"]))
    pk = picture_keystones(fitted_crop, info)
    if pk is None:
        raise SystemExit("the picture's eyes and mouth were not found")
    warped_crop, how = keystone_warp(fitted_crop, pk, mk, float(info["nose"][0]))
    warped_pic, _ = keystone_warp(face.apply_alignment(picture, today), pk, mk, float(info["nose"][0]))
    after = picture_keystones(warped_crop, info)
    rec = {"today_fit": {k: today.get(k) for k in ("how", "sx", "sy", "tx", "ty", "score")}, "model": mk, "picture_today": pk,
           "picture_keystone": after, "warp": how,
           "miss_today_px": {k: (None if pk.get(k) is None or mk.get(k) is None else round(pk[k] - mk[k], 1)) for k in ("eye", "nose", "mouth", "chin")},
           "miss_keystone_px": {k: (None if (after or {}).get(k) is None or mk.get(k) is None else round(after[k] - mk[k], 1)) for k in ("eye", "nose", "mouth", "chin")},
           "face_width_px": info["width_px"]}

    # Paint the app's close-up through the keystone fit: fit_and_paint with its fit replaced by "already in place".
    face.save_rgb(warped_pic, out / "picture_keystone.png")
    face.save_rgb(warped_crop, out / "crop_keystone.png")
    real_fit = fd.fit_picture
    fd.fit_picture = lambda src, render, inf, inner: {"sx": 1.0, "sy": 1.0, "tx": 0.0, "ty": 0.0, "centre": (512.0, 512.0),
                                                       "how": "keystones", "residual_px": 0.0,
                                                       "score": face.feature_match(src, render, inner, 6.0, 24.0)}
    try:
        done = fd.fit_and_paint(glb, base, out / "picture_keystone.png", glb, base, out / "crop_keystone.png", out, 1.0, None,
                                out / "picture_keystone.png", False, True)
    finally:
        fd.fit_picture = real_fit
    rec["paint"] = {k: done["metrics"].get(k) for k in ("sharpness_after", "feature_match_after", "fit_match", "eye_colour_shift")} if done.get("ok") else done.get("reason")
    with open(out / "keystones.json", "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1, default=str)

    # Overlay: the shape with its keystones (green), today's fit over it with the picture's (red), the keystone fit (both).
    shape = shape_image(mesh, view)
    x0, y0, x1, y1 = info["box"]
    pad = int(0.35 * info["width_px"])
    box = (max(0, x0 - pad), max(0, y0 - pad), min(1024, x1 + pad), min(1024, y1 + pad))
    a = mark(shape, mk, (40, 200, 40), x0, x1)
    b = mark(0.5 * shape + 0.5 * fitted_crop, mk, (40, 200, 40), x0, x1)
    b = mark(np.asarray(b) / 255.0, pk, (220, 40, 40), x0, x1)
    c = mark(0.5 * shape + 0.5 * warped_crop, mk, (40, 200, 40), x0, x1)
    c = mark(np.asarray(c) / 255.0, after or {}, (220, 40, 40), x0, x1)
    tiles = [im.crop(box) for im in (a, b, c)]
    sheet = Image.new("RGB", (sum(t.width for t in tiles), tiles[0].height))
    x = 0
    for t in tiles:
        sheet.paste(t, (x, 0))
        x += t.width
    sheet.save(out / "overlay.png")

    # Painted: today's fit (face_trial/app_11) against the keystone fit, front and three-quarter, face crops.
    def renders(tex_path: Path) -> list[np.ndarray]:
        tx = face.read_rgb(tex_path)
        return [face.render_view(mesh, tx, face.head_camera(head, yaw, 1024))["image"] for yaw in (0, 35)]

    rows = [renders(trial / "app_11" / "base_color_face.png")]
    if done.get("ok"):
        rows.append(renders(Path(done["base_color"])))
    cell = 448
    ps = Image.new("RGB", (cell * 2, cell * len(rows)))
    for i, r in enumerate(rows):
        for j, img in enumerate(r):
            ps.paste(Image.fromarray(face.to_uint8(img)).crop(box).resize((cell, cell), Image.LANCZOS), (j * cell, i * cell))
    ps.save(out / "painted.png")
    print(json.dumps({k: rec[k] for k in ("today_fit", "model", "picture_today", "miss_today_px", "miss_keystone_px", "warp", "paint")}, indent=1, default=str))


if __name__ == "__main__":
    main()
