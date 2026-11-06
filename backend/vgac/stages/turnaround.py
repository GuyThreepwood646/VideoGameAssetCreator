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
from ..graphs.qwen_edit import angle_graph, edit_graph, view_prompt
from ..imaging import alpha_bbox, colour_histogram, contact_sheet, histogram_distance, mirror_iou
from .base import StageContext, random_seed
from .concept import stage_recipe

DEFAULT_VIEWS = ["front", "left", "back", "right", "quarter"]
MAX_REROLLS = 2
LIMITS = {"height_ratio": (0.75, 1.3), "hist_distance": 0.35, "mirror_iou": 0.55, "min_coverage": 0.02}


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


def view_checks(view: str, stats: dict[str, Any], front: dict[str, Any] | None, hist_front, hist_view, mirror: float | None) -> dict[str, Any]:
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
    if mirror is not None:
        out["mirror_iou"] = round(mirror, 4)
        checks.append({"id": "mirror_iou", "ok": mirror >= LIMITS["mirror_iou"], "value": round(mirror, 4), "limit": LIMITS["mirror_iou"]})
    out["checks"] = [{"name": c["id"], "passed": c["ok"], "message": f"{c['value']} (limit {c['limit']})", **c} for c in checks]
    out["passed"] = all(c["ok"] for c in checks)
    out["ok"] = out["passed"]
    return out


async def _generate_view(sc: StageContext, src_name: str, view: str, seed: int, lora_strength: float, extra: str, base: float, span: float,
                         name: str | None = None) -> dict[str, Path]:
    """One drawn view. `name` (default the view) names its files: the bald pictures use bald_<view>."""
    name = name or view
    graph = angle_graph(src_name, view_prompt(view, extra), seed, sc.prefix(f"{name}_{seed}"), lora_strength=lora_strength)
    entry = await sc.comfy(graph, message=f"view {name}", base=base, span=span * 0.7)
    raw = await sc.download_image(entry, "save", sc.dir / f"{name}_raw.png")
    res = await sc.cutout(raw, name, base=base + span * 0.7, span=span * 0.3)
    return {"raw": raw, "cutout": res["cutout"], "white": res["white"], "stats": res.get("stats")}


def _measure(cutout: Path, stats: dict[str, Any] | None, other_cutout: Path | None,
             hist_paths: dict[str, Path | None]) -> tuple[dict[str, Any], float | None, dict[str, Any]]:
    """CPU part of a view's checks (no database access, so it runs in a worker thread): bbox stats, mirror IoU
    against the opposite view, and colour histograms for the views in `hist_paths`."""
    if stats is None:
        stats = alpha_bbox(cutout)
    mirror = mirror_iou(cutout, other_cutout) if other_cutout is not None else None
    hists = {v: (colour_histogram(p) if p else None) for v, p in hist_paths.items()}
    return stats, mirror, hists


def supplied_files(sc: StageContext, views: list[str] | None = None) -> dict[str, Path]:
    """The person's own pictures on this version (files supplied_<view>, written by the API)."""
    out: dict[str, Path] = {}
    for v in views or list(reference.TURNAROUND_VIEWS):
        p = sc.file(f"supplied_{v}")
        if p is not None:
            out[v] = p
    return out


async def _supplied_view(sc: StageContext, view: str, path: Path, base: float, span: float) -> dict[str, Any]:
    """A view the person supplied: kept as it is (copied as the view picture) and cut out like a drawn one."""
    raw = sc.copy_in(path, f"{view}_raw.png")
    res = await sc.cutout(raw, view, base=base, span=span)
    return {"raw": raw, "cutout": res["cutout"], "white": res["white"], "stats": res.get("stats")}


