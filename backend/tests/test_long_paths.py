"""Windows' 260-character path limit: run_blender refuses a path Blender cannot open before it starts, and the library
folder setting warns (never refuses) when the folder path is long. Nothing here starts Blender."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from vgac import setup_settings, tools
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


def test_library_warning_only_above_120_characters():
    assert setup_settings.long_library_warning("C:\\VGAC\\library") is None
    assert setup_settings.long_library_warning(_path_of_length(120)) is None
    warning = setup_settings.long_library_warning(_path_of_length(121))
    assert warning is not None and "121 characters" in warning
    assert "refuse" not in warning.lower()
