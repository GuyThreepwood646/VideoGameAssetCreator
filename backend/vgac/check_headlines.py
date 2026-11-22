"""The one-line headline of each check kind, in plain words, written for that check (WP-95).

A headline says what is wrong, not the numbers: the measured value, the limit and the full sentence stay under "Why" on the
page. check_tiers.annotate_metrics puts the headline on each failed, measured check of a served version.
tests/test_check_tiers.py fails for any check id the backend can emit that has no headline here.
"""
from __future__ import annotations

import re

HEADLINES: dict[str, str] = {
    # likely to show in the game
    "size_m": "The model is not the size it was meant to be",
    "facing_tests": "The model may face the wrong way",
    "clips_face_forward": "The moves may face the wrong way",
    "skin_not_metal": "Some skin is marked as metal and will look shiny",
    "materials_connected": "Unity is missing a texture, so the surface will look wrong",
    "normal_map_upright": "The surface bumps are lit the wrong way up",
    "baked_light": "Light is painted into the colours and will not match the game's light",
    "uv_set": "The model has no texture layout, so textures cannot be placed",
    "bones": "The skeleton is not what this asset needs",
    "single_root": "The skeleton has more than one root bone",
    "weight_coverage": "Part of the surface does not follow the bones",
    "legs_moved": "Some legs do not move in the walk",
    "wings_found": "The wings were not found, so it cannot fly",
    "clips": "Some animations are missing",
    "controller_ready": "No animation controller was made for Unity",
    "library_missing": "The shared animation library is not on this PC",
    "unity_import": "Unity could not import the asset",
    "collider_present": "No collision shape was made, so things pass through it",
    "limb_joins": "Arms or legs are grown into the body or its clothing",
    # worth a look
    "triangle_budget": "The model has more triangles than the platform budget",
    "manifold": "The surface has broken edges that can show as holes",
    "watertight": "The surface has open edges that may show small holes",
    "model_whole": "The 3D model looks torn or damaged",
    "silhouette_iou": "The 3D model's outline does not match the drawing",
    "seam_error": "The texture shows a seam where it repeats",
    "openings_clear": "The collision shape blocks a doorway or gap",
    "grid_fit": "The piece does not fit the grid, so it leaves gaps next to others",
    "placement_exact": "Hit boxes or attachment points are off from where they were fitted",
    "textures_full_size": "Unity uses a smaller texture than the file, so it looks soft",
    "metal_mixed": "Part of the surface is half metal and will look dull",
    "albedo_range": "Many colours are pure black or white, so game light cannot change them",
    "texture_padding": "Texture pieces sit too close together, so dark seams may show at a distance",
    "atlas_used": "Little of the texture is used, so the model looks less sharp",
    "texture_sharp": "The colour texture is softer than the picture",
    "texture_matches_picture": "The model's texture does not match the picture",
    "texture_colour_matches_picture": "The model's colours differ from the picture",
    "bake_matches": "The surface detail was made for an earlier copy of the model",
    "picture_projection": "The picture was not painted onto the model",
    "neck_join": "The seam between face and body paint may show at the neck",
    "look_applied": "The chosen look was not fully applied",
    "face_matches_picture": "The face does not match the picture well",
    "face_finder": "The eyes and mouth were not found, so the head was painted the older way",
    "hair_matches_picture": "The hair does not match the picture well",
    "hair_fit": "The library hairstyle does not fit the head well",
    "hair_skin_through": "Skin shows through the library hairstyle",
    "hair_bound": "Part of the library hairstyle does not follow the skeleton",
    "hair_off_eyes": "Hair covers the eyes",
    "max_influences": "Some surface points follow more bones than engines allow",
    "weights_normalised": "Some bone weights do not add up, so skin may stretch oddly",
    "fingers": "Some hands have no usable finger bones",
    "clips_loop": "A move that should loop stops after one cycle",
    "library_clips": "The hand-made moves could not be used, so built-in moves are used",
    "rig_restore": "The rigged model lost its textures or its size",
    "moving_parts": "Parts meant to move are not rigged yet",
    "unity_humanoid_avatar": "Unity could not read the skeleton as a person",
    "collider_vertices": "The collision shape has too many points for Unity",
    "lod_count": "Some detail levels were not made",
    "hitbox_cover": "The hit boxes cover too little of the body",
    "hitbox_overreach": "The hit boxes stick out past the body",
    "hitbox_fit": "Hit boxes could not be fitted to this skeleton",
    "grip_found": "Where the hand holds the item is a guess",
    "item_clear_of_body": "A held item passes into the body",
    "item_above_ground": "A held item dips below the floor",
    "one_subject": "The picture holds more than one subject",
    "in_frame": "The subject touches the edge of the picture",
    "big_enough": "The subject is too small in the picture",
    "same_side": "A side view faces the wrong way",
    "facing": "An older front check did not pass",
    "joins": "An older skeleton check did not pass",
    "height_m": "An older size check did not pass",
    "tiles_cleanly": "Copies placed side by side leave a gap or overlap",
    # advice
    "hy3d_raw_triangles": "The builder made more triangles than your look allows; clean-up reduces them",
    "degenerate_faces": "Some triangles are squashed flat",
    "collider_triangles": "The collision shape has more triangles than Unity likes",
    "collider_count": "There are more collision shapes than usual",
    "even_lighting": "One side of the photo is brighter than the other",
    "soft_light": "The picture has hard light with strong shadows",
    "face_lora": "Your own face add-on (LoRA) was not used",
    "hair_material": "The hair's own material was not made",
    "hair_material_shipped": "The hair's own material is not in the shipped files",
    "hair_triangles": "The library hairstyle pushes the model over the triangle limit",
    "head_build": "The head built on its own did not fit, so the body's head is used",
    "springs": "The swaying parts could not be set up",
    "springs_shipped": "The swaying parts are not in the shipped files",
    "added_moves": "Some moves you added could not be made",
    "body_marks": "Some joint markers on the body (elbows, knees, wrists) may be misplaced",
    "side_agree": "A side view is a different height from the front and back",
    "pose_held_build": "The three-quarter views do not hold the pose from your picture",
    "pose_for_3d": "The pose may join parts of the 3D model together",
    "pose_guard": "The pose redraw changed the character, so your picture was used",
    "hand_guard": "The hand redraw changed the character, so your picture was used",
    "redraw_guard": "The redraw changed what the character wears or its colours",
    "compose_match": "The combined picture does not keep the clothes or hair from your pictures",
    "detail_size": "The subject is smaller than the later steps are made for",
    "arms_away": "The arms are close to the body, so they may join it",
    "bald_picture": "The bald copy of the picture changed more than the hair",
    "second_quarter": "One three-quarter view is missing, so one side has less to go on",
    "straight_on": "The photo was taken at an angle",
    "face_drawn": "The character has no face drawn, so the model would have none",
    "items_apart": "Two held items pass into each other",
    "hair_matches_sides": "The back view's hair does not match the sides",
    "mirror_iou": "The left and right views are not mirror images",
    "height_ratio": "The side view is a different height from the front",
    "hist_dist": "The colours differ from the picture",
    "coverage": "Little of the texture is covered",
    "pose_held": "The pose was not held in the view",
    "pictures_sharpened": "The pictures were sharpened before use",
    "movement_shape": "The shape used for moving is not the usual one",
}

# Ids that carry a number or a name.
PATTERNS: list[tuple[str, str]] = [
    (r"^lod(\d+)_ratio$", "Detail level {n} has the wrong number of triangles"),
    (r"^texture_[a-z0-9_]+_budget$", "A texture is bigger than the platform allows"),
    (r"^texture_[a-z0-9_]+_pow2$", "A texture is an odd size that some game engines handle poorly"),
    (r"^texture_[a-z0-9_]+_square$", "A texture is not square"),
    (r"^hitbox_.+$", "A hit box is off"),
]


def headline_for(check_id: str) -> str | None:
    """The plain headline for a check id, or None when it has none."""
    if check_id in HEADLINES:
        return HEADLINES[check_id]
    for pat, text in PATTERNS:
        m = re.match(pat, check_id)
        if m:
            return text.format(n=m.group(1)) if m.groups() else text
    return None
