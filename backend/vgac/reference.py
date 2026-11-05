"""Reference data: categories, styles, platforms, models, stage adjust schemas.

Loaded once from backend/vgac/data and served by /api/categories, /api/styles, /api/platforms, /api/models
and /api/stages. Call reload() after editing the YAML files while the server runs.

Single sources (docs/CODE-AUDIT.md D1, D2): STAGE_ORDER is the one stage tuple (db, jobs, stages, guidance and
autopilot import it), and the concept models are the `concept: true` entries of data/models.yaml.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

import yaml

from .config import DATA_DIR

STAGE_ORDER = ("concept", "turnaround", "mesh", "prep", "rig", "export")
STAGE_LABELS = {
    "concept": "Concept",
    "turnaround": "Preview turnaround",
    "mesh": "Mesh and texture",
    "prep": "Game-ready prep",
    "rig": "Rig",
    "export": "Export",
}
TURNAROUND_VIEWS = ("front", "left", "back", "right", "quarter", "top")

# Knobs that apply to some categories only carry `categories` (docs/GREAT-ASSETS-PLAN.md 2.3). The plan leaves
# environment out of the size knob because G15's grid will size scenery; until G15 lands, environment keeps the knob
# (sized on its longest side) so a floor tile or wall can still be set by hand. Remove it here when G15 lands.
# Material has no mesh to size or turn.
SIZE_CATEGORIES = ["humanoid", "quadruped", "winged", "multileg", "serpent", "vehicle", "prop_rigid", "prop_articulated",
                   "environment", "foliage"]
FACING_CATEGORIES = ["humanoid", "quadruped", "winged", "multileg", "serpent", "vehicle", "prop_rigid", "prop_articulated",
                     "environment", "foliage"]
# The prep turn choices, in degrees counter-clockwise seen from above ("auto" lets prep decide from the mesh audit).
FACE_YAW_OPTIONS: list[Any] = ["auto", 0, 90, 180, 270]
# Painted-on shadows (G07): auto follows the style.
DELIGHT_OPTIONS = ("auto", "remove", "keep")


def _param(name: str, type_: str, default: Any, label: str | None = None, description: str | None = None, **extra: Any) -> dict[str, Any]:
    """Adjust knob: {name, label, type: number|integer|boolean|select|multiselect|string, min, max, step, default, options, description}."""
    d: dict[str, Any] = {"name": name, "label": label or name.replace("_", " "), "type": type_, "default": default}
    if description:
        d["description"] = description
    d.update(extra)
    return d


# The Adjust knobs per stage, as docs/API.md lists them. Defaults that depend on the asset
# (budgets, size, symmetry) are filled in by the stage module at run time.
STAGE_SCHEMAS: dict[str, list[dict[str, Any]]] = {
    "concept": [
        # options are filled from models.yaml when the schema is read (_schema), so a new model needs no edit here.
        _param("model", "select", "zturbo", options=None, description="Concept engine; the project style lock or style preset decides when empty"),
        _param("seed", "integer", None, min=0, max=2**31 - 1, description="Random when empty"),
        _param("width", "integer", 1024, min=512, max=2048, step=64),
        _param("height", "integer", 1024, min=512, max=2048, step=64),
        _param("steps", "integer", None, min=1, max=60, description="Model default when empty"),
        _param("readiness_block", "boolean", True, description="Append the category pose block to the prompt"),
        _param("prompt_override", "string", "", description="Replaces the asset prompt for this version"),
    ],
    "turnaround": [
        _param("views", "multiselect", ["front", "left", "back", "right", "quarter"], options=list(TURNAROUND_VIEWS)),
        _param("lora_strength", "number", 0.9, min=0.0, max=1.5, step=0.05),
        _param("seed", "integer", None, min=0, max=2**31 - 1),
        _param("auto_reroll", "boolean", True, description="Re-roll a failing view up to 2 times"),
    ],
    "mesh": [
        _param("engine", "select", "trellis", options=["trellis", "hy3d"]),
        _param("input_view", "select", "concept", options=["concept", "front", "quarter"]),
        _param("target_faces", "integer", None, min=500, max=500000, description="Category and platform budget when empty"),
        _param("texture_size", "select", None, options=[512, 1024, 2048, 4096], description="Platform budget when empty"),
        _param("upsample", "select", 1024, options=[1024, 1536]),
        _param("seed", "integer", None, min=0, max=2**31 - 1),
        _param("remesh", "boolean", None, description="Watertight remesh before decimation; category default when empty (off for scenery so doorways stay open)"),
        # G03: UnwrapMesh accepts 0 to 16 texels; the plan's 64 is beyond the node.
        _param("padding", "integer", None, min=1, max=16, label="Space around texture pieces (pixels)",
               description="Automatic when empty: 4 px at 1024, 8 at 2048, 16 at 4096"),
    ],
    "prep": [
        # height_m (the old name) is still read from older versions as an alias meaning the height.
        _param("size_m", "number", None, min=0.01, max=100.0, step=0.01, categories=SIZE_CATEGORIES,
               description="In metres along the category's size axis; the category size, or a size in the description, when empty"),
        _param("face_yaw", "select", "auto", options=FACE_YAW_OPTIONS, categories=FACING_CATEGORIES,
               description="Pick the picture where the front faces you. The game treats that side as forward."),
        _param("symmetric", "boolean", False, categories=SIZE_CATEGORIES,
               description="Off unless turned on; mirroring makes prep paint the textures again (G05)"),
        _param("pivot", "select", "base", options=["base", "center"]),
        _param("smooth_angle", "number", 60.0, min=0.0, max=180.0),
        # G07, G08: the style decides when empty (data/styles.yaml delight_default and pbr_fix_default).
        _param("delight", "select", "auto", options=list(DELIGHT_OPTIONS), categories=SIZE_CATEGORIES, label="Painted-on shadows"),
        _param("pbr_fix", "boolean", None, categories=SIZE_CATEGORIES, label="Keep colours in a realistic range",
               description="On for realistic styles, off for others, when empty"),
        _param("rebake", "boolean", False, categories=SIZE_CATEGORIES, label="Paint the surface again",
               description="Prep repaints by itself when mirroring or shrinking changes the shape"),
    ],
    "rig": [
        _param("template", "select", "auto", options=["auto", "mixamo", "generic", "none"]),
        _param("target_faces", "integer", 0, min=0, max=5000000, description="0 keeps the mesh as is"),
        _param("group_per_vertex", "integer", 4, min=1, max=8),
        _param("bottom_center_origin", "boolean", True),
        _param("bend_test", "boolean", True, description="Render bend tests at 35 and 90 degrees"),
        _param("motion", "select", None, options=["legs", "wings", "wave", "none"], categories=["quadruped", "multileg", "winged", "serpent"],
               description="From the category when empty"),
        _param("moves", "multiselect", None, options=["idle", "walk", "run", "turn", "attack", "hit", "death", "flap", "glide", "takeoff", "land", "slither"],
               categories=["humanoid", "quadruped", "multileg", "winged", "serpent"], description="Every move of the category when empty"),
        _param("move_size", "select", "normal", options=["small", "normal", "big"], categories=["humanoid", "quadruped", "multileg", "winged", "serpent"]),
        _param("move_speed", "select", "normal", options=["slow", "normal", "fast"], categories=["humanoid", "quadruped", "multileg", "winged", "serpent"]),
        _param("hitboxes", "select", None, options=["detailed", "simple", "none"], categories=["humanoid", "quadruped", "multileg", "winged", "serpent"],
               description="detailed on PC and Showcase, simple on Phone when empty"),
        _param("hitbox_fit", "select", "normal", options=["snug", "normal", "loose"], categories=["humanoid", "quadruped", "multileg", "winged", "serpent"]),
        _param("hitbox_regions", "multiselect", None,
               options=["head", "neck", "chest", "hips", "body", "tail", "upper_arm_l", "forearm_l", "upper_arm_r", "forearm_r", "thigh_l", "shin_l",
                        "thigh_r", "shin_r", "leg_fl", "leg_fr", "leg_bl", "leg_br", "leg_l", "leg_r", "wing_l", "wing_r", "wings"],
               categories=["humanoid", "quadruped", "multileg", "winged", "serpent"], description="Every region found when empty"),
    ],
    "export": [
        _param("lods", "string", None, description="LOD ratios after LOD0, comma separated, e.g. 0.5, 0.25, 0.1; category default when empty"),
        _param("collider", "select", "auto", label="Collision shape", options=["auto", "hull", "decompose", "mesh", "none"],
               categories=["prop_rigid", "prop_articulated", "vehicle", "environment", "foliage"],
               description="The invisible shapes the game uses to know where this is solid."),
        _param("collider_max_parts", "integer", None, label="Most shapes", min=1, max=32,
               categories=["prop_rigid", "prop_articulated", "vehicle", "environment"],
               description="Automatic when empty: 8 for props, 16 for scenery, 6 for vehicles, half on Phone"),
        _param("movement_shape", "select", "auto", options=["auto", "upright", "along", "box", "none"], categories=["humanoid", "quadruped", "multileg", "winged", "serpent"]),
        _param("formats", "multiselect", ["fbx", "glb"], options=["fbx", "glb"]),
        _param("unity", "boolean", True, description="Import into the Unity sandbox and build the prefab"),
        _param("clips_library", "select", "ual1", options=["ual1", "ual2", "none"], categories=["humanoid"]),  # a Humanoid retarget
        _param("sprites", "boolean", False),
        _param("sprite_directions", "integer", 8, min=1, max=16),
        _param("sprite_frames", "integer", 8, min=1, max=64, description="Frames per clip in the sprite sheet"),
        _param("sprite_size", "integer", 128, min=32, max=1024),
    ],
}

MAX_STRING_PARAM = 4000


def _match_option(value: Any, opts: list[Any]) -> Any:
    """The option equal to value, or the one whose text equals it ("90" for 90, as a form may send it); else value."""
    if not isinstance(value, bool) and value in opts:
        return value
    for o in opts:
        if str(o) == str(value):
            return o
    return value


def validate_params(stage: str, params: dict[str, Any] | None, category: str | None = None) -> dict[str, Any]:
    """Check the Adjust knobs of a stage against STAGE_SCHEMAS: type, range and options. Raises ValueError with a
    plain message. Keys the schema does not know (internal markers such as instruction or refine_of) pass through,
    and None means "use the default". With `category`, a knob whose `categories` list leaves it out is accepted and
    dropped (it does not apply to that kind of asset)."""
    out = dict(params or {})
    if stage not in STAGE_SCHEMAS:
        return out
    spec = {p["name"]: p for p in _schema(stage)}
    for key, value in list(out.items()):
        p = spec.get(key)
        if p is None or value is None:
            continue
        if category and p.get("categories") is not None and category not in p["categories"]:
            del out[key]
            continue
        t = p["type"]
        if t in ("integer", "number"):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{key} must be a number")
            lo, hi = p.get("min"), p.get("max")
            if (lo is not None and value < lo) or (hi is not None and value > hi):
                raise ValueError(f"{key} must be between {lo} and {hi}")
            out[key] = int(value) if t == "integer" else float(value)
        elif t == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be true or false")
        elif t == "select":
            opts = p.get("options")
            if opts:
                value = _match_option(value, opts)
                if isinstance(value, bool) or value not in opts:
                    raise ValueError(f"{key} must be one of {opts}")
                out[key] = value
        elif t == "multiselect":
            opts = p.get("options") or []
            if not isinstance(value, list):
                raise ValueError(f"{key} must be a list drawn from {opts}")
            value = [_match_option(v, opts) for v in value]
            if any(v not in opts for v in value):
                raise ValueError(f"{key} must be a list drawn from {opts}")
            out[key] = value
        elif t == "string":
            if not isinstance(value, str):
                raise ValueError(f"{key} must be text")
            if len(value) > MAX_STRING_PARAM:
                raise ValueError(f"{key} is too long (max {MAX_STRING_PARAM} characters)")
    return out


def _load_yaml(path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def _data() -> dict[str, Any]:
    categories: dict[str, dict[str, Any]] = {}
    for p in sorted((DATA_DIR / "categories").glob("*.yaml")):
        c = _load_yaml(p)
        c.setdefault("id", p.stem)
        c["pose_block"] = " ".join(str(c.get("pose_block", "")).split())
        categories[c["id"]] = c
    styles = {s["id"]: {**s, "prompt_block": " ".join(str(s.get("prompt_block", "")).split())}
              for s in _load_yaml(DATA_DIR / "styles.yaml").get("styles", [])}
    platforms = {p["id"]: p for p in _load_yaml(DATA_DIR / "platforms.yaml").get("platforms", [])}
    models = _load_yaml(DATA_DIR / "models.yaml").get("models", {})
    return {"categories": categories, "styles": styles, "platforms": platforms, "models": models}


def reload() -> None:
    _data.cache_clear()
    _presets.cache_clear()


def categories() -> list[dict[str, Any]]:
    return list(_data()["categories"].values())


def category(cid: str) -> dict[str, Any]:
    try:
        return _data()["categories"][cid]
    except KeyError:
        raise KeyError(f"unknown category {cid!r}; known: {sorted(_data()['categories'])}")


def styles() -> list[dict[str, Any]]:
    return list(_data()["styles"].values())


def style(sid: str) -> dict[str, Any]:
    try:
        return _data()["styles"][sid]
    except KeyError:
        raise KeyError(f"unknown style {sid!r}; known: {sorted(_data()['styles'])}")


def platforms() -> list[dict[str, Any]]:
    return list(_data()["platforms"].values())


def platform(pid: str) -> dict[str, Any]:
    try:
        return _data()["platforms"][pid]
    except KeyError:
        raise KeyError(f"unknown platform {pid!r}; known: {sorted(_data()['platforms'])}")


def models() -> dict[str, dict[str, Any]]:
    return _data()["models"]


MANIFEST_MODEL_KEYS = ("name", "file", "licence", "version")


def model_record(mid: str) -> dict[str, Any]:
    """The model as the export manifest records it: id, name, file, licence, version."""
    m = models().get(mid)
    if m is None:
        return {"id": mid, "name": mid, "licence": "unknown", "version": ""}
    return {"id": mid, **{k: m[k] for k in MANIFEST_MODEL_KEYS if k in m}}


def concept_models() -> tuple[str, ...]:
    """Ids of the concept painters, in models.yaml order."""
    return tuple(mid for mid, m in models().items() if m.get("concept"))


def concept_model(mid: str) -> dict[str, Any]:
    """A concept model's models.yaml entry with its id. Raises KeyError for an id that is not a concept model."""
    m = models().get(mid)
    if m is None or not m.get("concept"):
        raise KeyError(f"unknown concept model {mid!r}; known: {list(concept_models())}")
    return {"id": mid, **m}


