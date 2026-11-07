"""The app's head graft (vgac/headgraft.py + blender/headjoin.py) on the bake-off bald knight: one model, one texture.
Usage: backend/.venv/Scripts/python.exe bakeoff/headgraft_app_check.py [body_build head_build]
Output: bakeoff/out/texture_trials/graft_app/{graft.glb, graft_*.png, joined/model.glb, joined/*.png, sheet.png}
"""
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
from vgac import face, headgraft  # noqa: E402
from vgac.config import get_settings  # noqa: E402
from vgac.tools import run_blender  # noqa: E402

ROOT = Path(HERE) / "out" / "texture_trials"
body = ROOT / (sys.argv[1] if len(sys.argv) > 2 else "knightbald_u1024_t4096")
head = ROOT / (sys.argv[2] if len(sys.argv) > 2 else "knightbaldhead_u1024_t4096")
out = ROOT / "graft_app"
maps = lambda d: {k: d / f"{k}.png" for k in headgraft.NEUTRAL if (d / f"{k}.png").is_file()}  # noqa: E731
t0 = time.time()
g = headgraft.graft(body / "model.glb", maps(body), head / "model.glb", maps(head), out)
print("graft", g["ok"], g["reason"], json.dumps({k: v for k, v in g["metrics"].items() if "rows" not in k}), round(time.time() - t0, 1), "s", flush=True)
if not g["ok"]:
    raise SystemExit(1)
(out / "joined").mkdir(exist_ok=True)
mj = out / "graft_maps.json"
mj.write_text(json.dumps({k: str(p) for k, p in g["maps"].items()}))
tex = face.read_rgb(body / "base_color.png").shape[0]
t1 = time.time()
run_blender(get_settings(), "headjoin.py", [g["glb"], out / "joined" / "model.glb", "--maps", mj, "--tex", tex], out / "headjoin.log", 3600)
print("join", round(time.time() - t1, 1), "s", flush=True)
mesh = face.load_mesh(out / "joined" / "model.glb")
jt = face.read_rgb(out / "joined" / "base_color.png")
print("joined faces", len(mesh["faces"]), "texture", jt.shape)
bh = face.find_head(mesh["verts"])
tiles = [face.render_view(mesh, jt, face.head_camera(bh, y, 1024, margin=2.0))["image"] for y in (0, 35, 90)]
Image.fromarray(face.to_uint8(np.concatenate(tiles, 1))).resize((1536, 512)).save(out / "sheet.png")
Image.fromarray(face.to_uint8(jt)).resize((1024, 1024)).save(out / "atlas.png")
