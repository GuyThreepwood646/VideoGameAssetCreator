"""Setup check: is everything the app needs in place? (GET /api/setup, the "Is everything ready?" card).

run_checks(settings, clients) returns one item per check:

    {id, group, label, state, detail, fix, needed_for, technical}

group is tools | engines | models | storage; state is ok | warn | fail | unknown (unknown: could not be checked now,
for example the card is off or its graphics engine is not running yet; never counted as a problem). label, detail and
fix are plain sentences for people who are not programmers; technical names the config key, file or node for people
who want to look it up. needed_for lists the stage ids (reference.STAGE_ORDER plus "judge" and "material") that a
problem holds up. summarize(items) gives {ok, fails, warns, unknown, affected}.

What is checked:

- tools: Blender exists and is 5.0 or newer (`blender --version` once, 15 s limit, remembered per file), the Unity
  editor and the sandbox project (optional, so a warning), each shared animation library file (a warning).
- storage: free disk space on the drives of library_path and export_path (under 5 GB fails, under 20 GB warns), free
  memory against min_free_ram_gb (a warning: steps wait for memory).
- engines: each ComfyUI instance that is switched on answers from its own card (instances.search_ports finds one that
  moved port; one whose port answers from the other card fails with the fix). A card that is off, or an engine that
  is not running, is "unknown", not a failure.
- engines: every ComfyUI node class the graphs use (read from the graph sources, so a new node is picked up without an
  edit here), asked one at a time through /object_info/<NodeName>, grouped by the stage that needs it, on the
  instance that runs that stage.
- models: every model file of data/models.yaml the instance should have, found by asking the loader node's
  /object_info for its file choices and testing whether the one expected file name is among them. The list of
  choices is never returned, logged or kept: only found or missing is reported.
- tools: rigging needs Blender visible to the SkinToken node: the start script of the instance that rigs sets
  SKINTOKEN_BLENDER_BIN to a file that exists.

Every request is a read-only GET.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import yaml

from .config import DATA_DIR, PACKAGE_DIR, PROJECT_DIR, Settings
from .instances import card_matches, conflict_message, device_names, probe_url, search_ports

log = logging.getLogger("vgac.preflight")

GB = 1024**3
DISK_FAIL_GB = 5.0
DISK_WARN_GB = 20.0
BLENDER_MIN = (5, 0)
BLENDER_VERSION_TIMEOUT = 15.0
GRAPHS_DIR = PACKAGE_DIR / "graphs"

GROUPS = ("tools", "engines", "models", "storage")
GROUP_LABEL = {"tools": "Tools", "engines": "Graphics engines", "models": "Models", "storage": "Storage"}
STAGE_PLAIN = {"concept": "making the picture", "turnaround": "drawing all sides", "mesh": "building the 3D model",
               "prep": "cleaning up", "rig": "rigging", "export": "handing off", "judge": "reviewing",
               "material": "making materials"}
HOST_BLENDER_STAGES = ["mesh", "prep", "rig", "export"]

# Which graph builder serves which stage. required False: an optional route (one of several concept painters, the
# Hunyuan mesh engine, the review), so something missing there is a warning, not a failure.
# file stem -> function -> (stage, required, plain name of what it makes)
GRAPH_USES: dict[str, dict[str, tuple[str, bool, str]]] = {
    "birefnet": {"cutout_graph": ("concept", True, "cutting the picture out")},
    "concept": {
        "zimage_graph": ("concept", True, "the Z-Image Turbo painter"),
        "zbase_graph": ("concept", False, "the Z-Image Base painter"),
        "klein_graph": ("concept", False, "the FLUX.2 Klein painter"),
        "anima_graph": ("concept", False, "the Anima painter"),
        "pixel_graph": ("concept", False, "the pixel art painter"),
        "chroma_graph": ("concept", False, "the Chroma painter"),
    },
    "qwen_edit": {"angle_graph": ("turnaround", True, "drawing all sides"),
                  "edit_graph": ("concept", True, "changing a picture with words")},
    "trellis": {"convert_graph": ("mesh", True, "building and baking the 3D model (TRELLIS.2)")},
    "hy3d": {"hy3d_graph": ("mesh", False, "the Hunyuan3D model builder")},
    "skintoken": {"rig_graph": ("rig", True, "rigging (SkinToken)")},
    "judge": {"judge_graph": ("judge", False, "the picture review")},
    "material": {"t2i_graph": ("material", True, "making materials")},
}

# models.yaml id -> (loader node class, its file input, stage, required)
MODEL_LOADERS: dict[str, tuple[str, str, str, bool]] = {
    "zturbo": ("CheckpointLoaderSimple", "ckpt_name", "concept", True),
    "zbase": ("UNETLoader", "unet_name", "concept", False),
    "klein": ("UNETLoader", "unet_name", "concept", False),
    "anima": ("UNETLoader", "unet_name", "concept", False),
    "pixel": ("LoraLoaderModelOnly", "lora_name", "concept", False),
    "chroma": ("UNETLoader", "unet_name", "concept", False),
    "qwen_edit": ("UNETLoader", "unet_name", "turnaround", True),
    "qwen_angles_lora": ("LoraLoaderModelOnly", "lora_name", "turnaround", True),
    "qwen_lightning_lora": ("LoraLoaderModelOnly", "lora_name", "turnaround", True),
    "birefnet": ("LoadBackgroundRemovalModel", "bg_removal_name", "concept", True),
    "trellis2": ("UNETLoader", "unet_name", "mesh", True),
    "hy3d_mv": ("ImageOnlyCheckpointLoader", "ckpt_name", "mesh", False),
    "skintoken": ("SkinTokenRigTrimesh", "ckpt_name", "rig", True),
}

_CLASS_RE = re.compile(r'"class_type"\s*:\s*"([^"]+)"')
_DEF_RE = re.compile(r"^def\s+(\w+)\s*\(", re.MULTILINE)
_BLENDER_RE = re.compile(r"Blender\s+(\d+)\.(\d+)(?:\.(\d+))?")
_SKINTOKEN_RE = re.compile(r"""SKINTOKEN_BLENDER_BIN\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