def model_label(mid: str | None) -> str:
    """The short app label of a model, or the id when models.yaml has none."""
    if not mid:
        return ""
    m = models().get(mid) or {}
    return str(m.get("label") or m.get("name") or mid)


def models_public() -> list[dict[str, Any]]:
    """GET /api/models: the concept painters with what the app shows about them."""
    out = []
    for mid in concept_models():
        m = models()[mid]
        out.append({"id": mid, "label": model_label(mid), "description": m.get("description", ""), "name": m.get("name", mid),
                    "licence": m.get("licence", "unknown"), "version": m.get("version", ""), "default_steps": m.get("default_steps"),
                    "negative": bool(m.get("negative"))})
    return out


def _schema(stage: str) -> list[dict[str, Any]]:
    """STAGE_SCHEMAS[stage] with live option lists (the concept model knob reads models.yaml) and, on size_m, the
    per-category label, hint, default, axis and axis word from the category files (`by_category`)."""
    out = []
    for entry in STAGE_SCHEMAS[stage]:
        if stage == "concept" and entry["name"] == "model":
            entry = {**entry, "options": list(concept_models())}
        if stage == "prep" and entry["name"] == "size_m":
            entry = {**entry, "by_category": {cid: size_spec(cid) for cid in entry.get("categories") or []}}
        out.append(entry)
    return out


