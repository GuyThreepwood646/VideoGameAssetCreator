"""What a person can do about a failed check, in plain words (docs: the honest-labels part of the fix design).

Every failed check says one of four things next to its sentence:
  fix      a button that changes the cause (the guide's own fix; nothing is added here);
  rerun    a button that only runs the step again with the same settings, labelled "Try again" with a note saying so;
  by_hand  no button, but the app has a way: the note names it;
  outside  nothing in the app fixes it: the note says the one step to take outside the app;
  legacy   a check older versions made and the current app no longer makes.
describe_check (guidance.py) adds `fix_note` and `fix_kind` to a failed check from this table. The table is data:
a new check id must be in CHECK_GUIDE with a fix, or in one of the tables below (tests/test_fix_notes.py).
"""
from __future__ import annotations

import re
from typing import Any

RERUN_LABEL = "Try again"
RERUN_NOTE = ("This runs the step again with the same settings, so the result can come out different or exactly the same. "
              "Your current version stays, so it is safe to try once.")

# Checks whose guide fix only runs the step again with no changed settings. Their button reads "Try again".
RERUN: dict[str, str] = {
    "clips": RERUN_NOTE,
    "added_moves": RERUN_NOTE,
    "legs_moved": RERUN_NOTE,
    "library_clips": RERUN_NOTE,
    "hair_bound": RERUN_NOTE,
    "rig_restore": RERUN_NOTE,
    "weight_coverage": RERUN_NOTE,
    "weights_normalised": RERUN_NOTE,
    "controller_ready": RERUN_NOTE,
    "placement_exact": RERUN_NOTE,
    "clips_loop": RERUN_NOTE,
    "materials_connected": RERUN_NOTE,
    "textures_full_size": RERUN_NOTE,
    "springs_shipped": RERUN_NOTE,
    "hair_material_shipped": RERUN_NOTE,
    "collider_triangles": RERUN_NOTE,
    "lod_count": RERUN_NOTE,
    "hitbox_fit": RERUN_NOTE,
    "look_applied": RERUN_NOTE,
    "pose_held_build": RERUN_NOTE,
    "side_agree": RERUN_NOTE,
    "clips_face_forward": ("Making the files again repeats this, because the facing is set earlier: by the moves in the Make it move step and by the "
                           "model's front in the Clean up for the game step. Check those first; this button only runs the last step again."),
}

# New buttons for checks that had none: a new random draw of the 3D model (the same mechanism as "Build it again").
RETRY_NOTE = ("This builds the 3D model again with a new random draw, which can come out better or worse. Your current model stays, so it is safe to try.")
EXTRA_FIXES: dict[str, dict[str, Any]] = {
    "manifold": {"fix": {"stage": "mesh", "params": {"seed": None}}, "label": "Build the 3D model again", "note": RETRY_NOTE},
    "watertight": {"fix": {"stage": "mesh", "params": {"seed": None}}, "label": "Build the 3D model again", "note": RETRY_NOTE},
    "model_whole": {"fix": {"stage": "mesh", "params": {"seed": None}}, "label": "Build the 3D model again", "note": RETRY_NOTE},
    "degenerate_faces": {"fix": {"stage": "mesh", "params": {"seed": None}}, "label": "Build the 3D model again", "note": RETRY_NOTE},
    "uv_set": {"fix": {"stage": "mesh", "params": {"seed": None}}, "label": "Build the 3D model again", "note": RETRY_NOTE},
    "skin_not_metal": {"fix": {"stage": "prep", "params": {"pbr_fix": True}}, "label": "Make metal clearly metal",
                       "note": "This switches on the same metal clean-up as the metal check and runs the clean-up again. Your current version stays."},
}

