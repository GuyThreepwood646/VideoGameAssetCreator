"""Turnaround add-on test on Qwen-Image-Edit-2511 + Lightning 4 steps (the app's angle_graph).

Methods
  fal      the app today: fal multiple-angles LoRA 0.9, five absolute views, one job per view
  cam<s>   berkerdooo camera-angle LoRA at strength s instead of fal: relative moves +90, 180, -90, +45
  sheet<s> tarn59 character turnaround sheet LoRA at strength s instead of fal: one wide image, split into figures

Usage: python turn_ab.py source <subject>        (makes the painted source concept, seed 1101)
       python turn_ab.py run <subject> <method>...
       python turn_ab.py report
Images: bakeoff/out/recipes/turn/<subject>/<method>/...
"""
from __future__ import annotations

import json
import os
import shutil
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rlib  # noqa: E402
from rlib import VGAC, cg, qg, imaging  # noqa: E402
from comfy import stage_input  # noqa: E402

TURN = os.path.join(rlib.OUT, "turn")
CAM = {
    "rel+90": "Move the camera 90 degrees to the right around the object. Target camera elevation: 0 degrees. Keep the object, its colors, and the white background the same.",
    "rel180": "Move the camera 180 degrees to the right around the object. Target camera elevation: 0 degrees. Keep the object, its colors, and the white background the same.",
    "rel-90": "Move the camera 90 degrees to the left around the object. Target camera elevation: 0 degrees. Keep the object, its colors, and the white background the same.",
    "rel+45": "Move the camera 45 degrees to the right around the object. Target camera elevation: 0 degrees. Keep the object, its colors, and the white background the same.",
}
SEED = 777


def src_paths(subject):
    d = os.path.join(TURN, subject)
    return os.path.join(d, "source_img.png"), os.path.join(d, "source_cut.png"), os.path.join(d, "source_white.png")


def make_source(subject):
    category, asset = rlib.SUBJECTS[subject]
    prompt = rlib.build_prompt(asset, "painted", category)
    g = cg.zimage_graph(prompt, 1101, f"vgac_recipes/turn/{subject}_source")
    rlib.add_cutout(g, ["7", 0], f"vgac_recipes/turn/{subject}_source")
    d = os.path.join(TURN, subject)
    os.makedirs(d, exist_ok=True)
    got, _ = rlib.run_and_fetch(g, {"img": "8", "cut": "cs"}, os.path.join(d, "source"))
    imaging.white_composite(got["cut"], os.path.join(d, "source_white.png"))
    print(got)


def cut_local(path, name):
    """The app's BiRefNet cutout of a local file (graphs/birefnet.py)."""
    from vgac.graphs.birefnet import cutout_graph
    nm = stage_input(path, "vgacr_" + name + ".png")
    g = cutout_graph(nm, "vgac_recipes/turn/cut_" + name)
    base = path[:-4]
    got, _ = rlib.run_and_fetch(g, {"cut": "save"}, base)
    want = (path[:-8] if path.endswith("_img.png") else path[:-4]) + "_cut.png"
    os.replace(got["cut"], want)
    return want


def normalise(d):
    """Older runs saved <view>_img_cut.png; rename to <view>_cut.png."""
    for f in os.listdir(d):
        if f.endswith("_img_cut.png"):
            os.replace(os.path.join(d, f), os.path.join(d, f.replace("_img_cut.png", "_cut.png")))


def angle(src_name, prompt, prefix, lora_file=None, strength=0.9, width=1024, height=1024):
    g = qg.angle_graph(src_name, prompt, SEED, prefix, lora_strength=strength)
    if lora_file:
        g["l2"]["inputs"]["lora_name"] = lora_file
    g["lat"]["inputs"]["width"], g["lat"]["inputs"]["height"] = width, height
    return g


