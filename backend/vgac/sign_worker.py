"""Worker for the sign keep (PL-123): runs in the item finder's own Python environment (item_suggest.py, Grounding DINO tiny and
SAM 2.1 small, both Apache-2.0), never in the backend venv.

For each picture `<dir>/view_<i>.png` it asks the detector for lettered signs (shop sign, signboard), makes a SAM mask for each
box and writes `<dir>/view_<i>_sign.npy` (bool, H x W: the sign boards) and `<dir>/sign_report.json`. For the pictures from
`<panels_from>` on, it also names the flat panels one by one (signs, neon figures, shelves, machines): their masks go to
`<dir>/view_<i>_panels.npy` (bool, N x H x W) and their kinds and boxes to `<dir>/panel_report.json`.

Run: <finder python> sign_worker.py <dir> <count> <models_dir> [<panels_from>]      Prints SIGN_OK on success.
"""
import json
import os
import sys

PHRASES = ("shop sign", "signboard")
MIN_SCORE = 0.3
MAX_BOX_SHARE = 0.35   # a box over this share of the picture is the whole building
PANELS = {"shop sign": ("sign", 0.35), "signboard": ("sign", 0.35), "neon sign": ("sign", 0.35),
          "neon figure": ("figure", 0.35), "shelf of bottles": ("display", 0.3), "vending machine": ("case", 0.4)}
MAX_PANEL_SHARE = 0.1  # one panel, not a wall or the ground
THREADS = 2
GDINO = "IDEA-Research/grounding-dino-tiny"
SAM2 = "facebook/sam2.1-hiera-small"


def overlap(a, b):
    """Intersection over union of two boxes."""
    inter = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return inter / max((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter, 1e-6)


def main() -> int:
    work, count, models = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    panels_from = int(sys.argv[4]) if len(sys.argv) > 4 else count
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

    def detect(img, phrase):
        inp = gp(images=img, text=phrase + ".", return_tensors="pt")
        with torch.no_grad():
            out = gm(**inp)
        res = gp.post_process_grounded_object_detection(out, inp.input_ids, threshold=0.25, text_threshold=0.2,
                                                        target_sizes=[(img.size[1], img.size[0])])[0]
        return list(zip(res["scores"].tolist(), res["boxes"].tolist()))

    def segment(img, b):
        si = sp(images=img, input_boxes=[[b]], return_tensors="pt")
        with torch.no_grad():
            so = sm(**si, multimask_output=False)
        m = sp.post_process_masks(so.pred_masks, si["original_sizes"])[0][0]
        if m.ndim == 3:
            m = m[0]
        return m.numpy().astype(bool)

    def share(b, img):
        return (b[2] - b[0]) * (b[3] - b[1]) / float(img.size[0] * img.size[1])

    report, panel_report = {}, {}
    for i in range(count):
        img = Image.open(os.path.join(work, f"view_{i}.png")).convert("RGB")
        W, H = img.size
        found = {phrase: detect(img, phrase) for phrase in PHRASES}
        boxes = [(sc, b) for phrase in PHRASES for sc, b in found[phrase] if sc >= MIN_SCORE and share(b, img) < MAX_BOX_SHARE]
        mask = np.zeros((H, W), bool)
        kept = []
        for sc, b in sorted(boxes, key=lambda t: -t[0]):
            if any(abs(b[0] - k[0]) + abs(b[1] - k[1]) + abs(b[2] - k[2]) + abs(b[3] - k[3]) < 0.04 * (W + H) for _, k in kept):
                continue   # the same board found by both phrases
            kept.append((sc, b))
            mask |= segment(img, b)
        np.save(os.path.join(work, f"view_{i}_sign.npy"), mask)
        report[i] = [{"score": round(s, 3), "box": [round(x) for x in b]} for s, b in kept]
        if i < panels_from:
            continue
        dets = []
        for phrase, (kind, least) in PANELS.items():
            dets += [(sc, kind, b) for sc, b in (found[phrase] if phrase in found else detect(img, phrase))
                     if sc >= least and share(b, img) < MAX_PANEL_SHARE]
        panels = []
        for sc, kind, b in sorted(dets, key=lambda d: -d[0]):
            if all(overlap(b, k[2]) < 0.8 for k in panels):
                panels.append((sc, kind, b))
        masks = np.array([segment(img, b) for _, _, b in panels], bool).reshape(len(panels), H, W)
        np.save(os.path.join(work, f"view_{i}_panels.npy"), masks)
        panel_report[i] = [{"kind": k, "score": round(s, 3), "box": [round(x) for x in b]} for s, k, b in panels]
    json.dump(report, open(os.path.join(work, "sign_report.json"), "w"))
    json.dump(panel_report, open(os.path.join(work, "panel_report.json"), "w"))
    print("SIGN_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
