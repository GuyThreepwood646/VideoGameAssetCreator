"""Items: characters made empty-handed, items made as their own assets, and "Add an item" on a finished character.

Half 1, on the asset form. detect() finds carried-item words (data/items.yaml) in a character's or creature's
description. With "Make them separately" (the default) the character records them on Asset.equipment.set_aside, its
picture description drops those phrases and says the hands are empty (concept_prompt), the painter's negative prompt
lists them where it takes one, and each becomes a linked item asset "<Character> - <item>" (category prop_rigid,
Asset.held_item set) with the character's style, platform and painter, and an attachment to the usual place. Items
are created, not run. When the character is built by the autopilot, the autopilot builds them after the character
(one at a time) and then rebuilds the character's export so the equipped files exist (after_autopilot). Why after:
the graphics cards are shared, so the character the person asked for comes first, and attaching needs the finished,
rigged character with its attachment points.

Half 2, on a finished rigged character. An attachment is {id, item_asset_id, socket, stow_socket, position (metres),
rotation (quaternion x, y, z, w), scale} in the attachment point's own frame (glTF: +Z forward along the grip, +Y up
toward the thumb). The item's own file has its pivot at the grip and its axes in the grip frame (vgac/blender/grip.py),
so a zero offset is the usual hold. Nudges move in the character's own axes (1 cm or 5 degrees a press). The export
stage writes attachments.json, the item files, an equipped GLB and, in Unity, a second prefab "<Name> (equipped)".
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import library, reference, sockets
from .config import get_settings
from .db import Asset, Version, asset_to_dict, get_session, session_factory

log = logging.getLogger(__name__)
DATA_FILE = Path(__file__).resolve().parent / "data" / "items.yaml"
STEP_M, BIG_STEP_M = 0.01, 0.05
STEP_DEG, BIG_STEP_DEG = 5.0, 15.0
SCALE_STEP = 1.05
SCALE_MIN, SCALE_MAX = 0.2, 5.0
CLEAR_LIMIT_M = 0.03  # item_clear_of_body: deepest a non-held part may sink into the body

PLACES = {  # attachment point -> the plain words on the buttons
    "Socket_Hand_R": "In the right hand", "Socket_Hand_L": "In the left hand", "Socket_Back": "On the back",
    "Socket_Hip_L": "At the left hip", "Socket_Hip_R": "At the right hip", "Socket_Head": "On the head",
    "Socket_Chest": "On the chest", "Socket_Mouth": "In the mouth",
}
MIRROR = {"Socket_Hand_R": "Socket_Hand_L", "Socket_Hand_L": "Socket_Hand_R", "Socket_Hip_L": "Socket_Hip_R", "Socket_Hip_R": "Socket_Hip_L"}
HAND_LIMIT = "The hand does not close around the handle: the characters have no finger bones, so the hand stays a mitten."
UNREAL_NOTE = ("Unreal (not verified): import the character FBX, then add one skeletal-mesh socket per entry in "
               "attachments.json on the bone it names, with the offset given, and attach the item's static mesh to it by "
               "socket name. The FBX's Socket_* empties come in as extra bones, not sockets.")
GODOT_NOTE = ("Godot (not verified): open the _equipped.glb, or add a BoneAttachment3D on the bone each entry names and "
              "put the item's .glb under it with the offset given. Godot's glTF importer may create the "
              "BoneAttachment3D nodes for the Socket_* nodes by itself.")


# --- data ----------------------------------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def data() -> dict[str, Any]:
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        d = yaml.safe_load(f) or {}
    for t in (d.get("types") or {}).values():
        t["words"] = [str(w).lower() for w in t.get("words") or []]
    for k in ("empty_hands", "empty_hands_creature"):
        d[k] = " ".join(str(d.get(k, "")).split())
    d["pose_blocks"] = {k: " ".join(str(v).split()) for k, v in (d.get("pose_blocks") or {}).items()}
    return d


def types() -> dict[str, dict[str, Any]]:
    return data().get("types") or {}


def item_type(tid: str | None) -> dict[str, Any]:
    return types().get(tid or "") or {}


def public_words() -> dict[str, Any]:
    """GET /api/item-words: the list the asset form uses, from the one data file."""
    d = data()
    return {"version": d.get("version", 1), "categories": list(d.get("categories") or []),
            "types": [{"id": k, "label": t.get("label") or k, "words": t["words"], "kind": t.get("kind"),
                       "place": PLACES.get(t.get("socket") or "", ""), "socket": t.get("socket")} for k, t in types().items()],
            "worn_words": list(d.get("worn_words") or []), "places": PLACES,
            "help": "Clothes that bend with the body (a cloak, armour, boots, a belt) stay part of the body."}


# --- Half 1: finding item words -------------------------------------------------------------------------------------

_SPLIT = re.compile(r"[,;.!?\n]|\band\b|\bplus\b|\bwhile\b|\bas well as\b", re.IGNORECASE)
_LEAD = ("with", "holding", "carrying", "wielding", "wearing", "clutching", "gripping", "brandishing", "has", "carries",
         "holds", "wields", "bearing", "toting")
_LOCATION = ("at", "on", "in", "over", "across", "behind", "strapped", "slung", "hanging", "by", "from", "under", "upon",
             "to", "into", "against", "around", "beside", "tucked", "held", "worn", "sheathed", "raised", "ready")
_DETERMINERS = ("a", "an", "the", "his", "her", "their", "its", "one", "two", "some", "a pair of", "pair of")


def _matchers() -> list[tuple[str, str, re.Pattern[str]]]:
    out = []
    for tid, t in types().items():
        for w in t["words"]:
            pat = re.compile(r"(?<![\w-])" + r"\s+".join(re.escape(p) for p in w.split()) + r"(?![\w])", re.IGNORECASE)
            out.append((tid, w, pat))
    out.sort(key=lambda x: -len(x[1]))
    return out


def _clause(text: str, pos: int) -> tuple[int, int]:
    """The sub-clause (between commas, full stops and 'and') around pos. -> (start, end)."""
    start, end = 0, len(text)
    for m in _SPLIT.finditer(text):
        if m.end() <= pos:
            start = m.end()
        elif m.start() >= pos:
            end = m.start()
            break
    return start, end


def _words_before(text: str, pos: int, n: int = 3) -> list[str]:
    return re.findall(r"[a-zA-Z']+", text[:pos])[-n:]


def detect(prompt: str | None, category: str | None) -> dict[str, Any]:
    """Carried items in a description. -> {checked, items: [{type, label, word, phrase, place, socket, span, remove}],
    worn: [worn words found]}. checked is False for categories that keep their description as is. One item per type
    (a quiver and its arrows are one quiver); negated mentions ("without a sword") are left alone."""
    text = prompt or ""
    d = data()
    worn = sorted({w for w in d.get("worn_words") or [] if re.search(r"\b" + re.escape(w) + r"\b", text, re.IGNORECASE)})
    if category not in (d.get("categories") or []):
        return {"checked": False, "items": [], "worn": worn}
    neg = {w.lower() for w in d.get("negations") or []}
    notf = {k: [x.lower() for x in v] for k, v in (d.get("not_followed_by") or {}).items()}
    taken: list[tuple[int, int]] = []
    found: dict[str, dict[str, Any]] = {}
    for tid, word, pat in _matchers():
        for m in pat.finditer(text):
            s, e = m.span()
            if any(a < e and s < b for a, b in taken):
                continue
            taken.append((s, e))
            after = re.match(r"[\s-]*([a-zA-Z]+)", text[e:])
            head = word.split()[-1]
            if after and after.group(1).lower() in notf.get(head, []):
                continue
            if text[e:e + 1] == "-" and after and after.group(1).lower() in ("shaped", "like"):
                continue
            if any(w.lower() in neg for w in _words_before(text, s)):
                continue
            if tid in found:
                continue
            found[tid] = _describe(text, tid, word, s, e)
    items = sorted(found.values(), key=lambda x: x["span"][0])
    return {"checked": True, "items": items, "worn": worn}


def _describe(text: str, tid: str, word: str, s: int, e: int) -> dict[str, Any]:
    t = item_type(tid)
    cs, ce = _clause(text, s)
    # A hyphenated mention ("a sword-wielding knight") removes only that word.
    if text[e:e + 1] == "-":
        m = re.match(r"-[a-zA-Z]+", text[e:])
        rem = (s, e + (m.end() if m else 1))
        return {"type": tid, "label": t.get("label") or tid, "word": word, "phrase": text[s:e].strip(),
                "place": PLACES.get(t.get("socket") or "", ""), "socket": t.get("socket"), "span": [s, e], "remove": list(rem)}
    # Left: from the nearest lead word ("with", "holding") or the clause start, keeping the determiner.
    left_text = text[cs:s]
    lead = None
    for m in re.finditer(r"\b(" + "|".join(_LEAD) + r")\b", left_text, re.IGNORECASE):
        lead = m
    phrase_start = cs + (lead.end() if lead else 0)
    rem_start = cs + (lead.start() if lead else 0)
    # Right: descriptors ("of arrows", "with a leather grip") up to a place word ("at his hip") or the clause end.
    tail = text[e:ce]
    stop = len(tail)
    for m in re.finditer(r"\b(" + "|".join(_LOCATION) + r")\b", tail, re.IGNORECASE):
        stop = m.start()
        break
    phrase = re.sub(r"\s+", " ", text[phrase_start:e + stop]).strip(" ,;")
    return {"type": tid, "label": t.get("label") or tid, "word": word, "phrase": phrase,
            "place": PLACES.get(t.get("socket") or "", ""), "socket": t.get("socket"), "span": [s, e], "remove": [rem_start, ce]}


def strip_items(prompt: str, items: list[dict[str, Any]]) -> str:
    """The description without the item mentions, tidied ("a ranger, a sword at his hip and a cloak" -> "a ranger and a
    cloak")."""
    src = prompt or ""
    keep = [True] * len(src)
    for i in items:
        if not i.get("remove"):
            continue
        a, b = i["remove"]
        # Take a joining "and" just before the mention with it, so no dangling joiner is left.
        pre = re.search(r"\s+(and|plus)\s*$", src[:a], re.IGNORECASE)
        nxt = re.match(r"\s*and\s+", src[b:], re.IGNORECASE)
        lead = re.match(r"(with|holding|carrying|wielding)\s+", src[a:b], re.IGNORECASE)
        if pre:
            a = pre.start()
        elif lead and nxt:
            # "a dwarf with a big axe and a red beard": keep "with" for what follows.
            a, b = a + lead.end(), b + nxt.end()
        for k in range(max(0, a), min(len(src), b)):
            keep[k] = False
    text = "".join(ch for ch, k in zip(src, keep) if k)
    text = re.sub(r"\b(with|and|holding|carrying)\s*([,.;]|$)", r"\2", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r",(\s*,)+", ",", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"^[\s,;]+|[\s,;]+$", "", text)
    text = re.sub(r",\s*\.", ".", text)
    return text.strip()


def _det_strip(phrase: str) -> str:
    p = phrase.strip()
    for d in sorted(_DETERMINERS, key=len, reverse=True):
        if p.lower().startswith(d + " "):
            return p[len(d) + 1:].strip()
    return p


def item_size(tid: str, phrase: str) -> float:
    base = float(item_type(tid).get("size_m") or 0.5)
    low = (phrase or "").lower()
    if re.search(r"\b(short|small|little|tiny)\b", low):
        base *= 0.7
    elif re.search(r"\b(great|long|large|big|huge|giant|two-handed)\b", low):
        base *= 1.3
    return round(base, 2)


def item_prompt(tid: str, phrase: str) -> str:
    """The item asset's description: the phrase from the character's description and its usual size."""
    body = _det_strip(phrase) or (item_type(tid).get("label") or tid)
    body = body[0].upper() + body[1:]
    size = item_size(tid, phrase)
    return f"{body}, a game item, about {size:g} m."


def concept_prompt_parts(asset: Any) -> dict[str, Any] | None:
    """For a character with items set aside: {prompt (the description without them), sentence, negative words}. For
    a held item: {pose_block}. None otherwise."""
    held = getattr(asset, "held_item", None)
    if held:
        t = item_type(held.get("type"))
        block = data()["pose_blocks"].get(t.get("view") or "upright") or ""
        return {"pose_block": block.replace("{label}", held.get("label") or t.get("label") or "item")}
    eq = getattr(asset, "equipment", None) or {}
    aside = eq.get("set_aside") or []
    if eq.get("choice") != "separate" or not aside:
        return None
    found = detect(asset.prompt, asset.category)
    keep = {a.get("type") for a in aside}
    items = [i for i in found["items"] if i["type"] in keep]
    sentence = data()["empty_hands"] if asset.category == "humanoid" else data()["empty_hands_creature"]
    words = []
    for a in aside:
        t = item_type(a.get("type"))
        words += [a.get("label") or a.get("type"), *(t.get("words") or [])[:2]]
    neg = ", ".join(dict.fromkeys(w for w in words if w))
    return {"prompt": strip_items(asset.prompt, items), "sentence": sentence, "negative": neg,
            "set_aside": [a.get("label") for a in aside]}


# --- attachment maths ----------------------------------------------------------------------------------------------


def euler_to_quat(deg: list[float]) -> list[float]:
    """Degrees about x, y, z in three.js 'XYZ' order (matrix Rx Ry Rz) -> quaternion (x, y, z, w)."""
    x, y, z = (math.radians(float(a)) for a in (list(deg) + [0, 0, 0])[:3])
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return mat_to_quat(Rx @ Ry @ Rz)


def mat_to_quat(m) -> list[float]:
    m = np.asarray(m, float)
    q = sockets.quat_from_frame(m[:, 0], m[:, 1], m[:, 2])
    return [round(float(v), 6) for v in q]


def quat_to_euler(q) -> list[float]:
    """Quaternion -> degrees (three.js 'XYZ')."""
    m = sockets.quat_to_matrix(q)
    y = math.asin(max(-1.0, min(1.0, m[0, 2])))
    if abs(m[0, 2]) < 0.9999999:
        x = math.atan2(-m[1, 2], m[2, 2])
        z = math.atan2(-m[0, 1], m[0, 0])
    else:
        x = math.atan2(m[2, 1], m[1, 1])
        z = 0.0
    return [round(math.degrees(a), 2) + 0.0 for a in (x, y, z)]


def axis_angle(axis, deg) -> np.ndarray:
    a = np.asarray(axis, float)
    n = float(np.linalg.norm(a))
    if n < 1e-12:
        return np.eye(3)
    a = a / n
    t = math.radians(deg)
    K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + math.sin(t) * K + (1 - math.cos(t)) * (K @ K)


def default_socket(tid: str, category: str) -> str:
    t = item_type(tid)
    if category == "humanoid":
        return t.get("socket") or "Socket_Hand_R"
    return t.get("creature") or "Socket_Back"


def default_rotation(tid: str, socket: str) -> list[float]:
    """The item type's usual turn, mirrored when it goes on the other side from its usual one."""
    t = item_type(tid)
    rot = [float(v) for v in (list(t.get("rotation") or [0, 0, 0]) + [0, 0, 0])[:3]]
    usual = t.get("socket")
    if usual and MIRROR.get(usual) == socket:
        rot = [rot[0], -rot[1], -rot[2]]
    return rot


def new_attachment(item: Asset, category: str, socket: str | None = None) -> dict[str, Any]:
    held = item.held_item or {}
    tid = held.get("type") or "sword"
    sock = socket or default_socket(tid, category)
    t = item_type(tid)
    stow = t.get("stow") if category == "humanoid" else None
    att = {"id": uuid.uuid4().hex[:12], "item_asset_id": item.id, "item_name": item.name, "type": tid,
           "label": held.get("label") or t.get("label") or tid, "socket": sock, "stow_socket": stow if stow != sock else None,
           "position": [0.0, 0.0, 0.0], "rotation": euler_to_quat(default_rotation(tid, sock)), "scale": 1.0}
    att["rotation_deg"] = quat_to_euler(att["rotation"])
    return att


def reset(att: dict[str, Any]) -> dict[str, Any]:
    att = dict(att)
    att.update(position=[0.0, 0.0, 0.0], rotation=euler_to_quat(default_rotation(att.get("type") or "", att["socket"])), scale=1.0)
    att["rotation_deg"] = quat_to_euler(att["rotation"])
    return att


def socket_frame(doc: dict[str, Any] | None, name: str) -> np.ndarray:
    """The attachment point's rest frame in model space (columns right, up, forward); identity when unknown."""
    for s in (doc or {}).get("sockets") or []:
        if s.get("name") == name:
            r, u, f = (np.asarray(s.get(k) or d, float) for k, d in (("right", [1, 0, 0]), ("up", [0, 1, 0]), ("forward", [0, 0, 1])))
            return np.column_stack([r, u, f])
    return np.eye(3)


def nudge(att: dict[str, Any], frame: np.ndarray, move=None, turn=None, slide: float = 0.0, scale_steps: int = 0,
          big: bool = False) -> dict[str, Any]:
    """One press. move: (x, y, z) steps in the character's own axes (+x its left, +y up, +z forward); turn: steps about
    those axes; slide: steps along the item's own length (+1 holds it nearer the tip); scale_steps: +1 bigger, -1
    smaller. A step is 1 cm or 5 degrees (5 cm and 15 degrees with big)."""
    att = dict(att)
    step = BIG_STEP_M if big else STEP_M
    deg = BIG_STEP_DEG if big else STEP_DEG
    F = np.asarray(frame, float)
    pos = np.asarray(att.get("position") or [0, 0, 0], float)
    R = sockets.quat_to_matrix(att.get("rotation") or [0, 0, 0, 1])
    if move is not None:
        pos = pos + F.T @ (np.asarray(move, float) * step)
    if turn is not None:
        tv = np.asarray(turn, float)
        for i in range(3):
            if tv[i]:
                R = axis_angle(F.T @ np.eye(3)[i], tv[i] * deg) @ R
    if slide:
        pos = pos - R @ np.array([0.0, 0.0, float(slide) * step])
    if scale_steps:
        att["scale"] = round(float(min(SCALE_MAX, max(SCALE_MIN, float(att.get("scale") or 1.0) * SCALE_STEP ** int(scale_steps)))), 4)
    att["position"] = [round(float(v), 5) for v in pos]
    att["rotation"] = mat_to_quat(R)
    att["rotation_deg"] = quat_to_euler(att["rotation"])
    return att


def item_status(db: Session, item: Asset) -> dict[str, Any]:
    """What the item panel shows about one item: how far it is and its files."""
    from . import autopilot, pipeline

    ex = pipeline.selected(db, item.id, "export")
    state = autopilot.get_state(item)
    running = [v for v in item.versions if v.status in ("queued", "running")]
    if ex is not None and ex.status == "done":
        status = "done"
    elif running or state.get("status") == "running":
        status = "making"
    elif state.get("status") in ("paused", "error"):
        status = state["status"]
    elif item.versions:
        status = "started"
    else:
        status = "not_started"
    grip = (ex.metrics.get("grip") if ex is not None else None) or None
    files = ex.files if ex is not None and ex.status == "done" else {}
    concept = pipeline.selected(db, item.id, "concept")
    return {"id": item.id, "name": item.name, "status": status, "stage": state.get("stage") or (running[0].stage if running else None),
            "reason": state.get("reason"), "held_item": item.held_item, "glb": files.get("glb"), "fbx": files.get("fbx"),
            "export_version_id": ex.id if ex is not None and ex.status == "done" else None, "grip": grip,
            "thumbnail": (concept.files.get("cutout") or concept.files.get("image")) if concept is not None else None,
            "autopilot": state.get("status")}


# --- creating items ----------------------------------------------------------------------------------------------


def _unique_name(db: Session, project_id: str, name: str) -> str:
    names = {a.name for a in db.scalars(select(Asset).where(Asset.project_id == project_id)).all()}
    if name not in names:
        return name
    i = 2
    while f"{name} {i}" in names:
        i += 1
    return f"{name} {i}"


def make_item(db: Session, character: Asset, tid: str, phrase: str | None = None, description: str | None = None) -> Asset:
    """A new item asset linked to the character: "<Character> - <item>", prop_rigid, the character's style, platform
    and painter. Created, not run."""
    t = item_type(tid)
    if not t:
        raise ValueError(f"unknown item type {tid!r}")
    label = t.get("label") or tid
    phrase = (phrase or "").strip() or label
    prompt = (description or "").strip() or item_prompt(tid, phrase)
    item = Asset(project_id=character.project_id, name=_unique_name(db, character.project_id, f"{character.name} - {label}"),
                 category="prop_rigid", style_preset=character.style_preset, platform_target=character.platform_target, prompt=prompt)
    item.parts = []
    item.concept_model = character.concept_model
    item.held_item = {"type": tid, "label": label, "kind": t.get("kind"), "phrase": phrase, "character_id": character.id,
                      "character_name": character.name}
    db.add(item)
    db.flush()
    return item


def on_create(db: Session, a: Asset, choice: str | None) -> None:
    """After a character is created: set its carried items aside (choice 'separate', the default) or keep them part of
    the body ('keep'). Records the choice on Asset.equipment either way when items were found."""
    found = detect(a.prompt, a.category)
    if not found["checked"] or not found["items"]:
        return
    choice = "keep" if choice == "keep" else "separate"
    eq: dict[str, Any] = {"choice": choice, "found": [{k: i[k] for k in ("type", "label", "phrase")} for i in found["items"]],
                          "set_aside": [], "attachments": []}
    if choice == "separate":
        for i in found["items"]:
            item = make_item(db, a, i["type"], i["phrase"])
            eq["set_aside"].append({"type": i["type"], "label": i["label"], "phrase": i["phrase"], "item_asset_id": item.id})
            eq["attachments"].append(new_attachment(item, a.category))
    a.equipment = eq
    db.commit()


# --- the item panel's state --------------------------------------------------------------------------------------


def character_sockets(db: Session, a: Asset) -> tuple[dict[str, Any] | None, Version | None]:
    from . import pipeline

    ex = pipeline.selected(db, a.id, "export")
    if ex is None or ex.status != "done":
        return None, ex
    p = library.version_file(ex, "sockets", get_settings())
    if p is None or not Path(p).is_file():
        return None, ex
    try:
        return json.loads(Path(p).read_text(encoding="utf-8")), ex
    except (OSError, ValueError):
        return None, ex


def digest(eq: dict[str, Any] | None, item_exports: dict[str, str | None]) -> str:
    """What the character's equipped files were built from: the attachments and each item's export version."""
    atts = [{k: v for k, v in at.items() if k != "rotation_deg"} for at in (eq or {}).get("attachments") or []]
    blob = json.dumps({"a": atts, "x": item_exports}, sort_keys=True)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def panel(db: Session, a: Asset) -> dict[str, Any]:
    eq = a.equipment or {"choice": None, "set_aside": [], "attachments": []}
    doc, ex = character_sockets(db, a)
    can = bool(doc and doc.get("sockets"))
    if a.category not in sockets.SOCKET_CATEGORIES:
        reason = "Items go on characters and creatures with a skeleton."
    elif not can:
        reason = "Finish the character first: items go on the attachment points made in the last step."
    else:
        reason = None
    names = [s["name"] for s in (doc or {}).get("sockets") or []]
    places = [{"socket": n, "label": PLACES.get(n, sockets.HUMANOID.get(n, ("", ""))[0] or n)} for n in names]
    ids = {at.get("item_asset_id") for at in eq.get("attachments") or []} | {s.get("item_asset_id") for s in eq.get("set_aside") or []}
    items = {}
    for iid in ids:
        it = db.get(Asset, iid) if iid else None
        if it is not None:
            items[iid] = item_status(db, it)
    exports = {iid: s.get("export_version_id") for iid, s in items.items()}
    applied = (ex.metrics.get("attachments_digest") if ex is not None else None)
    current = digest(eq, exports)
    attached = [at for at in eq.get("attachments") or [] if items.get(at.get("item_asset_id"), {}).get("status") == "done"]
    # Items from this project the person may give the character ("Use one you made").
    mine = [it for it in db.scalars(select(Asset).where(Asset.project_id == a.project_id)).all() if it.held_item and it.id not in ids]
    build = eq.get("build")
    if build and build.get("status") == "equipping" and build.get("version_id"):
        v = db.get(Version, build["version_id"])
        if v is not None and v.status in ("done", "error", "rejected"):
            build = {**build, "status": "done" if v.status == "done" else "error"}
    return {"asset_id": a.id, "can_add": can, "reason": reason, "category": a.category, "places": places,
            "equipment": eq, "items": items, "character_glb": ex.files.get("glb") if ex is not None and ex.status == "done" else None,
            "sockets": doc, "saved": bool(attached) and applied == current, "needs_update": bool(attached) and applied != current,
            "build": build, "others": [item_status(db, it) for it in mine],
            "types": [{"id": k, "label": t.get("label") or k, "place": PLACES.get(default_socket(k, a.category), "")} for k, t in types().items()],
            "limit": HAND_LIMIT, "step_cm": STEP_M * 100, "step_deg": STEP_DEG}


# --- autopilot: items after the character --------------------------------------------------------------------------


def _set_build(aid: str, build: dict[str, Any] | None) -> None:
    with session_factory()() as db:
        a = db.get(Asset, aid)
        if a is None:
            return
        eq = dict(a.equipment or {})
        eq["build"] = build
        a.equipment = eq
        db.commit()


def after_autopilot(aid: str) -> None:
    """Called when an autopilot finishes. For a character with items set aside: build the next item that is not made
    (its own autopilot), and when all are made, rebuild the character's export with them attached. For an item: carry
    on with its character's chain if that chain is running."""
    from . import autopilot, pipeline

    with session_factory()() as db:
        a = db.get(Asset, aid)
        if a is None:
            return
        held = a.held_item or {}
        if held.get("character_id"):
            ch = db.get(Asset, held["character_id"])
            if ch is None or ((ch.equipment or {}).get("build") or {}).get("status") != "items":
                return
            aid = ch.id
            a = ch
        eq = a.equipment or {}
        atts = eq.get("attachments") or []
        if not atts or a.category not in sockets.SOCKET_CATEGORIES:
            return
        todo = None
        for at in atts:
            it = db.get(Asset, at.get("item_asset_id"))
            if it is None:
                continue
            st = item_status(db, it)
            if st["status"] == "done":
                continue
            if st["autopilot"] in ("paused", "error"):
                _set_build(aid, {"status": "stopped", "item": it.id, "reason": f"{it.name} needs you: {st.get('reason') or 'it stopped'}"})
                return
            todo = it.id
            break
        if todo is not None:
            _set_build(aid, {"status": "items", "item": todo})
            autopilot.start(todo)
            return
        # Every item is made: rebuild the export once so the equipped files exist.
        ex = pipeline.selected(db, aid, "export")
        if ex is None or ex.status != "done":
            return
        p = panel(db, a)
        if not p["needs_update"]:
            _set_build(aid, {"status": "done"})
            return
        v = pipeline.new_version(db, a, "export", ex, dict(ex.params))
        job = pipeline.submit(db, a, v, "adjust", {"params": {}})
        _set_build(aid, {"status": "equipping", "version_id": v.id, "job_id": job.id})


# --- export: item pivot and the equipped character -------------------------------------------------------------------


def is_held_item(sc: Any) -> bool:
    return bool(getattr(getattr(sc, "asset", None), "held_item", None))


async def pivot_item(sc: Any) -> None:
    """Item export, before packing: find the grip on the prepared mesh in Blender (blender/item_pivot.py), write
    grip.json and a copy of the mesh with its pivot at the grip in the grip frame; the pack step uses that copy."""
    held = sc.asset.held_item or {}
    t = item_type(held.get("type"))
    src = sc.lineage_file("glb", ("prep",)) or sc.lineage_file("glb", ("mesh",))
    if src is None:
        return
    out = Path(sc.dir) / "item_pivoted.glb"
    gj = Path(sc.dir) / "grip.json"
    for p in (out, gj):
        if p.is_file():
            p.unlink()
    sc.progress(0.02, "finding where the hand holds it")
    args = [src, out, gj, "--kind", t.get("kind") or "blade", "--grip-fraction", str(t.get("grip_fraction") or 0.38),
            "--head-end", t.get("head_end") or ("narrower" if held.get("type") == "wand" else "wider")]
    try:
        await sc.blender("item_pivot.py", args, log_name="item_pivot.log")
    except Exception as e:  # noqa: BLE001  (the item still exports, with its prepared pivot, and the check says so)
        sc.set_metrics(grip={"found": False, "error": str(e)[-300:]})
        return
    if not gj.is_file() or not out.is_file():
        sc.set_metrics(grip={"found": False, "error": "no grip written"})
        return
    g = json.loads(gj.read_text(encoding="utf-8"))
    sc.register("grip", gj)
    sc.item_input = out
    sc.set_metrics(grip={"found": True, "confidence": g.get("confidence"), "sure": g.get("sure"), "method": g.get("method"),
                         "threshold": g.get("threshold"), "reasons": g.get("reasons") or [], "kind": g.get("kind"),
                         "label": held.get("label"), "length_m": g.get("length")})


def grip_check(grip: dict[str, Any] | None, label: str) -> dict[str, Any]:
    from . import guidance

    g = grip or {}
    conf = float(g.get("confidence") or 0.0)
    thr = float(g.get("threshold") or 0.6)
    ok = bool(g.get("found")) and conf >= thr
    c = {"name": "grip_found", "id": "grip_found", "passed": ok, "ok": ok, "value": round(conf, 2), "limit": thr,
         "message": f"{round(conf, 2)} (limit {thr})", "vars": {"label": label or "item", "method": g.get("method") or "",
                                                                  "reasons": "; ".join(g.get("reasons") or [])}}
    return guidance.describe_check(c)


def export_checks(sc: Any) -> list[dict[str, Any]]:
    """grip_found on an item's export; grip_found (the least sure item) and item_clear_of_body on a character's."""
    from . import guidance

    out = []
    if is_held_item(sc):
        g = sc.version.metrics.get("grip")
        if g is not None:
            out.append(grip_check(g, (sc.asset.held_item or {}).get("label") or "item"))
        return out
    eq = sc.version.metrics.get("equipped")
    if not eq:
        return out
    grips = [(i.get("grip") or {}, i.get("label") or "item") for i in eq.get("items") or []]
    if grips:
        worst = min(grips, key=lambda x: float(x[0].get("confidence") or 0.0))
        out.append(grip_check(worst[0], worst[1]))
    clear = eq.get("clearance")
    if clear and clear.get("measured"):
        depth = float(clear.get("worst_depth_m") or 0.0)
        ok = depth <= CLEAR_LIMIT_M
        c = {"name": "item_clear_of_body", "id": "item_clear_of_body", "passed": ok, "ok": ok, "value": round(depth, 3),
             "limit": CLEAR_LIMIT_M, "message": f"{round(depth, 3)} (limit {CLEAR_LIMIT_M})",
             "vars": {"label": (clear.get("worst_label") or "item") if not ok else _listed([g[1] for g in grips] or ["item"]),
                      "stays": "stays" if not ok or len(grips) <= 1 else "stay", "clip": clear.get("worst_clip") or "a move",
                      "cm": round(depth * 100, 1), "clips": ", ".join(clear.get("clips") or [])}}
        out.append(guidance.describe_check(c))
    return out


def _listed(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _item_files(ex: Version) -> dict[str, Path]:
    s = get_settings()
    out = {}
    for k, rel in ex.files.items():
        if k in ("fbx", "glb", "glb_godot", "manifest", "grip", "base_color", "normal", "metallic", "roughness", "ao",
                 "metallic_smoothness", "orm"):
            p = library.abs_path(rel, s)
            if p.is_file():
                out[k] = p
    return out


async def equip(sc: Any, name: str) -> dict[str, Any] | None:
    """Character export, after packing: when items are attached and made, copy each item's files into items/<name>/,
    write attachments.json, build <name>_equipped.glb in Blender (blender/equip.py) and measure how close each item
    comes to the body in the attack, walk and run clips. -> the metrics.equipped block, or None."""
    from . import pipeline
    import shutil

    eq = sc.asset.equipment or {}
    atts = eq.get("attachments") or []
    if not atts:
        return None
    sdoc_p = Path(sc.dir) / "sockets.json"
    sdoc = json.loads(sdoc_p.read_text(encoding="utf-8")) if sdoc_p.is_file() else {}
    have = {s.get("name"): s for s in sdoc.get("sockets") or []}
    entries, exports, skipped = [], {}, []
    items_dir = Path(sc.dir) / "items"
    if items_dir.is_dir():
        shutil.rmtree(items_dir, ignore_errors=True)
    for at in atts:
        it = sc.db.get(Asset, at.get("item_asset_id"))
        ex = pipeline.selected(sc.db, it.id, "export") if it is not None else None
        exports[at.get("item_asset_id")] = ex.id if ex is not None and ex.status == "done" else None
        if it is None or ex is None or ex.status != "done":
            skipped.append({"item": at.get("item_name"), "reason": "not made yet"})
            continue
        if at.get("socket") not in have:
            skipped.append({"item": it.name, "reason": f"the character has no {at.get('socket')}"})
            continue
        files = _item_files(ex)
        if "glb" not in files:
            skipped.append({"item": it.name, "reason": "the item has no GLB"})
            continue
        export_name = ex.params.get("export_name") or Path(files["glb"]).stem
        d = items_dir / export_name
        d.mkdir(parents=True, exist_ok=True)
        rel = {}
        for k, p in files.items():
            shutil.copy2(p, d / p.name)
            rel[k] = f"items/{export_name}/{p.name}"
        s = have[at["socket"]]
        grip = ex.metrics.get("grip") or {}
        t = item_type(at.get("type"))
        entries.append({"id": at["id"], "item": it.name, "asset_id": it.id, "export_name": export_name, "type": at.get("type"),
                        "label": at.get("label"), "socket": at["socket"], "place": PLACES.get(at["socket"], at["socket"]),
                        "bone": s.get("bone") or "", "stow_socket": at.get("stow_socket") or "",
                        "stow_place": PLACES.get(at.get("stow_socket") or "", ""),
                        "position": [float(v) for v in at.get("position") or [0, 0, 0]],
                        "rotation": [float(v) for v in at.get("rotation") or [0, 0, 0, 1]],
                        "rotation_deg": quat_to_euler(at.get("rotation") or [0, 0, 0, 1]), "scale": float(at.get("scale") or 1.0),
                        "files": rel, "unity_prefab": f"Assets/VGAC/{export_name}/{export_name}.prefab",
                        "grip": {k: grip.get(k) for k in ("found", "confidence", "sure", "method", "reasons")},
                        "clip_set": t.get("clip_set") or "none"})
    doc = {"version": 1, "space": "gltf", "units": "m", "character": sc.asset.name, "export_name": name,
           "frame": "each offset is in its attachment point's own frame: +Z forward along the grip, +Y up toward the thumb; "
                    "rotation is a quaternion x, y, z, w; the item's own file has its pivot at the grip",
           "count": len(entries), "items": entries, "skipped": skipped, "equipped_glb": f"{name}_equipped.glb" if entries else "",
           "engines": {"unity": {"verified": True, "note": f'The prefab "{name} (equipped)" has each item under its attachment point; '
                                                            f'the "{name}" prefab stays bare.'},
                       "unreal": {"verified": False, "note": UNREAL_NOTE}, "godot": {"verified": False, "note": GODOT_NOTE}},
           "known_limits": [HAND_LIMIT]}
    ap = Path(sc.dir) / "attachments.json"
    result: dict[str, Any] = {"count": len(entries), "skipped": skipped, "digest": digest(eq, exports),
                              "items": [{"item": e["item"], "label": e["label"], "socket": e["socket"], "place": e["place"],
                                         "grip": e["grip"]} for e in entries]}
    if entries:
        glb = Path(sc.dir) / f"{name}.glb"
        out = Path(sc.dir) / f"{name}_equipped.glb"
        rp = Path(sc.dir) / "equip_report.json"
        for p in (out, rp):
            if p.is_file():
                p.unlink()
        spec = Path(sc.dir) / "equip_input.json"
        spec.write_text(json.dumps({"items": [{"label": e["label"], "item": e["item"], "socket": e["socket"],
                                                 "glb": str(Path(sc.dir) / e["files"]["glb"]), "position": e["position"],
                                                 "rotation": e["rotation"], "scale": e["scale"],
                                                 "held": e["socket"] in ("Socket_Hand_R", "Socket_Hand_L", "Socket_Mouth")}
                                                for e in entries]}, indent=1), encoding="utf-8")
        sc.progress(0.3, "putting the items on")
        try:
            await sc.blender("equip.py", [glb, spec, out, rp, "--clips", "attack,walk,run"], log_name="equip.log")
        except Exception as e:  # noqa: BLE001
            result["error"] = str(e)[-400:]
        if out.is_file():
            sc.register("glb_equipped", out)
            doc["equipped_glb"] = out.name
        if rp.is_file():
            rep = json.loads(rp.read_text(encoding="utf-8"))
            result["clearance"] = rep.get("clearance")
            doc["clearance"] = rep.get("clearance")
    ap.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    sc.register("attachments", ap)
    sc.set_metrics(equipped=result, attachments_digest=result["digest"])
    return result


def keep_if_equipping(sc: Any) -> None:
    """End of a character export that the items rebuild asked for (apply, or the autopilot chain): keep it, so the
    finish card, the viewer and the gallery show the character with the items on, and mark the rebuild done."""
    from . import pipeline

    eq = getattr(sc.asset, "equipment", None) or {}
    b = eq.get("build") or {}
    if b.get("status") != "equipping" or b.get("version_id") != sc.version.id:
        return
    pipeline.select(sc.db, sc.version)
    _set_build(sc.asset.id, {**b, "status": "done"})
    sc.db.refresh(sc.asset)


def unity_line(report: dict[str, Any] | None, name: str) -> str | None:
    """The one plain sentence for the finish card's Unity how-to."""
    r = report or {}
    if not r.get("equippedPrefab"):
        return None
    return f'"{name} (equipped)" is the same prefab with the items already in place; the plain "{name}" stays empty-handed.'


# --- API ---------------------------------------------------------------------------------------------------------

router = APIRouter()


class DetectIn(BaseModel):
    prompt: str = Field(default="", max_length=4000)
    category: str = "humanoid"


class AddItemIn(BaseModel):
    type: str | None = None
    description: str | None = Field(default=None, max_length=1000)
    item_asset_id: str | None = None
    socket: str | None = None
    build: bool = True


class AttachmentPut(BaseModel):
    socket: str | None = None
    stow_socket: str | None = None
    position: list[float] | None = None
    rotation_deg: list[float] | None = None
    scale: float | None = Field(default=None, gt=0)


class NudgeIn(BaseModel):
    move: list[float] | None = None
    turn: list[float] | None = None
    slide: float = 0.0
    scale_steps: int = 0
    big: bool = False
    reset: bool = False


def _asset_or_404(db: Session, aid: str) -> Asset:
    a = db.get(Asset, aid)
    if a is None:
        raise HTTPException(404, "asset not found")
    return a


def _guess_type(text: str) -> str | None:
    found = detect(text, "humanoid")["items"]
    return found[0]["type"] if found else None


@router.get("/api/item-words")
def item_words() -> dict[str, Any]:
    return public_words()


@router.post("/api/items/detect")
def detect_items(body: DetectIn) -> dict[str, Any]:
    out = detect(body.prompt, body.category)
    if out["items"]:
        out["sentence"] = sentence(body.prompt, out["items"])
    return out


def sentence(prompt: str, items: list[dict[str, Any]]) -> str:
    """The quiet line on the asset form (research 6.2)."""
    names = [i["label"] for i in items]
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    held = next((i["label"] for i in items if item_type(i["type"]).get("kind") in ("blade", "haft", "pole", "bow", "shield")), None)
    tail = f" That way they can really hold the {held} when they fight." if held else " That way each one can be moved, swapped or taken off."
    what = f"the {listed} as a separate item and put it on" if len(names) == 1 else f"the {listed} as separate items and put them on"
    return f"These work best as their own asset. We will make the character empty-handed, then make {what}.{tail}"


@router.get("/api/assets/{aid}/items")
def get_items(aid: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    return panel(db, _asset_or_404(db, aid))


@router.post("/api/assets/{aid}/items", status_code=201)
async def add_item(aid: str, body: AddItemIn, db: Session = Depends(get_session)) -> dict[str, Any]:
    """Give the character an item: a new one (type and/or a one-line description; the style is the character's) or one
    made earlier (item_asset_id). The attachment goes to the usual place for the type unless socket is given. A new
    item is built by the autopilot when build is true."""
    from . import autopilot

    a = _asset_or_404(db, aid)
    if a.category not in sockets.SOCKET_CATEGORIES:
        raise HTTPException(400, "items go on characters and creatures with a skeleton")
    if body.socket and body.socket not in PLACES:
        raise HTTPException(400, f"unknown attachment point {body.socket!r}")
    eq = dict(a.equipment or {"choice": None, "set_aside": [], "attachments": []})
    eq.setdefault("attachments", [])
    if body.item_asset_id:
        it = _asset_or_404(db, body.item_asset_id)
        if not it.held_item:
            raise HTTPException(400, "that asset is not an item")
        if any(at.get("item_asset_id") == it.id for at in eq["attachments"]):
            raise HTTPException(409, "that item is already on this character")
    else:
        tid = body.type or _guess_type(body.description or "")
        if tid not in types():
            raise HTTPException(400, "say what the item is (for example: a short sword with a leather grip)")
        desc = (body.description or "").strip()
        it = make_item(db, a, tid, desc or None, item_prompt(tid, desc) if desc else None)
    att = new_attachment(it, a.category, body.socket)
    eq["attachments"] = [*eq["attachments"], att]
    a.equipment = eq
    db.commit()
    started = None
    if body.build and not body.item_asset_id:
        # When it is made, the character's files are rebuilt with it on (after_autopilot).
        _set_build(a.id, {"status": "items", "item": it.id})
        started = autopilot.start(it.id)
    return {"attachment": att, "item": asset_to_dict(it), "autopilot": started, "panel": panel(db, a)}


def _find_att(a: Asset, att_id: str) -> tuple[dict[str, Any], int]:
    for i, at in enumerate((a.equipment or {}).get("attachments") or []):
        if at.get("id") == att_id:
            return dict(at), i
    raise HTTPException(404, "attachment not found")


def _save_att(db: Session, a: Asset, i: int, at: dict[str, Any]) -> dict[str, Any]:
    eq = dict(a.equipment or {})
    atts = list(eq.get("attachments") or [])
    atts[i] = at
    eq["attachments"] = atts
    a.equipment = eq
    db.commit()
    return at


@router.put("/api/assets/{aid}/attachments/{att_id}")
def put_attachment(aid: str, att_id: str, body: AttachmentPut, db: Session = Depends(get_session)) -> dict[str, Any]:
    """Change where it goes (socket: back to the usual hold for that place), where it goes when put away, or exact
    numbers (Advanced)."""
    a = _asset_or_404(db, aid)
    at, i = _find_att(a, att_id)
    for s in (body.socket, body.stow_socket):
        if s and s not in PLACES:
            raise HTTPException(400, f"unknown attachment point {s!r}")
    if body.socket and body.socket != at.get("socket"):
        at["socket"] = body.socket
        at = reset(at)
    if body.stow_socket is not None:
        at["stow_socket"] = body.stow_socket or None
    if body.position is not None:
        if len(body.position) != 3 or any(abs(v) > 2 for v in body.position):
            raise HTTPException(400, "position is three numbers in metres, each within 2 m")
        at["position"] = [round(float(v), 5) for v in body.position]
    if body.rotation_deg is not None:
        if len(body.rotation_deg) != 3:
            raise HTTPException(400, "rotation_deg is three angles in degrees")
        at["rotation"] = euler_to_quat(body.rotation_deg)
        at["rotation_deg"] = quat_to_euler(at["rotation"])
    if body.scale is not None:
        at["scale"] = round(float(min(SCALE_MAX, max(SCALE_MIN, body.scale))), 4)
    _save_att(db, a, i, at)
    return {"attachment": at, "panel": panel(db, a)}


@router.post("/api/assets/{aid}/attachments/{att_id}/nudge")
def nudge_attachment(aid: str, att_id: str, body: NudgeIn, db: Session = Depends(get_session)) -> dict[str, Any]:
    """One press of a nudge button: move, turn (character axes), slide along the item, Smaller or Bigger, or Start
    again (reset)."""
    a = _asset_or_404(db, aid)
    at, i = _find_att(a, att_id)
    for v, n in ((body.move, "move"), (body.turn, "turn")):
        if v is not None and (len(v) != 3 or any(abs(x) > 20 for x in v)):
            raise HTTPException(400, f"{n} is three step counts")
    if body.reset:
        at = reset(at)
    else:
        doc, _ = character_sockets(db, a)
        at = nudge(at, socket_frame(doc, at["socket"]), body.move, body.turn, body.slide, body.scale_steps, body.big)
    _save_att(db, a, i, at)
    return {"attachment": at}


@router.delete("/api/assets/{aid}/attachments/{att_id}")
def delete_attachment(aid: str, att_id: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    """Take the item off the character (the item asset stays in the project)."""
    a = _asset_or_404(db, aid)
    at, i = _find_att(a, att_id)
    eq = dict(a.equipment or {})
    eq["attachments"] = [x for j, x in enumerate(eq.get("attachments") or []) if j != i]
    a.equipment = eq
    db.commit()
    return {"removed": at, "panel": panel(db, a)}


@router.post("/api/assets/{aid}/items/apply")
def apply_items(aid: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    """Save: rebuild the character's files with the items in place (an adjust of its kept export, same settings)."""
    from . import pipeline
    from .db import job_to_dict, version_to_dict

    a = _asset_or_404(db, aid)
    ex = pipeline.selected(db, a.id, "export")
    if ex is None or ex.status != "done":
        raise HTTPException(409, "finish the character first")
    v = pipeline.new_version(db, a, "export", ex, dict(ex.params))
    job = pipeline.submit(db, a, v, "adjust", {"params": {}})
    _set_build(a.id, {"status": "equipping", "version_id": v.id, "job_id": job.id})
    return {"job": job_to_dict(job), "version": version_to_dict(v)}


@router.post("/api/assets/{aid}/items/build")
async def build_items(aid: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    """Make every attached item that is not made yet, one after another, then rebuild the character with them on."""
    a = _asset_or_404(db, aid)
    if not (a.equipment or {}).get("attachments"):
        raise HTTPException(409, "this character has no items")
    _set_build(a.id, {"status": "items"})
    after_autopilot(a.id)
    db.expire_all()
    return panel(db, db.get(Asset, aid))
