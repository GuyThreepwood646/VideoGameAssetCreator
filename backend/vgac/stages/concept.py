"""Stage 1, concept: prompt -> image (+ BiRefNet cutout) on instance B.

run: Z-Image Turbo by default; the model comes from params.model, else the project style lock, else the style preset
(zturbo | zbase | klein | anima | pixel | chroma). The prompt is the asset prompt (ending in a full stop) plus the
style block, the look's hair sentence (vgac/hairpic.py), a palette sentence when the project style lock names colours,
the category readiness block, and last the look's face sentence (vgac/hairpic.face_sentence). refine: Qwen-Image-Edit-2511
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


def hair_sentence(sc: StageContext, params: dict[str, Any], description: str) -> str:
    """The look's hair or fur sentence for this description (vgac/hairpic.py); "" for assets without hair, for a
    bald, helmeted or hooded character, or with params.hair_block false."""
    from .. import hairpic

    if not params.get("hair_block", True):
        return ""
    return hairpic.hair_sentence(description, getattr(sc, "style", None), getattr(sc, "category", None))


def face_sentence(sc: StageContext, params: dict[str, Any], description: str) -> str:
    """The look's face sentence for this description (vgac/hairpic.face_sentence); "" for a look without one, a
    category it does not name, a description that hides the face, or with params.face_block false."""
    from .. import hairpic

    if not params.get("face_block", True):
        return ""
    return hairpic.face_sentence(description, getattr(sc, "style", None), getattr(sc, "category", None))


def full_stop(text: str) -> str:
    """The description ending in a full stop, so the style block that follows is a new sentence."""
    text = text.rstrip().rstrip(",;:").rstrip()
    if not text or text[-1] in ".!?":
        return text
    if text[-1] in "\"')" and len(text) > 1 and text[-2] in ".!?":
        return text
    return text + "."


def hair_negative(sc: StageContext, params: dict[str, Any]) -> str:
    """The hair negative terms for the description the prompt was built from."""
    from .. import hairpic

    if not params.get("hair_block", True):
        return ""
    return hairpic.hair_negative(_description(sc, params), getattr(sc, "style", None), getattr(sc, "category", None))


def _description(sc: StageContext, params: dict[str, Any]) -> str:
    from .. import items

    held = items.concept_prompt_parts(sc.asset)
    return (params.get("prompt_override") or "").strip() or ((held or {}).get("prompt") or sc.asset.prompt).strip()


def build_prompt(sc: StageContext, params: dict[str, Any]) -> str:
    lock = sc.project.style_lock or {}
    # Items (vgac/items.py): a character with its items set aside is painted empty-handed; a held item gets its own
    # pose block instead of the rigid prop's.
    from .. import items

    held = items.concept_prompt_parts(sc.asset)
    base = (params.get("prompt_override") or "").strip() or ((held or {}).get("prompt") or sc.asset.prompt).strip()
    parts = [full_stop(base)]
    style_block = (lock.get("prompt_block") or "").strip() or (sc.style.get("prompt_block") or "").strip()
    if style_block:
        parts.append(style_block)
    hair = hair_sentence(sc, params, base)
    if hair:
        parts.append(hair)
    pal = palette_sentence(lock.get("palette")) if params.get("palette_block", True) else ""
    if pal:
        parts.append(pal)
    if params.get("readiness_block", True) and (held or {}).get("pose_block"):
        parts.append(held["pose_block"])
    elif params.get("readiness_block", True) and sc.category.get("pose_block"):
        parts.append(sc.category["pose_block"])
    if (held or {}).get("sentence"):
        parts.append(held["sentence"])
    if sc.asset.parts:
        parts.append("The following parts are clearly separate pieces: " + ", ".join(sc.asset.parts) + ".")
    # Last: placed after the style block it changed the character more (runtime/tmp/celface).
    parts.append(face_sentence(sc, params, base))
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
    from .. import items

    aside = items.concept_prompt_parts(sc.asset) or {}
    if aside.get("negative") and (reference.models().get(model) or {}).get("negative"):
        # Painters that take a negative prompt are also told not to paint the items set aside.
        negative = ", ".join(x for x in (negative, aside["negative"]) if x)
    hair_neg = hair_negative(sc, params) if (reference.models().get(model) or {}).get("negative") else ""
    if hair_neg:
        # Painters that take a negative prompt are told what hair converts badly (vgac/hairpic.py).
        negative = ", ".join(x for x in (negative, hair_neg) if x)
    if aside.get("set_aside"):
        sc.set_metrics(items_set_aside=aside["set_aside"])
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
        held = getattr(getattr(sc, "asset", None), "held_item", None) or {}
        checks = await asyncio.to_thread(readiness.concept_checks, cutout, sc.category,
                                         (held.get("label") or "item") if held else None)
    except (OSError, ValueError) as e:  # an unreadable cutout: no readiness checks, never a failed picture
        sc.set_metrics(readiness_error=str(e)[:200])
        return
    hair = await asyncio.to_thread(_hair_check, sc, cutout)
    if hair is not None:
        checks.append(hair)
    light = await asyncio.to_thread(light_check, cutout, sc.category, _style_id(sc))
    if light is not None:
        checks.append(light)
    params = getattr(getattr(sc, "version", None), "params", None) or {}
    if params.get("source") == "compose":
        pics = [(r.get("role", "other"), sc.file(f"compose_{i}")) for i, r in enumerate(params.get("compose_roles") or [], start=1)]
        match = await asyncio.to_thread(compose_check, [(r, f) for r, f in pics if f is not None], cutout)
        if match is not None:
            checks.append(match)
    drawn = await _face_check(sc, cutout)
    if drawn is not None:
        checks.append(drawn)
    sc.set_metrics(**quality.summarise(checks))


def _hair_check(sc: StageContext, cutout: Path) -> dict[str, Any] | None:
    """Hair over the eyes (vgac/hairpic.py), for characters with head hair whose description does not ask for it.
    Never fails the picture: anything unexpected means no check."""
    from .. import hairpic

    try:
        params = getattr(getattr(sc, "version", None), "params", None) or {}
        text = (params.get("prompt_override") or "").strip() or (getattr(sc.asset, "prompt", "") or "")
        return hairpic.eyes_check(cutout, text, getattr(sc, "category", None), getattr(sc, "style", None))
    except Exception:  # noqa: BLE001  a readiness extra must never break the concept step
        return None


# --- hard light on the person (readiness check soft_light) ---------------------------------------------------------
# The 3D step bakes the light of the picture into the colour of the model: half a face in cast shadow, or legs in
# shadow under a bright chest, come out as dark paint that no game lighting can lift. Measured on the cut-out alone,
# in well under a second: the skin pixels (the warm hues hairpic.eye_cover looks for, any brightness) are split into
# 8 bands down the figure and two halves across, each cell with enough skin gets its mean lightness (CIE L, the
# conversion of face.to_lab), and the ratio of the darkest cell to the brightest is the measure. Flat light keeps the
# ratio above 0.4 on every test picture (dark skin and dark clothes included, because the ratio compares skin with
# skin of the same person); the two hard-light pictures give 0.15 and 0.17. Over the 28 stored Realistic pictures the
# next lowest is 0.41.
LIGHT_RATIO_MIN = 0.29  # darkest skin cell over brightest skin cell; under this the light is hard
LIGHT_STYLE = "realistic_pbr"  # only the Realistic look: the painted looks have shading of their own (14 of 89 stored pictures fall under the limit)
LIGHT_SIDE = 256
LIGHT_BANDS = 8
LIGHT_CELL_MIN_PX = 25  # skin pixels (at LIGHT_SIDE) a cell needs to count
LIGHT_CELLS_MIN = 4  # fewer cells with skin (a fully covered person): no verdict


def light_measure(cutout: Any) -> dict[str, Any] | None:
    """{ratio, cells, bright, dark} for a standing person's cut-out (RGBA path or PIL image), or None when too little
    skin shows to tell (fewer than LIGHT_CELLS_MIN cells with skin)."""
    import numpy as np
    from PIL import Image

    from .. import face

    im = (cutout if isinstance(cutout, Image.Image) else Image.open(cutout)).convert("RGBA")
    w, h = im.size
    sc = LIGHT_SIDE / max(w, h)
    if sc < 1:
        im = im.resize((max(1, round(w * sc)), max(1, round(h * sc))), Image.Resampling.BOX)
    a = np.asarray(im, dtype=np.float32) / 255.0
    fg = a[..., 3] > 0.5
    if fg.sum() < 200:
        return None
    lab = face.to_lab(a[..., :3])
    lum, ca, cb = lab[..., 0], lab[..., 1], lab[..., 2]
    skin = fg & (ca > 4) & (ca < 30) & (cb > 6) & (cb > 0.6 * ca) & (lum > 8)
    ys, xs = np.nonzero(fg)
    top, bot, lo, hi = int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())
    cells: list[float] = []
    for b in range(LIGHT_BANDS):
        y0 = top + (bot - top + 1) * b // LIGHT_BANDS
        y1 = top + (bot - top + 1) * (b + 1) // LIGHT_BANDS
        for k in range(2):
            x0 = lo + (hi - lo + 1) * k // 2
            x1 = lo + (hi - lo + 1) * (k + 1) // 2
            m = skin[y0:y1, x0:x1]
            if int(m.sum()) >= LIGHT_CELL_MIN_PX:
                cells.append(float(lum[y0:y1, x0:x1][m].mean()))
    if len(cells) < LIGHT_CELLS_MIN or max(cells) <= 0:
        return None
    return {"ratio": min(cells) / max(cells), "cells": len(cells), "bright": max(cells), "dark": min(cells)}


# --- a combined picture keeps the clothes and the hair of the pictures it was made from ------------------------------
# Measured on the stored runs (1 composed run, 6 pictures; and the Realistic test's pictures as same-outfit and
# different-outfit pairs): the same outfit in another picture differs by at most 8.3 in Lab (colour distance) in the
# upper or lower body band; the repainted outfit of the stored composed run by 25 to 35. The hair colour of the same
# person differs by at most 16 across pictures; no stored picture has a different hair colour, so the hair limit is
# set from the colour scale (black against dark brown is under 20), not from a measured miss.
COMPOSE_OUTFIT_MAX = 20.0
COMPOSE_HAIR_MAX = 25.0
COMPOSE_SIDE = 256
COMPOSE_PLAIN_MIN = 0.8  # share of the border that is one plain colour; busier pictures give no verdict


def _compose_bands(path: Any) -> dict[str, Any] | None:
    """Mean Lab colour of the upper body (28 to 55 percent of the figure's height), the lower body (55 to 92) and the
    hair (the top 12 percent without skin) of a picture with a plain background, or a cut-out; None when the
    background is not plain (a candid photo) or the figure is too small to read."""
    import numpy as np
    from PIL import Image

    from .. import face

    im = path if isinstance(path, Image.Image) else Image.open(path)
    im = im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB")
    s = COMPOSE_SIDE / max(im.size)
    if s < 1:
        im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.Resampling.NEAREST)
    a = np.asarray(im.convert("RGBA"), dtype=np.float32) / 255.0
    lab = face.to_lab(a[..., :3])
    if im.mode == "RGBA" and float(a[..., 3].min()) < 0.99:
        fg = a[..., 3] > 0.5
    else:
        edge = np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])
        med = np.median(edge, axis=0)
        if float((np.linalg.norm(edge - med, axis=-1) < 12).mean()) < COMPOSE_PLAIN_MIN:
            return None
        fg = np.linalg.norm(lab - med, axis=-1) > 14
    if int(fg.sum()) < 300:
        return None
    ys = np.nonzero(fg)[0]
    top, height = int(ys.min()), int(ys.max()) - int(ys.min()) + 1
    L, ca, cb = lab[..., 0], lab[..., 1], lab[..., 2]
    skin = (ca > 4) & (ca < 30) & (cb > 6) & (cb > 0.6 * ca) & (L > 8)

    def band(lo: float, hi: float, no_skin: bool) -> Any:
        y0, y1 = top + int(height * lo), top + int(height * hi)
        m = fg[y0:y1] & (~skin[y0:y1] if no_skin else True)
        return lab[y0:y1][m].mean(axis=0) if int(m.sum()) >= 60 else None

    return {"upper": band(0.28, 0.55, False), "lower": band(0.55, 0.92, False), "hair": band(0.0, 0.12, True)}


def compose_measure(inputs: list[tuple[str, Any]], cutout: Any) -> dict[str, Any] | None:
    """How far the combined picture's clothes and hair are from the pictures they came from: {outfit, hair} (Lab colour
    distances, None for a part that could not be compared), or None when nothing could be. `inputs` is
    [(role, picture)]: body and outfit pictures give the clothes, face pictures the hair."""
    import numpy as np

    got = _compose_bands(cutout)
    if got is None:
        return None
    outfit: list[float] = []
    hair: list[float] = []
    for role, pic in inputs:
        try:
            b = _compose_bands(pic)
        except Exception:  # noqa: BLE001  an unreadable input: it simply is not compared
            continue
        if b is None:
            continue
        if role in ("body", "outfit"):
            parts = [float(np.linalg.norm(b[k] - got[k])) for k in ("upper", "lower") if b[k] is not None and got[k] is not None]
            if parts:
                outfit.append(max(parts))
        elif role == "face" and b["hair"] is not None and got["hair"] is not None:
            hair.append(float(np.linalg.norm(b["hair"] - got["hair"])))
    if not outfit and not hair:
        return None
    return {"outfit": min(outfit) if outfit else None, "hair": min(hair) if hair else None}


def compose_check(inputs: list[tuple[str, Any]], cutout: Any) -> dict[str, Any] | None:
    """The check `compose_match` (a warning only) for a picture combined from the person's own pictures: the clothes
    and the hair colour still look like the pictures'. None when nothing could be compared. Never breaks the step."""
    from .. import quality

    try:
        m = compose_measure(inputs, cutout)
    except Exception:  # noqa: BLE001
        return None
    if m is None:
        return None
    ratios = {"the clothes": (m["outfit"] or 0.0) / COMPOSE_OUTFIT_MAX, "the hair": (m["hair"] or 0.0) / COMPOSE_HAIR_MAX}
    changed = [k for k, v in ratios.items() if v > 1.0]
    return quality.check("compose_match", not changed, round(max(ratios.values()), 2), 1.0,
                         vars={"changed": " and ".join(changed), "outfit": None if m["outfit"] is None else round(m["outfit"], 1),
                               "hair": None if m["hair"] is None else round(m["hair"], 1)})


