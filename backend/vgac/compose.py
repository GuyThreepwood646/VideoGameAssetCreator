"""Step 0.5: a character from several of your own pictures (a face, a body, an outfit, anything else).

  POST /api/assets/{aid}/stages/concept/compose
       {pictures: [{role: face | body | outfit | other, image, note?}], words?, count?: 1..4}
       -> {versions: [{job, version}]}

Each picture goes through uploads.py (the same checks as every own picture) and is written into each new concept
version's folder as files.compose_1 .. compose_N. A job (op "compose", stages/__init__.py) then asks Qwen-Image-Edit
(graphs.qwen_edit.compose_graph) for one new full-body picture: the face from the face picture, the build from the
body picture, the clothes from the outfit picture, drawn in the project's look (the style lock's prompt block, palette
and the category's pose block, through stages/concept.build_prompt) on a plain background, standing the way the later
steps want. The result is a normal concept version (params.source = "compose"): it is cut out and checked like a
painted picture, and the person keeps one in step 1 as usual, so nothing after step 1 changes.

The edit model reads at most three pictures (COMPOSE_MAX). With more, the first two (in role order: face, body,
outfit, other) go in on their own and the rest are laid side by side on one sheet as the third picture; the prompt
says so and the version records it (metrics.compose).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import library, pipeline, uploads
from .config import get_settings
from .db import Asset, Version, get_session, job_to_dict, version_to_dict
from .graphs.qwen_edit import COMPOSE_MAX, compose_graph
from .own_images import decode_or_400, image_field

router = APIRouter()

ROLES = ("face", "body", "outfit", "other")
# What the painter takes from each kind of picture.
ROLE_WORDS = {
    "face": "the face, the head and the hair",
    "body": "the body: its build, height and proportions",
    "outfit": "the clothes, armour and gear",
    "other": "the details it shows",
}
PICTURES_MAX = 6
COUNT_MAX = 4
SHEET_CELL = 512
SHEET_BG = (235, 235, 235)
NO_CATEGORY = ("material", "environment", "foliage")


class PictureIn(BaseModel):
    role: str = "other"
    image: str = image_field(min_length=1)
    note: str | None = Field(default=None, max_length=200)


class ComposeIn(BaseModel):
    pictures: list[PictureIn] = Field(min_length=1, max_length=PICTURES_MAX)
    words: str | None = Field(default=None, max_length=1000)
    count: int = Field(default=2, ge=1, le=COUNT_MAX)
    seed: int | None = None


def ordered(roles: list[dict[str, Any]]) -> list[int]:
    """The pictures' indexes in role order (face, body, outfit, other), keeping the person's order within a role."""
    return sorted(range(len(roles)), key=lambda i: (ROLES.index(roles[i]["role"]) if roles[i]["role"] in ROLES else len(ROLES), i))


def plan_inputs(roles: list[dict[str, Any]]) -> list[list[int]]:
    """Which pictures go into each of the edit model's (at most three) inputs: one picture each, or with more than
    three, the first two on their own and the rest together on one sheet."""
    order = ordered(roles)
    if len(order) <= COMPOSE_MAX:
        return [[i] for i in order]
    return [[order[0]], [order[1]], order[2:]]


def _what(r: dict[str, Any]) -> str:
    words = ROLE_WORDS.get(r["role"], ROLE_WORDS["other"])
    note = (r.get("note") or "").strip().rstrip(".")
    return f"{words} ({note})" if note else words


def compose_prompt(roles: list[dict[str, Any]], plan: list[list[int]], look_prompt: str) -> str:
    """The instruction: what to take from which picture, then the asset's own prompt with the look (build_prompt)."""
    lines = ["Draw one new full-body picture of a single character that combines these pictures."]
    for n, group in enumerate(plan, start=1):
        if len(group) == 1:
            lines.append(f"Take {_what(roles[group[0]])} from Picture {n}.")
        else:
            parts = "; ".join(f"picture {k} on it, from the left: {_what(roles[i])}" for k, i in enumerate(group, start=1))
            lines.append(f"Picture {n} is a sheet of {len(group)} pictures side by side. Take from it: {parts}.")
    lines.append("It is the same one person or creature throughout. Do not copy the pictures' backgrounds, poses, "
                 "lighting or camera angles, and redraw everything in the look described next, not in the look of "
                 "the pictures.")
    lines.append(look_prompt.strip())
    lines.append("Plain light grey background with nothing else in the picture.")
    return " ".join(x for x in lines if x)


