"""VGAC's own ComfyUI (comfy_install.py). Nothing real is downloaded, installed or started: the network is a fake opener,
uv and Python are a stand-in runner, the graphics cards are made up, the engine supervisor runs on a stand-in process
layer. The data folder has spaces and non-English letters in its name on purpose. A pretend "person's own ComfyUI" sits
beside it, and every test that installs checks that not one byte of it changed."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import shutil
import socket
import sys
import threading
import urllib.error
import zipfile
from pathlib import Path

import pytest
import yaml

from vgac import comfy_find, comfy_install as ci, config, db, downloader, firstrun, hardware_setup, instances
from vgac.downloader import DownloadError, Stopped
from vgac.hardware_setup import Card, Hardware

ROOT_DIR = Path(__file__).resolve().parents[2]
REAL_LIST = ROOT_DIR / "setup" / "downloads.json"
REAL_LOCK = ROOT_DIR / "setup" / "comfyui-lock.json"
ZIP_URL = "https://github.com/Comfy-Org/ComfyUI/archive/c0ffee.zip"
PACK_URL = "https://github.com/Rizzlord/ComfyUI-SkinToken/archive/beef.zip"

SOURCE = {"main.py": b"print('comfy')\n", "comfy/cli_args.py": b"# --cache-ram\n", "comfy_extras/nodes_trellis2.py": b"# 3d\n",
          "custom_nodes/example_node.py.example": b"# example\n", "requirements.txt": b"torch\n"}
WHEELS = {"alpha-1.0-py3-none-any.whl": b"A" * 3000, "beta-2.0-cp313-cp313-win_amd64.whl": b"B" * 5000,
          "torch-2.12.1+cu130-cp313-cp313-win_amd64.whl": b"T" * 9000}


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def make_zip(files: dict[str, bytes], top: str = "ComfyUI-c0ffee", method: int = zipfile.ZIP_DEFLATED, level: int | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method, compresslevel=level) as z:
        for k, v in files.items():
            z.writestr(f"{top}/{k}", v)
    return buf.getvalue()


ZIP = make_zip(SOURCE)
PACK_ZIP = make_zip({"__init__.py": b"NODE_CLASS_MAPPINGS = {}\n", "requirements.txt": b"trimesh\n"}, top="ComfyUI-SkinToken-beef")


class Resp:
    def __init__(self, data: bytes, start: int = 0, hook=None):
        self.data, self.pos, self.hook = data, start, hook
        self.status = 206 if start else 200
        self.headers = {"Content-Length": str(len(data) - start)}

    def read(self, n):
        if self.hook:
            self.hook(self.pos)
        chunk = self.data[self.pos:self.pos + min(n, 1000)]
        self.pos += len(chunk)
        return chunk

    def close(self):
        pass


class Net:
    """The fake internet: url -> bytes. `seen` lists (url, start); `down` makes every request fail."""

    def __init__(self, files: dict[str, bytes]):
        self.files, self.seen, self.down, self.hook = dict(files), [], False, None

    def __call__(self, url: str, start: int):
        self.seen.append((url, start))
        if self.down:
            raise urllib.error.URLError("no route to host")
        if url not in self.files:
            raise urllib.error.HTTPError(url, 404, "gone", {}, None)  # type: ignore[arg-type]
        return Resp(self.files[url], start, self.hook)


class Runner:
    """Stands in for uv and Python: does what they would do to the folder, records each command."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.envs: list[dict] = []
        self.cuda = True
        self.fail: dict[str, tuple[int, str]] = {}
        self.on: dict[str, object] = {}

    def __call__(self, argv, cwd, env, log_path, cancel, timeout):
        self.calls.append(list(argv))
        key = ("venv" if argv[1:2] == ["venv"] else "pip" if argv[1:3] == ["pip", "install"] else "torch" if "-c" in argv
               else "quick" if "--quick-test-for-ci" in argv else "other")
        self.envs.append(dict(env))
        hook = self.on.get(key)
        if callable(hook):
            hook(cancel)
        if cancel.is_set():
            raise Stopped()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if key in self.fail:
            code, text = self.fail[key]
            log_path.write_text(text, encoding="utf-8")
            return code
        if key == "venv":
            py = Path(argv[2]) / "Scripts" / "python.exe"
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_bytes(b"MZ python")
        elif key == "pip":  # uv fills its cache and the venv
            cache = Path(env["UV_CACHE_DIR"])
            cache.mkdir(parents=True, exist_ok=True)
            (cache / "wheel-cache.txt").write_text("cached", encoding="utf-8")
            site = Path(argv[argv.index("--python") + 1]).parent.parent / "Lib" / "site-packages"
            site.mkdir(parents=True, exist_ok=True)
            (site / "installed.txt").write_text("torch", encoding="utf-8")
        elif key == "torch":
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"VGAC 2.12.1+cu130 {self.cuda}\n")
        elif key == "quick":
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("Import times for custom nodes:\n   0.1 seconds: ComfyUI-SkinToken\n")
        return 0


def two_cards() -> Hardware:
    return Hardware(cards=[Card(0, "NVIDIA GeForce RTX 3060", 12288, "00000000:01:00.0"),
                           Card(1, "NVIDIA GeForce RTX 3080", 10240, "00000000:02:00.0")], ram_total_gb=64, ram_free_gb=40)


