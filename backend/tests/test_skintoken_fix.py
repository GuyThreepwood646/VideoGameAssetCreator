"""The SkinToken bone-tail fix (vgac/skintoken_fix.py) and its use by the pack install step.

The fixture is a small stand-in for the pack's parser file: the same two-line shape around the second append, and
the same distance line that must survive. The fix's hashes are swapped for the fixture's own, so the fixture can
stand in for the real file.
"""

from __future__ import annotations

import hashlib
import types
from pathlib import Path

import pytest

from vgac import comfy_install, skintoken_fix as fix

# The real file: the first loop appends (kept), the second appends again (removed).
REAL = (
    "        for i in range(len(asset.parents)):\n"
    "            p = asset.parents[i]\n"
    "            if p == -1:\n"
    "                continue\n"
    "            sons[p].append(i)\n"
    "            length_sum += np.linalg.norm(joints[i] - joints[p])\n"
    "        for i in range(len(asset.parents)):\n"
    "            p = asset.parents[i]\n"
    "            if p == -1:\n"
    "                continue\n"
    "            sons[p].append(i)\n"
    "            d = np.linalg.norm(joints[i] - joints[p])\n"
    "            if d <= length * 1e-2:\n"
    "                pass\n"
)
AFTER = REAL.replace("            sons[p].append(i)\n            d = np", "            d = np")


def _pack(tmp_path: Path, text: bytes) -> Path:
    pack = tmp_path / "ComfyUI-SkinToken"
    f = pack / fix.PACK_FILE
    f.parent.mkdir(parents=True)
    f.write_bytes(text)
    return pack


@pytest.fixture
def known(monkeypatch):
    """Make the fix know the fixture's original and patched text."""
    orig = REAL.encode()
    patched = AFTER.encode()
    monkeypatch.setattr(fix, "ORIGINAL_SHA256", hashlib.sha256(orig).hexdigest())
    monkeypatch.setattr(fix, "PATCHED_SHA256", hashlib.sha256(patched).hexdigest())
    return orig, patched


def test_real_hashes_are_distinct_and_well_formed():
    assert len(fix.ORIGINAL_SHA256) == 64 and len(fix.PATCHED_SHA256) == 64
    assert fix.ORIGINAL_SHA256 != fix.PATCHED_SHA256


def test_applies_once_and_removes_only_the_second_append(known, tmp_path):
    orig, patched = known
    pack = _pack(tmp_path, orig)
    assert fix.status(pack) == "original"
    assert fix.apply(pack) == "applied"
    out = (pack / fix.PACK_FILE).read_bytes()
    assert out == patched
    # each child is appended once in the first loop: the second loop no longer appends it
    assert out.decode().count("sons[p].append(i)") == 1
    assert "d = np.linalg.norm(joints[i] - joints[p])" in out.decode()


def test_second_run_skips_an_already_fixed_file(known, tmp_path):
    orig, patched = known
    pack = _pack(tmp_path, patched)
    assert fix.status(pack) == "patched"
    assert fix.apply(pack) == "already"
    assert (pack / fix.PACK_FILE).read_bytes() == patched


def test_refuses_an_unknown_file_and_writes_nothing(known, tmp_path):
    unknown = b"# some other version of the file\n"
    pack = _pack(tmp_path, unknown)
    assert fix.status(pack) == "unknown"
    with pytest.raises(fix.PatchRefused):
        fix.apply(pack)
    assert (pack / fix.PACK_FILE).read_bytes() == unknown
    assert not list((pack / fix.PACK_FILE).parent.glob("*.vgac-fix"))


def test_missing_file_is_refused(known, tmp_path):
    pack = tmp_path / "empty-pack"
    pack.mkdir()
    assert fix.status(pack) == "missing"
    with pytest.raises(fix.PatchRefused):
        fix.apply(pack)


def test_backup_copy_is_the_original_bytes(known, tmp_path):
    orig, _ = known
    pack = _pack(tmp_path, orig)
    backup = tmp_path / "backup" / "bpy.py"
    fix.apply(pack, backup_to=backup)
    assert backup.read_bytes() == orig


def test_refuses_a_file_with_no_duplicate_to_remove(monkeypatch, tmp_path):
    """A file whose hash is the known original but whose text has no duplicate is refused, not edited."""
    text = b"def nothing():\n    pass\n"
    monkeypatch.setattr(fix, "ORIGINAL_SHA256", hashlib.sha256(text).hexdigest())
    monkeypatch.setattr(fix, "PATCHED_SHA256", "0" * 64)
    pack = _pack(tmp_path, text)
    with pytest.raises(fix.PatchRefused):
        fix.apply(pack)
    assert (pack / fix.PACK_FILE).read_bytes() == text


def test_pack_install_step_applies_the_fix_to_the_pack(known, tmp_path):
    orig, patched = known
    pack = _pack(tmp_path, orig)
    job = types.SimpleNamespace(text="")
    comfy_install._apply_skintoken_fix(job, pack)
    assert (pack / fix.PACK_FILE).read_bytes() == patched
    assert job.text == "Fixed the SkinToken bone tails"
    comfy_install._apply_skintoken_fix(job, pack)   # second install or check: nothing to do, no error
    assert (pack / fix.PACK_FILE).read_bytes() == patched


def test_pack_install_step_completes_when_the_file_is_unknown(known, tmp_path):
    pack = _pack(tmp_path, b"something else\n")
    job = types.SimpleNamespace(text="")
    comfy_install._apply_skintoken_fix(job, pack)   # no exception: the install carries on
    assert "not applied" in job.text
    assert (pack / fix.PACK_FILE).read_bytes() == b"something else\n"
