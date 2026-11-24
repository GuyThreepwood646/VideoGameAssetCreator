"""The project's own node pack (vgac-nodes) is updated when an older copy is found (PL-81; pack_update.py).

Temp folders only: the shipped pack is a planted copy, and every ComfyUI is a planted folder. The install-level tests
(step_packs) are in test_comfy_install.py, where the fake install lives."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from vgac import comfy_find, comfy_install, downloader, guidance, pack_update, preflight, reference

OLD = "old\n"
NEW = "new\n"


def _src(root: Path) -> Path:
    src = root / "comfy-nodes" / "vgac-nodes"
    (src / "nodes").mkdir(parents=True)
    (src / "__init__.py").write_text(NEW, encoding="utf-8")
    (src / "nodes" / "decode.py").write_text("decode new\n", encoding="utf-8")
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "x.pyc").write_bytes(b"cache")
    return src


def _old_copy(app: Path) -> Path:
    target = app / "custom_nodes" / "vgac-nodes"
    target.mkdir(parents=True)
    (target / "__init__.py").write_text(OLD, encoding="utf-8")
    return target


def _settings(*apps: Path):
    """A stand-in for the saved settings: each instance's linked engine, the way set_engines writes it."""
    return SimpleNamespace(instances={
        f"card{i}": SimpleNamespace(engine=SimpleNamespace(comfy_dir=str(a.parent), main="ComfyUI/main.py"))
        for i, a in enumerate(apps)})


def _copy_current(src: Path, target: Path) -> None:
    for rel in pack_update.pack_files(src):
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        (target / rel).write_bytes((src / rel).read_bytes())
    pack_update.write_manifest(target, src)


def test_an_old_pack_is_replaced_backed_up_and_keeps_the_persons_own_files(tmp_path):
    src = _src(tmp_path)
    target = _old_copy(tmp_path / "ComfyUI")
    (target / "gone.py").write_text("removed in the new version\n", encoding="utf-8")
    (target / "own_notes.txt").write_text("the person's own file\n", encoding="utf-8")
    # a manifest of another version that names the file the new pack dropped
    (target / pack_update.MANIFEST).write_text(json.dumps({"version": "not-the-shipped-one",
                                                           "files": ["__init__.py", "gone.py"]}), encoding="utf-8")
    assert pack_update.state(target, src) == pack_update.OUTDATED
    backups = tmp_path / "backups"
    backup = pack_update.replace(target, src, backups, "20261124-000000")
    assert (target / "__init__.py").read_text(encoding="utf-8") == NEW
    assert (target / "nodes" / "decode.py").read_text(encoding="utf-8") == "decode new\n"
    assert not (target / "gone.py").exists()  # named by the manifest and no longer shipped: removed
    assert (target / "own_notes.txt").read_text(encoding="utf-8") == "the person's own file\n"
    assert backup.parent == backups and (backup / "__init__.py").read_text(encoding="utf-8") == OLD
    assert (backup / "gone.py").is_file() and (backup / "own_notes.txt").is_file()
    assert pack_update.state(target, src) == pack_update.CURRENT


def test_a_current_pack_is_left_alone_and_a_changed_source_makes_it_outdated(tmp_path):
    src = _src(tmp_path)
    target = tmp_path / "ComfyUI" / "custom_nodes" / "vgac-nodes"
    target.mkdir(parents=True)
    _copy_current(src, target)
    assert pack_update.state(target, src) == pack_update.CURRENT
    (target / "__pycache__").mkdir()  # a cache folder does not change the version
    (target / "__pycache__" / "y.pyc").write_bytes(b"cache")
    assert pack_update.state(target, src) == pack_update.CURRENT
    (src / "nodes" / "decode.py").write_text("changed\n", encoding="utf-8")
    assert pack_update.state(target, src) == pack_update.OUTDATED


def test_a_copy_without_a_manifest_is_outdated_and_an_empty_folder_is_missing(tmp_path):
    src = _src(tmp_path)
    target = _old_copy(tmp_path / "ComfyUI")
    assert pack_update.state(target, src) == pack_update.OUTDATED  # no manifest: made before manifests existed
    empty = tmp_path / "ComfyUI" / "custom_nodes" / "empty"
    empty.mkdir()
    assert pack_update.state(empty, src) == pack_update.MISSING


def test_a_copy_without_a_manifest_keeps_every_file_it_holds_when_replaced(tmp_path):
    # Its file list is not known, so nothing the old copy holds is deleted; only the pack's own files are written.
    src = _src(tmp_path)
    target = _old_copy(tmp_path / "ComfyUI")
    (target / "stale_from_old.py").write_text("an old file the pack no longer ships\n", encoding="utf-8")
    backup = pack_update.replace(target, src, tmp_path / "backups", "20261124-000001")
    assert (target / "__init__.py").read_text(encoding="utf-8") == NEW
    assert (target / "stale_from_old.py").read_text(encoding="utf-8") == "an old file the pack no longer ships\n"
    assert (backup / "stale_from_old.py").is_file()
    assert pack_update.state(target, src) == pack_update.CURRENT