def snapshot(folder: Path) -> dict[str, str]:
    return {str(p.relative_to(folder)): sha(p.read_bytes()) for p in sorted(folder.rglob("*")) if p.is_file()}


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = {"instances": {"a": {"url": "http://127.0.0.1:8188", "role": "3d"},
                         "b": {"url": "http://127.0.0.1:8189", "role": "images", "output_dir": "runtime/comfy-b-output"}},
           "library_path": str(tmp_path / "library"), "min_free_ram_gb": 0}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    data = tmp_path / "Zoë data with spaces"
    machine = tmp_path / "local" / "machine.json"
    machine.parent.mkdir(parents=True)
    machine.write_text(json.dumps({"schema": 1, "starter": {"dataFolder": str(data)}}), encoding="utf-8")
    monkeypatch.setenv("VGAC_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("VGAC_MACHINE", str(machine))
    monkeypatch.setattr(firstrun, "_port_open", lambda url: False)
    monkeypatch.setattr(downloader, "project_dir", lambda: tmp_path)
    monkeypatch.setattr(downloader, "free_bytes", lambda p: 10**13)
    (tmp_path / "setup").mkdir()
    # The shipped list, with the ComfyUI pins and the SkinToken pack pointed at the test's own files.
    cat = json.loads(REAL_LIST.read_text(encoding="utf-8"))
    tree = {"commit": "c0ffee", "files": [{"path": k, "size": len(v), "sha256": sha(v)} for k, v in SOURCE.items()]}
    tree_bytes = json.dumps(tree).encode()
    (tmp_path / "setup" / "comfy-tree-test.json").write_bytes(tree_bytes)
    cat["comfyui_install"].update({"zip": ZIP_URL, "sha256": sha(ZIP), "tree_list": "setup/comfy-tree-test.json",
                                   "tree_sha256": sha(tree_bytes), "commit": "c0ffee", "max_root_chars": 400,
                                   "lock": "setup/comfyui-lock.json", "installed_bytes": 10_000, "unpacked_bytes": 100})
    cat["node_packs"]["skintoken"].update({"zip": PACK_URL, "sha256": sha(PACK_ZIP)})
    (tmp_path / "setup" / "downloads.json").write_text(json.dumps(cat), encoding="utf-8")
    lockw = [{"name": f.split("-")[0], "version": f.split("-")[1], "file": f, "url": "https://files.pythonhosted.org/packages/" + f,
              "size": len(b), "sha256": sha(b), "licence": "MIT"} for f, b in WHEELS.items()]
    (tmp_path / "setup" / "comfyui-lock.json").write_text(json.dumps({"wheels": lockw}), encoding="utf-8")
    nodes = tmp_path / "comfy-nodes" / "vgac-nodes"
    nodes.mkdir(parents=True)
    (nodes / "__init__.py").write_text("# ours\n", encoding="utf-8")
    uv = data / "tools" / "uv" / "uv.exe"
    uv.parent.mkdir(parents=True)
    uv.write_bytes(b"MZ uv")
    # The person's own ComfyUI, beside it, with a model in it.
    theirs = tmp_path / "Their ComfyUI"
    (theirs / "ComfyUI" / "models" / "checkpoints").mkdir(parents=True)
    (theirs / "ComfyUI" / "models" / "loras").mkdir(parents=True)
    (theirs / "ComfyUI" / "main.py").write_text("print('theirs')\n", encoding="utf-8")
    (theirs / "ComfyUI" / "models" / "checkpoints" / "mine.safetensors").write_bytes(b"weights")
    net = Net({ZIP_URL: ZIP, PACK_URL: PACK_ZIP, **{"https://files.pythonhosted.org/packages/" + f: b for f, b in WHEELS.items()}})
    runner = Runner()
    ops = ci.Ops(opener=net, run=runner, hardware=two_cards, port_in_use=lambda p: False, free_bytes=lambda p: 10**13,
                 found_comfies=lambda: [str(theirs)], sleep=lambda s: None)
    config.reset_settings()
    db.reset_engine()
    ci._current = None
    before = snapshot(theirs)
    yield {"tmp": tmp_path, "data": data, "root": data / "comfyui", "machine": machine, "net": net, "runner": runner, "ops": ops,
           "theirs": theirs, "before": before}
    assert snapshot(theirs) == before, "the person's own ComfyUI was changed"
    ci._current = None
    db.reset_engine()
    config.reset_settings()


def install(b, choice="C", shared=""):
    return ci.start(choice, shared, b["ops"], wait=True)


def machine(b) -> dict:
    return json.loads(b["machine"].read_text(encoding="utf-8"))


# --- a whole install ---------------------------------------------------------------------------------------------------


def test_full_install_c(box):
    job = install(box)
    assert job.state == "done", job.error
    root = box["root"]
    assert (root / "ComfyUI" / "main.py").read_bytes() == SOURCE["main.py"]
    assert (root / "ComfyUI" / "custom_nodes" / "ComfyUI-SkinToken" / "__init__.py").is_file()
    assert (root / "ComfyUI" / "custom_nodes" / "vgac-nodes" / "__init__.py").is_file()
    assert not (root / "ComfyUI" / "custom_nodes" / "ComfyUI-PixelArt-Detector").exists()  # left out on purpose
    m = ci.read_marker(root)
    assert m and all(m["steps"][s] == "done" for s, _ in ci.STEPS if s != "check")
    assert not (root / "wheels").exists()  # removed after success
    # uv: offline, from the checked wheels only, every hash required, nothing pulled in besides the list
    pip = next(c for c in box["runner"].calls if c[1:3] == ["pip", "install"])
    for flag in ("--offline", "--no-index", "--require-hashes", "--no-deps", "--only-binary"):
        assert flag in pip
    req = (root / "requirements.lock.txt").read_text(encoding="utf-8")
    assert all("--hash=sha256:" in ln for ln in req.splitlines()) and len(req.splitlines()) == len(WHEELS)
    # the engines: one per card of the hardware plan, the 12 GB card makes the 3D, free ports from 8288
    s = config.load_settings(create_library=False)
    a, bb = s.instances["a"], s.instances["b"]
    assert a.url == "http://127.0.0.1:8288" and bb.url == "http://127.0.0.1:8289"
    assert a.engine.cuda_device == 0 and bb.engine.cuda_device == 1
    assert Path(a.engine.python) == ci.venv_python(root) and Path(a.engine.comfy_dir) == root
    assert a.engine.extra_model_paths is None
    assert a.gpu == "RTX 3060" and s.stage_instances["mesh"] == "a"
    assert s.comfy_input_dir == root / "input" and s.loras_dir == root / "ComfyUI" / "models" / "loras"
    # every program got its paths as separate arguments, the data folder's spaces and letters untouched
    assert any(str(root / ".venv") == c[2] for c in box["runner"].calls if c[1:2] == ["venv"])
    assert all(str(box["theirs"]) not in " ".join(c) for c in box["runner"].calls)
    assert {"what": "VGAC's own ComfyUI"}.items() <= next(i for i in json.loads(downloader.ledger_path().read_text())["items"]
                                                         if i["path"] == str(root)).items()


def test_choice_a_shares_their_models_by_path_and_never_writes_there(box):
    models = box["theirs"] / "ComfyUI" / "models"
    job = install(box, "A", str(models))
    assert job.state == "done", job.error
    yaml_file = box["root"] / "shared-models.yaml"
    text = yaml_file.read_text(encoding="utf-8")
    data = yaml.safe_load(text)["your_comfyui_models"]
    assert data["base_path"] == str(models) and data["checkpoints"] == "checkpoints/" and data["loras"] == "loras/"
    assert "is_default" not in text
    s = config.load_settings(create_library=False)
    assert Path(s.instances["a"].engine.extra_model_paths) == yaml_file
    # the guard refuses their models folder and their ComfyUI, even through VGAC's own helpers
    fence = ci.Fence()
    fence.add(models)
    fence.add(box["theirs"])
    for target in (models / "checkpoints" / "new.safetensors", models, box["theirs"] / "ComfyUI" / "custom_nodes" / "x"):
        with pytest.raises(ci.NotOurs):
            ci.g_write(target, "no", fence)
    with pytest.raises(ci.NotOurs):
        ci.g_rmtree(models, box["ops"], fence)


def test_choice_a_needs_their_models_folder(box):
    with pytest.raises(DownloadError):
        ci.start("A", "", box["ops"], wait=True)
    with pytest.raises(DownloadError):
        ci.start("B", "", box["ops"], wait=True)


# --- the guard ---------------------------------------------------------------------------------------------------------


def test_guard_refuses_outside_without_marker_and_through_a_junction(box):
    root = box["root"]
    root.mkdir(parents=True)
    with pytest.raises(ci.NotOurs):
        ci.guard(root / "x")  # no marker yet
    ci.claim(root, "C", ci.Fence())
    ci.guard(root / "x")  # fine now
    for outside in (box["theirs"] / "ComfyUI" / "main.py", box["data"] / "elsewhere", root.parent, root / ".." / "comfyui-evil"):
        with pytest.raises(ci.NotOurs):
            ci.guard(outside)
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(box["theirs"]), str(root / "link"))
        with pytest.raises(ci.NotOurs):
            ci.g_write(root / "link" / "ComfyUI" / "main.py", "overwritten")
        assert (box["theirs"] / "ComfyUI" / "main.py").read_text(encoding="utf-8") == "print('theirs')\n"
        os.rmdir(root / "link")


