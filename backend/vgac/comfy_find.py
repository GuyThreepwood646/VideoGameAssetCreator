"""Find and link the person's own ComfyUI (setup card, phase 5a). Read-only until the person says "Use this one".

Look: ComfyUI Desktop in both of its layouts (the newer `Comfy Desktop\\installations.json` and the older
`%APPDATA%\\ComfyUI\\config.json`), portable folders, manual installs, and copies that are running right now (asked on
the usual ports, then the process command line). Nothing here writes into a found ComfyUI, starts it or stops it.

Choose: B ("use my ComfyUI as the engine") is linked here: the engine settings of each graphics card point at it. A (share
the models, separate engine) and C (a fresh VGAC copy) need VGAC's own ComfyUI install, which is a later step; they are
listed with that said plainly.

Node packs and Python packages: VGAC needs three node packs in the linked ComfyUI. Before anything is changed,
`pip install --dry-run` reports which of the person's installed Python packages the packs' requirements would change.
Nothing is changed until `apply_packs` is called with the person's go-ahead.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import config, downloader, setup_settings
from .downloader import DownloadError

log = logging.getLogger("vgac.comfy_find")

PORTS = (8188, 8189, 8190, 8191, 8000)
PYTHON_PLACES = ("standalone-env/python.exe", "python_embeded/python.exe", "ComfyUI/.venv/Scripts/python.exe",
                 ".venv/Scripts/python.exe", "venv/Scripts/python.exe", "../.venv/Scripts/python.exe", "../venv/Scripts/python.exe")
CHOICES = [
    {"id": "A", "name": "Share my models, separate engine", "available": True, "recommended": True,
     "text": "VGAC installs its own ComfyUI (about 5 to 6 GB) and points it at your models folder, so the large model files are not copied. "
             "Your ComfyUI and its models folder are only read, never changed."},
    {"id": "B", "name": "Use my ComfyUI as the engine", "available": True, "recommended": False,
     "text": "VGAC starts your ComfyUI itself. It needs three node packs in your custom_nodes folder and their Python packages in your Python; "
             "that can change package versions your other workflows rely on. VGAC shows what would change first and changes nothing until you press Go ahead."},
    {"id": "C", "name": "A fresh VGAC ComfyUI, nothing shared", "available": True, "recommended": False,
     "text": "VGAC installs its own ComfyUI (about 5 to 6 GB). Everything separate, and every model downloaded again."},
]


# --- where to look -------------------------------------------------------------------------------------------------


def _http_stats(port: int) -> dict[str, Any] | None:
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/system_stats", timeout=1.0) as r:
            d = json.loads(r.read(500_000).decode("utf-8", errors="replace"))
        return d if isinstance(d, dict) else None
    except Exception:  # noqa: BLE001  (refused, timeout and not-JSON all mean "not a ComfyUI here")
        return None


def _process_lines() -> list[str]:
    """Command lines of running Python programs (PowerShell; empty when it cannot ask)."""
    cmd = ("Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | ForEach-Object { $_.CommandLine }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=15, check=False,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in out.splitlines() if "main.py" in line]


@dataclass
class Env:
    """Everything the finders read from the machine; tests replace the fields."""

    appdata: Path | None = field(default_factory=lambda: Path(os.environ["APPDATA"]) if os.environ.get("APPDATA") else None)
    localappdata: Path | None = field(default_factory=lambda: Path(os.environ["LOCALAPPDATA"]) if os.environ.get("LOCALAPPDATA") else None)
    roots: list[Path] = field(default_factory=lambda: [p for p in (Path.home(), Path.home() / "Desktop", Path.home() / "Downloads",
                                                                    Path.home() / "Documents", Path("C:/")) if p.exists()])
    probe: Callable[[int], dict[str, Any] | None] = _http_stats
    process_lines: Callable[[], list[str]] = _process_lines


def cid(folder: Path) -> str:
    return "c-" + hashlib.sha1(os.path.normcase(str(folder)).encode("utf-8")).hexdigest()[:8]


def _read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _find_python(folder: Path) -> Path | None:
    for rel in PYTHON_PLACES:
        p = (folder / rel)
        if p.is_file():
            return p.resolve()
    return None


_VERSION_RE = re.compile(r"__version__\s*=\s*[\"']([^\"']+)[\"']")


def comfy_version(app_dir: Path) -> str:
    try:
        m = _VERSION_RE.search((app_dir / "comfyui_version.py").read_text(encoding="utf-8"))
        return m.group(1) if m else ""
    except OSError:
        return ""


def _candidate(folder: Path, kind: str, source: str, name: str = "", main: str = "ComfyUI/main.py", python: Path | None = None,
               extra_paths: Path | None = None, models: Path | None = None) -> dict[str, Any]:
    app = (folder / main).parent
    py = python or _find_python(folder)
    return {"id": cid(folder), "kind": kind, "source": source, "name": name or folder.name, "folder": str(folder), "main": main,
            "python": str(py) if py else "", "version": comfy_version(app),
            "models": str(models if models else app / "models"), "extra_model_paths": str(extra_paths) if extra_paths and extra_paths.is_file() else "",
            "has_trellis": (app / "comfy_extras" / "nodes_trellis2.py").is_file(), "running": None}


def desktop_new(env: Env) -> list[dict[str, Any]]:
    if not env.appdata:
        return []
    base = env.appdata / "Comfy Desktop"
    data = _read_json(base / "installations.json")
    out = []
    for inst in data if isinstance(data, list) else []:
        if not isinstance(inst, dict) or inst.get("sourceId") == "cloud" or not inst.get("installPath"):
            continue
        folder = Path(str(inst["installPath"]))
        if not (folder / "ComfyUI" / "main.py").is_file():
            continue
        c = _candidate(folder, "desktop", "ComfyUI Desktop", str(inst.get("name") or folder.name),
                       extra_paths=base / "instance-model-paths" / f"{inst.get('id', '')}.yaml")
        if not c["version"] and inst.get("version"):
            c["version"] = str(inst["version"]).lstrip("v")
        out.append(c)
    return out


def desktop_old(env: Env) -> list[dict[str, Any]]:
    """The older Desktop: %APPDATA%\\ComfyUI\\config.json names `basePath` (models, custom_nodes, .venv); the program itself
    sits in the app's own folder."""
    if not env.appdata:
        return []
    cfg = _read_json(env.appdata / "ComfyUI" / "config.json")
    base = Path(str(cfg["basePath"])) if isinstance(cfg, dict) and cfg.get("basePath") else None
    if not base or not base.is_dir():
        return []
    app = None
    for root in [env.localappdata / "Programs" / "ComfyUI" / "resources" / "ComfyUI"] if env.localappdata else []:
        if (root / "main.py").is_file():
            app = root
    py = base / ".venv" / "Scripts" / "python.exe"
    if app is None:
        c = _candidate(base, "desktop", "ComfyUI Desktop (older layout)", "ComfyUI Desktop", python=py if py.is_file() else None,
                       extra_paths=env.appdata / "ComfyUI" / "extra_models_config.yaml", models=base / "models")
        c["problems"] = ["The program files of this ComfyUI Desktop were not found, so the app cannot start it."]
        return [c]
    c = _candidate(app.parent, "desktop", "ComfyUI Desktop (older layout)", "ComfyUI Desktop", main=f"{app.name}/main.py",
                   python=py if py.is_file() else None, extra_paths=env.appdata / "ComfyUI" / "extra_models_config.yaml", models=base / "models")
    return [c]


