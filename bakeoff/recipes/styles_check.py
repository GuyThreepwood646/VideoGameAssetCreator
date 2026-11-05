"""Part 3: do the style presets deliver their look without any LoRA? Current model + current block ("now"),
then proposed blocks or models ("v2", "v3"), same seeds, on the knight and the chest (and the wolf for v2).

Usage: python styles_check.py <variant> [style ...]      variant: now | v2 | v3
       python styles_check.py sheet
Images: bakeoff/out/recipes/styles/<style>/<subject>_<seed>_<variant>_{img,cut}.png
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rlib  # noqa: E402
from rlib import cg, reference  # noqa: E402

DECODE = {"zturbo": "7", "zbase": "8", "klein": "11", "anima": "8", "pixel": "10", "chroma": "8"}
STYLES = ["retro64", "lowpoly_flat", "clay", "cel", "pixel", "realistic_pbr"]
SUBJ = ["knight", "chest"]

# Proposed blocks (filled in after looking at "now"). Each: (model, block). Medium first as a physical thing,
# no brand or console names, exclusions at the end (skill section 3).
CLAY = ("A stop-motion clay puppet photographed on a tabletop set, a real handmade plasticine figure, soft matte "
         "sculpted surfaces with faint fingerprints and tool marks, rounded simplified shapes, slightly lumpy "
         "hand-made edges, warm muted colours, soft diffused studio light. Not a drawing, not a glossy plastic toy.")
V2: dict[str, tuple[str, str]] = {
    "retro64": ("zturbo",
                "An in-game real-time 3D model from a late-1990s 64-bit home console game, a low-polygon mesh rendered "
                "in real time, not a drawing or illustration. Smooth gouraud vertex shading with soft glossy specular "
                "highlights, slightly angular silhouettes, chunky simplified hands and details, blurry low-resolution "
                "bilinear-filtered textures, bold saturated colours, bright ambient light with one warm key light from "
                "the upper left, a soft round blob shadow underneath, the whole image slightly soft like a low-resolution "
                "frame upscaled. Not built from cubes, no pixel art, no voxels."),
    "lowpoly_flat": ("zturbo",
                     "A low-poly 3D model rendered in real time with flat shading, built from a few hundred large flat "
                     "triangular facets, every facet one solid colour so each plane shows as a separate tone, sharp "
                     "angular edges everywhere including the face, hair, cloth and wood, no textures and no smooth or "
                     "rounded surfaces. Clean readable silhouette, soft pastel palette, simple soft ambient light. "
                     "No pixel art, no voxels, no fine surface detail."),
    "clay": ("zturbo", CLAY),
}
V3: dict[str, tuple[str, str]] = {"clay": ("klein", CLAY)}  # retro64 / lowpoly_flat v3 added below

V4 = {"clay": ("zturbo", "Clay animation style: the subject itself is sculpted from matte plasticine, soft rounded "
                 "simplified shapes with faint fingerprints and tool marks, slightly lumpy hand-made edges, warm muted "
                 "colours, photographed in soft diffused studio light. Not a drawing, not glossy plastic, no other "
                 "figures or props.")}
V3["retro64"] = ("zturbo:first", V2["retro64"][1])
V3["lowpoly_flat"] = ("zturbo:first", V2["lowpoly_flat"][1])

JSONL = os.path.join(rlib.OUT, "styles.jsonl")


def one(style, subject, seed, variant, model, block):
    category, asset = rlib.SUBJECTS[subject]
    first = model.endswith(":first")
    model = model.split(":")[0]
    if first:  # medium first: style block, then the asset prompt, then the pose block
        prompt = " ".join([" ".join(block.split()), asset, reference.category(category)["pose_block"]])
    else:
        prompt = rlib.build_prompt(asset, None, category, style_block=block)
    d = os.path.join(rlib.OUT, "styles", style)
    os.makedirs(d, exist_ok=True)
    base = os.path.join(d, f"{subject}_{seed}_{variant}")
    if os.path.exists(base + "_cut.png"):
        return
    g = cg.concept_graph(model, prompt, seed, f"vgac_recipes/styles/{style}_{subject}_{seed}_{variant}")
    rlib.add_cutout(g, [DECODE[model], 0], f"vgac_recipes/styles/{style}_{subject}_{seed}_{variant}")
    saves = {"img": cg.save_node(model), "cut": "cs"}
    if model == "pixel":
        saves["raw"] = "9"
    got, secs = rlib.run_and_fetch(g, saves, base)
    m = rlib.concept_metrics(got["cut"], category, got.get("raw") or got["img"])
    row = {"style": style, "subject": subject, "seed": seed, "variant": variant, "model": model, "secs": secs, **m}
    with open(JSONL, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    print(json.dumps(row), flush=True)


def blocks(variant):
    if variant == "now":
        return {s["id"]: (s["model"], s["prompt_block"]) for s in reference.styles()}
    return {"v2": V2, "v3": V3, "v4": V4}[variant]


def sheet():
    rows = {}
    for line in open(JSONL, encoding="utf-8"):
        r = json.loads(line)
        rows[(r["style"], r["subject"], r["seed"], r["variant"])] = r
    for style in STYLES:
        grid = []
        for subject in rlib.SUBJECTS:
            for seed in rlib.SEEDS:
                cells = []
                for v in ("now", "v2", "v3", "v4"):
                    r = rows.get((style, subject, seed, v))
                    if r:
                        p = os.path.join(rlib.OUT, "styles", style, f"{subject}_{seed}_{v}_img.png")
                        cells.append((p, f"{v} {r['model']} m{r['margin']:.2f} cc{r['components']} bg{r['bg_std']}\n"
                                         f"blk{r.get('block_err')} col{r['colours_5bit']}"))
                if cells:
                    grid.append(cells)
        if grid:
            print(rlib.label_sheet(grid, os.path.join(rlib.OUT, "sheets", f"style_{style}.png"), cell=320, title=f"style {style}"))


if __name__ == "__main__":
    if sys.argv[1] == "sheet":
        sheet()
        raise SystemExit
    variant = sys.argv[1]
    want = sys.argv[2:] or STYLES
    subjects = SUBJ + (["wolf"] if variant != "now" else [])
    table = blocks(variant)
    for style in want:
        if style not in table:
            continue
        model, block = table[style]
        for subject in subjects:
            for seed in rlib.SEEDS:
                one(style, subject, seed, variant, model, block)
