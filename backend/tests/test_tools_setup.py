"""Finding, linking and getting Blender, and finding and guiding Unity (tools_setup.py). Every folder is a temporary one,
Blender's version comes from a stub runner, the network is a fake opener, and nothing is installed anywhere real."""

from __future__ import annotations

import hashlib
import io
import re
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
               registry=kw.get("registry", lambda p: []), running=kw.get("running", lambda n: []), steam=kw.get("steam", lambda: []))


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
    """Serves one zip for every address; `log` records every URL asked for."""

    def __init__(self, data: bytes, refuse: bool = False) -> None:
        self.data = data
        self.refuse = refuse
        self.log: list[str] = []

    def __call__(self, url: str, start: int):
        self.log.append(url)
        if self.refuse:
            import urllib.error
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        r = io.BytesIO(self.data[start:])
        r.status = 206 if start else 200
        r.headers = {"Content-Length": str(len(self.data) - start)}
        return r


def same_size_other(data: bytes) -> bytes:
    """Another file of exactly the same length, so only the checksum can tell it apart."""
    return data[:-1] + bytes([data[-1] ^ 1])


def pin_blender(tmp: Path, data: bytes) -> None:
    """The list's pin for Blender becomes the checksum and size of `data` (a stand-in for the real zip)."""
    (tmp / "setup" / "downloads.local.json").write_text(json.dumps({"blender": {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}}), encoding="utf-8")


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


def test_get_it_downloads_checks_unpacks_and_links(box, monkeypatch):
    tmp, machine = box
    data = blender_zip()
    pin_blender(tmp, data)
    net = FakeNet(data)
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
    assert net.log == [net.log[0]] and net.log[0].endswith(".zip")  # no checksum file is fetched from anywhere


def test_a_copy_already_unpacked_is_not_fetched_again(box, monkeypatch):
    tmp, _ = box
    make_blender(tmp / "local" / "tools" / "blender-5.2.3-windows-x64")
    run = runner_for({"blender-5.2.3": "Blender 5.2.3"})
    monkeypatch.setattr(setup_settings, "blender_runner", run)
    net = FakeNet(b"unused")
    job = tools_setup.start_blender_install(opener=net, runner=run, wait=True)
    assert job.state == "done" and net.log == []


def test_a_file_that_is_not_the_pinned_one_is_refused_and_nothing_is_installed(box):
    tmp, machine = box
    pin_blender(tmp, blender_zip())
    job = tools_setup.start_blender_install(opener=FakeNet(same_size_other(blender_zip())), runner=runner_for({}), wait=True)
    assert job.state == "failed" and job.error["code"] == "bad_checksum"
    assert not machine.exists() and not (tmp / "local" / "tools" / "blender-5.2.3-windows-x64").exists()


def test_with_no_pin_in_the_list_nothing_is_downloaded(box):
    tmp, _ = box
    (tmp / "setup" / "downloads.local.json").write_text(json.dumps({"blender": {"sha256": ""}}), encoding="utf-8")
    net = FakeNet(blender_zip())
    job = tools_setup.start_blender_install(opener=net, runner=runner_for({}), wait=True)
    assert job.state == "failed" and job.error["code"] == "no_pin" and net.log == []


def test_the_shipped_pin_is_a_real_sha256_and_size_and_names_the_zip(box):
    cat = downloader.catalogue()["blender"]
    assert re.fullmatch(r"[0-9a-f]{64}", cat["sha256"]) and cat["size"] > 300_000_000
    assert cat["zip"].endswith(NAME) and "mirrors" not in cat


def test_a_refused_download_says_what_to_do_instead(box):
    tmp, _ = box
    pin_blender(tmp, blender_zip())
    job = tools_setup.start_blender_install(opener=FakeNet(b"", refuse=True), runner=runner_for({}), wait=True)
    assert job.state == "failed" and job.error["code"] == "link_gone"
    assert "your own browser" in job.error["fix"] and "I downloaded the zip" in job.error["fix"] and "get around" in job.error["fix"]


def test_a_blender_that_is_too_old_in_the_zip_is_not_linked(box):
    tmp, machine = box
    data = blender_zip()
    pin_blender(tmp, data)
    run = runner_for({"blender-5.2.3": "Blender 4.0.0"})
    job = tools_setup.start_blender_install(opener=FakeNet(data), runner=run, wait=True)
    assert job.state == "failed" and "too old" in job.error["message"] and not machine.exists()


def test_a_zip_the_person_downloaded_is_used_only_if_it_is_the_pinned_file(box, monkeypatch):
    tmp, machine = box
    data = blender_zip()
    pin_blender(tmp, data)
    z = tmp / "Downloads" / NAME
    z.parent.mkdir()
    z.write_bytes(data)
    run = runner_for({"blender-5.2.3": "Blender 5.2.3"})
    monkeypatch.setattr(setup_settings, "blender_runner", run)
    out = tools_setup.use_blender_zip(str(z), runner=run)
    assert out["changed"] and out["checksum_checked"] and json.loads(machine.read_text(encoding="utf-8"))["backend"]["blender_exe"].endswith("blender.exe")
    assert z.exists()  # the person's own file is never removed


