"""Find, ask, link or install the outside tools: Blender (found, linked or fetched) and Unity (found, linked or explained).

The same flow for each tool, shown as one panel in the setup card:
  look      silently and read-only: the saved setting, the Windows records, the usual folders, PATH, running programs
  choose    every copy found is checked (it exists, it starts, which version); the best one is marked, the reason shown
  link      "Use this" or a file the person points at: checked, then saved through setup_settings (one validator)
  get       Blender only: the official portable zip into local/tools, checked against Blender's own SHA-256 file,
            never as administrator. Unity cannot be installed for the person (a Unity ID, a sign-in and a licence
            activation are the person's own acts), so it is guided instead.

Nothing here changes a tool that is already installed. All the places it looks, and the network, can be replaced by
tests (the `Env` fields and the downloader's `opener`).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import downloader, preflight, setup_settings
from .config import get_settings
from .downloader import DownloadError, Job, Spec

log = logging.getLogger("vgac.tools_setup")

BLENDER_TESTED = (5, 2, 3)
BLENDER_GONE = ("Open blender.org in your own browser, download the Windows zip (portable), and choose it with \"I downloaded the zip\". "
                "The app does not try to get around a server that refuses it.")
UNITY_TESTED = "6000.4.3f1"
UNITY_PAGE = "https://unity.com/download"
UNITY_TERMS = "https://unity.com/products/unity-personal"
UNITY_LIMIT = ("Unity Personal is free for individuals and organisations with under 200,000 US dollars of revenue or "
               "funding in the last 12 months. Check Unity's own page for the current rule.")


def tools_dir() -> Path:
    return downloader.project_dir() / "local" / "tools"


# --- where to look ---------------------------------------------------------------------------------------------------


def _registry_install_dirs(publisher: str) -> list[str]:
    """InstallLocation of every uninstall entry whose publisher starts with `publisher` (empty off Windows)."""
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return []
    found: list[str] = []
    for hive, sub in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
                      (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")):
        try:
            with winreg.OpenKey(hive, sub) as root:
                count = winreg.QueryInfoKey(root)[0]
                for i in range(count):
                    try:
                        with winreg.OpenKey(root, winreg.EnumKey(root, i)) as k:
                            pub = str(winreg.QueryValueEx(k, "Publisher")[0])
                            if pub.lower().startswith(publisher.lower()):
                                found.append(str(winreg.QueryValueEx(k, "InstallLocation")[0]))
                    except OSError:
                        continue
        except OSError:
            continue
    return [f for f in found if f]


def _running_paths(image: str) -> list[str]:
    """Full paths of running programs named `image` (for example blender), by PowerShell; empty when it cannot ask."""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", f"(Get-Process {image} -ErrorAction SilentlyContinue).Path"],
                             capture_output=True, text=True, timeout=10, check=False,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


@dataclass
class Env:
    """Everything the finders read from the machine. Tests replace the fields."""

    program_files: list[Path] = field(default_factory=lambda: [Path(p) for k in ("ProgramFiles", "ProgramFiles(x86)") if (p := os.environ.get(k))])
    appdata: Path | None = field(default_factory=lambda: Path(os.environ["APPDATA"]) if os.environ.get("APPDATA") else None)
    which: Callable[[str], str | None] = shutil.which
    registry: Callable[[str], list[str]] = _registry_install_dirs
    running: Callable[[str], list[str]] = _running_paths


# --- Blender -----------------------------------------------------------------------------------------------------------


def _exes_under(folder: Path) -> list[Path]:
    try:
        return [folder / "blender.exe"] if (folder / "blender.exe").is_file() else []
    except OSError:
        return []


def blender_locations(env: Env, current: str = "") -> list[tuple[Path, str]]:
    """(blender.exe, where it was found) for every copy that can be seen, without duplicates."""
    out: list[tuple[Path, str]] = []
    if current:
        out.append((Path(current), "your saved setting"))
    for d in env.registry("Blender Foundation"):
        out += [(p, "Windows list of installed programs") for p in _exes_under(Path(d))]
    for pf in env.program_files:
        base = pf / "Blender Foundation"
        try:
            for d in sorted(base.glob("Blender *")):
                out += [(p, "Program Files") for p in _exes_under(d)]
        except OSError:
            pass
        for sub in ("Steam/steamapps/common/Blender",):
            out += [(p, "Steam") for p in _exes_under(pf / sub)]
        try:
            for d in sorted((pf / "WindowsApps").glob("BlenderFoundation.Blender*")):
                out += [(p, "Microsoft Store") for p in _exes_under(d)]
        except OSError:
            pass
    w = env.which("blender")
    if w:
        out.append((Path(w), "your PATH"))
    for r in env.running("blender"):
        out.append((Path(r), "running now"))
    try:
        for d in sorted(tools_dir().glob("blender-*")):
            out += [(p, "VGAC's own tools folder") for p in _exes_under(d)]
    except OSError:
        pass
    seen: set[str] = set()
    uniq: list[tuple[Path, str]] = []
    for p, why in out:
        key = os.path.normcase(str(p))
        if key not in seen and p.is_file():
            seen.add(key)
            uniq.append((p, why))
    return uniq


def _vtext(v: tuple[int, ...] | None) -> str:
    return ".".join(str(x) for x in v) if v else ""


def check_blender_copy(exe: Path, runner: Callable[[Path], str] | None = None) -> dict[str, Any]:
    kwargs = {"runner": runner} if runner else {}
    ver = preflight.blender_version(exe, **kwargs)
    item: dict[str, Any] = {"path": str(exe), "version": _vtext(ver), "usable": False, "reason": "", "tested": False}
    if ver is None:
        item["reason"] = "It did not say which version of Blender it is. Check that it starts."
    elif ver[:2] < preflight.BLENDER_MIN:
        item["reason"] = f"Blender {_vtext(ver)} is too old: the app needs 5.0 or newer."
    else:
        item["usable"] = True
        item["tested"] = ver == BLENDER_TESTED
        item["reason"] = ("The version VGAC was tested with." if item["tested"] else "A newer or different 5.x; not tested by VGAC.")
    return item


def find_blender(env: Env | None = None, runner: Callable[[Path], str] | None = None, current: str | None = None) -> list[dict[str, Any]]:
    """Every copy of Blender found, each checked; the best usable one is first and marked `best`."""
    env = env or Env()
    if current is None:
        current = get_settings().blender_exe or ""
    cands = []
    for exe, where in blender_locations(env, str(current or "")):
        c = check_blender_copy(exe, runner)
        c["source"] = where
        cands.append(c)

    def rank(c: dict[str, Any]) -> tuple:
        ver = tuple(int(x) for x in c["version"].split(".")) if c["version"] else ()
        return (not c["usable"], not c["tested"], tuple(-x for x in ver))

    cands.sort(key=rank)
    if cands and cands[0]["usable"]:
        cands[0]["best"] = True
    return cands


def blender_offer() -> dict[str, Any]:
    cat = downloader.catalogue().get("blender") or {}
    zip_url = str(cat.get("zip") or "")
    host = re.sub(r"^https?://([^/]+)/.*$", r"\1", zip_url) if zip_url else ""
    return {"available": bool(zip_url), "version": cat.get("version", ""), "from": host, "page": cat.get("page", ""),
            "size_mb": cat.get("size_mb", 0), "licence": cat.get("licence", "GPL"),
            "goes_to": str(tools_dir()), "needs_admin": False,
            "unconfirmed": cat.get("unconfirmed", "")}


# The sha file reads "<hash>  <file name>" per line.
_SHA_LINE = re.compile(r"^([0-9a-fA-F]{64})\s+\*?(\S+)\s*$")


def parse_sha_file(text: str, name: str) -> str:
    for line in text.splitlines():
        m = _SHA_LINE.match(line.strip())
        if m and m.group(2).split("/")[-1] == name:
            return m.group(1).lower()
    return ""


BLENDER_MAIN = "https://download.blender.org/release/"


def _with_mirrors(url: str, cat: dict[str, Any]) -> list[str]:
    """The official address first, then the same path on each of Blender's own mirrors. Never a different kind of address."""
    urls = [url]
    if url.startswith(BLENDER_MAIN):
        urls += [m.rstrip("/") + "/" + url[len(BLENDER_MAIN):] for m in cat.get("mirrors") or [] if isinstance(m, str) and m.startswith("https://")]
    return urls


