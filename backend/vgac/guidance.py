"""Plain-language layer: stage names, help text, parameter help, check sentences with fixes, and suggestions.

Everything a layperson sees comes from here, so the wording lives in one place. Technical names stay in `technical`.
"""
from __future__ import annotations

from typing import Any

STAGE_GUIDE: dict[str, dict[str, Any]] = {
    "concept": {
        "plain_name": "Describe", "plain_verb": "Make concept art", "estimate_seconds": 25,
        "help": {"meaning": "The first picture of your asset, painted from your description.",
                 "why": "Everything later is built from this picture, so get the look right here.",
                 "technical": "Concept image (text-to-image), with a background cutout"},
    },
    "turnaround": {
        "plain_name": "Check all sides", "plain_verb": "Show it from all sides", "estimate_seconds": 300,
        "help": {"meaning": "Views of your asset from the front, sides and back.",
                 "why": "The 3D builder guesses the sides it cannot see; checking them now avoids surprises later.",
                 "technical": "Turnaround views (multi-angle image editing), consistency checks per view"},
    },
    "mesh": {
        "plain_name": "Build the 3D model", "plain_verb": "Build the 3D model", "estimate_seconds": 180,
        "help": {"meaning": "Turns the picture into a textured 3D model sized for your platform.",
                 "why": "This is the model your game will use.",
                 "technical": "Image-to-3D (TRELLIS.2 shape and texture), remesh, decimate, unwrap, bake"},
    },
    "prep": {
        "plain_name": "Clean up for the game", "plain_verb": "Clean it up", "estimate_seconds": 10,
        "help": {"meaning": "Sets the real-world size, puts the pivot at the feet, mirrors the two halves and removes stray geometry.",
                 "why": "Game engines expect models at the right scale with a clean surface.",
                 "technical": "Scale, pivot, weld, symmetrize, manifold cleanup"},
    },
    "rig": {
        "plain_name": "Make it move", "plain_verb": "Add a skeleton", "estimate_seconds": 60,
        "help": {"meaning": "Puts a skeleton inside the model and attaches the surface to it, then tries a walk and an idle.",
                 "why": "Without a skeleton the model cannot animate.",
                 "technical": "Auto-rigging (SkinToken), skin weights, procedural idle and walk clips"},
    },
    "export": {
        "plain_name": "Hand off to the game", "plain_verb": "Finish and package", "estimate_seconds": 120,
        "help": {"meaning": "Packages the model with detail levels, a collider, textures and animations, and checks it in Unity.",
                 "why": "This is what you drop into your game project.",
                 "technical": "LOD chain, convex collider, FBX and GLB export, Unity prefab build"},
    },
}

