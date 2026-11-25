"""setup/Set-SkinTokenBlender.ps1: sets SKINTOKEN_BLENDER_BIN to the Blender that setup found.

The tests use -Target Process, so the person's own Windows environment is never changed.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "setup" / "Set-SkinTokenBlender.ps1"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")

pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="needs PowerShell")


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT), *args],
        capture_output=True, text=True, timeout=120,
    )


def test_default_target_is_the_user_environment():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "[ValidateSet('User', 'Process')][string]$Target = 'User'" in text
    assert "SKINTOKEN_BLENDER_BIN" in text


def test_missing_blender_is_refused(tmp_path):
    r = _run("-Exe", str(tmp_path / "missing.exe"), "-Target", "Process")
    assert r.returncode == 1 and "not a file" in r.stdout


def test_sets_once_then_reports_already_set(tmp_path):
    exe = tmp_path / "blender.exe"
    exe.write_bytes(b"x")
    first = _run("-Exe", str(exe), "-Target", "Process")
    assert first.returncode == 0, first.stdout + first.stderr
    assert "[done]" in first.stdout and "Restart ComfyUI" in first.stdout
    second = _run("-Exe", str(exe), "-Target", "Process")
    # a new process does not keep the first run's value, so the second run sets it again: still exit 0
    assert second.returncode == 0 and "[done]" in second.stdout


def test_dry_run_changes_nothing(tmp_path):
    exe = tmp_path / "blender.exe"
    exe.write_bytes(b"x")
    r = _run("-Exe", str(exe), "-Target", "Process", "-DryRun")
    assert r.returncode == 0 and "[dry run]" in r.stdout and "[done]" not in r.stdout


def test_value_is_read_back_in_the_same_process(tmp_path):
    """The script's own read-back check: a process-scope value that matches the file is reported as already set."""
    exe = tmp_path / "blender.exe"
    exe.write_bytes(b"x")
    cmd = (
        f"[Environment]::SetEnvironmentVariable('SKINTOKEN_BLENDER_BIN', '{exe}', 'Process'); "
        f"& '{SCRIPT}' -Exe '{exe}' -Target Process"
    )
    r = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "already points at" in r.stdout
