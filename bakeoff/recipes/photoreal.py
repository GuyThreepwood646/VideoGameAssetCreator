"""Photoreal recipe test (owner request): which painter and prompt give image-to-3D-ready photoreal concepts?

Subjects: lantern (prop_rigid), mossy wall (environment), wolf (quadruped), human (humanoid), seed 1101.
Variants: zbase_now (realistic_pbr today: block + default PAINT_NEG), zbase_new / zturbo_new / klein_new (studio
photograph block below; zbase gets PHOTO_NEG). Extra measures for 3D use: cast shadow on the backdrop, left/right
light asymmetry on the subject, sharpness and depth-of-field falloff across the subject.

Usage: python photoreal.py concepts | turn | report
Images: bakeoff/out/recipes/photoreal/
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rlib  # noqa: E402
from rlib import cg, reference  # noqa: E402

SUBJ = {
    "lantern": ("prop_rigid", "A weathered iron lantern with a domed top and a carry ring, four glass panes, rust and "
                "small dents on the frame, a short white candle inside."),
    "wall": ("environment", "A mossy stone wall section about two metres long, rough grey fieldstones stacked dry, "
             "green moss in the joints and along the top course, a few cracked stones."),
    "wolf": ("quadruped", rlib.SUBJECTS["wolf"][1]),
    "human": ("humanoid", "A man in his thirties with short brown hair and a trimmed beard, wearing a plain grey hooded "
              "jacket, dark work trousers and brown leather boots."),
}
NEW = ("A real high-resolution studio photograph made as a photogrammetry reference, shot with a sharp lens stopped "
       "down so the whole subject is in crisp focus from front to back, lit by large soft light boxes from the front, "
       "both sides and above for even, shadowless illumination, true-to-life materials and colours, fine crisp surface "
       "detail. No depth of field blur, no bokeh, no dramatic or directional lighting, no hard cast shadows, no rim "
       "light, no vignette.")
PHOTO_NEG = ("depth of field, bokeh, blurry background, tilt shift, dramatic lighting, directional light, hard shadows, "
             "cast shadow, rim light, spotlight, vignette, painting, illustration, drawing, 3d render, cgi, cartoon, "
             "plastic, low quality, blurry, jpeg artifacts, text, watermark, logo, frame, border, extra limbs, "
             "deformed hands, extra fingers, duplicate")
VARIANTS = {
    "zbase_now": ("zbase", None, None),
    "zbase_new": ("zbase", NEW, PHOTO_NEG),
    "zturbo_new": ("zturbo", NEW, None),
    "klein_new": ("klein", NEW, None),
}
DECODE = {"zturbo": "7", "zbase": "8", "klein": "11"}
D = os.path.join(rlib.OUT, "photoreal")
SEED = 1101


def lum(rgb):
    return 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]


def lap(g):
    return np.abs(4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:])


def photo_metrics(cut_path):
    im = np.array(Image.open(cut_path).convert("RGBA")).astype(np.float32)
    rgb, a = im[..., :3], im[..., 3]
    m = a > 127
    L = lum(rgb)
    near = rlib.dilate(a > 8, 6)
    ring = rlib.dilate(a > 8, 46) & ~near
    far = ~rlib.dilate(a > 8, 46)
    bgmed = float(np.median(L[far])) if far.sum() > 1000 else float(np.median(L[~near]))
    out = {"shadow_frac": round(float((L[ring] < 0.85 * bgmed).mean()), 3) if ring.sum() else None}
    ys, xs = np.where(m)
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    xm = (x0 + x1) // 2
    Ls = L[y0:y1 + 1]
    ml = m[y0:y1 + 1, x0:xm]
    mr = m[y0:y1 + 1, xm:x1 + 1]
    left, right = float(L[y0:y1 + 1, x0:xm][ml].mean()), float(L[y0:y1 + 1, xm:x1 + 1][mr].mean())
    out["lr_asym"] = round(abs(left - right) / max(1.0, (left + right) / 2), 3)
    g = L
    e = lap(g)
    inner = ~rlib.dilate(~m, 4)[1:-1, 1:-1]
    out["sharp"] = round(float(e[inner].mean()), 2)
    cells = []
    for i in range(3):
        for j in range(3):
            ya, yb = y0 + (y1 - y0) * i // 3, y0 + (y1 - y0) * (i + 1) // 3
            xa, xb = x0 + (x1 - x0) * j // 3, x0 + (x1 - x0) * (j + 1) // 3
            cm = inner[max(0, ya - 1):yb - 1, max(0, xa - 1):xb - 1]
            ce = e[max(0, ya - 1):yb - 1, max(0, xa - 1):xb - 1]
            if cm.size and cm.mean() > 0.5:
                cells.append(float(ce[cm].mean()))
    out["dof_ratio"] = round(min(cells) / max(1e-6, float(np.median(cells))), 3) if len(cells) >= 3 else None
    return out


def concepts():
    os.makedirs(D, exist_ok=True)
    for subject, (category, asset) in SUBJ.items():
        for v, (model, block, neg) in VARIANTS.items():
            base = os.path.join(D, f"{subject}_{v}")
            if os.path.exists(base + "_cut.png"):
                continue
            prompt = rlib.build_prompt(asset, "realistic_pbr" if block is None else None, category, style_block=block)
            if block is not None:  # medium first for the new block (skill rule); the app's order is asset first
                prompt = " ".join([block, asset, reference.category(category)["pose_block"]])
            g = cg.concept_graph(model, prompt, SEED, f"vgac_recipes/photoreal/{subject}_{v}", negative=neg)
            rlib.add_cutout(g, [DECODE[model], 0], f"vgac_recipes/photoreal/{subject}_{v}")
            got, secs = rlib.run_and_fetch(g, {"img": cg.save_node(model), "cut": "cs"}, base)
            print(subject, v, secs, flush=True)


def report():
    rows = []
    for subject, (category, _) in SUBJ.items():
        for v in VARIANTS:
            cut = os.path.join(D, f"{subject}_{v}_cut.png")
            if not os.path.exists(cut):
                continue
            r = {"subject": subject, "variant": v, **rlib.concept_metrics(cut, category), **photo_metrics(cut)}
            rows.append(r)
            print(json.dumps(r))
    json.dump(rows, open(os.path.join(rlib.OUT, "photoreal.json"), "w"), indent=1)
    grid = [[(os.path.join(D, f"{r['subject']}_{r['variant']}_img.png"),
              f"{r['variant']} m{r['margin']:.2f} sh{r['shadow_frac']} lr{r['lr_asym']}\nsharp{r['sharp']} dof{r['dof_ratio']} bg{r['bg_std']}")
             for r in rows if r["subject"] == s] for s in SUBJ]
    print(rlib.label_sheet([g for g in grid if g], os.path.join(rlib.OUT, "sheets", "photoreal.png"), cell=320,
                           title="Photoreal painters (seed 1101)"))


if __name__ == "__main__":
    {"concepts": concepts, "report": report}[sys.argv[1]]()
