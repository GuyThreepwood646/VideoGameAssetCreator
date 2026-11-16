"""Finding and linking the person's own ComfyUI, and the node pack report (comfy_find.py). Every layout is built in a
temporary folder, Python and pip are stubs, the network is a fake; nothing real is read, started or changed."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest
import yaml

from vgac import comfy_find, config, db, downloader, firstrun, setup_settings
from vgac.comfy_find import Env

REAL_LIST = Path(__file__).resolve().parents[2] / "setup" / "downloads.json"


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = {"instances": {"a": {"url": "http://127.0.0.1:59981", "gpu": "RTX 3060", "role": "3d"},
                         "b": {"url": "http://127.0.0.1:59982", "gpu": "RTX 3080", "role": "images"}},
           "library_path": str(tmp_path / "library"), "min_free_ram_gb": 0}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    machine = tmp_path / "local" / "machine.json"
    monkeypatch.setenv("VGAC_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("VGAC_MACHINE", str(machine))
    monkeypatch.setattr(firstrun, "_port_open", lambda url: False)
    monkeypatch.setattr(downloader, "project_dir", lambda: tmp_path)
    monkeypatch.setattr(downloader, "free_bytes", lambda p: 10**13)
    (tmp_path / "setup").mkdir()
    shutil.copy(REAL_LIST, tmp_path / "setup" / "downloads.json")
    config.reset_settings()
    db.reset_engine()
    yield tmp_path, machine
    db.reset_engine()
    config.reset_settings()


def make_install(folder: Path, trellis: bool = True, python: str = "standalone-env/python.exe", version: str = "0.34.0") -> Path:
    app = folder / "ComfyUI"
    (app / "comfy_extras").mkdir(parents=True)
    (app / "custom_nodes").mkdir()
    (app / "main.py").write_text("# main", encoding="utf-8")
    (app / "comfyui_version.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    if trellis:
        (app / "comfy_extras" / "nodes_trellis2.py").write_text("# nodes", encoding="utf-8")
    py = folder / python
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_bytes(b"MZ")
    return app


def desktop_files(appdata: Path, installs: list[dict]) -> None:
    base = appdata / "Comfy Desktop"
    (base / "instance-model-paths").mkdir(parents=True)
    (base / "installations.json").write_text(json.dumps(installs), encoding="utf-8")
    for i in installs:
        (base / "instance-model-paths" / f"{i['id']}.yaml").write_text("comfy.desktop_0:\n  base_path: 'X'\n", encoding="utf-8")


def env_for(tmp: Path, probe=lambda port: None, lines=lambda: []) -> Env:
    return Env(appdata=tmp / "AppData", localappdata=tmp / "Local", roots=[tmp / "Home"], probe=probe, process_lines=lines)


def ok_python(py, code):
    return "VGAC 2.12.1+cu130 True"


def test_the_newer_desktop_layout_is_read_and_the_cloud_entry_is_skipped(box):
    tmp, _ = box
    good = tmp / "Installs" / "ComfyUI (1)"
    make_install(good)
    desktop_files(tmp / "AppData", [
        {"id": "inst-1", "name": "GameDevComfy", "installPath": str(good), "sourceId": "standalone", "version": "v0.34.0"},
        {"id": "inst-2", "name": "Comfy Cloud", "sourceId": "cloud"},
        {"id": "inst-3", "name": "Gone", "installPath": str(tmp / "Installs" / "nothing"), "sourceId": "standalone"}])
    found = comfy_find.find_comfy(env_for(tmp))["found"]
    assert [c["name"] for c in found] == ["GameDevComfy"]
    c = found[0]
    assert c["kind"] == "desktop" and c["folder"] == str(good) and c["main"] == "ComfyUI/main.py" and c["version"] == "0.34.0"
    assert c["python"].endswith("standalone-env\\python.exe") or c["python"].endswith("standalone-env/python.exe")
    assert c["has_trellis"] and c["extra_model_paths"].endswith("inst-1.yaml")


def test_the_older_desktop_layout_uses_base_path_and_says_when_the_program_is_missing(box):
    tmp, _ = box
    base = tmp / "Base"
    (base / "models").mkdir(parents=True)
    (base / ".venv" / "Scripts").mkdir(parents=True)
    (base / ".venv" / "Scripts" / "python.exe").write_bytes(b"MZ")
    (tmp / "AppData" / "ComfyUI").mkdir(parents=True)
    (tmp / "AppData" / "ComfyUI" / "config.json").write_text(json.dumps({"basePath": str(base)}), encoding="utf-8")
    c = comfy_find.find_comfy(env_for(tmp))["found"][0]
    assert c["source"].endswith("(older layout)") and c["problems"] and "program files" in c["problems"][0]
    assert not comfy_find.check_candidate(c, ok_python)["ok"]
    # with the app's program folder present it is a usable candidate
    app = tmp / "Local" / "Programs" / "ComfyUI" / "resources" / "ComfyUI"
    (app / "comfy_extras").mkdir(parents=True)
    (app / "main.py").write_text("#", encoding="utf-8")
    (app / "comfy_extras" / "nodes_trellis2.py").write_text("#", encoding="utf-8")
    c = comfy_find.find_comfy(env_for(tmp))["found"][0]
    assert not c.get("problems") and c["has_trellis"] and c["models"] == str(base / "models")


def test_portable_and_manual_folders_are_found_by_their_layout_only(box):
    tmp, _ = box
    home = tmp / "Home"
    p = home / "ComfyUI_windows_portable"
    make_install(p, python="python_embeded/python.exe")
    (p / "run_nvidia_gpu.bat").write_text("@echo", encoding="utf-8")
    m = home / "MyComfy"
    make_install(m, python=".venv/Scripts/python.exe")
    (home / "Comfy notes").mkdir()  # not a ComfyUI
    (home / "Other").mkdir()
    found = comfy_find.find_comfy(env_for(tmp))["found"]
    kinds = {c["name"]: c["kind"] for c in found}
    assert kinds == {"ComfyUI_windows_portable": "portable", "MyComfy": "manual"}


def test_a_running_copy_is_marked_and_an_unknown_running_copy_is_added(box):
    tmp, _ = box
    good = tmp / "Installs" / "ComfyUI (1)"
    make_install(good)
    other = tmp / "Elsewhere" / "ComfyUI-Mine"
    make_install(other, python=".venv/Scripts/python.exe")
    desktop_files(tmp / "AppData", [{"id": "inst-1", "name": "Mine", "installPath": str(good), "sourceId": "standalone"}])
    lines = lambda: [f'"{good}\\standalone-env\\python.exe" -s ComfyUI\\main.py'.replace("ComfyUI\\main.py", str(good / "ComfyUI" / "main.py")),
                     f'{other}\\.venv\\Scripts\\python.exe -s {other}\\ComfyUI\\main.py --port 8190']
    probe = lambda port: {"devices": [{"name": "cuda:0 NVIDIA GeForce RTX 3080"}]} if port in (8188, 8190) else None
    res = comfy_find.find_comfy(env_for(tmp, probe, lines))
    assert [a["port"] for a in res["answering"]] == [8188, 8190]
    by = {c["name"]: c for c in res["found"]}
    assert by["Mine"]["running"] == [8188, 8190]
    assert len(res["found"]) == 2 and any(c["kind"] == "running" and c["folder"] == str(other) for c in res["found"])


def test_desktop_engines_started_with_a_relative_main_are_marked_not_listed_again(box):
    """ComfyUI Desktop starts each engine (a launcher and its child, so two lines each) as
    `<install>\\ComfyUI\\.venv\\Scripts\\python.exe -s ComfyUI\\main.py`. Round 4 showed them four times as 'too old'."""
    tmp, _ = box
    good = tmp / "Installs" / "ComfyUI (1)"
    make_install(good)
    desktop_files(tmp / "AppData", [{"id": "inst-1", "name": "Mine", "installPath": str(good), "sourceId": "standalone"}])
    py = good / "ComfyUI" / ".venv" / "Scripts" / "python.exe"
    one = f'"{py}" -s ComfyUI\\main.py --port 8189 --cuda-device 0'
    two = f'"{py}" -s ComfyUI\\main.py --cuda-device 1 --enable-assets'
    lines = lambda: [one, one, two, two]
    probe = lambda port: {"devices": [{"name": "cuda:0 NVIDIA GeForce RTX 3080"}]} if port in (8188, 8189) else None
    res = comfy_find.find_comfy(env_for(tmp, probe, lines))
    assert len(res["found"]) == 1 and res["found"][0]["name"] == "Mine" and res["found"][0]["has_trellis"]
    assert res["found"][0]["running"] == [8188, 8189]


def test_check_says_what_is_wrong_in_plain_words(box):
    tmp, _ = box
    make_install(tmp / "Old", trellis=False)
    c = comfy_find.desktop_new  # noqa: F841
    cand = comfy_find._candidate(tmp / "Old", "manual", "x")
    r = comfy_find.check_candidate(cand, ok_python)
    assert not r["ok"] and any("TRELLIS" in p for p in r["problems"])
    cand = comfy_find._candidate(make_install(tmp / "New") and tmp / "New", "manual", "x")
    assert comfy_find.check_candidate(cand, ok_python) == {"ok": True, "problems": [], "torch": "2.12.1+cu130"}
    r = comfy_find.check_candidate(cand, lambda py, code: "VGAC 2.12.1 False")
    assert "CUDA" in " ".join(r["problems"])
    r = comfy_find.check_candidate(cand, lambda py, code: "")
    assert "did not start" in " ".join(r["problems"])


def test_linking_writes_every_card_in_one_save_and_changes_nothing_in_their_comfy(box):
    tmp, machine = box
    inst = tmp / "Installs" / "ComfyUI (1)"
    make_install(inst)
    before = sorted(str(p) for p in inst.rglob("*"))
    cand = comfy_find._candidate(inst, "desktop", "x")
    out = comfy_find.link_comfy(cand, {"a": 1, "b": 0}, ok_python)
    saved = json.loads(machine.read_text(encoding="utf-8"))["backend"]["instances"]
    assert saved["a"]["engine"]["comfy_dir"] == str(inst) and saved["a"]["engine"]["cuda_device"] == 1
    assert saved["b"]["engine"]["cuda_device"] == 0 and saved["b"]["engine"]["main"] == "ComfyUI/main.py"
    assert config.get_settings().instances["a"].engine.comfy_dir == inst  # in use from now on
    assert sorted(str(p) for p in inst.rglob("*")) == before and out["instances"] == ["a", "b"]


def test_a_comfy_that_fails_its_check_is_not_linked(box):
    tmp, machine = box
    make_install(tmp / "Old", trellis=False)
    with pytest.raises(setup_settings.SettingError) as e:
        comfy_find.link_comfy(comfy_find._candidate(tmp / "Old", "manual", "x"), {}, ok_python)
    assert "TRELLIS" in str(e.value) and not machine.exists()


def test_a_and_c_go_through_the_install_and_b_is_the_only_one_linked_here(box):
    with pytest.raises(setup_settings.SettingError, match="Install VGAC's ComfyUI"):
        comfy_find.link_choice("A")
    with pytest.raises(setup_settings.SettingError, match="Install VGAC's ComfyUI"):
        comfy_find.link_choice("C")
    comfy_find.link_choice("B")
    assert [c["id"] for c in comfy_find.CHOICES if c["available"]] == ["A", "B", "C"]
    assert next(c for c in comfy_find.CHOICES if c["recommended"])["id"] == "A"


# --- node packs ---------------------------------------------------------------------------------------------------------


def pack_zip(requirements: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Pack-abc123/__init__.py", "# pack")
        z.writestr("Pack-abc123/requirements.txt", requirements)
    return buf.getvalue()


class PackNet:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.log: list[str] = []

    def __call__(self, url: str, start: int):
        self.log.append(url)
        r = io.BytesIO(self.data[start:])
        r.status = 206 if start else 200
        r.headers = {"Content-Length": str(len(self.data) - start)}
        return r


def set_pins(tmp: Path, data: bytes) -> None:
    sha = hashlib.sha256(data).hexdigest()
    real = json.loads(REAL_LIST.read_text(encoding="utf-8-sig"))["node_packs"]
    packs = {k: dict(real[k], sha256=sha) for k in ("skintoken",)}
    (tmp / "setup" / "downloads.local.json").write_text(json.dumps({"node_packs": packs}), encoding="utf-8")
    nodes = tmp / "comfy-nodes" / "vgac-nodes"
    nodes.mkdir(parents=True)
    (nodes / "__init__.py").write_text("# vgac", encoding="utf-8")


class FakePip:
    """Answers `pip list`, and writes a --report for a dry run. Records every call."""

    def __init__(self, installed: dict[str, str], would_install: dict[str, str], fail_install: bool = False) -> None:
        self.installed, self.would, self.fail_install = installed, would_install, fail_install
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> tuple[int, str]:
        self.calls.append(argv)
        if "list" in argv:
            return 0, json.dumps([{"name": k, "version": v} for k, v in self.installed.items()])
        if "--dry-run" in argv:
            rep = Path(argv[argv.index("--report") + 1])
            rep.write_text(json.dumps({"install": [{"metadata": {"name": k, "version": v}} for k, v in self.would.items()]}), encoding="utf-8")
            return 0, ""
        return (1, "") if self.fail_install else (0, "")


def test_the_report_names_the_packages_that_would_change_and_changes_nothing(box):
    tmp, _ = box
    inst = tmp / "Installs" / "ComfyUI (1)"
    app = make_install(inst)
    data = pack_zip("transformers==4.50.0\nopen3d\n# a comment\n")
    set_pins(tmp, data)
    (app / "custom_nodes" / "VGAC-NODES").mkdir()  # present under another capitalisation
    cand = comfy_find._candidate(inst, "desktop", "x")
    before = sorted(str(p) for p in app.rglob("*"))
    pip = FakePip({"transformers": "4.40.0", "torch": "2.12.1"}, {"transformers": "4.50.0", "open3d": "0.19.0", "torch": "2.12.1"})
    rep = comfy_find.packs_report(cand, PackNet(data), pip)
    present = {p["id"]: p["present"] for p in rep["packs"]}
    assert present == {"skintoken": False, "vgac": True}
    assert rep["python"]["checked"] and rep["python"]["changes"] == [{"name": "transformers", "from": "4.40.0", "to": "4.50.0"}]
    assert rep["python"]["adds"] == [{"name": "open3d", "to": "0.19.0"}]
    assert sorted(str(p) for p in app.rglob("*")) == before
    assert not any("install" in c and "--dry-run" not in c and "list" not in c for c in pip.calls)  # no real install ran


def test_no_pip_in_their_python_is_said_not_guessed(box):
    tmp, _ = box
    inst = tmp / "Installs" / "C"
    make_install(inst)
    data = pack_zip("open3d\n")
    set_pins(tmp, data)
    rep = comfy_find.packs_report(comfy_find._candidate(inst, "desktop", "x"), PackNet(data), lambda argv: (1, ""))
    assert rep["python"]["checked"] is False and "no pip" in rep["python"]["note"]


def test_go_ahead_adds_only_missing_packs_and_installs_their_requirements(box):
    tmp, _ = box
    inst = tmp / "Installs" / "C"
    app = make_install(inst)
    data = pack_zip("open3d\n")
    set_pins(tmp, data)
    mine = app / "custom_nodes" / "SomeoneElsesPack"  # a folder of theirs is never touched
    mine.mkdir()
    (mine / "keep.txt").write_text("mine", encoding="utf-8")
    pip = FakePip({}, {})
    out = comfy_find.apply_packs(comfy_find._candidate(inst, "desktop", "x"), PackNet(data), pip)
    assert set(out["added"]) == {"ComfyUI-SkinToken", "vgac-nodes (this project)"}
    assert (app / "custom_nodes" / "ComfyUI-SkinToken" / "__init__.py").is_file()
    assert (app / "custom_nodes" / "vgac-nodes" / "__init__.py").is_file()
    assert (mine / "keep.txt").read_text(encoding="utf-8") == "mine"
    assert not list((app / "custom_nodes").glob("*.vgac-tmp"))
    installs = [c for c in pip.calls if "-r" in c and "--dry-run" not in c]
    assert len(installs) == 1 and installs[0][:4] == [str(Path(inst / "standalone-env" / "python.exe").resolve()), "-m", "pip", "install"]


def test_a_failed_pip_install_is_reported(box):
    tmp, _ = box
    inst = tmp / "Installs" / "C"
    make_install(inst)
    data = pack_zip("open3d\n")
    set_pins(tmp, data)
    with pytest.raises(downloader.DownloadError) as e:
        comfy_find.apply_packs(comfy_find._candidate(inst, "desktop", "x"), PackNet(data), FakePip({}, {}, fail_install=True))
    assert e.value.code == "pip"


def test_a_wrong_pack_checksum_is_refused_before_anything_is_added(box):
    tmp, _ = box
    inst = tmp / "Installs" / "C"
    app = make_install(inst)
    set_pins(tmp, pack_zip("a\n"))
    with pytest.raises(downloader.DownloadError) as e:
        comfy_find.apply_packs(comfy_find._candidate(inst, "desktop", "x"), PackNet(pack_zip("tampered\n")), FakePip({}, {}))
    assert e.value.code == "bad_checksum" and not (app / "custom_nodes" / "ComfyUI-SkinToken").exists()


def test_apply_over_http_needs_the_go_ahead(box):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(comfy_find.router)
    r = TestClient(app).post("/api/comfy/packs/apply", json={"id": "c-nothing"})
    assert r.status_code == 400 and "Go ahead" in r.json()["detail"]
    r = TestClient(app).post("/api/comfy/choice/A")
    assert r.status_code == 400 and "Install VGAC's ComfyUI" in r.json()["detail"]


def test_a_node_pack_with_no_pin_is_not_fetched(box):
    tmp, _ = box
    inst = tmp / "Installs" / "C"
    make_install(inst)
    real = json.loads(REAL_LIST.read_text(encoding="utf-8-sig"))["node_packs"]
    (tmp / "setup" / "downloads.local.json").write_text(json.dumps({"node_packs": {"skintoken": dict(real["skintoken"], sha256="")}}), encoding="utf-8")
    net = PackNet(pack_zip("a\n"))
    with pytest.raises(downloader.DownloadError) as e:
        comfy_find.packs_report(comfy_find._candidate(inst, "desktop", "x"), net, FakePip({}, {}))
    assert e.value.code == "no_pin" and net.log == []
