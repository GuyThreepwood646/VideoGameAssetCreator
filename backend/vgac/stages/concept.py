"""Stage 1, concept: prompt -> image (+ BiRefNet cutout) on instance B.

run: Z-Image Turbo by default; the model comes from params.model, else the project style lock, else the style preset
(zturbo | zbase | klein | anima | pixel | chroma). The prompt is the asset prompt plus the style block plus the
category readiness block, plus a palette sentence when the project style lock names colours. refine: Qwen-Image-Edit-2511
instruction edit of the parent image, optionally limited to a mask. adjust: re-run with the parent's params updated.

Add-ons (data/addons.yaml): the recipe's defaults, then the project style lock's `loras` (registry ids, optionally
"id:strength"), then the version's `addons` knob ({id: off | subtle | normal | strong}). Each is applied only when the
chosen model is in its base_models; unknown ids are reported in metrics.addons_unknown, never a failure. The version
records the recipe (metrics.recipe) and the add-ons applied (params.addons_applied, metrics.addons).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .. import imaging, reference
from ..graphs.concept import concept_graph, prompt_with_triggers, save_node
from ..graphs.qwen_edit import edit_graph
from ..imaging import apply_mask
from .base import StageContext, random_seed


def stage_recipe(sc: StageContext) -> dict[str, Any]:
    """The recipe for this asset's style, category and platform (today's defaults for a style the app does not know)."""
    style = getattr(sc, "style", None) or {}
    asset = getattr(sc, "asset", None)
    sid = style.get("id") or getattr(asset, "style_preset", None) or ""
    try:
        return reference.recipe(sid, getattr(asset, "category", None), getattr(sc, "platform", None), strict=False)
    except (KeyError, TypeError):
        # A context without a known category or platform (a test double, an asset from an older data set): the
        # recipe of today's defaults.
        return reference.default_recipe(sid)


def palette_sentence(palette: Any) -> str:
    """The style lock's palette (hex colours) as a sentence the painter understands: colour names, not codes."""
    names = imaging.palette_names(palette)
    if not names:
        return ""
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f"Colour palette: mostly {listed}."


def build_prompt(sc: StageContext, params: dict[str, Any]) -> str:
    lock = sc.project.style_lock or {}
    base = (params.get("prompt_override") or "").strip() or sc.asset.prompt.strip()
    parts = [base]
    style_block = (lock.get("prompt_block") or "").strip() or (sc.style.get("prompt_block") or "").strip()
    if style_block:
        parts.append(style_block)
    pal = palette_sentence(lock.get("palette"))
    if pal:
        parts.append(pal)
    if params.get("readiness_block", True) and sc.category.get("pose_block"):
        parts.append(sc.category["pose_block"])
    if sc.asset.parts:
        parts.append("The following parts are clearly separate pieces: " + ", ".join(sc.asset.parts) + ".")
    return " ".join(p for p in parts if p)


def resolve_model(sc: StageContext, params: dict[str, Any]) -> str:
    lock = sc.project.style_lock or {}
    return params.get("model") or lock.get("model") or sc.style.get("model") or "zturbo"


def resolve_concept_addons(sc: StageContext, model: str, params: dict[str, Any], rec: dict[str, Any] | None = None) -> dict[str, Any]:
    rec = rec or stage_recipe(sc)
    lock = sc.project.style_lock or {}
    return reference.resolve_addons(model, "concept", rec["addons"], lock.get("loras"), params.get("addons") or None)


async def _generate(sc: StageContext, params: dict[str, Any]) -> None:
    rec = stage_recipe(sc)
    model = resolve_model(sc, params)
    seed = int(params.get("seed") or random_seed())
    width = int(params.get("width") or 1024)
    height = int(params.get("height") or 1024)
    steps = params.get("steps") or rec["concept"].get("steps")
    adds = resolve_concept_addons(sc, model, params, rec)
    applied = adds["applied"]
    prompt = build_prompt(sc, params)
    negative = (sc.project.style_lock or {}).get("negative") or rec["concept"].get("negative") or None
    sc.set_params(model=model, seed=seed, width=width, height=height, steps=steps, prompt=prompt, negative=negative or "",
                  addons_applied={a["id"]: a["strength"] if a["strength"] != "custom" else a["value"] for a in applied})
    sc.set_metrics(recipe=reference.recipe_record(rec, "concept", "addons"), addons=applied, addons_ignored=adds["ignored"],
                   addons_unknown=adds["unknown"], prompt_final=prompt_with_triggers(prompt, applied))
    sc.use_model(model)
    for a in applied:
        if not a.get("built_in"):
            sc.models_used[f"addon:{a['id']}"] = reference.addon_record(a["id"])
    graph = concept_graph(model, prompt, seed, sc.prefix("image"), width=width, height=height, steps=steps, negative=negative,
                          addons=applied)
    entry = await sc.comfy(graph, message="concept image", base=0.05, span=0.6)
    image = await sc.download_image(entry, save_node(model), sc.dir / "image.png")
    sc.register("image", image)
    sc.set_metrics(seed=seed, model=model, elapsed_seconds=entry.get("elapsed_seconds"), prompt_id=entry.get("prompt_id"),
                   models=list(sc.models_used.values()))
    await _cutout(sc, image)


