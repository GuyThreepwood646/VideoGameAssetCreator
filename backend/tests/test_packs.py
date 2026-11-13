"""The in-app "Get the hair library" engine (vgac/packs.py): routes, the background run, downloads, the click-through zip, the
build and its failures. The network and Blender are faked; the zips are tiny ones made here. Nothing touches the real
library, runtime folder or setup/downloads.json."""
from __future__ import annotations

import io
import json
import time
import urllib.error
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vgac import hairlib_builder, hairstyles, my_hair, packs, preflight


def make_zip(files: dict[str, bytes]) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n, data in files.items():
            z.writestr(n, data)
    return b.getvalue()


SYS_ZIP = make_zip({"hair/a/a.mhclo": b"x" * 400, "proxymeshes/p.obj": b"y" * 400})
QUA_ZIP = make_zip({"Q Pack/License.txt": b"CC0" * 100, "Q Pack/h.gltf": b"z" * 400})
WRONG_ZIP = make_zip({"something/else.txt": b"nope"})
EVIL_ZIP = make_zip({"hair/ok.txt": b"fine" * 100, "proxymeshes/p.obj": b"y" * 400, "../evil.txt": b"bad"})


@pytest.fixture
def world(monkeypatch, tmp_path):
    project = tmp_path / "project"
    (project / "setup").mkdir(parents=True)
    lib = tmp_path / "library"
    lib.mkdir()
    dl = {
        "hair_sources": [
            {"id": "sysx", "name": "System pack", "zip_name": "sys.zip", "zip": "http://fake/sys.zip", "zip_alt": "http://alt/sys.zip",
             "page": "http://page/sys", "zip_size": len(SYS_ZIP), "check_dir": "hair", "zip_expect": ["hair/", "proxymeshes/"]},
            {"id": "quax", "name": "Quaternius pack", "zip_name": "qua.zip", "page": "http://page/qua", "zip_size": len(QUA_ZIP),
             "check_dir": "Q Pack", "zip_expect": ["Q Pack/License.txt"], "click_text": "Click the thing."},
        ],
        "hair_packs": [
            {"id": "p_sys", "name": "System styles", "styles": 7, "what": "Seven.", "source": "sysx", "catalogue_pack": "System pack", "catalogue_author": "A"},
            {"id": "p_qua", "name": "Quaternius styles", "styles": 5, "what": "Five.", "source": "quax", "catalogue_pack": "Quaternius pack", "catalogue_author": "Q"},
            {"id": "p_rule", "name": "Rule built", "styles": 3, "what": "Three.", "no_download": True, "catalogue_pack": "VGAC rules", "catalogue_author": "VGAC"},
        ],
    }
    (project / "setup" / "downloads.json").write_text(json.dumps(dl), encoding="utf-8")
    exe = tmp_path / "blender.exe"
    exe.write_text("x")
    settings = SimpleNamespace(library_path=lib, blender_exe=exe, clips_libraries={})
    monkeypatch.setattr(packs, "project_dir", lambda: project)
    monkeypatch.setattr(packs, "library_dir", lambda: lib)
    monkeypatch.setattr(packs, "get_settings", lambda: settings)
    monkeypatch.setattr("vgac.config.get_settings", lambda: settings)
    monkeypatch.setattr(packs, "_blender_exe", lambda: exe)
    empty = tmp_path / "no_project_copy"
    empty.mkdir()
    monkeypatch.setattr(hairstyles, "DATA_DIR", empty)  # the copy inside the project does not exist here
    monkeypatch.setattr(my_hair, "entries", lambda settings=None: {})
    hairstyles.use_dir(None)
    packs.RUNS.clear()
    built: list[dict] = []

    def fake_build(packs_dir, out, **kw):
        out = Path(out)
        built.append({"packs": sorted(p.name for p in Path(packs_dir).iterdir()), "out": out})
        (out / "styles").mkdir(parents=True, exist_ok=True)
        entry = {"id": "fake_a", "name": "Fake A", "pack": "System pack", "author": "A", "family": "short", "kind": "shell", "alpha": "opaque",
                 "triangles": 800, "phone_ok": True, "licence": "CC0-1.0", "source_url": "", "thumbs": {}}
        (out / "catalogue.json").write_text(json.dumps({"styles": {"fake_a": entry}, "refused": [], "left_out": {}, "version": 1}), encoding="utf-8")
        kw["progress"](0.5, "Building")
        return {"styles": 1}

    monkeypatch.setattr(hairlib_builder, "build", fake_build)
    monkeypatch.setattr(hairlib_builder, "verify", lambda d: ({}, []))
    app = FastAPI()
    app.include_router(packs.router)
    yield SimpleNamespace(client=TestClient(app), project=project, lib=lib, built=built, dl=dl, tmp=tmp_path)
    hairstyles.use_dir(None)
    packs.RUNS.clear()