def _style_id(sc: StageContext) -> str:
    style = getattr(sc, "style", None) or {}
    return style.get("id") or getattr(getattr(sc, "asset", None), "style_preset", None) or ""


def light_check(cutout: Any, category: dict[str, Any] | None, style_id: str | None = LIGHT_STYLE) -> dict[str, Any] | None:
    """The readiness check `soft_light` (a warning only) for a person's cut-out, or None for anything but a person in
    the Realistic look, an unreadable picture, or one that shows too little skin to tell. Never breaks the concept step."""
    from .. import quality, readiness

    cat = category or {}
    if (cat.get("id") or "humanoid") != "humanoid" or style_id != LIGHT_STYLE:
        return None
    try:
        m = light_measure(cutout)
    except Exception:  # noqa: BLE001  a readiness extra must never break the concept step
        return None
    if m is None:
        return None
    return quality.check("soft_light", m["ratio"] >= LIGHT_RATIO_MIN, round(m["ratio"], 2), LIGHT_RATIO_MIN,
                         vars={"redraw": readiness.redraw_instruction(cat)})


async def _face_check(sc: StageContext, cutout: Path) -> dict[str, Any] | None:
    """The face is drawn (vgac/hairpic.face_check): for the looks whose face block asks for the check, a close-up of the
    head goes to the local vision model (graphs.judge, Gemma 3) on the judge's card. Never fails the picture: anything
    unexpected means no check; only a cancelled job stops it."""
    from .. import hairpic
    from ..comfy import ComfyClient
    from ..graphs.judge import judge_graph

    try:
        params = getattr(getattr(sc, "version", None), "params", None) or {}
        text = (params.get("prompt_override") or "").strip() or (getattr(sc.asset, "prompt", "") or "")
        if not hairpic.face_check_applies(text, getattr(sc, "category", None), getattr(sc, "style", None)):
            return None
        crop = await asyncio.to_thread(hairpic.head_crop, cutout)
        if crop is None:
            return None
        path = sc.dir / "face_check.png"
        await asyncio.to_thread(crop.save, path)
        instance = sc.ctx.instance if sc.ctx.instance in sc.ctx.clients else sc.settings.judge_instance
        name = await sc.stage_input(path, f"vgac_{sc.version.id[:8]}_face_check.png", instance=instance)
        sc.progress(0.97, "checking the face is drawn")
        entry = await sc.comfy(judge_graph(name, hairpic.FACE_CHECK_PROMPT, max_length=60), instance=instance, poll=1.0,
                               message="checking the face is drawn (Gemma 3)", base=0.97, span=0.02)
        answer = hairpic.parse_face_answer("\n".join(ComfyClient.texts_of(entry, "show")))
        return hairpic.face_check(answer, entry.get("elapsed_seconds"))
    except Exception as e:  # noqa: BLE001  a readiness extra must never break the concept step
        if type(e).__name__ == "JobCancelled":
            raise
        return None


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


