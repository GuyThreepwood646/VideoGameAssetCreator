"""Feature photos: one first picture combined from the person's own pictures of a face, a body, an outfit or anything
else, given in the New asset form (POST /api/projects/{pid}/assets with `features`; main.create_asset calls `start`).

Each picture goes through uploads.py (the same checks as every own picture) and is written into the new concept
version's folder as files.compose_1 .. compose_N. With "A picture I have" the person's main picture comes first, in the
role "character" (the whole character, except what the feature photos replace). One job (op "compose",
stages/__init__.py) then asks Qwen-Image-Edit (graphs.qwen_edit.compose_graph) for one new full-body picture: the face
from the face picture, the build from the body picture, the clothes from the outfit picture, drawn in the project's look
(the style lock's prompt block and the category's pose block, through stages/concept.build_prompt; not its palette or
hair rule, so the clothes and the hair stay as in the pictures) on a plain background, standing the way the later steps
want. All the pictures make ONE step 1 picture (one version, one job). It is a normal concept version
(params.source = "compose"): cut out and checked like a painted picture, so nothing after step 1 changes; Fine-tune
combines the same pictures again (`adjust`).

For a human character the first face picture is also kept as the asset's own face close-up (library.own_face_closeup,
the step 4 face pass), so the sharper face is used where it matters most. With "A picture I have" and only face
pictures, nothing is redrawn: the person's picture is step 1 as it came and the face goes to the close-up only.

The edit model reads at most three pictures (COMPOSE_MAX). With more, the first two (in role order: character, face,
body, outfit, other) go in on their own and the rest are laid side by side on one sheet as the third picture; the
prompt says so and the version records it (metrics.compose).
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import library, people, pipeline, uploads
from .config import get_settings
from .db import Asset, Version, job_to_dict, version_to_dict
from .graphs.qwen_edit import COMPOSE_MAX, compose_graph
from .own_images import FACE_CLOSEUP_CATEGORIES, decode_or_400, image_field

# The roles a feature photo can have; "character" is only the main picture of "A picture I have".
FEATURE_ROLES = ("face", "body", "outfit", "other")
ROLES = ("character", *FEATURE_ROLES)
# What the painter takes from each kind of picture.
ROLE_WORDS = {
    "character": "the whole character, except what the other pictures replace",
    "face": "the face, the head and the hair",
    "body": "the body: its build, height and proportions",
    "outfit": "the clothes, armour and gear",
    "other": "the details it shows",
}
PICTURES_MAX = 6
CLOSEUP_MIN = 128  # the smallest side own_images.upload_face_closeup takes
SHEET_CELL = 512
SHEET_BG = (235, 235, 235)
NO_CATEGORY = ("material", "environment", "foliage")


class CropIn(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(gt=0)
    h: int = Field(gt=0)


class FeatureIn(BaseModel):
    role: str = "other"
    image: str = image_field(min_length=1)
    note: str | None = Field(default=None, max_length=200)
    # The box round the person the form suggested or the person set (people.py), in the picture's own pixels. The picture
    # is cut to it before anything is drawn. Absent: the whole photo.
    crop: CropIn | None = None
    # How many people the form found in the picture (0 when none or not looked for). With the whole photo and more than
    # one, the version records that the other people may be on the character.
    people_found: int = Field(default=0, ge=0, le=people.MAX_PEOPLE)


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
    lines.append("It is the same one person or creature throughout. Keep the hair (its colour, length and style) and "
                 "the clothes exactly as the pictures show them, with the same colours. Do not copy the pictures' "
                 "backgrounds, poses, lighting or camera angles; the next sentences only set how it is drawn and "
                 "posed.")
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
    # The look sets how it is drawn, not what the person wears or how the hair is done: no palette sentence and no hair
    # rule here (the palette repainted the outfit and the hair rule tied the hair back in the one stored composed run).
    look = concept.build_prompt(sc, {**p, "prompt_override": words or None, "palette_block": False, "hair_block": False})
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
                            "sheet": any(len(g) > 1 for g in plan),
                            "crops": [r.get("crop") for r in roles], "whole_with_others": [bool(r.get("whole_with_others")) for r in roles]})
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


# --- from the New asset form ------------------------------------------------------------------------------------------


Checked = list[tuple[dict[str, Any], Image.Image]]


def check(category: str, features: list[FeatureIn]) -> Checked:
    """The feature photos decoded and checked before the asset is made: [({role, note}, picture)]. 400 for an unknown
    role, an unreadable picture or a kind of asset nothing is drawn for."""
    if not features:
        return []
    if category in NO_CATEGORY:
        raise HTTPException(400, "Feature photos work for characters, creatures and props, not for this kind of asset.")
    out: Checked = []
    for k, pic in enumerate(features, start=1):
        if pic.role not in FEATURE_ROLES:
            raise HTTPException(400, f"feature photo {k}: role must be one of {list(FEATURE_ROLES)}")
        img = decode_or_400(pic.image, f"{pic.role} picture")
        rec: dict[str, Any] = {"role": pic.role, "note": (pic.note or "").strip() or None, "cropped": False, "crop": None,
                               "people_found": pic.people_found, "whole_with_others": False}
        if pic.crop is not None:
            box = people.clamp_box(pic.crop.model_dump(), *img.size)
            if box is None:
                raise HTTPException(400, f"feature photo {k}: the box is outside the picture or too small")
            img = img.crop((box["x"], box["y"], box["x"] + box["w"], box["y"] + box["h"]))
            rec.update(cropped=True, crop=[box["x"], box["y"], box["w"], box["h"]])
        elif pic.people_found > 1:
            rec["whole_with_others"] = True
        out.append((rec, img))
    return out


def keep_face_closeup(a: Asset, features: Checked) -> bool:
    """The first face picture big enough becomes the asset's own face close-up (human characters only)."""
    if a.category not in FACE_CLOSEUP_CATEGORIES:
        return False
    for role, img in features:
        if role["role"] == "face" and min(img.size) >= CLOSEUP_MIN:
            dest = library.own_face_closeup(a.project_id, a.id, get_settings())
            dest.parent.mkdir(parents=True, exist_ok=True)
            uploads.save_png(uploads.flatten(img, (64, 64, 64)), dest.parent, dest.name)  # the painted crop's grey
            return True
    return False


