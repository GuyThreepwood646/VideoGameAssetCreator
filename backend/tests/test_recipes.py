"""Recipes: the chosen style carried through every step (reference.recipe, data/addons.yaml), the generic add-on chain
in the concept graphs, the palette sentence, the texture treatment in prep, the mesh and prep targets, the export and
importer settings, and the plain sentences where a recipe changes what good means."""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml
from PIL import Image

from vgac import guidance, imaging, reference
from vgac.graphs import concept as cg
from vgac.graphs import qwen_edit
from vgac.stages import concept as concept_stage
from vgac.stages import export as export_stage
from vgac.stages import mesh as mesh_stage
from vgac.stages import prep as prep_stage
from vgac.stages import turnaround as turn_stage

BACKEND = Path(__file__).resolve().parent.parent
IMPORTER = BACKEND.parent / "unity-sandbox" / "Assets" / "Editor" / "VgacImport.cs"
STYLES = ("painted", "cel", "pixel", "retro64", "lowpoly_flat", "realistic_pbr", "clay")


# --- the recipe -------------------------------------------------------------------------------------------------


def test_every_style_category_platform_resolves_with_a_plain_sentence():
    for s in reference.styles():
        for c in reference.categories():
            for p in reference.platforms():
                r = reference.recipe(s["id"], c["id"], p["id"])
                assert r["plain"].endswith(".") and "{" not in r["plain"] and ":" in r["plain"], r["plain"]
                assert set(r) >= {"id", "plain", "concept", "addons", "turnaround", "mesh", "surface", "engine"}
                assert r["surface"]["shading"] in ("smooth", "flat") and r["surface"]["texture_filter"] in ("smooth", "crisp")
                assert r["engine"]["filter_mode"] in ("bilinear", "point")
                b = reference.budget(c["id"], p["id"])
                if b["triangles"]:
                    assert r["mesh"]["triangles"] <= b["triangles"]
                assert r["mesh"]["texture"] <= b["texture"]
                assert re.fullmatch(r"[0-9a-f]{12}", r["id"])
    assert {s["id"] for s in reference.styles()} == set(STYLES)


def test_full_pipeline_styles_keep_todays_behaviour():
    for sid in ("painted", "cel", "clay", "realistic_pbr"):
        r = reference.recipe(sid, "humanoid", "pc_mid")
        assert r["mesh"]["triangles"] == 20000 and r["mesh"]["texture"] == 2048 and r["mesh"]["normal_map"]
        assert r["surface"]["shading"] == "smooth" and r["surface"]["texture_filter"] == "smooth" and r["surface"]["palette_colors"] is None
        assert r["engine"] == {"filter_mode": "bilinear", "mipmaps": True, "compress": True}
        assert r["turnaround"] == {"lora_strength": 0.9, "prompt_suffix": ""}
        assert r["concept"]["model"] == reference.style(sid)["model"]
    assert reference.recipe("realistic_pbr", "humanoid", "pc_mid")["surface"]["delight"] == "remove"
    assert reference.recipe("painted", "humanoid", "pc_mid")["surface"]["delight"] == "keep"


def test_low_poly_retro_and_pixel_recipes():
    low = reference.recipe("lowpoly_flat", "humanoid", "pc_mid")
    assert low["mesh"]["triangles"] == 5000 and low["mesh"]["texture"] == 512 and not low["mesh"]["normal_map"]
    assert low["surface"]["shading"] == "flat" and low["engine"]["filter_mode"] == "bilinear"
    assert low["plain"] == "Low-poly character: faceted shapes with one flat colour per face, a quarter of the usual triangles, simple textures."
    # The floor: a phone prop's quarter (250) is raised to 300.
    assert reference.recipe("lowpoly_flat", "prop_rigid", "mobile_low")["mesh"]["triangles"] == 300
    retro = reference.recipe("retro64", "humanoid", "pc_mid")
    assert retro["mesh"]["triangles"] == 8000 and retro["mesh"]["texture"] == 256 and not retro["mesh"]["normal_map"]
    assert retro["surface"]["texture_filter"] == "smooth" and retro["engine"]["mipmaps"]
    px = reference.recipe("pixel", "quadruped", "mobile_low")
    assert px["plain"].startswith("Pixel-art creature: small crisp textures")
    assert px["mesh"]["texture"] == 256 and not px["mesh"]["normal_map"]
    assert px["surface"] == {**px["surface"], "texture_filter": "crisp", "palette_colors": 32, "pixel_snap": True}
    assert px["engine"] == {"filter_mode": "point", "mipmaps": False, "compress": False}
    assert [a["id"] for a in px["addons"]] == ["pixel_art", "qwen_lightning", "qwen_angles"]


