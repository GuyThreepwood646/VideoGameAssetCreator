"""Texture sampler 12 against 24 steps, and 24 steps plus the colour match (vgac/colourmatch.py), against the picture.

For each subject (builds made by bakeoff/convert.py --tex-steps N from the same picture and seed) and each variant
(12, 24, 24+match) reports: texfidelity (silhouette IoU, sharpness ratio, dE, dL, chroma), 8-view sharpness, seam step,
local contrast away from seams (mean absolute luminance step between neighbouring texels inside the islands, 0..255),
seam step over local contrast, and the mean colour. Writes bakeoff/out/bake_chain/steps_match.json.

Usage: backend/.venv/Scripts/python.exe bakeoff/steps_match_trial.py
"""
import json
import os
import shutil
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
sys.path.insert(0, HERE)
from vgac import colourmatch, face, texfidelity  # noqa: E402
import bake_chain as bc  # noqa: E402

CONV = os.path.join(HERE, "out", "convert")
OUT = os.path.join(bc.OUT, "steps_match")
SUBJECTS = {"knight": ("knightS12_20000", "knightS24_20000"), "chest": ("chestS12_5000", "chestS24_5000")}


def local_contrast(tex: np.ndarray, cover: np.ndarray) -> float:
    lum = face.luminance(tex)
    both = cover[:, 1:] & cover[:, :-1]
    dx = np.abs(lum[:, 1:] - lum[:, :-1])[both]
    both = cover[1:, :] & cover[:-1, :]
    dy = np.abs(lum[1:, :] - lum[:-1, :])[both]
    return float(np.concatenate([dx, dy]).mean() * 255.0)


def row(label: str, glb: str, base: str, picture: str, yaw="auto") -> dict:
    s = texfidelity.score(glb, picture, base_color=base, views=True, yaw=yaw)
    mesh = face.load_mesh(glb)
    tex = face.read_rgb(base)
    cover = texfidelity.uv_cover(mesh, tex.shape[0])
    seam = bc.seam_step(mesh, tex)
    lc = local_contrast(tex, face.erode(cover, 3))
    r = {"variant": label, "yaw": s["yaw"], "iou": s["silhouette_iou"], "sharpness_ratio": s["sharpness_ratio"], "delta_e": s["delta_e"], "delta_l": s["delta_l"],
         "delta_chroma": s["delta_chroma"], "mean_sharpness": s["mean_sharpness"], "ssim": s["ssim"], "seam_step": seam,
         "local_contrast": round(lc, 3), "seam_over_contrast": round(seam / lc, 3),
         "mean_rgb": [round(float(tex[..., c][cover].mean()), 4) for c in range(3)]}
    print(json.dumps(r), flush=True)
    return r


def main() -> None:
    res = {}
    for subject, (d12, d24) in SUBJECTS.items():
        pic = os.path.join(CONV, d12, "input.png")
        rows = []
        for label, d in (("12", d12), ("24", d24)):
            w = os.path.join(OUT, f"{subject}_{label}")
            os.makedirs(w, exist_ok=True)
            shutil.copy2(os.path.join(CONV, d, "model.glb"), os.path.join(w, "model.glb"))
            shutil.copy2(os.path.join(CONV, d, "base.png"), os.path.join(w, "base_color.png"))
            rows.append(row(label, os.path.join(w, "model.glb"), os.path.join(w, "base_color.png"), pic))
        w = os.path.join(OUT, f"{subject}_24match")
        os.makedirs(w, exist_ok=True)
        shutil.copy2(os.path.join(OUT, f"{subject}_24", "model.glb"), os.path.join(w, "model.glb"))
        shutil.copy2(os.path.join(OUT, f"{subject}_24", "base_color.png"), os.path.join(w, "base_color.png"))
        rec = colourmatch.match_base_colour(os.path.join(w, "model.glb"), pic, os.path.join(w, "base_color.png"))
        print("MATCH", json.dumps(rec), flush=True)
        r = row("24+match", os.path.join(w, "model.glb"), os.path.join(w, "base_color.png"), pic, yaw=rows[1]["yaw"])  # the same turn as the plain 24-step row
        r["match"] = rec
        rows.append(r)
        res[subject] = rows
    json.dump(res, open(os.path.join(bc.OUT, "steps_match.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
