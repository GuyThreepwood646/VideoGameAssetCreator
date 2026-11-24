"""Bone-tail fix for the ComfyUI-SkinToken pack (its Blender parser appends each child bone twice).

The bug: in `rig_package/parser/bpy.py`, the loop that sets each bone's tail appends the child to `sons[p]` once more
than it needs to. The first loop of that function already appends it, so every child bone appears twice in `sons`.

The fix removes the second append and changes nothing else. The file is only touched when its SHA-256 is exactly the
copy the fix was written against: any other file is refused, and nothing is written. A file already fixed is left
alone, so running the fix again does nothing.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

PACK_FILE = Path("vendor") / "skintokens" / "rig_package" / "parser" / "bpy.py"

# SHA-256 of the file as the pack ships it (the copy the fix was written against).
ORIGINAL_SHA256 = "828ebead930f0cabf58949b821613fa4693b22e978439c89f313d1878be757ad"
# SHA-256 of the same file with the duplicate append removed.
PATCHED_SHA256 = "a2f1124a821128fc0aae3652a0c183b2ed2d2ea0d4c5ddc312dbbfe0be823b65"

# The second `sons[p].append(i)` sits right before the distance line; the distance line is kept.
_DUPLICATE = re.compile(rb"^( +)sons\[p\]\.append\(i\)(\r?\n)(\1d = np\.linalg\.norm\(joints\[i\] - joints\[p\]\)\r?\n)", re.M)


class PatchRefused(Exception):
    """The file is not a copy the fix knows: nothing was written."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def status(pack_dir: Path) -> str:
    """'original' (the fix can be applied), 'patched' (already fixed), 'missing' (no such file) or 'unknown'."""
    f = Path(pack_dir) / PACK_FILE
    if not f.is_file():
        return "missing"
    digest = _sha256(f)
    if digest == ORIGINAL_SHA256:
        return "original"
    if digest == PATCHED_SHA256:
        return "patched"
    return "unknown"


def apply(pack_dir: Path, backup_to: Path | None = None) -> str:
    """Apply the fix once. Returns 'applied' or 'already'. Raises PatchRefused for any other file.

    backup_to: when given, the original file is copied there first (the caller chooses the folder; the full
    original path is the caller's business to record).
    """
    pack_dir = Path(pack_dir)
    state = status(pack_dir)
    if state == "patched":
        return "already"
    if state != "original":
        raise PatchRefused(f"{pack_dir / PACK_FILE} is {state}; the fix only changes the copy with SHA-256 {ORIGINAL_SHA256}")
    f = pack_dir / PACK_FILE
    original = f.read_bytes()
    fixed, count = _DUPLICATE.subn(rb"\3", original)
    if count != 1:
        raise PatchRefused(f"expected one duplicate append in {f}, found {count}")
    if backup_to is not None:
        Path(backup_to).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(f, backup_to)
    tmp = f.with_name(f.name + ".vgac-fix")
    tmp.write_bytes(fixed)
    if hashlib.sha256(fixed).hexdigest() != PATCHED_SHA256:
        tmp.unlink(missing_ok=True)
        raise PatchRefused(f"the fixed text of {f} does not match the expected result; nothing was written")
    tmp.replace(f)
    return "applied"
