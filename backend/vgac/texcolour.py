"""Base colour (the colour texture) against the picture the 3D model was built from.

TRELLIS.2 sometimes predicts a near-black base colour from a bright picture. Flat, shadowless pictures (some Cel
characters) lean that way, and the seed decides how far: one Cel picture gave texture brightness 0.16 of the picture's on
one seed and 0.67 on another, while 33 Painted builds and two fresh Cel builds all stayed between 0.64 and 1.19
(runtime/tmp/celdark). Nothing checked colour before, so a black character passed 17 of 17 checks.

The measure: mean brightness of the base colour over the texels the model actually uses (the UV layout rasterised at
the map's own size, so atlas padding and the grown seams are never counted), over the mean brightness of the picture
where its cut-out is opaque (alpha 128 or more, so the black background the 3D step puts behind the cut-out is never
counted). Brightness is Rec. 709 luminance on sRGB values, 0 to 255, the units the albedo range rules use.

The 3D step builds again with a new full seed while the ratio is under RATIO_MIN, MAX_TRIES builds in all, and keeps the
closest (stages/mesh.py). The seeds after the first follow from the first (try_seed), so a Stop and Resume, which carries
the first seed (jobs.RESUME_SEEDS), builds the same tries again.

Not measured (None, and no check): a picture with no cut-out (no alpha, or all of it opaque), a model without a texture
layout or base colour (Hunyuan3D), tileable materials (they never reach the 3D step).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

import numpy as np
from PIL import Image

from .imaging import ALBEDO_MAX, ALBEDO_MIN, load_uv_mesh, rasterise_uv, read_map, srgb_luminance

CHECK_ID = "texture_matches_picture"
# The base colour's brightness over the picture's. Every normal build measured 0.64 or more; the dark ones 0.15 to 0.54.
RATIO_MIN = 0.5
# On the 0..255 scale: a texture this close to the picture's brightness passes whatever the ratio (a dark picture).
NEAR_LUMA = 12.0
# Builds in all, the first included.
MAX_TRIES = 3
ALPHA_OPAQUE = 128
# A cut-out covers at least this share of the picture and leaves at least this share clear.
CUTOUT_MIN_SHARE = 0.005
SEED_STEP = 1_000_003
SEED_MAX = 2**31 - 1


def _stats(lum: np.ndarray) -> dict[str, Any]:
    """lum on 0..255. {luma, dark, out, pixels}: mean, share under ALBEDO_MIN, share outside ALBEDO_MIN..ALBEDO_MAX."""
    n = int(lum.size)
    return {"luma": round(float(lum.mean()), 2), "dark": round(float((lum < ALBEDO_MIN).mean()), 4),
            "out": round(float(((lum < ALBEDO_MIN) | (lum > ALBEDO_MAX)).mean()), 4), "pixels": n}


def picture_stats_array(rgba: np.ndarray) -> dict[str, Any] | None:
    """rgba uint8 (h, w, 4). The stats of the opaque pixels, or None when there is no cut-out to measure."""
    if rgba.ndim != 3 or rgba.shape[2] != 4:
        return None
    opaque = rgba[..., 3] >= ALPHA_OPAQUE
    share = float(opaque.mean())
    if share < CUTOUT_MIN_SHARE or share > 1.0 - CUTOUT_MIN_SHARE:
        return None
    lum = srgb_luminance(rgba[..., :3].astype(np.float32) / 255.0) * 255.0
    return _stats(lum[opaque])


def picture_stats(path: Path | str | None) -> dict[str, Any] | None:
    """The picture's stats where its cut-out is opaque; None without a picture or a cut-out."""
    if not path or not Path(path).is_file():
        return None
    with Image.open(path) as im:
        if "A" not in im.getbands():
            return None
        rgba = np.asarray(im.convert("RGBA"))
    return picture_stats_array(rgba)


def texture_stats_array(base: np.ndarray, used: np.ndarray) -> dict[str, Any] | None:
    """base float (h, w, 3) in 0..1, used bool (h, w): the texels the model uses. None when it uses none."""
    if used is None or not used.any():
        return None
    lum = srgb_luminance(base) * 255.0
    return _stats(lum[used])


def texture_stats(base_png: Path | str | None, glb: Path | str | None) -> dict[str, Any] | None:
    """The base colour's stats over the texels the model's UV layout covers, at the map's own size."""
    if not base_png or not glb or not Path(base_png).is_file() or not Path(glb).is_file():
        return None
    uvm = load_uv_mesh(glb)
    if uvm is None:
        return None
    base = read_map(base_png)
    used = rasterise_uv(uvm, base.shape[0])["labels"] > 0
    if base.shape[0] != base.shape[1]:
        used = np.asarray(Image.fromarray(used).resize((base.shape[1], base.shape[0]), Image.NEAREST))
    return texture_stats_array(base, used)


def passes(ratio: float, picture_luma: float, texture_luma: float) -> bool:
    return ratio >= RATIO_MIN or (picture_luma - texture_luma) <= NEAR_LUMA