def test_by_category_override_and_unknown_style():
    assert reference.recipe("lowpoly_flat", "material", "pc_mid")["plain"] == "Low-poly material: a simple flat-coloured tile with little texture detail."
    with pytest.raises(KeyError):
        reference.recipe("nope", "humanoid", "pc_mid")
    loose = reference.recipe("nope", "humanoid", "pc_mid", strict=False)
    assert loose["mesh"]["triangles"] == 20000 and loose["concept"]["model"] == "zturbo"
    d = reference.default_recipe("x")
    assert d["mesh"]["normal_map"] and d["turnaround"]["lora_strength"] == 0.9 and d["mesh"]["triangles"] is None


def test_recipe_digest_changes_with_the_settings():
    a = reference.recipe("painted", "humanoid", "pc_mid")
    assert a["id"] == reference.recipe("painted", "humanoid", "pc_mid")["id"]
    assert a["id"] != reference.recipe("painted", "humanoid", "mobile_low")["id"] != reference.recipe("pixel", "humanoid", "mobile_low")["id"]


def test_recipe_public_lists_the_addons_for_the_painter():
    r = reference.recipe_public("pixel", "humanoid", "pc_mid")
    by = {e["id"]: e for e in r["addon_entries"]}
    assert set(by) == {"pixel_art", "qwen_lightning", "qwen_angles"}
    assert by["pixel_art"]["required"] and by["pixel_art"]["on"] and by["pixel_art"]["strength"] == "normal"
    assert by["qwen_angles"]["required"] and by["qwen_lightning"]["required"]
    assert "file" not in by["pixel_art"] and set(by["pixel_art"]["help"]) == {"meaning", "why", "technical"}
    painted = {e["id"]: e for e in reference.recipe_public("painted", "humanoid", "pc_mid")["addon_entries"]}
    assert not painted["pixel_art"]["on"] and not painted["pixel_art"]["required"]  # works with Z-Image Turbo, off by default
    cel = {e["id"] for e in reference.recipe_public("cel", "humanoid", "pc_mid")["addon_entries"]}
    assert cel == {"qwen_lightning", "qwen_angles"}  # nothing in the registry works with Anima


# --- the add-on registry ---------------------------------------------------------------------------------------------


def test_registry_seeds_the_three_wired_addons_with_every_field():
    reg = {a["id"]: a for a in reference.addons()}
    assert {"pixel_art", "qwen_lightning", "qwen_angles"} <= set(reg)
    for a in reg.values():
        assert a["label"] and set(a["help"]) == {"meaning", "why", "technical"}
        assert a["stage"] in ("concept", "turnaround") and a["file"] and "\\" not in a["file"]
        assert set(a["strengths"]) == set(reference.STRENGTH_NAMES) and a["default_strength"] in reference.STRENGTH_NAMES
        assert a["licence"] and a["source"] and a["base_models"]
        assert set(a["base_models"]) <= set(reference.models())
    assert reg["qwen_lightning"]["required"] and reg["qwen_angles"]["required"] and not reg["pixel_art"].get("required")
    assert reg["qwen_angles"]["strengths"]["normal"] == 0.9


def test_registry_files_match_the_wired_graphs_and_models_yaml():
    """The seeded add-ons are exactly the LoRAs the graphs and models.yaml already name (no folder is ever listed)."""
    reg = {a["id"]: a for a in reference.addons()}
    g = qwen_edit.angle_graph("i.png", "p", 1, "x")
    assert cg.lora_name(reg["qwen_lightning"]["file"]) == g["l1"]["inputs"]["lora_name"]
    assert cg.lora_name(reg["qwen_angles"]["file"]) == g["l2"]["inputs"]["lora_name"]
    assert cg.lora_name(reg["pixel_art"]["file"]) == cg.pixel_graph("p", 1, "x")["2"]["inputs"]["lora_name"]
    files = {m.get("file") for m in reference.models().values()}
    for aid in ("pixel_art", "qwen_lightning", "qwen_angles"):
        assert Path(reg[aid]["file"]).name in files


