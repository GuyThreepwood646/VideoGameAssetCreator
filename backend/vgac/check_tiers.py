"""How much each check matters to the finished asset: the one table the app reads (WP-95).

Three tiers, in the order the finished page shows them:
  likely  "Likely to show in the game": a person playing would see it, or the engine would not use the asset as intended.
  look    "Worth a look": probably visible up close or at a distance, or it costs performance, or an engine may react.
  advice  "Advice": about how the asset was made; unlikely to be seen in the game.

No tier means the asset failed: nothing blocks shipping (decision D23: a broken model rebuilds, then warns) and several limits are
still being calibrated, so the page never says "failed". This table is the only place a tier is decided; the frontend
reads `tier` off each check (added when a version is served, `annotate_metrics`) and the labels from GET /api/check-tiers.
tests/test_check_tiers.py fails for any check id the backend can emit that has no entry here.

A near miss (a measured value within NEAR_MISS of its limit) is advice whatever the check's own tier (UX-10): a picture
840 px across against an 860 px guide is not a defect anyone will see.
"""
from __future__ import annotations

import re
from typing import Any

from .check_headlines import headline_for

NEAR_MISS = 0.05
BOTH_HITBOX_HEADLINE = "The hit boxes are the wrong size in places"

TIERS: list[dict[str, str]] = [
    {"id": "likely", "label": "Likely to show in the game"},
    {"id": "look", "label": "Worth a look"},
    {"id": "advice", "label": "Advice"},
]
TIER_IDS = [t["id"] for t in TIERS]

# Checks no tier is decided for fall here: neutral, and the coverage test fails until the id has its own entry.
DEFAULT_TIER = "look"

_LIKELY = """
size_m facing_tests clips_face_forward skin_not_metal materials_connected normal_map_upright baked_light uv_set
bones single_root weight_coverage legs_moved wings_found clips controller_ready library_missing unity_import
collider_present limb_joins model_whole hitbox_cover hitbox_overreach moving_parts
"""
_LOOK = """
triangle_budget manifold watertight silhouette_iou seam_error openings_clear grid_fit placement_exact
textures_full_size metal_mixed albedo_range texture_padding atlas_used texture_sharp texture_matches_picture
texture_colour_matches_picture bake_matches picture_projection neck_join look_applied face_matches_picture face_finder
hair_matches_picture hair_fit hair_skin_through hair_bound hair_off_eyes max_influences weights_normalised fingers
clips_loop library_clips rig_restore moving_cut unity_humanoid_avatar collider_vertices lod_count
hitbox_fit grip_found item_clear_of_body item_above_ground one_subject in_frame big_enough
same_side facing joins height_m tiles_cleanly
"""
_ADVICE = """
hy3d_raw_triangles degenerate_faces collider_triangles collider_count even_lighting soft_light face_lora hair_material
hair_material_shipped hair_triangles head_build springs springs_shipped added_moves body_marks side_agree pose_held_build
pose_for_3d pose_guard hand_guard redraw_guard compose_match detail_size arms_away bald_picture second_quarter straight_on
face_drawn items_apart hair_matches_sides mirror_iou height_ratio hist_dist coverage pose_held
pictures_sharpened movement_shape
"""

# WP-86 and WP-92 (warnings only; never "likely" until each is calibrated per category).
_NEW_LOOK = """
painted_share texel_density_spread flipped_shells unreached_texels eye_spikes texture_speckle
"""
_NEW_ADVICE = """
slivers uv_transfer_offset
"""

TABLE: dict[str, str] = {}
for _tier, _ids in (("likely", _LIKELY), ("look", _LOOK), ("advice", _ADVICE), ("look", _NEW_LOOK), ("advice", _NEW_ADVICE)):
    for _i in _ids.split():
        assert _i not in TABLE, _i
        TABLE[_i] = _tier

# WP-16 outline check: the clean-up changed the outline from the sides or the top (worth a look, never advice or likely).
for _i in ("outline_kept", "triangle_floor"):
    assert _i not in TABLE, _i
    TABLE[_i] = "look"

# WP-83: a failed model_whole only reaches the page after the app rebuilt the model (buildings) and kept the best, so it is
# likely to show (tier likely, above).