# No button. by_hand: the app has a way, named in the note. outside: nothing in the app fixes it. legacy: no longer made.
NOTES: dict[str, tuple[str, str]] = {
    # step 3 (Hunyuan3D note, always passes): the raw count is reduced later, so there is nothing to do here
    "hy3d_raw_triangles": ("by_hand", "Nothing to do yet: Clean up reduces the Hunyuan3D count to the look's budget, and that result is checked."),
    # step 1 (picture) and step 2 (side views): used when no one-press fix is offered on this version
    "even_lighting": ("by_hand", "Redraw the picture in flat, even light from the first step (Refine), or accept it: uneven light is mostly baked into the colours."),
    "straight_on": ("by_hand", "Redraw the picture from the first step so the character is seen straight on from the front, with the camera level with the chest."),
    "pose_for_3d": ("by_hand", "Nothing in the app redraws the pose for this version; draw the side views again, or accept it and check the model's arms and hands later."),
    "pose_guard": ("by_hand", "The redraw is already kept or was not saved here, so there is nothing more to press. Your own picture is the one in use."),
    "hand_guard": ("by_hand", "The hand redraw was not used, so the 3D model was built from your own picture. Build the 3D model again for another try at it, or accept it."),
    "redraw_guard": ("by_hand", "Look at the redraw next to the version before it. If something was added or taken off, go back to that version, or redraw again from it."),
    "body_marks": ("by_hand", "Under Mark the body below the views, drag each mark it names to the right place, or press These look right."),
    "compose_match": ("by_hand", "Use Combine them again in the first step, or choose the picture you prefer and carry on."),
    # step 3 (3D model)
    "silhouette_iou": ("by_hand", "Building the model again repeats this. Try another 3D builder under Fine tune in the Build the 3D model step, "
                                  "or go back to the first step and redraw the character in a simpler pose."),
    "texture_sharp": ("by_hand", "Build the 3D model again with a larger texture size under Fine tune in the 3D model step, "
                                 "or make the picture bigger with Upscale it in the first step."),
    "texture_colour_matches_picture": ("by_hand", "Build the 3D model again for a new try; if the colours still drift, turn on Paint the picture onto the model "
                                                  "under Fine tune in the Clean up for the game step."),
    "seam_error": ("by_hand", "Build the 3D model again with a larger texture size or more space around the pieces, under Fine tune in the 3D model step."),
    "head_build": ("by_hand", "The model still works with the body's own head. Build the 3D model again for another try, or accept it."),
    "albedo_range": ("by_hand", "Pure black or white comes from the picture. Redraw the front picture a little lighter or darker in the first step "
                                "(Refine), then run Clean up for the game again."),
    "face_finder": ("by_hand", "Open Setup and check that the face finder is installed, then run Clean up for the game again."),
    "neck_join": ("by_hand", "No button moves this join on this version. Look at the neck from the front and the sides; if the line "
                             "shows, accept it or touch up the neck in the exported colour texture."),
    "picture_projection": ("by_hand", "The reason is in the sentence. Build the 3D model again from a picture that shows the character from the front, "
                                      "or accept the 3D builder's own colours."),
    "tiles_cleanly": ("by_hand", "Make the picture again, asking for a seamless, evenly lit texture with nothing large near the edges."),
    # step 5 (skeleton)
    "library_missing": ("by_hand", "Open the Health page (the Setup needs attention link at the top) and read the Shared animation library line, "
                                   "which says how to put the library back. Then make the moves again on the Make it move step."),
    "bones": ("by_hand", "Run the skeleton step again with the other skeleton template; if it fails twice, this model cannot be rigged automatically. "
                         "Use your own 3D model, build the 3D model again, or edit the parts by hand."),
    "moving_parts": ("by_hand", "On the skeleton step, click on each named part in the parts panel (a lid, a door, a wheel or a drawer), "
                                "then run the step again."),
    "moving_cut": ("by_hand", "The moving parts stay fixed for now. Press Make it move again on the skeleton step to retry; if it repeats, "
                              "build the 3D model again and rig that one."),
    "single_root": ("by_hand", "Run the skeleton step again; if it repeats, build the 3D model again and rig that one."),
    "max_influences": ("by_hand", "Run the skeleton step again; if it repeats, build the 3D model again and rig that one."),
    "wings_found": ("by_hand", "Go back to the first step and redraw the creature with both wings fully spread and clearly separate from the body, "
                               "then make the later steps again."),
    "fingers": ("by_hand", "Fingers fused into a lump cannot be rigged. Draw the side views again with Pose for 3D first, or build the 3D model again "
                           "with the hands opened first, then rig again."),
    "limb_joins": ("by_hand", "Arms or legs touching the body cannot be rigged cleanly. Draw the side views again with Pose for 3D first, "
                              "then build the 3D model and rig again."),
    # step 6 (export)
    "unity_import": ("outside", "Nothing in the app fixes this. Open the Unity console and read its error message, check the Unity project path "
                                "and Unity version in Look and settings, then make the files again once."),
    "collider_vertices": ("by_hand", "Under Fine tune in the last step, use fewer collision shapes, then make the files again."),
    "grip_found": ("by_hand", "Use the Items panel to place the item in the hand by hand."),
    "item_above_ground": ("by_hand", "Use the Items panel to move the item down to the ground by hand."),
    "items_apart": ("by_hand", "Use the Items panel to move the items apart by hand."),
    "grid_fit": ("by_hand", "Choose a grid size under Fine tune in the Clean up for the game step, then run it again."),
    "facing_tests": ("by_hand", "Use the front picker in the Clean up for the game step to choose which side is the front, then run it again."),
    # checks older versions made that the current app no longer makes
    "facing": ("legacy", "Older versions only: the app now checks the front with two tests (facing tests). Run Clean up for the game again to get them."),
    "joins": ("legacy", "Older versions only: this check is no longer made. Run the skeleton step again to get the current ones."),
    "height_m": ("legacy", "Older versions only: this is now the size check. Run Clean up for the game again to get it."),
}

