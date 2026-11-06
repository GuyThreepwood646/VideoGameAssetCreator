"""Left and right views of the bald bake-off knight with step 2's own graph and wording (angle_graph, view_prompt),
for the face reshape prototype: from the bald full figure (what step 2 makes) and from the bald head-and-shoulders crop
(more pixels across the face). Instance B. Output: bakeoff/out/texture_trials/sides/<source>_<view>.png
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
from comfy import B, download, images_of, run, stage_input  # noqa: E402

from vgac.graphs.qwen_edit import angle_graph, view_prompt  # noqa: E402

ROOT = os.path.join(HERE, "out", "texture_trials")
SOURCES = {"body": os.path.join(ROOT, "knightbald_input.png"), "head": os.path.join(ROOT, "knightbaldhead_input.png")}

if __name__ == "__main__":
    out = os.path.join(ROOT, "sides")
    os.makedirs(out, exist_ok=True)
    for src, path in SOURCES.items():
        name = stage_input(path, f"bakeoff_sides_{src}.png")
        for view in ("left", "right"):
            dest = os.path.join(out, f"{src}_{view}.png")
            if os.path.isfile(dest):
                continue
            outputs, secs = run(B, angle_graph(name, view_prompt(view), 3, f"bakeoff/sides/{src}_{view}"), poll=1.0)
            download(B, images_of(outputs)[0], dest)
            print(f"{src} {view}: {secs} s", flush=True)
    print("DONE", flush=True)
