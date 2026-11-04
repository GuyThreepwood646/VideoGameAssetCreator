"""Concept image graphs: Z-Image Turbo (default), Z-Image base, FLUX.2 Klein 4B, Anima, Z-Image + pixel LoRA, Chroma1-HD.

zimage_graph is bakeoff/gen_concepts.py node for node. The others follow
C:/Users/evanr/.claude/skills/comfyui-game-art/scripts/stylemodels.py.

Which model uses which builder, its default steps, its SaveImage node and whether it takes a negative prompt are
read from data/models.yaml (docs/CODE-AUDIT.md D1); this module only holds the graph functions.
"""

from __future__ import annotations

from typing import Any, Callable

from .. import reference
from . import BS

PAINT_NEG = ("comic book art, cartoon, cel shading, thick black outlines, ink line art, heavy linework, flat colors, sticker, "
             "anime, vector art, 3d render, plastic skin, low quality, worst quality, blurry, smudged, deformed hands, extra fingers, "
             "extra limbs, duplicate person, text, letters, numbers, logo, watermark, signature, border, frame")
ANIMA_NEG = "worst quality, low quality, artist name, blurry, jpeg artifacts, text, watermark"

def zimage_graph(prompt: str, seed: int, prefix: str, width: int = 1024, height: int = 1024, steps: int = 8) -> dict[str, Any]:
    ckpt = "Z-Image" + BS + "z-image-turbo-fp8-aio.safetensors"
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.0}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": prompt}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": ""}},
        "5": {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "6": {"class_type": "KSampler", "inputs": {"model": ["2", 0], "positive": ["3", 0], "negative": ["4", 0],
                                                  "latent_image": ["5", 0], "seed": seed, "steps": steps, "cfg": 1.0,
                                                  "sampler_name": "res_multistep", "scheduler": "simple", "denoise": 1.0}},
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
        "8": {"class_type": "SaveImage", "inputs": {"images": ["7", 0], "filename_prefix": prefix}},
    }


def zbase_graph(prompt: str, seed: int, prefix: str, width: int = 1024, height: int = 1024, steps: int = 30, cfg: float = 4.0,
                neg: str = PAINT_NEG) -> dict[str, Any]:
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "Z-Image" + BS + "z_image_int8_convrot.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_3_4b.safetensors", "type": "lumina2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
        "3b": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.0}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": neg}},
        "6": {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "7": {"class_type": "KSampler", "inputs": {"model": ["3b", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["6", 0],
              "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": "res_multistep", "scheduler": "simple", "denoise": 1.0}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": prefix}},
    }


def klein_graph(prompt: str, seed: int, prefix: str, width: int = 1024, height: int = 1024, steps: int = 4) -> dict[str, Any]:
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "Flux2" + BS + "flux-2-klein-4b-fp8.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_3_4b.safetensors", "type": "flux2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "Flux" + BS + "flux2-vae.safetensors"}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
        "5": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "6": {"class_type": "Flux2Scheduler", "inputs": {"steps": steps, "width": width, "height": height}},
        "7": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "8": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "9": {"class_type": "BasicGuider", "inputs": {"model": ["1", 0], "conditioning": ["4", 0]}},
        "10": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["7", 0], "guider": ["9", 0], "sampler": ["8", 0], "sigmas": ["6", 0], "latent_image": ["5", 0]}},
        "11": {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": ["3", 0]}},
        "12": {"class_type": "SaveImage", "inputs": {"images": ["11", 0], "filename_prefix": prefix}},
    }


def anima_graph(prompt: str, seed: int, prefix: str, width: int = 896, height: int = 1152, steps: int = 10, turbo: bool = True) -> dict[str, Any]:
    unet = "Anima" + BS + ("anima-turbo-v1.1.safetensors" if turbo else "anima-aesthetic-v1.1.safetensors")
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_3_06b_base.safetensors", "type": "stable_diffusion", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "Qwen" + BS + "qwen_image_vae.safetensors"}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": "masterpiece, best quality, " + prompt}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": ANIMA_NEG}},
        "6": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "7": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["6", 0], "seed": seed,
              "steps": steps if turbo else 30, "cfg": 1.0 if turbo else 4.5, "sampler_name": "er_sde", "scheduler": "simple", "denoise": 1.0}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": prefix}},
    }


def pixel_graph(prompt: str, seed: int, prefix: str, width: int = 1024, height: int = 1024, steps: int = 8,
                lora: str = "ZImage" + BS + "pixel_art_style_z_image_turbo.safetensors", trigger: str = "Pixel art style.",
                strength: float = 1.0, colors: int = 32) -> dict[str, Any]:
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "Z-Image" + BS + "z-image-turbo-fp8-aio.safetensors"}},
        "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": lora, "strength_model": strength}},
        "3": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["2", 0], "shift": 3.0}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": trigger + " " + prompt}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": ""}},
        "6": {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "7": {"class_type": "KSampler", "inputs": {"model": ["3", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["6", 0],
              "seed": seed, "steps": steps, "cfg": 1.0, "sampler_name": "res_multistep", "scheduler": "simple", "denoise": 1.0}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["1", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": prefix + "_raw"}},
        "10": {"class_type": "PixelArtDetectorToImage", "inputs": {"images": ["8", 0], "reduce_palette": True, "reduce_palette_max_colors": colors}},
        "11": {"class_type": "SaveImage", "inputs": {"images": ["10", 0], "filename_prefix": prefix}},
    }


def chroma_graph(prompt: str, seed: int, prefix: str, width: int = 1024, height: int = 1024, steps: int = 30, cfg: float = 3.5,
                 neg: str = PAINT_NEG) -> dict[str, Any]:
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "Chroma" + BS + "Chroma1-HD-fp8mixed.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "t5xxl_fp8_e4m3fn_scaled.safetensors", "type": "chroma", "device": "default"}},
        "2b": {"class_type": "T5TokenizerOptions", "inputs": {"clip": ["2", 0], "min_padding": 1, "min_length": 0}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2b", 0], "text": prompt}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2b", 0], "text": neg}},
        "6": {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "7": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["6", 0],
              "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": "res_multistep", "scheduler": "beta", "denoise": 1.0}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": prefix}},
    }


def _spec(model: str) -> dict[str, Any]:
    try:
        return reference.concept_model(model)
    except KeyError as e:
        raise ValueError(str(e.args[0])) from None


def builder(model: str) -> Callable[..., dict[str, Any]]:
    """The graph function models.yaml names for `model`."""
    name = _spec(model).get("builder")
    fn = globals().get(str(name))
    if not callable(fn) or not str(name).endswith("_graph"):
        raise ValueError(f"concept model {model!r}: models.yaml names builder {name!r}, which graphs/concept.py does not define")
    return fn


def default_steps(model: str) -> int:
    return int(_spec(model)["default_steps"])


def concept_graph(model: str, prompt: str, seed: int, prefix: str, width: int = 1024, height: int = 1024,
                  steps: int | None = None, negative: str | None = None) -> dict[str, Any]:
    """Dispatch to the builder for `model`. `negative` applies to models that use a real negative prompt."""
    fn = builder(model)
    kwargs: dict[str, Any] = {"width": width, "height": height, "steps": steps or default_steps(model)}
    if negative and _spec(model).get("negative"):
        kwargs["neg"] = negative
    return fn(prompt, seed, prefix, **kwargs)


def save_node(model: str) -> str:
    """Node id of the SaveImage that holds the final image for `model`."""
    return str(_spec(model)["save_node"])