def test_a_downloaded_zip_that_is_not_the_pinned_file_is_refused(box):
    tmp, machine = box
    pin_blender(tmp, blender_zip())
    z = tmp / NAME
    z.write_bytes(blender_zip() + b"other version")
    with pytest.raises(downloader.DownloadError) as e:
        tools_setup.use_blender_zip(str(z), runner=runner_for({}))
    assert e.value.code == "bad_checksum" and "Use a different one" in e.value.fix and not machine.exists()


# --- Steam libraries on any drive ---------------------------------------------------------------------------------------

VDF = '''"libraryfolders"
{
	"0"
	{
		"path"		"%s"
		"label"		""
	}
	"1"
	{
		"path"		"%s"
	}
	"2"
	{
		"path"		"%s"
	}
}
'''


def steam_env(tmp: Path, roots) -> Env:
    e = env_for(tmp)
    e.steam = lambda: [str(r) for r in roots]
    return e


def test_steam_copies_are_found_in_every_library_listed_by_steams_own_vdf(box):
    tmp, _ = box
    steam = tmp / "Steam"
    lib2, lib3, gone = tmp / "SteamLibrary", tmp / "Other drive" / "Games", tmp / "unplugged" / "Lib"
    (steam / "steamapps").mkdir(parents=True)

    def esc(p):
        return str(p).replace("\\", "\\\\")  # vdf escapes backslashes

    (steam / "steamapps" / "libraryfolders.vdf").write_text(VDF % (esc(steam), esc(lib2), esc(gone)), encoding="utf-8")
    b2 = make_blender(lib2 / "steamapps" / "common" / "Blender")
    b1 = make_blender(steam / "steamapps" / "common" / "Blender")
    make_blender(lib3 / "steamapps" / "common" / "Blender")  # a library the vdf does not list: not looked at
    run = runner_for({"": "Blender 5.2.3"})
    found = tools_setup.find_blender(steam_env(tmp, [steam]), run, current="")
    assert {Path(f["path"]) for f in found} == {b1, b2}
    assert {f["source"] for f in found} == {"Steam"}  # labelled Steam, no reliance on the installed-programs list


def test_a_malformed_or_missing_vdf_still_finds_the_steam_folders_own_copy(box):
    tmp, _ = box
    steam = tmp / "Steam"
    (steam / "steamapps").mkdir(parents=True)
    (steam / "steamapps" / "libraryfolders.vdf").write_bytes(b'"libraryfolders" { "path" "unterminated \x00\xff garbage')
    b1 = make_blender(steam / "steamapps" / "common" / "Blender")
    run = runner_for({"": "Blender 5.2.3"})
    assert [Path(f["path"]) for f in tools_setup.find_blender(steam_env(tmp, [steam]), run, current="")] == [b1]
    (steam / "steamapps" / "libraryfolders.vdf").unlink()
    assert [Path(f["path"]) for f in tools_setup.find_blender(steam_env(tmp, [steam]), run, current="")] == [b1]
    assert tools_setup.find_blender(steam_env(tmp, [tmp / "nosuch"]), run, current="") == []


def test_steam_is_also_looked_for_at_its_default_path_when_the_registry_says_nothing(box):
    tmp, _ = box
    b = make_blender(tmp / "PF" / "Steam" / "steamapps" / "common" / "Blender")
    found = tools_setup.find_blender(steam_env(tmp, []), runner_for({"": "Blender 5.2.3"}), current="")
    assert [Path(f["path"]) for f in found] == [b] and found[0]["source"] == "Steam"


def test_the_panel_offers_the_official_store_pages_and_nothing_to_download(box):
    offer = tools_setup.blender_offer()
    assert offer["steam_page"].startswith("https://store.steampowered.com/") and offer["store_page"].startswith("https://apps.microsoft.com/")


# --- Blender changed underneath (Steam / the Store update it) -------------------------------------------------------------


def _script() -> str:
    return next(config.BLENDER_SCRIPTS_DIR.glob("*.py")).name


def test_a_different_usable_version_is_reported_and_the_job_still_runs(box):
    from vgac import blender_watch, preflight, tools

    tmp, _ = box
    exe = make_blender(tmp / "Steamish" / "Blender")
    s = config.get_settings().model_copy(update={"blender_exe": exe})
    assert blender_watch.check(exe, lambda p: "Blender 5.2.3") is None  # first sight: remembered, silent
    assert blender_watch.check(exe, lambda p: "Blender 5.2.3") is None
    exe.write_bytes(exe.read_bytes() + b" updated")  # an in-place update changes size and time
    n = blender_watch.check(exe, lambda p: "Blender 5.4.1")
    assert n and n["kind"] == "changed" and n["usable"] and not n["tested"]
    assert "5.2.3 to 5.4.1" in n["message"] and "untested" in n["message"] and "will be used" in n["message"]
    item = preflight.check_blender(s, lambda p: "Blender 5.4.1")
    assert item["state"] == "ok" and "5.2.3 to 5.4.1" in item["detail"]  # still ready, with the plain note
    assert tools.blender_cmd(s, _script(), [])[0] == str(exe)  # the job's command is built; nothing raised
    blender_watch.accept(exe, lambda p: "Blender 5.4.1")
    assert blender_watch.check(exe, lambda p: "Blender 5.4.1") is None