# "Upscale it" (the detail_size check): the picture made bigger with a learned upscaler so the subject is about
# readiness.UPSCALE_TARGET_PX tall. Photos and the Realistic look use 4xRealWebPhoto v4 (Phhofm, CC BY 4.0, made for
# real phone and web photos with their noise and compression); painted and drawn looks use Real-ESRGAN x2plus (BSD).
UPSCALE_PHOTO_MODEL = "4xRealWebPhoto_v4_dat2.pth"
UPSCALE_ART_MODEL = "RealESRGAN_x2plus.pth"
UPSCALE_MAX_FACTOR = 4.0
UPSCALE_MAX_SIDE = 4096


def upscale_plan(size: tuple[int, int], subject_px: float | None, photo: bool) -> dict[str, Any]:
    """The factor and output size for an upscale: enough for the subject to reach the target, at least 1.25 times,
    at most UPSCALE_MAX_FACTOR and UPSCALE_MAX_SIDE on the longer side."""
    from ..readiness import UPSCALE_TARGET_PX

    w, h = size
    want = UPSCALE_TARGET_PX / float(subject_px) if subject_px else UPSCALE_TARGET_PX / float(max(w, h))
    factor = max(1.25, min(UPSCALE_MAX_FACTOR, want))
    factor = min(factor, UPSCALE_MAX_SIDE / float(max(w, h)))
    return {"factor": round(factor, 3), "width": int(round(w * factor)), "height": int(round(h * factor)),
            "model": UPSCALE_PHOTO_MODEL if photo else UPSCALE_ART_MODEL}


