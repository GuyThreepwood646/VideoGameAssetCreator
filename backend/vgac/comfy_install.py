"""VGAC's own ComfyUI (setup card, phase 5b): choice C (nothing shared) and choice A (share the person's models).

What it installs, in order (docs/INSTALLER-PLAN.md B3; setup/downloads.json "comfyui_install"):

1. check     the hardware plan (no usable NVIDIA card: refused, nothing downloaded), the pins (the ComfyUI zip's SHA-256
             and its file list; while either is missing nothing is installed), the folder (length, free space), uv.
2. source    the ComfyUI zip of one commit from GitHub, through the shared downloader. GitHub keeps the files inside a
             commit's zip the same but not the zip's bytes, so: the zip's pinned SHA-256 first, and every unpacked file is
             then checked against the pinned file list (path, size, SHA-256). Nothing missing, nothing extra.
3. wheels    every Python package in setup/comfyui-lock.json, through the shared downloader (resume, SHA-256).
4. venv      `uv venv` from the app's private Python 3.13 (made by VGAC.exe), offline.
5. packages  `uv pip install --offline --no-index --find-links <wheels> --require-hashes --no-deps --only-binary :all:`.
6. packs     ComfyUI-SkinToken (pinned zip) and vgac-nodes (this project).
7. test      its Python imports torch and sees the card; `main.py --quick-test-for-ci` loads every node and exits.
8. engines   one engine per card of the hardware plan, on free ports from 8288 up, written to the machine settings.

Afterwards `start_engines` starts them through the app's own engine supervisor.

Never touching a ComfyUI the app did not install: every write in this module goes through `guard()`. It refuses a
path outside the install folder (`<data folder>/comfyui`), a path whose real location (junctions followed) is
elsewhere, a folder without the app's marker file, and anything inside a ComfyUI found on this computer or the
person's shared models folder (choice A reads that folder by path only; the model-folders file VGAC writes for it lives
in VGAC's own folder).

Every network and process action is in `Ops`, which tests replace: the download opener, process runs, the hardware
reading, the port check, free space, the list of ComfyUI copies found on this computer.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import config, downloader, hardware_setup, setup_settings
from .downloader import DownloadError, Spec, Stopped

log = logging.getLogger("vgac.comfy_install")

MARKER = "vgac-install.json"
MADE_BY = "VGAC"
JOB_ID = "comfy-install"
SPACE_SLACK = 1.10  # 10 percent on top of what the install needs
RETRIES = 6  # tries for a rename or delete that Windows or an antivirus holds
TEST_TIMEOUT = 900
TORCH_CODE = "import torch;print('VGAC', torch.__version__, torch.cuda.is_available())"
# Python started for the test runs: no user site-packages (-s), no PYTHON* variables (-E), UTF-8 (-X utf8).
PY_FLAGS = ["-s", "-E", "-X", "utf8"]
# Inherited variables that could steer uv, pip or Python somewhere else; dropped before any program runs (F8).
DROP_ENV = ("UV_", "PIP_", "PYTHON", "VIRTUAL_ENV", "CONDA_")
STEPS = [("check", "Check this computer"), ("source", "Get ComfyUI"), ("venv", "Make its Python environment"),
         ("wheels", "Get its Python packages"), ("packages", "Install its Python packages"),
         ("packs", "Add the node packs VGAC needs"), ("test", "Test it"), ("engines", "Set up the engines")]
MODEL_FOLDERS = ("checkpoints", "clip", "clip_vision", "configs", "controlnet", "diffusion_models", "embeddings", "loras",
                 "text_encoders", "unet", "upscale_models", "vae", "vae_approx", "style_models", "gligen", "hypernetworks",
                 "photomaker", "model_patches", "audio_encoders", "latent_upscale_models", "sams", "skintoken")


class NotOurs(DownloadError):
    """A write was refused because the target is not inside VGAC's own ComfyUI install."""

    def __init__(self, target: Path, why: str):
        super().__init__("not_ours", f"VGAC refused to change {target}: {why}",
                         "Nothing was changed there. This is a safety stop; tell the app's makers if you see it.")


# --- the machine, behind one interface --------------------------------------------------------------------------------


def _port_in_use(port: int) -> bool:
    for host in ("127.0.0.1", "::1"):
        try:
            with socket.create_connection((host, port), timeout=0.3):
                return True
        except OSError:
            continue
    return False


def _run(argv: list[str], cwd: Path, env: dict[str, str], log_path: Path, cancel: threading.Event, timeout: float) -> int:
    """Run a program hidden with exactly `env` (clean_env()), its output into `log_path`. Stopped when `cancel` is set
    (its own process tree only)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    with open(log_path, "ab") as out:
        out.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {subprocess.list2cmdline(argv)} in {cwd}\n".encode("utf-8"))
        out.flush()
        proc = subprocess.Popen(argv, cwd=str(cwd), env=dict(env), stdin=subprocess.DEVNULL, stdout=out,
                                stderr=subprocess.STDOUT, creationflags=flags)
        end = time.monotonic() + timeout
        while proc.poll() is None:
            if cancel.is_set() or time.monotonic() > end:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, creationflags=flags, check=False)
                else:
                    proc.kill()
                proc.wait()
                if cancel.is_set():
                    raise Stopped()
                return -9
            time.sleep(0.25)
        return proc.returncode


def is_own_install(folder: str | Path) -> bool:
    """True only for VGAC's own install folder itself (or a folder inside it): reached without a junction, with its
    marker and its ledger entry."""
    root = install_root()
    plain = os.path.normcase(os.path.abspath(str(root)))
    if _real(root) != plain or not _within(_real(Path(folder)), plain):
        return False
    return read_marker(root) is not None and _in_ledger(root)


def fence_for(ops: "Ops", shared: str = "") -> "Fence":
    fence = Fence()
    for f in ops.found_comfies():
        if not is_own_install(f):
            fence.add(f)
    if shared:
        fence.add(shared)
    return fence


def _found_comfies() -> list[str]:
    from .comfy_find import find_comfy

    return [c["folder"] for c in find_comfy()["found"]]


@dataclass
class Ops:
    """Everything that touches the network, starts a program or reads the machine. Tests give stand-ins."""

    opener: Callable[[str, int], Any] | None = None  # downloader.default_open when None
    run: Callable[[list[str], Path, dict[str, str], Path, threading.Event, float], int] = _run
    hardware: Callable[[], hardware_setup.Hardware] = hardware_setup.read_hardware
    port_in_use: Callable[[int], bool] = _port_in_use
    free_bytes: Callable[[Path], int] = downloader.free_bytes
    found_comfies: Callable[[], list[str]] = _found_comfies
    sleep: Callable[[float], None] = time.sleep


# --- where -----------------------------------------------------------------------------------------------------------


def shipped() -> dict[str, Any]:
    """setup/downloads.json alone. The install's pins (the ComfyUI zip, its file list, the package list, the node packs)
    are never taken from the person's downloads.local.json: an overlay must not be able to swap a pinned file."""
    try:
        d = json.loads((downloader.project_dir() / "setup" / "downloads.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def entry() -> dict[str, Any]:
    return dict(shipped().get("comfyui_install") or {})


def shipped_packs() -> list[dict[str, Any]]:
    return [dict(v, id=k) for k, v in (shipped().get("node_packs") or {}).items() if isinstance(v, dict)]


def expand_percent(text: str) -> str:
    """%NAME% is replaced by the environment variable (the starter's VGAC.exe does the same, and only this: no $NAME, no ~)."""
    return re.sub(r"%([^%]+)%", lambda m: os.environ.get(m.group(1), m.group(0)), text)


def data_folder() -> Path:
    """The starter's data folder (machine.json starter.dataFolder; default local/data in the project)."""
    chosen = ""
    mpath = config.machine_path()
    if mpath is not None:
        try:
            chosen = str((config.read_machine(mpath).get("starter") or {}).get("dataFolder") or "")
        except config.ConfigError:
            chosen = ""
    base = downloader.project_dir()
    if not chosen:
        return base / "local" / "data"
    p = Path(expand_percent(chosen))
    return p if p.is_absolute() else base / p


def install_root() -> Path:
    return data_folder() / "comfyui"


def uv_exe() -> Path:
    return data_folder() / "tools" / "uv" / "uv.exe"


def venv_python(root: Path | None = None) -> Path:
    return (root or install_root()) / ".venv" / "Scripts" / "python.exe"


def _real(p: Path) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(str(p))))


