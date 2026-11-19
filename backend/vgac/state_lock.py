"""One named lock for the shared files in local/ (machine.json and installed.json).

The starter (VGAC.exe, starter/src/Core.cs `StateLock`) and the backend both rewrite those files. Each read-modify-write
must hold this lock so that neither overwrites the other's change. The lock is a file, `local/state.lock`, created
exclusively (O_CREAT | O_EXCL) by whichever process gets there first; the starter takes it the same way, so the two
interoperate. A lock held by a process that died is broken after STALE_SECONDS (a save takes milliseconds).

Temporary files for these writes get a unique name (unique_tmp), so two writers never share one `.tmp` path.
"""

from __future__ import annotations

import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

LOCK_NAME = "state.lock"
STALE_SECONDS = 60.0
WAIT_SECONDS = 10.0


class StateLockBusy(RuntimeError):
    """Another VGAC process kept the lock for the whole wait."""


def unique_tmp(path: Path) -> Path:
    """A temporary file name beside `path` that no other writer uses (never the fixed `name.tmp`)."""
    return path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")


@contextmanager
def state_lock(local_dir: Path | str, wait_seconds: float = WAIT_SECONDS) -> Iterator[None]:
    """Hold the shared-state lock in `local_dir` for the duration of the block."""
    local = Path(local_dir)
    local.mkdir(parents=True, exist_ok=True)
    lock = local / LOCK_NAME
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except (FileExistsError, PermissionError):   # Windows can refuse the open while another process holds the file
            if _is_stale(lock):
                try:
                    os.remove(lock)
                except OSError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise StateLockBusy(
                    "Another VGAC window or process is saving its settings. Wait a moment and try again.")
            time.sleep(0.05)
    try:
        os.write(fd, f"{os.getpid()} {time.time():.3f}\n".encode("ascii"))
        os.close(fd)
        yield
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


def _is_stale(lock: Path) -> bool:
    try:
        return time.time() - lock.stat().st_mtime > STALE_SECONDS
    except OSError:
        return False
