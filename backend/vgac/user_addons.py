"""Your own add-ons: LoRA files the person brings in, next to the tested ones in data/addons.yaml.

The built-in list stays in data/addons.yaml (tracked in git). The person's own entries live in the library folder
(<library_path>/my_addons.json) and reference.addons() / reference.addon() merge them in, so the concept catalog,
resolve_addons, addon_record and the style lock all see them. Every user entry has `mine: true`, an id that starts
with "mine_", and is dropped when its id matches a built-in one: a user entry never shadows a built-in add-on.

The files: one folder the app owns, VGAC/mine/ under ComfyUI's loras folder (settings.loras_dir). A user entry's
`file` is always "VGAC/mine/<name>.safetensors". PRIVACY: this folder is the only part of ComfyUI's model folders the
app ever lists or reads. Nothing here walks up from it, lists a sibling or follows a name with a folder in it.

Two ways in, both ending in the same short form (POST /api/my-addons):
  - upload: POST /api/my-addons/upload?name=<file name> with the raw file as the body. It is streamed to a hidden
    part file in VGAC/mine/ (never held in memory), checked as a safetensors LoRA as it arrives (8-byte header
    length, JSON header, tensor offsets, LoRA tensor names), and renamed into place only when complete. An existing
    file is never overwritten.
  - already on this computer: POST /api/my-addons/open-folder opens VGAC/mine/ in the file explorer, and
    GET /api/my-addons/unregistered lists the files there that are not set up yet.
  - on this computer, in the desktop app (addon_links.py): POST /api/my-addons/place with the real path the native picker
    returned. A file already in a folder ComfyUI reads for LoRAs is registered where it is; one on the same drive as
    VGAC/mine/ is hard-linked there; anything else needs the person to say Copy (never a junction or a symlink, and
    ComfyUI's settings are never changed). The file is accepted only when ComfyUI lists it. What the app made (a link or a
    copy) is recorded in <library_path>/my_addons_made.json and copied into the entry as `placement`; removing the LoRA
    takes away only that, never an original and never a file the app did not make.
The form is prefilled from the file's header metadata (prefill()): a title, the painter when the metadata names it,
and trigger words. Tensor data is never read.
"""

from __future__ import annotations

import json
import os
import re
import struct
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .config import get_settings

router = APIRouter()

REGISTRY_FILE = "my_addons.json"
MADE_FILE = "my_addons_made.json"
MINE_PARTS = ("VGAC", "mine")
MINE_PREFIX = "VGAC/mine/"
ID_PREFIX = "mine_"
EXT = ".safetensors"
MAX_FILE_BYTES = 4 * 1024 ** 3  # generous: LoRAs are usually 10 MB to 1 GB
MAX_HEADER_BYTES = 100_000_000  # the safetensors format's own limit on the JSON header
MAX_NAME_CHARS = 120
MAX_LABEL_CHARS = 80
MAX_TRIGGER_CHARS = 300
MAX_SOURCE_CHARS = 500
WRITE_CHUNK = 4 * 1024 * 1024
# The three words used everywhere, at the strengths a downloaded add-on is usually made for.
STRENGTHS = {"subtle": 0.6, "normal": 0.8, "strong": 1.0}
LICENCE_MINE = "Your own file: not checked by the app. You are responsible for being allowed to use it in your game."
WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
# Tensor names of a LoRA (kohya, diffusers/PEFT, LyCORIS, ComfyUI diff); a whole painter model has none of these.
LORA_KEY = re.compile(r"lora|lokr|hada_|\.alpha$|dora_scale|\.diff(_b)?$|\.oft_", re.IGNORECASE)

_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "items": []}


class AddonFileError(ValueError):
    """A file that cannot be an add-on, with a plain message for the person. `status` is the HTTP status."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# --- Where things live -------------------------------------------------------------------------------------------


def registry_path() -> Path:
    return Path(get_settings().library_path) / REGISTRY_FILE


def made_path() -> Path:
    return Path(get_settings().library_path) / MADE_FILE


def _read_made() -> dict[str, dict[str, Any]]:
    try:
        d = json.loads(made_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in d.items() if isinstance(v, dict)} if isinstance(d, dict) else {}


def made_record(key: str) -> dict[str, Any] | None:
    """What the app made for a file: {mode: in_place|link|copy, source, path, size, file}, or None for a file it did not make.
    Keys: "mine:<name>" for a file in VGAC/mine/, "place:<listed name>" for a file used where it is."""
    return _read_made().get(key)


def record_made(key: str, rec: dict[str, Any]) -> None:
    with _lock:
        d = _read_made()
        d[key] = rec
        _write_json(made_path(), d)


def forget_made(key: str) -> None:
    with _lock:
        d = _read_made()
        if d.pop(key, None) is not None:
            _write_json(made_path(), d)


def _write_json(p: Path, data: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def mine_dir(create: bool = False) -> Path:
    """VGAC/mine/ under ComfyUI's loras folder: the one folder of the model folders the app may list."""
    root = get_settings().loras_dir
    if root is None:
        raise AddonFileError("ComfyUI's LoRA folder is not set up yet (loras_dir in this machine's settings, "
                             "local/machine.json), so your own add-ons cannot be kept.", status=409)
    d = Path(root).joinpath(*MINE_PARTS)
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def file_value(name: str) -> str:
    """The `file` of a user add-on: a path under ComfyUI's loras folder, forward slashes."""
    return MINE_PREFIX + name