# WP-86 and WP-92 (warnings only): no button changes the cause yet, so each says what to do.
NOTES.update({
    "painted_share": ("by_hand", "Build the 3D model again from a clear, straight, full-length front picture, or accept the 3D builder's own colours."),
    "uv_transfer_offset": ("by_hand", "Run the skeleton step again; if it repeats, build the 3D model again and rig that one."),
    "slivers": ("by_hand", "Build the 3D model again for another try, or accept it: thin triangles only matter where the model bends or is lit closely."),
    "texel_density_spread": ("by_hand", "Run Clean up for the game again; if the same parts stay blurry, accept it or build the 3D model again."),
    "flipped_shells": ("by_hand", "Build the 3D model again for another try, and look at hair, leaves and cloth in the viewer from both sides."),
    "eye_spikes": ("by_hand", "Smooth the area round the eyes by hand, or build the 3D model again for another try; it only shows close up."),
    "unreached_texels": ("by_hand", "Run Clean up for the game again, or accept it if the empty patches are out of sight."),
})

# WP-16 outline check: no button changes the budget, so the line says what is honest.
NOTES["outline_kept"] = ("by_hand", "Look at the model from the side and from above in the viewer. If a thin part or a hole is gone, build the 3D model again, "
                         "or choose a platform with a larger triangle budget so Clean up keeps more of it; if it looks right, accept it.")

# Checks that are not in CHECK_GUIDE and carry their own fix when the app has one (rig.py fingers_check, limbjoins).
OWN_FIX = {"fingers", "limb_joins"}

# What a failed check says when no one-press fix is offered and the table has nothing for it.
FALLBACK_NOTE = "No button fixes this one on this version. Run the step again from its page, or accept it if it does not matter for your game."

_TEXTURE_LINE = re.compile(r"^texture_[a-z0-9_]+_(pow2|square|budget)$")
_LOD_RATIO = re.compile(r"^lod\d+_ratio$")

PATTERN_NOTE = ("by_hand", "The texture sizes come from your look and platform. Build the 3D model again, or change the texture size under Fine tune "
                            "in the 3D model step, then make the files again.")
LOD_NOTE = ("by_hand", "Use Rebuild the detail levels, or make the files again in the last step.")


def entry(check_id: str) -> tuple[str, str] | None:
    """(kind, note) for a check id from the no-button table (a pattern id like texture_normal_pow2 included), or None."""
    if check_id in NOTES:
        return NOTES[check_id]
    if _TEXTURE_LINE.match(check_id):
        return PATTERN_NOTE
    if _LOD_RATIO.match(check_id):
        return LOD_NOTE
    return None


