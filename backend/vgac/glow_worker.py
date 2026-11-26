"""Worker for the glow map (PL-98): runs in the item finder's own Python environment (item_suggest.py, Grounding DINO tiny and
SAM 2.1 small, both Apache-2.0), never in the backend venv.

For each picture `<dir>/view_<i>.png` it asks the detector for neon signs, neon figures and lit windows, makes a SAM mask for
each box, and writes `<dir>/view_<i>_gain.npy`: float32 (H, W), how much of the picture's own colour glows there. Neon
figures glow fully; signs and windows glow at PANEL_GAIN, rising to 1 for their brightest pixels (the letters). A window glows
as a whole when enough of its mask is lit (lit_pixels, WINDOW_LIT_SHARE), else not at all, so dark glass stays dark. Nothing
else glows (boxes, awnings and plain bright paint are never named).

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
    hue = hsv[..., 0] * (360.0 / 255.0)
    sat = hsv[..., 1] / 255.0
    warm = (hue >= WARM_HUE[0]) & (hue <= WARM_HUE[1]) & (((v >= WARM_STRONG[0]) & (sat >= WARM_STRONG[1])) | ((v >= WARM_PALE[0]) & (sat >= WARM_PALE[1])))
    return (v > wall + WINDOW_LIT_OVER) | warm


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
            shares.append(None if share is None else round(share, 3))
        np.save(os.path.join(work, f"view_{i}_gain.npy"), gain)
        report[i] = [{"kind": k, "score": round(s, 3), "box": [round(x) for x in b], **({"lit_share": sh} if sh is not None else {})}
                     for (k, s, b), sh in zip(kept, shares)]
    json.dump(report, open(os.path.join(work, "glow_report.json"), "w"))
    print("GLOW_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
