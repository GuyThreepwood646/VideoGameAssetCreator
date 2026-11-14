"""The models panel (setup card, phase 7): the model sets, files the person already has, licence gates, and the test character.

The list is setup/downloads.json (with setup/downloads.local.json laid over it); its `sets` say which files make a set and
its `gates` carry the sentence a person types to accept a licence. Files go where the engine reads them: the models folder
of the linked ComfyUI (downloader.models_root).

Using what is already there. Before a file is offered, its exact path is looked for in every models folder the engine
reads: its own, and every folder in its extra model paths file. Only exact paths are tried (`<folder>/<model subfolder>/
<file name>`, with the alternate folder names unet and clip); no folder is listed. A match of name and exact size counts as
present and is used where it is, never copied. Its SHA-256 is checked in the background at low priority; a file with the
right name that differs from the published one is shown as such, and the person may use it anyway.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import downloader, model_choices
from .config import get_settings
from .downloader import DownloadError, Spec

log = logging.getLogger("vgac.models_setup")

GB = downloader.GB
WORKING_SPACE = 20 * GB  # plus 10 percent of what is to come
ALT_FOLDERS = {"diffusion_models": "unet", "text_encoders": "clip"}


# --- the list -------------------------------------------------------------------------------------------------------------


def sets_list() -> list[dict[str, Any]]:
    return [s for s in (downloader.catalogue().get("sets") or []) if isinstance(s, dict) and s.get("id")]


def gates() -> dict[str, dict[str, Any]]:
    return {k: v for k, v in (downloader.catalogue().get("gates") or {}).items() if isinstance(v, dict)}


def gate_of(spec_id: str) -> str:
    """The gate a file is behind (the `optin` value in the list), or ''."""
    kind, _, key = spec_id.partition(":")
    section = {"model": "models", "addon": "addons", "project_file": "project_files"}.get(kind, "")
    v = ((downloader.catalogue().get(section) or {}).get(key) or {}).get("optin")
    return str(v) if v and isinstance(v, str) else ("" if not v else "gate")


def accepted_for(typed: dict[str, str]) -> set[str]:
    """Gates whose typed sentence is exactly the one in the list."""
    return {g for g, d in gates().items() if str(typed.get(g, "")).strip() == str(d.get("phrase", "")) and d.get("phrase")}


# --- where files are -----------------------------------------------------------------------------------------------------


def engine_extra_paths() -> list[Path]:
    out = []
    for inst in (get_settings().instances or {}).values():
        e = getattr(inst, "engine", None)
        p = getattr(e, "extra_model_paths", None) if e else None
        if p:
            out.append(Path(p))
    return out


def extra_folders(yaml_file: Path) -> dict[str, list[Path]]:
    """For one extra-model-paths file: model type (checkpoints, vae, ...) -> the folders ComfyUI reads for it."""
    try:
        data = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    out: dict[str, list[Path]] = {}
    for section in data.values() if isinstance(data, dict) else []:
        if not isinstance(section, dict) or not section.get("base_path"):
            continue
        base = Path(str(section["base_path"]))
        for typ, rel in section.items():
            if typ in ("base_path", "is_default") or not isinstance(rel, str):
                continue
            for line in rel.splitlines():
                if line.strip():
                    out.setdefault(typ, []).append(base / line.strip().rstrip("/\\"))
        out.setdefault("", []).append(base)
    return out


def engine_models_folders() -> list[Path]:
    """The models folder of every engine's own ComfyUI (a second instance on another card has its own install)."""
    out = []
    for inst in (get_settings().instances or {}).values():
        e = getattr(inst, "engine", None)
        cd = (e.get("comfy_dir") if isinstance(e, dict) else getattr(e, "comfy_dir", None)) if e else None
        for sub in ("ComfyUI/models", "models"):
            if cd and (Path(cd) / sub).is_dir():
                out.append(Path(cd) / sub)
                break
    return out


