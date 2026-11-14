"""The models panel's "I already have this" (model_choices.py): pointing the app at a file the person already has.

Tiny fake files with a fake pin; no real model, no engine. The file the person chooses lies in a folder the engine reads (here
an extra model path); its hash is read in the background and decides how it is shown."""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vgac import downloader, model_choices, models_setup

BASE_NAME = "z_image_base_int8_convrot.safetensors"


def st_file(n: int, fill: bytes = b"x") -> bytes:
    """A file of n bytes that starts like a safetensors file (8-byte header length, then a JSON header)."""
    return struct.pack("<Q", 2) + b"{}" + fill * (n - 10)


PIN = st_file(64)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    models = tmp_path / "ComfyUI" / "models"
    models.mkdir(parents=True)
    shared = tmp_path / "Shared" / "models"  # a folder the engine reads through its extra model paths
    (shared / "diffusion_models" / "Z-Image").mkdir(parents=True)
    ent = {"url": "http://fake/z", "page": "http://p", "size": len(PIN), "sha256": hashlib.sha256(PIN).hexdigest(), "dest": "diffusion_models/Z-Image",
           "save_as": BASE_NAME, "publisher": True, "older": ["diffusion_models/Z-Image/z_image_int8_convrot.safetensors", "checkpoints/Z-Image/aio.safetensors"]}
    (tmp_path / "setup").mkdir()
    (tmp_path / "setup" / "downloads.json").write_text(json.dumps({
        "models": {"zbase": ent, "other": {**ent, "save_as": "o.safetensors", "older": []}}, "addons": {}, "project_files": {},
        "sets": [{"id": "s", "name": "S", "items": ["model:zbase", "model:other"]}], "gates": {}}), encoding="utf-8")
    yml = tmp_path / "extra.yaml"
    yml.write_text(f"shared:\n  base_path: '{shared}'\n  diffusion_models: 'diffusion_models/'\n", encoding="utf-8")
    monkeypatch.setattr(downloader, "project_dir", lambda: tmp_path)
    monkeypatch.setattr(downloader, "models_root", lambda: models)
    monkeypatch.setattr(downloader, "free_bytes", lambda p: 10**13)
    monkeypatch.setattr(models_setup, "engine_extra_paths", lambda: [yml])
    models_setup.VERIFIED.clear()
    models_setup._verify_state.update(state="idle", done=0, total=0, current="")
    return tmp_path, models, shared / "diffusion_models" / "Z-Image"


def client() -> TestClient:
    app = FastAPI()
    app.include_router(models_setup.router)
    return TestClient(app)


def item(spec_id="model:zbase"):
    st = models_setup.set_status(downloader.models_root())
    return next(i for s in st["sets"] for i in s["items"] if i["id"] == spec_id)


def settle():
    """Wait for the background hash check to finish."""
    import time

    for _ in range(200):
        if models_setup._verify_state["state"] != "working":
            return
        time.sleep(0.02)


# --- refusals, with a reason ------------------------------------------------------------------------------------------------------


def test_a_wrong_kind_of_file_is_refused_with_a_reason(world):
    tmp, models, folder = world
    c = client()
    (folder / "notes.txt").write_text("hello", encoding="utf-8")
    r = c.post("/api/model-sets/choose", json={"id": "model:zbase", "path": str(folder / "notes.txt")})
    assert r.status_code == 400 and "does not look like a model file" in r.json()["detail"]
    (folder / "bad.safetensors").write_bytes(b"n" * len(PIN))  # the right size, but not a model
    r = c.post("/api/model-sets/choose", json={"id": "model:zbase", "path": str(folder / "bad.safetensors")})
    assert r.status_code == 400 and "safetensors layout" in r.json()["detail"]
    assert not model_choices.load()  # nothing was kept


def test_a_size_far_off_is_a_warning_not_a_refusal_and_is_used_only_after_the_person_says_so(world):
    tmp, models, folder = world
    (folder / "fp8.safetensors").write_bytes(st_file(len(PIN) * 2))  # twice the size: another precision of the model
    r = client().post("/api/model-sets/choose", json={"id": "model:zbase", "path": str(folder / "fp8.safetensors")})
    assert r.status_code == 200
    assert "not the file the app was tested with" in r.json()["warning"] and BASE_NAME in r.json()["warning"]
    it = item()
    assert it["state"] == "differs" and it["yours"] and it["check"] == "size"
    client().post("/api/model-sets/accept", json={"id": "model:zbase"})
    assert item()["state"] == "present" and item()["yours"]


