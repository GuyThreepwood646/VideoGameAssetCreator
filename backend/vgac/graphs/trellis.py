"""TRELLIS.2 shape + texture + native post chain: RGBA image -> textured low-poly GLB with PBR maps.

bakeoff/convert.py node for node. RemeshMesh takes dotted sub-inputs "sign_mode.fill" and "sign_mode.qef".
Output nodes: glb (SaveGLB) and SaveImage nodes s_base, s_met, s_rough, s_nrm, s_ao, s_in.
"""

from __future__ import annotations

from typing import Any

IMAGE_NODES = {"s_base": "base_color", "s_met": "metallic", "s_rough": "roughness", "s_nrm": "normal", "s_ao": "ao", "s_in": "input"}


def convert_graph(image_name: str, prefix: str, target_faces: int = 20000, tex: int = 2048, upsample: int = 1024, seed: int = 42,
                  remesh: bool = True) -> dict[str, Any]:
    g: dict[str, Any] = {
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
                                                   "seed": seed + 1, "steps": 12, "cfg": 1.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0}},
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
        "unwrap": {"class_type": "UnwrapMesh", "inputs": {"mesh": ["sm", 0], "segmenter": "pec", "resolution": tex, "padding": 1, "weld_distance": 0.0002}},
        "bake": {"class_type": "BakeTextureFromVoxel", "inputs": {"mesh": ["unwrap", 0], "voxel_colors": ["dtex", 0], "reference_mesh": ["dshape", 0], "texture_size": tex}},
        "nrm": {"class_type": "BakeNormalMapFromMesh", "inputs": {"low_poly": ["unwrap", 0], "high_poly": [high, 0], "resolution": tex, "cage_distance": 0.05, "ignore_backfaces": True}},
        "ao": {"class_type": "BakeAmbientOcclusion", "inputs": {"low_poly": ["unwrap", 0], "high_poly": [high, 0], "resolution": max(512, tex // 2), "samples": 64, "max_distance": 0.71, "strength": 1.0, "bias": 0.01}},
        "apply": {"class_type": "ApplyTextureToMesh", "inputs": {"mesh": ["unwrap", 0], "base_color": ["bake", 0], "metallic": ["bake", 1], "roughness": ["bake", 2], "occlusion": ["ao", 0], "normal_map": ["nrm", 0]}},
        "sm2": {"class_type": "MeshSmoothNormals", "inputs": {"mesh": ["apply", 0], "crease_angle": 180.0}},
        "glb": {"class_type": "SaveGLB", "inputs": {"mesh": ["sm2", 0], "filename_prefix": prefix + "/model"}},
        "s_base": {"class_type": "SaveImage", "inputs": {"images": ["bake", 0], "filename_prefix": prefix + "/base_color"}},
        "s_met": {"class_type": "SaveImage", "inputs": {"images": ["bake", 1], "filename_prefix": prefix + "/metallic"}},
        "s_rough": {"class_type": "SaveImage", "inputs": {"images": ["bake", 2], "filename_prefix": prefix + "/roughness"}},
        "s_nrm": {"class_type": "SaveImage", "inputs": {"images": ["nrm", 0], "filename_prefix": prefix + "/normal"}},
        "s_ao": {"class_type": "SaveImage", "inputs": {"images": ["ao", 0], "filename_prefix": prefix + "/ao"}},
        "s_in": {"class_type": "SaveImage", "inputs": {"images": ["crop", 0], "filename_prefix": prefix + "/input"}},
    })
    return g
