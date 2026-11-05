"""docs/GREAT-ASSETS-PLAN.md Phase 0 and Phase 2, backend half: the check contract (2.1), plain option labels (2.2),
knobs for some categories only (2.3), and G12 with G14 and light G13 (size on the category's axis, facing, size from
the description)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from vgac import guidance, quality, reference
from vgac.stages import prep as prep_stage

BACKEND = Path(__file__).resolve().parent.parent

# A sample value and limit per check that make it pass and fail (value, limit, passed).
SAMPLES = {
    "triangle_budget": ((18000, 20000), (26000, 20000)),
    "manifold": ((0, 4), (200, 4)),
    "watertight": ((0, 4), (90, 4)),
    "degenerate_faces": ((0, 0), (3, 0)),
    "size_m": ((1.8, 1.8), (1.2, 1.8)),
    "facing": ((0, 0), (90, 0)),
    "uv_set": ((1, 1), (0, 1)),
    "lod_count": ((4, 4), (2, 4)),
    "collider_present": ((54, ">0"), (0, ">0")),
    "collider_vertices": ((54, 255), (400, 255)),
    "bones": ((31, ">0"), (0, ">0")),
    "single_root": ((1, 1), (2, 1)),
    "weight_coverage": ((1.0, 0.99), (0.9, 0.99)),
    "max_influences": ((4, 4), (6, 4)),
    "weights_normalised": ((0, 0), (12, 0)),
    "clips": ((["idle", "walk"], ["idle", "walk"]), (None, ["idle", "walk"])),
    "silhouette_iou": ((0.8, 0.6), (0.4, 0.6)),
    "unity_import": ((True, True), (False, True)),
    "unity_humanoid_avatar": ((46, True), (0, True)),
    "seam_error": ((0.01, 0.05), (0.2, 0.05)),
    "hitbox_cover": ((0.9, 0.85), (0.6, 0.85)),
    "hitbox_overreach": ((1.5, 2.75), (3.4, 2.75)),
    "hitbox_fit": ((True, True), (False, True)),
    "legs_moved": ((8, 8), (4, 8)),
    "materials_connected": ((True, True), (False, True)),
    "clips_loop": ((True, True), ("walk", True)),
    "library_clips": ((15, ">0"), (0, ">0")),
    "controller_ready": ((True, True), (False, True)),
    "rig_restore": ((True, True), (False, True)),
    "wings_found": ((True, True), (False, True)),
    "collider_triangles": ((236, 255), (300, 255)),
    "collider_count": ((3, 8), (12, 8)),
    "openings_clear": ((0, 0), (1, 0)),
    "bake_matches": ((1.0, 0.99), (0.45, 0.99)),
    "texture_padding": ((8, 8), (1, 8)),
    "atlas_used": ((0.57, 0.5), (0.28, 0.5)),
    "baked_light": ((0.1, 0.2), (0.4, 0.2)),
    "albedo_range": ((0.004, 0.01), (0.18, 0.01)),
    "metal_mixed": ((0.02, 0.05), (0.61, 0.05)),
    "grid_fit": (([2.0, 0.3, 3.0], 1), ([2.4, 0.3, 3.0], 1)),
    "tiles_cleanly": ((0.0, 0.5), (45.0, 0.5)),
    "look_applied": (("about 1,250 triangles, flat faces, 512 px textures", "abc123"), ("about 1,250 triangles", "abc123")),
    # Start from your own images: concept readiness and material photos.
    "one_subject": ((1, 1), (3, 1)),
    "in_frame": ((0.05, 0.01), (0.0, 0.01)),
    "big_enough": ((900, 400), (220, 400)),
    "arms_away": ((0.4, 0.2), (0.0, 0.2)),
    "even_lighting": ((0.05, 0.25), (0.4, 0.25)),
    "straight_on": ((1.1, 2.0), (3.5, 2.0)),
}


def _described(name, value, limit, passed, vars=None):
    c = {"name": name, "id": name, "passed": passed, "ok": passed, "value": value, "limit": limit}
    if vars:
        c["vars"] = vars
    return guidance.describe_check(c)


# --- 2.1 check contract ----------------------------------------------------------------


def test_every_check_guide_entry_has_ok_and_fail_and_a_severity():
    assert set(SAMPLES) == set(guidance.CHECK_GUIDE)
    for name, g in guidance.CHECK_GUIDE.items():
        assert g.get("ok") and g.get("fail"), name
        assert g["severity"] in ("block", "warn"), name
        if "fix" in g:
            assert g.get("fix_label"), name


def test_passing_check_never_carries_the_fail_sentence():
    for name, ((pv, pl), (fv, fl)) in SAMPLES.items():
        ok = _described(name, pv, pl, True)
        bad = _described(name, fv, fl, False)
        assert ok["help"] and bad["help"], name
        assert ok["help"] != bad["help"], name
        # The fail sentence for the passing values is never what a pass shows.
        fail_text = _described(name, pv, pl, False)["help"]
        assert ok["help"] != fail_text, name
        assert "fix" not in ok and "fix_label" not in ok, name
        assert "{" not in ok["help"] and "{" not in bad["help"], (name, ok["help"], bad["help"])


def test_existing_severities_kept():
    sev = {n: g["severity"] for n, g in guidance.CHECK_GUIDE.items()}
    assert {n for n, s in sev.items() if s == "block"} == {"triangle_budget", "lod_count", "collider_present", "bones", "clips_loop"}


def test_fix_labels_and_default_label(monkeypatch):
    labels = {"triangle_budget": "Shrink to the budget", "size_m": "Set the size", "lod_count": "Rebuild the detail levels",
              "collider_present": "Add a collision shape", "weight_coverage": "Attach the loose surface",
              "weights_normalised": "Balance the weights"}
    for name, label in labels.items():
        (_, _), (fv, fl) = SAMPLES[name]
        c = _described(name, fv, fl, False)
        assert c["fix"]["label"] == label and c["fix_label"] == label, name
        assert c["fix"]["stage"] in reference.STAGE_ORDER
    monkeypatch.setitem(guidance.CHECK_GUIDE, "test_only", {"severity": "warn", "ok": "Fine.", "fail": "Not fine.",
                                                            "fix": lambda c: {"stage": "prep", "params": {}}})
    c = _described("test_only", 1, 0, False)
    assert c["fix"]["label"] == guidance.DEFAULT_FIX_LABEL == "Fix automatically" and c["help"] == "Not fine."


def test_formatting_with_missing_vars_never_raises():
    assert guidance.format_check_text("The model is {value:m} m {axis_word}.", {"value": 1.8}) == "The model is 1.8 m ."
    assert guidance.format_check_text("{nothing:pct} and {0} and {value}", {}) == " and  and "
    assert guidance.format_check_text("broken {", {"value": 1}) == "broken {"
    c = _described("size_m", 1.8, 1.8, True)  # no vars at all
    assert c["help"].startswith("The model is 1.8 m")
    c = _described("triangle_budget", None, 20000, False)  # over cannot be computed
    assert "?" in c["help"]


def test_number_formats():
    t = guidance.format_check_text
    assert t("{value:n}", {"value": 26000}) == "26,000"
    assert t("{value}", {"value": 1234567}) == "1,234,567"
    assert t("{value:pct}", {"value": 0.991}) == "99.1%" and t("{value:pct}", {"value": 0.99}) == "99%"
    assert t("{value:m}", {"value": 4.4999}) == "4.5" and t("{value:m}", {"value": 1.8}) == "1.8" and t("{value:m}", {"value": 0.123}) == "0.12"
    assert t("{value:dims}", {"value": [1000, 2048]}) == "1000 x 2048"
    c = _described("triangle_budget", 26000, 20000, False)
    assert c["help"] == "The model has 6,000 more triangles than your platform allows (26,000 of 20,000); it would run slower than it should."
    assert _described("triangle_budget", 18000, 20000, True)["help"] == "The model's 18,000 triangles fit your platform's budget of 20,000."


def test_pattern_checks_have_both_sentences():
    ok = _described("lod2_ratio", 0.25, 0.25, True)
    bad = _described("lod2_ratio", 0.6, 0.25, False)
    assert ok["help"] == "Detail level 2 keeps 25% of the triangles." and bad["help"].endswith("25% expected.")
    assert _described("texture_base_color_pow2", [2048, 2048], "power of two", True)["help"] == "The base color texture is a power-of-two size."
    assert _described("texture_normal_square", [1000, 2048], "square", False)["help"] == "The normal texture is 1000 x 2048 px; it must be square."
    assert _described("texture_ao_budget", 4096, 2048, False)["help"] == "The ao texture is 4,096 px; the platform allows 2,048 px."
    unknown = _described("not_a_check", 1, 2, False)
    assert unknown["severity"] == "warn" and "help" not in unknown


def test_quality_check_carries_outcome_sentence():
    good = quality.check("weight_coverage", True, 1.0, 0.99)
    bad = quality.check("weight_coverage", False, 0.9, 0.99)
    assert good["help"] == "100% of the surface follows the bones."
    assert bad["help"].startswith("Only 90% of the surface") and bad["fix"] == {"stage": "rig", "params": {}, "label": "Attach the loose surface"}


# --- 2.2 option labels, 2.3 knobs for some categories -----------------------------------


def test_select_knobs_carry_plain_option_labels():
    stages = {s["id"]: {p["name"]: p for p in s["adjust_schema"]} for s in reference.stages()}
    expect = {("mesh", "engine"), ("mesh", "input_view"), ("mesh", "texture_size"), ("mesh", "upsample"), ("prep", "pivot"),
              ("prep", "face_yaw"), ("rig", "template"), ("export", "formats"), ("export", "clips_library"),
              ("turnaround", "views"), ("export", "collider")}
    for stage, name in expect:
        p = stages[stage][name]
        labels = p["option_labels"]
        assert set(labels) == {str(o) for o in p["options"]}, (stage, name)
        assert all(isinstance(v, str) and v for v in labels.values())
    assert stages["export"]["clips_library"]["option_labels"]["ual1"] == "Standard human moves (46 animations)"
    assert stages["rig"]["template"]["option_labels"]["mixamo"] == "Standard human skeleton"
    assert stages["export"]["collider"]["option_labels"]["hull"] == "One wrapped shape"
    assert stages["prep"]["face_yaw"]["option_labels"]["90"] == "Turned a quarter to the left"
    # The concept model knob keeps its own labels (models.yaml); numbers and strings never carry labels.
    assert "option_labels" not in stages["concept"]["model"] and "option_labels" not in stages["prep"]["smooth_angle"]


def test_knobs_for_some_categories():
    prep = {p["name"]: p for p in next(s for s in reference.stages() if s["id"] == "prep")["adjust_schema"]}
    assert "height_m" not in prep
    assert "material" not in prep["size_m"]["categories"] and "material" not in prep["face_yaw"]["categories"]
    assert set(prep["size_m"]["by_category"]) == set(prep["size_m"]["categories"])
    assert prep["size_m"]["by_category"]["vehicle"] == {"axis": "horizontal", "default": 4.5, "word": "long", "label": "How long is it?",
                                                         "hint": "In metres, front to back. A car is about 4.5 m; a tank about 7 m."}
    assert prep["face_yaw"]["default"] == "auto"
    # Accepted and dropped for a category it does not apply to; checked for one it does.
    assert reference.validate_params("prep", {"size_m": 2.0, "face_yaw": 90}, category="material") == {}
    assert reference.validate_params("prep", {"size_m": 2.0, "face_yaw": "90"}, category="vehicle") == {"size_m": 2.0, "face_yaw": 90}
    assert reference.validate_params("prep", {"face_yaw": "auto"}) == {"face_yaw": "auto"}
    with pytest.raises(ValueError):
        reference.validate_params("prep", {"face_yaw": 45}, category="vehicle")
    with pytest.raises(ValueError):
        reference.validate_params("export", {"collider": True})
    assert reference.validate_params("mesh", {"texture_size": "2048"}) == {"texture_size": 2048}
    assert reference.validate_params("turnaround", {"views": ["front", "back"]}) == {"views": ["front", "back"]}


# --- G12, G14, light G13 -----------------------------------------------------------------


def test_every_category_yaml_has_a_size_axis():
    for p in sorted((BACKEND / "vgac" / "data" / "categories").glob("*.yaml")):
        c = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert "size_axis" in c, p.stem
        if c["id"] != "material":
            assert c["size_axis"] in reference.SIZE_AXES and c["size_label"] and c["size_hint"] and c["size_word"] in ("tall", "long", "across"), p.stem
    axes = {c["id"]: reference.size_spec(c["id"])["axis"] for c in reference.categories()}
    assert axes["humanoid"] == "height" and axes["quadruped"] == "horizontal" and axes["vehicle"] == "horizontal"
    assert axes["serpent"] == "longest" and axes["prop_rigid"] == "longest" and axes["winged"] == "horizontal"
    assert reference.category("quadruped")["size_m"] == 1.5 and reference.category("vehicle")["size_m"] == 4.5


@pytest.mark.parametrize("text,size,axis,word", [
    ("A mossy stone wall segment, two metres wide, with a crumbling top edge", 2.0, "horizontal", "across"),
    ("a 6 ft tall troll", 1.829, "height", "tall"),
    ("a 4.5 m long car", 4.5, "horizontal", "long"),
    ("an oak tree twenty feet high", 6.096, "height", "tall"),
    ("a 90 cm across shield", 0.9, "horizontal", "across"),
    ("a 2 meter long python", 2.0, "horizontal", "long"),
    ("a seventeen metres tall statue", 17.0, "height", "tall"),
    ("a 10m tower, 3 m wide", 3.0, "horizontal", "across"),
    ("a 3 m wall", 3.0, None, None),
])
def test_parse_size(text, size, axis, word):
    found = reference.parse_size(text)
    assert found is not None and found["size_m"] == size and found["axis"] == axis and found["word"] == word


def test_parse_size_ignores_other_numbers():
    for text in ("a 2mm wire", "a knight with 3 swords", "no size here", "", None, "a 500 m tall tower", "about 2 miles long"):
        assert reference.parse_size(text) is None, text
    assert reference.parse_size("a chest with 2 cm iron bands", default_m=0.8) is None
    assert reference.parse_size("a chest 2 m wide with 2 cm iron bands", default_m=0.8)["size_m"] == 2.0


def test_axis_extent_and_size_check():
    dims = [1.0, 4.5, 1.2]  # Blender x, y, z (z up)
    assert quality.axis_extent(dims, "height") == 1.2 and quality.axis_extent(dims, "horizontal") == 4.5
    assert quality.axis_extent([0.2, 0.3, 2.0], "longest") == 2.0
    ok = quality.geometry_checks({"dims_m": [1.0, 4.4, 1.2]}, None, 4.5, size_axis="horizontal", axis_word="long")[0]
    assert ok["name"] == "size_m" and ok["passed"] and ok["help"] == "The model is 4.4 m long, as intended."
    bad = quality.geometry_checks({"dims_m": [1.0, 4.0, 4.5]}, None, 4.5, size_axis="horizontal", axis_word="long")[0]
    assert not bad["passed"] and bad["help"] == "The model is 4 m long; it should be 4.5 m."
    assert bad["fix"] == {"stage": "prep", "params": {"size_m": 4.5, "size_axis": "horizontal"}, "label": "Set the size"}
    # The old call shape (third argument the height) still measures the height.
    legacy = quality.geometry_checks({"dims_m": [0.8, 0.4, 1.8]}, 20000, 1.8)
    assert [c["name"] for c in legacy] == ["size_m"] and legacy[0]["help"] == "The model is 1.8 m tall, as intended."


def test_facing_turns():
    # The knight's audit: offset 180 with sign -1 is the mirrored twin of offset 0, so no turn.
    assert quality.best_turn(180, -1) == 0 and quality.best_turn(0, 1) == 0
    assert quality.best_turn(90, 1) == 270 and quality.best_turn(345, 1) == 15 and quality.best_turn(150, -1) == 30
    assert quality.auto_turn(15) == 0 and quality.auto_turn(270) == 270 and quality.auto_turn(255) == 270
    assert quality.auto_turn(30) == 0 and quality.auto_turn(None) == 0
    assert quality.facing_check(30, 0)["passed"]
    bad = quality.facing_check(60, 0)
    assert not bad["passed"] and bad["fix"] == {"stage": "prep", "params": {"face_yaw": 90}, "label": "Turn it to face forward"}
    assert bad["help"] == "The model may face the wrong way: it matches the front picture best when turned 60 degrees."
    assert quality.facing_check(270, 270)["help"] == "The front of the model faces forward in the game."


def test_prep_resolves_size_and_facing():
    r = prep_stage.resolve_size
    assert r("vehicle", {}, "a boxy buggy") == {"size_m": 4.5, "size_axis": "horizontal", "size_word": "long", "size_source": "category"}
    assert r("humanoid", {}, "a 6 ft tall troll")["size_m"] == 1.829
    wall = r("environment", {}, "A mossy stone wall segment, two metres wide")
    assert wall == {"size_m": 2.0, "size_axis": "horizontal", "size_word": "across", "size_source": "description"}
    assert r("vehicle", {"size_m": 7.0}, "a 4.5 m long car") == {"size_m": 7.0, "size_axis": "horizontal", "size_word": "long", "size_source": "chosen"}
    # height_m from an older version still means the height.
    assert r("quadruped", {"height_m": 1.2}, "") == {"size_m": 1.2, "size_axis": "height", "size_word": "tall", "size_source": "chosen"}
    # A size fix carries the axis it measured.
    assert r("quadruped", {"size_m": 1.5, "size_axis": "horizontal"}, "")["size_word"] == "long"

    f = prep_stage.resolve_facing
    wolf = reference.category("quadruped")
    assert f(wolf, "auto", None) == {"face_yaw_applied": 0, "face_best_turn": None, "facing_source": "as built"}
    assert f(wolf, "auto", {"silhouette_offset": 90, "silhouette_sign": 1, "silhouette_iou": 0.8})["face_yaw_applied"] == 270
    low = f(wolf, "auto", {"silhouette_offset": 90, "silhouette_sign": 1, "silhouette_iou": 0.3})
    assert low["face_best_turn"] is None and low["face_yaw_applied"] == 0
    assert f(wolf, 180, {"silhouette_offset": 0, "silhouette_sign": 1, "silhouette_iou": 0.8}) == {"face_yaw_applied": 180, "face_best_turn": 0, "facing_source": "chosen"}

    report = {"dims_m": [0.42, 1.5, 1.21], "triangles": 19000}
    checks = prep_stage.prep_checks(report, {"size_m": 1.5, "size_axis": "horizontal", "size_word": "long"},
                                    {"face_best_turn": 0, "face_yaw_applied": 180}, "quadruped", 20000)
    by = {c["name"]: c for c in checks}
    assert by["size_m"]["passed"] and by["size_m"]["help"] == "The model is 1.5 m long, as intended."
    assert not by["facing"]["passed"] and by["facing"]["fix"]["params"] == {"face_yaw": 0}
    # An old version with only height_m is judged on the height.
    old = prep_stage.prep_checks({"dims_m": [0.42, 1.49, 1.2]}, {"height_m": 1.2}, {}, "quadruped", None)
    assert old[0]["passed"] and old[0]["help"] == "The model is 1.2 m tall, as intended."


def test_adjust_merge_rules():
    m = prep_stage._merge
    parent = {"size_m": 2.0, "size_axis": "horizontal", "size_word": "across", "height_m": 1.2, "face_yaw": "auto"}
    assert m(parent, {"symmetric": False}) == {**parent, "symmetric": False}
    assert m(parent, {"size_m": 3.0}) == {"size_m": 3.0, "face_yaw": "auto"}
    assert m(parent, {"size_m": 3.0, "size_axis": "height"})["size_axis"] == "height"


def _blender() -> str | None:
    exe = os.environ.get("VGAC_BLENDER")
    if not exe:
        cfg = BACKEND / "config.yaml"
        if cfg.is_file():
            exe = (yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}).get("blender_exe")
    if exe and Path(exe).is_file():
        return exe
    return shutil.which("blender")


def test_blender_prep_scales_a_vehicle_box_by_length(tmp_path):
    exe = _blender()
    if not exe:
        pytest.skip("Blender not installed")
    trimesh = pytest.importorskip("trimesh")
    src = tmp_path / "box.glb"
    trimesh.creation.box(extents=[1.0, 1.0, 4.0]).export(src)  # glTF: 1 wide, 1 tall, 4 long
    out = tmp_path / "out.glb"
    script = BACKEND / "vgac" / "blender" / "prep.py"
    spec = reference.size_spec("vehicle")
    subprocess.run([exe, "-b", "--factory-startup", "--python", str(script), "--", str(src), str(out), "box",
                    "--size", str(spec["default"]), "--size-axis", spec["axis"], "--yaw", "90"],
                   check=True, capture_output=True, timeout=300)
    report = json.loads((tmp_path / "out_report.json").read_text())
    assert abs(report["size_extent_m"] - 4.5) <= 0.03 * 4.5
    x, y, z = report["dims_m"]
    # Turned a quarter: the length now lies along Blender X; the height is untouched by the turn.
    assert abs(x - 4.5) <= 0.03 * 4.5 and abs(y - 1.125) < 0.01 and abs(z - 1.125) < 0.01 and report["yaw_deg"] == 90
    checks = quality.geometry_checks(report, None, spec["default"], size_axis=spec["axis"], axis_word=spec["word"])
    size = next(c for c in checks if c["name"] == "size_m")
    assert size["passed"] and size["help"] == "The model is 4.5 m long, as intended."
