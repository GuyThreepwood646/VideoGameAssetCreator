"""Worker for the sign keep (PL-123): runs in the item finder's own Python environment (item_suggest.py, Grounding DINO tiny and
SAM 2.1 small, both Apache-2.0), never in the backend venv.

For each picture `<dir>/view_<i>.png` it asks the detector for lettered signs (shop sign, signboard), makes a SAM mask for each
box and writes `<dir>/view_<i>_sign.npy` (bool, H x W: the sign boards) and `<dir>/sign_report.json`.

Run: <finder python> sign_worker.py <dir> <count> <models_dir>      Prints SIGN_OK on success.
"""
import json
import os
import sys

PHRASES = ("shop sign", "signboard")
MIN_SCORE = 0.3
MAX_BOX_SHARE = 0.35   # a box over this share of the picture is the whole building
THREADS = 2
GDINO = "IDEA-Research/grounding-dino-tiny"
SAM2 = "facebook/sam2.1-hiera-small"


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
        boxes = []
        for phrase in PHRASES:
            inp = gp(images=img, text=phrase + ".", return_tensors="pt")
            with torch.no_grad():
                out = gm(**inp)
            res = gp.post_process_grounded_object_detection(out, inp.input_ids, threshold=0.25, text_threshold=0.2, target_sizes=[(H, W)])[0]
            for sc, b in zip(res["scores"].tolist(), res["boxes"].tolist()):
                if sc >= MIN_SCORE and (b[2] - b[0]) * (b[3] - b[1]) < MAX_BOX_SHARE * W * H:
                    boxes.append((sc, b))
        mask = np.zeros((H, W), bool)
        kept = []
        for sc, b in sorted(boxes, key=lambda t: -t[0]):
            if any(abs(b[0] - k[0]) + abs(b[1] - k[1]) + abs(b[2] - k[2]) + abs(b[3] - k[3]) < 0.04 * (W + H) for _, k in kept):
                continue   # the same board found by both phrases
            kept.append((sc, b))
            si = sp(images=img, input_boxes=[[b]], return_tensors="pt")
            with torch.no_grad():
                so = sm(**si, multimask_output=False)
            m = sp.post_process_masks(so.pred_masks, si["original_sizes"])[0][0]
            if m.ndim == 3:
                m = m[0]
            mask |= m.numpy().astype(bool)
        np.save(os.path.join(work, f"view_{i}_sign.npy"), mask)
        report[i] = [{"score": round(s, 3), "box": [round(x) for x in b]} for s, b in kept]
    json.dump(report, open(os.path.join(work, "sign_report.json"), "w"))
    print("SIGN_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
