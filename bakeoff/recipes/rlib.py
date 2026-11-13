"""Shared helpers for the recipes bake-off (docs/RECIPES-BAKEOFF.md).

Uses the app's real graph builders (backend/vgac/graphs) and the app's prompt assembly order
(asset prompt + style prompt block + category pose block, as vgac/stages/concept.build_prompt), adds an optional
LoRA after the model loader, and appends the app's BiRefNet cutout to the same graph so one job gives the image and
its RGBA cutout. Run with backend/.venv/Scripts/python.exe.

LoRAs tested here live only in the VGAC/ folder under ComfyUI's loras folder (loras_dir in the app's settings;
downloaded for this bake-off).
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.dirname(HERE))

from comfy import run, download, images_of  # noqa: E402  (bakeoff/comfy.py)
from vgac import reference  # noqa: E402
from vgac.graphs import concept as cg  # noqa: E402
from vgac.graphs import qwen_edit as qg  # noqa: E402
from vgac import imaging  # noqa: E402

OUT = os.path.join(ROOT, "bakeoff", "out", "recipes")
BS = chr(92)
VGAC = "VGAC" + BS
API = "http://127.0.0.1:8765/api/gpus"
INSTANCES = {"b": "http://127.0.0.1:8189", "a": "http://127.0.0.1:8188"}

SUBJECTS = {
    "knight": ("humanoid", "A sturdy young woman knight with short dark hair and no helmet, wearing worn steel plate armour "
               "with brass trim over a deep blue padded tunic, leather gloves and heavy boots, a small round shield "
               "strapped flat on her back."),
    "wolf": ("quadruped", "A lean grey wolf with a thick cream chest ruff, dark stripes along the back, a long bushy tail "
             "held low, pointed ears, amber eyes and slightly oversized paws."),
    "chest": ("prop_rigid", "A small sturdy wooden treasure chest with a curved lid, dark oak planks, chunky iron bands "
              "and rivets, and a heavy brass padlock on the front."),
}
SEEDS = [int(s) for s in os.environ.get("RECIPE_SEEDS", "1101,2202").split(",")]


def instance() -> str:
    """Pick a ComfyUI instance by the app's card switches; wait (re-check every 3 min, give up after 10 min down)."""
    down_since = None
    while True:
        try:
            rows = {r["instance"]: r for r in json.load(urllib.request.urlopen(API, timeout=10))}
        except Exception as e:  # backend down: fall back to probing B directly
            rows = {}
            print("gpus api error", e, flush=True)
        b, a = rows.get("b", {}), rows.get("a", {})
        pin = os.environ.get("RECIPE_INSTANCE")  # "a": the owner asked for the 3060 only
        if pin == "a":
            b = {}
        if b.get("enabled") and b.get("reachable"):
            return INSTANCES["b"]
        if a.get("enabled") and a.get("reachable") and "pictures" in a.get("allowed_kinds", []):
            return INSTANCES["a"]
        down_since = down_since or time.time()
        if time.time() - down_since > 600:
            raise SystemExit("no instance usable for more than 10 minutes")
        print("no card free for pictures; waiting", flush=True)
        time.sleep(180)


def build_prompt(asset_prompt: str, style_id: str | None, category_id: str, style_block: str | None = None,
                 trigger: str = "") -> str:
    """The app's order: asset prompt, style block, pose block. `trigger` (LoRA trigger words) goes first."""
    parts = [asset_prompt.strip()]
    block = style_block if style_block is not None else (reference.style(style_id)["prompt_block"] if style_id else "")
    if block:
        parts.append(" ".join(block.split()))
    parts.append(reference.category(category_id)["pose_block"])
    p = " ".join(parts)
    return (trigger.strip() + " " + p) if trigger else p


def _loader_ids(g):
    return [k for k, n in g.items() if n["class_type"] in ("CheckpointLoaderSimple", "UNETLoader")]


def add_lora(g: dict, lora_file: str, strength: float, nid: str = "lx") -> dict:
    """Insert LoraLoaderModelOnly right after the model loader (as graphs/concept.pixel_graph does)."""
    src = _loader_ids(g)[0]
    for k, n in g.items():
        for key, v in n["inputs"].items():
            if key == "model" and v == [src, 0]:
                n["inputs"][key] = [nid, 0]
    g[nid] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": [src, 0], "lora_name": lora_file, "strength_model": strength}}
    return g


