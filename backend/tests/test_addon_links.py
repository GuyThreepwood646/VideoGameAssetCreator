"""Linking a LoRA the person already has (addon_links.py): in place, hard link, offered copy; ComfyUI must list it; removal takes away
only what the app made; a missing original is reported. The engine's LoRA list is faked (it lists what is really in the folders)."""

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


def lora_bytes() -> bytes:
    header: dict[str, Any] = {}
    off = 0
    for k in (LORA_KEY, LORA_KEY.replace("lora_A", "lora_B")):
        header[k] = {"dtype": "F16", "shape": [4, 8], "data_offsets": [off, off + 64]}
        off += 64
    raw = json.dumps(header).encode("utf-8")
    raw += b" " * (-len(raw) % 8)
    return struct.pack("<Q", len(raw)) + raw + b"\x01" * off


@pytest.fixture
def world(settings_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from vgac import addon_links, config, db, reference

    loras, extra, elsewhere = tmp_path / "loras", tmp_path / "extra_loras", tmp_path / "downloads"
    for d in (loras, extra, elsewhere):
        d.mkdir()
    cfg = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    cfg["loras_dir"] = str(loras)
    settings_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setenv("VGAC_CONFIG", str(settings_path))
    config.reset_settings()
    db.reset_engine()
    reference.reload()
    monkeypatch.setattr(addon_links, "lora_folders", lambda: [loras, extra])

    state = {"up": True, "hide": set()}

    def fake_list() -> list[str] | None:
        """What ComfyUI lists: every .safetensors under its LoRA folders, with backslashes, except the hidden ones."""
        if not state["up"]:
            return None
        out = []
        for base in (loras, extra):
            for p in base.rglob("*.safetensors"):
                rel = os.path.relpath(p, base).replace("/", "\\")
                if rel not in state["hide"]:
                    out.append(rel)
        return out

    monkeypatch.setattr(addon_links, "engine_lora_list", fake_list)
    yield {"loras": loras, "extra": extra, "elsewhere": elsewhere, "state": state, "mine": loras / "VGAC" / "mine"}
    db.reset_engine()
    config.reset_settings()


@pytest.fixture
def client(world):
    from vgac.main import app

    return TestClient(app)


def make(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_bytes(lora_bytes())
    return p


def form(file_name: str, in_place: bool = False) -> dict[str, Any]:
    return {"file_name": file_name, "label": "Mine", "base_models": ["zturbo"], "in_place": in_place}


# --- Route 1: already in a folder ComfyUI reads -----------------------------------------------------------------------------


def test_in_place_in_extra_folder_makes_nothing(client, world):
    src = make(world["extra"] / "styles", "ink.safetensors")
    r = client.post("/api/my-addons/place", json={"path": str(src)})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["route"] == "in_place" and got["in_place"] is True and got["file_name"] == "styles/ink.safetensors"
    assert "Nothing was copied or linked" in got["message"]
    assert not world["mine"].exists()  # nothing was made anywhere
    e = client.post("/api/my-addons", json=form(got["file_name"], True)).json()
    assert e["placement"]["mode"] == "in_place" and e["placement"]["file_missing"] is False
    from vgac import reference

    assert reference.addon(e["id"])["file"] == "styles/ink.safetensors"


def test_in_place_in_primary_folder_subfolder(client, world):
    src = make(world["loras"] / "mystuff", "x.safetensors")
    got = client.post("/api/my-addons/place", json={"path": str(src)}).json()
    assert got["route"] == "in_place" and got["file_name"] == "mystuff/x.safetensors"


def test_in_place_refused_when_comfyui_does_not_list_it_falls_to_link(client, world):
    """A folder the app thinks ComfyUI reads, but ComfyUI does not list the file: it is linked instead, and the message says linked."""
    src = make(world["extra"], "late.safetensors")
    world["state"]["hide"].add("late.safetensors")
    got = client.post("/api/my-addons/place", json={"path": str(src)}).json()
    assert got["route"] == "link"
    assert "Linked" in got["message"] and "Nothing was copied" not in got["message"]


def test_file_already_in_the_apps_folder_is_not_linked_again(client, world):
    src = make(world["mine"], "here.safetensors")
    got = client.post("/api/my-addons/place", json={"path": str(src)}).json()
    assert got["route"] == "already_here"
    assert [p.name for p in world["mine"].iterdir()] == ["here.safetensors"]


# --- Route 2: hard link on the same drive -----------------------------------------------------------------------------------


def test_hard_link_made_and_listed(client, world):
    src = make(world["elsewhere"], "style.safetensors")
    r = client.post("/api/my-addons/place", json={"path": str(src)})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["route"] == "link" and got["file_name"] == "style.safetensors" and got["in_place"] is False
    link = world["mine"] / "style.safetensors"
    assert link.is_file() and os.path.samefile(link, src)
    assert link.stat().st_nlink >= 2 and not link.is_symlink()
    assert "Linked" in got["message"] and "Copied" not in got["message"]


def test_link_refused_and_taken_away_when_comfyui_does_not_list_it(client, world):
    src = make(world["elsewhere"], "style.safetensors")
    world["state"]["hide"].add("VGAC\\mine\\style.safetensors")
    r = client.post("/api/my-addons/place", json={"path": str(src)})
    assert r.status_code == 409 and "does not list" in r.json()["detail"]
    assert not (world["mine"] / "style.safetensors").exists()  # the link just made is gone
    assert src.is_file()  # the original never is


def test_engine_silent_refuses_and_keeps_nothing(client, world):
    src = make(world["elsewhere"], "style.safetensors")
    world["state"]["up"] = False
    r = client.post("/api/my-addons/place", json={"path": str(src)})
    assert r.status_code == 409 and "could not ask ComfyUI" in r.json()["detail"]
    assert not (world["mine"] / "style.safetensors").exists()


def test_link_never_overwrites_another_file(client, world):
    src = make(world["elsewhere"], "style.safetensors")
    make(world["mine"], "style.safetensors")
    r = client.post("/api/my-addons/place", json={"path": str(src)})
    assert r.status_code == 409
    assert not os.path.samefile(world["mine"] / "style.safetensors", src)


# --- Route 3: another drive, a copy only after Copy -------------------------------------------------------------------------


@pytest.fixture
def other_drive(monkeypatch):
    from vgac import model_choices

    monkeypatch.setattr(model_choices, "_same_drive", lambda a, b: False)


def test_other_drive_offers_a_labelled_copy_and_does_nothing(client, world, other_drive):
    src = make(world["elsewhere"], "far.safetensors")
    got = client.post("/api/my-addons/place", json={"path": str(src)}).json()
    assert got["route"] == "copy_needed" and got["can_copy"] is True
    assert "another drive" in got["message"] and "copy of" in got["message"] and got["folder"] == str(world["mine"])
    assert not world["mine"].exists() or not any(world["mine"].iterdir())


def test_copy_made_after_copy_choice_and_listed(client, world, other_drive):
    src = make(world["elsewhere"], "far.safetensors")
    got = client.post("/api/my-addons/place", json={"path": str(src), "make_copy": True}).json()
    assert got["route"] == "copy" and "Copied" in got["message"] and "Linked" not in got["message"]
    dst = world["mine"] / "far.safetensors"
    assert dst.read_bytes() == src.read_bytes() and not os.path.samefile(dst, src)
    assert not list(world["mine"].glob(".copy-*"))


def test_copy_refused_when_not_listed_and_cleaned_up(client, world, other_drive):
    src = make(world["elsewhere"], "far.safetensors")
    world["state"]["hide"].add("VGAC\\mine\\far.safetensors")
    r = client.post("/api/my-addons/place", json={"path": str(src), "make_copy": True})
    assert r.status_code == 409
    assert not (world["mine"] / "far.safetensors").exists() and src.is_file()


def test_copy_refused_without_room(client, world, other_drive, monkeypatch):
    from vgac import addon_links

    src = make(world["elsewhere"], "far.safetensors")
    monkeypatch.setattr(addon_links, "free_bytes", lambda p: 10)
    r = client.post("/api/my-addons/place", json={"path": str(src), "make_copy": True})
    assert r.status_code == 409 and "not enough room" in r.json()["detail"]


# --- Refusals before anything is made ---------------------------------------------------------------------------------------


def test_bad_paths_and_files_refused(client, world):
    assert client.post("/api/my-addons/place", json={"path": "style.safetensors"}).status_code == 400
    assert client.post("/api/my-addons/place", json={"path": str(world["elsewhere"] / "none.safetensors")}).status_code == 404
    txt = world["elsewhere"] / "a.txt"
    txt.write_text("x")
    assert client.post("/api/my-addons/place", json={"path": str(txt)}).status_code == 400
    bad = world["elsewhere"] / "model.safetensors"
    bad.write_bytes(b"\x00" * 40)
    r = client.post("/api/my-addons/place", json={"path": str(bad)})
    assert r.status_code == 400
    assert not world["mine"].exists() or not any(world["mine"].iterdir())


# --- Removal: only what the app made ---------------------------------------------------------------------------------------


def _add(client, world, route_dir: Path, name: str, monkeypatch=None) -> tuple[dict, Path]:
    src = make(route_dir, name)
    got = client.post("/api/my-addons/place", json={"path": str(src), "make_copy": True}).json()
    e = client.post("/api/my-addons", json=form(got["file_name"], got["in_place"])).json()
    return e, src


def test_remove_link_takes_the_link_never_the_original(client, world):
    e, src = _add(client, world, world["elsewhere"], "style.safetensors")
    assert e["placement"]["mode"] == "link"
    r = client.delete(f"/api/my-addons/{e['id']}").json()
    assert r["file_removed"] is True and r["file_kept"] is False and r["mode"] == "link"
    assert not (world["mine"] / "style.safetensors").exists() and src.is_file()


def test_remove_copy_takes_the_copy_never_the_original(client, world, other_drive):
    e, src = _add(client, world, world["elsewhere"], "far.safetensors")
    assert e["placement"]["mode"] == "copy"
    r = client.delete(f"/api/my-addons/{e['id']}").json()
    assert r["file_removed"] is True
    assert not (world["mine"] / "far.safetensors").exists() and src.is_file()


def test_remove_in_place_removes_nothing(client, world):
    e, src = _add(client, world, world["extra"], "ink.safetensors")
    assert e["placement"]["mode"] == "in_place"
    r = client.delete(f"/api/my-addons/{e['id']}").json()
    assert r["file_removed"] is False and r["file_kept"] is True and r["mode"] == "in_place"
    assert src.is_file()


def test_remove_keeps_a_file_the_app_did_not_make(client, world):
    from vgac import user_addons

    p = make(world["mine"], "byhand.safetensors")
    e = user_addons.register("byhand.safetensors", "By hand", ["zturbo"])
    r = client.delete(f"/api/my-addons/{e['id']}").json()
    assert r["file_kept"] is True and r["mode"] == "" and p.is_file()


def test_remove_copy_that_was_changed_is_kept(client, world, other_drive):
    e, src = _add(client, world, world["elsewhere"], "far.safetensors")
    dst = world["mine"] / "far.safetensors"
    dst.write_bytes(dst.read_bytes() + b"more")  # no longer the copy the app made
    r = client.delete(f"/api/my-addons/{e['id']}").json()
    assert r["file_removed"] is False and "no longer the copy" in r["message"] and dst.is_file()


def test_browser_upload_is_recorded_as_a_copy_and_removed_with_it(client, world):
    r = client.post("/api/my-addons/upload?name=up.safetensors", content=lora_bytes())
    assert r.status_code == 200
    e = client.post("/api/my-addons", json=form("up.safetensors")).json()
    assert e["placement"]["mode"] == "copy" and "copy" in e["placement"]["note"].lower()
    out = client.delete(f"/api/my-addons/{e['id']}").json()
    assert out["file_removed"] is True and not (world["mine"] / "up.safetensors").exists()


# --- A missing original or file ---------------------------------------------------------------------------------------------


def test_link_survives_a_deleted_original_and_says_so(client, world):
    e, src = _add(client, world, world["elsewhere"], "style.safetensors")
    src.unlink()
    listing = client.get("/api/my-addons").json()["addons"][0]["placement"]
    assert listing["source_missing"] is True and listing["file_missing"] is False
    assert "original file you linked from is gone" in listing["note"] and "still holds the data" in listing["note"]
    from vgac import reference

    r = reference.resolve_addons("zturbo", "concept", [], None, {e["id"]: "normal"})
    assert [a["id"] for a in r["applied"]] == [e["id"]]  # still used
    # the link is now the only copy: removing the LoRA from the list keeps it
    out = client.delete(f"/api/my-addons/{e['id']}").json()
    assert out["file_kept"] is True and "only copy" in out["message"]
    assert (world["mine"] / "style.safetensors").is_file()


def test_in_place_file_gone_is_reported_and_skipped(client, world):
    e, src = _add(client, world, world["extra"], "ink.safetensors")
    src.unlink()
    p = client.get("/api/my-addons").json()["addons"][0]["placement"]
    assert p["file_missing"] is True and "no longer where you registered it" in p["note"]
    from vgac import reference

    r = reference.resolve_addons("zturbo", "concept", [], None, {e["id"]: "normal"})
    assert r["applied"] == []


def test_changing_a_linked_addon_keeps_its_file(client, world):
    e, src = _add(client, world, world["elsewhere"], "style.safetensors")
    r = client.put(f"/api/my-addons/{e['id']}", json={"label": "Renamed", "base_models": ["zbase"], "default_strength": "strong"})
    assert r.status_code == 200 and r.json()["label"] == "Renamed" and r.json()["placement"]["mode"] == "link"
    assert (world["mine"] / "style.safetensors").is_file()
    assert client.put("/api/my-addons/mine_nope", json={"label": "x", "base_models": ["zbase"]}).status_code == 404
    assert client.put(f"/api/my-addons/{e['id']}", json={"label": "x", "base_models": []}).status_code == 400


def test_no_symlink_or_junction_is_ever_made(client, world, other_drive):
    src = make(world["elsewhere"], "far.safetensors")
    client.post("/api/my-addons/place", json={"path": str(src), "make_copy": True})
    for p in world["mine"].rglob("*"):
        assert not p.is_symlink() and not (hasattr(p, "is_junction") and p.is_junction())