def test_never_installs_into_a_folder_with_someone_elses_files(box):
    root = box["root"]
    root.mkdir(parents=True)
    (root / "main.py").write_text("someone's", encoding="utf-8")
    job = install(box)
    assert job.state == "failed" and job.error["code"] == "folder_used"
    assert (root / "main.py").read_text(encoding="utf-8") == "someone's" and not (root / ci.MARKER).exists()


def test_never_installs_inside_a_found_comfyui(box):
    m = machine(box)
    m["starter"]["dataFolder"] = str(box["theirs"] / "ComfyUI")
    box["machine"].write_text(json.dumps(m), encoding="utf-8")
    job = install(box)
    assert job.state == "failed" and job.error["code"] == "not_ours"
    assert not (box["theirs"] / "ComfyUI" / "comfyui").exists()


# --- refusals before anything is downloaded ------------------------------------------------------------------------------


def _nothing_fetched(box):
    assert box["net"].seen == [] and box["runner"].calls == []


def test_refuses_while_the_comfyui_pin_is_missing(box):
    p = box["tmp"] / "setup" / "downloads.json"
    cat = json.loads(p.read_text(encoding="utf-8"))
    cat["comfyui_install"]["sha256"] = ""
    p.write_text(json.dumps(cat), encoding="utf-8")
    job = install(box)
    assert job.state == "failed" and job.error["code"] == "not_ready" and "checksum" in job.error["message"]
    _nothing_fetched(box)
    assert not box["root"].exists()
    assert not ci.offer()["ready"]


def test_refuses_when_the_file_list_does_not_match_its_pin(box):
    (box["tmp"] / "setup" / "comfy-tree-test.json").write_text('{"files": []}', encoding="utf-8")
    job = install(box)
    assert job.state == "failed" and job.error["code"] == "not_ready" and "list" in job.error["message"]
    _nothing_fetched(box)


def test_the_shipped_list_has_both_comfyui_pins_and_they_match(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, "project_dir", lambda: ROOT_DIR)
    e = ci.entry()
    assert ci.pins_missing() == []
    listing = ci.tree_list()  # None unless the committed list matches its pinned SHA-256
    assert listing is not None and len(listing["files"]) == e["files"] == 1346
    assert sum(f["size"] for f in listing["files"]) == e["unpacked_bytes"]
    assert e["commit"] in e["zip"] and e["zip"].startswith("https://github.com/Comfy-Org/ComfyUI/archive/")
    assert {"main.py", "comfy_extras/nodes_trellis2.py", "requirements.txt"} <= {f["path"] for f in listing["files"]}


def test_no_nvidia_card_downloads_nothing(box):
    box["ops"].hardware = lambda: Hardware(cards=[], ram_total_gb=32, other_cards=["AMD Radeon RX 7800 XT"])
    job = install(box)
    assert job.state == "failed" and job.error["code"] == "no_card" and "AMD Radeon" in job.error["message"]
    _nothing_fetched(box)


def test_too_small_a_card_downloads_nothing(box):
    box["ops"].hardware = lambda: Hardware(cards=[Card(0, "NVIDIA GeForce GTX 1650", 4096)], ram_total_gb=16)
    job = install(box)
    assert job.state == "failed" and job.error["code"] == "no_card"
    _nothing_fetched(box)


def test_an_8_gb_card_installs_with_its_warning(box):
    box["ops"].hardware = lambda: Hardware(cards=[Card(0, "NVIDIA GeForce RTX 3070", 8192)], ram_total_gb=32)
    job = install(box)
    assert job.state == "done", job.error
    assert any("8 GB" in w for w in job.warnings)
    s = config.load_settings(create_library=False)
    assert set(s.instances) == {"a"} and s.instances["a"].engine.cuda_device == 0


def test_not_enough_space_says_how_much(box):
    box["ops"].free_bytes = lambda p: 1000
    job = install(box)
    assert job.state == "failed" and job.error["code"] == "no_space" and "needs about" in job.error["message"]
    _nothing_fetched(box)


def test_a_folder_path_too_long_for_windows_is_refused(box):
    p = box["tmp"] / "setup" / "downloads.json"
    cat = json.loads(p.read_text(encoding="utf-8"))
    cat["comfyui_install"]["max_root_chars"] = 20
    p.write_text(json.dumps(cat), encoding="utf-8")
    job = install(box)
    assert job.state == "failed" and job.error["code"] == "path_long" and "260" in job.error["message"]
    _nothing_fetched(box)


def test_without_uv_it_says_to_start_vgac_exe(box):
    (box["data"] / "tools" / "uv" / "uv.exe").unlink()
    job = install(box)
    assert job.state == "failed" and job.error["code"] == "no_uv" and "VGAC.exe" in job.error["fix"]


# --- network, checksums, files held ---------------------------------------------------------------------------------------


def test_host_unreachable_then_continue(box):
    box["net"].down = True
    job = install(box)
    assert job.state == "failed" and job.error["code"] == "no_internet"
    box["net"].down = False
    job = install(box)
    assert job.state == "done", job.error


def test_a_wheel_that_is_not_the_published_file_is_deleted(box):
    url = "https://files.pythonhosted.org/packages/beta-2.0-cp313-cp313-win_amd64.whl"
    box["net"].files[url] = b"X" * 5000  # same size, other bytes
    job = install(box)
    assert job.state == "failed" and job.error["code"] == "bad_checksum"
    assert not (box["root"] / "wheels" / "beta-2.0-cp313-cp313-win_amd64.whl").exists()
    assert not any(c[1:3] == ["pip", "install"] for c in box["runner"].calls)  # never installed


def test_zip_bytes_changed_but_files_the_same_is_accepted(box):
    box["net"].files[ZIP_URL] = make_zip(SOURCE, method=zipfile.ZIP_STORED)  # GitHub changed its compression
    assert sha(box["net"].files[ZIP_URL]) != sha(ZIP)
    job = install(box)
    assert job.state == "done", job.error


def test_a_changed_source_file_is_refused_and_nothing_is_kept(box):
    box["net"].files[ZIP_URL] = make_zip({**SOURCE, "main.py": b"import evil\n"})
    job = install(box)
    assert job.state == "failed" and job.error["code"] == "bad_checksum" and "main.py" in job.error["message"]
    root = box["root"]
    assert not (root / "ComfyUI").exists() and not (root / "ComfyUI.new").exists() and not (root / "ComfyUI.unpack").exists()
    assert not any((root / "downloads").glob("*.zip"))


def test_an_extra_source_file_is_refused(box):
    box["net"].files[ZIP_URL] = make_zip({**SOURCE, "custom_nodes/surprise.py": b"x"})
    job = install(box)
    assert job.state == "failed" and job.error["code"] == "bad_checksum"