class Resp:
    def __init__(self, data: bytes, start: int = 0, partial: bool = False, hook=None):
        self.data, self.pos, self.hook = data[start:], 0, hook
        self.status = 206 if partial else 200
        self.headers = {"Content-Length": str(len(self.data))}

    def read(self, n: int) -> bytes:
        if self.hook:
            self.hook()
        chunk = self.data[self.pos:self.pos + min(n, 100)]  # small chunks so a cancel can land in the middle
        self.pos += len(chunk)
        return chunk

    def close(self) -> None:
        pass


def serve(monkeypatch, files: dict[str, bytes], seen: list | None = None, hook=None):
    def _open(url, start):
        if seen is not None:
            seen.append((url, start))
        data = files[url]
        return Resp(data, start, partial=bool(start), hook=hook)
    monkeypatch.setattr(packs, "_open", _open)


def wait(client, kit="hair", until=("done", "failed", "waiting", "cancelled")):
    for _ in range(400):
        st = client.get(f"/api/packs/{kit}").json()
        if st["run"]["state"] in until:
            return st
        time.sleep(0.03)
    raise AssertionError("the run did not finish: " + json.dumps(st["run"]))


def start(client, choices=None):
    r = client.post("/api/packs/hair/start", json={"choices": choices or []})
    assert r.status_code == 200
    return r.json()


def put_zip(client, source, data):
    return client.post(f"/api/packs/hair/zip/{source}", content=data, headers={"content-type": "application/octet-stream"})


# --- the happy path, with the click-through zip --------------------------------------------------------------------------


def test_status_before_anything(world):
    st = world.client.get("/api/packs/hair").json()
    assert st["installed"] is False and st["run"]["state"] == "idle"
    assert [s["id"] for s in st["sources"]] == ["sysx", "quax"]
    assert st["sources"][0]["direct"] is True and st["sources"][1]["direct"] is False
    assert st["sources"][1]["click_text"] == "Click the thing."
    assert [c["id"] for c in st["choices"]] == ["p_sys", "p_qua"]  # the rule-built pack is not a download
    assert st["download_bytes"] > 0 and st["free_bytes"] > 0


def test_download_then_wait_for_the_clickthrough_zip_then_build(world, monkeypatch):
    serve(monkeypatch, {"http://fake/sys.zip": SYS_ZIP})
    start(world.client)
    st = wait(world.client)
    assert st["run"]["state"] == "waiting" and st["run"]["waiting_for"] == ["quax"]
    assert st["sources"][0]["state"] == "downloaded"  # the direct one is already there
    # a wrong file is refused with a plain sentence and nothing is kept
    r = put_zip(world.client, "quax", WRONG_ZIP)
    assert r.status_code == 400 and "does not contain" in r.json()["detail"]
    assert not (world.project / "runtime" / "hairpacks" / "quax" / "qua.zip").exists()
    # the zip for the other pack is named as such
    r = put_zip(world.client, "quax", SYS_ZIP)
    assert r.status_code == 400 and "System pack" in r.json()["detail"]
    # the right one carries the run on to the end without another press
    r = put_zip(world.client, "quax", QUA_ZIP)
    assert r.status_code == 200
    st = wait(world.client, until=("done", "failed"))
    assert st["run"]["state"] == "done", st["run"]
    assert st["installed"] is True and st["where"] == "built" and st["count"] == 1
    assert (world.lib / "hairlib" / "catalogue.json").is_file()
    assert not (world.lib / "hairlib.new").exists() and not (world.lib / "hairlib.work").exists()
    assert world.built and world.built[0]["packs"] == ["quax", "sysx"]
    assert (world.project / "runtime" / "hairpacks" / "sysx" / "unpacked" / "hair" / "a" / "a.mhclo").is_file()
    assert not (world.lib / "hair-packs.json").exists()  # every pack chosen: no picks file
    assert hairstyles.library_dir() == world.lib / "hairlib" and hairstyles.installed()  # no restart needed


def test_choosing_only_one_pack_needs_no_other_zip_and_is_recorded(world, monkeypatch):
    serve(monkeypatch, {"http://fake/sys.zip": SYS_ZIP})
    start(world.client, ["p_sys"])
    st = wait(world.client)
    assert st["run"]["state"] == "done"
    picks = json.loads((world.lib / "hair-packs.json").read_text(encoding="utf-8"))["packs"]
    assert {p["id"] for p in picks} == {"p_sys", "p_rule"}  # the rule-built pack is always kept
    assert [c["id"] for c in st["choices"] if c["built"]] == ["p_sys"]


