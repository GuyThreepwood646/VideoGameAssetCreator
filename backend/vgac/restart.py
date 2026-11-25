"""Asking the launcher (VGAC.exe) to restart this backend, or to close (kind "quit").

The backend cannot restart itself without leaving the new copy owned by whoever asked, so it only writes a request
file, local/restart-request.json. The launcher that started this backend reads it, stops this process and starts a new
one that it owns (starter/src/Supervisor.cs, RestartRequests). The launcher tells the new backend through
environment variables what happened:

- VGAC_LAUNCHER_RESTART=1   set on every backend the launcher starts: "a launcher is watching the request file".
- VGAC_RESTART_ID           the id of the request that caused this start (empty after an ordinary start).
- VGAC_RESTART_ENGINES      "a,b": the engines to start again (only for engines "restart").

engines "keep" leaves the engines this backend started running; the new backend adopts them (instances.py,
engines.json). engines "restart" stops them with the backend and the new backend starts them again.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import PROJECT_DIR

REQUEST_FILE = Path("local") / "restart-request.json"
ENGINE_CHOICES = ("keep", "restart")
KIND_CHOICES = ("restart", "quit")
REQUEST_MAX_AGE_SECONDS = 60  # the launcher ignores and deletes an older request (starter Supervisor.cs)
STARTED_AT = datetime.now(timezone.utc).isoformat(timespec="seconds")


class RestartRefused(Exception):
    """The request was not written; `status` is the HTTP status and the message is a plain sentence."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def launched_by_launcher() -> bool:
    return os.environ.get("VGAC_LAUNCHER_RESTART") == "1"


def started_by_request() -> str:
    """The id of the restart request this backend was started for ("" after an ordinary start)."""
    return os.environ.get("VGAC_RESTART_ID", "")


def engines_to_start_again() -> list[str]:
    return [n for n in os.environ.get("VGAC_RESTART_ENGINES", "").split(",") if n]


def identity() -> dict[str, Any]:
    """Shown by /api/health: after a restart, the caller compares `restart_id` and `started_at` to know the new
    backend is the one answering."""
    return {"pid": os.getpid(), "started_at": STARTED_AT, "restart_id": started_by_request() or None,
            "launcher_restart": launched_by_launcher()}


def work_in_flight(status: dict[str, Any]) -> int:
    """Jobs running or waiting, from JobQueue.status(): running ones counted once by id (a lent card shows the
    borrower's id again), waiting ones by number."""
    running: set[str] = set()
    waiting = 0
    for one in status.values():
        if not isinstance(one, dict):
            continue
        for key in ("current_job_id", "lent_to", "quick_job_id"):
            if one.get(key):
                running.add(str(one[key]))
        for hid in one.get("helper_job_ids") or []:
            running.add(str(hid))
        waiting += int(one.get("queued") or 0)
    return len(running) + waiting


def write_request(engines: str, force: bool, in_flight: int, owned_engines: dict[str, int | None],
                  project_dir: Path | None = None, now: float | None = None, kind: str = "restart") -> dict[str, Any]:
    """Check the call and write the request file. `owned_engines`: engine name -> pid, for the engines this backend
    started and that are running now. `kind` "quit" asks the launcher to stop the backend, the engines it started and
    itself, as its Quit does. Returns {id, engines, restart_engines}."""
    if kind not in KIND_CHOICES:
        raise RestartRefused(f"kind must be one of {', '.join(KIND_CHOICES)}.", 422)
    verb = "Closing" if kind == "quit" else "Restarting"
    if engines not in ENGINE_CHOICES:
        raise RestartRefused(f"engines must be one of {', '.join(ENGINE_CHOICES)}.", 422)
    if not launched_by_launcher():
        raise RestartRefused("This copy of the app was not started by a VGAC.exe that can " + ("close" if kind == "quit" else "restart") + " it. Use "
                             "the VGAC tray menu, or close VGAC.exe, rebuild it with starter\\build.cmd and start it again.")
    if in_flight and not force:
        raise RestartRefused(f"The app is working on {in_flight} step{'s' if in_flight != 1 else ''} (running or waiting). "
                             f"{verb} now would stop them. Wait for them to finish, or go ahead anyway.")
    root = Path(project_dir) if project_dir is not None else PROJECT_DIR
    when = int(now if now is not None else time.time())
    try:
        earlier = json.loads((root / REQUEST_FILE).read_text(encoding="utf-8"))
        if when - int(earlier.get("time", 0)) < REQUEST_MAX_AGE_SECONDS:
            raise RestartRefused("A restart or close is already on its way. Wait a moment and look again.")
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    rid = secrets.token_hex(8)
    body = {
        "id": rid,
        "time": when,
        "kind": kind,
        "engines": engines,
        "force": force,
        # engines to bring back after the tree is stopped; for "keep" the pids the launcher must leave running
        "restart_engines": sorted(owned_engines) if engines == "restart" else [],
        "keep_pids": sorted(p for p in owned_engines.values() if isinstance(p, int)) if engines == "keep" else [],
    }
    path = root / REQUEST_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return {"id": rid, "engines": engines, "restart_engines": body["restart_engines"]}
