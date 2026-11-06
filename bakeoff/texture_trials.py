"""Texture research step 1 (docs/PHOTOREAL-TEXTURE-RESEARCH.md section 4): does a finer TRELLIS colour grid help?

Builds the bake-off subjects with the app's own graph (vgac.graphs.trellis.convert_graph, the app's unwrap padding)
at colour grid 1024 and 1536, same seed, on instance A. Only bake-off inputs; never library/projects.

Usage: backend/.venv/Scripts/python.exe bakeoff/texture_trials.py [subject ...]
Output: bakeoff/out/texture_trials/<subject>_u<upsample>_t<tex>/{model.glb, base_color.png, ...} and timings.tsv
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
from comfy import A, download, files_of, images_of, run, stage_input  # noqa: E402

from vgac.graphs.trellis import IMAGE_NODES, convert_graph  # noqa: E402

OUT = os.path.join(HERE, "out", "texture_trials")
INPUTS = {
    "knight": os.path.join(HERE, "out", "convert", "humanoid_20000", "input.png"),
    "wolf": os.path.join(HERE, "out", "convert", "quadruped_20000", "input.png"),
    "chest": os.path.join(HERE, "out", "convert", "prop_5000", "input.png"),
}
# (upsample, texture) per subject: every subject at 2048 with both grids; the knight also at 4096.
TRIALS = {
    "knight": [(1024, 2048), (1536, 2048), (1024, 4096), (1536, 4096)],
    "wolf": [(1024, 2048), (1536, 2048)],
    "chest": [(1024, 2048), (1536, 2048)],
}
FACES = 20000
SEED = 42


def to_rgba(path: str) -> str:
    """A figure on a near-black background as RGBA: alpha is everything brighter than 10 of 255, holes filled, so the
    builder crops to the figure (an edit model's "black" is 1 or 2, not 0, and the builder then builds the whole
    square). Returns the path (written in place)."""
    import numpy as np
    from PIL import Image
    from vgac.face import fill_holes
    rgb = np.asarray(Image.open(path).convert("RGB"))
    m = fill_holes(rgb.max(axis=2) > 10)
    Image.fromarray(np.dstack([rgb * m[..., None], (m * 255).astype(np.uint8)]).astype(np.uint8), "RGBA").save(path)
    return path


def padding(tex: int) -> int:
    """The app's unwrap padding (stages.mesh.resolve_padding with no override)."""
    return min(16, 2 * max(4, tex // 256))


def build(subject: str, ups: int, tex: int) -> None:
    name = f"{subject}_u{ups}_t{tex}"
    d = os.path.join(OUT, name)
    if os.path.isfile(os.path.join(d, "model.glb")):
        print(f"{name}: already built", flush=True)
        return
    os.makedirs(d, exist_ok=True)
    staged = stage_input(INPUTS[subject], f"bakeoff_tt_{subject}_in.png")
    graph = convert_graph(staged, f"bakeoff/texture_trials/{name}", FACES, tex, ups, SEED, True, padding(tex))
    t0 = time.time()
    outputs, secs = run(A, graph, poll=5.0)
    for item in files_of(outputs, "3d") + files_of(outputs, "result") + files_of(outputs, "files"):
        if isinstance(item, str):
            item = {"filename": os.path.basename(item), "subfolder": os.path.dirname(item), "type": "output"}
        download(A, item, os.path.join(d, "model.glb"))
    for node, key in IMAGE_NODES.items():
        imgs = (outputs.get(node) or {}).get("images") or []
        if imgs:
            download(A, imgs[0], os.path.join(d, f"{key}.png"))
    with open(os.path.join(OUT, "timings.tsv"), "a", encoding="utf-8") as f:
        f.write(f"{name}\t{secs}\t{round(time.time() - t0, 1)}\n")
    print(f"{name}: {secs} s", flush=True)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    subjects = sys.argv[1:] or list(TRIALS)
    for s in subjects:
        for ups, tex in TRIALS[s]:
            try:
                build(s, ups, tex)
            except Exception as e:  # noqa: BLE001  (one failed build should not stop the rest)
                print(f"{s}_u{ups}_t{tex}: FAILED {e}", flush=True)
    print("ALL DONE", flush=True)
