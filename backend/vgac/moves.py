"""Moves: what a human-shaped character can do, and "Add a move".

Three sources of hand-made moves, all copied onto the character's own skeleton by blender/retarget.py:
  - pack 1 and pack 2 of the CC0 Quaternius Universal Animation Library (settings.clips_libraries ual1 and ual2;
    data/animations/*/License.txt says CC0 1.0, free for commercial games);
  - the person's own animation files ("Use my own animation file"): FBX, GLB or BVH with a human-shaped skeleton,
    kept in <library_path>/my_moves/<id>/ with the registry <library_path>/my_moves.json.

A character gets the category's default set (humanoid.yaml library_clips, all from pack 1). The person's changes are
two rig params, stored in the rig version so a re-run keeps them:
  added_moves:   [{"clip": "ual2:Sword_Block", "name": "sword_block", "loop": false}, ...]
  removed_moves: ["roll", ...]  (default or built-in move names taken off)
POST /api/assets/{aid}/moves makes a new rig version from the kept one with those params; the rig stage then only
makes the moves again (rig.reuse path), not the skeleton.

Catalogue: clip names and lengths are read from each pack's GLB header (no Blender). Every clip has a plain name
and a plain group (CLIP_TABLE). Previews: each clip rendered once on its pack's mannequin (blender/moves_preview.py)
as a strip of FRAMES square cells, cached in <library_path>/moves_previews/<pack>/<hash>.png and served through
/api/library/. A pack's previews are made in the background the first time the catalogue is asked for.

Own files: POST /api/my-moves/upload?name=<file> with the raw file as the body (streamed to a part file, size cap,
first bytes checked for FBX, GLB or BVH), then one Blender read (moves_preview.py) that recognises the skeleton
("We recognised 21 of the 22 main bones", or "This file's skeleton is not human-shaped, so we cannot use it"),
lists the clips and renders their previews. PUT names the clips, says which loop and records "Where it came from",
which travels with the clips into anim_clips.json and the export.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import struct
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings

log = logging.getLogger("vgac.moves")
router = APIRouter()

FRAMES = 8
CELL = 112
PREVIEW_DIR = "moves_previews"
MINE_DIR = "my_moves"
REGISTRY_FILE = "my_moves.json"
MINE_PREFIX = "mine_"
MAX_FILE_BYTES = 200 * 1024 ** 2
MAX_LABEL = 60
MAX_SOURCE = 300
KINDS = (".fbx", ".glb", ".bvh")
RESPONSIBILITY = "You are responsible for being allowed to use this file in your game."
LICENCE_PACK = "CC0 (Quaternius Universal Animation Library)"
LICENCE_MINE = "Your own file: not checked by the app. You are responsible for being allowed to use it in your game."
WRITE_CHUNK = 4 * 1024 * 1024
ID_RE = re.compile(r"^(ual1|ual2|mine_[a-z0-9_]{1,48})$")

PACK_LABELS = {"ual1": "Hand-made moves, pack 1", "ual2": "Hand-made moves, pack 2", "mine": "Yours"}

GROUPS = [
    ("standing", "Standing"), ("walking", "Walking and running"), ("jumping", "Jumping and rolling"),
    ("fighting", "Fighting"), ("sword", "Sword"), ("shield", "Shield"), ("pistol", "Pistol"), ("magic", "Magic"),
    ("hit", "Getting hit and falling"), ("using", "Using things"), ("work", "Work and farming"),
    ("sitting", "Sitting and resting"), ("talking", "Talking and gestures"), ("swimming", "Swimming"),
    ("climbing", "Climbing and sliding"), ("zombie", "Zombie"), ("mine", "Yours"),
]

# action -> (plain name, group). Every clip of both packs except the reference poses and the root-motion copies of
# clips that also come in place (HIDDEN).
CLIP_TABLE: dict[str, dict[str, tuple[str, str]]] = {
    "ual1": {
        "Idle_Loop": ("Stand still", "standing"), "Idle_Torch_Loop": ("Stand holding a torch", "standing"),
        "Crouch_Idle_Loop": ("Crouch", "standing"), "Sword_Idle": ("Stand with a sword ready", "sword"),
        "Walk_Loop": ("Walk", "walking"), "Walk_Formal_Loop": ("Walk tall", "walking"), "Jog_Fwd_Loop": ("Run", "walking"),
        "Sprint_Loop": ("Sprint", "walking"), "Crouch_Fwd_Loop": ("Crouch walk", "walking"),
        "Jump_Start": ("Jump (take off)", "jumping"), "Jump_Loop": ("Jump (in the air)", "jumping"),
        "Jump_Land": ("Jump (land)", "jumping"), "Roll": ("Roll", "jumping"),
        "Punch_Enter": ("Raise fists", "fighting"), "Punch_Jab": ("Punch (jab)", "fighting"), "Punch_Cross": ("Punch (cross)", "fighting"),
        "Sword_Attack": ("Sword attack", "sword"),
        "Pistol_Idle_Loop": ("Stand with a pistol", "pistol"), "Pistol_Aim_Neutral": ("Aim a pistol ahead", "pistol"),
        "Pistol_Aim_Up": ("Aim a pistol up", "pistol"), "Pistol_Aim_Down": ("Aim a pistol down", "pistol"),
        "Pistol_Shoot": ("Shoot a pistol", "pistol"), "Pistol_Reload": ("Reload a pistol", "pistol"),
        "Spell_Simple_Enter": ("Raise hands for a spell", "magic"), "Spell_Simple_Idle_Loop": ("Hold a spell ready", "magic"),
        "Spell_Simple_Shoot": ("Cast a spell", "magic"), "Spell_Simple_Exit": ("Lower hands after a spell", "magic"),
        "Hit_Chest": ("Get hit in the chest", "hit"), "Hit_Head": ("Get hit on the head", "hit"), "Death01": ("Die", "hit"),
        "Interact": ("Use something", "using"), "PickUp_Table": ("Pick up from a table", "using"),
        "Push_Loop": ("Push something heavy", "using"), "Fixing_Kneeling": ("Kneel and fix something", "work"),
        "Sitting_Enter": ("Sit down on a chair", "sitting"), "Sitting_Idle_Loop": ("Sit on a chair", "sitting"),
        "Sitting_Talking_Loop": ("Sit and talk", "sitting"), "Sitting_Exit": ("Stand up from a chair", "sitting"),
        "Driving_Loop": ("Drive (sitting, hands on a wheel)", "sitting"),
        "Idle_Talking_Loop": ("Stand and talk", "talking"), "Dance_Loop": ("Dance", "talking"),
        "Swim_Idle_Loop": ("Tread water", "swimming"), "Swim_Fwd_Loop": ("Swim", "swimming"),
    },
    "ual2": {
        "Idle_FoldArms_Loop": ("Stand with arms folded", "standing"), "Idle_Lantern_Loop": ("Stand holding a lantern", "standing"),
        "Walk_Carry_Loop": ("Walk carrying something", "walking"),
        "NinjaJump_Start": ("Big jump (take off)", "jumping"), "NinjaJump_Idle_Loop": ("Big jump (in the air)", "jumping"),
        "NinjaJump_Land": ("Big jump (land)", "jumping"),
        "Melee_Hook": ("Hook punch", "fighting"), "Melee_Hook_Rec": ("Hook punch (recover)", "fighting"),
        "OverhandThrow": ("Throw overarm", "fighting"),
        "Sword_Block": ("Block with a sword", "sword"), "Sword_Regular_A": ("Sword slash 1", "sword"),
        "Sword_Regular_A_Rec": ("Sword slash 1 (recover)", "sword"), "Sword_Regular_B": ("Sword slash 2", "sword"),
        "Sword_Regular_B_Rec": ("Sword slash 2 (recover)", "sword"), "Sword_Regular_C": ("Sword slash 3 (big)", "sword"),
        "Sword_Regular_Combo": ("Sword combo (three slashes)", "sword"), "Sword_Dash_RM": ("Sword lunge", "sword"),
        "Idle_Shield_Loop": ("Stand behind a shield", "shield"), "Shield_OneShot": ("Shield bash", "shield"),
        "Idle_Shield_Break": ("Shield knocked aside", "shield"), "Shield_Dash_RM": ("Shield charge", "shield"),
        "Hit_Knockback": ("Get knocked back", "hit"), "LayToIdle": ("Get up from the ground", "hit"),
        "Chest_Open": ("Open a chest", "using"), "Consume": ("Eat or drink", "using"),
        "Farm_Harvest": ("Harvest crops", "work"), "Farm_PlantSeed": ("Plant seeds", "work"), "Farm_Watering": ("Water plants", "work"),
        "TreeChopping_Loop": ("Chop wood", "work"),
        "Idle_Rail_Loop": ("Lean on a rail", "sitting"), "Idle_Rail_Call": ("Lean on a rail and call out", "talking"),
        "Idle_TalkingPhone_Loop": ("Talk on a phone", "talking"), "Idle_No_Loop": ("Shake head (no)", "talking"), "Yes": ("Nod (yes)", "talking"),
        "ClimbUp_1m_RM": ("Climb up a ledge", "climbing"), "Slide_Start": ("Slide (start)", "climbing"),
        "Slide_Loop": ("Slide", "climbing"), "Slide_Exit": ("Slide (end)", "climbing"),
        "Zombie_Idle_Loop": ("Zombie stand", "zombie"), "Zombie_Walk_Fwd_Loop": ("Zombie walk", "zombie"),
        "Zombie_Scratch": ("Zombie claw attack", "zombie"),
    },
}
HIDDEN = {"A_TPose", "Roll_RM", "Sword_Attack_RM", "Hit_Knockback_RM"}
# Clips that travel: the hips leave the spot (root motion in the library, no in-place copy).
TRAVELS = {"ClimbUp_1m_RM", "Shield_Dash_RM", "Sword_Dash_RM"}
# Held poses and idles whose names do not say "Loop".
LOOP_EXTRA = {"Sword_Idle", "Pistol_Aim_Neutral", "Pistol_Aim_Up", "Pistol_Aim_Down"}
# Names for the built-in (procedural) moves a character keeps next to the library ones.
BUILT_IN_LABELS = {"turn_left": "Turn left", "turn_right": "Turn right", "idle": "Stand still", "walk": "Walk", "run": "Run",
                   "attack": "Attack", "hit": "Get hit", "death": "Die"}

_lock = threading.Lock()
_catalog_cache: dict[str, Any] = {}
_preview_state: dict[str, Any] = {"making": set(), "errors": {}}


class MovesError(ValueError):
    """A plain sentence for the person; `status` is the HTTP status."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# --- Names -------------------------------------------------------------------------------------------------------