PARAM_HELP: dict[str, dict[str, Any]] = {
    "model": {"simple": True, "help": {"meaning": "Which painting engine draws the concept.", "why": "Each has a different look and speed; the suggestion picks one for your style and platform.", "technical": "Diffusion model: zturbo, zbase, klein, anima, pixel, chroma"}},
    "seed": {"simple": False, "help": {"meaning": "A number that makes the same request give the same picture.", "why": "Change it to get a different take on the same description.", "technical": "Random seed"}},
    "width": {"simple": False, "help": {"meaning": "Picture width in pixels.", "why": "Bigger is slower; 1024 is right for most assets.", "technical": "Latent width"}},
    "height": {"simple": False, "help": {"meaning": "Picture height in pixels.", "why": "Bigger is slower; 1024 is right for most assets.", "technical": "Latent height"}},
    "steps": {"simple": False, "help": {"meaning": "How many refinement passes the painter makes.", "why": "More can add detail but takes longer; the model default is tuned.", "technical": "Sampler steps"}},
    "readiness_block": {"simple": True, "help": {"meaning": "Adds the standard pose and plain background your category needs.", "why": "Keeps the picture in a shape the 3D builder and the rigger can use.", "technical": "Pipeline-readiness prompt block"}},
    "prompt_override": {"simple": False, "help": {"meaning": "Replace the description for this one attempt.", "why": "Try a different wording without changing the asset.", "technical": "Prompt override"}},
    "views": {"simple": True, "help": {"meaning": "Which sides to look at.", "why": "Front, left, back and right are needed for the 3D builder's alternative engine; the rest are for you.", "technical": "Turnaround view set"}},
    "lora_strength": {"simple": False, "help": {"meaning": "How strongly the camera-angle helper is applied.", "why": "Lower keeps more of the original look; higher turns more reliably.", "technical": "Multiple-angles LoRA strength"}},
    "auto_reroll": {"simple": True, "help": {"meaning": "Retry a view automatically when it fails the consistency check.", "why": "Saves you clicking regenerate on bad views.", "technical": "Auto re-roll on failed checks"}},
    "engine": {"simple": True, "help": {"meaning": "Which 3D builder to use.", "why": "TRELLIS.2 gives the most detail from one picture; Hunyuan3D uses all four side views, better for long animals.", "technical": "Image-to-3D engine: trellis or hy3d"}},
    "input_view": {"simple": False, "help": {"meaning": "Which picture the builder starts from.", "why": "A three-quarter view usually gives better depth than a flat front.", "technical": "Conditioning image selection"}},
    "target_faces": {"simple": True, "help": {"meaning": "Detail level: how many triangles the model may have.", "why": "Phones need few, PCs allow more; the platform target sets a good default.", "technical": "Target triangle count (decimation budget)"}},
    "texture_size": {"simple": True, "help": {"meaning": "Sharpness of the painted surface.", "why": "Bigger textures look crisper close up but cost memory; the platform sets a default.", "technical": "Texture atlas resolution"}},
    "upsample": {"simple": False, "help": {"meaning": "Internal detail level of the 3D builder.", "why": "1536 captures finer shapes but takes several times longer.", "technical": "TRELLIS.2 upsample resolution"}},
    "remesh": {"simple": False, "help": {"meaning": "Rebuild the surface as one solid shell before simplifying.", "why": "Removes double walls and closes holes; keep it on.", "technical": "RemeshMesh (solid sign mode)"}},
    "height_m": {"simple": True, "help": {"meaning": "Real-world height in metres.", "why": "Keeps your asset the right size next to other things in the game.", "technical": "Bounding box height after scale"}},
    "symmetric": {"simple": True, "help": {"meaning": "Mirror the better half onto the other.", "why": "Characters and most props look right symmetric; turn it off for deliberately lopsided things.", "technical": "Symmetrize across X"}},
    "pivot": {"simple": False, "help": {"meaning": "Where the model's origin sits.", "why": "Feet for characters and props, centre for things that float or spin.", "technical": "Origin placement"}},
    "smooth_angle": {"simple": False, "help": {"meaning": "Edges sharper than this angle stay hard.", "why": "Lower keeps more crisp edges; higher looks softer.", "technical": "Auto-smooth crease angle (degrees)"}},
    "template": {"simple": True, "help": {"meaning": "Which kind of skeleton to build.", "why": "Auto picks from the category: human-style bones map to standard animations, others get a generic skeleton.", "technical": "Skeleton naming template (Mixamo or model names)"}},
    "group_per_vertex": {"simple": False, "help": {"meaning": "How many bones may move one point of the surface.", "why": "4 is the engine standard; more is smoother but slower.", "technical": "Max bone influences per vertex"}},
    "bottom_center_origin": {"simple": False, "help": {"meaning": "Put the skeleton's root at the feet.", "why": "Game engines expect it there.", "technical": "Bottom-centre origin"}},
    "bend_test": {"simple": False, "help": {"meaning": "Bend the joints and show pictures.", "why": "Shows whether the surface follows the bones naturally.", "technical": "Bend test renders at 35 and 90 degrees"}},
    "lods": {"simple": True, "help": {"meaning": "Detail levels for far away: each fraction is a smaller copy.", "why": "The game swaps to simpler copies at distance to stay fast.", "technical": "LOD ratios"}},
    "collider": {"simple": True, "help": {"meaning": "An invisible simple shape for collisions.", "why": "Lets the game know where the asset is solid.", "technical": "Convex hull collider"}},
    "formats": {"simple": True, "help": {"meaning": "Which file types to write.", "why": "FBX for Unity and Unreal, GLB for Godot and the web.", "technical": "Export formats"}},
    "unity": {"simple": True, "help": {"meaning": "Also build and check a Unity prefab.", "why": "Confirms the asset works in an engine and gives you a ready prefab.", "technical": "Unity batch import"}},
    "clips_library": {"simple": True, "help": {"meaning": "Which free animation set to attach to a human-style skeleton.", "why": "Gives walk, run, jump and more without any animation work.", "technical": "CC0 clip library retargeted through Humanoid"}},
    "sprites": {"simple": True, "help": {"meaning": "Also render 2D sprite sheets.", "why": "For 2D or top-down games that draw pictures instead of 3D models.", "technical": "8-direction sprite sheets with atlas"}},
    "sprite_directions": {"simple": False, "help": {"meaning": "How many directions to render.", "why": "8 covers top-down games; 4 is enough for side-scrollers.", "technical": "Sprite yaw count"}},
    "sprite_size": {"simple": False, "help": {"meaning": "Pixel size of one sprite frame.", "why": "Match your game's sprite scale.", "technical": "Sprite frame size"}},
}

