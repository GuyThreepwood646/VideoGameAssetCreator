"""Humanoid pose block: current text vs a proposed one asking for headroom and an explicit A-pose angle.
Painted style on Z-Image Turbo, knight, four seeds. Images: bakeoff/out/recipes/posefix/.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rlib  # noqa: E402
from rlib import cg, reference  # noqa: E402

NEW = ("The character stands in a relaxed A-pose, both arms straight and angled about thirty degrees out from the body "
       "with a clear gap between each arm and the torso, palms inward, feet shoulder-width apart, facing the camera "
       "directly. The whole figure from the top of the head to the soles of the feet fits inside the middle four fifths "
       "of the image height, with empty background above the head and below the feet, centred in frame. Plain flat "
       "light grey background. Soft even studio lighting from the front with no hard shadows. No text, no logos, no "
       "background scenery, no floor shadow, no motion blur, no cropping, nothing held in the hands.")
SEEDS = [1101, 2202, 3303, 4404]


def main():
    style = reference.style("painted")["prompt_block"]
    _, asset = rlib.SUBJECTS["knight"]
    d = os.path.join(rlib.OUT, "posefix")
    os.makedirs(d, exist_ok=True)
    rows = []
    for variant, pose in (("now", reference.category("humanoid")["pose_block"]), ("new", NEW)):
        for model in ("zturbo", "klein"):
            for seed in SEEDS:
                base = os.path.join(d, f"{model}_{seed}_{variant}")
                prompt = " ".join([asset, " ".join(style.split()), pose])
                if not os.path.exists(base + "_cut.png"):
                    g = cg.concept_graph(model, prompt, seed, f"vgac_recipes/posefix/{model}_{seed}_{variant}")
                    rlib.add_cutout(g, [{"zturbo": "7", "klein": "11"}[model], 0], f"vgac_recipes/posefix/{model}_{seed}_{variant}")
                    rlib.run_and_fetch(g, {"img": cg.save_node(model), "cut": "cs"}, base)
                m = rlib.concept_metrics(base + "_cut.png", "humanoid")
                r = {"model": model, "seed": seed, "variant": variant, **m}
                rows.append(r)
                print(json.dumps(r), flush=True)
    with open(os.path.join(rlib.OUT, "posefix.json"), "w") as f:
        json.dump(rows, f, indent=1)
    grid = []
    for model in ("zturbo", "klein"):
        for variant in ("now", "new"):
            grid.append([(os.path.join(d, f"{model}_{r['seed']}_{variant}_img.png"),
                          f"{model} {variant} m{r['margin']:.3f}\narms{r['arm_gap_rows']:.2f} hip/sh{r['hip_extent_over_shoulder']:.2f}")
                         for r in rows if r["model"] == model and r["variant"] == variant])
    print(rlib.label_sheet(grid, os.path.join(rlib.OUT, "sheets", "posefix.png"), cell=260, title="Humanoid pose block: now vs new"))


if __name__ == "__main__":
    main()