def _read_text(url: str, opener: Callable[[str, int], Any] | None, cat: dict[str, Any] | None = None) -> str:
    box: list[str] = []

    def attempt(u: str) -> None:
        with (opener or downloader.default_open)(u, 0) as r:
            box.append(r.read(200_000).decode("utf-8", errors="replace"))

    page = str(downloader.catalogue().get("blender", {}).get("page", ""))
    downloader.try_each(_with_mirrors(url, cat or {}), attempt, "Blender's checksum file", page, BLENDER_GONE, tools_dir())
    return box[0]


def safe_unzip(zip_path: Path, into: Path) -> None:
    """Unpack, refusing any entry that would land outside `into`."""
    into.mkdir(parents=True, exist_ok=True)
    root = into.resolve()
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            target = (into / info.filename).resolve()
            if root != target and root not in target.parents:
                raise DownloadError("bad_zip", "That zip has a file that would land outside its folder, so it was not opened.",
                                    "Download it again from blender.org.")
        z.extractall(into)


def _blender_zip_name(cat: dict[str, Any]) -> str:
    return str(cat.get("zip", "")).rsplit("/", 1)[-1]


def _finish_blender(folder: Path, runner: Callable[[Path], str] | None) -> dict[str, Any]:
    exe = folder / "blender.exe"
    if not exe.is_file():
        raise DownloadError("bad_zip", "The zip did not hold blender.exe.", "Download Blender's Windows zip (portable) again.")
    c = check_blender_copy(exe, runner)
    if not c["usable"]:
        raise DownloadError("bad_zip", c["reason"], "Download the Windows zip from blender.org again.")
    result = setup_settings.set_setting("blender_exe", str(exe))
    downloader.record("Blender " + c["version"], folder, "added")
    return result