def stages() -> list[dict[str, Any]]:
    from . import guidance
    out = []
    for s in STAGE_ORDER:
        g = guidance.stage_guide(s)
        schema = []
        for entry in _schema(s):
            ph = guidance.param_help(entry["name"])
            item = {**entry, "help": ph["help"], "simple": ph["simple"]}
            labels = ph.get("options")
            if labels and entry["type"] in ("select", "multiselect"):
                item["option_labels"] = {str(k): v for k, v in labels.items()}
            schema.append(item)
        out.append({"id": s, "label": STAGE_LABELS[s], "plain_name": g.get("plain_name", STAGE_LABELS[s]),
                    "plain_verb": g.get("plain_verb", "Run"), "help": g.get("help", {}),
                    "estimate_seconds": g.get("estimate_seconds", 60), "adjust_schema": schema})
    return out


def stage_defaults(stage: str) -> dict[str, Any]:
    return {p["name"]: p["default"] for p in STAGE_SCHEMAS[stage]}


def budget(category_id: str, platform_id: str) -> dict[str, int]:
    """Triangle and texture budget for a category on a platform, falling back to the platform table."""
    c = category(category_id)
    b = (c.get("budgets") or {}).get(platform_id)
    if b:
        return {"triangles": int(b.get("triangles", 0)), "texture": int(b.get("texture", 2048))}
    p = platform(platform_id)
    is_character = c.get("rig_engine", "none") != "none"
    return {"triangles": int(p["triangles_character"] if is_character else p["triangles_prop"]), "texture": int(p["texture"])}


