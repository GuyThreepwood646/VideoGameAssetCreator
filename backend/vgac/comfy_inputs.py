"""Clean-up of the pictures and meshes the app copies into ComfyUI's input folder.

Every job copies its inputs there (ComfyClient.stage_input) under a name that starts with "vgac_" and carries the
version's (or for the style read, the job's) first eight id characters. Each copy is also written into this backend's
record of what it staged: a small JSON file in the backend's own data folder (library folder), mapping file name to
the time it was written. The record is never in ComfyUI's input folder.

After every job, and once when the queue starts, sweep() removes a file only when all of these hold: it is in this
backend's record, its name starts with "vgac_", it is older than MIN_AGE_S, and no id of a queued or running job of
this backend is in its name. The record entry goes with the file, and an entry whose file is already gone is dropped.
Files this backend did not stage (the person's own pictures, other tools' files, other backends' files) are never
removed, whatever their names. Two backends sharing one ComfyUI input folder each have their own record, so neither
removes the other's inputs.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Iterable

from sqlalchemy import select

from .config import get_settings
from .db import Job, session_factory

log = logging.getLogger(__name__)

PREFIX = "vgac_"
MIN_AGE_S = 6 * 3600   # longer than any one job runs, so another backend's live inputs are never removed
RECORD_NAME = "comfy_inputs_staged.json"
_lock = threading.Lock()   # stage_input and sweep run on worker threads


def record_path(data_dir: Path | str) -> Path:
    """Where this backend keeps the record of the input files it staged."""
    return Path(data_dir) / RECORD_NAME


def _load(path: Path) -> dict[str, float]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        # An unreadable record means nothing is removed (its files stay, which is safe).
        log.warning("input record %s could not be read; its files are kept", path)
        return {}
    return {str(k): float(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _save(path: Path, data: dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def remember(data_dir: Path | str, name: str, when: float | None = None) -> None:
    """Record that this backend wrote the input file `name`."""
    with _lock:
        path = record_path(data_dir)
        data = _load(path)
        data[name] = time.time() if when is None else when
        _save(path, data)


def active_ids() -> set[str]:
    """The first eight characters of every queued or running job's id and version id."""
    out: set[str] = set()
    with session_factory()() as db:
        for j in db.scalars(select(Job).where(Job.status.in_(("queued", "running")))).all():
            out.add(j.id[:8])
            if j.version_id:
                out.add(j.version_id[:8])
    return out


def sweep(input_dir: Path | str | None, keep: Iterable[str], data_dir: Path | str | None,
          min_age_s: float = MIN_AGE_S) -> int:
    """Remove the files in input_dir that this backend's record (in data_dir) lists, whose names start with "vgac_" and
    contain none of the ids in keep, and that are older than min_age_s. Entries whose file is gone are dropped.
    Returns how many files went. Never raises (a file ComfyUI has open stays for the next sweep)."""
    if not input_dir or data_dir is None:
        return 0
    d = Path(input_dir)
    keep = set(keep)
    cutoff = time.time() - min_age_s
    n = 0
    with _lock:
        path = record_path(data_dir)
        data = _load(path)
        if not data:
            return 0
        changed = False
        for name in list(data):
            p = d / name
            if not p.is_file():
                del data[name]          # already gone: drop the entry, no error
                changed = True
                continue
            if not name.startswith(PREFIX) or any(k in name for k in keep):
                continue
            try:
                if p.stat().st_mtime > cutoff:
                    continue
                p.unlink()
            except OSError:
                continue               # kept for the next sweep
            del data[name]
            changed = True
            n += 1
        if changed:
            try:
                _save(path, data)
            except OSError:
                log.warning("input record %s could not be saved", path)
    if n:
        log.info("removed %d leftover input file(s) from %s", n, d)
    return n


def sweep_now(input_dir: Path | str | None, data_dir: Path | str | None = None) -> int:
    """sweep() keeping what the queued and running jobs need. data_dir defaults to this backend's library folder."""
    try:
        if data_dir is None:
            data_dir = get_settings().library_path
        return sweep(input_dir, active_ids(), data_dir)
    except Exception:  # noqa: BLE001
        log.exception("input folder clean-up failed")
        return 0
