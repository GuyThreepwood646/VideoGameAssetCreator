"""Phase 0 bake-off, step 4: auto-rig a mesh with SkinTokens on instance B (RTX 3080, restarted with SkinToken loaded).

Usage: python rig.py <subject> <engine> [target_faces]
Reads  out/mesh/<subject>/<engine>.glb, decimates to target_faces (default 150000) through VGACLoadTrimesh,
runs SkinToken Rig, copies the rigged GLB to out/rig/<subject>_<engine>.glb and appends a timing row.
"""
import glob
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from comfy import B, run, stage_input

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out", "rig")
from local_paths import comfy_b_output  # noqa: E402

COMFY_B_OUTPUT = comfy_b_output() or ""  # VGAC_COMFY_B_OUT, else instances.b.output_dir in the app's settings


def rig_graph(mesh_name, prefix, target_faces, ckpt="experiments/articulation_xl_quantization_256_token_4/grpo_1400.ckpt", template="Keep model names"):
    return {
        "load": {"class_type": "VGACLoadTrimesh", "inputs": {"path": mesh_name, "target_faces": target_faces, "merge_vertices": True}},
        "rig": {"class_type": "SkinTokenRigTrimesh", "inputs": {
            "trimesh": ["load", 0], "ckpt_name": ckpt, "device": "auto", "save_file": True,
            "filename_prefix": "bakeoff/rig/" + prefix, "file_format": "glb", "use_transfer": False,
            "use_postprocess": False, "group_per_vertex": 4, "bottom_center_origin": True, "smooth_angle": 55.0,
            "skeleton_template": template, "top_k": 5, "top_p": 0.95, "temperature": 1.0,
            "repetition_penalty": 2.0, "num_beams": 10}},
        "show": {"class_type": "PreviewAny", "inputs": {"source": ["rig", 1]}},
        "faces": {"class_type": "PreviewAny", "inputs": {"source": ["load", 2]}},
    }


if __name__ == "__main__":
    subject, engine = sys.argv[1], sys.argv[2]
    target = int(sys.argv[3]) if len(sys.argv) > 3 else 150000
    template = sys.argv[4] if len(sys.argv) > 4 else "Keep model names"
    os.makedirs(OUT, exist_ok=True)
    src = engine if engine.lower().endswith(".glb") else os.path.join(HERE, "out", "mesh", subject, f"{engine}.glb")
    engine = os.path.splitext(os.path.basename(src))[0] if src == engine else engine
    engine = subject + "_" + engine if engine == "model" else engine
    name = stage_input(src, f"bakeoff_{subject}_{engine}.glb")
    t0 = time.time()
    outputs, secs = run(B, rig_graph(name, f"{subject}_{engine}", target, template=template), poll=5.0)
    text = str(outputs)
    rigged = sorted(glob.glob(os.path.join(COMFY_B_OUTPUT, "bakeoff", "rig", f"{subject}_{engine}*.glb")), key=os.path.getmtime)
    dest = os.path.join(OUT, f"{subject}_{engine}.glb")
    if rigged:
        shutil.copy(rigged[-1], dest)
    line = f"{subject}\t{engine}\t{secs}s\t{os.path.getsize(dest) if rigged else 0} bytes\tfaces={target}\t{text[:300]}"
    with open(os.path.join(OUT, "timings.txt"), "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)