def test_a_zip_entry_that_escapes_is_refused(box):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ComfyUI-c0ffee/../../escaped.txt", b"x")
    box["net"].files[ZIP_URL] = buf.getvalue()
    job = install(box)
    assert job.state == "failed" and job.error["code"] in ("bad_zip", "bad_checksum")  # refused before it is opened
    assert not (box["data"] / "escaped.txt").exists() and not (box["tmp"] / "escaped.txt").exists()


def test_a_file_held_by_an_antivirus_is_retried_then_explained(box, monkeypatch):
    box["root"].mkdir(parents=True)
    ci.claim(box["root"], "C", ci.Fence())
    a, b = box["root"] / "a.txt", box["root"] / "b.txt"
    a.write_text("x", encoding="utf-8")
    real = os.replace
    tries = {"n": 0}

    def held(src, dst):
        tries["n"] += 1
        if tries["n"] <= 2:
            raise PermissionError(32, "The process cannot access the file because it is being used by another process")
        return real(src, dst)

    monkeypatch.setattr(os, "replace", held)
    ci.g_move(a, b, box["ops"])  # third try works
    assert b.is_file() and tries["n"] == 3
    monkeypatch.setattr(os, "replace", lambda s, d: (_ for _ in ()).throw(PermissionError(5, "Access is denied")))
    with pytest.raises(DownloadError) as e:
        ci.g_move(b, a, box["ops"])
    assert e.value.code == "locked" and "antivirus" in e.value.message


def test_uv_blocked_by_antivirus_is_explained(box):
    box["runner"].fail["pip"] = (2, "error: failed to write torch\\lib\\cudnn.dll: Access is denied. (os error 5)")
    job = install(box)
    assert job.state == "failed" and job.error["code"] == "locked" and "antivirus" in job.error["message"]


def test_a_quarantined_file_after_install_is_reported(box, monkeypatch):
    real = ci.step_test

    def antivirus_then_test(job, ops, fence, card):  # the antivirus takes the node pack between the steps
        shutil.rmtree(box["root"] / "ComfyUI" / "custom_nodes" / "ComfyUI-SkinToken")
        return real(job, ops, fence, card)

    monkeypatch.setattr(ci, "step_test", antivirus_then_test)
    job = install(box)
    assert job.state == "failed" and job.error["code"] == "missing_file" and "antivirus" in job.error["message"]


def test_torch_without_cuda_on_an_nvidia_machine_is_a_fault(box):
    box["runner"].cuda = False
    job = install(box)
    assert job.state == "failed" and job.error["code"] == "no_cuda" and "driver" in job.error["fix"]


def test_a_node_that_does_not_load_fails_the_test(box):
    box["runner"].fail["quick"] = (0, "IMPORT FAILED: ComfyUI-SkinToken\n")
    job = install(box)
    assert job.state == "failed" and job.error["code"] == "test" and "SkinToken" in job.error["message"]


# --- stop and continue ---------------------------------------------------------------------------------------------------


def test_stop_during_a_download_keeps_the_part_and_continues_with_a_range(box):
    box["net"].hook = lambda pos: job_ref["job"].cancel.set() if pos >= 1000 else None
    job_ref = {}
    orig = ci.Install

    class Grab(orig):  # type: ignore[misc,valid-type]
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            job_ref["job"] = self

    ci.Install = Grab
    try:
        job = install(box)
    finally:
        ci.Install = orig
    assert job.state == "stopped"
    parts = list((box["root"] / "wheels").glob("*.part")) + list((box["root"] / "downloads").glob("*.part"))
    assert parts, "the half-done file is kept"
    box["net"].hook = None
    box["net"].seen.clear()
    job = install(box)
    assert job.state == "done", job.error
    assert any(start > 0 for _, start in box["net"].seen), "carried on where it stopped"


def test_stop_during_the_package_install_then_continue(box):
    box["runner"].on["pip"] = lambda cancel: cancel.set()
    job = install(box)
    assert job.state == "stopped"
    m = ci.read_marker(box["root"])
    assert m["steps"]["packages"] == "working" and m["steps"]["venv"] == "done"
    box["runner"].on.clear()
    box["runner"].calls.clear()
    job = install(box)
    assert job.state == "done", job.error
    assert not any(c[1:2] == ["venv"] for c in box["runner"].calls), "the finished venv was kept"
    assert any(c[1:3] == ["pip", "install"] for c in box["runner"].calls)


def test_status_and_offer_before_anything(box):
    st = ci.status()
    assert st["state"] == "idle" and [s["id"] for s in st["steps"]] == [s for s, _ in ci.STEPS]
    off = ci.offer()
    assert off["ready"] and off["goes_to"] == str(box["root"]) and not off["needs_admin"]
    assert any("pixel-art look is not included" in t for t in off["left_out"])
    assert off["download_bytes"] >= sum(len(b) for b in WHEELS.values())


def test_a_second_start_while_running_is_refused(box):
    gate = threading.Event()
    box["runner"].on["venv"] = lambda cancel: gate.wait(5)
    job = ci.start("C", "", box["ops"])
    try:
        with pytest.raises(DownloadError):
            ci.start("C", "", box["ops"])
    finally:
        gate.set()
        job.thread.join()


# --- ports and engines --------------------------------------------------------------------------------------------------


def test_ports_in_use_and_usual_comfy_ports_are_skipped(box):
    box["ops"].port_in_use = lambda p: p in (8288, 8290)
    job = install(box)
    assert job.state == "done", job.error
    assert [e["port"] for e in job.engines] == [8289, 8291]
    assert all(p not in comfy_find.PORTS for p in (8289, 8291))


class StubProcs:
    def __init__(self):
        self.launched: list[tuple[list[str], Path, dict]] = []
        self.killed: list[int] = []

    def launch(self, argv, cwd, env, out_log, err_log):
        self.launched.append((argv, cwd, env))
        return 4000 + len(self.launched)

    def spawn(self, *a):
        raise AssertionError("no start scripts")

    def alive(self, pid):
        return pid not in self.killed

    def create_time(self, pid):
        return 1.0

    def kill_tree(self, pid):
        self.killed.append(pid)

    def find(self, match):
        return []

    def port_open(self, url):
        return False


class StubClient:
    def __init__(self, url, procs, card):
        self.base_url, self.procs, self.card, self.waiting = url, procs, card, 0

    def set_base_url(self, url):
        self.base_url = url

    async def cached_vram_summary(self, age):
        port = self.base_url.rsplit(":", 1)[-1]
        up = any(("--port" in a and a[a.index("--port") + 1] == port) for a, _, _ in self.procs.launched)
        return {"reachable": True, "devices": [{"name": f"cuda:0 NVIDIA GeForce {self.card}"}]} if up else {"reachable": False}