def add_cutout(g: dict, image_ref: list, prefix: str) -> dict:
    """The app's BiRefNet cutout (graphs/birefnet.py) on an image output inside the same graph."""
    g["cb"] = {"class_type": "LoadBackgroundRemovalModel", "inputs": {"bg_removal_name": "birefnet.safetensors"}}
    g["cm"] = {"class_type": "RemoveBackground", "inputs": {"bg_removal_model": ["cb", 0], "image": image_ref}}
    g["ci"] = {"class_type": "InvertMask", "inputs": {"mask": ["cm", 0]}}
    g["cj"] = {"class_type": "JoinImageWithAlpha", "inputs": {"image": image_ref, "alpha": ["ci", 0]}}
    g["cs"] = {"class_type": "SaveImage", "inputs": {"images": ["cj", 0], "filename_prefix": prefix + "_cut"}}
    return g


def run_and_fetch(g: dict, save_ids: dict[str, str], dest_base: str, base: str | None = None) -> tuple[dict[str, str], float]:
    """Queue, wait, download each named SaveImage node's first image to dest_base + '_' + name + '.png'."""
    base = base or instance()
    outputs, secs = run(base, g)
    got = {}
    for name, nid in save_ids.items():
        imgs = outputs.get(nid, {}).get("images", [])
        if imgs:
            got[name] = download(base, imgs[0], f"{dest_base}_{name}.png")
    return got, secs


# ---------------------------------------------------------------------------------------------- metrics

def _alpha(path, thr=127):
    return np.array(Image.open(path).convert("RGBA").getchannel("A")) > thr


def dilate(mask: np.ndarray, it: int) -> np.ndarray:
    m = mask.copy()
    for _ in range(it):
        n = m.copy()
        n[1:] |= m[:-1]; n[:-1] |= m[1:]; n[:, 1:] |= m[:, :-1]; n[:, :-1] |= m[:, 1:]
        m = n
    return m


def components(mask: np.ndarray, min_frac: float = 0.01) -> int:
    """Connected components (8-neighbour, on a 256 px copy) whose area is at least min_frac of the foreground."""
    small = np.array(Image.fromarray(mask.astype(np.uint8) * 255).resize((256, 256), Image.NEAREST)) > 127
    seen = np.zeros_like(small)
    areas = []
    H, W = small.shape
    for y0, x0 in zip(*np.where(small)):
        if seen[y0, x0]:
            continue
        stack, n = [(y0, x0)], 0
        seen[y0, x0] = True
        while stack:
            y, x = stack.pop()
            n += 1
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < H and 0 <= xx < W and small[yy, xx] and not seen[yy, xx]:
                        seen[yy, xx] = True
                        stack.append((yy, xx))
        areas.append(n)
    tot = max(1, sum(areas))
    return int(sum(a >= min_frac * tot for a in areas))