def blender_specs(opener: Callable[[str, int], Any] | None) -> list[Spec]:
    cat = downloader.catalogue().get("blender") or {}
    url = str(cat.get("zip") or "")
    if not url:
        raise DownloadError("no_link", "This copy of the app has no link for Blender.", "Choose the zip yourself from blender.org.")
    name = _blender_zip_name(cat)
    sha = ""
    if cat.get("sha256_file"):
        sha = parse_sha_file(_read_text(str(cat["sha256_file"]), opener, cat), name)
    if not sha:
        raise DownloadError("no_checksum", "Blender's checksum for this file could not be read, so nothing was downloaded.",
                            "Open blender.org, download the Windows zip yourself, and choose it with \"I downloaded the zip\".")
    need = int(cat.get("size_mb", 400)) * 3 * downloader.MB  # the zip, then the unpacked copy, with room to spare
    where = tools_dir()
    where.mkdir(parents=True, exist_ok=True)
    if downloader.free_bytes(where) < need + downloader.SPACE_MARGIN:
        raise downloader.no_space(where, need + downloader.SPACE_MARGIN)
    return [Spec(id="blender", name=f"Blender {cat.get('version', '')}", urls=_with_mirrors(url, cat), dest=tools_dir() / "downloads" / name,
                 size=0, sha256=sha, page=str(cat.get("page", "")), kind="tool", gone_fix=BLENDER_GONE)]


def start_blender_install(opener: Callable[[str, int], Any] | None = None, runner: Callable[[Path], str] | None = None,
                          wait: bool = False) -> Job:
    cat = downloader.catalogue().get("blender") or {}
    folder = tools_dir() / str(cat.get("folder") or "blender")

    def prepare(job: Job) -> list[Spec]:
        if (folder / "blender.exe").is_file():
            return []
        return blender_specs(opener)

    def after(job: Job) -> None:
        zp = tools_dir() / "downloads" / _blender_zip_name(cat)
        if not (folder / "blender.exe").is_file():
            job.text = "Unpacking Blender"
            safe_unzip(zp, tools_dir())
        _finish_blender(folder, runner)
        try:
            zp.unlink()
        except OSError:
            pass

    return downloader.start_job("tool-blender", [], opener=opener, wait=wait, prepare=prepare, after=after)


def use_blender_zip(zip_path: str, opener: Callable[[str, int], Any] | None = None, runner: Callable[[Path], str] | None = None) -> dict[str, Any]:
    """A zip the person downloaded themselves (for when blender.org refuses the app). Checked against Blender's checksum
    file when that can be read; refused when it can be read and does not match."""
    p = Path(zip_path.strip().strip('"'))
    cat = downloader.catalogue().get("blender") or {}
    if not p.is_file() or not zipfile.is_zipfile(p):
        raise DownloadError("bad_zip", f"{p} is not a zip file.", "Choose the Blender zip you downloaded.")
    checked = False
    try:
        want = parse_sha_file(_read_text(str(cat.get("sha256_file", "")), opener, cat), p.name) if cat.get("sha256_file") else ""
    except DownloadError:
        want = ""
    if want:
        if downloader.sha256_of(p) != want:
            raise DownloadError("bad_checksum", "That zip does not match Blender's published checksum, so it was not used.",
                                "Download it again from blender.org.")
        checked = True
    with zipfile.ZipFile(p) as z:
        tops = {n.split("/")[0] for n in z.namelist() if n}
    safe_unzip(p, tools_dir())
    folders = [t for t in tops if (tools_dir() / t / "blender.exe").is_file()]
    if not folders:
        raise DownloadError("bad_zip", "That zip does not hold Blender (no blender.exe).", "Choose Blender's Windows portable zip.")
    result = _finish_blender(tools_dir() / folders[0], runner)
    result["checksum_checked"] = checked
    return result


# --- Unity -----------------------------------------------------------------------------------------------------------


def _unity_version_of(exe: Path) -> str:
    # <editors>/<version>/Editor/Unity.exe
    return exe.parent.parent.name if exe.parent.name.lower() == "editor" else ""