def item(id: str, group: str, label: str, state: str, detail: str, fix: str = "", needed_for: list[str] | None = None,
         technical: str = "") -> dict[str, Any]:
    return {"id": id, "group": group, "label": label, "state": state, "detail": detail, "fix": fix,
            "needed_for": list(needed_for or []), "technical": technical}


# --- what the graphs need ------------------------------------------------------------------------------------------


def graph_nodes(graphs_dir: Path = GRAPHS_DIR) -> list[dict[str, Any]]:
    """[{stage, required, what, classes}] from the graph sources: every "class_type" inside each builder function of
    GRAPH_USES. Order: as in GRAPH_USES."""
    out = []
    for stem, funcs in GRAPH_USES.items():
        path = graphs_dir / f"{stem}.py"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        starts = [(m.group(1), m.start()) for m in _DEF_RE.finditer(text)]
        bodies = {name: text[pos:(starts[i + 1][1] if i + 1 < len(starts) else len(text))] for i, (name, pos) in enumerate(starts)}
        for func, (stage, required, what) in funcs.items():
            body = bodies.get(func)
            if body is None:
                continue
            classes = list(dict.fromkeys(_CLASS_RE.findall(body)))
            if classes:
                out.append({"stage": stage, "required": required, "what": what, "classes": classes})
    return out


def stage_instance(settings: Settings, stage: str) -> str | None:
    """The instance that runs a stage first ("judge": the judge instance; "material": the material category's
    picture step)."""
    if stage == "judge":
        return settings.judge_instance
    if stage == "material":
        return settings.material_stage_instances.get("concept")
    return settings.stage_instances.get(stage)


def load_models() -> dict[str, dict[str, Any]]:
    try:
        return (yaml.safe_load((DATA_DIR / "models.yaml").read_text(encoding="utf-8")) or {}).get("models", {}) or {}
    except (OSError, yaml.YAMLError):
        return {}


def combo_choices(info: dict[str, Any] | None, input_name: str) -> list[Any] | None:
    """The choices of a combo input in an /object_info node description (old [[...], {...}] and new
    ["COMBO", {"options": [...]}] forms), or None when the input is not a list of choices."""
    if not isinstance(info, dict):
        return None
    inputs = info.get("input") or {}
    for section in ("required", "optional"):
        spec = (inputs.get(section) or {}).get(input_name)
        if not isinstance(spec, (list, tuple)) or not spec:
            continue
        first = spec[0]
        if isinstance(first, list):
            return first
        if first == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict) and isinstance(spec[1].get("options"), list):
            return spec[1]["options"]
        return None
    return None