def portable_and_manual(env: Env) -> list[dict[str, Any]]:
    out = []
    seen: set[str] = set()
    for root in env.roots:
        try:
            kids = [k for k in root.iterdir() if k.is_dir() and "comfy" in k.name.lower()]
        except OSError:
            continue
        for k in kids:
            if (k / "run_nvidia_gpu.bat").is_file() and (k / "ComfyUI" / "main.py").is_file():
                c = _candidate(k, "portable", "a portable ComfyUI folder")
            elif (k / "ComfyUI" / "main.py").is_file() and _find_python(k):
                c = _candidate(k, "manual", "a ComfyUI folder")
            elif (k / "main.py").is_file() and (k / "comfy").is_dir() and (k / "custom_nodes").is_dir():
                py = _find_python(k)
                c = _candidate(k, "manual", "a ComfyUI folder", main="main.py", python=py)
            else:
                continue
            if c["id"] not in seen:
                seen.add(c["id"])
                out.append(c)
    return out


def _relative_main(line: str, end: int) -> Path | None:
    """ComfyUI Desktop starts its engines as `<install>\\ComfyUI\\.venv\\Scripts\\python.exe -s ComfyUI\\main.py`: main.py is
    named relative to the install folder, so find the install folder among the parents of that python.exe."""
    token = re.split(r"[\s\"]", line[:end + len("main.py")])[-1]
    py = re.match(r'\s*"?([A-Za-z]:[\\/][^"]*?\.exe)', line)
    if not token or not py:
        return None
    for parent in Path(py.group(1)).parents:
        if (parent / token).is_file():
            return parent / token
    return None


