"""Face detail pass, run at the end of the clean-up step (stages/prep.py) for characters with a face.

The 3D builder invents the face from one picture and gives it few, blurry texels: the eyes come out as smudges and the
mouth as a smear. This pass makes the face match the painted picture (vgac/face.py has the maths):

1. Render the model's head from the front with a camera we know (base colour only) and find the face on it: the nose
   tip from the shape, the skin around it by colour, eyes and mouth filled in.
2. Cut the painted picture's head out with the same framing (the concept's cut-out), and let Qwen-Image-Edit redraw
   it sharper with nothing moved (the same model and helpers the side-views step uses; one picture, no new download).
   When the redraw moved things (its features no longer sit on the crop's), the crop is used as it is.
3. Fit that picture onto the render: one scale and shift that put the picture's eye line and mouth on the model's
   (found as the darkest spots and line around the nose tip), or, when they cannot be found, the scale and shift that
   best correlate the two faces' features. A fit that does not find the face is rejected and nothing is painted.
4. When the face has too few texels to hold the detail (under an eighth of the texture's side across the face), the
   face's texture pieces are made bigger and every map is baked again onto the new layout (blender/faceuv.py).
5. Paint the fitted picture back through the model's UVs from the same camera, only on the face, weighted by how
   squarely the surface faces the camera and feathered at the edges, with its broad tone matched to the old texture.

Skipped, with the reason recorded, when the option is off, for categories other than humanoid, for crisp or flat
looks (pixel and low-poly recipes), without a painted picture, when no face is found or it is covered (helmet, hood,
mask). Metrics under "face_detail"; the before and after picture is the version file "face_before_after".
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import shutil
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from .. import face, facecloseup, facemarks, imaging, quality
from ..comfy import ComfyClient, ComfyError
from ..graphs.qwen_edit import compose_graph, edit_graph
from ..jobs import JobCancelled
from .base import StageContext, random_seed

CATEGORIES = ("humanoid",)
TARGET_FRACTION = 0.125  # texels wanted across the face: an eighth of the texture's side (256 at 2048)
GAIN_MIN = 1.15  # below this the layout is left alone (the repack and the re-bake take about 30 s)
SHARP_MIN = 1.0  # face sharpness after the pass (face.sharpness of the front render); smeared faces score 0.6 to 0.8
MATCH_MIN = 0.6  # the model's face against the fitted picture (face.feature_match)
ALIGN_MIN = 0.15  # the fit's correlation; under it the picture did not find the face (the fit's own search uses it)
# The face is painted only when the picture was lined up with the model's face at least this well (alignment.score).
# The 2026-11-13 test: the four worst faces scored 0.23, 0.31, 0.40 and 0.57 and were painted onto the wrong place
# (doubled eyes and mouths); every face scoring 0.75 or more was placed correctly. Under it the face is left as it was.
FIT_HOLD_MIN = 0.65
RIM_FRAC = 0.02  # how much of the painted face's edge is dropped outside the inner face, as a share of the picture
SOFTEN_M = 0.025  # the paint fades over about this much surface wherever it stops short (metres)
FACING = (0.15, 0.6)  # surface-to-view cosine where the paint fades in: from edge-on (old texture) to fully painted
# The same when prep has already painted the model from its pictures (vgac/srcproject.py): the cheeks under the painted
# face are then the three-quarter views' own, and the close-up's cheek outline laid on them showed as a line from the
# side (the photo boy's right cheek). The painted face hands over earlier: full to about 37 degrees, gone by 63.
FACING_OVER_PICTURES = (0.45, 0.8)
FEATURES_FEATHER = 0.05  # of the face's width: how softly the painted face ends around the eyes, nose and mouth
BROW_BAND = (0.32, 0.10)  # above and below the eye line, as shares of the face's width: where the old reach is kept
EYE_COLOUR_MAX = 8.0  # the eyes' colour on the model against the painting (Lab a-b distance); about a just visible hue change
# Each eye on its own (eye_measures, eye_state). Measured on the knights: painted eyes 0.98 to 1.0 painted, match 0.93
# to 1.0, sharp 0.58 to 0.80 of the picture's; the eye left as a smudge 0.004 painted, match 0.19, sharp 0.25.
EYE_PAINTED_MIN = 0.5  # the eye box's mean paint weight
EYE_MATCH_MIN = 0.6  # the render's eye against the picture's (face.feature_match in the box)
EYE_SHARP_MIN = 0.4  # the render's sharpness in the eye box over the picture's
EYE_COVER_STEP_M = 0.002  # hair over an eye is a flap in front of the face: a depth step (per render pixel) this big in the eye's box
EYE_HAIR_COVER = 0.3  # an unpainted eye whose box is at least this much the model's hair is under the hair
# Side views (the turnaround's pictures): which picture, from where. The quarter view's side is found by its outline.
# quarter2 is the three-quarter view from the other side: it takes the side the first one did not.
SIDE_VIEWS = (("quarter", (40.0, -40.0)), ("quarter2", (-40.0, 40.0)), ("left", (90.0,)), ("right", (-90.0,)))
SIDE_IOU_MIN = 0.85  # the side picture's head outline against the model's; under it the side is not used
SIDE_FACING = (0.25, 0.7)
DEBUG_SIDES = False  # write side_<view>.png (picture, toned, render; weight, front cover, skin) into the face folder
EYES_MOVED_MAX = 0.4  # how far the eyes may move between the model before and after, as a share of the face's width
EYE_FIT_MARGIN = 0.03  # share of the face's width by which the whole-face fit may place the eyes worse and still be used
LANDMARK_RESIDUAL_MAX = 0.12  # the eye-and-mouth fit's eye error, as a share of the face's width
REDRAW_KEEP_MIN = 0.5  # the sharpened picture against the crop: under it the redraw moved things
COVERED_WIDTH_MIN = 0.35
DEFAULT_STRENGTH = 1.0
# Reasons the person can do something about: the check fails softly with a Fix (run the clean-up step again).
ACTIONABLE = ("no_card", "not_lined_up", "poor_fit", "error")

REDRAW_PROMPT = ("Redraw this picture at high quality with crisp, sharp detail: clear eyes with irises, eyelids and lashes, "
                 "defined eyebrows, nose and lips, fine hair strands. Keep everything else exactly the same: the same "
                 "character, the same face, the same position, size, angle and framing, the same hair, clothing and grey "
                 "background, the same colours and the same painting style. Soft, even lighting.")

METHOD = "painted picture, sharpened by Qwen-Image-Edit, fitted to the 3D face and painted on through the texture layout"
METHOD_UNSHARPENED = "painted picture fitted to the 3D face and painted on through the texture layout (not sharpened)"
METHOD_CLOSEUP = ("painted picture's colour and light with the fine detail of a close-up drawn by Qwen-Image-Edit and checked "
                  "against the painting, fitted to the 3D face and painted on through the texture layout")
METHOD_GUIDED = ("painted picture's colour and light with the fine detail of a close-up drawn by Qwen-Image-Edit, guided by "
                 "your picture of the face and checked against the painting, fitted to the 3D face and painted on through the "
                 "texture layout")
METHOD_OWN = ("painted picture's colour and light with the fine detail of your own close-up of the face, lined up with the "
              "painting, fitted to the 3D face and painted on through the texture layout")

# --- A close-up source picture when the painted face is small ---------------------------------------------------
# The painted head in a full-body concept is small (the knight's face is about 68 of the concept's pixels across),
# while the texture gives the face 256 or more texels: the crop is enlarged about seven times and carries no detail at
# that size. When the painting has fewer pixels across the face than the texture will hold, the image edit model draws
# the same head as a close-up at 1024 (the crop's framing: Qwen keeps the framing of its input, which here is what is
# wanted). The face is then painted from the painting's colour and light at the painting's own resolution plus all the
# close-up's finer detail (closeup_source); the separate sharpening redraw is not run. Only when it is still the same face
# in the same look (vgac/facecloseup.py: likeness at the painting's own resolution, each feature found again by matching
# and put back if a little off, the eyes', skin's and hair's colours, skin texture). Otherwise today's path (the crop,
# sharpened) is used and the reason recorded.
CLOSEUP_PROMPT = ("A head-and-shoulders close-up portrait of this same character, drawn again at high resolution with crisp, "
                  "fine detail: the same face and features, the same eye colour, the same hair and hairstyle, the same "
                  "expression, the same clothing at the shoulders, lit from the same direction. Facing straight toward the "
                  "viewer, the head centred and the same size and position in the picture, plain flat grey background. The "
                  "same painting style and the same colours.")
# The look's own words, for a second close-up when the first drifted from the painting (closeup_tries). With the plain
# prompt the editor drew the Ranger's painted face as a photograph (pores, creases; likeness 0.86 to 0.95); with the
# look's words in the painted look and closer (0.978 and 0.983). Not first: on the first knight they gave visible brush
# strokes across the cheeks, where the plain close-up was already used. Only the painted look is measured; other looks
# get no second try.
CLOSEUP_LOOK_WORDS = {
    "painted": "a digital painting with smooth painterly shading, clean readable shapes, slightly exaggerated proportions "
               "and muted colours",
}
CLOSEUP_LOOK_PROMPT = ("A head-and-shoulders close-up of this same character, drawn again larger in the same look as the "
                       "picture: {look}. The same face and features, the same eye colour, the same hair, the same "
                       "expression, the same clothing at the shoulders, lit from the same direction. Facing straight "
                       "toward the viewer, the head centred and the same size and position in the picture, plain flat "
                       "grey background.")
# The same request for a photographic look (the Photoreal look, delight_default "remove", or realistic_pbr: `photo` in run):
# the plain prompt asks for "the same painting style", and with the realistic look the editor answered with an airbrushed
# digital painting, no pores, drawn hair strands. Used for every try there, not only the retry.
CLOSEUP_PHOTO_PROMPT = ("A head-and-shoulders close-up photograph of this same person, taken at high resolution: real skin "
                        "with visible pores and fine lines, individual hair strands, natural unretouched detail. The same "
                        "face and features, the same eye colour, the same hair and hairstyle, the same expression, the same "
                        "clothing at the shoulders, the same colours, lit from the same direction with natural light. "
                        "Facing straight toward the viewer, the head centred and the same size and position in the picture, "
                        "plain flat grey background. A real photograph, not a painting or an illustration.")
RETRY_REASONS = ("likeness", "features", "style")
CLOSEUP_TRIES_MAX = 3  # one close-up and up to two more with new seeds, each about a minute and a half of card time
CLOSEUP_DEFAULT = True  # the face_closeup option when it is not set
LIKENESS_MIN = 0.93  # face.likeness: good close-ups of the knight 0.976 to 0.980, different faces 0.19 to 0.87
CLOSEUP_SKIN_MAX = 8.0  # skin colour against the painting (Lab, lightness half); good close-ups 1.9 to 3.8
# Under a photographic look the hair is judged tone by tone (face.tone_distance, facecloseup hair_tone_shift): the middle colour
# read 12 to 18 on photographic close-ups whose hair was the same two tones (auburn and grey), over the limit of 10 that was set
# on painted faces. Tone distance: those same-hair draws 7.0 to 11.4, good stored close-ups 0.8 to 6.2, hair recoloured
# blond or blue 27.7 and more.
CLOSEUP_HAIR_TONE_MAX = 15.0
CLOSEUP_HAIR_MAX = 10.0  # hair colour, the same; good close-ups 1.6 to 3.5, a different hair colour 40 and more
CLOSEUP_MOVE_MAX = 0.04  # how far the eyes and mouth may sit from the painting's, as a share of the face's width
CLOSEUP_REASONS = {
    "likeness": "it did not look enough like the painted face",
    "features": "its eyes or mouth did not look like the painting's",
    "style": "it was drawn in a different style, more like a photograph than the painting",
    "eye_colour": "its eyes were a different colour from the painting's",
    "skin_colour": "its skin was a different colour from the painting's",
    "hair_colour": "its hair was a different colour from the painting's",
    "moved": "its eyes or mouth were not where the painting has them",
    "off_model": "its eyes and mouth did not land on the model's",
    "no_features": "its eyes and mouth could not be found",
    "error": "drawing it failed",
}

# --- Face texel density (face sharpness): how many texels the face gets -----------------------------------------
DENSITY_CHOICES = ("auto", "standard", "high")
HIGH_TEXELS = 512  # "High": about this many texels across the face, or what the source can fill, whichever is smaller
HIGH_GAIN_MOST = 4.0  # how many times its texels across the face may grow in one relayout for "High"
MAX_LOSS = 0.12  # what the rest of the body may lose, at most, in texels across (faceuv.py --max-loss)
SHIP_4096 = 4096


# The head's own turn: the camera yaw the model's face is seen straight on from. 0 for a model that faces forward; a model
# built turned (the clean-up turns it by the nearest quarter turn only, which leaves up to 45 degrees) has its face
# there, and the pass painted as if it were at 0 (the W1-clothed test run: the face painted onto a head turned 27 degrees).
# A context variable, so every camera of the pass (find, fit, paint, sides, the before-and-after sheet) agrees, including
# those in worker threads (asyncio.to_thread copies the context). Set by run().
HEAD_YAW: contextvars.ContextVar[float] = contextvars.ContextVar("face_head_yaw", default=0.0)
HEAD_YAW_MAX = 45.0


def head_yaw_from_remainder(remainder_deg: float | None) -> float:
    """The camera yaw for a head that still needs to be turned by remainder_deg (counter-clockwise from above) to face
    forward: a front is seen straight on from the yaw minus that turn. Clamped to a quarter turn's half."""
    if remainder_deg is None:
        return 0.0
    return float(max(-HEAD_YAW_MAX, min(HEAD_YAW_MAX, -float(remainder_deg))))


