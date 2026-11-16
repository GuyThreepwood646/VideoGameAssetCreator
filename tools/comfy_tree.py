"""Make the pinned file list of a ComfyUI source zip (setup/comfy-tree-v<version>.json) and print the two SHA-256 values
that go into setup/downloads.json "comfyui_install": the zip's ("sha256") and the list's ("tree_sha256").

    python -I tools/comfy_tree.py <the downloaded zip> <version>

Reads the zip only; runs nothing from it. The list holds every file's path below the zip's top folder, its size and
its SHA-256, so the app can accept the same files if GitHub ever compresses the zip differently.
"""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path


def main(zip_path: Path, version: str) -> None:
    files = []
    with zipfile.ZipFile(zip_path) as z:
        tops = {n.split("/", 1)[0] for n in z.namelist()}
        if len(tops) != 1:
            raise SystemExit(f"expected one top folder, found {sorted(tops)[:5]}")
        for info in z.infolist():
            if info.is_dir():
                continue
            rel = info.filename.split("/", 1)[1]
            data = z.read(info)
            files.append({"path": rel, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    files.sort(key=lambda f: f["path"])
    out = Path(__file__).resolve().parents[1] / "setup" / f"comfy-tree-v{version}.json"
    text = json.dumps({"version": version, "files": files}, indent=0) + "\n"
    out.write_text(text, encoding="utf-8", newline="\n")
    print(f"files: {len(files)}, bytes: {sum(f['size'] for f in files)}")
    print(f"sha256 (the zip): {hashlib.sha256(zip_path.read_bytes()).hexdigest()}")
    lf = out.read_bytes().replace(b"\r\n", b"\n")  # the app hashes the list with LF line endings
    print(f"tree_sha256 ({out.name}): {hashlib.sha256(lf).hexdigest()}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2])
