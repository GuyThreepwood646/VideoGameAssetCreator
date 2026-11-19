"""Model files the person already has: pointing the app at them instead of downloading (the models panel, "I already have this").

A choice says "for this entry of the list, use that file". It is kept in local/model_choices.json (never in the repo):
{ "model:zbase": {"path": "...", "name": "z_image_int8_convrot.safetensors", "size": 6201001296} }.

How the engine gets to load it. ComfyUI only loads from folders it reads, so a chosen file must be in one of them; the engine's
own list (object_info) then names it, whatever its file name. The graphs name the list's file, so just before a graph is queued
comfy.ComfyClient.queue_prompt swaps that name for the one the engine lists (apply_aliases). The app never writes into the
person's ComfyUI configuration. (A file somewhere the engine cannot read is refused with a plain sentence for now.)

Trust. The size and the first bytes are checked at once (a wrong kind of file, or a size far off, is refused with a reason);
the SHA-256 is checked in the background against the pin (models_setup.verify_all). A match is the publisher's file whatever
it is called; no match is "your own file, not the one this app was tested with" and is used only after the person says so.

The same alias table also carries the list's `older` files (earlier copies the app may use while the publisher's file is not
there), but only those the same loader can load: a different layout (the all-in-one Turbo checkpoint) is zimage_fallback's job.
"""

from __future__ import annotations

import json
import logging
import os
import struct
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from . import downloader
from .downloader import DownloadError, Spec

log = logging.getLogger("vgac.model_choices")

EXTENSIONS = (".safetensors", ".gguf", ".ckpt", ".sft")
# The loader each folder of the models tree belongs to (for the list's `older` files).
FOLDER_LOADERS = {"diffusion_models": "UNETLoader", "unet": "UNETLoader", "checkpoints": "CheckpointLoaderSimple", "text_encoders": "CLIPLoader",
                  "clip": "CLIPLoader", "vae": "VAELoader"}


def choices_file() -> Path:
    return downloader.project_dir() / "local" / "model_choices.json"


def load() -> dict[str, dict[str, Any]]:
    try:
        d = json.loads(choices_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in d.items() if isinstance(v, dict) and v.get("path")} if isinstance(d, dict) else {}


def _save(d: dict[str, dict[str, Any]]) -> None:
    choices_file().parent.mkdir(parents=True, exist_ok=True)
    choices_file().write_text(json.dumps(d, indent=1), encoding="utf-8")


def loader_of_file(file_name: str) -> tuple[str, str] | None:
    """(loader node, its input) the graphs load this file of the list with, found by its file name (models.yaml names the file of
    each model the checks know a loader for, LoRAs included), or None when the app cannot swap names for it (project files)."""
    from .preflight import MODEL_LOADERS, load_models

    models = load_models()
    for mid, (cls, inp, _stage, _req) in MODEL_LOADERS.items():
        if str((models.get(mid) or {}).get("file") or "").casefold() == str(file_name).casefold():
            return cls, inp
    return None


def loader_of(spec: Spec) -> tuple[str, str] | None:
    return loader_of_file(spec.dest.name) if spec.kind != "project_file" else None


def can_choose(spec: Spec) -> bool:
    return loader_of(spec) is not None


# --- looking at a file before it is taken -------------------------------------------------------------------------------------


NOT_A_MODEL = "That file does not look like a model file (it is not in the safetensors layout)."


def _header_keys(path: Path) -> tuple[list[str] | None, str]:
    """(the tensor names in a safetensors file's JSON header, a problem). A safetensors file starts with an 8-byte length and a
    JSON header; anything else is not a model of that kind."""
    try:
        with path.open("rb") as f:
            head = f.read(8)
            if len(head) < 8:
                return None, "That file is too small to be a model."
            n = struct.unpack("<Q", head)[0]
            if n < 2 or n > 200_000_000:
                return None, NOT_A_MODEL
            raw = f.read(n)
    except OSError as e:
        return None, f"That file cannot be read ({e.strerror or e})."
    if not raw.startswith(b"{"):
        return None, NOT_A_MODEL
    try:
        d = json.loads(raw)
    except ValueError:
        return None, NOT_A_MODEL
    return ([k for k in d if k != "__metadata__"] if isinstance(d, dict) else None), ""


