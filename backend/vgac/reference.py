"""Reference data: categories, styles, platforms, models, stage adjust schemas.

Loaded once from backend/vgac/data and served by /api/categories, /api/styles, /api/platforms
and /api/stages. Call reload() after editing the YAML files while the server runs.
"""

from __future__ import annotations

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
CONCEPT_MODELS = ("zturbo", "zbase", "klein", "anima", "pixel", "chroma")
TURNAROUND_VIEWS = ("front", "left", "back", "right", "quarter", "top")


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
        _param("model", "select", "zturbo", options=list(CONCEPT_MODELS), description="Concept engine; the project style lock or style preset decides when empty"),
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
        _param("remesh", "boolean", True, description="Watertight remesh before decimation"),
    ],
    "prep": [
        _param("height_m", "number", None, min=0.01, max=100.0, description="Category size when empty"),
        _param("symmetric", "boolean", None, description="Category default when empty"),
        _param("pivot", "select", "base", options=["base", "center"]),
        _param("smooth_angle", "number", 60.0, min=0.0, max=180.0),
    ],
    "rig": [
        _param("template", "select", "auto", options=["auto", "mixamo", "generic", "none"]),
        _param("target_faces", "integer", 0, min=0, max=5000000, description="0 keeps the mesh as is"),
        _param("group_per_vertex", "integer", 4, min=1, max=8),
        _param("bottom_center_origin", "boolean", True),
        _param("bend_test", "boolean", True, description="Render bend tests at 35 and 90 degrees"),
    ],
    "export": [
        _param("lods", "string", None, description="LOD ratios after LOD0, comma separated, e.g. 0.5, 0.25, 0.1; category default when empty"),
        _param("collider", "select", "hull", options=["hull", "none"]),
        _param("formats", "multiselect", ["fbx", "glb"], options=["fbx", "glb"]),
        _param("unity", "boolean", True, description="Import into the Unity sandbox and build the prefab"),
        _param("clips_library", "select", "ual1", options=["ual1", "ual2", "none"]),
        _param("sprites", "boolean", False),
        _param("sprite_directions", "integer", 8, min=1, max=16),
        _param("sprite_size", "integer", 128, min=32, max=1024),
    ],
}


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


def model_record(mid: str) -> dict[str, Any]:
    m = models().get(mid)
    if m is None:
        return {"id": mid, "name": mid, "licence": "unknown", "version": ""}
    return {"id": mid, **m}


def stages() -> list[dict[str, Any]]:
    from . import guidance
    out = []
    for s in STAGE_ORDER:
        g = guidance.stage_guide(s)
        schema = []
        for entry in STAGE_SCHEMAS[s]:
            ph = guidance.param_help(entry["name"])
            schema.append({**entry, "help": ph["help"], "simple": ph["simple"]})
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


def style_presets() -> list[dict[str, Any]]:
    """Ready-made looks that fill a project's style lock (docs/UX-REVIEW.md item H)."""
    import yaml
    path = DATA_DIR / "style_presets.yaml"
    return list(yaml.safe_load(path.read_text(encoding="utf-8")) or [])
