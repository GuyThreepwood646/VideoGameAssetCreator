"""Tileable material category (instance B, no 3D).

concept: Z-Image Turbo surface render (raw candidate), files.image. mesh: deterministic seamless blend of the
selected concept plus derived height, normal, roughness and metallic maps and a 2x2 tile preview. prep and rig are
skipped by their own modules. export: texture set zip plus manifest.json.
"""

from __future__ import annotations

import asyncio
import json
import zipfile
from pathlib import Path
from typing import Any

from .. import library, quality
from ..db import _iso, utcnow
from ..graphs.material import (TRIAL_DRAW, UPSCALE_OUT, crop_margin, derive_maps, draw_size, is_wood, make_seamless, seam_error, t2i_graph,
                               upscale_graph, upscale_plan, wrap_pad)
from ..imaging import image_size
from .base import StageContext, random_seed, slug

MAP_KEYS = ("base_color", "normal", "roughness", "height", "metallic", "tile_preview")


def _prompt(sc: StageContext, params: dict[str, Any]) -> str:
    lock = sc.project.style_lock or {}
    base = (params.get("prompt_override") or "").strip() or sc.asset.prompt.strip()
    block = (lock.get("prompt_block") or "").strip()
    return f"{base} {block}".strip() if block else base


# --- concept ---------------------------------------------------------------------


async def concept_run(sc: StageContext, params: dict[str, Any]) -> None:
    seed = int(params.get("seed") or random_seed())
    # WP-58: a wood material at 2048 is drawn at the trial's 1024 and upscaled in the mesh stage (graphs/material.py draw_size).
    size = int(params.get("width") or draw_size(sc.asset.prompt, sc.asset.style_preset, sc.budget["texture"] or 1024))
    prompt = _prompt(sc, params)
    sc.set_params(model="zturbo", seed=seed, width=size, height=size, drawn_size=size, prompt=prompt)
    sc.use_model("zturbo")
    entry = await sc.comfy(t2i_graph(prompt, seed, size, sc.prefix("raw")), message="material surface", base=0.05, span=0.9)
    img = await sc.download_image(entry, "8", sc.dir / "image.png")
    sc.register("image", img)
    sc.register("raw", img)
    sc.set_metrics(seed=seed, elapsed_seconds=entry.get("elapsed_seconds"), size=list(image_size(img)))


# --- mesh (seamless + maps) --------------------------------------------------------


async def mesh_run(sc: StageContext, params: dict[str, Any]) -> None:
    src = sc.lineage_file("image", ("concept",))
    if src is None:
        raise FileNotFoundError("no concept image in lineage")
    power = float(params.get("seam_power") or 1.6)
    sc.set_params(engine="material", seam_power=power, texture_size=sc.budget["texture"])
    # The numpy work on textures up to 2048 px runs in a worker thread so the API stays responsive; the session
    # (register, set_metrics) is only used back on the event loop.
    sc.progress(0.1, "seamless blend")
    base = await asyncio.to_thread(make_seamless, src, sc.dir / "base_color.png", power=power)
    # WP-58: wood only, per bakeoff/wp58/trialC/VERDICT.md (2026-11-21); every other surface keeps today's tile.
    plan = upscale_plan(sc.asset.prompt, sc.asset.style_preset, sc.budget["texture"])
    if plan is None and is_wood(sc.asset.prompt, sc.asset.style_preset) and int(sc.budget["texture"]) != UPSCALE_OUT:
        sc.set_metrics(upscaler="skipped", upscaler_note=f"the upscaler is used only at a {UPSCALE_OUT} px texture budget; this "
                                                         f"budget is {sc.budget['texture']} px, so today's tile is kept")
    elif plan is not None:
        note = upscale_skip_note(_concept_source(sc), image_size(src))
        if note is not None:
            sc.set_metrics(upscaler="skipped", upscaler_note=note)
        elif await upscale_model_ready(sc.client(), plan["model"]):
            base = await _upscale_wood(sc, base, plan)
            sc.set_metrics(upscaler="wood", upscale_size=plan["size"])
        else:
            sc.set_metrics(upscaler="skipped", upscaler_note=f"{plan['model']} is not on this ComfyUI; kept today's tile")
    sc.progress(0.5, "derived maps")
    maps = await asyncio.to_thread(derive_maps, base, sc.dir)
    sc.register("base_color", base)
    for key, p in maps.items():
        sc.register(key, p)
    from .export import pack_texture_maps  # late import: export imports this module's helpers

    packed = await asyncio.to_thread(pack_texture_maps, {"base_color": base, **{k: maps.get(k) for k in ("metallic", "roughness", "ao")}},
                                     sc.dir, slug(getattr(getattr(sc, "asset", None), "name", None) or "material"))
    for key, p in packed.items():
        sc.register(key, p)
    err = await asyncio.to_thread(seam_error, base)
    sc.set_metrics(seam_error=round(err, 5), size=list(image_size(base)), textured=True)
    await judge(sc)