# --- File names --------------------------------------------------------------------------------------------------


def safe_name(raw: str | None) -> str:
    """The file name to save as, or AddonFileError. Refuses folder parts and other path tricks outright (it never
    quietly strips them), and anything that is not a .safetensors file. Odd characters become underscores."""
    name = str(raw or "").strip()
    if not name:
        raise AddonFileError("The file has no name.")
    if any(c in name for c in "/\\:") or ".." in name or "\x00" in name:
        raise AddonFileError("The file name must be a plain file name, without folders.")
    if not name.lower().endswith(EXT):
        raise AddonFileError("LoRAs must be .safetensors files. Other kinds of model file are not accepted.")
    stem = name[: -len(EXT)]
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", stem).strip(" .")
    stem = re.sub(r"_+", "_", stem)
    if not stem or stem.startswith("."):
        raise AddonFileError("The file name must start with a letter or a number.")
    if stem.split(".")[0].lower() in WINDOWS_RESERVED:
        stem = "addon_" + stem
    if len(stem) + len(EXT) > MAX_NAME_CHARS:
        stem = stem[: MAX_NAME_CHARS - len(EXT)].rstrip(" .")
    return stem + EXT


def _existing_file(name: str) -> Path:
    """A file already in VGAC/mine/ by its plain name (no folders), or AddonFileError."""
    if not name or name != Path(name).name or any(c in name for c in "/\\:") or ".." in name:
        raise AddonFileError("The file name must be a plain file name, without folders.")
    if not name.lower().endswith(EXT):
        raise AddonFileError("LoRAs must be .safetensors files.")
    p = mine_dir() / name
    if not p.is_file():
        raise AddonFileError("That file is not in the LoRA folder.", status=404)
    return p


# --- The safetensors header ---------------------------------------------------------------------------------------


def header_length(first8: bytes) -> int:
    """The JSON header length from the first 8 bytes (little-endian u64), or AddonFileError."""
    if len(first8) < 8:
        raise AddonFileError("This file is too short to be a safetensors file.")
    n = struct.unpack("<Q", first8[:8])[0]
    if n < 2 or n > MAX_HEADER_BYTES:
        raise AddonFileError("This is not a valid safetensors file (its header is damaged).")
    return n


def parse_header(raw: bytes) -> tuple[dict[str, str], dict[str, dict[str, Any]], int]:
    """The JSON header -> (metadata, tensors, data length in bytes). Checks the shape of every tensor entry and that
    it holds LoRA tensors; AddonFileError otherwise."""
    try:
        text = raw.decode("utf-8")
        header = json.loads(text)
    except (UnicodeDecodeError, ValueError):
        raise AddonFileError("This is not a valid safetensors file (its header is not readable).")
    if not isinstance(header, dict):
        raise AddonFileError("This is not a valid safetensors file (its header is not readable).")
    meta_raw = header.pop("__metadata__", None) or {}
    meta = {str(k): v if isinstance(v, str) else json.dumps(v) for k, v in meta_raw.items()} if isinstance(meta_raw, dict) else {}
    end = 0
    for key, t in header.items():
        if not isinstance(t, dict) or not isinstance(t.get("dtype"), str) or not isinstance(t.get("shape"), list):
            raise AddonFileError("This is not a valid safetensors file (a tensor entry is damaged).")
        off = t.get("data_offsets")
        if (not isinstance(off, list) or len(off) != 2 or not all(isinstance(x, int) and not isinstance(x, bool) for x in off)
                or off[0] < 0 or off[1] < off[0]):
            raise AddonFileError("This is not a valid safetensors file (a tensor entry is damaged).")
        end = max(end, off[1])
    if not header:
        raise AddonFileError("This safetensors file holds no data.")
    if not any(LORA_KEY.search(k) for k in header):
        raise AddonFileError("This file does not look like a LoRA (style add-on). It may be a whole image model, which "
                             "belongs in a different folder and cannot be added here.")
    return meta, header, end


def read_header(path: Path) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """Read and check only the header of a file in VGAC/mine/ (never the tensor data)."""
    size = path.stat().st_size
    with open(path, "rb") as f:
        n = header_length(f.read(8))
        if 8 + n > size:
            raise AddonFileError("This is not a valid safetensors file (it is cut short).")
        meta, tensors, data_len = parse_header(f.read(n))
    if 8 + n + data_len != size:
        raise AddonFileError("This is not a valid safetensors file (it is cut short or has extra bytes).")
    return meta, tensors


# --- Prefill from the header metadata -------------------------------------------------------------------------------

# Metadata fields that name what the add-on was trained on; and fields that name the add-on itself.
ARCH_FIELDS = ("modelspec.architecture", "ss_base_model_version", "modelspec.base_model", "base_model", "baseModel",
               "ss_sd_model_name", "ss_base_model", "base_model_name")
NAME_FIELDS = ("modelspec.title", "name", "ss_output_name")
TRIGGER_FIELDS = ("modelspec.trigger_phrase", "trigger_phrase", "trigger_words", "trigger_word", "ss_trigger",
                  "activation_text", "instance_prompt")
UNSURE = "We could not tell which model this was made for."
QWEN_EDIT_FAMILY = "Qwen-Image-Edit"
QWEN_FAMILY = "Qwen-Image"
# Qwen-Image-Edit names: "qwen-image-edit", "qwen-edit", and the 2511 release as "qie2511" (not after a letter, so
# "my_qie2511_x" matches; \b would not fire after an underscore).
QWEN_EDIT_RE = re.compile(r"qwen[-_ ]?(image[-_ ]?)?edit|(?<![a-z])qie[-_ ]?\d{4}")
QWEN_RE = re.compile(r"qwen[-_ ]?image")