def test_resolve_addons_order_strengths_and_reports():
    r = reference.resolve_addons("zturbo", "concept", [{"id": "pixel_art", "strength": "subtle"}], ["pixel_art:strong", "mystery", {"id": "nope"}])
    assert [(a["id"], a["strength"], a["value"]) for a in r["applied"]] == [("pixel_art", "strong", 1.2)]
    assert r["unknown"] == ["mystery", "nope"] and not r["applied"][0]["built_in"]
    # A number from the lock, then the version switches it off.
    r = reference.resolve_addons("zturbo", "concept", None, ["pixel_art:0.7"])
    assert r["applied"][0]["strength"] == "custom" and r["applied"][0]["value"] == 0.7
    assert reference.resolve_addons("zturbo", "concept", None, ["pixel_art"], {"pixel_art": "off"})["applied"] == []
    # Required for the pixel painter: shown, built in, cannot be switched off.
    r = reference.resolve_addons("pixel", "concept", None, None, {"pixel_art": "off"})
    assert r["applied"][0]["built_in"] and r["applied"][0]["required"] and r["ignored"][0]["reason"].startswith("required")
    # Only applied when the model is in base_models.
    r = reference.resolve_addons("anima", "concept", None, ["pixel_art"])
    assert r["applied"] == [] and r["ignored"][0]["id"] == "pixel_art"
    # The turnaround's helpers are always there and cannot be switched off.
    t = reference.resolve_addons("qwen_edit", "turnaround", None, None, {"qwen_angles": "off", "qwen_lightning": "off"})
    assert [a["id"] for a in t["applied"]] == ["qwen_lightning", "qwen_angles"] and len(t["ignored"]) == 2
    t = reference.resolve_addons("qwen_edit", "turnaround", None, None, {"qwen_angles": "strong"})
    assert t["applied"][1]["value"] == 1.05


def test_validate_addons_param():
    ok = reference.validate_params("concept", {"addons": {"pixel_art": "Strong", "gone_from_registry": "off"}})
    assert ok["addons"] == {"pixel_art": "strong", "gone_from_registry": "off"}
    for bad in ({"addons": ["pixel_art"]}, {"addons": {"pixel_art": "loud"}}, {"addons": {"pixel_art": 1}}):
        with pytest.raises(ValueError):
            reference.validate_params("concept", bad)


def test_schemas_carry_the_addon_catalog_and_the_shading_knob():
    stages = {s["id"]: {p["name"]: p for p in s["adjust_schema"]} for s in reference.stages()}
    a = stages["concept"]["addons"]
    assert a["type"] == "addons" and a["options"] == ["off", "subtle", "normal", "strong"] and not a["simple"]
    assert {e["id"] for e in a["catalog"]} == {"pixel_art", "stylised_3d", "clay_finish", "retro_lowpoly", "chunky_pixels"} and a["help"]["meaning"]
    sh = stages["prep"]["shading"]
    assert sh["options"] == ["auto", "smooth", "flat"] and sh["option_labels"]["flat"] == "Flat faces" and not sh["simple"]
    assert 256 in stages["mesh"]["texture_size"]["options"] and stages["mesh"]["texture_size"]["option_labels"]["256"]
    assert guidance.STRENGTH_LABELS == {"off": "Off", "subtle": "Subtle", "normal": "Normal", "strong": "Strong"}


# --- the concept graphs --------------------------------------------------------------------------------------------


def _addons(model, **choices):
    return reference.resolve_addons(model, "concept", None, None, choices)["applied"]


