"""Score the atlases written by source_project_trial.py with Lane A's backend/vgac/texfidelity.py (the app's own fidelity
score) and add the SSIM ceiling that the model's geometry allows.

The ceiling: the picture warped onto the model's own shape (the flow-aligned picture the projection paints) against the
plainly fitted picture. A texture can only reproduce the picture as far as the model's shape follows it, so this is the
best front SSIM any texture could reach; the measured SSIM should be read against it.

Usage: backend/.venv/Scripts/python.exe bakeoff/source_project_score.py [subject ...]
Output: bakeoff/out/source_project/<subject>/fidelity.json and bakeoff/out/source_project/fidelity.md
"""
from __future__ import annotations

import glob
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
sys.path.insert(0, HERE)
from vgac import face, texfidelity  # noqa: E402

from source_project_trial import CONVERT, OUT, SUBJECTS, lowpass_lab_diff, small, ssim  # noqa: E402

KEYS = ["silhouette_iou", "yaw", "ssim", "delta_e", "sharpness_ratio", "real_detail_db", "mean_sharpness"]


def main() -> None:
    subjects = [a for a in sys.argv[1:] if not a.startswith("--")] or list(SUBJECTS)
    lines = ["| subject | atlas | " + " | ".join(KEYS) + " | ssim ceiling |", "|" + "---|" * (len(KEYS) + 3)]
    for key in subjects:
        build = SUBJECTS[key][0]
        d = os.path.join(OUT, key)
        glb = os.path.join(CONVERT, build, "model.glb")
        pic = os.path.join(CONVERT, build, "input.png")
        aff = face.read_rgb(os.path.join(d, "picture_affine.png"), 1024)
        ali = face.read_rgb(os.path.join(d, "picture_aligned.png"), 1024)
        mask = (aff.sum(2) > 0.02) & (ali.sum(2) > 0.02)
        ceiling = round(ssim(face.luminance(ali), face.luminance(aff), mask), 4)
        res = {}
        for p in sorted(glob.glob(os.path.join(d, "*_base.png"))):
            name = os.path.basename(p)[:-9]
            r = texfidelity.score(glb, pic, base_color=p)
            res[name] = {k: r.get(k) for k in KEYS}
            lines.append(f"| {key} | {name} | " + " | ".join(str(r.get(k)) for k in KEYS) + f" | {ceiling} |")
            print(lines[-1], flush=True)
        with open(os.path.join(d, "fidelity.json"), "w", encoding="utf-8") as f:
            json.dump({"ssim_ceiling": ceiling, "scores": res}, f, indent=1, default=float)
    with open(os.path.join(OUT, "fidelity.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
