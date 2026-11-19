"""Hunyuan3D-2mv graph: four views over white -> GLB (shape only). From bakeoff/mesh.py.

Defaults match Tencent's own Hunyuan3D-2mv code: 50 steps, CFG 5.0, detail (octree) 384.
This is the maker's setting, not a measured quality win. The bundled ComfyUI template used
20 steps, CFG 7.5 and octree 256; pass those to reproduce it.
"""

from __future__ import annotations

from typing import Any

from . import BS

HY3D_VIEWS = ("front", "left", "back", "right")


def hy3d_graph(names: dict[str, str], prefix: str, steps: int = 50, cfg: float = 5.0, seed: int = 42,
               ckpt: str = "3D Rendering" + BS + "Hunyuan3D-2mv.fp16.safetensors",
               octree_resolution: int = 384) -> dict[str, Any]:
    g: dict[str, Any] = {
        "ck": {"class_type": "ImageOnlyCheckpointLoader", "inputs": {"ckpt_name": ckpt}},
        "ms": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["ck", 0], "shift": 1.0}},
        "cond": {"class_type": "Hunyuan3Dv2ConditioningMultiView", "inputs": {}},
        "lat": {"class_type": "EmptyLatentHunyuan3Dv2", "inputs": {"resolution": 3072, "batch_size": 1}},
        "ks": {"class_type": "KSampler", "inputs": {"model": ["ms", 0], "positive": ["cond", 0], "negative": ["cond", 1], "latent_image": ["lat", 0],
                                                   "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0}},
        "dec": {"class_type": "VAEDecodeHunyuan3D", "inputs": {"samples": ["ks", 0], "vae": ["ck", 2], "num_chunks": 8000, "octree_resolution": octree_resolution}},
        "mesh": {"class_type": "VoxelToMesh", "inputs": {"voxel": ["dec", 0], "algorithm": "surface net", "threshold": 0.6}},
        "glb": {"class_type": "SaveGLB", "inputs": {"mesh": ["mesh", 0], "filename_prefix": prefix}},
    }
    for view, name in names.items():
        g["img_" + view] = {"class_type": "LoadImage", "inputs": {"image": name}}
        g["enc_" + view] = {"class_type": "CLIPVisionEncode", "inputs": {"clip_vision": ["ck", 1], "image": ["img_" + view, 0], "crop": "none"}}
        g["cond"]["inputs"][view] = ["enc_" + view, 0]
    return g