# --- failures: each has a plain message and a way to try again -------------------------------------------------------------


def test_no_internet_then_try_again(world, monkeypatch):
    def down(url, start):
        raise urllib.error.URLError("no route")
    monkeypatch.setattr(packs, "_open", down)
    start(world.client, ["p_sys"])
    st = wait(world.client)
    err = st["run"]["error"]
    assert st["run"]["state"] == "failed" and err["code"] == "no_internet"
    assert "could not reach the internet" in err["message"] and "Try again" in err["fix"]
    serve(monkeypatch, {"http://fake/sys.zip": SYS_ZIP})
    start(world.client, ["p_sys"])
    assert wait(world.client)["run"]["state"] == "done"


def test_link_gone_points_to_the_page_and_a_dropped_zip_works(world, monkeypatch):
    def gone(url, start):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    monkeypatch.setattr(packs, "_open", gone)
    start(world.client, ["p_sys"])
    st = wait(world.client)
    err = st["run"]["error"]
    assert err["code"] == "link_gone" and "no longer works" in err["message"] and "http://page/sys" in err["fix"] and "drop" in err["fix"]
    r = put_zip(world.client, "sysx", SYS_ZIP)  # the person downloads it some other way and drops it
    assert r.status_code == 200
    assert wait(world.client)["run"]["state"] == "done"


def test_cancel_keeps_the_part_and_the_next_start_resumes(world, monkeypatch):
    seen: list = []
    calls = {"n": 0}

    def hook():
        calls["n"] += 1
        if calls["n"] == 3:
            world.client.post("/api/packs/hair/cancel")
    serve(monkeypatch, {"http://fake/sys.zip": SYS_ZIP}, seen, hook)
    start(world.client, ["p_sys"])
    st = wait(world.client)
    assert st["run"]["state"] == "cancelled"
    part = world.project / "runtime" / "hairpacks" / "sysx" / "sys.zip.part"
    assert part.is_file() and 0 < part.stat().st_size < len(SYS_ZIP)
    kept = part.stat().st_size
    serve(monkeypatch, {"http://fake/sys.zip": SYS_ZIP}, seen)
    start(world.client, ["p_sys"])
    assert wait(world.client)["run"]["state"] == "done"
    assert seen[-1] == ("http://fake/sys.zip", kept)  # asked only for the rest
    assert not part.exists()


def test_a_damaged_download_is_refused_not_used(world, monkeypatch):
    serve(monkeypatch, {"http://fake/sys.zip": WRONG_ZIP})
    start(world.client, ["p_sys"])
    st = wait(world.client)
    assert st["run"]["state"] == "failed" and st["run"]["error"]["code"] == "bad_download"
    assert not (world.project / "runtime" / "hairpacks" / "sysx" / "sys.zip").exists()


def test_not_enough_space_is_found_before_anything_is_downloaded(world, monkeypatch):
    monkeypatch.setattr(packs, "free_bytes", lambda where: 1024)
    seen: list = []
    serve(monkeypatch, {"http://fake/sys.zip": SYS_ZIP}, seen)
    start(world.client, ["p_sys"])
    st = wait(world.client)
    assert st["run"]["state"] == "failed" and st["run"]["error"]["code"] == "no_space"
    assert "not enough free space" in st["run"]["error"]["message"] and "Try again" in st["run"]["error"]["fix"]
    assert seen == []


def test_blender_missing_says_so(world, monkeypatch):
    def nope():
        raise packs.PackError("blender_missing", "The hairstyles are built with Blender, and Blender was not found.", "Install Blender 5.")
    monkeypatch.setattr(packs, "_blender_exe", nope)
    serve(monkeypatch, {"http://fake/sys.zip": SYS_ZIP})
    start(world.client, ["p_sys"])
    st = wait(world.client)
    assert st["run"]["state"] == "failed" and st["run"]["error"]["code"] == "blender_missing"
    assert not (world.lib / "hairlib").exists()


def test_a_failed_build_leaves_the_existing_library_alone(world, monkeypatch):
    old = world.lib / "hairlib"
    old.mkdir()
    (old / "catalogue.json").write_text(json.dumps({"styles": {"keep_me": {"id": "keep_me", "name": "Keep", "pack": "x", "author": "y", "family": "short",
                                                                          "kind": "shell", "triangles": 1}}, "version": 1}), encoding="utf-8")

    def boom(*a, **k):
        raise hairlib_builder.BuildError("blender died", "build_failed")
    monkeypatch.setattr(hairlib_builder, "build", boom)
    serve(monkeypatch, {"http://fake/sys.zip": SYS_ZIP})
    start(world.client, ["p_sys"])
    st = wait(world.client)
    assert st["run"]["state"] == "failed" and st["run"]["error"]["code"] == "build_failed"
    assert "keep_me" in json.loads((old / "catalogue.json").read_text(encoding="utf-8"))["styles"]
    assert not (world.lib / "hairlib.new").exists() or not (world.lib / "hairlib.new" / "catalogue.json").exists()