def clip_name(action: str) -> str:
    """The name a library action gets on the character: 'Sitting_Idle_Loop' -> 'sitting_idle'."""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", action.split("|")[-1])
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower()
    s = re.sub(r"_(loop|rm)$", "", s)
    s = re.sub(r"_(loop|rm)$", "", s)
    return (s or "move")[:40]


_FILLER = {"a", "an", "the", "with", "on", "from", "of", "something"}


def name_from_label(label: str, fallback: str) -> str:
    """The clip name a move gets on the character from its plain name: 'Cast a spell' -> 'cast_spell', 'Stand behind
    a shield' -> 'stand_behind_shield', 'Climb up a ledge' -> 'climb_ledge'. It becomes the Unity parameter
    (CastSpell)."""
    words = [w for w in re.findall(r"[a-z0-9]+", label.lower()) if w not in _FILLER]
    s = "_".join(words)[:40].strip("_")
    return s or clip_name(fallback)


def humanise(action: str) -> str:
    """A readable name from a file's action or file name: 'Armature|mixamo.com|Layer0' -> '' (meaningless)."""
    a = action.split("|")[-1] if "|" in action else action
    if re.fullmatch(r"(?i)\s*(mixamo\.com|layer\s*\d*|take\s*\d*|scene|default|anim(ation)?|action|armature.*|unreal take|clip\s*\d*)\s*", a) \
            or not a.strip():
        return ""
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", a)
    s = re.sub(r"[_\-.|]+", " ", s)
    s = " ".join(s.split())[:MAX_LABEL]
    return s[:1].upper() + s[1:]


