"""Texture research step 2 (docs/PHOTOREAL-TEXTURE-RESEARCH.md section 4): whole-model view repaint, prototype.

The face pass (vgac/face.py, stages/face_detail.py) generalised to the whole model:
1. render the model's base colour from N orthographic cameras around it (the face pass's own rasteriser, so the
   render and the projection always line up);
2. Qwen-Image-Edit-2511 on instance B redraws each render sharper, with "keep everything the same" words;
3. a redraw that moved (face.align score or shift past its limit) is dropped;
4. face.merge_detail: colour and broad tone stay the render's, only fine lightness detail comes from the redraw;
5. every view is projected back through the UVs and the views are averaged, weighted by how squarely each sees
   the texel (face.project_back's facing weight); texels no view sees keep today's colour.
Then it measures before and after: view sharpness (face.sharpness) on the same cameras, real detail in the atlas
(PSNR against itself shrunk to half and blown back up, inside the islands; lower = more real detail), and writes a
contact sheet. Only bake-off builds; never library/projects.

Usage: backend/.venv/Scripts/python.exe bakeoff/repaint_trial.py <build_dir> [views=8] [size=1024] [--variant name]
Output: <build_dir>/repaint[_<variant>]/{view_XXX_render.png, view_XXX_redraw.png, base_color.png, sheet.png, report.json}
"""
from __future__ import annotations

import json
import math
import os
import sys
import time

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
from comfy import B, download, images_of, run, stage_input  # noqa: E402

from vgac import face, imaging  # noqa: E402
from vgac.graphs.qwen_edit import edit_graph  # noqa: E402
from vgac.texfidelity import real_detail_db, uv_cover  # noqa: E402,F401  (moved to the backend; kept importable here)

PROMPT = ("Redraw this picture at high quality with crisp, sharp, fine surface detail: clear texture in the cloth, "
          "leather, metal, fur, wood and stone, sharp edges, seams and small parts. Keep everything else exactly the same: "
          "the same object, the same position, size, angle and framing, the same shapes, the same colours, the same "
          "materials and the same grey background. Soft, even lighting. Add nothing new.")
BACKGROUND = 0.5
FACING = (0.45, 0.95)       # cosine range: surfaces under about 63 degrees off the view start to count, full at about 18
MIN_SCORE = 0.35            # face.align correlation below this: the redraw is not the same picture
MAX_SHIFT = 0.012           # of the picture size
MAX_SCALE = 0.03            # off 1.0


