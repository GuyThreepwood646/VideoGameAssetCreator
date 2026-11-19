"""Start gate for the big-files move: the backend refuses to start while a move is unfinished or unreadable.

This is the same rule as the starter's MoveGate (starter/src/Move.cs), in the form the backend, its restart scripts
(restart.ps1, run.ps1, run.sh) and the launcher can all apply:

- local/move.json exists: a move is unfinished (copying or switching), or its record cannot be judged. Either way
  nothing runs until Maintenance judges or discards it in the starter.
- local/restart-needed.json exists: the move switched and VGAC has not been restarted yet. The starter clears this
  marker when it starts the backend, so a backend started by hand while it exists is refused.
- The record cannot even be looked at: refused too (never guessed).

Run `python -m vgac.move_gate` from the backend folder: it prints the reason and exits 1 when blocked, 0 when clear.
The FastAPI lifespan calls blocking_reason() as well, so uvicorn refuses to serve in the same situation.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .config import PROJECT_DIR

MOVE_RECORD = Path("local") / "move.json"
RESTART_MARKER = Path("local") / "restart-needed.json"

# The same plain sentences as the starter's MoveGate, so the person reads one message in every place.
_UNFINISHED = ("A move of the big files is not finished. Until it is, nothing else runs: open Maintenance in the VGAC starter, "
               "Move the big files, and continue it or abandon it.")
_RESTART = ("VGAC has been moved to a new folder for the big files and has to be restarted first. Close it and start it again "
            "from the VGAC starter.")
_UNKNOWN = ("VGAC cannot tell whether a move of the big files is unfinished ({why}), so nothing runs. Fix local\\machine.json and "
            "local\\move.json, then start again.")


def blocking_reason(project_dir: Path | str | None = None) -> str | None:
    """The plain reason the start is refused, or None when nothing blocks it. Read from the files on every call."""
    root = Path(project_dir) if project_dir is not None else PROJECT_DIR
    try:
        if (root / RESTART_MARKER).exists():
            return _RESTART
        if (root / MOVE_RECORD).exists():
            return _UNFINISHED
        return None
    except OSError as exc:
        return _UNKNOWN.format(why=str(exc) or type(exc).__name__)


def main(argv: list[str] | None = None) -> int:
    reason = blocking_reason()
    if reason is None:
        return 0
    print(reason, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
