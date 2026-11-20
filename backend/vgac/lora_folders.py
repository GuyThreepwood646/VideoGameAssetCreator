"""The person's LoRA folders: folders of LoRA files the person already has on this computer, which ComfyUI reads where they are.

Saved in machine.json under backend.lora_folders (kept across starts). The app writes every saved folder into its own ComfyUI
model folders file (comfy_install.install_root() / shared-models.yaml) as a `loras` entry, under the section
vgac_lora_folders, next to whatever else that file already holds. The person's own ComfyUI settings are never written.
A ComfyUI the app did not start does not read that file, so it must be started by the app or given the folder in its own
settings (the answer says so).

ComfyUI reads model folders only at start-up. A change is therefore answered with the card whose ComfyUI must be stopped and
started again (the Graphics cards panel has both buttons). The app never restarts ComfyUI by itself.

Routes:
  GET  /api/lora-folders          the list: each folder's state (is it there, can the app read it, how many LoRA files) and the
                                  restart notes
  POST /api/lora-folders          {path}: add one folder. Checked first; nothing is saved when the check fails.
  POST /api/lora-folders/remove   {path}: take one folder off the list. The folder and its files are never touched.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import config

router = APIRouter(prefix="/api/lora-folders", tags=["setup"])

SECTION = "vgac_lora_folders"  # the section this module owns in the model folders file
APP_FILE = "shared-models.yaml"  # the app's own model folders file (comfy_install.step_engines writes it)
EXT = ".safetensors"
MAX_FOLDERS = 20
MAX_LISTED = 5000  # LoRA files counted per folder; more than this is shown as "many"


class FolderError(ValueError):
    """A folder the app will not take, with a plain message for the person. `status` is the HTTP status."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _key(p: str) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


# --- the saved list -----------------------------------------------------------------------------------------------------


def saved_folders() -> list[str]:
    """The folders saved in machine.json, in the order they were added."""
    mpath = config.machine_path()
    if mpath is None:
        return []
    try:
        backend = config.read_machine(mpath).get("backend") or {}
    except config.ConfigError:
        return []
    items = backend.get("lora_folders") if isinstance(backend, dict) else None
    return [str(x) for x in items or [] if isinstance(x, str) and x.strip()]


def app_file() -> Path:
    """The app's own ComfyUI model folders file (it exists once ComfyUI is installed by the app)."""
    from . import comfy_install

    return comfy_install.install_root() / APP_FILE


# --- looking at a folder ------------------------------------------------------------------------------------------------


def lora_names(folder: str) -> list[str]:
    """The LoRA files under `folder`, named the way ComfyUI lists them: relative to the folder, forward slashes. OSError when a
    folder inside cannot be read."""

    def fail(e: OSError) -> None:
        raise e

    out: list[str] = []
    for root, dirs, files in os.walk(folder, onerror=fail):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for f in files:
            if f.lower().endswith(EXT) and not f.startswith("."):
                out.append(os.path.relpath(os.path.join(root, f), folder).replace(os.sep, "/"))
        if len(out) > MAX_LISTED:
            break
    return sorted(out, key=str.casefold)


def check(folder: str) -> dict[str, Any]:
    """One saved folder's state: {path, exists, readable, lora_files, problem}. Never raises."""
    if os.path.isfile(folder):
        return {"path": folder, "exists": False, "readable": False, "lora_files": 0,
                "problem": "This is a file, not a folder. Remove it and add the folder the LoRA files are in."}
    if not os.path.isdir(folder):
        return {"path": folder, "exists": False, "readable": False, "lora_files": 0,
                "problem": "This folder is not on this computer any more. Remove it, or put the folder back where it was."}
    try:
        names = lora_names(folder)
    except OSError:
        return {"path": folder, "exists": True, "readable": False, "lora_files": 0,
                "problem": "The app cannot read this folder, so ComfyUI cannot use the LoRAs in it. Check the folder's permissions."}
    return {"path": folder, "exists": True, "readable": True, "lora_files": len(names), "problem": ""}