async def _run_views(sc: StageContext, views: list[str], params: dict[str, Any], source: Version | None,
                     keep: dict[str, dict[str, Any]] | None = None, extra: str = "",
                     supplied: dict[str, Path] | None = None) -> None:
    """Generate and check `views` from the concept image of `source`. Views in `supplied` are the person's own
    pictures: cut out and checked like the rest, never redrawn; one that fails a check warns and is kept."""
    supplied = dict(supplied or {})
    src = _source_image(sc, source)
    src_name = await sc.stage_input(src, f"vgac_{sc.version.id[:8]}_concept.png")
    rec = stage_recipe(sc)
    # The camera-angle helper's strength: the version's, else the recipe's (the add-on's normal step, 0.9, unless the
    # look sets its own). The recipe's prompt words go after the view words, before any refine instruction.
    chosen = params.get("lora_strength")
    lora_strength = float(chosen) if chosen is not None else float(rec["turnaround"]["lora_strength"])
    suffix = rec["turnaround"].get("prompt_suffix") or ""
    extra = " ".join(p for p in (suffix, extra) if p and p.strip())
    auto_reroll = bool(params.get("auto_reroll", True))
    base_seed = params.get("seed")
    sc.use_model("qwen_edit")
    sc.use_model("qwen_lightning_lora")
    sc.use_model("qwen_angles_lora")

    results: dict[str, dict[str, Any]] = dict(keep or {})
    order = ["front"] + [v for v in views if v != "front"] if "front" in views or "front" in results else list(views)
    todo = [v for v in order if v in views]
    n = max(1, len(todo))
    hist_cache: dict[str, Any] = {}

    for i, view in enumerate(todo):
        attempts = 0
        seed = int(base_seed) + i if base_seed else random_seed()
        while True:
            if view in supplied:
                sc.progress(0.05 + 0.9 * i / n, f"your {view} view")
                files = await _supplied_view(sc, view, supplied[view], 0.05 + 0.9 * i / n, 0.9 / n)
            else:
                sc.progress(0.05 + 0.9 * i / n, f"view {view} attempt {attempts + 1}")
                files = await _generate_view(sc, src_name, view, seed, lora_strength, extra, 0.05 + 0.9 * i / n, 0.9 / n)
            sc.register(view, files["raw"])
            sc.register(f"{view}_cutout", files["cutout"])
            sc.register(f"{view}_white", files["white"])
            hist_cache.pop(view, None)
            front_stats = results.get("front", {}).get("bbox") if view != "front" else None
            other = {"left": "right", "right": "left"}.get(view)
            # File lookups read the version row, so they stay on the event loop; the image work goes to a thread.
            other_cutout = sc.file(f"{other}_cutout") if other else None
            front_cutout = sc.file("front_cutout") if view != "front" else None
            want: dict[str, Path | None] = {view: sc.file(f"{view}_cutout")}
            if front_cutout and "front" not in hist_cache:
                want["front"] = front_cutout
            stats, mirror, new_hists = await asyncio.to_thread(_measure, files["cutout"], files.get("stats"), other_cutout, want)
            hist_cache.update(new_hists)
            info = view_checks(view, stats, front_stats, hist_cache["front"] if front_cutout else None, hist_cache[view], mirror)
            info["seed"] = None if view in supplied else seed
            info["rerolls"] = attempts
            if view in supplied:
                info["supplied"] = True
            results[view] = info
            if other in results and mirror is not None:
                results[other]["mirror_iou"] = round(mirror, 4)
            if view in supplied or info["passed"] or not auto_reroll or attempts >= MAX_REROLLS:
                info["flagged"] = not info["passed"]
                break
            attempts += 1
            seed = random_seed()
        _publish_views(sc, results)
    sc.set_params(views=list(views), lora_strength=lora_strength, auto_reroll=auto_reroll, seed=base_seed,
                  lora_strength_source="chosen" if chosen is not None else "recipe",
                  supplied_views=sorted({*(params.get("supplied_views") or []), *supplied}))
    sc.set_metrics(recipe=reference.recipe_record(rec, "turnaround"),
                   addons=[{"id": "qwen_lightning", "value": 1.0, "required": True},
                           {"id": "qwen_angles", "value": lora_strength, "required": True}])
    _publish_views(sc, results)


def _publish_views(sc: StageContext, results: dict[str, dict[str, Any]]) -> None:
    sc.set_metrics(views=results, views_total=len(results), views_passed=sum(1 for r in results.values() if r.get("passed")),
                   flagged_views=[v for v, r in results.items() if r.get("flagged")],
                   supplied_views=[v for v, r in results.items() if r.get("supplied")],
                   consistency_ok=not any(r.get("flagged") for r in results.values()))


async def run(sc: StageContext, params: dict[str, Any]) -> None:
    views = list(params.get("views") or sc.category.get("views") or DEFAULT_VIEWS)
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


