"""The minimum versions (data/minimum_versions.json, vgac/minimums.py): at the floor passes, one below fails, a much newer
one passes, an unreadable one is "unknown" (a warning in the app, never a block). The PowerShell setup check reads the same file."""

from __future__ import annotations

import json
import re

import pytest

from vgac import minimums, preflight

# key -> (at the floor, just below, much newer, unreadable)
CASES = {
    "python": ("Python 3.13.0", "Python 3.12.9", "Python 3.19.2", "Python"),
    "node": ("v22.12.0", "v22.11.9", "v40.0.0", "nonsense"),
    "blender": ("Blender 5.0.0", "Blender 4.5.9", "Blender 9.1.0", "Blender"),
    "unity": ("6000.0.0f1", "2022.3.60f1", "7000.2.1f1", ""),
    "comfyui": ("0.34.0", "0.33.9", "3.0.0", ""),
}


def test_the_file_names_every_dependency_with_a_kind():
    data = json.loads(minimums.FILE.read_text(encoding="utf-8"))
    for key in CASES:
        e = data[key]
        assert re.fullmatch(r"\d+(\.\d+)*", e["min"]) and e["tested"] and e["name"]
        assert e["kind"] in ("required by", "oldest evidence")
        assert minimums.check(key, e["tested"]) == "ok"  # the tested version is never below the minimum


@pytest.mark.parametrize("key", list(CASES))
def test_each_floor(key):
    at, below, newer, unreadable = CASES[key]
    assert minimums.check(key, at) == "ok"
    assert minimums.check(key, below) == "old"
    assert minimums.check(key, newer) == "ok"
    assert minimums.check(key, unreadable) == "unknown"
    assert minimums.check(key, None) == "unknown"


def test_the_wording_is_x_or_newer_tested_with_y():
    assert minimums.phrase("blender") == "Blender 5.0 or newer (tested with 5.2.3)"
    assert minimums.phrase("python") == "Python 3.13 or newer (tested with 3.13.7)"


def test_the_app_reads_its_blender_floor_from_the_file():
    assert preflight.BLENDER_MIN == minimums.floor("blender")[:2] == (5, 0)


def test_unity_version_comes_from_the_folder_name():
    exe = r"C:\Program Files\Unity\Hub\Editor\6000.4.3f1\Editor\Unity.exe"
    assert preflight.unity_version_of(exe) == "6000.4.3f1"
    assert preflight.unity_version_of(r"D:\Tools\Unity\Unity.exe") == ""


def _unity_item(tmp_path, version):
    from types import SimpleNamespace

    folder = tmp_path / version if version else tmp_path / "Unity"
    exe = folder / "Editor" / "Unity.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"x")
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    s = SimpleNamespace(unity_exe=str(exe), unity_project=str(proj))
    return preflight.check_unity(s)[0]


@pytest.mark.parametrize("version,state", [("6000.0.0f1", "ok"), ("2022.3.60f1", "warn"), ("6000.4.3f1", "ok"), ("7000.1.0f1", "ok"), ("", "warn")])
def test_unity_row(tmp_path, version, state):
    assert _unity_item(tmp_path, version)["state"] == state


@pytest.mark.parametrize("ver,state", [("0.34.0", "ok"), ("0.33.9", "fail"), ("5.0.0", "ok"), ("", "warn"), (None, "warn")])
def test_comfyui_version_row(ver, state):
    class Client:
        def last_vram_summary(self):
            return {"comfyui_version": ver}

    it = preflight._comfy_version_item(None, "b", Client(), None, ["concept"], "RTX 3080")
    assert it["state"] == state
    if state != "ok":
        assert "0.34.0 or newer (tested with 0.45.3)" in it["detail"] or "0.34.0 or newer (tested with 0.45.3)" in it["fix"] or state == "fail"


def test_the_setup_script_applies_the_same_floors():
    """setup/Check-Setup.ps1 reads the same file; run its own version functions on the same cases."""
    import shutil
    import subprocess
    from pathlib import Path

    ps = shutil.which("powershell")
    if ps is None:
        pytest.skip("Windows PowerShell is not available")
    here = Path(__file__).resolve()
    root = here.parents[2]
    out = subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(here.with_name("ps_floor_check.ps1")),
                          "-Script", str(root / "setup" / "Check-Setup.ps1"), "-Root", str(root)],
                         capture_output=True, text=True, timeout=60, check=True).stdout.splitlines()
    got = {}
    for line in out:
        parts = line.split()
        got.setdefault(parts[0], []).append(parts[-1])
    for key, (at, below, newer, unreadable) in CASES.items():
        assert got[key] == [minimums.check(key, v) for v in (at, below, newer, unreadable)] == ["ok", "old", "ok", "unknown"], key