def test_addon_chain_after_a_checkpoint_loader():
    g = cg.concept_graph("zturbo", "a goblin", 1, "p", addons=_addons("zturbo", pixel_art="subtle"))
    assert g["addon1"]["class_type"] == "LoraLoaderModelOnly" and g["addon1"]["inputs"]["model"] == ["1", 0]
    assert g["addon1"]["inputs"]["strength_model"] == 0.6 and g["addon1"]["inputs"]["lora_name"].startswith("ZImage\\")
    assert g["2"]["inputs"]["model"] == ["addon1", 0]  # ModelSamplingAuraFlow now reads the chain
    assert g["3"]["inputs"]["clip"] == ["1", 1] and g["7"]["inputs"]["vae"] == ["1", 2]  # CLIP and VAE untouched
    assert g["3"]["inputs"]["text"] == "a goblin Pixel art style."
    assert cg.concept_graph("zturbo", "a goblin", 1, "p") == cg.zimage_graph("a goblin", 1, "p")


def test_addon_chain_after_a_unet_loader_in_order():
    two = [{"id": "a", "file": "X/a.safetensors", "value": 0.5, "trigger": "aa"}, {"id": "b", "file": "X/b.safetensors", "value": 1.0}]
    for model in ("zbase", "klein", "anima", "chroma"):
        g = cg.concept_graph(model, "p", 1, "pre", addons=two)
        loader = next(k for k, n in g.items() if n["class_type"] == "UNETLoader")
        assert g["addon1"]["inputs"]["model"] == [loader, 0] and g["addon2"]["inputs"]["model"] == ["addon1", 0]
        users = [n for k, n in g.items() if not k.startswith("addon") and n["inputs"].get("model") == [loader, 0]]
        assert users == [], model
        assert any(n["inputs"].get("model") == ["addon2", 0] for n in g.values()), model
        assert g["addon1"]["inputs"]["lora_name"] == "X\\a.safetensors"


def test_pixel_painter_sets_its_own_lora_strength():
    g = cg.concept_graph("pixel", "p", 1, "pre", addons=_addons("pixel", pixel_art="subtle"))
    assert g["2"]["inputs"]["strength_model"] == 0.6 and "addon1" not in g
    assert g["4"]["inputs"]["text"] == "Pixel art style. p"  # the builder's own trigger, not a second copy
    assert cg.concept_graph("pixel", "p", 1, "pre", addons=_addons("pixel"))["2"]["inputs"]["strength_model"] == 1.0


def test_palette_sentence_and_prompt():
    assert concept_stage.palette_sentence(["#5b3a1e", "#c9a35c", "#2e4a6b", "#8a1c1c", "#e8dcc3"]) == \
        "Colour palette: mostly dark brown, gold, steel blue, dark red and cream."
    assert concept_stage.palette_sentence(["nothing", 3]) == "" and concept_stage.palette_sentence(None) == ""
    sc = SimpleNamespace(project=SimpleNamespace(style_lock={"prompt_block": "Painted.", "palette": ["#ffffff"]}),
                         asset=SimpleNamespace(prompt="A goblin.", parts=[]), style={"prompt_block": "x"}, category={"pose_block": "Stand."})
    assert concept_stage.build_prompt(sc, {}) == "A goblin. Painted. Colour palette: mostly white. Stand."


def test_concept_addons_from_recipe_lock_and_version():
    sc = SimpleNamespace(project=SimpleNamespace(style_lock={"loras": ["pixel_art:subtle", "who_knows"]}), style={"id": "painted"},
                         asset=SimpleNamespace(style_preset="painted", category="humanoid"), platform="pc_mid")
    r = concept_stage.resolve_concept_addons(sc, "zturbo", {})
    assert [(a["id"], a["strength"], a["source"]) for a in r["applied"]] == [("pixel_art", "subtle", "style lock")] and r["unknown"] == ["who_knows"]
    r = concept_stage.resolve_concept_addons(sc, "zturbo", {"addons": {"pixel_art": "strong"}})
    assert r["applied"][0]["strength"] == "strong" and r["applied"][0]["source"] == "version"
    assert concept_stage.resolve_concept_addons(sc, "klein", {})["applied"] == []


# --- mesh, turnaround, prep targets ------------------------------------------------------------------------------------