async def _cutout(sc: StageContext, image: Path) -> None:
    res = await sc.cutout(image, "cutout", base=0.7, span=0.25)
    sc.register("cutout", res["cutout"])
    sc.register("cutout_white", res["white"])
    sc.set_metrics(cutout=res["stats"])
    await readiness_judge(sc, Path(res["cutout"]))


async def readiness_judge(sc: StageContext, cutout: Path | None = None) -> None:
    """Is the picture ready for 3D? One subject, whole in frame, big enough, and for people arms away from the body
    (readiness.concept_checks, all warnings). Written as metrics.checks with the gate summary."""
    from .. import quality, readiness

    cutout = cutout or sc.file("cutout")
    if cutout is None:
        return
    sc.progress(0.96, "checking it is ready for 3D")
    try:
        checks = await asyncio.to_thread(readiness.concept_checks, cutout, sc.category)
    except (OSError, ValueError) as e:  # an unreadable cutout: no readiness checks, never a failed picture
        sc.set_metrics(readiness_error=str(e)[:200])
        return
    sc.set_metrics(**quality.summarise(checks))


async def judge(sc: StageContext) -> None:
    """Recompute checks (POST judge mode=metrics): the readiness checks from the stored cutout."""
    await readiness_judge(sc)


async def upload(sc: StageContext, params: dict[str, Any], view: str | None = None) -> None:
    """A concept made from the person's own picture (files.image written by the upload endpoint): the same cutout
    the painted pictures get, then the readiness checks."""
    image = sc.file("image")
    if image is None:
        raise FileNotFoundError("the uploaded picture is missing")
    if not sc.version.params.get("source"):
        sc.set_params(source="upload")
    sc.set_metrics(source=sc.version.params.get("source"), models=list(sc.models_used.values()))
    await _cutout(sc, image)


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
    # An edit of the person's own picture is no longer their picture as it came, but still not a painted one.
    source = "upload_edit" if parent.params.get("source") in ("upload", "upload_edit") else parent.params.get("source")
    sc.set_params(**{**parent.params, **params, "seed": seed, "instruction": instruction, "refine_of": parent.id, "source": source})
    sc.use_model("qwen_edit")
    sc.use_model("qwen_lightning_lora")
    name = await sc.stage_input(src, f"vgac_{sc.version.id[:8]}_refine_src.png")
    # EXPERIMENTAL: a reference picture (files.reference, written by the refine endpoint) as Picture 2.
    ref = sc.file("reference")
    ref_name = await sc.stage_input(ref, f"vgac_{sc.version.id[:8]}_refine_ref.png") if ref is not None else None
    if ref_name:
        sc.set_params(reference_experimental=True)
    entry = await sc.comfy(edit_graph(name, instruction, seed, sc.prefix("edit"), reference_name=ref_name),
                           message="instruction edit" + (" with a reference picture" if ref_name else ""), base=0.05, span=0.6)
    edited = await sc.download_image(entry, "save", sc.dir / ("edit_raw.png" if mask_png else "image.png"))
    if mask_png is not None:
        sc.register("edit_raw", edited)
        edited = apply_mask(src, edited, mask_png, sc.dir / "image.png")
    sc.register("image", edited)
    sc.set_metrics(seed=seed, model="qwen_edit", instruction=instruction, elapsed_seconds=entry.get("elapsed_seconds"))
    await _cutout(sc, Path(edited))


async def adjust(sc: StageContext, params: dict[str, Any]) -> None:
    parent = sc.parent
    if parent is not None and parent.params.get("source") in ("upload", "upload_edit") and not params.get("prompt_override"):
        # Fine-tune on the person's own picture keeps the picture: copy it and cut it out again, never paint over it.
        from .. import library

        src = library.version_file(parent, "image", sc.settings)
        if src is None:
            raise FileNotFoundError("the uploaded picture is missing")
        sc.set_params(**{**parent.params, **params})
        sc.register("image", sc.copy_in(src, "image.png"))
        await upload(sc, params)
        return
    merged = {**(parent.params if parent else {}), **params}
    merged.pop("prompt", None)
    merged.pop("addons_applied", None)
    await _generate(sc, merged)
