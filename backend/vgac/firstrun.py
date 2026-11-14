"""First run: what the app has, what it lacks, and which steps can run (docs/API.md "Setup").

A machine with nothing set up must start, say so, and refuse each step with the one thing it lacks and where to give
it. Three things live here, all built on the setup check (preflight.py), not beside it:

* the wording of what is missing (blender_problem, card_problem): preflight's items and the refusals use the same
  sentences;
* blockers(): what stops one step from running now, asked when a step is queued (pipeline.run_stage and submit) and
  when the queue picks a card (jobs.JobQueue.place, which leaves out a card that cannot work);
* first_run(): the state the setup card reads, added to GET /api/setup as `first_run`.

Settings `require_setup: false` turns the refusals off (tests that stub the engines).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import config
from .config import Settings

WHERE = "On the Health page, in the setup card"

PortOpen = Callable[[str], bool]


def _port_open(url: str) -> bool:
    from .instances import ProcessOps

    return ProcessOps().port_open(url)


def blender_problem(settings: Settings) -> tuple[str, str] | None:
    """(what is wrong, what to do) when Blender is not set or not where the settings say; None when the file is there.
    Reads no version (preflight.check_blender does)."""
    exe = settings.blender_exe
    fix = f"{WHERE}, give the path to blender.exe (Blender 5.0 or newer, free from blender.org)."
    if not exe:
        return "Blender is not set up, so cleaning up, rigging and handing off cannot run.", fix
    if not Path(exe).is_file():
        return f"Blender was not found at {exe}, so cleaning up, rigging and handing off cannot run.", fix
    return None


def engine_problem(settings: Settings, name: str) -> str:
    """What is wrong with the ComfyUI folder an instance's `engine` names ("" when it is fine or there is none)."""
    inst = settings.instances.get(name)
    e = inst.engine if inst is not None else None
    if e is None:
        return ""
    if not Path(e.comfy_dir).is_dir():
        return f"The ComfyUI folder {e.comfy_dir} does not exist."
    if not Path(e.python).is_file():
        return f"The ComfyUI folder {e.comfy_dir} has no Python at {e.python}."
    if not (Path(e.comfy_dir) / e.main).is_file():
        return f"The ComfyUI folder {e.comfy_dir} has no {e.main}."
    return ""


def card_problem(settings: Settings, name: str, supervisor: Any = None,
                 port_open: PortOpen | None = None) -> tuple[str, str] | None:
    """(what is wrong, what to do) when this ComfyUI instance can neither be started by the app nor is running now.
    A ComfyUI the person started themself at the instance's address counts as running."""
    from .jobs import gpu_name

    inst = settings.instances.get(name)
    if inst is None:
        return None
    gpu = gpu_name(name, settings)
    broken = engine_problem(settings, name)
    if broken:
        return (f"{broken} The app cannot start ComfyUI for {gpu}.",
                f"{WHERE}, correct the ComfyUI folder for {gpu}.")
    if inst.engine is not None or inst.start_script is not None:
        return None
    url = supervisor.current_url(name) if supervisor is not None else inst.url
    if (port_open or _port_open)(url):
        return None
    return (f"The app does not know where ComfyUI for {gpu} is, and nothing is answering at {url}, so its steps "
            "cannot run.",
            f"{WHERE}, give the ComfyUI folder for {gpu}, or start ComfyUI yourself at {url}.")


def unusable_cards(settings: Settings, supervisor: Any = None, port_open: PortOpen | None = None) -> set[str]:
    """The ComfyUI instances that cannot work now (card_problem). Empty when `require_setup` is off."""
    if not settings.require_setup:
        return set()
    return {n for n in settings.instances if card_problem(settings, n, supervisor, port_open) is not None}


def pick_instance(settings: Settings, stage: str, category: str, supervisor: Any = None, disabled: Any = (),
                  port_open: PortOpen | None = None) -> str:
    """Where a step would run now: the queue's choice (jobs.route) leaving out cards that cannot work; the step's own
    card when none can."""
    from .jobs import preferred_instance, route

    off = set(disabled or ()) | unusable_cards(settings, supervisor, port_open)
    return route(stage, category, settings, off) or preferred_instance(stage, category, settings)