def read_roots(root: Path | None) -> dict[str, Any]:
    """Every place ComfyUI reads models from: the models folder of each engine's ComfyUI and the folders of its extra model
    paths files. `idx` keeps the folder listings made while looking (candidates_below)."""
    typed: dict[str, list[Path]] = {}
    bases: list[Path] = [root] if root else []
    for f in engine_models_folders():
        if all(os.path.normcase(str(f)) != os.path.normcase(str(b)) for b in bases):
            bases.append(f)
    for f in engine_extra_paths():
        got = extra_folders(f)
        for t, folders in got.items():
            if t == "":
                bases += folders
            else:
                typed.setdefault(t, []).extend(folders)
    return {"typed": typed, "bases": bases, "idx": {}}


def _listing(folder: Path, depth: int = 5, limit: int = 60000) -> dict[str, list[Path]]:
    """Every file under `folder` (to `depth` folders down, like the engine's own lists, which show subfolders): lower-case name -> paths."""
    found: dict[str, list[Path]] = {}
    todo = [(folder, 0)]
    n = 0
    while todo and n < limit:
        d, level = todo.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    n += 1
                    try:
                        if e.is_dir():
                            if level < depth:
                                todo.append((Path(e.path), level + 1))
                        else:
                            found.setdefault(e.name.casefold(), []).append(Path(e.path))
                    except OSError:
                        continue
        except OSError:
            continue
    return found


def candidates_below(spec: Spec, root: Path, roots: dict[str, Any]) -> list[Path]:
    """Files of this name in a subfolder of the type folder in every place the engine reads (ComfyUI lists subfolders, so a file
    kept under skintoken/experiments/x/ is a file the engine has). Only the entry's own type folder is looked in, never a whole
    models folder; the listing of a folder is made once per look."""
    try:
        rel = spec.dest.relative_to(root)
    except ValueError:
        return []
    typ = rel.parts[0] if len(rel.parts) > 1 else ""
    if not typ:
        return []
    folders = [b / typ for b in roots["bases"]] + list(roots["typed"].get(typ, []))
    if typ in ALT_FOLDERS:
        folders += [b / ALT_FOLDERS[typ] for b in roots["bases"]]
    idx = roots.setdefault("idx", {})
    out: list[Path] = []
    for f in folders:
        key = os.path.normcase(str(f))
        if key not in idx:
            idx[key] = _listing(f) if f.is_dir() else {}
        out += idx[key].get(spec.dest.name.casefold(), [])
    return out


def candidates(spec: Spec, root: Path, roots: dict[str, Any]) -> list[Path]:
    """The exact paths this file could be at, in reading order: its own place first, then every other folder ComfyUI reads.
    A file that does not live under a ComfyUI models folder (the project's own landmark files, for example) is looked for
    at its own path only: ComfyUI does not read it."""
    try:
        rel = spec.dest.relative_to(root)
    except ValueError:
        return [spec.dest]
    parts = rel.parts
    typ, rest = parts[0], Path(*parts[1:]) if len(parts) > 1 else Path()
    out = [spec.dest]
    for folder in roots["typed"].get(typ, []):
        out.append(folder / rest)
    for base in roots["bases"]:
        out.append(base / rel)
        if typ in ALT_FOLDERS:
            out.append(base / ALT_FOLDERS[typ] / rest)
    seen: set[str] = set()
    uniq = []
    for p in out:
        k = os.path.normcase(str(p))
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


# --- checking in the background -------------------------------------------------------------------------------------------

_verify_lock = threading.Lock()
VERIFIED: dict[str, dict[str, Any]] = {}  # path -> {"mtime": .., "size": .., "ok": bool}
_verify_state: dict[str, Any] = {"state": "idle", "done": 0, "total": 0, "current": ""}


def accepted_file() -> Path:
    return downloader.project_dir() / "local" / "models_accepted.json"


def accepted_paths() -> set[str]:
    try:
        d = json.loads(accepted_file().read_text(encoding="utf-8"))
        return {str(x) for x in d} if isinstance(d, list) else set()
    except (OSError, ValueError):
        return set()


def accept_path(path: Path) -> None:
    cur = accepted_paths() | {os.path.normcase(str(path))}
    accepted_file().parent.mkdir(parents=True, exist_ok=True)
    accepted_file().write_text(json.dumps(sorted(cur)), encoding="utf-8")


def _lower_priority() -> None:
    try:
        import ctypes

        k = ctypes.windll.kernel32  # type: ignore[attr-defined]
        k.SetThreadPriority(k.GetCurrentThread(), -1)  # below normal: the checking must not slow the person's work
    except (AttributeError, OSError):
        pass