def has_file(choices: list[Any], expected: str) -> bool:
    """Whether the one expected file name is among a loader's choices, in any subfolder, ignoring case."""
    want = expected.replace("\\", "/").rsplit("/", 1)[-1].casefold()
    for c in choices:
        if isinstance(c, str) and c.replace("\\", "/").rsplit("/", 1)[-1].casefold() == want:
            return True
    return False


# --- tools ---------------------------------------------------------------------------------------------------------

_blender_versions: dict[tuple[str, float, int], tuple[int, int, int] | None] = {}


def _run_version(exe: Path) -> str:
    proc = subprocess.run([str(exe), "--version"], capture_output=True, timeout=BLENDER_VERSION_TIMEOUT,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    return (proc.stdout or b"").decode("utf-8", errors="replace")


def blender_version(exe: Path, runner: Callable[[Path], str] = _run_version) -> tuple[int, int, int] | None:
    """Blender's version from `blender --version` (run once per file and kept), or None when it cannot be read."""
    try:
        st = exe.stat()
    except OSError:
        return None
    key = (str(exe), st.st_mtime, st.st_size)
    if key in _blender_versions:
        return _blender_versions[key]
    try:
        m = _BLENDER_RE.search(runner(exe))
        ver = (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)) if m else None
    except (OSError, subprocess.SubprocessError) as e:
        log.info("could not read the Blender version of %s: %s", exe, e)
        ver = None
    _blender_versions[key] = ver
    return ver


def check_blender(settings: Settings, runner: Callable[[Path], str] = _run_version) -> dict[str, Any]:
    exe = settings.blender_exe
    tech = f"blender_exe in backend/config.yaml: {exe}"
    if not exe:
        return item("blender", "tools", "Blender", "fail", "Blender is not set up, so cleaning up, rigging and handing "
                    "off cannot run.", "Install Blender 5.0 or newer and put the path to blender.exe in blender_exe in "
                    "backend/config.yaml.", HOST_BLENDER_STAGES, tech)
    exe = Path(exe)
    if not exe.is_file():
        return item("blender", "tools", "Blender", "fail", "Blender was not found where the settings say it is.",
                    "Install Blender 5.0 or newer, or correct blender_exe in backend/config.yaml.", HOST_BLENDER_STAGES, tech)
    ver = blender_version(exe, runner)
    if ver is None:
        return item("blender", "tools", "Blender", "warn", "Blender is there, but it did not say which version it is.",
                    "Open Blender once to check it starts. The app needs version 5.0 or newer (blender_exe in "
                    "backend/config.yaml).", HOST_BLENDER_STAGES, tech)
    v = ".".join(str(x) for x in ver)
    if ver[:2] < BLENDER_MIN:
        return item("blender", "tools", "Blender", "fail", f"Blender {v} is too old: the animation steps need 5.0 or newer.",
                    "Install Blender 5.0 or newer and point blender_exe in backend/config.yaml at it.",
                    HOST_BLENDER_STAGES, f"{tech} (version {v}; action slots need 5.0)")
    return item("blender", "tools", "Blender", "ok", f"Blender {v} is ready.", "", HOST_BLENDER_STAGES, f"{tech} (version {v})")


def check_unity(settings: Settings) -> list[dict[str, Any]]:
    out = []
    exe = settings.unity_exe
    tech = f"unity_exe in backend/config.yaml: {exe}"
    if not exe or not Path(exe).is_file():
        out.append(item("unity", "tools", "Unity editor", "warn",
                        "The Unity editor was not found, so the app cannot try the asset in Unity for you. Everything "
                        "else works; the hand-off still makes the files.",
                        "Install the Unity editor, or set unity_exe in backend/config.yaml to its Unity.exe.", ["export"], tech))
    else:
        out.append(item("unity", "tools", "Unity editor", "ok", "The Unity editor is ready for the test import.", "",
                        ["export"], tech))
    proj = settings.unity_project
    tech = f"unity_project in backend/config.yaml: {proj}"
    if not proj or not Path(proj).is_dir():
        out.append(item("unity_project", "tools", "Unity test project", "warn",
                        "The Unity project the app imports into for a test was not found.",
                        "Set unity_project in backend/config.yaml to a Unity project folder (the app's own is "
                        "unity-sandbox).", ["export"], tech))
    else:
        out.append(item("unity_project", "tools", "Unity test project", "ok", "The Unity test project is there.", "",
                        ["export"], tech))
    return out


