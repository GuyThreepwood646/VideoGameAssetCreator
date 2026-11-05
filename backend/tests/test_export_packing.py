"""Lane A, Phase 0 and 1 of docs/GREAT-ASSETS-PLAN.md: engine texture packs (G01, G02), the importer refresh rule (2.6),
the Unity checks and the "Using it in Unity" lines (G26, G32, G33)."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

from vgac.stages import export

REPO = Path(__file__).resolve().parents[2]
IMPORTER = REPO / "unity-sandbox" / "Assets" / "Editor" / "VgacImport.cs"


def _grey(path: Path, value: int, size=(8, 8), mode="L") -> Path:
    Image.new(mode, size, value).save(path)
    return path


# --- packing ---------------------------------------------------------------------------------------------------------

def test_pack_round_trip_roughness_to_smoothness(tmp_path):
    base = tmp_path / "base_color.png"
    Image.new("RGB", (16, 8), (200, 100, 50)).save(base)
    rough = _grey(tmp_path / "roughness.png", round(0.3 * 255), size=(4, 2))  # smaller: resized to the base colour
    metal = _grey(tmp_path / "metallic.png", 255, size=(16, 8))
    ao = _grey(tmp_path / "ao.png", 90, size=(16, 8))
    out = export.pack_texture_maps({"base_color": base, "roughness": rough, "metallic": metal, "ao": ao}, tmp_path / "out", "Knight")
    assert set(out) == {"metallic_smoothness", "orm"}
    assert out["metallic_smoothness"].name == "T_Knight_MetallicSmoothness.png" and out["orm"].name == "T_Knight_ORM.png"
    ms = Image.open(out["metallic_smoothness"])
    assert ms.mode == "RGBA" and ms.size == (16, 8)
    px = np.asarray(ms)
    assert set(np.unique(px[..., 3])) <= {178, 179}  # roughness 0.3 -> smoothness 0.7
    assert (px[..., :3] == 255).all()
    orm = np.asarray(Image.open(out["orm"]))
    assert orm.shape == (8, 16, 3)
    assert (orm[..., 0] == 90).all() and set(np.unique(orm[..., 1])) <= {76, 77} and (orm[..., 2] == 255).all()


def test_pack_defaults_and_skips(tmp_path):
    # Roughness only: metallic defaults to 0, occlusion to 255.
    rough = _grey(tmp_path / "roughness.png", 200)
    out = export.pack_texture_maps({"roughness": rough, "metallic": None, "ao": tmp_path / "missing.png"}, tmp_path, "A")
    px = np.asarray(Image.open(out["metallic_smoothness"]))
    assert (px[..., 0] == 0).all() and (px[..., 3] == 55).all()
    orm = np.asarray(Image.open(out["orm"]))
    assert (orm[..., 0] == 255).all() and (orm[..., 1] == 200).all() and (orm[..., 2] == 0).all()
    # Occlusion only: no metal-and-shine pack (nothing to say about metal or shine), but an ORM pack.
    ao = _grey(tmp_path / "ao.png", 30)
    out = export.pack_texture_maps({"ao": ao}, tmp_path / "b", "B")
    assert set(out) == {"orm"}
    # No surface maps at all: nothing.
    assert export.pack_texture_maps({"base_color": None}, tmp_path / "c", "C") == {}


def test_pack_reads_16_bit_maps(tmp_path):
    p = tmp_path / "roughness.png"
    Image.fromarray(np.full((4, 4), 65535, dtype=np.uint16)).save(p)
    out = export.pack_texture_maps({"roughness": p}, tmp_path, "R")
    assert (np.asarray(Image.open(out["orm"]))[..., 1] == 255).all()


# --- importer refresh rule (2.6) -------------------------------------------------------------------------------------

def _cs(path: Path, version: str | None) -> Path:
    body = "public static class VgacImport {\n"
    if version is not None:
        body += f'    public const string Version = "{version}";\n'
    path.write_text(body + "}\n", encoding="utf-8")
    return path


def test_sandbox_importer_has_a_version():
    v = export.importer_version(IMPORTER)
    assert v is not None and v >= 2


def test_sync_importer_rules(tmp_path):
    src = _cs(tmp_path / "VgacImport.cs", "3")
    proj = tmp_path / "proj" / "Assets" / "Editor"
    assert export.sync_importer(src, proj) == {"action": "copied", "version": 3, "previous": None}
    assert export.importer_version(proj / "VgacImport.cs") == 3
    assert export.sync_importer(src, proj)["action"] == "current"
    _cs(proj / "VgacImport.cs", None)  # an old copy from before the constant existed
    assert export.sync_importer(src, proj) == {"action": "updated", "version": 3, "previous": None}
    _cs(proj / "VgacImport.cs", "2")
    assert export.sync_importer(src, proj) == {"action": "updated", "version": 3, "previous": 2}
    _cs(proj / "VgacImport.cs", "7")  # newer than ours: left alone
    r = export.sync_importer(src, proj)
    assert r["action"] == "kept_newer" and export.importer_version(proj / "VgacImport.cs") == 7
    assert export.sync_importer(tmp_path / "nope.cs", proj)["action"] == "no_source"


# --- Unity checks and how-to (G01, G26, G32, G33) --------------------------------------------------------------------

KNIGHT = {
    "ok": True, "humanoidValid": True, "avatar_mapping": "explicit", "clipCount": 46, "prefab": "Assets/VGAC/k/k.prefab",
    "texturesAssigned": ["base_color", "normal", "ao", "metallic_smoothness"],
    "clips": [{"name": "Rig|Idle_Loop", "loop": True}, {"name": "Rig|Walk_Loop", "loop": True}, {"name": "Rig|Jog_Fwd_Loop", "loop": True},
              {"name": "Rig|Sword_Attack", "loop": False}],
    "controller": {"states": ["Locomotion", "Attack", "Hit", "Dead", "More moves"],
                   "parameters": ["Speed:Float", "Attack:Trigger", "Hit:Trigger", "Die:Trigger"],
                   "roles": ["Idle=Rig|Idle_Loop", "Walk=Rig|Walk_Loop", "Run=Rig|Jog_Fwd_Loop", "Attack=Rig|Sword_Attack"]},
}


def _by_id(checks):
    return {c["id"]: c for c in checks}


def test_unity_checks_pass_for_a_good_import():
    c = _by_id(export.unity_checks(KNIGHT, humanoid=True, rigged=True))
    assert set(c) == {"materials_connected", "clips_loop", "controller_ready", "unity_humanoid_avatar"}
    assert all(x["passed"] for x in c.values())
    assert c["unity_humanoid_avatar"]["value"] == 46


def test_unity_checks_name_what_is_wrong():
    bad = dict(KNIGHT, texturesAssigned=["base_color", "normal", "ao"], humanoidValid=False,
               clips=[{"name": "Rig|Idle_Loop", "loop": True}, {"name": "Rig|Walk_Loop", "loop": False}],
               controller={"states": ["idle", "walk"], "parameters": [], "roles": ["Idle=Rig|Idle_Loop", "Walk=Rig|Walk_Loop"]})
    c = _by_id(export.unity_checks(bad, humanoid=True, rigged=True))
    assert not c["materials_connected"]["passed"]
    assert c["materials_connected"]["vars"] == {"missing": "metal and shine", "effect": "like plastic"}
    assert not c["clips_loop"]["passed"] and c["clips_loop"]["value"] == "walk"
    assert not c["controller_ready"]["passed"]
    assert not c["unity_humanoid_avatar"]["passed"] and c["unity_humanoid_avatar"]["value"] == 0


def test_unity_checks_for_a_prop():
    chest = {"ok": True, "texturesAssigned": ["base_color", "normal", "ao", "metallic_smoothness"], "clips": [], "controller": {}}
    assert [x["id"] for x in export.unity_checks(chest, humanoid=False, rigged=False)] == ["materials_connected"]


def test_unity_howto_names_only_the_moves_that_exist():
    lines = export.unity_howto("Assets/VGAC/k/k.prefab", KNIGHT["controller"])
    assert lines == [
        "Drag the prefab from Assets/VGAC/k/k.prefab into your scene.",
        'In your movement script, set the speed every frame: animator.SetFloat("Speed", speed);',
        'For one-off moves, call animator.SetTrigger("Attack"); (or "Hit", "Die").',
    ]
    assert export.unity_howto("x", {"states": [], "parameters": []}) == []
    assert export.unity_howto("x", None) == []


def test_speed_scale_for_creatures_only():
    rig = SimpleNamespace(metrics={"bones": 30}, params={})
    prep = SimpleNamespace(metrics={}, params={"size_m": 1.5})
    sc = SimpleNamespace(stage_version=lambda s: {"rig": rig, "prep": prep}[s], category={"size_m": 1.2})
    assert abs(export._speed_scale(sc, humanoid=False) - 1.5 / 1.8) < 1e-9
    assert export._speed_scale(sc, humanoid=True) is None
    prep.params = {}
    assert abs(export._speed_scale(sc, humanoid=False) - 1.2 / 1.8) < 1e-9
    rig.metrics = {"bones": 0}
    assert export._speed_scale(sc, humanoid=False) is None


def test_unity_args_pass_hitboxes_and_clip_info(tmp_path):
    rig = SimpleNamespace(metrics={"bones": 30}, params={})
    sc = SimpleNamespace(dir=tmp_path, category={"rig_engine": "skintoken_generic", "size_m": 1.2},
                         stage_version=lambda s: rig, settings=SimpleNamespace(clips_libraries={}))
    args, flags, _ = export._unity_args(sc, {}, "wolf")
    assert "-vgacHitboxes" not in args and "-vgacClipInfo" not in args
    (tmp_path / "hitboxes.json").write_text("{}")
    (tmp_path / "anim_clips.json").write_text("{}")
    args, flags, _ = export._unity_args(sc, {"movement_shape": "box"}, "wolf")
    assert args["-vgacHitboxes"] == tmp_path / "hitboxes.json" and args["-vgacMovementShape"] == "box"
    assert args["-vgacClipInfo"] == tmp_path / "anim_clips.json"
    assert export._unity_args(sc, {"movement_shape": "bogus"}, "wolf")[0]["-vgacMovementShape"] == "auto"
    assert args["-vgacSpeedScale"] == f"{1.2 / 1.8:.3f}"
