"""Worker for the glow map (PL-98): runs in the item finder's own Python environment (item_suggest.py, Grounding DINO tiny and
SAM 2.1 small, both Apache-2.0), never in the backend venv.

For each picture `<dir>/view_<i>.png` it asks the detector for neon signs, neon figures and lit windows, makes a SAM mask for
each box, and writes `<dir>/view_<i>_gain.npy`: float32 (H, W), how much of the picture's own colour glows there. Neon
figures glow fully; signs and windows glow at PANEL_GAIN, rising to 1 for their brightest pixels (the letters). A window glows
as a whole when enough of its mask is lit (lit_pixels, WINDOW_LIT_SHARE), else not at all, so dark glass stays dark. Where the finder named at least CATCH_MIN_REF lit windows in a view, warm, bright, window-shaped regions it missed glow
too (missed_windows). Nothing else glows (boxes, awnings and plain bright paint are never named).

Run: <finder python> glow_worker.py <dir> <count> <models_dir>      Prints GLOW_OK on success.
"""
import json
import os
import sys

PHRASES = {"neon sign": "sign", "neon figure": "figure", "lit window": "window"}
MIN_SCORE = {"sign": 0.3, "figure": 0.28, "window": 0.4}
MAX_BOX_SHARE = 0.35   # a box over this share of the picture is the whole building
MAX_SIDE = 1024
THREADS = 2
PANEL_GAIN = 0.6
WINDOW_LIT_OVER = 0.12   # a window pixel is lit when this much brighter than the wall round the window (V, 0..1) ...
WARM_HUE = (12.0, 75.0)   # ... or warm (orange to yellow-green degrees), bright and saturated
WINDOW_LIT_SHARE = 0.35   # a named window glows (all of it) when at least this share of its mask is lit, else none of it
WARM_STRONG = (0.6, 0.4)   # (V, S) at least: a saturated warm pane, even a little shaded
WARM_PALE = (0.75, 0.15)   # (V, S) at least: a pale cream or yellow pane, which is bright rather than saturated
CATCH_MIN_REF = 5         # the missed-window pass runs in a view only with at least this many lit named windows to learn from
CATCH_AREA = (0.12, 3.0)   # a caught region's area against the median lit named window's
CATCH_FILL = 0.6          # share of its bounding box a window-shaped region fills
CATCH_ASPECT = (0.3, 3.5)   # width over height
CATCH_HUE, CATCH_SV = 15.0, 0.25   # colour match to the lit named windows: hue degrees, S and V distance
GDINO = "IDEA-Research/grounding-dino-tiny"
SAM2 = "facebook/sam2.1-hiera-small"


def iou(a, b):
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    return inter / max((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter, 1e-6)


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
    return float((lit_pixels(rgb, hsv, box, mask) & mask).sum()) / max(int(mask.sum()), 1)


def main() -> int:
    work, count, models = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    os.environ["HF_HOME"] = models
    os.environ["HF_HUB_OFFLINE"] = "1"
    import numpy as np
    import torch
    from PIL import Image
    from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor, Sam2Model, Sam2Processor

    torch.set_num_threads(THREADS)   # runs on the processor inside step 4: never every core
    gp = AutoProcessor.from_pretrained(GDINO, local_files_only=True)
    gm = AutoModelForZeroShotObjectDetection.from_pretrained(GDINO, local_files_only=True).eval()
    sp = Sam2Processor.from_pretrained(SAM2, local_files_only=True)
    sm = Sam2Model.from_pretrained(SAM2, local_files_only=True).eval()
    report = {}
    for i in range(count):
        img = Image.open(os.path.join(work, f"view_{i}.png")).convert("RGB")
        W, H = img.size
        dets = []
        for phrase, kind in PHRASES.items():
            inp = gp(images=img, text=phrase + ".", return_tensors="pt")
            with torch.no_grad():
                out = gm(**inp)
            res = gp.post_process_grounded_object_detection(out, inp.input_ids, threshold=0.25, text_threshold=0.2, target_sizes=[(H, W)])[0]
            for sc, b in zip(res["scores"].tolist(), res["boxes"].tolist()):
                if sc >= MIN_SCORE[kind] and (b[2] - b[0]) * (b[3] - b[1]) < MAX_BOX_SHARE * W * H:
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
            si = sp(images=img, input_boxes=[[b]], return_tensors="pt")
            with torch.no_grad():
                so = sm(**si, multimask_output=False)
            m = sp.post_process_masks(so.pred_masks, si["original_sizes"])[0][0]
            if m.ndim == 3:
                m = m[0]
            m = m.numpy().astype(bool)
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
        report[i] = [{"kind": k, "score": round(s, 3), "box": [round(x) for x in b], **({"lit_share": sh} if sh is not None else {})}
                     for (k, s, b), sh in zip(kept, shares)] +                     [{"kind": "caught window", "box": [round(x) for x in box], "lit_share": round(sh, 3)} for box, _, sh in caught]
    json.dump(report, open(os.path.join(work, "glow_report.json"), "w"))
    print("GLOW_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