def check_clips(settings: Settings) -> list[dict[str, Any]]:
    out = []
    for lib_id, path in settings.clips_libraries.items():
        tech = f"clips_libraries.{lib_id} in backend/config.yaml: {path}"
        if Path(path).is_file():
            out.append(item(f"clips.{lib_id}", "tools", "Shared animation library", "ok",
                            "The shared animations (walk, run, idle and more) are there.", "", ["export"], tech))
        else:
            out.append(item(f"clips.{lib_id}", "tools", "Shared animation library", "warn",
                            "The shared animation file was not found, so characters are handed off without the shared "
                            "walk, run and idle moves.",
                            f"Put the animation library back, or correct clips_libraries.{lib_id} in backend/config.yaml.",
                            ["export"], tech))
    return out


def check_skintoken_blender(settings: Settings, project_dir: Path = PROJECT_DIR) -> dict[str, Any] | None:
    rigger = settings.stage_instances.get("rig")
    inst = settings.instances.get(rigger or "")
    if inst is None:
        return None
    gpu = inst.gpu or f"card {rigger}"
    label = "Blender for rigging"
    if inst.start_script is None:
        return item("skintoken_blender", "tools", label, "warn",
                    f"The app cannot tell whether the rigging engine on the {gpu} can find Blender.",
                    "Start that ComfyUI with the SKINTOKEN_BLENDER_BIN setting pointing at blender.exe.", ["rig"],
                    f"instance {rigger} has no start_script; SKINTOKEN_BLENDER_BIN unknown")
    script = Path(inst.start_script)
    script = script if script.is_absolute() else project_dir / script
    tech = f"SKINTOKEN_BLENDER_BIN in {script}"
    try:
        text = script.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return item("skintoken_blender", "tools", label, "fail", f"The start script of the rigging engine on the {gpu} "
                    "was not found.", f"Put the start script back, or correct instances.{rigger}.start_script in "
                    "backend/config.yaml.", ["rig"], tech)
    m = _SKINTOKEN_RE.search(text)
    if not m:
        return item("skintoken_blender", "tools", label, "fail", "The rigging engine is not told where Blender is, so "
                    "rigging fails.", f'Add $env:SKINTOKEN_BLENDER_BIN = "<path to blender.exe>" to {script.name}, '
                    "then restart that graphics engine.", ["rig"], tech)
    target = Path(m.group(1))
    if not target.is_file():
        return item("skintoken_blender", "tools", label, "fail", "The rigging engine is told to use a Blender that is "
                    "not there, so rigging fails.", f"Correct SKINTOKEN_BLENDER_BIN in {script.name} to the path of "
                    "blender.exe, then restart that graphics engine.", ["rig"], f"{tech}: {target}")
    return item("skintoken_blender", "tools", label, "ok", "The rigging engine knows where Blender is.", "", ["rig"],
                f"{tech}: {target}")


# --- storage -------------------------------------------------------------------------------------------------------


def _existing(p: Path) -> Path | None:
    p = Path(p)
    for q in [p, *p.parents]:
        if q.exists():
            return q
    return None


