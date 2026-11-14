"""Load the backend's settings: tracked defaults, with this machine's values laid over them.

Two files:

1. backend/config.yaml (tracked): defaults that are true on any machine. Relative paths are relative to the project
   folder.
2. local/machine.json (not tracked; the `local/` folder is ignored by git): this machine's values. Its "backend"
   section has the same keys as config.yaml and is laid over it key by key (nested tables merge; a null removes the
   key, so the default applies again, or for an entry of `instances` removes that instance). Other sections belong
   to other programs (the desktop launcher reads "desktop"). A missing machine.json is not an error: the defaults
   apply. Write it only with write_machine(), which validates and replaces the file atomically.

VGAC_CONFIG names another defaults file (tests use it). When it is set, machine.json is NOT laid over it, so a test
never picks up this machine's library or engines; VGAC_MACHINE names a machine file explicitly (also for tests).
Tests call reset_settings() after changing either.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError

log = logging.getLogger("vgac.config")

BACKEND_DIR = Path(__file__).resolve().parent.parent
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BACKEND_DIR.parent
DEFAULT_CONFIG_PATH = BACKEND_DIR / "config.yaml"
LOCAL_DIR = PROJECT_DIR / "local"
MACHINE_PATH = LOCAL_DIR / "machine.json"
MACHINE_SCHEMA = 1
DATA_DIR = PACKAGE_DIR / "data"
BLENDER_SCRIPTS_DIR = PACKAGE_DIR / "blender"


class ConfigError(Exception):
    """The settings cannot be loaded or saved. The message is plain and names the file and the key."""


class DiscoverConfig(BaseModel):
    """Find an instance by the graphics card it runs on instead of trusting a fixed port (instances.Supervisor.locate).

    ComfyUI Desktop picks its own port, so when `url` does not answer, or answers from a ComfyUI whose /system_stats
    device name does not contain `gpu_match`, the backend tries `ports` on the same host and uses the first ComfyUI
    whose device name contains `gpu_match` for the rest of the session. With no ports it only checks the card: a
    ComfyUI on the configured port that runs on another card is reported, never sent this instance's jobs."""

    gpu_match: str
    ports: list[int] = Field(default_factory=list)


class EngineConfig(BaseModel):
    """How the backend starts this instance's ComfyUI itself (instances.engine_command), with no start script.

    The command is `<python> -s <main> --port <port> --listen <listen> --cuda-device <n>
    --extra-model-paths-config <file> --input-directory <dir> --user-directory <dir> --output-directory <dir>
    <extra_args...>`, run in `comfy_dir`, with CUDA_DEVICE_ORDER=PCI_BUS_ID (so `cuda_device` is the number
    nvidia-smi shows) and SKINTOKEN_BLENDER_BIN set to `blender_exe` (how the rigging node finds Blender)."""

    # The ComfyUI install folder the command runs in.
    comfy_dir: Path
    # Its Python; relative to comfy_dir.
    python: Path = Path("ComfyUI/.venv/Scripts/python.exe")
    # ComfyUI's main.py, relative to comfy_dir, written as given on the command line (process matches rely on it).
    main: str = "ComfyUI/main.py"
    # Port; default: the port of the instance's url.
    port: int | None = None
    listen: str = "127.0.0.1"
    # The card, numbered as nvidia-smi numbers it. None leaves the choice to ComfyUI.
    cuda_device: int | None = None
    extra_model_paths: Path | None = None
    # Default: the settings' comfy_input_dir.
    input_dir: Path | None = None
    user_dir: Path | None = None
    # Default: the instance's output_dir.
    output_dir: Path | None = None
    extra_args: list[str] = Field(default_factory=list)