def test_engines_start_from_the_hardware_plan_through_the_supervisor(box):
    job = install(box)
    assert job.state == "done", job.error
    running = config.load_settings(machine=False, create_library=False)  # the app as it was started: before the install
    procs = StubProcs()
    clients = {"a": StubClient(running.instances["a"].url, procs, "RTX 3060"), "b": StubClient(running.instances["b"].url, procs, "RTX 3080")}

    async def no_probe(url):
        return None

    sup = instances.Supervisor(running, clients, ops=procs, poll_seconds=0, project_dir=box["tmp"], probe=no_probe)
    out = asyncio.run(ci.start_engines(sup))
    assert [o["state"] for o in out] == ["running", "running"], out
    argv_a, cwd_a, env_a = procs.launched[0]
    root = box["root"]
    assert argv_a[0] == str(ci.venv_python(root)) and Path(cwd_a) == root
    assert argv_a[argv_a.index("--port") + 1] == "8288" and argv_a[argv_a.index("--cuda-device") + 1] == "0"
    argv_b = procs.launched[1][0]
    assert argv_b[argv_b.index("--port") + 1] == "8289" and argv_b[argv_b.index("--cuda-device") + 1] == "1"
    assert env_a["CUDA_DEVICE_ORDER"] == "PCI_BUS_ID"
    assert clients["a"].base_url == "http://127.0.0.1:8288"
    assert sup.owned("a") and sup.owned("b")


def test_start_engines_leaves_a_busy_card_for_the_next_start(box):
    install(box)
    running = config.load_settings(machine=False, create_library=False)
    procs = StubProcs()
    sup = instances.Supervisor(running, {}, ops=procs, poll_seconds=0, project_dir=box["tmp"])
    sup.busy = lambda n: True
    out = asyncio.run(ci.start_engines(sup))
    assert {o["state"] for o in out} == {"restart"} and procs.launched == []


# --- remove --------------------------------------------------------------------------------------------------------------


def test_remove_keeps_models_unless_asked_and_clears_the_engines(box):
    install(box)
    root = box["root"]
    (root / "ComfyUI" / "models" / "checkpoints").mkdir(parents=True, exist_ok=True)
    (root / "ComfyUI" / "models" / "checkpoints" / "big.safetensors").write_bytes(b"w")
    r = ci.remove(False, box["ops"])
    assert r["kept_models"] and (root / "ComfyUI" / "models" / "checkpoints" / "big.safetensors").is_file()
    assert not (root / ".venv").exists() and not (root / "ComfyUI" / "main.py").exists()
    s = config.load_settings(create_library=False)
    assert s.instances["a"].engine is None and set(r["engines_cleared"]) == {"a", "b"}
    r = ci.remove(True, box["ops"])
    assert not root.exists()


def test_remove_refuses_a_folder_vgac_did_not_record(box):
    root = box["root"]
    root.mkdir(parents=True)
    (root / ci.MARKER).write_text(json.dumps({"made_by": "VGAC"}), encoding="utf-8")  # a marker alone is not enough
    (root / "keep.txt").write_text("x", encoding="utf-8")
    with pytest.raises(DownloadError) as e:
        ci.remove(True, box["ops"])
    assert e.value.code == "not_ours" and (root / "keep.txt").is_file()


# --- the 5a panel's view of VGAC's own install ------------------------------------------------------------------------------


def test_pixel_art_pack_is_left_out_not_missing_in_our_install(box):
    install(box)
    root = box["root"]
    c = {"folder": str(root), "main": "ComfyUI/main.py", "python": str(ci.venv_python(root))}
    st = {p["id"]: p for p in comfy_find.packs_state(c)}
    assert "not included in this install" in st["pixelart"]["left_out"] and not st["pixelart"]["present"]
    rep = comfy_find.packs_report(c, opener=box["net"], pip=lambda argv: (0, "[]"))
    px = next(p for p in rep["packs"] if p["id"] == "pixelart")
    assert px["left_out"] and "requirements" not in px
    assert rep["python"]["note"] == "Nothing to add."
    # someone else's ComfyUI: the pack is plainly missing, as before
    theirs = {"folder": str(box["theirs"]), "main": "ComfyUI/main.py", "python": ""}
    assert next(p for p in comfy_find.packs_state(theirs) if p["id"] == "pixelart")["left_out"] == ""


def test_choices_a_and_c_are_offered(box):
    ch = {c["id"]: c for c in comfy_find.CHOICES}
    assert ch["A"]["available"] and ch["C"]["available"] and ch["A"]["recommended"]


# --- the shipped lock --------------------------------------------------------------------------------------------------------


def test_the_shipped_lock_is_complete_pinned_and_from_official_hosts():
    d = json.loads(REAL_LOCK.read_text(encoding="utf-8"))
    ws = d["wheels"]
    assert len(ws) > 100
    names = {w["name"] for w in ws}
    for need in ("torch", "torchvision", "comfyui-frontend-package", "comfy-kitchen", "comfy-aimdo", "transformers", "open3d",
                 "lightning", "diffusers", "trimesh", "fast-simplification", "python-box", "omegaconf", "addict", "huggingface-hub"):
        assert need in names, need
    assert "pyclustering" not in names and "opencv-python" not in names and "torchaudio" not in names
    torch = next(w for w in ws if w["name"] == "torch")
    assert torch["version"] == "2.12.1+cu130" and torch["url"].startswith("https://download.pytorch.org/whl/cu130/")
    for w in ws:
        assert len(w["sha256"]) == 64 and int(w["size"]) > 0 and w["licence"], w["name"]
        assert w["url"].startswith(("https://files.pythonhosted.org/", "https://download.pytorch.org/whl/cu130/")), w["url"]
        assert w["file"].endswith((".whl")) and ("win_amd64" in w["file"] or "none-any" in w["file"]), w["file"]
        assert "cp313" in w["file"] or "abi3" in w["file"] or "-none-" in w["file"], w["file"]
    assert len(names) == len(ws), "one wheel per package"


# --- review fixes ------------------------------------------------------------------------------------------------------------


def _their_file(box, rel: str, text: str) -> Path:
    """A file in the pretend person's ComfyUI, made before the install runs (and counted in the unchanged check)."""
    p = box["theirs"] / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    box["before"].clear()
    box["before"].update(snapshot(box["theirs"]))
    return p


def _model(box) -> Path:
    m = box["root"] / "ComfyUI" / "models" / "checkpoints" / "downloaded.safetensors"
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_bytes(b"a model the person downloaded")
    return m


def test_f1_install_again_after_remove_keeps_the_kept_models(box):
    assert install(box).state == "done"
    model = _model(box)
    ci.remove(False, box["ops"])
    job = install(box)
    assert job.state == "done", job.error
    assert model.read_bytes() == b"a model the person downloaded"
    assert (box["root"] / "ComfyUI" / "main.py").read_bytes() == SOURCE["main.py"]


def test_f1_try_again_after_a_quarantined_file_keeps_models_and_packs(box):
    assert install(box).state == "done"
    model = _model(box)
    (box["root"] / "ComfyUI" / "main.py").unlink()  # the antivirus took it
    (box["root"] / "ComfyUI" / "user" / "default").mkdir(parents=True)
    (box["root"] / "ComfyUI" / "user" / "default" / "settings.json").write_text("{}", encoding="utf-8")
    job = install(box)
    assert job.state == "done", job.error
    assert (box["root"] / "ComfyUI" / "main.py").read_bytes() == SOURCE["main.py"]
    assert model.is_file() and (box["root"] / "ComfyUI" / "custom_nodes" / "ComfyUI-SkinToken" / "__init__.py").is_file()
    assert (box["root"] / "ComfyUI" / "user" / "default" / "settings.json").is_file()


def test_f1_nothing_to_fetch_when_every_pinned_file_is_there(box):
    assert install(box).state == "done"
    box["net"].seen.clear()
    assert install(box).state == "done"
    assert not any(u == ZIP_URL for u, _ in box["net"].seen)