def check_disks(settings: Settings, usage: Callable[[Path], Any] = shutil.disk_usage) -> list[dict[str, Any]]:
    paths = [("library_path", settings.library_path, "the library (every picture and model)")]
    if settings.export_path:
        paths.append(("export_path", settings.export_path, "the hand-off folder"))
    by_drive: dict[str, list[tuple[str, Path, str]]] = {}
    for key, p, what in paths:
        base = _existing(Path(p))
        if base is None:
            continue
        by_drive.setdefault(base.anchor or str(base), []).append((key, base, what))
    out = []
    for drive, entries in by_drive.items():
        keys = ", ".join(k for k, _, _ in entries)
        whats = " and ".join(w for _, _, w in entries)
        name = drive.rstrip("\\/") or drive
        try:
            free_gb = usage(entries[0][1]).free / GB
        except OSError as e:
            out.append(item(f"disk.{name}", "storage", f"Free space on {name}", "unknown",
                            f"The free space on {name} could not be read.", "", [], f"{keys}: {e}"))
            continue
        tech = f"{keys} on {drive}: {free_gb:.1f} GB free"
        label = f"Free space on {name}"
        if free_gb < DISK_FAIL_GB:
            out.append(item(f"disk.{name}", "storage", label, "fail",
                            f"Only {free_gb:.1f} GB is free on {name}, where {whats} is kept. New steps may fail to save.",
                            f"Free up space on {name} (at least {DISK_WARN_GB:g} GB is comfortable), for example by "
                            "deleting assets you no longer need.", ["concept", "turnaround", "mesh", "prep", "rig", "export"], tech))
        elif free_gb < DISK_WARN_GB:
            out.append(item(f"disk.{name}", "storage", label, "warn",
                            f"{free_gb:.1f} GB is free on {name}, where {whats} is kept. That runs out after a few dozen "
                            "3D models.", f"Free up some space on {name} soon.", ["mesh", "prep", "rig", "export"], tech))
        else:
            out.append(item(f"disk.{name}", "storage", label, "ok", f"{free_gb:.0f} GB is free on {name}.", "", [], tech))
    return out


def check_memory(settings: Settings, free_gb: Callable[[], float] | None = None) -> dict[str, Any]:
    if free_gb is None:
        import psutil

        def free_gb() -> float:
            return psutil.virtual_memory().available / GB
    free = free_gb()
    need = settings.min_free_ram_gb
    tech = f"min_free_ram_gb in backend/config.yaml: {need:g}; {free:.1f} GB free now"
    gpu_stages = ["concept", "turnaround", "mesh", "rig", "material", "judge"]
    if free < need:
        return item("memory", "storage", "Free memory", "warn",
                    f"{free:.1f} GB of memory is free; steps wait until {need:g} GB is free.",
                    "Close programs you do not need (games, browsers with many tabs), or lower min_free_ram_gb in "
                    "backend/config.yaml.", gpu_stages, tech)
    return item("memory", "storage", "Free memory", "ok", f"{free:.0f} GB of memory is free.", "", [], tech)


# --- graphics engines ----------------------------------------------------------------------------------------------


async def _engine_answer(settings: Settings, name: str, client: Any, supervisor: Any,
                         probe: Callable[[str], Awaitable[dict[str, Any] | None]]) -> tuple[bool, str, str, str]:
    """(answers from its own card, url in use, device names, setup problem). Read-only."""
    inst = settings.instances[name]
    if supervisor is not None:
        ok = await supervisor.answers(name)
        url = supervisor.current_url(name)
        summary = client.last_vram_summary() if client is not None and hasattr(client, "last_vram_summary") else None
        return ok, url, device_names(summary), supervisor.conflict(name)
    url = str(getattr(client, "base_url", inst.url)).rstrip("/")
    try:
        summary = await client.cached_vram_summary(5.0) if client is not None else None
    except Exception:  # noqa: BLE001
        summary = None
    reachable = bool(summary and summary.get("reachable"))
    d = inst.discover
    if d is None:
        return reachable, url, device_names(summary), ""
    if reachable and card_matches(summary, d.gpu_match):
        return True, url, device_names(summary), ""
    found = await search_ports(settings, name, url, probe)
    if found is not None:
        if client is not None and hasattr(client, "set_base_url"):
            client.set_base_url(found)
        ans = await probe(found)
        return True, found, device_names(ans), ""
    if reachable:
        return False, url, device_names(summary), conflict_message(settings, name, url, device_names(summary))
    return False, url, "", ""


async def _node_info(client: Any, cls: str, cache: dict[str, Any]) -> Any:
    """/object_info/<cls> for one node, kept for this run. Returns the description, None (no such node) or an
    Exception (could not ask)."""
    if cls not in cache:
        try:
            cache[cls] = await client.object_info(cls)
        except Exception as e:  # noqa: BLE001
            cache[cls] = e
    return cache[cls]


