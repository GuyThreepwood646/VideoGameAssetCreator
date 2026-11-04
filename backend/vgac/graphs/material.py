"""Tileable material engine (bakeoff/material.py): Z-Image Turbo surface render, deterministic seamless blend, derived maps.

The seam is closed with a numpy offset blend, never with AI inpainting (which invents objects on the seam cross).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageFilter

from . import BS

CKPT = "Z-Image" + BS + "z-image-turbo-fp8-aio.safetensors"
SURFACE = (" A flat, evenly lit, top-down photograph of the surface filling the whole frame edge to edge, uniform "
           "diffuse lighting with no shadows, no vignette, no objects, no horizon, no text, no borders, consistent "
           "scale across the image, suitable as a seamless tiling game texture.")


def t2i_graph(prompt: str, seed: int, size: int, prefix: str) -> dict[str, Any]:
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CKPT}},
        "2": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.0}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": prompt + SURFACE}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": ""}},
        "5": {"class_type": "EmptySD3LatentImage", "inputs": {"width": size, "height": size, "batch_size": 1}},
        "6": {"class_type": "KSampler", "inputs": {"model": ["2", 0], "positive": ["3", 0], "negative": ["4", 0], "latent_image": ["5", 0],
                                                  "seed": seed, "steps": 8, "cfg": 1.0, "sampler_name": "res_multistep", "scheduler": "simple", "denoise": 1.0}},
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
        "8": {"class_type": "SaveImage", "inputs": {"images": ["7", 0], "filename_prefix": prefix}},
    }


def make_seamless(raw_path: Path | str, dest: Path | str, power: float = 1.6) -> Path:
    """Deterministic seamless blend: mix the image with its half-offset copy using a weight that is 1 at the
    centre and 0 at the borders, so every border of the result equals the (continuous) centre of the offset copy."""
    im = np.asarray(Image.open(raw_path).convert("RGB"), dtype=np.float32)
    h, w, _ = im.shape
    shifted = np.roll(np.roll(im, h // 2, axis=0), w // 2, axis=1)
    yy = 1.0 - np.abs(np.linspace(-1, 1, h)) ** power
    xx = 1.0 - np.abs(np.linspace(-1, 1, w)) ** power
    wgt = np.outer(yy, xx)[..., None]
    wgt_s = np.roll(np.roll(wgt, h // 2, axis=0), w // 2, axis=1)
    out = (im * wgt + shifted * wgt_s) / np.maximum(wgt + wgt_s, 1e-6)
    Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(dest)
    return Path(dest)


def derive_maps(base_path: Path | str, d: Path | str, preview_size: int = 1024) -> dict[str, Path]:
    """Height from blurred luminance, normal from the height gradient (OpenGL convention), roughness as inverted
    high-pass detail, metallic flat black, and a 2x2 tile preview."""
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    im = Image.open(base_path).convert("RGB")
    g = np.asarray(im.convert("L"), dtype=np.float32) / 255.0
    h = np.asarray(Image.fromarray((g * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.5)), dtype=np.float32) / 255.0
    Image.fromarray((h * 255).astype(np.uint8)).save(d / "height.png")
    dx = np.roll(h, -1, axis=1) - np.roll(h, 1, axis=1)
    dy = np.roll(h, -1, axis=0) - np.roll(h, 1, axis=0)
    strength = 4.0
    nx, ny, nz = -dx * strength, dy * strength, np.ones_like(h)
    n = np.stack([nx, ny, nz], axis=-1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    Image.fromarray(((n * 0.5 + 0.5) * 255).astype(np.uint8)).save(d / "normal.png")
    low = np.asarray(Image.fromarray((g * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(8)), dtype=np.float32) / 255.0
    detail = np.abs(g - low)
    rough = np.clip(0.85 - detail * 2.0, 0.2, 1.0)
    Image.fromarray((rough * 255).astype(np.uint8)).save(d / "roughness.png")
    Image.new("L", im.size, 0).save(d / "metallic.png")
    tile = Image.new("RGB", (im.width * 2, im.height * 2))
    for x in (0, im.width):
        for y in (0, im.height):
            tile.paste(im, (x, y))
    tile.resize((preview_size, preview_size)).save(d / "tile_preview.png")
    return {k: d / f"{k}.png" for k in ("height", "normal", "roughness", "metallic", "tile_preview")}


def seam_error(base_path: Path | str) -> float:
    """Mean absolute difference between opposite borders (0 = perfectly seamless), in 0..1."""
    im = np.asarray(Image.open(base_path).convert("RGB"), dtype=np.float32) / 255.0
    v = np.abs(im[0, :, :] - im[-1, :, :]).mean()
    h = np.abs(im[:, 0, :] - im[:, -1, :]).mean()
    return float((v + h) / 2)
