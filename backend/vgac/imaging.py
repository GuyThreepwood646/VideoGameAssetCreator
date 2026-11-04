"""Image metrics shared by the stages: silhouettes, IoU, best-alignment search, colour histograms, composites, sheets.

silhouette, iou and best_alignment are bakeoff/metrics.py; white_composite is turnaround.py's white_copy.

The alpha channel of each PNG and its silhouettes are cached in small LRU caches keyed by (path, mtime, size), so the
several reads a cutout gets per turnaround view (alpha_bbox, mirror_iou, the mesh audit) decode it only once. A file
rewritten in place gets a new mtime or size and so a new entry. The functions are pure and hold no session state, so
the stages may call them through asyncio.to_thread.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image, ImageDraw

VIEW_YAW = {"front": 0, "left": 90, "back": 180, "right": 270}


def _file_key(path: Path | str) -> tuple[str, int, int]:
    """Cache key for a file: absolute path, mtime in ns and size in bytes."""
    p = os.path.abspath(os.fspath(path))
    st = os.stat(p)
    return p, st.st_mtime_ns, st.st_size


@lru_cache(maxsize=32)
def _alpha_cached(path: str, mtime_ns: int, size: int) -> np.ndarray:
    with Image.open(path) as im:
        a = np.array(im.convert("RGBA").getchannel("A"))
    a.flags.writeable = False  # shared between callers through the cache
    return a


def _alpha(path: Path | str) -> np.ndarray:
    """Alpha channel (uint8, read-only) of the image converted to RGBA, decoded once per file version."""
    return _alpha_cached(*_file_key(path))


@lru_cache(maxsize=128)
def _silhouette_cached(path: str, mtime_ns: int, size: int, out_size: int) -> np.ndarray:
    a = _alpha_cached(path, mtime_ns, size) > 127
    ys, xs = np.where(a)
    if len(xs) == 0:
        out = np.zeros((out_size, out_size), bool)
    else:
        crop = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        h, w = crop.shape
        s = out_size / max(h, w)
        small = Image.fromarray(crop.astype(np.uint8) * 255).resize((max(1, int(w * s)), max(1, int(h * s))))
        out = np.zeros((out_size, out_size), bool)
        sm = np.array(small) > 127
        y0, x0 = (out_size - sm.shape[0]) // 2, (out_size - sm.shape[1]) // 2
        out[y0:y0 + sm.shape[0], x0:x0 + sm.shape[1]] = sm
    out.flags.writeable = False
    return out


def silhouette(path: Path | str, size: int = 256) -> np.ndarray:
    """Alpha > 127 mask, cropped to its bounding box, scaled to fit `size` and centred. Bool array (a fresh copy)."""
    return _silhouette_cached(*_file_key(path), size).copy()


def clear_cache() -> None:
    """Drop the cached alpha channels and silhouettes (tests, or to free memory)."""
    _silhouette_cached.cache_clear()
    _alpha_cached.cache_clear()


def iou(a: np.ndarray, b: np.ndarray) -> float:
    return float((a & b).sum()) / max(1, float((a | b).sum()))


def best_alignment(refs: dict[str, np.ndarray], rendered: dict[int, np.ndarray], step: int = 15) -> dict[str, Any]:
    """Best mean IoU over yaw offsets and mirror direction. refs keyed by view name, rendered keyed by yaw degrees."""
    best = None
    views = [v for v in refs if v in VIEW_YAW]
    if not views or not rendered:
        return {"mean": 0.0, "offset": 0, "sign": 1, "views": {}}
    for offset in range(0, 360, step):
        for sign in (1, -1):
            scores = {}
            for v in views:
                yaw = (offset + sign * VIEW_YAW[v]) % 360
                if yaw not in rendered:
                    continue
                scores[v] = iou(refs[v], rendered[yaw])
            if not scores:
                continue
            mean = sum(scores.values()) / len(scores)
            if best is None or mean > best["mean"]:
                best = {"mean": round(mean, 4), "offset": offset, "sign": sign, "views": {k: round(x, 4) for k, x in scores.items()}}
    return best or {"mean": 0.0, "offset": 0, "sign": 1, "views": {}}


def alpha_bbox(path: Path | str) -> dict[str, Any]:
    """Bounding box of alpha > 127 and its size, aspect and coverage."""
    alpha = _alpha(path)
    a = alpha > 127
    ys, xs = np.where(a)
    h, w = alpha.shape
    if len(xs) == 0:
        return {"width": 0, "height": 0, "aspect": 0.0, "coverage": 0.0, "image_size": [w, h], "empty": True}
    bw, bh = int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)
    return {"width": bw, "height": bh, "aspect": round(bw / max(1, bh), 4), "coverage": round(float(a.mean()), 4),
            "image_size": [w, h], "empty": False, "alpha_corner": int(alpha[2, 2]),
            "alpha_centre": int(alpha[h // 2, w // 2])}


def colour_histogram(path: Path | str, bins: int = 8) -> np.ndarray:
    """Normalised RGB histogram over opaque pixels (alpha > 127), bins per channel."""
    im = Image.open(path).convert("RGBA")
    arr = np.array(im)
    mask = arr[..., 3] > 127
    rgb = arr[mask][:, :3] if mask.any() else arr[..., :3].reshape(-1, 3)
    hist, _ = np.histogramdd(rgb.astype(np.float32), bins=(bins, bins, bins), range=((0, 256),) * 3)
    hist = hist.astype(np.float64).ravel()
    s = hist.sum()
    return hist / s if s > 0 else hist


def histogram_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Half L1 distance between two normalised histograms: 0 identical, 1 disjoint."""
    return float(0.5 * np.abs(a - b).sum())


