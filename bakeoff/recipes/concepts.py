"""Concept add-on pairs: same prompt, same seed, LoRA at strength 0 (without) and at two strengths.

Usage: python concepts.py <experiment> [<experiment> ...] | all
Writes bakeoff/out/recipes/concepts/<exp>/<subject>_<seed>_<cond>_{img,cut}.png, appends metrics to
bakeoff/out/recipes/concepts.jsonl and draws a labelled sheet per experiment.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rlib  # noqa: E402
from rlib import VGAC, BS, cg  # noqa: E402

TARN = "ZImage" + BS + "pixel_art_style_z_image_turbo.safetensors"  # the app's current pixel LoRA (models.yaml)


def zt(prompt, seed, prefix):
    g = cg.zimage_graph(prompt, seed, prefix)
    return g, {"img": "8"}, ["7", 0]


def kl(prompt, seed, prefix):
    g = cg.klein_graph(prompt, seed, prefix)
    return g, {"img": "12"}, ["11", 0]


def kl_pixel(prompt, seed, prefix):
    g = cg.klein_graph(prompt, seed, prefix + "_raw")
    g["px"] = {"class_type": "PixelArtDetectorToImage", "inputs": {"images": ["11", 0], "reduce_palette": True, "reduce_palette_max_colors": 32}}
    g["pxs"] = {"class_type": "SaveImage", "inputs": {"images": ["px", 0], "filename_prefix": prefix}}
    return g, {"raw": "12", "img": "pxs"}, ["px", 0]


def pixel(lora, trigger):
    def f(prompt, seed, prefix):
        g = cg.pixel_graph(prompt, seed, prefix, lora=lora, trigger=trigger, strength=1.0)
        return g, {"raw": "9", "img": "11"}, ["10", 0]
    return f


# Each experiment: base builder, style, LoRA file (None for builders that carry their own LoRA in node "2"),
# trigger (prepended to the assembled prompt; for pixel builders it is passed to the builder), strengths, refs.
EXPS = {
    "render3d": dict(build=zt, style="painted", lora=VGAC + "zit_3drender_redmond.safetensors",
                     trigger="3D Render Style. 3DRenderAF.", strengths=[0.0, 0.7, 1.0]),
    "clay": dict(build=zt, style="clay", lora=VGAC + "zit_clay_redmond.safetensors",
                 trigger="Clay animation, Clay.", strengths=[0.0, 0.7, 1.0],
                 refs={"klein_now": (kl, "", None)}),
    "ps1": dict(build=zt, style="retro64", lora=VGAC + "zit_ps1_redmond.safetensors",
                trigger="Playstation 1 Graphics. PS1 Game.", strengths=[0.0, 0.6, 0.9],
                refs={"zt_now": (zt, "", None)}),
    "pixred": dict(build=pixel(VGAC + "zit_pixelart_redmond.safetensors", "Pixel Art, PixArFK"), style="pixel",
                   lora="node2", trigger="", strengths=[0.0, 0.7, 1.0],
                   refs={"tarn_now": (pixel(TARN, "Pixel art style."), "", None)}),
    "kleinpix": dict(build=kl_pixel, style="pixel", lora=VGAC + "klein4b_pixelart_limbicnation.safetensors",
                     trigger="pixel art sprite, game asset.", strengths=[0.0, 1.0],
                     refs={"tarn_now": (pixel(TARN, "Pixel art style."), "", None)}),
}

JSONL = os.path.join(rlib.OUT, "concepts.jsonl")


def one(exp, subject, seed, cond, builder, trigger, lora, strength):
    category, asset = rlib.SUBJECTS[subject]
    style = EXPS[exp]["style"]
    prompt = rlib.build_prompt(asset, style, category, trigger=trigger)
    d = os.path.join(rlib.OUT, "concepts", exp)
    os.makedirs(d, exist_ok=True)
    base = os.path.join(d, f"{subject}_{seed}_{cond}")
    if os.path.exists(base + "_cut.png"):
        return
    g, saves, cut_ref = builder(prompt, seed, f"vgac_recipes/{exp}/{subject}_{seed}_{cond}")
    if lora == "node2":
        g["2"]["inputs"]["strength_model"] = strength
    elif lora:
        rlib.add_lora(g, lora, strength)
    rlib.add_cutout(g, cut_ref, f"vgac_recipes/{exp}/{subject}_{seed}_{cond}")
    saves = {**saves, "cut": "cs"}
    got, secs = rlib.run_and_fetch(g, saves, base)
    m = rlib.concept_metrics(got["cut"], category, got.get("raw"))
    row = {"exp": exp, "subject": subject, "seed": seed, "cond": cond, "strength": strength, "secs": secs, **m}
    with open(JSONL, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    print(json.dumps(row), flush=True)


def run_exp(exp):
    e = EXPS[exp]
    for subject in rlib.SUBJECTS:
        for seed in rlib.SEEDS:
            for s in e["strengths"]:
                one(exp, subject, seed, f"s{s:.1f}", e["build"], e["trigger"] if e["lora"] != "node2" else "", e["lora"], s)
            for name, (b, trig, lora) in e.get("refs", {}).items():
                one(exp, subject, seed, name, b, trig, lora, 1.0)
    sheet(exp)


def _rows():
    rows = {}
    if os.path.exists(JSONL):
        for line in open(JSONL, encoding="utf-8"):
            r = json.loads(line)
            rows[(r["exp"], r["subject"], r["seed"], r["cond"])] = r
    return rows


def _lab(r):
    if not r:
        return "missing"
    s = f"{r['cond']} m{r.get('margin', 0):.2f} cc{r.get('components')} bg{r.get('bg_std')}"
    t = ""
    if "arm_gap_rows" in r:
        t += f"arms{r['arm_gap_rows']:.2f} hip/sh{r['hip_extent_over_shoulder']:.2f} "
    if "block_err" in r:
        t += f"blk{r['block_err']} "
    t += f"col{r.get('colours_5bit')}"
    return s + "\n" + t


def sheet(exp):
    e = EXPS[exp]
    conds = [f"s{s:.1f}" for s in e["strengths"]] + list(e.get("refs", {}))
    rows = _rows()
    grid = []
    d = os.path.join(rlib.OUT, "concepts", exp)
    for subject in rlib.SUBJECTS:
        for seed in rlib.SEEDS:
            grid.append([(os.path.join(d, f"{subject}_{seed}_{c}_img.png"), _lab(rows.get((exp, subject, seed, c)))) for c in conds])
    return rlib.label_sheet(grid, os.path.join(rlib.OUT, "sheets", f"concept_{exp}.png"), cell=320,
                            title=f"{exp}: {e.get('lora')} trigger '{e['trigger']}'")


if __name__ == "__main__":
    want = sys.argv[1:]
    if want == ["all"]:
        want = list(EXPS)
    for exp in want:
        if exp.startswith("sheet:"):
            print(sheet(exp[6:]))
        else:
            run_exp(exp)