# Ids that carry a number or a name (quality.py, guidance._pattern_guide): first match wins.
PATTERNS: list[tuple[str, str]] = [
    # LODs are what people see at a distance, so never advice (UX-01).
    (r"^lod\d+_ratio$", "look"),
    # The outline of a detail level against the full model (step 6): worth a look, never advice (UX-01).
    (r"^lod_outline_kept_LOD\d+$", "look"),
    # The platform's texture size limit costs memory; a map that is not a power of two or not square is made so by the app.
    (r"^texture_[a-z0-9_]+_budget$", "look"),
    (r"^texture_[a-z0-9_]+_(pow2|square)$", "advice"),
    (r"^hitbox_.+$", "look"),
]

# Why each tier was given, for the review of this table (also printed by tests on failure).
WHY = {
    "likely": "seen in play, or the engine cannot use the asset as intended (scale, facing, missing UVs, bones, weights, clips, collision, lit-in light, inverted normals, a failed Unity import)",
    "look": "visible up close or at distance (UV padding bleeds from mip 3 at phone distances; LODs are what people see far away), costs performance (triangles, collider size), or an engine may treat it differently",
    "tiles_cleanly": "worth a look, not likely: the seam measure misreads some good tiles (10 of 30 fails were false on a fresh set); a better measure is a logged follow-up",
    "advice": "about how it was made or a setting; unlikely to be seen (power-of-two size, hair material, redraw guards, pose notes, picture-stage advice)",
}


def explicit_tier(check_id: str) -> str | None:
    """The tier this id has in the table, or None when it has no entry (a pattern counts)."""
    if check_id in TABLE:
        return TABLE[check_id]
    for pat, tier in PATTERNS:
        if re.match(pat, check_id):
            return tier
    return None


def tier_for(check_id: str) -> str:
    return explicit_tier(check_id) or DEFAULT_TIER


def _num(x: Any) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    return float(x)


def is_near_miss(check: dict[str, Any]) -> bool:
    """A failed, measured check whose value is within NEAR_MISS of its limit (both single numbers)."""
    if bool(check.get("passed", check.get("ok", True))) or (check.get("vars") or {}).get("measured") is False:
        return False
    v, lim = _num(check.get("value")), _num(check.get("limit"))
    if v is None or lim is None or lim == 0:
        return False
    return abs(v - lim) / abs(lim) < NEAR_MISS


def tier_of(check: dict[str, Any]) -> str:
    """The tier a check is shown in: its table tier, or advice when it is a near miss."""
    if is_near_miss(check):
        return "advice"
    return tier_for(str(check.get("id") or check.get("name") or ""))


def is_problem(check: dict[str, Any]) -> bool:
    """A failed check that counts against the asset: failed, and actually measured. A check that could not be measured
    (quality.not_measured, vars.measured False) is a note on the finished page, never a problem: it never makes an asset
    not ready and never stops autopilot."""
    if bool(check.get("passed", check.get("ok", True))):
        return False
    return (check.get("vars") or {}).get("measured") is not False


def annotate_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    """A copy of metrics with `tier` (and `near_miss` when true) on every failed check; the stored metrics never change, so a
    change to this table reaches versions made before it. Never raises."""
    try:
        checks = metrics.get("checks") if isinstance(metrics, dict) else None
        if not isinstance(checks, list) or not checks:
            return metrics
        out: list[Any] = []
        for c in checks:
            if not isinstance(c, dict) or bool(c.get("passed", c.get("ok", True))):
                out.append(c)
                continue
            d = dict(c)
            d["tier"] = tier_of(c)
            if (c.get("vars") or {}).get("both"):  # the hit boxes too small and too big in places: one item (rig.hitbox_checks)
                hl = BOTH_HITBOX_HEADLINE
            else:
                hl = None if (c.get("vars") or {}).get("measured") is False else headline_for(str(c.get("id") or c.get("name") or ""))
            if hl:
                d["headline"] = hl
            if is_near_miss(c):
                d["near_miss"] = True
            out.append(d)
        return {**metrics, "checks": out}
    except Exception:  # noqa: BLE001
        return metrics


def served() -> dict[str, Any]:
    """GET /api/check-tiers: the tier labels, the table and the patterns, so a page can say what each tier means."""
    return {"tiers": TIERS, "table": dict(TABLE), "patterns": [{"pattern": p, "tier": t} for p, t in PATTERNS],
            "default": DEFAULT_TIER, "near_miss": NEAR_MISS, "why": WHY}
