"""Face close-up trial: would a face close-up supplied at the start paint a better face than the one the app draws?

Three arms on the same bake-off model, all through the app's own face pass code (stages/face_detail.py):
- none: today's TRELLIS face, no face pass;
- app: what the clean-up step does today: Qwen-Image-Edit draws a close-up from the painted head (about 68 concept
  pixels across, enlarged to 1024), checked by closeup_guard, merged with closeup_source, fitted and painted;
- supplied: a stand-in for a face shot the user uploads: Qwen-Image-Edit draws a close-up of the face from the whole
  character picture (so it has its own framing, like a real upload), it is lined up to the painted head by a wide
  scale-and-shift search (register), then the same guard, merge, fit and paint as the app arm.
Each picture arm runs with two seeds. No relayout (the app's Blender faceuv step): every arm paints into the build's
own texture layout, so the arms compare fairly but the face has fewer texels than the app would give it.

Measures on the front head camera: face sharpness (face.sharpness in the face region), likeness of the painted model
to the painting at the painting's own resolution (face.likeness), the guard's verdict and numbers. Sheet: painted head,
the close-up, the model before, after (app), after (supplied), front and three-quarter.

Usage: backend/.venv/Scripts/python.exe bakeoff/face_closeup_trial.py <build_dir> <concept_png>
Output: <build_dir>/face_trial/{none,app_<seed>,supplied_<seed>}/..., report.json, sheet.png. Bake-off files only.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
from comfy import B, download, images_of, run, stage_input  # noqa: E402

from vgac import face  # noqa: E402
from vgac.graphs.qwen_edit import edit_graph  # noqa: E402
from vgac.stages import face_detail as fd  # noqa: E402

SEEDS = (11, 22)
SUPPLIED_PROMPT = ("A close-up portrait of this same character's face: the head and shoulders fill the picture, facing "
                   "straight toward the viewer, drawn at high resolution with crisp, fine detail in the eyes, irises, lashes, "
                   "brows, nose, lips, skin and hair. The same face and features, the same eye colour, the same hair and "
                   "hairstyle, the same expression, the same armour at the shoulders, the same painting style and colours. "
                   "Soft, even lighting, plain flat grey background.")


def cutout_from_black(src: str, dest: Path) -> Path:
    """The bake-off input has the figure on black with no alpha: alpha is everything not black, holes filled."""
    rgb = np.asarray(Image.open(src).convert("RGB"), dtype=np.float64) / 255.0
    m = face.fill_holes(rgb.max(axis=2) > 10 / 255)
    rgba = np.dstack([rgb, m.astype(np.float64)])
    Image.fromarray((rgba * 255).round().astype(np.uint8), "RGBA").save(dest)
    return dest


def qwen(picture: Path, prompt: str, seed: int, tag: str, dest: Path) -> Path:
    if dest.is_file():
        return dest
    name = stage_input(str(picture), f"bakeoff_fc_{tag}.png")
    outputs, _ = run(B, edit_graph(name, prompt, seed, f"bakeoff/face_trial/{tag}", 1024, 1024), poll=1.0)
    imgs = images_of(outputs)
    if not imgs:
        raise RuntimeError("no image")
    download(B, imgs[0], str(dest))
    return dest


def on_grey(cutout: Path, dest: Path) -> Path:
    a = np.asarray(Image.open(cutout).convert("RGBA"), dtype=np.float64) / 255.0
    rgb = a[..., :3] * a[..., 3:4] + 0.5 * (1 - a[..., 3:4])
    return face.save_rgb(rgb, dest)


def register(closeup: np.ndarray, crop: np.ndarray, region: np.ndarray, dest: Path) -> dict:
    """Lay a close-up with its own framing over the painted head: a wide search over one scale (0.3 to 1.6) and any
    shift on band-passed lightness inside the face region, at 256, then face.align's own fine fit. The result is the
    close-up resampled into the crop's framing (what closeup_guard expects)."""
    W = 256
    k = crop.shape[0] / W
    d = face.band(face.resize(crop, W), 1.0, 6.0)
    msk = (face.resize(region.astype(np.float64), W) > 0.5).astype(np.float64)
    ys, xs = np.where(msk > 0)
    c = (xs.mean() + 0.5, ys.mean() + 0.5)
    d = (d - d[msk > 0].mean()) * msk
    s_img = face.resize(closeup, W)
    best = {"score": -2.0}
    for s in np.geomspace(0.3, 1.6, 48):
        b = face.band(face.scale_image(s_img, s, s, 0.0, 0.0, c), 1.0, 6.0)
        score, dx, dy = face._ncc_shift(d, b, msk, int(0.45 * W))
        if score > best["score"]:
            best = {"score": float(score), "s": float(s), "tx": dx * k, "ty": dy * k}
    coarse = {"sx": best["s"], "sy": best["s"], "tx": best["tx"], "ty": best["ty"], "centre": (c[0] * k, c[1] * k)}
    first = face.apply_alignment(closeup, coarse)
    fine = face.align(first, crop, region, scales=(0.94, 1.06))
    a = face.compose_alignment(coarse, fine)
    face.save_rgb(face.apply_alignment(closeup, a), dest)
    return {"coarse_scale": round(best["s"], 3), "coarse_score": round(best["score"], 4), "fine_score": fine["score"],
            "sx": a["sx"], "sy": a["sy"], "tx": a["tx"], "ty": a["ty"]}