def test_mesh_targets_follow_the_recipe_unless_chosen():
    b = reference.budget("humanoid", "pc_mid")
    low = reference.recipe("lowpoly_flat", "humanoid", "pc_mid")
    assert mesh_stage.resolve_targets(low, {}, b) == {"target_faces": 4900, "target_faces_source": "recipe", "texture_size": 512, "texture_source": "recipe"}
    assert mesh_stage.resolve_targets(low, {"target_faces": 9000, "texture_size": 1024}, b)["target_faces_source"] == "chosen"
    painted = reference.recipe("painted", "humanoid", "pc_mid")
    assert mesh_stage.resolve_targets(painted, {}, b)["target_faces"] == mesh_stage.default_target_faces(20000) == 19600
    tiny = reference.recipe("lowpoly_flat", "prop_rigid", "mobile_low")
    assert mesh_stage.recipe_target_faces(tiny, 1000) == 294
    # A child run follows the recipe as it is now, unless the parent's value was chosen.
    assert mesh_stage.inherit({"target_faces": 4900, "target_faces_source": "recipe", "seed": 3}, {}) == {"seed": 3}
    assert mesh_stage.inherit({"target_faces": 9000, "target_faces_source": "chosen"}, {})["target_faces"] == 9000


def test_triangle_and_texture_checks_speak_of_the_look():
    b = reference.budget("humanoid", "pc_mid")
    low = reference.recipe("lowpoly_flat", "humanoid", "pc_mid")
    limit, vars_ = mesh_stage.triangle_target(low, {}, b)
    assert limit == 5000 and vars_["look"] == "Low-poly" and vars_["recipe"]
    c = guidance.describe_check({"name": "triangle_budget", "passed": True, "value": 4890, "limit": limit, "vars": vars_})
    assert c["help"] == "The model's 4,890 triangles fit the Low-poly look's target of 5,000; your platform would allow up to 20,000."
    bad = guidance.describe_check({"name": "triangle_budget", "passed": False, "value": 6000, "limit": limit, "vars": vars_})
    assert bad["help"].startswith("The model has 1,000 more triangles than the Low-poly look calls for")
    assert mesh_stage.triangle_target(low, {"target_faces": 9000, "target_faces_source": "chosen"}, b) == (20000, None)
    assert mesh_stage.triangle_target(reference.recipe("painted", "humanoid", "pc_mid"), {}, b) == (20000, None)
    tl, tv = mesh_stage.texture_target(reference.recipe("pixel", "humanoid", "pc_mid"), b)
    t = guidance.describe_check({"name": "texture_base_color_budget", "passed": True, "value": 256, "limit": tl, "vars": tv})
    assert t["help"] == "The base color texture is 256 px, the Pixel-art look's size (your platform would allow 2,048 px)."
    plain = guidance.describe_check({"name": "texture_base_color_budget", "passed": True, "value": 1024, "limit": 2048})
    assert plain["help"] == "The base color texture is 1,024 px, within the 2,048 px budget."


def test_prep_limit_and_shading():
    b = reference.budget("humanoid", "pc_mid")
    low = reference.recipe("lowpoly_flat", "humanoid", "pc_mid")
    assert prep_stage.prep_triangle_limit(low, {"target_faces": 4900, "target_faces_source": "recipe"}, b) == {"limit": 5000, "source": "recipe"}
    assert prep_stage.prep_triangle_limit(low, {"target_faces": 12000, "target_faces_source": "chosen"}, b) == {"limit": 20000, "source": "platform"}
    assert prep_stage.prep_triangle_limit(reference.recipe("painted", "humanoid", "pc_mid"), {}, b) == {"limit": 20000, "source": "platform"}
    assert prep_stage.resolve_shading(low, {}) == {"shading": "flat", "source": "recipe"}
    assert prep_stage.resolve_shading(low, {"shading": "smooth"}) == {"shading": "smooth", "source": "chosen"}
    assert prep_stage.resolve_shading(low, {"shading": "auto"})["shading"] == "flat"
    # A small project palette stays a prompt hint: forced onto the textures it repaints the model.
    look = prep_stage.texture_look(reference.recipe("pixel", "humanoid", "pc_mid"), ["#000000", "#ffffff", "nothex"])
    assert look["crisp"] and look["cap"] == 256 and look["palette"] is None and look["palette_colors"] == 32 and not look["normal_map"]
    big = [f"#{i * 16:02x}{i * 16:02x}{i * 16:02x}" for i in range(16)]
    look = prep_stage.texture_look(reference.recipe("pixel", "humanoid", "pc_mid"), big)
    assert look["palette"] is not None and len(look["palette"]) == 16
    assert prep_stage.texture_look(reference.recipe("retro64", "humanoid", "pc_mid"), ["#000000"])["palette"] is None


