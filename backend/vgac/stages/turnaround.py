"""Stage 2, preview turnaround: concept image -> views with Qwen-Image-Edit-2511 + multiple-angles LoRA, per-view
BiRefNet cutouts and consistency checks (instance B).

Checks per view against the front view: silhouette height ratio, bounding-box aspect, colour histogram distance,
and left-right mirror IoU. A failing view is re-rolled with a new seed up to 2 times when auto_reroll is on, then
flagged. refine re-runs one view; adjust re-runs the whole set with new params.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .. import hairlib, library, quality, reference
from ..db import Version
from ..graphs.qwen_edit import CAMERA_TURNS, SHEET_INPUT, SHEET_ORDER, angle_graph, camera_graph, edit_graph, sheet_graph, view_prompt
from ..imaging import alpha_bbox, colour_histogram, contact_sheet, histogram_distance, pair_overlap, pose_extents, white_composite
from .base import StageContext, random_seed
from .concept import stage_recipe

DEFAULT_VIEWS = ["front", "left", "back", "right", "quarter"]
# The three-quarter view from the other side: drawn for these categories whenever the three-quarter view is, because
# the texture is painted from the views (prep) and one three-quarter view leaves the other cheek and flank to a full
# side view alone. `second_quarter: false` on the version leaves it out.
SECOND_QUARTER = "quarter2"
SECOND_QUARTER_CATEGORIES = ("humanoid",)


def with_second_quarter(views: list[str], category: str | None, params: dict[str, Any]) -> list[str]:
    if (category in SECOND_QUARTER_CATEGORIES and "quarter" in views and SECOND_QUARTER not in views
            and params.get("second_quarter") is not False):
        i = views.index("quarter") + 1
        return views[:i] + [SECOND_QUARTER] + views[i:]
    return views
MAX_REROLLS = 2
# Views drawn from opposite sides of the figure. The later-drawn one of a pair is checked against the earlier one
# (same_side): mirror images of each other when right, the same way round when one was drawn from the wrong side.
# Only left and right also take the mirror_iou limit, as before.
PARTNER = {"left": "right", "right": "left", "quarter": SECOND_QUARTER, SECOND_QUARTER: "quarter"}
MIRROR_PAIRS = ("left", "right")
# same_side fails when the as-drawn overlap beats the mirrored one by more than this. Not zero: a subject that looks the
# same from both sides (a barrel, a figure standing square to the camera) has the two overlaps equal, and noise would
# decide. The realistic test's two faulty pairs sit at +0.285 and +0.150, its 20 good pairs at -0.017 and below.
SAME_SIDE_MARGIN = 0.05
# pose_held: the views seen straight from the front or the back must keep the picture's pose. Their arm reach (outline
# width from shoulder to wrist height) and leg reach (width over the lower 40 percent), each as a share of the figure's
# height, are compared with the same of the picture's cut-out; a ratio outside the range means arms or legs are in a
# different place. The realistic test (17 runs): front views 0.34 to 0.62 of the picture's arm and leg reach in 16
# runs where the drawn arms hung down or the legs closed, 1.00 in the 1 run that held the pose; back views 0.95 to
# 1.03 (arms) and 0.87 to 0.96 (legs) in all 17. The gap between 0.62 and 0.87 holds the limits. Side and
# three-quarter views are not checked: a side outline hides arm spread, and no stored view of either kind has a
# known-held pose to calibrate against.
POSE_VIEWS = ("front", "back")
POSE_ARM_RANGE = (0.8, 1.25)
POSE_LEG_RANGE = (0.75, 1.3)
PARTNER_WORDS = {"left": "left side", "right": "right side", "quarter": "three-quarter", SECOND_QUARTER: "other three-quarter"}
LIMITS = {"height_ratio": (0.75, 1.3), "hist_distance": 0.35, "mirror_iou": 0.55, "min_coverage": 0.02}

# How the views are turned (turn_method). standard: the multiple-angles LoRA, one view at a time, every category.
# sheet: the character turnaround sheet LoRA draws front, right, back, left and quarter in one picture; characters
# only, it makes the look more photographic (bake-off 5.1). shadow_free: the camera-angle LoRA turns the camera
# around the concept on white, no cast shadows; rigid props only, colours match a little less (bake-off 5.2).
# A view a method cannot draw (top), a failing view's redraws with the sheet and any redraw of a sheet view with a
# described change use the standard method; metrics.views[view].method says which one drew each view.
TURN_METHODS: dict[str, list[str] | None] = {"standard": None, "sheet": ["humanoid"], "shadow_free": ["prop_rigid"]}
METHOD_ADDON = {"standard": ("qwen_angles", "qwen_angles_lora"), "sheet": ("turnaround_sheet", "qwen_sheet_lora"),
                "shadow_free": ("turnaround_white", "qwen_camera_lora")}
METHOD_STRENGTH = 1.0  # the sheet and the camera LoRA ran at 1.0 in the bake-off; lora_strength is the standard one's
SHEET_MIN_WIDTH = 20  # a run of figure columns narrower than this is a speck, not a figure
VIEW_SIZE = 1024  # each figure cut from the sheet is padded to a square and scaled to this, like a standard view


def turn_method(category: str | None, params: dict[str, Any]) -> str:
    """The version's turn_method, checked against the asset's category (standard when not set)."""
    m = str(params.get("turn_method") or "standard")
    if m not in TURN_METHODS:
        raise ValueError(f"turn_method must be one of {sorted(TURN_METHODS)}")
    allowed = TURN_METHODS[m]
    if allowed is not None and category not in allowed:
        what = {"sheet": "characters", "shadow_free": "rigid props"}[m]
        raise ValueError(f"the {m} turn is for {what} only")
    return m


def _category(sc: StageContext) -> str | None:
    return getattr(getattr(sc, "asset", None), "category", None)


def method_draws(method: str, view: str) -> bool:
    """Whether `method` itself can draw `view` (else the standard method draws it)."""
    if method == "sheet":
        return view in SHEET_ORDER
    if method == "shadow_free":
        return view == "front" or view in CAMERA_TURNS
    return True


def _source_image(sc: StageContext, source: Version | None) -> Path:
    """The concept image of `source`, the version the views are generated from (not necessarily sc.parent)."""
    if source is None:
        raise ValueError("turnaround needs a concept parent version")
    src = library.version_file(source, "image", sc.settings)
    if src is None:
        raise FileNotFoundError("parent concept image missing")
    return src


def concept_of(sc: StageContext, v: Version | None) -> Version | None:
    """The concept version a turnaround version's views come from: up the parents past every adjust and refine of
    the turnaround (a bald_only adjust, a refine of a refine), to the first version that is not a turnaround. For a
    turnaround made straight from the concept this is its parent, as before; None when there is none."""
    seen: set[str] = set()
    cur = sc.db.get(Version, v.parent_version_id) if v is not None and v.parent_version_id else None
    while cur is not None and cur.stage == "turnaround" and cur.id not in seen:
        seen.add(cur.id)
        cur = sc.db.get(Version, cur.parent_version_id) if cur.parent_version_id else None
    return None if cur is None or cur.stage == "turnaround" else cur


def same_side_check(view: str, as_drawn: float, mirror: float) -> dict[str, Any]:
    """same_side: fails when this view's outline matches its partner's better as drawn than mirrored, which means both
    were drawn from the same side of the figure (the realistic test: both side views of one run faced the same way and
    the 3D model came out facing backwards; both three-quarter views of another showed the same cheek). value is the
    as-drawn overlap, limit the mirrored one plus SAME_SIDE_MARGIN. A warning, like the other view checks; the sentence
    is the check's own."""
    ok = as_drawn <= mirror + SAME_SIDE_MARGIN
    partner = PARTNER_WORDS.get(PARTNER.get(view, ""), "opposite")
    if ok:
        text = f"This view and the {partner} view are drawn from opposite sides of the figure."
    else:
        text = (f"This view looks drawn from the same side as the {partner} view: their outlines match better as drawn "
                f"({as_drawn:.0%}) than mirrored ({mirror:.0%}). Draw this view again, or the model can come out facing "
                "the wrong way or with one side painted twice.")
    return {"id": "same_side", "ok": ok, "value": round(as_drawn, 4), "limit": round(mirror + SAME_SIDE_MARGIN, 4), "severity": "warn",
            "help": text}


def pose_check(view: str, ext: dict[str, float], ref: dict[str, float]) -> dict[str, Any]:
    """pose_held: fails when this front or back view's arms or legs reach sideways a different amount than the
    picture's (ext and ref are imaging.pose_extents of the view and of the picture's cut-out). value is the arm ratio,
    leg_ratio the other; a warning, with a sentence saying which view and what differs."""
    arm = ext["arm"] / max(1e-6, ref["arm"])
    leg = ext["leg"] / max(1e-6, ref["leg"])
    arm_ok = POSE_ARM_RANGE[0] <= arm <= POSE_ARM_RANGE[1]
    leg_ok = POSE_LEG_RANGE[0] <= leg <= POSE_LEG_RANGE[1]
    ok = arm_ok and leg_ok
    if ok:
        text = f"The arms and legs of this {view} view are where they are in your picture."
    else:
        what = []
        if not arm_ok:
            what.append(f"the arms reach {arm:.0%} as far out as in your picture ({'closer to the body' if arm < 1 else 'further out'})")
        if not leg_ok:
            what.append(f"the legs are {leg:.0%} as far apart as in your picture ({'closer together' if leg < 1 else 'further apart'})")
        text = (f"In this {view} view " + " and ".join(what) + ". The pose changed when it was drawn, so painting it onto "
                "the model can streak the arms and body. Draw this view again, or edit it so the arms and legs are where they are in your picture.")
    return {"id": "pose_held", "ok": ok, "value": round(arm, 4), "leg_ratio": round(leg, 4),
            "limit": [POSE_ARM_RANGE[0], POSE_ARM_RANGE[1]], "leg_limit": [POSE_LEG_RANGE[0], POSE_LEG_RANGE[1]],
            "severity": "warn", "help": text}


