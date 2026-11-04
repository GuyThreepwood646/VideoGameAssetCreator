"""ComfyUI API-format graph builders, one module per engine.

Each builder is a pure function returning the dict POSTed to /prompt. They are ports of the
bakeoff scripts (bakeoff/gen_concepts.py, turnaround.py, convert.py, mesh.py, rig.py) and of the
style-model recipes in the comfyui-game-art skill; backend/tests/test_graphs.py checks the ported
node structures against the bakeoff originals.
"""

BS = chr(92)  # backslash: ComfyUI model names on this machine use Windows subfolders