@lru_cache(maxsize=1)
def _presets() -> tuple[dict[str, Any], ...]:
    return tuple(_load_yaml(DATA_DIR / "style_presets.yaml") or [])


def style_presets() -> list[dict[str, Any]]:
    """Ready-made looks that fill a project's style lock (docs/UX-REVIEW.md item H)."""
    return [dict(p) for p in _presets()]


# --- Size along the category's axis (G12) and from the description (light G13) --------------------------------

SIZE_AXES = ("height", "horizontal", "longest")
AXIS_WORD_DEFAULT = {"height": "tall", "horizontal": "long", "longest": "long"}


def size_spec(category_id: str) -> dict[str, Any]:
    """How a category is sized: axis (height: the up extent; horizontal: the larger ground-plane extent; longest: the
    largest of all three), the default size in metres, the axis word for sentences, and the knob label and hint."""
    c = category(category_id)
    axis = c.get("size_axis") if c.get("size_axis") in SIZE_AXES else "height"
    return {"axis": axis, "default": float(c.get("size_m") or 1.0), "word": c.get("size_word") or AXIS_WORD_DEFAULT[axis],
            "label": c.get("size_label") or "How big is it?", "hint": c.get("size_hint") or "In metres."}


_NUMBER_WORDS = {w: i for i, w in enumerate(("one two three four five six seven eight nine ten eleven twelve thirteen "
                                             "fourteen fifteen sixteen seventeen eighteen nineteen twenty").split(), start=1)}
