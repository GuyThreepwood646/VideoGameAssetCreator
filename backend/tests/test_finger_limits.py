"""The finger limits the Unity importer writes into the Humanoid description (vgac/fingerlimits.py, stages/export.py _write_finger_limits).

Unity is not run here: these tests check the data the importer would read (the same JSON VgacImport.LoadFingerLimits parses)."""
from __future__ import annotations

import json
from types import SimpleNamespace

from vgac import fingerlimits as fl
from vgac.stages import export, rig

# Unity's HumanTrait.BoneName strings for the 24 non-thumb finger joints (the importer matches on these)
UNITY_FINGER_BONES = {f"{s} {f} {j}" for s in ("Left", "Right") for f in ("Index", "Middle", "Ring", "Little") for j in ("Proximal", "Intermediate", "Distal")}


def test_the_default_cap_is_the_rig_steps_cap():
    assert fl.DEFAULT_CAP == rig.FINGER_CAP_DEFAULT == 40.0


def test_twenty_four_finger_joints_get_a_symmetric_range_of_the_cap_and_the_thumb_is_left_alone():
    lim = fl.finger_limits(40)
    assert len(lim) == 24 and {x["bone"] for x in lim} == UNITY_FINGER_BONES
    assert not [x for x in lim if "Thumb" in x["bone"]]
    for x in lim:
        assert x["min"] == [0.0, -fl.SPREAD, -40.0] and x["max"] == [0.0, fl.SPREAD, 40.0]
    assert fl.finger_limits(55)[0]["max"][2] == 55.0


def test_the_document_is_what_the_importer_reads():
    d = json.loads(json.dumps(fl.document(40)))
    assert d["version"] == 1 and d["finger_cap"] == 40.0 and len(d["limits"]) == 24
    for x in d["limits"]:  # the keys and shapes VgacImport.FingerLimitDoc and FingerLimitEntry parse
        assert set(x) == {"bone", "min", "max"} and len(x["min"]) == 3 and len(x["max"]) == 3 and all(isinstance(v, float) for v in x["min"] + x["max"])


def test_the_cap_comes_from_the_rigs_setting():
    assert fl.cap_from_params({}) == 40.0 and fl.cap_from_params(None) == 40.0  # an older rig without the setting: the default
    assert fl.cap_from_params({"finger_cap": 55}) == 55.0
    assert fl.cap_from_params({"finger_cap": 0}) is None and fl.cap_from_params({"finger_cap": None}) is None and fl.cap_from_params({"finger_cap": False}) is None
    assert fl.cap_from_params({"finger_cap": "x"}) == 40.0


def _sc(tmp_path, params):
    rig_v = SimpleNamespace(metrics={"bones": 52}, params=params)
    return SimpleNamespace(dir=tmp_path, platform="pc_mid", category={"rig_engine": "skintoken", "size_m": 1.7}, stage_version=lambda s: rig_v,
                           settings=SimpleNamespace(clips_libraries={}))


def test_the_export_writes_the_file_for_a_humanoid_and_passes_it_to_the_importer(tmp_path, monkeypatch):
    monkeypatch.setattr(export, "_is_humanoid", lambda sc: True)
    sc = _sc(tmp_path, {"finger_cap": 40.0})
    args, flags, _ = export._unity_args(sc, {}, "w1")
    assert args["-vgacFingerLimits"] == tmp_path / "finger_limits.json" and "-vgacHumanoid" in flags
    d = json.loads((tmp_path / "finger_limits.json").read_text())
    assert d["finger_cap"] == 40.0 and len(d["limits"]) == 24


def test_no_file_for_a_creature_or_when_the_cap_was_turned_off(tmp_path, monkeypatch):
    monkeypatch.setattr(export, "_is_humanoid", lambda sc: False)
    assert "-vgacFingerLimits" not in export._unity_args(_sc(tmp_path, {"finger_cap": 40.0}), {}, "wolf")[0]
    monkeypatch.setattr(export, "_is_humanoid", lambda sc: True)
    off = tmp_path / "off"
    off.mkdir()
    assert "-vgacFingerLimits" not in export._unity_args(_sc(off, {"finger_cap": 0}), {}, "w1")[0] and not (off / "finger_limits.json").exists()


def test_the_importer_version_is_bumped_so_projects_get_the_new_copy():
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "unity-sandbox" / "Assets" / "Editor" / "VgacImport.cs"
    assert export.importer_version(src) >= 19
    text = src.read_text(encoding="utf-8")
    assert "-vgacFingerLimits" in text and "LoadFingerLimits" in text and "fingerLimitedBones" in text