async def refine(sc: StageContext, instruction: str, view: str | None, mask_png: Path | None, params: dict[str, Any]) -> None:
    parent = sc.parent
    if parent is None or parent.stage != "turnaround":
        raise ValueError("turnaround refine needs a turnaround parent version")
    view = view or "front"
    if view not in (parent.params.get("views") or DEFAULT_VIEWS):
        raise ValueError(f"view {view!r} is not part of the parent turnaround")
    # Copy every other view forward so the new version is complete.
    keep: dict[str, dict[str, Any]] = {}
    for v, info in (parent.metrics.get("views") or {}).items():
        if v == view:
            continue
        for key in (v, f"{v}_cutout", f"{v}_white"):
            p = library.version_file(parent, key, sc.settings)
            if p is not None:
                sc.register(key, sc.copy_in(p, p.name))
        keep[v] = info
    merged = {**inherit(parent.params, params), "seed": params.get("seed"), "instruction": instruction, "refine_of": parent.id, "refined_view": view}
    sc.set_params(**merged)
    sc.set_metrics(views=keep)
    _copy_bald(sc, parent)
    # The concept source is the turnaround parent's parent.
    grand = concept_of(sc, parent)
    if grand is None:
        raise ValueError("turnaround parent has no concept parent")
    await _run_views(sc, [view], merged, grand, keep=keep, extra=instruction)


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

# The wig trial's bald edit (runtime/tmp/wigtrial/edit.py PROMPTS["bald"], clean on 5 of 5 characters), plus the beard.
BALD_PROMPT = ("Make this character completely bald: remove all of the hair, including the fringe, any ponytail and any "
               "loose locks, and show a smooth, clean, hairless bald scalp with a natural rounded skull shape and both ears "
               "visible. Keep the face, eyes, expression, ears, skin tone, neck, outfit, pose, proportions, framing, art "
               "style, lighting and background exactly the same. Keep any beard or moustache exactly as it is.")
BALD_SIDE_VIEWS = ("quarter", "left", "right")  # quarter is the 3D input; left and right are for the face pass. No back.
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


def _bald_measure(orig_img: Path, orig_cut: Path | None, bald_raw: Path, bald_cut: Path, size: tuple[int, int]) -> dict[str, Any]:
    """The bald front against the concept (CPU work, run in a thread): hair outline, kept share, face change, reason."""
    from .. import baldpic  # lane 1

    orig = _rgba(orig_img, orig_cut, size)
    bald = _rgba(bald_raw, bald_cut, size)
    mask = np.asarray(baldpic.outline_mask(orig, bald)).astype(bool)
    kept = dict(baldpic.kept_measure(orig, bald, mask) or {})
    share = float(kept.get("kept_share") or 0.0)
    delta = kept.get("face_delta")
    reason = kept.get("reason")
    if reason is None:
        if mask.mean() < BALD_NO_HAIR_SHARE:
            reason = "no_hair_removed"
        elif share >= BALD_KEPT_MIN:
            reason = None
        elif delta is not None and float(delta) > BALD_FACE_DELTA_MAX:
            reason = "face_changed"
        else:
            reason = "clothes_changed"
    return {"mask": mask, "kept_share": round(share, 4), "face_delta": None if delta is None else round(float(delta), 3),
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
    graph = edit_graph(src_name, BALD_PROMPT, seed, sc.prefix(f"bald_front_{seed}"), width=ew, height=eh)
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
            meas = await asyncio.to_thread(_bald_measure, src, orig_cut, files["raw"], files["cutout"], size)
        except Exception as e:  # noqa: BLE001  (the measure failed: the picture is kept and the check says so)
            meas = {"mask": None, "kept_share": None, "face_delta": None, "reason": "error", "ok": False, "error": str(e)[:300]}
        tries.append({"seed": seed + n, "files": files, "meas": meas})
        if meas["ok"]:
            break
    best = max(range(len(tries)), key=lambda i: (bool(tries[i]["meas"]["ok"]), tries[i]["meas"].get("kept_share") or -1.0))
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
              "tries": [{"seed": t["seed"], "kept_share": t["meas"].get("kept_share"), "face_delta": t["meas"].get("face_delta"),
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
                         vars={"face_delta": record.get("face_delta"), "rerolled": bool(record.get("rerolled")),
                               "reason": None if ok else (record.get("reason") or "clothes_changed")})