def run_fal(subject):
    d = os.path.join(TURN, subject, "fal")
    os.makedirs(d, exist_ok=True)
    src = stage_input(src_paths(subject)[0], f"vgacr_{subject}_src.png")
    for view, p in qg.VIEWS.items():
        if view == "top" or view in os.environ.get("TURN_SKIP", "").split(","):
            continue
        base = os.path.join(d, view)
        if os.path.exists(base + "_cut.png"):
            continue
        g = angle(src, p, f"vgac_recipes/turn/{subject}_fal_{view}")
        got, secs = rlib.run_and_fetch(g, {"img": "save"}, base)
        cut_local(got["img"], f"{subject}_fal_{view}")
        print(subject, "fal", view, secs, flush=True)


def run_cam(subject, s):
    d = os.path.join(TURN, subject, f"cam{s}")
    os.makedirs(d, exist_ok=True)
    src = stage_input(src_paths(subject)[2], f"vgacr_{subject}_srcwhite.png")
    for view, p in CAM.items():
        base = os.path.join(d, view)
        if os.path.exists(base + "_cut.png"):
            continue
        g = angle(src, p, f"vgac_recipes/turn/{subject}_cam{s}_{view}", VGAC + "qie2511_camera_angle_berkerdooo.safetensors", s)
        got, secs = rlib.run_and_fetch(g, {"img": "save"}, base)
        cut_local(got["img"], f"{subject}_cam{s}_{view}")
        print(subject, "cam", s, view, secs, flush=True)


