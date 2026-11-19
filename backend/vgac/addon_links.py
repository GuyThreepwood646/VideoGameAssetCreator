"""Bring a LoRA the person already has on this computer into the app without copying it where that is not needed (user_addons.py).

Only the desktop app can do this: its native file picker returns the real path (frontend/src/desktop.ts hostPick); a browser
sends the file itself (user_addons.save_upload, a copy). Three routes, in this order:

  1. in place: the file is already in a folder ComfyUI reads for LoRAs (its own loras folder, an extra model path, the
     loras_dir setting). Nothing is made; the entry names the file the way ComfyUI lists it.
  2. link: the file is on the same drive as VGAC/mine/, so a hard link is made there (model_choices.link_file, the same code
     the models panel uses). No extra space, no administrator rights.
  3. copy: only after the person said Copy to a plain offer (what is copied, how big, into which folder). An offer is made by
     place_addon(copy=False) and does nothing; place_addon(copy=True) carries it out.

Never a folder junction or a symlink, and never a change to ComfyUI's own settings. Every route ends with the same check: the file
is kept only when ComfyUI's own LoRA list names it. A link or copy that ComfyUI does not list is taken away again at once (only
the one just made). What was made is recorded (user_addons.record_made) so that removing the LoRA later takes away that and
nothing else.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from . import guidance, model_choices, user_addons
from .config import get_settings
from .downloader import DownloadError, free_bytes
from .user_addons import AddonFileError, EXT, MAX_FILE_BYTES, MINE_PREFIX, WRITE_CHUNK

COPY_MARGIN = model_choices.COPY_MARGIN


# --- What ComfyUI reads --------------------------------------------------------------------------------------------------


def _norm(p: Any) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


def lora_folders() -> list[Path]:
    """Every folder ComfyUI reads LoRAs from, as the app knows them: the loras_dir setting, the loras folder of each models
    folder, and the loras entries of the extra model paths files (models_setup.read_roots)."""
    from . import downloader, models_setup

    out: list[Path] = []
    s = get_settings()
    if s.loras_dir is not None:
        out.append(Path(s.loras_dir))
    try:
        root = downloader.models_root()
        roots = models_setup.read_roots(root) if root else {"typed": {}, "bases": []}
    except Exception:  # noqa: BLE001  (an unreadable extra paths file must not stop adding a file)
        roots = {"typed": {}, "bases": []}
    out += [Path(b) / "loras" for b in roots["bases"]] + [Path(f) for f in roots["typed"].get("loras", [])]
    seen: set[str] = set()
    return [f for f in out if not (_norm(f) in seen or seen.add(_norm(f)))]


def listed_names(path: Path) -> list[str]:
    """The names the file would have in ComfyUI's LoRA list if it reads one of its LoRA folders: the path below that folder, with
    forward slashes. Empty when the file is in none of them."""
    p = _norm(path)
    out = []
    for f in lora_folders():
        base = _norm(f)
        if p.startswith(base + os.sep):
            out.append(os.path.relpath(str(path), str(f)).replace(os.sep, "/"))
    return out


def engine_lora_list() -> list[str] | None:
    """The LoRA file names the concept step's ComfyUI lists now, or None when it cannot be asked."""
    from .comfy import ComfyClient
    from .preflight import combo_choices

    s = get_settings()
    name = (s.stage_instances or {}).get("concept") or next(iter(s.instances), "")
    inst = s.instances.get(name)
    if inst is None:
        return None

    async def ask() -> list[str] | None:
        client = ComfyClient(inst.url, name=name)
        try:
            got = combo_choices(await client.object_info("LoraLoaderModelOnly"), "lora_name")
        finally:
            await client.aclose()
        return [str(c) for c in got if isinstance(c, str)] if got is not None else None

    try:
        return asyncio.run(ask())
    except Exception:  # noqa: BLE001  (not running, not answering: the caller says so plainly)
        return None


def _listed(choices: list[str], wanted: str) -> str | None:
    w = wanted.replace("\\", "/").casefold()
    for c in choices:
        if c.replace("\\", "/").casefold() == w:
            return c
    return None


def _engine_must_list(wanted: list[str]) -> str:
    """The name ComfyUI lists for the file under one of `wanted`; AddonFileError when it does not (or cannot be asked)."""
    choices = engine_lora_list()
    if choices is None:
        raise AddonFileError(guidance.ADDON_ENGINE_SILENT, status=409)
    for w in wanted:
        got = _listed(choices, w)
        if got is not None:
            return got
    raise AddonFileError(guidance.ADDON_NOT_LISTED, status=409)


# --- The three routes ----------------------------------------------------------------------------------------------------


def _chosen_file(path_text: str) -> Path:
    path = Path(str(path_text or "").strip().strip('"'))
    if not path.is_absolute():
        raise AddonFileError(guidance.ADDON_NEEDS_FULL_PATH)
    if not path.is_file():
        raise AddonFileError("That is not a file on this computer.", status=404)
    if path.suffix.lower() != EXT:
        raise AddonFileError("LoRAs must be .safetensors files. Other kinds of model file are not accepted.")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise AddonFileError(user_addons.too_large_message(MAX_FILE_BYTES), status=413)
    return path


def _inside(path: Path, folder: Path) -> bool:
    return _norm(path).startswith(_norm(folder) + os.sep)