class InstanceConfig(BaseModel):
    url: str
    # Optional: check (and find) this instance by its card (DiscoverConfig).
    discover: DiscoverConfig | None = None
    gpu: str = ""
    role: str = ""
    # Local output directory of that ComfyUI instance, for nodes that save files without
    # reporting them through /history (SkinToken). Optional.
    output_dir: Path | None = None
    # On-demand start (instances.py), preferred: the backend builds the ComfyUI command from these settings.
    engine: EngineConfig | None = None
    # On-demand start, older way: a PowerShell script that starts this instance, relative to the project root (or
    # absolute). Used only when `engine` is not set. Without either the backend never starts the instance.
    start_script: Path | None = None
    # Seconds to wait for a started instance to answer /system_stats before the start counts as failed.
    start_timeout_seconds: float = Field(default=180, gt=0)
    # A piece of the command line that identifies an already running copy (for example one started by the desktop
    # launcher or ComfyUI Desktop), so the backend waits for it instead of starting a second one.
    process_match: str | None = None
    # Who usually runs this instance when the backend did not start it, for the card's note ("ComfyUI Desktop").
    external_owner: str = ""


class Settings(BaseModel):
    instances: dict[str, InstanceConfig]
    library_path: Path
    export_path: Path | None = None
    blender_exe: Path | None = None
    unity_exe: Path | None = None
    unity_project: Path | None = None
    comfy_input_dir: Path | None = None
    # ComfyUI's loras folder. The app only ever lists or writes its own VGAC/mine/ inside it (user_addons.py).
    # Unset: the person's own add-ons are not available until it is set.
    loras_dir: Path | None = None
    clips_libraries: dict[str, Path] = Field(default_factory=dict)
    min_free_ram_gb: float = Field(default=24, ge=0)
    # Seconds a ComfyUI job may run before the worker gives up on it.
    job_timeout_seconds: float = Field(default=3600, ge=1)
    # Which instance runs each stage ("host" is Blender and Unity on this machine). Concept, turnaround and rig need
    # the image/rig GPU (b); mesh needs the 3D GPU (a). The material category is images only, so it stays on b.
    # jobs.check_stage_instances validates these against reference.STAGE_ORDER and the instances above at start.
    stage_instances: dict[str, str] = Field(default_factory=lambda: {
        "concept": "b", "turnaround": "b", "mesh": "a", "prep": "host", "rig": "b", "export": "host"})
    material_stage_instances: dict[str, str] = Field(default_factory=lambda: {
        "concept": "b", "turnaround": "b", "mesh": "b", "prep": "host", "rig": "host", "export": "host"})
    # The instance that runs the local vision judge (POST /api/versions/{id}/judge mode=local).
    judge_instance: str = "b"
    # What each graphics card can run when the card a step prefers is turned off in the app (jobs.route). Names are
    # stages plus "judge" (the local vision judge) and "material" (every GPU step of the material category). Rig is
    # b only by default: an engine the backend did not start itself (ComfyUI Desktop) may lack SKINTOKEN_BLENDER_BIN,
    # the only way the SkinToken node finds Blender. "host" (prep and export) is never a graphics card and always
    # runs. An instance missing from this table can run the stages the tables above give it.
    instance_capabilities: dict[str, list[str]] = Field(default_factory=lambda: {
        "a": ["concept", "turnaround", "mesh", "material", "judge"],
        "b": ["concept", "turnaround", "mesh", "rig", "judge", "material"]})
    # Seconds a routine Blender step (rig repair, rig inspection, clip baking and rendering) may run.
    blender_timeout_seconds: float = Field(default=1800, ge=1)
    # A step is refused, with a plain sentence saying what is missing and where to set it, while what it needs is not
    # set up (firstrun.blockers). False lets every step queue regardless (tests that stub the engines).
    require_setup: bool = True

    @property
    def db_path(self) -> Path:
        return self.library_path / "vgac.sqlite"


# --- layering -------------------------------------------------------------------------------------------------------


def merge(base: Any, over: Any) -> Any:
    """`over` laid over `base`: tables merge key by key, a None in `over` removes the key, anything else replaces.
    Neither argument is changed."""
    if not isinstance(base, dict) or not isinstance(over, dict):
        return copy.deepcopy(over)
    out = copy.deepcopy(base)
    for k, v in over.items():
        if v is None:
            out.pop(k, None)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _resolve(p: Path | None, base: Path) -> Path | None:
    if p is None:
        return None
    p = Path(os.path.expandvars(str(p)))
    return p if p.is_absolute() else base / p