def upscale_graph(image_name: str, model: str, width: int, height: int, prefix: str) -> dict[str, Any]:
    return {
        "img": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "um": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": model}},
        "up": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["um", 0], "image": ["img", 0]}},
        "fit": {"class_type": "ImageScale", "inputs": {"image": ["up", 0], "upscale_method": "lanczos", "width": width, "height": height, "crop": "disabled"}},
        "save": {"class_type": "SaveImage", "inputs": {"images": ["fit", 0], "filename_prefix": prefix}},
    }


async def upscale(sc: StageContext, params: dict[str, Any]) -> None:
    """A bigger copy of the parent concept's picture, then the same cutout and checks as an uploaded picture."""
    from PIL import Image

    from .. import library

    parent = sc.parent
    if parent is None:
        raise ValueError("upscaling needs the picture to upscale")
    src = library.version_file(parent, "image", sc.settings)
    if src is None:
        raise FileNotFoundError("the picture to upscale is missing")
    checks = {c.get("name"): c for c in (parent.metrics or {}).get("checks") or []}
    subject = (checks.get("detail_size") or checks.get("big_enough") or {}).get("value")
    with Image.open(src) as im:
        size = im.size
    photo = sc.asset.style_preset == "realistic_pbr" or parent.params.get("source") in ("upload", "upload_edit")
    plan = upscale_plan(size, float(subject) if subject else None, photo)
    source = "upload_edit" if parent.params.get("source") in ("upload", "upload_edit", None) else parent.params.get("source")
    sc.set_params(**{**parent.params, "source": source, "upscaled_from": parent.id, "upscale_model": plan["model"],
                     "upscale_factor": plan["factor"], "upload_size": [plan["width"], plan["height"]]})
    sc.use_model("upscale_photo" if photo else "upscale_art")
    name = await sc.stage_input(src, f"vgac_{sc.version.id[:8]}_upscale_src.png")
    entry = await sc.comfy(upscale_graph(name, plan["model"], plan["width"], plan["height"], sc.prefix("upscale")),
                           message=f"upscaling {plan['factor']:.2f} times", base=0.05, span=0.6)
    image = await sc.download_image(entry, "save", sc.dir / "image.png")
    sc.register("image", image)
    sc.set_metrics(source=source, upscale={**plan, "from_size": list(size), "subject_before": subject},
                   elapsed_seconds=entry.get("elapsed_seconds"))
    await _cutout(sc, Path(image))


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
    if source == "compose":
        source = "compose_edit"  # its folder has no source pictures, so a fine-tune of it paints afresh
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
    if parent is not None and parent.params.get("source") == "compose" and not params.get("prompt_override"):
        # Fine-tune of a picture combined from the person's pictures (step 0.5): combine the same pictures again.
        from .. import compose

        await compose.adjust(sc, params)
        return
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
