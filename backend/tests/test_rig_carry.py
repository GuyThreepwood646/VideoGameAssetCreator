"""rig.carry (WP-27): a rig made from a shaped clean-up carries over the rig the arm was separated on, without SkinToken,
and makes again only what the cut changes (renders, joined-limb check, hit boxes, moves, stretch measure)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from vgac.stages import rig

BASE = {"template_label": "Mixamo", "group_per_vertex": 4, "leg_skin": True}


def _version(vid, stage="prep", parent=None, metrics=None, files=None, params=None, folder=None):
    v = SimpleNamespace(id=vid, stage=stage, parent_version_id=parent, metrics=metrics or {}, files=files or {},
                        params=params or {}, status="done", selected=False)
    v.folder = folder
    return v


class _FakeDb:
    def __init__(self, *versions):
        self.by_id = {v.id: v for v in versions}

    def get(self, _cls, vid):
        return self.by_id.get(vid)


@pytest.fixture
def version_files(monkeypatch):
    def version_dir(asset, version, settings=None, create=True):
        return version.folder

    def version_file(version, key, settings=None):
        rel = version.files.get(key)
        return Path(rel) if rel and Path(rel).is_file() else None

    monkeypatch.setattr(rig.library, "version_dir", version_dir)
    monkeypatch.setattr(rig.library, "version_file", version_file)


def _file(folder: Path, name: str, text: str) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text, encoding="utf-8")
    return str(folder / name)


def _case(tmp_path, *, shaped=True, carried=True, donor_params=None, run_params=None):
    """The source clean-up, the rig the arm was separated on (donor), the shaped clean-up and a new rig version on it."""
    root = _version("root", folder=tmp_path / "root")
    d_dir = tmp_path / "donor"
    donor = _version("donor", stage="rig", parent="root", params=dict(donor_params or BASE), folder=d_dir,
                     metrics={"bones": 65, "palm_down": {"L": 90}, "clips": ["walk"], "hitboxes": {"count": 13}},
                     files={"glb": _file(d_dir, "rig.glb", "donor rig"), "anim_glb": _file(d_dir, "anim.glb", "donor moves"),
                            "hitboxes": _file(d_dir, "hitboxes.json", "{}"), "rig_fix": _file(d_dir, "rig_fix.json", "{}")})
    s_dir = tmp_path / "shaped"
    files = {"glb": _file(s_dir, "prep.glb", "cut model")}
    if carried:
        files[rig.CARRY_FILE] = _file(s_dir, "shape_rig.glb", "cut model on the donor skeleton")
    metrics = {"shaped": True, "shape_of": "root", "shape_log": {"rig_version": "donor"}} if shaped else {}
    shaped_v = _version("shaped", parent="root", metrics=metrics, files=files, folder=s_dir)
    new_rig = _version("new_rig", stage="rig", parent="shaped", params=dict(run_params or BASE), folder=tmp_path / "new_rig")
    new_rig.folder.mkdir()
    sc = SimpleNamespace(db=_FakeDb(root, donor, shaped_v, new_rig), settings=None, version=new_rig, dir=new_rig.folder,
                         parent=shaped_v, platform=None, asset=SimpleNamespace(id="a", project_id="p", category="humanoid"))
    sc.iter_lineage = lambda include_self=False: iter([shaped_v, root])
    sc.lineage_file = lambda key, stages=None: None
    sc.file = lambda key: Path(new_rig.files[key]) if new_rig.files.get(key) else None
    sc.progress = lambda *a, **k: None
    sc.register = lambda key, path: new_rig.files.__setitem__(key, str(path)) or str(path)
    sc.set_metrics = lambda **v: new_rig.metrics.update(v)
    sc.set_params = lambda **v: new_rig.params.update(v)
    return sc, new_rig, donor


@pytest.fixture
def redone(monkeypatch):
    """Record the steps carry makes again instead of running Blender."""
    calls: list[str] = []

    def step(name):
        async def run(*_a, **_k):
            calls.append(name)
        return run

    for name in ("_limb_joins", "_inspect", "fit_hitboxes", "_animate", "_skin_stretch", "judge"):
        monkeypatch.setattr(rig, name, step(name))
    monkeypatch.setattr(rig, "link_textures", lambda sc: None)
    monkeypatch.setattr(rig, "library_hair", lambda sc: None)
    return calls


def test_carry_takes_the_cut_model_and_remakes_only_what_the_cut_changes(version_files, redone, tmp_path):
    sc, new_rig, donor = _case(tmp_path)
    assert asyncio.run(rig.carry(sc, "Mixamo", 4, dict(BASE))) is True
    assert redone == ["_limb_joins", "_inspect", "fit_hitboxes", "_animate", "_skin_stretch", "judge"]
    assert Path(new_rig.files["glb"]).read_text() == "cut model on the donor skeleton"
    assert Path(donor.files["glb"]).read_text() == "donor rig"  # the donor is untouched
    # the donor's moves and hit boxes are not carried (made again); its other files and metrics are
    assert "anim_glb" not in new_rig.files and "hitboxes" not in new_rig.files
    assert "rig_fix" in new_rig.files
    m = new_rig.metrics
    assert m["rig_method"] == "carry" and m["carried_of"] == "donor" and m["carry_from"] == "shaped"
    assert m["bones"] == 65 and m["palm_down"] == {"L": 90}
    assert "clips" not in m and "hitboxes" not in m and m.get("refit_of") is None


def test_carry_does_not_apply_to_a_clean_up_that_was_not_shaped(version_files, redone, tmp_path):
    sc, new_rig, _ = _case(tmp_path, shaped=False)
    assert asyncio.run(rig.carry(sc, "Mixamo", 4, dict(BASE))) is False
    assert redone == [] and "carry_why" not in new_rig.metrics


@pytest.mark.parametrize("kind, why", [
    ("fresh", "Try again"),
    ("no_file", "no weights to keep"),
    ("settings", "settings were changed"),
    ("template", "different rig template"),
])
def test_carry_falls_back_to_a_real_rig_with_a_reason(version_files, redone, tmp_path, kind, why):
    sc, new_rig, _ = _case(tmp_path, carried=kind != "no_file",
                           run_params={**BASE, "leg_skin": False} if kind == "settings" else None)
    params = {"fresh_attempt": True} if kind == "fresh" else dict(BASE)
    template = "Keep model names" if kind == "template" else "Mixamo"
    assert asyncio.run(rig.carry(sc, template, 4, params)) is False
    assert why in new_rig.metrics["carry_why"]
    assert redone == [] and "glb" not in new_rig.files
    if kind == "settings":  # the setting names stay off the page, in their own metric
        assert new_rig.metrics["carry_settings_changed"] == ["leg_skin"]
        assert "leg_skin" not in new_rig.metrics["carry_why"]


def test_carry_refuses_when_the_rig_it_was_shaped_on_is_gone(version_files, redone, tmp_path):
    sc, new_rig, donor = _case(tmp_path)
    Path(donor.files["glb"]).unlink()
    assert asyncio.run(rig.carry(sc, "Mixamo", 4, dict(BASE))) is False
    assert "is gone" in new_rig.metrics["carry_why"]