def sheet_input(subject):
    """Source cutout on white, cropped to the subject with margin, padded to the LoRA's 600x1080 portrait input."""
    im = Image.open(src_paths(subject)[1]).convert("RGBA")
    a = np.array(im.getchannel("A")) > 127
    ys, xs = np.where(a)
    crop = im.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
    W, H = 600, 1080
    s = min(W * 0.9 / crop.width, H * 0.92 / crop.height)
    crop = crop.resize((int(crop.width * s), int(crop.height * s)), Image.LANCZOS)
    bg = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    bg.alpha_composite(crop, ((W - crop.width) // 2, (H - crop.height) // 2))
    p = os.path.join(TURN, subject, "sheet_input.png")
    bg.convert("RGB").save(p)
    return p


def run_sheet(subject, s):
    d = os.path.join(TURN, subject, f"sheet{s}")
    os.makedirs(d, exist_ok=True)
    base = os.path.join(d, "sheet")
    normalise(d)
    if not os.path.exists(base + "_cut.png") and not os.path.exists(base + "_img.png"):
        src = stage_input(sheet_input(subject), f"vgacr_{subject}_sheetin.png")
        g = angle(src, "Character turnaround sheet", f"vgac_recipes/turn/{subject}_sheet{s}",
                  VGAC + "qie2511_turnaround_sheet_tarn59.safetensors", s, width=2592, height=1088)
        # keep the input at its own size (the LoRA was trained on 600x1080 inputs), not 1 MP
        g["sc"]["inputs"]["megapixels"] = 0.65
        got, secs = rlib.run_and_fetch(g, {"img": "save"}, base)
        cut_local(got["img"], f"{subject}_sheet{s}")
        print(subject, "sheet", s, secs, flush=True)
    if not os.path.exists(base + "_cut.png"):
        cut_local(base + "_img.png", f"{os.path.basename(os.path.dirname(d))}_sheet{s}")
    split_sheet(base + "_cut.png", d)


def split_sheet(cut, d):
    """Split a sheet cutout into figures at empty columns; save each as fig<i>_cut.png."""
    im = Image.open(cut).convert("RGBA")
    a = np.array(im.getchannel("A")) > 127
    col = a.sum(axis=0) > 2
    figs, x, n = [], 0, len(col)
    while x < n:
        if col[x]:
            x0 = x
            while x < n and col[x]:
                x += 1
            if x - x0 > 20:
                figs.append((x0, x))
        x += 1
    for f in os.listdir(d):
        if f.startswith("fig"):
            os.remove(os.path.join(d, f))
    for i, (x0, x1) in enumerate(figs):
        im.crop((max(0, x0 - 10), 0, min(n, x1 + 10), im.height)).save(os.path.join(d, f"fig{i}_cut.png"))
    return len(figs)


def measure(subject, method):
    d = os.path.join(TURN, subject, method)
    normalise(d)
    src_cut = src_paths(subject)[1]
    ref = imaging.alpha_bbox(src_cut)
    href = imaging.colour_histogram(src_cut)
    if method == "fal":
        views = [v for v in ["front", "left", "back", "right", "quarter"] if os.path.exists(os.path.join(d, f"{v}_cut.png")) or os.path.exists(os.path.join(d, f"{v}_img_cut.png"))]
        files = {v: os.path.join(d, f"{v}_cut.png") for v in views}
        front = files["front"]
        pairs = [("left", "right")]
    elif method.startswith("cam"):
        views = list(CAM)
        files = {v: os.path.join(d, f"{v}_cut.png") for v in views}
        files["source"] = src_cut
        front = src_cut
        pairs = [("rel+90", "rel-90")] if subject == "knight" else [("source", "rel-90"), ("source", "rel+90")]
    else:
        figs = sorted(f for f in os.listdir(d) if f.startswith("fig"))
        files = {f[:-8]: os.path.join(d, f) for f in figs}
        front = files.get("fig0", src_cut)
        pairs = [("fig1", "fig3")] if len(files) >= 4 else []
    fstats = imaging.alpha_bbox(front)
    rows = {}
    for v, p in files.items():
        if not os.path.exists(p):
            continue
        st = imaging.alpha_bbox(p)
        a = np.array(Image.open(p).convert("RGBA").getchannel("A")) > 127
        rows[v] = {"height_ratio": round(st["height"] / max(1, fstats["height"]), 3),
                   "hist_vs_source": round(imaging.histogram_distance(href, imaging.colour_histogram(p)), 3),
                   "components": rlib.components(a)}
    mir = [round(imaging.mirror_iou(files[x], files[y]), 3) for x, y in pairs if x in files and y in files]
    gen = {k: r for k, r in rows.items() if k != "source"}  # generated views only
    hr = [r["height_ratio"] for r in gen.values()]
    rows_all, rows = rows, gen
    summary = {"subject": subject, "method": method, "n_views": len(rows),
               "mirror_iou": max(mir) if mir else None,
               "height_ratio_spread": round(max(hr) - min(hr), 3) if hr else None,
               "hist_vs_source_mean": round(float(np.mean([r["hist_vs_source"] for r in rows.values()])), 3) if rows else None,
               "hist_vs_source_max": round(max(r["hist_vs_source"] for r in rows.values()), 3) if rows else None,
               "single_subject_views": sum(r["components"] == 1 for r in rows.values()),
               "views": rows_all}
    return summary


def report():
    out = []
    rowsheet = []
    for subject in os.listdir(TURN):
        sd = os.path.join(TURN, subject)
        if not os.path.isdir(sd):
            continue
        for method in sorted(os.listdir(sd)):
            md = os.path.join(sd, method)
            if not os.path.isdir(md):
                continue
            s = measure(subject, method)
            out.append(s)
            print(json.dumps({k: v for k, v in s.items() if k != "views"}))
            cells = [(src_paths(subject)[0], f"{subject} source")]
            for v in s["views"]:
                if v == "source":
                    continue
                p = os.path.join(md, f"{v}_img.png") if not v.startswith("fig") else None
                if v.startswith("fig") and v == "fig0":
                    cells.append((os.path.join(md, "sheet_img.png"), f"{method} sheet"))
                if p:
                    r = s["views"][v]
                    cells.append((p, f"{method} {v}\nh{r['height_ratio']} hist{r['hist_vs_source']} cc{r['components']}"))
            rowsheet.append(cells)
    with open(os.path.join(rlib.OUT, "turn.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print(rlib.label_sheet(rowsheet, os.path.join(rlib.OUT, "sheets", "turnaround.png"), cell=260, title="Turnaround add-ons"))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "source":
        make_source(sys.argv[2])
    elif cmd == "run":
        subject = sys.argv[2]
        for m in sys.argv[3:]:
            if m == "fal":
                run_fal(subject)
            elif m.startswith("cam"):
                run_cam(subject, float(m[3:]))
            elif m.startswith("sheet"):
                run_sheet(subject, float(m[5:]))
    elif cmd == "report":
        report()