def resolve_paths(settings: Settings, project_dir: Path = PROJECT_DIR) -> Settings:
    """Make every path setting absolute: relative ones are relative to the project folder (an engine's python is
    relative to its comfy_dir). Changes `settings` in place and returns it."""
    s = settings
    for key in ("library_path", "export_path", "blender_exe", "unity_exe", "unity_project", "comfy_input_dir",
                "loras_dir"):
        setattr(s, key, _resolve(getattr(s, key), project_dir))
    s.clips_libraries = {k: _resolve(v, project_dir) for k, v in s.clips_libraries.items()}  # type: ignore[misc]
    for inst in s.instances.values():
        inst.output_dir = _resolve(inst.output_dir, project_dir)
        inst.start_script = _resolve(inst.start_script, project_dir)
        e = inst.engine
        if e is not None:
            e.comfy_dir = _resolve(e.comfy_dir, project_dir)  # type: ignore[assignment]
            e.python = _resolve(e.python, e.comfy_dir)  # type: ignore[assignment]
            for key in ("extra_model_paths", "input_dir", "user_dir", "output_dir"):
                setattr(e, key, _resolve(getattr(e, key), project_dir))
    return s


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except FileNotFoundError as err:
        raise ConfigError(f"The settings file {path} was not found.") from err
    except (OSError, yaml.YAMLError) as err:
        raise ConfigError(f"The settings file {path} could not be read: {err}") from err
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"The settings file {path} must hold a table of settings.")
    return raw


def read_machine(path: Path | None = None) -> dict[str, Any]:
    """The whole machine file as a dict ({} when there is none). ConfigError when it is there but unreadable."""
    path = Path(path or MACHINE_PATH)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    except OSError as err:
        raise ConfigError(f"This machine's settings file {path} could not be read: {err}") from err
    try:
        data = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as err:
        raise ConfigError(f"This machine's settings file {path} is not valid JSON (line {err.lineno}, column "
                          f"{err.colno}: {err.msg}). Fix it, or restore {path.name}.bak beside it.") from err
    if not isinstance(data, dict):
        raise ConfigError(f"This machine's settings file {path} must hold a JSON object.")
    backend = data.get("backend")
    if backend is not None and not isinstance(backend, dict):
        raise ConfigError(f'In {path}, "backend" must be an object of settings.')
    return data


def machine_path() -> Path | None:
    """The machine file laid over the defaults, or None when none is (VGAC_CONFIG set without VGAC_MACHINE)."""
    explicit = os.environ.get("VGAC_MACHINE")
    if explicit:
        return Path(explicit)
    if os.environ.get("VGAC_CONFIG"):
        return None
    return MACHINE_PATH


def _validate(raw: dict[str, Any], sources: str) -> Settings:
    try:
        return Settings.model_validate(raw)
    except ValidationError as err:
        lines = []
        for e in err.errors():
            where = ".".join(str(x) for x in e.get("loc", ()))
            what = "is missing" if e.get("type") == "missing" else e.get("msg", "is not valid")
            lines.append(f"{where}: {what}")
        raise ConfigError(f"The settings in {sources} are not valid: " + "; ".join(lines)) from err


def layered_raw(path: Path | str | None = None, machine: Path | str | None | bool = True) -> tuple[dict[str, Any], str]:
    """The defaults file with the machine file's "backend" section laid over it, before validation, and a text
    naming the files used. `machine`: True for the usual choice (machine_path()), False for none, or a path."""
    config_path = Path(path or os.environ.get("VGAC_CONFIG") or DEFAULT_CONFIG_PATH)
    raw = _read_yaml(config_path)
    sources = str(config_path)
    mpath = machine_path() if machine is True else (None if machine is False or machine is None else Path(machine))
    if mpath is not None:
        backend = read_machine(mpath).get("backend") or {}
        if backend:
            raw = merge(raw, backend)
            sources += f" and {mpath}"
    return raw, sources


def load_settings(path: Path | str | None = None, machine: Path | str | None | bool = True,
                  create_library: bool = True) -> Settings:
    raw, sources = layered_raw(path, machine)
    settings = resolve_paths(_validate(raw, sources))
    for problem in path_problems(settings):
        log.warning("settings: %s", problem)
    if create_library:
        try:
            settings.library_path.mkdir(parents=True, exist_ok=True)
        except OSError as err:
            raise ConfigError(f"The library folder {settings.library_path} (library_path) could not be created: "
                              f"{err}") from err
    return settings