def compare(picture: dict[str, Any] | None, texture: dict[str, Any] | None) -> dict[str, Any] | None:
    """{ratio, ok, picture_luma, texture_luma, texture_dark, picture_dark}, or None when either side is missing."""
    if not picture or not texture:
        return None
    p, t = float(picture["luma"]), float(texture["luma"])
    ratio = t / max(p, 1.0)
    return {"ratio": round(ratio, 3), "ok": passes(ratio, p, t), "picture_luma": round(p, 1), "texture_luma": round(t, 1),
            "picture_dark": picture.get("dark"), "texture_dark": texture.get("dark")}


def measure(base_png: Path | str | None, glb: Path | str | None, picture: Path | str | None) -> dict[str, Any] | None:
    return compare(picture_stats(picture), texture_stats(base_png, glb))


def try_seed(first: int, n: int) -> int:
    """The seed of build n (0 is the first): the first seed itself, then fixed steps from it, so a Resume repeats them."""
    if n == 0:
        return int(first)
    return (int(first) + n * SEED_STEP) % SEED_MAX or 1


def best_try(tries: list[dict[str, Any]]) -> int:
    """The build to keep: the first that passes, else the one closest to the picture (the highest ratio, all of them
    being under the limit); a build that could not be measured counts as passing."""
    for i, t in enumerate(tries):
        m = t.get("match")
        if m is None or m.get("ok"):
            return i
    return max(range(len(tries)), key=lambda i: float(tries[i]["match"]["ratio"]))


async def build_with_rerolls(first_seed: int, measured: bool, build_once: Callable[[int, int], Awaitable[dict[str, Any]]],
                             keep_aside: Callable[[int], Any], bring_back: Callable[[int], Any],
                             on_retry: Callable[[int, dict[str, Any]], Any] | None = None,
                             max_tries: int = MAX_TRIES) -> tuple[int, list[dict[str, Any]]]:
    """Build, and while the base colour comes out too dark against the picture, build again with the next seed.
    build_once(seed, n) makes build n in place and returns {seed, match, ...}; keep_aside(n) moves build n's files out of
    the way before the next; bring_back(n) puts a kept-aside build back in place. measured False (no picture to compare)
    builds once. Returns (index kept, every build's result)."""
    tries: list[dict[str, Any]] = []
    for n in range(max(1, max_tries)):
        if n and on_retry is not None:
            on_retry(n, tries[-1])
        t = await build_once(try_seed(first_seed, n), n)
        tries.append(t)
        m = t.get("match")
        if not measured or m is None or m.get("ok"):
            break
        if n + 1 < max_tries:
            keep_aside(n)
    best = best_try(tries)
    if best != len(tries) - 1:
        bring_back(best)
    return best, tries


def record(best: int, tries: list[dict[str, Any]]) -> dict[str, Any]:
    """The metrics the 3D step keeps: texture_match (the kept build's), texture_tries, and each build's seed and ratio."""
    kept = tries[best].get("match")
    return {"texture_match": kept, "texture_tries": len(tries), "texture_try_seeds": [t.get("seed") for t in tries],
            "texture_try_ratios": [None if t.get("match") is None else t["match"]["ratio"] for t in tries],
            "texture_try_kept": best + 1}


def check(metrics: dict[str, Any]) -> dict[str, Any] | None:
    """The texture_matches_picture check from the 3D step's metrics; None when it was not measured."""
    from . import quality

    m = (metrics or {}).get("texture_match")
    if not m:
        return None
    tries = int((metrics or {}).get("texture_tries") or 1)
    kept = int((metrics or {}).get("texture_try_kept") or tries)
    return quality.check(CHECK_ID, bool(m.get("ok")), float(m["ratio"]), RATIO_MIN,
                         vars={"tries": tries, "kept_try": kept, "rebuilt": tries > 1,
                               "picture_luma": m.get("picture_luma"), "texture_luma": m.get("texture_luma")})


# --- the clean-up step's albedo_range for looks that keep painted darks -------------------------------------------------
#
# A look that keeps its painted shading keeps its picture's darks and lights, so albedo_range excuses them. That
# excuse holds only while the base colour is about as bright as the picture (the same rule as the 3D step's check); a
# near-black texture from a bright picture is not the look and fails in every look. Comparing the share of dark texels
# with the picture's dark share instead was tried and rejected: a Painted texture that is 30 percent darker than its
# picture (normal for the builder) has three to five times the picture's dark share (Caped hero 0.61 against 0.11,
# High ponytail warrior 0.46 against 0.13), so three kept Painted characters would have failed (runtime/tmp/celdark).


def kept_look_excused(match: dict[str, Any] | None) -> bool:
    """Whether a kept look's out-of-range colours are the look's own: the clean-up step's base colour against the picture
    (compare()) passes, or there is nothing to compare (as before)."""
    return match is None or bool(match.get("ok"))