def test_turnaround_inherit_drops_a_recipe_strength():
    assert turn_stage.inherit({"lora_strength": 0.9, "lora_strength_source": "recipe", "views": ["front"]}, {}) == {"views": ["front"]}
    assert turn_stage.inherit({"lora_strength": 1.1, "lora_strength_source": "chosen"}, {})["lora_strength"] == 1.1


# --- textures ----------------------------------------------------------------------------------------------------------


def test_quantise_and_shrink(tmp_path):
    rng = np.random.default_rng(3)
    base = rng.uniform(0, 1, (64, 64, 3)).astype(np.float32)
    q = imaging.quantise_colours(base, None, 16)
    assert len(np.unique((q * 255).round().reshape(-1, 3), axis=0)) <= 16
    fixed = imaging.quantise_colours(base, None, palette=[(0, 0, 0), (255, 255, 255)])
    assert set(np.unique((fixed * 255).round())) <= {0.0, 255.0}
    p = imaging.write_map(base, tmp_path / "b.png")
    src = np.array(Image.open(p))
    small = np.array(Image.open(imaging.shrink_image(p, 16, True, tmp_path / "n.png")))
    assert small.shape == (16, 16, 3) and {tuple(c) for c in small.reshape(-1, 3)} <= {tuple(c) for c in src.reshape(-1, 3)}
    soft = imaging.shrink_image(p, 16, False, tmp_path / "s.png")
    assert imaging.image_size(soft) == (16, 16)
    assert imaging.image_size(imaging.shrink_image(p, 128, True, tmp_path / "same.png")) == (64, 64)
    assert imaging.palette_names(["#ff0000", "#c82828", "#00f"]) == ["red", "blue"]


def _uv_glb(path: Path, base: np.ndarray, normal: bool = True) -> Path:
    trimesh = pytest.importorskip("trimesh")
    v = np.array([[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1], [2, 0, 0], [3, 0, 0], [3, 1, 0], [2, 1, 0]], float)
    f = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7]])
    uv = np.array([[0.05, 0.95], [0.45, 0.95], [0.45, 0.55], [0.05, 0.55], [0.55, 0.45], [0.95, 0.45], [0.95, 0.05], [0.55, 0.05]])
    mat = trimesh.visual.material.PBRMaterial(
        baseColorTexture=Image.fromarray(base), metallicRoughnessTexture=Image.fromarray(np.full(base.shape, 128, np.uint8)),
        normalTexture=Image.fromarray(np.full(base.shape, (128, 128, 255), np.uint8)) if normal else None)
    trimesh.Trimesh(v, f, visual=trimesh.visual.TextureVisuals(uv=uv, material=mat), process=False).export(path)
    return path


def _gltf(path: Path) -> dict:
    data = Path(path).read_bytes()
    n = struct.unpack_from("<I", data, 12)[0]
    return json.loads(data[20:20 + n])


def test_glb_drop_normal_and_nearest(tmp_path):
    rng = np.random.default_rng(1)
    glb = _uv_glb(tmp_path / "m.glb", rng.integers(0, 255, (64, 64, 3), dtype=np.uint8))
    assert "normalTexture" in _gltf(glb)["materials"][0]
    before = glb.stat().st_size
    assert imaging.glb_replace_images(glb, drop_normal=True, nearest=True, mr_size=16)
    g = _gltf(glb)
    assert "normalTexture" not in g["materials"][0] and glb.stat().st_size < before
    assert all(s["magFilter"] == imaging.GL_NEAREST and s["minFilter"] == imaging.GL_NEAREST for s in g["samplers"])
    assert imaging.load_uv_mesh(glb) is not None  # still a valid GLB with its layout
    assert not imaging.glb_replace_images(tmp_path / "missing.txt") if (tmp_path / "missing.txt").write_bytes(b"x") else True


