"""Pose-ready report (docs/HANDS-PLAN.md, "Pose ready results"): one run's sheets and numbers.

    cd backend && PYTHONPATH=. .venv/Scripts/python.exe ../bakeoff/pose_ready_report.py NAME CONCEPT_DIR TURN_DIR PREP_DIR

CONCEPT_DIR holds the original picture (cutout.png or image.png), TURN_DIR the turnaround version (pose_ready_cutout.png
when pose_ready ran), PREP_DIR the clean-up version (model.glb, base_color.png). Writes under bakeoff/out/hands/:
  <name>_pose.png      the original front picture next to the posed one (or the original twice when pose_ready was off)
  <name>_textured.png  the textured hands after clean-up, front and back, left row and right row
and merges the numbers into pose_scores.json (hand score, fidelity of the final model against the original and the posed
picture, colour distance between the two pictures).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hands_measure as hm  # noqa: E402

from vgac import face, handscore, imaging, srcproject, texfidelity  # noqa: E402

OUT = hm.OUT


def first(d: Path, *names: str) -> Path | None:
    for n in names:
        if (d / n).is_file():
            return d / n
    return None


def on_white(p: Path, h: int = 600) -> Image.Image:
    im = Image.open(p).convert("RGBA")
    bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
    bg.alpha_composite(im)
    im = bg.convert("RGB")
    return im.resize((int(im.width * h / im.height), h), Image.LANCZOS)


def picture_numbers(a: Path, b: Path) -> dict:
    """Colour and face distance between two cut-outs of the same size or shape: histogram distance over the subject,
    and the mean Lab distance over the head band (top 18 percent of the subject's rows)."""
    A, B = Image.open(a).convert("RGBA"), Image.open(b).convert("RGBA").resize(Image.open(a).size)
    ha, hb = imaging.colour_histogram(a), imaging.colour_histogram(b)
    out = {"hist_dist": round(float(imaging.histogram_distance(ha, hb)), 4)}
    ma = np.asarray(A.getchannel("A")) > 127
    ys = np.where(ma.any(axis=1))[0]
    if len(ys):
        top, bot = ys[0], ys[-1]
        band = slice(top, top + int(0.18 * (bot - top)))
        ra, rb = np.asarray(A.convert("RGB"), float)[band] / 255, np.asarray(B.convert("RGB"), float)[band] / 255
        m = (ma[band]) & (np.asarray(B.getchannel("A")) > 127)[band]
        if m.sum() > 50:
            la, lb = face.to_lab(ra), face.to_lab(rb)
            out["head_lab_diff"] = round(float(np.linalg.norm(la - lb, axis=-1)[m].mean()), 3)
    return out


def textured_hands(mesh: dict, tex: np.ndarray) -> Image.Image:
    rows = []
    for side in (1, -1):
        h = handscore.find_hand(mesh["verts"], side)
        centre = h["wrist"] + h["axis"] * h["length"] * 0.5
        tiles = []
        for name in ("front", "back"):
            r, u, f = hm.VIEWS[name]
            cam = hm.ViewCam(centre, h["length"] * 2.4, hm.TILE, r, u, f)
            ras = srcproject.Raster(mesh, cam)
            img = srcproject.render_colour(ras, tex, bg=0.93)
            tiles.append((np.clip(img, 0, 1) * 255).astype(np.uint8))
        rows.append(np.concatenate(tiles, axis=1))
    return Image.fromarray(np.concatenate(rows, axis=0))


def main(argv):
    name, concept, turn, prep = argv[0], Path(argv[1]), Path(argv[2]), Path(argv[3])
    OUT.mkdir(parents=True, exist_ok=True)
    orig = first(concept, "cutout.png", "image.png")
    posed = first(turn, "pose_ready_cutout.png")
    glb = first(prep, "prep.glb", "model.glb") or prep / "model.glb"
    res: dict = {"name": name, "pose_ready": posed is not None}
    # Front picture before and after.
    left = on_white(orig)
    right = on_white(posed or orig)
    sheet = Image.new("RGB", (left.width + right.width + 30, 640), (255, 255, 255))
    sheet.paste(left, (10, 30))
    sheet.paste(right, (left.width + 20, 30))
    d = ImageDraw.Draw(sheet)
    d.text((10, 8), "original", fill=(200, 0, 0))
    d.text((left.width + 20, 8), "posed" if posed else "(pose_ready off: same picture)", fill=(200, 0, 0))
    sheet.save(OUT / f"{name}_pose.png")
    if posed:
        res["pictures"] = picture_numbers(orig, posed)
    # The final model: hand score, textured hands, fidelity against each picture.
    mesh = face.load_mesh(glb)
    if mesh is None:
        res["error"] = "no mesh"
    else:
        res["hands"] = handscore.score(mesh)
        tex = texfidelity.load_texture(glb, first(prep, "base_color.png"))
        if tex is not None:
            textured_hands(mesh, tex).save(OUT / f"{name}_textured.png")
        fid = {}
        for label, pic in (("original", orig), ("posed", posed)):
            if pic is None:
                continue
            s = texfidelity.score(glb, pic, first(prep, "base_color.png"), views=False)
            fid[label] = {k: s.get(k) for k in ("delta_e", "ssim", "silhouette_iou", "fit_ok", "error") if k in s}
        res["fidelity"] = fid
    sj = OUT / "pose_scores.json"
    scores = json.loads(sj.read_text()) if sj.exists() else {}
    scores[name] = res
    sj.write_text(json.dumps(scores, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
