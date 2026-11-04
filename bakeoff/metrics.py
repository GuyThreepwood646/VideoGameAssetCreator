"""Silhouette audit: compare a mesh's rendered views against the turnaround cutouts.

Usage: python metrics.py <subject> <engine>
Reads  out/mesh/<subject>/<engine>.glb, renders it with Blender (render_views.py), then for each turnaround view
(front, left, back, right) finds the best-matching rendered yaw under the four possible 90-degree alignments and
reports intersection-over-union of bounding-box-normalised silhouettes. Appends a row to out/mesh/metrics.tsv.
"""
import json
import os
import subprocess
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
BLENDER = r"F:\Tools\blender-5.2.3-windows-x64\blender.exe"
OUT = os.path.join(HERE, "out")
VIEW_YAW = {"front": 0, "left": 90, "back": 180, "right": 270}


def silhouette(path, size=256):
    im = Image.open(path).convert("RGBA")
    a = np.array(im.getchannel("A")) > 127
    ys, xs = np.where(a)
    if len(xs) == 0:
        return np.zeros((size, size), bool)
    crop = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    h, w = crop.shape
    s = size / max(h, w)
    small = Image.fromarray(crop.astype(np.uint8) * 255).resize((max(1, int(w * s)), max(1, int(h * s))))
    out = np.zeros((size, size), bool)
    sm = np.array(small) > 127
    y0, x0 = (size - sm.shape[0]) // 2, (size - sm.shape[1]) // 2
    out[y0:y0 + sm.shape[0], x0:x0 + sm.shape[1]] = sm
    return out


def iou(a, b):
    return float((a & b).sum()) / max(1, float((a | b).sum()))


if __name__ == "__main__":
    subject, engine = sys.argv[1], sys.argv[2]
    glb = os.path.join(OUT, "mesh", subject, f"{engine}.glb")
    rdir = os.path.join(OUT, "mesh", subject, f"{engine}_views")
    if not os.path.exists(os.path.join(rdir, "info.json")):
        subprocess.run([BLENDER, "-b", "--python", os.path.join(HERE, "render_views.py"), "--", glb, rdir], check=True,
                       stdout=subprocess.DEVNULL)
    info = json.load(open(os.path.join(rdir, "info.json")))
    rendered = {yaw: silhouette(os.path.join(rdir, f"shade_{yaw}.png")) for yaw in range(0, 360, 15)}
    refs = {v: silhouette(os.path.join(OUT, "turnaround", subject, f"{v}.png")) for v in VIEW_YAW}
    # Mesh "front" may face any of the 4 axes and either handedness; try 8 yaw offsets and both mirror directions.
    best = None
    for offset in range(0, 360, 15):
        for sign in (1, -1):
            scores = {v: iou(refs[v], rendered[(offset + sign * VIEW_YAW[v]) % 360]) for v in VIEW_YAW}
            mean = sum(scores.values()) / len(scores)
            if best is None or mean > best[0]:
                best = (mean, offset, sign, scores)
    mean, offset, sign, scores = best
    row = f"{subject}\t{engine}\t{info['triangles']}\tmeanIoU={mean:.3f}\t" + "\t".join(f"{v}={s:.3f}" for v, s in scores.items()) + f"\toffset={offset} sign={sign}"
    with open(os.path.join(OUT, "mesh", "metrics.tsv"), "a", encoding="utf-8") as f:
        f.write(row + "\n")
    print(row)