def guess_loop(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in ("loop", "idle", "walk", "run", "jog", "sprint", "swim", "breath", "dance", "crouch"))


def preview_name(action: str) -> str:
    return hashlib.sha1(action.encode("utf-8")).hexdigest()[:16] + ".png"


# --- Packs ---------------------------------------------------------------------------------------------------------


def pack_file(settings: Settings, pack: str) -> Path | None:
    """The GLB the retarget reads for a configured pack (rig.library_file), or None."""
    from .stages.rig import library_file

    return library_file((settings.clips_libraries or {}).get(pack))


def glb_clips(path: Path) -> list[tuple[str, float]]:
    """[(animation name, seconds)] from a GLB's JSON chunk, without loading it."""
    with open(path, "rb") as f:
        head = f.read(12)
        if len(head) < 12 or head[:4] != b"glTF":
            return []
        clen, ctype = struct.unpack("<II", f.read(8))
        doc = json.loads(f.read(clen))
    acc = doc.get("accessors") or []
    out = []
    for a in doc.get("animations") or []:
        try:
            secs = max(float(acc[s["input"]]["max"][0]) for s in a.get("samplers") or [])
        except (KeyError, IndexError, ValueError, TypeError):
            secs = 0.0
        out.append((str(a.get("name") or ""), round(secs, 3)))
    return out


def pack_clips(settings: Settings, pack: str) -> list[dict[str, Any]]:
    """Catalogue rows of one pack (cached until the file changes)."""
    p = pack_file(settings, pack)
    if p is None or not p.is_file():
        return []
    key = (str(p), p.stat().st_mtime_ns)
    hit = _catalog_cache.get(pack)
    if hit and hit[0] == key:
        return [dict(r) for r in hit[1]]
    table = CLIP_TABLE.get(pack, {})
    rows = []
    for action, secs in glb_clips(p):
        if not action or action in HIDDEN:
            continue
        label, group = table.get(action) or (humanise(action) or action, "using")
        rows.append({"id": f"{pack}:{action}", "pack": pack, "action": action, "label": label, "group": group,
                     "loop": action.lower().endswith("_loop") or action in LOOP_EXTRA, "seconds": secs,
                     "name": name_from_label(label, action), "moves_character": action in TRAVELS})
    _catalog_cache[pack] = (key, rows)
    return [dict(r) for r in rows]


def packs(settings: Settings) -> list[str]:
    return [p for p in ("ual1", "ual2") if pack_file(settings, p) is not None]


# --- Previews -------------------------------------------------------------------------------------------------------


def preview_dir(settings: Settings, pack: str) -> Path:
    return Path(settings.library_path) / PREVIEW_DIR / pack


def preview_url(settings: Settings, pack: str, action: str) -> str | None:
    p = preview_dir(settings, pack) / preview_name(action)
    return f"/api/library/{PREVIEW_DIR}/{pack}/{preview_name(action)}" if p.is_file() else None


def stitch(frames: list[Path], dest: Path, cell: int = CELL) -> Path:
    """Join a clip's frames into one strip, cells side by side."""
    from PIL import Image

    strip = Image.new("RGB", (cell * len(frames), cell), (210, 212, 216))
    for i, f in enumerate(frames):
        with Image.open(f) as im:
            strip.paste(im.convert("RGB").resize((cell, cell)), (cell * i, 0))
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp.png")
    strip.save(tmp, format="PNG", optimize=True)
    os.replace(tmp, dest)
    return dest


def render_previews(settings: Settings, src: Path, dest_dir: Path, actions: list[str] | None = None,
                    timeout: float = 900) -> dict[str, Any]:
    """Run moves_preview.py on `src`, stitch each clip's frames into dest_dir/<hash>.png. -> preview.json."""
    from .tools import run_blender

    work = Path(tempfile.mkdtemp(prefix="vgac_moves_", dir=str(Path(settings.library_path))))
    try:
        args: list[Any] = [src, work, "--frames", FRAMES, "--size", CELL]
        if actions:
            args += ["--actions", ",".join(actions)]
        try:
            run_blender(settings, "moves_preview.py", args, log_path=work / "moves_preview.log", timeout=timeout)
        except Exception as e:  # noqa: BLE001
            doc = json.loads((work / "preview.json").read_text(encoding="utf-8")) if (work / "preview.json").is_file() else {}
            doc.setdefault("error", str(e)[-500:])
            return doc
        doc = json.loads((work / "preview.json").read_text(encoding="utf-8"))
        for c in doc.get("clips") or []:
            files = [work / f for f in c.get("frames") or []]
            if files and all(f.is_file() for f in files):
                stitch(files, dest_dir / preview_name(c["action"]))
        return doc
    finally:
        shutil.rmtree(work, ignore_errors=True)


