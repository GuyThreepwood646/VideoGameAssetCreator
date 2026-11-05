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
import json
import shutil
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from .. import face, imaging, quality
from ..comfy import ComfyClient, ComfyError
from ..graphs.qwen_edit import edit_graph
from ..jobs import JobCancelled
from .base import StageContext, random_seed

CATEGORIES = ("humanoid",)
TARGET_FRACTION = 0.125  # texels wanted across the face: an eighth of the texture's side (256 at 2048)
GAIN_MIN = 1.15  # below this the layout is left alone (the repack and the re-bake take about 30 s)
SHARP_MIN = 1.0  # face sharpness after the pass (face.sharpness of the front render); smeared faces score 0.6 to 0.8
MATCH_MIN = 0.6  # the model's face against the fitted picture (face.feature_match)
ALIGN_MIN = 0.15  # the fit's correlation; under it the picture did not find the face
RIM_FRAC = 0.02  # how much of the painted face's edge is dropped outside the inner face, as a share of the picture
SOFTEN_M = 0.025  # the paint fades over about this much surface wherever it stops short (metres)
FACING = (0.15, 0.6)  # surface-to-view cosine where the paint fades in: from edge-on (old texture) to fully painted
EYE_COLOUR_MAX = 8.0  # the eyes' colour on the model against the painting (Lab a-b distance); about a just visible hue change
# Each eye on its own (eye_measures, eye_state). Measured on the knights: painted eyes 0.98 to 1.0 painted, match 0.93
# to 1.0, sharp 0.58 to 0.80 of the picture's; the eye left as a smudge 0.004 painted, match 0.19, sharp 0.25.
EYE_PAINTED_MIN = 0.5  # the eye box's mean paint weight
EYE_MATCH_MIN = 0.6  # the render's eye against the picture's (face.feature_match in the box)
EYE_SHARP_MIN = 0.4  # the render's sharpness in the eye box over the picture's
EYE_HAIR_COVER = 0.3  # an unpainted eye whose box is at least this much the model's hair is under the hair
# Side views (the turnaround's pictures): which picture, from where. The quarter view's side is found by its outline.
SIDE_VIEWS = (("quarter", (40.0, -40.0)), ("left", (90.0,)), ("right", (-90.0,)))
SIDE_IOU_MIN = 0.85  # the side picture's head outline against the model's; under it the side is not used
SIDE_FACING = (0.25, 0.7)
DEBUG_SIDES = False  # write side_<view>.png (picture, toned, render; weight, front cover, skin) into the face folder
LANDMARK_RESIDUAL_MAX = 0.12  # the eye-and-mouth fit's eye error, as a share of the face's width
REDRAW_KEEP_MIN = 0.5  # the sharpened picture against the crop: under it the redraw moved things
COVERED_WIDTH_MIN = 0.35
DEFAULT_STRENGTH = 1.0
# Reasons the person can do something about: the check fails softly with a Fix (run the clean-up step again).
ACTIONABLE = ("no_card", "not_lined_up", "error")

REDRAW_PROMPT = ("Redraw this picture at high quality with crisp, sharp detail: clear eyes with irises, eyelids and lashes, "
                 "defined eyebrows, nose and lips, fine hair strands. Keep everything else exactly the same: the same "
                 "character, the same face, the same position, size, angle and framing, the same hair, clothing and grey "
                 "background, the same colours and the same painting style. Soft, even lighting.")

METHOD = "painted picture, sharpened by Qwen-Image-Edit, fitted to the 3D face and painted on through the texture layout"
METHOD_UNSHARPENED = "painted picture fitted to the 3D face and painted on through the texture layout (not sharpened)"
METHOD_CLOSEUP = ("painted picture's colour and light with the fine detail of a close-up drawn by Qwen-Image-Edit and checked "
                  "against the painting, fitted to the 3D face and painted on through the texture layout")