def running_now(env: Env) -> list[dict[str, Any]]:
    """What answers on the usual ports, and the install folder of each (from the process command line)."""
    answering = []
    for port in PORTS:
        stats = env.probe(port)
        if stats is not None:
            devs = [str(d.get("name", "")) for d in (stats.get("devices") or []) if isinstance(d, dict)]
            answering.append({"port": port, "devices": devs})
    folders: list[tuple[Path, str]] = []
    for line in env.process_lines():
        end = line.find("main.py")
        starts = [m.start() for m in re.finditer(r"[A-Za-z]:[\\/]", line[:end])]
        if end <= 0 or not starts:
            continue
        p = Path(line[max(starts):end + len("main.py")].strip('"'))
        if not p.is_file():
            p = _relative_main(line, end) or p
        if all(os.path.normcase(str(p)) != os.path.normcase(str(q)) for q, _ in folders):  # a launcher and its child, or two engines of one install
            folders.append((p, line))
    return [{"answering": answering, "mains": [str(p) for p, _ in folders]}] if answering or folders else []


def find_comfy(env: Env | None = None) -> dict[str, Any]:
    env = env or Env()
    cands = desktop_new(env) + desktop_old(env) + portable_and_manual(env)
    by_folder = {os.path.normcase(c["folder"]): c for c in cands}
    run = running_now(env)
    answering = run[0]["answering"] if run else []
    for main in (run[0]["mains"] if run else []):
        for c in cands:
            if os.path.normcase(str(Path(c["folder"]) / c["main"])) == os.path.normcase(main):
                c["running"] = [a["port"] for a in answering] or True
    # a running copy whose folder none of the above knew about
    known = {os.path.normcase(str(Path(c["folder"]) / c["main"])) for c in cands}
    for main in (run[0]["mains"] if run else []):
        if os.path.normcase(main) not in known:
            p = Path(main)
            folder = p.parent.parent if p.parent.name.lower() == "comfyui" and (p.parent.parent / p.parent.name / "main.py").is_file() else p.parent
            c = _candidate(folder, "running", "running now", main=str(p.relative_to(folder)).replace("\\", "/") if p.is_relative_to(folder) else "main.py")
            c["running"] = [a["port"] for a in answering] or True
            if os.path.normcase(c["folder"]) not in by_folder:
                cands.append(c)
    cands.sort(key=lambda c: (not c["has_trellis"], c["kind"] != "desktop", c["name"].lower()))
    return {"found": cands, "answering": answering, "choices": CHOICES}


# --- checking -----------------------------------------------------------------------------------------------------------


