"""Declared parts: the pieces a person names for a character, as structured entries (WP-20).

Two levels, both plain JSON:

- The asset declares its parts (db.Asset.parts, set on step 1 and in the asset's settings): a list of
  {name, kind}. A kind says what the part is for:
    separate  a piece that must move or swap on its own (a lid, a weapon). Today this only names the piece in the
              step 1 picture prompt; nothing rigs it.
    sway      a part that hangs and should sway (a cape hem, a ponytail, a tail, a scarf end). Humanoids only. The
              rig step's spring bones (blender/springs.py --declared) take its location when it has one.
    stiff     a worn item that moves with one body part and does not bend (WP-69). Humanoids only.
    hinge     a lid, door, hatch or gate that swings open about a hinge (WP-29). Objects with no skeleton only.
    spin      a wheel, propeller or fan that turns about its own centre (WP-29). Objects with no skeleton only.
    slide     a drawer or sliding door that moves straight out (WP-29). Objects with no skeleton only.
  A part named without a kind takes one from its name where the category allows it (lid -> hinge, wheels -> spin,
  drawer -> slide), else separate.
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

KINDS = ("separate", "sway", "stiff", "hinge", "spin", "slide")
# Moving parts on objects with no skeleton (WP-29): each is cut from the model and moves about its own pivot
# (blender/moving_parts.py).
MOVING_KINDS = ("hinge", "spin", "slide")
# Kinds that need a location to do anything in the rig step: sway (spring bones), stiff (rigid with a spine bone) and
# the moving kinds (the points say where the piece is).
LOCATED_KINDS = ("sway", "stiff") + MOVING_KINDS
MULTI_POINT_KINDS = ("stiff",) + MOVING_KINDS
MAX_POINTS = 8  # points per stiff or moving part; each grows its own piece
RIGID_KINDS = ("separate",) + MOVING_KINDS
# Kinds a category may use. Sway and stiff are for people-shaped characters (the spring and worn-item passes only run
# on humanoids); hinge, spin and slide for objects with no skeleton.
CATEGORY_KINDS = {"humanoid": ("separate", "sway", "stiff"), "prop_rigid": RIGID_KINDS, "prop_articulated": RIGID_KINDS,
                  "vehicle": RIGID_KINDS, "environment": RIGID_KINDS}
DEFAULT_KIND = "separate"
# The moving part an item's own name or description names (item_defaults): the first group with a word of the item's name,
# else of its description. One part per item; the person adds or changes the rest.
ITEM_PARTS = (
    (("gate", "gates"), "gate leaf", "hinge"),
    (("chest", "trunk", "coffin", "lid", "lids"), "lid", "hinge"),
    (("door", "doors"), "door", "hinge"),
    (("hatch", "hatches", "trapdoor"), "hatch", "hinge"),
    (("wheel", "wheels", "wheelbarrow", "tyre", "tyres", "cart"), "wheel", "spin"),
    (("drawer", "drawers"), "drawer", "slide"),
)
# The kind a part named without one takes, where its category allows it: the first rule with a word of its name.
NAME_KINDS = (("spin", ("wheel", "wheels", "tyre", "tyres", "tire", "tires", "propeller", "fan", "rotor", "gear", "cog")),
              ("slide", ("drawer", "drawers", "tray")),
              ("hinge", ("lid", "door", "doors", "hatch", "gate", "flap", "shutter", "shutters", "cover", "trapdoor")))
SIDES = ("auto", "back", "front", "left", "right", "top", "bottom")
ANGLE_RANGE = (5.0, 180.0)  # hinge: degrees the part turns when open
DISTANCE_RANGE = (0.1, 1.0)  # slide: share of the piece's own depth it moves out
DOOR_WORDS = ("door", "doors", "gate")  # these open 90 degrees by default; anything else on a hinge 100
DOOR_ANGLE = 90.0
LID_ANGLE = 100.0
DEFAULT_DISTANCE = 0.8
NAME_MAX = 40
COORD_MAX_M = 20.0  # scenery and vehicles reach further from their centre than a character
FILE = "parts.json"


class PartError(ValueError):
    """A declared part that is not allowed; the message is plain words for the person."""


def kinds_for(category: str | None) -> tuple[str, ...]:
    return CATEGORY_KINDS.get(str(category or ""), ("separate",))


def _words(name: Any) -> list[str]:
    return "".join(c.lower() if c.isalnum() else " " for c in str(name)).split()


def kind_from_name(name: str, category: str | None) -> str:
    """The kind a part named without one takes: a moving kind from its name where the category allows it, else
    separate."""
    allowed = kinds_for(category)
    words = _words(name)
    for kind, names in NAME_KINDS:
        if kind in allowed and any(w in names for w in words):
            return kind
    return DEFAULT_KIND


def item_defaults(category: str | None, name: Any, prompt: Any, fallback: list[str]) -> list[dict[str, str]]:
    """The parts a new asset starts with when the person named none: the moving part the item's own name names (a
    gate gets a gate leaf, a chest a lid, a wheelbarrow a wheel), else the description's, else the category's fallback
    names. Kinds come from the name and the category. An object with no such word and no fallback gets no part."""
    allowed = kinds_for(category)
    for text in (name, prompt):
        words = set(_words(text or ""))
        for names, label, kind in ITEM_PARTS:
            if kind in allowed and words.intersection(names):
                return [{"name": label, "kind": kind}]
    return [{"name": n, "kind": kind_from_name(n, category)} for n in fallback]


def default_angle(name: str) -> float:
    return DOOR_ANGLE if any(w in DOOR_WORDS for w in _words(name)) else LID_ANGLE


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
        if k in MOVING_KINDS:
            raise PartError(f"A '{k}' part is only for objects with no skeleton: props, vehicles and scenery.")
        raise PartError(f"A '{k}' part is only for humanoid characters so far.")
    return k


def _number(v: Any, lo: float, hi: float, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)) or not lo <= float(v) <= hi:
        raise PartError(f"{what} must be a number from {lo:g} to {hi:g}.")
    return round(float(v), 3)


def clean_motion(it: dict[str, Any], kind: str, name: str) -> dict[str, Any]:
    """A moving part's settings: side (hinge: where the hinge is, or auto), open_at_rest (the model shows it open, so
    the clips run the other way), angle (hinge, degrees) and distance (slide, share of the piece's depth)."""
    side = str(it.get("side") or "auto").strip().lower()
    if side not in SIDES:
        raise PartError(f"The hinge side is one of: {', '.join(SIDES)}.")
    out: dict[str, Any] = {"side": side, "open_at_rest": bool(it.get("open_at_rest"))}
    if kind == "hinge":
        a = it.get("angle")
        out["angle"] = default_angle(name) if a is None else _number(a, *ANGLE_RANGE, "The opening angle")
    if kind == "slide":
        d = it.get("distance")
        out["distance"] = DEFAULT_DISTANCE if d is None else _number(d, *DISTANCE_RANGE, "The sliding distance")
    return out


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
        out.append({"name": name, "kind": clean_kind(it.get("kind") or kind_from_name(name, category), category)})
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
        if kind in MULTI_POINT_KINDS:
            locs = [clean_location(x) for x in (it.get("locations") or [])]
            if any(x is None for x in locs):
                raise PartError(f"Each point of a {kind} part is three numbers in metres.")
            if len(locs) > MAX_POINTS:
                raise PartError(f"A {kind} part takes at most {MAX_POINTS} points.")
            row = {"name": name, "kind": kind, "location": None, "locations": locs}
            if kind in MOVING_KINDS:
                row.update(clean_motion(it, kind, name))
            out.append(row)
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


def for_moving(placed_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The moving parts that have points, for blender/moving_parts.py --parts (WP-29): [{name, kind, locations, side,
    open_at_rest, angle or distance}]."""
    keys = ("name", "kind", "locations", "side", "open_at_rest", "angle", "distance")
    return [{k: r[k] for k in keys if k in r} for r in placed_rows if r.get("kind") in MOVING_KINDS and r.get("locations")]


def unplaced_moving(declared: list[dict[str, str]], placed_rows: list[dict[str, Any]]) -> list[str]:
    """The declared moving parts (hinge, spin, slide) with no points on this version, for the rig step's check."""
    have = {r["name"].lower() for r in for_moving(placed_rows)}
    return [p["name"] for p in declared if p.get("kind") in MOVING_KINDS and p["name"].lower() not in have]


def view(declared: list[dict[str, str]], placed_rows: list[dict[str, Any]], applies: bool) -> dict[str, Any]:
    """What the API answers: the asset's declared parts, this version's placed parts and whether the rig step reads
    them (a rig version only)."""
    loc = {r["name"].lower(): r for r in placed_rows}
    rows = []
    for p in declared:
        r = loc.get(p["name"].lower()) or {}
        row = {"name": p["name"], "kind": p["kind"], "location": r.get("location"), "locations": r.get("locations") or []}
        if p["kind"] in MOVING_KINDS:
            row.update({"side": r.get("side") or "auto", "open_at_rest": bool(r.get("open_at_rest"))})
            if p["kind"] == "hinge":
                row["angle"] = r.get("angle", default_angle(p["name"]))
            if p["kind"] == "slide":
                row["distance"] = r.get("distance", DEFAULT_DISTANCE)
        rows.append(row)
    return {"parts": rows, "placed": placed_rows, "applies_to_rig": applies}