CHECK_GUIDE: dict[str, dict[str, Any]] = {
    "triangle_budget": {"severity": "block", "help": "The model has more triangles than your platform target allows; it would run slower than it should.",
                        "fix": lambda c: {"stage": "prep", "params": {}}},
    "manifold": {"severity": "warn", "help": "A few edges on the surface overlap or meet oddly. Small counts are harmless; large ones can show as holes or shading errors."},
    "watertight": {"severity": "warn", "help": "The surface has small openings. Harmless for most game uses."},
    "degenerate_faces": {"severity": "warn", "help": "Some triangles have no area; engines ignore them but they are untidy."},
    "height": {"severity": "warn", "help": "The model is not the size the category expects.", "fix": lambda c: {"stage": "prep", "params": {}}},
    "lod_count": {"severity": "block", "help": "The detail-level chain is missing a step.", "fix": lambda c: {"stage": "export", "params": {}}},
    "collider_present": {"severity": "block", "help": "No collision shape was built.", "fix": lambda c: {"stage": "export", "params": {"collider": "hull"}}},
    "collider_vertices": {"severity": "warn", "help": "The collision shape is more detailed than engines like; it still works but costs a little."},
    "bones": {"severity": "block", "help": "No skeleton was found in the rigged model."},
    "single_root": {"severity": "warn", "help": "The skeleton has more than one root bone; most engines expect one."},
    "weight_coverage": {"severity": "warn", "help": "Some points of the surface are not attached to any bone and would stay behind when it moves.", "fix": lambda c: {"stage": "rig", "params": {}}},
    "max_influences": {"severity": "warn", "help": "Some points follow more than four bones; engines cap this and may deform differently."},
    "weights_normalised": {"severity": "warn", "help": "A few points have unbalanced bone weights, which can stretch oddly when animated.", "fix": lambda c: {"stage": "rig", "params": {}}},
    "clips": {"severity": "warn", "help": "The expected test animations are missing."},
}


def describe_check(c: dict[str, Any]) -> dict[str, Any]:
    g = CHECK_GUIDE.get(c.get("name") or c.get("id") or "", {})
    out = dict(c)
    if g:
        out["help"] = g["help"]
        out["severity"] = g.get("severity", "warn")
        if not c.get("passed") and "fix" in g:
            out["fix"] = g["fix"](c)
    else:
        out.setdefault("severity", "warn")
    for k in ("texture_", "lod"):
        if (c.get("name") or "").startswith(k) and "help" not in out:
            out["help"] = "A texture is not a power-of-two size." if k == "texture_" else "A detail level is not the expected fraction of the full model."
    return out


MODEL_LABELS = {
    "zturbo": "Z-Image Turbo (fast painted look)",
    "zbase": "Z-Image base (higher quality, slower)",
    "klein": "Klein (quick drafts)",
    "anima": "Anima (anime line art)",
    "pixel": "Z-Image pixel (pixel art)",
    "chroma": "Chroma (loose painterly)",
}


def suggest(category: str, style: str, platform: str, categories: list[dict[str, Any]], styles: list[dict[str, Any]],
            platforms: list[dict[str, Any]]) -> dict[str, Any]:
    cat = next((c for c in categories if c["id"] == category), None) or {}
    sty = next((s for s in styles if s["id"] == style), None) or {}
    plat = next((p for p in platforms if p["id"] == platform), None) or {}
    model = sty.get("model") or "zturbo"
    reasons = []
    if style == "painted" and platform in ("high",):
        model = "zbase"
        reasons.append("a showcase target deserves the slower, higher-quality painter")
    elif style == "painted":
        reasons.append("the fastest painted look at this size")
    elif style == "cel":
        reasons.append("anime and cel shading come out cleanest from Anima")
    elif style == "pixel":
        reasons.append("pixel art needs the pixel-grid model")
    elif style == "realistic_pbr":
        reasons.append("realistic surfaces need the base model's detail")
    elif style == "clay":
        reasons.append("soft clay shapes come out well from the quick drafting model")
    else:
        reasons.append("a good general choice for this style")
    budgets = (cat.get("budgets") or {}).get(platform) or {}
    est = {s: STAGE_GUIDE[s]["estimate_seconds"] for s in STAGE_GUIDE}
    if category == "material":
        est = {"concept": 60, "mesh": 10, "prep": 1, "rig": 1, "export": 5, "turnaround": 0}
    if platform == "high":
        est["mesh"] = 600
    if cat.get("rig_engine", "none") == "none":
        est["rig"] = 5
    total_min = max(1, round(sum(est.values()) / 60))
    return {"model": model, "model_label": MODEL_LABELS.get(model, model), "reason": "; ".join(reasons),
            "budgets": budgets, "estimates": est, "total_minutes": total_min,
            "platform_label": plat.get("plain_name") or plat.get("label") or platform,
            "category_label": cat.get("label") or category}


def stage_guide(stage_id: str) -> dict[str, Any]:
    return STAGE_GUIDE.get(stage_id, {})


def param_help(name: str) -> dict[str, Any]:
    return PARAM_HELP.get(name, {"simple": False, "help": {"meaning": name.replace("_", " "), "why": "", "technical": name}})
