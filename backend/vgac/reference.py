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

# Knobs that apply to some categories only carry `categories` (docs/GREAT-ASSETS-PLAN.md 2.3). Material has no mesh
# to size, turn or clean. Environment pieces are sized by the grid (G15, plan 3.8): they get the grid knobs instead
# of the size knob, and their pivot is always the minimum corner, so the pivot knob leaves them out too.
SURFACE_CATEGORIES = ["humanoid", "quadruped", "winged", "multileg", "serpent", "vehicle", "prop_rigid", "prop_articulated",
                      "environment", "foliage"]
SIZE_CATEGORIES = [c for c in SURFACE_CATEGORIES if c != "environment"]
FACING_CATEGORIES = list(SURFACE_CATEGORIES)
GRID_CATEGORIES = ["environment"]
PIVOT_CATEGORIES = SIZE_CATEGORIES + ["material"]
# Grid choices (G15, plan 3.8 B). Whole numbers stay ints so the option labels' keys read "1", not "1.0".
GRID_M_OPTIONS: list[Any] = [0.5, 1, 2, 4]
GRID_FOOTPRINT_OPTIONS: list[Any] = ["auto", "1x1", "2x1", "2x2", "4x1", "4x4"]
GRID_HEIGHT_OPTIONS: list[Any] = ["auto", 0.5, 1, 3, 4]
# The prep turn choices, in degrees counter-clockwise seen from above ("auto" lets prep decide from the mesh audit).
FACE_YAW_OPTIONS: list[Any] = ["auto", 0, 90, 180, 270]
# Painted-on shadows (G07): auto follows the style.
DELIGHT_OPTIONS = ("auto", "remove", "keep")
# Recipes: an add-on's three strength steps, plus "off"; the prep shading choice (auto follows the recipe).
STRENGTH_NAMES = ("subtle", "normal", "strong")
ADDON_CHOICES = ("off",) + STRENGTH_NAMES
SHADING_OPTIONS = ("auto", "smooth", "flat")


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
        # {add-on id: off | subtle | normal | strong}; ids missing here keep the recipe and style lock choice. The
        # schema read (_schema) adds `catalog`: the concept add-ons with label, help, strengths and base models.
        _param("addons", "addons", None, label="LoRAs (style add-ons)", options=list(ADDON_CHOICES),
               description="The recipe and the style lock decide when empty"),
    ],
    "turnaround": [
        _param("views", "multiselect", ["front", "left", "back", "right", "quarter"], options=list(TURNAROUND_VIEWS)),
        # stages/turnaround.py TURN_METHODS: the standard way for every category, one more way for characters and
        # rigid props (options_by_category); the categories without a second way do not show the knob.
        _param("turn_method", "select", "standard", label="How to turn the views", options=["standard", "sheet", "shadow_free"],
               categories=["humanoid", "prop_rigid"],
               options_by_category={"humanoid": ["standard", "sheet"], "prop_rigid": ["standard", "shadow_free"]}),
        _param("lora_strength", "number", 0.9, min=0.0, max=1.5, step=0.05),
        _param("seed", "integer", None, min=0, max=2**31 - 1),
        _param("auto_reroll", "boolean", True, description="Re-roll a failing view up to 2 times"),
    ],
    "mesh": [
        _param("engine", "select", "trellis", options=["trellis", "hy3d"]),
        _param("input_view", "select", "concept", options=["concept", "front", "quarter"]),
        _param("target_faces", "integer", None, min=200, max=500000, description="The recipe's share of the category and platform budget when empty"),
        _param("texture_size", "select", None, options=[256, 512, 1024, 2048, 4096], description="The recipe's texture size (never over the platform's) when empty"),
        _param("upsample", "select", 1024, options=[1024, 1536]),
        _param("tex_steps", "select", 24, options=[12, 24]),
        # The head built on its own (vgac/headgraft.py): people on the bald route (hair from the library) only.
        _param("head_build", "boolean", None, categories=["humanoid"], label="Build the head on its own",
               description="With hair from the library: a second build from a close crop of the head, joined at the neck; about 5 minutes more. On when empty"),
        _param("head_crop", "number", None, min=0.18, max=0.4, step=0.02, categories=["humanoid"], label="Head crop (share of the height)",
               description="How much of the figure, from the top, the head build sees; 0.26 when empty"),
        _param("seed", "integer", None, min=0, max=2**31 - 1),
        _param("remesh", "boolean", None, description="Watertight remesh before decimation; category default when empty (off for scenery so doorways stay open)"),
        # G03: UnwrapMesh accepts 0 to 16 texels; the plan's 64 is beyond the node.
        _param("padding", "integer", None, min=1, max=16, label="UV padding (seam space, in pixels)",
               description="Automatic when empty: 4 px at 1024, 8 at 2048, 16 at 4096"),
    ],
    "prep": [
        # height_m (the old name) is still read from older versions as an alias meaning the height.
        _param("size_m", "number", None, min=0.01, max=100.0, step=0.01, categories=SIZE_CATEGORIES,
               description="In metres along the category's size axis; the category size, or a size in the description, when empty"),
        _param("face_yaw", "select", "auto", options=FACE_YAW_OPTIONS, categories=FACING_CATEGORIES,
               description="Pick the picture where the front faces you. The game treats that side as forward."),
        # G15: scenery is rounded to the grid; footprint and height are in the simple layer, the grid size in Advanced.
        _param("grid_footprint", "select", "auto", label="Size on the grid", options=GRID_FOOTPRINT_OPTIONS, categories=GRID_CATEGORIES,
               description="Automatic rounds each side to the nearest whole square; a choice sets the size in squares."),
        _param("grid_height", "select", "auto", label="How tall is it?", options=GRID_HEIGHT_OPTIONS, categories=GRID_CATEGORIES,
               description="Automatic rounds the height to the nearest half square."),
        _param("grid_m", "select", 1, label="Grid size", options=GRID_M_OPTIONS, categories=GRID_CATEGORIES,
               description="The side of one grid square in metres; use the same grid for every piece in a set."),
        _param("symmetric", "boolean", False, categories=SURFACE_CATEGORIES,
               description="Off unless turned on; mirroring makes prep paint the textures again (G05)"),
        _param("pivot", "select", "base", options=["base", "center"], categories=PIVOT_CATEGORIES),
        _param("smooth_angle", "number", 60.0, min=0.0, max=180.0),
        _param("shading", "select", "auto", label="Shading", options=list(SHADING_OPTIONS), categories=SURFACE_CATEGORIES,
               description="Automatic follows the recipe: flat for low-poly, smooth for the rest"),
        # G07, G08: the style decides when empty (data/styles.yaml delight_default and pbr_fix_default).
        _param("delight", "select", "auto", options=list(DELIGHT_OPTIONS), categories=SURFACE_CATEGORIES, label="Delight (remove painted-on shadows)"),
        _param("pbr_fix", "boolean", None, categories=SURFACE_CATEGORIES, label="PBR range check (realistic colour range)",
               description="On for realistic styles, off for others, when empty"),
        _param("rebake", "boolean", False, categories=SURFACE_CATEGORIES, label="Rebake (paint the surface again)",
               description="Prep repaints by itself when mirroring or shrinking changes the shape"),
        # The picture painted into the base colour (vgac/srcproject.py). Empty: by itself for a character on a smooth
        # look. On: any model. Off: never.
        _param("source_project", "boolean", None, categories=SURFACE_CATEGORIES, label="Paint the picture onto the model",
               description="Puts your picture's own colours and detail on the side it shows. When empty: on for characters"),
        # The face detail pass (stages/face_detail.py): humanoids only; skipped by itself for crisp and flat looks.
        _param("face_detail", "boolean", True, categories=["humanoid"], label="Match the face to the picture",
               description="Paints the face from the picture so the eyes and mouth are sharp; skipped when there is no face to paint"),
        _param("face_strength", "number", 1.0, min=0.0, max=1.0, step=0.1, categories=["humanoid"], label="Face strength",
               description="How much of the painted face goes onto the model: 1 is all of it"),
        _param("face_density", "select", "auto", options=["auto", "standard", "high"], categories=["humanoid"],
               label="Face texel density (face sharpness)",
               description="Automatic picks High where the platform's texture allows it and the picture has the detail, else Standard"),
        _param("face_closeup", "boolean", True, categories=["humanoid"], label="Close-up source (a larger picture of the face)",
               description="When the face in the picture is small, draw a close-up of it and use it if it is still the same face"),
        _param("face_closeup_guided", "boolean", True, categories=["humanoid"], label="Guide the close-up by your face picture (optional)",
               description="With your own close-up, or a face picture in step 0.5, the close-up is drawn with that face's features; off draws it from the painting alone"),
        _param("face_sides", "boolean", True, categories=["humanoid"], label="Paint the cheeks from the side views (optional)",
               description="Hides the seam where the front painting ends on the cheek; turn off if it leaves smudges by the ears or neck"),
        # The hair detail pass (stages/hair_detail.py): humanoids; skipped by itself for crisp and flat looks.
        _param("hair_detail", "boolean", True, categories=["humanoid"], label="Hair detail (strands and shading painted on)",
               description="Paints strands, clumps and shading along the hair; skipped when the hair cannot be told from skin or clothes"),
        _param("hair_strength", "number", 1.0, min=0.0, max=1.0, step=0.1, categories=["humanoid"], label="Hair strength",
               description="How strongly the hair detail is painted: 1 is full, 0 changes nothing"),
        # Hair as its own material (vgac/hairmat.py): automatic on PC and Showcase budgets, off on Phone.
        _param("hair_material", "select", "auto", options=["auto", "on", "off"], categories=["humanoid"],
               label="Hair material (its own texture and shader)",
               description="The hair gets a second material with its own texture, so it is sharper and shines like hair in Unity; costs one more draw call. Automatic: on for PC and Showcase, off for Phone"),
        # More triangles on the head (vgac/headtris.py): humanoids in looks with smooth shading and the full budget.
        _param("head_triangles", "boolean", True, categories=["humanoid"], label="Weighted decimation (more triangles on the head)",
               description="When the 3D model was built with room for it, the head keeps about twice its share of the triangles and the body gives them up; the total stays inside the budget"),
    ],
    "rig": [
        _param("template", "select", "auto", options=["auto", "mixamo", "generic", "none"]),
        _param("target_faces", "integer", 0, min=0, max=5000000, description="0 keeps the mesh as is"),
        _param("group_per_vertex", "integer", 4, min=1, max=8),
        _param("bottom_center_origin", "boolean", True),
        _param("bend_test", "boolean", True, description="Render bend tests at 35 and 90 degrees"),
        _param("motion", "select", None, options=["legs", "wings", "wave", "none"], categories=["quadruped", "multileg", "winged", "serpent"],
               description="From the category when empty"),
        _param("moves", "multiselect", None, options=["idle", "walk", "run", "turn", "attack", "hit", "death", "jump", "crouch", "roll", "interact",
                                                      "flap", "glide", "takeoff", "land", "slither"],
               categories=["humanoid", "quadruped", "multileg", "winged", "serpent"], description="Every move of the category when empty"),
        _param("clips_source", "select", "auto", options=["auto", "library", "procedural"], categories=["humanoid"],
               description="Automatic uses the hand-made library for human-shaped characters when it is installed"),
        _param("move_size", "select", "normal", options=["small", "normal", "big"], categories=["humanoid", "quadruped", "multileg", "winged", "serpent"]),
        _param("move_speed", "select", "normal", options=["slow", "normal", "fast"], categories=["humanoid", "quadruped", "multileg", "winged", "serpent"]),
        _param("hitboxes", "select", None, options=["detailed", "simple", "none"], categories=["humanoid", "quadruped", "multileg", "winged", "serpent"],
               description="detailed on PC and Showcase, simple on Phone when empty"),
        _param("hitbox_fit", "select", "normal", options=["snug", "normal", "loose"], categories=["humanoid", "quadruped", "multileg", "winged", "serpent"]),
        _param("hitbox_regions", "multiselect", None,
               options=["head", "neck", "chest", "hips", "body", "tail", "upper_arm_l", "forearm_l", "upper_arm_r", "forearm_r", "thigh_l", "shin_l",
                        "thigh_r", "shin_r", "leg_fl", "leg_fr", "leg_bl", "leg_br", "leg_l", "leg_r", "wing_l", "wing_r", "wings"],
               categories=["humanoid", "quadruped", "multileg", "winged", "serpent"], description="Every region found when empty"),
        # Spring bones (vgac/springs.py): humanoids; parts that hang clear of the body sway in every move.
        _param("part_legs", "boolean", True, categories=["humanoid"], label="Part legs joined near the feet",
               description="Where the 3D build joined the legs low down (a strand between the shoes), split them so they move apart; a tabard or cape between the legs is left whole"),
        _param("springs", "boolean", True, categories=["humanoid"], label="Spring bones (small extra bones that sway on their own)",
               description="A ponytail, braid, cape or tail that hangs clear of the body gets a short chain of bones and sways in every move; hair resting on the shoulders and rigid things like a scabbard are left alone"),
        _param("head_sway", "select", "auto", options=["auto", "hair", "all"], categories=["humanoid"],
               label="What may sway on the head",
               description="Automatic: only hair sways, and a headscarf, ribbon or veil named in the description; a hat's brim, a hood or a helmet moves with the head. Hair only, or everything that hangs (as before)"),
    ],
    "export": [
        _param("lods", "string", None, description="LOD ratios after LOD0, comma separated, e.g. 0.5, 0.25, 0.1; category default when empty"),
        _param("collider", "select", "auto", label="Collider (collision shape)", options=["auto", "hull", "decompose", "mesh", "none"],
               categories=["prop_rigid", "prop_articulated", "vehicle", "environment", "foliage"],
               description="The invisible shapes the game uses to know where this is solid."),
        _param("collider_max_parts", "integer", None, label="Max collider parts (most shapes)", min=1, max=32,
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
            # A knob whose choices depend on the kind of asset (turn_method) offers only that category's.
            if category and (p.get("options_by_category") or {}).get(category):
                opts = p["options_by_category"][category]
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
        elif t == "addons":
            # {add-on id: off | subtle | normal | strong}. Ids the registry does not know are kept here and reported
            # (not applied) by resolve_addons, so an add-on removed from addons.yaml never breaks an older version.
            opts = p.get("options") or list(ADDON_CHOICES)
            if not isinstance(value, dict):
                raise ValueError(f"{key} must map each LoRA to one of {opts}")
            clean: dict[str, str] = {}
            for aid, choice in value.items():
                if not isinstance(aid, str) or not aid or len(aid) > 100:
                    raise ValueError(f"{key} has a LoRA id that is not text")
                if not isinstance(choice, str) or choice.lower() not in opts:
                    raise ValueError(f"{key}: {aid} must be one of {opts}")
                clean[aid] = choice.lower()
            out[key] = clean
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
    addon_path = DATA_DIR / "addons.yaml"
    addon_list = (_load_yaml(addon_path).get("addons") or []) if addon_path.is_file() else []
    addons_ = {a["id"]: a for a in addon_list if isinstance(a, dict) and a.get("id")}
    return {"categories": categories, "styles": styles, "platforms": platforms, "models": models, "addons": addons_}


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
        if stage == "concept" and entry["name"] == "addons":
            entry = {**entry, "catalog": [addon_public(a) for a in addons() if a.get("stage") == "concept"]}
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


# --- Add-ons (data/addons.yaml, plus the person's own in user_addons.py) --------------------------------------------
#
# The registry is the only list of add-ons: the tested ones in data/addons.yaml, then the person's own from
# <library_path>/my_addons.json (user_addons.entries(), each `mine: true`). A user entry whose id is a built-in one is
# dropped, so it never shadows a built-in add-on. Nothing in the app lists a models folder except user_addons.py,
# which lists only its own VGAC/mine/ folder. An add-on applies only when the chosen model is in its base_models.


def _user_addons() -> dict[str, dict[str, Any]]:
    """The person's own add-ons by id, without any whose id is a built-in one (empty when no settings load)."""
    from . import user_addons

    try:
        items = user_addons.entries()
    except (OSError, ValueError, KeyError):
        return {}
    builtin = _data()["addons"]
    return {a["id"]: a for a in items if a["id"] not in builtin}


def _mine_file_present(a: dict[str, Any]) -> bool:
    """Whether a user add-on's file is still in VGAC/mine/ (the only folder this checks)."""
    from . import user_addons

    try:
        return (user_addons.mine_dir() / str(a.get("file_name") or "")).is_file() and bool(a.get("file_name"))
    except (OSError, ValueError):
        return False


def addons() -> list[dict[str, Any]]:
    return list(_data()["addons"].values()) + list(_user_addons().values())


def addon(aid: str) -> dict[str, Any] | None:
    return _data()["addons"].get(aid) or _user_addons().get(aid)


def addon_required(a: dict[str, Any], model: str | None = None) -> bool:
    """True when the step cannot run without it: `required`, or the model is in its `required_for`."""
    return bool(a.get("required")) or (model is not None and model in (a.get("required_for") or []))


def addon_strength(a: dict[str, Any], name: str) -> float:
    """The strength a step name loads the add-on at (an unknown name reads as the add-on's default step)."""
    s = a.get("strengths") or {}
    if name not in s:
        name = a.get("default_strength") or "normal"
    return float(s.get(name, 1.0))


def addon_public(a: dict[str, Any], model: str | None = None) -> dict[str, Any]:
    """What the app shows about an add-on: id, label, help, stage, base_models, strengths, default_strength,
    required (for `model` when given, else always-required only), required_for, trigger, licence, source. The file
    name is not shown."""
    return {"id": a["id"], "label": a.get("label") or a["id"], "help": a.get("help") or {}, "stage": a.get("stage", "concept"),
            "base_models": list(a.get("base_models") or []), "strengths": dict(a.get("strengths") or {}),
            "default_strength": a.get("default_strength") or "normal", "required": addon_required(a, model),
            "required_for": list(a.get("required_for") or []), "trigger": a.get("trigger") or "",
            "licence": a.get("licence") or "unknown", "source": a.get("source") or "", "mine": bool(a.get("mine")),
            "turn_method": a.get("turn_method")}


def addon_record(aid: str) -> dict[str, Any]:
    """The add-on as the export manifest records it: id, name, file, licence, source (and mine: true for the
    person's own, whose source is the "Where it came from" they typed)."""
    a = addon(aid) or {}
    out = {"id": aid, "name": a.get("label") or aid, "file": a.get("file") or "", "licence": a.get("licence") or "unknown",
           "source": a.get("source") or "", "kind": "addon"}
    if a.get("mine"):
        out["mine"] = True
    return out


def _parse_strength(value: Any) -> tuple[str, float | None] | None:
    """A strength as the style lock or a recipe writes it: a step name, or a number from 0 to 2."""
    if value is None or value == "":
        return None
    if isinstance(value, str) and value.lower() in ADDON_CHOICES:
        return value.lower(), None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return ("custom", f) if 0.0 <= f <= 2.0 else None


def parse_lock_addons(items: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """The project style lock's `loras`: each an id, "id:strength" (a step name or a number), or {id, strength}.
    -> ([{id, strength, value}], unknown) where unknown lists entries that are not in the registry (or unreadable)."""
    out: list[dict[str, Any]] = []
    unknown: list[str] = []
    for item in items or []:
        aid, strength = None, None
        if isinstance(item, dict):
            aid, strength = item.get("id"), item.get("strength")
        elif isinstance(item, str):
            aid, _, strength = item.strip().partition(":")
            strength = strength.strip() or None
        if not isinstance(aid, str) or not aid.strip() or addon(aid.strip()) is None:
            unknown.append(str(aid if aid else item)[:100])
            continue
        parsed = _parse_strength(strength) if strength is not None else None
        name, value = parsed if parsed else (None, None)
        out.append({"id": aid.strip(), "strength": name, "value": value})
    return out, unknown


def resolve_addons(model: str, stage: str, defaults: list[dict[str, Any]] | None = None, lock: Any = None,
                   choices: dict[str, str] | None = None) -> dict[str, Any]:
    """The add-ons a step loads, in order: the recipe defaults, then the style lock's `loras`, then the version's
    `addons` choices ({id: off | subtle | normal | strong}). Later sources change the strength of an earlier entry or
    add one; "off" removes it unless it is required. Only add-ons for this stage whose base_models hold `model` apply.
    Required add-ons for this model are always there. ->
    {applied: [{id, label, strength (step name or "custom"), value, file, trigger, built_in, required}],
     ignored: [{id, reason}], unknown: [ids from the lock or the choices that the registry does not know]}"""
    order: list[str] = []
    picked: dict[str, dict[str, Any]] = {}
    ignored: list[dict[str, Any]] = []
    lock_items, unknown = parse_lock_addons(lock)

    def put(aid: str, name: str | None, value: float | None, source: str) -> None:
        a = addon(aid)
        if a is None:
            if aid not in unknown:
                unknown.append(aid)
            return
        if a.get("stage", "concept") != stage:
            return
        if model not in (a.get("base_models") or []):
            ignored.append({"id": aid, "reason": f"does not work with {model_label(model) or model}", "source": source})
            picked.pop(aid, None)
            return
        if a.get("mine") and name != "off" and not _mine_file_present(a):
            # Never send ComfyUI a file it does not have: its "not in list" error would name every LoRA file.
            ignored.append({"id": aid, "reason": "its file is no longer in the LoRA folder", "source": source})
            picked.pop(aid, None)
            return
        if name == "off":
            if addon_required(a, model):
                ignored.append({"id": aid, "reason": "required, cannot be switched off", "source": source})
                return
            picked.pop(aid, None)
            return
        if aid not in order:
            order.append(aid)
        name = name or (picked.get(aid) or {}).get("strength") or a.get("default_strength") or "normal"
        picked[aid] = {"strength": name, "value": value if value is not None else addon_strength(a, name), "source": source}

    for a in addons():
        if a.get("stage", "concept") == stage and model in (a.get("base_models") or []) and addon_required(a, model):
            put(a["id"], None, None, "required")
    for d in defaults or []:
        parsed = _parse_strength(d.get("strength"))
        put(d["id"], *(parsed or (None, None)), "recipe")
    for item in lock_items:
        put(item["id"], item["strength"], item["value"], "style lock")
    for aid, choice in (choices or {}).items():
        parsed = _parse_strength(choice)
        if parsed is None:
            continue
        put(aid, parsed[0], parsed[1], "version")
    applied = []
    for aid in order:
        if aid not in picked:
            continue
        a = addon(aid) or {}
        p = picked[aid]
        applied.append({"id": aid, "label": a.get("label") or aid, "strength": p["strength"], "value": round(float(p["value"]), 4),
                        "file": a.get("file") or "", "trigger": a.get("trigger") or "", "built_in": model in (a.get("built_in") or []),
                        "required": addon_required(a, model), "source": p["source"]})
    return {"applied": applied, "ignored": ignored, "unknown": unknown}


# --- Recipes: the chosen look carried through every step ------------------------------------------------------------
#
# reference.recipe(style, category, platform) resolves a style's `recipe` (styles.yaml), merged with its by_category
# override, over RECIPE_DEFAULTS (today's behaviour), and turns the shares into numbers for the platform budget.

RECIPE_DEFAULTS: dict[str, Any] = {
    "concept": {"steps": None, "negative": ""},
    "addons": [],
    "turnaround": {"lora_strength": None, "prompt_suffix": ""},
    "mesh": {"triangle_scale": 1.0, "triangle_floor": 0, "texture_cap": None, "normal_map": True, "upsample": 1024},
    "surface": {"shading": "smooth", "texture_filter": "smooth", "palette_colors": None, "pixel_snap": False},
    "engine": {},  # filled from surface.texture_filter (crisp: point, no mipmaps, no compression) unless the style sets it
}
SHADINGS = ("smooth", "flat")
TEXTURE_FILTERS = ("smooth", "crisp")
FILTER_MODES = ("bilinear", "point")
# The noun of the recipe's plain sentence, per category.
CATEGORY_NOUNS = {"humanoid": "character", "quadruped": "creature", "winged": "flying creature", "multileg": "creature",
                  "serpent": "creature", "vehicle": "vehicle", "prop_rigid": "prop", "prop_articulated": "prop",
                  "environment": "scenery piece", "foliage": "plant", "material": "material"}


def _deep_merge(base: dict[str, Any], over: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _as_choice(value: Any, options: tuple[str, ...], default: str) -> str:
    return value if value in options else default


def recipe(style_id: str, category_id: str, platform_id: str, strict: bool = True) -> dict[str, Any]:
    """The fully resolved recipe for a style, category and platform:
    {id (short digest), style, category, platform, plain, concept {model, steps, prompt_block, negative},
     addons [{id, strength}], turnaround {lora_strength, prompt_suffix}, mesh {triangle_scale, triangle_floor,
     triangles, budget_triangles, texture_cap, texture, budget_texture, normal_map}, surface {shading, texture_filter,
     palette_colors, pixel_snap, delight, pbr_fix}, engine {filter_mode, mipmaps, compress}}.
    With strict=False an unknown style reads as today's defaults with the Z-Image Turbo painter (a custom style id on
    an older asset); unknown categories and platforms always raise KeyError."""
    try:
        s = style(style_id)
    except KeyError:
        if strict:
            raise
        s = {"id": style_id, "label": style_id, "model": "zturbo", "prompt_block": ""}
    cat = category(category_id)
    budget_ = budget(category_id, platform_id)
    over = (s.get("by_category") or {}).get(category_id) or {}
    raw = _deep_merge(_deep_merge(RECIPE_DEFAULTS, s.get("recipe") or {}), over.get("recipe") or {})

    concept = {"model": s.get("model") or "zturbo", "steps": raw["concept"].get("steps"),
               "prompt_block": s.get("prompt_block") or "", "negative": " ".join(str(raw["concept"].get("negative") or "").split())}
    mesh_raw = raw["mesh"]
    scale = float(mesh_raw.get("triangle_scale") or 1.0)
    floor = int(mesh_raw.get("triangle_floor") or 0)
    tris_budget = int(budget_.get("triangles") or 0)
    tris = min(tris_budget, max(floor, int(round(tris_budget * scale)))) if tris_budget else None
    tex_budget = int(budget_.get("texture") or 2048)
    cap = mesh_raw.get("texture_cap")
    tex = min(int(cap), tex_budget) if cap else tex_budget
    mesh = {"triangle_scale": scale, "triangle_floor": floor, "triangles": tris, "budget_triangles": tris_budget or None,
            "texture_cap": int(cap) if cap else None, "texture": tex, "budget_texture": tex_budget,
            "normal_map": bool(mesh_raw.get("normal_map", True)),
            # Detail level of the 3D builder: 1536 is several times slower and only worth it for the Photoreal look.
            "upsample": 1536 if int(mesh_raw.get("upsample") or 1024) >= 1536 else 1024}
    sr = raw["surface"]
    crisp = sr.get("texture_filter") == "crisp"
    palette = sr.get("palette_colors")
    surface = {"shading": _as_choice(sr.get("shading"), SHADINGS, "smooth"),
               "texture_filter": "crisp" if crisp else "smooth",
               "palette_colors": int(palette) if palette else None,
               "pixel_snap": bool(sr.get("pixel_snap", crisp)),
               "delight": "remove" if s.get("delight_default") == "remove" else "keep",
               "pbr_fix": bool(s.get("pbr_fix_default", False))}
    er = raw["engine"] or {}
    engine = {"filter_mode": _as_choice(er.get("filter_mode"), FILTER_MODES, "point" if crisp else "bilinear"),
              "mipmaps": bool(er.get("mipmaps", not crisp)), "compress": bool(er.get("compress", not crisp))}

    defaults: list[dict[str, Any]] = []
    for d in raw.get("addons") or []:
        if isinstance(d, dict) and d.get("id") and addon(d["id"]) is not None:
            defaults.append({"id": d["id"], "strength": d.get("strength") or addon(d["id"]).get("default_strength") or "normal"})
    for a in addons():
        if addon_required(a, concept["model"]) and a.get("stage") == "concept" and concept["model"] in (a.get("base_models") or []) \
                or (a.get("required") and a.get("stage") == "turnaround"):
            if not any(d["id"] == a["id"] for d in defaults):
                defaults.append({"id": a["id"], "strength": a.get("default_strength") or "normal"})
    turn_raw = raw["turnaround"]
    lora = turn_raw.get("lora_strength")
    if lora is None:
        angles = addon("qwen_angles")
        pick = next((d["strength"] for d in defaults if d["id"] == "qwen_angles"), None)
        lora = addon_strength(angles, pick or "normal") if angles else 0.9
    turnaround = {"lora_strength": float(lora), "prompt_suffix": " ".join(str(turn_raw.get("prompt_suffix") or "").split())}

    look = s.get("look") or s.get("label") or style_id
    text = (over.get("plain") or s.get("plain") or "the full pipeline for your platform.").strip()
    noun = CATEGORY_NOUNS.get(category_id) or str(cat.get("label") or category_id).lower()
    plain = f"{look} {noun}: {text}"
    if not plain.endswith("."):
        plain += "."
    # Plain notes shown with the recipe: where the look falls short for this category, and the platform it needs.
    notes: list[str] = []
    caveat = " ".join(str(over.get("caveat") or "").split())
    if caveat:
        notes.append(caveat)
    wanted = s.get("recommend_platforms") or []
    if wanted and platform_id not in wanted and s.get("platform_note"):
        notes.append(" ".join(str(s["platform_note"]).split()))
    out = {"style": s["id"], "category": category_id, "platform": platform_id, "plain": plain, "notes": notes, "concept": concept,
           "addons": defaults, "turnaround": turnaround, "mesh": mesh, "surface": surface, "engine": engine}
    out["id"] = recipe_digest(out)
    return out


def default_recipe(style_id: str = "") -> dict[str, Any]:
    """Today's behaviour as a recipe, without a category or platform to size it (triangles and texture empty)."""
    d = RECIPE_DEFAULTS
    out = {"style": style_id or "", "category": None, "platform": None, "plain": "", "concept": {"model": "zturbo", "prompt_block": "", **d["concept"]},
           "addons": [{"id": a["id"], "strength": a.get("default_strength") or "normal"} for a in addons()
                      if a.get("required") and a.get("stage") == "turnaround"],
           "turnaround": {"lora_strength": addon_strength(addon("qwen_angles"), "normal") if addon("qwen_angles") else 0.9, "prompt_suffix": ""},
           "mesh": {**d["mesh"], "triangles": None, "budget_triangles": None, "texture": None, "budget_texture": None},
           "surface": {**d["surface"], "delight": "keep", "pbr_fix": False},
           "engine": {"filter_mode": "bilinear", "mipmaps": True, "compress": True}}
    out["id"] = recipe_digest(out)
    return out


def recipe_digest(r: dict[str, Any]) -> str:
    """A short hash of a resolved recipe (everything but its own id), so a version records exactly which one ran."""
    import hashlib
    import json

    body = {k: v for k, v in r.items() if k != "id"}
    return hashlib.sha1(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:12]


def recipe_record(r: dict[str, Any], *sections: str) -> dict[str, Any]:
    """What a version's metrics.recipe holds: id, style, category, platform, plain, and the named sections."""
    out = {k: r[k] for k in ("id", "style", "category", "platform", "plain")}
    for s in sections:
        out[s] = r[s]
    return out


def turn_methods_for(category_id: str | None) -> list[str]:
    """The ways of turning the views (turnaround turn_method) offered for a category: standard for every one."""
    p = next(x for x in STAGE_SCHEMAS["turnaround"] if x["name"] == "turn_method")
    return list((p.get("options_by_category") or {}).get(category_id or "") or ["standard"])


def recipe_public(style_id: str, category_id: str, platform_id: str) -> dict[str, Any]:
    """GET /api/recipe: the recipe plus `addon_entries`, every add-on that works with the recipe's painter or the
    turnaround (addon_public, with `on` and `strength` from the recipe defaults)."""
    r = recipe(style_id, category_id, platform_id)
    model = r["concept"]["model"]
    chosen = {d["id"]: d["strength"] for d in r["addons"]}
    entries = []
    turns = turn_methods_for(category_id)
    for a in addons():
        stage = a.get("stage", "concept")
        # A turnaround add-on tied to a way of turning the views is listed only where that way is offered.
        works = model in (a.get("base_models") or []) if stage == "concept" else (a.get("turn_method") or "standard") in turns
        if not works:
            continue
        e = addon_public(a, model if stage == "concept" else None)
        e["on"] = a["id"] in chosen
        e["strength"] = chosen.get(a["id"], e["default_strength"])
        entries.append(e)
    return {**r, "addon_entries": entries}


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