def mirror_iou(a_path: Path | str, b_path: Path | str) -> float:
    a = silhouette(a_path)
    b = silhouette(b_path)[:, ::-1]
    return iou(a, b)


def white_composite(rgba_path: Path | str, dest: Path | str | None = None) -> Path:
    src = Path(rgba_path)
    im = Image.open(src).convert("RGBA")
    bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
    bg.alpha_composite(im)
    out = Path(dest) if dest else src.with_name(src.stem + "_white.png")
    bg.convert("RGB").save(out)
    return out


def apply_mask(original: Path | str, edited: Path | str, mask_png: Path | str, dest: Path | str) -> Path:
    """Keep `edited` only where the mask is white; everywhere else keep `original`."""
    o = Image.open(original).convert("RGBA")
    e = Image.open(edited).convert("RGBA").resize(o.size)
    m = Image.open(mask_png).convert("L").resize(o.size)
    out = Image.composite(e, o, m)
    out.save(dest)
    return Path(dest)


def contact_sheet(paths: Iterable[Path | str], dest: Path | str, cell: int = 256, columns: int | None = None,
                  labels: Iterable[str] | None = None, background=(70, 70, 70)) -> Path:
    paths = [Path(p) for p in paths]
    labels = list(labels) if labels is not None else [p.stem for p in paths]
    n = len(paths)
    if n == 0:
        raise ValueError("no images for sheet")
    cols = columns or min(n, 8)
    rows = (n + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * (cell + 16)), background)
    draw = ImageDraw.Draw(sheet)
    for i, (p, label) in enumerate(zip(paths, labels)):
        im = Image.open(p).convert("RGBA")
        im.thumbnail((cell, cell))
        x, y = (i % cols) * cell, (i // cols) * (cell + 16)
        tile = Image.new("RGBA", (cell, cell), background + (255,))
        tile.alpha_composite(im, ((cell - im.width) // 2, (cell - im.height) // 2))
        sheet.paste(tile.convert("RGB"), (x, y))
        draw.text((x + 4, y + cell + 2), str(label)[:40], fill=(230, 230, 230))
    sheet.save(dest)
    return Path(dest)


def sprite_sheet(frames: list[Path | str], dest_png: Path | str, dest_json: Path | str, size: int, names: list[str]) -> tuple[Path, Path]:
    """Pack square frames of `size` into one row per 8 and write a JSON atlas {frames: {name: {x,y,w,h}}}."""
    n = len(frames)
    cols = min(n, 8) if n else 1
    rows = (n + cols - 1) // cols if n else 1
    sheet = Image.new("RGBA", (cols * size, rows * size), (0, 0, 0, 0))
    atlas: dict[str, Any] = {"size": size, "columns": cols, "rows": rows, "frames": {}}
    for i, (p, name) in enumerate(zip(frames, names)):
        im = Image.open(p).convert("RGBA").resize((size, size))
        x, y = (i % cols) * size, (i // cols) * size
        sheet.alpha_composite(im, (x, y))
        atlas["frames"][name] = {"x": x, "y": y, "w": size, "h": size}
    sheet.save(dest_png)
    Path(dest_json).write_text(json.dumps(atlas, indent=1), encoding="utf-8")
    return Path(dest_png), Path(dest_json)


def is_power_of_two(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


def image_size(path: Path | str) -> tuple[int, int]:
    with Image.open(path) as im:
        return im.size
