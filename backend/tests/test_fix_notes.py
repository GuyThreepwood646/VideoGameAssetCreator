"""Honest fix labels (vgac/fixnotes.py): every check id says what a person can do about a failure.

A new check cannot ship without a fix or a plain line: the coverage test fails until it has one."""
from __future__ import annotations

import re

from vgac import fixnotes, guidance
from vgac.db import _with_fix_notes

# Every check id seen on the stored test runs (library/vgac.sqlite, read-only) when this was written, guided or not.
STORED_IDS = """
added_moves albedo_range arms_away atlas_used bake_matches baked_light bald_picture big_enough bones clips clips_face_forward
clips_loop collider_count collider_present collider_triangles collider_vertices compose_match controller_ready degenerate_faces
detail_size even_lighting face_drawn face_finder face_matches_picture facing facing_tests fingers grid_fit grip_found hair_bound
hair_fit hair_matches_picture hair_material hair_material_shipped hair_off_eyes hair_skin_through hair_triangles head_build height_m
hitbox_cover hitbox_fit hitbox_overreach in_frame item_above_ground item_clear_of_body items_apart joins legs_moved library_clips
limb_joins lod1_ratio lod2_ratio lod3_ratio lod_count look_applied manifold materials_connected max_influences metal_mixed
normal_map_upright one_subject openings_clear picture_projection placement_exact pose_for_3d pose_guard pose_held_build rig_restore
seam_error second_quarter silhouette_iou single_root size_m skin_not_metal soft_light springs springs_shipped straight_on
texture_ao_budget texture_ao_pow2 texture_ao_square texture_base_color_budget texture_base_color_pow2 texture_base_color_square
texture_colour_matches_picture texture_height_budget texture_height_pow2 texture_height_square texture_matches_picture
texture_metallic_budget texture_metallic_pow2 texture_metallic_square texture_normal_budget texture_normal_pow2 texture_normal_square
texture_padding texture_roughness_budget texture_roughness_pow2 texture_roughness_square texture_sharp textures_full_size
tiles_cleanly triangle_budget unity_humanoid_avatar unity_import uv_set watertight weight_coverage weights_normalised wings_found
""".split()

EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⬀-⯿]")


def _universe() -> list[str]:
    return sorted(set(guidance.CHECK_GUIDE) | set(STORED_IDS))


def test_every_check_id_has_a_fix_or_a_line():
    """The guide's ids and every stored id: a fix, a rerun, a re-roll button, or a no-button line. A new id with none fails here."""
    uncovered = [i for i in _universe() if fixnotes.covered(i, guidance.CHECK_GUIDE.get(i)) is None]
    assert uncovered == [], ("These checks have no fix and no plain line: add a guide fix, or an entry in fixnotes.NOTES "
                             f"(what to do in the app, or the one outside step): {uncovered}")


def test_the_twenty_four_without_a_fix_and_the_eight_unguided_are_all_named():
    """The owner's list: 24 guided checks with no fix, and the ids that had no guide entry at all."""
    no_fix = [i for i, g in guidance.CHECK_GUIDE.items() if "fix" not in g]
    for i in no_fix:
        assert fixnotes.covered(i, guidance.CHECK_GUIDE[i]) in ("by_hand", "outside", "legacy", "extra"), i
    for i in ("facing", "fingers", "joins", "limb_joins", "height_m", "lod1_ratio", "lod2_ratio", "lod3_ratio"):
        assert fixnotes.covered(i, guidance.CHECK_GUIDE.get(i)) is not None, i
    assert len(no_fix) >= 20


def test_lines_are_plain_one_or_two_sentences_without_emoji():
    texts = [t for _k, t in fixnotes.NOTES.values()] + list(fixnotes.RERUN.values()) + [e["note"] for e in fixnotes.EXTRA_FIXES.values()]
    texts += [fixnotes.FALLBACK_NOTE, fixnotes.PATTERN_NOTE[1], fixnotes.LOD_NOTE[1]]
    for t in texts:
        assert t.strip() == t and t.endswith("."), t
        assert not EMOJI.search(t), t
        assert len(t) < 330, t
    for kind, _t in fixnotes.NOTES.values():
        assert kind in ("by_hand", "outside", "legacy")


def test_outside_the_app_lines_say_so():
    """A check nothing in the app fixes says "Nothing in the app fixes this" and gives the one step outside it."""
    outside = [i for i, (k, _t) in fixnotes.NOTES.items() if k == "outside"]
    assert "unity_import" in outside
    for i in outside:
        assert "Nothing in the app fixes this" in fixnotes.NOTES[i][1], i


