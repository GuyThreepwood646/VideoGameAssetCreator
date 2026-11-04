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
from ..graphs.material import derive_maps, make_seamless, seam_error, t2i_graph
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
    size = int(params.get("width") or sc.budget["texture"] or 1024)
    prompt = _prompt(sc, params)
    sc.set_params(model="zturbo", seed=seed, width=size, height=size, prompt=prompt)
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
    sc.progress(0.5, "derived maps")
    maps = await asyncio.to_thread(derive_maps, base, sc.dir)
    sc.register("base_color", base)
    for key, p in maps.items():
        sc.register(key, p)
    err = await asyncio.to_thread(seam_error, base)
    sc.set_metrics(seam_error=round(err, 5), size=list(image_size(base)), textured=True)
    await judge(sc)


async def judge(sc: StageContext) -> None:
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


async def refine(sc: StageContext, instruction: str, view: str | None, mask_png: Path | None, params: dict[str, Any]) -> None:
    parent = sc.parent
    merged = {**(parent.params if parent else {}), **params, "instruction": instruction}
    if sc.version.stage == "concept":
        merged["prompt_override"] = f"{_prompt(sc, parent.params if parent else {})} {instruction}".strip()
        merged["seed"] = params.get("seed") or random_seed()
    await run(sc, merged)


async def adjust(sc: StageContext, params: dict[str, Any]) -> None:
    parent = sc.parent
    merged = {**(parent.params if parent else {}), **params}
    await run(sc, merged)
