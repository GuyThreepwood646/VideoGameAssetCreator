"""WP-69: the stiff points are a rig setting, so a donor rig with other points is not reused (rig.reuse_differences), and a
run finds the points it applies from its own parts file, else the rig it was adjusted from, else the selected rig."""
import json
from pathlib import Path
from types import SimpleNamespace

from vgac.stages import rig


def _parts(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"parts": rows}), encoding="utf-8")
    return path


class _FakeSc:
    """The few StageContext members the stiff helpers read."""

    def __init__(self, own: Path | None, parent: Path | None, selected: Path | None = None):
        self._own = own
        self.parent = SimpleNamespace(stage="rig", id="p") if parent is not None else None
        self._parent_file = parent
        self._selected = selected
        self.settings = None
        self.asset = SimpleNamespace(id="a1")
        self.version = SimpleNamespace(id="v1", params={})
        self.db = None
        self.params_set: dict = {}

    def file(self, key: str):
        return self._own if key == "parts" else None

    def set_params(self, **values):
        self.params_set.update(values)
        self.version.params.update(values)


def test_a_changed_stiff_point_is_a_differing_setting():
    mine = {"template_label": "mixamo", "stiff_points": [{"name": "pack", "locations": [[0.0, 1.1, -0.25]]}]}
    donor = {"template_label": "mixamo"}
    assert rig.reuse_differences(mine, donor) == ["stiff_points"]
    moved = {"template_label": "mixamo", "stiff_points": [{"name": "pack", "locations": [[0.0, 1.2, -0.25]]}]}
    assert rig.reuse_differences(mine, moved) == ["stiff_points"]
    assert rig.reuse_differences(mine, dict(mine)) == []


def test_no_points_on_either_side_is_the_same_setting():
    assert rig.reuse_differences({"stiff_points": None, "template_label": "x"}, {"template_label": "x"}) == []


def test_points_come_from_own_parts_before_the_parent(tmp_path):
    own = _parts(tmp_path / "own" / "parts.json", [{"name": "pack", "kind": "stiff", "locations": [[0, 1, 0]]}])
    par = _parts(tmp_path / "par" / "parts.json", [{"name": "pack", "kind": "stiff", "locations": [[9, 9, 9]]}])
    sc = _FakeSc(own=own, parent=par)
    assert rig.stiff_points_for(sc) == [{"name": "pack", "locations": [[0.0, 1.0, 0.0]]}]


def test_points_come_from_the_parent_rig_when_this_version_has_none(tmp_path, monkeypatch):
    par = _parts(tmp_path / "par" / "parts.json", [{"name": "pack", "kind": "stiff", "locations": [[0.12345, 1, 0]]}])
    sc = _FakeSc(own=None, parent=par)
    monkeypatch.setattr(rig.library, "version_file", lambda v, key, settings: par if v is sc.parent or v.id == "p" else None)
    assert rig.stiff_points_for(sc) == [{"name": "pack", "locations": [[0.1235, 1.0, 0.0]]}]


def test_record_sets_the_setting_and_clears_it_when_there_are_no_points(tmp_path, monkeypatch):
    sc = _FakeSc(own=None, parent=None)
    monkeypatch.setattr(rig.library, "version_file", lambda v, key, settings: None)
    monkeypatch.setattr(rig, "stiff_parts_path", lambda s: None)
    assert rig.record_stiff_points(sc) == []
    assert sc.params_set == {"stiff_points": None}


# --- the moves rerun's base (rig._moves_base, WP-69 review) -----------------------------------------------------

import asyncio  # noqa: E402



def _base_case(tmp_path, monkeypatch, pre_text):
    """A parent rig dir holding rig_before_worn_region.glb (the pre-pass copy), and this version's rig GLB holding the
    pass (as the copied parent file does). Returns (sc, parent, rig_glb)."""
    parent_dir = tmp_path / "parent"
    parent_dir.mkdir()
    if pre_text is not None:
        (parent_dir / "rig_before_worn_region.glb").write_text(pre_text, encoding="utf-8")
    rig_glb = tmp_path / "child" / "rig.glb"
    rig_glb.parent.mkdir()
    rig_glb.write_text("PASSED", encoding="utf-8")
    monkeypatch.setattr(rig.library, "version_dir", lambda asset, version, settings=None, create=True: parent_dir)
    sc = SimpleNamespace(asset=SimpleNamespace(id="a1"), settings=None, progress=lambda *a, **k: None)
    return sc, SimpleNamespace(id="p"), rig_glb


def test_removing_the_last_point_gives_the_pre_pass_weights(tmp_path, monkeypatch):
    # The parent applied the pass; the person removed every point, so no points are applied on the rerun.
    sc, parent, rig_glb = _base_case(tmp_path, monkeypatch, pre_text="PRE")
    asyncio.run(rig._moves_base(sc, parent, rig_glb, refix=False))
    assert rig_glb.read_text(encoding="utf-8") == "PRE"


def test_a_parent_without_the_pass_keeps_its_rig(tmp_path, monkeypatch):
    sc, parent, rig_glb = _base_case(tmp_path, monkeypatch, pre_text=None)
    asyncio.run(rig._moves_base(sc, parent, rig_glb, refix=False))
    assert rig_glb.read_text(encoding="utf-8") == "PASSED"


def test_refix_plus_moves_keeps_the_refix_result(tmp_path, monkeypatch):
    # The clean-up runs on the pre-pass base; the copy does not overwrite it afterwards.
    sc, parent, rig_glb = _base_case(tmp_path, monkeypatch, pre_text="PRE")

    async def fake_fix(sc_, path):
        path.write_text(path.read_text(encoding="utf-8") + "+FIX", encoding="utf-8")

    monkeypatch.setattr(rig, "_fix_weights_again", fake_fix)
    asyncio.run(rig._moves_base(sc, parent, rig_glb, refix=True))
    assert rig_glb.read_text(encoding="utf-8") == "PRE+FIX"