def _concept_source(sc: StageContext) -> str | None:
    """How the concept picture was made: "upload" or "upload_edit" for a photo, else None (a Z-Image draw)."""
    for v in sc.iter_lineage():
        if v.stage == "concept":
            return (v.params or {}).get("source")
    return None


def upscale_skip_note(source: str | None, size: tuple[int, int]) -> str | None:
    """WP-58: why the wood upscaler is skipped for this picture, or None when it may run. The upscaler was tested only on
    the 1024 Z-Image draw: an uploaded photo, or a picture of another size, keeps today's tile."""
    if source in ("upload", "upload_edit"):
        return "the picture is an uploaded photo, and the upscaler was tested only on Z-Image draws; kept today's tile"
    if tuple(size) != (TRIAL_DRAW, TRIAL_DRAW):
        return (f"the picture is {size[0]} px, not the {TRIAL_DRAW} px draw the upscaler was tested on; "
                "kept today's tile")
    return None


async def upscale_model_ready(client: Any, model: str) -> bool:
    """True when this ComfyUI lists the upscale model in UpscaleModelLoader (object_info, the truth for what a graph can
    load). Any failure to ask counts as not ready, so the job keeps today's tile."""
    from ..preflight import combo_choices

    try:
        info = await client.object_info("UpscaleModelLoader")
    except Exception:
        return False
    return model in (combo_choices(info, "model_name") or [])


async def _upscale_wood(sc: StageContext, base: Path, plan: dict[str, Any]) -> Path:
    """WP-58 (wood only): the seamless 1024 tile is wrap-padded, upscaled 4x by the ComfyUI upscale model, cropped back to
    the tile and resized to the plan's size with Lanczos. Returns the new base colour picture (same path as before)."""
    import numpy as np
    from PIL import Image

    arr = await asyncio.to_thread(lambda: np.asarray(Image.open(base).convert("RGB")))
    pad_p = sc.dir / "upscale_in.png"
    await asyncio.to_thread(Image.fromarray(wrap_pad(arr, plan["pad"])).save, pad_p)
    name = await sc.stage_input(pad_p, f"vgac_{sc.version.id[:8]}_upscale_in.png")
    entry = await sc.comfy(upscale_graph(name, sc.prefix("upscale"), plan["model"]), message="wood upscale", base=0.12, span=0.2)
    raw = await sc.download_image(entry, "8", sc.dir / "upscale_4x.png")

    def finish() -> Path:
        up = np.asarray(Image.open(raw).convert("RGB"))
        out = Image.fromarray(crop_margin(up, plan["pad"], plan["scale"])).resize((plan["size"], plan["size"]), Image.LANCZOS)
        out.save(base)
        return base

    return await asyncio.to_thread(finish)


async def judge(sc: StageContext) -> None:
    if sc.version.stage == "concept":
        if sc.version.params.get("source") in ("upload", "upload_edit"):
            await photo_judge(sc)
        return
    files = {k: library.version_file(sc.version, k, sc.settings) for k in ("base_color", "normal", "roughness", "height", "metallic")}
    checks = quality.texture_checks(files, sc.budget["texture"], keys=tuple(files))
    se = sc.version.metrics.get("seam_error")
    if se is not None:
        checks.append(quality.check("seam_error", se <= 0.05, se, 0.05))
    sc.set_metrics(**quality.summarise(checks))


# --- export ------------------------------------------------------------------------


async def export_run(sc: StageContext, params: dict[str, Any]) -> None:
    name = f"{slug(sc.asset.name)}_{sc.asset.id[:8]}"
    sc.set_params(export_name=name)
    files = {}
    for key in MAP_KEYS:
        p = sc.lineage_file(key, ("mesh",))
        if p is not None:
            files[key] = sc.copy_in(p, f"{key}.png")
            sc.register(key, files[key])
    if "base_color" not in files:
        raise FileNotFoundError("no material maps in lineage")
    # One walk of the lineage through the StageContext helper, reused for models, seeds and the manifest.
    lineage = sc.lineage()
    models: dict[str, Any] = {}
    seeds: dict[str, Any] = {}
    for entry in lineage:
        v = sc.db.get(type(sc.version), entry["version_id"])
        if v is None:
            continue
        for m in v.metrics.get("models") or []:
            models[m.get("id", m.get("name"))] = m
        if v.params.get("seed") is not None:
            seeds[v.stage] = v.params["seed"]
    manifest = {
        "name": sc.asset.name, "export_name": name, "asset_id": sc.asset.id, "project_id": sc.project.id, "version_id": sc.version.id,
        "created_at": _iso(utcnow()), "category": "material", "style_preset": sc.asset.style_preset,
        "style_lock": sc.project.style_lock, "platform_target": sc.platform, "budget": sc.budget, "prompt": sc.asset.prompt,
        "textures": {k: {"file": p.name, "size": list(image_size(p))} for k, p in files.items() if k != "tile_preview"},
        "tileable": True, "normal_convention": "OpenGL", "models": list(models.values()),
        "licences": sorted({m.get("licence", "unknown") for m in models.values()}), "seeds": seeds, "lineage": lineage,
    }
    sc.progress(0.5, "zip texture set")
    zp = await asyncio.to_thread(_write_zip, sc.dir / f"{name}_textures.zip", name, files, json.dumps(manifest, indent=1))
    sc.register("zip", zp)
    checks = quality.texture_checks({k: p for k, p in files.items() if k != "tile_preview"}, sc.budget["texture"], keys=tuple(k for k in files if k != "tile_preview"))
    sc.set_metrics(**quality.summarise(checks))
    manifest["checks"] = sc.version.metrics.get("checks")
    manifest["files"] = sc.version.files
    mp = sc.dir / "manifest.json"
    mp.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    sc.register("manifest", mp)


