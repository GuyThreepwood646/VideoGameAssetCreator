"""Head-only build test: does the 3D builder follow the painted face's proportions when the face fills the picture?

Crops head and shoulders (the top 26 percent of the figure, square, centred on the head) from the bald bake-off
knight (bald_knight.py) and builds it with the texture trials' graph at colour grid 1024 and texture 4096.
Usage: backend/.venv/Scripts/python.exe bakeoff/head_build.py
Output: bakeoff/out/texture_trials/knightbaldhead_input.png and knightbaldhead_u1024_t4096/
"""
import os
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import texture_trials as tt  # noqa: E402

# python head_build.py [source.png name]: default the bald knight; e.g. the haired one:
#   bakeoff/out/convert/humanoid_20000/input.png knighthead
SRC = sys.argv[1] if len(sys.argv) > 2 else os.path.join(tt.OUT, "knightbald_input.png")
NAME = sys.argv[2] if len(sys.argv) > 2 else "knightbaldhead"
DEST = os.path.join(tt.OUT, f"{NAME}_input.png")

if __name__ == "__main__":
    src = Image.open(SRC)
    rgb = np.asarray(src.convert("RGB"))
    if src.mode == "RGBA":
        rgb = rgb * (np.asarray(src)[..., 3:4] > 127)
    m = rgb.max(axis=2) > 10
    rows = np.where(m.any(axis=1))[0]
    top, bot = int(rows[0]), int(rows[-1])
    h = int(float(os.environ.get("HEAD_CROP", "0.26")) * (bot - top))  # share of the figure height, from the top
    band = m[top: top + int(0.12 * (bot - top))]
    xs = np.where(band.any(axis=0))[0]
    cx = int((xs[0] + xs[-1]) / 2)
    side = int(h * 1.1)
    x0, y0 = cx - side // 2, top - int(0.05 * h)
    canvas = np.zeros((side, side, 3), dtype=np.uint8)
    sub = rgb[max(0, y0): y0 + side, max(0, x0): x0 + side]
    canvas[max(0, -y0): max(0, -y0) + sub.shape[0], max(0, -x0): max(0, -x0) + sub.shape[1]] = sub
    Image.fromarray(canvas).resize((1024, 1024), Image.LANCZOS).save(DEST)
    tt.to_rgba(DEST)
    if "--crop-only" in sys.argv:
        raise SystemExit(0)
    tt.INPUTS[NAME] = DEST
    tt.build(NAME, 1024, 4096)
    print("DONE", flush=True)
