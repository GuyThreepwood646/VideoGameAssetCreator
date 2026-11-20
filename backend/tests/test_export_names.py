"""Export folder and file names with a long asset name: cut to EXPORT_NAME_MAX with the id, so the export never fails
for the name alone. The full name stays in the manifest. Nothing here writes Blender or Unity files."""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from vgac import library
from vgac.stages import export

LONG = "Sir " * 50  # 200 characters, becomes 'Sir_Sir_...' after slugging


def _sc(name: str, asset_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(asset=SimpleNamespace(name=name, id=asset_id or str(uuid.uuid4())))


def test_a_200_character_asset_name_gives_a_short_export_name():
    sc = _sc(LONG[:200])
    assert len(LONG[:200]) == 200
    name = export.asset_export_name(sc)
    assert len(name) <= export.EXPORT_NAME_MAX + 1 + 8
    assert name.endswith("_" + sc.asset.id[:8])


def test_two_long_names_with_the_same_start_never_collide():
    a = _sc(LONG, str(uuid.uuid4()))
    b = _sc(LONG, str(uuid.uuid4()))
    assert export.asset_export_name(a) != export.asset_export_name(b)


def test_short_names_keep_their_old_export_name():
    sc = _sc("Painted dwarf helmet", "ad7809ba-0000-0000-0000-000000000000")
    assert export.asset_export_name(sc) == "Painted_dwarf_helmet_ad7809ba"


def test_delivery_folder_for_a_200_character_name_is_cut_and_stays_inside_the_export_root(tmp_path: Path):
    root = tmp_path / "exports"
    dest = export.delivery_dir(root, "My Game", LONG, "p" * 36, "a" * 36)
    assert dest.relative_to(root.resolve()).parts[0] == "My_Game"  # a short project name is unchanged
    assert len(dest.name) <= export.EXPORT_NAME_MAX + 1 + 8
    assert dest.name.endswith("_" + "a" * 8)


def test_long_project_name_is_cut_with_its_id(tmp_path: Path):
    dest = export.delivery_dir(tmp_path, "P" * 200, "Sir", "p" * 36, "a" * 36)
    assert dest.relative_to(tmp_path.resolve()).parts[0] == "P" * 24 + "_" + "p" * 8


def test_delivery_folder_short_names_are_unchanged(tmp_path: Path):
    assert export.delivery_dir(tmp_path, "My Game", "Sir Knight 2") == (tmp_path / "My_Game" / "Sir_Knight_2").resolve()


def test_export_room_refuses_a_too_deep_library_before_writing():
    # A library folder that puts the 200-character name's deepest export file at 260 characters or more.
    name = export.asset_export_name(_sc(LONG))
    below = f"items/{name}/T_{name}_MetallicSmoothness.png"
    rel = f"projects/{'x' * 36}/assets/{'x' * 36}/export/{'x' * 36}"
    root_len = library.LONG_PATH_LIMIT - 1 - len(rel) - 1 - len(below)  # one character too long
    root = Path("C:/" + "r" * (root_len - 3))
    assert len(str(root)) >= 3
    with pytest.raises(library.LibraryPathError) as info:
        export.check_export_room(root / rel, name)
    assert "too long for Windows" in str(info.value)
    assert "export files" in str(info.value)


def test_export_room_passes_a_short_library_for_a_200_character_name():
    name = export.asset_export_name(_sc(LONG))
    export.check_export_room(Path("C:/VGAC/lib") / f"projects/{'x' * 36}/assets/{'x' * 36}/export/{'x' * 36}", name)