def test_a_library_that_fails_its_check_is_not_swapped_in(world, monkeypatch):
    monkeypatch.setattr(hairlib_builder, "verify", lambda d: ({}, ["q_buns: styles/q_buns.npz is missing"]))
    serve(monkeypatch, {"http://fake/sys.zip": SYS_ZIP})
    start(world.client, ["p_sys"])
    st = wait(world.client)
    assert st["run"]["state"] == "failed" and st["run"]["error"]["code"] == "verify"
    assert not (world.lib / "hairlib").exists()


def test_a_zip_that_writes_outside_its_folder_is_refused(world, monkeypatch):
    serve(monkeypatch, {"http://fake/sys.zip": EVIL_ZIP})
    start(world.client, ["p_sys"])
    st = wait(world.client)
    assert st["run"]["state"] == "failed" and st["run"]["error"]["code"] == "unsafe"
    assert not (world.project / "runtime" / "hairpacks" / "evil.txt").exists() and not (world.project / "evil.txt").exists()


def test_cannot_start_twice(world, monkeypatch):
    gate = {"open": False}

    def hook():
        while not gate["open"]:
            time.sleep(0.01)
    serve(monkeypatch, {"http://fake/sys.zip": SYS_ZIP}, None, hook)
    start(world.client, ["p_sys"])
    r = world.client.post("/api/packs/hair/start", json={"choices": ["p_sys"]})
    assert r.status_code == 409
    gate["open"] = True
    assert wait(world.client)["run"]["state"] == "done"


# --- already installed, and removing the downloads --------------------------------------------------------------------------


def test_status_when_the_library_is_already_built(world):
    lib = world.lib / "hairlib"
    lib.mkdir()
    entry = {"id": "fake_a", "name": "Fake A", "pack": "Quaternius pack", "author": "Q", "family": "short", "kind": "shell", "triangles": 1}
    (lib / "catalogue.json").write_text(json.dumps({"styles": {"fake_a": entry}, "version": 1}), encoding="utf-8")
    st = world.client.get("/api/packs/hair").json()
    assert st["installed"] is True and st["where"] == "built"
    assert [c["id"] for c in st["choices"] if c["built"]] == ["p_qua"]


def test_remove_the_downloads_frees_space_and_keeps_the_library(world, monkeypatch):
    serve(monkeypatch, {"http://fake/sys.zip": SYS_ZIP})
    start(world.client, ["p_sys"])
    st = wait(world.client)
    assert st["run"]["state"] == "done" and st["cleanable_bytes"] > 0
    out = world.client.post("/api/packs/hair/clean").json()
    assert out["freed_bytes"] > 0 and out["cleanable_bytes"] == 0
    assert not (world.project / "runtime" / "hairpacks" / "sysx").exists()
    assert (world.lib / "hairlib" / "catalogue.json").is_file()
    assert out["installed"] is True


def test_unknown_kit_is_404(world):
    assert world.client.get("/api/packs/nothing").status_code == 404


# --- the setup card item -----------------------------------------------------------------------------------------------------


def test_setup_item_is_an_offer_not_a_fault(world, monkeypatch):
    empty = world.tmp / "empty"
    empty.mkdir()
    monkeypatch.setattr(hairstyles, "DATA_DIR", empty)
    monkeypatch.setattr(my_hair, "entries", lambda settings=None: {})
    hairstyles.use_dir(None)
    it = preflight.check_hair_library(SimpleNamespace())
    assert it["state"] == "optional" and it["offer"] == {"kit": "hair", "button": "Get the hair library"}
    assert preflight.summarize([it])["ok"] is True and preflight.summarize([it])["fails"] == 0 and preflight.summarize([it])["warns"] == 0
    lib = world.lib / "hairlib"
    lib.mkdir()
    (lib / "catalogue.json").write_text(json.dumps({"styles": {"fake_a": {"id": "fake_a", "name": "A", "pack": "p", "author": "a", "family": "short", "kind": "shell", "triangles": 1}}, "version": 1}), encoding="utf-8")
    hairstyles.use_dir(None)
    it = preflight.check_hair_library(SimpleNamespace())
    assert it["state"] == "ok" and "offer" not in it