def test_texture_pass_gives_the_pixel_look(tmp_path):
    rng = np.random.default_rng(2)
    base = rng.integers(0, 255, (64, 64, 3), dtype=np.uint8)
    glb = _uv_glb(tmp_path / "prep.glb", base)
    maps = {"base_color": tmp_path / "in_base.png", "metallic": tmp_path / "in_met.png", "roughness": tmp_path / "in_rough.png"}
    Image.fromarray(base).save(maps["base_color"])
    Image.fromarray(np.full((64, 64), 10, np.uint8)).save(maps["metallic"])
    Image.fromarray(np.full((64, 64), 200, np.uint8)).save(maps["roughness"])
    out = tmp_path / "out"
    out.mkdir()
    look = {"cap": 16, "crisp": True, "pixel_snap": True, "palette": None, "palette_colors": 8, "normal_map": False, "style": "pixel"}
    res = prep_stage.texture_pass(glb, glb, maps, out, 64, "keep", False, False, look)
    m, files = res["metrics"], res["files"]
    assert imaging.image_size(files["base_color"]) == (16, 16) and imaging.image_size(files["metallic"]) == (16, 16)
    assert m["texture_size_shipped"] == 16 and m["texture_colours"] <= 8 and m["palette_limit"] == 8 and m["palette_source"] == "recipe"
    assert np.array_equal(np.array(Image.open(files["base_color_original"])), base)
    g = _gltf(glb)
    assert "normalTexture" not in g["materials"][0] and g["samplers"][0]["magFilter"] == imaging.GL_NEAREST
    assert m["texture_padding_target_px"] == imaging.unwrap_padding(64)  # the layout is still measured at the bake size

    # A fixed palette from the style lock.
    out2 = tmp_path / "out2"
    out2.mkdir()
    res2 = prep_stage.texture_pass(_uv_glb(tmp_path / "b.glb", base), tmp_path / "b.glb", maps, out2, 64, "keep", False, False,
                                   {**look, "palette": [(0, 0, 0), (255, 255, 255)]})
    assert res2["metrics"]["palette_source"] == "style lock" and res2["metrics"]["texture_colours"] <= 2


def test_texture_pass_without_a_look_is_unchanged(tmp_path):
    base = np.full((64, 64, 3), 120, np.uint8)
    glb = _uv_glb(tmp_path / "p.glb", base)
    maps = {"base_color": tmp_path / "b.png"}
    Image.fromarray(base).save(maps["base_color"])
    res = prep_stage.texture_pass(glb, glb, maps, tmp_path, 64, "keep", False)
    assert res["files"] == {} and "texture_size_shipped" not in res["metrics"]
    assert "normalTexture" in _gltf(glb)["materials"][0]


def test_look_check_sentences(tmp_path):
    low = reference.recipe("lowpoly_flat", "humanoid", "pc_mid")
    p = tmp_path / "b.png"
    Image.new("RGB", (512, 512)).save(p)
    ok = [guidance.describe_check(c) for c in prep_stage.look_checks(low, {"shading": "flat"}, {"shading_applied": "flat"}, {"base_color": p})]
    assert ok[0]["passed"] and ok[0]["help"] == "Low-poly look applied: about 5,000 triangles, flat faces, 512 px textures, no surface bumps."
    big = tmp_path / "big.png"
    Image.new("RGB", (1024, 1024)).save(big)
    bad = guidance.describe_check(prep_stage.look_checks(low, {}, {"shading_applied": "smooth", "shading_source": "recipe"},
                                                         {"base_color": big, "normal": big})[0])
    assert not bad["passed"] and bad["help"].startswith("The Low-poly look is not fully applied: the textures are 1024 px, not 512 px")
    assert bad["fix"] == {"stage": "prep", "params": {}, "label": "Apply the look again"}
    # Looks that keep the full pipeline, and the material category, get no look check.
    assert prep_stage.look_checks(reference.recipe("painted", "humanoid", "pc_mid"), {}, {}, {}) == []
    assert prep_stage.look_checks(low, {}, {}, {}, "material") == []


