"""Blender script errors are reported (F14 follow-up): Blender exits 0 when its --python script raises, unless it is
started with --python-exit-code. tools.run_blender then raised nothing, and each caller found only missing files.

The Blender tests run tiny scripts through the app's own tools.run_blender; skipped without Blender.
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.machine_tools import blender as machine_blender
from vgac import tools

BLENDER = machine_blender() or Path("blender-not-found")
needs_blender = pytest.mark.skipif(not BLENDER.is_file(), reason="needs Blender (VGAC_TEST_BLENDER)")

RAISES = 'import sys\nprint("WORKING")\nraise ValueError("planted failure 42")\n'
FINE = 'print("SCRIPT_FINE")\n'


def _run(tmp_path, monkeypatch, body: str, name: str):
    scripts = tmp_path / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / name).write_text(body, encoding="utf-8")
    monkeypatch.setattr(tools, "BLENDER_SCRIPTS_DIR", scripts)
    monkeypatch.setenv("OMP_NUM_THREADS", "2")
    settings = SimpleNamespace(blender_exe=BLENDER)
    return tools.run_blender(settings, name, [], log_path=tmp_path / f"{name}.log", timeout=300)


@needs_blender
def test_a_blender_script_that_raises_is_an_error_with_the_reason_first(tmp_path, monkeypatch):
    with pytest.raises(tools.ToolError) as ei:
        _run(tmp_path, monkeypatch, RAISES, "raises.py")
    msg = str(ei.value)
    # the reason leads the message, so a caller that keeps its first 300 characters still has it
    assert msg.startswith("raises.py stopped on an error:")
    assert "ValueError: planted failure 42" in msg[:300]
    assert "WORKING" in (tmp_path / "raises.py.log").read_text(encoding="utf-8")


@needs_blender
def test_a_blender_script_that_finishes_still_returns_its_output(tmp_path, monkeypatch):
    assert "SCRIPT_FINE" in _run(tmp_path, monkeypatch, FINE, "fine.py")


def test_the_traceback_tail_skips_blender_lines():
    text = "import lines\nTraceback (most recent call last):\n  File \"x.py\", line 3, in <module>\n    boom()\nValueError: bad\n" \
           "Blender 5.2.3 LTS (hash x)\n\nBlender quit\n"
    assert tools.traceback_tail(text) == 'File "x.py", line 3, in <module> | boom() | ValueError: bad'
    assert tools.traceback_tail("no error here") == ""


def test_the_blender_command_asks_for_an_exit_code_on_a_script_error(tmp_path, monkeypatch):
    exe = tmp_path / "blender.exe"
    exe.write_bytes(b"")
    (tmp_path / "s.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(tools, "BLENDER_SCRIPTS_DIR", tmp_path)
    monkeypatch.setattr("vgac.blender_watch.check", lambda _exe: None)
    cmd = tools.blender_cmd(SimpleNamespace(blender_exe=exe), "s.py", ["a"])
    i = cmd.index("--python-exit-code")
    assert cmd[i + 1] == "1" and i < cmd.index("--python")  # Blender reads it only before --python
    assert os.path.basename(cmd[0]) == "blender.exe"