def blockers(settings: Settings, stage: str, category: str = "", instance: str | None = None, supervisor: Any = None,
             port_open: PortOpen | None = None, disabled: Any = ()) -> list[dict[str, str]]:
    """What stops a step from running now: [{id, message, setting}], each message plain and saying where to fix it.
    Empty when it can run (or `require_setup` is off). Reads no ComfyUI and starts nothing."""
    if not settings.require_setup:
        return []
    from .preflight import HOST_BLENDER_STAGES

    out: list[dict[str, str]] = []
    problem = config.settings_problem()
    if problem:
        out.append({"id": "settings", "setting": "", "message":
                    f"This machine's settings could not be used: {problem} Fix or remove that file, then restart the app."})
        return out
    target = instance or pick_instance(settings, stage, category, supervisor, disabled, port_open)
    if target in settings.instances:
        cp = card_problem(settings, target, supervisor, port_open)
        if cp is not None:
            out.append({"id": f"engine.{target}", "setting": f"instances.{target}.engine.comfy_dir",
                        "message": f"{cp[0]} {cp[1]}"})
    if stage in HOST_BLENDER_STAGES:
        bp = blender_problem(settings)
        if bp is not None:
            out.append({"id": "blender", "setting": "blender_exe", "message": f"{bp[0]} {bp[1]}"})
    return out


def sentence(found: list[dict[str, str]]) -> str:
    return " ".join(b["message"] for b in found)


# --- the state the setup card reads --------------------------------------------------------------------------------


def _brief(it: dict[str, Any]) -> dict[str, Any]:
    return {"id": it["id"], "label": it["label"], "detail": it["detail"], "fix": it.get("fix", ""),
            "setting": it.get("setting", ""), "needed_for": it.get("needed_for", [])}


def has_machine_settings() -> bool:
    """Whether this machine's settings file holds any setting of the app's own."""
    mp = config.machine_path()
    if mp is None:
        return False
    try:
        return bool(config.read_machine(mp).get("backend"))
    except config.ConfigError:
        return False


def first_run(settings: Settings, items: list[dict[str, Any]], supervisor: Any = None,
              port_open: PortOpen | None = None, restart: list[str] | None = None) -> dict[str, Any]:
    """The first-run state, from the setup check's items.

    state: "settings_problem" (the machine's file could not be used), "not_set_up" (nothing is set up yet and a step
    would be refused), "partly_set_up" (something a step needs is missing), "ready".
    missing: what stops steps (the items that fail, with the setting that fixes each); optional: what only widens
    what the app can do (a warning or an offer); configured: what is in place; unchecked: how many checks wait for
    a running ComfyUI. steps: each step, whether it can run now, and what it lacks. restart: settings saved but only
    in use after the app restarts."""
    from .reference import STAGE_ORDER
    from .preflight import STAGE_PLAIN

    problem = config.settings_problem()
    missing = [_brief(i) for i in items if i["state"] == "fail"]
    optional = [_brief(i) for i in items if i["state"] in ("warn", "optional")]
    configured = [{"id": i["id"], "label": i["label"]} for i in items if i["state"] == "ok"]
    unchecked = sum(1 for i in items if i["state"] == "unknown")
    if problem:
        missing.insert(0, {"id": "settings", "label": "This machine's settings file", "detail": problem,
                           "fix": "Fix or remove that file, then restart the app.", "setting": "", "needed_for": []})
    steps = []
    for stage in STAGE_ORDER:
        found = blockers(settings, stage, "", supervisor=supervisor, port_open=port_open)
        steps.append({"stage": stage, "plain": STAGE_PLAIN.get(stage, stage), "can_run": not found,
                      "missing": [b["id"] for b in found], "message": sentence(found)})
    runnable = [s for s in steps if s["can_run"]]
    if problem:
        state = "settings_problem"
    elif not runnable:
        state = "not_set_up"
    elif missing:
        state = "partly_set_up"
    else:
        state = "ready"
    return {"state": state, "fresh": not has_machine_settings(), "settings_problem": problem, "missing": missing,
            "optional": optional, "configured": configured, "unchecked": unchecked, "steps": steps,
            "restart_needed": list(restart or [])}
