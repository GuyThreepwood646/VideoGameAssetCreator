"""Windows' 260-character path limit: run_blender refuses a path Blender cannot open before it starts, and the library
folder setting warns (never refuses) when the folder path is long. Nothing here starts Blender."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from vgac import library, setup_settings, tools
from vgac.tools import ToolError

FAKE_BLENDER = SimpleNamespace(blender_exe=Path("C:/Blender/blender.exe"))


def _path_of_length(n: int) -> str:
    """An absolute Windows path of exactly n characters."""
    head = "C:\\VGAC\\"
    return head + "a" * (n - len(head))


@pytest.fixture
def no_blender(monkeypatch: pytest.MonkeyPatch):
    """Mock everything below run_blender so a call that gets past the check would be seen, not run."""
    calls: list[list[str]] = []
    monkeypatch.setattr(tools, "blender_cmd", lambda settings, script, args: ["blender", *map(str, args)])

    def fake_run_process(cmd, **kwargs):
        calls.append(cmd)
        return ""

    monkeypatch.setattr(tools, "run_process", fake_run_process)
    return calls


def test_path_of_exactly_260_characters_is_refused_before_blender_starts(no_blender, tmp_path: Path):
    long_path = _path_of_length(260)
    assert len(long_path) == 260
    with pytest.raises(ToolError) as info:
        tools.run_blender(FAKE_BLENDER, "prep.py", ["--in", long_path, "--out", str(tmp_path)])
    msg = str(info.value)
    assert "260 characters" in msg
    assert "the limit is 259" in msg
    assert "C:\\VGAC\\library" in msg
    assert no_blender == []  # Blender was never started


def test_path_object_of_260_characters_is_refused(no_blender):
    with pytest.raises(ToolError, match="260 characters"):
        tools.run_blender(FAKE_BLENDER, "export.py", [Path(_path_of_length(260))])
    assert no_blender == []


def test_path_of_259_characters_passes_through(no_blender):
    short_enough = _path_of_length(259)
    assert len(short_enough) == 259
    out = tools.run_blender(FAKE_BLENDER, "prep.py", ["--in", short_enough])
    assert out == ""
    assert len(no_blender) == 1
    assert short_enough in no_blender[0]


def test_non_path_arguments_are_ignored(no_blender):
    long_text = "x" * 400  # not a path: no drive, no UNC share
    args = ["--hair-kind", "shell", "--scale", "1.5", long_text, "--flag", "relative\\" + "b" * 300]
    tools.run_blender(FAKE_BLENDER, "rig.py", args)
    assert len(no_blender) == 1


def test_unc_path_is_checked(no_blender):
    unc = "\\\\server\\share\\" + "d" * (260 - len("\\\\server\\share\\"))
    with pytest.raises(ToolError, match="260 characters"):
        tools.check_blender_paths([unc])


def test_deepest_fixed_file_length_is_the_held_item_glb_under_export():
    # projects/<36>/assets/<36>/export/<36>/items/<8>/<name>.glb with the longest export name (25 characters), relative
    # to the library folder. The export LOD (173), the unreal FBX (170) and the clean-up face picture (169) are shallower.
    assert library.DEEPEST_FIXED_LENGTH == len("projects/") + 36 + len("/assets/") + 36 + len("/export/") + 36 + len("/items/") + 8 + 1 + 25 + len(".glb")
    assert library.DEEPEST_FIXED_LENGTH == 178
    assert library.max_library_folder_length() == 80


def test_deepest_real_path_at_the_folder_limit_stays_under_260():
    # The deepest file the app writes under a library folder, built from the real export name (the longest: 200 characters
    # cut to 16, plus the id's 8) and 36-character ids: the held item GLB Blender opens, under export/<id>/items/<id8>/.
    from vgac.stages import export
    name = export.asset_export_name(SimpleNamespace(asset=SimpleNamespace(name="N" * 200, id=str(uuid.uuid4()))))
    rel = f"projects/{'p' * 36}/assets/{'a' * 36}/export/{'v' * 36}/items/{uuid.uuid4().hex[:8]}/{name}.glb"
    n = library.max_library_folder_length()
    folder = "X:\\" + "a" * (n - 3)
    assert len(folder) == n
    deepest = folder + "\\" + rel.replace("/", "\\")
    assert len(deepest) < library.LONG_PATH_LIMIT  # Blender refuses 260 characters or more


def test_library_folder_at_the_limit_passes_one_more_is_refused():
    n = library.max_library_folder_length()
    assert library.library_path_problem("X:\\" + "a" * (n - 3)) is None
    message = library.library_path_problem("X:\\" + "a" * (n - 2))
    assert message is not None
    assert f"under {n + 1} characters" in message
    assert "for example F:\\Games\\VGAC library:" in message
    assert "too long for Windows" in message


def test_folder_limit_is_the_export_check_boundary_for_the_longest_name():
    # The export check (stages/export.py) refuses the held item GLB at the same folder length the setting refuses.
    from vgac.stages import export
    name = export.asset_export_name(SimpleNamespace(asset=SimpleNamespace(name="N" * 200, id=str(uuid.uuid4()))))
    n = library.max_library_folder_length()
    rel = Path(*f"projects/{'p' * 36}/assets/{'a' * 36}/export/{'v' * 36}".split("/"))
    folder = Path("X:\\" + "a" * (n - 3))
    export.check_export_room(folder / rel)  # at the limit: passes
    with pytest.raises(library.LibraryPathError):
        export.check_export_room(Path("X:\\" + "a" * (n - 2)) / rel)  # one more: refused


def test_plain_example_folder_passes_the_library_check():
    assert library.library_path_problem("F:\\Games\\VGAC library") is None
    assert library.library_path_problem(None) is None  # no value: nothing to check


def test_setting_refuses_a_long_library_folder_in_plain_words(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # A real folder, but the limit is set so low that it is too long: the setting is refused, nothing is written.
    monkeypatch.setattr(library, "max_library_folder_length", lambda: len(str(tmp_path)) - 1)
    with pytest.raises(setup_settings.SettingError) as info:
        setup_settings.v_library(str(tmp_path), SimpleNamespace())
    assert "too long for Windows" in str(info.value)
    assert f"under {len(str(tmp_path))} characters" in str(info.value)


def test_setting_accepts_a_library_folder_within_the_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(library, "max_library_folder_length", lambda: len(str(tmp_path)))
    assert setup_settings.v_library(str(tmp_path), SimpleNamespace()) == str(tmp_path)


def test_job_refuses_to_create_a_version_folder_that_would_hold_a_too_long_file(tmp_path: Path):
    # A library root that puts the version folder's deepest fixed file at 260 characters or more.
    asset = SimpleNamespace(project_id="p", id="a")
    version = SimpleNamespace(stage="prep", id="v")
    suffix = len(os.path.abspath(str(Path("C:/x") / "projects/p/assets/a/prep/v"))) - len(os.path.abspath("C:/x"))
    # The version folder is 222 characters; the deepest fixed file below it adds a separator and 37 more: 260.
    target = 222 - suffix  # root length
    root = Path("C:/VGAC") / ("r" * (target - len("C:\\VGAC") - 1))
    assert len(os.path.abspath(str(root / "projects/p/assets/a/prep/v"))) == 222
    with pytest.raises(library.LibraryPathError) as info:
        library.version_dir(asset, version, settings=SimpleNamespace(library_path=root))
    assert "too long for Windows" in str(info.value)
    assert not (root / "projects").exists()  # refused before any folder was made


def test_job_creates_a_version_folder_under_a_short_library(tmp_path: Path):
    asset = SimpleNamespace(project_id="p", id="a")
    version = SimpleNamespace(stage="prep", id="v")
    d = library.version_dir(asset, version, settings=SimpleNamespace(library_path=tmp_path))
    assert d.is_dir()