def ensure_pack_previews(settings: Settings, pack: str) -> None:
    """Make the missing previews of a pack in a background thread (once at a time per pack)."""
    with _lock:
        if pack in _preview_state["making"]:
            return
        missing = [r["action"] for r in pack_clips(settings, pack) if preview_url(settings, pack, r["action"]) is None]
        if not missing or _preview_state["errors"].get(pack, 0) >= 2:
            return
        _preview_state["making"].add(pack)
    src = pack_file(settings, pack)

    def work():
        try:
            doc = render_previews(settings, src, preview_dir(settings, pack), missing)
            if doc.get("error"):
                _preview_state["errors"][pack] = _preview_state["errors"].get(pack, 0) + 1
                log.warning("moves previews for %s: %s", pack, doc["error"])
        except Exception:  # noqa: BLE001
            _preview_state["errors"][pack] = _preview_state["errors"].get(pack, 0) + 1
            log.exception("moves previews for %s", pack)
        finally:
            with _lock:
                _preview_state["making"].discard(pack)

    threading.Thread(target=work, name=f"moves-previews-{pack}", daemon=True).start()


# --- Your own files: registry -----------------------------------------------------------------------------------------


def mine_root(settings: Settings | None = None) -> Path:
    return Path((settings or get_settings()).library_path) / MINE_DIR


def registry_path(settings: Settings | None = None) -> Path:
    return Path((settings or get_settings()).library_path) / REGISTRY_FILE


def _read(settings: Settings | None = None) -> list[dict[str, Any]]:
    p = registry_path(settings)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [f for f in (data.get("files") if isinstance(data, dict) else None) or [] if isinstance(f, dict) and ID_RE.match(str(f.get("id", "")))]


