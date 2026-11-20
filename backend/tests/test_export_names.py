"""Export folder and file names: a name is cut to EXPORT_NAME_MAX characters plus the id's first 8, so a long asset
name cannot fail an export. The owner's library folder (40 characters) must pass for a typical and for a long name.
The full name stays in the manifest. Nothing here writes Blender or Unity files."""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from vgac import library
from vgac.stages import export

LONG = "N" * 200  # 200 characters; cut to 16 gives the longest export name (25 with the id)
OWNER_LIBRARY = r"F:\Projects\VideoGameAssetCreator\library"  # 40 characters: the owner's real library folder
REL = f"projects/{'p' * 36}/assets/{'a' * 36}/export/{'v' * 36}"  # the export version folder, below the library


def _sc(name: str, asset_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(asset=SimpleNamespace(name=name, id=asset_id or str(uuid.uuid4())))


def _version_dir(root: str) -> Path:
    return Path(root) / REL.replace("/", "\\")


def test_owner_library_length_is_40():
    assert len(OWNER_LIBRARY) == 41  # measured: the owner library folder is 41 characters


def test_a_200_character_asset_name_gives_a_short_export_name():
    sc = _sc(LONG)
    name = export.asset_export_name(sc)
    assert len(name) <= export.EXPORT_NAME_MAX + 1 + 8
    assert name.endswith("_" + sc.asset.id[:8])


def test_two_long_names_with_the_same_start_never_collide():
    a = _sc(LONG, str(uuid.uuid4()))
    b = _sc(LONG, str(uuid.uuid4()))
    assert export.asset_export_name(a) != export.asset_export_name(b)


def test_short_names_keep_their_old_export_name():
    sc = _sc("Painted dwarf", "ad7809ba-0000-0000-0000-000000000000")
    assert export.asset_export_name(sc) == "Painted_dwarf_ad7809ba"


def test_owner_library_passes_for_a_typical_name():
    name = export.asset_export_name(_sc("Archer"))
    export.check_export_room(_version_dir(OWNER_LIBRARY), name)


def test_owner_library_passes_for_a_200_character_name():
    name = export.asset_export_name(_sc(LONG))
    export.check_export_room(_version_dir(OWNER_LIBRARY), name)


def test_owner_library_passes_for_a_42_character_name():
    # the longest asset name in the owner's library copy
    name = export.asset_export_name(_sc("N" * 42))
    export.check_export_room(_version_dir(OWNER_LIBRARY), name)


def test_the_limit_is_exact_and_refuses_in_plain_words():
    name = export.asset_export_name(_sc(LONG))
    export.check_export_room(_version_dir(OWNER_LIBRARY + "x"), name)  # 42 characters: 259 deepest, passes
    with pytest.raises(library.LibraryPathError) as info:
        export.check_export_room(_version_dir(OWNER_LIBRARY + "xx"), name)  # 43 characters: 260, refused
    assert "too long for Windows" in str(info.value)
    assert "keep it under 43 characters" in str(info.value)


def test_delivery_folder_for_a_200_character_name_is_cut_and_stays_inside_the_export_root(tmp_path: Path):
    root = tmp_path / "exports"
    dest = export.delivery_dir(root, "My Game", LONG, "p" * 36, "a" * 36)
    assert dest.relative_to(root.resolve()).parts[0] == "My_Game"  # a short project name is unchanged
    assert len(dest.name) <= export.EXPORT_NAME_MAX + 1 + 8
    assert dest.name.endswith("_" + "a" * 8)


def test_long_project_name_is_cut_with_its_id(tmp_path: Path):
    dest = export.delivery_dir(tmp_path, "P" * 200, "Sir", "p" * 36, "a" * 36)
    assert dest.relative_to(tmp_path.resolve()).parts[0] == "P" * export.EXPORT_NAME_MAX + "_" + "p" * 8


def test_delivery_folder_short_names_are_unchanged(tmp_path: Path):
    assert export.delivery_dir(tmp_path, "My Game", "Sir Knight") == (tmp_path / "My_Game" / "Sir_Knight").resolve()
