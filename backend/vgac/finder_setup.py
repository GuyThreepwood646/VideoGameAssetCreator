"""The item finder's model files, fetched through the one downloader (downloader.py).

The files are the `finder_*` entries of setup/downloads.json (project_files): Grounding DINO tiny and SAM 2.1 small, each
pinned to the commit the finder loads, with SHA-256, size and the official Hugging Face address. They sit in the
Hugging Face cache layout under local/finder/hf (the finder reads them with local_files_only, so the paths must not move).

    python -m vgac.finder_setup           fetch what is missing; check what is already there
    python -m vgac.finder_setup --check   check only; downloads nothing

The environment's Python packages are checked too, against setup/finder-requirements.txt (the pinned versions,
committed): the installed versions are read with the environment's own pip freeze. A difference is printed with the
fix; it does not change the exit code, which is about the model files.

A file already in place is hashed against its pin and left alone when it matches. A file that differs stops the run
(nothing is deleted or replaced: the person moves it aside). A missing file comes through downloader.fetch, which
refuses any file that does not match its pin. Exit codes: 0 all present and matching (or fetched), 1 some missing in
--check mode, 2 a file in place does not match its pin, 3 a download failed.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
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


REQUIREMENTS = "setup/finder-requirements.txt"


def requirements_path() -> Path:
    return downloader.project_dir() / REQUIREMENTS


def venv_python() -> Path:
    """The item finder's own Python (local/finder/venv), not the backend's."""
    base = downloader.project_dir() / "local" / "finder" / "venv"
    return base / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_pins(text: str) -> dict[str, str]:
    """name -> version for each name==version line. Comments, options and local or URL lines are not pins."""
    pins = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-") or "@" in line or "==" not in line:
            continue
        name, version = line.split("==", 1)
        pins[_canon(name)] = version.strip()
    return pins


def installed_versions(py: Path) -> dict[str, str] | None:
    """The packages the environment has now, from its own pip freeze; None when that cannot be read."""
    try:
        out = subprocess.run([str(py), "-m", "pip", "freeze"], capture_output=True, text=True, timeout=300,
                             check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return read_pins(out)


def pin_drift(pins: dict[str, str], have: dict[str, str]) -> list[str]:
    """One line per pinned package that is missing or at another version. Packages not in the file are not judged."""
    out = []
    for name, version in pins.items():
        if name not in have:
            out.append(f"{name} {version} is not installed")
        elif have[name] != version:
            out.append(f"{name} is {have[name]}, pinned {version}")
    return out


def check_environment(say) -> None:
    """Print what differs between the environment and the pin file. Nothing is changed."""
    py = venv_python()
    if not py.exists():
        return
    have = installed_versions(py)
    if have is None:
        say(f"Could not read the packages of the item finder environment ({py}); its pins were not checked.")
        return
    req = requirements_path()
    if not req.exists():
        say(f"The pin file {REQUIREMENTS} is missing.")
        return
    drift = pin_drift(read_pins(req.read_text(encoding="utf-8")), have)
    if drift:
        say("Item finder packages differ from " + REQUIREMENTS + ": " + "; ".join(drift)
            + ". Rebuild the environment: move local/finder/venv aside, then run setup/Setup-ItemFinder.ps1 -Install.")
    else:
        say(f"Item finder packages match {REQUIREMENTS}.")


def run(check_only: bool = False, say=print) -> int:
    specs = finder_specs()
    if not specs:
        say("The list has no item finder files (setup/downloads.json, finder_* entries).")
        return 3
    check_environment(say)
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
