from vgac import reference


def test_eleven_categories_with_required_fields():
    cats = reference.categories()
    assert len(cats) == 11
    ids = {c["id"] for c in cats}
    assert ids == {"humanoid", "quadruped", "winged", "multileg", "serpent", "vehicle", "prop_rigid", "prop_articulated", "environment", "foliage", "material"}
    for c in cats:
        for key in ("label", "rig_engine", "symmetric", "pose_block", "size_m", "parts_default", "budgets"):
            assert key in c, (c["id"], key)
        assert c["rig_engine"] in ("skintoken_mixamo", "skintoken", "none")
        for platform in ("mobile_low", "pc_mid", "high"):
            assert set(c["budgets"][platform]) == {"triangles", "texture"}
        assert c["pose_block"] and "\n" not in c["pose_block"]


def test_humanoid_pose_block_matches_bakeoff_text():
    block = reference.category("humanoid")["pose_block"]
    assert block.startswith("The character stands in a relaxed A-pose")
    assert block.endswith("nothing held in the hands.")


def test_styles_and_platforms():
    styles = reference.styles()
    assert {s["id"] for s in styles} == {"painted", "cel", "pixel", "retro64", "lowpoly_flat", "realistic_pbr", "clay"}
    for s in styles:
        assert s["model"] in reference.CONCEPT_MODELS
        assert s["prompt_block"]
    assert reference.style("painted")["model"] == "zturbo"
    plats = reference.platforms()
    assert [p["id"] for p in plats] == ["mobile_low", "pc_mid", "high"]
    for p in plats:
        assert set(p) >= {"id", "label", "triangles_character", "triangles_prop", "texture"}


def test_budget_lookup():
    assert reference.budget("humanoid", "pc_mid") == {"triangles": 20000, "texture": 2048}
    assert reference.budget("prop_rigid", "mobile_low") == {"triangles": 1000, "texture": 512}
    assert reference.budget("material", "high")["triangles"] == 0


def test_stage_schemas_shape():
    stages = reference.stages()
    assert [s["id"] for s in stages] == list(reference.STAGE_ORDER)
    allowed = {"number", "integer", "boolean", "select", "multiselect", "string"}
    for s in stages:
        assert s["label"]
        for p in s["adjust_schema"]:
            assert p["type"] in allowed, (s["id"], p)
            assert "name" in p and "default" in p and "label" in p
            if p["type"] in ("select", "multiselect"):
                assert p["options"]
    names = {p["name"] for p in reference.STAGE_SCHEMAS["export"]}
    assert {"lods", "collider", "formats", "unity", "clips_library", "sprites", "sprite_directions", "sprite_size"} <= names


def test_model_records_have_licences():
    for mid in ("zturbo", "trellis2", "hy3d_mv", "skintoken", "birefnet", "qwen_edit"):
        rec = reference.model_record(mid)
        assert rec["licence"]
