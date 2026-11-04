"""Phase 0 bake-off, step 2: turnaround views from a picked concept, on instance B (RTX 3080).

Qwen-Image-Edit-2511 fp8 + Lightning 4-step + the fal multiple-angles LoRA, following the recipe proven in
F:\\Projects\\ComfyUI\\training\\smuggins_lora\\LORA_PLAN.md. Each view is one job. Every output is background-removed
with BiRefNet and saved as RGBA, plus a copy composited over white for Hunyuan3D.

Usage: python turnaround.py <subject> <concept.png>
Output: bakeoff/out/turnaround/<subject>/{concept,front,left,back,right,quarter}.png (+ *_white.png)

View naming is SUBJECT-relative: "left" shows the subject's left side. The LoRA's "left side view" shows the
subject's RIGHT side (documented in LORA_PLAN), so the prompt table below swaps them.
"""
import os
import random
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(__file__))
from comfy import B, BS, run, download, images_of, stage_input

OUT = os.path.join(os.path.dirname(__file__), "out", "turnaround")

VIEWS = {
    "front":   "<sks> front view eye-level shot medium shot",
    "left":    "<sks> right side view eye-level shot medium shot",
    "back":    "<sks> back view eye-level shot medium shot",
    "right":   "<sks> left side view eye-level shot medium shot",
    "quarter": "<sks> front-right quarter view eye-level shot medium shot",
}

Q = "Qwen" + BS


def angle_graph(image_name, prompt, seed, prefix, lora_strength=0.9):
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
        "save": {"class_type": "SaveImage", "inputs": {"images": ["dec", 0], "filename_prefix": "bakeoff/turnaround/" + prefix}},
    }


def cutout_graph(image_name, prefix):
    """BiRefNet mask -> RGBA PNG. JoinImageWithAlpha treats 1 as transparent, so the mask is inverted first."""
    return {
        "img": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "bgm": {"class_type": "LoadBackgroundRemovalModel", "inputs": {"bg_removal_name": "birefnet.safetensors"}},
        "mask": {"class_type": "RemoveBackground", "inputs": {"bg_removal_model": ["bgm", 0], "image": ["img", 0]}},
        "inv": {"class_type": "InvertMask", "inputs": {"mask": ["mask", 0]}},
        "rgba": {"class_type": "JoinImageWithAlpha", "inputs": {"image": ["img", 0], "alpha": ["inv", 0]}},
        "save": {"class_type": "SaveImage", "inputs": {"images": ["rgba", 0], "filename_prefix": "bakeoff/cutout/" + prefix}},
    }


def white_copy(rgba_path):
    im = Image.open(rgba_path).convert("RGBA")
    bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
    bg.alpha_composite(im)
    dest = rgba_path[:-4] + "_white.png"
    bg.convert("RGB").save(dest)
    return dest


def cutout(local_png, subject, view):
    name = stage_input(local_png, f"bakeoff_{subject}_{view}_src.png")
    outputs, secs = run(B, cutout_graph(name, f"{subject}/{view}"))
    dest = os.path.join(OUT, subject, f"{view}.png")
    download(B, images_of(outputs)[0], dest)
    im = Image.open(dest)
    a = im.getchannel("A")
    w, h = im.size
    print(f"  cutout {view} {secs}s alpha corner={a.getpixel((2, 2))} centre={a.getpixel((w // 2, h // 2))}", flush=True)
    white_copy(dest)
    return dest


if __name__ == "__main__":
    subject, concept = sys.argv[1], sys.argv[2]
    views = sys.argv[3:] or list(VIEWS)
    os.makedirs(os.path.join(OUT, subject), exist_ok=True)
    cutout(concept, subject, "concept")
    src = stage_input(concept, f"bakeoff_{subject}_concept.png")
    for view in views:
        seed = random.randint(1, 2**31)
        outputs, secs = run(B, angle_graph(src, VIEWS[view], seed, f"{subject}/{view}_{seed}"))
        raw = os.path.join(OUT, subject, f"{view}_raw.png")
        download(B, images_of(outputs)[0], raw)
        print(f"{subject} {view} seed {seed} {secs}s", flush=True)
        cutout(raw, subject, view)