def head_cam(head: Any, size: int = 1024, extra: float = 0.0) -> Any:
    """face.head_camera looking at the head from where its face is (HEAD_YAW), plus `extra` degrees (a side view)."""
    return face.head_camera(head, HEAD_YAW.get() + extra, size)


def skip_reason(category_id: str, rec: dict[str, Any] | None, params: dict[str, Any], has_picture: bool) -> str | None:
    """Why the pass does not run for this version, or None when it should run. In the order a person would ask."""
    if params.get("face_detail") is False:
        return "off"
    if params.get("face_detail") is None and params.get("_face_matters") is False:
        return "face_not_important"
    if category_id not in CATEGORIES:
        return "not_humanoid"
    srf = (rec or {}).get("surface") or {}
    mesh = (rec or {}).get("mesh") or {}
    if srf.get("texture_filter") == "crisp" or srf.get("shading") == "flat" or (mesh.get("texture") or 2048) < 512:
        return "style"
    if not has_picture:
        return "no_picture"
    return None


def strength_of(params: dict[str, Any]) -> float:
    try:
        s = float(params.get("face_strength", DEFAULT_STRENGTH))
    except (TypeError, ValueError):
        s = DEFAULT_STRENGTH
    return min(1.0, max(0.0, s))


def target_texels(tex: int) -> int:
    return max(64, int(round(TARGET_FRACTION * tex)))


def density_choice(params: dict[str, Any]) -> str:
    c = params.get("face_density")
    return c if c in DENSITY_CHOICES else "auto"


def closeup_tries(style_id: str | None, photo: bool = False) -> list[tuple[str, str]]:
    """The close-ups the pass may draw, in order, as (the version's seed key, the instruction): the plain prompt, then,
    for a look whose words are known (CLOSEUP_LOOK_WORDS), the same request in the look's own words. Each try has its
    own seed, carried by Resume (jobs.RESUME_SEEDS)."""
    if photo:
        return [(f"face_closeup_seed{suffix}", CLOSEUP_PHOTO_PROMPT) for suffix in ("", "_2", "_3")]
    out = [("face_closeup_seed", CLOSEUP_PROMPT)]
    words = CLOSEUP_LOOK_WORDS.get(style_id or "")
    again = CLOSEUP_LOOK_PROMPT.format(look=words) if words else CLOSEUP_PROMPT
    out.append(("face_closeup_seed_2", again))
    out.append(("face_closeup_seed_3", again))
    return out


def closeup_spare(style_id: str | None, photo: bool = False) -> tuple[str, str]:
    """The fourth close-up, drawn only when two cards draw at once (a second round of two): its seed follows the second's."""
    if photo:
        return ("face_closeup_seed_4", CLOSEUP_PHOTO_PROMPT)
    words = CLOSEUP_LOOK_WORDS.get(style_id or "")
    return ("face_closeup_seed_4", CLOSEUP_LOOK_PROMPT.format(look=words) if words else CLOSEUP_PROMPT)


def closeup_score(t: dict[str, Any]) -> float:
    """How near the painted face a close-up that passed is: its likeness and its weakest feature's match together."""
    return float(t.get("likeness") or 0.0) + float(t.get("feature_score_min") or 0.0)


def pick_closeup(tries: list[dict[str, Any]]) -> dict[str, Any]:
    """The best close-up that passed (the first of equals), else the last one drawn."""
    ok = [t for t in tries if t.get("ok")]
    return max(ok, key=closeup_score) if ok else tries[-1]


LEND_KIND = "turnaround.run"  # the kind of work the lent card must be allowed: drawing pictures (the clean-up job itself is "prep")


async def borrow_card(sc: StageContext, home: str | None) -> tuple[Any, str]:
    """(a Lease on a second card, "") when two close-ups can be drawn at once, else (None, why not in plain words). The
    queue lends a card only when it is on, allowed to draw pictures, idle, has nothing waiting, its engine already answers
    and it has the memory for drawing (jobs.JobQueue.lend); no card, one card or a step that cannot ask: one at a time.
    The clean-up job runs on the host, not on a card, so the queue is asked for a card allowed LEND_KIND and not `home`
    (the card the first close-up is drawn on) when its ctx.borrow takes those (kind, avoid); a ctx.borrow that does not
    still lends by the clean-up job's own kind. A card lent that is the home card is handed straight back."""
    import inspect

    ask = getattr(getattr(sc, "ctx", None), "borrow", None)
    if ask is None:
        return None, "this step cannot ask for a second card"
    try:
        wanted = inspect.signature(ask).parameters
        lease, why = await (ask(kind=LEND_KIND, avoid=home) if "kind" in wanted and "avoid" in wanted else ask())
        if lease is not None and lease.instance == home:
            await lease.end()
            return None, "the only card free is the one drawing the first close-up"
        return lease, why
    except (JobCancelled, asyncio.CancelledError):
        raise
    except Exception as e:  # noqa: BLE001  (never fail the pass because a card could not be asked)
        return None, f"a second card could not be asked ({str(e)[:100]})"


def retry_wanted(first: dict[str, Any]) -> bool:
    """Another close-up is drawn (up to CLOSEUP_TRIES_MAX in all, each with its own seed) only when the last one was not
    the painting's face or look (likeness, features, style): the editor's drift, which a new seed or the look's own words
    changes (the same picture was refused at likeness 0.880 and accepted in the next run). "no_features" counts when the
    features could not be found in the drawn close-up, but not when the model's own face was not found (the guard marks
    that model_face_missing): that is the same for every seed. A close-up refused for its colours or for moved
    features is not drawn again, and neither is one whose drawing failed."""
    if first.get("ok"):
        return False
    reason = first.get("reason")
    if reason == "no_features":
        return not first.get("model_face_missing")
    return reason in RETRY_REASONS


def retry_message(n: int, total: int, message: str) -> str:
    """The progress words for draw number n (0 is the first): a retry says so."""
    return message if n == 0 else f"The close-up was not good enough; trying again (draw {n + 1} of {total})"


def closeup_on(params: dict[str, Any]) -> bool:
    v = params.get("face_closeup")
    return CLOSEUP_DEFAULT if v is None else bool(v)


def closeup_wanted(params: dict[str, Any], painting_face_px: float, texels_wanted: int) -> bool:
    """A close-up is drawn when the option is on and the painting has fewer of its own pixels across the face than
    the texture will give the face in texels (the knight: 68 against 256, or 459 for High). An uploaded portrait whose
    face is large already is its own source."""
    return closeup_on(params) and float(painting_face_px) < float(texels_wanted)


def source_face_px(face_width_px: float, painted_head_px: float, size: int = 1024) -> float:
    """How many of the painting's own pixels lie across the face: the face's width on the 1024 framing times the
    painted head box's size in the concept over 1024."""
    return round(float(face_width_px) * float(painted_head_px) / float(size), 1)


def plan_density(choice: str, budget_tex: int | None, atlas_tex: int, source_px: float | None) -> dict[str, Any]:
    """What the face gets: {choice, level (standard | high), target (texels across the face, at the shipped base
    colour's size), ship (the base colour's size), why}.

    Standard is today's: an eighth of the atlas's side. High is HIGH_TEXELS or as many as the source picture has pixels
    across the face, whichever is smaller (never under Standard). Where the platform's budget is 4096 and the atlas
    is smaller, High ships the base colour at 4096 (only the base colour: the other maps hold no face detail, so
    enlarging them would only cost memory). Automatic is High when the platform's budget is 2048 or more and the source
    has more pixels across the face than Standard gives texels, else Standard: on a 1024 budget the face's share is
    already an eighth of the texture and High would take a large part of what the body has."""
    budget = int(budget_tex or atlas_tex)
    std = target_texels(atlas_tex)
    src = float(source_px) if source_px is not None else float("inf")
    if choice == "high":
        level = "high"
        why = "chosen"
    elif choice == "standard":
        level, why = "standard", "chosen"
    elif budget < 2048:
        level, why = "standard", "small_budget"
    elif src <= std:
        level, why = "standard", "small_source"
    else:
        level, why = "high", "auto"
    ship = atlas_tex
    if level == "high" and budget >= SHIP_4096 and atlas_tex < SHIP_4096:
        ship = SHIP_4096
    if level == "standard":
        target = int(round(std * ship / atlas_tex))
    else:
        target = int(max(std * ship / atlas_tex, min(HIGH_TEXELS, src)))
    return {"choice": choice, "level": level, "target": target, "ship": int(ship), "why": why, "budget": budget,
            "standard": std}


def density_note(plan: dict[str, Any], achieved: int | None, rest_scale: float | None) -> str | None:
    """The plain sentence when High was asked for and not met: how many texels the face got, and why not more."""
    if plan.get("level") != "high" or achieved is None:
        return None
    if achieved >= 0.92 * float(plan["target"]):
        return None
    loss = "" if rest_scale is None else f" (the rest of the body now has {round(100 * float(rest_scale))} percent of its texels across)"
    return (f"High face detail asked for about {plan['target']} texels across the face; the {plan['budget']} px texture "
            f"budget allowed {achieved} without the rest of the body losing more than {round(100 * MAX_LOSS)} percent of "
            f"its sharpness{loss}.")


SIDE_WORDS = {"quarter": "the three-quarter view", "quarter2": "the other three-quarter view", "left": "the left side",
              "right": "the right side"}


