"""Stage 1, concept: prompt -> image (+ BiRefNet cutout) on instance B.

run: Z-Image Turbo by default; the model comes from params.model, else the project style lock, else the style preset
(zturbo | zbase | klein | anima | pixel | chroma). The prompt is the asset prompt plus the style block plus the
category readiness block. refine: Qwen-Image-Edit-2511 instruction edit of the parent image, optionally limited to a
mask. adjust: re-run with the parent's params updated.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..graphs.concept import concept_graph, save_node
from ..graphs.qwen_edit import edit_graph
from ..imaging import apply_mask
from .base import StageContext, random_seed


def build_prompt(sc: StageContext, params: dict[str, Any]) -> str:
    lock = sc.project.style_lock or {}
    base = (params.get("prompt_override") or "").strip() or sc.asset.prompt.strip()
    parts = [base]
    style_block = (lock.get("prompt_block") or "").strip() or (sc.style.get("prompt_block") or "").strip()
    if style_block:
        parts.append(style_block)
    if params.get("readiness_block", True) and sc.category.get("pose_block"):
        parts.append(sc.category["pose_block"])
    if sc.asset.parts:
        parts.append("The following parts are clearly separate pieces: " + ", ".join(sc.asset.parts) + ".")
    return " ".join(p for p in parts if p)


def resolve_model(sc: StageContext, params: dict[str, Any]) -> str:
    lock = sc.project.style_lock or {}
    return params.get("model") or lock.get("model") or sc.style.get("model") or "zturbo"


async def _generate(sc: StageContext, params: dict[str, Any]) -> None:
    model = resolve_model(sc, params)
    seed = int(params.get("seed") or random_seed())
    width = int(params.get("width") or 1024)
    height = int(params.get("height") or 1024)
    steps = params.get("steps")
    prompt = build_prompt(sc, params)
    negative = (sc.project.style_lock or {}).get("negative") or None
    sc.set_params(model=model, seed=seed, width=width, height=height, steps=steps, prompt=prompt, negative=negative or "")
    sc.use_model(model)
    graph = concept_graph(model, prompt, seed, sc.prefix("image"), width=width, height=height, steps=steps, negative=negative)
    entry = await sc.comfy(graph, message="concept image", base=0.05, span=0.6)
    image = await sc.download_image(entry, save_node(model), sc.dir / "image.png")
    sc.register("image", image)
    sc.set_metrics(seed=seed, model=model, elapsed_seconds=entry.get("elapsed_seconds"), prompt_id=entry.get("prompt_id"))
    await _cutout(sc, image)


async def _cutout(sc: StageContext, image: Path) -> None:
    res = await sc.cutout(image, "cutout", base=0.7, span=0.25)
    sc.register("cutout", res["cutout"])
    sc.register("cutout_white", res["white"])
    sc.set_metrics(cutout=res["stats"])


async def run(sc: StageContext, params: dict[str, Any]) -> None:
    await _generate(sc, params)


async def refine(sc: StageContext, instruction: str, view: str | None, mask_png: Path | None, params: dict[str, Any]) -> None:
    parent = sc.parent
    if parent is None or not parent.files.get("image"):
        raise ValueError("refine needs a parent concept version with an image")
    from .. import library

    src = library.version_file(parent, "image", sc.settings)
    if src is None:
        raise FileNotFoundError("parent image missing on disk")
    seed = int(params.get("seed") or random_seed())
    sc.set_params(**{**parent.params, **params, "seed": seed, "instruction": instruction, "refine_of": parent.id})
    sc.use_model("qwen_edit")
    sc.use_model("qwen_lightning_lora")
    name = await sc.stage_input(src, f"vgac_{sc.version.id[:8]}_refine_src.png")
    entry = await sc.comfy(edit_graph(name, instruction, seed, sc.prefix("edit")), message="instruction edit", base=0.05, span=0.6)
    edited = await sc.download_image(entry, "save", sc.dir / ("edit_raw.png" if mask_png else "image.png"))
    if mask_png is not None:
        sc.register("edit_raw", edited)
        edited = apply_mask(src, edited, mask_png, sc.dir / "image.png")
    sc.register("image", edited)
    sc.set_metrics(seed=seed, model="qwen_edit", instruction=instruction, elapsed_seconds=entry.get("elapsed_seconds"))
    await _cutout(sc, Path(edited))


async def adjust(sc: StageContext, params: dict[str, Any]) -> None:
    parent = sc.parent
    merged = {**(parent.params if parent else {}), **params}
    merged.pop("prompt", None)
    await _generate(sc, merged)
