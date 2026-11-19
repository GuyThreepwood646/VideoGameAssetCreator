"""The one downloader: resume, checksums, space checks, mirrors, stop-and-continue, and the install ledger.

Everything that fetches a file for the app uses this: the hair and animation packs (packs.py hands it the transfer and the
error wording), and the models, add-ons and node packs listed in setup/downloads.json (the same file setup/Setup-Extras.ps1
reads, plus the person's own setup/downloads.local.json).

    fetch(spec, cancel, on_bytes)     one file: skip if there, check room, resume the .part, check size and SHA-256, swap in
    Job / start_job(...)              a list of files in a background thread, with progress, stop and continue
    catalogue() / specs_for(...)      the list, read from downloads.json (+ downloads.local.json), with where each file goes
    record(...)                       the ledger, local/installed.json (what VGAC added), shared with VGAC.exe

A stop keeps the `.part` file: the next start carries on from it. A file whose checksum is wrong is deleted
and never used. Items that need the person to accept a licence first (`optin`) are refused unless the caller passes their
id in `accepted`, which only a panel that showed the licence page and got the person's own yes may do.
Every failure is a DownloadError: `message` says what happened, `fix` what to do.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from . import __version__

from . import state_lock as state_lock_mod
from .config import PROJECT_DIR, get_settings
from .state_lock import state_lock

log = logging.getLogger("vgac.downloader")

GB = 1024**3
MB = 1024**2
DOWNLOAD_TIMEOUT = 30
CHUNK = 1024 * 1024
USER_AGENT = f"VGAC-setup/{__version__}"  # an honest client name, never a browser's; a server that refuses it is not worked around
SPACE_MARGIN = 200 * MB  # left free after a download


class DownloadError(Exception):
    """A failure the person can read: `message` says what happened, `fix` what to do."""

    def __init__(self, code: str, message: str, fix: str = ""):
        super().__init__(message)
        self.code, self.message, self.fix = code, message, fix


class Stopped(Exception):
    """The person (or the app closing) asked the transfer to stop. Half-finished files are kept."""


def fmt_size(n: float) -> str:
    return f"{n / GB:.1f} GB" if n >= GB else f"{max(1, round(n / MB))} MB"


def project_dir() -> Path:  # a function so tests can point it at a scratch folder
    return PROJECT_DIR


def default_open(url: str, start: int):  # the one place the network is touched; tests replace the `opener`
    headers = {"User-Agent": USER_AGENT}
    if start:
        headers["Range"] = f"bytes={start}-"
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=DOWNLOAD_TIMEOUT)


def no_space(where: Path, need: int) -> DownloadError:
    probe = where
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        free = shutil.disk_usage(probe).free
    except OSError:
        free = 0
    if need:
        msg = f"There is not enough free space on this drive: about {fmt_size(need)} is needed and {fmt_size(free)} is free."
    else:
        msg = "The drive ran out of space."
    return DownloadError("no_space", msg, "Free some space (empty the recycle bin, remove files you do not need), then press Try again.")


def free_bytes(where: Path) -> int:
    probe = where
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return 0


# --- one transfer -------------------------------------------------------------------------------------------------------


def stream_to_part(url: str, part: Path, name: str, cancel: threading.Event, on_bytes: Callable[[int], None], opener: Callable[[str, int], Any]) -> None:
    """Write `url` into `part`, carrying on from what is already there when the site allows it. Raises Stopped (the part is
    kept) or DownloadError('short') when fewer bytes came than the site promised."""
    part.parent.mkdir(parents=True, exist_ok=True)
    have = part.stat().st_size if part.exists() else 0
    resp = opener(url, have)
    try:
        status = getattr(resp, "status", 200)
        length = int(resp.headers.get("Content-Length") or 0)
        if have and status != 206:
            have = 0  # the site ignored the range: start over
        total = have + length if length else 0
        with open(part, "ab" if have else "wb") as f:
            done = have
            on_bytes(done)
            while True:
                if cancel.is_set():
                    raise Stopped()
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                on_bytes(done)
    finally:
        try:
            resp.close()
        except Exception:  # noqa: BLE001
            pass
    if total and part.stat().st_size != total:
        raise DownloadError("short", f"{name} arrived incomplete.", "Press Try again; the download carries on where it stopped.")


def classify(exc: BaseException, name: str, page: str, gone_fix: str = "", where: Path | None = None) -> DownloadError:
    """The plain DownloadError for a network or disk failure during a transfer."""
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (404, 410, 403):
            return DownloadError("link_gone", f"The download link for {name} no longer works (the website answered {exc.code}); it may have moved or closed.",
                                 gone_fix or (f"Get it yourself from {page} and choose the file." if page else "Get it yourself from its official page."))
        return DownloadError("server", f"The website sent an error ({exc.code}) while giving {name}.", "Try again in a few minutes.")
    if isinstance(exc, (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError)):
        log.info("download of %s failed: %s", name, exc)
        return DownloadError("no_internet", f"The app could not reach the internet to download {name}.",
                             "Check your connection and press Try again. A half-finished download is kept and carries on where it stopped.")
    if isinstance(exc, OSError):
        if getattr(exc, "errno", None) == 28:
            return no_space(where or Path("."), 0)
        return DownloadError("write", f"The file could not be saved ({exc.strerror or exc}).", "Check that the disk is not full or locked, then press Try again.")
    return DownloadError("unexpected", f"Something unexpected went wrong ({type(exc).__name__}).", "Press Try again.")


def try_each(urls: list[str], attempt: Callable[[str], None], name: str, page: str, gone_fix: str = "", where: Path | None = None) -> None:
    """Run `attempt(url)` for each mirror until one finishes. Stopped goes straight out; otherwise the last failure is raised."""
    last: DownloadError | None = None
    for url in urls:
        try:
            attempt(url)
            return
        except Stopped:
            raise
        except DownloadError as e:
            if e.code == "no_space":
                raise
            last = e
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
            err = classify(e, name, page, gone_fix, where)
            if err.code == "no_space":
                raise err
            last = err
    if last is None:
        last = DownloadError("no_link", f"There is no download link for {name}.", gone_fix or (f"Get it from {page}." if page else "Get it from its official page."))
    raise last


def sha256_of(path: Path, cancel: threading.Event | None = None, on_bytes: Callable[[int], None] | None = None) -> str:
    h = hashlib.sha256()
    done = 0
    with open(path, "rb") as f:
        while True:
            if cancel is not None and cancel.is_set():
                raise Stopped()
            chunk = f.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
            done += len(chunk)
            if on_bytes:
                on_bytes(done)
    return h.hexdigest()


@dataclass
class Spec:
    """One file to get. `dest` is the final path; the transfer writes `dest` + '.part' and swaps it in when checked."""
    id: str
    name: str
    urls: list[str]
    dest: Path
    size: int = 0
    sha256: str = ""
    page: str = ""
    licence: str = ""
    licence_page: str = ""
    optin: bool = False  # the person must accept the licence first (the caller passes the id in `accepted`)
    publisher: bool = True  # False: a community upload, say so
    required: bool | None = None
    note: str = ""
    gone_fix: str = ""  # what to say when every link is refused or gone (default: get it from the page)
    kind: str = "model"  # model | addon | project_file
    older: list[str] = field(default_factory=list)  # earlier files (paths under the models folder) the app can use while this one is not there

    @property
    def part(self) -> Path:
        return self.dest.with_name(self.dest.name + ".part")

    def present(self) -> bool:
        return self.dest.is_file()


def verify_pin(part: Path, want: str, name: str, page: str, cancel: threading.Event | None = None) -> None:
    """The file is accepted only if its SHA-256 is the pinned one; otherwise it is deleted and DownloadError is raised."""
    if sha256_of(part, cancel).lower() != want.lower():
        part.unlink(missing_ok=True)
        raise DownloadError("bad_checksum", f"{name} was downloaded, but it is not the original file (it may be damaged or changed). It has been deleted.",
                            f"Press Try again. If it happens again, check {page or 'the source page'}.")


def fetch(spec: Spec, cancel: threading.Event, on_bytes: Callable[[int, int], None] | None = None,
          opener: Callable[[str, int], Any] | None = None, accepted: set[str] | None = None) -> str:
    """Get one file. Returns 'present' (already there, nothing touched) or 'downloaded'. A finished file is never replaced."""
    opener = opener or default_open
    if spec.optin and spec.id not in (accepted or set()):
        raise DownloadError("licence", f"{spec.name} comes with a licence that has to be accepted first.",
                            f"Read it at {spec.licence_page or spec.page} and accept it in the app, then try again.")
    if spec.present():
        return "present"
    if not spec.sha256:
        raise DownloadError("no_pin", f"{spec.name} has no published checksum in the list, so it will not be downloaded.", f"Get it yourself from {spec.page or 'its official page'}.")
    if not spec.urls:
        raise DownloadError("no_link", f"There is no download link for {spec.name}.", f"Get it from {spec.page} and put it at {spec.dest}." if spec.page else "Get it from its official page.")
    have = spec.part.stat().st_size if spec.part.exists() else 0
    if spec.size and have > spec.size:
        spec.part.unlink()  # longer than it should be: cannot be a good start
        have = 0
    if spec.size:
        need = spec.size - have
        if free_bytes(spec.dest.parent) < need + SPACE_MARGIN:
            raise no_space(spec.dest.parent, need + SPACE_MARGIN)
    report = on_bytes or (lambda done, total: None)
    if not (spec.size and have == spec.size):
        try_each(spec.urls, lambda url: stream_to_part(url, spec.part, spec.name, cancel, lambda d: report(d, spec.size), opener),
                 spec.name, spec.page, spec.gone_fix, spec.dest.parent)
    size = spec.part.stat().st_size
    if spec.size and size != spec.size:
        raise DownloadError("short", f"{spec.name} is {size} bytes but should be {spec.size}.", "Press Try again; the download carries on where it stopped.")
    if spec.sha256:
        verify_pin(spec.part, spec.sha256, spec.name, spec.page, cancel)
    spec.dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(spec.part, spec.dest)
    return "downloaded"


# --- the ledger (local/installed.json, the same file VGAC.exe writes) ----------------------------------------------------

_ledger_lock = threading.Lock()


def ledger_path() -> Path:
    return project_dir() / "local" / "installed.json"


def record(what: str, path: Path, kind: str = "added", size: int | None = None) -> None:
    """Note that VGAC added (or linked) `path`. Writing is atomic; a damaged ledger starts again."""
    p = ledger_path()
    with _ledger_lock, state_lock(p.parent):   # the starter (VGAC.exe) writes this file too
        try:
            items = list(json.loads(p.read_text(encoding="utf-8-sig")).get("items", []))
        except (OSError, ValueError, AttributeError):
            items = []
        if any(str(i.get("path", "")).lower() == str(path).lower() for i in items if isinstance(i, dict)):
            return
        if size is None:
            try:
                size = path.stat().st_size if path.is_file() else 0
            except OSError:
                size = 0
        items.append({"what": what, "path": str(path), "kind": kind, "bytes": size, "when": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_lock_mod.unique_tmp(p)
        tmp.write_text(json.dumps({"items": items}, indent=2), encoding="utf-8")
        os.replace(tmp, p)


# --- the list (downloads.json, plus the person's downloads.local.json) ---------------------------------------------------


def catalogue() -> dict[str, Any]:
    """setup/downloads.json with setup/downloads.local.json laid over it, section by section and key by key."""
    out: dict[str, Any] = {}
    for fn in ("downloads.json", "downloads.local.json"):
        try:
            d = json.loads((project_dir() / "setup" / fn).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        for k, v in d.items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                out[k] = {**out[k], **v}
            else:
                out[k] = v
    return out


def models_root() -> Path | None:
    """The models folder of the first engine whose ComfyUI folder is known: <comfy_dir>/ComfyUI/models, else <comfy_dir>/models."""
    for inst in (get_settings().instances or {}).values():
        eng = getattr(inst, "engine", None) or {}
        cd = (eng.get("comfy_dir") if isinstance(eng, dict) else getattr(eng, "comfy_dir", None)) or ""
        if not cd:
            continue
        for sub in ("ComfyUI/models", "models"):
            p = Path(cd) / sub
            if p.is_dir():
                return p
    return None


def _spec(kind: str, key: str, d: dict[str, Any], root: Path) -> Spec | None:
    if kind == "model":
        rel = Path(d.get("dest", "")) / d.get("save_as", "")
        dest = root / rel
    elif kind == "addon":
        dest = root / Path(d.get("dest", ""))  # add-ons list the full relative path, file name included
    else:  # project_file
        dest = project_dir() / Path(d.get("dest", "")) / d.get("save_as", "")
    urls = [u for u in (d.get("url"), d.get("url_alt")) if u]
    return Spec(id=f"{kind}:{key}", name=d.get("name") or key, urls=urls, dest=dest, size=int(d.get("size") or 0), sha256=d.get("sha256") or "",
                page=d.get("page", ""), licence=d.get("licence", ""), licence_page=d.get("licence_page", ""), optin=bool(d.get("optin")),
                publisher=d.get("publisher", True) is not False, required=d.get("required"), note=d.get("note", ""), kind=kind,
                older=[str(x) for x in (d.get("older") or []) if isinstance(x, str)])


def _inside(path: Path, base: Path) -> bool:
    """True when `path` stays under `base` (no .. and no absolute entry in the list can send a file elsewhere)."""
    try:
        return Path(os.path.normpath(base)) in Path(os.path.normpath(path)).parents
    except (OSError, ValueError):
        return False


def specs_for(root: Path | None = None) -> list[Spec]:
    """Every model, add-on and project file in the list, with where it goes. `root` is the ComfyUI models folder."""
    cat = catalogue()
    root = root or models_root()
    out: list[Spec] = []
    for section, kind in (("models", "model"), ("addons", "addon"), ("project_files", "project_file")):
        for key, d in (cat.get(section) or {}).items():
            if not isinstance(d, dict):
                continue
            if kind != "project_file" and root is None:
                continue  # nowhere to put it until a ComfyUI is known
            base = root if kind != "project_file" else project_dir()
            s = _spec(kind, key, d, root or project_dir())
            if s and base is not None and _inside(s.dest, base):
                out.append(s)
            else:
                log.warning("download list: %s %s would land outside its folder; left out", kind, key)
    return out


# --- a job: several files, one thread ------------------------------------------------------------------------------------


@dataclass
class Job:
    id: str
    specs: list[Spec]
    accepted: set[str] = field(default_factory=set)
    state: str = "idle"  # idle | working | done | failed | stopped
    index: int = 0
    text: str = ""
    bytes_done: int = 0
    bytes_total: int = 0
    error: dict[str, str] | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    opener: Callable[[str, int], Any] | None = None
    results: dict[str, str] = field(default_factory=dict)  # spec id -> present | downloaded
    prepare: Callable[["Job"], list[Spec]] | None = None  # runs first in the thread; returns the specs (may raise DownloadError)
    after: Callable[["Job"], None] | None = None  # runs when every file is in place (unpack, link); may raise DownloadError

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "state": self.state, "index": self.index, "count": len(self.specs), "text": self.text, "error": self.error,
                "bytes_done": self.bytes_done, "bytes_total": self.bytes_total, "results": dict(self.results),
                "fraction": round(self.bytes_done / self.bytes_total, 3) if self.bytes_total else 0.0}


JOBS: dict[str, Job] = {}
_jobs_lock = threading.Lock()


def _work(job: Job) -> None:
    try:
        if job.prepare:
            job.text = "Getting ready"
            job.specs = job.prepare(job)
        todo = [s for s in job.specs if not s.present()]
        job.bytes_total = sum(s.size for s in todo)
        base = 0
        for n, s in enumerate(job.specs):
            job.index = n
            if job.cancel.is_set():
                raise Stopped()
            if s.present():
                job.results[s.id] = "present"
                continue
            job.text = f"Downloading {s.name}"

            def on_bytes(done: int, total: int, base: int = base) -> None:
                job.bytes_done = base + done

            how = fetch(s, job.cancel, on_bytes, job.opener, job.accepted)
            job.results[s.id] = how
            base += s.size
            job.bytes_done = base
            if how == "downloaded":
                record(s.name, s.dest)
        if job.after:
            job.text = "Setting it up"
            job.after(job)
        job.state, job.text = "done", "Done"
    except Stopped:
        job.state, job.text = "stopped", "Stopped. Anything half-downloaded is kept and carries on next time."
    except DownloadError as e:
        job.state, job.error, job.text = "failed", {"code": e.code, "message": e.message, "fix": e.fix}, e.message
    except Exception as e:  # noqa: BLE001
        log.exception("download job failed")
        job.state, job.text = "failed", "Something unexpected went wrong."
        job.error = {"code": "unexpected", "message": f"Something unexpected went wrong ({type(e).__name__}).", "fix": "Press Try again."}


def start_job(job_id: str, specs: list[Spec], accepted: set[str] | None = None, opener: Callable[[str, int], Any] | None = None, wait: bool = False,
              prepare: Callable[[Job], list[Spec]] | None = None, after: Callable[[Job], None] | None = None) -> Job:
    """Start (or restart, after a stop or failure) the job `job_id`. One job per id at a time."""
    with _jobs_lock:
        old = JOBS.get(job_id)
        if old and old.state == "working":
            raise DownloadError("busy", "This is already downloading.", "Wait for it to finish, or stop it first.")
        job = Job(job_id, specs, set(accepted or ()), state="working", opener=opener, prepare=prepare, after=after)
        JOBS[job_id] = job
        job.thread = threading.Thread(target=_work, args=(job,), daemon=True, name=f"download-{job_id}")
        job.thread.start()
    if wait:
        job.thread.join()
    return job


def stop_job(job_id: str) -> None:
    job = JOBS.get(job_id)
    if job:
        job.cancel.set()


def job_status(job_id: str) -> dict[str, Any] | None:
    job = JOBS.get(job_id)
    return job.public() if job else None
