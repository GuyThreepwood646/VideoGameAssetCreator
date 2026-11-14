"""Write this machine's settings from the setup card, one at a time (docs/API.md "Setup").

GET  /api/setup/settings   every setting the card may give: label, kind, what is saved, what is in use, when a change
                           takes effect.
PUT  /api/setup/settings   {key, value}: check one value, save it through config.write_machine (atomic, validated), and
                           answer what changed, when it takes effect and what is still missing. A null value removes
                           the setting, so the default applies again.

Rules:
* Only these keys (SPECS). Anything else is refused.
* A path must be absolute and is stored as the person wrote it (normalised, never resolved through links, no
  environment variables, no `..`); the thing at the end must exist and be the right kind (a file that is Blender, a
  folder that holds ComfyUI, a folder the app may write in).
* A value that would leave the app unable to start is refused with the reason, and nothing is written.
* Taking effect: settings the app reads each time it needs them (Blender, Unity, the hand-off folder, the add-on
  folder, how to start a ComfyUI) are used from now on ("now"). Settings the running app holds (the library, a
  ComfyUI's address, ComfyUI's input folder) are saved and used after the app restarts ("next_start"); until then the
  answer lists them in `restart_needed`.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import config, firstrun
from .config import ConfigError, Settings

log = logging.getLogger("vgac.setup_settings")

router = APIRouter(prefix="/api/setup", tags=["setup"])

LOOPBACK = ("127.0.0.1", "localhost", "::1")
_lock = threading.Lock()
# Called after a setting changed (main.py clears the cached setup report).
after_change: list[Callable[[], None]] = []
# Tests replace how Blender's version is read.
blender_runner: Callable[[Path], str] | None = None


class SettingError(Exception):
    """A value that cannot be accepted; the message is plain and says what to give instead."""


# --- reading and checking values -----------------------------------------------------------------------------------


def clean_path(value: Any, what: str) -> str:
    """The path the person named, normalised: absolute, no `..`, no environment variables, not resolved through links."""
    if not isinstance(value, str) or not value.strip():
        raise SettingError(f"Give the full path of {what}.")
    text = value.strip().strip('"').strip("'").strip()
    if not text or any(c in text for c in "\x00\r\n"):
        raise SettingError(f"Give the full path of {what}.")
    if text.startswith(("\\\\?\\", "\\\\.\\")):
        raise SettingError("Give an ordinary path such as C:\\Tools\\thing, not a device path.")
    if "%" in text or text.startswith("~") or "$" in text:
        raise SettingError("Give the full path as it is; variables such as %USERPROFILE% and ~ are not expanded.")
    p = Path(text)
    if not p.is_absolute():
        raise SettingError(f"Give the full path of {what} (starting with a drive such as C:\\), not a short one.")
    if ".." in p.parts:
        raise SettingError("Give the path without '..' in it.")
    return os.path.normpath(text)


def _folder(value: Any, what: str, writable: bool = False) -> str:
    text = clean_path(value, what)
    p = Path(text)
    if p.is_file():
        raise SettingError(f"{text} is a file; give the folder {what} is in.")
    if not p.is_dir():
        raise SettingError(f"The folder {text} does not exist. Create it first, or check the spelling.")
    if writable:
        try:
            with tempfile.TemporaryFile(dir=p):
                pass
        except OSError:
            raise SettingError(f"The app cannot write in {text}. Choose a folder you can save files in.") from None
    return text


def _file(value: Any, what: str) -> str:
    text = clean_path(value, what)
    p = Path(text)
    if p.is_dir():
        raise SettingError(f"{text} is a folder; give {what} itself.")
    if not p.is_file():
        raise SettingError(f"There is no file at {text}. Check the spelling.")
    return text


def v_blender(value: Any, saved: Settings) -> str:
    from .preflight import BLENDER_MIN, blender_version

    if isinstance(value, str) and value.strip():
        folder = Path(value.strip().strip('"'))
        if folder.is_dir() and (folder / "blender.exe").is_file():
            raise SettingError(f"{folder} is Blender's folder; give the file blender.exe inside it.")
    text = _file(value, "blender.exe")
    p = Path(text)
    if p.stem.lower() != "blender":
        raise SettingError(f"{p.name} is not Blender. Give the file blender.exe.")
    kwargs = {"runner": blender_runner} if blender_runner is not None else {}
    ver = blender_version(p, **kwargs)
    if ver is None:
        raise SettingError(f"{text} did not say which version of Blender it is. Check that it starts.")
    if ver[:2] < BLENDER_MIN:
        raise SettingError(f"That is Blender {'.'.join(str(x) for x in ver)}; the app needs 5.0 or newer.")
    return text


def v_unity_exe(value: Any, saved: Settings) -> str:
    text = _file(value, "Unity.exe")
    if Path(text).stem.lower() != "unity":
        raise SettingError(f"{Path(text).name} is not the Unity editor. Give the file Unity.exe.")
    return text


def v_unity_project(value: Any, saved: Settings) -> str:
    text = _folder(value, "the Unity project")
    if not (Path(text) / "Assets").is_dir() or not (Path(text) / "ProjectSettings").is_dir():
        raise SettingError(f"{text} is not a Unity project (it has no Assets and ProjectSettings folders).")
    return text


def v_library(value: Any, saved: Settings) -> str:
    return _folder(value, "the library", writable=True)


def v_writable_folder(what: str) -> Callable[[Any, Settings], str]:
    return lambda value, saved: _folder(value, what, writable=True)


def _instance_of(key: str) -> str:
    return key.split(".")[1]


def v_url(key: str) -> Callable[[Any, Settings], str]:
    def check(value: Any, saved: Settings) -> str:
        from .instances import port_of

        if not isinstance(value, str) or not value.strip():
            raise SettingError("Give the address ComfyUI answers at, for example http://127.0.0.1:8188.")
        text = value.strip().rstrip("/")
        u = urlparse(text)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise SettingError("Give the address with http:// in front, for example http://127.0.0.1:8188.")
        if u.hostname.lower() not in LOOPBACK:
            raise SettingError("ComfyUI must run on this computer: use 127.0.0.1 (or localhost) as the address.")
        if u.path not in ("", "/") or u.query or u.fragment:
            raise SettingError("Give only the address and port, for example http://127.0.0.1:8188.")
        try:
            port = u.port
        except ValueError:
            port = None
        if not port:
            raise SettingError("The address needs a port, for example http://127.0.0.1:8188.")
        name = _instance_of(key)
        for other, inst in saved.instances.items():
            if other != name and port_of(inst.url) == port:
                raise SettingError(f"The other graphics card already uses port {port}. Give this one a different port.")
        return f"{u.scheme}://{u.hostname}:{port}"

    return check


def v_comfy_dir(key: str) -> Callable[[Any, Settings], str]:
    def check(value: Any, saved: Settings) -> str:
        text = _folder(value, "the ComfyUI folder")
        e = saved.instances[_instance_of(key)].engine
        main = e.main if e is not None else "ComfyUI/main.py"
        if not (Path(text) / main).is_file():
            raise SettingError(f"{text} does not hold ComfyUI (there is no {main} in it). Give the folder that holds "
                               "the ComfyUI folder and its Python, or set the other engine settings first.")
        return text

    return check


def v_engine_python(key: str) -> Callable[[Any, Settings], str]:
    return lambda value, saved: _file(value, "ComfyUI's python.exe")


def v_engine_main(key: str) -> Callable[[Any, Settings], str]:
    def check(value: Any, saved: Settings) -> str:
        if not isinstance(value, str) or not value.strip():
            raise SettingError("Give the place of main.py inside the ComfyUI folder, for example ComfyUI/main.py.")
        text = value.strip().replace("\\", "/")
        p = Path(text)
        if p.is_absolute() or ".." in p.parts or text.startswith("/"):
            raise SettingError("Give main.py's place inside the ComfyUI folder (for example ComfyUI/main.py), not a full "
                               "path.")
        e = saved.instances[_instance_of(key)].engine
        if e is None:
            raise SettingError("Give the ComfyUI folder first.")
        if not (Path(e.comfy_dir) / text).is_file():
            raise SettingError(f"There is no {text} in {e.comfy_dir}.")
        return text

    return check


def v_extra_model_paths(key: str) -> Callable[[Any, Settings], str]:
    def check(value: Any, saved: Settings) -> str:
        text = _file(value, "ComfyUI's model folders file")
        if Path(text).suffix.lower() not in (".yaml", ".yml"):
            raise SettingError("That is not a model folders file; it ends in .yaml (ComfyUI's extra_model_paths.yaml).")
        return text

    return check


def v_cuda(key: str) -> Callable[[Any, Settings], int]:
    def check(value: Any, saved: Settings) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value).strip().lstrip("-").isdigit():
            raise SettingError("Give the number of the graphics card as the NVIDIA tools number it (0, 1, ...).")
        n = int(value)
        if not 0 <= n <= 15:
            raise SettingError("The graphics card number must be between 0 and 15.")
        return n

    return check


# --- the settings the card may give --------------------------------------------------------------------------------


@dataclass
class Spec:
    key: str
    label: str
    help: str
    kind: str  # file, folder, address, number
    applies: str  # now, next_start
    validate: Callable[[Any, Settings], Any]


def _specs() -> dict[str, Spec]:
    top = [
        Spec("blender_exe", "Blender", "The file blender.exe of Blender 5.0 or newer. Free from blender.org.", "file", "now",
             v_blender),
        Spec("unity_exe", "Unity editor", "The file Unity.exe, only to try the asset in Unity.", "file", "now", v_unity_exe),
        Spec("unity_project", "Unity test project", "A Unity project folder to try the asset in (the app's own is "
             "unity-sandbox).", "folder", "now", v_unity_project),
        Spec("library_path", "Library folder", "Where every picture and model is kept. A new folder starts empty; "
             "characters in the old one are not moved.", "folder", "next_start", v_library),
        Spec("export_path", "Hand-off folder", "Where finished files are copied. Default: next to the library.", "folder",
             "now", v_writable_folder("the hand-off folder")),
        Spec("loras_dir", "ComfyUI add-on folder", "ComfyUI's loras folder; your own add-ons are kept in a VGAC/mine "
             "folder inside it.", "folder", "now", v_writable_folder("ComfyUI's loras folder")),
        Spec("comfy_input_dir", "ComfyUI input folder", "The folder both ComfyUI engines read pictures from.", "folder",
             "next_start", v_writable_folder("ComfyUI's input folder")),
    ]
    out = {s.key: s for s in top}
    return out


def spec_for(key: str, settings: Settings) -> Spec:
    """The spec of `key`, or SettingError for a key the card may not give."""
    specs = _specs()
    if key in specs:
        return specs[key]
    parts = key.split(".") if isinstance(key, str) else []
    if len(parts) >= 3 and parts[0] == "instances" and parts[1] in settings.instances:
        name, rest = parts[1], ".".join(parts[2:])
        gpu = settings.instances[name].gpu or f"graphics card {name}"
        table: dict[str, Spec] = {
            "url": Spec(key, f"ComfyUI address ({gpu})", "Where this ComfyUI answers, on this computer.", "address",
                        "next_start", v_url(key)),
            "engine.comfy_dir": Spec(key, f"ComfyUI folder ({gpu})", "The folder the app starts ComfyUI from.", "folder",
                                     "now", v_comfy_dir(key)),
            "engine.python": Spec(key, f"ComfyUI's Python ({gpu})", "The python.exe that runs ComfyUI, when it is not "
                                  "the one in the ComfyUI folder.", "file", "now", v_engine_python(key)),
            "engine.main": Spec(key, f"ComfyUI's main.py ({gpu})", "Where main.py is inside the ComfyUI folder.", "text",
                                "now", v_engine_main(key)),
            "engine.extra_model_paths": Spec(key, f"Model folders file ({gpu})", "ComfyUI's extra_model_paths.yaml, "
                                             "which says where its models are.", "file", "now", v_extra_model_paths(key)),
            "engine.cuda_device": Spec(key, f"Graphics card number ({gpu})", "Which graphics card this ComfyUI uses, "
                                       "numbered as the NVIDIA tools number them.", "number", "now", v_cuda(key)),
        }
        if rest in table:
            return table[rest]
    raise SettingError(f"{key!r} is not a setting that can be given here.")


def all_specs(settings: Settings) -> list[Spec]:
    out = list(_specs().values())
    for name in settings.instances:
        for rest in ("url", "engine.comfy_dir", "engine.python", "engine.main", "engine.extra_model_paths",
                     "engine.cuda_device"):
            out.append(spec_for(f"instances.{name}.{rest}", settings))
    return out


def get_value(settings: Settings, key: str) -> Any:
    """What `settings` holds for `key` as plain text (None when unset)."""
    if key.startswith("instances."):
        _, name, *rest = key.split(".")
        inst = settings.instances.get(name)
        if inst is None:
            return None
        if rest == ["url"]:
            return inst.url
        e = inst.engine
        if e is None:
            return None
        v = getattr(e, rest[1])
        return v if isinstance(v, (int, str)) else (str(v) if v is not None else None)
    v = getattr(settings, key, None)
    return str(v) if v is not None else None


def _nest(key: str, value: Any) -> dict[str, Any]:
    out: Any = value
    for part in reversed(key.split(".")):
        out = {part: out}
    return out


def _saved(create_library: bool = False) -> Settings:
    return config.load_settings(create_library=create_library)


def restart_needed() -> list[str]:
    """Keys saved but not in use yet (settings only read at start that differ from what is running)."""
    try:
        in_use = config.get_settings()
        saved = _saved()
    except ConfigError:
        return []
    out = []
    for s in all_specs(in_use):
        if s.applies == "next_start" and get_value(in_use, s.key) != get_value(saved, s.key):
            out.append(s.key)
    return out


def _apply_now(saved: Settings, key: str) -> None:
    """Use a saved value from now on, in the settings object the whole app shares."""
    cur = config.get_settings()
    if key.startswith("instances."):
        name = key.split(".")[1]
        if name in cur.instances and name in saved.instances:
            cur.instances[name].engine = saved.instances[name].engine
        return
    setattr(cur, key, getattr(saved, key))


def missing_now(settings: Settings, supervisor: Any = None) -> list[dict[str, str]]:
    """What still stops a step from running, once per cause: [{id, message, setting}]."""
    from .reference import STAGE_ORDER

    seen: dict[str, dict[str, str]] = {}
    for stage in STAGE_ORDER:
        for b in firstrun.blockers(settings, stage, "", supervisor=supervisor):
            seen.setdefault(b["id"], b)
    return list(seen.values())


def describe(settings_in_use: Settings | None = None) -> dict[str, Any]:
    in_use = settings_in_use or config.get_settings()
    saved = _saved()
    pending = set(restart_needed())
    rows = []
    for s in all_specs(in_use):
        rows.append({"key": s.key, "label": s.label, "help": s.help, "kind": s.kind, "applies": s.applies,
                     "value": get_value(saved, s.key), "in_use": get_value(in_use, s.key),
                     "pending_restart": s.key in pending})
    return {"settings": rows, "restart_needed": sorted(pending), "problem": config.settings_problem()}


def set_setting(key: str, value: Any, supervisor: Any = None) -> dict[str, Any]:
    """Check and save one setting; returns the answer of PUT /api/setup/settings. SettingError for a refusal."""
    with _lock:
        problem = config.settings_problem()
        if problem:
            raise SettingError(f"This machine's settings file could not be used ({problem}) and nothing can be saved "
                               "until it is fixed or removed.")
        mpath = config.machine_path()
        if mpath is None:
            raise SettingError("This copy of the app has no machine settings file to write to.")
        try:
            saved = _saved()
        except ConfigError as err:
            raise SettingError(str(err)) from None
        spec = spec_for(key, saved)
        before = get_value(saved, key)
        if value is None:
            changes = _nest(key, None)
            if key.endswith("engine.comfy_dir"):
                changes = {"instances": {key.split(".")[1]: {"engine": None}}}
            new_value = None
        else:
            new_value = spec.validate(value, saved)
            changes = _nest(key, new_value)
        try:
            config.update_machine({"backend": changes}, mpath)
        except ConfigError as err:
            raise SettingError(f"That would leave the app unable to start, so it was not saved: {err}") from None
        try:
            now_saved = _saved(create_library=False)
        except ConfigError as err:
            raise SettingError(f"The settings could not be read back after saving: {err}") from None
        after = get_value(now_saved, key)
        if spec.applies == "now":
            _apply_now(now_saved, key)
        for cb in list(after_change):
            try:
                cb()
            except Exception:  # noqa: BLE001
                log.exception("after a setting changed")
    changed = before != after
    pending = restart_needed()
    if not changed:
        message = f"{spec.label} was already set to that."
    elif after is None:
        message = f"{spec.label} was cleared; the default applies" + (" after the app restarts." if spec.applies == "next_start" else ".")
    elif spec.applies == "now":
        message = f"{spec.label} is set and is used from now on."
    else:
        message = f"{spec.label} is saved. It is used the next time the app starts; restart it to apply it."
    return {"key": key, "label": spec.label, "changed": changed, "from": before, "to": after, "applies": spec.applies,
            "message": message, "restart_needed": pending, "missing": missing_now(now_saved, supervisor)}


# --- HTTP ----------------------------------------------------------------------------------------------------------


class SettingIn(BaseModel):
    key: str
    value: Any = None


@router.get("/settings")
def list_settings() -> dict[str, Any]:
    return describe()


@router.put("/settings")
def put_setting(body: SettingIn) -> dict[str, Any]:
    from .jobs import active_queue

    queue = active_queue()
    try:
        return set_setting(body.key, body.value, queue.supervisor if queue else None)
    except SettingError as err:
        raise HTTPException(400, str(err)) from None