def head_views(glb: Path, tex_path: Path) -> dict:
    mesh = face.load_mesh(glb)
    tex = face.read_rgb(tex_path)
    head = face.find_head(mesh["verts"])
    out = {}
    for yaw in (0, 35):
        cam = face.head_camera(head, yaw, 1024)
        out[yaw] = face.render_view(mesh, tex, cam)
    return out


def paint_arm(glb: Path, base: Path, crop: Path, closeup: Path, head_px: float, arm_dir: Path, paint_anyway: bool) -> dict:
    arm_dir.mkdir(parents=True, exist_ok=True)
    guard = fd.closeup_guard(glb, base, crop, closeup, head_px)
    rec = {"guard_ok": guard["ok"], "guard_reason": guard["reason"],
           **{k: guard.get(k) for k in ("likeness", "feature_score_min", "feature_move", "feature_move_raw", "eye_colour_shift",
                                        "skin_shift", "hair_shift", "skin_texture", "source_face_px")}}
    if not guard["ok"] and not paint_anyway:
        return rec
    fixed = Path(guard["registered"]) if guard.get("registered") else closeup
    merged = fd.closeup_source(crop, fixed, head_px, arm_dir / "merged.png")
    done = fd.fit_and_paint(glb, base, merged, glb, base, crop, arm_dir, 1.0, None, merged, False, True)
    rec["painted"] = bool(done.get("ok"))
    if done.get("ok"):
        m = done["metrics"]
        rec.update({k: m.get(k) for k in ("sharpness_before", "sharpness_after", "feature_match_after", "fit_match",
                                           "eye_colour_shift", "face_texels_after", "eyes_moved")})
        rec["texture"] = str(done["base_color"])
    else:
        rec["paint_reason"] = done.get("reason")
    return rec


