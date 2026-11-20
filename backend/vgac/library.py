"""Library path layout and file registration.

Layout: library/projects/<project_id>/assets/<asset_id>/<stage>/<version_id>/<files>
Every path stored on a Version is relative to the library root with forward slashes, so the
frontend can fetch it from GET /api/library/{path}.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from .config import Settings, get_settings
from .db import Asset, Version


def library_root(settings: Settings | None = None) -> Path:
    return (settings or get_settings()).library_path.resolve()


def project_dir(project_id: str, settings: Settings | None = None) -> Path:
    return library_root(settings) / "projects" / project_id


def asset_dir(project_id: str, asset_id: str, settings: Settings | None = None) -> Path:
    return project_dir(project_id, settings) / "assets" / asset_id


def own_face_closeup(project_id: str, asset_id: str, settings: Settings | None = None) -> Path:
    """Where the person's own close-up of the character's face is kept (own_images: POST /api/assets/{aid}/face-closeup).
    The clean-up step's face pass paints from it instead of drawing a close-up (stages/face_detail.py)."""
    return asset_dir(project_id, asset_id, settings) / "own" / "face_closeup.png"


def version_dir(asset: Asset, version: Version, settings: Settings | None = None, create: bool = True) -> Path:
    d = asset_dir(asset.project_id, asset.id, settings) / version.stage / version.id
    if create:
        check_version_room(d)
        d.mkdir(parents=True, exist_ok=True)
    return d


# Windows' 260-character limit (MAX_PATH) applies to every file the app writes, and a library folder path is the
# start of each of them. The deepest fixed-layout file the app writes is a clean-up face picture, with 36-character ids
# (project, asset, version): projects/<id>/assets/<id>/prep/<id>/face/painted_head_closeup_2_drawn.png. Asset names
# also go into export folder names and are not checked here (see the note in ARCHITECTURE).
LONG_PATH_LIMIT = 260
_DEEPEST_FIXED = "projects/{p}/assets/{a}/prep/{v}/face/painted_head_closeup_2_drawn.png"
DEEPEST_FIXED_LENGTH = len(_DEEPEST_FIXED.format(p="x" * 36, a="x" * 36, v="x" * 36))
_BELOW_VERSION = "face/painted_head_closeup_2_drawn.png"  # the deepest fixed file, below a version folder


class LibraryPathError(ValueError):
    """The library folder path is too long for Windows to write the files inside it. The message is plain."""


def max_library_folder_length() -> int:
    """The longest library folder path (characters) that still lets the deepest fixed file fit under 260 characters:
    folder + one separator + the file's relative path must stay at 259 characters or fewer."""
    return LONG_PATH_LIMIT - 2 - DEEPEST_FIXED_LENGTH


def library_path_problem(folder: Any) -> str | None:
    """A plain sentence when the library folder path is too long for Windows, else None. Used when the setting is saved."""
    if not isinstance(folder, (str, Path)) or not str(folder).strip():
        return None
    n = len(os.path.abspath(str(folder)))
    if n <= max_library_folder_length():
        return None
    return library_path_message()


def library_path_message() -> str:
    return (f"The library folder's path is too long for Windows: keep it under {max_library_folder_length() + 1} characters, "
            "for example F:\\Games\\VGAC library.")


def check_version_room(d: Path) -> None:
    """Before a job writes into a version folder: refuse, in plain words, when the deepest fixed file below it would
    be 260 characters or more. Cheap (one length), and it stops the job before any file is written."""
    if len(os.path.abspath(str(d))) + 1 + len(_BELOW_VERSION) >= LONG_PATH_LIMIT:
        raise LibraryPathError(library_path_message())


def rel_path(path: Path | str, settings: Settings | None = None) -> str:
    """Library-relative path with forward slashes. Raises if the file is outside the library."""
    p = Path(path).resolve()
    root = library_root(settings)
    return p.relative_to(root).as_posix()


def abs_path(rel: str, settings: Settings | None = None) -> Path:
    root = library_root(settings)
    target = (root / rel).resolve()
    target.relative_to(root)
    return target


def register_file(version: Version, key: str, path: Path | str, settings: Settings | None = None) -> str:
    """Record a file on the version under `key`. Returns the library-relative path."""
    rel = rel_path(path, settings)
    files = version.files
    files[key] = rel
    version.files = files
    return rel


def register_files(version: Version, mapping: dict[str, Path | str], settings: Settings | None = None) -> dict[str, str]:
    out = {}
    for key, path in mapping.items():
        out[key] = register_file(version, key, path, settings)
    return out


def version_file(version: Version, key: str, settings: Settings | None = None) -> Path | None:
    rel = version.files.get(key)
    if not rel:
        return None
    p = abs_path(rel, settings)
    return p if p.is_file() else None


def update_metrics(version: Version, **values: Any) -> dict[str, Any]:
    m = version.metrics
    m.update(values)
    version.metrics = m
    return m


def import_file(version_folder: Path, src: Path | str, name: str | None = None) -> Path:
    """Copy an external file into a version folder and return the new path."""
    src_p = Path(src)
    dest = version_folder / (name or src_p.name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_p, dest)
    return dest


def remove_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