def _within(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent.rstrip("\\/") + os.sep)


# --- the guard: the only way this module writes -------------------------------------------------------------------------


def read_marker(root: Path | None = None) -> dict[str, Any] | None:
    try:
        d = json.loads(((root or install_root()) / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("made_by") == MADE_BY else None


@dataclass
class Fence:
    """What the guard keeps out: ComfyUI folders found on this computer and the person's shared models folder."""

    foreign: list[str] = field(default_factory=list)

    def add(self, folder: str | Path) -> None:
        if str(folder).strip():
            self.foreign.append(_real(Path(folder)))


_fence = Fence()


def guard(target: Path, fence: Fence | None = None) -> Path:
    """`target` if VGAC may write there: inside its own install folder (real location, junctions followed), with the
    marker present, and outside every ComfyUI found on this computer and the shared models folder. NotOurs otherwise."""
    fence = fence or _fence
    root = install_root()
    t, r = _real(target), _real(root)
    if not _within(t, r):
        raise NotOurs(target, "it is not inside VGAC's own ComfyUI folder.")
    if read_marker(root) is None:
        raise NotOurs(target, "VGAC's own ComfyUI folder has no VGAC marker, so VGAC did not make it.")
    for f in fence.foreign:
        if _within(t, f) or _within(f, t):
            raise NotOurs(target, "it is part of a ComfyUI (or a models folder) VGAC did not install.")
    return target


def _retry(what: Path, fn: Callable[[], Any], ops: Ops) -> Any:
    """A rename or delete Windows (or an antivirus scanning a new file) holds for a moment: try again, then say so."""
    for n in range(RETRIES):
        try:
            return fn()
        except PermissionError as e:
            if n == RETRIES - 1:
                raise DownloadError("locked", f"Windows or your antivirus is holding {what} ({e.strerror or e}).",
                                    "Wait a minute and press Try again. If your antivirus removed or blocked a file, "
                                    "restore it there and press Try again.") from None
            ops.sleep(0.5 * (n + 1))


def g_mkdir(p: Path, fence: Fence | None = None) -> Path:
    guard(p, fence).mkdir(parents=True, exist_ok=True)
    return p


def g_write(p: Path, text: str, fence: Fence | None = None) -> None:
    guard(p, fence)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    _retry(p, lambda: os.replace(tmp, p), Ops())


def g_rmtree(p: Path, ops: Ops, fence: Fence | None = None) -> None:
    guard(p, fence)
    if p.exists():
        _retry(p, lambda: shutil.rmtree(p), ops)


def g_move(src: Path, dst: Path, ops: Ops, fence: Fence | None = None) -> None:
    guard(src, fence)
    guard(dst, fence)
    _retry(dst, lambda: os.replace(src, dst), ops)


def g_unzip(zip_path: Path, into: Path, fence: Fence | None = None) -> None:
    """Unpack into `into` (VGAC's own), refusing any entry that would land outside it."""
    guard(into, fence)
    into.mkdir(parents=True, exist_ok=True)
    root = Path(os.path.realpath(into))
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            t = Path(os.path.realpath(into / info.filename))
            if t != root and root not in t.parents:
                raise DownloadError("bad_zip", "The ComfyUI download has a file that would land outside its folder, so it was not opened.",
                                    "Press Try again; if it happens again the download is not the published one.")
        z.extractall(into)


def claim(root: Path, choice: str, fence: Fence) -> dict[str, Any]:
    """Write the marker into a new or empty folder (or keep the one VGAC wrote). A folder with anything else in it is
    refused: VGAC installs only into a folder it made."""
    m = read_marker(root)
    if m is not None and _in_ledger(root):
        _ensure_id(root, m)
        return m
    if m is not None and _adopt_if_proved(root):
        return read_marker(root) or m
    fresh = {"made_by": MADE_BY, "what": "VGAC's own ComfyUI", "version": entry().get("version", ""), "choice": choice,
             "install_id": _ledger_id(root) or uuid.uuid4().hex,
             "steps": {}, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if (root / MARKER).is_file() and _in_ledger(root):
        log.warning("the marker of VGAC's own ComfyUI could not be read; writing a fresh one (every step checks again)")
        (root / MARKER).write_text(json.dumps(fresh, indent=2), encoding="utf-8")
        return fresh
    r = _real(root)
    for f in fence.foreign:
        if _within(r, f) or _within(f, r):
            raise NotOurs(root, "it is part of a ComfyUI (or a models folder) VGAC did not install.")
    if root.exists() and any(root.iterdir()):
        raise DownloadError("folder_used", f"{root} already has files in it that VGAC did not put there, so VGAC will not install into it.",
                            "Move those files somewhere else, or choose another data folder for VGAC.")
    root.mkdir(parents=True, exist_ok=True)
    m = fresh
    (root / MARKER).write_text(json.dumps(m, indent=2), encoding="utf-8")
    downloader.record("VGAC's own ComfyUI", root, "added", 0)
    _stamp_ledger_id(root, m["install_id"])
    return m


def save_marker(m: dict[str, Any], fence: Fence | None = None) -> None:
    g_write(install_root() / MARKER, json.dumps(m, indent=2), fence)


# --- the lists ---------------------------------------------------------------------------------------------------------


def lock() -> list[dict[str, Any]]:
    e = entry()
    p = downloader.project_dir() / str(e.get("lock") or "setup/comfyui-lock.json")
    try:
        return list(json.loads(p.read_text(encoding="utf-8")).get("wheels") or [])
    except (OSError, ValueError):
        return []


def wheel_specs(root: Path) -> list[Spec]:
    out = []
    for w in lock():
        name = str(w["file"])
        if "/" in name or "\\" in name or ".." in name:
            continue
        out.append(Spec(id=f"wheel:{w['name']}", name=f"{w['name']} {w['version']}", urls=[str(w["url"])], dest=root / "wheels" / name,
                        size=int(w.get("size") or 0), sha256=str(w.get("sha256") or ""), page="https://pypi.org/project/" + str(w["name"]),
                        licence=str(w.get("licence") or ""), kind="tool"))
    return out


def requirements_text() -> str:
    return "".join(f"{w['name']}=={w['version']} --hash=sha256:{w['sha256']}\n" for w in lock())


def list_sha256(raw: bytes) -> str:
    """The SHA-256 of a pinned text list with its line endings made LF first, so a copy that git checked out with CRLF
    (core.autocrlf=true, the Windows default) matches the same pin. Nothing else about the text is changed."""
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def tree_list() -> dict[str, Any] | None:
    """The pinned file list of the ComfyUI source, or None while it is missing or does not match its pin."""
    e = entry()
    pin = str(e.get("tree_sha256") or "")
    rel = str(e.get("tree_list") or "")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", pin) or not rel:
        return None
    p = downloader.project_dir() / rel
    try:
        raw = p.read_bytes()
    except OSError:
        return None
    if list_sha256(raw) != pin.lower():
        return None
    d = json.loads(raw.decode("utf-8"))
    return d if isinstance(d, dict) and isinstance(d.get("files"), list) else None


def pins_missing() -> list[str]:
    e = entry()
    out = []
    if not re.fullmatch(r"[0-9a-fA-F]{64}", str(e.get("sha256") or "")):
        out.append("the checksum of the ComfyUI download")
    if tree_list() is None:
        out.append("the checked list of ComfyUI's files")
    if not lock():
        out.append("the list of its Python packages")
    return out


def check_tree(folder: Path, listing: dict[str, Any]) -> list[str]:
    """Problems between an unpacked folder and the pinned list: missing, extra, wrong size or wrong SHA-256."""
    want = {str(f["path"]).replace("\\", "/"): f for f in listing["files"]}
    have: dict[str, Path] = {}
    for dp, _dn, fn in os.walk(folder):
        for f in fn:
            p = Path(dp) / f
            have[p.relative_to(folder).as_posix()] = p
    problems = [f"missing {k}" for k in sorted(set(want) - set(have))]
    problems += [f"not in the published files: {k}" for k in sorted(set(have) - set(want))]
    for k in sorted(set(want) & set(have)):
        p, w = have[k], want[k]
        if p.stat().st_size != int(w["size"]) or downloader.sha256_of(p) != str(w["sha256"]).lower():
            problems.append(f"changed {k}")
    return problems


def zip_problems(zip_path: Path, listing: dict[str, Any]) -> list[str]:
    """Without unpacking: the zip's entries against the pinned list, both ways, by name and size. A zip that cannot be
    read (cut short, damaged) is a problem too."""
    want = {str(f["path"]).replace("\\", "/"): int(f["size"]) for f in listing["files"]}
    try:
        with zipfile.ZipFile(zip_path) as z:
            infos = [i for i in z.infolist() if not i.is_dir()]
            if len({i.filename.split("/", 1)[0] for i in z.infolist()}) != 1:
                return ["the zip is not one folder"]
            have = {i.filename.split("/", 1)[1] if "/" in i.filename else i.filename: i.file_size for i in infos}
    except Exception:  # noqa: BLE001
        return ["the zip cannot be read (it may be incomplete or damaged)"]
    problems = [f"missing {k}" for k in sorted(set(want) - set(have))]
    problems += [f"not in the published files: {k}" for k in sorted(set(have) - set(want))]
    problems += [f"changed {k}" for k in sorted(set(want) & set(have)) if want[k] != have[k]]
    return problems


def pinned_problems(app: Path, listing: dict[str, Any]) -> list[str]:
    """The pinned files of an installed ComfyUI folder that are missing or changed. Other files there (models,
    custom_nodes, user, caches) are not looked at."""
    out = []
    for f in listing["files"]:
        p = app / str(f["path"])
        try:
            if p.stat().st_size != int(f["size"]) or downloader.sha256_of(p) != str(f["sha256"]).lower():
                out.append(f"changed {f['path']}")
        except OSError:
            out.append(f"missing {f['path']}")
    return out


# --- planning: what is still to do, and how big ------------------------------------------------------------------------


def _done(m: dict[str, Any] | None, step: str) -> bool:
    return bool(m and (m.get("steps") or {}).get(step) == "done")


def _zip_bytes(e: dict[str, Any]) -> int:
    """The ComfyUI zip's size: as pinned, else a third of the unpacked size."""
    return int(e.get("size") or 0) or int(e.get("unpacked_bytes") or 0) // 3


def plan(root: Path | None = None) -> list[dict[str, Any]]:
    """The steps with their plain names, download sizes and state (done / to do)."""
    root = root or install_root()
    m = read_marker(root) if root.exists() else None
    e = entry()
    ws = wheel_specs(root)
    left_wheels = sum(max(0, s.size - (s.part.stat().st_size if s.part.exists() else 0)) for s in ws if not s.present())
    sizes = {"source": _zip_bytes(e) if not _done(m, "source") else 0,
             "wheels": left_wheels if not _done(m, "packages") else 0}
    return [{"id": sid, "label": label, "download_bytes": sizes.get(sid, 0), "done": _done(m, sid)} for sid, label in STEPS]


def space_needed(root: Path) -> int:
    m = read_marker(root) if root.exists() else None
    steps = plan(root)
    dl = sum(s["download_bytes"] for s in steps)
    e = entry()
    unpacked = 0 if _done(m, "packages") else int(e.get("installed_bytes") or 0)
    unpacked += 0 if _done(m, "source") else int(e.get("unpacked_bytes") or 0)
    return int((dl + unpacked) * SPACE_SLACK) + downloader.SPACE_MARGIN


def shared_models_yaml(models: Path) -> str:
    """ComfyUI's model-folders file for choice A: the person's models folder, read by path. No is_default, so ComfyUI
    keeps its own folder first for anything it saves."""
    lines = ["# Written by VGAC: your own ComfyUI's models, read from where they are. VGAC never writes into that folder.",
             "your_comfyui_models:", f"    base_path: {json.dumps(str(models))}"]
    for name in MODEL_FOLDERS:
        if (models / name).is_dir():
            lines.append(f"    {name}: {name}/")
    return "\n".join(lines) + "\n"


# --- the run ------------------------------------------------------------------------------------------------------------


@dataclass
class Install:
    choice: str = "C"
    shared_models: str = ""  # choice A: the person's models folder
    state: str = "idle"  # idle | working | done | failed | stopped
    step: str = ""
    text: str = ""
    bytes_done: int = 0
    bytes_total: int = 0
    error: dict[str, str] | None = None
    warnings: list[str] = field(default_factory=list)
    engines: list[dict[str, Any]] = field(default_factory=list)
    source_files: list[str] = field(default_factory=list)  # set by the source step when it placed files
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None

    def public(self) -> dict[str, Any]:
        return {"state": self.state, "step": self.step, "text": self.text, "error": self.error, "choice": self.choice,
                "bytes_done": self.bytes_done, "bytes_total": self.bytes_total, "warnings": list(self.warnings),
                "engines": list(self.engines), "steps": plan()}


_current: Install | None = None
_lock = threading.Lock()


def uv_cache() -> Path:
    """uv's cache lives inside the install (guarded, and deleted after a successful install and by Remove). uv
    hard-links from it into the venv, which works because both are on the same drive; deleting the cache afterwards
    leaves the venv's files in place."""
    return install_root() / ".uv-cache"


def clean_env() -> dict[str, str]:
    """This process's environment without anything that could steer uv, pip or Python elsewhere (UV_*, PIP_*,
    PYTHON*, VIRTUAL_ENV, CONDA_*), plus the app's own settings for uv."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(DROP_ENV)}
    d = data_folder()
    env.update({"UV_PYTHON_INSTALL_DIR": str(d / "python"), "UV_CACHE_DIR": str(uv_cache()), "UV_PYTHON_PREFERENCE": "only-managed",
                "UV_PYTHON_DOWNLOADS": "never", "UV_OFFLINE": "1", "UV_NO_CONFIG": "1", "UV_PYTHON_INSTALL_REGISTRY": "0",
                "UV_LINK_MODE": "hardlink", "UV_NO_PROGRESS": "1", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    return env


def _log_file(name: str) -> Path:
    return downloader.project_dir() / "runtime" / "logs" / f"comfy-install-{name}.log"


def _tail(p: Path, n: int = 4000) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")[-n:]
    except OSError:
        return ""


def _program_failed(what: str, log_path: Path) -> DownloadError:
    t = _tail(log_path)
    if re.search(r"access is denied|being used by another process|os error 5\b|os error 32\b", t, re.I):
        return DownloadError("locked", f"{what}: Windows or your antivirus blocked a file.",
                             f"Wait a minute and press Try again. The details are in {log_path}.")
    if re.search(r"hash mismatch|hashes do not match", t, re.I):
        return DownloadError("bad_checksum", f"{what}: a package is not the published file.", "Press Try again; the packages are checked again.")
    return DownloadError("program", f"{what} did not finish.", f"Press Try again. The details are in {log_path}.")


def step_check(job: Install, ops: Ops, fence: Fence) -> hardware_setup.Plan:
    r = _real(install_root())
    for f in fence.foreign:
        if _within(r, f) or _within(f, r):
            raise NotOurs(install_root(), "it is part of a ComfyUI (or a models folder) VGAC did not install. Choose another data folder.")
    hw = ops.hardware()
    p = hardware_setup.propose(hw)
    if p.status == "cannot":
        raise DownloadError("no_card", " ".join(m["text"] for m in p.messages if m["level"] == "bad") or
                            "This computer has no graphics card VGAC can use.",
                            "Nothing was downloaded. VGAC's own ComfyUI is only installed where it can run.")
    job.warnings = [m["text"] for m in p.messages if m["level"] == "warn"]
    missing = pins_missing()
    if missing:
        raise DownloadError("not_ready", "VGAC's own ComfyUI cannot be installed by this copy of the app yet: "
                            + " and ".join(missing) + (" is" if len(missing) == 1 else " are") + " not in it.",
                            "Nothing was downloaded. Update the app, or link a ComfyUI you have.")
    root = install_root()
    limit = int(entry().get("max_root_chars") or 90)
    if len(str(root)) > limit:
        raise DownloadError("path_long", f"The folder {root} is too long for ComfyUI's files ({len(str(root))} characters; at most {limit}). "
                            "Windows cannot open files whose full path is longer than 260 characters.",
                            "Choose a data folder with a shorter path, for example D:\\VGAC.")
    if not uv_exe().is_file():
        raise DownloadError("no_uv", "The app's package tool (uv) is not in its data folder.",
                            "Start the app once with VGAC.exe, which sets it up, then press Try again.")
    need = space_needed(root)
    probe = root
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free = ops.free_bytes(probe)
    if free < need:
        raise DownloadError("no_space", f"This needs about {downloader.fmt_size(need)}; the drive of {root} has {downloader.fmt_size(free)} free.",
                            "Free some space, or choose a data folder on another drive, then press Try again.")
    return p


def source_cap(e: dict[str, Any]) -> int:
    """The most bytes the ComfyUI download may have: a zip is never much larger than the files in it."""
    return max(int(e.get("size") or 0) * 2, int(int(e.get("unpacked_bytes") or 0) * 1.1)) + downloader.MB


def _bad_source(detail: str) -> DownloadError:
    return DownloadError("bad_checksum", f"The ComfyUI download is not the published one ({detail}). It has been deleted and not used.",
                         "Press Try again. If it happens again, GitHub may be serving something else; do not install it.")


def step_source(job: Install, ops: Ops, fence: Fence) -> None:
    """Get the pinned ComfyUI files into <root>/ComfyUI. Only the pinned files are ever written there: the models,
    node packs, user folders and anything else in it are left as they are (a repair never costs a model)."""
    root = install_root()
    e = entry()
    app = root / "ComfyUI"
    listing = tree_list()
    assert listing is not None  # step_check refused otherwise
    if app.is_dir() and not pinned_problems(app, listing):
        return  # every pinned file is there and unchanged
    zip_path = root / "downloads" / f"ComfyUI-{e.get('commit', 'source')}.zip"
    g_mkdir(zip_path.parent, fence)
    part = zip_path.with_name(zip_path.name + ".part")
    guard(part, fence)
    unpack, staging = root / "ComfyUI.unpack", root / "ComfyUI.new"

    def discard() -> None:
        for p in (part, zip_path):
            if p.exists():
                _retry(p, lambda p=p: guard(p, fence).unlink(), ops)
        g_rmtree(unpack, ops, fence)
        g_rmtree(staging, ops, fence)

    if not zip_path.is_file():
        job.text = "Downloading ComfyUI from github.com"

        cap = source_cap(e)

        def on_bytes(done: int) -> None:
            job.bytes_done = done
            if done > cap:
                raise DownloadError("too_big", f"The ComfyUI download is larger than the published one ({downloader.fmt_size(done)} "
                                    f"and still coming); it was stopped and deleted.", "Press Try again. If it happens again, do not install it.")

        try:
            downloader.try_each([str(e["zip"])], lambda url: downloader.stream_to_part(url, part, "ComfyUI", job.cancel, on_bytes,
                                                                                      ops.opener or downloader.default_open),
                                "ComfyUI", str(e.get("page", "")), where=zip_path.parent)
        except DownloadError as err:
            if err.code in ("too_big", "no_space", "write"):
                discard()  # nothing worth resuming
            raise
        if downloader.sha256_of(part, job.cancel) != str(e["sha256"]).lower():
            # GitHub keeps the files of a commit's zip but not its bytes. The same size with other bytes is damage; any
            # other difference must still hold exactly the pinned files (names and sizes here, contents after unpacking).
            if int(e.get("size") or 0) and part.stat().st_size == int(e["size"]):
                discard()
                raise _bad_source("same size as the published zip but other bytes")
            problems = zip_problems(part, listing)
            if problems:
                discard()
                raise _bad_source(f"{len(problems)} differences, for example: {problems[0]}")
            log.info("ComfyUI zip: the bytes differ from the pin, the file names and sizes match; checking every file after unpacking")
        _retry(zip_path, lambda: os.replace(part, zip_path), ops)
    job.text = "Unpacking ComfyUI and checking every file"
    try:
        g_rmtree(staging, ops, fence)
        g_rmtree(unpack, ops, fence)
        g_unzip(zip_path, unpack, fence)
        tops = list(unpack.iterdir())
        if len(tops) != 1 or not tops[0].is_dir():
            raise _bad_source("not laid out as the published zip")
        g_move(tops[0], staging, ops, fence)
        problems = check_tree(staging, listing)
        if problems:
            log.warning("ComfyUI source does not match the published files: %s", problems[:20])
            raise _bad_source(f"{len(problems)} files differ, for example: {problems[0]}")
    except DownloadError as err:
        discard()
        if err.code in ("locked", "not_ours", "bad_zip", "bad_checksum"):
            raise
        raise _bad_source(err.message) from None
    except Exception as err:  # noqa: BLE001  (zlib.error and the like on a damaged body: never keep that zip)
        discard()
        raise _bad_source(f"it could not be unpacked: {type(err).__name__}") from None
    # Put the pinned files in place one by one; nothing else in the folder is touched, except files that an earlier
    # pinned version of ComfyUI put there and this one no longer has (recorded in the marker, so never a model).
    new = [str(f["path"]) for f in listing["files"]]
    for rel in new:
        dst = app / rel
        g_mkdir(dst.parent, fence)
        g_move(staging / rel, dst, ops, fence)
    m = read_marker(root) or {}
    for rel in sorted(set(m.get("source_files") or []) - set(new)):
        old = app / str(rel)
        if ".." not in Path(str(rel)).parts and old.is_file():
            _retry(old, lambda old=old: guard(old, fence).unlink(), ops)
    job.source_files = new
    discard()


def step_wheels(job: Install, ops: Ops, fence: Fence) -> None:
    root = install_root()
    specs = wheel_specs(root)
    g_mkdir(root / "wheels", fence)
    todo = [s for s in specs if not s.present()]
    job.bytes_total = sum(s.size for s in todo)
    base = 0
    for s in todo:
        guard(s.dest, fence)
        job.text = f"Downloading {s.name}"

        def on_bytes(done: int, total: int, base: int = base) -> None:
            job.bytes_done = base + done

        downloader.fetch(s, job.cancel, on_bytes, ops.opener)
        base += s.size
        job.bytes_done = base


def step_venv(job: Install, ops: Ops, fence: Fence) -> None:
    root = install_root()
    venv = root / ".venv"
    g_rmtree(venv, ops, fence)  # a half-made one is ours: make it again
    guard(venv, fence)
    lp = _log_file("venv")
    code = ops.run([str(uv_exe()), "venv", str(venv), "--python", "3.13"], root, clean_env(), lp, job.cancel, 600)
    if code != 0 or not venv_python(root).is_file():
        raise _program_failed("Making ComfyUI's Python environment", lp)


def step_packages(job: Install, ops: Ops, fence: Fence) -> None:
    root = install_root()
    req = root / "requirements.lock.txt"
    g_write(req, requirements_text(), fence)
    wheels = guard(root / "wheels", fence)
    lp = _log_file("packages")
    job.text = "Installing its Python packages (this takes a few minutes)"
    code = ops.run([str(uv_exe()), "pip", "install", "--python", str(venv_python(root)), "--offline", "--no-index",
                    "--find-links", str(wheels), "--require-hashes", "--no-deps", "--only-binary", ":all:", "-r", str(req)],
                   root, clean_env(), lp, job.cancel, 3600)
    if code != 0:
        raise _program_failed("Installing ComfyUI's Python packages", lp)


def step_packs(job: Install, ops: Ops, fence: Fence) -> None:
    root = install_root()
    cn = root / "ComfyUI" / "custom_nodes"
    g_mkdir(cn, fence)
    wanted = [str(x) for x in (entry().get("packs") or ["skintoken", "vgac"])]
    for p in shipped_packs():
        if p["id"] not in wanted:
            continue
        target = cn / str(p.get("folder") or p["id"])
        guard(target, fence)
        if target.is_dir() and any(target.iterdir()):
            continue
        job.text = f"Adding {p.get('name', p['id'])}"
        if p.get("local"):
            src = downloader.project_dir() / str(p["local"])
            g_rmtree(target, ops, fence)
            shutil.copytree(src, guard(target, fence), ignore=shutil.ignore_patterns("__pycache__"))
            continue
        z = guard(root / "downloads" / f"pack-{p['id']}.zip", fence)
        spec = Spec(id="pack-" + p["id"], name=str(p.get("name", p["id"])), urls=[str(p["zip"])], dest=z, sha256=str(p.get("sha256", "")),
                    page=str(p.get("repo", "")), licence=str(p.get("licence", "")), kind="addon")
        downloader.fetch(spec, job.cancel, None, ops.opener)
        tmp = cn / (target.name + ".vgac-tmp")
        g_rmtree(tmp, ops, fence)
        g_unzip(z, tmp, fence)  # type: ignore[arg-type]
        kids = [k for k in tmp.iterdir() if k.is_dir()]
        g_move(kids[0] if len(kids) == 1 else tmp, target, ops, fence)
        g_rmtree(tmp, ops, fence)


def step_test(job: Install, ops: Ops, fence: Fence, card_expected: bool) -> None:
    root = install_root()
    py = venv_python(root)
    for must in (py, root / "ComfyUI" / "main.py", root / "ComfyUI" / "custom_nodes" / "ComfyUI-SkinToken"):
        if not must.exists():
            raise DownloadError("missing_file", f"{must} is not there any more. An antivirus may have removed it.",
                                "Restore it from your antivirus' quarantine, or press Try again to put it back.")
    lp = _log_file("test")
    g_mkdir(root / "test", fence)
    code = ops.run([str(py), *PY_FLAGS, "-c", TORCH_CODE], root, clean_env(), lp, job.cancel, 300)
    found = re.findall(r"VGAC (\S+) (True|False)", _tail(lp, 20_000))
    if code != 0 or not found:
        raise _program_failed("ComfyUI's Python could not load torch", lp)
    ver, cuda = found[-1]
    if cuda != "True" and card_expected:
        raise DownloadError("no_cuda", f"ComfyUI's Python (torch {ver}) cannot use the graphics card.",
                            "Update the NVIDIA driver (CUDA 13.0 needs a recent one), restart the computer, then press Try again.")
    job.text = "Starting ComfyUI once to load every node"
    code = ops.run([str(py), *PY_FLAGS, "ComfyUI/main.py", "--quick-test-for-ci", "--user-directory", str(guard(root / "test" / "user", fence)),
                    "--output-directory", str(guard(root / "test" / "output", fence))], root, clean_env(), lp, job.cancel, TEST_TIMEOUT)
    failed = re.findall(r"IMPORT FAILED[: ]+(.*)", _tail(lp, 200_000))
    if code != 0 or failed:
        raise DownloadError("test", "ComfyUI was installed but did not load everything" + (f" ({failed[0].strip()})" if failed else "") + ".",
                            f"Press Try again. The details are in {lp}.")


def free_ports(ops: Ops, count: int, taken: set[int]) -> list[int]:
    from .comfy_find import PORTS

    port = int(entry().get("first_port") or 8288)
    out: list[int] = []
    while len(out) < count and port < 65000:
        if port not in taken and port not in PORTS and not ops.port_in_use(port):
            out.append(port)
        port += 1
    return out


def engine_settings(p: hardware_setup.Plan, ports: list[int], extra_yaml: Path | None) -> dict[str, dict[str, Any]]:
    """The whole `engine` block of each of VGAC's own engines, one per engine of the hardware plan. It replaces what
    was there, so nothing from an earlier choice survives (another ComfyUI's model-folders file, input or output
    folder, extra arguments)."""
    root = install_root()
    out: dict[str, dict[str, Any]] = {}
    for eng, port in zip(p.engines, ports):
        e: dict[str, Any] = {"comfy_dir": str(root), "python": str(venv_python(root)), "main": "ComfyUI/main.py", "port": port,
                             "listen": "127.0.0.1", "cuda_device": eng["card"], "user_dir": str(root / "user" / eng["name"]),
                             "output_dir": str(root / "output" / eng["name"]), "extra_args": []}
        if extra_yaml is not None:
            e["extra_model_paths"] = str(extra_yaml)
        out[eng["name"]] = e
    return out


def _outside_fence(value: Any, fence: Fence) -> bool:
    return value is not None and not any(_within(_real(Path(value)), f) for f in fence.foreign)


def step_engines(job: Install, ops: Ops, fence: Fence, p: hardware_setup.Plan) -> None:
    root = install_root()
    extra = root / "shared-models.yaml"
    if job.choice == "A" and job.shared_models:
        g_write(extra, shared_models_yaml(Path(job.shared_models)), fence)
    else:
        if extra.exists():  # choice C: nothing shared any more
            _retry(extra, lambda: guard(extra, fence).unlink(), ops)
        extra = None  # type: ignore[assignment]
    for eng in p.engines:
        g_mkdir(root / "user" / eng["name"], fence)
        g_mkdir(root / "output" / eng["name"], fence)
    g_mkdir(root / "input", fence)
    try:
        saved = config.load_settings(create_library=False)
    except config.ConfigError as err:
        raise DownloadError("settings", f"The app's settings could not be read ({err}).", "Fix or remove local/machine.json, then press Try again.") from None
    names = {e["name"] for e in p.engines}
    ours = {n for n, i in saved.instances.items() if i.engine is not None and _within(_real(Path(i.engine.comfy_dir)), _real(root))}
    taken = {_port(i) for n, i in saved.instances.items() if n not in ours and n not in names}
    ports = [_port(saved.instances[e["name"]]) for e in p.engines if e["name"] in ours]
    if len(ports) != len(p.engines) or any(x is None for x in ports):
        ports = free_ports(ops, len(p.engines), {x for x in taken if x})
    if len(ports) < len(p.engines):
        raise DownloadError("no_port", "There are not enough free ports on this computer for VGAC's engines.",
                            "Close programs that listen on ports 8288 and up, then press Try again.")
    engines = engine_settings(p, ports, extra)
    top: dict[str, Any] = {}
    # The app's input and add-on folders: VGAC's own, unless the person chose folders outside every other ComfyUI.
    if not _outside_fence(saved.comfy_input_dir, fence):
        top["comfy_input_dir"] = str(root / "input")
    if not _outside_fence(saved.loras_dir, fence):
        top["loras_dir"] = str(root / "ComfyUI" / "models" / "loras")
    with setup_settings._lock:
        mpath = config.machine_path()
        if mpath is None:
            raise DownloadError("settings", "This copy of the app has no machine settings file to write to.", "")
        try:
            data = config.merge(config.read_machine(mpath), {"backend": top})
            insts = data.setdefault("backend", {}).setdefault("instances", {})
            for name, e in engines.items():
                inst = insts.get(name) if isinstance(insts.get(name), dict) else {}
                for k in ("process_match", "external_owner", "start_script", "discover"):
                    inst.pop(k, None)  # discover comes back from the hardware plan, without another ComfyUI's ports
                inst["url"] = f"http://127.0.0.1:{e['port']}"
                inst["output_dir"] = e["output_dir"]  # where SkinToken's files are looked for: VGAC's own
                inst["engine"] = e  # replaced whole, never merged
                insts[name] = inst
            config.write_machine(data, mpath)
        except config.ConfigError as err:
            raise DownloadError("settings", f"The engines could not be saved ({err}).", "Press Try again.") from None
    try:
        hardware_setup.accept(hw=ops.hardware())
    except setup_settings.SettingError as err:
        raise DownloadError("settings", str(err), "Press Try again.") from None
    job.engines = [{"name": e["name"], "card": e["card"], "gpu": e["gpu"], "port": port} for e, port in zip(p.engines, ports)]


def _port(inst: Any) -> int | None:
    from .instances import port_of

    e = inst.engine
    return (e.port if e is not None and e.port is not None else None) or port_of(inst.url)


def run(job: Install, ops: Ops) -> None:
    """The whole install in this thread. Each step is skipped when its result is already there."""
    global _fence
    fence = Fence()
    try:
        fence = fence_for(ops, job.shared_models)
        _fence = fence
        job.step, job.text = "check", "Checking this computer"
        p = step_check(job, ops, fence)
        m = claim(install_root(), job.choice, fence)
        m["choice"] = job.choice
        m.setdefault("steps", {})["check"] = "done"
        save_marker(m, fence)
        steps: list[tuple[str, Callable[[], None]]] = [
            ("source", lambda: step_source(job, ops, fence)), ("venv", lambda: step_venv(job, ops, fence)),
            ("wheels", lambda: step_wheels(job, ops, fence)), ("packages", lambda: step_packages(job, ops, fence)),
            ("packs", lambda: step_packs(job, ops, fence)), ("test", lambda: step_test(job, ops, fence, bool(p.engines))),
            ("engines", lambda: step_engines(job, ops, fence, p))]
        for sid, fn in steps:
            if job.cancel.is_set():
                raise Stopped()
            job.step = sid
            job.text = dict(STEPS)[sid]
            if sid in ("wheels", "packages") and _done(m, "packages"):
                continue  # installed already
            if sid == "venv" and _done(m, sid) and venv_python().is_file():
                continue  # made before; a venv that stopped half-way is made again from its start
            m["steps"][sid] = "working"
            save_marker(m, fence)
            fn()
            m["steps"][sid] = "done"
            if sid == "source" and job.source_files:
                m["source_files"] = job.source_files
            if sid in ("source", "venv"):
                # A new environment has none of the packages, and new files need testing again.
                for later in (("wheels", "packages", "test") if sid == "venv" else ("test",)):
                    m["steps"].pop(later, None)
            save_marker(m, fence)
        _cleanup_wheels(ops, fence)
        job.state, job.step, job.text = "done", "", "VGAC's own ComfyUI is installed."
    except Stopped:
        job.state, job.text = "stopped", "Stopped. What was downloaded is kept; Continue carries on where it stopped."
    except DownloadError as e:
        job.state, job.error, job.text = "failed", {"code": e.code, "message": e.message, "fix": e.fix}, e.message
    except Exception as e:  # noqa: BLE001
        log.exception("ComfyUI install failed")
        job.state, job.text = "failed", "Something unexpected went wrong."
        job.error = {"code": "unexpected", "message": f"Something unexpected went wrong ({type(e).__name__}).", "fix": "Press Try again."}


def _cleanup_wheels(ops: Ops, fence: Fence) -> None:
    """After success the downloaded packages, the zip and uv's cache are not needed: a repair downloads again."""
    try:
        for name in ("wheels", "downloads", ".uv-cache"):
            g_rmtree(install_root() / name, ops, fence)
    except DownloadError:
        log.info("could not remove the downloaded packages; they are left in place")


def start(choice: str = "C", shared_models: str = "", ops: Ops | None = None, wait: bool = False) -> Install:
    global _current
    if choice not in ("A", "C"):
        raise DownloadError("choice", "Choose A (share your models) or C (nothing shared).", "")
    if choice == "A":
        if not shared_models or not Path(shared_models).is_dir():
            raise DownloadError("choice", "Choice A needs the models folder of your ComfyUI.", "Choose your ComfyUI in the list first.")
        r, m = _real(install_root()), _real(Path(shared_models))
        if _within(m, r) or _within(r, m):
            raise DownloadError("choice", "That is VGAC's own ComfyUI; choose your own ComfyUI to share its models.", "")
    with _lock:
        if _current is not None and _current.state == "working":
            raise DownloadError("busy", "The install is already running.", "Wait for it, or stop it first.")
        job = Install(choice=choice, shared_models=shared_models, state="working")
        _current = job
        job.thread = threading.Thread(target=run, args=(job, ops or Ops()), daemon=True, name="comfy-install")
        job.thread.start()
    if wait:
        job.thread.join()
    return job


def stop() -> None:
    if _current is not None:
        _current.cancel.set()


def status() -> dict[str, Any]:
    if _current is not None:
        return _current.public()
    root = install_root()
    m = read_marker(root) if root.exists() else None
    left = [s for s in plan(root) if not s["done"]]
    state = "idle" if m is None else ("done" if not left else "unfinished")
    return {"state": state, "step": "", "text": "", "error": None, "choice": (m or {}).get("choice", ""), "bytes_done": 0, "bytes_total": 0,
            "warnings": [], "engines": [], "steps": plan(root)}


def offer() -> dict[str, Any]:
    """What the panel shows before anything starts."""
    e = entry()
    root = install_root()
    total = sum(int(w.get("size") or 0) for w in lock())
    return {"version": e.get("version", ""), "goes_to": str(root), "hosts": ["github.com", "download.pytorch.org", "files.pythonhosted.org"],
            "download_bytes": total + _zip_bytes(e), "needs_bytes": space_needed(root),
            "licence": e.get("licence", ""), "needs_admin": False, "ready": not pins_missing(), "missing": pins_missing(),
            "status": status()}


# --- removing ------------------------------------------------------------------------------------------------------------


def _in_ledger(root: Path) -> bool:
    try:
        items = json.loads(downloader.ledger_path().read_text(encoding="utf-8-sig")).get("items", [])
    except (OSError, ValueError, AttributeError):
        return False
    return any(isinstance(i, dict) and i.get("kind") == "added" and _real(Path(str(i.get("path", "")))) == _real(root) for i in items)


def _ledger_items() -> list[Any]:
    try:
        return list(json.loads(downloader.ledger_path().read_text(encoding="utf-8-sig")).get("items", []))
    except (OSError, ValueError, AttributeError):
        return []


def _ledger_id(root: Path) -> str:
    for i in _ledger_items():
        if isinstance(i, dict) and i.get("kind") == "added" and _real(Path(str(i.get("path", "")))) == _real(root):
            return str(i.get("id") or "")
    return ""


def _write_ledger(items: list[Any]) -> None:
    p = downloader.ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps({"items": items}, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def _stamp_ledger_id(root: Path, install_id: str) -> None:
    """Put this install's id on its ledger entry (the entry that names `root`), so the entry can be found again if the folder is
    renamed or its drive letter changes."""
    with downloader._ledger_lock:
        items = _ledger_items()
        for i in items:
            if isinstance(i, dict) and i.get("kind") == "added" and _real(Path(str(i.get("path", "")))) == _real(root):
                i["id"] = install_id
        _write_ledger(items)


def _ensure_id(root: Path, m: dict[str, Any]) -> None:
    """An install made before ids existed gets one (marker and ledger entry) the first time it is looked at."""
    iid = str(m.get("install_id") or "") or _ledger_id(root)
    if not iid:
        iid = uuid.uuid4().hex
    if m.get("install_id") != iid:
        m["install_id"] = iid
        (root / MARKER).write_text(json.dumps(m, indent=2), encoding="utf-8")
    if _ledger_id(root) != iid:
        _stamp_ledger_id(root, iid)


def _data_folder_is_ours() -> bool:
    """The data folder carries the starter's marker whose id is the one in the settings (the starter's own proof for it)."""
    try:
        mpath = config.machine_path()
        sid = str(((config.read_machine(mpath).get("starter") or {}) if mpath is not None else {}).get("dataFolderId") or "")
        d = json.loads((data_folder() / ".vgac-data.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, config.ConfigError):
        return False
    return bool(sid) and isinstance(d, dict) and d.get("made_by") == MADE_BY and str(d.get("id")) == sid


def _adoptable(root: Path) -> tuple[dict[str, Any] | None, str]:
    """(the ledger entry that can be moved to `root`, "") or (None, why not). The proof is the same shape as the data folder's: the
    install's own id is in its marker AND on a ledger entry whose old place is gone, in a data folder that is VGAC's by its id.
    A marker copied into somebody else's folder has no such entry, and only the folder the settings name (`root`) is ever looked at."""
    m = read_marker(root)
    iid = str((m or {}).get("install_id") or "")
    if not root.is_dir() or m is None or not iid:
        return None, "its install record has no id VGAC can match with its ledger"
    if _real(root) != os.path.normcase(os.path.abspath(str(root))):
        return None, "the install folder is reached through a link"
    if not _data_folder_is_ours():
        return None, "the folder for the big files is not shown to be VGAC's (its marker does not match the settings)"
    hits = [i for i in _ledger_items() if isinstance(i, dict) and i.get("kind") == "added" and str(i.get("id") or "") == iid]
    if len(hits) != 1:
        return None, "the ledger has no single entry with this install's id"
    old = Path(str(hits[0].get("path", "")))
    if old.exists() and (_real(old) != _real(root)) and (read_marker(old) or {}).get("install_id") == iid:
        return None, f"another copy of this install still exists at {old}, so VGAC cannot tell which one is the real one"
    return hits[0], ""


def _adopt_if_proved(root: Path) -> bool:
    with downloader._ledger_lock:
        hit, _ = _adoptable(root)
        if hit is None:
            return False
        items = _ledger_items()
        for i in items:
            if isinstance(i, dict) and i.get("id") == hit.get("id") and i.get("kind") == "added":
                i["path"] = str(root)
        _write_ledger(items)
    return True


def remove(also_models: bool = False, ops: Ops | None = None, supervisor: Any = None) -> dict[str, Any]:
    """Delete VGAC's own ComfyUI: only when its marker and the ledger both say VGAC made it, and only once none of its
    engines runs (the app stops the ones it started; any other is the person's to close). The files go first, then the
    settings that point at them; the models are kept unless asked."""
    global _current
    ops = ops or Ops()
    with _lock:  # no install can start while this runs
        if _current is not None and _current.state == "working":
            raise DownloadError("busy", "The install is running.", "Stop it first.")
        root = install_root()
        if read_marker(root) is None or not _in_ledger(root):
            raise DownloadError("not_ours", "VGAC did not install a ComfyUI there, so it removes nothing.", "")
        fence = fence_for(ops)
        saved = config.load_settings(create_library=False)
        r = _real(root)
        mine = [n for n, i in saved.instances.items() if i.engine is not None and _within(_real(Path(i.engine.comfy_dir)), r)]
        for n in mine:
            if supervisor is not None:
                supervisor.stop_if_owned(n)
        ports = [x for x in (_port(saved.instances[n]) for n in mine) if x]
        for _ in range(10):
            busy = [x for x in ports if ops.port_in_use(x)]
            if not busy:
                break
            ops.sleep(0.5)
        else:
            raise DownloadError("engine_running", f"VGAC's ComfyUI is still running on port {busy[0]}, and the app did not start it, so it "
                                "cannot stop it. Nothing was removed.", "Close that ComfyUI window or program, then press Remove again.")
        _current = None  # the finished job no longer describes what is there
        m = read_marker(root) or {}
        m.update({"made_by": MADE_BY, "steps": {}})  # if a locked file stops the removal half way, nothing says done
        save_marker(m, fence)
        kept = ""
        for child in list(root.iterdir()):
            if child.name == MARKER:
                continue
            if child.name == "ComfyUI" and not also_models and (child / "models").is_dir():
                for sub in list(child.iterdir()):
                    if sub.name != "models":
                        (g_rmtree(sub, ops, fence) if sub.is_dir() else _retry(sub, lambda s=sub: guard(s, fence).unlink(), ops))
                kept = str(child / "models")
                continue
            g_rmtree(child, ops, fence) if child.is_dir() else _retry(child, lambda c=child: guard(c, fence).unlink(), ops)
        if not kept:
            _retry(root, lambda: (guard(root / MARKER, fence).unlink(), root.rmdir()), ops)
        # Only now that the files are gone: the settings that pointed at them.
        changes: dict[str, Any] = {"instances": {n: {"engine": None} for n in mine}} if mine else {}
        for key in ("comfy_input_dir", "loras_dir"):
            v = getattr(saved, key)
            if v is not None and _within(_real(Path(v)), r):
                changes[key] = None
        if changes:
            with setup_settings._lock:
                mpath = config.machine_path()
                if mpath is not None:
                    config.update_machine({"backend": changes}, mpath)
    return {"removed": True, "kept_models": kept, "engines_cleared": mine,
            "message": "VGAC's own ComfyUI was removed." + (f" Its models were kept in {kept}." if kept else "")}


# --- the command line (the starter's Move and Remove call these; the backend need not be running) -------------------------
#
# `python -m vgac.comfy_install <command>` prints ONE json line on stdout and exits 0, or prints {"ok": false, "code", "reason"}
# and exits non-zero (2 for a refusal). The commands change nothing the guard does not allow; the only deleting is remove().
#   check                    is there a ComfyUI VGAC made here (marker and ledger)? what would remove() keep? (code "adoptable":
#                            it is VGAC's by its id but its folder was renamed or changed drive letter: run adopt)
#   adopt                    point the ledger entry that carries this install's id at the folder the settings name (needs the data
#                            folder's id to match too; a copied marker in somebody else's folder has no such entry)
#   remove [--models]        remove(): the same proof, models kept unless --models
#   retarget <old> <new>     the data folder was moved from <old> to <new> (the settings already name <new>): the settings that
#                            pointed into <old>/comfyui now point into the same places in <new>/comfyui
#   rebuild-venv             make its Python environment again (the old one holds the old place's paths)


def _require_own(root: Path) -> None:
    if not root.exists() or read_marker(root) is None or not _in_ledger(root):
        if root.exists() and _adoptable(root)[0] is not None:
            raise DownloadError("adoptable", "This ComfyUI is VGAC's (its id matches), but the ledger still names its old place.", "")
        raise DownloadError("not_ours", "VGAC did not install a ComfyUI in " + str(root) + ", so nothing is done there.", "")


def _models_dir(root: Path) -> Path:
    return root / "ComfyUI" / "models"


def adopt() -> dict[str, Any]:
    """The install folder was renamed or its drive letter changed: the ledger entry with this install's id is pointed at it."""
    root = install_root()
    if _in_ledger(root) and read_marker(root) is not None:
        return {"adopted": False, "already": True}
    if not _adopt_if_proved(root):
        raise DownloadError("not_ours", f"VGAC cannot show that it made the ComfyUI in {root} ({_adoptable(root)[1]}), so nothing is changed.", "")
    return {"adopted": True}


def check() -> dict[str, Any]:
    root = install_root()
    _require_own(root)
    _ensure_id(root, read_marker(root) or {})
    models = _models_dir(root)
    return {"root": str(root), "keeps": [str(models)] if models.is_dir() else []}


def retarget(old_data: str, new_data: str, ops: Ops | None = None) -> dict[str, Any]:
    ops = ops or Ops()
    root = install_root()
    if _real(Path(new_data)) != _real(data_folder()):
        raise DownloadError("not_current", f"The settings do not name {new_data} as the data folder, so nothing was changed.", "")
    _require_own(root)
    old_root = os.path.abspath(str(Path(old_data) / "comfyui"))
    if os.path.normcase(old_root) == os.path.normcase(os.path.abspath(str(root))):
        return {"changed": 0}
    fence = fence_for(ops)

    def moved(v: Any) -> Any:
        if not isinstance(v, str) or not v:
            return v
        a = os.path.abspath(v)
        if os.path.normcase(a) == os.path.normcase(old_root) or os.path.normcase(a).startswith(os.path.normcase(old_root) + os.sep):
            return str(guard(Path(str(root) + a[len(old_root):]), fence))  # refused unless the new place is inside VGAC's own folder
        return v

    with setup_settings._lock:
        mpath = config.machine_path()
        if mpath is None:
            raise DownloadError("settings", "This copy of the app has no machine settings file.", "")
        try:
            backend = config.read_machine(mpath).get("backend") or {}
            change: dict[str, Any] = {}
            n = 0
            for key in ("comfy_input_dir", "loras_dir"):
                if moved(backend.get(key)) != backend.get(key):
                    change[key] = moved(backend.get(key))
                    n += 1
            insts: dict[str, Any] = {}
            for name, inst in (backend.get("instances") or {}).items():
                if not isinstance(inst, dict):
                    continue
                one: dict[str, Any] = {}
                if moved(inst.get("output_dir")) != inst.get("output_dir"):
                    one["output_dir"] = moved(inst.get("output_dir"))
                    n += 1
                eng = inst.get("engine")
                if isinstance(eng, dict):
                    e2 = {k: moved(eng.get(k)) for k in ("comfy_dir", "python", "user_dir", "output_dir", "extra_model_paths") if k in eng}
                    e2 = {k: v for k, v in e2.items() if v != eng.get(k)}
                    if e2:
                        one["engine"] = e2
                        n += len(e2)
                if one:
                    insts[name] = one
            if insts:
                change["instances"] = insts
            if change:
                config.update_machine({"backend": change}, mpath)
        except config.ConfigError as err:
            raise DownloadError("settings", f"The settings could not be updated ({err}).", "Nothing was changed.") from None
    return {"changed": n}


def rebuild_venv(ops: Ops | None = None) -> dict[str, Any]:
    ops = ops or Ops()
    root = install_root()
    _require_own(root)
    fence = fence_for(ops)
    with _lock:
        if _current is not None and _current.state == "working":
            raise DownloadError("busy", "The install is running.", "Stop it first.")
        job = Install(state="working")
        m = read_marker(root) or {}
        steps = m.setdefault("steps", {})
        for s in ("venv", "wheels", "packages"):
            steps.pop(s, None)  # until all three are done again, nothing says the environment is finished
        save_marker(m, fence)
        step_venv(job, ops, fence)
        steps["venv"] = "done"
        save_marker(m, fence)
        step_wheels(job, ops, fence)
        step_packages(job, ops, fence)
        steps["wheels"] = steps["packages"] = "done"
        save_marker(m, fence)
        _cleanup_wheels(ops, fence)
    return {"rebuilt": True}


def main(argv: list[str], ops: Ops | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    try:
        cmd = argv[0] if argv else ""
        if cmd == "check" and len(argv) == 1:
            r = check()
        elif cmd == "remove" and set(argv[1:]) <= {"--models"}:
            r = remove(also_models="--models" in argv[1:], ops=ops)
        elif cmd == "adopt" and len(argv) == 1:
            r = adopt()
        elif cmd == "retarget" and len(argv) == 3:
            r = retarget(argv[1], argv[2], ops)
        elif cmd == "rebuild-venv" and len(argv) == 1:
            r = rebuild_venv(ops)
        else:
            print(json.dumps({"ok": False, "code": "usage", "reason": "Commands: check, adopt, remove [--models], retarget <old> <new>, rebuild-venv."}))
            return 2
    except DownloadError as e:
        print(json.dumps({"ok": False, "code": e.code, "reason": f"{e.message} {e.fix}".strip(), "adoptable": e.code == "adoptable"}))
        return 2
    except config.ConfigError as e:
        print(json.dumps({"ok": False, "code": "settings", "reason": f"The app's settings could not be read ({e})."}))
        return 2
    except Exception as e:  # noqa: BLE001
        log.exception("comfy_install command failed")
        print(json.dumps({"ok": False, "code": "unexpected", "reason": f"Something unexpected went wrong ({type(e).__name__})."}))
        return 1
    print(json.dumps({"ok": True, **r}))
    return 0


# --- starting the engines in the running app ----------------------------------------------------------------------------


async def start_engines(supervisor: Any, saved: config.Settings | None = None) -> list[dict[str, str]]:
    """Point the running app's engines at what the install saved and start each one through the supervisor (it starts
    only processes it then owns). An engine that is busy, or new to the running app, is left for the next start."""
    saved = saved or config.load_settings(create_library=False)
    root = _real(install_root())
    out = []
    for name, inst in saved.instances.items():
        if inst.engine is None or not _within(_real(Path(inst.engine.comfy_dir)), root):
            continue
        live = supervisor.settings.instances.get(name)
        if live is None or supervisor.in_use(name):
            out.append({"name": name, "state": "restart", "text": "It starts the next time the app starts."})
            continue
        live.url, live.engine, live.discover = inst.url, inst.engine, inst.discover
        live.process_match, live.external_owner, live.start_script = None, "", None
        client = supervisor.clients.get(name)
        if client is not None and hasattr(client, "set_base_url"):
            client.set_base_url(inst.url)
        supervisor.found.pop(name, None)
        supervisor.conflicts.pop(name, None)
        try:
            await supervisor.ensure_running(name)
            out.append({"name": name, "state": "running", "text": f"Running on port {_port(inst)}."})
        except Exception as err:  # noqa: BLE001  (StartFailed and friends: shown, not raised)
            out.append({"name": name, "state": "failed", "text": str(err)})
    return out


# --- HTTP ----------------------------------------------------------------------------------------------------------------

router = APIRouter(prefix="/api/comfy/install", tags=["setup"])


class StartIn(BaseModel):
    choice: str = "C"
    id: str | None = None  # choice A: the ComfyUI found by /api/comfy whose models are shared


class RemoveIn(BaseModel):
    confirm: bool = False
    also_models: bool = False


def _bad(e: DownloadError) -> HTTPException:
    return HTTPException(400, f"{e.message} {e.fix}".strip())


@router.get("")
def get_install() -> dict[str, Any]:
    return offer()


@router.post("/start")
def post_start(body: StartIn) -> dict[str, Any]:
    shared = ""
    try:
        if body.choice == "A":
            from .comfy_find import find_by_id

            if not body.id:
                raise DownloadError("choice", "Choice A needs your ComfyUI chosen in the list.", "")
            shared = str(find_by_id(body.id)["models"])
        return start(body.choice, shared).public()
    except DownloadError as e:
        raise _bad(e) from None


@router.post("/stop")
def post_stop() -> dict[str, Any]:
    stop()
    return status()


@router.get("/status")
def get_status() -> dict[str, Any]:
    return status()


@router.post("/engines")
async def post_engines() -> dict[str, Any]:
    from .jobs import active_queue

    q = active_queue()
    if q is None or q.supervisor is None:
        return {"engines": [], "message": "The engines start the next time the app starts."}
    return {"engines": await start_engines(q.supervisor)}


@router.post("/remove")
def post_remove(body: RemoveIn) -> dict[str, Any]:
    if not body.confirm:
        raise HTTPException(400, "Nothing is removed until you confirm.")
    from .jobs import active_queue

    q = active_queue()
    try:
        return remove(body.also_models, supervisor=q.supervisor if q else None)
    except DownloadError as e:
        raise _bad(e) from None


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