def sheet(images: list[Image.Image], cell: int = SHEET_CELL) -> Image.Image:
    """Pictures side by side on plain grey, each fitted into a cell x cell square."""
    out = Image.new("RGB", (cell * len(images), cell), SHEET_BG)
    for k, im in enumerate(images):
        im = uploads.flatten(im) if im.mode == "RGBA" else im.convert("RGB")
        im.thumbnail((cell, cell), Image.LANCZOS)
        out.paste(im, (k * cell + (cell - im.width) // 2, (cell - im.height) // 2))
    return out


# --- the job (op "compose" on a concept version) ---------------------------------------------------------------------


async def run(sc, params: dict[str, Any]) -> None:
    """Draw the combined picture from files.compose_1..N, then the cutout and readiness checks of step 1."""
    from .stages import concept
    from .stages.base import random_seed

    p = {**(sc.version.params or {}), **(params or {})}
    roles = list(p.get("compose_roles") or [])
    if not roles:
        raise ValueError("no pictures to combine")
    paths = []
    for i in range(len(roles)):
        f = sc.file(f"compose_{i + 1}")
        if f is None:
            raise FileNotFoundError(f"picture {i + 1} to combine is missing")
        paths.append(f)
    plan = plan_inputs(roles)
    seed = int(p.get("seed") or random_seed())
    width = int(p.get("width") or 1024)
    height = int(p.get("height") or 1024)
    words = (p.get("words") or "").strip()
    look = concept.build_prompt(sc, {**p, "prompt_override": words or None})
    prompt = compose_prompt(roles, plan, look)
    sc.set_params(seed=seed, width=width, height=height, prompt=prompt, model="qwen_edit", source="compose")
    sc.use_model("qwen_edit")
    sc.use_model("qwen_lightning_lora")
    names = []
    for n, group in enumerate(plan, start=1):
        if len(group) == 1:
            src = paths[group[0]]
        else:
            src = sc.dir / "compose_sheet.png"
            sheet([Image.open(paths[i]) for i in group]).save(src)
            sc.register("compose_sheet", src)
        names.append(await sc.stage_input(src, f"vgac_{sc.version.id[:8]}_compose_{n}.png"))
    sc.progress(0.05, "combining your pictures")
    entry = await sc.comfy(compose_graph(names, prompt, seed, sc.prefix("compose"), width=width, height=height),
                           message=f"combining {len(roles)} picture{'s' if len(roles) > 1 else ''}", base=0.05, span=0.6)
    image = await sc.download_image(entry, "save", sc.dir / "image.png")
    sc.register("image", image)
    sc.set_metrics(seed=seed, model="qwen_edit", source="compose", elapsed_seconds=entry.get("elapsed_seconds"),
                   prompt_id=entry.get("prompt_id"), prompt_final=prompt, models=list(sc.models_used.values()),
                   compose={"pictures": len(roles), "inputs": [[roles[i]["role"] for i in g] for g in plan],
                            "sheet": any(len(g) > 1 for g in plan)})
    await concept._cutout(sc, image)


async def adjust(sc, params: dict[str, Any]) -> None:
    """Fine-tune of a combined picture: the same pictures again (copied from the parent) with the changed settings."""
    parent = sc.parent
    roles = list((parent.params or {}).get("compose_roles") or [])
    for i in range(len(roles)):
        src = library.version_file(parent, f"compose_{i + 1}", sc.settings)
        if src is None:
            raise FileNotFoundError(f"picture {i + 1} to combine is missing")
        sc.register(f"compose_{i + 1}", sc.copy_in(src, f"compose_{i + 1}.png"))
    merged = {**(parent.params or {}), **(params or {})}
    merged.pop("prompt", None)
    if "seed" not in (params or {}):
        merged.pop("seed", None)
    sc.set_params(**merged)
    await run(sc, merged)


# --- the endpoint -----------------------------------------------------------------------------------------------------


def _register(db: Session, v: Version, key: str, path) -> None:
    library.register_file(v, key, path, get_settings())
    db.add(v)
    db.commit()


@router.post("/api/assets/{aid}/stages/concept/compose")
def compose_concept(aid: str, body: ComposeIn, db: Session = Depends(get_session)) -> dict[str, Any]:
    a = db.get(Asset, aid)
    if a is None:
        raise HTTPException(404, "asset not found")
    if a.category in NO_CATEGORY:
        raise HTTPException(400, "Combining pictures into one character works for characters, creatures and props, not for this kind of asset.")
    roles: list[dict[str, Any]] = []
    images: list[Image.Image] = []
    for k, pic in enumerate(body.pictures, start=1):
        if pic.role not in ROLES:
            raise HTTPException(400, f"picture {k}: role must be one of {list(ROLES)}")
        images.append(decode_or_400(pic.image, f"{pic.role} picture"))
        roles.append({"role": pic.role, "note": (pic.note or "").strip() or None})
    words = (body.words or "").strip() or None
    if words and not (a.prompt or "").strip():
        a.prompt = words
        db.commit()
    from .stages.base import random_seed

    seed0 = body.seed if body.seed is not None else random_seed()
    out = []
    for n in range(body.count):
        v = pipeline.new_version(db, a, "concept", None, {"source": "compose", "compose_roles": roles, "words": words,
                                                           "seed": int(seed0) + n, "model": "qwen_edit",
                                                           "readiness_block": True})
        folder = library.version_dir(a, v, get_settings())
        for i, img in enumerate(images, start=1):
            _register(db, v, f"compose_{i}", uploads.save_png(uploads.flatten(img) if img.mode == "RGBA" else img, folder, f"compose_{i}.png"))
        job = pipeline.submit(db, a, v, "compose", {"params": {}})
        db.refresh(v)
        out.append({"job": job_to_dict(job), "version": version_to_dict(v)})
    return {"versions": out}