_UNITS = {"m": 1.0, "metre": 1.0, "metres": 1.0, "meter": 1.0, "meters": 1.0, "cm": 0.01, "ft": 0.3048, "foot": 0.3048, "feet": 0.3048}
_AXIS_WORDS = {"tall": ("height", "tall"), "high": ("height", "tall"), "long": ("horizontal", "long"),
               "wide": ("horizontal", "across"), "across": ("horizontal", "across")}
# Longer number words first so "seventeen" is not read as "seven".
_SIZE_RE = re.compile(
    r"(?<![\w.])(?P<num>\d+(?:\.\d+)?|" + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True)) + r")\s*-?\s*"
    r"(?P<unit>metres|metre|meters|meter|feet|foot|cm|ft|m)\b(?:\s*-?\s*(?P<axis>tall|high|long|wide|across)\b)?",
    re.IGNORECASE)


def parse_size(text: str | None, default_m: float | None = None) -> dict[str, Any] | None:
    """A size written in a description ("two metres wide", "6 ft tall", "4.5 m long"): {size_m, axis, word, text}, or
    None. Digits or the words one to twenty; units m, metre(s), meter(s), cm, ft, foot, feet; axis words tall or high
    (height), long, wide or across (horizontal), and nothing else. A size with an axis word wins over one without;
    axis and word are None when no axis word follows. Sizes outside 0.01 to 100 m are ignored. With default_m (the
    category size), a size without an axis word is ignored unless it is within a factor of four of it, so a detail
    such as "2 cm iron bands" does not size the whole chest."""
    found = []
    for m in _SIZE_RE.finditer(text or ""):
        raw = m.group("num").lower()
        n = float(_NUMBER_WORDS[raw]) if raw in _NUMBER_WORDS else float(raw)
        metres = round(n * _UNITS[m.group("unit").lower()], 3)
        if not 0.01 <= metres <= 100:
            continue
        axis, word = _AXIS_WORDS.get((m.group("axis") or "").lower(), (None, None))
        if axis is None and default_m and not default_m / 4 <= metres <= default_m * 4:
            continue
        found.append({"size_m": metres, "axis": axis, "word": word, "text": m.group(0)})
    if not found:
        return None
    return next((f for f in found if f["axis"]), found[0])
