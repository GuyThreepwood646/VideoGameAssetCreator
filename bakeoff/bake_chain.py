"""Lane C of docs/TEXTURE-FIDELITY-PLAN.md: measure the bake chain (blender/prep.py re-bake) with different bake settings.

For each variant (a JSON of rebake options, see blender/rebake.py OPTIONS) runs Blender's prep.py on a bake-off 3D-step
build (bakeoff/out/convert/<subject>: model.glb + base/metallic/roughness/normal/ao), then measures the prepared
base colour: real detail dB, mean 8-view sharpness, front sharpness, seam step, seconds.
Output goes to bakeoff/out/bake_chain/<subject>__<variant>/.

Usage: backend/.venv/Scripts/python.exe bakeoff/bake_chain.py <subject> <variant_name> [json options]
       subjects: knight (humanoid_20000, symmetric, budget 15000), chest (prop_5000, 1024 atlas)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
sys.path.insert(0, HERE)
from vgac import face  # noqa: E402
from texture_measure import measure  # noqa: E402

from local_paths import blender  # noqa: E402

BLENDER = blender() or "blender"  # VGAC_BLENDER, else blender_exe in the app's settings
PREP = os.path.join(HERE, "..", "backend", "vgac", "blender", "prep.py")
OUT = os.path.join(HERE, "out", "bake_chain")
SUBJECTS = {
    "knight": {"src": "humanoid_20000", "tex": 2048, "args": ["--symmetric", "--budget", "15000", "--protect-head", "0.13", "--head-gain", "2"]},
    # a second generation: the prepared knight (variant "adj") put through the same re-bake again with its layout
    # unchanged, which is what the face relayout and the head graft do to it
    "knight_gen2": {"src": "bake_chain/knight__adj", "tex": 2048, "args": ["--size", "1.8"], "named": True},
    # the sampler-step builds (bakeoff/convert.py --tex-steps), same recipe as the knight
    "knightS12": {"src": "knightS12_20000", "tex": 2048, "args": ["--symmetric", "--budget", "15000", "--protect-head", "0.13", "--head-gain", "2"]},
    "knightS24": {"src": "knightS24_20000", "tex": 2048, "args": ["--symmetric", "--budget", "15000", "--protect-head", "0.13", "--head-gain", "2"]},
    "chest": {"src": "prop_5000", "tex": 1024, "args": ["--size", "1.0", "--size-axis", "longest", "--shading", "smooth"]},
}
KEYMAP = {"base_color": "base.png", "metallic": "metallic.png", "roughness": "roughness.png", "normal": "normal.png", "ao": "ao.png"}


def seam_step(mesh: dict, tex: np.ndarray) -> float:
    """Mean absolute luminance difference (0..255) across UV seams: the two sides of every 3D edge whose two faces
    carry different UVs, sampled at 5 points along the edge."""
    verts, faces, uv = mesh["verts"], mesh["faces"], mesh["uv"]
    key = np.round(verts * 1e5).astype(np.int64)
    _, pid = np.unique(key, axis=0, return_inverse=True)
    pid = pid.reshape(-1)
    edges: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for f in faces:
        for a, b in ((0, 1), (1, 2), (2, 0)):
            ia, ib = int(f[a]), int(f[b])
            pa, pb = int(pid[ia]), int(pid[ib])
            if pa == pb:
                continue
            k = (pa, pb) if pa < pb else (pb, pa)
            edges.setdefault(k, []).append((ia, ib) if pa < pb else (ib, ia))
    lum = face.luminance(tex)
    T = tex.shape[0]

    def sample(u, v):
        x = np.clip(u * T - 0.5, 0, T - 1.001)
        y = np.clip((1.0 - v) * T - 0.5, 0, T - 1.001)
        x0, y0 = int(x), int(y)
        fx, fy = x - x0, y - y0
        return (lum[y0, x0] * (1 - fx) * (1 - fy) + lum[y0, x0 + 1] * fx * (1 - fy)
                + lum[y0 + 1, x0] * (1 - fx) * fy + lum[y0 + 1, x0 + 1] * fx * fy)

    diffs = []
    for k, lst in edges.items():
        if len(lst) != 2:
            continue
        (a0, b0), (a1, b1) = lst
        if np.allclose(uv[a0], uv[a1], atol=1e-5) and np.allclose(uv[b0], uv[b1], atol=1e-5):
            continue
        for t in (0.1, 0.3, 0.5, 0.7, 0.9):
            p0 = uv[a0] * (1 - t) + uv[b0] * t
            p1 = uv[a1] * (1 - t) + uv[b1] * t
            diffs.append(abs(sample(*p0) - sample(*p1)))
    return round(float(np.mean(diffs)) * 255.0, 3) if diffs else 0.0


def run(subject: str, variant: str, opts: dict) -> dict:
    cfg = SUBJECTS[subject]
    src = os.path.join(HERE, "out", "" if cfg.get("named") else "convert", cfg["src"])
    dest = os.path.join(OUT, f"{subject}__{variant}")
    os.makedirs(dest, exist_ok=True)
    keymap = {k: f"{k}.png" for k in KEYMAP} if cfg.get("named") else KEYMAP
    maps = {k: os.path.abspath(os.path.join(src, v)) for k, v in keymap.items() if os.path.isfile(os.path.join(src, v))}
    mj = os.path.join(dest, "bake_maps.json")
    json.dump(maps, open(mj, "w"))
    tex = cfg["tex"]
    cmd = [BLENDER, "-b", "--python", PREP, "--", os.path.join(src, "model.glb"), os.path.join(dest, "model.glb"), "mesh",
           "--maps", mj, "--tex", str(tex), "--padding", str(max(4, tex // 256)), "--margin", str(max(8, tex // 128)), "--rebake", "always"] + cfg["args"]
    env = dict(os.environ, VGAC_REBAKE_OPTS=json.dumps(opts))
    t = time.time()
    with open(os.path.join(dest, "prep.log"), "w") as log:
        subprocess.run(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    seconds = round(time.time() - t, 1)
    over = int(opts.get("oversample", 1))
    if over > 1:  # bake at n times the size, shrink outside Blender with Lanczos (colour maps) / area-ish for data maps
        for k in maps:
            p = os.path.join(dest, f"{k}.png")
            if not os.path.isfile(p) or k == "ao":
                continue
            im = Image.open(p)
            im.save(os.path.join(dest, f"{k}_{over}x.png"))
            im.convert("RGB").resize((tex, tex), Image.LANCZOS).save(p)
    report = json.load(open(os.path.join(dest, "model_report.json"))) if os.path.isfile(os.path.join(dest, "model_report.json")) else {}
    rb = report.get("rebake_seconds")
    m = measure(dest, {})
    mesh = face.load_mesh(os.path.join(dest, "model.glb"))
    tx = face.read_rgb(os.path.join(dest, "base_color.png"))
    row = {"subject": subject, "variant": variant, "opts": opts, "real_detail_db": m["real_detail_db"], "mean_sharpness": m["mean_sharpness"],
           "front_sharpness": m["view_sharpness"]["0"], "atlas_used": m["atlas_used"], "brightness": m["brightness"],
           "seam_step": seam_step(mesh, tx), "rebake_seconds": rb, "total_seconds": seconds}
    json.dump(row, open(os.path.join(dest, "row.json"), "w"), indent=1)
    print(json.dumps(row), flush=True)
    return row


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2], json.loads(sys.argv[3]) if len(sys.argv) > 3 else {})