def unity_locations(env: Env, current: str = "") -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    if current:
        out.append((Path(current), "your saved setting"))
    roots: list[tuple[Path, str]] = [(pf / "Unity" / "Hub" / "Editor", "Unity Hub's usual folder") for pf in env.program_files]
    if env.appdata:
        f = env.appdata / "UnityHub" / "secondaryInstallPath.json"
        try:
            import json

            text = json.loads(f.read_text(encoding="utf-8-sig"))
            if isinstance(text, str) and text.strip():
                roots.append((Path(text.strip()), "the folder Unity Hub was told to use"))
        except (OSError, ValueError):
            pass
    for r, why in roots:
        try:
            for d in sorted(r.glob("*")):
                exe = d / "Editor" / "Unity.exe"
                if exe.is_file():
                    out.append((exe, why))
        except OSError:
            continue
    seen: set[str] = set()
    uniq = []
    for p, why in out:
        key = os.path.normcase(str(p))
        if key not in seen and p.is_file():
            seen.add(key)
            uniq.append((p, why))
    return uniq


def find_unity(env: Env | None = None, current: str | None = None) -> list[dict[str, Any]]:
    env = env or Env()
    if current is None:
        current = get_settings().unity_exe or ""
    cands = []
    for exe, why in unity_locations(env, str(current or "")):
        ver = _unity_version_of(exe)
        tested = ver == UNITY_TESTED
        six = ver.startswith("6000.")
        cands.append({"path": str(exe), "version": ver, "source": why, "tested": tested, "usable": True,
                      "reason": ("The version VGAC's test project was made with." if tested else
                                 "Another 6000 version: untested; Unity will convert the test project." if six else
                                 "Not a Unity 6 editor: the test project needs 6000.x." if ver else
                                 "Its version could not be read from the folder name."),
                      })
        if ver and not six:
            cands[-1]["usable"] = False
    cands.sort(key=lambda c: (not c["usable"], not c["tested"], c["version"]))
    if cands and cands[0]["usable"]:
        cands[0]["best"] = True
    return cands


def unity_guide(env: Env | None = None) -> dict[str, Any]:
    env = env or Env()
    hub = any((pf / "Unity Hub" / "Unity Hub.exe").is_file() for pf in env.program_files)
    return {"optional": True, "hub_installed": hub, "download_page": UNITY_PAGE, "terms_page": UNITY_TERMS,
            "licence_line": UNITY_LIMIT, "wanted_version": UNITY_TESTED,
            "loses_without": "Only the test import and the ready-made Unity prefab. FBX and GLB are made either way.",
            "cannot_install": ("VGAC cannot install Unity for you: it needs a Unity ID, signing in to Unity Hub and "
                               "activating a licence, and those are yours to do."),
            "steps": ["Install Unity Hub from unity.com and sign in.",
                      f"In the Hub, install the editor {UNITY_TESTED} (another Unity 6 version also works, untested).",
                      "Come back here and press Check again."]}


# --- HTTP ------------------------------------------------------------------------------------------------------------

router = APIRouter()


class PathIn(BaseModel):
    path: str


def _problem(e: DownloadError) -> HTTPException:
    return HTTPException(400, f"{e.message} {e.fix}".strip())


@router.get("/api/tools/blender")
def get_blender() -> dict[str, Any]:
    s = get_settings()
    job = downloader.job_status("tool-blender")
    return {"current": str(s.blender_exe or ""), "found": find_blender(), "offer": blender_offer(), "install": job}


@router.post("/api/tools/blender/use")
def post_blender_use(body: PathIn) -> dict[str, Any]:
    try:
        return setup_settings.set_setting("blender_exe", body.path)
    except setup_settings.SettingError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/api/tools/blender/install")
def post_blender_install() -> dict[str, Any]:
    try:
        return start_blender_install().public()
    except DownloadError as e:
        raise _problem(e) from None


@router.post("/api/tools/blender/stop")
def post_blender_stop() -> dict[str, Any]:
    downloader.stop_job("tool-blender")
    return {"install": downloader.job_status("tool-blender")}


@router.post("/api/tools/blender/zip")
def post_blender_zip(body: PathIn) -> dict[str, Any]:
    try:
        return use_blender_zip(body.path)
    except DownloadError as e:
        raise _problem(e) from None
    except setup_settings.SettingError as e:
        raise HTTPException(400, str(e)) from None


@router.get("/api/tools/unity")
def get_unity() -> dict[str, Any]:
    s = get_settings()
    return {"current": str(s.unity_exe or ""), "found": find_unity(), "guide": unity_guide()}


@router.post("/api/tools/unity/use")
def post_unity_use(body: PathIn) -> dict[str, Any]:
    try:
        return setup_settings.set_setting("unity_exe", body.path)
    except setup_settings.SettingError as e:
        raise HTTPException(400, str(e)) from None
