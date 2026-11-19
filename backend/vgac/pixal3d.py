"""Whether the Pixal3D four-view builder can be offered in step 3 (graphs/pixal3d.py).

It is offered only when the three files its graph loads are in a folder the linked ComfyUI reads: the multi-view model
(diffusion_models), the DINOv3 encoder with NAF's weights (clip_vision) and MoGe-2 (geometry_estimation), in any
subfolder, as the engine lists them. They are not in setup/downloads.json yet, so the app never fetches them; a person
who has them gets the choice. The answer is kept for CACHE_SECONDS so the step list does not walk the folders each time.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .graphs.pixal3d import MOGE_MODEL, PIXAL3D_ENCODER, PIXAL3D_MODEL

# model type folder (as ComfyUI names it) -> the file the four-view graph loads from it
FILES: dict[str, str] = {"diffusion_models": PIXAL3D_MODEL, "clip_vision": PIXAL3D_ENCODER, "geometry_estimation": MOGE_MODEL}
CACHE_SECONDS = 60.0
_cache: dict[str, Any] = {"at": 0.0, "missing": None}


def _search(roots: dict[str, Any]) -> list[str]:
    from . import models_setup

    missing = []
    for typ, name in FILES.items():
        folders = [Path(b) / typ for b in roots["bases"]] + list(roots["typed"].get(typ, []))
        if typ in models_setup.ALT_FOLDERS:
            folders += [Path(b) / models_setup.ALT_FOLDERS[typ] for b in roots["bases"]]
        if not any(f.is_dir() and models_setup._listing(f).get(name.casefold()) for f in folders):
            missing.append(name)
    return missing


def missing_files(fresh: bool = False) -> list[str]:
    """The file names of FILES that no folder the engines read holds (all of them when no ComfyUI is linked)."""
    now = time.monotonic()
    if not fresh and _cache["missing"] is not None and now - _cache["at"] < CACHE_SECONDS:
        return list(_cache["missing"])
    from . import downloader, models_setup

    try:
        root = downloader.models_root()
        missing = _search(models_setup.read_roots(root)) if root is not None else list(FILES.values())
    except Exception:  # noqa: BLE001  (settings not readable: not offered)
        missing = list(FILES.values())
    _cache.update(at=now, missing=missing)
    return list(missing)


def available(fresh: bool = False) -> bool:
    return not missing_files(fresh)