def path_problems(settings: Settings) -> list[str]:
    """Plain sentences for set paths that do not exist (not errors: the setup card says what each one costs)."""
    out = []
    for key in ("blender_exe", "unity_exe"):
        p = getattr(settings, key)
        if p is not None and not Path(p).is_file():
            out.append(f"{key} is set to {p}, which does not exist.")
    for key in ("unity_project", "comfy_input_dir", "loras_dir"):
        p = getattr(settings, key)
        if p is not None and not Path(p).is_dir():
            out.append(f"{key} is set to {p}, which does not exist.")
    for name, inst in settings.instances.items():
        e = inst.engine
        if e is None:
            continue
        if not Path(e.comfy_dir).is_dir():
            out.append(f"instances.{name}.engine.comfy_dir is set to {e.comfy_dir}, which does not exist.")
        elif not Path(e.python).is_file():
            out.append(f"instances.{name}.engine.python is set to {e.python}, which does not exist.")
        if e.extra_model_paths is not None and not Path(e.extra_model_paths).is_file():
            out.append(f"instances.{name}.engine.extra_model_paths is set to {e.extra_model_paths}, which does not "
                       "exist.")
    return out


# --- writing the machine file ---------------------------------------------------------------------------------------


def write_machine(data: dict[str, Any], path: Path | str | None = None, defaults: Path | str | None = None) -> Path:
    """Replace the machine file with `data`, after checking that the defaults with its "backend" section laid over
    them are valid settings. Atomic: written to a temporary file beside it, then swapped in; the previous file is
    kept as machine.json.bak. Raises ConfigError (and leaves the old file untouched) when anything is wrong."""
    path = Path(path or MACHINE_PATH)
    if not isinstance(data, dict):
        raise ConfigError("The machine settings must be a JSON object.")
    data = dict(data)
    data.setdefault("schema", MACHINE_SCHEMA)
    backend = data.get("backend") or {}
    if not isinstance(backend, dict):
        raise ConfigError('"backend" must be an object of settings.')
    try:
        text = json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n"
    except (TypeError, ValueError) as err:
        raise ConfigError(f"The machine settings cannot be written as JSON: {err}") from err
    defaults_path = Path(defaults or os.environ.get("VGAC_CONFIG") or DEFAULT_CONFIG_PATH)
    _validate(merge(_read_yaml(defaults_path), json.loads(text).get("backend") or {}),
              f"{defaults_path} with the new machine settings")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if path.exists():
            shutil.copy2(path, path.with_name(path.name + ".bak"))
        os.replace(tmp, path)
    except OSError as err:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise ConfigError(f"This machine's settings file {path} could not be written: {err}") from err
    return path


def update_machine(changes: dict[str, Any], path: Path | str | None = None,
                   defaults: Path | str | None = None) -> Path:
    """Lay `changes` over the current machine file (merge(); a None removes a key) and write it (write_machine)."""
    path = Path(path or MACHINE_PATH)
    return write_machine(merge(read_machine(path), changes), path, defaults)


_problem = ""


def settings_problem() -> str:
    """Plain words when this machine's settings file could not be used and the app started on the defaults alone
    (empty when all is well). While it is set, no step runs and no setting is written (firstrun.py)."""
    get_settings()
    return _problem


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The settings, once. A machine settings file that cannot be read, or whose values do not make valid settings,
    must not stop the app from starting: it starts on the defaults alone and settings_problem() says why."""
    global _problem
    _problem = ""
    try:
        return load_settings()
    except ConfigError as err:
        if machine_path() is None:
            raise
        try:
            fallback = load_settings(machine=False)
        except ConfigError:
            raise err from None
        _problem = str(err)
        log.error("settings: %s The app started on the defaults alone; no step will run until this is fixed.", err)
        return fallback


def reset_settings() -> None:
    """Forget the cached settings (tests switch config files)."""
    get_settings.cache_clear()