async def check_instance(settings: Settings, name: str, client: Any, disabled: set[str], supervisor: Any = None,
                         can_start: bool | None = None,
                         probe: Callable[[str], Awaitable[dict[str, Any] | None]] = probe_url) -> list[dict[str, Any]]:
    inst = settings.instances[name]
    gpu = inst.gpu or f"card {name}"
    stages_here = [s for s in ("concept", "turnaround", "mesh", "rig", "material", "judge") if stage_instance(settings, s) == name]
    uses = [g for g in graph_nodes() if g["stage"] in stages_here]
    models = load_models()
    model_checks = [(mid, spec) for mid, spec in MODEL_LOADERS.items() if spec[2] in stages_here and models.get(mid, {}).get("file")]
    out: list[dict[str, Any]] = []
    eng_id, eng_label = f"engine.{name}", f"Graphics engine on the {gpu}"
    starts = can_start if can_start is not None else inst.start_script is not None

    def not_checked(reason: str) -> list[dict[str, Any]]:
        rest = []
        for g in uses:
            rest.append(item(f"nodes.{name}.{g['stage']}.{len(rest)}", "engines", f"Parts for {g['what']}", "unknown",
                             f"Not checked: {reason}", "", [g["stage"]], f"instance {name}: " + ", ".join(g["classes"])))
        for mid, (cls, inp, stage, _req) in model_checks:
            rest.append(item(f"model.{name}.{mid}", "models", models[mid].get("name", mid), "unknown",
                             f"Not checked: {reason}", "", [stage], f"instance {name}: {models[mid]['file']} ({cls}.{inp})"))
        return rest

    if name in disabled:
        reason = "the card is off."
        return [item(eng_id, "engines", eng_label, "unknown", "Not checked: the card is off.", "", stages_here,
                     f"instance {name} at {inst.url} (turned off in the app)")] + not_checked(reason)
    ok, url, devices, problem = await _engine_answer(settings, name, client, supervisor, probe)
    tech = f"instance {name} at {url}" + (f" (configured {inst.url}, found by its card)" if url != inst.url.rstrip("/") else "")
    if devices:
        tech += f"; device {devices}"
    if problem:
        # conflict_message: "<who> is using port N on the <other card>; close it or change its port so ..."
        what, _, fix = problem.partition("; ")
        fix = (fix[:1].upper() + fix[1:]) if fix else "Close the other ComfyUI or change its port."
        return [item(eng_id, "engines", eng_label, "fail", f"{what}, so the app will not send the {gpu}'s work to it.",
                     fix, stages_here, tech)] + not_checked("the engine on its port runs on the other card.")
    if not ok:
        if starts:
            return [item(eng_id, "engines", eng_label, "unknown", "Not running now; it starts when needed.", "",
                         stages_here, tech)] + not_checked("the engine is not running; it starts when needed.")
        return [item(eng_id, "engines", eng_label, "unknown", "Not running now, and the app cannot start it.",
                     f"Start ComfyUI on the {gpu} yourself (the app has no start script for it).", stages_here,
                     tech)] + not_checked("the engine is not running.")
    where = " (found on another port, by its card)" if url != inst.url.rstrip("/") else ""
    out.append(item(eng_id, "engines", eng_label, "ok", f"Running{where}.", "", stages_here, tech))

    cache: dict[str, Any] = {}
    for i, g in enumerate(uses):
        missing, unasked = [], []
        for cls in g["classes"]:
            info = await _node_info(client, cls, cache)
            if isinstance(info, Exception):
                unasked.append(cls)
            elif info is None:
                missing.append(cls)
        label = f"Parts for {g['what']}"
        ntech = f"instance {name}: node classes " + ", ".join(g["classes"])
        if missing:
            state = "fail" if g["required"] else "warn"
            n = len(missing)
            parts = f"{n} part{'s' if n != 1 else ''}"
            plain = STAGE_PLAIN.get(g["stage"], g["stage"])
            if g["required"]:
                detail = f"The engine on the {gpu} is missing {parts} needed for {g['what']}, so {plain} cannot run."
            else:
                detail = (f"The engine on the {gpu} is missing {parts} needed for {g['what']}; {plain} still works with "
                          "the other choices.")
            out.append(item(f"nodes.{name}.{g['stage']}.{i}", "engines", label, state, detail,
                            "Install the missing custom nodes in that ComfyUI (ComfyUI Manager can do it), or update "
                            "ComfyUI, then restart it.", [g["stage"]], ntech + "; missing: " + ", ".join(missing)))
        elif unasked:
            out.append(item(f"nodes.{name}.{g['stage']}.{i}", "engines", label, "unknown",
                            "Not checked: the engine stopped answering.", "", [g["stage"]], ntech))
        else:
            out.append(item(f"nodes.{name}.{g['stage']}.{i}", "engines", label, "ok", "All parts are installed.", "",
                            [g["stage"]], ntech))

    for mid, (cls, inp, stage, required) in model_checks:
        m = models[mid]
        expected = str(m["file"])
        label = str(m.get("name") or mid)
        mtech = f"instance {name}: {expected} ({cls}.{inp}; models.yaml {mid})"
        info = await _node_info(client, cls, cache)
        if isinstance(info, Exception):
            out.append(item(f"model.{name}.{mid}", "models", label, "unknown", "Not checked: the engine stopped "
                            "answering.", "", [stage], mtech))
            continue
        if info is None:
            out.append(item(f"model.{name}.{mid}", "models", label, "unknown", "Not checked: the part that loads it is "
                            "missing (see the parts above).", "", [stage], mtech))
            continue
        choices = combo_choices(info, inp)
        if choices is None:
            out.append(item(f"model.{name}.{mid}", "models", label, "unknown", "Not checked: this engine does not list "
                            "its files.", "", [stage], mtech))
            continue
        found = has_file(choices, expected)
        del choices  # only found or missing leaves this function
        if found:
            out.append(item(f"model.{name}.{mid}", "models", label, "ok", "Installed.", "", [stage], mtech))
        else:
            state = "fail" if required else "warn"
            what = STAGE_PLAIN.get(stage, stage)
            detail = (f"The model file is not installed on the {gpu}, so {what} cannot run." if required else
                      f"The model file is not installed on the {gpu}; {what} works with the others, but not this one.")
            out.append(item(f"model.{name}.{mid}", "models", label, state, detail,
                            f"Download {expected} and put it in that ComfyUI's models folder (the {cls} loader's "
                            "folder), then restart it.", [stage], mtech))
    return out