def test_a_rerun_button_says_try_again_and_why():
    for cid in ("clips_face_forward", "weight_coverage", "lod_count", "controller_ready", "look_applied"):
        c = guidance.describe_check({"name": cid, "id": cid, "passed": False, "value": 1, "limit": 0, "vars": {}})
        assert c["fix_label"] == c["fix"]["label"] == "Try again", cid
        assert c["fix_kind"] == "rerun" and ("same settings" in c["fix_note"] or cid == "clips_face_forward"), cid
    c = guidance.describe_check({"name": "clips_face_forward", "id": "clips_face_forward", "passed": False, "value": 178, "limit": 45, "vars": {}})
    assert "Clean up for the game" in c["fix_note"] and c["fix"]["stage"] == "export"


def test_every_rerun_id_really_is_a_rerun_in_the_guide():
    """Each id in RERUN has a guide fix that changes no setting (so the label is true), and the real fixes are not relabelled."""
    for cid in fixnotes.RERUN:
        g = guidance.CHECK_GUIDE[cid]
        sample = g["fix"]({"value": 1, "limit": 1, "vars": {}})
        assert sample and sample.get("params") == {} and not sample.get("op") and not sample.get("ui"), cid
    for cid in ("triangle_budget", "size_m", "hitbox_cover", "metal_mixed", "hair_matches_picture"):
        assert cid not in fixnotes.RERUN
    c = guidance.describe_check({"name": "size_m", "id": "size_m", "passed": False, "value": 2, "limit": 1.8, "vars": {}})
    assert c["fix_label"] == "Set the size" and "fix_note" not in c


def test_a_check_with_no_button_carries_its_line():
    c = guidance.describe_check({"name": "unity_import", "id": "unity_import", "passed": False, "value": 1, "limit": 0, "vars": {}})
    assert "fix" not in c and c["fix_kind"] == "outside" and c["fix_note"].startswith("Nothing in the app fixes this")
    c = guidance.describe_check({"name": "silhouette_iou", "id": "silhouette_iou", "passed": False, "value": 0.5, "limit": 0.8, "vars": {}})
    assert "fix" not in c and c["fix_kind"] == "by_hand" and "another 3D builder" in c["fix_note"]
    # the texture size lines and detail-level ratios (pattern ids) carry a line too
    for cid in ("texture_normal_pow2", "lod2_ratio"):
        c = guidance.describe_check({"name": cid, "id": cid, "passed": False, "value": 100, "limit": 64, "vars": {}})
        assert c["fix_note"] and c["fix_kind"] == "by_hand", cid


def test_new_buttons_for_checks_that_had_none_are_a_new_try():
    c = guidance.describe_check({"name": "manifold", "id": "manifold", "passed": False, "value": 50, "limit": 0, "vars": {}})
    assert c["fix"] == {"stage": "mesh", "params": {"seed": None}, "label": "Build the 3D model again"}
    assert "new random draw" in c["fix_note"] and c["fix_kind"] == "rerun"


def test_passing_and_unmeasured_checks_get_no_line():
    ok = guidance.describe_check({"name": "unity_import", "id": "unity_import", "passed": True, "value": 0, "limit": 0, "vars": {}})
    assert "fix_note" not in ok and "fix_kind" not in ok
    unmeasured = guidance.describe_check({"name": "unity_import", "id": "unity_import", "passed": False, "value": "not measured", "limit": None,
                                          "vars": {"measured": False, "what": "the import", "why": "Unity was not found"}})
    assert "fix_note" not in unmeasured


def test_stored_versions_get_the_lines_without_changing_what_is_stored():
    metrics = {"checks": [{"name": "unity_import", "id": "unity_import", "passed": False, "value": 1, "limit": 0},
                          {"name": "lod_count", "id": "lod_count", "passed": False, "value": 1, "limit": 3,
                           "fix": {"stage": "export", "params": {}, "label": "Rebuild the detail levels"}},
                          {"name": "manifold", "id": "manifold", "passed": True, "value": 0, "limit": 0}]}
    out = _with_fix_notes(metrics)
    assert out["checks"][0]["fix_kind"] == "outside"
    assert out["checks"][1]["fix"]["label"] == "Try again" and out["checks"][1]["fix_kind"] == "rerun"
    assert "fix_note" not in out["checks"][2]
    assert "fix_kind" not in metrics["checks"][0] and metrics["checks"][1]["fix"]["label"] == "Rebuild the detail levels"
    assert _with_fix_notes({"other": 1}) == {"other": 1} and _with_fix_notes({}) == {}
