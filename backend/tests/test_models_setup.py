"""The models panel (models_setup.py): sets, files already there (exact paths only), the background check, licence gates,
space, and the shipped list. A tiny fake list and a fake host; no real model, no real ComfyUI."""

from __future__ import annotations

import hashlib
import io
import json
import re
from pathlib import Path

import pytest

from vgac import downloader, models_setup

REAL_LIST = Path(__file__).resolve().parents[2] / "setup" / "downloads.json"

A = b"a" * 100
B = b"b" * 200
C = b"c" * 300
D = b"d" * 50
GEM = b"g" * 400
PHRASE = "I accept the Gemma terms myself"


def entry(data: bytes, dest: str, save_as: str, **kw):
    return dict(url=f"http://fake/{save_as}", page="http://page", size=len(data), sha256=hashlib.sha256(data).hexdigest(), dest=dest, save_as=save_as, publisher=True, **kw)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    models = tmp_path / "ComfyUI" / "models"
    models.mkdir(parents=True)
    (tmp_path / "setup").mkdir()
    lst = {
        "models": {"a": entry(A, "checkpoints", "a.safetensors"), "b": entry(B, "diffusion_models/Sub", "b.safetensors"),
                   "c": entry(C, "vae", "c.safetensors"), "g": entry(GEM, "text_encoders", "g.safetensors", optin="gemma")},
        "addons": {"d": {**entry(D, "loras/X/d.safetensors", "d.safetensors")}},
        "project_files": {},
        "sets": [{"id": "s1", "name": "Set one", "required": True, "items": ["model:a", "model:b", "model:c", "addon:d"]},
                 {"id": "s2", "name": "Judge", "gate": "gemma", "items": ["model:g"]}],
        "gates": {"gemma": {"phrase": PHRASE, "notice": ["Read the terms."]}},
    }
    (tmp_path / "setup" / "downloads.json").write_text(json.dumps(lst), encoding="utf-8")
    monkeypatch.setattr(downloader, "project_dir", lambda: tmp_path)
    monkeypatch.setattr(downloader, "models_root", lambda: models)
    monkeypatch.setattr(downloader, "free_bytes", lambda p: 10**13)
    monkeypatch.setattr(models_setup, "engine_extra_paths", lambda: [])
    models_setup.VERIFIED.clear()
    return tmp_path, models


class Net:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.log: list[str] = []

    def __call__(self, url: str, start: int):
        self.log.append(url)
        data = self.files[url.rsplit("/", 1)[1]]
        r = io.BytesIO(data[start:])
        r.status = 206 if start else 200
        r.headers = {"Content-Length": str(len(data) - start)}
        return r


ALL = {"a.safetensors": A, "b.safetensors": B, "c.safetensors": C, "d.safetensors": D, "g.safetensors": GEM}


def states(models: Path | None = None):
    st = models_setup.set_status(models or downloader.models_root())
    return {i["id"]: i for s in st["sets"] for i in s["items"]}, st


def test_nothing_there_means_everything_is_missing_and_sized(world):
    _, models = world
    items, st = states()
    assert {i["state"] for i in items.values()} == {"missing"}
    s1 = st["sets"][0]
    assert s1["total"] == len(A) + len(B) + len(C) + len(D) and s1["remaining"] == s1["total"] and not s1["complete"]
    assert s1["need"] == int(s1["total"] * 1.1) + 20 * downloader.GB  # the set, ten percent, and 20 GB of working space


def test_a_file_in_its_own_place_with_the_right_size_is_present(world):
    _, models = world
    (models / "checkpoints").mkdir()
    (models / "checkpoints" / "a.safetensors").write_bytes(A)
    items, _ = states()
    assert items["model:a"]["state"] == "present" and not items["model:a"]["reused"]


def test_files_in_the_folders_comfyui_reads_are_used_where_they_are(world, tmp_path, monkeypatch):
    _, models = world
    shared = tmp_path / "Shared"
    (shared / "diffusion_models" / "Sub").mkdir(parents=True)
    (shared / "diffusion_models" / "Sub" / "b.safetensors").write_bytes(B)
    (shared / "clip").mkdir(parents=True)
    (shared / "clip" / "g.safetensors").write_bytes(GEM)  # the alternate folder name for text_encoders
    (shared / "vae").mkdir()
    (shared / "vae" / "c.safetensors").write_bytes(C)
    yml = tmp_path / "extra.yaml"
    yml.write_text(f"mine:\n  base_path: '{shared.as_posix()}'\n  vae: 'vae/'\n", encoding="utf-8")
    monkeypatch.setattr(models_setup, "engine_extra_paths", lambda: [yml])
    items, st = states()
    assert items["model:b"]["state"] == "present" and items["model:b"]["reused"] and items["model:b"]["path"].startswith(str(shared))
    assert items["model:c"]["state"] == "present" and items["model:c"]["reused"]
    assert items["model:g"]["state"] == "present"  # found as clip/..., never listed, only asked for by exact path
    assert items["model:a"]["state"] == "missing"
    assert str(shared) in st["reads"]