def place_addon(path_text: str, copy: bool = False) -> dict[str, Any]:
    """Make the file at `path_text` (a real path from the desktop picker) usable as a LoRA, by the first route that applies.
    -> {route: in_place|already_here|link|copy|copy_needed, file_name, in_place, bytes, prefill, message, ...}. AddonFileError with a
    plain message when it cannot be. `copy` is the person's yes to a copy_needed offer; without it that route only describes."""
    path = _chosen_file(path_text)
    meta, tensors = user_addons.read_header(path)  # a damaged file or a whole model is refused before anything is made
    size = path.stat().st_size
    pre = user_addons.prefill(meta, tensors, path.name)
    base = {"bytes": size, "prefill": pre, "source": str(path)}
    mine = user_addons.mine_dir()  # AddonFileError when ComfyUI's LoRA folder is not set up

    # 0. already in the app's own folder (a file put there by hand): nothing to do, "Look again" would find it too
    if path.parent.exists() and mine.exists() and os.path.samefile(path.parent, mine):
        if user_addons.file_value(path.name) in {a["file"] for a in user_addons.entries()}:
            raise AddonFileError("This file is already set up as one of your LoRAs.", status=409)
        _engine_must_list([user_addons.file_value(path.name)])
        return {**base, "route": "already_here", "file_name": path.name, "in_place": False,
                "message": guidance.addon_place_words("already_here")}

    # 1. in a folder ComfyUI reads for LoRAs: used where it is, when ComfyUI really lists it under that name
    names = listed_names(path)
    if names:
        choices = engine_lora_list()
        if choices is None:
            raise AddonFileError(guidance.ADDON_ENGINE_SILENT, status=409)
        listed = next((g for g in (_listed(choices, n) for n in names) if g), None)
        if listed is not None:
            file = listed.replace("\\", "/")
            if file in {a["file"] for a in user_addons.entries()}:
                raise AddonFileError("This file is already set up as one of your LoRAs.", status=409)
            user_addons.record_made("place:" + file, {"mode": "in_place", "source": "", "path": str(path), "file": file, "size": size})
            where = str(path.parent)
            return {**base, "route": "in_place", "file_name": file, "in_place": True,
                    "message": guidance.addon_place_words("in_place", where=where)}
        # in a folder the app believes ComfyUI reads, but ComfyUI does not list it (a folder added after ComfyUI started):
        # fall through to the link or copy, which put it in a place ComfyUI does list.

    final = mine / user_addons.safe_name(path.name)
    if final.exists():
        raise AddonFileError("A file with this name is already in the app's LoRA folder. Use \"Look again\" to set it up, "
                             "or rename your file and choose it again.", status=409)
    listed_as = user_addons.file_value(final.name)

    # 2. same drive: a hard link
    if model_choices._same_drive(path, mine):
        try:
            model_choices.link_file(path, final)
        except DownloadError as e:
            if e.code != "cross_drive":
                raise AddonFileError(f"{e.message} {e.fix}".strip(), status=409) from None
        else:
            try:
                _engine_must_list([listed_as])
            except AddonFileError:
                _drop_made(final, lambda: os.path.samefile(final, path))
                raise
            user_addons.record_made("mine:" + final.name, {"mode": "link", "source": str(path), "size": size})
            return {**base, "route": "link", "file_name": final.name, "in_place": False,
                    "message": guidance.addon_place_words("link", source=str(path))}

    # 3. another drive (or a link the drive refused): a copy, only when the person said Copy
    if not copy:
        return {**base, "route": "copy_needed", "file_name": final.name, "in_place": False, "folder": str(mine),
                "can_copy": free_bytes(mine) >= size + COPY_MARGIN, "free": free_bytes(mine),
                "message": guidance.addon_place_words("copy_needed", size=size, folder=str(mine))}
    if free_bytes(mine) < size + COPY_MARGIN:
        raise AddonFileError(f"There is not enough room for a copy ({size / 1e9:.1f} GB needed). Free some space and try again.", status=409)
    _copy_into(path, final, size)
    try:
        _engine_must_list([listed_as])
    except AddonFileError:
        _drop_made(final, lambda: final.stat().st_size == size)
        raise
    user_addons.record_made("mine:" + final.name, {"mode": "copy", "source": str(path), "size": size})
    return {**base, "route": "copy", "file_name": final.name, "in_place": False, "folder": str(mine),
            "message": guidance.addon_place_words("copy", size=size, folder=str(mine))}


def _copy_into(source: Path, final: Path, size: int) -> None:
    """Copy to a hidden part file in the same folder and rename into place (never over an existing file)."""
    final.parent.mkdir(parents=True, exist_ok=True)
    part = final.with_name(f".copy-{uuid.uuid4().hex}.part")
    try:
        with open(source, "rb") as r, open(part, "xb") as w:
            shutil.copyfileobj(r, w, WRITE_CHUNK)
        if part.stat().st_size != size:
            raise AddonFileError("The copy did not come out the same size as your file, so it was not kept. Try again.", status=500)
        if final.exists():
            raise AddonFileError("A file with this name arrived in the LoRA folder meanwhile. Choose it again with another name.", status=409)
        os.rename(part, final)
    except OSError as e:
        raise AddonFileError(f"The file could not be copied ({e.strerror or e}).", status=500) from None
    finally:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass


def _drop_made(final: Path, still_ours: Any) -> None:
    """Take away the link or copy this call just made (it was not listed by ComfyUI), after checking it still is that file."""
    try:
        if final.is_file() and still_ours():
            final.unlink()
    except OSError:
        pass