def lora_files() -> list[dict[str, Any]]:
    """Every LoRA file in every saved folder that can be read: {folder, name, path, bytes}. The name is the one ComfyUI lists."""
    out: list[dict[str, Any]] = []
    for folder in saved_folders():
        if not os.path.isdir(folder):
            continue
        try:
            names = lora_names(folder)
        except OSError:
            continue
        for name in names:
            p = os.path.join(folder, *name.split("/"))
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            out.append({"folder": folder, "name": name, "path": p, "bytes": size})
    return out


# --- changing the list --------------------------------------------------------------------------------------------------


def add(value: Any) -> dict[str, Any]:
    from . import setup_settings

    try:
        text = setup_settings.clean_path(value, "the LoRA folder")
    except setup_settings.SettingError as err:
        raise FolderError(str(err)) from None
    current = saved_folders()
    if os.path.isfile(text):
        raise FolderError("That is a file. Give the folder the LoRA files are in.")
    if not os.path.isdir(text):
        raise FolderError("That folder is not on this computer. Check the full path and try again.", 404)
    if any(_key(f) == _key(text) for f in current):
        raise FolderError("That folder is already in your list.", 409)
    if len(current) >= MAX_FOLDERS:
        raise FolderError(f"There can be up to {MAX_FOLDERS} LoRA folders. Remove one before adding another.", 409)
    state = check(text)
    if not state["readable"]:
        raise FolderError(state["problem"])
    _save(current + [text])
    found = state["lora_files"]
    count = "no LoRA files yet" if found == 0 else (f"{found} LoRA file" + ("" if found == 1 else "s"))
    return {**listing(), "message": f"Added. This folder has {count}. Restart ComfyUI to see them (see the note below the list).",
            "added": text}


def remove(value: Any) -> dict[str, Any]:
    text = str(value or "").strip()
    current = saved_folders()
    left = [f for f in current if _key(f) != _key(text)]
    if len(left) == len(current):
        raise FolderError("That folder is not in your list.", 404)
    _save(left)
    return {**listing(), "message": "Removed from the list. The folder and its files were not touched. Restart ComfyUI to stop "
                                    "it reading them (see the note below the list).", "removed": text}


def _save(folders: list[str]) -> None:
    from . import setup_settings

    mpath = config.machine_path()
    if mpath is None:
        raise FolderError("This copy of the app has no settings file to save to.", 500)
    with setup_settings._lock:
        data = config.read_machine(mpath)
        change: dict[str, Any] = {"lora_folders": folders}
        if folders:
            # An engine the app starts that has no model folders file yet is given the app's file (after a fresh install
            # they already have it; this covers an older install).
            path = str(app_file())
            insts = (data.get("backend") or {}).get("instances") or {}
            fix = {name: {"engine": {"extra_model_paths": path}} for name, inst in insts.items()
                   if isinstance(inst, dict) and isinstance(inst.get("engine"), dict) and not inst["engine"].get("extra_model_paths")
                   and _inside(inst["engine"].get("comfy_dir"))}
            if fix:
                change["instances"] = fix
        data = config.merge(data, {"backend": change})
        try:
            config.write_machine(data, mpath)
        except config.ConfigError as err:
            raise FolderError(f"The folders could not be saved ({err}).", 500) from None
    _write_file(folders)
    for fn in setup_settings.after_change:
        fn()


def _inside(comfy_dir: Any) -> bool:
    from . import comfy_install

    if not comfy_dir:
        return False
    root = os.path.normcase(os.path.abspath(str(comfy_install.install_root())))
    here = os.path.normcase(os.path.abspath(str(comfy_dir)))
    return here == root or here.startswith(root + os.sep)


# --- the model folders file ---------------------------------------------------------------------------------------------