_loaded_from: list[Path | None] = [None]


def verified_file() -> Path:
    return downloader.project_dir() / "local" / "models_verified.json"


def _load_verified() -> None:
    """The checksums read in earlier runs (kept in local/models_verified.json with the file's size and modified time, so a changed
    file is read again): a 6 to 20 GB file is read once, not every time the app starts."""
    f = verified_file()
    if _loaded_from[0] == f:
        return
    _loaded_from[0] = f
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    for k, v in (d.items() if isinstance(d, dict) else []):
        if isinstance(v, dict) and k not in VERIFIED and {"mtime", "size", "ok"} <= set(v):
            VERIFIED[k] = v


def _save_verified() -> None:
    try:
        verified_file().parent.mkdir(parents=True, exist_ok=True)
        verified_file().write_text(json.dumps(VERIFIED), encoding="utf-8")
    except OSError as e:
        log.debug("could not keep the checked checksums: %s", e)


def check_state(path: Path, spec: Spec) -> str:
    """'ok' or 'differs' once its checksum was read; 'unchecked' before."""
    try:
        st = path.stat()
    except OSError:
        return "unchecked"
    _load_verified()
    v = VERIFIED.get(os.path.normcase(str(path)))
    if v and v["mtime"] == st.st_mtime and v["size"] == st.st_size:
        return "ok" if v["ok"] else "differs"
    return "unchecked"


def verify_all(specs: list[tuple[Spec, Path]], wait: bool = False) -> dict[str, Any]:
    """Start (or report) the background check of every file that is present with the right size."""
    with _verify_lock:
        if _verify_state["state"] == "working":
            return dict(_verify_state)
        todo = [(s, p) for s, p in specs if p.is_file() and check_state(p, s) == "unchecked"]
        _verify_state.update(state="working" if todo else "done", done=0, total=len(todo), current="")
        if not todo:
            return dict(_verify_state)

    def work() -> None:
        _lower_priority()
        try:
            for s, p in todo:
                _verify_state["current"] = s.name
                try:
                    st = p.stat()
                    ok = downloader.sha256_of(p) == s.sha256.lower()
                    VERIFIED[os.path.normcase(str(p))] = {"mtime": st.st_mtime, "size": st.st_size, "ok": ok}
                    _save_verified()
                except OSError:
                    pass
                _verify_state["done"] += 1
        finally:
            _verify_state["state"] = "done"

    t = threading.Thread(target=work, daemon=True, name="verify-models")
    t.start()
    if wait:
        t.join()
    return dict(_verify_state)


# --- status ---------------------------------------------------------------------------------------------------------------


def item_state(spec: Spec, root: Path, roots: dict[str, Any]) -> dict[str, Any]:
    """The state of one file, but one entry that cannot be looked at (whatever the reason) is reported missing with its error
    instead of taking the whole panel down."""
    try:
        return _item_state(spec, root, roots)
    except Exception as e:  # noqa: BLE001
        log.warning("could not look for %s: %s", spec.id, e)
        return {"state": "missing", "path": str(spec.dest), "reused": False, "check": "", "accepted": False, "error": str(e)}


def _chosen_state(spec: Spec, acc: set[str]) -> dict[str, Any] | None:
    """The state of the file the person pointed at for this entry ("I already have this"), or None when there is none (or it has
    gone, in which case the ordinary search goes on). present: the publisher's file (hash matched) or one the person said to use
    anyway; checking: the hash is still being read; differs: not the published file."""
    p = model_choices.chosen(spec.id)
    if p is None:
        return None
    try:
        size = p.stat().st_size if p.is_file() else -1
    except OSError:
        size = -1
    if size < 0:
        return None
    key = os.path.normcase(str(p))
    chk = check_state(p, spec) if size == spec.size else "size"
    anyway = key in acc
    state = "present" if chk == "ok" or anyway else ("checking" if chk == "unchecked" else "differs")
    placed = (model_choices.load().get(spec.id) or {}).get("placed") or {}
    return {"state": state, "path": str(p), "reused": True, "check": chk, "accepted": anyway, "chosen": True,
            "yours": chk in ("differs", "size"), **({"source": placed["source"], "placed": placed.get("mode")} if placed.get("source") else {})}


