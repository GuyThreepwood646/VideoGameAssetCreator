"""Glow map (emission) for scenery: the bright, saturated areas of the picture (neon signs, lit windows) carried onto
the texture where they land.

The texture model predicts a de-lit base colour, so a lit sign comes out dull. The picture still shows it bright, so
the picture's own bright and saturated pixels are projected onto the model by the views the picture paint already
fitted (`srcproject.compose` over the kept views, the same fit and the same blend, on a black texture) and written as
`emission.png` beside the other maps. Nothing is fitted again.
"""
from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path
from typing import Any

import numpy as np

from . import face, srcproject
from .srcproject import Atlas

# A sign is a lit panel (bright and saturated, a neon hue) that carries letters. Warm surfaces (cardboard boxes, roof edges,
# an awning, window glass, orange panels) do not glow: they cannot be told from a lit window by colour or shape.
V_LOW, V_HIGH = 0.65, 0.85
S_LOW, S_HIGH = 0.35, 0.55
LETTER_V = 0.70                # a letter is at least this bright (it is pale or white: low saturation, which is why a
                               # saturation rule alone leaves every letter black)
WARM_HUE = (12.0, 75.0)        # degrees: orange to yellow-green; neon colours (pink, magenta, blue, cyan, green) lie outside
CLOSE = 0.015                  # share of the longer side: bridges the gaps letters leave in a panel
MIN_LETTERS = 0.02             # a panel is a sign only when this share of it is letters
FINDER_SIDE = 1024               # the longer side the finder sees
MAX_LETTER_AREA = 0.004        # share of the picture: the largest hole in a panel that still counts as a letter
PANEL_GAIN = 0.6               # the panel glows less than its letters
# Colour rule only: when more than this share of the painted texels would glow, the picture is colourful all over, not lit in
# places: no map. A map from the item finder's named parts is not held to it.
MAX_SHARE = 0.25
# A map with fewer glowing texels than this share is empty (noise).
MIN_SHARE = 0.0005
GLOW_AT = 0.2


def mask_picture(img: np.ndarray, alpha: np.ndarray | None = None) -> tuple[np.ndarray, dict[str, Any]]:
    """(H, W, 3) floats 0..1 -> (the picture's glowing pixels in their own colour, the rest black; record). Letters of a sign
    glow fully, the lit panel behind them at PANEL_GAIN. `alpha` (H, W) keeps the background out."""
    import cv2

    img = np.clip(np.asarray(img, dtype=np.float32), 0.0, 1.0)
    h, w = img.shape[:2]
    v = img.max(axis=-1)
    s = (v - img.min(axis=-1)) / np.maximum(v, 1e-6)
    inside = np.ones((h, w), bool) if alpha is None else np.asarray(alpha) > 0.5
    lit = np.clip((v - V_LOW) / (V_HIGH - V_LOW), 0.0, 1.0) * np.clip((s - S_LOW) / (S_HIGH - S_LOW), 0.0, 1.0)
    panel0 = (lit > 0.2) & inside
    k = max(3, int(round(CLOSE * max(h, w))) | 1)
    closed = cv2.morphologyEx(panel0.astype(np.uint8), cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)
    hue = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)[..., 0]
    out = np.zeros_like(img)
    signs, letters_px = 0, 0
    pale = (v >= LETTER_V) & inside
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < 12:
            continue
        comp = lab == i
        filled = cv2.morphologyEx(comp.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)).astype(bool)
        ys, xs = np.nonzero(comp)
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        crop = comp[y0:y1, x0:x1].astype(np.uint8)
        holes = cv2.floodFill(np.pad(1 - crop, 1).astype(np.uint8).copy(), None, (0, 0), 0)[1]
        holes = holes[1:-1, 1:-1].astype(np.uint8)   # holes of the panel: where its letters are
        hn, hl, hs, _ = cv2.connectedComponentsWithStats(holes, connectivity=8)
        small = np.zeros(hn, bool)
        small[1:] = hs[1:, cv2.CC_STAT_AREA] <= MAX_LETTER_AREA * h * w   # a ring round a wall is not a letter
        holes = small[hl]
        area = np.zeros((h, w), bool)
        area[y0:y1, x0:x1] = holes | crop.astype(bool)
        letters = area & pale & ~panel0
        m_hue = float(np.median(hue[comp & panel0])) if (comp & panel0).any() else 0.0
        warm = WARM_HUE[0] <= m_hue <= WARM_HUE[1]
        share = float(letters.sum()) / max(int(area.sum()), 1)
        if warm or share < MIN_LETTERS:
            continue
        signs += 1
        letters_px += int(letters.sum())
        out[comp & panel0] = img[comp & panel0] * (lit[comp & panel0] * PANEL_GAIN)[:, None]
        out[letters] = img[letters]
    return out, {"signs": signs, "letter_share": round(letters_px / max(int(inside.sum()), 1), 4)}


