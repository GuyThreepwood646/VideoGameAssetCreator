"""Tools on this machine for the tests that run them for real (Blender). Not a test module.

Blender: VGAC_TEST_BLENDER, then VGAC_BLENDER, then blender_exe in this machine's settings (backend/config.yaml with
local/machine.json laid over it, read directly so a test's VGAC_CONFIG does not hide it), then `blender` on PATH.
None when none of those is an existing file; the tests that need it skip with a reason.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def blender() -> Path | None:
    for env in ("VGAC_TEST_BLENDER", "VGAC_BLENDER"):
        if os.environ.get(env) and Path(os.environ[env]).is_file():
            return Path(os.environ[env])
    try:
        from vgac import config

        exe = config.load_settings(config.DEFAULT_CONFIG_PATH, machine=config.MACHINE_PATH,
                                   create_library=False).blender_exe
    except Exception:  # noqa: BLE001  (no settings here: fall through to PATH)
        exe = None
    if exe is not None and Path(exe).is_file():
        return Path(exe)
    found = shutil.which("blender")
    return Path(found) if found else None