def choice_usable(path: Path) -> bool:
    """Whether a chosen file may be used by the graphs now: its hash matched, or the person said to use it anyway."""
    return check_state(path, None) == "ok" or os.path.normcase(str(path)) in accepted_paths()  # type: ignore[arg-type]


def current_alias_table() -> dict[str, list[model_choices.Alt]]:
    return model_choices.alias_table(choice_usable)


def row_info(file_name: str) -> dict[str, Any]:
    """What a model or LoRA row of the System page needs to show next to the panel's: the list entry the file belongs to (so the
    "Set model location" button works the same there) and which file is in use: the app's own, the publisher's file under another
    name, or the person's own file. {} when the file is not in the list."""
    cat = downloader.catalogue()
    for section, kind in (("models", "model"), ("addons", "addon")):
        for key, d in (cat.get(section) or {}).items():
            if not isinstance(d, dict):
                continue
            name = str(d.get("save_as") or Path(str(d.get("dest") or "")).name)
            if name.casefold() != str(file_name).casefold():
                continue
            spec_id = f"{kind}:{key}"
            out: dict[str, Any] = {"spec_id": spec_id, "can_choose": model_choices.loader_of_file(name) is not None, "in_use": {"kind": "app"}}
            p = model_choices.chosen(spec_id)
            if p is not None and p.is_file() and choice_usable(p):
                same = check_state(p, None) == "ok"  # type: ignore[arg-type]
                out["in_use"] = {"kind": "renamed" if same else "own", "path": str(p)}
            else:
                try:  # no choice: an earlier copy may stand in for the publisher's file (the panel's answer, the same words)
                    root = downloader.models_root()
                    spec = all_specs_by_id(root).get(spec_id) if root else None
                    got = item_state(spec, root, read_roots(root)) if spec else {}
                except Exception:  # noqa: BLE001
                    got = {}
                if got.get("renamed"):
                    out["in_use"] = {"kind": "renamed", "path": got["path"]}
                elif got.get("older") and got.get("state") in ("missing", "checking"):
                    out["in_use"] = {"kind": "earlier", "path": got["older"]}
            return out
    return {}


def _item_state(spec: Spec, root: Path, roots: dict[str, Any]) -> dict[str, Any]:
    """Where this file is and in what state: present (own place), reused (elsewhere ComfyUI reads), differs, or missing."""
    acc = accepted_paths()
    mine = _chosen_state(spec, acc)
    if mine is not None:
        return mine
    found_size_wrong: Path | None = None
    for p in candidates(spec, root, roots) + candidates_below(spec, root, roots):
        try:
            size = p.stat().st_size if p.is_file() else -1
        except OSError:
            size = -1
        if size < 0:
            continue
        if size == spec.size:
            chk = check_state(p, spec)
            state = "differs" if chk == "differs" and os.path.normcase(str(p)) not in acc else "present"
            return {"state": state, "path": str(p), "reused": p != spec.dest, "check": chk, "accepted": os.path.normcase(str(p)) in acc}
        found_size_wrong = found_size_wrong or p
    if found_size_wrong is not None:
        return {"state": "differs", "path": str(found_size_wrong), "reused": found_size_wrong != spec.dest, "check": "size", "accepted": False}
    out = {"state": "missing", "path": str(spec.dest), "reused": False, "check": "", "accepted": False}
    older = _older_copy(spec, root, roots)
    if older is not None:
        out["older"] = str(older)  # not here, but an earlier copy is, and the app uses it (zimage_fallback)
        try:
            same_size = older.stat().st_size == spec.size
        except OSError:
            same_size = False
        if same_size:
            chk = check_state(older, spec)
            if chk == "ok":  # the checksum says it is the publisher's file, only under another name
                return {"state": "present", "path": str(older), "reused": True, "check": "ok", "accepted": False, "renamed": True}
            if chk == "unchecked":
                out["pending"] = str(older)  # being read in the background (set_status starts it)
                out["state"] = "checking"
    return out


def _older_copy(spec: Spec, root: Path, roots: dict[str, Any]) -> Path | None:
    """An earlier file the list names for this one (`older`), looked for like any other: exact paths in every models folder."""
    for rel in spec.older:
        for p in candidates(replace(spec, dest=root / rel, older=[]), root, roots):
            try:
                if p.is_file():
                    return p
            except OSError:
                continue
    return None


