"""The item finder's worker: one module, three modes, run in the finder's own Python environment (item_suggest.py: Grounding
DINO tiny and SAM 2.1 small, both Apache-2.0), never in the backend venv. Models load one way, once per run, on two threads.

glow  Neon signs, neon figures and lit windows are named in each picture `<dir>/view_<i>.png`; a SAM mask for each box. Writes
      `<dir>/view_<i>_gain.npy`: float32 (H, W), how much of the picture's own colour glows there. Neon figures glow fully;
      signs and windows glow at PANEL_GAIN, rising to 1 for their brightest pixels (the letters). A window glows as a whole
      when enough of its mask is lit (lit_pixels, WINDOW_LIT_SHARE), else not at all, so dark glass stays dark. Where the
      finder named at least CATCH_MIN_REF lit windows in a view, warm, bright, window-shaped regions it missed glow too
      (missed_windows). Nothing else glows (boxes, awnings and plain bright paint are never named). Writes glow_report.json.
sign  Lettered signs (shop sign, signboard) in each picture `<dir>/view_<i>.png`: their boards as `<dir>/view_<i>_sign.npy`
      (bool, H x W) and `<dir>/sign_report.json`. From the picture `<panels_from>` on, the flat panels are named one by one
      (signs, neon figures, shelves, machines): masks in `<dir>/view_<i>_panels.npy` (bool, N x H x W), kinds and boxes in
      `<dir>/panel_report.json`.
hair  A phrase (the hair, or the worn item's label) in each picture `<views_dir>/<name>_color.png`: the best box, then its SAM
      mask as `<name>_mask.npy` (bool) and detection.json. The vote over polygons is done by the backend (item_suggest.py,
      hair_outline.py), which holds the id maps.

Run:
  <finder python> finder_worker.py glow <dir> <count> <models_dir>
  <finder python> finder_worker.py sign <dir> <count> <models_dir> [<panels_from>]
  <finder python> finder_worker.py hair <views_dir> "<phrase>" <models_dir> [name,name,...]
The optional names list replaces the default worn-item views (VIEWS). Prints FINDER_OK on success; a failure exits
non-zero with the reason on stderr.
"""
import json
import os
import sys
from typing import Any, NamedTuple

GDINO = "IDEA-Research/grounding-dino-tiny"
SAM2 = "facebook/sam2.1-hiera-small"
THREADS = 2              # runs on the processor inside a step: never every core
BOX_THRESHOLD = 0.25
TEXT_THRESHOLD = 0.2
MAX_BOX_SHARE = 0.35     # glow and sign: a box over this share of the picture is the whole building
HAIR_MAX_BOX_SHARE = 0.4  # hair: a box over this share of the picture is the whole figure, not the item
VIEWS = ["back", "left", "right", "back_left", "back_right", "top_back"]

GLOW_PHRASES = {"neon sign": "sign", "neon figure": "figure", "lit window": "window"}
GLOW_MIN_SCORE = {"sign": 0.3, "figure": 0.28, "window": 0.4}
MAX_SIDE = 1024
PANEL_GAIN = 0.6
WINDOW_LIT_OVER = 0.12   # a window pixel is lit when this much brighter than the wall round the window (V, 0..1) ...
WARM_HUE = (12.0, 75.0)  # ... or warm (orange to yellow-green degrees), bright and saturated
WINDOW_LIT_SHARE = 0.35  # a named window glows (all of it) when at least this share of its mask is lit, else none of it
WARM_STRONG = (0.6, 0.4)  # (V, S) at least: a saturated warm pane, even a little shaded
WARM_PALE = (0.75, 0.15)  # (V, S) at least: a pale cream or yellow pane, which is bright rather than saturated
CATCH_MIN_REF = 5        # the missed-window pass runs in a view only with at least this many lit named windows to learn from
CATCH_AREA = (0.12, 3.0)  # a caught region's area against the median lit named window's
CATCH_FILL = 0.6         # share of its bounding box a window-shaped region fills
CATCH_ASPECT = (0.3, 3.5)  # width over height
CATCH_HUE, CATCH_SV = 15.0, 0.25  # colour match to the lit named windows: hue degrees, S and V distance