# --- all of it -----------------------------------------------------------------------------------------------------


async def run_checks(settings: Settings, clients: dict[str, Any], disabled: set[str] | None = None,
                     supervisor: Any = None, blender_runner: Callable[[Path], str] = _run_version,
                     probe: Callable[[str], Awaitable[dict[str, Any] | None]] = probe_url) -> list[dict[str, Any]]:
    """Every check, in group order (tools, engines, models, storage). Read-only."""
    disabled = set(disabled or ())
    items: list[dict[str, Any]] = []
    items.append(await asyncio.to_thread(check_blender, settings, blender_runner))
    items.extend(check_unity(settings))
    items.extend(check_clips(settings))
    sk = check_skintoken_blender(settings)
    if sk is not None:
        items.append(sk)
    for name in settings.instances:
        client = clients.get(name)
        can_start = supervisor.can_start(name) if supervisor is not None else None
        if client is None:
            items.append(item(f"engine.{name}", "engines", f"Graphics engine on the {settings.instances[name].gpu}",
                              "unknown", "Not checked yet.", "", [], f"instance {name}"))
            continue
        try:
            items.extend(await check_instance(settings, name, client, disabled, supervisor, can_start, probe))
        except Exception as e:  # noqa: BLE001
            log.exception("setup check of instance %s failed", name)
            items.append(item(f"engine.{name}", "engines", f"Graphics engine on the {settings.instances[name].gpu}",
                              "unknown", "Not checked: the check itself failed.", "", [], f"{type(e).__name__}: {e}"))
    items.extend(check_disks(settings))
    items.append(check_memory(settings))
    order = {g: i for i, g in enumerate(GROUPS)}
    items.sort(key=lambda it: order.get(it["group"], 99))
    return items


def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
    fails = sum(1 for i in items if i["state"] == "fail")
    warns = sum(1 for i in items if i["state"] == "warn")
    unknown = sum(1 for i in items if i["state"] == "unknown")
    stages: list[str] = []
    for i in items:
        if i["state"] in ("fail", "warn"):
            for s in i["needed_for"]:
                if s not in stages:
                    stages.append(s)
    order = ["concept", "turnaround", "mesh", "prep", "rig", "export", "material", "judge"]
    stages.sort(key=lambda s: order.index(s) if s in order else 99)
    return {"ok": fails == 0, "fails": fails, "warns": warns, "unknown": unknown, "affected": stages}


def report(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"summary": summarize(items), "items": items, "groups": [{"id": g, "label": GROUP_LABEL[g]} for g in GROUPS],
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
