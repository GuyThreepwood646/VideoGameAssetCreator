"""Export folder and file names: the name is cut to the longest length the library folder leaves room for (16 characters at
most, 4 at least), plus "_" and the id's first 8, so a long asset name cannot fail an export and a deep library gets
shorter names instead of a refusal. The full name stays in the manifest. Nothing here writes Blender or Unity files."""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from vgac import library
from vgac.stages import export

LONG = "N" * 200  # 200 characters; the cut is at most 16 (25 with "_" and the id's 8)
OWNER_LIBRARY = r"F:\Projects\VideoGameAssetCreator\library"  # 41 characters: the owner's real library folder
OWNER_LENGTH = len(OWNER_LIBRARY)
REL = f"projects/{'p' * 36}/assets/{'a' * 36}/export/{'v' * 36}"  # the export version folder, below the library


def _sc(name: str, asset_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(asset=SimpleNamespace(name=name, id=asset_id or str(uuid.uuid4())))


def _version_dir(root: str) -> Path:
    return Path(root) / REL.replace("/", "\\")


def _folder_of(length: int) -> str:
    return "X:\\" + "a" * (length - 3)


def test_owner_library_length_is_41():
    assert OWNER_LENGTH == 41  # measured: the owner library folder is 41 characters


def test_short_library_keeps_the_full_16_character_name():
    name = export.asset_export_name(_sc(LONG), folder_length=OWNER_LENGTH)
    assert name == "N" * 16 + "_" + name[-8:]
    assert len(name) == library.name_length(library.EXPORT_NAME_MAX) == 25


def test_two_long_names_with_the_same_start_never_collide_at_any_depth():
    for length in (OWNER_LENGTH, 60, library.max_library_folder_length()):
        a = _sc(LONG, str(uuid.uuid4()))
        b = _sc(LONG, str(uuid.uuid4()))
        assert export.asset_export_name(a, folder_length=length) != export.asset_export_name(b, folder_length=length)


def test_short_names_keep_their_old_export_name():
    sc = _sc("Painted dwarf", "ad7809ba-0000-0000-0000-000000000000")
    assert export.asset_export_name(sc, folder_length=OWNER_LENGTH) == "Painted_dwarf_ad7809ba"


def test_a_name_is_sized_to_the_room_the_folder_leaves():
    # a deeper folder gets a shorter cut, and the cut only ever gets shorter as the folder gets longer
    cuts = [len(export.asset_export_name(_sc(LONG, "a" * 36), folder_length=n).split("_")[0])
            for n in range(OWNER_LENGTH, library.max_library_folder_length() + 1)]
    assert cuts[0] == 16
    assert cuts == sorted(cuts, reverse=True)
    assert min(cuts) == library.MIN_NAME_CUT


def test_the_item_file_blender_reads_is_at_most_259_under_the_owner_library():
    # items/<8 characters of the item id>/<export name>.glb: the item file Blender opens (equip.py), at the full cut
    below = f"items/{'i' * 8}/{'n' * 25}.glb"
    assert len(below) == 44  # 6 + 8 + 1 + 25 + 4
    assert len(str(_version_dir(OWNER_LIBRARY))) + 1 + len(below) == 220  # 39 characters to spare under 259


def test_owner_library_passes_the_room_check_with_its_full_name():
    name = export.asset_export_name(_sc(LONG), folder_length=OWNER_LENGTH)
    export.check_export_room(_version_dir(OWNER_LIBRARY), name)


def test_deep_library_passes_the_room_check_with_its_sized_name():
    limit = library.max_library_folder_length()  # the folder limit: the shortest cut still fits
    name = export.asset_export_name(_sc(LONG), folder_length=limit)
    assert len(name) == library.name_length(library.MIN_NAME_CUT)
    export.check_export_room(_version_dir(_folder_of(limit)), name)


def test_room_check_refuses_in_plain_words_one_character_past_the_limit():
    limit = library.max_library_folder_length()
    name = "x" * library.name_length(library.MIN_NAME_CUT)
    with pytest.raises(library.LibraryPathError) as info:
        export.check_export_room(_version_dir(_folder_of(limit + 1)), name)
    assert "too long for Windows" in str(info.value)
    assert f"under {limit + 1} characters" in str(info.value)


def test_a_folder_too_deep_for_the_shortest_cut_is_refused_with_its_number():
    limit = library.max_library_folder_length()
    with pytest.raises(library.LibraryPathError) as info:
        export.asset_export_name(_sc(LONG), folder_length=limit + 1)
    assert f"under {limit + 1} characters" in str(info.value)


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
