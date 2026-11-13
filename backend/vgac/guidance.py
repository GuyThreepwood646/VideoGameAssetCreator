"""Plain-language layer: stage names, help text, parameter help, check sentences with fixes, and suggestions.

Everything a layperson sees comes from here, so the wording lives in one place. Technical names stay in `technical`.
STAGE_GUIDE is the only place stage time estimates live (served by GET /api/stages as estimate_seconds); the numbers
are the measured times in STATUS.md. Stage ids and order come from reference.STAGE_ORDER, model labels from
data/models.yaml.
"""
from __future__ import annotations

import re
import string
from typing import Any

from . import reference
from .reference import STAGE_ORDER

STAGE_GUIDE: dict[str, dict[str, Any]] = {
    "concept": {
        # Seconds per candidate.
        "plain_name": "Describe", "plain_verb": "Make concept art", "estimate_seconds": 25,
        "help": {"meaning": "The first picture of your asset, painted from your description.",
                 "why": "Everything later is built from this picture, so get the look right here.",
                 "technical": "Concept image (text-to-image), with a background cutout"},
    },
    "turnaround": {
        "plain_name": "Check all sides", "plain_verb": "Show it from all sides", "estimate_seconds": 180,
        "help": {"meaning": "Views of your asset from the front, sides and back.",
                 "why": "The 3D builder guesses the sides it cannot see; checking them now avoids surprises later.",
                 "technical": "Turnaround views (multi-angle image editing), consistency checks per view"},
    },
    "mesh": {
        "plain_name": "Build the 3D model", "plain_verb": "Build the 3D model", "estimate_seconds": 200,
        "help": {"meaning": "Turns the picture into a textured 3D model sized for your platform.",
                 "why": "This is the model your game will use.",
                 "technical": "Image-to-3D (TRELLIS.2 shape and texture), remesh, decimate, unwrap, bake"},
    },
    "prep": {
        "plain_name": "Clean up for the game", "plain_verb": "Clean it up", "estimate_seconds": 20,
        "help": {"meaning": "Turns the model to face forward, sets its real-world size (scenery is rounded to the grid), puts the pivot at the base (a corner for scenery), removes stray geometry and checks the textures; it mirrors the two halves only when you ask.",
                 "why": "Game engines expect models at the right scale, facing the right way, with a clean surface and textures painted for that surface.",
                 "technical": "Yaw to face +Z, uniform scale on the category size axis (grid snap and corner pivot for environment), pivot, weld, optional symmetrize with re-bake, manifold cleanup, PBR and baked-light checks"},
    },
    "rig": {
        "plain_name": "Make it move", "plain_verb": "Add a skeleton", "estimate_seconds": 90,
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
    "model": {"simple": True, "help": {"meaning": "Which image model paints the first picture.", "why": "Each has a different look and speed; the suggestion picks one for your style and platform.", "technical": "Diffusion model (the options are the concept models in models.yaml)"}},
    "seed": {"simple": False, "help": {"meaning": "A number that makes the same request give the same picture.", "why": "Change it to get a different take on the same description.", "technical": "Random seed"}},
    "width": {"simple": False, "help": {"meaning": "Picture width in pixels.", "why": "Bigger is slower; 1024 is right for most assets.", "technical": "Latent width"}},
    "height": {"simple": False, "help": {"meaning": "Picture height in pixels.", "why": "Bigger is slower; 1024 is right for most assets.", "technical": "Latent height"}},
    "steps": {"simple": False, "help": {"meaning": "How many refinement passes the model makes.", "why": "More can add detail but takes longer; the model default is tuned.", "technical": "Sampler steps"}},
    "readiness_block": {"simple": True, "help": {"meaning": "Adds the standard pose and plain background your category needs.", "why": "Keeps the picture in a shape the image-to-3D model and the rigging model (SkinToken) can use.", "technical": "Pipeline-readiness prompt block"}},
    "prompt_override": {"simple": False, "help": {"meaning": "Replace the description for this one attempt.", "why": "Try a different wording without changing the asset.", "technical": "Prompt override"}},
    "views": {"simple": True, "help": {"meaning": "Which sides to draw. For a character, Three-quarter (other side) is drawn too, so both cheeks get painted from a picture; untick it to skip it.",
                                       "why": "The 3D step uses the views to get the back and sides right, and the clean-up step paints the texture from them. Front, left, back and right are also what the other 3D builder (Hunyuan3D) needs.",
                                       "technical": "Turnaround view set; quarter2 is added for humanoids whenever quarter is drawn unless second_quarter is false"},
              "options": {"front": "Front", "left": "Left side", "back": "Back", "right": "Right side", "quarter": "Three-quarter", "quarter2": "Three-quarter (other side)", "top": "Top"}},
    "turn_method": {"simple": True, "help": {"meaning": "How the side, back and three-quarter views are drawn from your picture.",
                                             "why": "Standard works for everything and keeps your look. For a character, all sides in one picture makes the clothes and details match best but looks more photographic. For a rigid prop, shadow-free gives clean white views for the 3D model, with colours a little further from the picture.",
                                             "technical": "standard: multiple-angles LoRA, one job per view; sheet: tarn59 turnaround sheet LoRA, one 2592 x 1088 job split into five figures; shadow_free: berkerdooo camera-angle LoRA from the concept on white. A redraw of a sheet view uses standard."},
                    "options": {"standard": "Standard", "sheet": "All sides in one picture", "shadow_free": "Shadow-free on white"}},
    "lora_strength": {"simple": False, "help": {"meaning": "How strongly the multiple-angles LoRA is applied (the standard way of turning the views only).", "why": "Lower keeps more of the original look; higher turns more reliably.", "technical": "lora_strength (Qwen-Image-Edit-2511 multiple-angles LoRA)"}},
    # Pose for 3D first (docs/HANDS-PLAN.md, "Pose ready results"): off by default. It removed a robot's four tool arms
    # and colourised a black-and-white photo, so the visible hint carries both warnings.
    "pose_ready": {"simple": True, "help": {"meaning": "Redraws your picture once with both hands open and empty and the arms slightly away from the body (A-pose), so the 3D model gets separate fingers; adds about a minute. Leave it off when extra arms, claws or tools are part of the design (it removes them), and check the result: it can recolour a black-and-white photo and add gloves.",
                                            "why": "Best for photos and drawings where a hand is in a pocket, holds something such as a cane, or is hidden in a sleeve: a soldier with a cane and a knight in gauntlets both came out with four separate fingers. Anything held is removed on purpose; give it back later with Add an item. Your step 1 picture is not changed, and the redrawn picture is shown first in this step.",
                                            "technical": "pose_ready: one Qwen-Image-Edit pass on the step 1 picture before any view is drawn (files pose_ready_picture, pose_ready_cutout); every view and the 3D build start from it"}},
    "neutral_mouth": {"simple": True, "help": {"meaning": "In the same redraw, gives the face a relaxed, closed mouth. Works only with Pose for 3D first.",
                                               "why": "A grin or an open mouth is painted into the face and shows in every move; a closed mouth suits all of them. New and little tried: compare the face in the redrawn picture with yours.",
                                               "technical": "neutral_mouth: one more sentence in the pose_ready edit prompt"}},
    "auto_reroll": {"simple": True, "help": {"meaning": "Retry a view automatically when it fails the consistency check.", "why": "Saves you clicking regenerate on bad views.", "technical": "Auto re-roll on failed checks"}},
    "engine": {"simple": True, "help": {"meaning": "Which image-to-3D model (builder) to use.", "why": "TRELLIS.2 gives the most detail from one picture; Hunyuan3D uses all four side views, better for long animals.", "technical": "Image-to-3D engine: trellis or hy3d"},
               "options": {"trellis": "TRELLIS (most detail from one picture)", "hy3d": "Hunyuan (uses all four sides; good for long animals)"}},
    "input_view": {"simple": False, "help": {"meaning": "Which picture the builder starts from.", "why": "A three-quarter view usually gives better depth than a flat front.", "technical": "Conditioning image selection"},
                   "options": {"concept": "The first picture", "front": "Front view", "quarter": "Three-quarter view"}},
    "target_faces": {"simple": True, "help": {"meaning": "Triangle count (detail level): how many triangles the model may have.", "why": "Phones need few, PCs allow more; the platform target sets a good default.", "technical": "Target triangle count (decimation budget)"}},
    "texture_size": {"simple": True, "help": {"meaning": "Sharpness of the painted surface.", "why": "Bigger textures look crisper close up but cost memory; your style and platform set a default.", "technical": "Texture atlas resolution"},
                     "options": {"256": "256 px (smallest, retro and pixel looks)", "512": "512 px", "1024": "1024 px", "2048": "2048 px", "4096": "4096 px (largest)"}},
    "upsample": {"simple": False, "help": {"meaning": "Finer colour and shape: builds the model on a finer grid, so colour detail such as faces, eyes and markings, and small shapes, can come out sharper.", "why": "It redoes the whole colour pass, so colours can also shift and results vary: try it as a second version and keep the better one. About 2 to 3 times slower, and very slow for large, bulky objects.", "technical": "TRELLIS.2 upsample resolution"},
                 "options": {"1024": "Normal", "1536": "Finer (about 2 to 3 times slower)"}},
    "tex_steps": {"simple": False, "help": {"meaning": "How carefully the colours are painted: more steps give a richer, more detailed texture.", "why": "24 (the default) gives clearly more detail than 12 for a few seconds more; the overall tone is then shifted back towards your picture. Use 12 if a model comes out too busy.", "technical": "TRELLIS.2 texture sampler steps"},
                  "options": {"12": "Quicker, flatter colours", "24": "Richer detail (recommended)"}},
    "head_build": {"simple": True, "help": {"meaning": "Build the head on its own: with hair from the library, the head is built a second time from a close crop of the bald picture and joined to the body at the neck.",
                                            "why": "Built with the whole body, the face gets only a few pixels of the builder's attention and comes out smudged; built on its own it gets real brows, eyes, nose and lips. It adds about 5 minutes. If the head does not fit, the body's own head is kept.",
                                            "technical": "Second TRELLIS.2 build from the top 26 percent of the bald figure; joined by colour at the neck; both textures baked into one atlas (head at most 30 percent of it)"}},
    "head_crop": {"simple": False, "help": {"meaning": "Head crop: how much of the figure, from the top, the head build sees.",
                                            "why": "Smaller crops give the face more detail; a crop that is too tight can come out flat, so it is built again once and otherwise the body's own head is kept.",
                                            "technical": "Share of the figure's height, 0.18 to 0.4 (default 0.26)"}},
    # The lighter hand fix of the 3D step; the side views step's pose_ready already does it (and the build skips it then).
    "hand_edit": {"simple": False, "help": {"meaning": "Redraws only the hands in the picture the 3D model is built from, open with the fingers apart, so the model gets separate fingers; the pose and anything held are meant to stay. Not needed when Pose for 3D first was on in step 2, and leave it off for claws or robot hands, which it can turn into human hands.",
                                            "why": "Use it when step 2 was made without Pose for 3D first and the hands came out as mittens. It changes less than Pose for 3D first, and adds about a minute. Tried on two characters so far.",
                                            "technical": "hand_edit: one Qwen-Image-Edit pass on the build's input picture before TRELLIS (stages/mesh.py HAND_EDIT); skipped when the step 2 version holds a pose_ready picture"}},
    "remesh": {"simple": False, "help": {"meaning": "Rebuild the surface as one solid shell before simplifying.", "why": "Removes double walls and closes holes; keep it on.", "technical": "RemeshMesh (solid sign mode)"}},
    "size_m": {"simple": True, "help": {"meaning": "How big the asset is in the game, in metres, measured the way that makes sense for it: height for people, length for cars and snakes.", "why": "Keeps everything in proportion, so a car is car-sized next to your character.", "technical": "Uniform scale so the extent on size_axis equals size_m"}},
    "face_yaw": {"simple": True, "help": {"meaning": "Which side of the model the game treats as its front.", "why": "Characters walk and cars drive toward their front; if it is wrong, they move sideways.", "technical": "Yaw about Y so the concept front faces +Z"},
                 "options": {"auto": "Automatic", "0": "As built", "90": "Turned a quarter to the left", "180": "Turned around", "270": "Turned a quarter to the right"}},
    "symmetric": {"simple": True, "help": {"meaning": "Mirror one half of the model onto the other.", "why": "Fixes mismatched arms or legs, but the two halves then share one painted side, so it is off unless you turn it on.", "technical": "Symmetrize across X, then re-bake the maps onto the mirrored mesh"}},
    "pivot": {"simple": False, "help": {"meaning": "Where the model's origin sits.", "why": "Feet for characters and props, centre for things that float or spin.", "technical": "Origin placement"},
              "options": {"base": "At the bottom (stands on the ground)", "center": "In the middle (floats or spins)"}},
    # G15 (plan 3.8): scenery on the grid.
    "grid_footprint": {"simple": True, "help": {"meaning": "How many grid squares the piece covers on the ground, wide by deep.",
                                                "why": "Pieces made to whole squares fit together with no gaps or overlaps.",
                                                "technical": "Footprint in grid cells; automatic rounds width and depth to the nearest whole step (at most 15 percent per side)"},
                       "options": {"auto": "Automatic (nearest fit)", "1x1": "1 x 1 squares", "2x1": "2 x 1 squares", "2x2": "2 x 2 squares",
                                   "4x1": "4 x 1 squares", "4x4": "4 x 4 squares"}},
    "grid_height": {"simple": True, "help": {"meaning": "How tall the piece is, rounded to the grid.",
                                             "why": "Walls of the same height line up at the top, and floors sit flush.",
                                             "technical": "Height snapped to half grid steps, or set to the chosen value"},
                    "options": {"auto": "Automatic", "0.5": "Floor (0.5 m)", "1": "Low (1 m)", "3": "Wall (3 m)", "4": "Tall (4 m)"}},
    "grid_m": {"simple": False, "help": {"meaning": "An invisible floor of squares that scenery pieces line up on.",
                                         "why": "Pieces made to the grid fit together with no gaps or overlaps.",
                                         "technical": "Modular grid, grid_m; dimensions rounded to grid steps"},
               "options": {"0.5": "0.5 m", "1": "1 m (standard)", "2": "2 m", "4": "4 m"}},
    "smooth_angle": {"simple": False, "help": {"meaning": "Edges sharper than this angle stay hard.", "why": "Lower keeps more crisp edges; higher looks softer.", "technical": "Auto-smooth crease angle (degrees)"}},
    "template": {"simple": True, "help": {"meaning": "Rig template: which kind of skeleton to build.", "why": "Auto picks from the category: human-style bones map to standard animations, others get a generic skeleton.", "technical": "Skeleton naming template (Mixamo or model names)"},
                 "options": {"auto": "Automatic (from the category)", "mixamo": "Standard human skeleton", "generic": "Fitted to the shape", "none": "No skeleton"}},
    "group_per_vertex": {"simple": False, "help": {"meaning": "How many bones may move one point of the surface.", "why": "4 is the engine standard; more is smoother but slower.", "technical": "Max bone influences per vertex"}},
    "bottom_center_origin": {"simple": False, "help": {"meaning": "Put the skeleton's root at the feet.", "why": "Game engines expect it there.", "technical": "Bottom-centre origin"}},
    "bend_test": {"simple": False, "help": {"meaning": "Bend the joints and show pictures.", "why": "Shows whether the surface follows the bones naturally.", "technical": "Bend test renders at 35 and 90 degrees"}},
    "motion": {"simple": True, "help": {"meaning": "Whether the creature walks, flies or slithers.",
                                        "why": "Decides which animations it gets; change it for a bird that cannot fly or a fish shaped like a snake.",
                                        "technical": "Procedural locomotion mode: legs, wings, wave"},
               "options": {"legs": "Walks on its legs", "wings": "Flies, and walks when it lands", "wave": "Slithers or swims", "none": "Does not move by itself"}},
    "moves": {"simple": True, "help": {"meaning": "Animation clips: the moves made for this character or creature.",
                                       "why": "Leave out moves your game will never use to keep the file smaller.",
                                       "technical": "Library clips retargeted in Blender (retarget.py) for human shapes, else the procedural set (procanim.py)"},
              "options": {"idle": "Standing still", "walk": "Walk", "run": "Run", "turn": "Turn on the spot", "attack": "Attack", "hit": "Get hit", "death": "Die",
                          "jump": "Jump", "crouch": "Crouch", "roll": "Roll", "interact": "Use or pick up",
                          "flap": "Fly", "glide": "Glide", "takeoff": "Take off", "land": "Land", "slither": "Slither or swim"}},
    "clips_source": {"simple": True, "help": {"meaning": "Where the character's moves come from.",
                                              "why": "The hand-made library moves look natural; the built-in moves work for any skeleton and need nothing installed.",
                                              "technical": "CC0 Quaternius Universal Animation Library retargeted in Blender (retarget.py), else procanim.py"},
                     "options": {"auto": "Automatic", "library": "Hand-made library", "procedural": "Built-in moves"}},
    "move_size": {"simple": False, "help": {"meaning": "How big the moves are.", "why": "Small suits heavy or calm creatures, big suits lively ones.",
                                            "technical": "Amplitude 0.7, 1.0, 1.3"},
                  "options": {"small": "Small", "normal": "Normal", "big": "Big"}},
    "move_speed": {"simple": False, "help": {"meaning": "How fast the moves play.", "why": "Match the creature's size and mood.",
                                             "technical": "Clip speed 0.8, 1.0, 1.25"},
                   "options": {"slow": "Slow", "normal": "Normal", "fast": "Fast"}},
    "hitboxes": {"simple": True, "help": {"meaning": "Invisible shapes on each body part that tell the game where an attack landed.",
                                          "why": "Hits count where they look like they land, and you can tell a head hit from an arm hit.",
                                          "technical": "Per-bone trigger capsules and boxes, fitted from skin weights"},
                 "options": {"detailed": "One per body part", "simple": "Just head and body", "none": "None"}},
    "hitbox_fit": {"simple": True, "help": {"meaning": "How tightly the hit boxes wrap the body.",
                                            "why": "Snug is fairer for the player being hit; loose is kinder to the player aiming.",
                                            "technical": "Radius factor 0.9, 1.0 or 1.15 on the 80th-percentile vertex distance"},
                   "options": {"snug": "Snug", "normal": "Normal", "loose": "Loose"}},
    "hitbox_regions": {"simple": False, "help": {"meaning": "Which body parts get a hit box.",
                                                 "why": "Leave out parts your game never needs to tell apart.",
                                                 "technical": "Region ids from hitboxes.json regions_found"},
                       "options": {"head": "Head", "neck": "Neck", "chest": "Chest", "hips": "Hips", "body": "Body", "tail": "Tail",
                                   "upper_arm_l": "Left upper arm", "forearm_l": "Left forearm", "upper_arm_r": "Right upper arm", "forearm_r": "Right forearm",
                                   "thigh_l": "Left thigh", "shin_l": "Left shin", "thigh_r": "Right thigh", "shin_r": "Right shin",
                                   "leg_fl": "Left front leg", "leg_fr": "Right front leg", "leg_bl": "Left back leg", "leg_br": "Right back leg",
                                   "leg_l": "Left leg", "leg_r": "Right leg", "wing_l": "Left wing", "wing_r": "Right wing", "wings": "Wings"}},
    "springs": {"simple": True, "help": {"meaning": "Spring bones (small extra bones that sway on their own): parts that hang clear of the body, such as a ponytail, a braid, a cape or a tail, get a short chain of bones that lags behind the body and settles again.",
                                         "why": "A ponytail or cape that swings makes a character feel alive. Only parts that hang clear of the body can move: hair resting on the shoulders is joined to them in the 3D model, and a short haircut has nothing that hangs. Rigid things such as a sword, a scabbard or a quiver are left alone.",
                                         "technical": "Voxel slices find parts joined to the body at one end only; 2 to 4 bones per chain, weights faded from the root; a 120 Hz spring simulation with the hit boxes as colliders baked into every clip; springs.json and a VgacSpringBones script in Unity"}},
    "movement_shape": {"simple": False, "help": {"meaning": "One simple shape around the whole body that the game uses for walking into walls and standing on floors.",
                                                 "why": "It stays still while the hit boxes move, so movement is smooth and predictable.",
                                                 "technical": "Root CapsuleCollider (direction Y or Z) or BoxCollider"},
                       "options": {"auto": "Automatic (by category)", "upright": "Standing capsule (works with Unity's Character Controller)",
                                   "along": "Lying capsule along the body", "box": "Box", "none": "None"}},
    "lods": {"simple": True, "help": {"meaning": "LOD (detail levels): smaller copies for far away; each fraction is one copy.", "why": "The game swaps to simpler copies at distance to stay fast.", "technical": "LOD ratios"}},
    "collider": {"simple": True, "help": {"meaning": "Invisible simple shapes the game uses to know where the asset is solid.",
                                          "why": "Without them things pass straight through it; the right shapes keep doorways open and seats usable.",
                                          "technical": "Primitive, convex hull (255-triangle cap), CoACD convex decomposition, or mesh collider (static only)"},
                 "options": {"auto": "Best fit (recommended)", "hull": "One wrapped shape", "decompose": "Several shapes, for hollow things",
                             "mesh": "Exact shape (only for scenery that never moves)", "none": "None"}},
    "collider_max_parts": {"simple": False, "help": {"meaning": "The most separate collision shapes the asset may get.",
                                                     "why": "More shapes fit hollow things better; fewer keep physics cheap, which matters most on phones.",
                                                     "technical": "CoACD max_convex_hull; automatic is 8 for props, 16 for scenery, 6 for vehicles, half on Phone"}},
    "formats": {"simple": True, "help": {"meaning": "Which file types to write.", "why": "FBX for Unity and Unreal, GLB for Godot and the web.", "technical": "Export formats"},
                "options": {"fbx": "FBX (Unity, Unreal, Godot)", "glb": "GLB (web, Godot, Blender)"}},
    "unity": {"simple": True, "help": {"meaning": "Also build and check a Unity prefab.", "why": "Confirms the asset works in an engine and gives you a ready prefab.", "technical": "Unity batch import"}},
    "clips_library": {"simple": True, "help": {"meaning": "Which free animation set to attach to a human-style skeleton.", "why": "Gives walk, run, jump and more without any animation work.", "technical": "CC0 clip library retargeted through Humanoid"},
                      "options": {"ual1": "Standard human moves (46 animations)", "ual2": "Extra human moves (second library)", "none": "Only its own walk and idle"}},
    "sprites": {"simple": True, "help": {"meaning": "Also render 2D sprite sheets.", "why": "For 2D or top-down games that draw pictures instead of 3D models.", "technical": "8-direction sprite sheets with atlas"}},
    "sprite_directions": {"simple": False, "help": {"meaning": "How many directions to render.", "why": "8 covers top-down games; 4 is enough for side-scrollers.", "technical": "Sprite yaw count"}},
    "sprite_size": {"simple": False, "help": {"meaning": "Pixel size of one sprite frame.", "why": "Match your game's sprite scale.", "technical": "Sprite frame size"}},
    # Phase 4 (G03, G05) and Phase 5 (G07, G08), docs/GREAT-ASSETS-PLAN.md 3.5 and 3.9.
    "padding": {"simple": False, "help": {"meaning": "Extra painted space around each piece of the texture.",
                                          "why": "Stops thin dark lines appearing at seams when the model is far away.",
                                          "technical": "UV island padding and edge dilation, in texels"}},
    "rebake": {"simple": False, "help": {"meaning": "Paint the colours and surface detail onto the cleaned-up model again, even when its shape did not change.",
                                         "why": "If it is painted onto a different shape, the shading looks wavy or faceted.",
                                         "technical": "Cycles selected-to-active bake (normal, AO, colour, metal, roughness) from the mesh stage model"}},
    "delight": {"simple": True, "help": {"meaning": "Shadows and highlights from the original picture that ended up in the model's colours.",
                                         "why": "They stay put when your game's sun moves, and crevices look twice as dark. Stylised looks keep them on purpose.",
                                         "technical": "Baked lighting in albedo; AO and up-normal guided delight pass"},
                "options": {"auto": "Automatic (by look)", "remove": "Remove them", "keep": "Keep them (part of the look)"}},
    "face_detail": {"simple": True, "help": {"meaning": "Paint the face from the picture onto the model, so the eyes, brows and mouth are sharp and look like the picture.",
                                             "why": "The 3D builder makes up the face from one picture and blurs it; this puts the painted face back.",
                                             "technical": "Front render, painted head sharpened by Qwen-Image-Edit, fitted to the render, projected through the UVs; more texels for the face when it has too few"}},
    "face_strength": {"simple": False, "help": {"meaning": "How much of the painted face goes onto the model.",
                                                "why": "Lower it if the painted face looks pasted on; 1 uses all of it.",
                                                "technical": "Projection weight multiplier, 0 to 1"}},
    "face_density": {"simple": False, "help": {"meaning": "Face texel density (face sharpness): how many texture pixels the face gets. Standard is an eighth of the texture's width; High is about 512 across the face, or as many as the picture has detail for.",
                                               "why": "More texels make the eyes and mouth crisp up close. Where the texture budget is 4096, High ships the colour texture at 4096; at 2048 and 1024 the face takes a larger share and the rest of the body gives up at most 12 percent of its sharpness.",
                                               "technical": "Face UV island scale and repack (max loss 12 percent), base colour at 4096 on 4096 budgets"},
                     "options": {"auto": "Automatic (by platform and picture)", "standard": "Standard", "high": "High"}},
    "face_closeup": {"simple": False, "help": {"meaning": "Close-up source (a larger picture of the face): when the face in the picture is small, the image model draws a close-up of the same head and the face is painted from it.",
                                               "why": "A small painted face has too few pixels to fill the texture. The close-up is used only if it is still the same face; otherwise the painting is used.",
                                               "technical": "Qwen-Image-Edit close-up at 1024 (drawn once more in the look's own words if the first drifts from the painting); each eye and the mouth found again by block matching at the painting's resolution and moved back if a little off; checked by likeness, eye, skin and hair colour, and skin texture (a photograph-like close-up is not used)"}},
    "hair_detail": {"simple": True, "help": {"meaning": "Hair detail (strands and shading painted on): the hair gets strands and clumps along the way it falls, darker roots and under-layers, lighter tips and, for painted looks, a soft highlight band.",
                                             "why": "The 3D builder makes hair as one flat-coloured shape; this makes it read as hair. Nothing is added to the shape. It stands back when hair cannot be told from skin or clothes.",
                                             "technical": "Hair mask by a colour vote over the model and the turnaround pictures; flow from oriented line filters or down from the crown; line integral convolution in 3D; strand height in the normal map; more texels for the hair in the face's relayout"}},
    "hair_strength": {"simple": False, "help": {"meaning": "How strongly the hair detail is painted: 1 is full, 0 changes nothing.",
                                                "why": "Lower it if the strands look too busy for your look.",
                                                "technical": "Blend weight of the colour, normal, roughness and metallic changes on the hair, 0 to 1"}},
    "hair_material": {"simple": False, "help": {"meaning": "Hair material (its own texture and shader): the hair gets a second material with its own texture, so it holds about three times the texels and, in Unity, a highlight that stretches across the strands.",
                                                "why": "Hair is what people look at up close. It costs one more draw call per character, so Automatic turns it on for PC and Showcase and leaves Phone with one material. It stays one material when the hair cannot be told from the skin or the clothes with confidence.",
                                                "technical": "Second material slot on the hair triangles (triangles across the hairline stay with the body), own UV layout and 1024 px maps (2048 on Showcase), flow map, KHR_materials_anisotropy in the GLB, VGAC/Hair Kajiya-Kay shader on the Unity built-in pipeline"},
                      "options": {"auto": "Automatic (PC and Showcase on, Phone off)", "on": "On", "off": "Off"}},
    "head_triangles": {"simple": False, "help": {"meaning": "When the model is shrunk to your platform's triangle budget, the head keeps about twice the triangles an even shrink would leave it, so the face and hair stay round; the body gives them up and the total stays inside the budget.",
                                                 "why": "Players look at the head first. Characters in the Low-poly, Pixel-art and Retro 64 looks never get it.",
                                                 "technical": "Weighted decimation: the 3D step builds 2.5 times the target; Blender's Decimate (collapse) with an inverted vertex group on the top 13 percent of the height, then one rebake"}},
    "hand_triangles": {"simple": False, "help": {"meaning": "The head's weighted decimation, for the hands: they keep about three times the triangles an even shrink would leave them, so separate fingers survive the shrink.",
                                                 "why": "Fingers are thin, and an even shrink melts them back into mittens. Works only while Weighted decimation (more triangles on the head) is on; the total stays inside the budget.",
                                                 "technical": "Weighted decimation: the hands join the head's vertex group (blender/headweight.py hand_zones), hand gain 3.0 (vgac/headtris.py HAND_GAIN)"}},
    # The texture painted from the pictures (vgac/srcproject.py); empty means automatic, which the frontend shows as such.
    "source_project": {"simple": False, "help": {"meaning": "Paints your own picture's colours and detail onto the side of the model it shows, and the drawn views onto the other sides, so the model looks like the picture; adds about 2 minutes. Automatic does it for characters on smooth looks; turn it on to try it on other models, or off if it leaves patches.",
                                                 "why": "The 3D builder makes up its colours and they come out soft. Your own picture is used first, then the views drawn from it.",
                                                 "technical": "Picture fitted to a render by outline and optical flow, projected through the UVs with depth, edge and stretch limits (vgac/srcproject.py)"}},
    "face_closeup_guided": {"simple": False, "help": {"meaning": "With a picture of the real face (your own close-up in this step, or the Face picture in step 0.5), the close-up is drawn with that face's features.",
                                                      "why": "The close-up keeps the painting's pose, expression and light and takes the eyes, nose, mouth, skin and hair from your picture, so the model looks more like the person. Without a face picture this changes nothing.",
                                                      "technical": "Qwen-Image-Edit with the painted crop as Picture 1 and the face picture as Picture 2; two tries, each checked like any close-up"}},
    "face_sides": {"simple": False, "help": {"meaning": "Paints the sides of the face from the side views of step 2, where the front picture cannot reach.",
                                             "why": "It hides the seam where the front painting ends on the cheek. Turn it off if it leaves smudges by the ears or the neck; the front of the face is unchanged either way.",
                                             "technical": "Second projections from the quarter and side cut-outs, only where the front painted little"}},
    "part_legs": {"simple": False, "help": {"meaning": "Where the 3D build joined the legs together low down (a strand between the shoes), the legs are split so each moves on its own.",
                                            "why": "Joined legs drag each other along when the character walks and stretch the shoes. A tabard or cape between the legs is left whole.",
                                            "technical": "Spring-bone pass (springs.py): separates leg weights below the knee when the legs share vertices near the feet"}},
    "head_sway": {"simple": False, "help": {"meaning": "Chooses which parts on the head may sway: only hair (and a headscarf, ribbon or veil named in the description), or everything that hangs.",
                                            "why": "A hat brim, hood or helmet should move with the head, so Automatic only lets hair sway. Pick Everything that hangs to get the older behaviour.",
                                            "technical": "head_sway auto | hair | all; selects which head-attached meshes get spring-bone chains"}},
    "sprite_frames": {"simple": False, "help": {"meaning": "How many pictures each move gets in the 2D sprite sheet.",
                                                "why": "More frames look smoother but make a bigger sheet; 8 is a good start for a walk or an attack.",
                                                "technical": "Frames per clip rendered into sprite_sheet and sprite_atlas (1 to 64)"}},
    "pbr_fix": {"simple": False, "help": {"meaning": "Keep colours, metal and shine inside the range real materials have.",
                                          "why": "Colours outside it look flat, chalky or glowing under modern game lighting.",
                                          "technical": "PBR validation: albedo 30 to 240 sRGB, metallic near 0 or 1, roughness above 0"}},
    # Recipes (reference.recipe, data/addons.yaml).
    "addons": {"simple": False, "help": {"meaning": "LoRAs (style add-ons): small trained files that load on top of the image model and push it toward a look.",
                                         "why": "Your style already switches on the ones it needs. Turn one up, down or off here; required ones stay on.",
                                         "technical": "LoRA chain (LoraLoaderModelOnly after the model loader), strengths from data/addons.yaml"},
               "options": {"off": "Off", "subtle": "Subtle", "normal": "Normal", "strong": "Strong"}},
    "shading": {"simple": False, "help": {"meaning": "Whether the surface looks smooth and rounded or cut into flat faces.",
                                          "why": "Automatic follows your style: flat faces for low-poly, smooth for the rest.",
                                          "technical": "Smooth: auto-smooth at the crease angle; flat: per-face normals (split vertices)"},
                "options": {"auto": "Automatic (by look)", "smooth": "Smooth", "flat": "Flat faces"}},
}

# Plain names of the LoRA strength steps (the LoRAs list in Advanced).
STRENGTH_LABELS = {"off": "Off", "subtle": "Subtle", "normal": "Normal", "strong": "Strong"}

# --- Check contract (docs/GREAT-ASSETS-PLAN.md 2.1) -------------------------------------------------------------
#
# Each entry: severity ("block" or "warn"); ok and fail, the sentence for each outcome (a format string, or a
# function of the check returning one); optional vars, a function of the check returning extra placeholder values;
# optional fix, a function of the check returning {"stage", "params"} or {"ui": ...} (None means no useful fix);
# optional fix_label (a string, or a function of the check returning one). Placeholders take a format spec: n (whole number with thousands separators), pct (ratio as a
# percentage), m (metres, two decimals at most), x (two decimals), list (comma separated), dims (joined with " x ").
# A placeholder the check does not have becomes an empty string; a value that is None prints as "?".
# This is the one source of check wording; frontend help.ts CHECK_TEXT is only the mock fallback.

DEFAULT_FIX_LABEL = "Fix automatically"


def _is_zero(c: dict[str, Any]) -> bool:
    try:
        return float(c.get("value") or 0) == 0
    except (TypeError, ValueError):
        return False


def _over(c: dict[str, Any]) -> dict[str, Any]:
    try:
        return {"over": float(c["value"]) - float(c["limit"])}
    except (KeyError, TypeError, ValueError):
        return {}


def _size_fix(c: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {"size_m": c.get("limit")}
    axis = (c.get("vars") or {}).get("size_axis")
    if axis:
        params["size_axis"] = axis
    return {"stage": "prep", "params": params}


def _facing_fix(c: dict[str, Any]) -> dict[str, Any] | None:
    yaw = (c.get("vars") or {}).get("fix_yaw")
    if yaw is None:
        return None
    return {"stage": "prep", "params": {"face_yaw": int(yaw)}}


def _var(c: dict[str, Any], name: str) -> Any:
    return (c.get("vars") or {}).get(name)


def _head_line(c: dict[str, Any]) -> str:
    """The clean-up step's line on the head's triangles after the weighted decimation (vars head_weighted,
    head_triangles, head_share, head_even; headtris.head_vars), or nothing."""
    if not _var(c, "head_weighted") or _var(c, "head_triangles") is None:
        return ""
    line = " The head has {head_triangles:n} of them"
    if _var(c, "head_share") is not None:
        line += " ({head_share:pct})"
    if _var(c, "head_even") is not None:
        line += "; an even spread would have left it {head_even:n}"
    return line + "."


def _atlas_fix(c: dict[str, Any]) -> dict[str, Any]:
    """Mirroring stacks the halves on one painted side: stop mirroring. Otherwise the layout came from the 3D builder:
    build it again."""
    if _var(c, "symmetric"):
        return {"stage": "prep", "params": {"symmetric": False}}
    return {"stage": "mesh", "params": {}}


def _padding_fix(c: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return {"stage": "mesh", "params": {"padding": max(1, min(16, int(c.get("limit"))))}}
    except (TypeError, ValueError):
        return None


REDRAW_LABEL = "Redraw it ready for 3D"


def _redraw_fix(c: dict[str, Any]) -> dict[str, Any] | None:
    """Run the concept refine on this version with the fixed redraw instruction (vars.redraw)."""
    instruction = _var(c, "redraw")
    if not instruction:
        return None
    return {"stage": "concept", "op": "refine", "instruction": instruction, "params": {}}


CHECK_GUIDE: dict[str, dict[str, Any]] = {
    "triangle_budget": {
        "severity": "block",
        # With the recipe's own target (vars recipe, look): a low-poly model is measured against its look, not the platform.
        # The 3D step's model with room for the head (vars head_room, headtris) is measured against its own target, which
        # is over the platform's budget on purpose; the clean-up step's sentence adds the head's share (vars head_weighted).
        "ok": lambda c: ("Your model's body has {value:n} triangles, inside the {limit:n} left for it after {hair_allowance:n} of your platform's "
                         "limit of {platform_triangles:n} are kept for the library hairstyle."
                         if _var(c, "hair_allowance") is not None and _var(c, "platform_triangles") is not None else
                         "Your full model has {value:n} triangles, more than your platform's limit of {platform_triangles:n} on purpose: Clean up for the game "
                         "brings it back to the limit with weighted decimation (more triangles on the head)."
                         if _var(c, "head_room") else
                         "Your full model has {value:n} triangles, inside the {look} look's limit of {limit:n} (your platform would allow up to {platform_triangles:n})."
                         if _var(c, "recipe") else "Your full model has {value:n} triangles (the flat pieces it is made of), inside your platform's limit of {limit:n}.") + _head_line(c),
        "fail": lambda c: ("Your model's body has {value:n} triangles, {over:n} more than the {limit:n} left for it after {hair_allowance:n} of your "
                           "platform's limit of {platform_triangles:n} are kept for the library hairstyle."
                           if _var(c, "hair_allowance") is not None and _var(c, "platform_triangles") is not None else
                           "Your full model has {value:n} triangles, {over:n} more than the 3D step's limit of {limit:n} (which already leaves room for the head)."
                           if _var(c, "head_room") else
                           "Your full model has {value:n} triangles, {over:n} more than the {look} look's limit of {limit:n}, so it looks less chunky than it should."
                           if _var(c, "recipe") else
                           "Your full model has {value:n} triangles (the flat pieces it is made of), {over:n} more than your platform's limit of {limit:n}; it would run slower than it should."),
        "vars": _over,
        "fix": lambda c: {"stage": "prep", "params": {}},
        "fix_label": "Shrink to the budget",
    },
    "manifold": {
        "severity": "warn",
        "ok": lambda c: "The surface has no overlapping or broken edges." if _is_zero(c)
        else "The surface has {value:n} broken or overlapping edges, few enough to be harmless.",
        "fail": "The surface has {value:n} broken or overlapping edges; more than {limit:n} can show as holes or shading errors.",
    },
    "watertight": {
        "severity": "warn",
        "ok": lambda c: "The surface is closed with no holes." if _is_zero(c)
        else "The surface has {value:n} open edges, few enough to be harmless.",
        "fail": "The surface has {value:n} open edges, so it may show small holes. Harmless for most game uses.",
    },
    "degenerate_faces": {
        "severity": "warn",
        "ok": "No triangles are squashed flat.",
        "fail": "{value:n} triangles are squashed flat; engines ignore them, but they are untidy.",
    },
    "size_m": {
        "severity": "warn",
        "ok": "The model is {value:m} m {axis_word}, as intended.",
        "fail": "The model is {value:m} m {axis_word}; it should be {limit:m} m.",
        "fix": _size_fix,
        "fix_label": "Set the size",
    },
    "facing": {
        "severity": "warn",
        "ok": "The front of the model faces forward in the game.",
        "fail": "The model may face the wrong way: it matches the front picture best when turned {value} degrees.",
        "fix": _facing_fix,
        "fix_label": "Turn it to face forward",
    },
    "facing_tests": {
        "severity": "warn",
        "ok": "The two ways of telling which way the model faces (its outline against the side pictures, and where its toes point) agree.",
        "fail": ("The model may face the wrong way: its outline against the side pictures says turn it {outline_turn} degrees, but its "
                 "toes say {toes_turn} degrees, {value} degrees apart. Look at the model from the front; if the face is on the back "
                 "of the head, turn it."),
        "vars": lambda c: {"outline_turn": _var(c, "outline_turn") if _var(c, "outline_turn") is not None else "?",
                           "toes_turn": _var(c, "toes_turn") if _var(c, "toes_turn") is not None else "?"},
        "fix": _facing_fix,
        "fix_label": "Turn it to face forward",
    },
    "uv_set": {
        "severity": "warn",
        "ok": lambda c: "The model has one texture layout." if c.get("value") == 1 else "The model has {value:n} texture layouts.",
        "fail": "The model has no texture layout, so its textures cannot be placed.",
    },
    "lod_count": {
        "severity": "block",
        "ok": "{value:n} detail levels were made.",
        "fail": "{value:n} detail levels were made; {limit:n} expected. The detail-level chain is missing a step.",
        "fix": lambda c: {"stage": "export", "params": {}},
        "fix_label": "Rebuild the detail levels",
    },
    "collider_present": {
        "severity": "block",
        "ok": "A collision shape is present.",
        "fail": "No collision shape was made, so things would pass straight through the asset.",
        "fix": lambda c: {"stage": "export", "params": {"collider": "hull"}},
        "fix_label": "Add a collision shape",
    },
    "collider_vertices": {
        "severity": "warn",
        "ok": "The collision shape has {value:n} points, inside the limit of {limit:n}.",
        "fail": lambda c: "There is no collision shape to measure." if _is_zero(c)
        else "The collision shape has {value:n} points, more than the {limit:n} we aim for; it still works but costs a little.",
    },
    "bones": {
        "severity": "block",
        "ok": lambda c: "No skeleton, as intended for this kind of asset." if c.get("limit") == 0 else "The skeleton has {value:n} bones.",
        "fail": lambda c: "The model has {value:n} bones, but this kind of asset should have none." if c.get("limit") == 0
        else "No skeleton was found in the rigged model.",
    },
    "single_root": {
        "severity": "warn",
        "ok": "The skeleton starts from one root bone.",
        "fail": "The skeleton has {value:n} root bones; most engines expect one.",
    },
    "weight_coverage": {
        "severity": "warn",
        "ok": "{value:pct} of the surface follows the bones.",
        "fail": "Only {value:pct} of the surface follows the bones; it needs {limit:pct}. The rest would stay behind when it moves.",
        "fix": lambda c: {"stage": "rig", "params": {}},
        "fix_label": "Attach the loose surface",
    },
    "hitbox_cover": {
        "severity": "warn",
        "ok": "{n} hit boxes cover {cover} of the body and follow the skeleton.",
        "fail": "The hit boxes cover only {cover} of the body, so some hits would pass through.",
        "fix": lambda c: {"stage": "rig", "params": {"hitbox_fit": "loose"}},
        "fix_label": "Fit them looser",
    },
    "hitbox_overreach": {
        "severity": "warn",
        "ok": "The hit boxes stay close to the body.",
        "fail": "The hit boxes stick out well past the body ({times} times its size), so hits would land in thin air.",
        "fix": lambda c: {"stage": "rig", "params": {"hitbox_fit": "snug"}},
        "fix_label": "Fit them snugger",
    },
    "hitbox_fit": {
        "severity": "warn",
        "ok": "Hit boxes were fitted to the skeleton.",
        "fail": "We could not fit hit boxes on this skeleton; it still has its body shape for moving.",
        "fix": lambda c: {"stage": "rig", "params": {}},
        "fix_label": "Try again",
    },
    # Spring bones (vgac/springs.py, blender/springs.py), checked in rig: what sways, or why nothing does. Passes when
    # the pass stood back with its reason; fails softly on an error, a looping move that jumps at its seam or a part
    # that passes into the body.
    # Parts the 3D build joined (stages/rig.py _joins, blender/joins.py).
    "joins": {
        "severity": "warn",
        "ok": lambda c: ("No forearm or hand is grown into the body."
                         + (" The legs were joined low down (a strand between the feet) and have been parted, so they move apart freely." if _var(c, "legs_parted") else "")
                         + (" The legs are joined low down; parting them is turned off." if _var(c, "measured_only") and _var(c, "legs_found") else "")),
        "fail": lambda c: ("The " + ("forearms and hands are" if _var(c, "both") else "{sides} forearm or hand is") + " grown into the body in the 3D model "
                           "(they touched or nearly touched in the pictures), so the body's surface is pulled along when the arm moves. "
                           "Making the first picture again with the arms held clear of the body, then building the 3D model again, fixes it."
                           + (" The legs were joined low down and have been parted." if _var(c, "legs_parted") else "")),
        "fix": lambda c: {"stage": "concept", "params": {}},
        "fix_label": lambda c: "Make the first picture again",
    },
    "springs": {
        "severity": "warn",
        "ok": lambda c: ("No spring bones (small extra bones that sway on their own) were added: {why}"
                         + ("" if (_var(c, "why") or "").endswith(".") else ".") + (" {left_alone}" if _var(c, "left_alone") else "")
                         if _var(c, "skipped") else
                         "Spring bones (small extra bones that sway on their own): {what} sway"
                         + ("" if _var(c, "plural") else "s") + " when the character moves, on {value:n} added bones. "
                         "The sway is baked into every move, and looping moves still join up without a jump."
                         + (" {left_alone}" if _var(c, "left_alone") else "")),
        "fail": lambda c: {
            "error": "Spring bones (small extra bones that sway on their own) were not added: something went wrong on the way.",
            "seam": "Spring bones were added to {what}, but a looping move jumps by {seam_cm} cm where it starts again.",
            "into_body": "Spring bones were added to {what}, but in some moves it passes {deep_cm} cm into the body.",
        }.get(_var(c, "problem") or _var(c, "reason") or "", "The spring bones did not come out as they should."),
        "fix": lambda c: {"stage": "rig", "params": {"springs": True}},
        "fix_label": "Make the spring bones again",
    },
    # The hand-off's side: the files carry the chains, springs.json, and Unity put the spring script on the prefab.
    "springs_shipped": {
        "severity": "warn",
        "ok": lambda c: ("The files carry the spring bones: {value:n} chain" + ("" if c.get("value") == 1 else "s")
                         + " with the sway baked into the moves in the FBX and every GLB, and springs.json for other engines"
                         + (". In Unity the prefab has the spring script, so the chains sway in the shared moves too" if _var(c, "unity") else "")
                         + "."),
        "fail": lambda c: ("The spring bones were made at the rig step, but the files that ship do not carry them"
                           + (" ({problem})" if _var(c, "problem") else "") + "."),
        "fix": lambda c: {"stage": "export", "params": {}},
        "fix_label": "Make the files again",
    },
    "max_influences": {
        "severity": "warn",
        "ok": "Each surface point follows at most {value:n} bones.",
        "fail": "Some surface points follow {value:n} bones; the limit is {limit:n}, so engines may bend them differently.",
    },
    "weights_normalised": {
        "severity": "warn",
        "ok": "All skin weights add up correctly.",
        "fail": "{value:n} surface points have bone weights that do not add up, which can stretch oddly when animated.",
        "fix": lambda c: {"stage": "rig", "params": {}},
        "fix_label": "Balance the weights",
    },
    "clips": {
        "severity": "warn",
        "ok": "Animations: {value:list}.",
        "fail": "Some animations are missing: {missing:list}.",
        "fix": lambda c: {"stage": "rig", "params": {}},
        "fix_label": "Make the moves again",
    },
    "library_clips": {
        "severity": "warn",
        "ok": "{count:n} moves come from the hand-made library.",
        "fail": "The hand-made moves could not be put on this skeleton, so it uses the built-in moves. {reason}",
        "fix": lambda c: {"stage": "rig", "params": {}},
        "fix_label": "Make the moves again",
    },
    # "Add a move" (moves.py): the moves the person added from the library or their own file.
    "added_moves": {
        "severity": "warn",
        "ok": "The {value:n} moves you added are on the character.",
        "fail": "Some moves you added could not be made: {missing:list}. {reason}",
        "fix": lambda c: {"stage": "rig", "params": {}},
        "fix_label": "Make the moves again",
    },
    "legs_moved": {
        "severity": "warn",
        "ok": "All {value:n} legs move in the walk.",
        "fail": "Only {value:n} of {limit:n} legs move in the walk.",
        "fix": lambda c: {"stage": "rig", "params": {}},
        "fix_label": "Make the moves again",
    },
    "wings_found": {
        "severity": "warn",
        "ok": "Both wings were found, so it can fly and glide.",
        "fail": "We could not find the wings in the skeleton, so it walks but cannot fly.",
    },
    "silhouette_iou": {
        "severity": "warn",
        "ok": "The model outline matches the drawing ({value:pct}).",
        "fail": "The model outline only matches the drawing {value:pct}; it needs {limit:pct}.",
    },
    "unity_import": {
        "severity": "warn",
        "ok": "Unity imported the asset without errors.",
        "fail": "Unity could not import the asset.",
    },
    "unity_humanoid_avatar": {
        "severity": "warn",
        "ok": "Unity recognised the skeleton as a person, so all {value} shared animations work.",
        "fail": "Unity could not read the skeleton as a person, so the shared animations are missing; it still has its own moves.",
        "fix": lambda c: {"stage": "rig", "params": {"template": "mixamo"}},
        "fix_label": "Fit the standard skeleton again",
    },
    "materials_connected": {
        "severity": "warn",
        "ok": lambda c: ("Unity uses all the surface textures: colour, shadows, metal and shine (this look has no surface bumps)."
                         if _var(c, "no_bumps") else "Unity uses all the surface textures: colour, bumps, shadows, metal and shine."),
        "fail": "Unity is missing the {missing} texture, so the surface will look {effect}.",
        "fix": lambda c: {"stage": "export", "params": {}},
        "fix_label": "Rebuild for Unity",
    },
    "textures_full_size": {
        "severity": "warn",
        "ok": "Unity imports every texture at its full size (the largest is {file_px} px).",
        "fail": "Unity imports the {texture} texture at {import_px} px, smaller than the file's {file_px} px, so it looks softer in the game.",
        "fix": lambda c: {"stage": "export", "params": {}},
        "fix_label": "Rebuild for Unity",
    },
    "clips_loop": {
        "severity": "block",
        "ok": "Idle and walk loop smoothly.",
        "fail": "The {value} animation does not loop, so it would stop after one cycle.",
        "fix": lambda c: {"stage": "export", "params": {}},
        "fix_label": "Rebuild for Unity",
    },
    "clips_face_forward": {
        # Unity hand-off (VgacImport Version 16): the body's heading while each move the controller plays runs,
        # against the prefab's forward; the moves were once imported facing backwards (docs/KNOWN-FAULTS-RESEARCH.md 5).
        "severity": "warn",
        "ok": lambda c: ("Every move faces the character's forward in Unity; the furthest any turns is {value} degrees"
                         + (" ({clip})." if _var(c, "clip") else ".")),
        "fail": lambda c: (("The {role} move ({clip})" if _var(c, "role") else ("The {clip} move" if _var(c, "clip") else "A move"))
                           + " plays with the body turned {value} degrees away from the character's forward"
                           + (" (to its {side})" if _var(c, "side") else "")
                           + (", and {others} more moves are turned too" if _var(c, "others") else "")
                           + ", so in a game the character would face the wrong way while it plays."),
        "fix": lambda c: {"stage": "export", "params": {}},
        "fix_label": "Rebuild for Unity",
    },
    "placement_exact": {
        "severity": "warn",
        "ok": "Hit boxes and attachment points sit exactly where they were fitted.",
        "fail": "Hit boxes or attachment points landed {cm} cm away from where they were fitted, so hits and held items would look off.",
        "fix": lambda c: {"stage": "export", "params": {}},
        "fix_label": "Rebuild for Unity",
    },
    "grip_found": {
        "severity": "warn",
        "ok": lambda c: "The hand holds the {label} by its handle" + (" ({method})." if (c.get("vars") or {}).get("method") else "."),
        "fail": "We guessed where the hand holds the {label}; check it in the preview under Add an item and move it if it looks wrong.",
    },
    "item_clear_of_body": {
        "severity": "warn",
        "ok": lambda c: ("The {label} " + ("{stays}" if (c.get("vars") or {}).get("stays") else "stays")
                         + " clear of the body in every move we checked ({clips})."),
        "fail": lambda c: (("{pct}% of the {label} is inside the body during {clip} (up to {cm} cm in). "
                            if _var(c, "by") == "share" else "The {label} passes {cm} cm into the body during {clip}. ")
                           + ("{tried} " if _var(c, "tried") else "")
                           + "Move it under Add an item, or choose another place for it."),
        # A placement the person set by hand was not fitted: fitting it is the useful fix. One we already fitted is not
        # fitted again by a rebuild (it would find the same answer), so there is no button then.
        "fix": lambda c: None if _var(c, "fitted") else {"stage": "export", "params": {"fit_items": True}},
        "fix_label": "Fit it for me",
    },
    "item_above_ground": {
        "severity": "warn",
        "ok": "The {items} stay above the floor in every move we checked.",
        "fail": "The {label} dips {cm} cm below the floor during {clip}, so it would cut into the ground in a game. Move it under Add an item.",
    },
    "items_apart": {
        "severity": "warn",
        "ok": "The {items} never pass through each other in the moves we checked.",
        "fail": "The {a} passes {cm} cm into the {b} during {clip}. Move one of them under Add an item.",
    },
    "controller_ready": {
        "severity": "warn",
        "ok": "An animation controller switches between idle, walk and run by speed.",
        "fail": "No animation controller was built, so you would have to set one up in Unity.",
        "fix": lambda c: {"stage": "export", "params": {}},
        "fix_label": "Rebuild for Unity",
    },
    "rig_restore": {
        "severity": "warn",
        "ok": "The rigged model kept its textures and its size.",
        "fail": "The rigged model lost its textures or its size while the skeleton was fitted.",
        "fix": lambda c: {"stage": "rig", "params": {}},
        "fix_label": "Fit the skeleton again",
    },
    "seam_error": {
        "severity": "warn",
        "ok": "The texture repeats without a visible seam.",
        "fail": "The texture shows a seam where it repeats (edge difference {value:x}, limit {limit:x}).",
    },
    # Collision (G16, G35), emitted by the export stage with vars.
    "collider_triangles": {
        "severity": "warn",
        "ok": "Each collision shape has at most {value:n} triangles, inside Unity's limit of {limit:n}.",
        "fail": "The collision shape has {value:n} triangles; Unity would simplify it on its own and it may stop fitting.",
        "fix": lambda c: {"stage": "export", "params": {}},
        "fix_label": "Simplify the collision shape",
    },
    "collider_count": {
        "severity": "warn",
        "ok": "{value:n} collision shape(s), inside the {limit:n} your platform allows.",
        "fail": "The collision is {value:n} shapes, more than the {limit:n} we aim for on your platform; physics costs a little more each frame.",
        "fix": lambda c: {"stage": "export", "params": {"collider_max_parts": c.get("limit")}},
        "fix_label": "Use fewer shapes",
    },
    "openings_clear": {
        "severity": "warn",
        "ok": "Openings stay open: things can pass through doorways and gaps.",
        "fail": "The collision shape fills {value:n} opening(s), so players cannot pass through.",
        "fix": lambda c: {"stage": "export", "params": {"collider": "mesh" if (c.get("vars") or {}).get("category") == "environment" else "decompose"}},
        "fix_label": "Use several shapes",
    },
    # Phase 4 (G05, G03): checked in prep, where the final mesh and its maps are known. The plan lists bake_matches
    # under export; prep is where the re-bake happens, so the check lives there.
    "bake_matches": {
        "severity": "warn",
        "ok": "The surface detail was painted onto the final model.",
        "fail": "The surface detail was painted for an earlier copy of the model, so shading may look wavy.",
        "fix": lambda c: {"stage": "prep", "params": {"rebake": True}},
        "fix_label": "Repaint the surface",
    },
    # The 3D step's normal map with its green channel the right way up (vgac/normalfix.py), checked at the 3D step on
    # its own map and at the clean-up step on the map it baked from. The Fix rebuilds the step that measured it.
    "normal_map_upright": {
        "severity": "warn",
        "ok": "The normal map (bump texture) is the right way up, so the surface shades smoothly.",
        "fail": lambda c: ("The normal map (bump texture) is upside down, so the surface shows triangle-shaped facets and dark patches "
                           + ("under light. Cleaning up again bakes it the right way up." if _var(c, "stage") == "prep"
                              else "under light. Building the 3D model again writes it the right way up.")),
        "fix": lambda c: {"stage": "prep" if _var(c, "stage") == "prep" else "mesh", "params": {}},
        "fix_label": lambda c: "Clean up again" if _var(c, "stage") == "prep" else "Build the 3D model again",
    },
    # The 3D step's base colour against the picture (vgac/texcolour.py): the builder sometimes makes a near-black colour
    # texture from a bright picture and is built again up to three times; this blocks only when even the closest build
    # is too dark. The Fix builds it again with a new seed.
    "texture_matches_picture": {
        "severity": "block",
        "ok": lambda c: ("The first build's base colour (the colour texture) came out too dark, so the 3D model was built again; "
                         "build {kept_try:n} of {tries:n} keeps {value:pct} of the picture's brightness."
                         if _var(c, "rebuilt") else
                         "The base colour (the colour texture) keeps {value:pct} of the picture's brightness."),
        "fail": lambda c: ("The base colour (the colour texture) came out much darker than the picture: {value:pct} of its brightness, "
                           "it needs {limit:pct}. "
                           + ("It was built {tries:n} times and this is the closest. " if _var(c, "rebuilt") else "")
                           + "Build it again, or use a picture with some soft light and shadow on the character."),
        "fix": lambda c: {"stage": "mesh", "params": {"seed": None}},
        "fix_label": "Build it again",
    },
    # Report-only (vgac/texfidelity.py): the model drawn flat from the picture's side against the picture. Severity warn,
    # so they show on the card and never block a run. No fix yet: sharper colour needs the picture projected onto the
    # model (docs/TEXTURE-FIDELITY-PLAN.md lane B).
    "texture_sharp": {
        "severity": "warn",
        "ok": "The model's colour texture is about as sharp as the picture ({value:x} of the picture's fine detail).",
        "fail": "The model's colour texture is softer than the picture: it keeps {value:x} of the picture's fine detail, "
                "it should keep at least {limit:x}.",
    },
    "texture_colour_matches_picture": {
        "severity": "warn",
        "ok": "Seen from the picture's side, the model's colours match the picture (colour difference {value:x}).",
        "fail": "Seen from the picture's side, the model's colours differ from the picture (colour difference {value:x}, "
                "it should stay under {limit:x}). The model looks "
                "{tone} than the picture.",
        "vars": lambda c: {"tone": "lighter" if float((c.get("vars") or {}).get("delta_l") or 0) > 0 else "darker"},
    },
    "texture_padding": {
        "severity": "warn",
        "ok": "Texture pieces have {value:n} px of space around them, enough for distant views.",
        "fail": "Texture pieces have only {value:n} px of space, so thin dark seams may show at a distance.",
        "fix": _padding_fix,
        "fix_label": "Add more space",
    },
    "atlas_used": {
        "severity": "warn",
        "ok": "The model uses {value:pct} of its texture.",
        "fail": "The model uses only {value:pct} of its texture, so it looks less sharp than it could.",
        "fix": _atlas_fix,
        "fix_label": "Repaint the surface",
    },
    # The face detail pass (stages/face_detail.py), checked in prep. It measures sharpness and how well the model's
    # face carries the fitted picture's features; it does not claim the face is the same person.
    "face_matches_picture": {
        "severity": "warn",
        "ok": lambda c: ("The face was painted from " + (_var(c, "source") or "the picture")
                         + ": its detail went from {before:n} to {value:n} (sharp from {limit:n})."
                         + (" The cheeks were also painted from {sides}." if _var(c, "sides") else "")
                         + ((" Your close-up of the face was not used: {closeup_not_used}." if _var(c, "closeup_own") else
                             " A close-up was drawn but not used: {closeup_not_used}.") if _var(c, "closeup_not_used") else "")
                         + (" Your close-up differs from the painting: {closeup_notes}." if _var(c, "closeup_notes") else "")
                         + (" {texels:n} texels (texture pixels) across the face." if _var(c, "texels") else "")
                         + (" {density_note}" if _var(c, "density_note") else "")),
        "fail": lambda c: {
            "no_card": "The face was not painted from the picture: no graphics card that draws pictures was free.",
            "not_lined_up": "The face was not painted from the picture: the picture's face could not be lined up with the model's.",
            "poor_fit": ("The face was left as it was, not painted from the picture, because the picture could not be lined up with "
                         "the model's face well enough (fit {value:n} of 1, needs {limit:n}). Painting it anyway would have put the eyes "
                         "and mouth in the wrong place. Try the clean-up again, or use a clearer picture of the face looking straight "
                         "at the camera."),
            "error": "The face was not painted from the picture: something went wrong on the way.",
            "misplaced": ("The face was painted onto the wrong part of the head: the app could not find where the model's eyes are, "
                          "so the eyes kept the 3D step's blurred look. Painting the face again rarely helps; building the 3D model again usually does."),
            "blurry": "The face is still blurry after painting it from the picture (detail {value:n}, sharp from {limit:n}).",
            "off_picture": "The face was painted, but it follows the picture poorly (match {match:n} of 1).",
            "eye_colour": "The face was painted, but the eyes' colour differs from the picture's (by {eye_shift:n}, fine up to 8).",
            "eye_missing": "The face was painted, but {eye_words} {eye_verb} not: the picture shows the eye, the model still has the blurred smudge from the 3D step.",
            "eye_under_hair": ("The face was painted, but {eye_words} {eye_verb} not: the model's hair covers that eye, although the picture shows it. "
                               "Painting the face again cannot change that; building the 3D model again can give hair that leaves the eye free."),
            "eye_blurry": "The face was painted, but {eye_words} came out blurry, much softer than the picture's.",
        }.get(_var(c, "reason") or "", "The face does not match the picture yet."),
        "fix": lambda c: ({"stage": "mesh", "params": {}} if _var(c, "reason") in ("eye_under_hair", "misplaced")
                          else {"stage": "prep", "params": {"face_detail": True, "face_seed": None, "face_closeup_seed": None, "face_closeup_seed_2": None}}),
        "fix_label": lambda c: "Build the 3D model again" if _var(c, "reason") in ("eye_under_hair", "misplaced") else "Paint the face again",
    },
    # The hair detail pass (stages/hair_detail.py), checked in prep. When the hair cannot be told from skin or clothes
    # the pass stands back and the check passes with the reason; it fails softly when something went wrong or when the
    # face's own measures moved.
    "hair_matches_picture": {
        "severity": "warn",
        "ok": lambda c: ("Hair detail (strands and shading painted on) was not added: {why}. The hair keeps its colours from the 3D step."
                         if _var(c, "skipped") else
                         "Hair detail (strands and shading painted on): strands, clumps, darker roots and lighter tips were painted along the hair"
                         + (", with a soft highlight band" if _var(c, "highlight") else "")
                         + ". Texels (texture pixels) on the hair: {before} per cm before, {after} now."
                         + (" {room_note}" if _var(c, "room_note") else "")),
        "fail": lambda c: {
            "error": "Hair detail (strands and shading painted on) was not added: something went wrong on the way.",
            "face_changed": "Hair detail was painted, but the face changed with it (its sharpness or the eyes' places moved). Paint the hair again with less strength.",
        }.get(_var(c, "reason") or "", "The hair detail did not come out as it should."),
        "fix": lambda c: {"stage": "prep", "params": {"hair_detail": True, **({"hair_strength": 0.5} if _var(c, "reason") == "face_changed" else {})}},
        "fix_label": "Paint the hair again",
    },
    # Hair as its own material (vgac/hairmat.py), checked in prep: humanoids whose hair pass ran, on PC and Showcase
    # budgets. Passes with the reason when the split was doubtful or not worth it (the hair stays in the body material).
    "hair_material": {
        "severity": "warn",
        "ok": lambda c: ("Hair material (its own texture and shader) was not used: {why}. The hair stays in the body material, "
                         "painted as before, and the character keeps one material."
                         if _var(c, "used") is False else
                         "Hair material (its own texture and shader): the hair has its own material"
                         + (" and a {tex} px texture" if _var(c, "tex") else "")
                         + ", with {value} texels (texture pixels) per cm on the hair against {limit} in the body texture. "
                         "It costs one more draw call per character."
                         + (" The rest of the body has {body} texels per cm ({body_before} before)." if _var(c, "body") and _var(c, "body_before") else "")),
        "fail": "Hair material (its own texture and shader) was not made: something went wrong on the way, so the hair stays in the body material.",
        "fix": lambda c: {"stage": "prep", "params": {"hair_material": "on"}},
        "fix_label": "Give the hair its own material again",
    },
    # The hand-off's side of it: the files that ship carry the second material.
    "hair_material_shipped": {
        "severity": "warn",
        "ok": lambda c: ("The hair ships with its own material: the GLB has {value} materials (body and hair) and the FBX two "
                         "material slots" + (" named {slots}" if _var(c, "slots") else "")
                         + (". Unity built the hair material with the {shader} shader" if _var(c, "shader") else "")
                         + ". One more draw call per character."),
        "fail": lambda c: ("The hair was meant to have its own material, but the files that ship have {value} material(s)"
                           + (" ({problem})" if _var(c, "problem") else "") + "."),
        "fix": lambda c: {"stage": "export", "params": {}},
        "fix_label": "Make the files again",
    },
    # G15 (plan 3.8): scenery on the grid, checked in prep.
    "grid_fit": {
        "severity": "warn",
        "ok": lambda c: ("The piece is {w:m} x {d:m} x {h:m} m, smaller than one {grid:m} m square, so it sits inside one square."
                         if _var(c, "small") else "The piece is {w:m} x {d:m} x {h:m} m and lines up with the {grid:m} m grid."),
        "fail": "The piece is {side:m} m {side_word}, between grid steps, so it leaves a gap next to other pieces.",
        "fix": lambda c: {"stage": "prep", "params": _var(c, "fix_params")} if _var(c, "fix_params") else None,
        "fix_label": "Snap to the grid",
    },
    "tiles_cleanly": {
        "severity": "warn",
        "ok": "Copies placed side by side meet without gaps.",
        "fail": "Copies placed side by side leave a gap or overlap of {value} cm.",
    },
    # Phase 5 (G07, G08).
    "baked_light": {
        "severity": "warn",
        "ok": lambda c: ("Painted shading is kept on purpose for this look." if _var(c, "kept")
                         else "Painted-on shadows removed, so it reacts to your game's lights." if _var(c, "removed")
                         else "No painted-on shadows found in the colours."),
        "fail": "The colours still carry shadows from the picture, so they will fight your game's lights.",
        "fix": lambda c: None if _var(c, "removed") else {"stage": "prep", "params": {"delight": "remove"}},
        "fix_label": "Remove painted-on shadows",
    },
    "albedo_range": {
        "severity": "warn",
        "ok": lambda c: ("Painted darks and lights are kept on purpose for this look." if _var(c, "kept")
                         else "Colours stay inside the range real materials have."),
        "fail": "{value:pct} of the colours are darker or brighter than real materials get, so they may look flat or glow under game lights.",
        "fix": lambda c: None if _var(c, "pbr_fix_on") else {"stage": "prep", "params": {"pbr_fix": True}},
        "fix_label": "Bring colours into range",
    },
    "metal_mixed": {
        "severity": "warn",
        "ok": "Metal parts are clearly metal and the rest clearly not.",
        "fail": "{value:pct} of the surface is half metal, which looks dull and grey in game engines.",
        "fix": lambda c: None if _var(c, "pbr_fix_on") else {"stage": "prep", "params": {"pbr_fix": True}},
        "fix_label": "Make metal clearly metal",
    },
    # Recipes: the look a style asks for after the concept (fewer triangles, flat faces, small or crisp textures, no
    # surface bumps), checked in prep. Only for looks that change something.
    "look_applied": {
        "severity": "warn",
        "ok": lambda c: "{look} look applied: {value}." if _var(c, "look") else "Look applied: {value}.",
        "fail": lambda c: ("The {look} look is not fully applied: {problem}." if _var(c, "look") and _var(c, "problem")
                           else "The look is not fully applied to the model yet."),
        "fix": lambda c: {"stage": "prep", "params": {}},
        "fix_label": "Apply the look again",
    },
    # Start from your own images: readiness of any concept picture (readiness.concept_checks). The Fix runs the concept
    # refine on the version with the fixed redraw instruction built from the category's pose block.
    "one_subject": {
        "severity": "warn",
        "ok": "The picture shows one subject.",
        "fail": lambda c: ("We could not find a subject in the picture; the background may be too busy to cut out."
                           if _var(c, "empty") else
                           "The picture shows {value:n} separate things; the 3D step works best with one subject on its own."),
        "fix": _redraw_fix,
        "fix_label": REDRAW_LABEL,
    },
    "in_frame": {
        "severity": "warn",
        "ok": "The whole subject is in the picture, with space around it.",
        "fail": "The subject touches the {sides_text} edge of the picture, so part of it may be cut off and the 3D model would miss it.",
        "fix": _redraw_fix,
        "fix_label": REDRAW_LABEL,
    },
    "big_enough": {
        "severity": "warn",
        "ok": "The subject is {value:n} pixels {measure}, big enough for the 3D step.",
        "fail": "The subject is only {value:n} pixels {measure}; at least {limit:n} keeps the detail in the 3D model.",
        "fix": _redraw_fix,
        "fix_label": REDRAW_LABEL,
    },
    "detail_size": {
        "severity": "warn",
        "ok": "The subject is {value:n} pixels {measure}, the size the later steps are made for.",
        "fail": "The subject is {value:n} pixels {measure}. The pictures that make good models here are about 1 megapixel with the subject at least {limit:n} pixels {measure}; smaller, the face and small parts come out soft. Upscaling it to about {target:n} pixels adds the detail back.",
        "fix": lambda c: {"stage": "concept", "op": "upscale", "params": {}},
        "fix_label": "Upscale it",
    },
    "arms_away": {
        "severity": "warn",
        "ok": "The arms stand away from the body, so the skeleton step can tell them apart.",
        "fail": "The arms are close to the body, so the hands may be hidden or holding something; the 3D model may join the arms to the sides and the skeleton step may not tell them apart. The redraw also opens the hands and removes held items.",
        "fix": _redraw_fix,
        "fix_label": REDRAW_LABEL,
    },
    # Hair-friendly pictures (vgac/hairpic.py): hair over the eyes on a character's concept picture, found by colour.
    # The Fix runs the concept refine with vars.redraw, which asks the picture editor to move the hair off the face.
    "hair_off_eyes": {
        "severity": "warn",
        "ok": "Both eyes are clear of hair, so the face can be built and painted cleanly.",
        "fail": lambda c: ("Hair covers {side_text} ({value:pct} of the eye area). The 3D model turns hair over the face into a solid flap, and the face cannot be painted cleanly."
                           if _var(c, "side_text") else
                           "Hair covers the eyes ({value:pct} of the eye area). The 3D model turns hair over the face into a solid flap, and the face cannot be painted cleanly."),
        "fix": _redraw_fix,
        "fix_label": "Move the hair off the face",
    },
    # The face is drawn (vgac/hairpic.face_check): a close-up of the head read by the local vision model, for the looks
    # whose painter sometimes leaves the face empty or paints the figure from behind. The Fix asks the picture editor
    # to draw the face.
    "face_drawn": {
        "severity": "warn",
        "ok": "The face is drawn, with eyes, so the 3D model gets a face.",
        "fail": lambda c: ("The character {problem}, so the 3D model would have no face."
                           if _var(c, "problem") else
                           "The face has no eyes drawn, so the 3D model would have no face."),
        "fix": _redraw_fix,
        "fix_label": "Draw the face",
    },
    # Hair from the library (docs/HAIR-LIBRARY-PLAN.md section 7): the bald picture at Check all sides, the fit at the
    # 3D step (best style) and at Clean up (the style used), skin through and triangles at Clean up, the skeleton at
    # Make it move. All warn.
    "bald_picture": {
        "severity": "warn",
        "ok": lambda c: ("Bald picture (the character without hair, used to build the 3D model): only the hair was taken away, "
                         "and {value:pct} of the rest of the picture is unchanged."
                         + (" The first try changed too much, so it was drawn a second time." if _var(c, "rerolled") else "")),
        "fail": lambda c: {
            "face_changed": "Bald picture (the character without hair, used to build the 3D model): the face changed as well as the hair, "
                            "although {value:pct} of the rest of the picture is unchanged. The face is still painted from your own "
                            "picture, but the head of the 3D model may not match it.",
            "clothes_changed": "Bald picture (the character without hair, used to build the 3D model): the clothes changed as well as the hair "
                               "({value:pct} of the rest of the picture is unchanged, it needs {limit:pct}), so the 3D model would not match "
                               "your picture.",
            "no_hair_removed": "Bald picture (the character without hair, used to build the 3D model): the hair was not taken away, so the "
                               "library hairstyle would sit on top of the old hair.",
            "error": "Bald picture (the character without hair, used to build the 3D model): it could not be made, because something "
                     "went wrong on the way.",
        }.get(_var(c, "reason") or "", "Bald picture (the character without hair, used to build the 3D model): more than the hair "
                                       "changed ({value:pct} of the rest of the picture is unchanged, it needs {limit:pct}).")
        + (" It was drawn twice and this is the closer one." if _var(c, "rerolled") and _var(c, "reason") != "error" else ""),
        "fix": lambda c: {"stage": "turnaround", "params": {"bald_only": True, "bald_seed": None}},
        "fix_label": "Make the bald picture again",
    },
    "head_build": {
        "severity": "warn",
        "ok": lambda c: "The head was built on its own and joined to the body at the neck, so the face is much sharper.",
        "fail": lambda c: "The head built on its own did not fit, so the body's own head is used: {reason_words}.",
    },
    "hair_fit": {
        "severity": "warn",
        "ok": lambda c: ((("Hair from the library (ready-made hairstyles): \"{style_name}\" was fitted to the head"
                           + (", the style you picked" if _var(c, "picked") else ", the best match") + ". ")
                          if _var(c, "step") == "prep" and _var(c, "style_name") else
                          ("Hair from the library (ready-made hairstyles): {candidates:n} styles were tried on the bald model, and "
                           + ("\"{best_name}\"" if _var(c, "best_name") else "the best one") + " matches best. ")
                          if _var(c, "candidates") else "Hair from the library (ready-made hairstyles): ")
                         + "It follows the hair outline in the picture well (fit score {value:x}, good from {limit:x})."
                         + (" The description does not name a hairstyle, so the styles were ranked by outline alone; check them by eye."
                            if _var(c, "family_source") == "none" else "")),
        "fail": lambda c: (("Hair from the library (ready-made hairstyles): "
                            + ("\"{style_name}\"" if _var(c, "style_name") else "the hairstyle")
                            + " follows the hair outline in the picture poorly (fit score {value:x}, good from {limit:x}). "
                              "Choose another style, or use the hair built with the 3D model.")
                           if _var(c, "step") == "prep" else
                           ("Hair from the library (ready-made hairstyles): nothing in the library fits this picture well. "
                            + ("The closest, \"{best_name}\", has" if _var(c, "best_name") else "The closest style has")
                            + " a fit score of {value:x} (good from {limit:x}). Pick one by eye, or use the hair built with the "
                              "3D model.")),
        "fix": lambda c: {"stage": "mesh", "params": {"hair_source": "model"}},
        "fix_label": "Use the hair built with the 3D model",
    },
    "hair_skin_through": {
        "severity": "warn",
        "ok": lambda c: ("No skin shows through the library hairstyle. A scalp cap (a hair-coloured layer under the hair) sits under it."
                         if _is_zero(c) else
                         "The library hairstyle covers the head: {value:pct} of the scalp shows through, under the {limit:pct} limit. "
                         "A scalp cap (a hair-coloured layer under the hair) hides small gaps."),
        "fail": lambda c: ("Skin shows through the library hairstyle on {value:pct} of the scalp"
                           + (" (crown {crown:pct}, sides {sides:pct})" if _var(c, "crown") is not None and _var(c, "sides") is not None else "")
                           + ", more than {limit:pct}. Fitting it again with more cover pushes the hair out a little and thickens "
                             "the scalp cap (a hair-coloured layer under the hair)."),
        "fix": lambda c: {"stage": "prep", "params": {"hair_cover": "more"}},
        "fix_label": "Fit the hair again with more cover",
    },
    "hair_triangles": {
        "severity": "warn",
        "ok": lambda c: ("With the library hairstyle your full model has {value:n} triangles, inside your platform's limit of {limit:n}"
                         + (": {body:n} for the body and {hair:n} for the hair." if _var(c, "body") is not None and _var(c, "hair") is not None else ".")),
        "fail": lambda c: ("With the library hairstyle your full model has {value:n} triangles, {over:n} more than your platform's limit of {limit:n}" + ("; the hair alone has {hair:n}" if _var(c, "hair") is not None else "")
                           + ", so it would run slower than it should. Choose a lighter hairstyle."),
        "vars": _over,
        "fix": lambda c: {"stage": "mesh", "ui": "hair_picker", "params": {}},
        "fix_label": "Choose a lighter hairstyle",
    },
    "hair_bound": {
        "severity": "warn",
        "ok": lambda c: ("The library hairstyle follows the skeleton: it moves with the head, and "
                         + ("1 hanging part sways" if _as_float(_var(c, "groups_swaying")) == 1 else "{groups_swaying:n} hanging parts sway")
                         + " with spring bones (small extra bones that sway on their own)."
                         if (_as_float(_var(c, "groups_swaying")) or 0) > 0 else
                         "The library hairstyle follows the skeleton: it rests on the head, so it moves with the head."),
        "fail": "Only {value:pct} of the library hairstyle follows the skeleton, so the rest would stay behind when the character "
                "moves. Making the moves again joins all of it to the skeleton.",
        "fix": lambda c: {"stage": "rig", "params": {}},
        "fix_label": "Make the moves again",
    },
    # Material from a photo (readiness.photo_checks). No automatic fix: a better photo is the fix.
    "even_lighting": {
        "severity": "warn",
        "ok": "The light is even across the photo.",
        "fail": "One side of the photo is much brighter than the other ({value:pct} difference), so the repeating texture will show stripes of light and dark. Take it in even, cloudy-day light.",
    },
    "straight_on": {
        "severity": "warn",
        "ok": "The photo looks taken straight on.",
        "fail": "The detail is much finer on one side of the photo than the other, which usually means it was taken at an angle; the texture would look stretched. Take it straight on.",
    },
}


def _map_name(name: str) -> str:
    m = re.match(r"^texture_(.+)_(pow2|square|budget)$", name)
    return (m.group(1).replace("_", " ") + " texture") if m else "texture"


def _pattern_guide(name: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Entries for check ids that carry a number or a map name: lodN_ratio and texture_<map>_<kind>."""
    m = re.match(r"^lod(\d+)_ratio$", name)
    if m:
        return ({"severity": "warn", "ok": "Detail level {level} keeps {value:pct} of the triangles.",
                 "fail": "Detail level {level} keeps {value:pct} of the triangles; {limit:pct} expected."}, {"level": m.group(1)})
    if name.startswith("texture_"):
        tmap = {"map": _map_name(name)}
        if name.endswith("_pow2"):
            return ({"severity": "warn", "ok": "The {map} is a power-of-two size.",
                     "fail": "The {map} is {value:dims} px; it must be a power of two."}, tmap)
        if name.endswith("_square"):
            return ({"severity": "warn", "ok": "The {map} is square.", "fail": "The {map} is {value:dims} px; it must be square."}, tmap)
        if name.endswith("_budget"):
            # With the recipe's own size (vars recipe, look): small textures are the look, not a shortfall.
            return ({"severity": "warn",
                     "ok": lambda c: ("The {map} is {value:n} px, the {look} look's size (your platform would allow {platform_texture:n} px)."
                                      if _var(c, "recipe") and _as_float(c.get("value")) == _as_float(c.get("limit"))
                                      else "The {map} is {value:n} px, within the {look} look's {limit:n} px." if _var(c, "recipe")
                                      else "The {map} is {value:n} px, within the {limit:n} px budget."),
                     "fail": lambda c: ("The {map} is {value:n} px; the {look} look uses {limit:n} px." if _var(c, "recipe")
                                        else "The {map} is {value:n} px; the platform allows {limit:n} px.")}, tmap)
    return None


def check_guide(name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The guide entry for a check id and the placeholder values its id implies ({} and {} when unknown)."""
    g = CHECK_GUIDE.get(name)
    if g is not None:
        return g, {}
    return _pattern_guide(name) or ({}, {})


def _as_float(x: Any) -> float | None:
    if isinstance(x, bool) or x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _fmt_whole(x: Any) -> str:
    f = _as_float(x)
    if f is None:
        return str(x)
    return f"{round(f):,}" if abs(f - round(f)) < 1e-9 or abs(f) >= 100 else f"{f:,.2f}"


def _fmt_pct(x: Any) -> str:
    f = _as_float(x)
    if f is None:
        return str(x)
    p = f * 100
    return f"{round(p)}%" if abs(p - round(p)) < 0.05 else f"{p:.1f}%"


def _fmt_m(x: Any) -> str:
    f = _as_float(x)
    if f is None:
        return str(x)
    s = f"{f:.2f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _fmt_list(x: Any, sep: str = ", ") -> str:
    if isinstance(x, (list, tuple)):
        return sep.join(str(v) for v in x)
    return str(x)


class _CheckFormatter(string.Formatter):
    """str.format with the specs above; a missing placeholder is an empty string, never an error."""

    def get_value(self, key: Any, args: Any, kwargs: dict[str, Any]) -> Any:
        if isinstance(key, int):
            return args[key] if key < len(args) else ""
        return kwargs.get(key, "")

    def format_field(self, value: Any, format_spec: str) -> str:
        if value is None:
            return "?"
        if isinstance(value, str) and value == "":
            return ""
        spec = format_spec.strip()
        if spec == "n":
            return _fmt_whole(value)
        if spec == "pct":
            return _fmt_pct(value)
        if spec == "m":
            return _fmt_m(value)
        if spec == "x":
            f = _as_float(value)
            return f"{f:.2f}" if f is not None else str(value)
        if spec == "list":
            return _fmt_list(value)
        if spec == "dims":
            return _fmt_list(value, " x ")
        if isinstance(value, bool):
            return "yes" if value else "no"
        if isinstance(value, int):
            return f"{value:,}"
        if isinstance(value, float):
            return _fmt_whole(value) if abs(value - round(value)) < 1e-9 else _fmt_m(value)
        if isinstance(value, (list, tuple)):
            return _fmt_list(value)
        try:
            return super().format_field(value, spec)
        except (ValueError, TypeError):
            return str(value)


_FORMATTER = _CheckFormatter()


def format_check_text(template: str, values: dict[str, Any]) -> str:
    """Fill a check sentence. Never raises: a broken template comes back as written."""
    try:
        return _FORMATTER.vformat(template, (), values)
    except (ValueError, IndexError, KeyError, AttributeError):
        return template


def describe_check(c: dict[str, Any]) -> dict[str, Any]:
    """A check with its plain sentence: help is the ok or the fail sentence by outcome, filled from value, limit and the
    check's optional vars; severity from the guide; on a failed check with a fix, fix (with label) and fix_label."""
    name = c.get("name") or c.get("id") or ""
    g, implied = check_guide(name)
    out = dict(c)
    passed = bool(c.get("passed", c.get("ok", False)))
    if not g:
        out.setdefault("severity", "warn")
        return out
    out["severity"] = g.get("severity", "warn")
    values: dict[str, Any] = {**implied, **(c.get("vars") or {})}
    if "vars" in g:
        values.update(g["vars"](c) or {})
    values["value"] = c.get("value")
    values["limit"] = c.get("limit")
    template = g.get("ok" if passed else "fail")
    if callable(template):
        template = template(c)
    if template:
        out["help"] = format_check_text(template, values)
    if not passed and "fix" in g:
        fix = g["fix"](c)
        if fix:
            label = g.get("fix_label")
            label = (label(c) if callable(label) else label) or DEFAULT_FIX_LABEL
            out["fix"] = {**fix, "label": label}
            out["fix_label"] = label
    return out


def grid_finish_line(metrics: dict[str, Any]) -> dict[str, Any] | None:
    """The finish card's grid line for scenery (plan 2.4, 3.8 B), for finish_lines to place in the category slot:
    {id, text, state, term}, or None when the prep metrics have no grid."""
    m = metrics or {}
    if m.get("grid_m") is None:
        return None
    grid = format_check_text("{g:m}", {"g": m["grid_m"]})
    if m.get("grid_fit", True):
        return {"id": "grid", "text": f"Snaps to a {grid} m grid, pivot on the corner, ready for baked lighting.", "state": "ok", "term": "grid"}
    return {"id": "grid", "text": f"Pivot on the corner and ready for baked lighting, but it does not fit the {grid} m grid yet.",
            "state": "warn", "term": "grid"}


# The tileable material category makes images only: these replace the stage estimates for it.
MATERIAL_ESTIMATES = {"concept": 60, "turnaround": 0, "mesh": 10, "prep": 1, "rig": 1, "export": 5}


def model_label_long(mid: str) -> str:
    """"Z-Image Turbo (fast painted look)": the models.yaml label plus its description."""
    desc = (reference.models().get(mid) or {}).get("description")
    label = reference.model_label(mid)
    return f"{label} ({desc})" if desc else label


def suggest(category: str, style: str, platform: str, categories: list[dict[str, Any]], styles: list[dict[str, Any]],
            platforms: list[dict[str, Any]]) -> dict[str, Any]:
    cat = next((c for c in categories if c["id"] == category), None) or {}
    sty = next((s for s in styles if s["id"] == style), None) or {}
    plat = next((p for p in platforms if p["id"] == platform), None) or {}
    model = sty.get("model") or "zturbo"
    reasons = []
    if style == "painted" and platform in ("high",):
        model = "zbase"
        reasons.append("a showcase target deserves the slower, higher-quality model")
    elif style == "painted":
        reasons.append("the fastest painted look at this size")
    elif style == "cel":
        reasons.append("anime and cel shading come out cleanest from Anima")
    elif style == "pixel":
        reasons.append("pixel art needs the pixel-grid model")
    elif style == "realistic_pbr":
        reasons.append("photoreal surfaces need the base model's detail")
    elif style == "clay":
        reasons.append("soft clay shapes come out well from the quick drafting model")
    else:
        reasons.append("a good general choice for this style")
    budgets = (cat.get("budgets") or {}).get(platform) or {}
    est = {s: STAGE_GUIDE[s]["estimate_seconds"] for s in STAGE_ORDER}
    if category == "material":
        est = {s: MATERIAL_ESTIMATES[s] for s in STAGE_ORDER}
    if platform == "high":
        est["mesh"] = 600
    if cat.get("rig_engine", "none") == "none":
        est["rig"] = 5
    try:
        rec = reference.recipe(style, category, platform)
        recipe_plain, recipe_notes = rec["plain"], list(rec.get("notes") or [])
        if rec["mesh"].get("upsample") == 1536 and category != "material":
            est["mesh"] = max(est["mesh"], 600)  # the finer 3D build is several times slower
    except KeyError:
        recipe_plain, recipe_notes = "", []
    total_min = max(1, round(sum(est.values()) / 60))
    return {"model": model, "model_label": model_label_long(model), "reason": "; ".join(reasons), "recipe_plain": recipe_plain,
            "recipe_notes": recipe_notes,
            "budgets": budgets, "estimates": est, "total_minutes": total_min,
            "platform_label": plat.get("plain_name") or plat.get("label") or platform,
            "category_label": cat.get("label") or category}


def stage_guide(stage_id: str) -> dict[str, Any]:
    return STAGE_GUIDE.get(stage_id, {})


def param_help(name: str) -> dict[str, Any]:
    return PARAM_HELP.get(name, {"simple": False, "help": {"meaning": name.replace("_", " "), "why": "", "technical": name}})
