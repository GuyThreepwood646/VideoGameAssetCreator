"""Stage 2, preview turnaround: concept image -> views with Qwen-Image-Edit-2511 + multiple-angles LoRA, per-view
BiRefNet cutouts and consistency checks (instance B).

Checks per view against the front view: silhouette height ratio, bounding-box aspect, colour histogram distance,
and left-right mirror IoU. A failing view is re-rolled with a new seed up to 2 times when auto_reroll is on, then
flagged. refine re-runs one view; adjust re-runs the whole set with new params.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import library
from ..graphs.qwen_edit import angle_graph, view_prompt
from ..imaging import alpha_bbox, colour_histogram, histogram_distance, mirror_iou
from .base import StageContext, random_seed

DEFAULT_VIEWS = ["front", "left", "back", "right", "quarter"]
MAX_REROLLS = 2
LIMITS = {"height_ratio": (0.75, 1.3), "hist_distance": 0.35, "mirror_iou": 0.55, "min_coverage": 0.02}


def _source_image(sc: StageContext) -> Path:
    parent = sc.parent
    if parent is None:
        raise ValueError("turnaround needs a concept parent version")
    src = library.version_file(parent, "image", sc.settings)
    if src is None:
        raise FileNotFoundError("parent concept image missing")
    return src


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


async def _generate_view(sc: StageContext, src_name: str, view: str, seed: int, lora_strength: float, extra: str, base: float, span: float) -> dict[str, Path]:
    graph = angle_graph(src_name, view_prompt(view, extra), seed, sc.prefix(f"{view}_{seed}"), lora_strength=lora_strength)
    entry = await sc.comfy(graph, message=f"view {view}", base=base, span=span * 0.7)
    raw = await sc.download_image(entry, "save", sc.dir / f"{view}_raw.png")
    res = await sc.cutout(raw, view, base=base + span * 0.7, span=span * 0.3)
    return {"raw": raw, "cutout": res["cutout"], "white": res["white"]}


async def _run_views(sc: StageContext, views: list[str], params: dict[str, Any], keep: dict[str, dict[str, Any]] | None = None,
                     extra: str = "") -> None:
    src = _source_image(sc)
    src_name = await sc.stage_input(src, f"vgac_{sc.version.id[:8]}_concept.png")
    lora_strength = float(params.get("lora_strength", 0.9))
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

    def hist(view: str):
        if view not in hist_cache:
            p = sc.file(f"{view}_cutout")
            hist_cache[view] = colour_histogram(p) if p else None
        return hist_cache[view]

    for i, view in enumerate(todo):
        attempts = 0
        seed = int(base_seed) + i if base_seed else random_seed()
        while True:
            sc.progress(0.05 + 0.9 * i / n, f"view {view} attempt {attempts + 1}")
            files = await _generate_view(sc, src_name, view, seed, lora_strength, extra, 0.05 + 0.9 * i / n, 0.9 / n)
            sc.register(view, files["raw"])
            sc.register(f"{view}_cutout", files["cutout"])
            sc.register(f"{view}_white", files["white"])
            hist_cache.pop(view, None)
            stats = alpha_bbox(files["cutout"])
            front_stats = results.get("front", {}).get("bbox") if view != "front" else None
            mirror = None
            other = {"left": "right", "right": "left"}.get(view)
            if other and sc.file(f"{other}_cutout") is not None:
                mirror = mirror_iou(files["cutout"], sc.file(f"{other}_cutout"))
            info = view_checks(view, stats, front_stats, hist("front") if view != "front" and sc.file("front_cutout") else None, hist(view), mirror)
            info["seed"] = seed
            info["rerolls"] = attempts
            results[view] = info
            if other in results and mirror is not None:
                results[other]["mirror_iou"] = round(mirror, 4)
            if info["passed"] or not auto_reroll or attempts >= MAX_REROLLS:
                info["flagged"] = not info["passed"]
                break
            attempts += 1
            seed = random_seed()
        _publish_views(sc, results)
    sc.set_params(views=list(views), lora_strength=lora_strength, auto_reroll=auto_reroll, seed=base_seed)
    _publish_views(sc, results)


def _publish_views(sc: StageContext, results: dict[str, dict[str, Any]]) -> None:
    sc.set_metrics(views=results, views_total=len(results), views_passed=sum(1 for r in results.values() if r.get("passed")),
                   flagged_views=[v for v, r in results.items() if r.get("flagged")],
                   consistency_ok=not any(r.get("flagged") for r in results.values()))


async def run(sc: StageContext, params: dict[str, Any]) -> None:
    views = list(params.get("views") or sc.category.get("views") or DEFAULT_VIEWS)
    await _run_views(sc, views, params)


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
    merged = {**parent.params, **params, "seed": params.get("seed"), "instruction": instruction, "refine_of": parent.id, "refined_view": view}
    sc.set_params(**merged)
    sc.set_metrics(views=keep)
    # The concept source is the turnaround parent's parent.
    grand = sc.db.get(type(parent), parent.parent_version_id) if parent.parent_version_id else None
    if grand is None:
        raise ValueError("turnaround parent has no concept parent")
    sc.version.parent_version_id = parent.id
    # _run_views reads the concept through sc.parent; temporarily point at the concept version.
    original_parent = sc.version.parent_version_id
    sc.version.parent_version_id = grand.id
    try:
        await _run_views(sc, [view], merged, keep=keep, extra=instruction)
    finally:
        sc.version.parent_version_id = original_parent
        sc.save()


async def adjust(sc: StageContext, params: dict[str, Any]) -> None:
    parent = sc.parent
    merged = {**(parent.params if parent else {}), **params}
    views = list(merged.get("views") or DEFAULT_VIEWS)
    if parent is not None and parent.stage == "turnaround":
        grand_id = parent.parent_version_id
        sc.version.parent_version_id = grand_id
        try:
            await _run_views(sc, views, merged)
        finally:
            sc.version.parent_version_id = parent.id
            sc.save()
    else:
        await _run_views(sc, views, merged)
