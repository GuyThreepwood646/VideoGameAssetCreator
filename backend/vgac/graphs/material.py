"""Tileable material engine (bakeoff/material.py): Z-Image Turbo surface render, deterministic seamless blend, derived maps.

The seam is closed with a numpy offset blend, never with AI inpainting (which invents objects on the seam cross).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageFilter

from .concept import ZTURBO_UNET, zimage_loaders
SURFACE = (" A flat, evenly lit, top-down photograph of the surface filling the whole frame edge to edge, uniform "
           "diffuse lighting with no shadows, no vignette, no objects, no horizon, no text, no borders, consistent "
           "scale across the image, suitable as a seamless tiling game texture.")


def t2i_graph(prompt: str, seed: int, size: int, prefix: str) -> dict[str, Any]:
    return {
        **zimage_loaders(ZTURBO_UNET),
        "2": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.0}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1c", 0], "text": prompt + SURFACE}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1c", 0], "text": ""}},
        "5": {"class_type": "EmptySD3LatentImage", "inputs": {"width": size, "height": size, "batch_size": 1}},
        "6": {"class_type": "KSampler", "inputs": {"model": ["2", 0], "positive": ["3", 0], "negative": ["4", 0], "latent_image": ["5", 0],
                                                  "seed": seed, "steps": 8, "cfg": 1.0, "sampler_name": "res_multistep", "scheduler": "simple", "denoise": 1.0}},
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1v", 0]}},
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
    # Where a border meets a middle line both weights are 0 (8 texels, a 2 x 2 dot at the middle of each join in a
    # repeat): take the plain average there instead of 0 / 1e-6, which was pure black.
    out = np.where(wgt + wgt_s > 0, out, (im + shifted) / 2)
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


# WP-58: the AI upscaler (4x-PBRify UpscalerV4, CC0) runs for wood only, per bakeoff/wp58/trialC/VERDICT.md (2026-11-21).
# Every other surface keeps today's path exactly. The trial's tested output was 2048 px (wrap pad, 4x, crop, Lanczos to 2048).
WOOD_WORDS = frozenset({"wood", "wooden", "timber", "plank", "planks", "oak", "pine", "walnut", "bark", "log", "floorboard",
                        "parquet", "plywood"})  # whole words only: "woodland" is not wood
REALISTIC_PRESETS = frozenset({"realistic_pbr"})  # the stylised presets (painted, cel, retro64, lowpoly_flat, clay) never upscale
UPSCALE_MODEL = "4x-PBRify_UpscalerV4.pth"
UPSCALE_PAD = 32  # px of wrap-around padding on each side of the 1024 tile before the 4x upscale
UPSCALE_SCALE = 4
UPSCALE_OUT = 2048


def is_wood(prompt: str | None, style_preset: str | None) -> bool:
    """True when the material's description names wood (whole words in WOOD_WORDS) and the preset is realistic."""
    if style_preset not in REALISTIC_PRESETS:
        return False
    words = set(re.findall(r"[a-z]+", (prompt or "").lower()))
    return bool(words & WOOD_WORDS)


def upscale_plan(prompt: str | None, style_preset: str | None, texture_budget: int = UPSCALE_OUT) -> dict[str, Any] | None:
    """The upscaler plan for a material, or None (today's path). Used only for wood and only at the tested size: a
    texture budget other than exactly UPSCALE_OUT (2048) keeps today's tile (the trial measured detail at 2048, seams at
    1024; at 4096 the upscaler would ship 2048 where today's path may give 4096)."""
    if not is_wood(prompt, style_preset) or int(texture_budget) != UPSCALE_OUT:
        return None
    return {"model": UPSCALE_MODEL, "pad": UPSCALE_PAD, "scale": UPSCALE_SCALE, "size": UPSCALE_OUT}


# The size the trial drew the surface at before the 4x upscale (bakeoff/wp58/trialC: 1024 Z-Image, 4x, 2048). Drawing at
# 2048 and upscaling would make an 8192 picture only to shrink it, which was never tested.
TRIAL_DRAW = 1024


def draw_size(prompt: str | None, style_preset: str | None, texture_budget: int) -> int:
    """The size the concept stage draws at: the trial's 1024 for a wood material that the upscaler will take to 2048,
    otherwise the texture budget (every other material unchanged)."""
    if upscale_plan(prompt, style_preset, texture_budget) is not None:
        return TRIAL_DRAW
    return int(texture_budget)


def wrap_pad(arr: np.ndarray, pad: int = UPSCALE_PAD) -> np.ndarray:
    """Wrap-around pad, so the upscaler sees the tile's neighbours and the crop after it keeps the seam closed."""
    return np.pad(arr, ((pad, pad), (pad, pad), (0, 0)), mode="wrap")


def crop_margin(arr: np.ndarray, pad: int = UPSCALE_PAD, scale: int = UPSCALE_SCALE) -> np.ndarray:
    """Remove the upscaled padding again (pad * scale on each side)."""
    m = pad * scale
    return arr[m:-m, m:-m]


def upscale_graph(input_name: str, prefix: str, model: str = UPSCALE_MODEL) -> dict[str, Any]:
    """Core nodes only: load the picture, load the upscale model, upscale, save (output node 8)."""
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": input_name}},
        "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": model}},
        "3": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]}},
        "8": {"class_type": "SaveImage", "inputs": {"images": ["3", 0], "filename_prefix": prefix}},
    }