def st_with_keys(keys: list[str], n: int = 64) -> bytes:
    hdr = json.dumps({k: {"dtype": "F16", "shape": [1], "data_offsets": [0, 0]} for k in keys}).encode()
    return struct.pack("<Q", len(hdr)) + hdr + b"z" * n


def test_a_file_of_a_kind_the_entry_cannot_load_is_refused_in_plain_words(world):
    tmp, models, folder = world
    (folder / "whole.safetensors").write_bytes(st_with_keys(["model.diffusion_model.a", "first_stage_model.decoder.b", "cond_stage_model.c"]))
    r = client().post("/api/model-sets/choose", json={"id": "model:zbase", "path": str(folder / "whole.safetensors")})
    assert r.status_code == 400 and "all-in-one checkpoint" in r.json()["detail"] and "separate diffusion model" in r.json()["detail"]
    (folder / "lora.safetensors").write_bytes(st_with_keys(["lora_unet_a.lora_down.weight", "lora_unet_a.lora_up.weight"]))
    r = client().post("/api/model-sets/choose", json={"id": "model:zbase", "path": str(folder / "lora.safetensors")})
    assert r.status_code == 400 and "LoRA" in r.json()["detail"]
    # a diffusion-only file with other tensor names is allowed (warned if it differs), and a gguf is not read further
    (folder / "unet.safetensors").write_bytes(st_with_keys(["blocks.0.attn.weight"]))
    assert client().post("/api/model-sets/choose", json={"id": "model:zbase", "path": str(folder / "unet.safetensors")}).status_code == 200
    (folder / "x.gguf").write_bytes(b"GGUF" + b"0" * 60)
    assert client().post("/api/model-sets/choose", json={"id": "model:zbase", "path": str(folder / "x.gguf")}).status_code == 200


def test_the_kind_of_a_file_is_read_from_its_tensor_names():
    assert model_choices.kind_of(["model.diffusion_model.x", "first_stage_model.y"]) == "checkpoint"
    assert model_choices.kind_of(["a.lora_A.weight", "a.lora_B.weight"]) == "lora"
    assert model_choices.kind_of(["model.diffusion_model.x"]) == "other"  # a diffusion model alone
    assert model_choices.kind_of(["encoder.conv_in.weight", "decoder.conv_out.weight"]) == "other"


def test_a_file_in_a_folder_the_engine_does_not_read_is_refused_plainly(world):
    tmp, models, folder = world
    elsewhere = tmp / "Downloads" / "mine.safetensors"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(PIN)
    r = client().post("/api/model-sets/choose", json={"id": "model:zbase", "path": str(elsewhere)})
    assert r.status_code == 400 and "does not read that folder" in r.json()["detail"] and "does not change your ComfyUI settings" in r.json()["detail"]
    assert client().post("/api/model-sets/choose", json={"id": "model:nope", "path": str(elsewhere)}).status_code == 404


# --- the publisher's file under another name ------------------------------------------------------------------------------------------