def view_checks(view: str, stats: dict[str, Any], front: dict[str, Any] | None, hist_front, hist_view, mirror: float | None,
                as_drawn: float | None = None, pose: tuple[dict[str, float], dict[str, float]] | None = None) -> dict[str, Any]:
    """The checks of one view. mirror and as_drawn are its outline's overlap with its partner view's (mirrored, and as
    drawn; imaging.pair_overlap), given when the partner exists: left and right take the mirror_iou limit, and every
    pair takes same_side when as_drawn is given. pose is (the view's pose_extents, the picture's) for a front or back view
    of a humanoid; it adds pose_held."""
    checks: list[dict[str, Any]] = []
    checks.append({"id": "coverage", "ok": stats["coverage"] >= LIMITS["min_coverage"], "value": stats["coverage"], "limit": LIMITS["min_coverage"]})
    out: dict[str, Any] = {"bbox": stats}
    if front is not None and view != "front" and front.get("height"):
        ratio = stats["height"] / max(1, front["height"])
        out["height_ratio"] = round(ratio, 4)
        lo, hi = LIMITS["height_ratio"]
        checks.append({"id": "height_ratio", "ok": lo <= ratio <= hi, "value": round(ratio, 4), "limit": [lo, hi]})
        out["aspect"] = stats["aspect"]
        out["aspect_vs_front"] = round(stats["aspect"] / max(1e-6, front["aspect"]), 4)
    if hist_front is not None and hist_view is not None and view != "front":
        d = histogram_distance(hist_front, hist_view)
        out["hist_dist"] = round(d, 4)
        checks.append({"id": "hist_dist", "ok": d <= LIMITS["hist_distance"], "value": round(d, 4), "limit": LIMITS["hist_distance"]})
    if mirror is not None and view in MIRROR_PAIRS:
        out["mirror_iou"] = round(mirror, 4)
        checks.append({"id": "mirror_iou", "ok": mirror >= LIMITS["mirror_iou"], "value": round(mirror, 4), "limit": LIMITS["mirror_iou"]})
    if mirror is not None and as_drawn is not None:
        out["pair_as_drawn"], out["pair_mirrored"] = round(as_drawn, 4), round(mirror, 4)
        checks.append(same_side_check(view, as_drawn, mirror))
    if pose is not None and view in POSE_VIEWS:
        pc = pose_check(view, *pose)
        out["pose_arm_ratio"], out["pose_leg_ratio"] = pc["value"], pc["leg_ratio"]
        checks.append(pc)
    out["checks"] = [{"name": c["id"], "passed": c["ok"], "message": f"{c['value']} (limit {c['limit']})", **c} for c in checks]
    out["passed"] = all(c["ok"] for c in checks)
    out["ok"] = out["passed"]
    return out


async def _generate_view(sc: StageContext, src_name: str, view: str, seed: int, lora_strength: float, extra: str, base: float, span: float,
                         name: str | None = None, instance: str | None = None) -> dict[str, Path]:
    """One drawn view. `name` (default the view) names its files: the bald pictures use bald_<view>. `instance`: the
    card it is drawn and cut out on (default the job's own card); the graph is the same on every card."""
    name = name or view
    graph = angle_graph(src_name, view_prompt(view, extra), seed, sc.prefix(f"{name}_{seed}"), lora_strength=lora_strength)
    on = {"instance": instance} if instance else {}
    entry = await sc.comfy(graph, message=f"view {name}", base=base, span=span * 0.7, **on)
    raw = await sc.download_image(entry, "save", sc.dir / f"{name}_raw.png", **on)
    res = await sc.cutout(raw, name, base=base + span * 0.7, span=span * 0.3, **on)
    return {"raw": raw, "cutout": res["cutout"], "white": res["white"], "stats": res.get("stats")}


def _measure(cutout: Path, stats: dict[str, Any] | None, other_cutout: Path | None,
             hist_paths: dict[str, Path | None], ref_cutout: Path | None = None):
    """CPU part of a view's checks (no database access, so it runs in a worker thread): bbox stats, the outline's
    overlap with the partner view's (as drawn, mirrored), colour histograms for the views in `hist_paths`, and, given the
    picture's cut-out, the arm and leg reach of this view and of the picture (pose_held)."""
    if stats is None:
        stats = alpha_bbox(cutout)
    pair = pair_overlap(cutout, other_cutout) if other_cutout is not None else None
    hists = {v: (colour_histogram(p) if p else None) for v, p in hist_paths.items()}
    pose = (pose_extents(cutout), pose_extents(ref_cutout)) if ref_cutout is not None else None
    return stats, pair, hists, pose


