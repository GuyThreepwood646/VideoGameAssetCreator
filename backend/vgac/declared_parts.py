"""Declared parts: the pieces a person names for a character, as structured entries (WP-20).

Two levels, both plain JSON:

- The asset declares its parts (db.Asset.parts, set on step 1 and in the asset's settings): a list of
  {name, kind}. A kind says what the part is for:
    separate  a piece that must move or swap on its own (a lid, a weapon). Today this only names the piece in the
              step 1 picture prompt; nothing rigs it.
    sway      a part that hangs and should sway (a cape hem, a ponytail, a tail, a scarf end). Humanoids only. The
              rig step's spring bones (blender/springs.py --declared) take its location when it has one.
- A finished rig version places them: its own parts.json, a list of {name, kind, location}. A location is a point on
  the model in its rest pose, in metres, in the rig GLB's own frame (glTF: Y up, the character facing +Z). The
  location lives on the version because a new mesh moves every point; a location is only ever read from the version
  it was set on.

Nothing here touches Blender. blender/springs.py reads the rig's declared list through rig.py, which calls
`for_springs` below.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

KINDS = ("separate", "sway", "stiff")
# Kinds that need a location to do anything in the rig step: sway (spring bones) and stiff (rigid with a spine bone).
LOCATED_KINDS = ("sway", "stiff")
MAX_POINTS = 8  # points per stiff part; each grows its own piece and the pieces join
# Kinds a category may use. Sway is for people-shaped characters; the spring pass only runs on humanoids.
CATEGORY_KINDS = {"humanoid": ("separate", "sway", "stiff")}
DEFAULT_KIND = "separate"
NAME_MAX = 40
COORD_MAX_M = 5.0
FILE = "parts.json"


class PartError(ValueError):
    """A declared part that is not allowed; the message is plain words for the person."""


def kinds_for(category: str | None) -> tuple[str, ...]:
    return CATEGORY_KINDS.get(str(category or ""), ("separate",))


def clean_name(name: Any) -> str:
    text = " ".join(str(name or "").split())
    if not text:
        raise PartError("A part needs a name.")
    if len(text) > NAME_MAX:
        raise PartError(f"A part name can be at most {NAME_MAX} characters: {text[:NAME_MAX]}...")
    return text


def clean_kind(kind: Any, category: str | None) -> str:
    k = str(kind or DEFAULT_KIND).strip().lower()
    if k not in KINDS:
        raise PartError(f"Unknown part kind '{k}'. Use one of: {', '.join(KINDS)}.")
    if k not in kinds_for(category):
        raise PartError(f"A '{k}' part is only for humanoid characters so far.")
    return k


def clean_location(loc: Any) -> list[float] | None:
    if loc is None:
        return None
    if not isinstance(loc, (list, tuple)) or len(loc) != 3:
        raise PartError("A location is three numbers: x, y and z in metres.")
    out = []
    for v in loc:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
            raise PartError("A location is three numbers: x, y and z in metres.")
        if abs(float(v)) > COORD_MAX_M:
            raise PartError(f"A location must be within {COORD_MAX_M:g} metres of the model's centre.")
        out.append(round(float(v), 4))
    return out


def normalize(items: Any, category: str | None) -> list[dict[str, str]]:
    """The asset's declared parts: [{name, kind}], names unique without regard to case. Raises PartError."""
    if items is None:
        return []
    if not isinstance(items, list):
        raise PartError("Parts must be a list.")
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for it in items:
        if isinstance(it, str):  # the old label form is not accepted
            raise PartError("Parts are named pieces with a kind now, for example {\"name\": \"cape\", \"kind\": \"sway\"}.")
        if not isinstance(it, dict):
            raise PartError("Each part is a name and a kind.")
        name = clean_name(it.get("name"))
        key = name.lower()
        if key in seen:
            raise PartError(f"The part '{name}' is listed twice.")
        seen.add(key)
        out.append({"name": name, "kind": clean_kind(it.get("kind"), category)})
    return out


def read_stored(raw: Any) -> list[dict[str, str]]:
    """The declared parts as stored (db.Asset.parts). Rows in the old label form are not read: they show as no parts."""
    if not isinstance(raw, list):
        return []
    return [{"name": str(r["name"]), "kind": str(r.get("kind") or DEFAULT_KIND)}
            for r in raw if isinstance(r, dict) and r.get("name")]


def placed(items: Any, declared: list[dict[str, str]], category: str | None) -> list[dict[str, Any]]:
    """A version's placed parts: [{name, kind, location}]. Each must be declared on the asset with the same kind, and
    a sway part with a location must be a humanoid's. Raises PartError."""
    if not isinstance(items, list):
        raise PartError("Placed parts must be a list.")
    have = {p["name"].lower(): p["kind"] for p in declared}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for it in items:
        if not isinstance(it, dict):
            raise PartError("Each placed part is a name, a kind and a location.")
        name = clean_name(it.get("name"))
        key = name.lower()
        if key in seen:
            raise PartError(f"The part '{name}' is placed twice.")
        seen.add(key)
        if key not in have:
            raise PartError(f"'{name}' is not a declared part of this character.")
        kind = clean_kind(it.get("kind") or have[key], category)
        if kind != have[key]:
            raise PartError(f"'{name}' is declared as {have[key]}, not {kind}.")
        if kind == "stiff":
            locs = [clean_location(x) for x in (it.get("locations") or [])]
            if any(x is None for x in locs):
                raise PartError("Each point of a stiff part is three numbers in metres.")
            if len(locs) > MAX_POINTS:
                raise PartError(f"A stiff part takes at most {MAX_POINTS} points.")
            out.append({"name": name, "kind": kind, "location": None, "locations": locs})
        else:
            out.append({"name": name, "kind": kind, "location": clean_location(it.get("location")), "locations": []})
    return out


def read_placed(path: Path | None) -> list[dict[str, Any]]:
    """A version's parts.json as a list (an unreadable or missing file is no parts)."""
    if path is None or not Path(path).is_file():
        return []
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = doc.get("parts") if isinstance(doc, dict) else None
    return [r for r in rows or [] if isinstance(r, dict) and r.get("name")]


def write_placed(path: Path, parts: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"parts": parts}, indent=1), encoding="utf-8")
    return path


def for_springs(placed_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The sway parts that have a location, for blender/springs.py --declared: [{name, kind, location}]."""
    return [{"name": r["name"], "kind": r["kind"], "location": r["location"]}
            for r in placed_rows if r.get("kind") == "sway" and r.get("location")]


def for_stiff(placed_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The stiff parts that have points, for blender/worn_region.py --points: [{name, locations}] (WP-69)."""
    return [{"name": r["name"], "locations": r["locations"]}
            for r in placed_rows if r.get("kind") == "stiff" and r.get("locations")]


def view(declared: list[dict[str, str]], placed_rows: list[dict[str, Any]], applies: bool) -> dict[str, Any]:
    """What the API answers: the asset's declared parts, this version's placed parts and whether the rig step reads
    them (a rig version only)."""
    loc = {r["name"].lower(): r for r in placed_rows}
    rows = []
    for p in declared:
        r = loc.get(p["name"].lower()) or {}
        rows.append({"name": p["name"], "kind": p["kind"], "location": r.get("location"),
                     "locations": r.get("locations") or []})
    return {"parts": rows, "placed": placed_rows, "applies_to_rig": applies}