def test_a_folder_comfyui_does_not_read_is_not_used(world, tmp_path):
    other = tmp_path / "Other" / "diffusion_models" / "Sub"
    other.mkdir(parents=True)
    (other / "b.safetensors").write_bytes(B)
    items, _ = states()
    assert items["model:b"]["state"] == "missing"


def test_the_right_name_with_another_size_is_differs_and_never_overwritten(world):
    _, models = world
    (models / "checkpoints").mkdir()
    f = models / "checkpoints" / "a.safetensors"
    f.write_bytes(A + b"x")
    items, _ = states()
    assert items["model:a"]["state"] == "differs" and items["model:a"]["check"] == "size"
    net = Net(ALL)
    job = models_setup.start_set("s1", {}, net, wait=True)
    assert job.state == "done" and f.read_bytes() == A + b"x"  # left alone
    assert not any(u.endswith("a.safetensors") for u in net.log)


def test_the_background_check_flags_a_same_size_file_that_is_not_the_published_one(world):
    _, models = world
    (models / "checkpoints").mkdir()
    f = models / "checkpoints" / "a.safetensors"
    f.write_bytes(b"z" * len(A))
    items, _ = states()
    assert items["model:a"]["state"] == "present" and items["model:a"]["check"] == "unchecked"
    pairs = [(s, f) for s in downloader.specs_for(models) if s.id == "model:a"]
    assert models_setup.verify_all(pairs, wait=True)["total"] == 1
    items, _ = states()
    assert items["model:a"]["state"] == "differs" and items["model:a"]["check"] == "differs"
    models_setup.accept_path(f)  # "Use it anyway"
    items, _ = states()
    assert items["model:a"]["state"] == "present" and items["model:a"]["accepted"]


def test_a_good_file_checks_out_and_is_not_checked_twice(world):
    _, models = world
    (models / "checkpoints").mkdir()
    f = models / "checkpoints" / "a.safetensors"
    f.write_bytes(A)
    pairs = [(s, f) for s in downloader.specs_for(models) if s.id == "model:a"]
    models_setup.verify_all(pairs, wait=True)
    items, _ = states()
    assert items["model:a"]["check"] == "ok"
    assert models_setup.verify_all(pairs, wait=True)["total"] == 0


def test_getting_a_set_downloads_only_what_is_missing_and_never_what_is_already_there(world):
    _, models = world
    (models / "vae").mkdir()
    (models / "vae" / "c.safetensors").write_bytes(C)
    net = Net(ALL)
    job = models_setup.start_set("s1", {}, net, wait=True)
    assert job.state == "done", job.error
    assert sorted(u.rsplit("/", 1)[1] for u in net.log) == ["a.safetensors", "b.safetensors", "d.safetensors"]
    assert (models / "diffusion_models" / "Sub" / "b.safetensors").read_bytes() == B
    assert (models / "loras" / "X" / "d.safetensors").read_bytes() == D
    _, st = states(), models_setup.set_status(models)
    assert st["sets"][0]["complete"]


def test_a_gated_file_needs_the_exact_sentence_typed(world):
    _, models = world
    net = Net(ALL)
    for typed in ({}, {"gemma": "i accept the gemma terms myself"}, {"gemma": PHRASE + "."}, {"anima": PHRASE}):
        with pytest.raises(downloader.DownloadError) as e:
            models_setup.start_set("s2", typed, net, wait=True)
        assert e.value.code == "licence" and PHRASE in e.value.fix
    assert net.log == []
    job = models_setup.start_set("s2", {"gemma": f"  {PHRASE}  "}, net, wait=True)
    assert job.state == "done" and (models / "text_encoders" / "g.safetensors").read_bytes() == GEM