SIGN_PHRASES = ("shop sign", "signboard")
SIGN_MIN_SCORE = 0.3
PANELS = {"shop sign": ("sign", 0.35), "signboard": ("sign", 0.35), "neon sign": ("sign", 0.35),
          "neon figure": ("figure", 0.35), "shelf of bottles": ("display", 0.3), "vending machine": ("case", 0.4)}
MAX_PANEL_SHARE = 0.1    # one panel, not a wall or the ground


class Models(NamedTuple):
    gproc: Any
    gmodel: Any
    sproc: Any
    smodel: Any


def load_models(models_dir: str) -> Models:
    """Grounding DINO and SAM 2.1 from the finder's model folder, offline, on THREADS threads."""
    os.environ["HF_HOME"] = models_dir
    os.environ["HF_HUB_OFFLINE"] = "1"
    import torch
    from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor, Sam2Model, Sam2Processor

    torch.set_num_threads(THREADS)
    gproc = AutoProcessor.from_pretrained(GDINO, local_files_only=True)
    gmodel = AutoModelForZeroShotObjectDetection.from_pretrained(GDINO, local_files_only=True).eval()
    sproc = Sam2Processor.from_pretrained(SAM2, local_files_only=True)
    smodel = Sam2Model.from_pretrained(SAM2, local_files_only=True).eval()
    return Models(gproc, gmodel, sproc, smodel)


def detect(models: Models, img, phrase: str) -> list[tuple[float, list[float]]]:
    """Every box Grounding DINO finds for `phrase` (a full stop is added), as (score, [x0, y0, x1, y1]) in picture pixels."""
    import torch

    gp = models.gproc
    inp = gp(images=img, text=phrase + ".", return_tensors="pt")
    with torch.no_grad():
        out = models.gmodel(**inp)
    res = gp.post_process_grounded_object_detection(out, inp.input_ids, threshold=BOX_THRESHOLD, text_threshold=TEXT_THRESHOLD,
                                                    target_sizes=[(img.size[1], img.size[0])])[0]
    return list(zip(res["scores"].tolist(), res["boxes"].tolist()))


def segment(models: Models, img, box) -> Any:
    """The SAM 2.1 mask (bool, H x W) of one box."""
    import torch

    sp = models.sproc
    si = sp(images=img, input_boxes=[[box]], return_tensors="pt")
    with torch.no_grad():
        so = models.smodel(**si, multimask_output=False)
    m = sp.post_process_masks(so.pred_masks, si["original_sizes"])[0][0]
    if m.ndim == 3:
        m = m[0]
    return m.numpy().astype(bool)


