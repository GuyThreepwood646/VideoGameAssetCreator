"""Hand measurement (docs/HANDS-PLAN.md phase 0): score both hands of one or more mesh versions and render a close-up sheet.

    cd backend && PYTHONPATH=. .venv/Scripts/python.exe ../bakeoff/hands_measure.py NAME=path_to_version_dir_or_glb [...]

Writes bakeoff/out/hands/<name>.png (left hand row, right hand row; front, back, top, and the cut section) and
bakeoff/out/hands/scores.json (merged by name). Untextured shading, CPU only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from vgac import face, handscore, srcproject

OUT = Path(__file__).resolve().parent / "out" / "hands"
TILE = 360


class ViewCam(face.Camera):
    """An orthographic camera with any viewing direction (face.Camera only turns around the up axis)."""

    def __init__(self, target, scale, size, right, up, fwd):
        super().__init__(target=np.asarray(target, float), yaw_deg=0.0, scale=scale, size=size)
        self._b = (np.asarray(right, float), np.asarray(up, float), np.asarray(fwd, float))

    @property
    def basis(self):
        return self._b


VIEWS = {
    "front": ((1, 0, 0), (0, 1, 0), (0, 0, -1)),
    "back": ((-1, 0, 0), (0, 1, 0), (0, 0, 1)),
    "top": ((1, 0, 0), (0, 0, -1), (0, -1, 0)),
}


def shade(mesh, cam):
    ras = srcproject.Raster(mesh, cam)
    c = np.clip(ras.cos, 0, 1)
    img = np.full(ras.mask.shape + (3,), 0.93)
    g = 0.25 + 0.75 * c
    img[ras.mask] = np.stack([g, g, g * 0.97], axis=2)[ras.mask]
    # Dark outline where the depth jumps, so overlapping fingers read.
    z = np.where(ras.mask, ras.z, np.nan)
    dz = np.abs(np.nan_to_num(np.diff(z, axis=0, prepend=z[:1]), nan=0)) + np.abs(np.nan_to_num(np.diff(z, axis=1, prepend=z[:, :1]), nan=0))
    edge = ras.mask & (dz > 0.03 * cam.scale)
    img[edge] = 0.1
    return (np.clip(img, 0, 1) * 255).astype(np.uint8)


def section_tile(cs):
    m = cs["mask"]
    im = np.where(m, 40, 235).astype(np.uint8)
    return np.array(Image.fromarray(im).resize((TILE, TILE), Image.NEAREST).convert("RGB"))


def render_hand(mesh, hand, cs):
    tiles = []
    centre = hand["wrist"] + hand["axis"] * hand["length"] * 0.5
    scale = hand["length"] * 2.4
    for name, (r, u, f) in VIEWS.items():
        cam = ViewCam(centre, scale, TILE, r, u, f)
        img = shade(mesh, cam)
        Image.fromarray(img)
        tiles.append(img)
    tiles.append(section_tile(cs))
    return np.concatenate(tiles, axis=1)


def resolve(p: str) -> Path:
    p = Path(p)
    return p if p.suffix == ".glb" else p / "model.glb"


def main(argv):
    OUT.mkdir(parents=True, exist_ok=True)
    sj = OUT / "scores.json"
    scores = json.loads(sj.read_text()) if sj.exists() else {}
    for arg in argv:
        name, path = arg.split("=", 1)
        mesh = face.load_mesh(resolve(path))
        if mesh is None:
            print(name, "no mesh / no UVs")
            continue
        sc = handscore.score(mesh)
        rows = []
        for key, side in (("left", 1), ("right", -1)):
            h = handscore.find_hand(mesh["verts"], side)
            cs = handscore.cross_section(mesh["verts"], mesh["faces"], h)
            rows.append(render_hand(mesh, h, cs))
        sheet = Image.fromarray(np.concatenate(rows, axis=0))
        d = ImageDraw.Draw(sheet)
        for i, key in enumerate(("left", "right")):
            s = sc[key]
            d.text((6, 6 + i * TILE), f"{name} {key}: pieces {s['pieces']}  tris {s['triangles']}  spikes {s['spikes']}  (front | back | top | cut at 70%)", fill=(200, 0, 0))
        sheet.save(OUT / f"{name}.png")
        scores[name] = sc
        print(name, json.dumps({k: sc[k] for k in ("left", "right", "hand_share", "total_triangles")}))
    sj.write_text(json.dumps(scores, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