def qwen_kind(text: str) -> str:
    """"Qwen-Image-Edit" or "Qwen-Image" when the text (lower case) names one, else ""."""
    if QWEN_EDIT_RE.search(text):
        return QWEN_EDIT_FAMILY
    if QWEN_RE.search(text):
        return QWEN_FAMILY
    return ""


# What a LoRA was made for, when the file says so for sure. Training tools write ss_base_model_version "qwen_image" on
# edit LoRAs too, so that key alone is not proof of plain Qwen-Image.
MADE_FOR_EDIT = "qwen_image_edit"
MADE_FOR_PLAIN = "qwen_image"
PLAIN_FIELDS = ("modelspec.architecture", "modelspec.base_model", "base_model", "baseModel", "ss_base_model", "base_model_name",
                "ss_sd_model_name")


def edit_signal(text: str) -> bool:
    """An Edit signal anywhere in the name or header: Qwen-Image-Edit, QwenEdit, qwen_edit, a 2509/2511 release
    named with Edit, in any case. (Lower-case text.)"""
    if QWEN_EDIT_RE.search(text):
        return True
    return ("qwen" in text or QWEN_RE.search(text) is not None) and "edit" in text


def qwen_made_for(meta: dict[str, str], file_name: str) -> str:
    """MADE_FOR_EDIT or MADE_FOR_PLAIN when the name or header says so for sure, else "" (the person must say).
    An Edit signal wins over everything. Plain needs a plain signal in the name or in the model fields (not the
    ss_base_model_version key alone), and no other family named in the header."""
    arch = " ".join(str(meta.get(k, "")) for k in ARCH_FIELDS).lower()
    names = " ".join([*(str(meta.get(k, "")) for k in NAME_FIELDS), file_name]).lower()
    if edit_signal(arch + " " + names):
        return MADE_FOR_EDIT
    if any(pattern.search(arch) for pattern, _fam in OTHER_FAMILIES):
        return ""
    fields = " ".join(str(meta.get(k, "")) for k in PLAIN_FIELDS).lower()
    if QWEN_RE.search(names) or QWEN_RE.search(fields):
        return MADE_FOR_PLAIN
    return ""
# Shown on a LoRA made for plain Qwen-Image that is used for picture edits (made_for "qwen_image").
QWEN_PLAIN_WARNING = ("This LoRA was made for Qwen-Image, a close relative of the model the app uses for picture edits. "
                      "It may only partly work, or change results in ways you do not expect. Try it at Subtle first.")
# Families this app has no painter for, by the words their metadata uses. The Qwen families are read in prefill().
OTHER_FAMILIES = (
    (re.compile(r"sdxl|stable-diffusion-xl|sd_xl|\bxl\b|pony|illustrious|noob"), "Stable Diffusion XL"),
    (re.compile(r"sd[-_ ]?1|stable-diffusion-v1|sd[-_ ]?v1|sd[-_ ]?15|sd 1\.5"), "Stable Diffusion 1.5"),
    (re.compile(r"sd[-_ ]?3|stable-diffusion-3"), "Stable Diffusion 3"),
    (re.compile(r"flux[-_. ]?1|flux[-_. ]?dev|flux[-_. ]?schnell"), "FLUX.1"),
    (re.compile(r"\bwan\b|wan[-_ ]?2"), "Wan (video)"),
    (re.compile(r"hunyuan"), "Hunyuan"),
    (re.compile(r"\bltx"), "LTX (video)"),
)


def _label_from_name(name: str) -> str:
    stem = name[: -len(EXT)] if name.lower().endswith(EXT) else name
    stem = re.sub(r"[_-]+", " ", stem)
    stem = " ".join(stem.split())
    return (stem[:1].upper() + stem[1:])[:MAX_LABEL_CHARS] if stem else "My LoRA"