def _write(items: list[dict[str, Any]], settings: Settings | None = None) -> None:
    p = registry_path(settings)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps({"files": items}, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def mine_file_path(entry: dict[str, Any], settings: Settings | None = None) -> Path | None:
    """The stored file of an entry, only inside its own folder under my_moves/."""
    name = str(entry.get("file_name") or "")
    if not name or name != Path(name).name or ".." in name:
        return None
    p = mine_root(settings) / str(entry["id"]) / name
    return p if p.is_file() else None


def mine_public(entry: dict[str, Any], settings: Settings | None = None) -> dict[str, Any]:
    s = settings or get_settings()
    clips = []
    for c in entry.get("clips") or []:
        clips.append({"id": f"{entry['id']}:{c['action']}", "action": c["action"], "label": c.get("label") or c["action"],
                      "loop": bool(c.get("loop")), "seconds": c.get("seconds") or 0, "preview": preview_url(s, entry["id"], c["action"]),
                      "frames": FRAMES, "still": FRAMES // 2, "moves_character": bool(c.get("moves_character"))})
    sk = entry.get("skeleton") or {}
    from .blender import gait

    return {"id": entry["id"], "label": entry.get("label") or entry.get("file_name"), "file_name": entry.get("file_name"),
            "kind": entry.get("kind"), "source": entry.get("source") or "", "recognised": sk.get("recognised"),
            "total": sk.get("total"), "sentence": sk.get("sentence") or "", "missing": [gait.body_plain(b) for b in sk.get("missing") or []],
            "clips": clips, "added_at": entry.get("added_at") or ""}


def mine_entries(settings: Settings | None = None) -> list[dict[str, Any]]:
    return _read(settings)


def mine_entry(fid: str, settings: Settings | None = None) -> dict[str, Any] | None:
    return next((e for e in _read(settings) if e.get("id") == fid), None)


def safe_upload_name(raw: str | None) -> str:
    name = str(raw or "").strip()
    if not name:
        raise MovesError("The file has no name.")
    if any(c in name for c in "/\\:") or ".." in name or "\x00" in name:
        raise MovesError("The file name must be a plain file name, without folders.")
    stem, ext = os.path.splitext(name)
    if ext.lower() not in KINDS:
        raise MovesError("Use an FBX, GLB or BVH animation file.")
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", stem).strip(" .")
    stem = re.sub(r"_+", "_", stem)[:80] or "moves"
    if stem.split(".")[0].lower() in {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}:
        stem = "moves_" + stem
    return stem + ext.lower()


def sniff_kind(first: bytes, ext: str) -> str:
    """Check the first bytes match the kind the name says. -> 'fbx' | 'glb' | 'bvh', else MovesError."""
    head = first.lstrip(b"\xef\xbb\xbf")
    if ext == ".glb" and first[:4] == b"glTF":
        return "glb"
    if ext == ".fbx" and (first.startswith(b"Kaydara FBX Binary") or head.lstrip().startswith(b";") or b"FBXHeaderExtension" in first):
        return "fbx"
    if ext == ".bvh" and head.lstrip().upper().startswith(b"HIERARCHY"):
        return "bvh"
    raise MovesError("This file is not a readable " + ext.lstrip(".").upper() + " file. Use an FBX, GLB or BVH animation file.")


def _new_id(label: str, taken: set[str]) -> str:
    base = MINE_PREFIX + (re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:30].strip("_") or "moves")
    fid, i = base, 2
    while fid in taken:
        fid, i = f"{base}_{i}", i + 1
    return fid


async def save_upload(request: Request, raw_name: str, settings: Settings | None = None) -> dict[str, Any]:
    """Stream the body into my_moves/<id>/<name>, read it in Blender, keep it when its skeleton is human-shaped and
    it has moves. -> the public entry."""
    import asyncio

    s = settings or get_settings()
    name = safe_upload_name(raw_name)
    ext = os.path.splitext(name)[1].lower()
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_FILE_BYTES:
        raise MovesError(f"This file is larger than {MAX_FILE_BYTES // 1024 ** 2} MB. Animation files are much smaller.", 413)
    root = mine_root(s)
    root.mkdir(parents=True, exist_ok=True)
    stem = os.path.splitext(name)[0]
    with _lock:
        fid = _new_id(stem, {str(e.get("id")) for e in _read(s)} | {p.name for p in root.iterdir()})
        folder = root / fid
        folder.mkdir()
    part = folder / f".upload-{uuid.uuid4().hex}.part"
    total = 0
    first = b""
    ok = False
    try:
        with open(part, "xb") as f:
            buf = bytearray()
            async for chunk in request.stream():
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_FILE_BYTES:
                    raise MovesError(f"This file is larger than {MAX_FILE_BYTES // 1024 ** 2} MB. Animation files are much smaller.", 413)
                if len(first) < 512:
                    first += chunk[: 512 - len(first)]
                buf.extend(chunk)
                if len(buf) >= WRITE_CHUNK:
                    data, buf = bytes(buf), bytearray()
                    await asyncio.to_thread(f.write, data)
            if buf:
                await asyncio.to_thread(f.write, bytes(buf))
        if total == 0:
            raise MovesError("The file arrived empty. Try choosing it again.")
        kind = sniff_kind(first, ext)
        dest = folder / name
        os.rename(part, dest)
        doc = await asyncio.to_thread(render_previews, s, dest, preview_dir(s, fid), None, 300)
        sk = doc.get("skeleton") or {}
        if doc.get("error") and not sk:
            raise MovesError("The app could not read this file. It may be damaged, or saved in a way Blender cannot open.")
        if not sk.get("usable"):
            raise MovesError(sk.get("sentence") or "This file's skeleton is not human-shaped, so we cannot use it.")
        moving = [c for c in doc.get("clips") or [] if c.get("moves_bones")]
        if not moving:
            raise MovesError("This file has no moves in it. Choose a file with at least one animation.")
        single = len(moving) == 1
        clips = []
        for c in moving:
            label = humanise(c["action"]) if not single else (humanise(c["action"]) or humanise(stem))
            label = label or humanise(stem) or "My move"
            clips.append({"action": c["action"], "label": label[:MAX_LABEL], "loop": guess_loop(c["action"] + " " + stem),
                          "seconds": c.get("seconds") or 0, "moves_character": float(c.get("travel_m") or 0) > 0.5})
        entry = {"id": fid, "label": (humanise(stem) or stem)[:MAX_LABEL], "file_name": name, "kind": kind, "source": "",
                 "skeleton": {k: sk.get(k) for k in ("recognised", "total", "missing", "sentence", "usable")},
                 "clips": clips, "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        with _lock:
            items = _read(s)
            items.append(entry)
            _write(items, s)
        ok = True
        return mine_public(entry, s)
    finally:
        if part.exists():
            try:
                part.unlink()
            except OSError:
                pass
        if not ok:
            shutil.rmtree(folder, ignore_errors=True)
            shutil.rmtree(preview_dir(s, fid), ignore_errors=True)


def update_mine(fid: str, label: str | None, source: str | None, clips: list[dict[str, Any]] | None,
                settings: Settings | None = None) -> dict[str, Any]:
    s = settings or get_settings()
    with _lock:
        items = _read(s)
        e = next((x for x in items if x.get("id") == fid), None)
        if e is None:
            raise MovesError("That file is not on your list.", 404)
        if label is not None:
            label = " ".join(str(label).split())[:MAX_LABEL]
            if not label:
                raise MovesError("Give the file a name.")
            e["label"] = label
        if source is not None:
            e["source"] = " ".join(str(source).split())[:MAX_SOURCE]
        if clips is not None:
            by = {c["action"]: c for c in e.get("clips") or []}
            for c in clips:
                act = str(c.get("action") or "")
                if act not in by:
                    raise MovesError("That move is not in this file.")
                if c.get("label") is not None:
                    lab = " ".join(str(c["label"]).split())[:MAX_LABEL]
                    if not lab:
                        raise MovesError("Give every move a name.")
                    by[act]["label"] = lab
                if c.get("loop") is not None:
                    by[act]["loop"] = bool(c["loop"])
        _write(items, s)
    return mine_public(e, s)


def remove_mine(fid: str, settings: Settings | None = None) -> dict[str, Any]:
    s = settings or get_settings()
    with _lock:
        items = _read(s)
        keep = [x for x in items if x.get("id") != fid]
        if len(keep) == len(items):
            raise MovesError("That file is not on your list.", 404)
        _write(keep, s)
    if ID_RE.match(fid) and fid.startswith(MINE_PREFIX):
        shutil.rmtree(mine_root(s) / fid, ignore_errors=True)
        shutil.rmtree(preview_dir(s, fid), ignore_errors=True)
    return {"removed": fid}


# --- The catalogue -----------------------------------------------------------------------------------------------------


def catalogue(settings: Settings | None = None, make_previews: bool = True) -> dict[str, Any]:
    s = settings or get_settings()
    clips: list[dict[str, Any]] = []
    pk = []
    ready = total = 0
    for pack in packs(s):
        rows = pack_clips(s, pack)
        if make_previews:
            ensure_pack_previews(s, pack)
        for r in rows:
            r["preview"] = preview_url(s, pack, r["action"])
            r["frames"], r["still"] = FRAMES, FRAMES // 2
            ready += r["preview"] is not None
            total += 1
        clips += rows
        pk.append({"id": pack, "label": PACK_LABELS[pack], "licence": LICENCE_PACK, "count": len(rows)})
    mine = 0
    for e in mine_entries(s):
        pub = mine_public(e, s)
        for c in pub["clips"]:
            clips.append({"id": c["id"], "pack": "mine", "action": c["action"], "label": c["label"], "group": "mine", "loop": c["loop"],
                          "seconds": c["seconds"], "name": name_from_label(c["label"] or "", c["action"]), "moves_character": c["moves_character"],
                          "preview": c["preview"], "frames": FRAMES, "still": FRAMES // 2, "file": e["id"]})
            mine += 1
    pk.append({"id": "mine", "label": PACK_LABELS["mine"], "licence": LICENCE_MINE, "count": mine})
    return {"packs": pk, "groups": [{"id": g, "label": l} for g, l in GROUPS], "clips": clips,
            "previews": {"ready": ready, "total": total, "making": bool(_preview_state["making"])}}


def find_clip(clip_id: str, settings: Settings | None = None) -> dict[str, Any] | None:
    """A catalogue row by id ('ual2:Sword_Block', 'mine_x:Armature|mixamo.com|Layer0')."""
    s = settings or get_settings()
    pack, sep, action = str(clip_id).partition(":")
    if not sep or not ID_RE.match(pack):
        return None
    if pack.startswith(MINE_PREFIX):
        e = mine_entry(pack, s)
        if e is None:
            return None
        c = next((c for c in e.get("clips") or [] if c["action"] == action), None)
        if c is None:
            return None
        return {"id": clip_id, "pack": pack, "action": action, "label": c.get("label") or action, "loop": bool(c.get("loop")),
                "name": name_from_label(c.get("label") or "", action), "moves_character": bool(c.get("moves_character")),
                "origin": e.get("source") or "", "licence": LICENCE_MINE, "file_label": e.get("label")}
    return next((r for r in pack_clips(s, pack) if r["action"] == action), None)


# --- A character's moves -----------------------------------------------------------------------------------------------


def label_for(action: str) -> str | None:
    for table in CLIP_TABLE.values():
        if action in table:
            return table[action][0]
    return None


def default_rows(category: dict[str, Any]) -> list[dict[str, Any]]:
    """The category's default library moves (all from pack 1), in its order."""
    out = []
    for r in category.get("library_clips") or []:
        if not isinstance(r, dict) or not r.get("name"):
            continue
        src = r.get("from") or [r["name"]]
        acts = [str(a) for a in (src if isinstance(src, (list, tuple)) else [src])]
        lib = str(r.get("library") or "ual1")
        out.append({"name": str(r["name"]), "from": [{"library": lib, "action": a} for a in acts], "loop": bool(r.get("loop")),
                    "move": str(r.get("move") or r["name"]), "label": label_for(acts[0]) or str(r["name"]).replace("_", " ").capitalize(),
                    "added": False, "default": True, "clip": f"{lib}:{acts[0]}", "in_place": acts[0] not in TRAVELS,
                    "licence": LICENCE_PACK})
    return out


def _as_list(v: Any) -> list[Any]:
    return list(v) if isinstance(v, (list, tuple)) else []


def added_list(params: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for a in _as_list(params.get("added_moves")):
        if isinstance(a, dict) and a.get("clip") and a.get("name"):
            out.append({"clip": str(a["clip"]), "name": str(a["name"]), "loop": bool(a.get("loop"))})
        elif isinstance(a, str) and ":" in a:
            out.append({"clip": a, "name": clip_name(a.partition(":")[2]), "loop": guess_loop(a)})
    return out


def removed_list(params: dict[str, Any]) -> list[str]:
    return [str(x) for x in _as_list(params.get("removed_moves")) if isinstance(x, str)]


def move_rows(category: dict[str, Any], params: dict[str, Any], chosen_moves: Any = None,
              settings: Settings | None = None) -> list[dict[str, Any]]:
    """Every library move to make for this character: the defaults (narrowed by the moves knob when it was set,
    minus the removed ones), then the added ones with their library, label, loop and origin."""
    removed = set(removed_list(params))
    rows = [r for r in default_rows(category) if r["name"] not in removed]
    if chosen_moves is not None:
        chosen = {str(m).strip().lower() for m in (chosen_moves if isinstance(chosen_moves, (list, tuple)) else str(chosen_moves).split(","))}
        rows = [r for r in rows if r["move"].lower() in chosen]
    names = {r["name"] for r in rows}
    for a in added_list(params):
        if a["name"] in names or a["name"] in removed:
            continue
        pack, _, action = a["clip"].partition(":")
        row = {"name": a["name"], "from": [{"library": pack, "action": action}], "loop": a["loop"], "move": a["name"],
               "label": a["name"].replace("_", " ").capitalize(), "added": True, "default": False, "clip": a["clip"],
               "in_place": action not in TRAVELS}
        if pack.startswith(MINE_PREFIX):
            info = find_clip(a["clip"], settings)
            row["licence"] = LICENCE_MINE
            if info is not None:
                row["label"] = info.get("label") or row["label"]
                row["origin"] = info.get("origin") or ""
                row["in_place"] = not info.get("moves_character")
        else:
            row["label"] = label_for(action) or row["label"]
            row["licence"] = LICENCE_PACK
        rows.append(row)
        names.add(a["name"])
    return rows


def libraries_for(rows: list[dict[str, Any]], settings: Settings) -> tuple[list[tuple[str, Path]], dict[str, str]]:
    """([(library id, file)], {library id: reason it is missing}) for the libraries the rows use."""
    want = list(dict.fromkeys(c["library"] for r in rows for c in r["from"]))
    out, missing = [], {}
    for lid in want:
        if lid.startswith(MINE_PREFIX):
            e = mine_entry(lid, settings)
            p = mine_file_path(e, settings) if e else None
            if p is None:
                missing[lid] = "the file it came from is no longer on your list"
                continue
        else:
            p = pack_file(settings, lid)
            if p is None:
                missing[lid] = f"clip library {lid} not found on disk"
                continue
        out.append((lid, p))
    return out, missing


def source_records(rows: list[dict[str, Any]], settings: Settings | None = None) -> list[dict[str, Any]]:
    """Where the made moves came from, as the export manifest lists models (metrics.models of the rig version): one
    record per pack, and per own file with its "Where it came from" text and the responsibility line."""
    from . import reference

    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        for c in r.get("from") or []:
            lid = c.get("library")
            if not lid or lid in out:
                continue
            if str(lid).startswith(MINE_PREFIX):
                e = mine_entry(lid, settings) or {}
                out[lid] = {"id": lid, "name": f"{e.get('label') or lid} (your own animation file {e.get('file_name') or ''})".replace(" )", ")"),
                            "file": e.get("file_name") or "", "licence": LICENCE_MINE, "version": "", "source": e.get("source") or "",
                            "role": "moves"}
            else:
                out[lid] = {**reference.model_record(lid), "role": "moves"}
            break
    return list(out.values())


def plain_reason(text: str) -> str:
    """retarget.py's reason a clip was not made, in plain words."""
    t = str(text or "")
    if t.startswith("bones not found"):
        return "its skeleton is missing bones this character needs"
    if t == "not in the library":
        return "it is no longer in the library"
    return t


def spec_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The JSON retarget.py --spec reads."""
    keys = ("name", "from", "loop", "added", "label", "origin", "licence", "in_place")
    return [{k: r[k] for k in keys if k in r} for r in rows]


def moves_metrics(sidecar: dict[str, Any] | None, rows: list[dict[str, Any]], removed: list[str]) -> list[dict[str, Any]]:
    """metrics.moves: every move the character has (from anim_clips.json), then the asked-for ones that could not be
    made (made: false)."""
    by_row = {r["name"]: r for r in rows}
    out = []
    for c in (sidecar or {}).get("clips") or []:
        n = c.get("name")
        if not n:
            continue
        r = by_row.get(n)
        lib = c.get("library")
        if c.get("source") == "library":
            src = "mine" if str(lib or "").startswith(MINE_PREFIX) else "library"
            clip = f"{lib}:{c.get('from')}" if lib else (r or {}).get("clip")
            label = c.get("label") or (r or {}).get("label") or label_for(str(c.get("from") or "")) or n.replace("_", " ").capitalize()
        else:
            src, clip = "built-in", None
            label = BUILT_IN_LABELS.get(n) or n.replace("_", " ").capitalize()
        out.append({"name": n, "label": label, "clip": clip, "source": src, "loop": bool(c.get("loop")),
                    "added": bool((r or {}).get("added") or c.get("added")), "default": bool((r or {}).get("default")) or src == "built-in",
                    "made": True})
    have = {m["name"] for m in out}
    for r in rows:
        if r["name"] not in have and r["name"] not in removed:
            out.append({"name": r["name"], "label": r["label"], "clip": r.get("clip"), "source": "mine" if str(r.get("clip", "")).startswith(MINE_PREFIX) else "library",
                        "loop": r["loop"], "added": r["added"], "default": r["default"], "made": False})
    return out


# --- API ---------------------------------------------------------------------------------------------------------------


def _http(e: MovesError) -> HTTPException:
    return HTTPException(e.status, str(e))


def _db():
    from .db import get_session

    yield from get_session()


def _can_add(asset: Any) -> tuple[bool, str]:
    from . import reference

    try:
        cat = reference.category(asset.category)
    except Exception:  # noqa: BLE001
        cat = {}
    if cat.get("rig_engine") in (None, "none"):
        return False, "This model has no skeleton, so it does not move."
    if cat.get("rig_engine") != "skintoken_mixamo" or not cat.get("library_clips"):
        return False, "The hand-made moves only fit human-shaped characters, so this one keeps the moves made for its own skeleton."
    return True, ""


PLAIN_MOVE = {"idle": "stand still", "walk": "walk", "run": "run", "turn_left": "turn", "turn_right": "turn", "attack": "attack",
              "hit": "get hit", "death": "die", "flap": "fly", "glide": "glide", "takeoff": "take off", "land": "land",
              "slither": "slither"}


def creature_moves(category_id: str) -> list[str]:
    """What a creature gets instead of the hand-made moves: its category's built-in moves, in plain words."""
    from . import reference

    try:
        clips = reference.category(category_id).get("clips") or []
    except Exception:  # noqa: BLE001
        clips = []
    out = list(dict.fromkeys(PLAIN_MOVE.get(str(c), str(c).replace("_", " ")) for c in clips))
    return out


def _rig_versions(db: Session, aid: str) -> list[Any]:
    from .db import Version

    return list(db.scalars(select(Version).where(Version.asset_id == aid, Version.stage == "rig")).all())


def asset_moves(db: Session, asset: Any, settings: Settings | None = None) -> dict[str, Any]:
    from . import reference
    from .db import Asset

    s = settings or get_settings()
    can, reason = _can_add(asset)
    versions = _rig_versions(db, asset.id)
    kept = next((v for v in versions if v.selected), None)
    busy = any(v.status in ("queued", "running") for v in versions)
    out: dict[str, Any] = {"can_add": can, "reason": reason, "instead": [] if can else creature_moves(asset.category),
                           "rig_version": kept.id if kept is not None and kept.status == "done" else None, "busy": busy,
                           "moves": [], "added": [], "removed": [], "others": []}
    if kept is None:
        return out
    params, metrics = kept.params or {}, kept.metrics or {}
    out["added"], out["removed"] = added_list(params), removed_list(params)
    if isinstance(metrics.get("moves"), list):
        out["moves"] = metrics["moves"]
    else:
        # Versions made before "Add a move": from the sidecar.
        from . import library

        side = library.version_file(kept, "anim_clips", s)
        doc = json.loads(Path(side).read_text(encoding="utf-8")) if side else {}
        cat = reference.category(asset.category) if can else {}
        out["moves"] = moves_metrics(doc, move_rows(cat, params, None, s), out["removed"]) if doc else []
    if can:
        others = db.scalars(select(Asset).where(Asset.project_id == asset.project_id, Asset.id != asset.id)).all()
        for o in others:
            if not _can_add(o)[0]:
                continue
            ov = next((v for v in _rig_versions(db, o.id) if v.selected), None)
            if ov is None:
                continue
            ad, rm = added_list(ov.params or {}), removed_list(ov.params or {})
            if ad or rm:
                out["others"].append({"asset_id": o.id, "name": o.name, "added": len(ad), "removed": len(rm)})
    return out


class MovesIn(BaseModel):
    add: list[str] = Field(default_factory=list, max_length=100)
    remove: list[str] = Field(default_factory=list, max_length=100)
    copy_from: str | None = Field(default=None, max_length=64)


def change_moves(db: Session, asset: Any, body: MovesIn, settings: Settings | None = None) -> dict[str, Any]:
    """New added/removed lists from the kept rig version and the request, then a new rig version that remakes only the
    moves. -> {job, version}."""
    from . import pipeline
    from .db import Asset, job_to_dict, version_to_dict

    s = settings or get_settings()
    can, reason = _can_add(asset)
    if not can:
        raise MovesError(reason)
    versions = _rig_versions(db, asset.id)
    if any(v.status in ("queued", "running") for v in versions):
        raise MovesError("The moves are already being made again. Wait until that finishes.", 409)
    kept = next((v for v in versions if v.selected), None)
    if kept is None or kept.status != "done":
        raise MovesError("Make the skeleton first (the Make it move step), then add moves.", 409)
    if (kept.metrics or {}).get("rigid"):
        raise MovesError("This model has no skeleton, so it cannot take moves.", 409)
    params = dict(kept.params or {})
    added, removed = added_list(params), removed_list(params)
    if body.copy_from:
        other = db.get(Asset, body.copy_from)
        if other is None or other.project_id != asset.project_id or not _can_add(other)[0]:
            raise MovesError("That character is not in this project, or it is not human-shaped.")
        ov = next((v for v in _rig_versions(db, other.id) if v.selected), None)
        if ov is None:
            raise MovesError("That character has no moves yet.")
        added = [a for a in added_list(ov.params or {}) if find_clip(a["clip"], s) is not None]
        removed = removed_list(ov.params or {})
    from . import reference

    cat = reference.category(asset.category)
    current = {m.get("name") for m in asset_moves(db, asset, s)["moves"]} | {r["name"] for r in default_rows(cat)} | {a["name"] for a in added}
    for cid in body.add:
        info = find_clip(cid, s)
        if info is None:
            raise MovesError("One of the chosen moves is not in the library any more. Open the list again.")
        if any(a["clip"] == cid for a in added):
            continue
        dflt = next((r for r in default_rows(cat) if r["clip"] == cid), None)
        if dflt is not None:  # a default move taken off earlier: put it back
            if dflt["name"] in removed:
                removed.remove(dflt["name"])
            continue
        name, k = info["name"], 2
        while name in current:
            name, k = f"{info['name']}_{k}", k + 1
        current.add(name)
        added.append({"clip": cid, "name": name, "loop": bool(info.get("loop"))})
    for n in body.remove:
        if any(a["name"] == n for a in added):
            added = [a for a in added if a["name"] != n]
        elif n in current or n in ("turn_left", "turn_right"):
            if n not in removed:
                removed.append(n)
        else:
            raise MovesError("That move is not on this character.")
    if not body.add and not body.remove and not body.copy_from:
        raise MovesError("Choose a move to add or take off first.")
    left = [m for m in current if m not in removed] + [a["name"] for a in added]
    if not left:
        raise MovesError("A character needs at least one move.")
    new_params = {"added_moves": added, "removed_moves": removed}
    v = pipeline.new_version(db, asset, "rig", kept, {**params, **new_params})
    job = pipeline.submit(db, asset, v, "adjust", {"params": {**new_params, "_keep_when_done": True}})
    return {"job": job_to_dict(job), "version": version_to_dict(v)}


@router.get("/api/moves/library")
def get_library() -> dict[str, Any]:
    return catalogue()


@router.get("/api/assets/{aid}/moves")
def get_asset_moves(aid: str, db: Session = Depends(_db)) -> dict[str, Any]:
    from .db import Asset

    a = db.get(Asset, aid)
    if a is None:
        raise HTTPException(404, "asset not found")
    return asset_moves(db, a)


@router.post("/api/assets/{aid}/moves")
def post_asset_moves(aid: str, body: MovesIn, db: Session = Depends(_db)) -> dict[str, Any]:
    from .db import Asset

    a = db.get(Asset, aid)
    if a is None:
        raise HTTPException(404, "asset not found")
    try:
        return change_moves(db, a, body)
    except MovesError as e:
        raise _http(e)


@router.get("/api/my-moves")
def get_my_moves() -> dict[str, Any]:
    return {"files": [mine_public(e) for e in mine_entries()], "responsibility": RESPONSIBILITY,
            "max_mb": MAX_FILE_BYTES // 1024 ** 2, "kinds": list(KINDS)}


@router.post("/api/my-moves/upload")
async def post_my_moves_upload(request: Request, name: str = Query(..., max_length=400)) -> dict[str, Any]:
    try:
        return await save_upload(request, name)
    except MovesError as e:
        raise _http(e)


class MyMoveClipIn(BaseModel):
    action: str = Field(max_length=400)
    label: str | None = Field(default=None, max_length=MAX_LABEL * 2)
    loop: bool | None = None


class MyMoveIn(BaseModel):
    label: str | None = Field(default=None, max_length=MAX_LABEL * 2)
    source: str | None = Field(default=None, max_length=MAX_SOURCE * 2)
    clips: list[MyMoveClipIn] | None = Field(default=None, max_length=500)


@router.put("/api/my-moves/{fid}")
def put_my_move(fid: str, body: MyMoveIn) -> dict[str, Any]:
    try:
        return update_mine(fid, body.label, body.source, [c.model_dump() for c in body.clips] if body.clips is not None else None)
    except MovesError as e:
        raise _http(e)


@router.delete("/api/my-moves/{fid}")
def delete_my_move(fid: str) -> dict[str, Any]:
    try:
        return remove_mine(fid)
    except MovesError as e:
        raise _http(e)
