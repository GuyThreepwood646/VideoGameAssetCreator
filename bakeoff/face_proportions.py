"""Face proportions: the painting against each model, measured the same way (bakeoff/keystone_fit.py's keystones).

Rows: eye line, nose bottom, mouth, chin bottom. On a model: the nose bottom, mouth and chin from its shape down the
face's centre line, the eye line from its texture's eye smudges. On the painting: from light and dark. Reported as the
nose-to-mouth and mouth-to-chin lengths over the eye-to-nose length (so the sizes of the pictures do not matter).

Usage: backend/.venv/Scripts/python.exe bakeoff/face_proportions.py
Writes bakeoff/out/texture_trials/face_proportions.json and face_proportions.png (each model's head, rows drawn).
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
from vgac import face  # noqa: E402

from keystone_fit import mesh_keystones, picture_keystones  # noqa: E402

ROOT = os.path.join(HERE, "out", "texture_trials")
MODELS = {  # build folder: the share of the model's height that is the head (find_head)
    "knight_u1024_t4096": 0.13,
    "knightbald_u1024_t4096": 0.13,
    "knightbaldhead_u1024_t4096": 0.45,
}
PAINTINGS = {"bald painting (head crop)": os.path.join(ROOT, "knightbaldhead_input.png")}


def ratios(k: dict) -> dict:
    en = k["nose"] - k["eye"]
    return {"nose_to_chin_front": round((k["chin_front"] - k["nose"]) / en, 3),"nose_to_mouth": round((k["mouth"] - k["nose"]) / en, 3) if k.get("mouth") else None,
            "mouth_to_chin": round((k["chin"] - k["mouth"]) / en, 3) if k.get("chin") and k.get("mouth") else None}


def model(name: str, share: float) -> tuple[dict, Image.Image]:
    d = os.path.join(ROOT, name)
    mesh = face.load_mesh(os.path.join(d, "model.glb"))
    tex = face.read_rgb(os.path.join(d, "base_color.png"))
    head = face.find_head(mesh["verts"], share)
    cam = face.head_camera(head, 0, 1024)
    view = face.render_view(mesh, tex, cam)
    pos = face.interpolate(mesh["verts"], mesh["faces"], view["raster"])
    nose = face.find_nose(view["raster"]["z"], view["mask"], pos, cam, head)
    if nose is None:  # a head-only model: the nearest point in the middle of the picture is the nose tip
        z = np.where(view["mask"], view["raster"]["z"], np.inf)
        sub = z[300:700, 380:644]
        iy, ix = np.unravel_index(int(np.argmin(sub)), sub.shape)
        nose = (int(ix + 380), int(iy + 300))
    px_per_m = 1024 / cam.scale
    w = 0.62 * head.width * px_per_m  # about the face's width (the skin search spreads over a bald scalp)
    nx, ny = nose
    region = np.zeros((1024, 1024), dtype=bool)
    region[max(0, int(ny - 0.6 * w)): int(ny + 1.1 * w), max(0, int(nx - 0.55 * w)): int(nx + 0.55 * w)] = True
    info = {"nose": nose, "width_px": w, "region": region & view["mask"]}
    k = mesh_keystones(mesh, view, info, w / px_per_m)
    k.pop("profile_every_10px", None)
    im = Image.fromarray(face.to_uint8(view["image"]))
    return k, im


def painting(path: str) -> tuple[dict, Image.Image]:
    img = face.read_rgb(path, 1024)
    # the face's nose and width found on the painting itself: the skin around the image's middle column
    lab = face.to_lab(img)
    H = img.shape[0]
    # nose: the lightest small spot on the centre column between 25 and 45 percent down (the nose tip's highlight)
    lum = face.blur(face.luminance(img), 3)
    cx = H // 2
    col = lum[int(0.25 * H): int(0.45 * H), cx - 8: cx + 9].mean(axis=1)
    ny = int(0.25 * H) + int(np.argmax(col))
    skin = np.median(lab[ny - 5: ny + 6, cx - 5: cx + 6].reshape(-1, 3), axis=0)
    row = np.linalg.norm((lab[ny] - skin) * np.array([0.5, 1, 1]), axis=-1) < 16
    xs = np.where(row)[0]
    w = float(xs.max() - xs.min())
    region = np.zeros((H, H), dtype=bool)
    region[max(0, int(ny - 0.6 * w)): int(ny + 1.1 * w), max(0, int(cx - 0.55 * w)): int(cx + 0.55 * w)] = True
    k = picture_keystones(img, {"nose": (cx, ny), "width_px": w, "region": region})
    return k, Image.fromarray(face.to_uint8(img))


def draw(im: Image.Image, k: dict, label: str) -> Image.Image:
    d = ImageDraw.Draw(im)
    for key, c in (("eye", (40, 200, 40)), ("nose", (40, 120, 220)), ("mouth", (220, 160, 40)), ("chin", (220, 40, 40)), ("chin_front", (220, 40, 220))):
        if k.get(key) is not None:
            d.line([(0, k[key]), (1024, k[key])], fill=c, width=3)
    d.text((10, 10), label, fill=(255, 255, 255))
    return im.resize((448, 448), Image.LANCZOS)


def main() -> None:
    out, tiles = {}, []
    for name, p in PAINTINGS.items():
        k, im = painting(p)
        out[name] = {"rows": k and {x: k[x] for x in ("eye", "nose", "mouth", "chin", "chin_front")}, **ratios(k)}
        tiles.append(draw(im, k, name))
    for name, share in MODELS.items():
        if not os.path.isfile(os.path.join(ROOT, name, "model.glb")):
            continue
        k, im = model(name, share)
        out[name] = {"rows": {x: k[x] for x in ("eye", "nose", "mouth", "chin", "chin_front")}, **ratios(k)}
        tiles.append(draw(im, k, name))
    sheet = Image.new("RGB", (448 * len(tiles), 448))
    for i, t in enumerate(tiles):
        sheet.paste(t, (448 * i, 0))
    sheet.save(os.path.join(ROOT, "face_proportions.png"))
    with open(os.path.join(ROOT, "face_proportions.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, default=float)
    for k, v in out.items():
        print(f"{k:32s} nose-chinfront {v['nose_to_chin_front']}  nose-mouth {v['nose_to_mouth']}  mouth-chin {v['mouth_to_chin']}   rows {v['rows']}")


if __name__ == "__main__":
    main()
