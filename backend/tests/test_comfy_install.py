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
        self.cuda = True
        self.fail: dict[str, tuple[int, str]] = {}
        self.on: dict[str, object] = {}

    def __call__(self, argv, cwd, env, log_path, cancel, timeout):
        self.calls.append(list(argv))
        key = "venv" if argv[1:2] == ["venv"] else "pip" if argv[1:3] == ["pip", "install"] else "torch" if argv[1:2] == ["-c"] else "quick"
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


def test_the_shipped_list_refuses_until_the_owner_pins_comfyui(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, "project_dir", lambda: ROOT_DIR)
    assert ci.entry()["sha256"] == "" and ci.pins_missing()  # until the owner's one download is approved


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
    assert job.state == "failed" and job.error["code"] == "bad_zip"
    assert not (box["data"] / "escaped.txt").exists()


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
