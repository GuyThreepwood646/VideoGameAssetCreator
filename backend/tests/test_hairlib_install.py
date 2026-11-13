"""Where the app reads the hair library from: the library setup builds on this PC (<library_path>/hairlib) first, the
copy inside the project while it still exists, and a plain how-to message when neither is there. The person's own
hairstyles must work in every case. No Blender needed."""
from __future__ import annotations

import json
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from vgac import hairlib, hairstyles, my_hair

PACK = "Test pack"


def _entry(sid: str, pack: str = PACK, author: str = "Tester") -> dict:
    return {"id": sid, "name": sid, "pack": pack, "author": author, "family": "short", "kind": "shell", "alpha": "opaque",
            "triangles": 800, "phone_ok": True, "licence": "CC0-1.0", "source_url": "", "thumbs": {}}


def _write_library(folder: Path, ids=("fake_a",), **kw) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "catalogue.json").write_text(json.dumps({"styles": {i: _entry(i, **kw) for i in ids}, "refused": [], "left_out": {}, "version": 1}),
                                           encoding="utf-8")


@pytest.fixture(autouse=True)
def _clean():
    hairstyles.use_dir(None)
    yield
    hairstyles.use_dir(None)


def _settings(monkeypatch, library_path: Path) -> None:
    monkeypatch.setattr("vgac.config.get_settings", lambda: SimpleNamespace(library_path=library_path))
    hairstyles.use_dir(None)


def _asset(**kw):
    base = {"category": "humanoid", "style_preset": "painted", "prompt": "a knight with short hair", "platform_target": "pc_mid"}
    return types.SimpleNamespace(**{**base, **kw})


def test_built_library_is_read_first(monkeypatch, tmp_path):
    _write_library(tmp_path / "hairlib", ("fake_a", "fake_b"))
    _settings(monkeypatch, tmp_path)
    assert hairstyles.library_dir() == tmp_path / "hairlib"
    assert set(hairstyles.catalogue()["styles"]) == {"fake_a", "fake_b"}  # not the 26 in the project folder
    assert hairstyles.installed()
    assert hairstyles.file_path("styles/fake_a.npz") == (tmp_path / "hairlib" / "styles" / "fake_a.npz").resolve()


def test_falls_back_to_the_project_copy_while_it_exists(monkeypatch, tmp_path):
    _settings(monkeypatch, tmp_path)  # nothing built
    assert hairstyles.library_dir() == hairstyles.DATA_DIR
    assert len(hairstyles.catalogue()["styles"]) >= 26
    assert hairstyles.installed()


def test_a_folder_without_a_catalogue_is_not_a_built_library(monkeypatch, tmp_path):
    (tmp_path / "hairlib").mkdir()  # a build that never finished has no catalogue.json
    _settings(monkeypatch, tmp_path)
    assert hairstyles.library_dir() == hairstyles.DATA_DIR


def test_pack_picks_apply_to_the_built_library(monkeypatch, tmp_path):
    _write_library(tmp_path / "hairlib", ("fake_a",))
    (tmp_path / "hair-packs.json").write_text(json.dumps({"packs": [{"catalogue_pack": "Other pack", "catalogue_author": "Tester"}]}), encoding="utf-8")
    _settings(monkeypatch, tmp_path)
    assert hairstyles.catalogue()["styles"] == {}
    (tmp_path / "hair-packs.json").write_text(json.dumps({"packs": [{"catalogue_pack": PACK, "catalogue_author": "Tester"}]}), encoding="utf-8")
    hairstyles.use_dir(None)
    assert set(hairstyles.catalogue()["styles"]) == {"fake_a"}


def test_no_library_says_how_to_get_it(monkeypatch, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(hairstyles, "DATA_DIR", empty)
    monkeypatch.setattr(my_hair, "entries", lambda settings=None: {})
    _settings(monkeypatch, tmp_path)
    assert not hairstyles.installed()
    reason = hairlib.available(_asset())
    assert reason == hairlib.NOT_INSTALLED
    assert "not installed" in reason and "Get the hair library" in reason and "System page" in reason
    lib = hairlib.hair_library(db=None)
    assert lib["installed"] is False and lib["styles"] == [] and lib["how_to_install"] == hairlib.NOT_INSTALLED


def test_own_hairstyles_work_without_the_library(monkeypatch, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    own = {"mine_1": {**_entry("mine_1", pack="Your hairstyles", author="You"), "own": True}}
    monkeypatch.setattr(hairstyles, "DATA_DIR", empty)
    monkeypatch.setattr(my_hair, "entries", lambda settings=None: own)
    _settings(monkeypatch, tmp_path)
    assert not hairstyles.installed()  # the built-in library is still missing
    assert "mine_1" in hairstyles.catalogue()["styles"]
    assert hairlib.available(_asset()) is None  # the panel is usable: the person's own style can be picked
    lib = hairlib.hair_library(db=None)
    assert [s["id"] for s in lib["styles"]] == ["mine_1"] and lib["installed"] is False


def test_own_hairstyles_still_merge_with_a_built_library(monkeypatch, tmp_path):
    _write_library(tmp_path / "hairlib", ("fake_a",))
    own = {"mine_1": {**_entry("mine_1", pack="Your hairstyles", author="You"), "own": True}}
    monkeypatch.setattr(my_hair, "entries", lambda settings=None: own)
    _settings(monkeypatch, tmp_path)
    assert set(hairstyles.catalogue()["styles"]) == {"fake_a", "mine_1"}
    assert my_hair._builtin_ids() == {"fake_a"}


# --- hairlib_builder.verify / compare: the check run before a freshly built library is swapped in -------------------------


def test_verify_accepts_the_project_library_and_refuses_a_damaged_copy(tmp_path):
    import shutil

    from vgac import hairlib_builder as hb

    lib = Path(hairstyles.DATA_DIR)
    if not (lib / "catalogue.json").is_file():
        pytest.skip("the project copy of the library has been removed")
    _cat, bad = hb.verify(lib)
    assert bad == []
    copy = tmp_path / "copy"
    shutil.copytree(lib, copy)
    victim = next((copy / "tex").glob("*.png"))
    victim.write_bytes(victim.read_bytes() + b"x")
    (copy / "styles" / "q_buns.npz").unlink()
    _cat, bad = hb.verify(copy)
    assert any("q_buns" in b and "missing" in b for b in bad)
    assert any(victim.name in b and "checksum" in b for b in bad)
    assert hb.compare(copy, lib)  # and it no longer equals the original