# --- export and the importer -------------------------------------------------------------------------------------


def _export_sc(tmp_path, style, prep_metrics=None):
    prep_v = SimpleNamespace(metrics=prep_metrics or {}, params={})
    return SimpleNamespace(dir=tmp_path, style={"id": style}, asset=SimpleNamespace(style_preset=style, category="humanoid"),
                           platform="pc_mid", category={"rig_engine": "none"}, settings=SimpleNamespace(clips_libraries={}),
                           stage_version=lambda s: prep_v if s == "prep" else None)


def test_export_engine_args_and_pack_look(tmp_path):
    px = _export_sc(tmp_path, "pixel")
    args, _, _ = export_stage._unity_args(px, {}, "gob")
    assert {k: args[k] for k in ("-vgacFilter", "-vgacMipmaps", "-vgacCompress", "-vgacNormalMap")} == \
        {"-vgacFilter": "point", "-vgacMipmaps": "off", "-vgacCompress": "off", "-vgacNormalMap": "off"}
    assert export_stage.pack_look(px) == {"shading": "smooth", "texture_filter": "crisp"} and not export_stage.ships_normal(px)
    low = _export_sc(tmp_path, "lowpoly_flat", {"shading_applied": "flat"})
    assert export_stage.pack_look(low) == {"shading": "flat", "texture_filter": "smooth"}
    painted = _export_sc(tmp_path, "painted")
    assert export_stage.engine_args(painted)["-vgacFilter"] == "bilinear" and export_stage.ships_normal(painted)
    # A context with no style (older test doubles) gets today's defaults.
    assert export_stage.engine_args(SimpleNamespace(dir=tmp_path)) == {"-vgacFilter": "bilinear", "-vgacMipmaps": "on", "-vgacCompress": "on",
                                                                        "-vgacNormalMap": "on"}
    assert export_stage.ships_normal(SimpleNamespace())


def test_materials_connected_without_a_bump_map():
    u = {"texturesAssigned": ["base_color", "ao", "metallic_smoothness"]}
    c = export_stage.unity_checks(u, humanoid=False, rigged=False, expect_normal=False)[0]
    assert c["passed"] and c["help"] == "Unity uses all the surface textures: colour, shadows, metal and shine (this look has no surface bumps)."
    c = export_stage.unity_checks(u, humanoid=False, rigged=False)[0]
    assert not c["passed"] and c["vars"] == {"missing": "bump", "effect": "flat"}


def test_importer_version_7_takes_the_engine_settings():
    src = IMPORTER.read_text(encoding="utf-8")
    assert export_stage.importer_version(IMPORTER) >= 7
    for token in ("-vgacFilter", "-vgacMipmaps", "-vgacCompress", "-vgacNormalMap", "FilterMode.Point", "mipmapEnabled",
                  "TextureImporterCompression.Uncompressed", "textureFilter"):
        assert token in src, token


def test_suggest_carries_the_recipe_line():
    s = guidance.suggest("quadruped", "pixel", "mobile_low", reference.categories(), reference.styles(), reference.platforms())
    assert s["recipe_plain"] == reference.recipe("pixel", "quadruped", "mobile_low")["plain"]
    assert guidance.suggest("humanoid", "nope", "pc_mid", reference.categories(), reference.styles(), reference.platforms())["recipe_plain"] == ""


def test_styles_yaml_recipes_only_use_known_fields():
    data = yaml.safe_load((BACKEND / "vgac" / "data" / "styles.yaml").read_text(encoding="utf-8"))
    for s in data["styles"]:
        assert set(s.get("recipe") or {}) <= set(reference.RECIPE_DEFAULTS), s["id"]
        for sec, body in (s.get("recipe") or {}).items():
            if isinstance(reference.RECIPE_DEFAULTS[sec], dict) and reference.RECIPE_DEFAULTS[sec]:
                assert set(body) <= set(reference.RECIPE_DEFAULTS[sec]) | {"filter_mode", "mipmaps", "compress"}, (s["id"], sec)
        for cid in s.get("by_category") or {}:
            assert cid in {c["id"] for c in reference.categories()}
