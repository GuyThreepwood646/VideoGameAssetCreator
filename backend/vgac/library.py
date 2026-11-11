"""Library path layout and file registration.

Layout: library/projects/<project_id>/assets/<asset_id>/<stage>/<version_id>/<files>
Every path stored on a Version is relative to the library root with forward slashes, so the
frontend can fetch it from GET /api/library/{path}.
"""

from __future__ import annotations

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
        d.mkdir(parents=True, exist_ok=True)
    return d


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