def all_specs_by_id(root: Path | None) -> dict[str, Spec]:
    return {s.id: s for s in downloader.specs_for(root)}


def set_status(root: Path | None) -> dict[str, Any]:
    roots = read_roots(root) if root else {"typed": {}, "bases": []}
    by_id = all_specs_by_id(root)
    out = []
    pending: list[tuple[Spec, Path]] = []
    for st in sets_list():
        items = []
        remaining = 0
        covered_n = covered_size = 0
        for sid in st.get("items") or []:
            spec = by_id.get(sid)
            if spec is None:
                continue
            s = item_state(spec, root, roots) if root else {"state": "missing", "path": str(spec.dest), "reused": False, "check": "", "accepted": False}
            # An earlier copy the app uses stands in for the publisher's file: that file is optional, never "to get" and never what
            # makes a set look unfinished.
            covered = s["state"] in ("missing", "checking") and bool(s.get("older"))
            if s.get("pending"):
                pending.append((spec, Path(s["pending"])))
            if s["state"] == "missing" and not covered:
                remaining += spec.size
            if covered:
                covered_n += 1
                covered_size += spec.size
            items.append({"id": sid, "name": spec.name, "size": spec.size, "gate": gate_of(sid), "page": spec.page, "licence": spec.licence,
                          "publisher": spec.publisher, "note": spec.note, "can_choose": model_choices.can_choose(spec), "covered": covered, **s})
        job = downloader.job_status("set-" + st["id"])
        need = int(remaining * 1.1) + WORKING_SPACE if remaining else 0
        free = downloader.free_bytes(root) if root and root.exists() else None
        out.append({"id": st["id"], "name": st.get("name", st["id"]), "what": st.get("what", ""), "required": bool(st.get("required")),
                    "gate": st.get("gate", ""), "items": items, "total": sum(i["size"] for i in items), "remaining": remaining,
                    "need": need, "free": free, "enough_space": free is None or not need or free >= need, "job": job,
                    "covered": covered_n, "covered_size": covered_size,
                    "complete": bool(items) and all(i["state"] == "present" or i["covered"] for i in items)})
    if pending and _verify_state["state"] != "working":
        verify_all(pending)  # read in the background, once; the answer is kept for next time
    return {"sets": out, "gates": {k: {"notice": v.get("notice", []), "phrase": v.get("phrase", "")} for k, v in gates().items()},
            "models_folder": str(root) if root else "", "reads": [str(p) for p in roots["bases"]] if root else [],
            "verify": dict(_verify_state), "copying": {k: dict(v) for k, v in _copies.items() if v.get("state") in ("working", "failed")}}


def start_set(set_id: str, typed: dict[str, str], opener: Callable[[str, int], Any] | None = None, wait: bool = False,
              only: list[str] | None = None) -> downloader.Job:
    root = downloader.models_root()
    if root is None:
        raise DownloadError("no_comfy", "No ComfyUI is linked yet, so there is nowhere to put the models.", "Link your ComfyUI first (Find or use my ComfyUI).")
    st = next((s for s in sets_list() if s["id"] == set_id), None)
    if st is None:
        raise DownloadError("no_set", "There is no such set.", "Press Look again.")
    roots = read_roots(root)
    by_id = all_specs_by_id(root)
    want = [by_id[i] for i in st.get("items") or [] if i in by_id]
    todo = []
    for s in want:
        got = item_state(s, root, roots)
        if got["state"] not in ("missing", "checking") or (got["state"] == "checking" and not got.get("older")):
            continue
        if only is not None and s.id not in only:
            continue  # asked for particular files (the optional "get the publisher's file")
        if only is None and got.get("older"):
            continue  # an earlier copy stands in: not fetched unless asked for
        todo.append(s)
    ok_gates = accepted_for(typed)
    for s in todo:
        g = gate_of(s.id)
        if g and g not in ok_gates:
            d = gates().get(g, {})
            raise DownloadError("licence", f"{s.name} comes with a licence that has to be accepted first.",
                                f"Read it and type the sentence \"{d.get('phrase', '')}\" yourself in the box; the app never accepts for you.")
    remaining = sum(s.size for s in todo)
    need = int(remaining * 1.1) + WORKING_SPACE
    if remaining and downloader.free_bytes(root) < need:
        raise downloader.no_space(root, need)
    accepted = {s.id for s in todo if gate_of(s.id) in ok_gates or not gate_of(s.id)}
    return downloader.start_job("set-" + set_id, todo, accepted, opener, wait)