def kind_of(keys: list[str]) -> str:
    """What kind of model a file is, from its tensor names: "checkpoint" (model, text encoder and picture decoder in one file),
    "lora", or "other" (a diffusion model, text encoder or VAE on its own, or anything the names cannot settle)."""
    if any("lora_down" in k or "lora_up" in k or ".lora_A" in k or ".lora_B" in k or k.startswith("lora_") for k in keys[:4000]):
        return "lora"
    has_unet = any(k.startswith("model.diffusion_model.") for k in keys)
    has_decoder = any(k.startswith(("first_stage_model.", "vae.")) for k in keys)
    return "checkpoint" if has_unet and has_decoder else "other"


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{max(1, round(n / 1e6))} MB" if n >= 1e6 else f"{n} bytes"


def refusal(spec: Spec, path: Path) -> str:
    """Why this file cannot work for `spec` at all, in plain words, or ''. Only what cannot work is refused: a file that is not a
    model file, or one of a kind the entry's loader cannot load (a whole checkpoint for an entry loaded as a separate diffusion
    model, a LoRA for a model, and the reverse). A different size or a different file is allowed, with a warning (warning())."""
    if not path.is_file():
        return "That is not a file."
    if path.suffix.lower() not in EXTENSIONS:
        return f"That does not look like a model file: model files end in {', '.join(EXTENSIONS)}."
    if path.suffix.lower() not in (".safetensors", ".sft"):
        return ""  # gguf and ckpt files are not read further: warned, not refused
    keys, problem = _header_keys(path)
    if keys is None:
        return problem
    kind = kind_of(keys)
    lo = loader_of(spec)
    cls = lo[0] if lo else ""
    wants_lora, wants_ckpt = cls.startswith("LoraLoader"), cls == "CheckpointLoaderSimple"
    if kind == "lora" and not wants_lora:
        return (f"That file is a LoRA (a small add-on), but {spec.name} is a whole model, so it cannot be loaded there. "
                "Choose a model file for it, or use the download button.")
    if kind == "checkpoint" and not wants_ckpt:
        return (f"That file is an all-in-one checkpoint (model, text encoder and picture decoder in one file), but {spec.name} is loaded "
                "as a separate diffusion model, text encoder or decoder, so it cannot be loaded there. Choose the single file for it, or "
                "use the download button.")
    if wants_lora and kind != "lora":
        return (f"That file is a model, not a LoRA, but {spec.name} is a LoRA (a small add-on), so it cannot be loaded there. "
                "Choose a LoRA file for it, or use the download button.")
    return ""


def warning(spec: Spec, path: Path) -> str:
    """What to say about a file that can work but is not the one the app was tested with ('' when it looks like the published
    one by size; the hash settles the rest in the background)."""
    size = path.stat().st_size
    if spec.size and size != spec.size:
        return ("This is not the file the app was tested with. Results may differ or the step may fail. "
                f"(It is {_gb(size)}; the one the app was tested with is {_gb(spec.size)}, {spec.dest.name}.)")
    return ""


def readable_by_engine(path: Path, root: Path | None, roots: dict[str, Any]) -> bool:
    """Whether `path` is inside a folder the engine reads (its own models folder or any of its extra model paths)."""
    places = ([root] if root else []) + list(roots.get("bases", [])) + [f for fs in roots.get("typed", {}).values() for f in fs]
    p = os.path.normcase(os.path.normpath(str(path)))
    for b in places:
        base = os.path.normcase(os.path.normpath(str(b)))
        if p.startswith(base + os.sep):
            return True
    return False


# --- choosing and undoing --------------------------------------------------------------------------------------------------------


def choose(spec: Spec, path_text: str, root: Path | None, roots: dict[str, Any], placed: dict[str, Any] | None = None) -> dict[str, Any]:
    """Take `path_text` as the file for `spec`. Raises DownloadError with a plain message and what to do. `placed` says the file was
    put there by the app (a hard link or a copy of the person's file), so undoing the choice can take it away again."""
    if not can_choose(spec):
        raise DownloadError("not_supported", f"{spec.name} cannot be pointed at a file of your own yet.", "Use the download button for it.")
    path = Path(path_text.strip().strip('"'))
    why = refusal(spec, path)
    if why:
        raise DownloadError("refused", why, "Choose another file, or use the download button.")
    if not readable_by_engine(path, root, roots):
        raise DownloadError("not_readable", "Your ComfyUI does not read that folder, so it could not load the file from there.",
                            "Put the file in one of the folders your ComfyUI reads (the panel lists them), or add its folder to your "
                            "ComfyUI's extra model paths yourself; the app does not change your ComfyUI settings.")
    d = load()
    d[spec.id] = {"path": str(path), "name": path.name, "size": path.stat().st_size, **({"placed": placed} if placed else {})}
    _save(d)
    return {**d[spec.id], "warning": warning(spec, path)}


