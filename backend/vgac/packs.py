"""Optional content packs the person can get from inside the app: the hair library and (second part) the two animation packs.

One engine, two kits. A kit is a list of source zips plus what to do once they are unpacked:

    hair       three source packs (two MakeHuman zips by direct link, one Quaternius zip behind an itch.io click-through),
               unpacked into runtime/hairpacks and built into <library>/hairlib (vgac/hairlib_builder.py)
    animation  the two Universal Animation Library zips (both itch.io click-through), unpacked into data/animations/<id>

Routes (all under /api/packs):

    GET  /api/packs                      every kit: state, sources, choices, disk, the run
    GET  /api/packs/{kit}                one kit
    POST /api/packs/{kit}/start          {choices: [...]} start (or try again): downloads, unpacks, builds, in a thread
    POST /api/packs/{kit}/cancel         stop; half-finished downloads are kept and resume on the next start
    POST /api/packs/{kit}/zip/{source}   the zip itself as the body (a click-through pack, or a link that went away)
    POST /api/packs/{kit}/clean          remove the downloaded zips and unpacked files (the built library stays)

The work runs in a background thread; the page polls the kit. Nothing is replaced until the new files passed their check,
so a failure leaves any existing library as it was. Sources (links, sizes) are read from setup/downloads.json, the same
file the PowerShell helper uses. Every failure carries a plain message and what to do (`error: {code, message, fix}`).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .config import PROJECT_DIR, get_settings

log = logging.getLogger("vgac.packs")
router = APIRouter()

GB = 1024**3
MB = 1024**2
DOWNLOAD_TIMEOUT = 30
CHUNK = 1024 * 1024
UNPACK_MARK = ".unpacking"
SIZE_SLACK = (0.4, 2.5)  # a downloaded zip this far from the recorded size is suspicious (the contents decide)
UNPACK_FACTOR = 1.15  # unpacked size, as a multiple of the zip, for the disk check
BUILD_MARGIN = 400 * MB


class PackError(Exception):
    """A failure the person can read: `message` says what happened, `fix` what to do."""

    def __init__(self, code: str, message: str, fix: str = ""):
        super().__init__(message)
        self.code, self.message, self.fix = code, message, fix


class Stopped(Exception):
    pass


# --- where things are (functions, so tests can point them at a scratch folder) -----------------------------------------


def project_dir() -> Path:
    return PROJECT_DIR


def library_dir() -> Path:
    return Path(get_settings().library_path)


def downloads() -> dict[str, Any]:
    p = project_dir() / "setup" / "downloads.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# --- sources and kits --------------------------------------------------------------------------------------------------


@dataclass
class Source:
    id: str
    name: str
    zip_name: str
    size: int
    page: str
    store: Path  # the folder that holds the zip and, inside it, the unpacked files
    dest: Path  # where the zip is unpacked
    ready_path: Path  # exists once the pack is unpacked (a folder or a file found by name)
    expect: list[str]  # entries a right zip has (a prefix ending in "/" or an exact name)
    urls: list[str] = field(default_factory=list)  # direct links; empty = itch.io click-through
    needs: list[str] = field(default_factory=list)
    click_text: str = ""
    what: str = ""
    find_name: str = ""  # a file to look for anywhere under dest instead of a fixed ready_path

    @property
    def zip_path(self) -> Path:
        return self.store / self.zip_name

    @property
    def direct(self) -> bool:
        return bool(self.urls)

    def ready(self) -> bool:
        if (self.dest / UNPACK_MARK).exists():
            return False
        if self.find_name:
            return self.dest.is_dir() and next(self.dest.rglob(self.find_name), None) is not None
        return self.ready_path.exists()


@dataclass
class Kit:
    id: str
    label: str
    summary: str
    sources: list[Source]
    choices: list[dict[str, Any]]  # {id, name, what, sources: [source ids], styles}
    build: bool = False  # a build step follows the unpacking

    def source(self, sid: str) -> Source | None:
        return next((s for s in self.sources if s.id == sid), None)

    def sources_for(self, choice_ids: list[str]) -> list[Source]:
        """The sources the chosen packs need, with what those need, in the order to get them (needed ones first)."""
        want: list[str] = []
        for c in self.choices:
            if c["id"] in choice_ids:
                for sid in c["sources"]:
                    s = self.source(sid)
                    for n in (s.needs if s else []) + [sid]:
                        if n not in want:
                            want.append(n)
        return [s for sid in want if (s := self.source(sid))]


def hair_kit() -> Kit:
    d = downloads()
    store_root = project_dir() / "runtime" / "hairpacks"
    sources = []
    for h in d.get("hair_sources", []):
        store = store_root / h["id"]
        dest = store / "unpacked"
        sources.append(Source(id=h["id"], name=h["name"], zip_name=h["zip_name"], size=int(h.get("zip_size") or 0), page=h.get("page", ""),
                              store=store, dest=dest, ready_path=dest / h.get("check_dir", "hair"), expect=list(h.get("zip_expect") or []),
                              urls=[u for u in (h.get("zip"), h.get("zip_alt")) if u], needs=list(h.get("needs") or []),
                              click_text=h.get("click_text", ""), what=h.get("note", "")))
    choices = []
    for hp in d.get("hair_packs", []):
        if hp.get("no_download") or not hp.get("source"):
            continue
        choices.append({"id": hp["id"], "name": hp["name"], "what": hp.get("what", ""), "styles": int(hp.get("styles") or 0),
                        "sources": [hp["source"]], "catalogue_pack": hp.get("catalogue_pack"), "catalogue_author": hp.get("catalogue_author")})
    return Kit("hair", "Hair library", "26 ready-made hairstyles you can put on a character, free and in the public domain (CC0).",
               sources, choices, build=True)


KITS: dict[str, Callable[[], Kit]] = {"hair": hair_kit}


def get_kit(kit_id: str) -> Kit:
    f = KITS.get(kit_id)
    if f is None:
        raise HTTPException(404, "There is no such pack.")
    return f()


# --- the run (one per kit, in memory) -----------------------------------------------------------------------------------


@dataclass
class Run:
    kit: str
    state: str = "idle"  # idle | working | waiting | done | failed | cancelled
    phase: str = ""  # check | download | unpack | build | finish
    text: str = ""
    fraction: float = 0.0
    error: dict[str, str] | None = None
    waiting_for: list[str] = field(default_factory=list)
    choices: list[str] = field(default_factory=list)
    started: float = 0.0
    finished: float = 0.0
    bytes_done: int = 0
    bytes_total: int = 0
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None

    def public(self) -> dict[str, Any]:
        return {"state": self.state, "phase": self.phase, "text": self.text, "fraction": round(self.fraction, 3), "error": self.error,
                "waiting_for": list(self.waiting_for), "choices": list(self.choices), "bytes_done": self.bytes_done,
                "bytes_total": self.bytes_total, "seconds": round((self.finished or time.time()) - self.started, 1) if self.started else 0}


RUNS: dict[str, Run] = {}
_lock = threading.Lock()


def run_of(kit_id: str) -> Run:
    with _lock:
        return RUNS.setdefault(kit_id, Run(kit_id))


# --- helpers: zip checks, download, unpack -----------------------------------------------------------------------------


def _fmt_gb(n: float) -> str:
    return f"{n / GB:.1f} GB" if n >= GB else f"{max(1, round(n / MB))} MB"


def check_zip(path: Path, src: Source, kit: Kit) -> str | None:
    """None when the file is the right zip for `src`, else a plain sentence saying what is wrong."""
    try:
        size = path.stat().st_size
    except OSError:
        return "The file could not be read."
    if size == 0:
        return "The file is empty."
    if not zipfile.is_zipfile(path):
        return f"This is not a zip file. {src.name} comes as a .zip; if your browser unpacked it, choose the zip itself."
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
    except (zipfile.BadZipFile, OSError):
        return "This zip file is damaged or unfinished. Download it again."

    def has(expect: list[str]) -> bool:
        return all(any(n == e or (e.endswith("/") and n.startswith(e)) for n in names) for e in expect)

    if src.expect and not has(src.expect):
        for other in kit.sources:
            if other.id != src.id and other.expect and has(other.expect):
                return f"This is the zip for \"{other.name}\", not for \"{src.name}\"."
        return f"This zip does not contain what \"{src.name}\" should. Check that you picked the right file on its page ({src.page})."
    if src.size and not (SIZE_SLACK[0] * src.size <= size <= SIZE_SLACK[1] * src.size):
        return (f"This zip is {_fmt_gb(size)}, but \"{src.name}\" is about {_fmt_gb(src.size)}. "
                "It is probably a different download from the same page.")
    return None


def _open(url: str, start: int):  # replaced in tests
    headers = {"User-Agent": "VGAC-app"}
    if start:
        headers["Range"] = f"bytes={start}-"
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=DOWNLOAD_TIMEOUT)


def _download_one(url: str, src: Source, run: Run, on_bytes: Callable[[int], None]) -> None:
    src.store.mkdir(parents=True, exist_ok=True)
    part = src.store / (src.zip_name + ".part")
    have = part.stat().st_size if part.exists() else 0
    resp = _open(url, have)
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
                if run.cancel.is_set():
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
        raise PackError("short", f"{src.name} arrived incomplete.", "Press Try again; the download carries on where it stopped.")
    os.replace(part, src.zip_path)


def _download(src: Source, run: Run, on_bytes: Callable[[int], None]) -> None:
    last: PackError | None = None
    gone = 0
    for url in src.urls:
        try:
            _download_one(url, src, run, on_bytes)
            return
        except Stopped:
            raise
        except urllib.error.HTTPError as e:
            if e.code in (404, 410, 403):
                gone += 1
                last = PackError("link_gone", f"The download link for {src.name} no longer works (the website answered {e.code}); it may have moved or closed.",
                                 f"Download the zip yourself from {src.page} and drop it onto this panel.")
            else:
                last = PackError("server", f"The website sent an error ({e.code}) while giving {src.name}.", "Try again in a few minutes.")
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
            last = PackError("no_internet", f"The app could not reach the internet to download {src.name}.",
                             "Check your connection and press Try again. A half-finished download is kept and carries on where it stopped.")
            log.info("download of %s failed: %s", url, e)
        except OSError as e:
            if getattr(e, "errno", None) == 28:
                raise _no_space(src.store, 0)
            last = PackError("write", f"The file could not be saved ({e.strerror or e}).", "Check that the disk is not full or locked, then press Try again.")
    if last is None:
        last = PackError("no_link", f"There is no download link for {src.name}.", f"Download the zip from {src.page} and drop it onto this panel.")
    raise last


def _no_space(where: Path, need: int) -> PackError:
    try:
        free = shutil.disk_usage(where if where.exists() else where.parent).free
    except OSError:
        free = 0
    return PackError("no_space", f"There is not enough free space on this drive: about {_fmt_gb(need)} is needed and {_fmt_gb(free)} is free." if need else
                     "The drive ran out of space.", "Free some space (empty the recycle bin, remove files you do not need), then press Try again.")


def unpack(src: Source, run: Run, on_progress: Callable[[float], None]) -> None:
    """Unpack the zip next to it without overwriting and without letting an entry climb out of the folder."""
    src.dest.mkdir(parents=True, exist_ok=True)
    mark = src.dest / UNPACK_MARK
    mark.write_text("unpacking", encoding="utf-8")
    base = src.dest.resolve()
    with zipfile.ZipFile(src.zip_path) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        for n, i in enumerate(infos):
            if run.cancel.is_set():
                raise Stopped()
            target = (base / i.filename).resolve()
            if base not in target.parents:
                raise PackError("unsafe", f"The zip for {src.name} holds a file that would be written outside its folder, so it was not unpacked.",
                                "Download the zip again from the official page.")
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with z.open(i) as r, open(target, "wb") as w:
                        shutil.copyfileobj(r, w, CHUNK)
                except OSError as e:
                    if getattr(e, "errno", None) == 28:
                        raise _no_space(src.dest, 0)
                    raise
            if n % 25 == 0:
                on_progress(n / max(1, len(infos)))
    mark.unlink(missing_ok=True)
    on_progress(1.0)


def dir_bytes(p: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


# --- the work ----------------------------------------------------------------------------------------------------------


def _set(run: Run, **kw: Any) -> None:
    for k, v in kw.items():
        setattr(run, k, v)


def _blender_exe() -> Path:
    exe = get_settings().blender_exe
    if not exe or not Path(exe).is_file():
        raise PackError("blender_missing", "The hairstyles are built with Blender, and Blender was not found.",
                        "Install Blender 5 or newer (free, from blender.org), set its location as blender_exe in local/machine.json, then press Try again.")
    try:
        from . import preflight

        v = preflight.blender_version(Path(exe))
    except Exception:  # noqa: BLE001
        v = None
    if v is not None and v[0] < 5:
        raise PackError("blender_old", f"The Blender found ({v[0]}.{v[1]}) is too old; the hairstyles need Blender 5 or newer.",
                        "Install Blender 5 or newer (free, from blender.org), set its location as blender_exe in local/machine.json, then press Try again.")
    return Path(exe)


def finish_hair(run: Run, kit: Kit, base: float, span: float) -> None:
    """Build the library from every unpacked pack into <library>/hairlib.new, check it, swap it in."""
    from . import hairlib_builder, hairstyles, my_hair

    blender = _blender_exe()
    lib = library_dir()
    lib.mkdir(parents=True, exist_ok=True)
    new, work, old, cur = lib / "hairlib.new", lib / "hairlib.work", lib / "hairlib.old", lib / "hairlib"
    for d in (new, work):
        shutil.rmtree(d, ignore_errors=True)

    def progress(f: float, t: str) -> None:
        _set(run, phase="build", text=t, fraction=base + span * 0.9 * f)

    try:
        hairlib_builder.build(project_dir() / "runtime" / "hairpacks", new, blender=str(blender), scratch=work, progress=progress,
                              cancel=run.cancel.is_set)
    except hairlib_builder.Cancelled:
        raise Stopped()
    except hairlib_builder.BuildError as e:
        if e.code == "no_packs":
            raise PackError("no_packs", "None of the hairstyle packs could be read.", "Press Try again; if it keeps failing, remove the downloaded packs and start over.")
        raise PackError(e.code, "Blender stopped before the hairstyles were finished.",
                        f"Press Try again. If it keeps failing, the log is in {work}.")
    _set(run, phase="finish", text="Checking the new hairstyles", fraction=base + span * 0.93)
    _cat, bad = hairlib_builder.verify(new)
    if bad:
        log.warning("hair library check failed: %s", bad[:5])
        raise PackError("verify", "The hairstyles were built, but they did not pass the app's own check, so they were not used.",
                        "Press Try again. Your existing hairstyles were left as they were.")
    shutil.rmtree(old, ignore_errors=True)
    if cur.exists():
        cur.rename(old)
    new.rename(cur)
    shutil.rmtree(old, ignore_errors=True)
    shutil.rmtree(work, ignore_errors=True)
    _write_picks(kit, run.choices, lib)
    hairstyles.use_dir(None)  # drops the cached catalogue: the new library is read from the next request on
    try:
        my_hair._stats.cache_clear()
    except Exception:  # noqa: BLE001
        pass


def _write_picks(kit: Kit, chosen: list[str], lib: Path) -> None:
    """library/hair-packs.json follows the packs the person chose (the credits and the app hide the others). All packs chosen
    = no file = every built style."""
    f = lib / "hair-packs.json"
    if set(chosen) >= {c["id"] for c in kit.choices}:
        f.unlink(missing_ok=True)
        return
    packs = [{"id": c["id"], "catalogue_pack": c["catalogue_pack"], "catalogue_author": c["catalogue_author"]} for c in kit.choices if c["id"] in chosen]
    d = downloads()
    for hp in d.get("hair_packs", []):
        if hp.get("no_download"):
            packs.append({"id": hp["id"], "catalogue_pack": hp["catalogue_pack"], "catalogue_author": hp["catalogue_author"]})
    f.write_text(json.dumps({"note": "Written by the app's Get the hair library screen. Styles from packs not listed here are hidden. Delete this file to show every built style.",
                             "packs": packs}, indent=1), encoding="utf-8")


FINISH: dict[str, Callable[[Run, Kit, float, float], None]] = {"hair": finish_hair}


def free_bytes(where: Path) -> int:
    p = where
    while not p.exists() and p.parent != p:
        p = p.parent
    return shutil.disk_usage(p).free


def needed_bytes(kit: Kit, sources: list[Source]) -> int:
    need = 0
    for s in sources:
        if s.ready():
            continue
        if not s.zip_path.exists():
            part = s.store / (s.zip_name + ".part")
            need += max(0, s.size - (part.stat().st_size if part.exists() else 0))
        need += int(s.size * UNPACK_FACTOR)
    if kit.build:
        need += BUILD_MARGIN
    return need


def work(run: Run, kit: Kit) -> None:
    try:
        sources = kit.sources_for(run.choices)
        todo = [s for s in sources if not s.ready()]
        if not todo and not kit.build:
            _set(run, state="done", text="Everything is already installed.", fraction=1.0)
            return
        _set(run, phase="check", text="Checking the free space", fraction=0.01)
        if todo:
            need = needed_bytes(kit, sources)
            where = todo[0].store
            if free_bytes(where) < need:
                raise _no_space_msg(where, need)
        total = sum(s.size for s in todo if not s.zip_path.exists()) or 1
        base_done = 0
        waiting: list[str] = []
        # 1. get every zip
        for s in todo:
            if run.cancel.is_set():
                raise Stopped()
            if s.zip_path.exists():
                bad = check_zip(s.zip_path, s, kit)
                if bad is None:
                    continue
                s.zip_path.unlink(missing_ok=True)  # a leftover that is not the right file
            if not s.direct:
                waiting.append(s.id)
                continue

            def on_bytes(done: int, s: Source = s, base_done: int = base_done) -> None:
                _set(run, phase="download", text=f"Downloading {s.name}", bytes_done=base_done + done, bytes_total=total,
                     fraction=0.02 + 0.55 * min(1.0, (base_done + done) / total))

            _set(run, phase="download", text=f"Downloading {s.name}")
            _download(s, run, on_bytes)
            bad = check_zip(s.zip_path, s, kit)
            if bad:
                s.zip_path.unlink(missing_ok=True)
                raise PackError("bad_download", f"{s.name} was downloaded, but it is not the file expected: {bad}",
                                f"Try again, or download it yourself from {s.page} and drop it onto this panel.")
            base_done += s.size
        if waiting:
            _set(run, state="waiting", phase="download", waiting_for=waiting, text="Waiting for a file from you.", error=None)
            return
        # 2. unpack
        for n, s in enumerate(todo):
            if s.ready():
                continue
            _set(run, phase="unpack", text=f"Unpacking {s.name}")
            unpack(s, run, lambda f, n=n: _set(run, fraction=0.58 + 0.07 * (n + f) / len(todo)))
        # 3. what follows (the hair build)
        fin = FINISH.get(kit.id)
        if fin:
            fin(run, kit, 0.65, 0.35)
        _set(run, state="done", phase="finish", text="Done", fraction=1.0, waiting_for=[])
    except Stopped:
        _set(run, state="cancelled", text="Stopped. Anything half-downloaded is kept and carries on next time.")
    except PackError as e:
        _set(run, state="failed", error={"code": e.code, "message": e.message, "fix": e.fix}, text=e.message)
    except OSError as e:
        pe = _no_space(Path(kit.sources[0].store), 0) if getattr(e, "errno", None) == 28 else None
        err = {"code": pe.code, "message": pe.message, "fix": pe.fix} if pe else {"code": "disk", "message": f"A file could not be written ({e.strerror or e}).", "fix": "Press Try again."}
        _set(run, state="failed", error=err, text=err["message"])
    except Exception as e:  # noqa: BLE001
        log.exception("pack run failed")
        _set(run, state="failed", error={"code": "unexpected", "message": f"Something unexpected went wrong ({type(e).__name__}).", "fix": "Press Try again."},
             text="Something unexpected went wrong.")
    finally:
        run.finished = time.time()


def _no_space_msg(where: Path, need: int) -> PackError:
    return _no_space(where, need)


def start_run(kit: Kit, choices: list[str]) -> Run:
    run = run_of(kit.id)
    with _lock:
        if run.state == "working":
            raise HTTPException(409, "This is already running.")
        valid = [c["id"] for c in kit.choices]
        picked = [c for c in choices if c in valid] or valid
        run.state, run.phase, run.text, run.fraction, run.error = "working", "check", "Starting", 0.0, None
        run.waiting_for, run.choices, run.started, run.finished = [], picked, time.time(), 0.0
        run.bytes_done = run.bytes_total = 0
        run.cancel = threading.Event()
        run.thread = threading.Thread(target=work, args=(run, kit), name=f"packs-{kit.id}", daemon=True)
        run.thread.start()
    return run


# --- status ------------------------------------------------------------------------------------------------------------


def hair_state() -> dict[str, Any]:
    from . import hairstyles

    built = hairstyles.built_dir()
    cat = hairstyles._load(str(hairstyles.library_dir())) if hairstyles.installed() else {"styles": {}}
    packs_built = {(e.get("pack"), e.get("author")) for e in (cat.get("styles") or {}).values()}
    return {"installed": hairstyles.installed(), "count": len(cat.get("styles") or {}), "where": "built" if built else "project",
            "packs_built": sorted({p for p, _a in packs_built if p})}


def kit_status(kit: Kit, with_sizes: bool = True) -> dict[str, Any]:
    run = run_of(kit.id)
    st: dict[str, Any] = {"id": kit.id, "label": kit.label, "summary": kit.summary, "run": run.public()}
    if kit.id == "hair":
        st.update(hair_state())
    sources = []
    for s in kit.sources:
        ready, have_zip = s.ready(), s.zip_path.exists()
        sources.append({"id": s.id, "name": s.name, "size": s.size, "page": s.page, "direct": s.direct, "needs": s.needs, "what": s.what,
                        "click_text": s.click_text, "state": "unpacked" if ready else "downloaded" if have_zip else "missing"})
    st["sources"] = sources
    names = {s.id: s.name for s in kit.sources}
    built = set(st.get("packs_built") or [])
    st["choices"] = [{"id": c["id"], "name": c["name"], "what": c["what"], "styles": c["styles"], "sources": c["sources"],
                      "requires": [names[n] for sid in c["sources"] for n in (kit.source(sid).needs if kit.source(sid) else [])],
                      "built": bool(c.get("catalogue_pack") in built)} for c in kit.choices]
    st["download_bytes"] = needed_bytes(kit, kit.sources_for([c["id"] for c in kit.choices])) if with_sizes else 0
    try:
        st["free_bytes"] = free_bytes(kit.sources[0].store) if kit.sources else 0
    except OSError:
        st["free_bytes"] = 0
    if run.state != "working" and with_sizes:
        st["cleanable_bytes"] = sum(dir_bytes(s.store) for s in kit.sources if s.store.exists())
    else:
        st["cleanable_bytes"] = 0
    return st


# --- routes ------------------------------------------------------------------------------------------------------------


class StartIn(BaseModel):
    choices: list[str] = Field(default_factory=list, max_length=20)


@router.get("/api/packs")
def get_packs() -> dict[str, Any]:
    return {"kits": [kit_status(f()) for f in KITS.values()]}


@router.get("/api/packs/{kit_id}")
def get_pack(kit_id: str) -> dict[str, Any]:
    return kit_status(get_kit(kit_id))


@router.post("/api/packs/{kit_id}/start")
def post_start(kit_id: str, body: StartIn) -> dict[str, Any]:
    kit = get_kit(kit_id)
    start_run(kit, body.choices)
    return kit_status(kit, with_sizes=False)


@router.post("/api/packs/{kit_id}/cancel")
def post_cancel(kit_id: str) -> dict[str, Any]:
    kit = get_kit(kit_id)
    run = run_of(kit_id)
    run.cancel.set()
    with _lock:
        if run.state == "waiting":
            run.state, run.text = "cancelled", "Stopped."
    return kit_status(kit, with_sizes=False)


@router.post("/api/packs/{kit_id}/clean")
def post_clean(kit_id: str) -> dict[str, Any]:
    kit = get_kit(kit_id)
    run = run_of(kit_id)
    if run.state == "working":
        raise HTTPException(409, "Wait until it has finished, or stop it first.")
    freed = 0
    for s in kit.sources:
        if s.store.exists():
            freed += dir_bytes(s.store)
            shutil.rmtree(s.store, ignore_errors=True)
    if kit.id == "hair":
        shutil.rmtree(library_dir() / "hairlib.work", ignore_errors=True)
    out = kit_status(kit)
    out["freed_bytes"] = freed
    return out


@router.post("/api/packs/{kit_id}/zip/{source_id}")
async def post_zip(kit_id: str, source_id: str, request: Request) -> dict[str, Any]:
    """The zip the person downloaded (or chose or dropped): streamed to disk, then checked. A wrong file is refused with a
    plain sentence and removed. When the run was waiting for it, the run carries on."""
    import asyncio

    kit = get_kit(kit_id)
    src = kit.source(source_id)
    if src is None:
        raise HTTPException(404, "There is no such pack.")
    run = run_of(kit_id)
    if run.state == "working":
        raise HTTPException(409, "Wait until the current step has finished.")
    declared = request.headers.get("content-length")
    limit = max(src.size * 3, 64 * MB) if src.size else 4 * GB
    if declared and declared.isdigit():
        if int(declared) > limit:
            raise HTTPException(413, f"This file is much bigger than \"{src.name}\" should be ({_fmt_gb(src.size)}). It is probably not the right one.")
        try:
            if free_bytes(src.store) < int(declared) + int(src.size * UNPACK_FACTOR):
                raise HTTPException(507, _no_space(src.store, int(declared) + int(src.size * UNPACK_FACTOR)).message)
        except OSError:
            pass
    src.store.mkdir(parents=True, exist_ok=True)
    part = src.store / (src.zip_name + ".upload")
    total = 0
    try:
        with open(part, "wb") as f:
            async for chunk in request.stream():
                if not chunk:
                    continue
                total += len(chunk)
                if total > limit:
                    raise HTTPException(413, f"This file is much bigger than \"{src.name}\" should be. It is probably not the right one.")
                await asyncio.to_thread(f.write, chunk)
        bad = check_zip(part, src, kit)
        if bad:
            raise HTTPException(400, bad)
        os.replace(part, src.zip_path)
    except HTTPException:
        part.unlink(missing_ok=True)
        raise
    except OSError as e:
        part.unlink(missing_ok=True)
        if getattr(e, "errno", None) == 28:
            raise HTTPException(507, _no_space(src.store, 0).message)
        raise HTTPException(500, "The file could not be saved. Try again.")
    if run.state in ("waiting", "failed", "cancelled", "idle") and kit.sources_for(run.choices or [c["id"] for c in kit.choices]):
        start_run(kit, run.choices)
    return kit_status(kit, with_sizes=False)