def test_f2_a_rebuilt_environment_gets_its_packages_again(box):
    assert install(box).state == "done"
    shutil.rmtree(box["root"] / ".venv")
    box["runner"].calls.clear()
    job = install(box)
    assert job.state == "done", job.error
    kinds = [c[1:3] for c in box["runner"].calls]
    assert ["venv", str(box["root"] / ".venv")] in kinds and ["pip", "install"] in kinds
    assert (box["root"] / ".venv" / "Lib" / "site-packages" / "installed.txt").is_file()


def test_f2_and_f9_remove_keeping_models_resets_the_steps_and_status_says_so(box):
    assert install(box).state == "done"
    ci._current = None  # as after a restart of the app
    assert ci.status()["state"] == "done"  # F9: "check" is recorded, so a finished install says done
    _model(box)
    ci.remove(False, box["ops"])
    m = ci.read_marker(box["root"])
    assert m is not None and m["steps"] == {}
    assert ci.status()["state"] == "unfinished"


def _link_b(box, extra: Path) -> None:
    """Choice B as comfy_find links it, plus an engine input folder, extra arguments and folders inside their ComfyUI."""
    from vgac import setup_settings

    setup_settings.set_engines(str(box["theirs"]), str(box["theirs"] / "python.exe"), "ComfyUI/main.py", str(extra), {"a": 0, "b": 1})
    data = machine(box)
    for n in ("a", "b"):
        data["backend"]["instances"][n]["engine"].update({"extra_args": ["--their-flag"], "input_dir": str(box["theirs"] / "input"),
                                                          "output_dir": str(box["theirs"] / "output")})
    data["backend"]["loras_dir"] = str(box["theirs"] / "ComfyUI" / "models" / "loras")
    box["machine"].write_text(json.dumps(data), encoding="utf-8")
    config.reset_settings()


def _engine_block(box, name="a") -> dict:
    return machine(box)["backend"]["instances"][name]["engine"]


def test_f3_a_then_c_shares_nothing(box):
    models = box["theirs"] / "ComfyUI" / "models"
    assert install(box, "A", str(models)).state == "done"
    assert install(box, "C").state == "done"
    e = _engine_block(box)
    assert "extra_model_paths" not in e and not (box["root"] / "shared-models.yaml").exists()


def test_f3_b_then_c_drops_everything_of_their_comfyui(box):
    extra = _their_file(box, "extra_models_config.yaml", "comfyui:\n    base_path: x\n    is_default: true\n")
    _link_b(box, extra)
    assert install(box, "C").state == "done"
    for n in ("a", "b"):
        e = _engine_block(box, n)
        assert set(e) == {"comfy_dir", "python", "main", "port", "listen", "cuda_device", "user_dir", "output_dir", "extra_args"}, e
        assert e["extra_args"] == [] and Path(e["comfy_dir"]) == box["root"]
    s = config.load_settings(create_library=False)
    assert all(str(box["theirs"]) not in str(v) for v in (s.loras_dir, s.comfy_input_dir))
    assert all(i.engine.extra_model_paths is None and i.engine.input_dir is None for i in s.instances.values())
    assert all(Path(i.engine.output_dir).is_relative_to(box["root"]) for i in s.instances.values())


def test_f3_b_then_a_uses_vgacs_own_models_file_not_theirs(box):
    extra = _their_file(box, "extra_models_config.yaml", "comfyui:\n    base_path: x\n    is_default: true\n")
    _link_b(box, extra)
    models = box["theirs"] / "ComfyUI" / "models"
    assert install(box, "A", str(models)).state == "done"
    e = _engine_block(box)
    assert Path(e["extra_model_paths"]) == box["root"] / "shared-models.yaml" and "input_dir" not in e and e["extra_args"] == []


