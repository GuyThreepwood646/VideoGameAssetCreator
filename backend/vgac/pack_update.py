"""Version and update of the project's own node pack (vgac-nodes, PL-81).

A pack copied from comfy-nodes/vgac-nodes gets a manifest (MANIFEST) that records the version of the source it came
from and the list of files it installed. The version is a hash of the source's files, so any change to the pack makes
it a new version without a number to maintain by hand. A folder without a manifest (made before this existed) is
outdated.

replace() updates an installed copy: it backs the old folder up first, writes the source's files over the old ones,
removes only the files the old manifest named that the source no longer has, and keeps everything else in the folder
(the person's own files). The callers say where writes are allowed (`ensure`, the install's guard).
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Callable

MANIFEST = ".vgac-pack.json"
SKIP_DIRS = {"__pycache__"}

CURRENT, OUTDATED, MISSING = "current", "outdated", "missing"


def pack_files(src: Path) -> list[str]:
    """Relative POSIX paths of the source's files, sorted, without caches and without a manifest."""
    out: list[str] = []
    for p in sorted(src.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(src)
        if p.name == MANIFEST or any(part in SKIP_DIRS for part in rel.parts):
            continue
        out.append(rel.as_posix())
    return out


def version_of(src: Path) -> str:
    h = hashlib.sha256()
    for rel in pack_files(src):
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(hashlib.sha256((src / rel).read_bytes()).digest())
    return h.hexdigest()


def read_manifest(folder: Path) -> dict | None:
    try:
        d = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def state(target: Path, src: Path) -> str:
    """missing: no folder (or an empty one). outdated: no manifest, or a manifest of another version. current otherwise."""
    if not target.is_dir() or not any(target.iterdir()):
        return MISSING
    m = read_manifest(target)
    if m is None or m.get("version") != version_of(src):
        return OUTDATED
    return CURRENT


def write_manifest(target: Path, src: Path, ensure: Callable[[Path], Path] = lambda p: p) -> None:
    body = {"version": version_of(src), "files": pack_files(src)}
    ensure(target / MANIFEST).write_text(json.dumps(body, indent=1), encoding="utf-8")


def replace(target: Path, src: Path, backups: Path, stamp: str,
            ensure: Callable[[Path], Path] = lambda p: p) -> Path:
    """Back the folder up under backups/<folder name>-<stamp>, then bring it to the source's version. Returns the backup."""
    backup = ensure(backups / f"{target.name}-{stamp}")
    ensure(backups).mkdir(parents=True, exist_ok=True)
    shutil.copytree(target, backup)
    old = read_manifest(target) or {}
    new_files = pack_files(src)
    for rel in new_files:
        dst = ensure(target / rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, dst)
    for rel in old.get("files") or []:
        if rel in new_files:
            continue
        f = ensure(target / str(rel))
        if f.is_file():
            f.unlink()
    write_manifest(target, src, ensure)
    return backup