# --- A close-up source picture when the painted face is small ---------------------------------------------------
# The painted head in a full-body concept is small (the knight's face is about 68 of the concept's pixels across),
# while the texture gives the face 256 or more texels: the crop is enlarged about seven times and carries no detail at
# that size. When the painting has fewer pixels across the face than the texture will hold, the image edit model draws
# the same head as a close-up at 1024 (the crop's framing: Qwen keeps the framing of its input, which here is what is
# wanted). The face is then painted from the painting's colour and light at the painting's own resolution plus all the
# close-up's finer detail (closeup_source); the separate sharpening redraw is not run. Only when it is still the same face: compared with the
# painting at the painting's own resolution (likeness), the eyes', skin's and hair's colours, and where the eyes and
# mouth fall, before and after fitting to the model. Otherwise today's path (the crop, sharpened) is used and the reason
# recorded.
CLOSEUP_PROMPT = ("A head-and-shoulders close-up portrait of this same character, drawn again at high resolution with crisp, "
                  "fine detail: the same face and features, the same eye colour, the same hair and hairstyle, the same "
                  "expression, the same clothing at the shoulders, lit from the same direction. Facing straight toward the "
                  "viewer, the head centred and the same size and position in the picture, plain flat grey background. The "
                  "same painting style and the same colours.")
