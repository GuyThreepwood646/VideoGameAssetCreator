"""docs/GREAT-ASSETS-PLAN.md Phase 4 (G03 seam space, G05 maps baked on the final model) and Phase 5 (G07 painted-on
light, G08 physically based ranges): the texture-space maths, the light score and correction, the range checks, the
GLB image swap, the prep texture pass and its checks, and (when Blender is installed) the prep re-bake."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
import yaml
from PIL import Image

from vgac import guidance, imaging, reference
from vgac.graphs.trellis import convert_graph
from vgac.stages import mesh as mesh_stage
from vgac.stages import prep as prep_stage

BACKEND = Path(__file__).resolve().parent.parent


def _blender() -> str | None:
    import os
    import shutil

    exe = os.environ.get("VGAC_BLENDER")
    if not exe:
        cfg = BACKEND / "config.yaml"
        if cfg.is_file():
            exe = (yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}).get("blender_exe")
    if exe and Path(exe).is_file():
        return exe
    return shutil.which("blender")


# --- G03: padding, dilation, gutter ---------------------------------------------------------------


def test_padding_and_dilation_scale_with_texture_size():
    assert [imaging.unwrap_padding(s) for s in (512, 1024, 2048, 4096)] == [4, 4, 8, 16]
    assert [imaging.dilation_px(s) for s in (512, 1024, 2048, 4096)] == [8, 8, 16, 32]
    assert mesh_stage.resolve_padding({}, 2048) == 8 and mesh_stage.resolve_padding({"padding": 40}, 2048) == 16
    assert mesh_stage.resolve_padding({"padding": 3}, 4096) == 3
    g = convert_graph("in.png", "x", padding=40)
    assert g["unwrap"]["inputs"]["padding"] == 16
    assert convert_graph("in.png", "x")["unwrap"]["inputs"]["padding"] == 1  # the bakeoff graph, unchanged by default


def test_mesh_target_leaves_headroom_under_the_budget():
    assert mesh_stage.default_target_faces(5000) == 4900 and mesh_stage.default_target_faces(20000) == 19600
    assert mesh_stage.default_target_faces(None) == 20000 and mesh_stage.default_target_faces(100) == 500


def _two_squares(gap: int, size: int = 64) -> np.ndarray:
    lab = np.zeros((size, size), np.int32)
    lab[10:30, 5:20] = 1
    lab[10:30, 20 + gap:40 + gap] = 2
    return lab


@pytest.mark.parametrize("gap", [0, 1, 3, 6])
def test_island_gap_is_the_empty_texels_between_pieces(gap):
    gaps = imaging.island_gaps(_two_squares(gap), 8)
    assert list(gaps) == [gap, gap]
    assert imaging.gutter_px(_two_squares(gap), 8) == gap


def test_island_gap_is_capped_when_no_neighbour_is_near():
    lab = np.zeros((64, 64), np.int32)
    lab[2:8, 2:8] = 1
    lab[50:60, 50:60] = 2
    assert list(imaging.island_gaps(lab, 3)) == [7, 7]
    assert imaging.gutter_px(np.zeros((8, 8), np.int32), 3) == 0


def test_dilate_grows_into_empty_space_by_the_given_ring():
    img = np.zeros((20, 20, 3), np.uint8)
    mask = np.zeros((20, 20), bool)
    img[8:12, 8:12] = (200, 10, 10)
    mask[8:12, 8:12] = True
    out = imaging.dilate(img, mask, 3)
    assert tuple(out[5, 10]) == (200, 10, 10) and tuple(out[10, 14]) == (200, 10, 10)
    assert tuple(out[4, 10]) == (0, 0, 0) and tuple(out[0, 0]) == (0, 0, 0)
    assert np.array_equal(out[mask], img[mask])


# --- UV raster, hashes ---------------------------------------------------------------------------------------


def _uv_glb(path: Path, offset=(0.0, 0.0, 0.0), scale: float = 1.0, base: np.ndarray | None = None,
            mr: np.ndarray | None = None) -> Path:
    """Two quads (four triangles) facing up and sideways, with two UV islands: one top left, one bottom right."""
    trimesh = pytest.importorskip("trimesh")
    v = np.array([[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1],      # horizontal quad, normal +Y (glTF up)
                  [2, 0, 0], [3, 0, 0], [3, 1, 0], [2, 1, 0]], float)  # vertical quad, normal +Z
    v = v * scale + np.array(offset)
    f = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7]])
    uv = np.array([[0.05, 0.95], [0.45, 0.95], [0.45, 0.55], [0.05, 0.55],
                   [0.55, 0.45], [0.95, 0.45], [0.95, 0.05], [0.55, 0.05]])
    img = Image.fromarray(base if base is not None else np.full((64, 64, 3), 128, np.uint8))
    mat = trimesh.visual.material.PBRMaterial(baseColorTexture=img,
                                              metallicRoughnessTexture=Image.fromarray(mr) if mr is not None else None)
    m = trimesh.Trimesh(v, f, visual=trimesh.visual.TextureVisuals(uv=uv, material=mat), process=False)
    m.export(path)
    return path


def test_rasterised_layout_islands_orientation_and_up(tmp_path):
    m = imaging.load_uv_mesh(_uv_glb(tmp_path / "a.glb"))
    assert m is not None and len(set(m["island"].tolist())) == 2
    r = imaging.rasterise_uv(m, 64)
    lab = r["labels"]
    # glTF V runs down the image: the island at v 0.55 to 0.95 sits in the top rows, the other in the bottom right.
    assert lab[5, 5] > 0 and lab[5, 60] == 0 and lab[60, 60] > 0 and lab[60, 5] == 0 and lab[5, 5] != lab[60, 60]
    assert abs(imaging.atlas_used(lab) - 2 * 0.16) < 0.05  # the raster includes the edge texels
    # The horizontal quad faces up (up 1), the vertical one sideways (up 0); NaN where empty.
    assert r["up"][10, 10] > 0.95 and abs(r["up"][55, 55]) < 0.05 and np.isnan(r["up"][32, 2])


def test_uv_hash_ignores_size_and_position(tmp_path):
    a = imaging.load_uv_mesh(_uv_glb(tmp_path / "a.glb"))
    b = imaging.load_uv_mesh(_uv_glb(tmp_path / "b.glb", offset=(5, 1, 2), scale=1.8))
    assert imaging.uv_hash(a) == imaging.uv_hash(b) and imaging.uv_match_fraction(a, b) == 1.0
    c = dict(b, uv=b["uv"] * 0.5)
    assert imaging.uv_hash(c) != imaging.uv_hash(a) and imaging.uv_match_fraction(a, c) == 0.0
    assert imaging.uv_hash(None) is None and imaging.uv_match_fraction(None, a) is None


# --- G07: light score and correction -------------------------------------------------------------------------


def _lit_texture(seed: int = 1, size: int = 64, light: float = 0.6):
    rng = np.random.default_rng(seed)
    albedo = rng.uniform(0.3, 0.7, (size, size, 3)).astype(np.float32)
    ao = rng.uniform(0.2, 1.0, (size, size)).astype(np.float32)
    up = rng.uniform(-1.0, 1.0, (size, size)).astype(np.float32)
    shade = (1 - light) + light * ao * (0.75 + 0.25 * up)
    return albedo, np.clip(albedo * shade[..., None], 0, 1), ao, up, np.ones((size, size), bool)


def test_light_score_finds_light_painted_into_the_colours():
    albedo, lit, ao, up, mask = _lit_texture()
    clean = imaging.light_score(albedo, mask, ao, up)
    baked = imaging.light_score(lit, mask, ao, up)
    assert clean["score"] < 0.1 and baked["score"] > 0.5 and baked["corr_ao"] > baked["corr_up"] > 0
    assert imaging.light_score(lit, mask)["score"] == 0.0  # nothing to compare with
    # Darker where facing up (a dark back, a pale belly) is colouring, not light: it scores 0.
    assert imaging.light_score(np.clip(albedo * (0.6 - 0.3 * up)[..., None], 0, 1), mask, None, up)["score"] == 0.0


def test_delight_removes_the_fitted_shading_and_keeps_hue_and_brightness():
    albedo, lit, ao, up, mask = _lit_texture()
    out = imaging.delight(lit, mask, ao, up, 1.0)
    assert imaging.light_score(out, mask, ao, up)["score"] < 0.1
    lum_in, lum_out = imaging.srgb_luminance(lit).mean(), imaging.srgb_luminance(out).mean()
    assert abs(lum_out - lum_in) < 0.03
    ratio = out / np.maximum(lit, 1e-3)  # the three channels move together
    assert np.allclose(ratio[..., 0], ratio[..., 1], atol=0.02)
    assert np.array_equal(imaging.delight(lit, mask, ao, up, 0.0), lit)
    half = imaging.delight(lit, mask, ao, up, 0.5)
    assert imaging.light_score(out, mask, ao, up)["score"] < imaging.light_score(half, mask, ao, up)["score"] < imaging.light_score(lit, mask, ao, up)["score"]
    assert imaging.delight_strength(0.1, 0.2) == 0.0 and imaging.delight_strength(0.25, 0.2) == 0.75
    assert imaging.delight_strength(0.21, 0.2) == pytest.approx(0.55) and imaging.delight_strength(0.5, 0.2) == 1.0


# --- G08: ranges -------------------------------------------------------------------------------------------


def test_pbr_stats_and_corrections():
    base = np.full((10, 10, 3), 0.5, np.float32)
    base[0, :] = 0.0                 # 10 black non-metal texels
    base[1, :] = 1.0                 # 10 white non-metal texels
    met = np.zeros((10, 10), np.float32)
    met[2, :] = 0.5                  # 10 half-metal texels
    met[3, :] = 1.0
    base[3, :] = 0.02                # dark metal is not judged by the albedo rule
    rough = np.full((10, 10), 0.5, np.float32)
    rough[4, :5] = 0.0
    mask = np.ones((10, 10), bool)
    s = imaging.pbr_stats(base, met, rough, mask)
    # Non-metal texels: 80 (the half-metal row counts as metal for the albedo rule, metallic >= 0.5).
    assert s == {"albedo_out": 0.25, "albedo_black": 0.125, "albedo_white": 0.125,
                 "metal_mixed": 0.1, "roughness_low": 0.05}
    fixed = imaging.clamp_albedo(base, met)
    lum = imaging.srgb_luminance(fixed) * 255
    assert lum[met < 0.5].min() >= imaging.ALBEDO_MIN - 0.5 and lum[met < 0.5].max() <= imaging.ALBEDO_MAX + 0.5
    assert np.allclose(fixed[3], base[3]) and np.allclose(fixed[5], base[5], atol=1e-6)  # metal and mid colours untouched
    assert imaging.pbr_stats(fixed, imaging.sharpen_metallic(met), imaging.clamp_roughness(rough), mask) == {
        "albedo_out": 0.0, "albedo_black": 0.0, "albedo_white": 0.0, "metal_mixed": 0.1, "roughness_low": 0.0}
    assert list(imaging.sharpen_metallic(np.array([0.0, 0.2, 0.45, 0.8, 1.0]))) == pytest.approx([0, 0, 0.3, 1, 1])


def test_glb_image_swap(tmp_path):
    trimesh = pytest.importorskip("trimesh")
    mr = np.zeros((32, 32, 3), np.uint8)
    mr[..., 0], mr[..., 1], mr[..., 2] = 255, 100, 20
    glb = _uv_glb(tmp_path / "a.glb", mr=mr)
    new_base = tmp_path / "b.png"
    Image.new("RGB", (64, 64), (10, 200, 30)).save(new_base)
    rough = tmp_path / "r.png"
    Image.new("L", (32, 32), 77).save(rough)
    assert imaging.glb_replace_images(glb, base_color=new_base, roughness=rough)
    m = trimesh.load(str(glb), force="mesh")
    mat = m.visual.material
    assert np.array(mat.baseColorTexture.convert("RGB"))[5, 5].tolist() == [10, 200, 30]
    mrn = np.array(mat.metallicRoughnessTexture.convert("RGB"))
    assert mrn[0, 0].tolist() == [255, 77, 20]  # occlusion and metallic kept, roughness swapped
    assert len(m.faces) == 4
    assert not imaging.glb_replace_images(glb)  # nothing asked


# --- styles, knobs, checks ------------------------------------------------------------------------------------


def test_styles_say_whether_painted_light_is_kept():
    styles = {s["id"]: s for s in yaml.safe_load((BACKEND / "vgac" / "data" / "styles.yaml").read_text(encoding="utf-8"))["styles"]}
    assert {sid for sid, s in styles.items() if s["delight_default"] == "remove"} == {"realistic_pbr"}
    assert {sid for sid, s in styles.items() if s["pbr_fix_default"]} == {"realistic_pbr"}
    r = prep_stage.resolve_surface
    assert r(styles["realistic_pbr"], {}) == {"delight": "remove", "delight_source": "style", "pbr_fix": True}
    assert r(styles["painted"], {}) == {"delight": "keep", "delight_source": "style", "pbr_fix": False}
    assert r(styles["painted"], {"delight": "remove", "pbr_fix": True}) == {"delight": "remove", "delight_source": "chosen", "pbr_fix": True}
    assert r(styles["realistic_pbr"], {"delight": "keep", "pbr_fix": False})["pbr_fix"] is False
    assert r({"id": "unknown"}, {"delight": "auto"})["delight"] == "keep"


def test_new_knobs_are_registered_with_help_and_labels():
    stages = {s["id"]: {p["name"]: p for p in s["adjust_schema"]} for s in reference.stages()}
    pad = stages["mesh"]["padding"]
    assert pad["type"] == "integer" and pad["min"] == 1 and pad["max"] == 16 and pad["simple"] is False
    d = stages["prep"]["delight"]
    assert d["default"] == "auto" and d["simple"] is True and d["option_labels"] == {
        "auto": "Automatic (by look)", "remove": "Remove them", "keep": "Keep them (part of the look)"}
    assert stages["prep"]["pbr_fix"]["simple"] is False and stages["prep"]["rebake"]["simple"] is False
    assert stages["prep"]["symmetric"]["default"] is False and "material" not in stages["prep"]["symmetric"]["categories"]
    for name in ("padding", "delight", "pbr_fix", "rebake"):
        h = guidance.PARAM_HELP[name]["help"]
        assert h["meaning"] and h["why"] and h["technical"], name
    assert reference.validate_params("prep", {"delight": "remove", "pbr_fix": True, "rebake": True}, category="humanoid") == {
        "delight": "remove", "pbr_fix": True, "rebake": True}
    assert reference.validate_params("prep", {"delight": "remove"}, category="material") == {}
    with pytest.raises(ValueError):
        reference.validate_params("prep", {"delight": "maybe"})
    with pytest.raises(ValueError):
        reference.validate_params("mesh", {"padding": 64})


def _metrics(**over):
    m = {"export_mesh_hash": "a", "bake_mesh_hash": "a", "bake_match": 1.0, "rebaked": False, "texture_padding_px": 8,
         "texture_padding_target_px": 8, "atlas_used": 0.57, "light_score": 0.1, "delight_mode": "remove", "delight_applied": False,
         "pbr": {"albedo_out": 0.0, "metal_mixed": 0.0}, "pbr_fix_applied": False}
    m.update(over)
    return m


def test_surface_checks_sentences_and_fixes():
    by = {c["name"]: c for c in prep_stage.surface_checks(_metrics())}
    assert set(by) == {"bake_matches", "texture_padding", "atlas_used", "baked_light", "albedo_range", "metal_mixed"}
    assert all(c["passed"] for c in by.values())
    assert by["texture_padding"]["help"] == "Texture pieces have 8 px of space around them, enough for distant views."
    assert by["atlas_used"]["help"] == "The model uses 57% of its texture."
    assert by["baked_light"]["help"] == "No painted-on shadows found in the colours."

    bad = {c["name"]: c for c in prep_stage.surface_checks(_metrics(
        bake_match=0.45, texture_padding_px=1, atlas_used=0.28, light_score=0.3, pbr={"albedo_out": 0.18, "metal_mixed": 0.61}),
        {"symmetric": True})}
    assert not any(c["passed"] for c in bad.values())
    assert bad["bake_matches"]["fix"] == {"stage": "prep", "params": {"rebake": True}, "label": "Repaint the surface"}
    assert bad["texture_padding"]["fix"] == {"stage": "mesh", "params": {"padding": 8}, "label": "Add more space"}
    assert bad["texture_padding"]["help"] == "Texture pieces have only 1 px of space, so thin dark seams may show at a distance."
    assert bad["atlas_used"]["fix"]["params"] == {"symmetric": False} and bad["atlas_used"]["fix"]["label"] == "Repaint the surface"
    assert bad["baked_light"]["fix"] == {"stage": "prep", "params": {"delight": "remove"}, "label": "Remove painted-on shadows"}
    assert bad["albedo_range"]["help"].startswith("18% of the colours are darker or brighter")
    assert bad["metal_mixed"]["fix"] == {"stage": "prep", "params": {"pbr_fix": True}, "label": "Make metal clearly metal"}
    # Padding passes at three quarters of the target (6 of 8).
    assert {c["name"]: c for c in prep_stage.surface_checks(_metrics(texture_padding_px=6))}["texture_padding"]["passed"]

    # A kept look always passes with its own sentence; a correction that ran and still fails offers no loop.
    kept = {c["name"]: c for c in prep_stage.surface_checks(_metrics(delight_mode="keep", light_score=0.6))}["baked_light"]
    assert kept["passed"] and kept["help"] == "Painted shading is kept on purpose for this look."
    ran = {c["name"]: c for c in prep_stage.surface_checks(_metrics(light_score=0.25, delight_applied=True,
                                                                    pbr_fix_applied=True, pbr={"albedo_out": 0.05}))}
    assert not ran["baked_light"]["passed"] and "fix" not in ran["baked_light"] and "fix" not in ran["albedo_range"]
    done = {c["name"]: c for c in prep_stage.surface_checks(_metrics(light_score=0.05, delight_applied=True))}["baked_light"]
    assert done["help"] == "Painted-on shadows removed, so it reacts to your game's lights."
    assert prep_stage.surface_checks({}) == [] and "atlas_used" not in {c["name"] for c in prep_stage.surface_checks(_metrics(atlas_used=None))}


def test_texture_pass_corrects_a_realistic_asset_and_keeps_the_original(tmp_path):
    albedo, lit, ao, up, _ = _lit_texture(size=64, light=0.7)
    # Make the light follow the AO map the pass will read, on a mesh whose layout covers the texture.
    glb = _uv_glb(tmp_path / "prep.glb", base=(lit * 255).astype(np.uint8))
    maps = {}
    for key, arr in (("base_color", lit), ("ao", ao), ("metallic", np.full((64, 64), 0.3, np.float32)), ("roughness", np.full((64, 64), 0.01, np.float32))):
        maps[key] = imaging.write_map(arr, tmp_path / f"in_{key}.png")
    out = tmp_path / "out"
    out.mkdir()
    res = prep_stage.texture_pass(glb, glb, maps, out, 64, "remove", True)
    m, files = res["metrics"], res["files"]
    assert m["bake_match"] == 1.0 and m["bake_mesh_hash"] == m["export_mesh_hash"] and m["texture_padding_target_px"] == 4
    assert m["light_score_before"] > prep_stage.LIGHT_SCORE_LIMIT and m["delight_applied"] and m["light_score"] < m["light_score_before"]
    assert m["roughness_clamped"] and m["pbr"]["roughness_low"] == 0.0 and m["pbr"]["metal_mixed"] < m["pbr_before"]["metal_mixed"]
    assert set(files) == {"base_color", "base_color_original", "metallic", "roughness"}
    assert np.array_equal(np.array(Image.open(files["base_color_original"])), np.array(Image.open(maps["base_color"])))
    checks = {c["name"]: c for c in prep_stage.surface_checks({**m, "delight_mode": "remove"})}
    assert {"bake_matches", "texture_padding", "atlas_used", "baked_light", "albedo_range", "metal_mixed"} <= set(checks)

    # A stylised asset keeps its painted light and is only measured (roughness is still floored).
    res2 = prep_stage.texture_pass(glb, glb, maps, out, 64, "keep", False)
    assert not res2["metrics"]["delight_applied"] and "base_color" not in res2["files"] and "roughness" in res2["files"]


# --- G05: the re-bake in Blender ------------------------------------------------------------------------------


def _grid_glb(path: Path, n: int = 24) -> Path:
    """A bumpy n x n sheet standing upright (glTF Y up), with a UV layout over the whole square."""
    trimesh = pytest.importorskip("trimesh")
    xs = np.linspace(-1, 1, n)
    v, uv = [], []
    for j, y in enumerate(np.linspace(0, 2, n)):
        for i, x in enumerate(xs):
            v.append([x + 0.3 * (x > 0) * 0.2, y, 0.05 * np.sin(3 * x + 2 * y)])  # deliberately lopsided
            uv.append([0.02 + 0.96 * i / (n - 1), 0.02 + 0.96 * j / (n - 1)])
    f = []
    for j in range(n - 1):
        for i in range(n - 1):
            a = j * n + i
            f += [[a, a + 1, a + n + 1], [a, a + n + 1, a + n]]
    mat = trimesh.visual.material.PBRMaterial(baseColorTexture=Image.new("RGB", (64, 64), (120, 90, 60)))
    m = trimesh.Trimesh(np.array(v), np.array(f), visual=trimesh.visual.TextureVisuals(uv=np.array(uv), material=mat), process=False)
    m.export(path)
    return path


def test_blender_prep_rebakes_when_mirroring_and_shrinking(tmp_path):
    exe = _blender()
    if not exe:
        pytest.skip("Blender not installed")
    src = _grid_glb(tmp_path / "src.glb")
    maps = {}
    rng = np.random.default_rng(0)
    for key, size in (("base_color", 128), ("metallic", 128), ("roughness", 128), ("normal", 128), ("ao", 64)):
        arr = rng.integers(60, 200, (size, size, 3), dtype=np.uint8)
        if key == "normal":
            arr[...] = (128, 128, 255)
        p = tmp_path / f"{key}.png"
        Image.fromarray(arr).save(p)
        maps[key] = str(p)
    (tmp_path / "maps.json").write_text(json.dumps(maps))
    script = BACKEND / "vgac" / "blender" / "prep.py"
    out = tmp_path / "out" / "prep.glb"
    subprocess.run([exe, "-b", "--factory-startup", "--python", str(script), "--", str(src), str(out), "sheet", "--size", "2",
                    "--symmetric", "--budget", "600", "--maps", str(tmp_path / "maps.json"), "--tex", "128"],
                   check=True, capture_output=True, timeout=600)
    report = json.loads((tmp_path / "out" / "prep_report.json").read_text())
    assert report["rebaked"] and report["rebake_reason"] == "symmetrize, decimate" and report["repacked"]
    assert report["triangles"] <= 600 and report["rebake_seconds"] > 0
    for key in ("base_color", "metallic", "roughness", "normal", "ao"):
        p = Path(report["rebake_maps"][key])
        assert p.is_file() and Image.open(p).size == ((64, 64) if key == "ao" else (128, 128)), key
    # The shipped mesh is the one the maps were painted on, and its layout uses the texture.
    final = imaging.load_uv_mesh(out)
    assert final is not None and imaging.atlas_used(imaging.rasterise_uv(final, 128)["labels"]) > 0.3

    # No mirroring and no shrinking: nothing to re-bake, the maps stay the mesh stage's.
    out2 = tmp_path / "out2" / "prep.glb"
    subprocess.run([exe, "-b", "--factory-startup", "--python", str(script), "--", str(src), str(out2), "sheet", "--size", "2",
                    "--maps", str(tmp_path / "maps.json"), "--tex", "128"], check=True, capture_output=True, timeout=600)
    report2 = json.loads((tmp_path / "out2" / "prep_report.json").read_text())
    assert not report2["rebaked"] and report2["rebake_reason"] is None
    assert imaging.uv_match_fraction(imaging.load_uv_mesh(src), imaging.load_uv_mesh(out2)) >= prep_stage.BAKE_MATCH_MIN
