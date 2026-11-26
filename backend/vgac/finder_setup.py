"""The item finder's model files, fetched through the one downloader (downloader.py).

The files are the `finder_*` entries of setup/downloads.json (project_files): Grounding DINO tiny and SAM 2.1 small, each
pinned to the commit the finder loads, with SHA-256, size and the official Hugging Face address. They sit in the
Hugging Face cache layout under local/finder/hf (the finder reads them with local_files_only, so the paths must not move).

    python -m vgac.finder_setup           fetch what is missing; check what is already there
    python -m vgac.finder_setup --check   check only; downloads nothing

A file already in place is hashed against its pin and left alone when it matches. A file that differs stops the run
(nothing is deleted or replaced: the person moves it aside). A missing file comes through downloader.fetch, which
refuses any file that does not match its pin. Exit codes: 0 all present and matching (or fetched), 1 some missing in
--check mode, 2 a file in place does not match its pin, 3 a download failed.
"""
from __future__ import annotations

import logging
import re
import sys
import threading
from pathlib import Path

from . import downloader
from .downloader import DownloadError

log = logging.getLogger("vgac.finder_setup")

PREFIX = "project_file:finder_"
SNAPSHOT = re.compile(r"^(?P<repo>.*[\\/]models--[^\\/]+)[\\/]snapshots[\\/](?P<commit>[0-9a-f]{40})$")


def finder_specs() -> list[downloader.Spec]:
    """The finder's files from the list, with where each one goes."""
    return [s for s in downloader.specs_for(downloader.project_dir()) if s.id.startswith(PREFIX)]


def write_refs(spec: downloader.Spec) -> None:
    """Write refs/main of the repository (the commit the snapshot folder names) when there is none yet. An existing
    refs file is never changed."""
    m = SNAPSHOT.match(str(spec.dest.parent))
    if not m:
        return
    refs = Path(m.group("repo")) / "refs" / "main"
    if refs.exists():
        return
    refs.parent.mkdir(parents=True, exist_ok=True)
    refs.write_text(m.group("commit"), encoding="utf-8")


def run(check_only: bool = False, say=print) -> int:
    specs = finder_specs()
    if not specs:
        say("The list has no item finder files (setup/downloads.json, finder_* entries).")
        return 3
    cancel = threading.Event()
    wrong = []
    for s in specs:
        if s.present() and downloader.sha256_of(s.dest, cancel).lower() != s.sha256.lower():
            wrong.append(s)
    if wrong:
        for s in wrong:
            say(f"Not the pinned file: {s.dest}. Move it aside yourself, then run this again; nothing was changed.")
        return 2
    missing = [s for s in specs if not s.present()]
    if check_only:
        say(f"Item finder files: {len(specs) - len(missing)} of {len(specs)} present and matching; {len(missing)} to download.")
        return 1 if missing else 0
    try:
        for s in missing:
            say(f"Downloading {s.name} ({downloader.fmt_size(s.size)})")
            if downloader.fetch(s, cancel) == "downloaded":
                downloader.record(s.name, s.dest)
            write_refs(s)
    except DownloadError as e:
        say(f"{e.message} {e.fix}".strip())
        return 3
    for s in specs:
        write_refs(s)
    say(f"Item finder files: {len(specs)} of {len(specs)} present and matching.")
    return 0


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.WARNING)
    return run(check_only="--check" in argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
