"""Guide lines for side views: do lines drawn across the front picture at the eyes, under the nose, the mouth and the
chin keep those features at the same heights in the side views step 2 draws?

Front: the bald head-and-shoulders crop (bakeoff/out/texture_trials/knightbaldhead_input.png). Its keystone rows are
measured, thin lines drawn full width at each, and step 2's own side view graph (angle_graph, view_prompt "left") run
three ways, two seeds each: no lines (today), lines, lines plus words asking to keep them. In each result: the lines'
rows (if still there) and the profile's keystones (face_reshape.profile_keys: top of the head, nose tip, under the
nose, chin front, chin bottom), against the front's rows. Instance B. Output: bakeoff/out/texture_trials/side_guides/.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
from comfy import B, download, images_of, run, stage_input  # noqa: E402

from face_reshape import picture_mask, profile_keys  # noqa: E402
from vgac import face  # noqa: E402
from vgac.graphs.qwen_edit import angle_graph, view_prompt  # noqa: E402

ROOT = os.path.join(HERE, "out", "texture_trials")
OUT = os.path.join(ROOT, "side_guides")
FRONT = os.path.join(ROOT, "knightbaldhead_input.png")
# rows on the front crop (1024): eye line, under the nose, mouth, chin bottom (read off the picture; the chin sits just
# above the collar)
ROWS = {"eye": 271, "subnasale": 364, "mouth": 411, "chin_bottom": 432}
COLOURS = {"eye": (0, 255, 0), "subnasale": (0, 160, 255), "mouth": (255, 200, 0), "chin_bottom": (255, 0, 255)}
WORDS = ("Keep the four thin coloured horizontal guide lines exactly where they are, straight across the whole picture: "
         "the eyes on the green line, the bottom of the nose on the blue line, the mouth on the yellow line and the bottom "
         "of the chin on the magenta line.")
SEEDS = (3, 4)


def with_lines(dest: str) -> str:
    im = Image.open(FRONT).convert("RGB")
    d = ImageDraw.Draw(im)
    for k, r in ROWS.items():
        d.line([(0, r), (1024, r)], fill=COLOURS[k], width=3)
    im.save(dest)
    return dest


def line_rows(path: str) -> dict:
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    out = {}
    for k, c in COLOURS.items():
        hit = (np.abs(a - np.array(c)).sum(axis=2) < 120).sum(axis=1)
        rows = np.where(hit > 0.25 * a.shape[1])[0]
        out[k] = float(rows.mean()) if len(rows) else None
    return out


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    lined = with_lines(os.path.join(OUT, "front_lines.png"))
    jobs = {"none": (FRONT, ""), "lines": (lined, ""), "lines_words": (lined, WORDS)}
    report = {"front_rows": ROWS, "front_profile_rows": None, "runs": {}}
    for arm, (src, extra) in jobs.items():
        name = stage_input(src, f"bakeoff_sg_{arm}.png")
        for seed in SEEDS:
            dest = os.path.join(OUT, f"{arm}_{seed}.png")
            if not os.path.isfile(dest):
                outputs, _ = run(B, angle_graph(name, view_prompt("left", extra), seed, f"bakeoff/side_guides/{arm}_{seed}"), poll=1.0)
                download(B, images_of(outputs)[0], dest)
            _, m = picture_mask(dest)
            # which way the face points: the side with more of the figure's reach at the nose band
            rows = np.where(m.any(axis=1))[0]
            mid = rows[0] + int(0.25 * (rows[-1] - rows[0]))
            xs = np.where(m[mid])[0]
            cx = np.where(m[rows[0]: rows[0] + 40].any(axis=0))[0].mean()
            k = profile_keys(m, bool((cx - xs.min()) > (xs.max() - cx)))
            rec = {"profile": {x: k.get(x) for x in ("top", "tip", "subnasale", "chin_front", "chin_bottom")}, "lines": line_rows(dest)}
            rec["miss_px"] = {x: (None if k.get(x) is None else k[x] - ROWS[x]) for x in ("subnasale", "chin_bottom")}
            report["runs"][f"{arm}_{seed}"] = rec
            print(arm, seed, rec, flush=True)
    with open(os.path.join(OUT, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, default=float)
    tiles = [Image.open(lined).convert("RGB").resize((340, 340))] + [Image.open(os.path.join(OUT, f"{a}_{s}.png")).convert("RGB").resize((340, 340))
                                                                     for a in jobs for s in SEEDS]
    sheet = Image.new("RGB", (340 * len(tiles), 340))
    for i, t in enumerate(tiles):
        sheet.paste(t, (340 * i, 0))
    d = ImageDraw.Draw(sheet)
    for k, r in ROWS.items():  # the front's rows across every tile, thin
        d.line([(0, r * 340 / 1024), (sheet.width, r * 340 / 1024)], fill=COLOURS[k], width=1)
    sheet.save(os.path.join(OUT, "sheet.png"))
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
