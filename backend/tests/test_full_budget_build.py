"""Buildings are built at the platform's triangle and texture budget and cleaned up to the look's budget
(category file `build_at_platform_budget`, params `full_budget`): the targets of the 3D step, the limits its checks
measure against, and the limit the clean-up step reduces to."""

from __future__ import annotations

from vgac import reference
from vgac.stages import mesh as mesh_stage
from vgac.stages import prep as prep_stage

B = reference.budget("environment", "pc_mid")  # 15,000 triangles, 2,048 px


def rec(style: str, category: str = "environment") -> dict:
    return reference.recipe(style, category, "pc_mid")


def test_lowpoly_building_is_built_at_the_platform_budget():
    t = mesh_stage.resolve_targets(rec("lowpoly_flat"), {}, B, "environment")
    assert t["target_faces"] == mesh_stage.default_target_faces(B["triangles"]) == 14700
    assert t["texture_size"] == 2048
    assert t["target_faces_source"] == "recipe" and t["texture_source"] == "recipe"
    assert t["full_budget"] is True


def test_the_clean_up_step_reduces_it_to_the_looks_budget():
    r = rec("lowpoly_flat")
    t = mesh_stage.resolve_targets(r, {}, B, "environment")
    params = {"target_faces": t["target_faces"], "target_faces_source": t["target_faces_source"], "full_budget": True}
    limit = prep_stage.prep_triangle_limit(r, params, B)
    assert limit == {"limit": 1500, "source": "recipe"}


def test_the_checks_of_the_3d_step_measure_against_the_platform():
    r = rec("lowpoly_flat")
    params = {"target_faces": 14700, "target_faces_source": "recipe", "full_budget": True}
    assert mesh_stage.triangle_target(r, params, B) == (15000, None)
    assert mesh_stage.texture_target(r, B, baked=True, full_budget=True) == (2048, None)
    # the same look built the old way still measures against its own, smaller budget
    limit, vars_ = mesh_stage.triangle_target(r, {"target_faces": 1470, "target_faces_source": "recipe"}, B)
    assert limit == 1500 and vars_["recipe"]
    assert mesh_stage.texture_target(r, B, baked=True)[0] == 512


def test_other_looks_and_categories_keep_their_route():
    painted = mesh_stage.resolve_targets(rec("painted"), {}, B, "environment")
    assert "full_budget" not in painted and painted["target_faces"] == 14700 and painted["texture_size"] == 2048
    prop = reference.budget("prop_rigid", "pc_mid")
    low_prop = mesh_stage.resolve_targets(rec("lowpoly_flat", "prop_rigid"), {}, prop, "prop_rigid")
    assert "full_budget" not in low_prop and low_prop["texture_size"] == 512 and low_prop["target_faces"] < 1500
    retro = mesh_stage.resolve_targets(rec("retro64"), {}, B, "environment")
    assert "full_budget" not in retro
    assert "full_budget" not in mesh_stage.resolve_targets(rec("lowpoly_flat"), {}, B)  # no category given


def test_a_chosen_count_or_size_is_kept():
    r = rec("lowpoly_flat")
    chosen = mesh_stage.resolve_targets(r, {"target_faces": 3000, "texture_size": 1024}, B, "environment")
    assert chosen["target_faces"] == 3000 and chosen["texture_size"] == 1024
    assert "full_budget" not in chosen
    only_size = mesh_stage.resolve_targets(r, {"texture_size": 1024}, B, "environment")
    assert only_size["texture_size"] == 1024 and only_size["full_budget"] is True


def test_a_new_run_follows_the_route_again():
    merged = mesh_stage.inherit({"target_faces": 14700, "target_faces_source": "recipe", "texture_size": 2048,
                                 "texture_source": "recipe", "full_budget": True}, {})
    assert "target_faces" not in merged and "texture_size" not in merged
    assert mesh_stage.resolve_targets(rec("lowpoly_flat"), merged, B, "environment")["target_faces"] == 14700