def _run_python(py: Path, code: str, timeout: int = 90) -> str:
    proc = subprocess.run([str(py), "-c", code], capture_output=True, text=True, timeout=timeout, check=False,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return proc.stdout or ""


TORCH_CODE = "import torch;print('VGAC', torch.__version__, torch.cuda.is_available())"


def check_candidate(c: dict[str, Any], runner: Callable[[Path, str], str] | None = None) -> dict[str, Any]:
    """The checks of the plan: main.py exists, its Python runs and imports torch with CUDA, the ComfyUI is new enough for
    the built-in TRELLIS.2 nodes. Returns {ok, problems, torch}."""
    problems: list[str] = list(c.get("problems") or [])
    folder = Path(c["folder"])
    if not (folder / c["main"]).is_file():
        problems.append(f"main.py was not found at {folder / c['main']}.")
    py = Path(c["python"]) if c.get("python") else None
    torch = ""
    if not py or not py.is_file():
        problems.append("The Python that runs this ComfyUI was not found.")
    else:
        try:
            out = (runner or _run_python)(py, TORCH_CODE)
        except (OSError, subprocess.SubprocessError) as e:
            out = ""
            log.info("python check failed: %s", e)
        m = re.search(r"VGAC (\S+) (True|False)", out)
        if not m:
            problems.append("Its Python did not start, or it does not have torch.")
        else:
            torch = m.group(1)
            if m.group(2) != "True":
                problems.append("Its torch cannot use the graphics card (no CUDA).")
    if not c.get("has_trellis"):
        problems.append("This ComfyUI is too old: it lacks the built-in 3D (TRELLIS.2) nodes. Update it, or let VGAC use its own.")
    return {"ok": not problems, "problems": problems, "torch": torch}


# --- linking (choice B) ------------------------------------------------------------------------------------------------


def find_by_id(cid_: str, env: Env | None = None) -> dict[str, Any]:
    for c in find_comfy(env)["found"]:
        if c["id"] == cid_:
            return c
    raise DownloadError("not_found", "That ComfyUI is not there any more.", "Press Look again.")


def link_comfy(c: dict[str, Any], cards: dict[str, int] | None = None, runner: Callable[[Path, str], str] | None = None) -> dict[str, Any]:
    """Choice B: point every instance's engine at this ComfyUI. `cards` maps an instance name to its graphics card number."""
    res = check_candidate(c, runner)
    if not res["ok"]:
        raise setup_settings.SettingError("That ComfyUI cannot be used yet: " + " ".join(res["problems"]))
    return setup_settings.set_engines(c["folder"], c["python"], c["main"], c.get("extra_model_paths") or None, cards or {})


def link_choice(choice: str) -> None:
    if choice in ("A", "C"):
        raise setup_settings.SettingError("That choice is VGAC's own ComfyUI: start it with Install VGAC's ComfyUI in this panel.")
    if choice != "B":
        raise setup_settings.SettingError("Choose A, B or C.")


# --- node packs and Python packages ----------------------------------------------------------------------------------


def pack_list() -> list[dict[str, Any]]:
    return [dict(v, id=k) for k, v in (downloader.catalogue().get("node_packs") or {}).items() if isinstance(v, dict)]


def _norm(name: str) -> str:
    return re.sub(r"[-_.\s]+", "-", name).lower()


def pack_present(app_dir: Path, pack: dict[str, Any]) -> str:
    """The folder name of this pack in custom_nodes (any capitalisation), or ''."""
    cn = app_dir / "custom_nodes"
    want = {_norm(str(pack.get("folder", ""))), _norm(str(pack.get("name", "")))}
    try:
        for d in cn.iterdir():
            if d.is_dir() and _norm(d.name) in want:
                return d.name
    except OSError:
        pass
    return ""


def left_out(c: dict[str, Any]) -> dict[str, str]:
    """Packs VGAC's own install leaves out on purpose (pack id -> plain reason); empty for anyone else's ComfyUI."""
    from .comfy_install import MARKER, MADE_BY

    try:
        mine = json.loads((Path(c["folder"]) / MARKER).read_text(encoding="utf-8")).get("made_by") == MADE_BY
    except (OSError, ValueError, AttributeError):
        mine = False
    return {str(k): str(v) for k, v in ((downloader.catalogue().get("comfyui_install") or {}).get("left_out") or {}).items()} if mine else {}


def packs_state(c: dict[str, Any]) -> list[dict[str, Any]]:
    app = (Path(c["folder"]) / c["main"]).parent
    out = left_out(c)
    return [{"id": p["id"], "name": p.get("name", p["id"]), "what": p.get("what", ""), "licence": p.get("licence", ""),
             "present": bool(pack_present(app, p)), "folder": pack_present(app, p) or p.get("folder", ""),
             "left_out": out.get(p["id"], "")} for p in pack_list()]


def pack_cache(pack: dict[str, Any]) -> Path:
    return downloader.project_dir() / "local" / "tools" / "packs" / f"{pack['id']}.zip"


def pack_requirements(pack: dict[str, Any], zip_path: Path | None) -> str:
    """The text of the pack's requirements.txt: from this project's own folder (vgac-nodes) or from the fetched zip."""
    if pack.get("local"):
        p = downloader.project_dir() / str(pack["local"]) / "requirements.txt"
        return p.read_text(encoding="utf-8") if p.is_file() else ""
    if zip_path and zip_path.is_file():
        with zipfile.ZipFile(zip_path) as z:
            for n in z.namelist():
                parts = n.split("/")
                if len(parts) == 2 and parts[1] == "requirements.txt":
                    return z.read(n).decode("utf-8", errors="replace")
    return ""


def _pip_json(py: Path, args: list[str], runner: Callable[[list[str]], tuple[int, str]]) -> tuple[int, Any]:
    code, out = runner([str(py), "-m", "pip", *args])
    try:
        return code, json.loads(out) if out.strip() else None
    except ValueError:
        return code, None


def _real_pip(argv: list[str]) -> tuple[int, str]:
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=900, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return proc.returncode, proc.stdout or ""


def dry_run(py: Path, requirements_text: str, runner: Callable[[list[str]], tuple[int, str]] | None = None) -> dict[str, Any]:
    """`pip install --dry-run` of the requirements against this Python, compared with what it has now. Changes nothing."""
    runner = runner or _real_pip
    if not requirements_text.strip():
        return {"checked": True, "adds": [], "changes": [], "note": "No extra Python packages are needed."}
    code, installed = _pip_json(py, ["list", "--format=json"], runner)
    if code != 0 or not isinstance(installed, list):
        return {"checked": False, "adds": [], "changes": [], "note": "Its Python has no pip, so what would change could not be checked."}
    have = {_norm(str(p.get("name", ""))): str(p.get("version", "")) for p in installed if isinstance(p, dict)}
    with tempfile.TemporaryDirectory() as td:
        req = Path(td) / "requirements.txt"
        req.write_text(requirements_text, encoding="utf-8")
        rep = Path(td) / "report.json"
        code, _ = runner([str(py), "-m", "pip", "install", "--dry-run", "--quiet", "--report", str(rep), "-r", str(req)])
        report = _read_json(rep) if rep.is_file() else None
    if code != 0 or not isinstance(report, dict):
        return {"checked": False, "adds": [], "changes": [], "note": "pip could not work out what would change (a requirement may not be available)."}
    adds, changes = [], []
    for item in report.get("install") or []:
        meta = item.get("metadata") or {}
        name, new = str(meta.get("name", "")), str(meta.get("version", ""))
        old = have.get(_norm(name))
        if old is None:
            adds.append({"name": name, "to": new})
        elif old != new:
            changes.append({"name": name, "from": old, "to": new})
    return {"checked": True, "adds": adds, "changes": changes, "note": ""}


def fetch_pack(pack: dict[str, Any], opener: Callable[[str, int], Any] | None = None) -> Path | None:
    """Get a pack's pinned zip into VGAC's own cache (small, checked against the pinned SHA-256). None for a local pack."""
    if pack.get("local"):
        return None
    spec = downloader.Spec(id="pack-" + pack["id"], name=str(pack.get("name", pack["id"])), urls=[str(pack["zip"])], dest=pack_cache(pack),
                           sha256=str(pack.get("sha256", "")), page=str(pack.get("repo", "")), licence=str(pack.get("licence", "")), kind="addon")
    downloader.fetch(spec, __import__("threading").Event(), None, opener)
    return spec.dest


def packs_report(c: dict[str, Any], opener: Callable[[str, int], Any] | None = None,
                 pip: Callable[[list[str]], tuple[int, str]] | None = None) -> dict[str, Any]:
    """For each missing pack: fetch its pinned zip (VGAC's cache only), read its requirements, and say which of the person's
    packages would change. Changes nothing in their ComfyUI."""
    py = Path(c["python"]) if c.get("python") else None
    out = []
    all_req: list[str] = []
    skip = left_out(c)
    for p in pack_list():
        present = bool(pack_present((Path(c["folder"]) / c["main"]).parent, p))
        item = {"id": p["id"], "name": p.get("name", p["id"]), "present": present, "left_out": skip.get(p["id"], "")}
        if p["id"] in skip and not present:
            out.append(item)
            continue
        if not present:
            z = fetch_pack(p, opener)
            req = pack_requirements(p, z)
            item["requirements"] = [ln.strip() for ln in req.splitlines() if ln.strip() and not ln.strip().startswith("#")]
            all_req.append(req)
        out.append(item)
    combined = "\n".join(all_req)
    report = dry_run(py, combined, pip) if py and any(not i["present"] and not i["left_out"] for i in out) else {"checked": True, "adds": [], "changes": [], "note": "Nothing to add."}
    return {"packs": out, "python": report}


def apply_packs(c: dict[str, Any], opener: Callable[[str, int], Any] | None = None,
                pip: Callable[[list[str]], tuple[int, str]] | None = None) -> dict[str, Any]:
    """The person's go-ahead: put each missing pack into the ComfyUI's custom_nodes (never over a folder that is there) and
    install its requirements with that ComfyUI's Python."""
    app = (Path(c["folder"]) / c["main"]).parent
    py = Path(c["python"]) if c.get("python") else None
    if not py or not py.is_file():
        raise DownloadError("no_python", "The Python of this ComfyUI was not found, so nothing was installed.", "Check the ComfyUI in the list again.")
    runner = pip or _real_pip
    added: list[str] = []
    skip = left_out(c)
    for p in pack_list():
        if pack_present(app, p) or p["id"] in skip:
            continue
        target = app / "custom_nodes" / str(p.get("folder") or p["id"])
        if target.exists():
            continue
        if p.get("local"):
            shutil.copytree(downloader.project_dir() / str(p["local"]), target, ignore=shutil.ignore_patterns("__pycache__"))
            z = None
        else:
            z = fetch_pack(p, opener)
            tmp = app / "custom_nodes" / (str(p.get("folder")) + ".vgac-tmp")
            from .tools_setup import safe_unzip

            safe_unzip(z, tmp)  # type: ignore[arg-type]
            kids = [k for k in tmp.iterdir() if k.is_dir()]
            shutil.move(str(kids[0] if len(kids) == 1 else tmp), str(target))
            shutil.rmtree(tmp, ignore_errors=True)
        req = pack_requirements(p, z) if not p.get("local") else pack_requirements(p, None)
        if req.strip():
            rq = target / "requirements.txt"
            code, _ = runner([str(py), "-m", "pip", "install", "-r", str(rq if rq.is_file() else _write_tmp(req))])
            if code != 0:
                raise DownloadError("pip", f"{p.get('name', p['id'])} was added, but its Python packages did not install.", "Look at ComfyUI's own log, or press Try again.")
        downloader.record(str(p.get("name", p["id"])), target, "added")
        added.append(str(p.get("name", p["id"])))
    return {"added": added}


def _write_tmp(text: str) -> Path:
    fd, name = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    Path(name).write_text(text, encoding="utf-8")
    return Path(name)


# --- HTTP -------------------------------------------------------------------------------------------------------------

router = APIRouter()


class IdIn(BaseModel):
    id: str
    cards: dict[str, int] | None = None
    confirm: bool = False


def _bad(e: Exception) -> HTTPException:
    if isinstance(e, DownloadError):
        return HTTPException(400, f"{e.message} {e.fix}".strip())
    return HTTPException(400, str(e))


@router.get("/api/comfy")
def get_comfy() -> dict[str, Any]:
    s = config.get_settings()
    cur = {n: {"comfy_dir": str(i.engine.comfy_dir) if i.engine else "", "cuda_device": i.engine.cuda_device if i.engine else None}
           for n, i in s.instances.items()}
    return {**find_comfy(), "current": cur}


@router.post("/api/comfy/check")
def post_check(body: IdIn) -> dict[str, Any]:
    try:
        return check_candidate(find_by_id(body.id))
    except DownloadError as e:
        raise _bad(e) from None


@router.post("/api/comfy/link")
def post_link(body: IdIn) -> dict[str, Any]:
    try:
        return link_comfy(find_by_id(body.id), body.cards)
    except (DownloadError, setup_settings.SettingError) as e:
        raise _bad(e) from None


@router.post("/api/comfy/choice/{choice}")
def post_choice(choice: str) -> dict[str, Any]:
    try:
        link_choice(choice)
    except setup_settings.SettingError as e:
        raise _bad(e) from None
    return {"ok": True}


@router.post("/api/comfy/packs/report")
def post_report(body: IdIn) -> dict[str, Any]:
    try:
        return packs_report(find_by_id(body.id))
    except DownloadError as e:
        raise _bad(e) from None


@router.post("/api/comfy/packs/apply")
def post_apply(body: IdIn) -> dict[str, Any]:
    if not body.confirm:
        raise HTTPException(400, "Nothing is changed in your ComfyUI until you press Go ahead.")
    try:
        return apply_packs(find_by_id(body.id))
    except DownloadError as e:
        raise _bad(e) from None
