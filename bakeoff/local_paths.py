"""This machine's paths for the bake-off scripts: environment variables first, then the app's settings
(backend/config.yaml with local/machine.json laid over it, backend/vgac/config.py). Run with backend/.venv's Python.

    VGAC_BLENDER       blender.exe            (settings: blender_exe)
    VGAC_COMFY_INPUT   ComfyUI input folder   (settings: comfy_input_dir)
    VGAC_COMFY_B_OUT   engine B output folder (settings: instances.b.output_dir)
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _settings():
    backend = os.path.join(HERE, "..", "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    try:
        from vgac.config import load_settings

        return load_settings(create_library=False)
    except Exception as err:  # noqa: BLE001  (a bake-off script must still start to print its own usage)
        print(f"bake-off: the app's settings could not be read ({err})", file=sys.stderr)
        return None


def _get(env: str, pick) -> str | None:
    if os.environ.get(env):
        return os.environ[env]
    s = _settings()
    try:
        value = pick(s) if s is not None else None
    except (KeyError, AttributeError):
        value = None
    return str(value) if value is not None else None


def blender() -> str | None:
    return _get("VGAC_BLENDER", lambda s: s.blender_exe)


def comfy_input() -> str | None:
    return _get("VGAC_COMFY_INPUT", lambda s: s.comfy_input_dir)


def comfy_b_output() -> str | None:
    return _get("VGAC_COMFY_B_OUT", lambda s: s.instances["b"].output_dir)