def needs_drawing(features: Checked, picture: Image.Image | None) -> bool:
    """Whether step 1 is drawn from the pictures: always from a description, and from the person's own picture only
    when a feature photo other than a face is given (a face alone goes to the close-up; the picture stays as it came)."""
    if not features:
        return False
    return picture is None or any(r["role"] != "face" for r, _ in features)


def _register(db: Session, v: Version, key: str, path) -> None:
    library.register_file(v, key, path, get_settings())
    db.add(v)
    db.commit()


def start(db: Session, a: Asset, features: Checked, picture: Image.Image | None, seed: int | None = None) -> dict[str, Any]:
    """The one step 1 picture combined from the feature photos (the person's main picture first, as "character"):
    {job, version}. The asset's description gives the words."""
    from .stages.base import random_seed

    pairs = ([({"role": "character", "note": None}, picture)] if picture is not None else []) + list(features)
    roles = [r for r, _ in pairs]
    words = (a.prompt or "").strip() or None
    v = pipeline.new_version(db, a, "concept", None, {"source": "compose", "compose_roles": roles, "words": words,
                                                       "seed": int(seed if seed is not None else random_seed()),
                                                       "model": "qwen_edit", "readiness_block": True})
    folder = library.version_dir(a, v, get_settings())
    for i, (_, img) in enumerate(pairs, start=1):
        _register(db, v, f"compose_{i}", uploads.save_png(uploads.flatten(img) if img.mode == "RGBA" else img, folder, f"compose_{i}.png"))
    job = pipeline.submit(db, a, v, "compose", {"params": {}})
    db.refresh(v)
    return {"job": job_to_dict(job), "version": version_to_dict(v)}