def redraw(render_png: str, seed: int, prefix: str, size: int, dest: str) -> tuple[str, float]:
    name = stage_input(render_png, "bakeoff_rp_" + os.path.basename(render_png))
    outputs, secs = run(B, edit_graph(name, PROMPT, seed, prefix, size, size), poll=1.0)
    imgs = images_of(outputs)
    if not imgs:
        raise RuntimeError("no image from the redraw")
    download(B, imgs[0], dest)
    return dest, secs


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    variant = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--variant" and i + 1 < len(sys.argv)), "")
    build = os.path.abspath(args[0])
    n_views = int(args[1]) if len(args) > 1 else 8
    S = int(args[2]) if len(args) > 2 else 1024
    out = os.path.join(build, "repaint" + (f"_{variant}" if variant else ""))
    os.makedirs(out, exist_ok=True)
    mesh = face.load_mesh(os.path.join(build, "model.glb"))
    if mesh is None:
        raise SystemExit("the model has no UVs")
    tex_path = os.path.join(build, "base_color.png")
    tex = face.read_rgb(tex_path)
    T = tex.shape[0]
    v = mesh["verts"]
    lo, hi = v.min(0), v.max(0)
    centre = (lo + hi) / 2
    scale = float(max(hi[1] - lo[1], np.hypot(hi[0] - lo[0], hi[2] - lo[2]))) * 1.06
    yaws = [round(i * 360.0 / n_views, 1) for i in range(n_views)]
    acc = np.zeros_like(tex)
    wsum = np.zeros((T, T))
    views = []
    t0 = time.time()
    for i, yaw in enumerate(yaws):
        cam = face.Camera(target=centre, yaw_deg=yaw, scale=scale, size=S)
        r = face.render_view(mesh, tex, cam, background=BACKGROUND)
        rp = os.path.join(out, f"view_{int(yaw):03d}_render.png")
        face.save_rgb(r["image"], rp)
        dp = os.path.join(out, f"view_{int(yaw):03d}_redraw.png")
        info: dict = {"yaw": yaw}
        try:
            if not os.path.isfile(dp):
                _, secs = redraw(rp, 1000 + i, f"bakeoff/repaint/{os.path.basename(build)}_{variant or 'a'}/{int(yaw):03d}", S, dp)
                info["seconds"] = secs
            sharp = face.read_rgb(dp, S)
        except Exception as e:  # noqa: BLE001
            info["skipped"] = f"redraw failed: {e}"
            views.append(info)
            print(f"view {yaw}: {info['skipped']}", flush=True)
            continue
        a = face.align(sharp, r["image"], r["mask"])
        info["align"] = {k: (round(float(a[k]), 4) if isinstance(a.get(k), (int, float)) else None) for k in ("sx", "sy", "tx", "ty", "score")}
        shift = max(abs(a["tx"]), abs(a["ty"])) / S
        if a["score"] < MIN_SCORE or shift > MAX_SHIFT or max(abs(a["sx"] - 1), abs(a["sy"] - 1)) > MAX_SCALE:
            info["skipped"] = "the redraw moved or changed too much"
            views.append(info)
            print(f"view {yaw}: skipped ({info['align']})", flush=True)
            continue
        sharp = face.apply_alignment(sharp, a)
        merged = face.merge_detail(r["image"], sharp, sigma=3.0)
        merged = np.where(r["mask"][..., None], merged, r["image"])
        weight = face.screen_weight(r["mask"], feather_frac=0.004)
        res = face.project_back(mesh, np.zeros_like(tex), cam, merged, weight, facing=FACING)
        acc += res["texture"]
        wsum += res["texel_weight"]
        info["texels"] = res["touched"]
        views.append(info)
        print(f"view {yaw}: projected, {res['touched']} texels", flush=True)
    a = np.clip(wsum, 0.0, 1.0)[..., None]
    avg = acc / np.maximum(wsum, 1e-9)[..., None]
    new = np.clip(tex * (1 - a) + avg * a, 0.0, 1.0)
    cover = uv_cover(mesh, T)
    new = np.where(cover[..., None], new, tex)  # the gutter keeps what it had (it was grown from the islands)
    newp = os.path.join(out, "base_color.png")
    face.save_rgb(new, newp)

    # Measures on the same cameras: before (today's texture) and after.
    rows = []
    for yaw in yaws:
        cam = face.Camera(target=centre, yaw_deg=yaw, scale=scale, size=S)
        before = face.render_view(mesh, tex, cam, background=BACKGROUND)
        after = face.render_view(mesh, new, cam, background=BACKGROUND)
        sb, sa = face.sharpness(before["image"], before["mask"]), face.sharpness(after["image"], after["mask"])
        rows.append({"yaw": yaw, "sharp_before": sb, "sharp_after": sa, "ratio": round(sa / sb, 3) if sb else None,
                     "before": before["image"], "after": after["image"]})
    report = {
        "build": os.path.basename(build), "variant": variant or "a", "views": [{k: x[k] for k in x} for x in views],
        "texture": T, "render": S, "seconds": round(time.time() - t0, 1),
        "texels_covered": round(float((wsum > 0.05).sum() / max(1, cover.sum())), 3),
        "real_detail_db_before": real_detail_db(tex, cover), "real_detail_db_after": real_detail_db(new, cover),
        "view_sharpness": [{k: r[k] for k in ("yaw", "sharp_before", "sharp_after", "ratio")} for r in rows],
        "mean_colour_shift": round(float(np.abs(face.blur(new, 8) - face.blur(tex, 8))[cover].mean() * 255), 2),
    }
    with open(os.path.join(out, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)

    # Contact sheet: before above after, every view at 384 px, plus a 1:1 crop of the front and back.
    cell = 384
    sheet = Image.new("RGB", (cell * len(rows), cell * 2 + 512), (40, 40, 40))
    for i, r in enumerate(rows):
        for j, key in enumerate(("before", "after")):
            sheet.paste(Image.fromarray(face.to_uint8(r[key])).resize((cell, cell), Image.LANCZOS), (i * cell, j * cell))
    for k, idx in enumerate((0, len(rows) // 2)):
        for j, key in enumerate(("before", "after")):
            img = Image.fromarray(face.to_uint8(rows[idx][key]))
            c = S // 2
            sheet.paste(img.crop((c - 128, int(S * 0.3) - 128, c + 128, int(S * 0.3) + 128)).resize((256, 256), Image.NEAREST),
                        (k * 512 + j * 256, cell * 2))
    sheet.save(os.path.join(out, "sheet.png"))
    print(json.dumps({k: report[k] for k in ("real_detail_db_before", "real_detail_db_after", "texels_covered", "mean_colour_shift", "seconds")}), flush=True)
    for r in report["view_sharpness"]:
        print(r, flush=True)


if __name__ == "__main__":
    main()
