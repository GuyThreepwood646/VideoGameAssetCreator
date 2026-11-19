"""The person's saved settings for each step of an automatic build, per platform and look (decision D6).

Stored in prefs.json as `step_defaults`: {"<platform id>|<look id>": {"<step id>": {"<setting>": value}}}. A saved set
applies only to assets with that exact platform and look. Every value is checked against the step's own settings
(`reference.STAGE_SCHEMAS`) before it is saved, so what is stored is always valid on its own.

Saved values never override the look's limits: when they are applied (`applied`), a triangle count above the look's
and platform's ceiling, or a texture size above the platform's, is brought down to it and a plain note says so. The
saved value itself is kept, so it comes back into force on a platform or look with more room.
"""

from __future__ import annotations

import math
import threading
from typing import Any

from . import prefs, reference
from .config import Settings

# Settings that make no sense as a standing default: a seed would repeat the same result every build.
NOT_SAVED = {"seed"}
_lock = threading.RLock()  # one save at a time: a save reads the stored sets, changes one, writes all


def key(platform: str, look: str) -> str:
    return f"{platform}|{look}"


def _check_ids(platform: str, look: str) -> None:
    try:
        reference.platform(platform)
        reference.style(look)
    except KeyError as e:
        raise ValueError(str(e.args[0])) from None


def clean_stage(stage: str, params: Any, allow_remove: bool = False) -> dict[str, Any]:
    """The step's settings checked against its schema. Raises ValueError with a plain message for an unknown step or
    setting, a value out of range or of the wrong kind, or a setting that cannot be a default."""
    if stage not in reference.STAGE_ORDER:
        raise ValueError(f"unknown step {stage!r}; known: {list(reference.STAGE_ORDER)}")
    if not isinstance(params, dict):
        raise ValueError(f"the settings for {stage} must be a set of name and value pairs")
    known = {p["name"]: p for p in reference._schema(stage)}
    for name, value in params.items():
        p = known.get(name)
        if p is None or p["type"] == "addons" or name in NOT_SAVED:
            raise ValueError(f"{name!r} is not a setting of step {stage!r} that can be saved")
        if value is None:
            if allow_remove:
                continue
            raise ValueError(f"{name} needs a value; leave a setting out to use the usual one")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{name} must be a number")
    return reference.validate_params(stage, params)  # a null passes through untouched (a removal)


def _limits(platform: str, look: str, category: str | None) -> dict[str, int]:
    if not category:
        return {}
    try:
        mesh = reference.recipe(look, category, platform)["mesh"]
    except KeyError:
        return {}
    out: dict[str, int] = {}
    if mesh.get("triangles"):
        out["target_faces"] = int(mesh["triangles"])
    # The size the 3D step builds at: the look's bake size where it has one, else its (platform-capped) texture.
    tex = mesh.get("bake_texture") or mesh.get("texture") or mesh.get("budget_texture")
    if tex:
        out["texture_size"] = int(tex)
    return out


def get(platform: str, look: str, category: str | None = None, settings: Settings | None = None) -> dict[str, Any]:
    """{platform, look, saved, applied, notes}: `saved` as stored, `applied` the same clamped to this look and
    platform's limits (for the category given), `notes` the plain sentences for every value that was brought down."""
    _check_ids(platform, look)
    saved = prefs.load(settings)["step_defaults"].get(key(platform, look), {})
    limits = _limits(platform, look, category)
    applied: dict[str, dict[str, Any]] = {}
    notes: list[str] = []
    for stage, params in saved.items():
        out = dict(params)
        for name, cap in (limits.items() if stage == "mesh" else ()):
            v = out.get(name)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > cap:
                out[name] = cap
                what = "triangle count" if name == "target_faces" else "texture size"
                notes.append(f"Your saved {what} ({int(v):,}) is more than this look and platform allow, so {cap:,} is used.")
        applied[stage] = out
    return {"platform": platform, "look": look, "saved": saved, "applied": applied, "notes": notes}


def save(platform: str, look: str, stages: dict[str, Any], settings: Settings | None = None) -> None:
    """Change the saved settings of the steps named, one setting at a time: a value is set, a null removes that
    setting, settings not named are kept (so a form that shows only some of a step's settings, or a saved value that
    was brought down on screen, is never lost by a save). Nothing is written if any value is refused."""
    _check_ids(platform, look)
    if not isinstance(stages, dict):
        raise ValueError("stages must map step ids to their settings")
    cleaned = {s: clean_stage(s, p, allow_remove=True) for s, p in stages.items()}
    with _lock:
        allp = prefs.load(settings)["step_defaults"]
        mine = dict(allp.get(key(platform, look), {}))
        for s, p in cleaned.items():
            merged = {**mine.get(s, {}), **p}
            merged = {k: v for k, v in merged.items() if v is not None}
            if merged:
                mine[s] = merged
            else:
                mine.pop(s, None)
        allp[key(platform, look)] = mine
        prefs.update(settings, step_defaults=allp)


def clear(platform: str, look: str, stage: str | None = None, settings: Settings | None = None) -> None:
    """Forget the saved settings of one step, or of every step when `stage` is None."""
    _check_ids(platform, look)
    if stage is not None and stage not in reference.STAGE_ORDER:
        raise ValueError(f"unknown step {stage!r}; known: {list(reference.STAGE_ORDER)}")
    with _lock:
        allp = prefs.load(settings)["step_defaults"]
        mine = allp.get(key(platform, look), {})
        if stage is None:
            mine = {}
        else:
            mine.pop(stage, None)
        allp[key(platform, look)] = mine
        prefs.update(settings, step_defaults=allp)
