"""The person's LoRA folders (lora_folders.py): add, remove and validation; the app's model folders file with several folders and
the other entries kept; the restart note per card; a LoRA in a linked folder listed and used in place; and no copy or hard link
of a LoRA anywhere in the code."""

from __future__ import annotations

import json
import os
import struct
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

LORA_KEY = "diffusion_model.layers.0.attention.to_k.lora_A.weight"
VGAC_ROOT = Path(__file__).resolve().parents[1] / "vgac"


def lora_bytes() -> bytes:
    header: dict[str, Any] = {}
    off = 0
    for k in (LORA_KEY, LORA_KEY.replace("lora_A", "lora_B")):
        header[k] = {"dtype": "F16", "shape": [4, 8], "data_offsets": [off, off + 64]}
        off += 64
    raw = json.dumps(header).encode("utf-8")
    raw += b" " * (-len(raw) % 8)
    return struct.pack("<Q", len(raw)) + raw + b"\x01" * off


class Box:
    """A temporary world: a machine file, an app install folder, and three folders of files on this computer."""

    def __init__(self, root: Path):
        self.root = root
        self.machine = root / "machine.json"
        self.machine.write_text(json.dumps({"backend": {}}), encoding="utf-8")
        self.install = root / "data" / "comfyui"
        self.install.mkdir(parents=True)
        self.a = root / "loras_a"
        self.b = root / "loras_b"
        self.c = root / "loras_c"
        for d in (self.a, self.b, self.c):
            d.mkdir()
        (self.a / "look.safetensors").write_bytes(lora_bytes())
        (self.a / "sub").mkdir()
        (self.a / "sub" / "deep.safetensors").write_bytes(lora_bytes())
        (self.a / "notes.txt").write_text("not a LoRA", encoding="utf-8")
        self.listed: list[str] | None = None  # None: ComfyUI is not running; else what ComfyUI lists
        self.state: dict[str, str] = {}  # card -> supervisor state


@pytest.fixture
def box(tmp_path: Path, settings_path: Path, monkeypatch: pytest.MonkeyPatch):
    from unittest import mock

    from vgac import comfy_install, config, db, lora_folders, reference, user_addons

    b = Box(tmp_path)
    monkeypatch.setenv("VGAC_MACHINE", str(b.machine))
    monkeypatch.setenv("VGAC_CONFIG", str(settings_path))
    config.reset_settings()
    db.reset_engine()
    reference.reload()

    class Sup:
        def state(self, name: str) -> str:
            return b.state.get(name, "stopped")

    def fake_choices():
        if b.listed is None:
            return None
        return list(b.listed)

    # Patched and restored explicitly here, so no other test ever sees the fake queue.
    patches = [mock.patch.object(comfy_install, "install_root", lambda: b.install),
               mock.patch.object(lora_folders, "_supervisor", lambda: Sup()),
               mock.patch.object(user_addons, "_engine_choices", fake_choices)]
    for p in patches:
        p.start()
    try:
        yield b
    finally:
        for p in reversed(patches):
            p.stop()
        db.reset_engine()
        config.reset_settings()


@pytest.fixture
def client(box):
    from vgac.main import app

    return TestClient(app)


def machine_folders(box: Box) -> list[str]:
    return json.loads(box.machine.read_text(encoding="utf-8"))["backend"].get("lora_folders", [])


# --- add, remove, validation ---------------------------------------------------------------------------------------------


