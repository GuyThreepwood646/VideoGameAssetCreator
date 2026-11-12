"""More triangles on the head (vgac/headtris.py, blender/headweight.py): the budget arithmetic of the 3D and clean-up
steps, which looks and categories get it, the guard that keeps an over-budget model from the rig, and the check
sentences."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from vgac import guidance, headtris, quality, reference
from vgac.stages import mesh as mesh_stage
from vgac.stages import prep as prep_stage
from vgac.stages import rig as rig_stage

B = reference.budget("humanoid", "pc_mid")  # 20,000 triangles


def rec(style: str, category: str = "humanoid") -> dict:
    return reference.recipe(style, category, "pc_mid")


# --- which looks and categories ------------------------------------------------------------------------------------


@pytest.mark.parametrize("style", ["painted", "cel", "clay", "realistic_pbr"])
def test_smooth_full_budget_humanoids_get_it(style):
    assert headtris.applies(rec(style), "humanoid")


@pytest.mark.parametrize("style", ["lowpoly_flat", "pixel", "retro64"])
def test_flat_crisp_and_reduced_looks_do_not(style):
    assert not headtris.applies(rec(style), "humanoid")


@pytest.mark.parametrize("category", ["quadruped", "prop_rigid", "winged", "environment"])
def test_only_humanoids(category):
    assert not headtris.applies(rec("painted", category), category)


def test_a_custom_look_with_the_default_recipe_gets_it():
    custom = reference.recipe("my_own_look", "humanoid", "pc_mid", strict=False)
    assert headtris.applies(custom, "humanoid")


def test_plan_reasons():
    room = {"head_room": 2.0, "target_faces": 39200}
    painted = rec("painted")
    assert headtris.plan(painted, "humanoid", {}, "smooth", room) == {"weighted": True, "reason": None}
    assert headtris.plan(painted, "humanoid", {"head_triangles": False}, "smooth", room)["reason"] == "off"
    assert headtris.plan(painted, "humanoid", {}, "flat", room)["reason"] == "flat"  # the shading knob chose flat
    assert headtris.plan(rec("lowpoly_flat"), "humanoid", {}, "flat", room)["reason"] == "look"
    # A 3D model built at the budget (every version made before this) is left as it was.
    assert headtris.plan(painted, "humanoid", {}, "smooth", {"target_faces": 19600, "target_faces_source": "recipe"})["reason"] == "no_room"


# --- budget arithmetic ---------------------------------------------------------------------------------------------


def test_the_3d_step_asks_for_room_only_where_it_applies():
    painted = rec("painted")
    t = mesh_stage.resolve_targets(painted, {}, B, "humanoid")
    assert t["target_faces"] == int(2.5 * 19600) == 49000 and t["head_room"] == headtris.HEAD_ROOM and t["target_faces_source"] == "recipe"
    # Without the category (older callers) and for other categories: unchanged.
    assert "head_room" not in mesh_stage.resolve_targets(painted, {}, B)
    assert mesh_stage.resolve_targets(rec("painted", "prop_rigid"), {}, reference.budget("prop_rigid", "pc_mid"), "prop_rigid").get("head_room") is None
    # Low-poly keeps its quarter of the budget and no room.
    low = mesh_stage.resolve_targets(rec("lowpoly_flat"), {}, B, "humanoid")
    assert low["target_faces"] == 4900 and "head_room" not in low
    # A chosen count is never raised.
    chosen = mesh_stage.resolve_targets(painted, {"target_faces": 15000}, B, "humanoid")
    assert chosen["target_faces"] == 15000 and "head_room" not in chosen


def test_the_3d_check_measures_a_model_with_room_against_its_own_target():
    painted = rec("painted")
    params = {"target_faces": 39200, "target_faces_source": "recipe", "head_room": 2.0}
    limit, vars_ = mesh_stage.triangle_target(painted, params, B)
    assert limit == 39200 and vars_ == {"head_room": True, "platform_triangles": 20000}
    # Kept versions built at the budget: unchanged.
    assert mesh_stage.triangle_target(painted, {"target_faces": 19600, "target_faces_source": "recipe"}, B) == (20000, None)
    assert headtris.mesh_limit({"head_room": 2.0}, 20000) == 40000  # no target recorded: room times the platform


def test_the_clean_up_step_shrinks_a_model_with_room_to_the_platform_budget():
    params = {"target_faces": 39200, "target_faces_source": "recipe", "head_room": 2.0}
    assert prep_stage.prep_triangle_limit(rec("painted"), params, B) == {"limit": 20000, "source": "platform"}


def test_a_new_run_recomputes_the_room():
    # inherit drops a recipe target; the run then resolves it again (and sets head_room again, or None).
    merged = mesh_stage.inherit({"target_faces": 39200, "target_faces_source": "recipe", "head_room": 2.0, "seed": 5}, {})
    assert "target_faces" not in merged
    assert mesh_stage.resolve_targets(rec("painted"), merged, B, "humanoid")["target_faces"] == 49000


def test_head_metrics_from_the_blender_report():
    report = {"head_triangles": 2430, "head_weighted": True, "head_triangles_even": 1308, "head_triangles_source": 2439,
              "head_weight_factor": 1e-6, "head_weight_seconds": 1.5}
    m = prep_stage.head_metrics(report, {"weighted": True, "reason": None})
    assert m["head_weighted"] and m["head_triangles"] == 2430 and m["head_triangles_even"] == 1308
    off = prep_stage.head_metrics({"head_triangles": 1241}, {"weighted": False, "reason": "no_room"})
    assert off == {"head_triangles": 1241, "head_weighted": False, "head_weight_reason": "no_room"}
    failed = prep_stage.head_metrics({"head_triangles": 1300, "head_weighted": False, "head_weight_error": "boom"}, {"weighted": True, "reason": None})
    assert failed["head_weight_reason"] == "error" and failed["head_weight_error"] == "boom"


# --- the guard before the rig --------------------------------------------------------------------------------------


def test_handoff_problem():
    room = {"head_room": 2.0, "target_faces": 39200}
    assert headtris.handoff_problem({}, 39000, None, 20000) is None  # no room: as before
    assert headtris.handoff_problem(room, 39194, 19900, 20000) is None  # cleaned up to the budget
    assert "Run Clean up for the game first" in headtris.handoff_problem(room, 39194, None, 20000)
    assert "Run Clean up for the game again" in headtris.handoff_problem(room, 39194, 20400, 20000)
    assert headtris.handoff_problem(room, 19000, None, 20000) is None  # the builder came in under the budget anyway


def _rig_sc(tmp_path: Path, mesh_params, mesh_tris, prep=None):
    mesh_v = SimpleNamespace(params=mesh_params, metrics={"triangles": mesh_tris})
    versions = {"mesh": mesh_v, "prep": prep}
    return SimpleNamespace(stage_version=lambda s: versions.get(s), settings=None, budget=dict(B))


def test_the_rig_refuses_a_model_over_the_budget_on_purpose(tmp_path, monkeypatch):
    mesh_glb, prep_glb = tmp_path / "model.glb", tmp_path / "prep.glb"
    mesh_glb.write_bytes(b"x")
    prep_glb.write_bytes(b"x")
    room = {"head_room": 2.0, "target_faces": 39200}
    prep_v = SimpleNamespace(metrics={"triangles": 19900})
    monkeypatch.setattr(rig_stage.library, "version_file", lambda v, key, settings: prep_glb if v is prep_v and key == "glb" else None)
    # The clean-up step was skipped: the rig would get the 3D model itself.
    with pytest.raises(ValueError, match="Clean up for the game first"):
        rig_stage.guard_budget(_rig_sc(tmp_path, room, 39194), mesh_glb)
    # Cleaned up to the budget: on it goes.
    rig_stage.guard_budget(_rig_sc(tmp_path, room, 39194, prep_v), prep_glb)
    # A clean-up result still over the budget (its decimation failed): refused.
    over = SimpleNamespace(metrics={"triangles": 21000})
    monkeypatch.setattr(rig_stage.library, "version_file", lambda v, key, settings: prep_glb if v is over and key == "glb" else None)
    with pytest.raises(ValueError, match="again"):
        rig_stage.guard_budget(_rig_sc(tmp_path, room, 39194, over), prep_glb)
    # Older models built at the budget pass without a clean-up model, as before.
    rig_stage.guard_budget(_rig_sc(tmp_path, {"target_faces": 19600}, 19594), mesh_glb)


# --- check sentences -----------------------------------------------------------------------------------------------


def test_the_3d_step_sentence_says_over_on_purpose():
    limit, vars_ = mesh_stage.triangle_target(rec("painted"), {"target_faces": 39200, "head_room": 2.0}, B)
    tris = 39194
    c = quality.check("triangle_budget", tris <= limit * (1 + quality.TRIANGLE_BUDGET_TOLERANCE), tris, limit, vars=vars_)
    assert c["passed"] and c["severity"] == "block"
    assert c["help"] == ("The model's 39,194 triangles are more than your platform's 20,000 on purpose: Clean up for the game "
                         "brings it back to the budget with weighted decimation (more triangles on the head).")
    bad = quality.check("triangle_budget", False, 42000, limit, vars=vars_)
    assert bad["help"] == "The model has 2,800 more triangles than the 3D step asked for (42,000 of 39,200, which already leaves room for the head)."
    assert bad["fix"]["stage"] == "prep"


def test_the_clean_up_sentence_says_how_many_triangles_the_head_has():
    m = {"triangles": 19900, "head_weighted": True, "head_triangles": 2430, "head_triangles_even": 1308}
    hv = headtris.head_vars(m)
    assert hv == {"head_weighted": True, "head_triangles": 2430, "head_even": 1308, "head_share": 0.1221}
    checks = prep_stage.prep_checks({"triangles": 19900}, {}, m, "humanoid", 20000, hv)
    c = next(c for c in checks if c["name"] == "triangle_budget")
    assert c["passed"]
    assert c["help"] == ("The model's 19,900 triangles fit your platform's budget of 20,000. "
                         "The head has 2,430 of them (12.2%); an even spread would have left it 1,308.")
    # Not weighted (off, another look, a model built at the budget): the sentence is as before.
    assert headtris.head_vars({"triangles": 19594, "head_weighted": False, "head_triangles": 1241}) is None
    plain = guidance.describe_check({"name": "triangle_budget", "passed": True, "value": 19594, "limit": 20000})
    assert plain["help"] == "The model's 19,594 triangles fit your platform's budget of 20,000."


def test_the_option_is_in_advanced_with_the_real_term_first():
    spec = {p["id"] if "id" in p else p["name"]: p for p in reference.STAGE_SCHEMAS["prep"]}
    p = spec[headtris.PARAM]
    assert p["default"] is True and p["categories"] == ["humanoid"]
    assert p["label"] == "Weighted decimation (more triangles on the head)"
    assert guidance.PARAM_HELP[headtris.PARAM]["simple"] is False
    assert headtris.on({}) and not headtris.on({"head_triangles": False})
