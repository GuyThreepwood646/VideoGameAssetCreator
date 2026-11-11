"""Clean-up of the pictures and meshes the app copies into ComfyUI's input folder.

Every job copies its inputs there (ComfyClient.stage_input) under a name that starts with "vgac_" and carries the
version's (or for the style read, the job's) first eight id characters. Nothing removed them, so the folder filled
with thousands of files from deleted projects. After every job, and once when the queue starts, each "vgac_" file at
the top of the folder is removed unless a queued or running job still needs it (its version or job id is in the name).

Only "vgac_" files are touched; the person's own pictures and ComfyUI's subfolders stay.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from sqlalchemy import select

from .db import Job, session_factory

log = logging.getLogger(__name__)

PREFIX = "vgac_"


def active_ids() -> set[str]:
    """The first eight characters of every queued or running job's id and version id."""
    out: set[str] = set()
    with session_factory()() as db:
        for j in db.scalars(select(Job).where(Job.status.in_(("queued", "running")))).all():
            out.add(j.id[:8])
            if j.version_id:
                out.add(j.version_id[:8])
    return out


def sweep(input_dir: Path | str | None, keep: Iterable[str]) -> int:
    """Remove the "vgac_" files in input_dir whose name has none of the ids in keep. Returns how many went. Never
    raises (a file ComfyUI has open stays for the next sweep)."""
    if not input_dir:
        return 0
    d = Path(input_dir)
    if not d.is_dir():
        return 0
    keep = set(keep)
    n = 0
    for p in d.iterdir():
        name = p.name
        if not name.startswith(PREFIX) or not p.is_file():
            continue
        if any(k in name for k in keep):
            continue
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    if n:
        log.info("removed %d leftover input file(s) from %s", n, d)
    return n


def sweep_now(input_dir: Path | str | None) -> int:
    """sweep() keeping what the queued and running jobs need."""
    try:
        return sweep(input_dir, active_ids())
    except Exception:  # noqa: BLE001
        log.exception("input folder clean-up failed")
        return 0
