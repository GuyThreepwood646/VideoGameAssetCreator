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


# Blender cannot open a file whose path is 260 characters or more (measured on this machine: 259 opens, 260 does not;
# its Python is not long-path aware). The backend's own Python and ComfyUI's Python open 271 characters fine. Windows
# long-path support is not reliable across Blender, Unity and ComfyUI, so the 260 check stays for every path the app writes.
#
# The room under the library folder is shared by the files whose names carry the asset's export name. The export name is
# the asset name cut to a length (EXPORT_NAME_MAX at most, at least MIN_NAME_CUT) plus "_" and the first 8 characters of
# the asset id, so ids stay unique. A folder with room keeps the full cut; a deeper folder gets a shorter cut; only a
# folder too deep for the minimum cut is refused. The paths below are the ones the app writes with the export name; the
# fixed clean-up face picture is the one path with no name in it.
LONG_PATH_LIMIT = 260
EXPORT_NAME_MAX = 16  # stages/export.py: the asset name's cut at most
MIN_NAME_CUT = 4  # the shortest cut a deep library gets before its folder is refused
ID_CHARS = 8  # the id characters in every export name, and the item folder items/<id8>/ (items.py equip)
_ID36 = "x" * 36
NAMED_TEMPLATES = (  # paths under a version folder that carry the export name {n}; {i} is the item folder
    "projects/{p}/assets/{a}/export/{v}/{n}.glb",
    "projects/{p}/assets/{a}/export/{v}/{n}.fbx",
    "projects/{p}/assets/{a}/export/{v}/{n}_godot.glb",
    "projects/{p}/assets/{a}/export/{v}/{n}_unreal.fbx",  # blender/pack.py
    "projects/{p}/assets/{a}/export/{v}/lods/{n}_LOD0.glb",  # blender/pack.py
    "projects/{p}/assets/{a}/export/{v}/T_{n}_MetallicSmoothness.png",
    "projects/{p}/assets/{a}/export/{v}/T_{n}_ORM.png",
    "projects/{p}/assets/{a}/export/{v}/T_{n}_Hair_MetallicSmoothness.png",
    "projects/{p}/assets/{a}/export/{v}/T_{n}_Hair_ORM.png",
    "projects/{p}/assets/{a}/export/{v}/items/{i}/{n}.glb",  # a held item's GLB, read by blender/equip.py
    "projects/{p}/assets/{a}/export/{v}/items/{i}/{n}.fbx",
    "projects/{p}/assets/{a}/export/{v}/items/{i}/{n}_godot.glb",
    "projects/{p}/assets/{a}/export/{v}/items/{i}/T_{n}_MetallicSmoothness.png",
    "projects/{p}/assets/{a}/export/{v}/items/{i}/T_{n}_ORM.png",
)
_FIXED = ("projects/{p}/assets/{a}/prep/{v}/face/painted_head_closeup_2_drawn.png",)  # the clean-up face picture (rebake)
_BELOW_VERSION = "face/painted_head_closeup_2_drawn.png"  # the clean-up face picture, below a version folder (guard only)


def name_length(cut: int) -> int:
    """Characters in an export name made with a cut of this length: the cut, "_", and the id's first ID_CHARS."""
    return cut + 1 + ID_CHARS


def _length(template: str, name: str) -> int:
    return len(template.format(p=_ID36, a=_ID36, v=_ID36, i="x" * ID_CHARS, n=name))


def cut_for_library(folder_length: int) -> int | None:
    """The longest cut of the asset name (EXPORT_NAME_MAX down to MIN_NAME_CUT) for which every path the app writes under
    a library folder of this many characters is 259 characters or fewer. None when even MIN_NAME_CUT does not fit."""
    room = LONG_PATH_LIMIT - 1 - folder_length - 1  # below the folder: the separator and the path must fit in 259
    for cut in range(EXPORT_NAME_MAX, MIN_NAME_CUT - 1, -1):
        if all(_length(t, "x" * name_length(cut)) <= room for t in NAMED_TEMPLATES) and all(_length(t, "") <= room for t in _FIXED):
            return cut
    return None


class LibraryPathError(ValueError):
    """The library folder path is too long for Windows to write the files inside it. The message is plain."""


def max_library_folder_length() -> int:
    """The longest library folder path (characters) for which even the minimum export name fits: every path the app
    writes under it stays at 259 characters or fewer. A folder up to this long is never refused; it may get shorter names."""
    return LONG_PATH_LIMIT - 2 - max(_length(t, "x" * name_length(MIN_NAME_CUT)) for t in NAMED_TEMPLATES + _FIXED)


def library_path_problem(folder: Any) -> str | None:
    """A plain sentence when the library folder path is too long even for the minimum export name, else None.
    Used when the setting is saved."""
    if not isinstance(folder, (str, Path)) or not str(folder).strip():
        return None
    n = len(os.path.abspath(str(folder)))
    if n <= max_library_folder_length():
        return None
    return library_path_message()


def library_path_message() -> str:
    return (f"Move the library folder to a shorter path, under {max_library_folder_length() + 1} characters, for example F:\\Games\\VGAC library: "
            "the path is too long for Windows to write the files inside it, even with the shortest export names.")


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