def _clean_line(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _trigger(meta: dict[str, str]) -> str:
    for k in TRIGGER_FIELDS:
        v = meta.get(k)
        if v:
            try:  # some trainers write a JSON list
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    v = ", ".join(str(x) for x in parsed if x)
            except ValueError:
                pass
            t = _clean_line(v, MAX_TRIGGER_CHARS)
            if t:
                return t
    freq = meta.get("ss_tag_frequency")
    if not freq:
        return ""
    try:
        data = json.loads(freq)
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    tags: list[str] = []
    for folder in data.values():
        if isinstance(folder, dict):
            tags.extend(str(t).strip() for t in folder if str(t).strip())
    tags = list(dict.fromkeys(tags))
    if 0 < len(tags) <= 2 and all(len(t) <= 100 for t in tags):
        return _clean_line(", ".join(tags), MAX_TRIGGER_CHARS)
    # kohya folder names are "<repeats>_<trigger words>"
    if len(data) == 1:
        folder_name = str(next(iter(data)))
        m = re.match(r"^\d+_(.+)$", folder_name)
        if m and len(m.group(1)) <= 60:
            return _clean_line(m.group(1), MAX_TRIGGER_CHARS)
    return ""


def _family_from_keys(tensors: dict[str, Any]) -> str:
    keys = " ".join(list(tensors)[:400]).lower()
    if "lora_te1_" in keys or "lora_te2_" in keys or "input_blocks" in keys or "down_blocks" in keys:
        return "Stable Diffusion"
    if "double_blocks" in keys or "single_blocks" in keys or "single_transformer_blocks" in keys:
        return "FLUX"
    if "distilled_guidance_layer" in keys:
        return "Chroma"
    if re.search(r"layers\.\d+\.(adaln_modulation|attention\.to_[kqv])", keys) or "noise_refiner" in keys or "context_refiner" in keys:
        return "Z-Image"
    return ""


def prefill(meta: dict[str, str], tensors: dict[str, Any], file_name: str) -> dict[str, Any]:
    """What the short form can start with, from the header alone:
    {label, base_models (painter ids, empty when unsure), trigger, family (the model family the add-on looks made
    for, or ""), painter_note (a plain sentence when the painter is unknown, else "")}."""
    from . import reference

    painters = set(reference.concept_models())
    arch = " ".join(str(meta.get(k, "")) for k in ARCH_FIELDS).lower()
    names = " ".join([*(str(meta.get(k, "")) for k in NAME_FIELDS), file_name]).lower()
    both = arch + " " + names
    title = _clean_line(meta.get("modelspec.title"), MAX_LABEL_CHARS)
    out: dict[str, Any] = {"label": title or _label_from_name(file_name), "base_models": [], "trigger": _trigger(meta),
                           "family": "", "painter_note": "", "warning": ""}
    picks: list[str] = []
    note = ""
    family = ""
    warning = ""
    made = ""  # MADE_FOR_EDIT or MADE_FOR_PLAIN when the file says so for sure (the Qwen branch below)
    zimage = re.search(r"z[-_. ]?image", both) is not None
    if zimage:
        family = "Z-Image"
        if "turbo" in both:  # also "de-turbo" adapters, which are trained to be used on Turbo
            picks = ["zturbo"]
        elif re.search(r"z[-_. ]?image[-_. ]?base|\bbase\b", arch):
            picks = ["zbase"]
        else:
            note = UNSURE + " It looks like a Z-Image LoRA: choose Z-Image Turbo or Z-Image Base."
    elif "klein" in both and "flux" in both:
        family = "FLUX.2 Klein"
        if re.search(r"9\s?b", both):
            note = "It looks like it was made for FLUX.2 Klein 9B. This app has the 4B model, so it will probably not work."
        elif re.search(r"4\s?b", both):
            picks = ["klein"]
        else:
            note = UNSURE + " It looks like a FLUX.2 Klein LoRA; this app has the 4B model."
    elif "chroma" in arch:
        family, picks = "Chroma", ["chroma"]
    elif re.search(r"\banima\b", arch):
        family, picks = "Anima", ["anima"]
    else:
        # The Qwen question first (qwen_made_for): an Edit signal wins; plain only when the file says so for sure.
        # Otherwise the other families named in the header, and if none, the person is asked (made_for stays empty).
        other = next((fam for pattern, fam in OTHER_FAMILIES if pattern.search(arch)), "")
        made = qwen_made_for(meta, file_name)
        if made == MADE_FOR_EDIT:
            family, picks = QWEN_EDIT_FAMILY, [reference.EDIT_MODEL]
        elif made == MADE_FOR_PLAIN:
            family, picks = QWEN_FAMILY, [reference.EDIT_MODEL]
            warning = QWEN_PLAIN_WARNING
        elif other:
            family = other
            note = f"It looks like it was made for {other}, which this app does not use. It will probably not work with any of these models."
    if not family:
        family = _family_from_keys(tensors)
        if family == "Stable Diffusion":
            note = "It looks like it was made for Stable Diffusion, which this app does not use. It will probably not work with any of these models."
        elif family:
            note = UNSURE + f" It looks like a {family} LoRA."
    picks = [p for p in picks if p in painters or p == reference.EDIT_MODEL]
    if not picks and not note:
        note = UNSURE
    out.update(base_models=picks, family=family, painter_note=note, warning=warning, made_for=made)
    return out


# --- The registry (<library_path>/my_addons.json) --------------------------------------------------------------------


def _read_raw() -> list[dict[str, Any]]:
    p = registry_path()
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("addons") if isinstance(data, dict) else None
    return [a for a in items or [] if isinstance(a, dict)]


def _normalise(a: dict[str, Any]) -> dict[str, Any] | None:
    """A stored entry in the shape reference.py reads, or None when it is not a usable user entry."""
    aid = a.get("id")
    f = a.get("file")
    if not isinstance(aid, str) or not aid.startswith(ID_PREFIX) or not isinstance(f, str):
        return None
    from . import reference

    pl = a.get("placement") if isinstance(a.get("placement"), dict) else None
    if pl and pl.get("mode") == "in_place" and isinstance(pl.get("path"), str) and pl["path"]:
        # Used where it is: `file` is the name ComfyUI lists it under (relative to one of its LoRA folders).
        if not f or ".." in f.replace("\\", "/").split("/") or f.startswith(("/", "\\")) or ":" in f:
            return None
        name = f.replace("\\", "/").rsplit("/", 1)[-1]
    else:
        pl = pl if pl and pl.get("mode") in ("link", "copy") else None
        if not f.startswith(MINE_PREFIX):
            return None
        name = f[len(MINE_PREFIX):]
        if not name or "/" in name or "\\" in name or ".." in name:
            return None
    strength = a.get("default_strength") if a.get("default_strength") in STRENGTHS else "normal"
    models = [m for m in a.get("base_models") or [] if isinstance(m, str)]
    labels = ", ".join(models)
    label = _clean_line(a.get("label"), MAX_LABEL_CHARS) or _label_from_name(name)
    edit = models == [reference.EDIT_MODEL]
    made_for = a.get("made_for") if a.get("made_for") in ("qwen_image", "qwen_image_edit") else ""
    return {
        "id": aid, "label": label, "file": f, "file_name": name, "base_models": models,
        "stage": "edit" if edit else "concept",
        "trigger": _clean_line(a.get("trigger"), MAX_TRIGGER_CHARS), "strengths": dict(STRENGTHS),
        "default_strength": strength, "licence": LICENCE_MINE, "source": _clean_line(a.get("source"), MAX_SOURCE_CHARS),
        "made_for": made_for, "warning": QWEN_PLAIN_WARNING if edit and made_for == "qwen_image" else "",
        "mine": True, "added_at": a.get("added_at") or "",
        "placement": {k: pl[k] for k in ("mode", "source", "path", "size") if k in pl} if pl else None,
        "help": {"meaning": f"Your own LoRA, from the file {name}.",
                 "why": "You added it yourself and the app has not tested it. If the picture looks wrong, try Subtle or switch it off.",
                 "technical": f"LoRA {f}, model only, for {labels or 'no model'}"},
    }


def entries() -> list[dict[str, Any]]:
    """The person's own add-ons, normalised (cached until my_addons.json changes). reference.py merges these after
    the built-in list and drops any whose id is a built-in one."""
    p = registry_path()
    try:
        st = p.stat()
        key = (str(p), st.st_mtime_ns, st.st_size)
    except OSError:
        key = (str(p), None, None)
    if _cache["key"] != key:
        items = [n for n in (_normalise(a) for a in _read_raw()) if n is not None]
        seen: set[str] = set()
        _cache["items"] = [a for a in items if not (a["id"] in seen or seen.add(a["id"]))]
        _cache["key"] = key
    return [dict(a) for a in _cache["items"]]


def _write_raw(items: list[dict[str, Any]]) -> None:
    p = registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps({"addons": items}, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    _cache["key"] = None


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s[:40].strip("_") or "addon"


def _new_id(label: str, taken: set[str]) -> str:
    base = ID_PREFIX + _slug(label)
    aid, i = base, 2
    while aid in taken:
        aid, i = f"{base}_{i}", i + 1
    return aid


def _builtin_ids() -> set[str]:
    from . import reference

    return set(reference._data()["addons"])


def public(a: dict[str, Any]) -> dict[str, Any]:
    from . import reference

    return {**reference.addon_public(a), "mine": True, "file_name": a.get("file_name") or "", "added_at": a.get("added_at") or "",
            "warning": a.get("warning") or "", "made_for": a.get("made_for") or "", "placement": placement_view(a)}


def addon_path(a: dict[str, Any]) -> Path:
    """Where the file of one of the person's add-ons is: where it was registered, or in VGAC/mine/."""
    pl = a.get("placement") or {}
    if pl.get("mode") == "in_place":
        return Path(pl["path"])
    return mine_dir() / str(a.get("file_name") or "")


def placement_view(a: dict[str, Any]) -> dict[str, Any]:
    """How the file is kept, for the list: {mode ("" for a file put in the app's folder by hand), note, source, source_missing,
    file_missing}. The note is plain words (guidance.addon_placement_note) and says so when the file or its original is gone."""
    from . import guidance

    pl = a.get("placement") or {}
    mode = str(pl.get("mode") or "")
    try:
        file_missing = not addon_path(a).is_file()
    except (OSError, ValueError, AddonFileError):
        file_missing = True
    source = str(pl.get("source") or "")
    source_missing = mode == "link" and bool(source) and not Path(source).exists()
    return {"mode": mode, "source": source, "source_missing": source_missing, "file_missing": file_missing,
            "note": guidance.addon_placement_note(mode, source_missing=source_missing, file_missing=file_missing, has_source=bool(source))}


def _check_form(label: str, base_models: list[str], trigger: str, default_strength: str, source: str) -> tuple[str, list[str]]:
    """The checks of the short form -> (cleaned label, models). AddonFileError when something is wrong."""
    from . import reference

    label = _clean_line(label, MAX_LABEL_CHARS)
    if not label:
        raise AddonFileError("Give the LoRA a name.")
    painters = list(reference.concept_models())
    edit = reference.EDIT_MODEL
    models = [m for m in dict.fromkeys(base_models or [])]
    if not models:
        raise AddonFileError("Choose at least one model the LoRA was made for.")
    if edit in models and len(models) > 1:
        raise AddonFileError("A LoRA is either for picture edits or for the painters, not both. Add it twice to use it both ways.")
    bad = [m for m in models if m not in painters and m != edit]
    if bad:
        raise AddonFileError(f"Unknown model: {', '.join(bad)}.")
    if default_strength not in STRENGTHS:
        raise AddonFileError("The starting strength must be Subtle, Normal or Strong.")
    if len(trigger or "") > MAX_TRIGGER_CHARS * 2 or len(source or "") > MAX_SOURCE_CHARS * 2:
        raise AddonFileError("That text is too long.")
    return label, models


def register(file_name: str, label: str, base_models: list[str], trigger: str = "", default_strength: str = "normal",
             source: str = "", in_place: bool = False, made_for_answer: str = "") -> dict[str, Any]:
    """Set up a file already in VGAC/mine/ (or, with `in_place`, one addon_links.place_addon registered where it is) as one of
    the person's add-ons. -> the public entry. AddonFileError (or ValueError for the form) when it cannot be."""
    from . import reference

    if in_place:
        rec = made_record("place:" + file_name)
        if not rec or rec.get("mode") != "in_place" or not rec.get("path"):
            raise AddonFileError("That file was not chosen on this computer. Choose it again.", status=404)
        path, fv = Path(rec["path"]), str(rec["file"])
        if not path.is_file():
            raise AddonFileError("That file is no longer where you chose it.", status=404)
    else:
        path = _existing_file(file_name)
        fv = file_value(path.name)
        rec = made_record("mine:" + path.name)
    meta, tensors = read_header(path)  # still a valid LoRA (it may have changed since it was uploaded)
    label, models = _check_form(label, base_models, trigger, default_strength, source)
    edit = reference.EDIT_MODEL
    fam = prefill(meta, tensors, path.name)["family"]
    if edit in models and fam and fam not in (QWEN_EDIT_FAMILY, QWEN_FAMILY):
        raise AddonFileError(f"This LoRA was made for {fam}, which picture edits cannot use.")
    # The file decides when it says so for sure (an Edit signal, or a plain Qwen-Image signal); a person cannot overrule
    # that. When the file cannot tell, the person's answer is stored, and there must be one.
    detected = qwen_made_for(meta, path.name)
    if edit in models and not detected:
        if made_for_answer not in (MADE_FOR_EDIT, MADE_FOR_PLAIN):
            raise AddonFileError("Choose what it was made for: Qwen-Image-Edit, or plain Qwen-Image / not sure.")
        made_for = made_for_answer
    else:
        made_for = detected
    with _lock:
        items = _read_raw()
        if any(a.get("file") == fv for a in items):
            raise AddonFileError("This file is already set up as one of your LoRAs.", status=409)
        taken = {str(a.get("id")) for a in items} | _builtin_ids()
        entry = {"id": _new_id(label, taken), "label": label, "file": fv, "base_models": models,
                 "trigger": _clean_line(trigger, MAX_TRIGGER_CHARS), "default_strength": default_strength,
                 "source": _clean_line(source, MAX_SOURCE_CHARS), "mine": True, "made_for": made_for,
                 "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        if rec:
            entry["placement"] = {k: rec[k] for k in ("mode", "source", "path", "size") if k in rec}
        items.append(entry)
        _write_raw(items)
    return public(_normalise(entry) or entry)


def _update_made_for(aid: str, answer: str) -> str:
    """What a LoRA moved onto picture edits was made for: the file's own answer when it is sure, else the person's answer
    (the same rule as register). AddonFileError when the file is not usable for picture edits or no answer was given."""
    from . import reference

    found = next((a for a in _read_raw() if a.get("id") == aid), None)
    norm = _normalise(found) if found else None
    if norm is None:
        raise AddonFileError("That LoRA is not on your list.", status=404)
    try:
        path = addon_path(norm)
    except (OSError, ValueError):
        raise AddonFileError("That file is no longer where it was.", status=404)
    meta, tensors = read_header(path)
    fam = prefill(meta, tensors, path.name)["family"]
    if fam and fam not in (QWEN_EDIT_FAMILY, QWEN_FAMILY):
        raise AddonFileError(f"This LoRA was made for {fam}, which picture edits cannot use.")
    detected = qwen_made_for(meta, path.name)
    if detected:
        return detected
    if answer not in (MADE_FOR_EDIT, MADE_FOR_PLAIN):
        raise AddonFileError("Choose what it was made for: Qwen-Image-Edit, or plain Qwen-Image / not sure.")
    return answer


def update(aid: str, label: str, base_models: list[str], trigger: str = "", default_strength: str = "normal", source: str = "",
           made_for_answer: str = "") -> dict[str, Any]:
    """Change the name, models, trigger words, strength or source note of one of the person's add-ons. The file is not touched.
    A LoRA moved onto picture edits gets its made-for answer (asked the same way as when it was added)."""
    from . import reference

    if aid in _builtin_ids():
        raise AddonFileError("Tested LoRAs come with the app and cannot be changed.")
    label, models = _check_form(label, base_models, trigger, default_strength, source)
    made_for = _update_made_for(aid, made_for_answer) if reference.EDIT_MODEL in models else None
    with _lock:
        items = _read_raw()
        for a in items:
            if a.get("id") == aid:
                a.update(label=label, base_models=models, trigger=_clean_line(trigger, MAX_TRIGGER_CHARS),
                         default_strength=default_strength, source=_clean_line(source, MAX_SOURCE_CHARS))
                if made_for is not None:
                    a["made_for"] = made_for
                _write_raw(items)
                done = _normalise(a)
                break
        else:
            raise AddonFileError("That LoRA is not on your list.", status=404)
    return public(done or a)


def _take_away(a: dict[str, Any]) -> tuple[bool, str]:
    """Remove the file the app made for this entry. -> (removed, why not). Only a link or a copy the app made, and only while it
    still is what the app made: never an original, a file used where it is, or a file the app has no record of."""
    pl = a.get("placement") or {}
    mode = pl.get("mode")
    if mode not in ("link", "copy"):
        return False, ""
    name = str(a.get("file") or "")[len(MINE_PREFIX):]
    try:
        mine = _existing_file(name)
    except AddonFileError:
        return False, ""
    try:
        if mode == "link":
            src = str(pl.get("source") or "")
            if not src or not Path(src).exists():
                return False, "The original is gone, so this link is now the only copy of the file. It was kept in the LoRA folder."
            if not os.path.samefile(mine, src):
                return False, "The file in the LoRA folder is no longer the link the app made, so it was kept."
        elif pl.get("size") is not None and mine.stat().st_size != pl.get("size"):
            return False, "The file in the LoRA folder is no longer the copy the app made, so it was kept."
        mine.unlink()
    except OSError:
        return False, "The file could not be removed, so it was kept in the LoRA folder."
    forget_made("mine:" + name)
    return True, ""


def remove(aid: str) -> dict[str, Any]:
    """Take one of the person's add-ons off the list, and take away what the app made for it (a link or a copy it made; never an
    original, never a file used where it is, never a file put in VGAC/mine/ by hand)."""
    if aid in _builtin_ids():
        raise AddonFileError("Tested LoRAs come with the app and cannot be removed.")
    with _lock:
        items = _read_raw()
        keep = [a for a in items if a.get("id") != aid]
        if len(keep) == len(items):
            raise AddonFileError("That LoRA is not on your list.", status=404)
        gone = next(a for a in items if a.get("id") == aid)
        _write_raw(keep)
    norm = _normalise(gone) or {"file": gone.get("file"), "placement": gone.get("placement")}
    removed, why = _take_away(norm)
    mode = str((norm.get("placement") or {}).get("mode") or "")
    if mode == "in_place":
        forget_made("place:" + str(gone.get("file") or ""))
    name = str(gone.get("file") or "").replace("\\", "/").rsplit("/", 1)[-1]
    return {"removed": aid, "file_name": name, "file_kept": not removed, "file_removed": removed, "mode": mode, "message": why}


def listing() -> dict[str, Any]:
    from . import reference

    builtin = _builtin_ids()
    folder = mine_dir() if get_settings().loras_dir is not None else None
    return {"addons": [public(a) for a in entries() if a["id"] not in builtin], "folder": str(folder or ""),
            "folder_exists": bool(folder and folder.is_dir()), "strengths": dict(STRENGTHS),
            "painters": [{"id": m, "label": reference.model_label(m)} for m in reference.concept_models()],
            "edit_models": [{"id": reference.EDIT_MODEL, "label": "Picture edits (Qwen-Image-Edit)"}]}


def unregistered() -> list[dict[str, Any]]:
    """Files in VGAC/mine/ (that folder only) that are not set up yet, each with its prefill or a plain error."""
    if get_settings().loras_dir is None:
        return []
    d = mine_dir()
    if not d.is_dir():
        return []
    registered = {a["file"] for a in entries()}
    out = []
    for p in sorted(d.iterdir(), key=lambda x: x.name.lower()):
        if p.name.startswith(".") or not p.name.lower().endswith(EXT) or not p.is_file():
            continue
        if file_value(p.name) in registered:
            continue
        item: dict[str, Any] = {"file_name": p.name, "bytes": p.stat().st_size}
        try:
            meta, tensors = read_header(p)
            item["prefill"] = prefill(meta, tensors, p.name)
        except AddonFileError as e:
            item["error"] = str(e)
        except OSError:
            item["error"] = "This file could not be read."
        out.append(item)
    return out


def open_folder() -> dict[str, Any]:
    """Open VGAC/mine/ in the file explorer (made first if it is missing)."""
    d = mine_dir(create=True)
    shown = _launch_explorer(d)
    return {"opened": True, "folder": str(d), **(shown or {})}


def _launch_explorer(folder: Path) -> dict[str, Any]:
    """Open the folder and bring its window to the front (vgac/folders.py). -> {"front": bool | None}."""
    from . import folders

    return folders.open_in_explorer(folder)


# --- Upload (streamed) -------------------------------------------------------------------------------------------


class _Checker:
    """Checks a safetensors stream as it arrives: header length, then the JSON header, then the total size."""

    def __init__(self, cap: int):
        self.cap = cap
        self.head = bytearray()
        self.header_len: int | None = None
        self.expected: int | None = None
        self.meta: dict[str, str] = {}
        self.tensors: dict[str, Any] = {}
        self.total = 0

    def feed(self, chunk: bytes) -> None:
        self.total += len(chunk)
        if self.total > self.cap:
            raise AddonFileError(too_large_message(self.cap), status=413)
        if self.expected is not None:
            if self.total > self.expected:
                raise AddonFileError("This is not a valid safetensors file (it has extra bytes at the end).")
            return
        self.head.extend(chunk)
        if self.header_len is None and len(self.head) >= 8:
            self.header_len = header_length(bytes(self.head[:8]))
        if self.header_len is not None and len(self.head) >= 8 + self.header_len:
            self.meta, self.tensors, data_len = parse_header(bytes(self.head[8:8 + self.header_len]))
            self.expected = 8 + self.header_len + data_len
            if self.expected > self.cap:
                raise AddonFileError(too_large_message(self.cap), status=413)
            if self.total > self.expected:
                raise AddonFileError("This is not a valid safetensors file (it has extra bytes at the end).")
            self.head = bytearray()

    def finish(self) -> None:
        if self.expected is None:
            if self.header_len is None:
                raise AddonFileError("This file is too short to be a safetensors file.")
            raise AddonFileError("This is not a valid safetensors file (it is cut short).")
        if self.total != self.expected:
            raise AddonFileError("The file arrived incomplete or is cut short. Try choosing it again.")


def too_large_message(cap: int) -> str:
    return f"This file is larger than {cap / 1024 ** 3:.0f} GB. LoRAs are much smaller; this may not be a LoRA."


async def save_upload(request: Request, raw_name: str) -> dict[str, Any]:
    """Stream the request body into VGAC/mine/<name>: checked as it arrives, renamed into place only when complete
    and valid, never over an existing file. -> {file_name, bytes, prefill}."""
    import asyncio

    name = safe_name(raw_name)
    cap = MAX_FILE_BYTES
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cap:
        raise AddonFileError(too_large_message(cap), status=413)
    d = mine_dir(create=True)
    final = d / name
    if final.exists():
        raise AddonFileError("A file with this name is already in the LoRA folder. Use \"Look again\" to set it up, "
                             "or rename your file and choose it again.", status=409)
    part = d / f".upload-{uuid.uuid4().hex}.part"
    check = _Checker(cap)
    buf = bytearray()
    try:
        with open(part, "xb") as f:
            async for chunk in request.stream():
                if not chunk:
                    continue
                check.feed(chunk)
                buf.extend(chunk)
                if len(buf) >= WRITE_CHUNK:
                    data, buf = bytes(buf), bytearray()
                    await asyncio.to_thread(f.write, data)
            check.finish()
            if buf:
                await asyncio.to_thread(f.write, bytes(buf))
        if final.exists():
            raise AddonFileError("A file with this name arrived in the LoRA folder meanwhile. Choose it again with another name.", status=409)
        os.rename(part, final)  # never replaces: refuses when the name is taken
        record_made("mine:" + name, {"mode": "copy", "source": "", "size": check.total})
    except FileExistsError:
        raise AddonFileError("A file with this name is already in the LoRA folder.", status=409)
    finally:
        if part.exists():
            try:
                part.unlink()
            except OSError:
                pass
    return {"file_name": name, "bytes": check.total, "prefill": prefill(check.meta, check.tensors, name)}


# --- API -----------------------------------------------------------------------------------------------------------


def _http(e: AddonFileError) -> HTTPException:
    return HTTPException(e.status, str(e))


class MyAddonIn(BaseModel):
    file_name: str = Field(max_length=MAX_NAME_CHARS + 20)
    label: str = Field(max_length=MAX_LABEL_CHARS * 2)
    base_models: list[str] = Field(default_factory=list, max_length=20)
    trigger: str = Field(default="", max_length=MAX_TRIGGER_CHARS * 2)
    default_strength: str = "normal"
    source: str = Field(default="", max_length=MAX_SOURCE_CHARS * 2)
    # True for a file addon_links.place_addon registered where it is (file_name is then the name ComfyUI lists it under).
    in_place: bool = False
    # The person's answer when the file cannot tell: MADE_FOR_EDIT or MADE_FOR_PLAIN (ignored when the file is sure).
    made_for: str = Field(default="", max_length=40)


class PlaceIn(BaseModel):
    path: str = Field(max_length=1000)
    make_copy: bool = False  # the person said Copy to the offer made for a file on another drive


class MyAddonChange(BaseModel):
    label: str = Field(max_length=MAX_LABEL_CHARS * 2)
    base_models: list[str] = Field(default_factory=list, max_length=20)
    trigger: str = Field(default="", max_length=MAX_TRIGGER_CHARS * 2)
    default_strength: str = "normal"
    source: str = Field(default="", max_length=MAX_SOURCE_CHARS * 2)
    made_for: str = Field(default="", max_length=40)


@router.get("/api/my-addons")
def get_my_addons() -> dict[str, Any]:
    return listing()


@router.post("/api/my-addons")
def post_my_addon(body: MyAddonIn) -> dict[str, Any]:
    try:
        return register(body.file_name, body.label, body.base_models, body.trigger, body.default_strength, body.source,
                        body.in_place, made_for_answer=body.made_for)
    except AddonFileError as e:
        raise _http(e)


@router.put("/api/my-addons/{aid}")
def put_my_addon(aid: str, body: MyAddonChange) -> dict[str, Any]:
    try:
        return update(aid, body.label, body.base_models, body.trigger, body.default_strength, body.source,
                     made_for_answer=body.made_for)
    except AddonFileError as e:
        raise _http(e)


@router.post("/api/my-addons/place")
def post_place(body: PlaceIn) -> dict[str, Any]:
    from . import addon_links

    try:
        return addon_links.place_addon(body.path, copy=body.make_copy)
    except AddonFileError as e:
        raise _http(e)


@router.delete("/api/my-addons/{aid}")
def delete_my_addon(aid: str) -> dict[str, Any]:
    try:
        return remove(aid)
    except AddonFileError as e:
        raise _http(e)


@router.get("/api/my-addons/unregistered")
def get_unregistered() -> list[dict[str, Any]]:
    return unregistered()


@router.post("/api/my-addons/open-folder")
def post_open_folder() -> dict[str, Any]:
    try:
        return open_folder()
    except OSError as e:
        raise HTTPException(500, f"The folder could not be opened: {e.strerror or e}")


@router.post("/api/my-addons/upload")
async def post_upload(request: Request, name: str = Query(..., max_length=400)) -> dict[str, Any]:
    try:
        return await save_upload(request, name)
    except AddonFileError as e:
        raise _http(e)
