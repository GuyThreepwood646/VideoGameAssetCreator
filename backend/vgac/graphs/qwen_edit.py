"""Qwen-Image-Edit-2511 graphs: multiple-angles turnaround views and free instruction edits.

angle_graph is bakeoff/turnaround.py node for node. View names are SUBJECT-relative: "left" shows the
subject's left side, and the LoRA's "left side view" shows the subject's RIGHT side, so the prompt table
swaps them. edit_graph is the same pipeline without the angles LoRA, for Refine instructions.
"""

from __future__ import annotations

from typing import Any

from . import BS

Q = "Qwen" + BS

VIEWS = {
    "front":   "<sks> front view eye-level shot medium shot",
    "left":    "<sks> right side view eye-level shot medium shot",
    "back":    "<sks> back view eye-level shot medium shot",
    "right":   "<sks> left side view eye-level shot medium shot",
    "quarter": "<sks> front-right quarter view eye-level shot medium shot",
    # Not validated in the bake-off; same prompt grammar as the LoRA's documented tags.
    "top":     "<sks> top view high-angle shot medium shot",
}


def angle_graph(image_name: str, prompt: str, seed: int, prefix: str, lora_strength: float = 0.9) -> dict[str, Any]:
    return {
        "u": {"class_type": "UNETLoader", "inputs": {"unet_name": Q + "qwen_image_edit_2511_fp8mixed.safetensors", "weight_dtype": "default"}},
        "l1": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["u", 0], "lora_name": Q + "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors", "strength_model": 1.0}},
        "l2": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["l1", 0], "lora_name": Q + "qwen-image-edit-2511-multiple-angles-lora.safetensors", "strength_model": lora_strength}},
        "ms": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["l2", 0], "shift": 3.1}},
        "cn": {"class_type": "CFGNorm", "inputs": {"model": ["ms", 0], "strength": 1.0}},
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": Q + "qwen_2.5_vl_7b_fp8_scaled.safetensors", "type": "qwen_image", "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": Q + "qwen_image_vae.safetensors"}},
        "img": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "sc": {"class_type": "ImageScaleToTotalPixels", "inputs": {"image": ["img", 0], "upscale_method": "lanczos", "megapixels": 1.0, "resolution_steps": 8}},
        "pos": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["clip", 0], "prompt": prompt, "vae": ["vae", 0], "image1": ["sc", 0]}},
        "neg": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["clip", 0], "prompt": "", "vae": ["vae", 0], "image1": ["sc", 0]}},
        "posr": {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {"conditioning": ["pos", 0], "reference_latents_method": "index_timestep_zero"}},
        "negr": {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {"conditioning": ["neg", 0], "reference_latents_method": "index_timestep_zero"}},
        "lat": {"class_type": "EmptySD3LatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
        "ks": {"class_type": "KSampler", "inputs": {"model": ["cn", 0], "positive": ["posr", 0], "negative": ["negr", 0], "latent_image": ["lat", 0],
                                                   "seed": seed, "steps": 4, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "dec": {"class_type": "VAEDecode", "inputs": {"samples": ["ks", 0], "vae": ["vae", 0]}},
        "save": {"class_type": "SaveImage", "inputs": {"images": ["dec", 0], "filename_prefix": prefix}},
    }


def edit_graph(image_name: str, instruction: str, seed: int, prefix: str, width: int = 1024, height: int = 1024) -> dict[str, Any]:
    """Instruction edit of one image: the angle graph without the angles LoRA."""
    g = angle_graph(image_name, instruction, seed, prefix)
    del g["l2"]
    g["ms"]["inputs"]["model"] = ["l1", 0]
    g["lat"]["inputs"]["width"] = width
    g["lat"]["inputs"]["height"] = height
    return g


def view_prompt(view: str, extra: str = "") -> str:
    if view not in VIEWS:
        raise ValueError(f"unknown view {view!r}; known: {sorted(VIEWS)}")
    p = VIEWS[view]
    return (p + " " + extra.strip()) if extra and extra.strip() else p
