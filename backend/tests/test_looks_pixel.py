"""Pixel art settings from the look research (docs/research/looks/pixel.md, local). On for the look: a 2-texel gap, the
concept picture's own colours as the palette with the stray clean after it, and every GLB of a crisp look sampled
nearest. Available but off (they wait for the clean-up's facing and outline checks): the 3D step building from a shaded
redraw of its picture (mesh.build_from) and the pixel pictures painted on before the palette (surface.picture_projection);
their tests turn them on in a copy of the recipe. Every other look keeps its recipe (tests/test_looks_lowtex.py)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from vgac import imaging, reference, srcproject
from vgac.stages import export as export_stage, mesh as mesh_stage, prep

OTHER_LOOKS = ("realistic_pbr", "painted", "cel", "clay", "retro64", "lowpoly_flat")
NEW_MESH = ("padding", "build_from")
NEW_SURFACE = ("palette_source", "clean_strays", "picture_projection")


def test_pixel_recipe_carries_the_settings_that_are_on():
    r = reference.recipe("pixel", "humanoid", "pc_mid")
    assert r["mesh"]["padding"] == 2 and "build_from" not in r["mesh"]
    assert r["surface"]["palette_source"] == "concept" and r["surface"]["clean_strays"] == 2
    assert "picture_projection" not in r["surface"]
    assert r["mesh"]["texture"] == 256 and r["surface"]["texture_filter"] == "crisp"  # as before
    look = prep.texture_look(r)
    assert "projection" not in look and not srcproject.on({}, "humanoid", look)  # no painting, as before
    assert not prep.look_after_painting(look, {}, "humanoid")  # the palette in the texture pass, as before
    assert not mesh_stage.shaded_on(r, "humanoid", {})


def _pixel_with_switches(monkeypatch):
    """The pixel recipe with the two switches that are off turned on (as a look would set them)."""
    real = reference.style

    def fake(style_id):
        s = dict(real(style_id))
        if style_id == "pixel":
            s["recipe"] = {**s["recipe"], "mesh": {**s["recipe"]["mesh"], "build_from": "shaded"},
                           "surface": {**s["recipe"]["surface"], "picture_projection": True}}
        return s

    monkeypatch.setattr(reference, "style", fake)


def test_switches_reach_the_recipe_when_a_look_sets_them(monkeypatch):
    _pixel_with_switches(monkeypatch)
    r = reference.recipe("pixel", "humanoid", "pc_mid")
    assert r["mesh"]["build_from"] == "shaded" and r["surface"]["picture_projection"] is True


@pytest.mark.parametrize("style", OTHER_LOOKS)
def test_other_looks_get_none_of_them(style):
    try:
        r = reference.recipe(style, "humanoid", "pc_mid")
    except KeyError:
        pytest.skip(f"no style {style}")
    assert not set(NEW_MESH) & set(r["mesh"]) and not set(NEW_SURFACE) & set(r["surface"])


def test_recipe_keeps_values_in_range(monkeypatch):
    real = reference.style

    def fake(style_id):
        s = dict(real("pixel"))
        s["recipe"] = {**s["recipe"], "mesh": {**s["recipe"]["mesh"], "padding": 40, "build_from": "drawn"},
                       "surface": {**s["recipe"]["surface"], "palette_source": "texture", "clean_strays": 9}}
        return s

    monkeypatch.setattr(reference, "style", fake)
    r = reference.recipe("pixel", "humanoid", "pc_mid")
    assert r["mesh"]["padding"] == 16 and "build_from" not in r["mesh"]
    assert "palette_source" not in r["surface"] and r["surface"]["clean_strays"] == 4


def test_padding_from_the_version_then_the_look_then_the_size():
    rec = reference.recipe("pixel", "humanoid", "pc_mid")
    assert mesh_stage.resolve_padding({}, 256, rec) == 2
    assert mesh_stage.resolve_padding({"padding": 6}, 256, rec) == 6
    assert mesh_stage.resolve_padding({}, 256, reference.recipe("painted", "humanoid", "pc_mid")) == 8
    assert mesh_stage.resolve_padding({}, 256) == 8


def test_shaded_redraw_only_for_pixel_characters_and_can_be_turned_off(monkeypatch):
    _pixel_with_switches(monkeypatch)
    pixel = reference.recipe("pixel", "humanoid", "pc_mid")
    assert mesh_stage.shaded_on(pixel, "humanoid", {})
    assert not mesh_stage.shaded_on(pixel, "humanoid", {"build_shaded": False})
    assert not mesh_stage.shaded_on(reference.recipe("pixel", "prop_rigid", "pc_mid"), "prop_rigid", {})
    assert not mesh_stage.shaded_on(reference.recipe("painted", "humanoid", "pc_mid"), "humanoid", {})
    words = mesh_stage.edit_prompt(mesh_stage.SHADED_EDIT)
    assert "smooth, softly shaded 3D render" in words and "same colours" in words and "Not pixel art" in words


def test_projection_runs_for_the_pixel_look_only_when_it_asks(monkeypatch):
    _pixel_with_switches(monkeypatch)
    pixel = prep.texture_look(reference.recipe("pixel", "humanoid", "pc_mid"))
    assert pixel["projection"] and pixel["palette_source"] == "concept" and pixel["clean_strays"] == 2
    assert srcproject.on({}, "humanoid", pixel)
    assert not srcproject.on({}, "prop_rigid", pixel)
    assert not srcproject.on({"source_project": False}, "humanoid", pixel)
    crisp_only = {k: v for k, v in pixel.items() if k != "projection"}
    assert not srcproject.on({}, "humanoid", crisp_only)  # a crisp look that does not ask: off, as before
    assert prep.look_after_painting(pixel, {}, "humanoid") and not prep.look_after_painting(pixel, {}, "prop_rigid")
    assert not prep.look_after_painting(prep.texture_look(reference.recipe("painted", "humanoid", "pc_mid")), {}, "humanoid")


def test_the_original_pictures_are_kept_for_the_shaded_redraw_alone():
    assert srcproject.shaded_only({"build_shaded": True, "picture_edit": True})
    assert not srcproject.shaded_only({"build_shaded": True, "picture_edit": True, "instruction": "add a hat"})
    assert not srcproject.shaded_only({"build_shaded": True, "picture_edit": True, "hand_edit": True})
    assert not srcproject.shaded_only({"picture_edit": True})


def _picture(path: Path) -> Path:
    a = np.zeros((10, 10, 4), np.uint8)
    a[2:8, 2:8] = (200, 40, 40, 255)  # 36 red
    a[2:4, 2:8] = (20, 20, 20, 255)  # 12 dark (replaces 12 red: 24 red)
    a[9, 9] = (0, 255, 0, 100)  # see-through: not a colour of the picture
    Image.fromarray(a, "RGBA").save(path)
    return path


def test_concept_palette_is_the_pictures_own_colours_most_used_first(tmp_path):
    p = _picture(tmp_path / "c.png")
    assert imaging.picture_palette(p) == [(200, 40, 40), (20, 20, 20)]
    assert imaging.picture_palette(p, most=1) == [(200, 40, 40)]
    look = prep.texture_look(reference.recipe("pixel", "humanoid", "pc_mid"))
    got = prep.concept_palette(look, p)
    assert got["palette"] == [(200, 40, 40), (20, 20, 20)] and got["palette_from"] == "concept picture"
    assert got["concept_palette_cover"] == 1.0 and "concept_palette_skipped" not in got
    none = prep.concept_palette(look, None)
    assert none["palette"] is None and none["concept_palette_skipped"] == "no concept picture"
    painted = prep.texture_look(reference.recipe("painted", "humanoid", "pc_mid"))
    assert prep.concept_palette(painted, p) is painted  # a look that does not ask: untouched


def test_a_soft_concept_picture_keeps_todays_palette(tmp_path):
    """A picture with soft edges (thousands of shades): its top 32 colours would be near-identical, so the texture keeps
    its own 32 colours, and the reason is recorded."""
    pal = imaging.picture_palette(_picture(tmp_path / "c.png"))
    soft = np.zeros((64, 64, 4), np.uint8)
    soft[..., 0] = np.linspace(40, 220, 64)[None, :]  # shading across, as a painter draws a sleeve
    soft[..., 1] = np.linspace(30, 90, 64)[:, None]
    soft[..., 2], soft[..., 3] = 60, 255
    Image.fromarray(soft, "RGBA").save(tmp_path / "soft.png")
    rng = np.random.default_rng(0)
    noisy = (rng.random((40, 40, 3)) * 255).astype(np.uint8)
    Image.fromarray(noisy).save(tmp_path / "noisy.png")
    look = prep.texture_look(reference.recipe("pixel", "humanoid", "pc_mid"))
    for p in (tmp_path / "soft.png", tmp_path / "noisy.png"):
        _, cover = imaging.picture_palette_cover(p)
        assert cover < prep.CONCEPT_PALETTE_COVER_MIN
        got = prep.concept_palette(look, p)
        assert got["palette"] is None and got["palette_colors"] == 32
        assert "not drawn in a few colours" in got["concept_palette_skipped"]
    assert len(pal) == 2


def test_the_projects_own_palette_keeps_priority(tmp_path):
    p = _picture(tmp_path / "c.png")
    lock = ["#%02x%02x%02x" % (i * 15, 255 - i * 15, 100) for i in range(16)]  # 16 colours: a texture palette
    look = prep.texture_look(reference.recipe("pixel", "humanoid", "pc_mid"), lock)
    assert len(look["palette"]) == 16
    got = prep.concept_palette(look, p)
    assert got["palette"] == look["palette"] and got["concept_palette_skipped"] == "the project's own palette is used"
    small = prep.texture_look(reference.recipe("pixel", "humanoid", "pc_mid"), lock[:8])  # 8: a prompt hint only
    assert prep.concept_palette(small, p)["palette_from"] == "concept picture"


def test_apply_look_records_why_the_concept_palette_was_skipped(tmp_path):
    base = np.full((64, 64, 3), 120, np.uint8)
    glb = _uv_glb(tmp_path / "m.glb", base)
    src = imaging.write_map(base.astype(np.float32) / 255.0, tmp_path / "base.png")
    look = {**prep.texture_look(reference.recipe("pixel", "humanoid", "pc_mid")), "cap": 64,
            "concept_palette_skipped": "no concept picture"}
    files: dict = {}
    m, _ = prep.apply_look(look, imaging.load_uv_mesh(glb), {"base_color": src}, files, base.astype(np.float32) / 255.0, 64,
                           tmp_path)
    assert m["palette_source"] == "recipe" and m["concept_palette_skipped"] == "no concept picture"


def test_stray_clean_joins_single_texels_and_keeps_edges_and_pieces():
    a = np.zeros((8, 8, 3), np.float32)
    a[:, 4:] = 1.0  # two areas side by side: the edge must stay
    a[2, 1] = (1.0, 0.0, 0.0)  # a single stray in the left area
    lab = np.ones((8, 8), np.int32)
    lab[6:, :] = 2  # a second piece below
    a[6, 0] = (0.0, 0.0, 1.0)  # a stray in the corner of the second piece: 4 neighbours in its piece, under 5, stays
    out = imaging.clean_strays(a, lab, 2)
    assert tuple(out[2, 1]) == (0.0, 0.0, 0.0)
    assert (out[:6, 4:] == 1.0).all() and (out[:6, :4][np.arange(6) != 2] == 0.0).all()
    assert tuple(out[6, 0]) == (0.0, 0.0, 1.0)
    unlabelled = np.zeros((8, 8), np.int32)
    assert np.array_equal(imaging.clean_strays(a, unlabelled, 2), a)


def test_apply_look_takes_the_palette_then_cleans(tmp_path):
    rng = np.random.default_rng(3)
    base = np.zeros((64, 64, 3), np.uint8)
    base[:] = (200, 40, 40)
    noisy = rng.random((64, 64)) < 0.05
    base[noisy] = (30, 30, 30)
    glb = _uv_glb(tmp_path / "m.glb", base)
    uvf = imaging.load_uv_mesh(glb)
    src = imaging.write_map(base.astype(np.float32) / 255.0, tmp_path / "base.png")
    look = {**prep.texture_look(reference.recipe("pixel", "humanoid", "pc_mid")), "cap": 64,
            "palette": [(200, 40, 40), (30, 30, 30)], "palette_from": "concept picture"}
    files: dict = {}
    m, _ = prep.apply_look(look, uvf, {"base_color": src}, files, base.astype(np.float32) / 255.0, 64, tmp_path)
    out = np.asarray(Image.open(files["base_color"]).convert("RGB"))
    mask = imaging.rasterise_uv(uvf, 64)["labels"] > 0
    assert m["palette_source"] == "concept picture" and m["strays_cleaned"] == 2
    assert (out[mask] == (30, 30, 30)).all(-1).mean() < 0.005  # the scattered dark texels joined the red


def test_padding_check_does_not_apply_to_a_crisp_look():
    m = {"export_mesh_hash": "x", "texture_padding_px": 1, "texture_padding_target_px": 4, "atlas_used": 0.5}
    assert any(c["id"] == "texture_padding" for c in prep.surface_checks(m))
    assert not any(c["id"] == "texture_padding" for c in prep.surface_checks({**m, "texture_crisp": True}))


def test_small_pictures_are_enlarged_by_whole_pixels(tmp_path):
    a = np.zeros((204, 100, 3), np.uint8)
    a[::2, ::2] = 255
    p = tmp_path / "small.png"
    Image.fromarray(a).save(p)
    big = srcproject.enlarge_whole_pixels(p, tmp_path / "big.png")
    b = np.asarray(Image.open(big))
    assert b.shape == (1020, 500, 3) and np.array_equal(b[::5, ::5], a)
    large = tmp_path / "large.png"
    Image.fromarray(np.zeros((1024, 1024, 3), np.uint8)).save(large)
    assert srcproject.enlarge_whole_pixels(large, tmp_path / "x.png") == large


def test_drawn_picture_is_what_the_clean_up_paints(tmp_path):
    drawn = tmp_path / "drawn.png"
    drawn.write_bytes(b"png")
    sc = SimpleNamespace(lineage_file=lambda key, stages: drawn if key == "drawn_input" else None)
    assert srcproject.drawn_picture(sc) == drawn
    assert srcproject.drawn_picture(SimpleNamespace(lineage_file=lambda key, stages: None)) is None


def _uv_glb(path: Path, base: np.ndarray) -> Path:
    trimesh = pytest.importorskip("trimesh")
    v = np.array([[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1], [2, 0, 0], [3, 0, 0], [3, 1, 0], [2, 1, 0]], float)
    f = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7]])
    uv = np.array([[0.05, 0.95], [0.45, 0.95], [0.45, 0.55], [0.05, 0.55], [0.55, 0.45], [0.95, 0.45], [0.95, 0.05], [0.55, 0.05]])
    mat = trimesh.visual.material.PBRMaterial(baseColorTexture=Image.fromarray(base))
    trimesh.Trimesh(v, f, visual=trimesh.visual.TextureVisuals(uv=uv, material=mat), process=False).export(path)
    return path


def test_export_sets_every_glb_of_a_crisp_look_to_nearest(tmp_path):
    import json
    import struct

    base = np.full((16, 16, 3), 90, np.uint8)
    for n in ("hero.glb", "hero_godot.glb", "hero_LOD1.glb"):
        _uv_glb(tmp_path / n, base)
    done = export_stage.nearest_samplers(tmp_path, "hero", {"glb_lods": {"LOD1": str(tmp_path / "hero_LOD1.glb")}})
    assert sorted(done) == ["hero.glb", "hero_LOD1.glb", "hero_godot.glb"]
    for n in done:
        data = (tmp_path / n).read_bytes()
        g = json.loads(data[20:20 + struct.unpack_from("<I", data, 12)[0]])
        assert all(s["minFilter"] == 9728 and s["magFilter"] == 9728 for s in g["samplers"])
        assert imaging.load_uv_mesh(tmp_path / n) is not None
