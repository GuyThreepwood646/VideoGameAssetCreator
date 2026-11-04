"""Phase 0 bake-off, step 3: mesh generation on instance A (RTX 3060).

Two engines:
  trellis   TRELLIS.2 from ONE RGBA cutout (the graph from the comfyui-game-art skill, image and prefix swapped).
  hy3d      Hunyuan3D-2mv turbo from FOUR views (front, left, back, right) composited over white.

Usage:
  python mesh.py trellis <subject> <rgba.png>
  python mesh.py hy3d <subject> <dir with front_white.png left_white.png back_white.png right_white.png> [steps] [cfg]
Output: bakeoff/out/mesh/<subject>/<engine>.glb plus a timing line appended to bakeoff/out/mesh/timings.txt
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from comfy import A, BS, run, download, files_of, stage_input

OUT = os.path.join(os.path.dirname(__file__), "out", "mesh")
SKILL_WF = r"C:\Users\evanr\.claude\skills\comfyui-game-art\workflows\trellis2_image_to_mesh_api.json"


def trellis_graph(image_name, prefix, seed=42):
    g = json.load(open(SKILL_WF, encoding="utf-8"))
    g["img"]["inputs"]["image"] = image_name
    g["glb"]["inputs"]["filename_prefix"] = "bakeoff/mesh/" + prefix
    g["saveimg_in"]["inputs"]["filename_prefix"] = "bakeoff/mesh/" + prefix + "_input"
    for k in ("k1", "k2", "k3"):
        g[k]["inputs"]["seed"] = seed
    return g


def hy3d_graph(names, prefix, steps=20, cfg=7.5, seed=42, ckpt="3D Rendering" + BS + "Hunyuan3D-2mv.fp16.safetensors"):
    # Mirrors the bundled 3d_hunyuan3d_multiview_to_model template (non-turbo): 20 steps, CFG 7.5, no FluxGuidance.
    # The mv-turbo .ckpt on disk is not loadable by ImageOnlyCheckpointLoader (load_diffusion_model_state_dict fails).
    g = {
        "ck": {"class_type": "ImageOnlyCheckpointLoader", "inputs": {"ckpt_name": ckpt}},
        "ms": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["ck", 0], "shift": 1.0}},
        "cond": {"class_type": "Hunyuan3Dv2ConditioningMultiView", "inputs": {}},
        "lat": {"class_type": "EmptyLatentHunyuan3Dv2", "inputs": {"resolution": 3072, "batch_size": 1}},
        "ks": {"class_type": "KSampler", "inputs": {"model": ["ms", 0], "positive": ["cond", 0], "negative": ["cond", 1], "latent_image": ["lat", 0],
                                                   "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0}},
        "dec": {"class_type": "VAEDecodeHunyuan3D", "inputs": {"samples": ["ks", 0], "vae": ["ck", 2], "num_chunks": 8000, "octree_resolution": 256}},
        "mesh": {"class_type": "VoxelToMesh", "inputs": {"voxel": ["dec", 0], "algorithm": "surface net", "threshold": 0.6}},
        "glb": {"class_type": "SaveGLB", "inputs": {"mesh": ["mesh", 0], "filename_prefix": "bakeoff/mesh/" + prefix}},
    }
    for view, name in names.items():
        g["img_" + view] = {"class_type": "LoadImage", "inputs": {"image": name}}
        g["enc_" + view] = {"class_type": "CLIPVisionEncode", "inputs": {"clip_vision": ["ck", 1], "image": ["img_" + view, 0], "crop": "none"}}
        g["cond"]["inputs"][view] = ["enc_" + view, 0]
    return g


def save_result(outputs, subject, engine, secs, note=""):
    os.makedirs(os.path.join(OUT, subject), exist_ok=True)
    items = files_of(outputs, "3d") or files_of(outputs, "result") or files_of(outputs, "files")
    if not items:
        # SaveGLB may report under another key; dump everything so the key can be found.
        print(json.dumps(outputs)[:2000])
        raise SystemExit("no 3d output found")
    item = items[0]
    if isinstance(item, str):
        item = {"filename": os.path.basename(item), "subfolder": os.path.dirname(item), "type": "output"}
    dest = os.path.join(OUT, subject, f"{engine}.glb")
    download(A, item, dest)
    line = f"{subject}\t{engine}\t{secs}s\t{os.path.getsize(dest)} bytes\t{note}"
    with open(os.path.join(OUT, "timings.txt"), "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)
    return dest


if __name__ == "__main__":
    engine, subject = sys.argv[1], sys.argv[2]
    if engine == "trellis":
        name = stage_input(sys.argv[3], f"bakeoff_{subject}_trellis_in.png")
        outputs, secs = run(A, trellis_graph(name, f"{subject}_trellis"), poll=5.0)
        save_result(outputs, subject, "trellis", secs, "single RGBA input")
    elif engine == "hy3d":
        d = sys.argv[3]
        steps = int(sys.argv[4]) if len(sys.argv) > 4 else 20
        cfg = float(sys.argv[5]) if len(sys.argv) > 5 else 7.5
        names = {v: stage_input(os.path.join(d, f"{v}_white.png"), f"bakeoff_{subject}_hy_{v}.png") for v in ("front", "left", "back", "right")}
        outputs, secs = run(A, hy3d_graph(names, f"{subject}_hy3d", steps, cfg), poll=5.0)
        save_result(outputs, subject, "hy3d", secs, f"2mv steps={steps} cfg={cfg}")
    else:
        raise SystemExit("engine must be trellis or hy3d")
