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
The form is prefilled from the file's header metadata (prefill()): a title, the painter when the metadata names it,
and trigger words. Tensor data is never read.
"""

from __future__ import annotations

import json
import os
import re
import struct
import subprocess
import sys
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


def mine_dir(create: bool = False) -> Path:
    """VGAC/mine/ under ComfyUI's loras folder: the one folder of the model folders the app may list."""
    d = Path(get_settings().loras_dir).joinpath(*MINE_PARTS)
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
        raise AddonFileError("Add-ons must be .safetensors files. Other kinds of model file are not accepted.")
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
        raise AddonFileError("Add-ons must be .safetensors files.")
    p = mine_dir() / name
    if not p.is_file():
        raise AddonFileError("That file is not in the add-on folder.", status=404)
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
        raise AddonFileError("This file does not look like an add-on (LoRA). It may be a whole painter model, which "
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
UNSURE = "We could not tell which painter this was made for."
# Families this app has no painter for, by the words their metadata uses.
OTHER_FAMILIES = (
    (re.compile(r"sdxl|stable-diffusion-xl|sd_xl|\bxl\b|pony|illustrious|noob"), "Stable Diffusion XL"),
    (re.compile(r"sd[-_ ]?1|stable-diffusion-v1|sd[-_ ]?v1|sd[-_ ]?15|sd 1\.5"), "Stable Diffusion 1.5"),
    (re.compile(r"sd[-_ ]?3|stable-diffusion-3"), "Stable Diffusion 3"),
    (re.compile(r"flux[-_. ]?1|flux[-_. ]?dev|flux[-_. ]?schnell"), "FLUX.1"),
    (re.compile(r"qwen[-_ ]?image"), "Qwen-Image"),
    (re.compile(r"\bwan\b|wan[-_ ]?2"), "Wan (video)"),
    (re.compile(r"hunyuan"), "Hunyuan"),
    (re.compile(r"\bltx"), "LTX (video)"),
)


def _label_from_name(name: str) -> str:
    stem = name[: -len(EXT)] if name.lower().endswith(EXT) else name
    stem = re.sub(r"[_-]+", " ", stem)
    stem = " ".join(stem.split())
    return (stem[:1].upper() + stem[1:])[:MAX_LABEL_CHARS] if stem else "My add-on"


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
                           "family": "", "painter_note": ""}
    picks: list[str] = []
    note = ""
    family = ""
    zimage = re.search(r"z[-_. ]?image", both) is not None
    if zimage:
        family = "Z-Image"
        if "turbo" in both:  # also "de-turbo" adapters, which are trained to be used on Turbo
            picks = ["zturbo"]
        elif re.search(r"z[-_. ]?image[-_. ]?base|\bbase\b", arch):
            picks = ["zbase"]
        else:
            note = UNSURE + " It looks like a Z-Image add-on: choose Z-Image Turbo or Z-Image Base."
    elif "klein" in both and "flux" in both:
        family = "FLUX.2 Klein"
        if re.search(r"9\s?b", both):
            note = "It looks like it was made for FLUX.2 Klein 9B. This app has the 4B painter, so it will probably not work."
        elif re.search(r"4\s?b", both):
            picks = ["klein"]
        else:
            note = UNSURE + " It looks like a FLUX.2 Klein add-on; this app has the 4B painter."
    elif "chroma" in arch:
        family, picks = "Chroma", ["chroma"]
    elif re.search(r"\banima\b", arch):
        family, picks = "Anima", ["anima"]
    else:
        for pattern, fam in OTHER_FAMILIES:
            if pattern.search(arch):
                family = fam
                note = f"It looks like it was made for {fam}, which this app does not use. It will probably not work with any of these painters."
                break
    if not family:
        family = _family_from_keys(tensors)
        if family == "Stable Diffusion":
            note = "It looks like it was made for Stable Diffusion, which this app does not use. It will probably not work with any of these painters."
        elif family:
            note = UNSURE + f" It looks like a {family} add-on."
    picks = [p for p in picks if p in painters]
    if not picks and not note:
        note = UNSURE
    out.update(base_models=picks, family=family, painter_note=note)
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
    if not isinstance(aid, str) or not aid.startswith(ID_PREFIX) or not isinstance(f, str) or not f.startswith(MINE_PREFIX):
        return None
    name = f[len(MINE_PREFIX):]
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    strength = a.get("default_strength") if a.get("default_strength") in STRENGTHS else "normal"
    models = [m for m in a.get("base_models") or [] if isinstance(m, str)]
    labels = ", ".join(models)
    label = _clean_line(a.get("label"), MAX_LABEL_CHARS) or _label_from_name(name)
    return {
        "id": aid, "label": label, "file": f, "file_name": name, "base_models": models, "stage": "concept",
        "trigger": _clean_line(a.get("trigger"), MAX_TRIGGER_CHARS), "strengths": dict(STRENGTHS),
        "default_strength": strength, "licence": LICENCE_MINE, "source": _clean_line(a.get("source"), MAX_SOURCE_CHARS),
        "mine": True, "added_at": a.get("added_at") or "",
        "help": {"meaning": f"Your own add-on, from the file {name}.",
                 "why": "You added it yourself and the app has not tested it. If the picture looks wrong, try Subtle or switch it off.",
                 "technical": f"LoRA {f}, model only, for {labels or 'no painter'}"},
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

    return {**reference.addon_public(a), "mine": True, "file_name": a.get("file_name") or "", "added_at": a.get("added_at") or ""}


def register(file_name: str, label: str, base_models: list[str], trigger: str = "", default_strength: str = "normal",
             source: str = "") -> dict[str, Any]:
    """Set up a file already in VGAC/mine/ as one of the person's add-ons. -> the public entry. AddonFileError (or
    ValueError for the form) when it cannot be."""
    from . import reference

    path = _existing_file(file_name)
    read_header(path)  # still a valid LoRA (it may have changed since it was uploaded)
    label = _clean_line(label, MAX_LABEL_CHARS)
    if not label:
        raise AddonFileError("Give the add-on a name.")
    painters = list(reference.concept_models())
    models = [m for m in dict.fromkeys(base_models or [])]
    if not models:
        raise AddonFileError("Choose at least one painter the add-on was made for.")
    bad = [m for m in models if m not in painters]
    if bad:
        raise AddonFileError(f"Unknown painter: {', '.join(bad)}.")
    if default_strength not in STRENGTHS:
        raise AddonFileError("The starting strength must be Subtle, Normal or Strong.")
    if len(trigger or "") > MAX_TRIGGER_CHARS * 2 or len(source or "") > MAX_SOURCE_CHARS * 2:
        raise AddonFileError("That text is too long.")
    with _lock:
        items = _read_raw()
        fv = file_value(path.name)
        if any(a.get("file") == fv for a in items):
            raise AddonFileError("This file is already set up as one of your add-ons.", status=409)
        taken = {str(a.get("id")) for a in items} | _builtin_ids()
        entry = {"id": _new_id(label, taken), "label": label, "file": fv, "base_models": models,
                 "trigger": _clean_line(trigger, MAX_TRIGGER_CHARS), "default_strength": default_strength,
                 "source": _clean_line(source, MAX_SOURCE_CHARS), "mine": True,
                 "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        items.append(entry)
        _write_raw(items)
    return public(_normalise(entry) or entry)


def remove(aid: str) -> dict[str, Any]:
    """Take one of the person's add-ons off the list. The file stays in VGAC/mine/."""
    if aid in _builtin_ids():
        raise AddonFileError("Tested add-ons come with the app and cannot be removed.")
    with _lock:
        items = _read_raw()
        keep = [a for a in items if a.get("id") != aid]
        if len(keep) == len(items):
            raise AddonFileError("That add-on is not on your list.", status=404)
        gone = next(a for a in items if a.get("id") == aid)
        _write_raw(keep)
    name = str(gone.get("file") or "")[len(MINE_PREFIX):]
    return {"removed": aid, "file_name": name, "file_kept": True}


def listing() -> dict[str, Any]:
    from . import reference

    builtin = _builtin_ids()
    return {"addons": [public(a) for a in entries() if a["id"] not in builtin], "folder": str(mine_dir()),
            "folder_exists": mine_dir().is_dir(), "strengths": dict(STRENGTHS),
            "painters": [{"id": m, "label": reference.model_label(m)} for m in reference.concept_models()]}


def unregistered() -> list[dict[str, Any]]:
    """Files in VGAC/mine/ (that folder only) that are not set up yet, each with its prefill or a plain error."""
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
    _launch_explorer(d)
    return {"opened": True, "folder": str(d)}


def _launch_explorer(folder: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(folder))  # type: ignore[attr-defined]  # Explorer, on this folder only
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    else:
        subprocess.Popen(["xdg-open", str(folder)])


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
    return f"This file is larger than {cap / 1024 ** 3:.0f} GB. Add-ons are much smaller; this may not be an add-on."


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
        raise AddonFileError("A file with this name is already in the add-on folder. Use \"Look again\" to set it up, "
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
            raise AddonFileError("A file with this name arrived in the add-on folder meanwhile. Choose it again with another name.", status=409)
        os.rename(part, final)  # never replaces: refuses when the name is taken
    except FileExistsError:
        raise AddonFileError("A file with this name is already in the add-on folder.", status=409)
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


@router.get("/api/my-addons")
def get_my_addons() -> dict[str, Any]:
    return listing()


@router.post("/api/my-addons")
def post_my_addon(body: MyAddonIn) -> dict[str, Any]:
    try:
        return register(body.file_name, body.label, body.base_models, body.trigger, body.default_strength, body.source)
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