# --- the test character -------------------------------------------------------------------------------------------------------


def test_character_file() -> Path:
    return downloader.project_dir() / "local" / "test_character.json"


def test_character_state() -> dict[str, Any]:
    try:
        d = json.loads(test_character_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def record_test_character(asset_id: str, passed: bool | None) -> dict[str, Any]:
    """The panel makes the test character through the ordinary asset routes and tells us how it went; `passed` stays None
    until the autopilot has finished. The setup card does not say Ready for the 3D steps before it is True."""
    d = {"asset_id": asset_id, "passed": passed}
    test_character_file().parent.mkdir(parents=True, exist_ok=True)
    test_character_file().write_text(json.dumps(d), encoding="utf-8")
    return d


# --- HTTP ---------------------------------------------------------------------------------------------------------------

router = APIRouter()


class StartIn(BaseModel):
    phrases: dict[str, str] = Field(default_factory=dict)
    only: list[str] | None = None  # the entries to get (the optional publisher's files), instead of the whole set


class IdIn(BaseModel):
    id: str


class TestIn(BaseModel):
    asset_id: str
    passed: bool | None = None


@router.get("/api/model-sets")
def get_sets() -> dict[str, Any]:
    return {**set_status(downloader.models_root()), "test_character": test_character_state()}


@router.post("/api/model-sets/{set_id}/start")
def post_start(set_id: str, body: StartIn) -> dict[str, Any]:
    try:
        return start_set(set_id, body.phrases, only=body.only).public()
    except DownloadError as e:
        raise HTTPException(400, f"{e.message} {e.fix}".strip()) from None


@router.post("/api/model-sets/{set_id}/stop")
def post_stop(set_id: str) -> dict[str, Any]:
    downloader.stop_job("set-" + set_id)
    return {"job": downloader.job_status("set-" + set_id)}


@router.post("/api/model-sets/verify")
def post_verify() -> dict[str, Any]:
    root = downloader.models_root()
    if root is None:
        raise HTTPException(400, "No ComfyUI is linked yet.")
    roots = read_roots(root)
    pairs = []
    for spec in downloader.specs_for(root):
        s = item_state(spec, root, roots)
        if s["state"] != "missing" and s["check"] != "size":
            pairs.append((spec, Path(s["path"])))
    return verify_all(pairs)


class ChooseIn(BaseModel):
    id: str
    path: str


class PlaceIn(BaseModel):
    id: str
    path: str
    mode: str  # "link" (a hard link, same drive) or "copy"


class FolderIn(BaseModel):
    path: str
    ids: list[str] | None = None  # the entries to take (the preview's matches); all of them when left out


@router.post("/api/model-sets/choose")
def post_choose(body: ChooseIn) -> dict[str, Any]:
    """"I already have this": use the file at `path` for this entry instead of downloading it. Refused with a reason when it is
    the wrong kind of file, far from the right size, or in a folder the engine does not read. Its hash is checked in the
    background."""
    root = downloader.models_root()
    spec = all_specs_by_id(root).get(body.id) if root else None
    if spec is None:
        raise HTTPException(404, "That file is not in the list.")
    roots = read_roots(root)
    try:
        got = model_choices.choose(spec, body.path, root, roots)
    except DownloadError as e:
        if e.code == "not_readable":
            # Not refused: the person is told what can be done (link it, copy it, or add its folder themselves).
            src = Path(body.path.strip().strip('"'))
            return {"needs_place": {**model_choices.placement_options(spec, src), "message": f"{e.message} {e.fix}".strip()}, "warning": ""}
        raise HTTPException(400, f"{e.message} {e.fix}".strip()) from None
    p = Path(body.path.strip().strip('"'))
    if p.stat().st_size == spec.size:
        verify_all([(spec, p)])
    return {"item": item_state(spec, root, roots), "verify": dict(_verify_state), "warning": got.get("warning", "")}


def scan_folder(set_id: str, folder_text: str) -> dict[str, Any]:
    """"I already have these": look in a folder the person chose (to six levels, model files only) for the files of one set. A file
    matches an entry by its name (any size: a different size is warned about later), or, failing that, by its exact size when only
    one file of that size is there (a renamed copy; the hash settles it). Nothing is saved: the person sees the matches first."""
    root = downloader.models_root()
    st = next((s for s in sets_list() if s["id"] == set_id), None)
    if st is None or root is None:
        raise DownloadError("no_set", "There is no such set, or no ComfyUI is linked yet.", "Press Look again.")
    folder = Path(folder_text.strip().strip('"'))
    if not folder.is_dir():
        raise DownloadError("no_folder", "That is not a folder.", "Choose the folder your model files are in.")
    roots = read_roots(root)
    by_id = all_specs_by_id(root)
    files = _listing(folder, depth=6, limit=200000)
    by_size: dict[int, list[Path]] = {}
    for paths in files.values():
        for fp in paths:
            if fp.suffix.lower() in model_choices.EXTENSIONS:
                try:
                    by_size.setdefault(fp.stat().st_size, []).append(fp)
                except OSError:
                    continue
    found, missing = [], []
    for sid in st.get("items") or []:
        spec = by_id.get(sid)
        if spec is None or not model_choices.can_choose(spec):
            continue
        named = [x for x in files.get(spec.dest.name.casefold(), []) if x.suffix.lower() in model_choices.EXTENSIONS]
        same = [x for x in by_size.get(spec.size, []) if x not in named]
        pick, how = (None, "")
        if named:
            pick, how = next((x for x in named if x.stat().st_size == spec.size), named[0]), "name"
        elif len(same) == 1:
            pick, how = same[0], "size"
        if pick is None:
            missing.append({"id": sid, "name": spec.name})
            continue
        problem = model_choices.refusal(spec, pick)
        readable = model_choices.readable_by_engine(pick, root, roots)
        can_link = not readable and model_choices.placement_options(spec, pick)["can_link"]
        already = _item_state_quiet(spec, root, roots)
        found.append({"id": sid, "name": spec.name, "path": str(pick), "how": how, "size_matches": pick.stat().st_size == spec.size,
                      "problem": problem, "readable": readable, "can_link": can_link, "already": already in ("present",),
                      "ok": not problem and (readable or can_link)})
    return {"folder": str(folder), "found": found, "missing": missing}


def _item_state_quiet(spec: Spec, root: Path, roots: dict[str, Any]) -> str:
    return item_state(spec, root, roots)["state"]


@router.post("/api/model-sets/{set_id}/scan-folder")
def post_scan_folder(set_id: str, body: FolderIn) -> dict[str, Any]:
    """The preview for "I already have these": which files of the set the chosen folder holds. Saves nothing."""
    try:
        return scan_folder(set_id, body.path)
    except DownloadError as e:
        raise HTTPException(400, f"{e.message} {e.fix}".strip()) from None


@router.post("/api/model-sets/{set_id}/use-folder")
def post_use_folder(set_id: str, body: FolderIn) -> dict[str, Any]:
    """Take the matches of a folder (all, or the entries in `ids`) as the person's files, each like a single choice: refused ones
    are reported with their reason, the rest are hashed in the background."""
    try:
        scan = scan_folder(set_id, body.path)
    except DownloadError as e:
        raise HTTPException(400, f"{e.message} {e.fix}".strip()) from None
    root = downloader.models_root()
    roots = read_roots(root)
    by_id = all_specs_by_id(root)
    taken, left = [], []
    for f in scan["found"]:
        if body.ids is not None and f["id"] not in body.ids:
            continue
        spec = by_id[f["id"]]
        try:
            if f["readable"]:
                model_choices.choose(spec, f["path"], root, roots)
            elif f["can_link"]:
                model_choices.place_link(spec, Path(f["path"]), root, roots)  # same drive: a hard link, nothing is copied
            else:
                model_choices.choose(spec, f["path"], root, roots)  # says why it cannot be used
        except DownloadError as e:
            left.append({"id": f["id"], "name": f["name"], "reason": f"{e.message} {e.fix}".strip()})
            continue
        taken.append({"id": f["id"], "name": f["name"], "path": f["path"]})
    exact = [(by_id[x["id"]], Path(model_choices.chosen(x["id"]) or x["path"])) for x in taken if Path(x["path"]).stat().st_size == by_id[x["id"]].size]
    if exact:
        verify_all(exact)
    return {"taken": taken, "left": left, "verify": dict(_verify_state)}


_copies: dict[str, dict[str, Any]] = {}  # spec id -> {"state": working|done|failed, "done": bytes, "total": bytes, "error": str}


def _copy_worker(spec: Spec, source: Path, target: Path, root: Path, roots: dict[str, Any]) -> None:
    st = _copies[spec.id]
    part = target.with_name(target.name + ".part")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as r, part.open("wb") as w:
            while True:
                chunk = r.read(8 * 1024 * 1024)
                if not chunk:
                    break
                w.write(chunk)
                st["done"] += len(chunk)
        os.replace(part, target)
        model_choices.choose(spec, str(target), root, roots, placed={"mode": "copy", "source": str(source)})
        if target.stat().st_size == spec.size:
            verify_all([(spec, target)])
        st["state"] = "done"
    except (OSError, DownloadError) as e:
        st["state"] = "failed"
        st["error"] = getattr(e, "message", None) or str(e)
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass


@router.post("/api/model-sets/place")
def post_place(body: PlaceIn) -> dict[str, Any]:
    """Make a file the engine cannot read where it is available to it: a hard link (same drive; nothing is copied) or a copy (any
    drive; started in the background, space-checked, and only with the person's say-so). The app never changes ComfyUI's settings."""
    root = downloader.models_root()
    spec = all_specs_by_id(root).get(body.id) if root else None
    if spec is None:
        raise HTTPException(404, "That file is not in the list.")
    roots = read_roots(root)
    source = Path(body.path.strip().strip('"'))
    try:
        if body.mode == "link":
            got = model_choices.place_link(spec, source, root, roots)
            if source.stat().st_size == spec.size:
                verify_all([(spec, Path(got["path"]))])
            return {"item": item_state(spec, root, roots), "warning": got.get("warning", "")}
        if body.mode == "copy":
            why = model_choices.refusal(spec, source)
            if why:
                raise DownloadError("refused", why, "Choose another file, or use the download button.")
            opts = model_choices.placement_options(spec, source)
            if not opts["can_copy"]:
                raise DownloadError("no_space", f"There is not enough room for a copy ({size_text(opts['size'])} needed, {size_text(opts['free'])} free).",
                                    "Free some space, or add the folder to your ComfyUI's extra model paths yourself.")
            if _copies.get(spec.id, {}).get("state") == "working":
                raise DownloadError("busy", "That copy is already running.", "Wait for it to finish.")
            _copies[spec.id] = {"state": "working", "done": 0, "total": opts["size"], "error": ""}
            threading.Thread(target=_copy_worker, args=(spec, source, Path(opts["target"]), root, roots), daemon=True, name="copy-model").start()
            return {"copying": _copies[spec.id]}
    except DownloadError as e:
        raise HTTPException(400, f"{e.message} {e.fix}".strip()) from None
    raise HTTPException(400, "Choose link or copy.")


def size_text(n: int) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{max(1, round(n / 1e6))} MB"


@router.post("/api/model-sets/unchoose")
def post_unchoose(body: IdIn) -> dict[str, Any]:
    """Stop using the file that was pointed at; the person's file is never touched."""
    return {"removed": model_choices.unchoose(body.id)}


@router.post("/api/model-sets/accept")
def post_accept(body: IdIn) -> dict[str, Any]:
    root = downloader.models_root()
    spec = all_specs_by_id(root).get(body.id) if root else None
    if spec is None:
        raise HTTPException(404, "That file is not in the list.")
    s = item_state(spec, root, read_roots(root))
    if s["state"] == "missing":
        raise HTTPException(400, "That file is not there.")
    accept_path(Path(s["path"]))
    return {"ok": True}


@router.post("/api/model-sets/test_character")
def post_test_character(body: TestIn) -> dict[str, Any]:
    return record_test_character(body.asset_id, body.passed)