def main() -> None:
    build = Path(sys.argv[1]).resolve()
    concept = sys.argv[2]
    out = build / "face_trial"
    out.mkdir(exist_ok=True)
    glb, base = build / "model.glb", build / "base_color.png"
    cut = cutout_from_black(concept, out / "cutout.png")
    found = fd.inspect(glb, base, cut, out)
    if not found.get("found"):
        raise SystemExit(f"no face: {found}")
    crop = out / "painted_head.png"
    head_px = float(found["painted_head_px"])
    print(f"face found: {found['face_width_px']:.0f} px wide on the render, painted head {head_px} concept px, "
          f"face texels {found['face_width_texels']}", flush=True)
    mesh = face.load_mesh(glb)
    head = face.find_head(mesh["verts"])
    cam = face.head_camera(head, 0, 1024)
    view = face.render_view(mesh, face.read_rgb(base), cam)
    info = face.find_face(view, mesh, cam, head)
    region = info["region"]
    crop_img = face.read_rgb(crop, 1024)

    report: dict = {"build": build.name, "painted_head_px": head_px, "face_width_texels_before": found["face_width_texels"], "arms": {}}
    full = on_grey(cut, out / "concept_on_grey.png")
    for seed in SEEDS:
        a_cu = qwen(crop, fd.CLOSEUP_PROMPT, seed, f"app_{seed}", out / f"app_{seed}_closeup.png")
        report["arms"][f"app_{seed}"] = paint_arm(glb, base, crop, a_cu, head_px, out / f"app_{seed}", True)
        s_raw = qwen(full, SUPPLIED_PROMPT, seed, f"supplied_{seed}", out / f"supplied_{seed}_raw.png")
        reg = register(face.read_rgb(s_raw, 1024), crop_img, region, out / f"supplied_{seed}_registered.png")
        rec = paint_arm(glb, base, crop, out / f"supplied_{seed}_registered.png", head_px, out / f"supplied_{seed}", True)
        rec["register"] = reg
        report["arms"][f"supplied_{seed}"] = rec
        for k in (f"app_{seed}", f"supplied_{seed}"):
            r = report["arms"][k]
            print(f"{k:12s} guard {r['guard_reason'] or 'ok':10s} likeness {r.get('likeness')}  sharp "
                  f"{r.get('sharpness_before')} -> {r.get('sharpness_after')}  match {r.get('feature_match_after')}", flush=True)

    # Likeness of each painted model to the painting, at the painting's own resolution, on the same camera.
    before = head_views(glb, base)
    report["none"] = {"sharpness": face.sharpness(before[0]["image"], region),
                      "likeness_to_painting": round(face.likeness(crop_img, before[0]["image"], region, head_px), 4)}
    rows = [("none", before)]
    for k, r in report["arms"].items():
        if r.get("texture"):
            v = head_views(glb, Path(r["texture"]))
            r["likeness_to_painting"] = round(face.likeness(crop_img, v[0]["image"], region, head_px), 4)
            rows.append((k, v))
    with open(out / "report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, default=str)

    # Sheet: top row the pictures (painted head, app close-ups, supplied raw and registered), then one row per arm with
    # the front and three-quarter renders, cropped to the face.
    cell = 384
    pics = [crop] + [out / f"app_{s}_closeup.png" for s in SEEDS] + [out / f"supplied_{s}_raw.png" for s in SEEDS] + \
           [out / f"supplied_{s}_registered.png" for s in SEEDS]
    cols = max(len(pics), 2 * len(rows))
    sheet = Image.new("RGB", (cell * cols, cell * 2), (40, 40, 40))
    for i, p in enumerate(pics):
        sheet.paste(Image.open(p).convert("RGB").resize((cell, cell), Image.LANCZOS), (i * cell, 0))

    def face_crop(img: np.ndarray) -> Image.Image:
        ys, xs = np.where(region)
        cx, cy, half = int(xs.mean()), int(ys.mean()), int(0.75 * (xs.max() - xs.min()))
        return Image.fromarray(face.to_uint8(img)).crop((cx - half, cy - half, cx + half, cy + half)).resize((cell, cell), Image.LANCZOS)

    for j, (k, v) in enumerate(rows):
        sheet.paste(face_crop(v[0]["image"]), (2 * j * cell, cell))
        sheet.paste(face_crop(v[35]["image"]), ((2 * j + 1) * cell, cell))
    sheet.save(out / "sheet.png")
    print("rows on the sheet:", [k for k, _ in rows], flush=True)
    print(json.dumps({"none": report["none"], **{k: {"likeness_to_painting": r.get("likeness_to_painting"),
                                                      "sharp": r.get("sharpness_after"), "guard": r["guard_reason"]}
                                                  for k, r in report["arms"].items()}}, indent=1), flush=True)


if __name__ == "__main__":
    main()