def merged_text(existing: str, folders: list[str]) -> str:
    """The model folders file with the `loras` entries of `folders` in its own section. Every other section and value is kept
    as it was; only the section this module owns is replaced (or removed when there are no folders)."""
    try:
        data = yaml.safe_load(existing) if existing.strip() else {}
    except yaml.YAMLError:
        data = None
    if not isinstance(data, dict):
        raise FolderError("The model folders file is not readable YAML, so the LoRA folders could not be written. "
                          "Ask for ComfyUI to be set up again from the setup card.", 500)
    data.pop(SECTION, None)
    if folders:
        data[SECTION] = {"loras": "\n".join(folders)}  # ComfyUI reads one folder per line of this value
    head = "# Written by VGAC: model folders ComfyUI reads. VGAC keeps its own sections here and never writes into your ComfyUI settings.\n"
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False) if data else ""
    return head + body


def _write_file(folders: list[str]) -> None:
    """Rewrite the app's model folders file, but only when something here is installed (the file is made by the install)."""
    path = app_file()
    if not path.parent.is_dir():
        return
    old = path.read_text(encoding="utf-8") if path.is_file() else ""
    new = merged_text(old, folders)
    if new == old:
        return
    tmp = path.with_name(path.name + f".{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(new, encoding="utf-8")
    os.replace(tmp, path)


# --- the restart note ---------------------------------------------------------------------------------------------------


def restart_notes() -> list[dict[str, Any]]:
    """Per configured ComfyUI card: {card, state, text}. state: restart (the app started it and it is running: stop and start it),
    starts_later (the app started it and it is not running: it reads the folders when it next starts), not_app (the app
    started it, but its model folders file is not the app's), external (no engine of the app's: the app does not start it)."""
    from . import jobs

    mpath = config.machine_path()
    try:
        insts = (config.read_machine(mpath).get("backend") or {}).get("instances") or {} if mpath else {}
    except config.ConfigError:
        insts = {}
    queue = jobs.active_queue()
    sup = queue.supervisor if queue is not None else None
    path = str(app_file())
    out: list[dict[str, Any]] = []
    for name in sorted(insts):
        inst = insts[name]
        if not isinstance(inst, dict):
            continue
        gpu = str(inst.get("gpu") or "").strip()
        card = f"Card {name}" + (f" ({gpu})" if gpu else "")
        eng = inst.get("engine") if isinstance(inst.get("engine"), dict) else None
        if eng is None:
            out.append({"card": name, "state": "external", "text":
                        f"{card}: the app does not start this ComfyUI, and it does not read the app's folders file. Start it from the app, "
                        "or add the LoRA folders in that ComfyUI's own settings."})
            continue
        if not eng.get("extra_model_paths") or os.path.normcase(str(eng["extra_model_paths"])) != os.path.normcase(path):
            out.append({"card": name, "state": "not_app", "text":
                        f"{card}: this ComfyUI does not read the app's model folders file. Start it from the app, "
                        "or add the LoRA folders in that ComfyUI's own settings."})
            continue
        running = sup is not None and sup.state(name) == "running"
        if running:
            out.append({"card": name, "state": "restart", "text":
                        f"{card}: restart its ComfyUI to see the change. On the Graphics cards panel press Stop, then Start on this card."})
        else:
            out.append({"card": name, "state": "starts_later", "text":
                        f"{card}: its ComfyUI is not running. It reads the folders when the app starts it."})
    return out


def listing() -> dict[str, Any]:
    folders = [check(f) for f in saved_folders()]
    notes = restart_notes()
    return {"folders": folders, "max_folders": MAX_FOLDERS, "restart": notes,
            "restart_cards": [n["card"] for n in notes if n["state"] == "restart"]}


# --- API -----------------------------------------------------------------------------------------------------------------


class FolderIn(BaseModel):
    path: str = Field(max_length=1000)


def _http(e: FolderError) -> HTTPException:
    return HTTPException(e.status, str(e))


@router.get("")
def get_folders() -> dict[str, Any]:
    return listing()


@router.post("")
def post_folder(body: FolderIn) -> dict[str, Any]:
    try:
        return add(body.path)
    except FolderError as e:
        raise _http(e)


@router.post("/remove")
def post_remove(body: FolderIn) -> dict[str, Any]:
    try:
        return remove(body.path)
    except FolderError as e:
        raise _http(e)