def sheet_input(cutout: Path, dest: Path) -> Path:
    """The sheet LoRA's input: the concept cut-out on white, cropped to the subject and padded to SHEET_INPUT
    (turn_ab.py sheet_input)."""
    with Image.open(cutout) as im:
        im = im.convert("RGBA")
    a = np.asarray(im.getchannel("A")) > 127
    ys, xs = np.where(a)
    if not len(ys):
        raise ValueError("the concept cut-out has no subject in it")
    crop = im.crop((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
    W, H = SHEET_INPUT
    s = min(W * 0.9 / crop.width, H * 0.92 / crop.height)
    crop = crop.resize((max(1, int(crop.width * s)), max(1, int(crop.height * s))), Image.LANCZOS)
    bg = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    bg.alpha_composite(crop, ((W - crop.width) // 2, (H - crop.height) // 2))
    bg.convert("RGB").save(dest)
    return dest


def sheet_figures(cutout: Path) -> list[tuple[int, int]]:
    """The column ranges [x0, x1) of the figures in a sheet cut-out: runs of columns with subject in them, wider than
    SHEET_MIN_WIDTH (turn_ab.py split_sheet)."""
    with Image.open(cutout) as im:
        a = np.asarray(im.convert("RGBA").getchannel("A")) > 127
    col = a.sum(axis=0) > 2
    figs, x, n = [], 0, len(col)
    while x < n:
        if col[x]:
            x0 = x
            while x < n and col[x]:
                x += 1
            if x - x0 > SHEET_MIN_WIDTH:
                figs.append((x0, x))
        x += 1
    return figs


def _square(im: Image.Image, fill: tuple[int, ...]) -> Image.Image:
    side = max(im.width, im.height)
    bg = Image.new(im.mode, (side, side), fill)
    bg.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
    return bg.resize((VIEW_SIZE, VIEW_SIZE), Image.LANCZOS)


def split_sheet(raw: Path, cutout: Path, out_dir: Path) -> dict[str, dict[str, Path]] | None:
    """The sheet cut into one picture per view, in SHEET_ORDER: {view: {raw, cutout, white}}, each padded to a square
    of VIEW_SIZE like a standard view. None when the sheet does not hold exactly five figures."""
    figs = sheet_figures(cutout)
    if len(figs) != len(SHEET_ORDER):
        return None
    with Image.open(raw) as im:
        rgb = im.convert("RGB")
    with Image.open(cutout) as im:
        rgba = im.convert("RGBA").resize(rgb.size)
    out: dict[str, dict[str, Path]] = {}
    for view, (x0, x1) in zip(SHEET_ORDER, figs):
        box = (max(0, x0 - 10), 0, min(rgb.width, x1 + 10), rgb.height)
        r = out_dir / f"{view}_raw.png"
        c = out_dir / f"{view}.png"
        _square(rgb.crop(box), (255, 255, 255)).save(r)
        _square(rgba.crop(box), (0, 0, 0, 0)).save(c)
        out[view] = {"raw": r, "cutout": c, "white": white_composite(c, out_dir / f"{view}_white.png")}
    return out


# --- the pose-ready picture (docs/HANDS-PLAN.md, "Pose ready results") --------------------------------------------------

# One edit of the front picture before any angle is drawn, so every view and the 3D build agree about the hands. The
# concept version itself is never touched. Unlike the 3D step's hand_edit, a held object is removed on purpose.
POSE_READY = ("Redraw the character ready for 3D: each hand open with the back of the hand toward the camera, all five fingers "
              "straight, long and slightly spread apart with clear gaps between them, thumb out to the side; nothing held in "
              "either hand, so remove any cane, tool, bag or toy from the hands; no hand in a pocket, behind the body or hidden "
              "by clothing; arms hanging down and slightly away from the body in an A pose; standing upright with both feet "
              "on the ground. Gloves, gauntlets and claws keep their design but are open and spread.")
POSE_MOUTH = " Give the face a relaxed, closed mouth with the lips together; keep the same face, eyes, nose and features."
POSE_SUFFIX = (" Keep everything else exactly the same: the same character, face, hair, clothes, outfit design, colours, materials, "
               "body proportions, camera angle, size and framing, the whole body in frame, on a plain white background.")
POSE_KEYS = ("pose_ready_picture", "pose_ready_cutout")


def pose_ready_on(params: dict[str, Any], category: str | None) -> bool:
    """Whether the pose-ready edit applies: the param is on and the asset is a humanoid."""
    return bool(params.get("pose_ready")) and category == "humanoid"


def pose_ready_prompt(neutral_mouth: bool = False) -> str:
    return POSE_READY + (POSE_MOUTH if neutral_mouth else "") + POSE_SUFFIX


def _pose_parent_files(sc: StageContext, neutral_mouth: bool) -> dict[str, Path] | None:
    """The parent turnaround's pose-ready files, when it made them with the same mouth setting (a redraw of one view,
    an upload or an adjust keeps the picture the other views were drawn from)."""
    parent = sc.parent
    if parent is None or parent.stage != "turnaround" or not (parent.params or {}).get("pose_ready"):
        return None
    if bool((parent.params or {}).get("neutral_mouth")) != neutral_mouth:
        return None
    out = {k: library.version_file(parent, k, sc.settings) for k in POSE_KEYS}
    return {k: Path(p) for k, p in out.items() if p is not None and Path(p).is_file()} if all(out.values()) else None


async def _pose_ready_picture(sc: StageContext, params: dict[str, Any], src: Path) -> Path | None:
    """The concept picture edited once so the hands are open and apart (files pose_ready_picture, pose_ready_cutout);
    None when the param is off or the asset is not a humanoid. Reuses the parent's edit or this version's own (Resume)."""
    if not pose_ready_on(params, _category(sc)):
        return None
    mouth = bool(params.get("neutral_mouth"))
    own = {k: sc.file(k) for k in POSE_KEYS}
    if all(own.values()):
        return Path(own["pose_ready_picture"])
    kept = _pose_parent_files(sc, mouth)
    if kept:
        for k, p in kept.items():
            sc.register(k, sc.copy_in(p, p.name))
        sc.set_params(pose_ready=True, neutral_mouth=mouth)
        return Path(sc.file("pose_ready_picture"))
    from . import mesh  # the mesh step's picture-edit helpers (it does not import this module at load time)
    from ..imaging import image_size

    seed = random_seed()
    white = await asyncio.to_thread(white_composite, src, sc.dir / "pose_source.png")
    w, h = mesh.edit_size(*image_size(white))
    cards = mesh._picture_cards(sc)
    if not cards:
        raise RuntimeError("no graphics card can edit the picture")
    sc.use_model("qwen_edit")
    sc.use_model("qwen_lightning_lora")
    prompt = pose_ready_prompt(mouth)
    last: Exception | None = None
    for card in cards:
        try:
            name = await sc.stage_input(white, f"vgac_{sc.version.id[:8]}_pose_src.png", card)
            entry = await sc.comfy(edit_graph(name, prompt, seed, sc.prefix("pose"), w, h), instance=card, poll=1.0,
                                   message="Posing the character for 3D", base=0.0, span=0.04)
            edited = await sc.download_image(entry, "save", sc.dir / "pose_ready_picture.png", card)
            cut = await sc.cutout(Path(edited), "pose_ready_cutout", instance=card, base=0.04, span=0.01)
            break
        except Exception as e:  # noqa: BLE001  (try the next card; a cancelled job stops at once)
            if type(e).__name__ == "JobCancelled":
                raise
            last = e
    else:
        raise RuntimeError(f"the picture could not be posed: {last}")
    stats = cut.get("stats") or {}
    if stats.get("empty") or float(stats.get("coverage") or 0) < 0.02:
        raise RuntimeError("the posed picture came out without the character; run it again")
    sc.register("pose_ready_picture", edited)
    sc.register("pose_ready_cutout", cut["cutout"])
    sc.set_params(pose_ready=True, neutral_mouth=mouth, pose_seed=seed)
    sc.set_metrics(pose_ready={"seed": seed, "card": card, "size": [w, h], "neutral_mouth": mouth, "prompt": prompt,
                               "elapsed_seconds": entry.get("elapsed_seconds")})
    sc.publish()  # the redraw exists about 40 seconds in; show it now, not when the whole step ends (UI-18)
    return Path(sc.file("pose_ready_picture"))


async def _concept_cutout(sc: StageContext, source: Version | None, src: Path) -> Path:
    """The concept's own cut-out (the pose-ready one when this version made it), or a fresh one of its picture."""
    posed = sc.file("pose_ready_cutout")
    if posed is not None and Path(posed).is_file():
        return Path(posed)
    p = library.version_file(source, "cutout", sc.settings) if source is not None else None
    if p is not None:
        return Path(p)
    res = await sc.cutout(src, "concept_cut", base=0.02, span=0.03)
    return Path(res["cutout"])


async def _draw_sheet(sc: StageContext, source: Version | None, src: Path, seed: int) -> dict[str, dict[str, Path]] | None:
    """One sheet job and its split; None (and metrics.sheet says why) when it does not split into five figures."""
    cut = await _concept_cutout(sc, source, src)
    inp = await asyncio.to_thread(sheet_input, cut, sc.dir / "sheet_input.png")
    name = await sc.stage_input(inp, f"vgac_{sc.version.id[:8]}_sheet_input.png")
    sc.progress(0.05, "drawing every side in one picture")
    entry = await sc.comfy(sheet_graph(name, seed, sc.prefix(f"sheet_{seed}"), strength=METHOD_STRENGTH),
                           message="every side in one picture", base=0.05, span=0.5)
    raw = await sc.download_image(entry, "save", sc.dir / "sheet_raw.png")
    res = await sc.cutout(raw, "sheet", base=0.55, span=0.05)
    sc.register("sheet", raw)
    views = await asyncio.to_thread(split_sheet, Path(raw), Path(res["cutout"]), sc.dir)
    figs = len(await asyncio.to_thread(sheet_figures, Path(res["cutout"])))
    sc.set_metrics(sheet={"seed": seed, "figures": figs, "split": views is not None})
    return views


async def _camera_source(sc: StageContext, source: Version | None, src: Path) -> dict[str, Path]:
    """The shadow-free turn's input and front view: the concept's cut-out on white (the concept is the front)."""
    cut = await _concept_cutout(sc, source, src)
    c = sc.copy_in(cut, "front.png")
    w = await asyncio.to_thread(white_composite, c, sc.dir / "front_white.png")
    raw = sc.copy_in(w, "front_raw.png")
    return {"raw": raw, "cutout": c, "white": w}


async def _camera_view(sc: StageContext, src_name: str, view: str, seed: int, extra: str, base: float, span: float) -> dict[str, Path]:
    graph = camera_graph(src_name, view, seed, sc.prefix(f"{view}_{seed}"), strength=METHOD_STRENGTH, extra=extra)
    entry = await sc.comfy(graph, message=f"view {view}", base=base, span=span * 0.7)
    raw = await sc.download_image(entry, "save", sc.dir / f"{view}_raw.png")
    res = await sc.cutout(raw, view, base=base + span * 0.7, span=span * 0.3)
    return {"raw": raw, "cutout": res["cutout"], "white": res["white"], "stats": res.get("stats")}


def supplied_files(sc: StageContext, views: list[str] | None = None) -> dict[str, Path]:
    """The person's own pictures on this version (files supplied_<view>, written by the API)."""
    out: dict[str, Path] = {}
    for v in views or list(reference.TURNAROUND_VIEWS):
        p = sc.file(f"supplied_{v}")
        if p is not None:
            out[v] = p
    return out


# Two cards (docs/AFTER-TEST-CHANGES.md A3, A5, D2 items 17 and 19). When the job's queue lends a second card that is
# free (jobs.JobQueue.lend: on, allowed to draw pictures, idle, nothing waiting, engine running, memory for the
# drawing model), the views are drawn in this order: the front; then the left side (quarter, then left) on the job's
# own card and the right side (quarter2, then right) on the lent card at the same time; then the back, on the job's
# card once both sides are done, after the lent card is handed back. Each view keeps its seed (the base seed plus its
# place in the usual order), prompt and settings, so the same seeds give the same pictures as one after another (on
# the same kind of card). A redraw stays on its side's card. The pair checks run on the right side's view once its
# left partner is finished, as one after another runs them on the later view of each pair. When the lent card's
# engine stops answering, or the person turns it off, the rest of the right side is drawn on the job's card after the
# left side; when the job is stopped the lent card is interrupted and handed back. No second card free: one after
# another on one card, in the usual order, exactly as before. The sheet and the shadow-free turn always run one after
# another: the sheet draws every side in one picture, and the shadow-free turn is for rigid props, whose four views
# are drawn by another model add-on that was never tried on the second card.
LEFT_SIDE = ("quarter", "left")
RIGHT_SIDE = (SECOND_QUARTER, "right")


def _home_card(sc: StageContext) -> str | None:
    """The card the step's job runs on (None in tests without a job)."""
    return getattr(getattr(sc, "ctx", None), "instance", None)


def _gpu(sc: StageContext, card: str | None) -> str:
    """The card's plain name ("RTX 3080") for messages."""
    settings = getattr(sc, "settings", None)
    if card and settings is not None and card in getattr(settings, "instances", {}):
        from ..jobs import gpu_name
        return gpu_name(card, settings)
    return f"graphics card {card}" if card else "graphics card"


async def _borrow_for_sides(sc: StageContext, method: str, left: list[str], right: list[str]) -> tuple[Any, str]:
    """(the lent card's Lease, "") when the two sides can be drawn at once on two cards, else (None, why not)."""
    if not left or not right:
        return None, "only one side of the figure is drawn in this step"
    if method == "sheet":
        return None, "the turnaround sheet draws every side in one picture"
    if method != "standard":
        return None, "the shadow-free turn draws its views one after another"
    borrow = getattr(getattr(sc, "ctx", None), "borrow", None)
    if borrow is None:
        return None, "this step cannot ask for a second card"
    return await borrow()


async def _both_sides(sc: StageContext, lease: Any, left: list[str], right: list[str], do_view) -> str | None:
    """Draw the left side on the job's card and the right side on the lent card at the same time, and hand the lent
    card back. Returns a plain note when the right side had to be finished on the job's card, else None. A failure on
    either side stops the other and is raised; stopping the job stops both (the caller interrupts the lent card)."""
    from ..jobs import is_engine_lost

    notes: list[str] = []

    async def left_side() -> None:
        for v in left:
            await do_view(v, None, "skip")

    left_task = asyncio.create_task(left_side())

    async def right_side() -> None:
        for v in right:
            if not lease.ended and not lease.revoked:
                try:
                    await do_view(v, lease.instance, "wait")
                    continue
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    if not (lease.revoked or is_engine_lost(e)):
                        raise
                    why = lease.revoked_reason or f"the graphics engine on {lease.gpu} stopped answering"
                    notes.append(f"{why}, so the right side was finished on the {_gpu(sc, _home_card(sc))}")
                    await lease.end()
            elif not lease.ended:
                notes.append(f"{lease.revoked_reason}, so the right side was finished on the {_gpu(sc, _home_card(sc))}")
                await lease.end()
            # The job's card is free once the left side is done (waiting does not stop the left side).
            await asyncio.wait({left_task})
            if left_task.cancelled() or left_task.exception() is not None:
                return   # the left side failed: that failure is the one reported
            await do_view(v, None, "wait")

    right_task = asyncio.create_task(right_side())
    tasks = {left_task, right_task}
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        failed = [t for t in (left_task, right_task) if t.done() and not t.cancelled() and t.exception() is not None]
        if failed:
            raise failed[0].exception()  # type: ignore[misc]
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.wait(tasks)
        for t in tasks:
            if not t.cancelled():
                t.exception()  # retrieved, so a second failure is not reported as never retrieved
    await lease.end()
    return "; ".join(notes) or None


async def _supplied_view(sc: StageContext, view: str, path: Path, base: float, span: float,
                         instance: str | None = None) -> dict[str, Any]:
    """A view the person supplied: kept as it is (copied as the view picture) and cut out like a drawn one."""
    raw = sc.copy_in(path, f"{view}_raw.png")
    res = await sc.cutout(raw, view, base=base, span=span, **({"instance": instance} if instance else {}))
    return {"raw": raw, "cutout": res["cutout"], "white": res["white"], "stats": res.get("stats")}


async def _run_views(sc: StageContext, views: list[str], params: dict[str, Any], source: Version | None,
                     keep: dict[str, dict[str, Any]] | None = None, extra: str = "",
                     supplied: dict[str, Path] | None = None, method: str | None = None) -> None:
    """Generate and check `views` from the concept image of `source`. Views in `supplied` are the person's own
    pictures: cut out and checked like the rest, never redrawn; one that fails a check warns and is kept. `method`
    (default: the params' turn_method) says how the views are turned (TURN_METHODS)."""
    supplied = dict(supplied or {})
    chosen_method = turn_method(_category(sc), params)
    method = method or chosen_method
    src = _source_image(sc, source)
    posed = await _pose_ready_picture(sc, params, src)
    if posed is not None:
        src = posed  # every drawn view starts from the pose-ready picture; the concept stays as it is
    src_name = await sc.stage_input(src, f"vgac_{sc.version.id[:8]}_concept.png")
    rec = stage_recipe(sc)
    # The camera-angle helper's strength: the version's, else the recipe's (the add-on's normal step, 0.9, unless the
    # look sets its own). The recipe's prompt words go after the view words, before any refine instruction.
    chosen = params.get("lora_strength")
    lora_strength = float(chosen) if chosen is not None else float(rec["turnaround"]["lora_strength"])
    suffix = rec["turnaround"].get("prompt_suffix") or ""
    instruction = (extra or "").strip()  # a described change; the camera turn gets it without the look's words
    extra = " ".join(p for p in (suffix, extra) if p and p.strip())
    auto_reroll = bool(params.get("auto_reroll", True))
    base_seed = params.get("seed")
    sc.use_model("qwen_edit")
    sc.use_model("qwen_lightning_lora")

    results: dict[str, dict[str, Any]] = dict(keep or {})
    order = ["front"] + [v for v in views if v != "front"] if "front" in views or "front" in results else list(views)
    todo = [v for v in order if v in views]
    n = max(1, len(todo))
    hist_cache: dict[str, Any] = {}
    drawn = [v for v in todo if v not in supplied]
    used: set[str] = set()

    # The sheet draws every view it can in one job before the loop; the shadow-free turn starts from the concept on
    # white, which is also its front view. The recipe's prompt words are for the standard method's view prompts.
    sheet: dict[str, dict[str, Path]] | None = None
    cam: dict[str, Any] | None = None
    if method == "sheet" and any(method_draws("sheet", v) for v in drawn):
        sheet_seed = int(base_seed) if base_seed else random_seed()
        sc.use_model("qwen_sheet_lora")
        used.add("sheet")
        sheet = await _draw_sheet(sc, source, src, sheet_seed)
    elif method == "shadow_free" and any(method_draws("shadow_free", v) for v in drawn):
        sc.use_model("qwen_camera_lora")
        used.add("shadow_free")
        front = await _camera_source(sc, source, src)
        cam = {"front": front, "name": await sc.stage_input(front["white"], f"vgac_{sc.version.id[:8]}_concept_white.png")}
    # The sheet's figures fill the rest of the bar quickly; the per-view jobs share it otherwise.
    lo, width = (0.6, 0.35) if sheet else (0.05, 0.9)

    # The picture's cut-out the front and back views' arm and leg reach is held to (pose_held); humanoids only.
    pose_ref: Path | None = None
    if _category(sc) == "humanoid" and any(v in POSE_VIEWS for v in todo):
        posed_cut = sc.file("pose_ready_cutout")
        stored = Path(posed_cut) if posed_cut is not None and Path(posed_cut).is_file() else (
            library.version_file(source, "cutout", sc.settings) if source is not None else None)
        pose_ref = Path(stored) if stored is not None and Path(stored).is_file() else None   # no cut-out yet: no check, no new job

    # The cards (see LEFT_SIDE): the job's own card draws everything, unless a second card is lent for the right side.
    home = _home_card(sc)
    src_names: dict[str | None, str] = {None: src_name}

    async def draw(view: str, seed: int, attempts: int, at: float, card: str | None = None) -> tuple[dict[str, Any], str]:
        if sheet is not None and attempts == 0 and view in sheet:
            return dict(sheet[view]), "sheet"
        if cam is not None and method_draws("shadow_free", view):
            if view == "front":
                return dict(cam["front"]), "shadow_free"
            return await _camera_view(sc, cam["name"], view, seed, instruction, at, width / n), "shadow_free"
        sc.use_model("qwen_angles_lora")
        used.add("standard")
        if card is None:
            return await _generate_view(sc, src_name, view, seed, lora_strength, extra, at, width / n), "standard"
        if card not in src_names:
            # Its own copy of the start picture, so the job's card never reads a file while it is being copied.
            src_names[card] = await sc.stage_input(src, f"vgac_{sc.version.id[:8]}_concept_{card}.png", instance=card)
        return await _generate_view(sc, src_names[card], view, seed, lora_strength, extra, at, width / n,
                                    instance=card), "standard"

    finished = [0]   # views done, for the progress bar when two cards draw at once
    parallel = [False]
    index = {v: i for i, v in enumerate(todo)}
    view_done: dict[str, asyncio.Event] = {v: asyncio.Event() for v in todo}

    async def do_view(view: str, card: str | None = None, partner: str = "check") -> None:
        """Draw (or cut out) one view, check it and redraw it on the same card until it passes or runs out of
        redraws. card: a lent card (None: the job's own). partner: "check" compares it with its partner view as today;
        "skip" leaves that to the partner (two cards: the left side's views are drawn while their partners are);
        "wait" waits until the partner is finished first (the right side's views)."""
        i = index[view]
        attempts = 0
        seed = int(base_seed) + i if base_seed else random_seed()
        where = f" on the {_gpu(sc, card or home)}" if parallel[0] else ""
        while True:
            at = lo + width * (finished[0] if parallel[0] else i) / n
            drew = "supplied"
            if view in supplied:
                sc.progress(at, f"your {view} view{where}")
                files = await _supplied_view(sc, view, supplied[view], at, width / n, instance=card)
            else:
                sc.progress(at, f"view {view} attempt {attempts + 1}{where}")
                files, drew = await draw(view, seed, attempts, at, card)
            sc.register(view, files["raw"])
            sc.register(f"{view}_cutout", files["cutout"])
            sc.register(f"{view}_white", files["white"])
            hist_cache.pop(view, None)
            front_stats = results.get("front", {}).get("bbox") if view != "front" else None
            other = PARTNER.get(view)
            if partner == "wait" and other in view_done:
                await view_done[other].wait()
            # File lookups read the version row, so they stay on the event loop; the image work goes to a thread.
            other_cutout = sc.file(f"{other}_cutout") if other and partner != "skip" else None
            front_cutout = sc.file("front_cutout") if view != "front" else None
            want: dict[str, Path | None] = {view: sc.file(f"{view}_cutout")}
            if front_cutout and "front" not in hist_cache:
                want["front"] = front_cutout
            stats, pair, new_hists, pose = await asyncio.to_thread(_measure, files["cutout"], files.get("stats"), other_cutout, want,
                                                                    pose_ref if view in POSE_VIEWS else None)
            hist_cache.update(new_hists)
            as_drawn, mirror = pair if pair is not None else (None, None)
            info = view_checks(view, stats, front_stats, hist_cache["front"] if front_cutout else None, hist_cache[view], mirror,
                               as_drawn, pose)
            info["seed"] = None if view in supplied or (drew == "shadow_free" and view == "front") else seed
            info["rerolls"] = attempts
            info["method"] = drew
            if card or home:
                info["card"] = card or home
            if view in supplied:
                info["supplied"] = True
            results[view] = info
            if other in results and mirror is not None and view in MIRROR_PAIRS:
                results[other]["mirror_iou"] = round(mirror, 4)
            if view in supplied or info["passed"] or not auto_reroll or attempts >= MAX_REROLLS:
                info["flagged"] = not info["passed"]
                break
            attempts += 1
            seed = random_seed()
        finished[0] += 1
        view_done[view].set()
        _publish_views(sc, results)

    left = [v for v in LEFT_SIDE if v in todo]
    right = [v for v in RIGHT_SIDE if v in todo]
    lease, why = await _borrow_for_sides(sc, method, left, right)
    cards: dict[str, Any] = {"mode": "one after another", "why": why, "card": home}
    try:
        if lease is None:
            for view in todo:
                await do_view(view)
        else:
            cards = {"mode": "two cards", "card": home, "second_card": lease.instance}
            parallel[0] = True
            for view in [v for v in todo if v == "front"]:
                await do_view(view)
            sc.progress(lo + width * finished[0] / n, f"drawing the left side on the {_gpu(sc, home)} and the right side "
                                                      f"on the {lease.gpu} at the same time")
            fallback = await _both_sides(sc, lease, left, right, do_view)
            if fallback:
                cards["fallback"] = fallback
            for view in [v for v in todo if v != "front" and v not in left and v not in right]:
                await do_view(view)   # the back (and any other view) after both sides
    finally:
        if lease is not None and not lease.ended:
            await lease.end(interrupt=True)   # stopped or failed part-way: stop what is left on the lent card
        sc.set_metrics(cards=cards)
    sc.set_params(views=list(views), lora_strength=lora_strength, auto_reroll=auto_reroll, seed=base_seed,
                  **({"pose_ready": True} if posed is not None else {}),
                  lora_strength_source="chosen" if chosen is not None else "recipe", turn_method=chosen_method,
                  supplied_views=sorted({*(params.get("supplied_views") or []), *supplied}))
    # The LoRAs behind every view this version holds, the ones copied from the parent included (a view from before
    # turn_method was drawn the standard way).
    behind = {r.get("method") or "standard" for r in results.values() if not r.get("supplied")} | used
    addons = [{"id": "qwen_lightning", "value": 1.0, "required": True}]
    for m in ("standard", "sheet", "shadow_free"):
        if m in behind:
            sc.use_model(METHOD_ADDON[m][1])
            addons.append({"id": METHOD_ADDON[m][0], "value": lora_strength if m == "standard" else METHOD_STRENGTH,
                           "required": m == "standard"})
    sc.set_metrics(recipe=reference.recipe_record(rec, "turnaround"), addons=addons, turn_method=chosen_method)
    _publish_views(sc, results)


def _publish_views(sc: StageContext, results: dict[str, dict[str, Any]]) -> None:
    sc.set_metrics(views=results, views_total=len(results), views_passed=sum(1 for r in results.values() if r.get("passed")),
                   flagged_views=[v for v, r in results.items() if r.get("flagged")],
                   supplied_views=[v for v, r in results.items() if r.get("supplied")],
                   consistency_ok=not any(r.get("flagged") for r in results.values()))


async def run(sc: StageContext, params: dict[str, Any]) -> None:
    views = with_second_quarter(list(params.get("views") or sc.category.get("views") or DEFAULT_VIEWS), _category(sc), params)
    supplied = supplied_files(sc)
    views += [v for v in supplied if v not in views]
    bald = _bald_on(sc, params)
    if bald:
        _start_bald(sc, params)
    await _run_views(sc, views, params, sc.parent, supplied=supplied)
    if bald:
        await _run_bald(sc, sc.parent, params)


async def upload(sc: StageContext, params: dict[str, Any], view: str | None = None) -> None:
    """POST /api/versions/{vid}/views/{view}/upload: a new turnaround version with the person's own picture for one
    view (files supplied_<view>) and every other view copied forward from the parent turnaround."""
    parent = sc.parent
    if parent is None or parent.stage != "turnaround":
        raise ValueError("your own view needs a turnaround version to add it to")
    if not view:
        raise ValueError("which view the picture is for was not given")
    path = sc.file(f"supplied_{view}")
    if path is None:
        raise FileNotFoundError("your picture for this view is missing")
    keep: dict[str, dict[str, Any]] = {}
    for v, info in (parent.metrics.get("views") or {}).items():
        if v == view:
            continue
        for key in (v, f"{v}_cutout", f"{v}_white", f"supplied_{v}"):
            p = library.version_file(parent, key, sc.settings)
            if p is not None:
                sc.register(key, sc.copy_in(p, p.name))
        keep[v] = info
    views = list(parent.params.get("views") or DEFAULT_VIEWS)
    if view not in views:
        views.append(view)
    merged = {**inherit(parent.params, params), "views": views, "supplied_of": parent.id,
              "supplied_views": sorted({*(parent.params.get("supplied_views") or []), view})}
    sc.set_params(**merged)
    sc.set_metrics(views=keep)
    _copy_bald(sc, parent)
    grand = concept_of(sc, parent)
    if grand is None:
        raise ValueError("turnaround parent has no concept parent")
    await _run_views(sc, [view], merged, grand, keep=keep, supplied={view: path})
    sc.set_params(views=views)


def turnaround_views(v: Any) -> list[str]:
    """Every view a turnaround version has: the ones its settings list plus any it holds a result for. (A redraw made
    before 2026-11-06 left only the redrawn view in the settings, so the results are read too.)"""
    listed = list((v.params or {}).get("views") or DEFAULT_VIEWS)
    extra = [x for x in ((v.metrics or {}).get("views") or {}) if x not in listed]
    if not extra:
        return listed
    known = list(reference.TURNAROUND_VIEWS)
    return sorted(listed + extra, key=lambda x: known.index(x) if x in known else len(known))


async def refine(sc: StageContext, instruction: str, view: str | None, mask_png: Path | None, params: dict[str, Any],
                 views: list[str] | None = None) -> None:
    """Redraw one view (`view`) or several together (`views`) in one new version; every other view is copied
    forward from the parent, so the new version is complete and can be redrawn from again."""
    parent = sc.parent
    if parent is None or parent.stage != "turnaround":
        raise ValueError("turnaround refine needs a turnaround parent version")
    targets = list(dict.fromkeys(views or [view or "front"]))
    all_views = turnaround_views(parent)
    for t in targets:
        if t not in all_views:
            raise ValueError(f"view {t!r} is not part of the parent turnaround")
    # Copy every other view forward so the new version is complete.
    keep: dict[str, dict[str, Any]] = {}
    for v, info in (parent.metrics.get("views") or {}).items():
        if v in targets:
            continue
        for key in (v, f"{v}_cutout", f"{v}_white"):
            p = library.version_file(parent, key, sc.settings)
            if p is not None:
                sc.register(key, sc.copy_in(p, p.name))
        keep[v] = info
    # A view the person supplied and now has redrawn is no longer their own picture.
    still_supplied = [v for v in (parent.params.get("supplied_views") or []) if v not in targets]
    merged = {**inherit(parent.params, params), "seed": params.get("seed"), "instruction": instruction, "refine_of": parent.id,
              "refined_view": targets[0], "refined_views": targets, "views": all_views, "supplied_views": still_supplied}
    sc.set_params(**merged)
    sc.set_metrics(views=keep)
    _copy_bald(sc, parent)
    # The concept source is the turnaround parent's parent.
    grand = concept_of(sc, parent)
    if grand is None:
        raise ValueError("turnaround parent has no concept parent")
    # The sheet draws all five sides at once, so a redraw of some of them uses the standard turn.
    method = "standard" if turn_method(_category(sc), merged) == "sheet" else None
    await _run_views(sc, targets, merged, grand, keep=keep, extra=instruction, method=method)
    # _run_views records the views it drew; the version holds all of them (as upload does).
    sc.set_params(views=all_views)


def inherit(parent_params: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Parent params under the new ones, without a camera-angle strength the parent took from the recipe."""
    base = dict(parent_params or {})
    if base.pop("lora_strength_source", None) == "recipe" and "lora_strength" not in params:
        base.pop("lora_strength", None)
    if "bald_only" not in params:
        base.pop("bald_only", None)  # the marker belongs to the adjust that asked for it
    return {**base, **params}


async def adjust(sc: StageContext, params: dict[str, Any]) -> None:
    parent = sc.parent
    merged = inherit(parent.params if parent else {}, params)
    if not params.get("bald_only"):
        merged.pop("bald_only", None)  # the marker belongs to the adjust that asked for it
    views = list(merged.get("views") or DEFAULT_VIEWS)
    if parent is not None and parent.stage == "turnaround":
        # The concept source is the turnaround parent's parent; this version's own parent stays the turnaround.
        source = concept_of(sc, parent)
    else:
        source = parent
    if merged.get("bald_only"):
        await _bald_only(sc, parent, source, merged)
        return
    bald = _bald_on(sc, merged)
    if bald:
        _start_bald(sc, merged)
    await _run_views(sc, views, merged, source)
    if bald:
        await _run_bald(sc, source, merged)


# --- the bald pictures (hair from the library, docs/HAIR-LIBRARY-PLAN.md section 2) ----------------------------------

# The wig trial's bald edit, word for word (runtime/tmp/wigtrial/edit.py PROMPTS["bald"], clean on 5 of 5 characters).
# A sentence about keeping a beard is added only when the description has one: said to a beardless character, the word
# drew a beard on her (the live run of 11-06: both women came back as bald bearded men).
BALD_PROMPT = ("Make this character completely bald: remove all of the hair, including the fringe, any ponytail and any "
               "loose locks, and show a smooth, clean, hairless bald scalp with a natural rounded skull shape and both ears "
               "visible. Keep the face, eyes, expression, ears, skin tone, neck, outfit, pose, proportions, framing, art "
               "style, lighting and background exactly the same.")
BALD_BEARD_SENTENCE = " Keep the beard and moustache exactly as they are."
BEARD_WORDS = ("beard", "bearded", "moustache", "mustache", "moustached", "goatee", "stubble", "sideburns", "whiskers")


def has_facial_hair(description: str | None) -> bool:
    """The description names a beard, moustache or stubble (BEARD_WORDS)."""
    from .. import hairpic

    return hairpic.any_of(hairpic.norm(description), list(BEARD_WORDS))


def bald_prompt(description: str | None) -> str:
    """The bald edit's prompt: the trial's, plus the beard sentence only when the description names facial hair."""
    return BALD_PROMPT + (BALD_BEARD_SENTENCE if has_facial_hair(description) else "")
# quarter is the 3D input; the other three-quarter view, left and right are for the face pass. No back.
BALD_SIDE_VIEWS = ("quarter", "quarter2", "left", "right")
BALD_KEPT_MIN = 0.9  # bald_picture: share of the picture outside the hair that must stay unchanged
BALD_FACE_DELTA_MAX = 10.0  # over this the face changed (the measure's own units, from baldpic.kept_measure)
BALD_NO_HAIR_SHARE = 0.002  # a hair outline under this share of the picture: no hair was taken away
BALD_REDRAWS = 1  # one automatic redraw with bald_seed + 1 when the check fails; the better one is kept
BALD_FILE_KEYS = ("hair_outline_front", "hair_picture", "bald_sheet")
BALD_METRIC_KEYS = ("bald", "checks", "checks_passed", "checks_failed", "gate_ok")


def _bald_on(sc: StageContext, params: dict[str, Any]) -> bool:
    """Draw the bald pictures: the version says so (bald_views), else the asset's hair setting is the library."""
    if isinstance(params.get("bald_views"), bool):
        return params["bald_views"]
    return hairlib.wants_bald(getattr(sc, "asset", None))


def _start_bald(sc: StageContext, params: dict[str, Any]) -> int:
    """Pick the bald seed and write it at once (the picked_seed pattern), so a Stop and Resume draws the same picture."""
    seed = params.get("bald_seed")
    seed = int(seed) if seed not in (None, "") else random_seed()
    sc.set_params(bald_views=True, bald_seed=seed)
    return seed


def _copy_bald(sc: StageContext, parent: Version) -> None:
    """refine and upload: every bald picture and its record go forward unchanged (nothing when there are none)."""
    files = getattr(parent, "files", None) or {}
    keys = [k for k in files if k.startswith("bald_") or k in BALD_FILE_KEYS]
    if not keys:
        return
    for key in keys:
        p = library.version_file(parent, key, sc.settings)
        if p is not None:
            sc.register(key, sc.copy_in(p, p.name))
    sc.set_metrics(**{k: v for k, v in (parent.metrics or {}).items() if k in BALD_METRIC_KEYS})


async def _bald_only(sc: StageContext, parent: Version | None, source: Version | None, params: dict[str, Any]) -> None:
    """The bald_only adjust: every view is copied from the parent turnaround and only the bald pictures are drawn."""
    if parent is None or parent.stage != "turnaround":
        raise ValueError("the bald pictures need a turnaround version to add them to")
    keep: dict[str, dict[str, Any]] = {}
    for v, info in (parent.metrics.get("views") or {}).items():
        for key in (v, f"{v}_cutout", f"{v}_white", f"supplied_{v}"):
            p = library.version_file(parent, key, sc.settings)
            if p is not None:
                sc.register(key, sc.copy_in(p, p.name))
        keep[v] = info
    sc.set_params(**{**params, "bald_only": True})
    sc.set_metrics(**{k: v for k, v in (parent.metrics or {}).items() if k not in BALD_METRIC_KEYS and k != "models"})
    _publish_views(sc, keep)
    _start_bald(sc, params)
    await _run_bald(sc, source, params)


def _edit_size(w: int, h: int) -> tuple[int, int]:
    """About a megapixel in the concept's own proportions, in steps of 16 (the edit model's latent)."""
    s = math.sqrt(1024 * 1024 / max(1, w * h))
    return max(256, int(round(w * s / 16)) * 16), max(256, int(round(h * s / 16)) * 16)


def _rgba(image: Path, alpha_from: Path | None, size: tuple[int, int]) -> np.ndarray:
    with Image.open(image) as im:
        a = np.asarray(im.convert("RGB").resize(size, Image.LANCZOS))
    if alpha_from is not None and Path(alpha_from).is_file():
        with Image.open(alpha_from) as im:
            al = np.asarray(im.convert("RGBA").resize(size, Image.LANCZOS))[..., 3]
    else:
        al = np.full(a.shape[:2], 255, np.uint8)
    return np.dstack([a, al])


def _target(bald_alpha: np.ndarray) -> dict[str, Any]:
    """The picture-side measures of the trial's fitprep.py on the bald cut-out: the figure's height and top, the neck
    (narrowest row 8 to 25 percent below the top), the head's centre, width and height (as shares of the height)."""
    cut = np.asarray(bald_alpha) > 127
    ys, xs = np.where(cut)
    if not len(ys):
        raise ValueError("the bald picture has no figure in it")
    top, bot = int(ys.min()), int(ys.max())
    H = max(1, bot - top)
    lo, hi = int(0.08 * H), max(int(0.08 * H) + 1, int(0.25 * H))
    widths = np.array([cut[y].sum() for y in range(top, min(cut.shape[0], top + hi))])
    neck = top + lo + int(np.argmin(widths[lo:hi])) if len(widths) > lo else top + hi
    rows = cut[top:neck]
    hx = np.where(rows)[1]
    head_cx = float(np.median(hx)) if len(hx) else float(np.median(xs))
    sums = [int(r.sum()) for r in rows if r.sum()]
    head_w = float(np.percentile(sums, 90)) if sums else 0.0
    return {"H_px": int(H), "top_px": top, "neck_px": int(neck), "head_cx_px": head_cx, "head_w": head_w / H,
            "head_h": (neck - top) / H, "img_w": int(cut.shape[1]), "img_h": int(cut.shape[0])}


def _eye_cover(mask: np.ndarray, boxes: Any) -> float | None:
    """The share of the eye boxes ([x0, y0, x1, y1] in pixels) the hair covers in the original picture."""
    total, covered = 0, 0
    for b in boxes or []:
        try:
            x0, y0, x1, y1 = (int(round(float(v))) for v in list(b)[:4])
        except (TypeError, ValueError):
            continue
        part = mask[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
        total += part.size
        covered += int(part.sum())
    return round(covered / total, 4) if total else None


def _bald_measure(orig_img: Path, orig_cut: Path | None, bald_raw: Path, bald_cut: Path, size: tuple[int, int],
                  bearded: bool = False) -> dict[str, Any]:
    """The bald front against the concept (CPU work, run in a thread): hair outline, kept share, face change, reason."""
    from .. import baldpic  # lane 1

    orig = _rgba(orig_img, orig_cut, size)
    bald = _rgba(bald_raw, bald_cut, size)
    mask = np.asarray(baldpic.outline_mask(orig, bald)).astype(bool)
    kept = dict(baldpic.kept_measure(orig, bald, mask) or {})
    share = float(kept.get("kept_share") or 0.0)
    delta = kept.get("face_delta")
    target = baldpic.measure_target(bald) if hasattr(baldpic, "measure_target") else _target(bald[..., 3])
    fm = face_measure(orig, bald, mask, target, bearded=bearded)
    reason = kept.get("reason")
    if reason is None:
        if mask.mean() < BALD_NO_HAIR_SHARE:
            reason = "no_hair_removed"
        elif not face_kept(fm):
            reason = "face_changed"  # a beard or another face drawn: kept_share alone did not see it
        elif share >= BALD_KEPT_MIN:
            reason = None
        elif delta is not None and float(delta) > BALD_FACE_DELTA_MAX:
            reason = "face_changed"
        else:
            reason = "clothes_changed"
    return {"mask": mask, "kept_share": round(share, 4), "face_delta": None if delta is None else round(float(delta), 3),
            "face_dark": fm["face_dark"], "face_change": fm["face_change"], "face_px": fm["face_px"],
            "beard_change": fm.get("beard_change"), "bearded": bearded,
            "reason": reason, "ok": reason is None, "orig": orig, "bald": bald, "hair_px": int(mask.sum())}


def _jsonable(o: Any) -> Any:
    return o.tolist() if hasattr(o, "tolist") else float(o)


def _bald_record(meas: dict[str, Any], out_dir: Path, size: tuple[int, int]) -> dict[str, Any]:
    """hair_outline_front (L at the concept's size) and hair_picture (JSON) from the kept bald front's measures."""
    from .. import baldpic  # lane 1

    mask = meas["mask"]
    if hasattr(baldpic, "picture_record"):
        # Lane 1's port of the trial's fitprep.py and prep_pic.py: target (with the hair's extent), eyes, colour.
        mask, rec = baldpic.picture_record(meas["orig"], meas["bald"], meas["bald"][..., :3])
        mask = np.asarray(mask).astype(bool)
        rec = {**rec, "size": list(size)}
    else:
        target = baldpic.target(meas["bald"]) if hasattr(baldpic, "target") else _target(meas["bald"][..., 3])
        boxes = baldpic.eye_boxes(meas["bald"], target)
        boxes = boxes.tolist() if hasattr(boxes, "tolist") else boxes
        rec = {"target": target, "eye_boxes": boxes, "pic_eye_cover": _eye_cover(mask, boxes),
               "colour": baldpic.hair_colour(meas["orig"], mask), "outline_px": int(mask.sum()), "size": list(size)}
    outline = out_dir / "hair_outline_front.png"
    Image.fromarray((mask * 255).astype(np.uint8), "L").resize(size).save(outline)
    pic = out_dir / "hair_picture.json"
    pic.write_text(json.dumps(rec, default=_jsonable), encoding="utf-8")
    return {"outline": outline, "picture": pic, "record": json.loads(pic.read_text(encoding="utf-8"))}


def _resize_to(path: Path, size: tuple[int, int]) -> None:
    with Image.open(path) as im:
        out = im.convert("RGB").resize(size, Image.LANCZOS)
    out.save(path)


async def _bald_front(sc: StageContext, src_name: str, seed: int, n: int, size: tuple[int, int]) -> dict[str, Path]:
    """One bald edit of the concept, resized to the concept's size and cut out."""
    ew, eh = _edit_size(*size)
    graph = edit_graph(src_name, bald_prompt(getattr(sc.asset, "prompt", "")), seed, sc.prefix(f"bald_front_{seed}"), width=ew, height=eh)
    base = 0.95 + 0.01 * n
    entry = await sc.comfy(graph, message="bald picture (the character without hair)", base=base, span=0.005)
    raw = Path(await sc.download_image(entry, "save", sc.dir / f"bald_front_try{n + 1}_raw.png"))
    await asyncio.to_thread(_resize_to, raw, size)
    res = await sc.cutout(raw, f"bald_front_try{n + 1}", base=base + 0.005, span=0.005)
    return {"raw": raw, "cutout": Path(res["cutout"]), "white": Path(res["white"])}


async def _run_bald(sc: StageContext, source: Version | None, params: dict[str, Any]) -> None:
    """Hair from the library: the bald front edited from the concept (checked with bald_picture, one redraw with
    bald_seed + 1 when it fails, the better one kept), then bald quarter, left and right drawn from it, each with the
    seed its haired view used. Files bald_<view>, bald_<view>_cutout, bald_<view>_white, hair_outline_front,
    hair_picture and bald_sheet; metrics.bald (never inside metrics.views) and the bald_picture check."""
    t0 = time.time()
    src = _source_image(sc, source)
    orig_cut = library.version_file(source, "cutout", sc.settings) if source is not None else None
    if sc.file("pose_ready_picture") is not None and sc.file("pose_ready_cutout") is not None:
        # The bald picture is edited from the pose-ready one, and its unchanged-share is measured against that picture.
        src, orig_cut = Path(sc.file("pose_ready_picture")), Path(sc.file("pose_ready_cutout"))
    seed = sc.params.get("bald_seed")
    seed = int(seed) if seed not in (None, "") else _start_bald(sc, params)
    with Image.open(src) as im:
        size = im.size
    src_name = await sc.stage_input(src, f"vgac_{sc.version.id[:8]}_bald_concept.png")
    sc.use_model("qwen_edit")
    sc.use_model("qwen_lightning_lora")
    tries: list[dict[str, Any]] = []
    for n in range(1 + BALD_REDRAWS):
        sc.progress(0.95, "Drawing the bald picture (the character without hair)" + (", second try" if n else ""))
        files = await _bald_front(sc, src_name, seed + n, n, size)
        try:
            meas = await asyncio.to_thread(_bald_measure, src, orig_cut, files["raw"], files["cutout"], size,
                                           has_facial_hair(getattr(sc.asset, "prompt", "")))
        except Exception as e:  # noqa: BLE001  (the measure failed: the picture is kept and the check says so)
            meas = {"mask": None, "kept_share": None, "face_delta": None, "reason": "error", "ok": False, "error": str(e)[:300]}
        tries.append({"seed": seed + n, "files": files, "meas": meas})
        if meas["ok"]:
            break
    best = max(range(len(tries)), key=lambda i: _try_rank(tries[i]["meas"]))
    kept = tries[best]
    sc.register("bald_front", kept["files"]["raw"])
    sc.register("bald_front_cutout", kept["files"]["cutout"])
    sc.register("bald_front_white", kept["files"]["white"])
    meas = kept["meas"]
    info: dict[str, Any] = {}
    if meas.get("mask") is not None:
        try:
            info = await asyncio.to_thread(_bald_record, meas, sc.dir, size)
            sc.register("hair_outline_front", info["outline"])
            sc.register("hair_picture", info["picture"])
        except Exception as e:  # noqa: BLE001
            meas = {**meas, "reason": "error", "ok": False, "error": str(e)[:300]}

    # Bald quarter, left and right from the bald front, with the seeds the haired views used.
    rec = stage_recipe(sc)
    chosen = params.get("lora_strength")
    lora_strength = float(chosen) if chosen is not None else float(rec["turnaround"]["lora_strength"])
    extra = rec["turnaround"].get("prompt_suffix") or ""
    sc.use_model("qwen_angles_lora")
    bald_name = await sc.stage_input(kept["files"]["raw"], f"vgac_{sc.version.id[:8]}_bald_front.png")
    view_info = sc.version.metrics.get("views") or {}
    seeds: dict[str, int] = {"front": int(kept["seed"])}
    for k, view in enumerate(BALD_SIDE_VIEWS):
        vs = (view_info.get(view) or {}).get("seed")
        vs = int(vs) if vs else seed + 10 + k
        sc.progress(0.97, f"Drawing the bald {view} view")
        files = await _generate_view(sc, bald_name, view, vs, lora_strength, extra, 0.97, 0.01, name=f"bald_{view}")
        sc.register(f"bald_{view}", files["raw"])
        sc.register(f"bald_{view}_cutout", files["cutout"])
        sc.register(f"bald_{view}_white", files["white"])
        seeds[view] = vs
    try:
        pairs = []
        for v in ("front", *BALD_SIDE_VIEWS):
            for key in (f"{v}_white", f"bald_{v}_white"):
                p = sc.file(key)
                if p is not None:
                    pairs.append((p, key[:-len("_white")]))
        if pairs:
            sheet = await asyncio.to_thread(contact_sheet, [p for p, _ in pairs], sc.dir / "bald_sheet.png", 256, 2,
                                            [label for _, label in pairs])
            sc.register("bald_sheet", sheet)
    except Exception:  # noqa: BLE001  (the sheet is for looking at; nothing depends on it)
        pass
    record = {"seed": seed, "kept_seed": int(kept["seed"]), "seeds": seeds, "kept_share": meas.get("kept_share"),
              "face_delta": meas.get("face_delta"), "reason": meas.get("reason"), "rerolled": len(tries) > 1,
              "face_dark": meas.get("face_dark"), "face_change": meas.get("face_change"),
              "beard_change": meas.get("beard_change"), "bearded": bool(meas.get("bearded")),
              "tries": [{"seed": t["seed"], "kept_share": t["meas"].get("kept_share"), "face_delta": t["meas"].get("face_delta"),
                         "face_dark": t["meas"].get("face_dark"), "face_change": t["meas"].get("face_change"),
                         "beard_change": t["meas"].get("beard_change"),
                         "reason": t["meas"].get("reason")} for t in tries],
              "hair_px": meas.get("hair_px"), "eye_cover": (info.get("record") or {}).get("pic_eye_cover"),
              "colour": (info.get("record") or {}).get("colour"), "seconds": round(time.time() - t0, 1)}
    if meas.get("error"):
        record["error"] = meas["error"]
    sc.set_metrics(bald=record)
    sc.set_metrics(**quality.summarise([bald_check(record)]))


def bald_check(record: dict[str, Any]) -> dict[str, Any]:
    """bald_picture (warn): kept_share against BALD_KEPT_MIN, with the reason when it fails."""
    share = record.get("kept_share")
    ok = record.get("reason") is None and share is not None and float(share) >= BALD_KEPT_MIN
    return quality.check("bald_picture", ok, share, BALD_KEPT_MIN,
                         vars={"face_delta": record.get("face_delta"), "face_dark": record.get("face_dark"),
                               "face_change": record.get("face_change"), "beard_change": record.get("beard_change"),
                               "rerolled": bool(record.get("rerolled")),
                               "reason": None if ok else (record.get("reason") or "clothes_changed")})


def _try_rank(meas: dict[str, Any]) -> tuple:
    """Which bald front to keep: one that passes; else the one whose face changed least (the drawn beard, then the
    change over the face), then the larger kept share."""
    dark = meas.get("face_dark")
    if meas.get("bearded"):
        dark = meas.get("beard_change")  # for a bearded character: how much the beard changed
    change = meas.get("face_change")
    return (bool(meas.get("ok")), -(float(dark) if dark is not None else 99.0), -(float(change) if change is not None else 99.0),
            float(meas.get("kept_share") or -1.0))


# --- the face inside the bald edit (live fault 11-06: women came back as bearded men and kept_share passed) ----------

# Where the face sits in the head box measured on the bald cut-out (top of the skull to the neck), as shares of its
# height and width: the face below the brow, and the lower face (upper lip, jaw and chin), where a beard shows.
FACE_BOX = (0.36, 1.0, 0.40)  # from, to (share of head height), half width (share of head width)
LOWER_BOX = (0.66, 1.04, 0.32)
CROWN_BOX = (-0.05, 0.22, 0.30)  # the top of the head, hair in the original wherever the outline covers it
SKIN_BOX = (0.58, 0.72, 0.12)  # nose and inner cheeks: skin in the original
FACE_DARKER_L = 18.0  # Lab lightness units: a pixel this much darker in the bald picture counts as darkened
# Calibrated on 50 real bald edits (runtime/tmp/hairpipe/calib.json, bald_prompt_check.png): 32 clean ones (the trial
# prompt and the trial prompt with a face sentence, 8 characters, 2 seeds) had face_dark 0.000 to 0.006 and face_change
# 3.6 to 6.9; 18 with a drawn beard (the live ponytail and bob knight, and 16 from the prompt that named a beard) had
# face_dark 0.056 to 0.285 and face_change 4.8 to 12.6. face_dark alone separates them.
FACE_DARK_MAX = 0.03  # face_dark: share of the lower face darkened; over it a beard or stubble was drawn
FACE_CHANGE_MAX = 9.0  # face_change: mean Lab difference over the face, outside the hair; over it the face changed
# A character whose description has facial hair: the beard is left out of the face measures (it is beard, not skin, and
# the original's beard and long hair run together), face_change is measured on the upper face (forehead, eyes, nose and
# cheeks above the beard line) and beard_change (every pixel of the lower face, beard included) catches a beard the edit
# took away or changed. Calibrated in runtime/tmp/hairpipe/calib_beard.json (see the report of 11-06).
UPPER_BOX = (0.40, 0.62, 0.30)  # eyes, nose and cheeks above the beard line, inside the temples
BEARD_BOX = (0.70, 1.0, 0.32)  # moustache, jaw and chin: the beard
BEARD_SKIN_BOX = (0.50, 0.64, 0.10)  # nose and inner cheeks above the beard line: the skin colour
BEARD_FROM_SKIN = 20.0  # Lab: a pixel of the beard box this far from the skin colour is beard (or lips) in the original
BEARD_PIXEL_DELTA = 20.0  # Lab: a beard pixel that changed this much was taken away or redrawn
HEAD_TALL_MIN = 1.25  # a head is at least this many times as tall as it is wide; a beard hides the neck from the outline
UPPER_CHANGE_MAX = 12.0  # median Lab difference over the upper face
BEARD_CHANGE_MAX = 0.35  # share of the original's beard pixels the edit changed


def _box(target: dict[str, Any], spec: tuple[float, float, float], shape: tuple[int, int]) -> tuple[int, int, int, int]:
    top, hh = float(target["top_px"]), float(target["neck_px"]) - float(target["top_px"])
    cx, hw = float(target["head_cx_px"]), float(target["head_w"]) * float(target["H_px"])
    y0, y1 = int(round(top + spec[0] * hh)), int(round(top + spec[1] * hh))
    x0, x1 = int(round(cx - spec[2] * hw)), int(round(cx + spec[2] * hw))
    return max(0, x0), max(0, y0), min(shape[1], max(x0 + 1, x1)), min(shape[0], max(y0 + 1, y1))


def face_measure(orig: np.ndarray, bald: np.ndarray, mask: np.ndarray, target: dict[str, Any], bearded: bool = False) -> dict[str, Any]:
    """Original against bald inside the face only, at the picture's own resolution, leaving out the hair outline
    (grown a little) and the background: face_change (mean Lab difference over the face) and face_dark (share of the
    lower face, upper lip to chin, that became FACE_DARKER_L darker: a drawn beard or stubble)."""
    from .. import baldpic  # lane 1

    o, b = np.asarray(orig), np.asarray(bald)
    H, W = o.shape[:2]
    lab_o, lab_b = baldpic.to_lab(o[..., :3]), baldpic.to_lab(b[..., :3])
    # The hair outline is where the two pictures differ, so a drawn beard falls inside it. Only the outline's pixels
    # that were hair in the original are left out: those closer to the hair's colour (the outline on the crown) than
    # to the skin's (the original's nose and cheeks).
    outline = np.asarray(mask, bool)
    crown = np.zeros((H, W), bool)
    cx0, cy0, cx1, cy1 = _box(target, CROWN_BOX, (H, W))
    crown[max(0, cy0 - int(0.2 * (cy1 - cy0))):cy1, cx0:cx1] = True
    sx0, sy0, sx1, sy1 = _box(target, SKIN_BOX, (H, W))
    skin_px = lab_o[sy0:sy1, sx0:sx1].reshape(-1, 3)
    hair = outline.copy()
    if (outline & crown).any() and len(skin_px):
        hair_lab = np.median(lab_o[outline & crown], axis=0)
        skin_lab = np.median(skin_px, axis=0)
        hair = outline & (np.linalg.norm(lab_o - hair_lab, axis=-1) < np.linalg.norm(lab_o - skin_lab, axis=-1))
    # Specks of shading that happen to match the hair colour are dropped (an opening) before the hair is left out.
    r = max(1, int(round(0.003 * max(H, W))))
    hair = baldpic.morph(baldpic.morph(hair, r, "erode"), r, "dilate")
    keep = ~hair
    if o.shape[2] == 4:
        keep &= o[..., 3] > 127
    if b.shape[2] == 4:
        keep &= b[..., 3] > 127
    out: dict[str, Any] = {}
    for name, spec in (("face", FACE_BOX), ("lower", LOWER_BOX)):
        x0, y0, x1, y1 = _box(target, spec, (H, W))
        sel = keep[y0:y1, x0:x1]
        out[f"{name}_px"] = int(sel.sum())
        if not sel.any():
            out[f"{name}_change"] = None
            out[f"{name}_dark"] = None
            continue
        lo, lb = lab_o[y0:y1, x0:x1][sel], lab_b[y0:y1, x0:x1][sel]
        out[f"{name}_change"] = round(float(np.linalg.norm(lo - lb, axis=1).mean()), 2)
        out[f"{name}_dark"] = round(float(((lo[:, 0] - lb[:, 0]) > FACE_DARKER_L).mean()), 4)
    if not bearded:
        return {"face_change": out["face_change"], "face_dark": out["lower_dark"], "face_px": out["face_px"],
                "lower_px": out["lower_px"], "lower_change": out["lower_change"]}
    # Bearded: the upper face with the hair left out as above, and the lower face as it is (the beard is to be kept).
    # A full beard joins the head to the chest in the outline, so the neck found there can sit at the nose: the head
    # is taken to be at least HEAD_TALL_MIN times as tall as it is wide.
    target = dict(target)
    hw = float(target["head_w"]) * float(target["H_px"])
    if float(target["neck_px"]) - float(target["top_px"]) < HEAD_TALL_MIN * hw:
        target["neck_px"] = float(target["top_px"]) + HEAD_TALL_MIN * hw
    x0, y0, x1, y1 = _box(target, UPPER_BOX, (H, W))
    sel = keep[y0:y1, x0:x1]
    upper = None
    if sel.any():
        upper = round(float(np.median(np.linalg.norm(lab_o[y0:y1, x0:x1][sel] - lab_b[y0:y1, x0:x1][sel], axis=1))), 2)
    x0, y0, x1, y1 = _box(target, BEARD_BOX, (H, W))
    fig = np.ones((H, W), bool)
    if o.shape[2] == 4:
        fig &= o[..., 3] > 127
    if b.shape[2] == 4:
        fig &= b[..., 3] > 127
    # The beard in the original: pixels of the beard box whose colour is far from the face's skin (the nose and
    # cheeks above the beard line). beard_change is the share of them the edit changed by more than BEARD_PIXEL_DELTA.
    sx0, sy0, sx1, sy1 = _box(target, BEARD_SKIN_BOX, (H, W))
    skin_lab = np.median(lab_o[sy0:sy1, sx0:sx1].reshape(-1, 3), axis=0)
    lo, lb = lab_o[y0:y1, x0:x1], lab_b[y0:y1, x0:x1]
    beard_px = fig[y0:y1, x0:x1] & (np.linalg.norm(lo - skin_lab, axis=-1) > BEARD_FROM_SKIN)
    beard = None
    if beard_px.sum() >= 20:
        beard = round(float((np.linalg.norm(lo - lb, axis=-1)[beard_px] > BEARD_PIXEL_DELTA).mean()), 4)
    return {"face_change": upper, "face_dark": None, "beard_change": beard, "bearded": True, "face_px": int(sel.sum()),
            "lower_px": int(beard_px.sum()), "lower_change": beard}


def face_kept(fm: dict[str, Any]) -> bool:
    """The face is the picture's own: no beard drawn on the lower face and no large change over the face. For a bearded
    character: the upper face unchanged and the beard kept."""
    if fm.get("bearded"):
        up, beard = fm.get("face_change"), fm.get("beard_change")
        return (up is None or float(up) <= UPPER_CHANGE_MAX) and (beard is None or float(beard) <= BEARD_CHANGE_MAX)
    dark, change = fm.get("face_dark"), fm.get("face_change")
    return (dark is None or float(dark) <= FACE_DARK_MAX) and (change is None or float(change) <= FACE_CHANGE_MAX)
