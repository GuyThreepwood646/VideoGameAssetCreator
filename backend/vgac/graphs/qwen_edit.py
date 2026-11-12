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
    # The three-quarter view from the other side (the angles LoRA's own tag for it). Without it one cheek and one flank
    # have no picture between the front and the full side, and a seam shows there (vgac/srcproject.py, face_detail.py).
    "quarter2": "<sks> front-left quarter view eye-level shot medium shot",
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


# The two other ways to turn the views (docs/RECIPES-BAKEOFF.md sections 5.1 and 5.2, bakeoff/recipes/turn_ab.py).
# Each REPLACES the angles LoRA in slot l2; two view LoRAs are never stacked.
VGAC = "VGAC" + BS
SHEET_LORA = VGAC + "qie2511_turnaround_sheet_tarn59.safetensors"
CAMERA_LORA = VGAC + "qie2511_camera_angle_berkerdooo.safetensors"
SHEET_PROMPT = "Character turnaround sheet"
SHEET_SIZE = (2592, 1088)  # one wide picture: front, right, back, left, quarter (subject-relative), left to right
SHEET_INPUT = (600, 1080)  # the LoRA was trained on a 600 x 1080 portrait input
SHEET_ORDER = ("front", "right", "back", "left", "quarter")
# The camera LoRA turns the camera relative to the source (taken as the front). +90 shows the subject's left side
# and +45 a front-LEFT quarter in the bake-off, so the app's front-right quarter is -45 (not run in the bake-off).
CAMERA_TURNS = {"left": (90, "right"), "back": (180, "right"), "right": (90, "left"), "quarter": (45, "left")}


def camera_prompt(view: str, extra: str = "") -> str:
    if view not in CAMERA_TURNS:
        raise ValueError(f"the shadow-free turn has no {view!r} view; known: {sorted(CAMERA_TURNS)}")
    deg, side = CAMERA_TURNS[view]
    p = (f"Move the camera {deg} degrees to the {side} around the object. Target camera elevation: 0 degrees. "
         "Keep the object, its colors, and the white background the same.")
    return (p + " " + extra.strip()) if extra and extra.strip() else p


def sheet_graph(image_name: str, seed: int, prefix: str, strength: float = 1.0) -> dict[str, Any]:
    """All five views in one picture with the character turnaround sheet LoRA (run_sheet in turn_ab.py): the input
    is kept near its own size (0.65 MP), the output is SHEET_SIZE."""
    g = angle_graph(image_name, SHEET_PROMPT, seed, prefix, lora_strength=strength)
    g["l2"]["inputs"]["lora_name"] = SHEET_LORA
    g["lat"]["inputs"]["width"], g["lat"]["inputs"]["height"] = SHEET_SIZE
    g["sc"]["inputs"]["megapixels"] = 0.65
    return g


def camera_graph(image_name: str, view: str, seed: int, prefix: str, strength: float = 1.0, extra: str = "") -> dict[str, Any]:
    """One view with the camera-angle LoRA (run_cam in turn_ab.py), from the source composited on white."""
    g = angle_graph(image_name, camera_prompt(view, extra), seed, prefix, lora_strength=strength)
    g["l2"]["inputs"]["lora_name"] = CAMERA_LORA
    return g


def edit_graph(image_name: str, instruction: str, seed: int, prefix: str, width: int = 1024, height: int = 1024,
               reference_name: str | None = None) -> dict[str, Any]:
    """Instruction edit of one image: the angle graph without the angles LoRA.

    EXPERIMENTAL, reference_name: a second picture wired into image2 of both TextEncodeQwenImageEditPlus nodes (the
    node takes image1 to image3 and adds each as "Picture N" plus a reference latent). The prompt then names the
    pictures. The wiring matches the node's inputs; how well the model follows a reference is not yet measured."""
    prompt = reference_prompt(instruction) if reference_name else instruction
    g = angle_graph(image_name, prompt, seed, prefix)
    del g["l2"]
    g["ms"]["inputs"]["model"] = ["l1", 0]
    g["lat"]["inputs"]["width"] = width
    g["lat"]["inputs"]["height"] = height
    if reference_name:
        g["img2"] = {"class_type": "LoadImage", "inputs": {"image": reference_name}}
        g["sc2"] = {"class_type": "ImageScaleToTotalPixels", "inputs": {"image": ["img2", 0], "upscale_method": "lanczos", "megapixels": 1.0, "resolution_steps": 8}}
        g["pos"]["inputs"]["image2"] = ["sc2", 0]
        g["neg"]["inputs"]["image2"] = ["sc2", 0]
    return g


COMPOSE_MAX = 3  # TextEncodeQwenImageEditPlus takes image1 to image3


def compose_graph(image_names: list[str], prompt: str, seed: int, prefix: str, width: int = 1024,
                  height: int = 1024) -> dict[str, Any]:
    """One new picture from up to three source pictures (step 0.5, stages/compose.py): the edit graph with each
    picture wired into image1..image3 of both TextEncodeQwenImageEditPlus nodes, so the prompt can name them as
    Picture 1 to Picture 3. The output size is the latent's, not the first picture's."""
    if not 1 <= len(image_names) <= COMPOSE_MAX:
        raise ValueError(f"compose takes 1 to {COMPOSE_MAX} pictures, got {len(image_names)}")
    g = edit_graph(image_names[0], prompt, seed, prefix, width=width, height=height)
    for i, name in enumerate(image_names[1:], start=2):
        g[f"img{i}"] = {"class_type": "LoadImage", "inputs": {"image": name}}
        g[f"sc{i}"] = {"class_type": "ImageScaleToTotalPixels", "inputs": {"image": [f"img{i}", 0], "upscale_method": "lanczos", "megapixels": 1.0, "resolution_steps": 8}}
        g["pos"]["inputs"][f"image{i}"] = [f"sc{i}", 0]
        g["neg"]["inputs"][f"image{i}"] = [f"sc{i}", 0]
    return g


def reference_prompt(instruction: str) -> str:
    """The edit prompt when a reference picture rides along as Picture 2."""
    return (f"Edit Picture 1. {instruction.strip()} Use Picture 2 only as a reference for what is asked; keep the "
            "subject, pose, framing and background of Picture 1.")


def view_prompt(view: str, extra: str = "") -> str:
    if view not in VIEWS:
        raise ValueError(f"unknown view {view!r}; known: {sorted(VIEWS)}")
    p = VIEWS[view]
    return (p + " " + extra.strip()) if extra and extra.strip() else p
