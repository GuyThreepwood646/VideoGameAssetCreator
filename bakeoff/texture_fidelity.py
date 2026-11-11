"""Texture fidelity baseline (docs/TEXTURE-FIDELITY-PLAN.md lane A): how closely a textured model matches its picture.

With no arguments it scores the bake-off builds under bakeoff/out/convert/ (knight = humanoid, wolf = quadruped, chest =
prop; each build's input.png is the picture it was built from) and writes bakeoff/out/texture_fidelity/baseline.md and
baseline.json plus a contact sheet per build (render and fitted picture, whole and 1:1 crop).

Score any model and picture (one or more pairs, repeated --pair):
  backend/.venv/Scripts/python.exe bakeoff/texture_fidelity.py --pair model.glb picture.png [--pair other.glb other.png]
Optional: --base-color map.png (applies to the pair before it is set with --pair; give it after that pair), --yaw N (camera
turn; default is automatic), --out DIR (default bakeoff/out/texture_fidelity), --name LABEL (for the pair before it),
--no-views (skip the 8-view sharpness, faster).
Pairs given on the command line write <out>/scores.md and scores.json (the baseline files are left alone).

Numbers (inside the overlap of the two silhouettes, 4 px border left out):
  iou       silhouette match after fitting scale and shift (under 0.8: do not trust the rest)
  ssim      structural similarity of lightness, 1 is identical
  dE        mean Lab colour difference after a low-pass; about 2 is barely visible, 10 a clearly different colour
  dL        signed lightness difference, render minus picture (negative: the render is darker)
  sharp     Laplacian variance of the render over the picture's (1 is as sharp as the picture)
  detail dB atlas against its own half-size copy inside the UV islands (lower: more real detail)
  view      mean sharpness over 8 renders around the model (only the front has a picture to compare with)
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
from vgac import texfidelity  # noqa: E402

CONVERT = os.path.join(HERE, "out", "convert")
BASELINE = [("knight", "humanoid_20000"), ("knight 5k", "humanoid_5000"), ("wolf", "quadruped_20000"), ("chest", "prop_5000")]


def fmt(v, spec: str = "") -> str:
    return "n/a" if v is None else format(v, spec)


def table(rows: list[dict]) -> str:
    head = "| subject | yaw | iou | ssim | dE | dL | chroma | sharp | detail dB | view sharp | atlas px |\n|---|---|---|---|---|---|---|---|---|---|---|\n"
    lines = []
    for r in rows:
        if r.get("error"):
            lines.append(f"| {r['name']} | {r['error']} | | | | | | | | | |")
            continue
        flag = "" if r.get("fit_ok") else " (poor fit)"
        lines.append(f"| {r['name']} | {r['yaw']:g} | {r['silhouette_iou']:.3f}{flag} | {fmt(r.get('ssim'), '.3f')} | {fmt(r.get('delta_e'), '.1f')} | "
                     f"{fmt(r.get('delta_l'), '+.1f')} | {fmt(r.get('delta_chroma'), '.1f')} | {fmt(r.get('sharpness_ratio'), '.2f')} | "
                     f"{r['real_detail_db']:.1f} | {fmt(r.get('mean_sharpness'), '.2f')} | {r['texture_px']} |")
    return head + "\n".join(lines) + "\n"


NOTES = """
How to read it: the model is drawn flat (base colour only, no light) from the turn that fits the picture's silhouette best and
the picture is fitted to it by scale and shift. A picture of a character seen from the front is compared with the model's front;
a three-quarter picture (the wolf, the chest) with the model turned to match, so the turn is a fit and its colour comparison is
rougher. Sharpness is a ratio to the picture (1.0 is as sharp). Only the one side the picture shows is compared: the back and
the other side have no picture, and a model part hidden behind another in the picture is not seen. Normal, metal, roughness maps
and lighting are not measured.
"""


def main() -> None:
    argv = sys.argv[1:]
    out_dir = os.path.join(HERE, "out", "texture_fidelity")
    yaw: float | str = "auto"
    views = True
    pairs: list[dict] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--pair":
            pairs.append({"glb": argv[i + 1], "picture": argv[i + 2], "base": None, "name": os.path.basename(os.path.dirname(os.path.abspath(argv[i + 1]))) or argv[i + 1]})
            i += 3
        elif a == "--base-color":
            pairs[-1]["base"] = argv[i + 1]
            i += 2
        elif a == "--name":
            pairs[-1]["name"] = argv[i + 1]
            i += 2
        elif a == "--yaw":
            yaw = float(argv[i + 1])
            i += 2
        elif a == "--out":
            out_dir = os.path.abspath(argv[i + 1])
            i += 2
        elif a == "--no-views":
            views = False
            i += 1
        else:
            sys.exit(f"unknown argument {a}; see the top of this file")
    custom = bool(pairs)
    if not custom:
        for name, d in BASELINE:
            b = os.path.join(CONVERT, d)
            if os.path.isfile(os.path.join(b, "model.glb")):
                pairs.append({"glb": os.path.join(b, "model.glb"), "picture": os.path.join(b, "input.png"),
                              "base": os.path.join(b, "base.png"), "name": f"{name} ({d})"})
    os.makedirs(out_dir, exist_ok=True)
    rows = []
    for p in pairs:
        r = texfidelity.score(p["glb"], p["picture"], p["base"], yaw=yaw, views=views, keep_images=True)
        r["name"] = p["name"]
        r["glb"] = os.path.abspath(p["glb"])
        r["picture"] = os.path.abspath(p["picture"])
        slug = "".join(c if c.isalnum() else "_" for c in p["name"]).strip("_")
        sheet = texfidelity.save_comparison(r, os.path.join(out_dir, f"sheet_{slug}.png"))
        r.pop("_images", None)
        if sheet:
            r["sheet"] = os.path.basename(str(sheet))
        rows.append(r)
        print(f"{p['name']:34s} iou {r.get('silhouette_iou')} ssim {r.get('ssim')} dE {r.get('delta_e')} sharp {r.get('sharpness_ratio')} "
              f"detail {r.get('real_detail_db')} dB", flush=True)
    stem = "scores" if custom else "baseline"
    with open(os.path.join(out_dir, stem + ".json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=1)
    with open(os.path.join(out_dir, stem + ".md"), "w", encoding="utf-8") as f:
        f.write("# Texture fidelity " + ("scores" if custom else "baseline (before any texture change)") + "\n\n")
        f.write(table(rows) + NOTES)
        checks = [(r["name"], [(c["id"], c["ok"]) for c in texfidelity.fidelity_checks(r)]) for r in rows if not r.get("error")]
        f.write("\nReport-only checks (texture_sharp needs sharp >= %.2f, texture_colour_matches_picture needs dE <= %.0f; none when the fit is poor):\n\n"
                % (texfidelity.SHARP_RATIO_MIN, texfidelity.DELTA_E_MAX))
        for name, cs in checks:
            f.write(f"- {name}: " + (", ".join(f"{i} {'pass' if ok else 'fail'}" for i, ok in cs) or "not judged") + "\n")
    print("wrote", os.path.join(out_dir, stem + ".md"))


if __name__ == "__main__":
    main()