def unchoose(spec_id: str) -> bool:
    """Stop using the file chosen for this entry. A hard link or a copy the app made for it is taken away again (only that: the
    link, never the person's file it points to; a copy only while it is still the size it was made)."""
    d = load()
    if spec_id not in d:
        return False
    rec = d.pop(spec_id)
    _save(d)
    placed = rec.get("placed")
    if placed:
        try:
            mine = Path(rec["path"])
            if placed.get("mode") == "link" and mine.exists() and Path(placed["source"]).exists() and os.path.samefile(mine, placed["source"]):
                mine.unlink()
            elif placed.get("mode") == "copy" and mine.is_file() and mine.stat().st_size == rec.get("size"):
                mine.unlink()
        except OSError as e:
            log.warning("could not take away %s: %s", rec.get("path"), e)
    return True


# --- a file the engine cannot read where it is: a hard link, or a copy ---------------------------------------------------------------------------


def place_target(spec: Spec, source: Path) -> Path:
    """Where a link or copy of the person's file goes: next to the entry's own place, under the person's own file name (never the
    app's file name, so the trust rules still apply to it)."""
    name = source.name
    if name.casefold() == spec.dest.name.casefold():
        name = f"{source.stem}_yours{source.suffix}"
    return spec.dest.parent / name


def placement_options(spec: Spec, source: Path) -> dict[str, Any]:
    """What can be done for a file in a folder the engine does not read: a hard link (no copy, no extra space, same drive only), or
    a copy (any drive, needs the room), or adding the folder to the engine's extra model paths themselves."""
    target = place_target(spec, source)
    size = source.stat().st_size
    free = downloader.free_bytes(target.parent)
    same = _same_drive(source, target.parent)
    return {"can_link": same, "same_drive": same, "can_copy": free >= size + COPY_MARGIN, "size": size, "free": free, "target": str(target),
            "add_folder": str(source.parent)}


COPY_MARGIN = 2 * 1024**3  # room to keep free after a copy


def _same_drive(a: Path, b: Path) -> bool:
    probe = b
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return os.stat(a).st_dev == os.stat(probe).st_dev
    except OSError:
        return False


def link_file(source: Path, target: Path) -> bool:
    """Hard-link `source` at `target` (same drive; no copy, no space, no administrator rights). -> True when a new link was made,
    False when `target` already is the very same file. DownloadError when another file is in the way or the drive differs. Shared by
    the model panel and the person's own LoRAs (user_addons / addon_links)."""
    if target.exists():
        try:
            same = os.path.samefile(target, source)
        except OSError:
            same = False
        if not same:
            raise DownloadError("exists", f"A different file called {target.name} is already in {target.parent}.", "Rename your file, or remove that one.")
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError as e:
        raise DownloadError("cross_drive", "ComfyUI cannot read your file from where it is, and it cannot be linked from there "
                            f"({e.strerror or e}).", "Copy it into the models folder with the Copy button, or add its folder to your "
                            "ComfyUI's extra model paths yourself; the app does not change your ComfyUI settings.") from None
    return True


def place_link(spec: Spec, source: Path, root: Path | None, roots: dict[str, Any]) -> dict[str, Any]:
    """Hard-link the person's file into the engine's models folder (same drive; no copy, no space, no administrator rights), then take
    it as their file."""
    why = refusal(spec, source)
    if why:
        raise DownloadError("refused", why, "Choose another file, or use the download button.")
    target = place_target(spec, source)
    link_file(source, target)
    return choose(spec, str(target), root, roots, placed={"mode": "link", "source": str(source)})


def chosen(spec_id: str) -> Path | None:
    c = load().get(spec_id)
    return Path(c["path"]) if c else None


# --- the table of names the graphs may swap ------------------------------------------------------------------------------------------


Alt = tuple[str, str, str, str]  # (loader node, input, file name to use, the person's own file's full path or "" for an earlier copy)