def test_add_saves_the_folder_and_lists_its_loras(client, box):
    r = client.post("/api/lora-folders", json={"path": str(box.a)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert machine_folders(box) == [str(box.a)]
    (folder,) = body["folders"]
    assert folder["exists"] and folder["readable"] and folder["lora_files"] == 2 and folder["problem"] == ""
    assert "2 LoRA files" in body["message"]
    assert client.get("/api/lora-folders").json()["folders"][0]["lora_files"] == 2


def test_several_folders_are_kept_in_order_and_one_can_be_removed(client, box):
    for d in (box.a, box.b, box.c):
        assert client.post("/api/lora-folders", json={"path": str(d)}).status_code == 200
    assert machine_folders(box) == [str(box.a), str(box.b), str(box.c)]
    r = client.post("/api/lora-folders/remove", json={"path": str(box.b)})
    assert r.status_code == 200 and "not touched" in r.json()["message"]
    assert machine_folders(box) == [str(box.a), str(box.c)]
    assert box.b.is_dir() and (box.a / "look.safetensors").is_file()  # the folders and their files are never touched


def test_add_refuses_what_is_not_a_readable_folder(client, box, monkeypatch):
    from vgac import lora_folders

    missing = box.root / "nowhere"
    r = client.post("/api/lora-folders", json={"path": str(missing)})
    assert r.status_code == 404 and "not on this computer" in r.json()["detail"]
    one_file = box.a / "look.safetensors"
    r = client.post("/api/lora-folders", json={"path": str(one_file)})
    assert r.status_code == 400 and "is a file" in r.json()["detail"]
    r = client.post("/api/lora-folders", json={"path": "relative\\folder"})
    assert r.status_code == 400 and "full path" in r.json()["detail"]
    r = client.post("/api/lora-folders", json={"path": "%USERPROFILE%\\loras"})
    assert r.status_code == 400
    def denied(folder: str) -> list[str]:
        raise PermissionError("denied")

    monkeypatch.setattr(lora_folders, "lora_names", denied)
    r = client.post("/api/lora-folders", json={"path": str(box.c)})
    assert r.status_code == 400 and "cannot read" in r.json()["detail"]
    assert machine_folders(box) == []


def test_add_refuses_a_duplicate_and_a_list_that_is_full(client, box, monkeypatch):
    from vgac import lora_folders

    assert client.post("/api/lora-folders", json={"path": str(box.a)}).status_code == 200
    r = client.post("/api/lora-folders", json={"path": str(box.a)})
    assert r.status_code == 409 and "already" in r.json()["detail"]
    monkeypatch.setattr(lora_folders, "MAX_FOLDERS", 1)
    r = client.post("/api/lora-folders", json={"path": str(box.b)})
    assert r.status_code == 409 and "up to 1" in r.json()["detail"]
    assert machine_folders(box) == [str(box.a)]


def test_remove_refuses_a_folder_not_in_the_list(client, box):
    r = client.post("/api/lora-folders/remove", json={"path": str(box.a)})
    assert r.status_code == 404


def test_a_folder_that_disappears_is_said_plainly(client, box):
    client.post("/api/lora-folders", json={"path": str(box.c)})
    box.c.rmdir()
    f = client.get("/api/lora-folders").json()["folders"][0]
    assert f["exists"] is False and "not on this computer any more" in f["problem"]


# --- the app's model folders file ----------------------------------------------------------------------------------------


def test_the_model_folders_file_gets_every_folder_and_keeps_the_other_entries(client, box):
    from vgac import lora_folders

    target = box.install / lora_folders.APP_FILE
    target.write_text("# old header\nyour_comfyui_models:\n    base_path: \"D:/Models\"\n    checkpoints: checkpoints/\n", encoding="utf-8")
    client.post("/api/lora-folders", json={"path": str(box.a)})
    client.post("/api/lora-folders", json={"path": str(box.b)})
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert data["your_comfyui_models"] == {"base_path": "D:/Models", "checkpoints": "checkpoints/"}  # kept as it was
    assert data[lora_folders.SECTION]["loras"].split("\n") == [str(box.a), str(box.b)]  # one folder a line, ComfyUI's form
    assert "# Written by VGAC" in target.read_text(encoding="utf-8")

    client.post("/api/lora-folders/remove", json={"path": str(box.a)})
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert data[lora_folders.SECTION]["loras"] == str(box.b)
    assert data["your_comfyui_models"]["checkpoints"] == "checkpoints/"

    client.post("/api/lora-folders/remove", json={"path": str(box.b)})
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert lora_folders.SECTION not in data and data["your_comfyui_models"]["base_path"] == "D:/Models"


def test_merged_text_is_valid_for_comfyui_with_several_folders(box):
    from vgac import lora_folders

    text = lora_folders.merged_text("your_comfyui_models:\n    base_path: X\n", [r"F:\A", r"G:\B with space\x"])
    data = yaml.safe_load(text)
    assert data["your_comfyui_models"] == {"base_path": "X"}
    lines = data[lora_folders.SECTION]["loras"].split("\n")
    assert lines == [r"F:\A", r"G:\B with space\x"]
    assert lora_folders.merged_text(text, []).count(lora_folders.SECTION) == 0


def test_no_folder_file_is_written_before_the_app_is_installed(client, box):
    from vgac import lora_folders

    box.install.rmdir()
    assert client.post("/api/lora-folders", json={"path": str(box.a)}).status_code == 200
    assert not (box.install / lora_folders.APP_FILE).exists()  # the install writes it, with the folders saved then


# --- the restart note ----------------------------------------------------------------------------------------------------


def _set_cards(box: Box, cards: dict[str, Any]) -> None:
    """Write the saved card settings: each value is 'app' (the app's own engine and file), 'other' (an engine with another file)
    or 'external' (no engine)."""
    insts: dict[str, Any] = {}
    for name, kind in cards.items():
        inst: dict[str, Any] = {"url": f"http://127.0.0.1:{9000 + ord(name)}", "gpu": f"Card {name}"}
        if kind == "app":
            inst["engine"] = {"comfy_dir": str(box.install / "ComfyUI"), "python": "p", "main": "main.py",
                              "extra_model_paths": str(box.install / "shared-models.yaml")}
        elif kind == "other":
            inst["engine"] = {"comfy_dir": "D:/Other", "python": "p", "main": "main.py", "extra_model_paths": "D:/Other/x.yaml"}
        insts[name] = inst
    data = json.loads(box.machine.read_text(encoding="utf-8"))
    data["backend"]["instances"] = insts
    box.machine.write_text(json.dumps(data), encoding="utf-8")


def test_the_restart_note_names_the_card_to_stop_and_start(client, box):
    _set_cards(box, {"a": "app", "b": "app", "c": "external"})
    box.state = {"a": "running"}  # b is not running
    body = client.post("/api/lora-folders", json={"path": str(box.a)}).json()
    notes = {n["card"]: n for n in body["restart"]}
    assert notes["a"]["state"] == "restart" and "Stop, then Start on this card" in notes["a"]["text"]
    assert notes["b"]["state"] == "starts_later" and "reads the folders when the app starts it" in notes["b"]["text"]
    assert notes["c"]["state"] == "external" and "add the LoRA folders in that ComfyUI's own settings" in notes["c"]["text"]
    assert body["restart_cards"] == ["a"]


def test_an_engine_with_another_file_is_told_to_add_the_folder_itself(client, box):
    _set_cards(box, {"a": "other"})
    body = client.post("/api/lora-folders", json={"path": str(box.a)}).json()
    (note,) = body["restart"]
    assert note["state"] == "not_app" and "does not read the app's model folders file" in note["text"]
    assert body["restart_cards"] == []


def test_removing_a_folder_gives_the_same_restart_note(client, box):
    _set_cards(box, {"a": "app"})
    box.state = {"a": "running"}
    client.post("/api/lora-folders", json={"path": str(box.a)})
    body = client.post("/api/lora-folders/remove", json={"path": str(box.a)}).json()
    assert body["restart_cards"] == ["a"] and "Restart ComfyUI" in body["message"]


# --- a LoRA in a linked folder: listed, and used where it is --------------------------------------------------------------


def test_a_lora_in_a_linked_folder_is_listed_then_used_in_place(client, box):
    client.post("/api/lora-folders", json={"path": str(box.a)})
    box.listed = ["look.safetensors", "sub/deep.safetensors"]
    names = {f["file_name"]: f for f in client.get("/api/my-addons/available").json()}
    assert set(names) == {"look.safetensors", "sub/deep.safetensors"}
    assert names["look.safetensors"]["folder"] == str(box.a) and "prefill" in names["look.safetensors"]
    r = client.post("/api/my-addons", json={"file_name": "sub/deep.safetensors", "folder": str(box.a), "label": "Deep look",
                                            "base_models": ["zturbo"]})
    assert r.status_code == 200, r.text
    entry = r.json()
    assert entry["file_name"] == "deep.safetensors"
    from vgac import reference

    assert reference.addon(entry["id"])["file"] == "sub/deep.safetensors"  # the name ComfyUI lists it under
    assert entry["placement"]["mode"] == "in_place" and entry["placement"]["source"] == str(box.a / "sub" / "deep.safetensors")
    assert [f["file_name"] for f in client.get("/api/my-addons/available").json()] == ["look.safetensors"]
    assert client.get("/api/my-addons").json()["addons"][0]["placement"]["mode"] == "in_place"
    assert (box.a / "sub" / "deep.safetensors").read_bytes() == lora_bytes()  # untouched


def test_a_lora_comfyui_does_not_list_is_not_kept_and_the_restart_is_asked(client, box):
    client.post("/api/lora-folders", json={"path": str(box.a)})
    box.listed = []  # the folder was added after ComfyUI started: it does not list the file
    r = client.post("/api/my-addons", json={"file_name": "look.safetensors", "folder": str(box.a), "label": "Look",
                                            "base_models": ["zturbo"]})
    assert r.status_code == 409 and "restart ComfyUI" in r.json()["detail"]
    assert client.get("/api/my-addons").json()["addons"] == []


def test_a_lora_outside_the_linked_folders_is_refused(client, box):
    client.post("/api/lora-folders", json={"path": str(box.a)})
    box.listed = ["look.safetensors"]
    r = client.post("/api/my-addons", json={"file_name": "look.safetensors", "folder": str(box.b), "label": "Look",
                                            "base_models": ["zturbo"]})
    assert r.status_code == 404 and "LoRA folders" in r.json()["detail"]


def test_removing_a_lora_takes_it_off_the_list_and_keeps_the_file(client, box):
    client.post("/api/lora-folders", json={"path": str(box.a)})
    box.listed = ["look.safetensors"]
    e = client.post("/api/my-addons", json={"file_name": "look.safetensors", "folder": str(box.a), "label": "Look",
                                            "base_models": ["zturbo"]}).json()
    r = client.delete(f"/api/my-addons/{e['id']}")
    assert r.status_code == 200 and r.json()["file_kept"] is True and r.json()["file_removed"] is False
    assert (box.a / "look.safetensors").is_file()


# --- no copy and no hard link of a LoRA ----------------------------------------------------------------------------------


FORBIDDEN_IN_LORA_CODE = ("os.link(", "copyfileobj", "shutil.copy", "link_file(", "place_link(", "mine_dir", "VGAC/mine", "VGAC\\mine",
                          "os.symlink", "symlink_to", "junction")


@pytest.mark.parametrize("module", ["user_addons.py", "lora_folders.py", "guidance.py"])
def test_no_lora_code_path_copies_or_links_a_file(module):
    text = (VGAC_ROOT / module).read_text(encoding="utf-8")
    for word in FORBIDDEN_IN_LORA_CODE:
        assert word not in text, f"{module} still has {word}"


def test_the_copy_and_link_routes_are_gone(client, box):
    assert client.post("/api/my-addons/upload?name=up.safetensors", content=lora_bytes()).status_code in (404, 405)
    assert client.post("/api/my-addons/place", json={"path": str(box.a / "look.safetensors")}).status_code in (404, 405)
    assert client.post("/api/my-addons/open-folder").status_code in (404, 405)
    assert client.get("/api/my-addons/unregistered").status_code in (404, 405)


def test_the_list_says_which_folders_are_missing_or_unreadable(client, box):
    client.post("/api/lora-folders", json={"path": str(box.a)})
    client.post("/api/lora-folders", json={"path": str(box.b)})
    box.b.rmdir()
    folders = client.get("/api/my-addons").json()["folders"]
    assert [f["exists"] for f in folders] == [True, False]