def test_going_back_to_the_tested_version_says_so(box):
    from vgac import blender_watch

    tmp, _ = box
    exe = make_blender(tmp / "S" / "Blender")
    blender_watch.check(exe, lambda p: "Blender 5.4.1")
    exe.write_bytes(exe.read_bytes() + b"x")
    n = blender_watch.check(exe, lambda p: "Blender 5.2.3")
    assert n["usable"] and n["tested"] and "was tested with" in n["message"]


def test_an_update_to_a_too_old_missing_or_unreadable_blender_is_a_plain_sentence(box, monkeypatch):
    from vgac import blender_watch, preflight, tools

    tmp, _ = box
    exe = make_blender(tmp / "S" / "Blender")
    s = config.get_settings().model_copy(update={"blender_exe": exe})
    blender_watch.check(exe, lambda p: "Blender 5.2.3")
    exe.write_bytes(exe.read_bytes() + b"1")
    old = blender_watch.check(exe, lambda p: "Blender 4.2.1")
    assert old["kind"] == "old" and not old["usable"] and "too old" in old["message"] and "newer Blender" in old["message"]
    assert "4.2.1" in preflight.check_blender(s, lambda p: "Blender 4.2.1")["detail"]
    monkeypatch.setattr(preflight, "_run_version", lambda p: "Blender 4.2.1")  # what a job's own check reads
    preflight._blender_versions.clear()
    with pytest.raises(tools.ToolError) as e:
        tools.blender_cmd(s, _script(), [])
    assert "too old" in str(e.value) and "exited" not in str(e.value) and "Traceback" not in str(e.value)
    exe.write_bytes(exe.read_bytes() + b"2")
    unreadable = blender_watch.check(exe, lambda p: "gibberish")
    assert unreadable["kind"] == "unreadable" and not unreadable["usable"] and "5.2.3" in unreadable["message"]
    exe.unlink()
    gone = blender_watch.check(exe, lambda p: "")
    assert gone["kind"] == "missing" and "no longer there" in gone["message"]
    assert "no longer there" in preflight.check_blender(s, lambda p: "")["detail"]
    with pytest.raises(tools.ToolError) as e2:
        tools.blender_cmd(s, _script(), [])
    assert "not found" in str(e2.value)


def test_the_watch_never_raises_and_does_nothing_with_no_blender_set(box, monkeypatch):
    from vgac import blender_watch, preflight

    assert blender_watch.check(None) is None and blender_watch.check("") is None
    tmp, _ = box
    exe = make_blender(tmp / "Q" / "Blender")

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(preflight, "blender_version", boom)
    assert blender_watch.check(exe) is None


def test_the_panel_data_carries_the_change_and_the_seen_route_clears_it(box, monkeypatch):
    from fastapi.testclient import TestClient

    from vgac import blender_watch, preflight
    from vgac.main import app

    tmp, _ = box
    exe = make_blender(tmp / "Steamish" / "Blender")
    monkeypatch.setattr(preflight, "_run_version", lambda p: "Blender 5.2.3")
    blender_watch.check(exe)
    config.reset_settings()
    monkeypatch.setattr(setup_settings, "blender_runner", lambda p: "Blender 5.2.3")
    setup_settings.set_setting("blender_exe", str(exe))
    exe.write_bytes(exe.read_bytes() + b"upd")
    monkeypatch.setattr(preflight, "_run_version", lambda p: "Blender 5.3.0")
    monkeypatch.setattr(tools_setup, "find_blender", lambda *a, **k: [])
    c = TestClient(app)
    got = c.get("/api/tools/blender").json()
    assert got["changed"] and "5.2.3 to 5.3.0" in got["changed"]["message"]
    c.post("/api/tools/blender/seen")
    assert c.get("/api/tools/blender").json()["changed"] is None


def test_a_copy_in_a_steam_library_reads_steam_whichever_finder_saw_it_first(box):
    tmp, _ = box
    b = make_blender(tmp / "SteamLibrary" / "steamapps" / "common" / "Blender")
    run = runner_for({"": "Blender 5.2.3"})
    # the installed-programs list, the saved setting and the running program all name the same copy before the Steam finder
    env = env_for(tmp, registry=lambda p: [str(b.parent)], running=lambda n: [str(b)])
    for current in ("", str(b)):
        found = tools_setup.find_blender(env, run, current=current)
        assert [Path(f["path"]) for f in found] == [b] and found[0]["source"] == "Steam"
    store = make_blender(tmp / "PF" / "WindowsApps" / "BlenderFoundation.Blender_5.2.3.0_x64__abc")
    found = tools_setup.find_blender(env_for(tmp, running=lambda n: [str(store)]), run, current="")
    assert found[0]["source"] == "Microsoft Store"
