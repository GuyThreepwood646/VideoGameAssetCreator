"""Finding, linking and getting Blender, and finding and guiding Unity (tools_setup.py). Every folder is a temporary one,
Blender's version comes from a stub runner, the network is a fake opener, and nothing is installed anywhere real."""

from __future__ import annotations

import hashlib
import io
import shutil
import json
import zipfile
from pathlib import Path

import pytest
import yaml

from vgac import config, db, downloader, firstrun, setup_settings, tools_setup
from vgac.tools_setup import Env

REAL_LIST = Path(__file__).resolve().parents[2] / "setup" / "downloads.json"
NAME = "blender-5.2.3-windows-x64.zip"


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
    shutil.copy(REAL_LIST, tmp_path / "setup" / "downloads.json")  # the shipped list, so the Blender entry is the real one
    config.reset_settings()
    db.reset_engine()
    yield tmp_path, machine
    db.reset_engine()
    config.reset_settings()


def runner_for(versions: dict[str, str]):
    def run(p: Path) -> str:
        for key, text in versions.items():
            if key in str(p):
                return text
        return ""
    return run


def make_blender(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    exe = folder / "blender.exe"
    exe.write_bytes(b"MZ stub " + folder.name.encode())
    return exe


def env_for(tmp: Path, **kw) -> Env:
    return Env(program_files=[tmp / "PF"], appdata=tmp / "AppData", which=kw.get("which", lambda n: None),
               registry=kw.get("registry", lambda p: []), running=kw.get("running", lambda n: []))


def test_finds_every_copy_and_marks_the_tested_one_best(box):
    tmp, _ = box
    a = make_blender(tmp / "PF" / "Blender Foundation" / "Blender 5.2")
    b = make_blender(tmp / "PF" / "Blender Foundation" / "Blender 4.2")
    c = make_blender(tmp / "local" / "tools" / "blender-5.4.0-windows-x64")
    d = make_blender(tmp / "elsewhere" / "pathcopy")
    run = runner_for({"Blender 5.2": "Blender 5.2.3 (hash)", "Blender 4.2": "Blender 4.2.1 LTS", "5.4.0": "Blender 5.4.0", "pathcopy": "nonsense"})
    found = tools_setup.find_blender(env_for(tmp, which=lambda n: str(d)), run, current="")
    by = {Path(f["path"]): f for f in found}
    assert set(by) == {a, b, c, d}
    assert found[0]["path"] == str(a) and found[0]["best"] and found[0]["tested"]
    assert by[c]["usable"] and not by[c]["tested"]
    assert not by[b]["usable"] and "too old" in by[b]["reason"] and "5.0" in by[b]["reason"]
    assert not by[d]["usable"] and "did not say" in by[d]["reason"]
    assert [f["usable"] for f in found] == [True, True, False, False]  # usable ones first


def test_the_saved_setting_and_running_and_registry_copies_are_found_once(box):
    tmp, _ = box
    exe = make_blender(tmp / "Tools" / "Blender")
    run = runner_for({"Blender": "Blender 5.2.3"})
    env = env_for(tmp, registry=lambda pub: [str(exe.parent)] if pub == "Blender Foundation" else [], running=lambda n: [str(exe)])
    found = tools_setup.find_blender(env, run, current=str(exe))
    assert len(found) == 1 and found[0]["source"] == "your saved setting"
    found = tools_setup.find_blender(env, run, current="")
    assert len(found) == 1 and found[0]["source"] == "Windows list of installed programs"


def test_nothing_found_is_an_empty_list_and_a_missing_saved_file_is_ignored(box):
    tmp, _ = box
    assert tools_setup.find_blender(env_for(tmp), runner_for({}), current=str(tmp / "gone" / "blender.exe")) == []


def test_use_this_saves_through_the_one_validator(box, monkeypatch):
    tmp, machine = box
    monkeypatch.setattr(setup_settings, "blender_runner", lambda p: "Blender 5.2.3")
    exe = make_blender(tmp / "Tools" / "Blender")
    client = _client()
    r = client.post("/api/tools/blender/use", json={"path": str(exe)})
    assert r.status_code == 200, r.text
    assert json.loads(machine.read_text(encoding="utf-8"))["backend"]["blender_exe"] == str(exe)
    monkeypatch.setattr(setup_settings, "blender_runner", lambda p: "Blender 4.2.1")
    old = make_blender(tmp / "Old" / "Blender")  # a different file: the version of a file is read once and kept
    r = client.post("/api/tools/blender/use", json={"path": str(old)})
    assert r.status_code == 400 and "5.0 or newer" in r.json()["detail"]


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(tools_setup.router)
    return TestClient(app)


def blender_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("blender-5.2.3-windows-x64/blender.exe", b"MZ fake blender")
        z.writestr("blender-5.2.3-windows-x64/readme.txt", b"hi")
    return buf.getvalue()


class FakeNet:
    """Serves the zip and its checksum file; `log` records every URL asked for."""

    def __init__(self, data: bytes, sha_text: str | None = None, refuse: bool = False) -> None:
        self.data = data
        self.sha = sha_text if sha_text is not None else f"{hashlib.sha256(data).hexdigest()}  {NAME}\n"
        self.refuse = refuse
        self.log: list[str] = []

    def __call__(self, url: str, start: int):
        self.log.append(url)
        if self.refuse:
            import urllib.error
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        body = self.sha.encode() if url.endswith(".sha256") else self.data[start:]
        r = io.BytesIO(body)
        r.status = 206 if start else 200
        r.headers = {"Content-Length": str(len(body))}
        return r


def test_get_it_downloads_checks_unpacks_and_links(box, monkeypatch):
    tmp, machine = box
    net = FakeNet(blender_zip())
    run = runner_for({"blender-5.2.3": "Blender 5.2.3"})
    monkeypatch.setattr(setup_settings, "blender_runner", run)
    job = tools_setup.start_blender_install(opener=net, runner=run, wait=True)
    assert job.state == "done", job.error
    exe = tmp / "local" / "tools" / "blender-5.2.3-windows-x64" / "blender.exe"
    assert exe.is_file()
    assert json.loads(machine.read_text(encoding="utf-8"))["backend"]["blender_exe"] == str(exe)
    assert not (tmp / "local" / "tools" / "downloads" / NAME).exists()  # the zip is tidied away
    ledger = json.loads((tmp / "local" / "installed.json").read_text(encoding="utf-8"))
    assert "blender-5.2.3-windows-x64" in json.dumps(ledger) and "added" in json.dumps(ledger)
    assert any(u.endswith(".sha256") for u in net.log) and any(u.endswith(".zip") for u in net.log)


def test_a_copy_already_unpacked_is_not_fetched_again(box, monkeypatch):
    tmp, _ = box
    make_blender(tmp / "local" / "tools" / "blender-5.2.3-windows-x64")
    run = runner_for({"blender-5.2.3": "Blender 5.2.3"})
    monkeypatch.setattr(setup_settings, "blender_runner", run)
    net = FakeNet(b"unused")
    job = tools_setup.start_blender_install(opener=net, runner=run, wait=True)
    assert job.state == "done" and net.log == []


def test_a_wrong_checksum_is_refused_and_nothing_is_installed(box, monkeypatch):
    tmp, machine = box
    net = FakeNet(blender_zip(), sha_text=f"{'0' * 64}  {NAME}\n")
    job = tools_setup.start_blender_install(opener=net, runner=runner_for({}), wait=True)
    assert job.state == "failed" and job.error["code"] == "bad_checksum"
    assert not machine.exists() and not (tmp / "local" / "tools" / "blender-5.2.3-windows-x64").exists()


def test_a_missing_checksum_file_stops_before_downloading_the_zip(box):
    net = FakeNet(blender_zip(), sha_text="no hashes here\n")
    job = tools_setup.start_blender_install(opener=net, runner=runner_for({}), wait=True)
    assert job.state == "failed" and job.error["code"] == "no_checksum"
    assert not any(u.endswith(".zip") for u in net.log)


def test_a_refused_download_says_what_to_do_instead(box):
    job = tools_setup.start_blender_install(opener=FakeNet(b"", refuse=True), runner=runner_for({}), wait=True)
    assert job.state == "failed" and job.error["message"] and job.error["fix"]


def test_a_blender_that_is_too_old_in_the_zip_is_not_linked(box, monkeypatch):
    tmp, machine = box
    run = runner_for({"blender-5.2.3": "Blender 4.0.0"})
    job = tools_setup.start_blender_install(opener=FakeNet(blender_zip()), runner=run, wait=True)
    assert job.state == "failed" and "too old" in job.error["message"] and not machine.exists()


def test_a_zip_the_person_downloaded_is_checked_and_used(box, monkeypatch):
    tmp, machine = box
    data = blender_zip()
    z = tmp / "Downloads" / NAME
    z.parent.mkdir()
    z.write_bytes(data)
    run = runner_for({"blender-5.2.3": "Blender 5.2.3"})
    monkeypatch.setattr(setup_settings, "blender_runner", run)
    out = tools_setup.use_blender_zip(str(z), opener=FakeNet(data), runner=run)
    assert out["changed"] and json.loads(machine.read_text(encoding="utf-8"))["backend"]["blender_exe"].endswith("blender.exe")
    assert z.exists()  # the person's own file is never removed


def test_a_downloaded_zip_with_the_wrong_checksum_is_refused(box):
    tmp, machine = box
    z = tmp / NAME
    z.write_bytes(blender_zip())
    with pytest.raises(downloader.DownloadError) as e:
        tools_setup.use_blender_zip(str(z), opener=FakeNet(b"other data"), runner=runner_for({}))
    assert e.value.code == "bad_checksum" and not machine.exists()


def test_a_zip_that_escapes_its_folder_is_refused(box, tmp_path):
    bad = tmp_path / "evil.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("../outside.txt", b"x")
    with pytest.raises(downloader.DownloadError) as e:
        tools_setup.safe_unzip(bad, tmp_path / "into")
    assert e.value.code == "bad_zip" and not (tmp_path / "outside.txt").exists()


def test_the_offer_says_what_where_how_big_and_that_no_admin_is_needed(box):
    o = tools_setup.blender_offer()
    assert o["available"] and o["from"] == "download.blender.org" and o["size_mb"] > 0 and o["needs_admin"] is False
    assert "tools" in o["goes_to"] and o["licence"]


def test_sha_file_parsing():
    h = "a" * 64
    assert tools_setup.parse_sha_file(f"{h}  {NAME}\n{'b' * 64}  other.zip", NAME) == h
    assert tools_setup.parse_sha_file(f"{h} *{NAME}", NAME) == h
    assert tools_setup.parse_sha_file("junk", NAME) == ""


def make_unity(root: Path, version: str) -> Path:
    exe = root / version / "Editor" / "Unity.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    return exe


def test_unity_prefers_the_pinned_version_and_explains_the_others(box):
    tmp, _ = box
    root = tmp / "PF" / "Unity" / "Hub" / "Editor"
    make_unity(root, "6000.0.30f1")
    pinned = make_unity(root, "6000.4.3f1")
    make_unity(root, "2022.3.10f1")
    found = tools_setup.find_unity(env_for(tmp), current="")
    assert found[0]["path"] == str(pinned) and found[0]["best"] and found[0]["tested"]
    other = next(f for f in found if f["version"] == "6000.0.30f1")
    assert other["usable"] and "untested" in other["reason"]
    old = next(f for f in found if f["version"] == "2022.3.10f1")
    assert not old["usable"]


def test_unity_hub_folder_setting_is_followed(box):
    tmp, _ = box
    editors = tmp / "D" / "Editors"
    exe = make_unity(editors, "6000.4.3f1")
    f = tmp / "AppData" / "UnityHub" / "secondaryInstallPath.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps(str(editors)), encoding="utf-8")
    found = tools_setup.find_unity(env_for(tmp), current="")
    assert [x["path"] for x in found] == [str(exe)] and found[0]["source"].startswith("the folder Unity Hub")


def test_unity_guide_is_honest_that_it_cannot_install(box):
    tmp, _ = box
    g = tools_setup.unity_guide(env_for(tmp))
    assert g["optional"] and not g["hub_installed"] and "cannot install" in g["cannot_install"]
    assert g["download_page"].startswith("https://unity.com") and "200,000" in g["licence_line"] and g["steps"]
    hub = tmp / "PF" / "Unity Hub" / "Unity Hub.exe"
    hub.parent.mkdir(parents=True)
    hub.write_bytes(b"MZ")
    assert tools_setup.unity_guide(env_for(tmp))["hub_installed"]


def test_use_unity_exe_saves_it(box):
    tmp, machine = box
    exe = make_unity(tmp / "Editors", "6000.4.3f1")
    r = _client().post("/api/tools/unity/use", json={"path": str(exe)})
    assert r.status_code == 200, r.text
    assert json.loads(machine.read_text(encoding="utf-8"))["backend"]["unity_exe"] == str(exe)
    r = _client().post("/api/tools/unity/use", json={"path": str(tmp / "notunity.exe")})
    assert r.status_code == 400


def test_the_user_agent_is_an_honest_client_name_never_a_browser(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["ua"] = req.get_header("User-agent")
        return io.BytesIO(b"x")

    monkeypatch.setattr(downloader.urllib.request, "urlopen", fake_urlopen)
    downloader.default_open("https://example.test/f", 0)
    assert seen["ua"] == f"VGAC-setup/{__import__('vgac').__version__}"
    assert "Mozilla" not in seen["ua"] and downloader.USER_AGENT == seen["ua"]


class MainRefused(FakeNet):
    """download.blender.org refuses; the official mirrors answer."""

    def __call__(self, url: str, start: int):
        if "download.blender.org" in url:
            self.log.append(url)
            import urllib.error
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        return super().__call__(url, start)


def test_when_the_main_server_refuses_the_official_mirrors_are_tried(box, monkeypatch):
    tmp, machine = box
    run = runner_for({"blender-5.2.3": "Blender 5.2.3"})
    monkeypatch.setattr(setup_settings, "blender_runner", run)
    net = MainRefused(blender_zip())
    job = tools_setup.start_blender_install(opener=net, runner=run, wait=True)
    assert job.state == "done", job.error
    mirror_hits = [u for u in net.log if "download.blender.org" not in u]
    assert any(u.endswith(".zip") for u in mirror_hits) and all(u.startswith("https://") for u in mirror_hits)


def test_when_every_server_refuses_the_person_is_told_to_use_their_own_browser(box):
    job = tools_setup.start_blender_install(opener=FakeNet(b"", refuse=True), runner=runner_for({}), wait=True)
    assert job.state == "failed" and job.error["code"] == "link_gone"
    assert "your own browser" in job.error["fix"] and "I downloaded the zip" in job.error["fix"]
    assert "get around" in job.error["fix"]