CLOSEUP_DEFAULT = True  # the face_closeup option when it is not set
LIKENESS_MIN = 0.93  # face.likeness: good close-ups of the knight 0.976 to 0.980, different faces 0.19 to 0.87
CLOSEUP_SKIN_MAX = 8.0  # skin colour against the painting (Lab, lightness half); good close-ups 1.9 to 3.8
CLOSEUP_HAIR_MAX = 10.0  # hair colour, the same; good close-ups 1.6 to 3.5, a different hair colour 40 and more
CLOSEUP_MOVE_MAX = 0.04  # how far the eyes and mouth may sit from the painting's, as a share of the face's width
CLOSEUP_REASONS = {
    "likeness": "it did not look enough like the painted face",
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


def skip_reason(category_id: str, rec: dict[str, Any] | None, params: dict[str, Any], has_picture: bool) -> str | None:
    """Why the pass does not run for this version, or None when it should run. In the order a person would ask."""
    if params.get("face_detail") is False:
        return "off"
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


SIDE_WORDS = {"quarter": "the three-quarter view", "left": "the left side", "right": "the right side"}


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
        return [quality.check("face_matches_picture", False, reason, SHARP_MIN, vars={"reason": reason})]
    sharp = float(fd.get("sharpness_after") or 0.0)
    match = float(fd.get("feature_match_after") or 0.0)
    shift = fd.get("eye_colour_shift")
    colour_ok = shift is None or float(shift) <= EYE_COLOUR_MAX
    eye_reason, bad_eyes = eyes_verdict(fd.get("eyes"))
    ok = sharp >= SHARP_MIN and match >= MATCH_MIN and colour_ok and eye_reason is None
    reason = None if ok else ("blurry" if sharp < SHARP_MIN else eye_reason if eye_reason
                              else "off_picture" if match < MATCH_MIN else "eye_colour")
    cu = fd.get("closeup") or {}
    dens = fd.get("density") or {}
    return [quality.check("face_matches_picture", ok, round(sharp, 3), SHARP_MIN,
                          vars={"reason": reason, "eye_shift": shift, "sides": side_words(fd.get("sides_used") or []),
                                **eye_words(bad_eyes if reason == eye_reason else []),
                                "before": fd.get("sharpness_before"), "match": round(match, 3), "texels": fd.get("face_texels_after"),
                                "source": source_words(fd), "closeup_not_used": CLOSEUP_REASONS.get(cu.get("reason") or "", "")
                                if cu.get("wanted") and not cu.get("used") else "",
                                "density_note": dens.get("note") or "", "level": dens.get("level") or "standard"})]


def eye_state(e: dict[str, Any]) -> str | None:
    """What is wrong with one eye from eye_measures, or None when it was painted and is sharp: "eye_under_hair" (not
    painted because the model's hair covers it), "eye_missing" (not painted, or the render does not show the
    picture's eye), "eye_blurry" (painted but much softer than the picture's)."""
    painted = float(e.get("painted") or 0.0)
    if painted < EYE_PAINTED_MIN:
        return "eye_under_hair" if float(e.get("hair") or 0.0) >= EYE_HAIR_COVER else "eye_missing"
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
    cam = face.head_camera(head, 0, 1024)
    view = face.render_view(mesh, tex, cam)
    info = face.find_face(view, mesh, cam, head)
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
    return {"found": True, "reason": None, "texture_size": int(tex.shape[0]), "face_width_px": info["width_px"],
            "painted_head_px": crop["head_px"], "face_triangles": face.triangle_centres(mesh, tris).round(6).tolist(), **dens}


def closeup_guard(glb: Path, base_color: Path, crop: Path, closeup: Path, painted_head_px: float) -> dict[str, Any]:
    """Is the close-up still the painted face? Measured against the painted crop (the same framing) at the painting's
    own resolution, and against the model's front render. Returns {ok, reason (a CLOSEUP_REASONS key or None),
    likeness, eye_colour_shift, skin_shift, hair_shift, feature_move, fit_residual, source_face_px}."""
    mesh = face.load_mesh(glb)
    tex = face.read_rgb(base_color)
    head = face.find_head(mesh["verts"])
    cam = face.head_camera(head, 0, 1024)
    view = face.render_view(mesh, tex, cam)
    info = face.find_face(view, mesh, cam, head)
    if not info.get("found"):
        return {"ok": False, "reason": "no_features"}
    a = face.read_rgb(crop, 1024)
    b = face.read_rgb(closeup, 1024)
    w = float(info["width_px"])
    inner = face.inner_face(info)
    out: dict[str, Any] = {"likeness": face.likeness(a, b, inner, painted_head_px), "source_face_px": round(w, 1)}
    seg = face.hair_and_skin(view["image"], a, info, view["mask"])
    skin = inner & ~seg["pic_hair"]
    out["skin_shift"] = face.colour_distance(a, b, skin)
    out["hair_shift"] = face.colour_distance(a, b, seg["pic_hair"]) if seg.get("found") else None
    # When the crop's eyes and mouth as framed do not fit the model's, they are searched after lining the crop up with
    # the model's face by the whole face, the close-up moved the same way (so how far its features moved from the crop's is unchanged), as fit_picture does: as framed,
    # the crop's eyes can sit above the band face.landmarks searches, and the darkest spots found there are not eyes
    # (the second knight's close-up was rejected as "moved" for that). The eye colours are measured on the moved pictures.
    lm = face.landmarks(view["image"], info)
    la = face.landmarks(a, info)
    direct = face.fit_landmarks(la, lm, info["nose"]) if la and lm else None
    if direct is None or direct["residual_px"] > LANDMARK_RESIDUAL_MAX * w:
        pre = face.align(a, view["image"], inner)
        if face.feature_match(face.apply_alignment(a, pre), view["image"], inner, 6.0, 24.0) >= ALIGN_MIN:
            a, b = face.apply_alignment(a, pre), face.apply_alignment(b, pre)
            out["lined_up_first"] = True
    la, lb = face.landmarks(a, info), face.landmarks(b, info)
    reason = None
    if not la or not lb:
        reason = "no_features"
    else:
        pa = la["eyes"] + [la["mouth"]]
        pb = lb["eyes"] + [lb["mouth"]]
        out["feature_move"] = round(max(float(np.hypot(p[0] - q[0], p[1] - q[1])) for p, q in zip(pa, pb)) / w, 4)
        out["eye_colour_shift"] = face.colour_shift(face.eye_colour(a, la["eyes"], w), face.eye_colour(b, la["eyes"], w))
        fit = face.fit_landmarks(lb, lm, info["nose"]) if lm else None
        out["fit_residual"] = None if fit is None else round(fit["residual_px"] / w, 4)
    if reason is None:
        reason = closeup_verdict(out)
    return {"ok": reason is None, "reason": reason, **out}


def closeup_verdict(m: dict[str, Any]) -> str | None:
    """Why a close-up is not used (a CLOSEUP_REASONS key), from closeup_guard's measures, or None when it is the same
    face. Not measurable colours do not count against it; an unmeasurable fit to the model is left to the paint step's
    whole-face fit and its ALIGN_MIN, as for the painting."""
    if m.get("feature_move") is None:
        return "no_features"
    if float(m.get("likeness") or 0.0) < LIKENESS_MIN:
        return "likeness"
    if float(m["feature_move"]) > CLOSEUP_MOVE_MAX:
        return "moved"
    if float(m.get("eye_colour_shift") or 0.0) > EYE_COLOUR_MAX:
        return "eye_colour"
    if m.get("skin_shift") is not None and float(m["skin_shift"]) > CLOSEUP_SKIN_MAX:
        return "skin_colour"
    if m.get("hair_shift") is not None and float(m["hair_shift"]) > CLOSEUP_HAIR_MAX:
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


def redraw_kept(sharp: Path, crop: Path) -> float:
    """How well the redrawn picture keeps the crop's features in place (whole picture)."""
    a = face.read_rgb(sharp, 1024)
    b = face.read_rgb(crop, 1024)
    return face.feature_match(a, b, np.ones(a.shape[:2], dtype=bool), lo=2.0, hi=10.0)


def fit_and_paint(glb: Path, base_color: Path, picture: Path, before_glb: Path, before_tex: Path, crop: Path, out_dir: Path,
                  strength: float, sides: dict[str, Path] | None = None, colour_source: Path | None = None,
                  require_landmarks: bool = False) -> dict[str, Any]:
    """Steps 3 and 5 on the final layout: fit the picture to the render, paint it back, measure, and draw the before
    and after sheet. Writes base_color_face.png and face_before_after.png into out_dir.

    colour_source: the picture whose colours and broad tone are painted (the painted crop by default; the checked
    close-up when one is used). The eye colour is always measured against the painted crop. require_landmarks: the
    eye-and-mouth fit must work (a close-up whose features do not land on the model's is not used: reason
    "closeup_off_model")."""
    mesh = face.load_mesh(glb)
    tex = face.read_rgb(base_color)
    T = tex.shape[0]
    head = face.find_head(mesh["verts"])
    cam = face.head_camera(head, 0, 1024)
    view = face.render_view(mesh, tex, cam)
    info = face.find_face(view, mesh, cam, head)
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
    a = fit_picture(src, view["image"], info, inner)
    if a["how"] != "eyes and mouth" and require_landmarks:
        return {"ok": False, "reason": "closeup_off_model"}
    if a["score"] < ALIGN_MIN:
        return {"ok": False, "reason": "not_lined_up", "alignment": a}
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
    core = seg["pic_hair"] | (seg["pic_background"] & ~inner)
    # Outside the eyes-nose-mouth area the painted face's own rim goes too (the shadow along its jaw and cheek, which
    # lands inside the model's wider cheek and shows as a line from the side).
    rim = face.dilate_mask(core, max(2, int(RIM_FRAC * view["image"].shape[0]))) & ~face.features_box(info)
    bad = (core | rim) & paint
    if bad.any():
        filled = face.inpaint(fitted, paint & ~bad)
        fitted = np.where(bad[..., None], filled, fitted)
    weight = face.screen_weight(paint)
    # where the paint will fade (the region's edge, and surface turning away from the camera) the tone is matched
    # locally, so the cheeks do not show a step where the painted face hands over to the old texture
    fade = weight * face.smoothstep(FACING[0], FACING[1] + 0.25, view["facing"])
    lm_fit = face.landmarks(fitted, info)
    keep = face.eye_boxes(region.shape, lm_fit["eyes"], float(info["width_px"])) if lm_fit else None
    toned = face.tone_match_local(fitted, view["image"], paint, fade, keep=keep)
    res = face.project_back(mesh, tex, cam, toned, weight, facing=FACING, strength=strength, soften_m=SOFTEN_M)
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
    before = face.render_view(old_mesh, old_tex, face.head_camera(old_head, 0, 1024))
    metrics = {
        "sharpness_before": face.sharpness(before["image"], region), "sharpness_after": face.sharpness(after["image"], region),
        "feature_match_before": face.feature_match(before["image"], toned, region),
        "feature_match_after": face.feature_match(after["image"], toned, region),
        "fit_match": match_fit,
        "alignment": {k: a.get(k) for k in ("how", "sx", "sy", "tx", "ty", "score", "residual_px")},
        "texels_painted": res["changed"], "hair_kept_px": int((region & seg["old_hair"]).sum()), "hair_filled_px": int(bad.sum()), "tone_spread_texels": spread_n,
        "face_texels_after": int(face.face_texels(mesh, cam, region, T)["face_width_texels"]),
        "eyes_moved": eyes_moved(view["image"], after["image"], info),
        "eyes": eye_measures(toned, after["image"], weight, seg["old_hair"], lm_fit, info),
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
    lm_dst = face.landmarks(render, info)

    def scored(a: dict[str, Any], how: str) -> dict[str, Any]:
        a["how"] = how
        a["score"] = face.feature_match(face.apply_alignment(src, a), render, inner, 6.0, 24.0)
        return a

    def good(a: dict[str, Any] | None) -> bool:
        return a is not None and a["residual_px"] <= LANDMARK_RESIDUAL_MAX * w and a["score"] >= ALIGN_MIN

    lm_src = face.landmarks(src, info)
    a = face.fit_landmarks(lm_src, lm_dst, info["nose"]) if lm_src and lm_dst else None
    if a is not None:
        a = scored(a, "eyes and mouth")
        if good(a):
            return a
    whole = scored(face.align(src, render, inner), "whole face")
    if lm_dst and whole["score"] >= ALIGN_MIN:
        lm_pre = face.landmarks(face.apply_alignment(src, whole), info)
        then = face.fit_landmarks(lm_pre, lm_dst, info["nose"]) if lm_pre else None
        if then is not None:
            b = {**face.compose_alignment(whole, then), "residual_px": then["residual_px"]}
            b = scored(b, "eyes and mouth")
            if good(b):
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
        for yaw in yaws:
            cam = face.head_camera(head, yaw, 1024)
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


def eye_measures(picture: np.ndarray, after: np.ndarray, weight: np.ndarray, model_hair: np.ndarray,
                 lm: dict[str, Any] | None, info: dict[str, Any]) -> list[dict[str, Any]] | None:
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
                    "match": face.feature_match(after, picture, wide),
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
        imgs = [face.render_view(m, t, face.head_camera(h, yaw, 768))["image"] for m, t, h in (before, after)]
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
        inst = jobs.route("turnaround", sc.asset.category, sc.settings, prefs.disabled_instances(sc.settings), p.get("allowed_kinds"))
    except Exception:  # noqa: BLE001
        inst = sc.settings.stage_instances.get("turnaround")
    if not inst or inst == jobs.HOST or inst not in sc.ctx.clients:
        return None
    return inst


async def redraw(sc: StageContext, instance: str, crop: Path, seed: int, dest: Path, prompt: str = REDRAW_PROMPT,
                 key: str = "face_redraw", message: str = "Sharpening the painted face") -> Path:
    """Qwen-Image-Edit redraw of the crop (the sharpening redraw, or the close-up with CLOSEUP_PROMPT). Stop works: a
    cancelled job takes its own prompt off the card (removes it from the queue, or interrupts it when it is the one
    running), never someone else's."""
    client: ComfyClient = sc.client(instance)
    name = await client.stage_input(crop, f"vgac_{sc.version.id[:8]}_{key}_head.png")
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
    return await client.download_output(imgs[0], dest)


def picked_seed(sc: StageContext, params: dict[str, Any], key: str) -> int:
    """The version's seed for one of the pass's picture jobs (face_seed, face_closeup_seed): the one given (a Resume
    carries the stopped run's, jobs.RESUME_SEEDS), else a new one, written on the version at once so a Stop and Resume
    redraws the same picture."""
    seed = params.get(key)
    if seed is None:
        seed = random_seed()
        sc.set_params(**{key: seed})
    return int(seed)


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
              hair_plan: dict[str, Any] | None = None) -> dict[str, Any]:
    """The pass on the prepared model `glb` (changed in place) with the current maps. Returns {files: {key: path},
    metrics: {"face_detail": {...}}, relayout: bool, base_color_size: int, layout: the relayout report or None}. Never
    raises for a reason that should not fail the clean-up step (those are recorded as skipped); Stop still stops it.

    hair_plan: {points (Path), gain} from the hair pass (stages/hair_detail.py find): the one relayout gives the hair
    more texels too (blender/faceuv.py --hair-points), and runs for the hair even when the face needs none."""
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

    instance = image_instance(sc)
    reachable = False
    if instance is not None:
        reachable = bool((await sc.client(instance).vram_summary()).get("reachable"))
    if not reachable:
        return _skipped("no_card", t0, record)

    # A. The close-up: when the painting has fewer pixels across the face than the texture will hold.
    wanted = plan_density(choice, budget_tex, atlas, None)["target"]
    picture, colour_source, method, source = crop, None, METHOD_UNSHARPENED, "painting"
    closeup: dict[str, Any] = {"wanted": closeup_wanted(params, crop_px, wanted), "needed_texels": wanted}
    if closeup["wanted"]:
        seed_c = picked_seed(sc, params, "face_closeup_seed")
        sc.progress(0.77, "Drawing a close-up of the painted face")
        t1 = time.time()
        try:
            cu = await redraw(sc, instance, crop, seed_c, out_dir / "painted_head_closeup.png", CLOSEUP_PROMPT,
                              "face_closeup", "Drawing a close-up of the painted face")
            guard = await asyncio.to_thread(closeup_guard, glb, Path(base), crop, cu, found["painted_head_px"])
            closeup.update({k: v for k, v in guard.items() if k != "ok"})
            closeup["used"] = bool(guard["ok"])
            if guard["ok"]:
                merged = await asyncio.to_thread(closeup_source, crop, cu, found["painted_head_px"], out_dir / "painted_head_closeup_merged.png")
                picture, colour_source, method, source = merged, merged, METHOD_CLOSEUP, "closeup"
            sc.use_model("qwen_edit")
            sc.use_model("qwen_lightning_lora")
        except (JobCancelled, asyncio.CancelledError):
            raise
        except Exception as e:  # noqa: BLE001  (a failed close-up: today's path)
            closeup.update(used=False, reason="error", error=str(e)[:300])
        closeup["seconds"] = round(time.time() - t1, 1)
    elif closeup_on(params):
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
    if plan["ship"] > atlas:
        # 4096 base colour where the platform allows it: the atlas's base colour enlarged, then the face painted at 4096
        up = out_dir / "base_color_atlas_up.png"
        await asyncio.to_thread(enlarge, work_maps["base_color"], plan["ship"], up)
        work_maps["base_color"] = up

    # 3 and 5. Fit and paint.
    sc.progress(0.88, "Painting the face")
    sides = side_pictures(sc)
    done = await asyncio.to_thread(fit_and_paint, work_glb, work_maps["base_color"], picture, Path(glb), Path(maps["base_color"]),
                                   crop, out_dir, strength, sides, colour_source, source == "closeup")
    if not done.get("ok") and done.get("reason") == "closeup_off_model":
        closeup.update(used=False, reason="off_model")
        source, colour_source = "painting", None
        picture, method, redraw_seconds = await sharpen()
        done = await asyncio.to_thread(fit_and_paint, work_glb, work_maps["base_color"], picture, Path(glb), Path(maps["base_color"]),
                                       crop, out_dir, strength, sides)
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
        files["face_closeup"] = out_dir / "painted_head_closeup.png"
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
    """The side-views step's cut-out pictures this pass can paint from (quarter, left, right), the ones that exist."""
    out: dict[str, Path] = {}
    for name, _ in SIDE_VIEWS:
        p = sc.lineage_file(f"{name}_cutout", ("turnaround",))
        if p is not None and Path(p).is_file():
            out[name] = Path(p)
    return out


def _skipped(reason: str, t0: float, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"files": {}, "metrics": {"face_detail": {**(extra or {}), "ran": False, "skipped": reason,
                                                     "seconds": round(time.time() - t0, 1)}}, "relayout": False}
