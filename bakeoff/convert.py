"""Phase 0.5: image -> game-ready textured low-poly GLB entirely inside ComfyUI (instance A, RTX 3060).

Mirrors the bundled 3d_pixal3d_trellis2_image_to_model template: TRELLIS.2 shape + texture, then the native
post chain (remesh to a watertight solid, decimate to the budget, smooth normals, unwrap, bake PBR from the
texture voxel, bake normal and AO from the dense mesh, apply, save GLB).

Usage: python convert.py <subject> <rgba.png> [target_faces=20000] [tex=2048] [upsample=1024] [--no-remesh]
Output: bakeoff/out/convert/<subject>_<faces>/{model.glb, base_color.png, metallic.png, roughness.png, normal.png, ao.png}
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from comfy import A, run, download, files_of, images_of, stage_input

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "convert")


def convert_graph(image_name, prefix, target_faces=20000, tex=2048, upsample=1024, seed=42, remesh=True, tex_steps=24, segmenter="pec"):
    g = {
        "img": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "inv": {"class_type": "InvertMask", "inputs": {"mask": ["img", 1]}},
        "crop": {"class_type": "ImageCropToMask", "inputs": {"images": ["img", 0], "masks": ["inv", 0], "width": 1024, "height": 1024, "pad_factor": 1.1, "grow_mask": 0, "background": "#000000"}},
        "cv": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": "dino_v3_vit_l.safetensors"}},
        "cond": {"class_type": "Trellis2Conditioning", "inputs": {"clip_vision_model": ["cv", 0], "image": ["crop", 0]}},
        "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": "trellis_2_int8_convrot.safetensors", "weight_dtype": "default"}},
        "svae": {"class_type": "VAELoader", "inputs": {"vae_name": "trellis_2_shape_vae_bf16.safetensors"}},
        "tvae": {"class_type": "VAELoader", "inputs": {"vae_name": "trellis_2_texture_vae_bf16.safetensors"}},
        "co1": {"class_type": "CFGOverride", "inputs": {"model": ["unet", 0], "cfg": 1.0, "start_percent": 0.667, "end_percent": 1.0}},
        "rc1": {"class_type": "RescaleCFG", "inputs": {"model": ["co1", 0], "multiplier": 0.7}},
        "ms1": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["rc1", 0], "shift": 5.0}},
        "co2": {"class_type": "CFGOverride", "inputs": {"model": ["unet", 0], "cfg": 1.0, "start_percent": 0.769, "end_percent": 1.0}},
        "rc2": {"class_type": "RescaleCFG", "inputs": {"model": ["co2", 0], "multiplier": 0.5}},
        "el": {"class_type": "EmptyTrellis2LatentStructure", "inputs": {"batch_size": 1}},
        "k1": {"class_type": "KSampler", "inputs": {"model": ["ms1", 0], "positive": ["cond", 0], "negative": ["cond", 1], "latent_image": ["el", 0],
                                                   "seed": seed, "steps": 12, "cfg": 7.5, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0}},
        "vs": {"class_type": "VaeDecodeStructureTrellis2", "inputs": {"samples": ["k1", 0], "vae": ["svae", 0], "resolution": "32"}},
        "ss": {"class_type": "Trellis2ShapeStage", "inputs": {"positive": ["cond", 0], "negative": ["cond", 1], "voxel": ["vs", 0]}},
        "k2": {"class_type": "KSampler", "inputs": {"model": ["rc2", 0], "positive": ["ss", 0], "negative": ["ss", 1], "latent_image": ["ss", 2],
                                                   "seed": seed, "steps": 20, "cfg": 7.5, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0}},
        "upst": {"class_type": "Trellis2UpsampleStage", "inputs": {"positive": ["ss", 0], "negative": ["ss", 1], "shape_latent": ["k2", 0], "vae": ["svae", 0], "target_resolution": upsample}},
        "k3": {"class_type": "KSampler", "inputs": {"model": ["rc2", 0], "positive": ["upst", 0], "negative": ["upst", 1], "latent_image": ["upst", 2],
                                                   "seed": seed, "steps": 12, "cfg": 7.5, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "dshape": {"class_type": "VaeDecodeShapeTrellis", "inputs": {"samples": ["k3", 0], "vae": ["svae", 0]}},
        "tex": {"class_type": "Trellis2TextureStage", "inputs": {"positive": ["upst", 0], "negative": ["upst", 1], "shape_latent": ["k3", 0]}},
        "k4": {"class_type": "KSampler", "inputs": {"model": ["unet", 0], "positive": ["tex", 0], "negative": ["tex", 1], "latent_image": ["tex", 2],
                                                   "seed": seed + 1, "steps": tex_steps, "cfg": 1.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0}},
        "dtex": {"class_type": "VaeDecodeTextureTrellis", "inputs": {"samples": ["k4", 0], "vae": ["tvae", 0], "shape_subdivides": ["dshape", 1]}},
        "info": {"class_type": "GetMeshInfo", "inputs": {"mesh": ["dshape", 0]}},
    }
    high = "info"
    if remesh:
        g["remesh"] = {"class_type": "RemeshMesh", "inputs": {"mesh": ["info", 0], "resolution": 768, "sign_mode": "solid", "sign_mode.fill": 20, "sign_mode.qef": False, "band": 1.0, "project_back": 0.0,
                                                               "fix_poles": False, "smooth_iters": 20, "drop_small_components": 0.01, "precluster_max_verts": 20000000}}
        high = "remesh"
    g.update({
        "dec": {"class_type": "DecimateMesh", "inputs": {"mesh": [high, 0], "target_face_count": target_faces, "placement_mode": "midpoint"}},
        "sm": {"class_type": "MeshSmoothNormals", "inputs": {"mesh": ["dec", 0], "crease_angle": 180.0}},
        "unwrap": {"class_type": "UnwrapMesh", "inputs": {"mesh": ["sm", 0], "segmenter": segmenter, "resolution": tex, "padding": 1, "weld_distance": 0.0002}},
        "bake": {"class_type": "BakeTextureFromVoxel", "inputs": {"mesh": ["unwrap", 0], "voxel_colors": ["dtex", 0], "reference_mesh": ["dshape", 0], "texture_size": tex}},
        "nrm": {"class_type": "BakeNormalMapFromMesh", "inputs": {"low_poly": ["unwrap", 0], "high_poly": [high, 0], "resolution": tex, "cage_distance": 0.05, "ignore_backfaces": True}},
        "ao": {"class_type": "BakeAmbientOcclusion", "inputs": {"low_poly": ["unwrap", 0], "high_poly": [high, 0], "resolution": max(512, tex // 2), "samples": 64, "max_distance": 0.71, "strength": 1.0, "bias": 0.01}},
        "apply": {"class_type": "ApplyTextureToMesh", "inputs": {"mesh": ["unwrap", 0], "base_color": ["bake", 0], "metallic": ["bake", 1], "roughness": ["bake", 2], "occlusion": ["ao", 0], "normal_map": ["nrm", 0]}},
        "sm2": {"class_type": "MeshSmoothNormals", "inputs": {"mesh": ["apply", 0], "crease_angle": 180.0}},
        "glb": {"class_type": "SaveGLB", "inputs": {"mesh": ["sm2", 0], "filename_prefix": "bakeoff/convert/" + prefix + "/model"}},
        "s_base": {"class_type": "SaveImage", "inputs": {"images": ["bake", 0], "filename_prefix": "bakeoff/convert/" + prefix + "/base_color"}},
        "s_met": {"class_type": "SaveImage", "inputs": {"images": ["bake", 1], "filename_prefix": "bakeoff/convert/" + prefix + "/metallic"}},
        "s_rough": {"class_type": "SaveImage", "inputs": {"images": ["bake", 2], "filename_prefix": "bakeoff/convert/" + prefix + "/roughness"}},
        "s_nrm": {"class_type": "SaveImage", "inputs": {"images": ["nrm", 0], "filename_prefix": "bakeoff/convert/" + prefix + "/normal"}},
        "s_ao": {"class_type": "SaveImage", "inputs": {"images": ["ao", 0], "filename_prefix": "bakeoff/convert/" + prefix + "/ao"}},
        "s_in": {"class_type": "SaveImage", "inputs": {"images": ["crop", 0], "filename_prefix": "bakeoff/convert/" + prefix + "/input"}},
    })
    return g


if __name__ == "__main__":
    args = [a for i, a in enumerate(sys.argv[1:], 1) if not a.startswith("--") and sys.argv[i - 1] not in ("--tex-steps", "--segmenter")]
    subject, src = args[0], args[1]
    faces = int(args[2]) if len(args) > 2 else 20000
    tex = int(args[3]) if len(args) > 3 else 2048
    ups = int(args[4]) if len(args) > 4 else 1024
    remesh = "--no-remesh" not in sys.argv
    tex_steps = next((int(sys.argv[i + 1]) for i, a in enumerate(sys.argv) if a == "--tex-steps"), 24)  # texture sampler steps (the app default is 24)
    segmenter = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--segmenter"), "pec")  # UnwrapMesh segmenter (trial: pec or adaptive)
    prefix = f"{subject}_{faces}"
    name = stage_input(src, f"bakeoff_{subject}_convert_in.png")
    t0 = time.time()
    outputs, secs = run(A, convert_graph(name, prefix, faces, tex, ups, remesh=remesh, tex_steps=tex_steps, segmenter=segmenter), poll=5.0)
    d = os.path.join(OUT, prefix)
    os.makedirs(d, exist_ok=True)
    saved = []
    for item in files_of(outputs, "3d") + files_of(outputs, "result") + files_of(outputs, "files"):
        if isinstance(item, str):
            item = {"filename": os.path.basename(item), "subfolder": os.path.dirname(item), "type": "output"}
        saved.append(download(A, item, os.path.join(d, "model.glb")))
    for img in images_of(outputs):
        stem = img["filename"].split("_")[0]
        saved.append(download(A, img, os.path.join(d, stem + ".png")))
    line = f"{prefix}\t{secs}s\tremesh={remesh}\tups={ups}\ttex={tex}\t{[os.path.basename(s) for s in saved]}"
    with open(os.path.join(OUT, "timings.txt"), "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)
    if not any(s.endswith(".glb") for s in saved):
        print(json.dumps(outputs)[:1500])