def alias_table(usable: Any) -> dict[str, list[Alt]]:
    """published file name (lower case) -> [(loader node, input, name to use instead, own path)]: the person's own file first (it
    wins even when the publisher's is there: they chose it), then the list's earlier copies.

    `usable(path)` says whether a chosen file may be used now (its hash matched, or the person said to use it anyway)."""
    cat = downloader.catalogue()
    table: dict[str, list[Alt]] = {}
    choices = load()
    for section, kind in (("models", "model"), ("addons", "addon")):
        for key, d in (cat.get(section) or {}).items():
            if not isinstance(d, dict):
                continue
            name = str(d.get("save_as") or Path(str(d.get("dest") or "")).name)
            lo = loader_of_file(name) if name else None
            if lo is None:
                continue
            alts: list[Alt] = []
            c = choices.get(f"{kind}:{key}")
            if c and Path(c["path"]).is_file() and usable(Path(c["path"])) and Path(c["path"]).name.casefold() != name.casefold():
                alts.append((lo[0], lo[1], Path(c["path"]).name, str(c["path"])))
            for rel in d.get("older") or []:
                parts = Path(str(rel)).parts
                if len(parts) > 1 and FOLDER_LOADERS.get(parts[0]) == lo[0]:  # only what the same loader can load
                    alts.append((lo[0], lo[1], parts[-1], ""))
            if alts:
                table[name.casefold()] = alts
    return table


def _base(name: Any) -> str:
    return str(name).replace("\\", "/").rsplit("/", 1)[-1].casefold()


def _listed(choices: list[Any] | None, wanted: str) -> str | None:
    for c in choices or []:
        if isinstance(c, str) and _base(c) == _base(wanted):
            return c
    return None


def _match(node: Any, table: dict[str, list[Alt]]) -> tuple[str, list[Alt]] | None:
    """(the input naming a file the table has alternatives for, those alternatives for this node's loader), or None."""
    if not isinstance(node, dict):
        return None
    for inp, value in (node.get("inputs") or {}).items():
        if isinstance(value, str) and _base(value) in table:
            options = [a for a in table[_base(value)] if a[0] == node.get("class_type") and a[1] == inp]
            if options:
                return inp, options
    return None


def wanted_classes(graph: dict[str, Any], table: dict[str, list[Alt]]) -> set[tuple[str, str]]:
    """The (loader, input) pairs of this graph that name a file the table has alternatives for (what to ask the engine)."""
    out: set[tuple[str, str]] = set()
    for node in graph.values():
        m = _match(node, table)
        if m:
            out.add((node["class_type"], m[0]))
    return out


def apply_aliases(graph: dict[str, Any], table: dict[str, list[Alt]], lists: dict[tuple[str, str], list[Any] | None]
                  ) -> tuple[dict[str, Any], list[str]]:
    """(the graph to queue, notes). For each loader node naming a file in the table, the engine's own list decides:
      - the person's own file, when they chose one and the engine lists it (their choice wins, even over the publisher's file);
      - else the publisher's file, named as the engine lists it;
      - else the first earlier copy the engine lists.
    Nothing listed (or the engine could not be asked): left alone. Every use of the person's own file is noted (note_used)."""
    out = graph
    notes: list[str] = []
    for node_id, node in graph.items():
        m = _match(node, table)
        if m is None:
            continue
        inp, options = m
        name = node["inputs"][inp]
        choices = lists.get((node["class_type"], inp))
        if choices is None:
            continue
        use, note, own = None, "", ""
        for _c, _i, alt, own_path in options:
            if own_path and (use := _listed(choices, alt)) is not None:
                note, own = f"{_base(name)}: using your own file {use}", own_path
                break
        if use is None:
            use = _listed(choices, name)
        if use is None:
            for _c, _i, alt, own_path in options:
                if not own_path and (use := _listed(choices, alt)) is not None:
                    note = f"{_base(name)}: using the earlier copy {use}"
                    break
        if use is not None and use != name:
            out = {k: dict(v) for k, v in out.items()} if out is graph else out
            out[node_id]["inputs"] = {**out[node_id]["inputs"], inp: use}
            if note:
                notes.append(note)
        if own:
            note_used(_base(name), own)
    return out, notes


# --- which of the person's own files a job used ---------------------------------------------------------------------------------------------

_used: ContextVar[dict[str, str] | None] = ContextVar("own_files_used", default=None)


def start_tracking() -> dict[str, str]:
    """Called when a job starts: the files of the person's own that its graphs name are collected here (the job's own context)."""
    d: dict[str, str] = {}
    _used.set(d)
    return d


def note_used(published_name: str, own_path: str) -> None:
    d = _used.get()
    if d is not None:
        d[published_name] = own_path


def used_message(used: dict[str, str] | None) -> str:
    """The sentence added to a failed step's message when it used a file of the person's own, with the way back ('' when not)."""
    if not used:
        return ""
    names = "; ".join(f"{Path(p).name} for {n}" for n, p in sorted(used.items()))
    return (f"\nThis step used your own file ({names}). If that is the cause, open Set up (or the System page), find the entry and "
            "press \"Use the app's own file again\".")
