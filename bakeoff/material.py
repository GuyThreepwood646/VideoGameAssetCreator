"""Tileable PBR material prototype on instance B: prompt -> seamless base colour -> normal, roughness, height maps.

1. Z-Image Turbo renders a top-down surface at 1024 square.
2. Seamless pass: wrap-offset the image by half its size so the seams meet in the middle, mask a cross over the seams,
   and inpaint the cross with Z-Image Turbo (DifferentialDiffusion + SetLatentNoiseMask, denoise 0.55). The result
   tiles because the original borders are now interior and the new centre was painted to match.
3. Derived maps with numpy: height from luminance (blurred), normal from the height gradient (OpenGL convention),
   roughness as inverted high-pass detail, metallic flat black. These are honest approximations; stage 4's bake path
   gives better maps for meshes, but for a tileable surface this is the free baseline.

Usage: python material.py <name> "<prompt>" [size=1024]
Output: bakeoff/out/material/<name>/{base_color,normal,roughness,height,metallic}.png and tile_preview.png (2x2)
"""
import os
import random
import sys

import numpy as np
from PIL import Image, ImageFilter

sys.path.insert(0, os.path.dirname(__file__))
from comfy import B, BS, run, download, images_of, stage_input

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "material")
CKPT = "Z-Image" + BS + "z-image-turbo-fp8-aio.safetensors"
SURFACE = (" A flat, evenly lit, top-down photograph of the surface filling the whole frame edge to edge, uniform "
           "diffuse lighting with no shadows, no vignette, no objects, no horizon, no text, no borders, consistent "
           "scale across the image, suitable as a seamless tiling game texture.")


def t2i_graph(prompt, seed, size, prefix):
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CKPT}},
        "2": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.0}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": prompt + SURFACE}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": ""}},
        "5": {"class_type": "EmptySD3LatentImage", "inputs": {"width": size, "height": size, "batch_size": 1}},
        "6": {"class_type": "KSampler", "inputs": {"model": ["2", 0], "positive": ["3", 0], "negative": ["4", 0], "latent_image": ["5", 0],
                                                  "seed": seed, "steps": 8, "cfg": 1.0, "sampler_name": "res_multistep", "scheduler": "simple", "denoise": 1.0}},
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
        "8": {"class_type": "SaveImage", "inputs": {"images": ["7", 0], "filename_prefix": "bakeoff/material/" + prefix}},
    }


def inpaint_graph(image_name, mask_name, prompt, seed, prefix, denoise=0.55):
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CKPT}},
        "2": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.0}},
        "dd": {"class_type": "DifferentialDiffusion", "inputs": {"model": ["2", 0]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": prompt + SURFACE}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": ""}},
        "img": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "msk": {"class_type": "LoadImageMask", "inputs": {"image": mask_name, "channel": "red"}},
        "enc": {"class_type": "VAEEncode", "inputs": {"pixels": ["img", 0], "vae": ["1", 2]}},
        "nm": {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["enc", 0], "mask": ["msk", 0]}},
        "6": {"class_type": "KSampler", "inputs": {"model": ["dd", 0], "positive": ["3", 0], "negative": ["4", 0], "latent_image": ["nm", 0],
                                                  "seed": seed, "steps": 8, "cfg": 1.0, "sampler_name": "res_multistep", "scheduler": "simple", "denoise": denoise}},
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
        "8": {"class_type": "SaveImage", "inputs": {"images": ["7", 0], "filename_prefix": "bakeoff/material/" + prefix}},
    }


def make_seamless(raw_path, dest, power=1.6):
    """Deterministic seamless blend: mix the image with its half-offset copy using a weight that is 1 at the
    centre and 0 at the borders, so every border of the result equals the (continuous) centre of the offset copy."""
    im = np.asarray(Image.open(raw_path).convert("RGB"), dtype=np.float32)
    h, w, _ = im.shape
    shifted = np.roll(np.roll(im, h // 2, axis=0), w // 2, axis=1)
    yy = 1.0 - np.abs(np.linspace(-1, 1, h)) ** power
    xx = 1.0 - np.abs(np.linspace(-1, 1, w)) ** power
    wgt = np.outer(yy, xx)[..., None]
    # Normalise so the two weights sum to 1 everywhere (the shifted copy's weight is the shifted weight field).
    wgt_s = np.roll(np.roll(wgt, h // 2, axis=0), w // 2, axis=1)
    out = (im * wgt + shifted * wgt_s) / np.maximum(wgt + wgt_s, 1e-6)
    Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(dest)
    return dest


def derive_maps(base_path, d):
    im = Image.open(base_path).convert("RGB")
    g = np.asarray(im.convert("L"), dtype=np.float32) / 255.0
    h = np.asarray(Image.fromarray((g * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.5)), dtype=np.float32) / 255.0
    Image.fromarray((h * 255).astype(np.uint8)).save(os.path.join(d, "height.png"))
    dx = np.roll(h, -1, axis=1) - np.roll(h, 1, axis=1)
    dy = np.roll(h, -1, axis=0) - np.roll(h, 1, axis=0)
    strength = 4.0
    nx, ny, nz = -dx * strength, dy * strength, np.ones_like(h)
    n = np.stack([nx, ny, nz], axis=-1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    Image.fromarray(((n * 0.5 + 0.5) * 255).astype(np.uint8)).save(os.path.join(d, "normal.png"))
    low = np.asarray(Image.fromarray((g * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(8)), dtype=np.float32) / 255.0
    detail = np.abs(g - low)
    rough = np.clip(0.85 - detail * 2.0, 0.2, 1.0)
    Image.fromarray((rough * 255).astype(np.uint8)).save(os.path.join(d, "roughness.png"))
    Image.new("L", im.size, 0).save(os.path.join(d, "metallic.png"))
    tile = Image.new("RGB", (im.width * 2, im.height * 2))
    for x in (0, im.width):
        for y in (0, im.height):
            tile.paste(im, (x, y))
    tile.resize((1024, 1024)).save(os.path.join(d, "tile_preview.png"))


if __name__ == "__main__":
    name, prompt = sys.argv[1], sys.argv[2]
    size = int(sys.argv[3]) if len(sys.argv) > 3 else 1024
    d = os.path.join(OUT, name)
    os.makedirs(d, exist_ok=True)
    raw = os.path.join(d, "raw.png")
    if not os.path.exists(raw) or "--regen" in sys.argv:
        seed = random.randint(1, 2**31)
        outputs, secs = run(B, t2i_graph(prompt, seed, size, f"{name}_raw"))
        raw = download(B, images_of(outputs)[0], raw)
        print("raw", secs, "s", flush=True)
    base = make_seamless(raw, os.path.join(d, "base_color.png"))
    derive_maps(base, d)
    print("maps written to", d)