def concept_metrics(cut_path: str, category: str, raw_path: str | None = None) -> dict:
    im = Image.open(cut_path).convert("RGBA")
    rgb = np.array(im)[..., :3].astype(np.float32)
    a = np.array(im.getchannel("A"))
    m = a > 127
    h, w = m.shape
    out = {}
    ys, xs = np.where(m)
    if len(xs) == 0:
        return {"empty": True}
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    # fully in frame: smallest gap between the alpha box and an image edge, as a fraction of the image side
    out["margin"] = round(float(min(x0, y0, w - 1 - x1, h - 1 - y1)) / max(w, h), 4)
    out["coverage"] = round(float(m.mean()), 4)
    out["components"] = components(m)
    # background plainness: RGB std outside the cutout, eroded away from the subject edge
    bg = ~dilate(a > 8, 12)
    out["bg_std"] = round(float(rgb[bg].std(axis=0).mean()) if bg.sum() > 1000 else -1.0, 2)
    # silhouette width per row inside the box
    widths = m[y0:y1 + 1].sum(axis=1)
    bh = y1 - y0 + 1
    if category == "humanoid":
        def band(f0, f1):
            r0, r1 = y0 + int(f0 * bh), y0 + int(f1 * bh)
            return m[r0:r1]
        sh = band(0.18, 0.30).sum(axis=1).mean()  # shoulders
        hips = band(0.45, 0.55)
        # widest extent (leftmost to rightmost foreground) at hip/hand height, vs the shoulder width
        ext = [(int(np.ptp(np.where(r)[0])) + 1) if r.any() else 0 for r in hips]
        out["hip_extent_over_shoulder"] = round(float(np.mean(ext)) / max(1.0, float(sh)), 3)
        # arms clear of the body: fraction of rows from 30 to 55 percent of the height with 3 or more separate runs
        rows = band(0.30, 0.55)
        runs = [int(((r[1:].astype(int) - r[:-1].astype(int)) == 1).sum() + r[0]) for r in rows]
        out["arm_gap_rows"] = round(float(np.mean([x >= 3 for x in runs])), 3)
    # style: colour count inside the subject (5 bits per channel) and blockiness of the raw image
    sub = (rgb[m] // 8).astype(np.int32)
    out["colours_5bit"] = int(len(np.unique(sub[:, 0] * 1024 + sub[:, 1] * 32 + sub[:, 2])))
    if raw_path:
        out["block_err"] = blockiness(raw_path)
    return out


def blockiness(path: str) -> float:
    """How well the image is explained by a coarse pixel grid: mean abs error (0-255) after a block-mean
    downscale and nearest upscale, at the best block size from 4 to 32 px. Low = real pixel art."""
    im = np.array(Image.open(path).convert("RGB")).astype(np.float32)
    best = 1e9
    for k in (4, 6, 8, 10, 12, 16, 20, 24, 32):
        h, w = (im.shape[0] // k) * k, (im.shape[1] // k) * k
        for oy in (0, k // 2):
            for ox in (0, k // 2):
                c = im[oy:oy + h - k, ox:ox + w - k] if oy or ox else im[:h, :w]
                hh, ww = (c.shape[0] // k) * k, (c.shape[1] // k) * k
                c = c[:hh, :ww]
                blk = c.reshape(hh // k, k, ww // k, k, 3).mean(axis=(1, 3))
                rec = np.repeat(np.repeat(blk, k, 0), k, 1)
                best = min(best, float(np.abs(c - rec).mean()))
    return round(best, 2)


# ---------------------------------------------------------------------------------------------- sheets

def label_sheet(rows: list[list[tuple[str, str]]], dest: str, cell: int = 300, title: str = "") -> str:
    """rows of (path, label) cells; labels drawn under each cell. Missing paths draw a grey box."""
    from PIL import ImageDraw, ImageFont
    try:
        font = ImageFont.truetype("arial.ttf", 15)
        tfont = ImageFont.truetype("arial.ttf", 20)
    except Exception:
        font = tfont = ImageFont.load_default()
    ncol = max(len(r) for r in rows)
    lab_h, top = 40, (34 if title else 0)
    sheet = Image.new("RGB", (ncol * cell, top + len(rows) * (cell + lab_h)), (30, 30, 30))
    d = ImageDraw.Draw(sheet)
    if title:
        d.text((8, 6), title, fill=(255, 255, 255), font=tfont)
    for ri, r in enumerate(rows):
        for ci, (p, lab) in enumerate(r):
            x, y = ci * cell, top + ri * (cell + lab_h)
            if p and os.path.exists(p):
                im = Image.open(p).convert("RGBA")
                sc = cell / max(im.width, im.height)
                im = im.resize((max(1, int(im.width * sc)), max(1, int(im.height * sc))),
                               Image.NEAREST if sc > 1 else Image.LANCZOS)
                bgc = Image.new("RGBA", (cell, cell), (255, 255, 255, 255))
                bgc.alpha_composite(im, ((cell - im.width) // 2, (cell - im.height) // 2))
                sheet.paste(bgc.convert("RGB"), (x, y))
            else:
                d.rectangle([x, y, x + cell - 1, y + cell - 1], fill=(90, 90, 90))
            for li, line in enumerate(lab.split("\n")[:2]):
                d.text((x + 4, y + cell + 2 + li * 18), line, fill=(230, 230, 230), font=font)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    sheet.save(dest)
    return dest
