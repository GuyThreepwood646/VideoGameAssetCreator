"""The bake-off knight made bald with the app's own wording (stages/turnaround.bald_prompt) on instance B, then built
with the texture trials' graph at colour grid 1024 and texture 4096 on instance A, so the face close-up trial can run on
a head with no modelled hair (the app's "hair from the library" route builds the model bald the same way).

Usage: backend/.venv/Scripts/python.exe bakeoff/bald_knight.py
Output: bakeoff/out/texture_trials/knightbald_input.png and knightbald_u1024_t4096/
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
from comfy import B, download, images_of, run, stage_input  # noqa: E402

import texture_trials as tt  # noqa: E402
from vgac.graphs.qwen_edit import edit_graph  # noqa: E402
from vgac.stages.turnaround import bald_prompt  # noqa: E402

SRC = os.path.join(HERE, "out", "convert", "humanoid_20000", "input.png")
DEST = os.path.join(tt.OUT, "knightbald_input.png")

if __name__ == "__main__":
    os.makedirs(tt.OUT, exist_ok=True)
    if not os.path.isfile(DEST):
        name = stage_input(SRC, "bakeoff_bald_knight_in.png")
        outputs, secs = run(B, edit_graph(name, bald_prompt("a female knight in silver plate armour"), 7, "bakeoff/bald_knight", 1024, 1024), poll=1.0)
        download(B, images_of(outputs)[0], DEST)
        print(f"bald picture: {secs} s", flush=True)
    tt.to_rgba(DEST)
    tt.INPUTS["knightbald"] = DEST
    tt.build("knightbald", 1024, 4096)
    print("DONE", flush=True)