def covered(check_id: str, guide: dict[str, Any] | None) -> str | None:
    """How a check id is covered: "fix" (the guide has a fix, or the check carries its own), "rerun", "extra", a no-button
    kind from NOTES, or None when nothing covers it (the test fails on that)."""
    if check_id in EXTRA_FIXES:
        return "extra"
    if check_id in RERUN:
        return "rerun" if guide and "fix" in guide else None
    e = entry(check_id)
    if e:
        return e[0]
    if check_id in OWN_FIX or (guide and "fix" in guide):
        return "fix"
    return None


def annotate(out: dict[str, Any], check_id: str, guide: dict[str, Any] | None) -> None:
    """Add fix_note and fix_kind to a failed check (out is the described check). Relabels a rerun fix "Try again" and gives
    the checks that had no button a real one where the table has it. Never touches a passing check."""
    from . import fixes

    snippet = fixes.snippet_for(check_id)
    if snippet:
        out["fix_snippet"] = snippet  # the picture checks that can be ticked together for one redraw (data/fixes.yaml)
    if check_id in OWN_FIX:
        return  # the check's own producer sets its fix and its full explanation after this
    fix = out.get("fix")
    if fix and check_id in RERUN:
        out["fix"] = {**fix, "label": RERUN_LABEL}
        out["fix_label"] = RERUN_LABEL
        out["fix_note"], out["fix_kind"] = RERUN[check_id], "rerun"
        return
    if fix:
        return
    extra = EXTRA_FIXES.get(check_id)
    if extra:
        out["fix"] = {**extra["fix"], "label": extra["label"]}
        out["fix_label"] = extra["label"]
        out["fix_note"], out["fix_kind"] = extra["note"], "rerun"
        return
    e = entry(check_id)
    if e:
        out["fix_kind"], out["fix_note"] = e
    else:
        out["fix_kind"], out["fix_note"] = "by_hand", FALLBACK_NOTE


def annotate_checks(metrics: dict[str, Any]) -> dict[str, Any]:
    """metrics with the honest notes added to its failed checks, for versions stored before the notes existed (a copy; the
    stored metrics are never changed). A check that already has a fix_note is left as it is."""
    checks = metrics.get("checks") if isinstance(metrics, dict) else None
    if not isinstance(checks, list) or not checks:
        return metrics
    from . import guidance
    out: list[Any] = []
    for c in checks:
        if not isinstance(c, dict) or c.get("fix_note") or bool(c.get("passed", c.get("ok", True))) or (c.get("vars") or {}).get("measured") is False:
            out.append(c)
            continue
        name = c.get("name") or c.get("id") or ""
        g, _ = guidance.check_guide(name)
        d = dict(c)
        annotate(d, name, g or None)
        out.append(d)
    return {**metrics, "checks": out}


AFTER_FRAME_NOTE = (
    "This redraw fitted the whole {sub} inside the picture, and it is now {value} pixels {measure}; the later steps want at "
    "least {limit}. The picture editor drew it smaller than the frame allows. \"Upscale it\" makes the picture bigger and "
    "adds the detail back without redrawing the {sub}, or redraw once more.")


def after_fix_notes(metrics: dict[str, Any], params: dict[str, Any] | None) -> dict[str, Any]:
    """The re-check after a redraw made to fit the picture (params.fix_for has in_frame): when the detail_size check now
    fails, its line says the redraw is what made the subject small and offers Upscale it. A copy; nothing stored changes."""
    if "in_frame" not in ((params or {}).get("fix_for") or []):
        return metrics
    checks = metrics.get("checks") if isinstance(metrics, dict) else None
    if not isinstance(checks, list):
        return metrics
    out: list[Any] = []
    for c in checks:
        if (isinstance(c, dict) and (c.get("name") or c.get("id")) == "detail_size" and not bool(c.get("passed", c.get("ok", True)))
                and isinstance(c.get("value"), (int, float)) and isinstance(c.get("limit"), (int, float))):
            v = c.get("vars") or {}
            c = {**c, "fix_note": AFTER_FRAME_NOTE.format(sub="person" if v.get("measure") == "tall" else "subject", value=f"{c['value']:g}",
                                                           measure=v.get("measure") or "tall", limit=f"{c['limit']:g}")}
        out.append(c)
    return {**metrics, "checks": out}