def test_every_comfyui_the_app_uses_is_checked_including_linked_ones(tmp_path, monkeypatch):
    src = _src(tmp_path)
    own = tmp_path / "install" / "ComfyUI"
    linked = tmp_path / "linked" / "ComfyUI"
    monkeypatch.setattr(comfy_install, "install_root", lambda: tmp_path / "install")
    # nothing installed anywhere: nothing to report
    assert preflight.check_node_pack(_settings(linked), tmp_path) == []
    # a linked ComfyUI with an old copy is reported; VGAC's own install has none yet
    _old_copy(linked)
    rows = preflight.check_node_pack(_settings(linked), tmp_path)
    assert len(rows) == 1 and rows[0]["state"] == "warn"
    assert rows[0]["label"] == guidance.NODE_PACK["label"] == "Node pack out of date"
    assert "Update the node pack" in rows[0]["fix"] and "restart ComfyUI" in rows[0]["fix"]
    # VGAC's own install with an old copy is reported too; a folder listed twice is checked once
    _old_copy(own)
    rows = preflight.check_node_pack(_settings(linked, linked), tmp_path)
    assert sorted(r["state"] for r in rows) == ["warn", "warn"]
    # current copies in both are quiet
    for app in (own, linked):
        target = app / "custom_nodes" / "vgac-nodes"
        for f in target.iterdir():
            f.unlink()
        _copy_current(src, target)
    assert [r["state"] for r in preflight.check_node_pack(_settings(linked), tmp_path)] == ["ok", "ok"]


def test_apply_packs_updates_an_old_copy_in_a_linked_comfyui_with_a_backup(tmp_path, monkeypatch):
    _src(tmp_path)
    (tmp_path / "setup").mkdir()
    (tmp_path / "setup" / "downloads.json").write_text(json.dumps({"node_packs": {"vgac": {
        "name": "vgac-nodes (this project)", "local": "comfy-nodes/vgac-nodes", "folder": "vgac-nodes",
        "licence": "GPL-3.0-or-later"}}}), encoding="utf-8")
    monkeypatch.setattr(downloader, "project_dir", lambda: tmp_path)
    linked = tmp_path / "linked" / "ComfyUI"
    linked.mkdir(parents=True)
    (linked / "main.py").write_text("", encoding="utf-8")
    py = tmp_path / "linked" / "python.exe"
    py.write_bytes(b"MZ")
    target = _old_copy(linked)
    (target / "own_notes.txt").write_text("kept\n", encoding="utf-8")
    c = {"folder": str(tmp_path / "linked"), "main": "ComfyUI/main.py", "python": str(py)}
    assert comfy_find.packs_state(c)[0]["outdated"] is True
    out = comfy_find.apply_packs(c, pip=lambda argv: (0, ""))
    assert out["added"] and "restart ComfyUI" in out["added"][0]
    assert (target / "__init__.py").read_text(encoding="utf-8") == NEW
    assert (target / "own_notes.txt").read_text(encoding="utf-8") == "kept\n"
    backups = list((tmp_path / "local" / "pack-backups").iterdir())
    assert len(backups) == 1 and (backups[0] / "__init__.py").read_text(encoding="utf-8") == OLD
    assert comfy_find.packs_state(c)[0]["outdated"] is False


def test_needed_for_names_the_stages_whose_graphs_use_the_pack_nodes():
    # "mesh" (the 3D build): stages/mesh.py:629 queues graphs/trellis.py convert_graph, which preflight maps to "mesh" (preflight.py:84).
    # "rig": graphs/skintoken.py:19 loads the mesh with VGACLoadTrimesh, and workflows.py:27 maps skintoken_rig to the rig stage.
    root = Path(__file__).resolve().parents[1] / "vgac"
    assert {"mesh", "rig"} <= set(reference.STAGE_ORDER)
    assert "convert_graph(name, prefix" in (root / "stages" / "mesh.py").read_text(encoding="utf-8")
    assert '"convert_graph": ("mesh"' in (root / "preflight.py").read_text(encoding="utf-8")
    assert "VGACLoadTrimesh" in (root / "graphs" / "skintoken.py").read_text(encoding="utf-8")
    assert "skintoken_rig" in (root / "workflows.py").read_text(encoding="utf-8")
    assert preflight.item("x", "tools", "l", "warn", "d", "f", ["mesh", "rig"])["needed_for"] == ["mesh", "rig"]


def test_the_restart_line_is_plain_words():
    text = guidance.NODE_PACK["updated"]
    assert "Restart ComfyUI" in text and text.isascii()
