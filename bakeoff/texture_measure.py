"""Measures for the texture research trials (docs/PHOTOREAL-TEXTURE-RESEARCH.md section 4), CPU only.

For every build folder under bakeoff/out/texture_trials (or the folders given):
- real detail (dB): the base colour against itself shrunk to half and blown back up, inside the UV islands; lower
  means more real detail;
- view sharpness: face.sharpness on 8 orthographic renders of the base colour at 1024 px, cameras framed on the
  model's own bounds so builds of one subject compare;
- brightness: mean luminance of the base colour inside the islands (the colour ratio check compares builds);
- seconds from timings.tsv.
Writes bakeoff/out/texture_trials/measures.json and a sheet per subject (front and back renders side by side, plus
1:1 crops).

Usage: backend/.venv/Scripts/python.exe bakeoff/texture_measure.py [build_dir ...]
"""
from __future__ import annotations

import glob
import json
import os
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
sys.path.insert(0, HERE)
from vgac import face, texfidelity  # noqa: E402

from repaint_trial import real_detail_db, uv_cover  # noqa: E402

ROOT = os.path.join(HERE, "out", "texture_trials")
YAWS = [0, 45, 90, 135, 180, 225, 270, 315]
S = 1024


def views(mesh: dict, tex: np.ndarray) -> list[dict]:
    return texfidelity.view_sharpness(mesh, tex, S)


def measure(build: str, timings: dict[str, float]) -> dict:
    mesh = face.load_mesh(os.path.join(build, "model.glb"))
    tex = face.read_rgb(os.path.join(build, "base_color.png"))
    cover = uv_cover(mesh, tex.shape[0])
    vs = views(mesh, tex)
    lum = face.luminance(tex)[cover]
    name = os.path.basename(build)
    return {"build": name, "texture": tex.shape[0], "real_detail_db": real_detail_db(tex, cover),
            "atlas_used": round(float(cover.mean()), 3), "brightness": round(float(lum.mean()), 4),
            "view_sharpness": {str(x["yaw"]): x["sharpness"] for x in vs},
            "mean_sharpness": round(float(np.mean([x["sharpness"] for x in vs])), 3),
            "seconds": timings.get(name), "_views": vs}


def sheet(rows: list[dict], dest: str) -> None:
    cell = 448
    img = Image.new("RGB", (cell * 2 + 512, cell * len(rows)), (40, 40, 40))
    for i, r in enumerate(rows):
        for j, idx in enumerate((0, 4)):
            img.paste(Image.fromarray(face.to_uint8(r["_views"][idx]["image"])).resize((cell, cell), Image.LANCZOS), (j * cell, i * cell))
        front = Image.fromarray(face.to_uint8(r["_views"][0]["image"]))
        c = S // 2
        img.paste(front.crop((c - 128, int(S * 0.32) - 128, c + 128, int(S * 0.32) + 128)).resize((448, 448), Image.NEAREST), (cell * 2 + 32, i * cell))
    img.save(dest)


def main() -> None:
    builds = sys.argv[1:] or sorted(d for d in glob.glob(os.path.join(ROOT, "*")) if os.path.isfile(os.path.join(d, "model.glb")))
    timings: dict[str, float] = {}
    tp = os.path.join(ROOT, "timings.tsv")
    if os.path.isfile(tp):
        for line in open(tp, encoding="utf-8"):
            parts = line.strip().split("\t")
            if len(parts) >= 2:
                timings[parts[0]] = float(parts[1])
    results = [measure(b, timings) for b in builds]
    by_subject: dict[str, list[dict]] = {}
    for r in results:
        by_subject.setdefault(r["build"].split("_")[0], []).append(r)
    for subject, rows in by_subject.items():
        sheet(rows, os.path.join(ROOT, f"sheet_{subject}.png"))
    clean = [{k: v for k, v in r.items() if not k.startswith("_")} for r in results]
    with open(os.path.join(ROOT, "measures.json"), "w", encoding="utf-8") as f:
        json.dump(clean, f, indent=1)
    for r in clean:
        print(f"{r['build']:22s} detail {r['real_detail_db']:6.2f} dB  sharp {r['mean_sharpness']:6.3f}  bright {r['brightness']:.3f}  "
              f"atlas {r['atlas_used']:.3f}  {r['seconds']} s", flush=True)


if __name__ == "__main__":
    main()