def test_the_same_file_under_another_name_is_shown_as_the_publishers_once_its_hash_matches_and_the_graphs_name_it(world):
    tmp, models, folder = world
    mine = folder / "my_old_name.safetensors"
    mine.write_bytes(PIN)
    r = client().post("/api/model-sets/choose", json={"id": "model:zbase", "path": str(mine)})
    assert r.status_code == 200
    settle()
    it = item()
    assert it["state"] == "present" and it["chosen"] and not it["yours"] and it["path"] == str(mine) and it["check"] == "ok"
    table = models_setup.current_alias_table()
    assert table[BASE_NAME.casefold()][0] == ("UNETLoader", "unet_name", "my_old_name.safetensors", str(mine))
    assert models_setup.row_info(BASE_NAME)["in_use"] == {"kind": "renamed", "path": str(mine)}  # the System page row says the same
    graph = {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": "Z-Image\\" + BASE_NAME, "weight_dtype": "default"}}}
    lists = {("UNETLoader", "unet_name"): ["Z-Image\\my_old_name.safetensors", "Z-Image\\other.safetensors"]}
    new, notes = model_choices.apply_aliases(graph, table, lists)
    assert new["1"]["inputs"]["unet_name"] == "Z-Image\\my_old_name.safetensors" and notes
    assert graph["1"]["inputs"]["unet_name"] == "Z-Image\\" + BASE_NAME  # never changed in place


def test_while_the_hash_is_being_read_the_file_is_checking_and_not_yet_named_in_graphs(world):
    tmp, models, folder = world
    mine = folder / "mine.safetensors"
    mine.write_bytes(PIN)
    model_choices.choose(next(s for s in downloader.specs_for(models) if s.id == "model:zbase"), str(mine), models, models_setup.read_roots(models))
    it = item()
    assert it["state"] == "checking" and it["chosen"]
    assert BASE_NAME.casefold() in models_setup.current_alias_table() and all(a[2] != "mine.safetensors" for a in models_setup.current_alias_table()[BASE_NAME.casefold()])


def test_a_file_the_person_chose_wins_even_when_the_publishers_is_there_and_a_failure_says_so(world):
    tmp, models, folder = world
    mine = folder / "mine.safetensors"
    mine.write_bytes(PIN)
    client().post("/api/model-sets/choose", json={"id": "model:zbase", "path": str(mine)})
    settle()
    table = models_setup.current_alias_table()
    graph = {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": "Z-Image\\" + BASE_NAME}}}
    lists = {("UNETLoader", "unet_name"): ["Z-Image\\" + BASE_NAME, "Z-Image\\mine.safetensors"]}
    used = model_choices.start_tracking()
    new, notes = model_choices.apply_aliases(graph, table, lists)
    assert new["1"]["inputs"]["unet_name"] == "Z-Image\\mine.safetensors" and "your own file" in notes[0]
    msg = model_choices.used_message(used)
    assert used == {BASE_NAME: str(mine)} and "This step used your own file" in msg and "mine.safetensors" in msg and "own file again" in msg
    # the file is not listed by the engine (not where it reads, or not refreshed): the publisher's stays
    same, _ = model_choices.apply_aliases(graph, table, {("UNETLoader", "unet_name"): ["Z-Image\\" + BASE_NAME]})
    assert same is graph
    # a job that used no file of the person's own says nothing extra
    assert model_choices.used_message(model_choices.start_tracking()) == ""


# --- a file that is not the published one -------------------------------------------------------------------------------------------------


def test_a_different_file_is_shown_as_your_own_and_used_only_after_the_person_says_so(world):
    tmp, models, folder = world
    mine = folder / "tuned.safetensors"
    mine.write_bytes(st_file(len(PIN) - 4))  # close in size, different content
    assert client().post("/api/model-sets/choose", json={"id": "model:zbase", "path": str(mine)}).status_code == 200
    settle()
    it = item()
    assert it["state"] == "differs" and it["yours"] and it["chosen"]
    assert all(a[2] != "tuned.safetensors" for a in models_setup.current_alias_table().get(BASE_NAME.casefold(), []))  # never silently
    assert client().post("/api/model-sets/accept", json={"id": "model:zbase"}).status_code == 200
    it = item()
    assert it["state"] == "present" and it["yours"] and it["accepted"]
    assert models_setup.current_alias_table()[BASE_NAME.casefold()][0][2] == "tuned.safetensors"


def test_an_exact_size_with_a_different_hash_is_also_your_own_file(world):
    tmp, models, folder = world
    mine = folder / "same_size.safetensors"
    mine.write_bytes(st_file(len(PIN), fill=b"y"))
    client().post("/api/model-sets/choose", json={"id": "model:zbase", "path": str(mine)})
    settle()
    it = item()
    assert it["state"] == "differs" and it["check"] == "differs" and it["yours"]


# --- undoing, the list's own earlier files, and what the choice never touches ----------------------------------------------------------------


def test_stop_using_my_file_removes_the_choice_and_leaves_the_file(world):
    tmp, models, folder = world
    mine = folder / "mine.safetensors"
    mine.write_bytes(PIN)
    client().post("/api/model-sets/choose", json={"id": "model:zbase", "path": str(mine)})
    settle()
    assert client().post("/api/model-sets/unchoose", json={"id": "model:zbase"}).json() == {"removed": True}
    assert mine.read_bytes() == PIN and item()["state"] == "missing" and "chosen" not in item()
    assert client().post("/api/model-sets/unchoose", json={"id": "model:zbase"}).json() == {"removed": False}


def test_a_chosen_file_that_has_gone_falls_back_to_the_ordinary_search(world):
    tmp, models, folder = world
    mine = folder / "mine.safetensors"
    mine.write_bytes(PIN)
    client().post("/api/model-sets/choose", json={"id": "model:zbase", "path": str(mine)})
    settle()
    mine.unlink()
    assert item()["state"] == "missing"
    assert BASE_NAME.casefold() not in models_setup.current_alias_table() or all(a[2] != "mine.safetensors" for a in models_setup.current_alias_table()[BASE_NAME.casefold()])


def test_only_earlier_files_the_same_loader_can_load_are_in_the_table(world):
    table = model_choices.alias_table(lambda p: False)
    assert table[BASE_NAME.casefold()] == [("UNETLoader", "unet_name", "z_image_int8_convrot.safetensors", "")]  # the checkpoint is a different layout
    assert "o.safetensors" not in table


def test_the_choice_file_lives_in_local_never_in_the_repo_list(world):
    tmp, models, folder = world
    mine = folder / "mine.safetensors"
    mine.write_bytes(PIN)
    client().post("/api/model-sets/choose", json={"id": "model:zbase", "path": str(mine)})
    assert model_choices.choices_file() == tmp / "local" / "model_choices.json" and model_choices.load()["model:zbase"]["path"] == str(mine)
    assert "mine.safetensors" not in (tmp / "setup" / "downloads.json").read_text(encoding="utf-8")


def test_entries_the_app_cannot_swap_names_for_offer_no_button(world):
    st = models_setup.set_status(downloader.models_root())
    flags = {i["id"]: i["can_choose"] for s in st["sets"] for i in s["items"]}
    assert flags == {"model:zbase": True, "model:other": False}  # only entries a loader of the graphs is known for


# --- an earlier copy that stands in: never "to get", and the publisher's file under another name is not missing -----------------------------


def put_old(folder: Path, data: bytes) -> Path:
    old = folder / "z_image_int8_convrot.safetensors"  # the earlier file the list names for this entry
    old.write_bytes(data)
    return old


def setrow(spec_id="model:zbase"):
    st = models_setup.set_status(downloader.models_root())
    s = st["sets"][0]
    return next(i for i in s["items"] if i["id"] == spec_id), s


def test_an_earlier_copy_that_is_the_publishers_file_under_another_name_is_not_missing(world):
    tmp, models, folder = world
    old = put_old(folder, PIN)
    first, _ = setrow()  # the hash is not known yet: being read in the background
    assert first["state"] == "checking" and first["older"] == str(old) and first["covered"]
    settle()
    it, s = setrow()
    assert it["state"] == "present" and it["renamed"] and it["path"] == str(old) and it["check"] == "ok" and not it["covered"]
    # the answer is kept: after a restart (nothing in memory) it is known at once
    models_setup.VERIFIED.clear()
    models_setup._loaded_from[0] = None
    again, _ = setrow()
    assert again["state"] == "present" and again["renamed"]
    assert (tmp / "local" / "models_verified.json").is_file()


def test_an_earlier_copy_that_is_a_different_file_stands_in_and_is_optional_not_to_get(world):
    tmp, models, folder = world
    put_old(folder, st_file(len(PIN), fill=b"q"))  # same size, other content
    settle()
    setrow()
    settle()
    it, s = setrow()
    assert it["state"] == "missing" and it["covered"] and it["older"]
    other = next(i for i in s["items"] if i["id"] == "model:other")
    assert s["remaining"] == other["size"] and s["covered"] == 1 and s["covered_size"] == it["size"]  # only the really missing file is "to get"
    assert not s["complete"]  # "other" is still missing
    # take "other" away from the set: now nothing is to get and the set is not unfinished
    lst = json.loads((tmp / "setup" / "downloads.json").read_text(encoding="utf-8"))
    lst["sets"][0]["items"] = ["model:zbase"]
    (tmp / "setup" / "downloads.json").write_text(json.dumps(lst), encoding="utf-8")
    _, s = setrow()
    assert s["remaining"] == 0 and s["need"] == 0 and s["complete"]


def test_getting_a_set_skips_the_covered_file_unless_it_is_asked_for(world, monkeypatch):
    tmp, models, folder = world
    put_old(folder, st_file(len(PIN) - 3))
    taken = []
    monkeypatch.setattr(downloader, "start_job", lambda name, todo, accepted, opener, wait: taken.append([s.id for s in todo]) or "job")
    models_setup.start_set("s", {})
    assert taken[-1] == ["model:other"]
    models_setup.start_set("s", {}, only=["model:zbase"])
    assert taken[-1] == ["model:zbase"]
    models_setup.start_set("s", {}, only=["model:other"])
    assert taken[-1] == ["model:other"]