def iou(a, b) -> float:
    """Intersection over union of two boxes."""
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    return inter / max((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter, 1e-6)


# ---- glow ------------------------------------------------------------------------------------------------------------

def lit_pixels(rgb, hsv, box, mask):
    """Boolean picture: the pixels of a named window that are lit, clearly brighter than the wall round the box or warm and
    bright. Dark glass is neither, so it is left out."""
    import numpy as np

    H, W = mask.shape
    x0, y0, x1, y1 = box
    px, py = int((x1 - x0) * 0.4), int((y1 - y0) * 0.4)
    ring = np.zeros((H, W), bool)
    ring[max(int(y0) - py, 0):min(int(y1) + py, H), max(int(x0) - px, 0):min(int(x1) + px, W)] = True
    ring[max(int(y0), 0):min(int(y1), H), max(int(x0), 0):min(int(x1), W)] = False
    ring &= ~mask
    v = rgb.max(-1)
    wall = float(np.median(v[ring])) if ring.any() else 1.0
    return (v > wall + WINDOW_LIT_OVER) | warm_pixels(rgb, hsv)


def warm_pixels(rgb, hsv):
    """Boolean picture: warm (orange to yellow-green) and bright, saturated or pale."""
    v = rgb.max(-1)
    hue = hsv[..., 0] * (360.0 / 255.0)
    sat = hsv[..., 1] / 255.0
    return (hue >= WARM_HUE[0]) & (hue <= WARM_HUE[1]) & (((v >= WARM_STRONG[0]) & (sat >= WARM_STRONG[1])) | ((v >= WARM_PALE[0]) & (sat >= WARM_PALE[1])))


def _morph(mask, op, size):
    import numpy as np
    from PIL import Image, ImageFilter

    f = ImageFilter.MaxFilter(size) if op == "dilate" else ImageFilter.MinFilter(size)
    return np.asarray(Image.fromarray(mask.astype(np.uint8) * 255).filter(f)) > 127


def _components(mask):
    """4-connected components of a boolean picture -> list of (ys, xs) arrays."""
    import numpy as np

    H, W = mask.shape
    seen = np.zeros((H, W), bool)
    out = []
    for y0, x0 in zip(*np.nonzero(mask)):
        if seen[y0, x0]:
            continue
        seen[y0, x0] = True
        stack, ys, xs = [(y0, x0)], [], []
        while stack:
            y, x = stack.pop()
            ys.append(y)
            xs.append(x)
            for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= yy < H and 0 <= xx < W and mask[yy, xx] and not seen[yy, xx]:
                    seen[yy, xx] = True
                    stack.append((yy, xx))
        out.append((np.array(ys), np.array(xs)))
    return out


def missed_windows(rgb, hsv, named, lit_windows):
    """Warm, bright, window-shaped regions the finder did not name. `named` is the boolean picture of every named mask,
    `lit_windows` the masks of the named windows that glow. With fewer than CATCH_MIN_REF of them nothing is learned from and
    nothing is caught. A region counts when it is as big as a lit window (CATCH_AREA), fills its bounding box, is neither
    tall and thin nor wide and flat, has the lit windows' colour (CATCH_HUE, CATCH_SV) and passes the same lit-share test as
    a named window. -> list of (box, mask, lit_share)."""
    import numpy as np

    if len(lit_windows) < CATCH_MIN_REF:
        return []
    v = rgb.max(-1)
    hue = hsv[..., 0] * (360.0 / 255.0)
    sat = hsv[..., 1] / 255.0
    ref_area = float(np.median([m.sum() for m in lit_windows]))
    ref = np.concatenate([np.stack([hue[m], sat[m], v[m]], 1) for m in lit_windows])
    rh, rs, rv = (float(x) for x in np.median(ref, axis=0))
    cand = _morph(warm_pixels(rgb, hsv) & ~_morph(named, "dilate", 9), "erode", 3)
    cand = _morph(_morph(_morph(cand, "dilate", 3), "dilate", 5), "erode", 5) & ~_morph(named, "dilate", 9)   # open, then close: bars between panes join
    found = []
    for ys, xs in _components(cand):
        area = len(ys)
        if not (CATCH_AREA[0] * ref_area <= area <= CATCH_AREA[1] * ref_area):
            continue
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        bw, bh = x1 - x0, y1 - y0
        if area / float(bw * bh) < CATCH_FILL or not (CATCH_ASPECT[0] <= bw / bh <= CATCH_ASPECT[1]):
            continue
        h, s, vv = float(np.median(hue[ys, xs])), float(np.median(sat[ys, xs])), float(np.median(v[ys, xs]))
        if abs(h - rh) > CATCH_HUE or abs(s - rs) > CATCH_SV or abs(vv - rv) > CATCH_SV:
            continue
        m = np.zeros(named.shape, bool)
        m[ys, xs] = True
        box = (float(x0), float(y0), float(x1), float(y1))
        share = window_lit_share(rgb, hsv, box, m)
        if share >= WINDOW_LIT_SHARE:
            found.append((box, m, share))
    return found


def window_lit_share(rgb, hsv, box, mask) -> float:
    """The share of a named window's mask that is lit (lit_pixels)."""
    import numpy as np

    return float((lit_pixels(rgb, hsv, box, mask) & mask).sum()) / max(int(mask.sum()), 1)


def run_glow(work: str, count: int, models_dir: str) -> None:
    import numpy as np
    from PIL import Image

    models = load_models(models_dir)
    report = {}
    for i in range(count):
        img = Image.open(os.path.join(work, f"view_{i}.png")).convert("RGB")
        W, H = img.size
        dets = []
        for phrase, kind in GLOW_PHRASES.items():
            for sc, b in detect(models, img, phrase):
                if sc >= GLOW_MIN_SCORE[kind] and (b[2] - b[0]) * (b[3] - b[1]) < MAX_BOX_SHARE * W * H:
                    dets.append((kind, sc, b))
        kept = []
        for kind, sc, b in sorted(dets, key=lambda d: -d[1]):
            if all(iou(b, k[2]) < 0.8 for k in kept):
                kept.append((kind, sc, b))
        rgb = np.asarray(img, dtype=np.float32) / 255.0
        v = rgb.max(-1)
        hsv = np.asarray(img.convert("HSV"))
        gain = np.zeros((H, W), np.float32)
        shares = []
        named = np.zeros((H, W), bool)
        lit_windows = []
        for kind, sc, b in kept:
            share = None
            m = segment(models, img, b)
            if kind == "figure":
                g = m.astype(np.float32)
            else:   # panel at PANEL_GAIN, its brightest pixels (letters, lit panes) up to 1
                base = float(np.median(v[m])) if m.any() else 0.0
                g = m * (PANEL_GAIN + (1 - PANEL_GAIN) * np.clip((v - base - 0.03) / 0.12, 0, 1))
                if kind == "window":   # decided per window: lit enough as a whole, or dark glass
                    share = window_lit_share(rgb, hsv, b, m)
                    if share < WINDOW_LIT_SHARE:
                        g = g * 0
            gain = np.maximum(gain, g.astype(np.float32))
            named |= m
            if kind == "window" and share >= WINDOW_LIT_SHARE:
                lit_windows.append(m)
            shares.append(None if share is None else round(share, 3))
        np.save(os.path.join(work, f"view_{i}_gain_named.npy"), gain)   # before the missed-window pass (comparison only)
        caught = missed_windows(rgb, hsv, named, lit_windows)
        for box, m, share in caught:   # a caught window glows like a named one: panel gain, its brightest pixels up to 1
            base = float(np.median(v[m]))
            gain = np.maximum(gain, (m * (PANEL_GAIN + (1 - PANEL_GAIN) * np.clip((v - base - 0.03) / 0.12, 0, 1))).astype(np.float32))
        np.save(os.path.join(work, f"view_{i}_gain.npy"), gain)
        report[i] = ([{"kind": k, "score": round(s, 3), "box": [round(x) for x in b], **({"lit_share": sh} if sh is not None else {})}
                      for (k, s, b), sh in zip(kept, shares)]
                     + [{"kind": "caught window", "box": [round(x) for x in box], "lit_share": round(sh, 3)} for box, _, sh in caught])
    with open(os.path.join(work, "glow_report.json"), "w") as fh:
        json.dump(report, fh)


# ---- sign ------------------------------------------------------------------------------------------------------------

def run_sign(work: str, count: int, models_dir: str, panels_from: int) -> None:
    import numpy as np
    from PIL import Image

    models = load_models(models_dir)

    def share(b, img):
        return (b[2] - b[0]) * (b[3] - b[1]) / float(img.size[0] * img.size[1])

    report, panel_report = {}, {}
    for i in range(count):
        img = Image.open(os.path.join(work, f"view_{i}.png")).convert("RGB")
        W, H = img.size
        found = {phrase: detect(models, img, phrase) for phrase in SIGN_PHRASES}
        boxes = [(sc, b) for phrase in SIGN_PHRASES for sc, b in found[phrase] if sc >= SIGN_MIN_SCORE and share(b, img) < MAX_BOX_SHARE]
        mask = np.zeros((H, W), bool)
        kept = []
        for sc, b in sorted(boxes, key=lambda t: -t[0]):
            if any(abs(b[0] - k[0]) + abs(b[1] - k[1]) + abs(b[2] - k[2]) + abs(b[3] - k[3]) < 0.04 * (W + H) for _, k in kept):
                continue   # the same board found by both phrases
            kept.append((sc, b))
            mask |= segment(models, img, b)
        np.save(os.path.join(work, f"view_{i}_sign.npy"), mask)
        report[i] = [{"score": round(s, 3), "box": [round(x) for x in b]} for s, b in kept]
        if i < panels_from:
            continue
        dets = []
        for phrase, (kind, least) in PANELS.items():
            dets += [(sc, kind, b) for sc, b in (found[phrase] if phrase in found else detect(models, img, phrase))
                     if sc >= least and share(b, img) < MAX_PANEL_SHARE]
        panels = []
        for sc, kind, b in sorted(dets, key=lambda d: -d[0]):
            if all(iou(b, k[2]) < 0.8 for k in panels):
                panels.append((sc, kind, b))
        masks = np.array([segment(models, img, b) for _, _, b in panels], bool).reshape(len(panels), H, W)
        np.save(os.path.join(work, f"view_{i}_panels.npy"), masks)
        panel_report[i] = [{"kind": k, "score": round(s, 3), "box": [round(x) for x in b]} for s, k, b in panels]
    with open(os.path.join(work, "sign_report.json"), "w") as fh:
        json.dump(report, fh)
    with open(os.path.join(work, "panel_report.json"), "w") as fh:
        json.dump(panel_report, fh)


# ---- hair (the worn item, or the phrase the caller names) -----------------------------------------------------------

def run_hair(views_dir: str, phrase: str, models_dir: str, views: list[str]) -> None:
    import numpy as np
    from PIL import Image

    models = load_models(models_dir)
    text = phrase.strip().rstrip(".")
    detections = {}
    for v in views:
        img = Image.open(os.path.join(views_dir, f"{v}_color.png")).convert("RGB")
        W, H = img.size
        keep = [(s, b) for s, b in detect(models, img, text) if (b[2] - b[0]) * (b[3] - b[1]) < HAIR_MAX_BOX_SHARE * W * H]
        if not keep:
            detections[v] = {"box": None, "score": 0.0}
            np.save(os.path.join(views_dir, f"{v}_mask.npy"), np.zeros((H, W), dtype=bool))
            continue
        score, box = max(keep, key=lambda t: t[0])
        mask = segment(models, img, box)
        np.save(os.path.join(views_dir, f"{v}_mask.npy"), mask)
        detections[v] = {"box": [round(x, 1) for x in box], "score": round(score, 3), "pixels": int(mask.sum())}
    with open(os.path.join(views_dir, "detection.json"), "w", encoding="utf-8") as fh:
        json.dump({"prompt": text + ".", "views": detections}, fh, indent=1)


def main() -> int:
    mode, args = sys.argv[1], sys.argv[2:]
    if mode == "glow":
        run_glow(args[0], int(args[1]), args[2])
    elif mode == "sign":
        run_sign(args[0], int(args[1]), args[2], int(args[3]) if len(args) > 3 else int(args[1]))
    elif mode == "hair":
        views = [v for v in args[3].split(",") if v] if len(args) > 3 else VIEWS
        run_hair(args[0], args[1], args[2], views)
    else:
        raise ValueError(f"unknown mode {mode!r}: glow, sign or hair")
    print("FINDER_OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 - the backend reads the reason from stderr
        sys.stderr.write(f"{type(e).__name__}: {e}\n")
        sys.exit(2)