def side_words(used: list[str]) -> str:
    """'the three-quarter view and the right side' for the sides used."""
    words = [SIDE_WORDS.get(u, u) for u in used]
    if not words:
        return ""
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def face_checks(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """face_matches_picture (warn), from the "face_detail" metrics block. Nothing when the pass does not apply (off,
    not a humanoid, a crisp or flat look, no picture, no face or a covered one): there is nothing to measure. A soft
    failure when it was skipped for a reason the person can act on, or when the face is still blurry or does not
    follow the fitted picture by the measured numbers. It measures sharpness and how well the model's face carries
    the fitted picture's features, not whether it is the same person."""
    fd = (metrics or {}).get("face_detail") or {}
    if not fd:
        return []
    if not fd.get("ran"):
        reason = fd.get("skipped")
        if reason not in ACTIONABLE:
            return []
        if reason == "poor_fit":
            score = float((fd.get("alignment") or {}).get("score") or 0.0)
            return [quality.check("face_matches_picture", False, round(score, 3), FIT_HOLD_MIN, vars={"reason": reason})]
        return [quality.check("face_matches_picture", False, reason, SHARP_MIN, vars={"reason": reason})]
    sharp = float(fd.get("sharpness_after") or 0.0)
    match = float(fd.get("feature_match_after") or 0.0)
    shift = fd.get("eye_colour_shift")
    colour_ok = shift is None or float(shift) <= EYE_COLOUR_MAX
    eye_reason, bad_eyes = eyes_verdict(fd.get("eyes"))
    # The face found on the model far from where its eyes are: the painting lands on the wrong part of the head (step
    # 0.5's man: the face finder put the eyes on the mouth and the eyes stayed the 3D step's smudges; moved 0.63 of the
    # face's width, where 48 other passes moved 0.05 to 0.32).
    misplaced = fd.get("eyes_moved") is not None and float(fd["eyes_moved"]) > EYES_MOVED_MAX
    ok = sharp >= SHARP_MIN and match >= MATCH_MIN and colour_ok and eye_reason is None and not misplaced
    reason = None if ok else ("misplaced" if misplaced else "blurry" if sharp < SHARP_MIN else eye_reason if eye_reason
                              else "off_picture" if match < MATCH_MIN else "eye_colour")
    cu = fd.get("closeup") or {}
    dens = fd.get("density") or {}
    value, limit = failure_measure(reason, fd, sharp, match, shift) if not ok else (round(sharp, 3), SHARP_MIN)
    return [quality.check("face_matches_picture", ok, value, limit,
                          vars={"reason": reason, "eye_shift": shift, "sides": side_words(fd.get("sides_used") or []),
                                **eye_words(bad_eyes if reason == eye_reason else []),
                                "before": fd.get("sharpness_before"), "match": round(match, 3), "texels": fd.get("face_texels_after"),
                                "source": source_words(fd), "closeup_not_used": CLOSEUP_REASONS.get(cu.get("reason") or "", "")
                                if cu.get("wanted") and not cu.get("used") else "",
                                "closeup_own": bool(cu.get("own")), "closeup_notes": "; ".join(cu.get("notes") or []) if cu.get("used") else "",
                                "density_note": dens.get("note") or "", "level": dens.get("level") or "standard"})]


def failure_measure(reason: str | None, fd: dict[str, Any], sharp: float, match: float, shift: Any) -> tuple[Any, Any]:
    """The number and the limit that belong to the reason a face_matches_picture check failed, so the check's value reads
    "1.678 (limit 1.0)" only when sharpness is the reason (it showed sharpness against 1.0 for every failure)."""
    if reason == "blurry":
        return round(sharp, 3), SHARP_MIN
    if reason == "off_picture":
        return round(match, 3), MATCH_MIN
    if reason == "eye_colour":
        return round(float(shift), 2), EYE_COLOUR_MAX
    if reason == "misplaced":
        return round(float(fd["eyes_moved"]), 3), EYES_MOVED_MAX
    if reason in ("eye_under_hair", "eye_missing", "eye_blurry"):
        bad = [e for e in (fd.get("eyes") or []) if eye_state(e) == reason]
        if bad and reason == "eye_blurry":
            return round(min(float(e.get("sharp") or 0.0) for e in bad), 3), EYE_SHARP_MIN
        if bad and reason == "eye_missing" and all(float(e.get("painted") or 0.0) >= EYE_PAINTED_MIN for e in bad):
            return round(min(float(e.get("match") or 0.0) for e in bad), 3), EYE_MATCH_MIN
        if bad:
            return round(min(float(e.get("painted") or 0.0) for e in bad), 3), EYE_PAINTED_MIN
    return round(sharp, 3), SHARP_MIN


def eye_state(e: dict[str, Any]) -> str | None:
    """What is wrong with one eye from eye_measures, or None when it was painted and is sharp: "eye_under_hair" (not
    painted because the model's hair covers it), "eye_missing" (not painted, or the render does not show the
    picture's eye), "eye_blurry" (painted but much softer than the picture's)."""
    painted = float(e.get("painted") or 0.0)
    if painted < EYE_PAINTED_MIN:
        # the hair mask is colour only: a shadowed cheek or brow of a dark face counts as hair (the live M1-clothed, 0.7 of an
        # eye box that has no hair over it). Covered means a flap in front of the eye: a step in depth in its box (0.2 to 0.6 mm per
        # pixel on the false alarms, 3 to 19 on real flaps). Measured runs before the step was recorded keep the old verdict.
        covered = float(e.get("hair") or 0.0) >= EYE_HAIR_COVER and (e.get("step") is None or float(e["step"]) >= EYE_COVER_STEP_M)
        return "eye_under_hair" if covered else "eye_missing"
    if e.get("match") is not None and float(e["match"]) < EYE_MATCH_MIN:
        return "eye_missing"
    if e.get("sharp") is not None and float(e["sharp"]) < EYE_SHARP_MIN:
        return "eye_blurry"
    return None


def eyes_verdict(eyes: list[dict[str, Any]] | None) -> tuple[str | None, list[str]]:
    """The worst eye state over both eyes (under hair, then missing, then blurry) and the sides that have it. (None,
    []) when both are fine or the eyes were not measured (older versions, eyes not found in the picture)."""
    states = [(eye_state(e), e.get("side")) for e in (eyes or [])]
    for reason in ("eye_under_hair", "eye_missing", "eye_blurry"):
        sides = [s for r, s in states if r == reason]
        if sides:
            return reason, sides
    return None, []


def eye_words(sides: list[str]) -> dict[str, Any]:
    """Placeholders for the eye sentences: eye_words ("the right eye (on the viewer's left)", or "both eyes") and
    eye_verb (was or were). The side is the character's own."""
    if not sides:
        return {}
    if len(sides) > 1:
        return {"eye_words": "both eyes", "eye_verb": "were", "eye_sides": sides}
    viewer = "left" if sides[0] == "right" else "right"
    return {"eye_words": f"the {sides[0]} eye (on the viewer's {viewer})", "eye_verb": "was", "eye_sides": sides}


def source_words(fd: dict[str, Any]) -> str:
    """Which picture the face was painted from, in plain words."""
    if fd.get("source") == "closeup" and (fd.get("closeup") or {}).get("guided") and (fd.get("closeup") or {}).get("used"):
        return "a close-up of the painted face, drawn guided by your picture of the face and checked against the painting"
    if fd.get("source") == "closeup" and (fd.get("closeup") or {}).get("own"):
        return "your own close-up of the face, lined up with the painting"
    if fd.get("source") == "closeup":
        return "a close-up of the painted face, drawn larger and checked against the painting"
    return "the painted picture"


# --- CPU parts (run in a worker thread) ---------------------------------------------------------------------


def inspect(glb: Path, base_color: Path, cutout: Path, out_dir: Path) -> dict[str, Any]:
    """Find the face on the model and cut the painted head out to match. Writes painted_head.png. Returns
    {found, reason, ...measures}."""
    mesh = face.load_mesh(glb)
    if mesh is None:
        return {"found": False, "reason": "no_face", "detail": "the model has no texture layout"}
    tex = face.read_rgb(base_color)
    head = face.find_head(mesh["verts"])
    if head is None:
        return {"found": False, "reason": "no_face"}
    cam = head_cam(head, 1024)
    view = face.render_view(mesh, tex, cam)
    info = face.find_face_checked(view, mesh, cam, head)
    if not info.get("found"):
        return {"found": False, "reason": "no_face", "detail": info.get("reason")}
    if face.covered_head(info, head, cam, COVERED_WIDTH_MIN):
        return {"found": False, "reason": "covered", "face_width_px": info.get("width_px")}
    rgba = np.asarray(Image.open(cutout).convert("RGBA"), dtype=np.float64) / 255.0
    crop = face.painted_head(rgba, head, cam)
    if crop is None:
        return {"found": False, "reason": "no_picture"}
    face.save_rgb(crop["image"], out_dir / "painted_head.png")
    dens = face.face_texels(mesh, cam, info["region"], tex.shape[0])
    tris = face.face_triangles(view, info["region"])
    # nose_from is only there when the shape's nose was corrected by the finder's eyes and mouth (face.find_face_checked)
    return {"found": True, "reason": None, "texture_size": int(tex.shape[0]), "face_width_px": info["width_px"],
            "painted_head_px": crop["head_px"], "face_triangles": face.triangle_centres(mesh, tris).round(6).tolist(), **dens,
            **({"nose_from": info["nose_from"]} if info.get("nose_from") else {})}


def closeup_guard(glb: Path, base_color: Path, crop: Path, closeup: Path, painted_head_px: float,
                  photo: bool = False, guided: bool = False) -> dict[str, Any]:
    """Is the close-up still the painted face? (vgac/facecloseup.py) Each of the painting's eyes and its mouth is
    looked for in the close-up by block matching at the painting's own resolution; one that sits a little off is moved
    back onto the painting's place by a local warp (the head's outline stays); then likeness at the painting's
    resolution, how well each feature matches, what is left of the move and the eye, skin and hair colours are judged.
    The corrected close-up is written next to it (<name>_fixed.png, "registered" in the result) and is the one painted
    from. Returns {ok, reason (a CLOSEUP_REASONS key or None), likeness, feature_scores, feature_score_min,
    feature_move, feature_move_raw, eye_colour_shift, skin_shift, hair_shift, correction, registered, source_face_px}."""
    mesh = face.load_mesh(glb)
    tex = face.read_rgb(base_color)
    head = face.find_head(mesh["verts"])
    cam = head_cam(head, 1024)
    view = face.render_view(mesh, tex, cam)
    info = face.find_face_checked(view, mesh, cam, head)
    if not info.get("found"):
        return {"ok": False, "reason": "no_features", "model_face_missing": True}
    out = facecloseup.guard(view, info, face.read_rgb(crop, 1024), face.read_rgb(closeup, 1024), painted_head_px,
                            LANDMARK_RESIDUAL_MAX, ALIGN_MIN)
    fixed = facecloseup.save_registered(out, Path(closeup).with_name(Path(closeup).stem + "_fixed.png"))
    out["registered"] = str(fixed) if fixed else None
    reason = closeup_verdict(out, photo, guided)
    return {"ok": reason is None, "reason": reason, **out}


def hair_too_far(m: dict[str, Any], photo: bool, guided: bool = False) -> bool:
    """The close-up's hair is another colour: tone by tone under a photographic look (when measured), else the middle colour."""
    if photo and m.get("hair_tone_shift") is not None:
        return float(m["hair_tone_shift"]) > CLOSEUP_HAIR_TONE_MAX
    return m.get("hair_shift") is not None and float(m["hair_shift"]) > (CLOSEUP_HAIR_MAX_GUIDED if guided else CLOSEUP_HAIR_MAX)


def closeup_verdict(m: dict[str, Any], photo: bool = False, guided: bool = False) -> str | None:
    """Why a close-up is not used (a CLOSEUP_REASONS key), from closeup_guard's measures, or None when it is the same
    face. Not measurable colours do not count against it; an unmeasurable fit to the model is left to the paint step's
    whole-face fit and its ALIGN_MIN, as for the painting (the second guard measures none: the close-up is painted
    through the painting's own fit). photo: the Photoreal look, where pores and creases are wanted, so the style
    measure (a painted face turned photographic) does not apply."""
    if m.get("feature_move") is None:
        return "no_features"
    if float(m.get("likeness") or 0.0) < (LIKENESS_MIN_GUIDED if guided else LIKENESS_MIN):
        return "likeness"
    if m.get("feature_score_min") is not None and float(m["feature_score_min"]) < facecloseup.FEATURE_MIN:
        return "features"
    if not photo and m.get("skin_texture") is not None and float(m["skin_texture"]) > facecloseup.SKIN_TEXTURE_MAX:
        return "style"
    if float(m["feature_move"]) > CLOSEUP_MOVE_MAX:
        return "moved"
    if float(m.get("eye_colour_shift") or 0.0) > EYE_COLOUR_MAX:
        return "eye_colour"
    if m.get("skin_shift") is not None and float(m["skin_shift"]) > CLOSEUP_SKIN_MAX:
        return "skin_colour"
    if hair_too_far(m, photo, guided):
        return "hair_colour"
    if m.get("fit_residual") is not None and float(m["fit_residual"]) > LANDMARK_RESIDUAL_MAX:
        return "off_model"
    return None


def closeup_source(crop: Path, closeup: Path, painted_head_px: float, dest: Path) -> Path:
    """The picture painted from a checked close-up: the painting's colour and light at every scale the painting
    really has (blurred at one of its own pixels on the 1024 framing), and the close-up's finer detail, colour
    included, on top. So the eyes keep the painting's grey-green and the cheek keeps its painted shadow, while the
    lashes, iris, brows and lips come out as sharp as the close-up draws them (judged on the knight: the close-up alone
    lit her face more evenly and turned her eyes a little hazel)."""
    a = face.read_rgb(crop, 1024)
    b = face.read_rgb(closeup, 1024)
    sigma = 1024.0 / max(float(painted_head_px), 1.0)
    return face.save_rgb(face.merge_detail(a, b, sigma, rgb=True), dest)


# --- The guided close-up -----------------------------------------------------------------------------------------
# With a picture of the real face, the close-up is drawn from the painted crop (Picture 1: its framing, pose, expression
# and light) with the face's identity taken from the picture (Picture 2). On the T-pose man, guided by a crop of his
# selfie: likeness to the painting 0.94 to 0.96 and features 0.89 to 0.91 against 0.89 to 0.92 and 0.85 to 0.89 for
# the plain close-up, and by eye clearly his face (curls, grey in the beard, eye shape), where the plain ones were a
# similar man. Its hair came out 10.7 to 12.2 from the painting's colour (grey strands from the photo): the painting's
# own colour is laid back over the close-up (closeup_source), so a guided close-up may differ in hair colour up to
# CLOSEUP_HAIR_MAX_GUIDED. Two tries, each judged by the same guard; the first that passes is used. Lining your own
# close-up up directly (frame_own) is the fallback: a photo with another expression rarely lines up.
# A margin round a tight crop. The editor draws a head-and-shoulders close-up: from a crop the head fills (step 0.5's
# man, face 688 of 1024 px wide) it zoomed out, so every close-up landed smaller and lower than the painting and was
# refused (likeness 0.28 to 0.39). Shrunk into a grey margin first and cut back to the crop's framing afterwards, the
# guided ones scored 0.88 and 0.90 with the features in place (0.003 to 0.005 of the face's width). Guided close-ups get
# the margin only for a tight crop (on the T-pose man, face 519 px, it lowered them from 0.94 to 0.96 to 0.79 to 0.91);
# plain close-ups in the Photoreal look always get at least PLAIN_MARGIN (the T-pose's went from 0.89 to 0.91, refused,
# to 0.93 to 0.96); painted looks get it only for a tight crop (their plain close-ups already passed at 0.976).
MARGIN_FACE_PX = 540.0  # a crop whose face is wider than this (on its 1024 framing) is shrunk until it is this wide
MARGIN_MIN = 0.7
PLAIN_MARGIN = 0.8
# A guided close-up carries the guide's finer features, so it differs a little more from the blurry painting: 0.88 is
# still above every different face the guard was measured on (up to 0.87), and each feature must still match (0.85).
LIKENESS_MIN_GUIDED = 0.88


def closeup_margin(face_width_px: float, guided: bool, photo: bool = False) -> float:
    """The scale the crop is drawn at inside a grey margin (1.0: no margin)."""
    k = min(1.0, MARGIN_FACE_PX / max(float(face_width_px), 1.0))
    if not guided and photo:
        k = min(k, PLAIN_MARGIN)
    return round(max(MARGIN_MIN, k), 3)


def add_margin(src: Path, k: float, dest: Path, size: int = 1024, grey: int = 64) -> Path:
    im = Image.open(src).convert("RGB").resize((size, size), Image.LANCZOS)
    n = int(round(size * k))
    out = Image.new("RGB", (size, size), (grey, grey, grey))
    out.paste(im.resize((n, n), Image.LANCZOS), ((size - n) // 2, (size - n) // 2))
    out.save(dest)
    return dest


def remove_margin(src: Path, k: float, dest: Path, size: int = 1024) -> Path:
    im = Image.open(src).convert("RGB").resize((size, size), Image.LANCZOS)
    n = int(round(size * k))
    o = (size - n) // 2
    im.crop((o, o, o + n, o + n)).resize((size, size), Image.LANCZOS).save(dest)
    return dest


GUIDE_FILE = "face_guide.png"
CLOSEUP_HAIR_MAX_GUIDED = 16.0
GUIDED_PROMPT = ("Picture 1 and Picture 2 show the same character; Picture 2 is a close-up of their face. Redraw Picture 1 as a "
                 "sharp, high-resolution head-and-shoulders close-up of this exact character with the identical face from "
                 "Picture 2: the same eyes, nose, mouth, skin texture, hairline, hair and facial hair. Keep everything about "
                 "Picture 1's framing: the head the same size, position and angle, the same expression, the same lighting "
                 "and colours, so every feature sits exactly where it is in Picture 1. Plain flat grey background. {look}")
GUIDED_LOOK = {True: "Photorealistic.", False: "In the same look and colours as Picture 1."}


def guided_tries(photo: bool) -> list[tuple[str, str]]:
    prompt = GUIDED_PROMPT.format(look=GUIDED_LOOK[bool(photo)])
    return [("face_closeup_seed", prompt), ("face_closeup_seed_2", prompt)]


def compose_face_picture(sc: StageContext) -> Path | None:
    """Step 0.5's face picture, when the first picture came from step 0.5 and one picture was given as the face."""
    for v in sc.iter_lineage(include_self=False):
        if v.stage != "concept":
            continue
        roles = (v.params or {}).get("compose_roles") or []
        for i, r in enumerate(roles, start=1):
            if (r or {}).get("role") == "face":
                from .. import library

                p = library.version_file(v, f"compose_{i}", sc.settings)
                if p is not None and Path(p).is_file():
                    return Path(p)
        return None
    return None


def guide_picture(sc: StageContext, params: dict[str, Any], own: Path | None) -> tuple[Path, str] | None:
    """(the picture of the real face that guides the close-up, "own" or "compose"), or None. Off with
    face_closeup_guided false on the run, or with the close-up option off."""
    if params.get("face_closeup_guided") is False or not closeup_on(params):
        return None
    if own is not None:
        return own, "own"
    try:
        p = compose_face_picture(sc)
    except Exception:  # noqa: BLE001  (a context without a lineage)
        p = None
    return (p, "compose") if p is not None else None


# --- Your own close-up of the face ----------------------------------------------------------------------------------
# The person may give the face pass their own close-up (library.own_face_closeup, added in the clean-up step). It is
# used instead of the drawn one, whatever the painting's size: framed like the painted crop first (frame_own: one
# scale and shift found by matching its features against the painting's), then the same guard as a drawn close-up,
# which moves any feature that sits a little off back onto the painting's place. Because the person chose it, only the
# reasons that would paint the features in the wrong place stop it (OWN_HARD); a different likeness, style or colour
# is recorded as a note (closeup.notes) and the picture is still used. As for a drawn close-up, the painting gives the
# broad colour and light, the close-up the fine detail (closeup_source), so the face still matches the body.
OWN_HARD = ("no_features", "moved", "off_model", "not_found")
OWN_SCALES = (0.35, 2.0)  # how much larger or smaller than the painted crop's head the person's head may be
OWN_FILE = "own_face_closeup.png"
CLOSEUP_REASONS["not_found"] = "the face in it could not be lined up with the painted face"
CLOSEUP_REASONS["own_error"] = "it could not be read"

# The close-up shown on the step's card while the clean-up step is still working (the card reads files[LIVE_KEY] and
# metrics["face_closeup_live"] = {state, caption} of the running version). state: checking, again, used, refused.
LIVE_KEY = "face_closeup_draft"


def live_caption(n: int, total: int, guard: dict[str, Any] | None, again: bool) -> tuple[str, str]:
    """(state, plain caption) for draw n of total: just drawn (guard None), or its verdict."""
    if guard is None:
        return "checking", f"The close-up of the face (draw {n} of {total}) has just been drawn. It is being checked against the painted face."
    if guard.get("ok"):
        return "used", f"The close-up of the face (draw {n} of {total}) passed its checks. The face is being painted from it."
    why = CLOSEUP_REASONS.get(str(guard.get("reason")), "it did not pass the checks")
    if again:
        return "again", f"This close-up (draw {n} of {total}) is not used, because {why}. Drawing it again."
    return "refused", f"This close-up (draw {n} of {total}) was not used, because {why}."


def show_closeup(sc: StageContext, path: Path, state: str, caption: str) -> None:
    """Put a close-up on the running step's card now, with its caption. Never fails the pass: a picture on the card is a
    courtesy."""
    register = getattr(sc, "register", None)
    if register is None or not Path(path).is_file():
        return
    try:
        register(LIVE_KEY, path)
        sc.set_metrics(face_closeup_live={"state": state, "caption": caption})
        sc.publish()
    except Exception:  # noqa: BLE001
        pass


def own_closeup_file(sc: StageContext, params: dict[str, Any]) -> Path | None:
    """The person's own close-up for this asset, or None (none added, or face_closeup_own is false on the run)."""
    if params.get("face_closeup_own") is False:
        return None
    a = getattr(sc, "asset", None)
    if a is None or not getattr(a, "project_id", None):
        return None
    from .. import library

    p = library.own_face_closeup(a.project_id, a.id, getattr(sc, "settings", None))
    return p if p.is_file() else None


def square_picture(path: Path, size: int = 1024, background: float = 0.25) -> np.ndarray:
    """The picture on a square of `size` without stretching it: centred, the rest filled with the crop's grey."""
    im = Image.open(path).convert("RGB")
    w, h = im.size
    k = size / max(w, h)
    im = im.resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS)
    g = int(round(background * 255))
    out = Image.new("RGB", (size, size), (g, g, g))
    out.paste(im, ((size - im.size[0]) // 2, (size - im.size[1]) // 2))
    return np.asarray(out, dtype=np.float64) / 255.0


def coarse_frame(src: np.ndarray, dst: np.ndarray, region: np.ndarray, scales: tuple[float, float] = OWN_SCALES,
                 work: int = 256, steps: int = 32) -> dict[str, Any]:
    """One scale and a wide shift that lay src's face over dst's (band-passed luminance inside region), searched over
    a much wider range than face.align (a person's portrait is framed any way). The result is an alignment in the
    full-size pixels, refined afterwards by face.align."""
    S = dst.shape[0]
    k = S / work
    d = face.band(face.resize(dst, work), 1.0, 6.0)
    s_img = face.resize(src, work)
    msk = (face.resize(region.astype(np.float64), work) > 0.5).astype(np.float64)
    ys, xs = np.where(msk > 0)
    c = ((xs.mean() + 0.5), (ys.mean() + 0.5)) if len(xs) else (work / 2, work / 2)
    d = (d - d[msk > 0].mean()) * msk if len(xs) else d
    best: dict[str, Any] = {"score": -2.0, "s": 1.0, "dx": 0, "dy": 0}
    for s in np.geomspace(scales[0], scales[1], steps):
        b = face.band(face.scale_image(s_img, float(s), float(s), 0.0, 0.0, c), 1.0, 6.0)
        score, dx, dy = face._ncc_shift(d, b, msk, int(0.35 * work))
        if score > best["score"]:
            best = {"score": score, "s": float(s), "dx": dx, "dy": dy}
    return {"sx": best["s"], "sy": best["s"], "tx": best["dx"] * k, "ty": best["dy"] * k, "centre": (c[0] * k, c[1] * k),
            "score": round(float(best["score"]), 4)}


def frame_own(glb: Path, base_color: Path, crop: Path, own: Path, dest: Path) -> dict[str, Any]:
    """The person's close-up framed like the painted crop (1024, written to dest): a wide search for the scale and
    shift, then face.align's finer one (scale per axis within a little, shift), as one resampling. Returns {ok,
    reason ("not_found" or "no_features"), score, alignment}; ok when the features correlate at ALIGN_MIN or more."""
    mesh = face.load_mesh(glb)
    tex = face.read_rgb(base_color)
    head = face.find_head(mesh["verts"]) if mesh is not None else None
    if head is None:
        return {"ok": False, "reason": "no_features"}
    cam = head_cam(head, 1024)
    view = face.render_view(mesh, tex, cam)
    info = face.find_face_checked(view, mesh, cam, head)
    if not info.get("found"):
        return {"ok": False, "reason": "no_features"}
    inner = face.inner_face(info)
    a = face.read_rgb(crop, 1024)
    b = square_picture(own)
    first = coarse_frame(b, a, inner)
    fine = face.align(face.apply_alignment(b, first), a, inner)
    total = face.compose_alignment(first, fine)
    framed = face.apply_alignment(b, total)
    # outside the person's picture: the crop's plain grey, not its edge pixels stretched out
    inside = np.ones(b.shape[:2] + (3,), dtype=np.float64)
    inside[0, :] = inside[-1, :] = inside[:, 0] = inside[:, -1] = 0.0
    framed = np.where(face.apply_alignment(inside, total)[..., :1] > 0.5, framed, 0.25)
    face.save_rgb(framed, dest)
    score = float(fine.get("score") or 0.0)
    return {"ok": score >= ALIGN_MIN, "reason": None if score >= ALIGN_MIN else "not_found", "score": round(score, 4),
            "coarse_score": first["score"], "alignment": {k: total.get(k) for k in ("sx", "sy", "tx", "ty")}}


def own_verdict(guard: dict[str, Any], photo: bool = False) -> tuple[str | None, list[str]]:
    """For the person's own close-up: (the reason it cannot be used, or None; the notes on how it differs from the
    painting). Only a picture whose features would be painted in the wrong place is refused (OWN_HARD, or no corrected
    picture); likeness, style and colours become notes."""
    m = guard
    if m.get("reason") in OWN_HARD:
        return m["reason"], []
    if m.get("feature_move") is None or not m.get("registered"):
        return "no_features", []
    if float(m["feature_move"]) > CLOSEUP_MOVE_MAX:
        return "moved", []
    notes: list[str] = []
    # every soft reason that applies, not only the first one closeup_verdict meets
    if float(m.get("likeness") or 1.0) < LIKENESS_MIN:
        notes.append("likeness")
    if m.get("feature_score_min") is not None and float(m["feature_score_min"]) < facecloseup.FEATURE_MIN:
        notes.append("features")
    if not photo and m.get("skin_texture") is not None and float(m["skin_texture"]) > facecloseup.SKIN_TEXTURE_MAX:
        notes.append("style")
    if float(m.get("eye_colour_shift") or 0.0) > EYE_COLOUR_MAX:
        notes.append("eye_colour")
    if m.get("skin_shift") is not None and float(m["skin_shift"]) > CLOSEUP_SKIN_MAX:
        notes.append("skin_colour")
    if hair_too_far(m, photo):
        notes.append("hair_colour")
    return None, notes


def redraw_kept(sharp: Path, crop: Path) -> float:
    """How well the redrawn picture keeps the crop's features in place (whole picture)."""
    a = face.read_rgb(sharp, 1024)
    b = face.read_rgb(crop, 1024)
    return face.feature_match(a, b, np.ones(a.shape[:2], dtype=bool), lo=2.0, hi=10.0)


def fit_and_paint(glb: Path, base_color: Path, picture: Path, before_glb: Path, before_tex: Path, crop: Path, out_dir: Path,
                  strength: float, sides: dict[str, Path] | None = None, colour_source: Path | None = None,
                  require_landmarks: bool = False, fit_from_crop: bool = False,
                  facing: tuple[float, float] = FACING) -> dict[str, Any]:
    """Steps 3 and 5 on the final layout: fit the picture to the render, paint it back, measure, and draw the before
    and after sheet. Writes base_color_face.png and face_before_after.png into out_dir.

    colour_source: the picture whose colours and broad tone are painted (the painted crop by default; the checked
    close-up when one is used). The eye colour is always measured against the painted crop. require_landmarks: the
    eye-and-mouth fit must work (a close-up whose features do not land on the model's is not used: reason
    "closeup_off_model"). fit_from_crop: the fit is found on the painted crop and the picture painted through it (a
    close-up checked by facecloseup has its features where the crop has them, and its sharp lashes would only mislead
    the darkest-spot search)."""
    mesh = face.load_mesh(glb)
    tex = face.read_rgb(base_color)
    T = tex.shape[0]
    head = face.find_head(mesh["verts"])
    cam = head_cam(head, 1024)
    view = face.render_view(mesh, tex, cam)
    info = face.find_face_checked(view, mesh, cam, head)
    if not info.get("found"):
        return {"ok": False, "reason": "no_face"}
    region = info["region"]
    crop_img = face.read_rgb(crop, 1024)
    colour = Path(colour_source) if colour_source is not None else Path(crop)
    painted = crop_img if colour == Path(crop) else face.read_rgb(colour, 1024)
    # The colour source is the authority on colour and broad look; a sharpened picture only adds fine detail.
    src = painted if Path(picture) == colour else face.merge_detail(painted, face.read_rgb(picture, 1024))
    inner = face.inner_face(info)
    # The eye-and-mouth fit is used when it explains the picture: its eyes land within LANDMARK_RESIDUAL_MAX of the face's
    # width of the model's and the broad features agree; it keeps the eye line and the mouth where the model has them.
    # Otherwise the whole-face correlation (bounded stretch). Scores are on broad features: the model's face is
    # blurred, so its fine detail matches nothing.
    a = fit_picture(crop_img if fit_from_crop else src, view["image"], info, inner)
    if a["how"] != "eyes and mouth" and require_landmarks:
        return {"ok": False, "reason": "closeup_off_model"}
    if a["score"] < FIT_HOLD_MIN:
        # held: nothing is painted and the model keeps the face it had before the pass (the caller returns before it
        # replaces the model's texture)
        return {"ok": False, "reason": "poor_fit", "alignment": a}
    # No local warping after this: block matching against the blurred model face moved the brows into a frown on the
    # knight (tried and dropped); one scale and shift keeps the painted face as it was drawn.
    fitted = face.apply_alignment(src, a)
    match_fit = face.feature_match(fitted, view["image"], region)
    # Hair stays hair and skin stays skin: never paint onto the model's hair (a flap over an eye, the fringe), and
    # where the picture has hair or background over the model's skin (its fringe and jaw are not where the model's
    # are), fill that spot from the painted skin around it instead.
    painted_fitted = face.apply_alignment(crop_img, a)  # the eye colour is measured against the painting itself
    seg = face.hair_and_skin(view["image"], fitted, info, view["mask"])
    paint = region & ~face.dilate_mask(seg["old_hair"], 2)
    # (grey background, but never inside the eyes-nose-mouth area: a dark grey pupil or brow is close to the grey
    # background's colour, and the silver-haired mage's close-up had both filled as background, in boxes)
    core = seg["pic_hair"] | (seg["pic_background"] & ~inner & ~face.features_box(info))
    # Outside the eyes-nose-mouth area the painted face's own rim goes too (the shadow along its jaw and cheek, which
    # lands inside the model's wider cheek and shows as a line from the side).
    rim = face.dilate_mask(core, max(2, int(RIM_FRAC * view["image"].shape[0]))) & ~face.features_box(info)
    bad = (core | rim) & paint
    if bad.any():
        filled = face.inpaint(fitted, paint & ~bad)
        fitted = np.where(bad[..., None], filled, fitted)
    weight = face.screen_weight(paint)
    if tuple(facing) != tuple(FACING):
        # The body was painted from the pictures: the forehead, the fringe, the temples and the cheeks already carry the
        # picture itself, sharper and truer than a redrawn close-up. The painted face keeps to the eyes, nose and mouth,
        # fading out around them (the photo boy: a line at the temple and a soft band under the fringe otherwise).
        box = face.features_box(info).astype(np.float64)
        weight = weight * np.clip(face.blur(box, FEATURES_FEATHER * float(info["width_px"])), 0.0, 1.0)
    # where the paint will fade (the region's edge, and surface turning away from the camera) the tone is matched
    # locally, so the cheeks do not show a step where the painted face hands over to the old texture
    fade = weight * face.smoothstep(facing[0], min(1.0, facing[1] + 0.25), view["facing"])
    lm_fit = face.landmarks(fitted, info)
    keep = face.eye_boxes(region.shape, lm_fit["eyes"], float(info["width_px"])) if lm_fit else None
    toned = face.tone_match_local(fitted, view["image"], paint, fade, keep=keep)
    res = face.project_back(mesh, tex, cam, toned, weight, facing=facing, strength=strength, soften_m=SOFTEN_M)
    if tuple(facing) != tuple(FACING) and lm_fit:
        # Handing over early is for the cheeks and the jaw. Across the brows and eyes the painted face keeps its old
        # reach: under it lies the picture's own brow, a little higher, and where the paint thinned out there a second
        # brow showed above the painted one (the photo boy, both sides).
        wpx, rows = float(info["width_px"]), view["image"].shape[0]
        eye_y = float(np.mean([e[1] for e in lm_fit["eyes"]]))
        band = np.zeros(view["image"].shape[:2])
        band[int(max(0, eye_y - BROW_BAND[0] * wpx)): int(min(rows, eye_y + BROW_BAND[1] * wpx))] = 1.0
        band = np.clip(face.blur(band, 0.03 * wpx), 0.0, 1.0)
        more = face.project_back(mesh, res["texture"], cam, toned, weight * band, facing=FACING, strength=strength,
                                 soften_m=SOFTEN_M, cover=res["texel_weight"])
        tw = np.clip(res["texel_weight"] + more["texel_weight"], 0.0, 1.0)
        boxes = [bx for bx in (res.get("bbox"), more.get("bbox")) if bx]
        bbox = (min(b_[0] for b_ in boxes), min(b_[1] for b_ in boxes), max(b_[2] for b_ in boxes), max(b_[3] for b_ in boxes)) if boxes else None
        res = {**res, "texture": more["texture"], "texel_weight": tw, "changed": int((tw > 0.5).sum()), "bbox": bbox}
    spread, spread_n = face.spread_tone(mesh, tex, res["texture"], res["texel_weight"], info["skin"])
    uvm = imaging.load_uv_mesh(glb)
    coverage = imaging.rasterise_uv(uvm, T)["labels"] > 0
    new_tex = face.grow_into_gutter(spread, coverage, res["bbox"], imaging.dilation_px(T))
    # The cheeks, jaw sides and temples from the side pictures, only where the front painted little or nothing.
    side_record: dict[str, Any] = {}
    if sides and seg.get("found"):
        # the eyes are the front painting's alone: the side pictures never paint them, even where the front faded
        side_cover = res["texel_weight"]
        if keep is not None:
            eyes_tw = face.project_back(mesh, np.zeros_like(tex), cam, np.ones_like(view["image"]),
                                        face.dilate_mask(keep, int(0.02 * view["image"].shape[0])).astype(np.float64), facing=(0.0, 0.05))["texel_weight"]
            side_cover = np.maximum(side_cover, eyes_tw)
        new_tex, side_record = paint_sides(mesh, tex, new_tex, side_cover, head, info, seg["hair_lab"], sides, coverage,
                                           debug_dir=out_dir if DEBUG_SIDES else None)
    out = face.save_rgb(new_tex, out_dir / "base_color_face.png")
    after = face.render_view(mesh, new_tex, cam)
    old_mesh = face.load_mesh(before_glb)
    old_tex = face.read_rgb(before_tex)
    old_head = face.find_head(old_mesh["verts"])
    before = face.render_view(old_mesh, old_tex, head_cam(old_head, 1024))
    metrics = {
        "sharpness_before": face.sharpness(before["image"], region), "sharpness_after": face.sharpness(after["image"], region),
        "feature_match_before": face.feature_match(before["image"], toned, region),
        "feature_match_after": face.feature_match(after["image"], toned, region),
        "fit_match": match_fit,
        "alignment": {k: a.get(k) for k in ("how", "sx", "sy", "tx", "ty", "score", "residual_px")},
        "texels_painted": res["changed"], "hair_kept_px": int((region & seg["old_hair"]).sum()), "hair_filled_px": int(bad.sum()), "tone_spread_texels": spread_n,
        "face_texels_after": int(face.face_texels(mesh, cam, region, T)["face_width_texels"]),
        "eyes_moved": eyes_moved(view["image"], after["image"], info),
        "eyes": eye_measures(toned, after["image"], weight, seg["old_hair"], lm_fit, info,
                             (view["raster"]["z"], view["mask"])),
        **eye_colour_measure(painted_fitted, after["image"], fitted, info, paint),
        "sides": side_record, "sides_used": [k for k, v in side_record.items() if v.get("used")],
    }
    sheet = before_after_sheet(crop, (old_mesh, old_tex, old_head), (mesh, new_tex, head), out_dir / "face_before_after.png")
    return {"ok": True, "base_color": out, "sheet": sheet, "metrics": metrics}


def fit_picture(src: np.ndarray, render: np.ndarray, info: dict[str, Any], inner: np.ndarray) -> dict[str, Any]:
    """The scale and shift that lay the picture on the model's face (see fit_and_paint), with "how" and "score".

    1. The eye-and-mouth fit on the picture as framed (its eyes and mouth searched where the model has them).
    2. When that fails: the same fit on the picture first lined up by the whole face. The painted head is framed from
       the figure's height, so its eyes can sit a tenth of the face's width above the model's (the second knight's sat
       0.45 of the face's width above the nose, the search band starts at 0.36): then the darkest spots found are not
       her eyes, and the fit falls back to the whole face, which places the eyes less exactly.
    3. Else the whole-face fit.
    A fit is kept when its eyes land within LANDMARK_RESIDUAL_MAX of the face's width of the model's and the broad
    features agree (ALIGN_MIN)."""
    w = float(info["width_px"])
    lm_src, lm_dst = face.landmark_pair(src, render, info)

    def scored(a: dict[str, Any], how: str) -> dict[str, Any]:
        a["how"] = how
        a["score"] = face.feature_match(face.apply_alignment(src, a), render, inner, 6.0, 24.0)
        return a

    def whole_places_eyes_better(a: dict[str, Any]) -> bool:
        m = face.landmarks(face.apply_alignment(src, whole), info)
        if not m or m.get("how") != "mediapipe" or not lm_dst or whole["score"] < ALIGN_MIN:
            return False
        err = float(np.abs(np.array(m["eyes"]) - np.array(lm_dst["eyes"])).max())
        a["whole_eye_error_px"] = round(err, 1)
        return err <= float(a["residual_px"]) + EYE_FIT_MARGIN * w

    def good(a: dict[str, Any] | None) -> bool:
        return a is not None and a["residual_px"] <= LANDMARK_RESIDUAL_MAX * w and a["score"] >= ALIGN_MIN

    a = face.fit_landmarks(lm_src, lm_dst, info["nose"]) if lm_src and lm_dst else None
    whole = scored(face.align(src, render, inner), "whole face")
    if a is not None:
        a = scored(a, "eyes and mouth")
        # With MediaPipe's points the eye-and-mouth fit is found far more often. The whole-face fit stays the default
        # when it already puts the painted eyes on the model's within EYE_FIT_MARGIN of the best (the red-haired knight
        # and the mage: 14 and 19 px, where an eye under the fringe came out worse with eyes and mouth); the boy and the
        # T-pose man, where the whole face missed the eyes by 49 to 72 px against 17, take the eye-and-mouth fit.
        if good(a) and (lm_src.get("how") != "mediapipe" or not whole_places_eyes_better(a)):
            return a
    if lm_dst and whole["score"] >= ALIGN_MIN:
        lm_pre, lm_dst2 = face.landmark_pair(face.apply_alignment(src, whole), render, info)
        lm_dst = lm_dst2 or lm_dst
        then = face.fit_landmarks(lm_pre, lm_dst, info["nose"]) if lm_pre else None
        if then is not None:
            b = {**face.compose_alignment(whole, then), "residual_px": then["residual_px"]}
            b = scored(b, "eyes and mouth")
            if good(b) and ((lm_pre or {}).get("how") != "mediapipe" or not whole_places_eyes_better(b)):
                b["after_whole_face"] = True
                return b
    return whole


def paint_sides(mesh: dict[str, Any], old_tex: np.ndarray, tex: np.ndarray, cover: np.ndarray, head: Any, info: dict[str, Any],
                hair_lab: Any, sides: dict[str, Path], coverage: np.ndarray, debug_dir: Path | None = None) -> tuple[np.ndarray, dict[str, Any]]:
    """Second projections from the side pictures. Each picture's head is cut out with the model's framing, lined up
    by its outline with a render of the model from that side (not used under SIDE_IOU_MIN, with the reason
    recorded), cleaned of hair and background over the model's bare skin, given the colour and tone the front
    painting already put on the surface where both see it, and painted only on the share of each skin texel the
    front (and earlier sides) left unpainted, weighted by how squarely it faces this camera. The quarter view goes
    first, then the full sides. Returns (texture, record per side)."""
    record: dict[str, Any] = {}
    T = tex.shape[0]
    cover = np.clip(cover.copy(), 0.0, 1.0)
    for name, yaws in SIDE_VIEWS:
        path = sides.get(name)
        if path is None or not Path(path).is_file():
            continue
        rgba = np.asarray(Image.open(path).convert("RGBA"), dtype=np.float64) / 255.0
        best = None
        first = (record.get("quarter") or {}).get("yaw") if name == "quarter2" else None
        if first is not None:
            yaws = (-float(first),)  # the side the first three-quarter view was not fitted to
        for yaw in yaws:
            cam = head_cam(head, 1024, yaw)
            crop = face.painted_head(rgba, head, cam)
            if crop is None:
                continue
            view_o = face.render_view(mesh, old_tex, cam)
            pos = face.interpolate(mesh["verts"], mesh["faces"], view_o["raster"])
            rows_head = np.nonzero((view_o["mask"] & (pos[..., 1] >= head.top - 0.15 * head.height)).any(axis=1))[0]
            if len(rows_head) < 20:
                continue
            rows = (int(rows_head[0]), int(rows_head[-1]) + 1)
            fit = face.silhouette_fit(crop["alpha"], view_o["mask"], rows)
            if best is None or fit["iou"] > best[0]["iou"]:
                best = (fit, yaw, cam, crop, view_o, rows)
        if best is None:
            record[name] = {"used": False, "reason": "no head in the picture"}
            continue
        fit, yaw, cam, crop, view_o, rows = best
        rec = {"yaw": yaw, "outline_match": fit["iou"], "scale": [fit["sx"], fit["sy"]], "shift_px": [fit["tx"], fit["ty"]]}
        if fit["iou"] < SIDE_IOU_MIN:
            record[name] = {**rec, "used": False, "reason": "the outline does not match the model's"}
            continue
        pic = face.apply_alignment(crop["image"], fit)
        alpha = face.apply_alignment(np.repeat(crop["alpha"][..., None], 3, axis=2), fit)[..., 0]
        sk = face.side_skin(view_o["image"], view_o["mask"], info["skin"], hair_lab, rows[0] + max(4, (rows[1] - rows[0]) // 5))
        skin = sk["skin"] & face.erode(view_o["mask"], 2)
        rows_m = np.zeros_like(skin)
        rows_m[rows[0]:rows[1] + (rows[1] - rows[0]) // 4] = True  # the head and the top of the neck
        skin &= rows_m
        if skin.sum() < 500:
            record[name] = {**rec, "used": False, "reason": "no bare skin seen from this side"}
            continue
        # the picture: hair or background over the model's bare skin is filled from the picture's skin around it
        lab_p = face.to_lab(np.clip(pic, 0, 1))
        kk = np.array([0.5, 1.0, 1.0])
        on = skin & (alpha > 0.5)
        skin_p = np.median(lab_p[on], axis=0) if on.sum() > 50 else np.asarray(info["skin"])
        hair_src = sk["hair"] & (alpha > 0.5)
        hair_p = np.median(lab_p[hair_src], axis=0) if hair_src.sum() > 50 else np.asarray(hair_lab)
        pic_hair = np.linalg.norm((lab_p - hair_p) * kk, axis=-1) < np.linalg.norm((lab_p - skin_p) * kk, axis=-1) - 6.0
        bad = skin & (pic_hair | (alpha < 0.5))
        good = skin & ~bad
        if good.sum() < 300:
            record[name] = {**rec, "used": False, "reason": "the picture shows little bare skin where the model has it"}
            continue
        if bad.any():
            pic = np.where(bad[..., None], face.inpaint(pic, good), pic)
        # colour and tone: the front painting's, where it covers the surface seen from this side
        view_now = face.render_view(mesh, tex, cam)
        cov_img = face.render_view(mesh, np.repeat(cover[..., None], 3, axis=2), cam)["image"][..., 0]
        trusted = skin & (cov_img > 0.6)
        toned = face.tone_to_reference(pic, view_now["image"], trusted)
        weight = face.screen_weight(skin)
        if debug_dir is not None:
            g = lambda m: np.repeat(m[..., None].astype(np.float64), 3, axis=2)  # noqa: E731
            face.save_rgb(np.concatenate([np.concatenate([pic, toned, view_now["image"]], axis=1),
                                          np.concatenate([g(weight), g(cov_img), g(skin) * 0.5 + g(trusted) * 0.5], axis=1)], axis=0)[::2, ::2],
                          Path(debug_dir) / f"side_{name}.png")
        res = face.project_back(mesh, tex, cam, toned, weight, facing=SIDE_FACING, soften_m=SOFTEN_M, cover=cover)
        tex = face.grow_into_gutter(res["texture"], coverage, res["bbox"], imaging.dilation_px(T))
        cover = np.clip(cover + res["texel_weight"], 0.0, 1.0)
        used = res["changed"] > 0
        record[name] = {**rec, "used": used, "texels_painted": res["changed"], "hair_filled_px": int(bad.sum()),
                        "trusted_px": int(trusted.sum())}
        if not used:
            record[name]["reason"] = "the front and the earlier views already cover the skin this side sees"
    return tex, record


def eye_colour_measure(painted: np.ndarray, after: np.ndarray, fitted: np.ndarray, info: dict[str, Any],
                       paint: np.ndarray) -> dict[str, Any]:
    """The eyes' colour in the painting (fitted to the model) against the painted model's front render, at the
    picture's own eye points: {eye_colour_painting, eye_colour_model, eye_colour_shift (the larger of the two eyes,
    Lab a-b distance), eyes_measured}. An eye whose box is mostly not painted (covered by hair on the model) is left
    out; with no eye measured the shift is None."""
    lm = face.landmarks(fitted, info)
    if not lm:
        return {"eye_colour_shift": None, "eyes_measured": 0}
    w = float(info["width_px"])
    hx, hy = max(2, int(0.05 * w)), max(2, int(0.025 * w))
    eyes = [(x, y) for x, y in lm["eyes"] if paint[max(0, int(y) - hy): int(y) + hy + 1, max(0, int(x) - hx): int(x) + hx + 1].mean() >= 0.6]
    if not eyes:
        return {"eye_colour_shift": None, "eyes_measured": 0}
    a = face.eye_colour(painted, eyes, w)
    b = face.eye_colour(after, eyes, w)
    return {"eye_colour_painting": a, "eye_colour_model": b, "eye_colour_shift": face.colour_shift(a, b), "eyes_measured": len(eyes)}


def depth_step(z: np.ndarray | None, mask: np.ndarray | None, box: np.ndarray) -> float | None:
    """The biggest jump in depth between neighbouring render pixels inside box, in metres (None without a depth picture)."""
    if z is None or mask is None:
        return None
    zz = np.where(mask, z, np.nan)
    out = 0.0
    for d, b in ((np.abs(np.diff(zz, axis=1)), box[:, :-1]), (np.abs(np.diff(zz, axis=0)), box[:-1, :])):
        v = d[b]
        if np.isfinite(v).any():
            out = max(out, float(np.nanmax(v)))
    return round(out, 5)


def eye_measures(picture: np.ndarray, after: np.ndarray, weight: np.ndarray, model_hair: np.ndarray,
                 lm: dict[str, Any] | None, info: dict[str, Any], depth: tuple[np.ndarray, np.ndarray] | None = None) -> list[dict[str, Any]] | None:
    """Each eye of the picture (fitted to the model) against the painted model's front render, in a box around the eye
    and its lids (face.eye_boxes): {side ("right" or "left": the character's own, so the eye on the viewer's left is
    her right), painted (the box's mean paint weight, 0..1), hair (the share of the box that is the model's hair),
    match (face.feature_match of the render and the picture in the box: 1 the same eye, 0 none of it), sharp (the
    render's sharpness in the box over the picture's)}. None when the picture's eyes were not found."""
    if not lm:
        return None
    w = float(info["width_px"])
    shape = weight.shape
    hair = face.dilate_mask(model_hair, 2)
    out = []
    for i, (x, y) in enumerate(lm["eyes"]):
        box = face.eye_boxes(shape, [(x, y)], w)
        wide = face.eye_boxes(shape, [(x, y)], w, grow=2.4)  # feature_match drops a rim of the box
        sp = face.sharpness(picture, box)
        out.append({"side": "right" if i == 0 else "left", "at": [round(x, 1), round(y, 1)],
                    "painted": round(float(weight[box].mean()), 3), "hair": round(float(hair[box].mean()), 3),
                    "match": face.feature_match(after, picture, wide), "step": depth_step(*(depth or (None, None)), box),
                    "sharp": round(face.sharpness(after, box) / sp, 3) if sp > 0 else None})
    return out


def eyes_moved(before: np.ndarray, after: np.ndarray, info: dict[str, Any]) -> float | None:
    """How far the darkest spot of each eye moved between the old and the new front render, as a share of the face's
    width (the larger of the two eyes). A registration measure: the old texture's eye smudges sit where the 3D builder
    put the eyes, so a large move means the painted eyes are off the model's eye places. None when not measurable."""
    eb = face.eye_points(before, info)
    ea = face.eye_points(after, info)
    if not eb or not ea:
        return None
    d = max(float(np.hypot(a[0] - b[0], a[1] - b[1])) for a, b in zip(ea, eb))
    return round(d / max(float(info["width_px"]), 1.0), 4)


def before_after_sheet(crop: Path, before: tuple, after: tuple, dest: Path, cell: int = 320) -> Path:
    """Painted head | 3D face before | 3D face after, from the front, both three-quarter sides and both full sides;
    then close-ups of the new face: both eyes and the brow from the front, the cheeks from the three-quarter sides
    and from the full sides (where the front painting hands over to the side pictures)."""
    views = ((0, "From the front"), (35, "From the left"), (-35, "From the right"), (90, "Left side"), (-90, "Right side"))
    rows = []
    for yaw, label in views:
        imgs = [face.render_view(m, t, head_cam(h, 768, yaw))["image"] for m, t, h in (before, after)]
        rows.append((label, imgs))
    pad = 24
    n_close = 2
    sheet = Image.new("RGB", (cell * 3, pad + cell * (len(rows) + n_close) + pad * n_close), (32, 32, 32))
    d = ImageDraw.Draw(sheet)
    for i, label in enumerate(("Painted picture", "3D face before", "3D face after")):
        d.text((i * cell + 8, 6), label, fill=(230, 230, 230))
    painted = Image.open(crop).convert("RGB").resize((cell, cell), Image.LANCZOS)
    for r, (label, imgs) in enumerate(rows):
        y = pad + r * cell
        if r == 0:
            sheet.paste(painted, (0, y))
        else:
            d.text((8, y + 8), label, fill=(230, 230, 230))
        for c, img in enumerate(imgs):
            sheet.paste(Image.fromarray(face.to_uint8(img)).resize((cell, cell), Image.LANCZOS), ((c + 1) * cell, y))
    # close-ups of the new face (fractions of the 768 px renders: the framing of head_camera)
    closes = (
        ("Close-ups after: both eyes, left cheek, right cheek",
         ((0, (0.22, 0.3, 0.78, 0.62)), (1, (0.2, 0.35, 0.6, 0.85)), (2, (0.4, 0.35, 0.8, 0.85)))),
        ("Close-ups after: left side cheek, right side cheek",
         ((3, (0.0, 0.3, 0.5, 0.8)), (4, (0.5, 0.3, 1.0, 0.8)))),
    )
    y = pad + len(rows) * cell
    for title, boxes in closes:
        d.text((8, y + 4), title, fill=(230, 230, 230))
        for c, (row, (fx0, fy0, fx1, fy1)) in enumerate(boxes):
            img = Image.fromarray(face.to_uint8(rows[row][1][1]))
            W = img.size[0]
            part = img.crop((int(fx0 * W), int(fy0 * W), int(fx1 * W), int(fy1 * W)))
            part.thumbnail((cell, cell), Image.LANCZOS)
            sheet.paste(part, (c * cell + (cell - part.size[0]) // 2, y + pad))
        y += cell + pad
    sheet.save(dest)
    return dest


# --- the pass ------------------------------------------------------------------------------------------------


def image_instance(sc: StageContext) -> str | None:
    """The card that draws pictures now (the side-views step's, or the one that took over while it is off)."""
    from .. import jobs, prefs

    try:
        p = prefs.load(sc.settings)
        inst = jobs.route("turnaround", sc.asset.category, sc.settings, prefs.disabled_instances(sc.settings),
                          p.get("allowed_kinds"), p.get("default_instance"))
    except Exception:  # noqa: BLE001
        inst = sc.settings.stage_instances.get("turnaround")
    if not inst or inst == jobs.HOST or inst not in sc.ctx.clients:
        return None
    return inst


async def redraw(sc: StageContext, instance: str, crop: Path, seed: int, dest: Path, prompt: str = REDRAW_PROMPT,
                 key: str = "face_redraw", message: str = "Sharpening the painted face", reference: Path | None = None,
                 margin: float = 1.0) -> Path:
    """Qwen-Image-Edit redraw of the crop (the sharpening redraw, or the close-up with CLOSEUP_PROMPT). Stop works: a
    cancelled job takes its own prompt off the card (removes it from the queue, or interrupts it when it is the one
    running), never someone else's."""
    client: ComfyClient = sc.client(instance)
    if margin < 1.0:
        crop = await asyncio.to_thread(add_margin, crop, margin, Path(dest).with_name(Path(dest).stem + "_input.png"))
    name = await client.stage_input(crop, f"vgac_{sc.version.id[:8]}_{key}_head.png")
    if reference is not None:
        ref = await client.stage_input(reference, f"vgac_{sc.version.id[:8]}_{key}_guide.png")
        graph = compose_graph([name, ref], prompt, seed, sc.prefix(key), 1024, 1024)
    else:
        graph = edit_graph(name, prompt, seed, sc.prefix(key), 1024, 1024)
    prompt_id = await client.queue_prompt(graph)

    def on_poll(elapsed: float) -> None:
        sc.ctx.progress(None, f"{message} on {instance}: {int(elapsed)} s")
        sc.ctx.check_cancelled()

    try:
        entry = await client.wait(prompt_id, poll_seconds=1.0, timeout=sc.settings.job_timeout_seconds, on_poll=on_poll)
    except (JobCancelled, asyncio.CancelledError):
        await _withdraw(client, prompt_id)
        raise
    imgs = ComfyClient.images_of(entry, "save")
    if not imgs:
        raise ComfyError("the redraw produced no picture")
    if margin >= 1.0:
        return await client.download_output(imgs[0], dest)
    raw = await client.download_output(imgs[0], Path(dest).with_name(Path(dest).stem + "_drawn.png"))
    return await asyncio.to_thread(remove_margin, raw, margin, Path(dest))


def picked_seed(sc: StageContext, params: dict[str, Any], key: str) -> int:
    """The version's seed for one of the pass's picture jobs (face_seed, face_closeup_seed): the one given (a Resume
    carries the stopped run's, jobs.RESUME_SEEDS), else a new one, written on the version at once so a Stop and Resume
    redraws the same picture."""
    seed = params.get(key)
    if seed is None and key == "face_closeup_seed_3" and params.get("face_closeup_seed_2") is not None:
        # the third draw follows the second's seed, which a Resume carries, so it is the same draw again
        seed = int(params["face_closeup_seed_2"]) + 1
        sc.set_params(**{key: seed})
    elif seed is None and key == "face_closeup_seed_4" and params.get("face_closeup_seed_2") is not None:
        seed = int(params["face_closeup_seed_2"]) + 2  # the same way: a Resume carries the second seed
        sc.set_params(**{key: seed})
    if seed is None:
        seed = random_seed()
        sc.set_params(**{key: seed})
    return int(seed)

def pin_pass_seed(sc: StageContext, params: dict[str, Any], face_detail_seed: int | None) -> None:
    """One master seed (face_detail_seed) pins the whole pass so a close-up and its sharpen are deterministic: the
    close-up base is S, a second close-up try is S + 1, the sharpen is S. Each key only when not already given (an API
    per-try seed or a Resume seed wins), and written on the version at once so a Stop and Resume redraw the same
    pictures, like picked_seed."""
    if face_detail_seed is None:
        return
    master = int(face_detail_seed)
    pinned: dict[str, int] = {}
    for key, value in (("face_closeup_seed", master), ("face_closeup_seed_2", master + 1), ("face_closeup_seed_3", master + 2),
                       ("face_closeup_seed_4", master + 3), ("face_seed", master)):
        if params.get(key) is None:
            params[key] = value
            pinned[key] = value
    if pinned:
        sc.set_params(**pinned)



async def _withdraw(client: ComfyClient, prompt_id: str) -> None:
    try:
        q = await client.queue_state()
        running = [item[1] for item in q.get("queue_running", []) if len(item) > 1]
        if prompt_id in running:
            await client.interrupt()
        else:
            await client._client().post("/queue", json={"delete": [prompt_id]}, timeout=5.0)
    except Exception:  # noqa: BLE001  (the card may be gone; nothing more to do)
        pass


async def run(sc: StageContext, glb: Path, maps: dict[str, Path], rec: dict[str, Any], params: dict[str, Any],
              hair_plan: dict[str, Any] | None = None, face_detail_seed: int | None = None,
              after_relayout: Any = None, body_painted: bool = False, head_yaw: float = 0.0) -> dict[str, Any]:
    """_run with the image card's MediaPipe face landmarker finding the eyes and mouth (vgac/facemarks.py). head_yaw: the
    camera yaw the model's face is seen straight on from (HEAD_YAW), from the clean-up's facing decision."""
    yaw_token = HEAD_YAW.set(float(head_yaw))
    token = None
    try:
        inst = image_instance(sc)
        if inst is not None and params.get("face_marks") is not False:
            client = sc.client(inst)
            token = facemarks.use(getattr(client, "base_url", None), getattr(client, "input_dir", None))
    except Exception:  # noqa: BLE001  (no card: the darkest-spot finder)
        token = None
    before = dict(facemarks.stats)
    try:
        out = await _run(sc, glb, maps, rec, params, hair_plan, face_detail_seed, after_relayout, body_painted)
    finally:
        facemarks.done(token)
        HEAD_YAW.reset(yaw_token)
    fd = (out.get("metrics") or {}).get("face_detail")
    if isinstance(fd, dict):
        fd["head_yaw_deg"] = round(float(head_yaw), 1)
        fd["landmarks"] = {"mediapipe": token is not None, **{k: facemarks.stats[k] - before.get(k, 0) for k in facemarks.stats}}
    return out


async def _run(sc: StageContext, glb: Path, maps: dict[str, Path], rec: dict[str, Any], params: dict[str, Any],
               hair_plan: dict[str, Any] | None = None, face_detail_seed: int | None = None,
               after_relayout: Any = None, body_painted: bool = False) -> dict[str, Any]:
    """The pass on the prepared model `glb` (changed in place) with the current maps. Returns {files: {key: path},
    metrics: {"face_detail": {...}}, relayout: bool, base_color_size: int, layout: the relayout report or None}. Never
    raises for a reason that should not fail the clean-up step (those are recorded as skipped); Stop still stops it.

    hair_plan: {points (Path), gain} from the hair pass (stages/hair_detail.py find): the one relayout gives the hair
    more texels too (blender/faceuv.py --hair-points), and runs for the hair even when the face needs none.

    after_relayout: async (glb, maps) -> maps, called once the texture has been packed again and every map baked onto
    the new layout, before the face is painted: prep paints the picture onto the body again there (vgac/srcproject.py
    run_again), because that bake softens it. body_painted: prep painted the model from its pictures, so the painted
    face fades out earlier on the cheeks (FACING_OVER_PICTURES)."""
    t0 = time.time()
    out_dir = sc.dir / "face"
    cutout = sc.lineage_file("cutout", ("concept",))
    reason = skip_reason(sc.asset.category, rec, params, cutout is not None and Path(cutout).is_file())
    strength = strength_of(params)
    if reason:
        return _skipped(reason, t0)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = maps.get("base_color")
    if base is None:
        return _skipped("no_face", t0)
    sc.progress(0.75, "Finding the face")
    found = await asyncio.to_thread(inspect, glb, Path(base), Path(cutout), out_dir)
    if not found.get("found"):
        return _skipped(found.get("reason") or "no_face", t0, {k: v for k, v in found.items() if k not in ("found", "reason")})
    crop = out_dir / "painted_head.png"
    atlas = int(found["texture_size"])
    budget_tex = int((sc.budget or {}).get("texture") or atlas)
    choice = density_choice(params)
    crop_px = source_face_px(found["face_width_px"], found["painted_head_px"])
    record: dict[str, Any] = {"ran": False, "face_texels_before": found["face_width_texels"], "texture_size": atlas,
                              "painted_head_px": found["painted_head_px"], "painting_face_px": crop_px, "strength": strength}

    master_seed = face_detail_seed if face_detail_seed is not None else params.get("face_detail_seed")
    pin_pass_seed(sc, params, master_seed)

    instance = image_instance(sc)
    reachable = False
    if instance is not None:
        reachable = bool((await sc.client(instance).vram_summary()).get("reachable"))
    own = own_closeup_file(sc, params)
    if not reachable and own is None:
        return _skipped("no_card", t0, record)

    # A. The close-up. With a picture of the real face (your own close-up, or step 0.5's face picture) the close-up is
    # drawn from the painted crop guided by it (guide_picture). Otherwise, or when no guided one passes the check: your
    # own close-up lined up directly, else a close-up drawn from the crop alone when the painting has fewer pixels
    # across the face than the texture will hold.
    wanted = plan_density(choice, budget_tex, atlas, None)["target"]
    picture, colour_source, method, source = crop, None, METHOD_UNSHARPENED, "painting"
    look_style = getattr(sc, "style", None) or {}
    look = look_style.get("id")
    photo = look_style.get("delight_default") == "remove" or getattr(sc.asset, "style_preset", None) == "realistic_pbr"
    guide = guide_picture(sc, params, own) if reachable else None
    closeup: dict[str, Any] = {"wanted": guide is not None or own is not None or closeup_wanted(params, crop_px, wanted),
                               "needed_texels": wanted}

    def use_closeup(guard: dict[str, Any], fallback: Path) -> Path:
        # the close-up with any feature that sat a little off moved back onto the painting's place
        return Path(guard["registered"]) if guard.get("registered") else fallback

    async def draw_tries(plan: list[tuple[str, str]], reference: Path | None, stem: str, message: str,
                         spare: tuple[str, str] | None = None) -> list[dict[str, Any]]:
        """Draw the close-up (up to len(plan) times, each with its own seed) and check each. With a second card lent by the
        queue (item 20b, the same lending step 2 uses) two are drawn at once, one on each card, and `spare` is one more
        draw for a second round; a draw that follows a refusal always waits for the round before it. One card, or no card
        free: one after another, as before."""
        from ..jobs import is_engine_lost

        tries: list[dict[str, Any]] = []
        lease, why = await borrow_card(sc, instance)
        if lease is not None and spare is not None:
            plan = [*plan, spare]
        closeup["cards"] = {"two": lease is not None,
                            "note": f"the {lease.gpu} drew a second close-up at the same time" if lease is not None else why}

        async def one(n: int, card: str, parallel: bool) -> dict[str, Any]:
            key, prompt = plan[n]
            seed_c = picked_seed(sc, params, key)
            if n and not parallel:
                sc.progress(0.77, retry_message(n, len(plan), message))
            name = f"{stem}.png" if n == 0 else f"{stem}_{n + 1}.png"
            job_key = stem.replace("painted_head_", "face_") + ("" if n == 0 else f"_{n + 1}")
            k = closeup_margin(found["face_width_px"], reference is not None, photo)
            closeup.setdefault("margins", {})["guided" if reference is not None else "plain"] = k
            cu = await redraw(sc, card, crop, seed_c, out_dir / name, prompt, job_key, retry_message(n, len(plan), message),
                              reference=reference, margin=k)
            show_closeup(sc, out_dir / name, *live_caption(n + 1, len(plan), None, False))
            guard = await asyncio.to_thread(closeup_guard, glb, Path(base), crop, cu, found["painted_head_px"], photo,
                                            reference is not None)
            again = not parallel and not guard["ok"] and n + 1 < len(plan) and (reference is not None or retry_wanted(guard))
            show_closeup(sc, out_dir / name, *live_caption(n + 1, len(plan), guard, again))
            kind = "guided" if reference is not None else "photo" if prompt == CLOSEUP_PHOTO_PROMPT else "plain" if prompt == CLOSEUP_PROMPT else "look"
            return {**guard, "file": name, "prompt": kind, "seed": seed_c}

        async def two_at_once(a: int, b: int) -> list[dict[str, Any]]:
            """Draw a on the job's card and b on the lent one. The lent card failing or taken back: b is drawn again on the
            job's card once a is done. Either failing otherwise (or a stop) stops the other."""
            ta = asyncio.ensure_future(one(a, instance, True))
            tb = asyncio.ensure_future(one(b, lease.instance, True))
            try:
                await asyncio.wait({ta, tb}, return_when=asyncio.FIRST_EXCEPTION)
                if tb.done() and not tb.cancelled() and tb.exception() is not None:
                    e = tb.exception()
                    if isinstance(e, JobCancelled) or not (lease.revoked or is_engine_lost(e)):
                        raise e
                    gone = lease.revoked_reason or f"the graphics engine on {lease.gpu} stopped answering"
                    closeup["cards"]["note"] = f"{gone}, so the second close-up was drawn on the job's card"
                    await lease.end()
                    ra = await ta
                    return [ra, await one(b, instance, False)]
                return [await ta, await tb]
            except BaseException:
                for t in (ta, tb):
                    t.cancel()
                raise

        n = 0
        try:
            while n < len(plan):
                together = lease is not None and not lease.ended and not lease.revoked and n + 1 < len(plan)
                if together:
                    sc.progress(0.77, message if n == 0 else "The close-ups were not good enough; drawing two more at the same time")
                    got = await two_at_once(n, n + 1)
                else:
                    if lease is not None and not lease.ended:
                        await lease.end()  # the second card is handed back as soon as it is no longer drawing
                    got = [await one(n, instance, False)]
                tries.extend(got)
                n += len(got)
                if any(t["ok"] for t in got):
                    break
                if n < len(plan) and not (reference is not None or any(retry_wanted(t) for t in got)):
                    break
        except BaseException:
            if lease is not None:
                await lease.end(interrupt=True)
            raise
        if lease is not None:
            await lease.end()
        sc.use_model("qwen_edit")
        sc.use_model("qwen_lightning_lora")
        return tries

    def record_tries(tries: list[dict[str, Any]]) -> None:
        if tries:
            keys = ("prompt", "seed", "ok", "reason", "likeness", "feature_scores", "feature_score_min", "feature_move",
                    "hair_shift", "hair_tone_shift", "skin_texture")
            closeup["tries"] = [{k: t.get(k) for k in keys} for t in tries]
            closeup["draws"] = len(tries)

    t1 = time.time()
    if guide is not None:
        sc.progress(0.77, "Drawing a close-up of the face, guided by your picture of it")
        closeup.update(guided=True, guide=guide[1])
        try:
            shutil.copy2(guide[0], out_dir / GUIDE_FILE)  # the version keeps the picture it was guided by
            tries = await draw_tries(guided_tries(photo), out_dir / GUIDE_FILE, "painted_head_closeup_guided",
                                     "Drawing a close-up of the face, guided by your picture of it")
            record_tries(tries)
            closeup["guided_tries"] = closeup.get("tries")  # kept when a plain close-up is drawn after them
            ok = pick_closeup(tries) if any(t["ok"] for t in tries) else None
            if ok is not None:
                closeup.update({k: v for k, v in ok.items() if k != "ok"}, used=True)
                merged = await asyncio.to_thread(closeup_source, crop, use_closeup(ok, out_dir / ok["file"]), found["painted_head_px"],
                                                 out_dir / "painted_head_closeup_merged.png")
                picture, colour_source, method, source = merged, merged, METHOD_GUIDED, "closeup"
            else:
                closeup.update({k: v for k, v in tries[-1].items() if k != "ok"}, used=False)
        except (JobCancelled, asyncio.CancelledError):
            raise
        except Exception as e:  # noqa: BLE001  (a failed guided close-up: the paths below)
            closeup.update(used=False, reason="error", error=str(e)[:300])
    if source == "painting" and own is not None:
        sc.progress(0.77, "Lining up your close-up of the face")
        closeup["own"] = True
        try:
            own_copy = out_dir / OWN_FILE
            shutil.copy2(own, own_copy)  # the version keeps the picture it was painted from
            framed_path = out_dir / "own_face_closeup_framed.png"
            framed = await asyncio.to_thread(frame_own, glb, Path(base), crop, own_copy, framed_path)
            closeup["framing"] = {k: framed.get(k) for k in ("score", "coarse_score", "alignment")}
            if not framed["ok"]:
                closeup.update(used=False, reason=framed.get("reason") or "not_found")
            else:
                guard = await asyncio.to_thread(closeup_guard, glb, Path(base), crop, framed_path, found["painted_head_px"], photo)
                closeup.update({k: v for k, v in guard.items() if k not in ("ok", "reason")})
                hard, notes = own_verdict(guard, photo)
                closeup["used"] = hard is None
                closeup["reason"] = hard
                closeup["notes"] = [CLOSEUP_REASONS[n] for n in notes]
                if hard is None:
                    merged = await asyncio.to_thread(closeup_source, crop, use_closeup(guard, framed_path), found["painted_head_px"],
                                                     out_dir / "painted_head_closeup_merged.png")
                    picture, colour_source, method, source = merged, merged, METHOD_OWN, "closeup"
        except (JobCancelled, asyncio.CancelledError):
            raise
        except Exception as e:  # noqa: BLE001  (a picture that cannot be used: today's path)
            closeup.update(used=False, reason="own_error", error=str(e)[:300])
    elif source == "painting" and reachable and (closeup_wanted(params, crop_px, wanted) if guide is not None else closeup["wanted"]):
        # also after guided close-ups that did not pass (step 0.5's face with another expression): the plain close-up
        sc.progress(0.77, "Drawing a close-up of the painted face")
        try:
            tries = await draw_tries(closeup_tries(look, photo), None, "painted_head_closeup", "Drawing a close-up of the painted face",
                                     spare=closeup_spare(look, photo))
            record_tries(tries)
            guard = pick_closeup(tries)
            closeup.update({k: v for k, v in guard.items() if k != "ok"})
            closeup["used"] = bool(guard["ok"])
            if guard["ok"]:
                merged = await asyncio.to_thread(closeup_source, crop, use_closeup(guard, out_dir / guard["file"]), found["painted_head_px"],
                                                 out_dir / "painted_head_closeup_merged.png")
                picture, colour_source, method, source = merged, merged, METHOD_CLOSEUP, "closeup"
        except (JobCancelled, asyncio.CancelledError):
            raise
        except Exception as e:  # noqa: BLE001  (a failed close-up: today's path)
            closeup.update(used=False, reason="error", error=str(e)[:300])
    if closeup["wanted"]:
        closeup["seconds"] = round(time.time() - t1, 1)
    if not closeup["wanted"] and closeup_on(params):
        closeup["reason_skipped"] = "the painting is large enough" if crop_px >= wanted else None

    async def sharpen() -> tuple[Path, str, float]:
        """Today's path: the crop sharpened (colour stays the painting's)."""
        seed = picked_seed(sc, params, "face_seed")
        sc.progress(0.78, "Sharpening the painted face")
        t1 = time.time()
        pic, meth = crop, METHOD_UNSHARPENED
        try:
            sharp = await redraw(sc, instance, crop, seed, out_dir / "painted_head_sharp.png")
            kept = await asyncio.to_thread(redraw_kept, sharp, crop)
            record["redraw_kept"] = kept
            if kept >= REDRAW_KEEP_MIN:
                pic, meth = sharp, METHOD
            sc.use_model("qwen_edit")
            sc.use_model("qwen_lightning_lora")
        except (JobCancelled, asyncio.CancelledError):
            raise
        except Exception as e:  # noqa: BLE001  (a failed redraw: the crop still carries the painted face)
            record["redraw_error"] = str(e)[:300]
        return pic, meth, round(time.time() - t1, 1)

    redraw_seconds = 0.0
    if source == "painting":
        picture, method, redraw_seconds = await sharpen()

    # B. How many texels the face gets, and the layout to give them.
    src_px = closeup.get("source_face_px") if source == "closeup" else crop_px
    plan = plan_density(choice, budget_tex, atlas, src_px)
    scale = plan["ship"] / atlas  # the face's texels across at the shipped size, per texel at the atlas size
    gain = face.gain_needed(found["face_width_texels"] * scale, plan["target"], HIGH_GAIN_MOST if plan["level"] == "high" else 3.0)
    work_glb, work_maps, relayout = Path(glb), dict(maps), None
    layout_report: dict[str, Any] | None = None
    hair_wants = bool(hair_plan) and float(hair_plan.get("gain") or 1.0) > 1.0
    if gain >= GAIN_MIN or hair_wants:
        sc.progress(0.82, "Giving the face more of the texture" if not hair_wants else "Giving the face and the hair more of the texture")
        (out_dir / "face_points.json").write_text(json.dumps(found["face_triangles"]), encoding="utf-8")
        (out_dir / "face_maps.json").write_text(json.dumps({k: str(p) for k, p in maps.items()}), encoding="utf-8")
        new_glb = out_dir / "face.glb"
        hair_args: list[Any] = ["--hair-points", hair_plan["points"], "--hair-gain", hair_plan["gain"]] if hair_wants else []
        await sc.blender("faceuv.py", [glb, new_glb, "--points", out_dir / "face_points.json", "--maps", out_dir / "face_maps.json",
                                       "--gain", round(gain, 3) if gain >= GAIN_MIN else 1.0, "--tex", atlas,
                                       "--padding", imaging.unwrap_padding(atlas),
                                       "--margin", imaging.dilation_px(atlas), "--max-loss", MAX_LOSS, *hair_args], log_name="faceuv.log")
        rep_path = out_dir / "face_report.json"
        relayout = json.loads(rep_path.read_text()) if rep_path.is_file() else None
        layout_report = relayout
        if relayout and relayout.get("layout_mode") != "unchanged" and new_glb.is_file():
            work_glb = new_glb
            work_maps = {k: Path(p) for k, p in (relayout.get("rebake_maps") or {}).items() if Path(p).is_file()}
        else:
            relayout = None
    if relayout and after_relayout is not None:
        work_maps = await after_relayout(work_glb, work_maps)
    if plan["ship"] > atlas:
        # 4096 base colour where the platform allows it: the atlas's base colour enlarged, then the face painted at 4096
        up = out_dir / "base_color_atlas_up.png"
        await asyncio.to_thread(enlarge, work_maps["base_color"], plan["ship"], up)
        work_maps["base_color"] = up

    # 3 and 5. Fit and paint.
    sc.progress(0.88, "Painting the face")
    # Optional (face_sides, on unless turned off): it hides the seam where the front painting ends on the cheek (the boy,
    # the red-haired knight), but on the T-pose man it painted a brown smear behind the ear from a side view whose
    # beard sat elsewhere; it also lowers the front's match a little (0.96 to 0.90, 0.90 to 0.88, 0.67 to 0.63).
    # (not when the body was painted from the pictures: every view is already on the model there, in its own order)
    sides = side_pictures(sc) if params.get("face_sides") is not False and not body_painted else {}
    facing = FACING_OVER_PICTURES if body_painted else FACING
    over = (facing,) if body_painted else ()  # the earlier hand-over, only when the body was painted from the pictures
    done = await asyncio.to_thread(fit_and_paint, work_glb, work_maps["base_color"], picture, Path(glb), Path(maps["base_color"]),
                                   crop, out_dir, strength, sides, colour_source, False, source == "closeup", *over)
    if not done.get("ok") and done.get("reason") == "closeup_off_model":
        closeup.update(used=False, reason="off_model")
        source, colour_source = "painting", None
        picture, method, redraw_seconds = await sharpen()
        done = await asyncio.to_thread(fit_and_paint, work_glb, work_maps["base_color"], picture, Path(glb), Path(maps["base_color"]),
                                       crop, out_dir, strength, sides, None, False, False, *over)
    if closeup.get("used") and source == "closeup":
        shown = out_dir / (OWN_FILE if closeup.get("own") else closeup.get("file") or "painted_head_closeup.png")
        if done.get("ok"):
            show_closeup(sc, shown, "used", "This close-up was used: the face is painted from it.")
        else:
            show_closeup(sc, shown, "refused", "This close-up passed its checks, but it was not used: the picture could not be lined up with "
                                               "the model's face well enough, so the face was left as it was.")
    if not done.get("ok"):
        return _skipped(done.get("reason") or "error", t0, {**record, "closeup": closeup, "alignment": done.get("alignment")})
    files: dict[str, Path] = {k: p for k, p in work_maps.items() if k != "base_color"} if relayout else {}
    # The painted base colour becomes base_color.png in the face folder; the GLB carries it.
    final_base = out_dir / "base_color.png"
    if relayout:
        shutil.copy2(work_maps["base_color"], out_dir / "base_color_before_face.png")
    shutil.copy2(done["base_color"], final_base)
    files["base_color"] = final_base
    files["face_before_after"] = done["sheet"]
    if source == "closeup":
        files["face_closeup"] = out_dir / (OWN_FILE if closeup.get("own") else closeup.get("file") or "painted_head_closeup.png")
        if closeup.get("guided"):
            files["face_guide"] = out_dir / GUIDE_FILE
    if relayout:
        shutil.copy2(work_glb, glb)
    await asyncio.to_thread(imaging.glb_replace_images, glb, base_color=final_base)
    achieved = int(done["metrics"].get("face_texels_after") or 0)
    rest = (relayout or {}).get("rest_scale")
    density = {**plan, "achieved": achieved, "rest_scale": rest, "note": density_note(plan, achieved, rest)}
    record.update(ran=True, method=method, source=source, closeup=closeup, density=density, redraw_seconds=redraw_seconds,
                  **done["metrics"],
                  layout=None if not relayout else {k: relayout.get(k) for k in ("layout_mode", "gain", "rest_scale", "island_scale", "islands", "rebake_seconds")},
                  seconds=round(time.time() - t0, 1))
    return {"files": files, "metrics": {"face_detail": record}, "relayout": bool(relayout), "base_color_size": int(plan["ship"]),
            "layout": layout_report}


def enlarge(src: Path, size: int, dest: Path) -> Path:
    """The base colour at size (Lanczos): the atlas's own colours, more texels for the face to be painted into."""
    Image.open(src).convert("RGB").resize((size, size), Image.LANCZOS).save(dest)
    return dest


def side_pictures(sc: StageContext) -> dict[str, Path]:
    """The side-views step's cut-out pictures this pass can paint from (both quarters, left, right), the ones that exist.
    With hair from the library (the 3D model was built bald) only the bald side pictures are read, and none at all when
    they are missing, so no haired side picture is ever painted onto the skull."""
    from .. import hairlib

    bald = hairlib.is_library(sc)
    out: dict[str, Path] = {}
    for name, _ in SIDE_VIEWS:
        p = sc.lineage_file(f"bald_{name}_cutout" if bald else f"{name}_cutout", ("turnaround",))
        if p is not None and Path(p).is_file():
            out[name] = Path(p)
    return out


def _skipped(reason: str, t0: float, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"files": {}, "metrics": {"face_detail": {**(extra or {}), "ran": False, "skipped": reason,
                                                     "seconds": round(time.time() - t0, 1)}}, "relayout": False}
