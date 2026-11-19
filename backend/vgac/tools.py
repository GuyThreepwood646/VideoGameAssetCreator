"""Subprocess runners for the host tools: Blender headless scripts and the Unity sandbox importer.

Both are blocking; stage modules call them through asyncio.to_thread. The running process is registered on the
job context so a cancel can kill it.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from .config import BLENDER_SCRIPTS_DIR, Settings


log = logging.getLogger("vgac.tools")


class ToolError(RuntimeError):
    pass


def run_process(cmd: list[str], log_path: Path | None = None, timeout: float | None = None,
                on_start: Callable[[subprocess.Popen], None] | None = None, cwd: Path | None = None) -> str:
    """Run a command, return its combined output, raise ToolError on a non-zero exit."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(cwd) if cwd else None,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if on_start:
        on_start(proc)
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
        raise ToolError(f"timed out after {timeout}s: {' '.join(cmd[:3])}")
    text = (out or b"").decode("utf-8", errors="replace")
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(text, encoding="utf-8")
    if proc.returncode != 0:
        raise ToolError(f"{Path(cmd[0]).name} exited {proc.returncode}:\n{text[-4000:]}")
    return text


def blender_cmd(settings: Settings, script: str, args: list[Any]) -> list[str]:
    if not settings.blender_exe or not Path(settings.blender_exe).is_file():
        from .firstrun import blender_problem

        problem = blender_problem(settings)
        raise ToolError(" ".join(problem) if problem else "Blender is not set up (blender_exe).")
    script_path = BLENDER_SCRIPTS_DIR / script
    if not script_path.is_file():
        raise ToolError(f"blender script missing: {script_path}")
    from . import blender_watch

    note = blender_watch.check(settings.blender_exe)  # Steam / the Store may have updated it; never a raw error for that
    if note is not None:
        log.warning("blender: %s", note["message"])
        if not note["usable"]:
            raise ToolError(note["message"])
    return [str(settings.blender_exe), "-b", "--python", str(script_path), "--", *[str(a) for a in args]]


LONG_PATH_LIMIT = 260  # Windows MAX_PATH: a path this many characters or longer cannot be opened by Blender's Python
_ABSOLUTE_LOOKING = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


def check_blender_paths(args: list[Any]) -> None:
    """Raise ToolError before Blender starts when a path argument is too long for Windows to open.

    Path objects and strings that look like absolute paths (a drive or a UNC share) are checked; other arguments
    are ignored. Without this check Blender's glTF importer fails with only "Please select a file"."""
    longest = 0
    for a in args:
        if isinstance(a, Path):
            text = str(a)
        elif isinstance(a, str) and _ABSOLUTE_LOOKING.match(a):
            text = a
        else:
            continue
        n = len(os.path.abspath(text))
        if n >= LONG_PATH_LIMIT:
            longest = max(longest, n)
    if longest:
        raise ToolError(f"A file path is too long for Blender ({longest} characters; the limit is {LONG_PATH_LIMIT - 1}). "
                        "Move the library to a shorter folder, for example C:\\VGAC\\library, in Settings "
                        "(Library folder).")


def run_blender(settings: Settings, script: str, args: list[Any], log_path: Path | None = None, timeout: float | None = 3600,
                on_start: Callable[[subprocess.Popen], None] | None = None) -> str:
    check_blender_paths(args)
    return run_process(blender_cmd(settings, script, args), log_path=log_path, timeout=timeout, on_start=on_start)


def unity_cmd(settings: Settings, method: str, args: dict[str, Any], flags: list[str], log_path: Path,
              project_path: Path | str | None = None) -> list[str]:
    if not settings.unity_exe or not Path(settings.unity_exe).is_file():
        raise ToolError(f"unity_exe not configured or missing: {settings.unity_exe}")
    project = Path(project_path) if project_path else settings.unity_project
    if not project or not Path(project).is_dir():
        raise ToolError(f"unity project not configured or missing: {project}")
    cmd = [str(settings.unity_exe), "-batchmode", "-quit", "-projectPath", str(project), "-executeMethod", method]
    cmd += flags
    for k, v in args.items():
        cmd += [k, str(v)]
    cmd += ["-logFile", str(log_path)]
    return cmd


def run_unity(settings: Settings, method: str, args: dict[str, Any], flags: list[str], log_path: Path,
              timeout: float | None = 3600, on_start: Callable[[subprocess.Popen], None] | None = None,
              project_path: Path | str | None = None) -> str:
    cmd = unity_cmd(settings, method, args, flags, log_path, project_path=project_path)
    try:
        return run_process(cmd, log_path=None, timeout=timeout, on_start=on_start)
    except ToolError as e:
        tail = ""
        if log_path.is_file():
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
        raise ToolError(f"{e}\n--- unity log tail ---\n{tail}")