def test_f4_a_cut_short_zip_is_deleted_and_the_next_try_downloads_again(box):
    box["net"].files[ZIP_URL] = ZIP[: len(ZIP) // 2]  # codeload sends no length, so a short download looks complete
    job = install(box)
    assert job.state == "failed" and job.error["code"] == "bad_checksum"
    assert not list((box["root"] / "downloads").glob("*")) and not (box["root"] / "ComfyUI.unpack").exists()
    box["net"].files[ZIP_URL] = ZIP
    assert install(box).state == "done"


def test_f4_same_size_other_bytes_is_refused(box):
    bad = bytearray(ZIP)
    bad[len(bad) // 2] ^= 0xFF
    box["net"].files[ZIP_URL] = bytes(bad)
    p = box["tmp"] / "setup" / "downloads.json"
    cat = json.loads(p.read_text(encoding="utf-8"))
    cat["comfyui_install"]["size"] = len(ZIP)
    p.write_text(json.dumps(cat), encoding="utf-8")
    job = install(box)
    assert job.state == "failed" and job.error["code"] == "bad_checksum" and "same size" in job.error["message"]


def test_f6_a_missing_or_extra_file_is_refused_before_unpacking(box, monkeypatch):
    opened = []
    monkeypatch.setattr(ci, "g_unzip", lambda *a, **k: opened.append(a))
    for files in ({k: v for k, v in SOURCE.items() if k != "main.py"}, {**SOURCE, "extra/big.bin": b"x" * 1000}):
        box["net"].files[ZIP_URL] = make_zip(files)
        job = install(box)
        assert job.state == "failed" and job.error["code"] == "bad_checksum"
    assert opened == []


def test_f5_uvs_cache_is_inside_the_install_and_gone_after_it(box):
    assert install(box).state == "done"
    pip_env = box["runner"].envs[[c[1:3] for c in box["runner"].calls].index(["pip", "install"])]
    assert Path(pip_env["UV_CACHE_DIR"]) == box["root"] / ".uv-cache"
    assert not (box["root"] / ".uv-cache").exists()


def test_f8_inherited_variables_are_dropped_and_python_ignores_them(box, monkeypatch):
    for k, v in {"PYTHONPATH": "C:/evil", "PYTHONHOME": "C:/evil", "UV_INDEX_URL": "https://evil.example", "PIP_INDEX_URL": "https://evil.example",
                 "UV_CACHE_DIR": "C:/elsewhere", "VIRTUAL_ENV": "C:/other"}.items():
        monkeypatch.setenv(k, v)
    assert install(box).state == "done"
    for env in box["runner"].envs:
        assert not any(k in env for k in ("PYTHONPATH", "PYTHONHOME", "UV_INDEX_URL", "PIP_INDEX_URL", "VIRTUAL_ENV"))
        assert Path(env["UV_CACHE_DIR"]) == box["root"] / ".uv-cache" and env["UV_OFFLINE"] == "1"
    torch = next(c for c in box["runner"].calls if "-c" in c)
    quick = next(c for c in box["runner"].calls if "--quick-test-for-ci" in c)
    for argv in (torch, quick):
        assert argv[1:5] == ["-s", "-E", "-X", "utf8"]


def test_pins_cannot_be_overridden_by_the_local_list(box):
    evil = "https://evil.example/ComfyUI.zip"
    (box["tmp"] / "setup" / "downloads.local.json").write_text(json.dumps({
        "comfyui_install": {"zip": evil, "sha256": sha(b"other"), "tree_list": "setup/other.json", "lock": "setup/other-lock.json"},
        "node_packs": {"skintoken": {"zip": "https://evil.example/pack.zip", "sha256": sha(b"pack")}}}), encoding="utf-8")
    assert ci.entry()["zip"] == ZIP_URL
    job = install(box)
    assert job.state == "done", job.error
    assert all(not u.startswith("https://evil.example") for u, _ in box["net"].seen)
    assert PACK_URL in {u for u, _ in box["net"].seen}


def test_f7_remove_refuses_while_an_engine_it_did_not_start_runs(box):
    assert install(box).state == "done"
    before = box["machine"].read_text(encoding="utf-8")
    box["ops"].port_in_use = lambda p: p == 8288
    with pytest.raises(DownloadError) as e:
        ci.remove(True, box["ops"])
    assert e.value.code == "engine_running" and "8288" in e.value.message
    assert (box["root"] / ".venv").exists() and box["machine"].read_text(encoding="utf-8") == before


def test_f7_remove_stops_its_own_engine_then_deletes_then_clears_settings(box):
    assert install(box).state == "done"
    running = {8288, 8289}

    class Sup:
        def __init__(self):
            self.stopped: list[str] = []

        def stop_if_owned(self, name):
            self.stopped.append(name)
            running.discard({"a": 8288, "b": 8289}[name])
            return True

    box["ops"].port_in_use = lambda p: p in running
    sup = Sup()
    r = ci.remove(True, box["ops"], sup)
    assert sorted(sup.stopped) == ["a", "b"] and not box["root"].exists()
    s = config.load_settings(create_library=False)
    assert s.instances["a"].engine is None and s.comfy_input_dir is None and s.loras_dir is None
    assert set(r["engines_cleared"]) == {"a", "b"}


def test_f7_remove_under_choice_a_leaves_their_models_and_no_setting_points_at_it(box):
    models = box["theirs"] / "ComfyUI" / "models"
    assert install(box, "A", str(models)).state == "done"
    ci.remove(True, box["ops"])
    assert not box["root"].exists() and (models / "checkpoints" / "mine.safetensors").is_file()
    s = config.load_settings(create_library=False)
    assert s.comfy_input_dir is None and s.loras_dir is None
    assert all(i.engine is None for i in s.instances.values())
    assert "shared-models.yaml" not in box["machine"].read_text(encoding="utf-8")


def test_f10_too_few_free_ports_is_an_error_not_a_lost_engine(box):
    box["ops"].port_in_use = lambda p: p != 8288  # only one port free for two engines
    job = install(box)
    assert job.state == "failed" and job.error["code"] == "no_port"


def test_f10_vgacs_own_install_cannot_be_the_shared_models(box):
    assert install(box).state == "done"
    (box["root"] / "ComfyUI" / "models").mkdir(parents=True, exist_ok=True)
    with pytest.raises(DownloadError) as e:
        ci.start("A", str(box["root"] / "ComfyUI" / "models"), box["ops"], wait=True)
    assert "VGAC's own ComfyUI" in e.value.message


def test_f10_the_pack_download_can_be_stopped(box):
    big = make_zip({"__init__.py": b"NODE_CLASS_MAPPINGS = {}\n", "weights.bin": os.urandom(20_000)}, top="ComfyUI-SkinToken-beef",
                   method=zipfile.ZIP_STORED)
    box["net"].files[PACK_URL] = big
    lst = box["tmp"] / "setup" / "downloads.json"
    cat = json.loads(lst.read_text(encoding="utf-8"))
    cat["node_packs"]["skintoken"]["sha256"] = sha(big)
    lst.write_text(json.dumps(cat), encoding="utf-8")
    ref = {}
    orig = ci.Install

    class Grab(orig):  # type: ignore[misc,valid-type]
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            ref["job"] = self

    def hook(pos):
        if box["net"].seen and box["net"].seen[-1][0] == PACK_URL:
            ref["job"].cancel.set()

    box["net"].hook = hook
    ci.Install = Grab
    try:
        job = install(box)
    finally:
        ci.Install = orig
    assert job.state == "stopped"
    part = box["root"] / "downloads" / "pack-skintoken.zip.part"
    assert part.is_file() and part.stat().st_size < len(big), "stopped part way, in VGAC's own folder"
    assert not (box["root"] / "ComfyUI" / "custom_nodes" / "ComfyUI-SkinToken").exists()
    box["net"].hook = None
    assert install(box).state == "done"


def test_a_damaged_marker_of_a_recorded_install_is_written_again(box):
    assert install(box).state == "done"
    (box["root"] / ci.MARKER).write_text("{ not json", encoding="utf-8")
    job = install(box)
    assert job.state == "done", job.error
    assert ci.read_marker(box["root"])["steps"]["engines"] == "done"


def test_a_damaged_marker_without_the_ledger_is_not_taken_over(box):
    box["root"].mkdir(parents=True)
    (box["root"] / ci.MARKER).write_text("{ not json", encoding="utf-8")
    job = install(box)
    assert job.state == "failed" and job.error["code"] == "folder_used"


def test_the_http_endpoints(box, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setattr(ci, "Ops", lambda: box["ops"])
    app = FastAPI()
    app.include_router(ci.router)
    c = TestClient(app)
    off = c.get("/api/comfy/install").json()
    assert off["ready"] and off["status"]["state"] == "idle"
    assert c.post("/api/comfy/install/start", json={"choice": "B"}).status_code == 400
    assert c.post("/api/comfy/install/start", json={"choice": "A"}).status_code == 400  # no ComfyUI chosen
    assert c.post("/api/comfy/install/remove", json={}).status_code == 400  # not confirmed
    assert c.post("/api/comfy/install/start", json={"choice": "C"}).status_code == 200
    ci._current.thread.join()
    assert c.get("/api/comfy/install/status").json()["state"] == "done"
    assert c.post("/api/comfy/install/stop", json={}).status_code == 200
    assert c.post("/api/comfy/install/engines", json={}).json()["engines"] == []  # no running app here
    r = c.post("/api/comfy/install/remove", json={"confirm": True, "also_models": True})
    assert r.status_code == 200 and r.json()["removed"]


# --- fix check round 2 ---------------------------------------------------------------------------------------------------


def test_status_after_remove_is_not_the_finished_job(box):
    assert install(box).state == "done"
    assert ci.status()["state"] == "done"
    ci.remove(True, box["ops"])
    assert ci.status()["state"] == "idle"
    assert install(box).state == "done"  # and it can be installed again at once


def test_another_comfyuis_output_folder_and_ports_do_not_survive(box):
    data = machine(box)
    data.setdefault("backend", {})["instances"] = {
        n: {"output_dir": str(box["theirs"] / "output"), "discover": {"gpu_match": "30", "ports": [8188, 8189]}} for n in ("a", "b")}
    box["machine"].write_text(json.dumps(data), encoding="utf-8")
    config.reset_settings()
    assert install(box, "C").state == "done"
    s = config.load_settings(create_library=False)
    for n, i in s.instances.items():
        assert Path(i.output_dir).is_relative_to(box["root"]) and Path(i.engine.output_dir) == Path(i.output_dir)
        assert i.discover is None or i.discover.ports == []
        argv, _, _ = instances.engine_command(s, n)
        assert str(box["theirs"]) not in " ".join(argv)


def test_a_marker_without_the_ledger_entry_is_not_trusted(box):
    root = box["root"]
    root.mkdir(parents=True)
    (root / ci.MARKER).write_text(json.dumps({"made_by": "VGAC", "steps": {}}), encoding="utf-8")
    (root / "something.txt").write_text("not VGAC's", encoding="utf-8")
    job = install(box)
    assert job.state == "failed" and job.error["code"] == "folder_used"
    assert (root / "something.txt").read_text(encoding="utf-8") == "not VGAC's"


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows'")
def test_a_data_folder_junction_into_their_comfyui_is_refused(box):
    import _winapi

    box["data"].mkdir(parents=True, exist_ok=True)
    target = box["theirs"] / "ComfyUI"
    _winapi.CreateJunction(str(target), str(box["root"]))
    try:
        box["ops"].found_comfies = lambda: [str(target)]
        job = install(box)
        assert job.state == "failed" and job.error["code"] == "not_ours"
        assert not (target / ci.MARKER).exists()
    finally:
        os.rmdir(box["root"])


def test_an_unpack_error_of_any_kind_discards_the_zip(box, monkeypatch):
    import zlib

    def broken(*a, **k):
        raise zlib.error("Error -3 while decompressing data: invalid block type")

    monkeypatch.setattr(ci, "g_unzip", broken)
    job = install(box)
    assert job.state == "failed" and job.error["code"] == "bad_checksum"
    assert not list((box["root"] / "downloads").glob("*"))


def test_a_source_download_larger_than_any_published_zip_is_stopped_and_deleted(box):
    box["net"].files[ZIP_URL] = ZIP + b"\0" * (ci.source_cap(ci.entry()) + 10)
    job = install(box)
    assert job.state == "failed" and job.error["code"] == "too_big"
    assert not list((box["root"] / "downloads").glob("*"))


def test_a_full_disk_during_the_source_download_deletes_the_part(box):
    def disk_fills(pos):
        if pos >= 100:
            raise OSError(28, "No space left on device")

    def full(url, start):
        r = box["net"](url, start)
        if url == ZIP_URL:
            r.hook = disk_fills  # the disk fills part way through the zip
        return r

    box["ops"].opener = full
    job = install(box)
    assert job.state == "failed" and job.error["code"] == "no_space"
    assert not list((box["root"] / "downloads").glob("*"))


def test_remove_holds_the_install_lock(box):
    assert install(box).state == "done"
    held = []

    def found():
        got = ci._lock.acquire(blocking=False)
        if got:
            ci._lock.release()
        held.append(not got)
        return [str(box["theirs"])]

    box["ops"].found_comfies = found
    ci.remove(True, box["ops"])
    assert held == [True]


def test_the_port_check_sees_an_ipv6_only_listener():
    if not socket.has_ipv6:
        pytest.skip("no IPv6 on this computer")
    srv = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    try:
        srv.bind(("::1", 0))
    except OSError:
        pytest.skip("::1 is not available")
    srv.listen(1)
    try:
        assert ci._port_in_use(srv.getsockname()[1])
    finally:
        srv.close()


def test_files_of_an_older_pinned_version_are_removed_and_nothing_else(box):
    assert install(box).state == "done"
    app = box["root"] / "ComfyUI"
    (app / "comfy" / "old_module.py").write_text("from an older ComfyUI", encoding="utf-8")
    (app / "comfy" / "mine.py").write_text("the person's own file", encoding="utf-8")
    m = ci.read_marker(box["root"])
    m["source_files"] = list(m["source_files"]) + ["comfy/old_module.py"]
    (box["root"] / ci.MARKER).write_text(json.dumps(m), encoding="utf-8")
    (app / "main.py").unlink()  # a changed pinned file makes the source step run
    assert install(box).state == "done"
    assert not (app / "comfy" / "old_module.py").exists() and (app / "comfy" / "mine.py").is_file()


def test_a_locked_file_half_way_through_remove_leaves_no_step_marked_done(box, monkeypatch):
    assert install(box).state == "done"
    real = ci.g_rmtree

    def locked(p, ops, fence=None):
        if p.name == ".venv":
            raise DownloadError("locked", "Windows or your antivirus is holding it.", "")
        return real(p, ops, fence)

    monkeypatch.setattr(ci, "g_rmtree", locked)
    with pytest.raises(DownloadError):
        ci.remove(True, box["ops"])
    assert ci.read_marker(box["root"])["steps"] == {}
    assert ci.status()["state"] == "unfinished"
    assert config.load_settings(create_library=False).instances["a"].engine is not None  # settings cleared only after


# --- line endings: a pin must not depend on how git wrote the file -----------------------------------------------------


def _shipped_setup_copy(dest: Path, crlf: bool) -> None:
    (dest / "setup").mkdir(parents=True)
    for name in ("downloads.json", "comfyui-lock.json", "comfy-tree-v0.45.3.json"):
        raw = (ROOT_DIR / "setup" / name).read_bytes().replace(b"\r\n", b"\n")
        (dest / "setup" / name).write_bytes(raw.replace(b"\n", b"\r\n") if crlf else raw)


@pytest.mark.parametrize("crlf", [False, True])
def test_the_file_list_pin_holds_with_either_line_ending(tmp_path, monkeypatch, crlf):
    _shipped_setup_copy(tmp_path, crlf)
    monkeypatch.setattr(downloader, "project_dir", lambda: tmp_path)
    assert ci.pins_missing() == [] and ci.tree_list() is not None


def test_a_changed_file_list_still_fails_its_pin(tmp_path, monkeypatch):
    _shipped_setup_copy(tmp_path, crlf=True)
    p = tmp_path / "setup" / "comfy-tree-v0.45.3.json"
    p.write_bytes(p.read_bytes().replace(b'"size": 0', b'"size": 1', 1) + b" ")
    monkeypatch.setattr(downloader, "project_dir", lambda: tmp_path)
    assert ci.tree_list() is None and ci.pins_missing()


def _git(*args: str, cwd: Path) -> str:
    import subprocess

    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True).stdout


def test_git_writes_the_pinned_list_byte_for_byte():
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    out = _git("check-attr", "text", "--", "setup/comfy-tree-v0.45.3.json", cwd=ROOT_DIR)
    assert out.strip().endswith("text: unset"), out


def test_a_fresh_clone_with_crlf_checkout_passes_the_pins(tmp_path, monkeypatch):
    """What GitHub's Windows runner does: a clone with core.autocrlf=true. Only setup/ is checked out."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    try:
        _git("rev-parse", "--is-inside-work-tree", cwd=ROOT_DIR)
    except Exception:  # noqa: BLE001
        pytest.skip("not a git checkout")
    clone = tmp_path / "clone"
    _git("clone", "--quiet", "--no-checkout", "--config", "core.autocrlf=true", str(ROOT_DIR), str(clone), cwd=tmp_path)
    _git("checkout", "--quiet", "HEAD", "--", "setup", ".gitattributes", cwd=clone)
    monkeypatch.setattr(downloader, "project_dir", lambda: clone)
    assert ci.pins_missing() == []