def test_not_enough_space_is_found_before_anything_starts(world, monkeypatch):
    _, models = world
    monkeypatch.setattr(downloader, "free_bytes", lambda p: 1000)
    net = Net(ALL)
    with pytest.raises(downloader.DownloadError) as e:
        models_setup.start_set("s1", {}, net, wait=True)
    assert e.value.code == "no_space" and net.log == []


def test_without_a_linked_comfyui_nothing_is_started(world, monkeypatch):
    monkeypatch.setattr(downloader, "models_root", lambda: None)
    with pytest.raises(downloader.DownloadError) as e:
        models_setup.start_set("s1", {}, Net(ALL), wait=True)
    assert e.value.code == "no_comfy" and "Link your ComfyUI" in e.value.fix
    st = models_setup.set_status(None)
    assert st["models_folder"] == "" and all(i["state"] == "missing" for s in st["sets"] for i in s["items"])


def test_a_stopped_set_keeps_its_part_and_carries_on(world):
    _, models = world
    net = Net(ALL)
    job = models_setup.start_set("s1", {}, net)
    downloader.stop_job("set-s1")
    job.thread.join(10)
    again = models_setup.start_set("s1", {}, net, wait=True)
    assert again.state == "done" and (models / "checkpoints" / "a.safetensors").read_bytes() == A


def test_the_test_character_record_survives_a_restart(world):
    assert models_setup.test_character_state() == {}
    models_setup.record_test_character("asset-1", None)
    assert models_setup.test_character_state() == {"asset_id": "asset-1", "passed": None}
    models_setup.record_test_character("asset-1", True)
    assert models_setup.test_character_state()["passed"] is True


def test_http_status_and_gate_refusal(world):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(models_setup.router)
    c = TestClient(app)
    r = c.get("/api/model-sets").json()
    assert [s["id"] for s in r["sets"]] == ["s1", "s2"] and r["gates"]["gemma"]["phrase"] == PHRASE
    bad = c.post("/api/model-sets/s2/start", json={"phrases": {}})
    assert bad.status_code == 400 and "accepted first" in bad.json()["detail"]
    assert c.post("/api/model-sets/test_character", json={"asset_id": "x", "passed": True}).json()["passed"] is True


# --- the shipped list ----------------------------------------------------------------------------------------------------


def test_the_shipped_sets_cover_every_file_once_and_gates_are_consistent():
    d = json.loads(REAL_LIST.read_text(encoding="utf-8-sig"))
    ids = {f"{k}:{n}" for sec, k in (("models", "model"), ("addons", "addon"), ("project_files", "project_file")) for n in d[sec]}
    seen: list[str] = []
    for s in d["sets"]:
        seen += s["items"]
        for i in s["items"]:
            sec, _, n = i.partition(":")
            section = {"model": "models", "addon": "addons", "project_file": "project_files"}[sec]
            gate = d[section][n].get("optin")
            if gate:
                assert s.get("gate") == gate and gate in d["gates"], i
    assert sorted(seen) == sorted(ids)  # every file is in exactly one set
    for g in d["gates"].values():
        assert g["phrase"] and g["notice"]
    starter = next(s for s in d["sets"] if s["id"] == "starter")
    assert starter["required"]
    total = sum(d[{"model": "models", "addon": "addons", "project_file": "project_files"}[i.split(":")[0]]][i.split(":")[1]]["size"] for i in starter["items"])
    assert 55e9 < total < 58e9  # "about 56 GB": the Z-Image diffusion file, text encoder and VAE are three files now
    required = {f"model:{k}" for k, v in d["models"].items() if v.get("required")} | {f"project_file:{k}" for k, v in d["project_files"].items() if v.get("required")}
    assert required <= set(starter["items"])
    assert re.fullmatch(r"[0-9a-f]{64}", next(iter(d["models"].values()))["sha256"])


# --- a file that does not live under a ComfyUI models folder (the real list's landmark files) ----------------------------

def _app():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(models_setup.router)
    return TestClient(app)


def _with_project_file(tmp: Path, data: bytes):
    lst = json.loads((tmp / "setup" / "downloads.json").read_text(encoding="utf-8"))
    lst["project_files"] = {"lm": {**entry(data, "runtime/models", "lm.task"), "required": True}}
    lst["sets"][0]["items"].append("project_file:lm")
    (tmp / "setup" / "downloads.json").write_text(json.dumps(lst), encoding="utf-8")


