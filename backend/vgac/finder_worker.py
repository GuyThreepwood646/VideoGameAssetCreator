"""Worker for the suggested worn item (WP-69): runs in the finder's own Python environment, never in the backend venv.

For each rendered view (`<view>_color.png`): Grounding DINO (tiny, Apache-2.0) finds the best box for the phrase, then
SAM 2.1 (hiera small, Apache-2.0) makes the mask from that box. Writes `<view>_mask.npy` (bool) and detection.json.
The vote over polygons is done by the backend (item_suggest.py), which holds the id maps.

Run: <finder python> finder_worker.py <views_dir> "<phrase>" <models_dir> [name,name,...]
The optional names list replaces the worn-item views (the hair outline, hair_outline.py, names the turnaround pictures).
Models: Hugging Face cache under <models_dir> (HF_HOME is set here, before transformers is imported).
Prints FINDER_OK on success; a failure exits non-zero with the reason on stderr.
"""
import json
import os
import sys

VIEWS = ["back", "left", "right", "back_left", "back_right", "top_back"]
GDINO = "IDEA-Research/grounding-dino-tiny"
SAM2 = "facebook/sam2.1-hiera-small"
BOX_THRESHOLD = 0.25
TEXT_THRESHOLD = 0.2
MAX_BOX_SHARE = 0.4  # a box over this share of the picture is the whole figure, not the item


def main() -> int:
    views_dir, phrase, models_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    views = [v for v in sys.argv[4].split(",") if v] if len(sys.argv) > 4 else VIEWS
    os.environ["HF_HOME"] = models_dir
    import numpy as np
    import torch
    from PIL import Image
    from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor, Sam2Model, Sam2Processor

    torch.set_num_threads(max(1, min(4, os.cpu_count() or 2)))
    gproc = AutoProcessor.from_pretrained(GDINO, local_files_only=True)
    gmodel = AutoModelForZeroShotObjectDetection.from_pretrained(GDINO, local_files_only=True).eval()
    sproc = Sam2Processor.from_pretrained(SAM2, local_files_only=True)
    smodel = Sam2Model.from_pretrained(SAM2, local_files_only=True).eval()
    prompt = phrase.strip().rstrip(".") + "."
    detections = {}
    for v in views:
        img = Image.open(os.path.join(views_dir, f"{v}_color.png")).convert("RGB")
        W, H = img.size
        inputs = gproc(images=img, text=prompt, return_tensors="pt")
        with torch.no_grad():
            out = gmodel(**inputs)
        res = gproc.post_process_grounded_object_detection(
            out, inputs.input_ids, threshold=BOX_THRESHOLD, text_threshold=TEXT_THRESHOLD, target_sizes=[(H, W)])[0]
        keep = [(float(s), [float(x) for x in b]) for s, b in zip(res["scores"].tolist(), res["boxes"].tolist())
                if (b[2] - b[0]) * (b[3] - b[1]) < MAX_BOX_SHARE * W * H]
        if not keep:
            detections[v] = {"box": None, "score": 0.0}
            np.save(os.path.join(views_dir, f"{v}_mask.npy"), np.zeros((H, W), dtype=bool))
            continue
        score, box = max(keep, key=lambda t: t[0])
        sin = sproc(images=img, input_boxes=[[box]], return_tensors="pt")
        with torch.no_grad():
            sout = smodel(**sin, multimask_output=False)
        masks = sproc.post_process_masks(sout.pred_masks, sin["original_sizes"])[0]
        m = masks[0]
        if m.ndim == 3:
            m = m[0]
        mask = m.numpy().astype(bool)
        np.save(os.path.join(views_dir, f"{v}_mask.npy"), mask)
        detections[v] = {"box": [round(x, 1) for x in box], "score": round(score, 3), "pixels": int(mask.sum())}
    with open(os.path.join(views_dir, "detection.json"), "w", encoding="utf-8") as fh:
        json.dump({"prompt": prompt, "views": detections}, fh, indent=1)
    print("FINDER_OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 - the backend reads the reason from stderr
        sys.stderr.write(f"{type(e).__name__}: {e}\n")
        sys.exit(2)