def finder_gains(settings: Any, kept: dict[str, Any], work: Path) -> tuple[list[np.ndarray] | None, dict[str, Any]]:
    """With the item finder installed (item_suggest.installed: Grounding DINO tiny and SAM 2.1 small, both Apache-2.0, in their own
    Python), the parts the pictures show lit are named by a text prompt (neon signs, neon figures, lit windows) and only those
    glow (finder_worker.py, glow mode): one gain picture per fitted view. -> (gains, record), or (None, record) when the finder is not
    installed or fails, and the caller uses the no-download rule (mask_picture)."""
    import subprocess

    from PIL import Image

    from . import item_suggest

    if not item_suggest.installed(settings):
        return None, {"method": "colour rule", "finder": "not installed"}
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    shapes = []
    for i, im in enumerate(kept["images"]):
        a = np.clip(np.asarray(im), 0.0, 1.0)
        shapes.append(a.shape[:2])
        pic = Image.fromarray((a * 255).astype(np.uint8), "RGB")
        pic.thumbnail((FINDER_SIDE, FINDER_SIDE), Image.LANCZOS)
        pic.save(work / f"view_{i}.png")
    env = {**os.environ, "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2"}   # two cores, not all
    t0 = time.time()
    r = subprocess.run([str(item_suggest.finder_python(settings)), str(Path(__file__).with_name("finder_worker.py")), "glow", str(work),
                        str(len(shapes)), str(item_suggest.finder_models(settings))], capture_output=True, text=True, timeout=1800, env=env)
    if r.returncode != 0 or "FINDER_OK" not in r.stdout:
        return None, {"method": "colour rule", "finder": "failed: " + (r.stderr or r.stdout)[-160:].strip()}
    gains = []
    for i, (h, w) in enumerate(shapes):
        g = np.load(work / f"view_{i}_gain.npy")
        if g.shape != (h, w):
            g = np.asarray(Image.fromarray(g, "F").resize((w, h), Image.BILINEAR))
        gains.append(g)
    return gains, {"method": "finder", "finder_seconds": round(time.time() - t0, 1), "detected": json.loads((work / "glow_report.json").read_text())}


def emission_map(mesh: dict[str, np.ndarray], kept: dict[str, Any], size: int,
                 gains: list[np.ndarray] | None = None) -> tuple[np.ndarray | None, dict[str, Any]]:
    """The glow texture (size x size x 3, floats 0..1) for the model's current layout from the fitted views `kept`
    (srcproject.paint's keep), or None with the reason in the record."""
    if not kept or not kept.get("views"):
        return None, {"made": False, "reason": "no fitted picture views"}
    atlas = Atlas(mesh, size)
    if atlas.shared > srcproject.MAX_SHARED:
        return None, {"made": False, "reason": "mirrored layout"}
    images, signs = [], 0
    for i, (v, im) in enumerate(zip(kept["views"], kept["images"])):
        if gains is not None:   # the finder named the lit parts: they glow in the picture's own colour
            images.append(np.clip(np.asarray(im), 0.0, 1.0) * np.asarray(gains[i])[..., None])
            continue
        al = getattr(v, "alpha_img", None)
        m, r = mask_picture(im, al if al is not None and np.shape(al)[:2] == np.shape(im)[:2] else None)
        images.append(m)
        signs = max(signs, r["signs"])
    c = srcproject.compose(atlas, kept["views"], images, kept["n_primary"], np.zeros((size, size, 3)), kept.get("zone"))
    em, alpha = c["base_color"], c["alpha"] > 0.5
    own = ~atlas.is_gut
    painted = max(int((alpha & own).sum()), 1)
    glow = em.max(axis=-1) > GLOW_AT
    share = float(((em.reshape(-1, 3)[atlas.idx].max(axis=-1) > GLOW_AT) & own & alpha).sum()) / painted
    rec = {"made": True, "glow_share": round(share, 4), "size": size, "signs": signs}
    if gains is None and share > MAX_SHARE:   # the guard is for the colour rule; the finder named the lit parts
        return None, {**rec, "made": False, "reason": "bright and saturated over most of the picture"}
    if share < MIN_SHARE or not glow.any():
        return None, {**rec, "made": False, "reason": "nothing bright and saturated enough"}
    return np.clip(em, 0.0, 1.0), rec


def write_emission(glb: Any, kept: dict[str, Any], size: int, out_png: Any, gains: list[np.ndarray] | None = None) -> tuple[Any, dict[str, Any]]:
    """emission_map for a model file; writes out_png when there is a glow. -> (path or None, record).

    Each view's depths are taken again from the model as it is now: the clean-up may have moved the surface since the
    pictures were fitted (a flattened sign, vgac/signface.py), and old depths would hide the moved part from every view."""
    mesh = face.load_mesh(glb)
    if mesh is None:
        return None, {"made": False, "reason": "the model has no texture layout"}
    if kept and kept.get("views"):
        views = [copy.copy(v) for v in kept["views"]]
        for v in views:
            v.raster = srcproject.Raster(mesh, v.cam)
        kept = {**kept, "views": views}
    em, rec = emission_map(mesh, kept, size, gains)
    if em is None:
        return None, rec
    return face.save_rgb(em, out_png), rec


def carry(parent_png: Any, out_png: Any, painted: np.ndarray | None = None) -> Any:
    """Touch-up painting keeps the glow of the clean-up it was made from: the parent's emission.png copied, with the texels
    the person painted (`painted`, a boolean mask at the picture's size) set to no glow. Returns out_png, or None when the
    parent has none."""
    from PIL import Image

    if not parent_png or not Path(parent_png).is_file():
        return None
    with Image.open(parent_png) as im:
        arr = np.array(im.convert("RGB"))
    if painted is not None:
        m = np.asarray(painted, dtype=bool)
        if m.shape != arr.shape[:2]:
            m = np.asarray(Image.fromarray(m.astype(np.uint8) * 255).resize((arr.shape[1], arr.shape[0]), Image.NEAREST)) > 127
        arr[m] = 0
    Image.fromarray(arr, "RGB").save(out_png)
    return Path(out_png)