def test_a_file_outside_the_comfyui_models_folders_is_looked_for_at_its_own_path(world):
    """The exact layout that broke the live app: dest under the project's runtime/models, ComfyUI's models folder elsewhere
    (GET /api/model-sets answered 500 with ValueError from relative_to)."""
    tmp, models = world
    data = b"landmark" * 5
    _with_project_file(tmp, data)
    r = _app().get("/api/model-sets")
    assert r.status_code == 200
    item = next(i for s in r.json()["sets"] for i in s["items"] if i["id"] == "project_file:lm")
    assert item["state"] == "missing" and item["path"] == str(tmp / "runtime" / "models" / "lm.task")
    (tmp / "runtime" / "models").mkdir(parents=True)
    (tmp / "runtime" / "models" / "lm.task").write_bytes(data)
    item = next(i for s in _app().get("/api/model-sets").json()["sets"] for i in s["items"] if i["id"] == "project_file:lm")
    assert item["state"] == "present" and not item["reused"]
    # another ComfyUI folder is never searched for it
    stray = models / "runtime" / "models"
    stray.mkdir(parents=True)
    (stray / "lm.task").write_bytes(b"x" * len(data))
    assert models_setup.candidates(next(s for s in downloader.specs_for(models) if s.id == "project_file:lm"), models, models_setup.read_roots(models)) == [tmp / "runtime" / "models" / "lm.task"]


def test_one_entry_that_cannot_be_looked_at_never_takes_the_whole_panel_down(world, monkeypatch):
    tmp, models = world
    real = models_setup.candidates

    def boom(spec, root, roots):
        if spec.id == "model:b":
            raise RuntimeError("something odd about this entry")
        return real(spec, root, roots)

    monkeypatch.setattr(models_setup, "candidates", boom)
    r = _app().get("/api/model-sets")
    assert r.status_code == 200
    items = {i["id"]: i for s in r.json()["sets"] for i in s["items"]}
    assert items["model:b"]["state"] == "missing" and "odd" in items["model:b"]["error"]
    assert items["model:a"]["state"] == "missing" and "error" not in items["model:a"]  # the others are looked at as usual


def test_the_default_picture_route_files_are_in_the_starter_set_and_the_old_community_files_are_gone():
    d = json.loads(REAL_LIST.read_text(encoding="utf-8"))
    starter = next(s for s in d["sets"] if s["id"] == "starter")["items"]
    assert {"model:zturbo", "model:zimage_te", "model:flux_ae"} <= set(starter)
    for k in ("zturbo", "zbase"):
        e = d["models"][k]
        assert e["publisher"] is True and "/Comfy-Org/" in e["url"] and e["size"] == 6201001296 and e["dest"] == "diffusion_models/Z-Image"
    text = REAL_LIST.read_text(encoding="utf-8")
    assert "Elena2810" not in text and "Winnougan" not in text
    # the zbase file has a name of its own: the earlier community file z_image_int8_convrot.safetensors (a different file) never meets it
    assert d["models"]["zbase"]["save_as"] != "z_image_int8_convrot.safetensors"


def test_old_z_image_files_in_a_models_folder_are_ignored_not_reported_as_wrong(world, monkeypatch):
    """Someone who has the earlier fp8 all-in-one and the community int8 file: neither is looked for, neither is called
    'differs', and nothing tells them to delete them; the new files simply show as missing."""
    tmp, models = world
    real = json.loads(REAL_LIST.read_text(encoding="utf-8"))
    keep = ("zturbo", "zbase", "zimage_te", "flux_ae")
    lst = {"models": {k: real["models"][k] for k in keep}, "addons": {}, "project_files": {},
           "sets": [{"id": "starter", "name": "Starter", "required": True, "items": [f"model:{k}" for k in keep]}], "gates": {}}
    (tmp / "setup" / "downloads.json").write_text(json.dumps(lst), encoding="utf-8")
    (models / "checkpoints" / "Z-Image").mkdir(parents=True)
    (models / "checkpoints" / "Z-Image" / "z-image-turbo-fp8-aio.safetensors").write_bytes(b"old" * 10)
    (models / "diffusion_models" / "Z-Image").mkdir(parents=True)
    (models / "diffusion_models" / "Z-Image" / "z_image_int8_convrot.safetensors").write_bytes(b"old" * 10)
    items, _ = states(models)
    assert items["model:zturbo"]["state"] == "missing" and items["model:zbase"]["state"] == "missing"
    assert not [i for i in items.values() if i["state"] == "differs"]