def _write_zip(zp: Path, name: str, files: dict[str, Path], manifest_json: str) -> Path:
    """Deflate the texture set and manifest into one zip (CPU and disk only, run in a thread)."""
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for key, p in files.items():
            z.write(p, f"{name}/{key}.png")
        z.writestr(f"{name}/manifest.json", manifest_json)
    return zp


# --- dispatch used by the registry (module_for returns this module for concept, mesh, export) ----

async def run(sc: StageContext, params: dict[str, Any]) -> None:
    stage = sc.version.stage
    if stage == "concept":
        await concept_run(sc, params)
    elif stage == "mesh":
        await mesh_run(sc, params)
    elif stage == "export":
        await export_run(sc, params)
    else:
        sc.set_metrics(skipped=True, reason="material category")


async def upload(sc: StageContext, params: dict[str, Any], view: str | None = None) -> None:
    """Material from a photo: files.image is the person's photo (written by the upload endpoint). It is kept as the
    raw surface for the seamless and derived-maps step, and checked for even light and a straight-on view."""
    if sc.version.stage != "concept":
        raise ValueError("only the first step of a material takes a photo")
    img = sc.file("image")
    if img is None:
        raise FileNotFoundError("the uploaded photo is missing")
    if not sc.version.params.get("source"):
        sc.set_params(source="upload")
    sc.register("raw", img)
    await photo_judge(sc)


async def photo_judge(sc: StageContext) -> None:
    from .. import readiness

    img = sc.file("image")
    if img is None:
        return
    sc.progress(0.5, "checking the photo")
    checks = await asyncio.to_thread(readiness.photo_checks, img)
    sc.set_metrics(source=sc.version.params.get("source"), size=list(image_size(img)), **quality.summarise(checks))


async def _edit_photo(sc: StageContext, parent, instruction: str, params: dict[str, Any]) -> None:
    """Change something on a photo-based material: an instruction edit of the photo (the concept refine graph),
    never a fresh surface from the description."""
    from ..graphs.qwen_edit import edit_graph

    src = library.version_file(parent, "image", sc.settings)
    if src is None:
        raise FileNotFoundError("the photo is missing")
    seed = int(params.get("seed") or random_seed())
    sc.set_params(**{**parent.params, **params, "seed": seed, "instruction": instruction, "refine_of": parent.id, "source": "upload_edit"})
    sc.use_model("qwen_edit")
    sc.use_model("qwen_lightning_lora")
    name = await sc.stage_input(src, f"vgac_{sc.version.id[:8]}_refine_src.png")
    entry = await sc.comfy(edit_graph(name, instruction, seed, sc.prefix("edit")), message="instruction edit", base=0.05, span=0.8)
    img = await sc.download_image(entry, "save", sc.dir / "image.png")
    sc.register("image", img)
    sc.register("raw", img)
    await photo_judge(sc)


async def refine(sc: StageContext, instruction: str, view: str | None, mask_png: Path | None, params: dict[str, Any]) -> None:
    parent = sc.parent
    if sc.version.stage == "concept" and parent is not None and parent.params.get("source") in ("upload", "upload_edit"):
        await _edit_photo(sc, parent, instruction, params)
        return
    merged = {**(parent.params if parent else {}), **params, "instruction": instruction}
    if sc.version.stage == "concept":
        merged["prompt_override"] = f"{_prompt(sc, parent.params if parent else {})} {instruction}".strip()
        merged["seed"] = params.get("seed") or random_seed()
    await run(sc, merged)


async def adjust(sc: StageContext, params: dict[str, Any]) -> None:
    parent = sc.parent
    if sc.version.stage == "concept" and parent is not None and parent.params.get("source") in ("upload", "upload_edit") \
            and not params.get("prompt_override"):
        src = library.version_file(parent, "image", sc.settings)
        if src is None:
            raise FileNotFoundError("the photo is missing")
        sc.set_params(**{**parent.params, **params})
        sc.register("image", sc.copy_in(src, "image.png"))
        await upload(sc, params)
        return
    merged = {**(parent.params if parent else {}), **params}
    await run(sc, merged)
