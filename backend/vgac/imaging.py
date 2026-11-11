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


# --- Texture space (G03, G05) ----------------------------------------------------------------------------------
#
# The UV layout of a GLB rasterised at a texture size: which texels a triangle covers (the coverage mask), which UV
# island owns each texel (labels), and the up component of each triangle's normal. The gutter between islands, the
# share of the atlas in use, dilation of baked maps into the empty space, and the light score all build on it.
# glTF puts the UV origin at the top left of the image; trimesh flips V on load, so a texel row is (1 - v) * size.

UP_AXIS = 1  # glTF and trimesh are Y-up


def unwrap_padding(texture_size: int) -> int:
    """UV island padding in texels for a texture size (docs/GREAT-ASSETS-PLAN.md 3.5): max(4, size / 256)."""
    return max(4, int(texture_size) // 256)


def dilation_px(texture_size: int) -> int:
    """How far each baked map is grown into the empty texture space: max(8, size / 128)."""
    return max(8, int(texture_size) // 128)


def _neighbours() -> list[tuple[int, int]]:
    return [(dy, dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx]


def _shift(a: np.ndarray, dy: int, dx: int, fill: Any = 0) -> np.ndarray:
    """a moved by (dy, dx): out[y, x] = a[y - dy, x - dx], with `fill` where that falls outside."""
    out = np.full_like(a, fill)
    h, w = a.shape[:2]
    ys, yd = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else (slice(-dy, h), slice(0, h + dy))
    xs, xd = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else (slice(-dx, w), slice(0, w + dx))
    out[yd, xd] = a[ys, xs]
    return out


def face_islands(faces: np.ndarray, vertex_count: int) -> np.ndarray:
    """Connected components of faces through shared vertices (union-find), numbered 0..k-1 per face."""
    parent = list(range(int(vertex_count)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, c in faces.tolist():
        for v in (b, c):
            ra, rv = find(a), find(v)
            if ra != rv:
                parent[rv] = ra
    roots = np.array([find(f[0]) for f in faces.tolist()], dtype=np.int64)
    _, island = np.unique(roots, return_inverse=True)
    return island.astype(np.int64)


def load_uv_mesh(glb: Path | str) -> dict[str, np.ndarray] | None:
    """{uv (n, 2), faces (m, 3), up (m,) the up component of each face normal, island (m,) the UV island of each face}
    from a GLB, or None when it has no UVs. Islands are faces connected through shared vertices; glTF vertices are
    split along UV seams, so that is the UV island."""
    import trimesh

    mesh = trimesh.load(str(glb), force="mesh", process=False)
    uv = getattr(getattr(mesh, "visual", None), "uv", None)
    if uv is None or len(uv) != len(mesh.vertices) or len(mesh.faces) == 0:
        return None
    faces = np.asarray(mesh.faces)
    # Exporters also split vertices along hard edges, so join vertices that share a UV position before finding the
    # islands (two islands meeting at an identical UV position only happens where they overlap anyway).
    uv_key = np.round(np.asarray(uv, dtype=np.float64) * 2**20).astype(np.int64)
    _, uv_id = np.unique(uv_key, axis=0, return_inverse=True)
    uv_id = uv_id.reshape(-1)
    island = face_islands(uv_id[faces], int(uv_id.max()) + 1)
    normals = np.asarray(mesh.face_normals)
    return {"uv": np.asarray(uv, dtype=np.float64), "faces": faces, "up": normals[:, UP_AXIS], "island": np.asarray(island)}


def rasterise_uv(uvmesh: dict[str, np.ndarray], size: int) -> dict[str, np.ndarray]:
    """{labels: int32 (size, size), 0 empty else island + 1; up: float32 (size, size), the face up component, NaN
    where empty}. Later triangles overwrite earlier ones where UVs overlap."""
    uv, faces = uvmesh["uv"], uvmesh["faces"]
    px = np.empty_like(uv)
    px[:, 0] = uv[:, 0] * size - 0.5
    px[:, 1] = (1.0 - uv[:, 1]) * size - 0.5
    lab = Image.new("I", (size, size), 0)
    upi = Image.new("L", (size, size), 0)
    dl, du = ImageDraw.Draw(lab), ImageDraw.Draw(upi)
    for f, isl, up in zip(faces, uvmesh["island"], uvmesh["up"]):
        pts = [(float(px[i, 0]), float(px[i, 1])) for i in f]
        dl.polygon(pts, fill=int(isl) + 1)
        du.polygon(pts, fill=int(round((float(up) + 1.0) * 127.0)) + 1)  # 1..255; 0 stays empty
    labels = np.array(lab, dtype=np.int32)
    upa = np.array(upi, dtype=np.float32)
    up = np.where(upa > 0, (upa - 1.0) / 127.0 - 1.0, np.nan).astype(np.float32)
    return {"labels": labels, "up": up}


def atlas_used(labels: np.ndarray) -> float:
    """Share of the texture that some triangle samples."""
    return float((labels > 0).mean())


def island_gaps(labels: np.ndarray, max_px: int) -> np.ndarray:
    """For each island that covers at least one texel, the smallest number of empty texels between it and any other
    island (8-neighbour steps), capped at 2 * max_px + 1 when no other island is that close."""
    owner = labels.copy()
    dist = np.where(labels > 0, 0, -1).astype(np.int32)
    for step in range(1, int(max_px) + 1):
        empty = owner == 0
        if not empty.any():
            break
        new_owner, new_dist = owner.copy(), dist.copy()
        for dy, dx in _neighbours():
            src = _shift(owner, dy, dx)
            take = empty & (src > 0) & (new_owner == 0)
            new_owner[take] = src[take]
            new_dist[take] = step
        owner, dist = new_owner, new_dist
    n = int(labels.max())
    cap = 2 * int(max_px) + 1
    gaps = np.full(n + 1, cap, dtype=np.int64)
    for dy, dx in ((0, 1), (1, 0), (1, 1), (1, -1)):
        a, b = owner, _shift(owner, dy, dx)
        da, db = dist, _shift(dist, dy, dx, -1)
        meet = (a > 0) & (b > 0) & (a != b)
        g = (da + db)[meet]
        np.minimum.at(gaps, a[meet], g)
        np.minimum.at(gaps, b[meet], g)
    present = np.bincount(labels.ravel(), minlength=n + 1) > 0
    present[0] = False
    return gaps[present]


def gutter_px(labels: np.ndarray, max_px: int, percentile: float = 10.0) -> int:
    """The gutter most islands have: the given low percentile of island_gaps (a few islands pressed together by the
    packer do not decide it). max_px bounds the search; the answer is at most 2 * max_px + 1."""
    gaps = island_gaps(labels, max_px)
    if gaps.size == 0:
        return 0
    return int(np.percentile(gaps, percentile, method="lower"))


def dilate(img: np.ndarray, mask: np.ndarray, px: int) -> np.ndarray:
    """Grow the covered texels (mask) into the empty space by px texels, each empty texel taking the colour of a
    nearest covered one (8-neighbour rings). Texels further than px keep their value."""
    out = img.copy()
    filled = mask.astype(bool).copy()
    for _ in range(int(px)):
        if filled.all():
            break
        new_filled = filled.copy()
        for dy, dx in _neighbours():
            take = ~new_filled & _shift(filled, dy, dx, False)
            if take.any():
                out[take] = _shift(out, dy, dx)[take]
                new_filled |= take
        filled = new_filled
    return out


def fill_empty(img: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Fill every texel outside the mask with a smooth blend of the nearby covered colours (push-pull: average down a
    pyramid of halvings, then fill each level's holes from the next coarser level). Covered texels keep their value.
    Used after dilate so the far empty space is not black, which would bleed into the seams at small mip levels."""
    m = mask.astype(bool)
    if m.all() or not m.any():
        return img.copy()
    src = img.astype(np.float32)
    if src.ndim == 2:
        src = src[..., None]
    levels = [(src * m[..., None], m.astype(np.float32))]
    while min(levels[-1][1].shape) > 1:
        c, w = levels[-1]
        h2, w2 = (c.shape[0] + 1) // 2, (c.shape[1] + 1) // 2
        cp = np.zeros((h2 * 2, w2 * 2, c.shape[2]), np.float32)
        wp = np.zeros((h2 * 2, w2 * 2), np.float32)
        cp[:c.shape[0], :c.shape[1]], wp[:w.shape[0], :w.shape[1]] = c, w
        levels.append((cp.reshape(h2, 2, w2, 2, -1).sum((1, 3)), wp.reshape(h2, 2, w2, 2).sum((1, 3))))
    colour = levels[-1][0] / np.maximum(levels[-1][1], 1e-6)[..., None]
    for c, w in reversed(levels[:-1]):
        up = np.repeat(np.repeat(colour, 2, axis=0), 2, axis=1)[:c.shape[0], :c.shape[1]]
        have = w > 0
        colour = np.where(have[..., None], c / np.maximum(w, 1e-6)[..., None], up)
    out = np.where(m[..., None], src, colour)
    out = np.clip(np.round(out), 0, 255).astype(img.dtype) if img.dtype == np.uint8 else out.astype(img.dtype)
    return out[..., 0] if img.ndim == 2 else out


def dilate_png(path: Path | str, mask: np.ndarray, px: int, fill_rest: bool = False) -> None:
    """Dilate a map in place (the mask is resized to the map when their sizes differ)."""
    with Image.open(path) as im:
        mode = im.mode
        arr = np.array(im)
    if arr.shape[:2] != mask.shape:
        mask = np.array(Image.fromarray(mask.astype(np.uint8) * 255).resize((arr.shape[1], arr.shape[0]), Image.NEAREST)) > 127
    out = dilate(arr, mask, px)
    if fill_rest:
        grown = dilate(mask.astype(np.uint8), mask, px) > 0
        out = fill_empty(out, grown)
    Image.fromarray(out, mode).save(path)


def _uv_rows(m: dict[str, np.ndarray]) -> np.ndarray:
    """One row per triangle: its three UV corners on a 1/4096 grid, in a fixed order (so a flipped winding or a
    rotated corner order is the same triangle)."""
    tri = np.round(m["uv"][m["faces"]] * 4096).astype(np.int64)  # (m, 3, 2)
    key = tri[..., 0] * (1 << 20) + tri[..., 1]
    order = np.argsort(key, axis=1)
    tri = np.take_along_axis(tri, order[..., None], axis=1)
    return tri.reshape(len(m["faces"]), 6)


def uv_hash(uvmesh: dict[str, np.ndarray] | None) -> str | None:
    """A hash of the mesh as the textures see it: its UV triangles (rounded, order-free), which turning, scaling and
    moving the model leave alone. Equal hashes mean the maps were baked on these triangles."""
    import hashlib

    if uvmesh is None:
        return None
    rows = _uv_rows(uvmesh)
    rows = rows[np.lexsort(rows.T[::-1])]
    return hashlib.sha1(rows.tobytes()).hexdigest()[:16]


def uv_match_fraction(bake: dict[str, np.ndarray] | None, final: dict[str, np.ndarray] | None) -> float | None:
    """Share of the final mesh's UV triangles that are also in the mesh the maps were baked on (1.0: the same mesh)."""
    if bake is None or final is None:
        return None
    b = {r.tobytes() for r in _uv_rows(bake)}
    f = [r.tobytes() for r in _uv_rows(final)]
    return sum(1 for r in f if r in b) / max(1, len(f))


# --- Colour and light (G07, G08) ---------------------------------------------------------------------------------
#
# The physically based ranges are the Substance PBR guidance [S28]: non-metal base colour between 30 and 240 sRGB,
# metallic near 0 or 1, roughness above 0.

ALBEDO_MIN, ALBEDO_MAX = 30, 240
METAL_MIXED_LO, METAL_MIXED_HI = 0.2, 0.8
ROUGHNESS_FLOOR = 0.04


def read_map(path: Path | str | None, size: int | None = None, grey: bool = False) -> np.ndarray | None:
    """A map as float32 in 0..1: (h, w, 3) RGB, or (h, w) when grey; resized to size x size when given. Resized
    with Lanczos (antialiased; measured on the knight's maps it beats bilinear by about 1 dB both shrinking and
    enlarging, colour and data maps alike; the 8-bit result is clipped, so the overshoot cannot leave 0..255)."""
    if not path or not Path(path).is_file():
        return None
    with Image.open(path) as im:
        im = im.convert("L" if grey else "RGB")
        if size and im.size != (size, size):
            im = im.resize((size, size), Image.LANCZOS)
        return np.asarray(im, dtype=np.float32) / 255.0


def write_map(arr: np.ndarray, path: Path | str) -> Path:
    a = np.clip(np.round(arr * 255.0), 0, 255).astype(np.uint8)
    Image.fromarray(a, "RGB" if a.ndim == 3 else "L").save(path)
    return Path(path)


def srgb_luminance(rgb: np.ndarray) -> np.ndarray:
    """Rec. 709 weights on sRGB-encoded values, 0..1: how bright a colour reads, in the units the range rules use."""
    return rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    if a.size < 16:
        return 0.0
    a = a.astype(np.float64) - float(a.mean())
    b = b.astype(np.float64) - float(b.mean())
    d = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / d) if d > 1e-9 else 0.0


def light_score(base: np.ndarray, mask: np.ndarray, ao: np.ndarray | None = None, up: np.ndarray | None = None) -> dict[str, Any]:
    """How much lighting is painted into the base colour: the correlation of log luminance with the AO (dark in the
    crevices) and with the up component of the surface normal (bright on top, dark underneath), over covered texels.
    score is the larger of the two, 0 when neither is positive."""
    lum = np.log(np.clip(srgb_luminance(base), 1 / 255, 1.0))
    m = mask.astype(bool)
    corr_ao = _pearson(lum[m], ao[m]) if ao is not None else None
    corr_up = None
    if up is not None:
        mu = m & ~np.isnan(up)
        corr_up = _pearson(lum[mu], up[mu])
    vals = [v for v in (corr_ao, corr_up) if v is not None]
    return {"score": round(max([0.0] + vals), 4), "corr_ao": None if corr_ao is None else round(corr_ao, 4),
            "corr_up": None if corr_up is None else round(corr_up, 4)}


def delight(base: np.ndarray, mask: np.ndarray, ao: np.ndarray | None, up: np.ndarray | None, strength: float) -> np.ndarray:
    """Remove painted-on light: fit log luminance as a linear function of the AO and the up normal over the covered
    texels, then divide out `strength` times the fitted shading (only the parts that brighten with AO and with facing
    up). The three channels scale together, so the hue stays; the shading is centred, so the mean brightness stays."""
    m = mask.astype(bool)
    cols = [c for c in (ao, None if up is None else np.nan_to_num(up, nan=0.0)) if c is not None]
    if not cols or not m.any() or strength <= 0:
        return base.copy()
    y = np.log(np.clip(srgb_luminance(base), 1 / 255, 1.0))
    X = np.stack([c[m] for c in cols] + [np.ones(int(m.sum()))], axis=1).astype(np.float64)
    coef, *_ = np.linalg.lstsq(X, y[m].astype(np.float64), rcond=None)
    shade = np.zeros_like(y)
    for c, k in zip(cols, coef[:-1]):
        if k > 0:
            shade += float(k) * (c - float(c[m].mean()))
    out = np.clip(base * np.exp(-float(strength) * shade)[..., None], 0.0, 1.0)
    out[~m] = base[~m]
    return out


def delight_strength(score: float, limit: float) -> float:
    """How strongly the correction runs for a light score: none at the limit, full at twice the limit, and never
    below a half-strength pass once it runs."""
    if score <= limit:
        return 0.0
    return float(min(1.0, max(0.5, (score - limit) / max(1e-6, limit) + 0.5)))


def pbr_stats(base: np.ndarray, metallic: np.ndarray | None, roughness: np.ndarray | None, mask: np.ndarray) -> dict[str, Any]:
    """Range figures over covered texels: albedo_out (share of non-metal texels darker than 30 or brighter than 240
    sRGB), albedo_black and albedo_white (shares below 4 and above 250), metal_mixed (share with metallic between 0.2
    and 0.8), roughness_low (share below 0.04)."""
    m = mask.astype(bool)
    lum = srgb_luminance(base) * 255.0
    nonmetal = m & (metallic < 0.5) if metallic is not None else m
    n = max(1, int(nonmetal.sum()))
    mc = max(1, int(m.sum()))
    out: dict[str, Any] = {
        "albedo_out": float(((lum < ALBEDO_MIN) | (lum > ALBEDO_MAX))[nonmetal].sum()) / n,
        "albedo_black": float((lum < 4)[nonmetal].sum()) / n,
        "albedo_white": float((lum > 250)[nonmetal].sum()) / n,
        "metal_mixed": None if metallic is None else float(((metallic > METAL_MIXED_LO) & (metallic < METAL_MIXED_HI))[m].sum()) / mc,
        "roughness_low": None if roughness is None else float((roughness < ROUGHNESS_FLOOR)[m].sum()) / mc,
    }
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out.items()}


def clamp_albedo(base: np.ndarray, metallic: np.ndarray | None) -> np.ndarray:
    """Soft-clamp non-metal colours into 30 to 240 sRGB luminance. Each texel is scaled (hue kept) so its luminance
    lands inside the range; the last 10 levels at each end ease in rather than cut hard. Metal texels are untouched."""
    lum = srgb_luminance(base) * 255.0
    lo, hi, knee = float(ALBEDO_MIN), float(ALBEDO_MAX), 10.0
    target = lum.copy()
    low = lum < lo + knee
    t = np.clip(lum[low] / (lo + knee), 0, 1)
    target[low] = lo + knee * t * t * (3 - 2 * t)
    high = lum > hi - knee
    t = np.clip((lum[high] - (hi - knee)) / (255.0 - (hi - knee)), 0, 1)
    target[high] = (hi - knee) + knee * t * (2 - t)
    out = base * (target / np.maximum(lum, 1e-3))[..., None]
    black = lum <= 1e-3
    out[black] = lo / 255.0  # a black texel cannot be scaled up: it takes the floor grey
    out = np.clip(out, 0, 1)
    if metallic is not None:
        metal = metallic >= 0.5
        out[metal] = base[metal]
    return out


def sharpen_metallic(metallic: np.ndarray) -> np.ndarray:
    """Push metallic toward 0 or 1: a contrast of 4 about 0.5, clipped (0.2 becomes 0, 0.8 becomes 1)."""
    return np.clip((metallic - 0.5) * 4.0 + 0.5, 0.0, 1.0)


def clamp_roughness(roughness: np.ndarray) -> np.ndarray:
    return np.maximum(roughness, ROUGHNESS_FLOOR)


# --- GLB image replacement --------------------------------------------------------------------------------------


GL_NEAREST = 9728
# A 1 x 1 flat normal (128, 128, 255) PNG: what a dropped normal map's image becomes, so the file loses its bytes
# without renumbering every buffer view.
_FLAT_NORMAL_PNG: bytes | None = None


def _flat_normal_png() -> bytes:
    global _FLAT_NORMAL_PNG
    if _FLAT_NORMAL_PNG is None:
        import io

        buf = io.BytesIO()
        Image.new("RGB", (1, 1), (128, 128, 255)).save(buf, "PNG")
        _FLAT_NORMAL_PNG = buf.getvalue()
    return _FLAT_NORMAL_PNG


def glb_replace_images(glb: Path | str, base_color: Path | str | None = None, metallic: Path | str | None = None,
                       roughness: Path | str | None = None, *, drop_normal: bool = False, nearest: bool = False,
                       mr_size: int | None = None) -> bool:
    """Swap, in place, the base colour image and the green (roughness) and blue (metallic) channels of the
    metallic-roughness image of a GLB's first material. The binary chunk is rebuilt view by view; every other view
    keeps its bytes. Returns False, leaving the file alone, when there is nothing to change.

    Recipes: mr_size resizes the metallic-roughness image (and with it the occlusion in its red channel, when the
    material shares it) to mr_size square first; drop_normal removes every material's normal map (its image becomes a
    1 x 1 flat normal, unreferenced); nearest sets every texture sampler to nearest-neighbour without mipmaps."""
    import io
    import struct

    p = Path(glb)
    data = p.read_bytes()
    if data[:4] != b"glTF":
        return False
    jlen = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20:20 + jlen])
    bin_off = 20 + jlen
    blen = struct.unpack_from("<I", data, bin_off)[0]
    bin_data = data[bin_off + 8: bin_off + 8 + blen]
    mats = gltf.get("materials") or []
    images = gltf.get("images") or []
    views = gltf.get("bufferViews") or []
    if not mats or not images:
        return False
    pbr = mats[0].get("pbrMetallicRoughness") or {}
    changed_json = False
    if drop_normal:
        for m in mats:
            ref = m.pop("normalTexture", None)
            if ref is None:
                continue
            changed_json = True
    if nearest and gltf.get("textures"):
        samplers = gltf.setdefault("samplers", [])
        if not samplers:
            samplers.append({})
        for s in samplers:
            s["magFilter"] = GL_NEAREST
            s["minFilter"] = GL_NEAREST
        for t in gltf["textures"]:
            t.setdefault("sampler", 0)
        changed_json = True

    def image_index(ref: dict[str, Any] | None) -> int | None:
        if not ref:
            return None
        src = (gltf.get("textures") or [])[ref["index"]].get("source")
        return src if src is not None and "bufferView" in images[src] else None

    def view_bytes(view_i: int) -> bytes:
        bv = views[view_i]
        o = bv.get("byteOffset", 0)
        return bin_data[o:o + bv["byteLength"]]

    replace: dict[int, bytes] = {}  # buffer view index -> new bytes
    bi = image_index(pbr.get("baseColorTexture"))
    if base_color and bi is not None:
        replace[images[bi]["bufferView"]] = Path(base_color).read_bytes()
        images[bi]["mimeType"] = "image/png"
    if drop_normal:
        used = {(gltf.get("textures") or [])[ref["index"]].get("source")
                for m in mats for ref in [*(m.get("pbrMetallicRoughness") or {}).values(), m.get("occlusionTexture"), m.get("emissiveTexture")]
                if isinstance(ref, dict) and "index" in ref}
        for i, im in enumerate(images):
            if i not in used and "bufferView" in im and im["bufferView"] not in replace:
                replace[im["bufferView"]] = _flat_normal_png()
                im["mimeType"] = "image/png"
    mi = image_index(pbr.get("metallicRoughnessTexture"))
    if mi is not None and (metallic or roughness or mr_size):
        with Image.open(io.BytesIO(view_bytes(images[mi]["bufferView"]))) as im:
            rgb = im.convert("RGB")
            if mr_size and rgb.size != (mr_size, mr_size):
                rgb = rgb.resize((mr_size, mr_size), Image.NEAREST if nearest else Image.LANCZOS)
            arr = np.array(rgb)
        for path, ch in ((roughness, 1), (metallic, 2)):
            if path:
                with Image.open(path) as src:
                    arr[..., ch] = np.array(src.convert("L").resize((arr.shape[1], arr.shape[0]), Image.BILINEAR))
        buf = io.BytesIO()
        Image.fromarray(arr, "RGB").save(buf, "PNG")
        replace[images[mi]["bufferView"]] = buf.getvalue()
        images[mi]["mimeType"] = "image/png"
    if not replace and not changed_json:
        return False
    new_bin = bytearray()
    for vi, bv in enumerate(views):
        if bv.get("buffer", 0) != 0:
            continue
        chunk = replace.get(vi, view_bytes(vi))
        while len(new_bin) % 4:
            new_bin.append(0)
        bv["byteOffset"] = len(new_bin)
        bv["byteLength"] = len(chunk)
        new_bin += chunk
    while len(new_bin) % 4:
        new_bin.append(0)
    gltf["buffers"][0]["byteLength"] = len(new_bin)
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(new_bin)
    p.write_bytes(struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I", len(js)) + b"JSON" + js
                  + struct.pack("<I", len(new_bin)) + b"BIN\x00" + bytes(new_bin))
    return True


# --- Recipes: palettes and texture treatment (reference.recipe surface) ------------------------------------------

# Plain colour names for the palette sentence of the concept prompt (painters read words, not hex codes).
COLOUR_NAMES: tuple[tuple[str, tuple[int, int, int]], ...] = (
    ("black", (20, 20, 20)), ("charcoal", (54, 54, 58)), ("dark grey", (85, 85, 85)), ("grey", (128, 128, 128)),
    ("light grey", (190, 190, 190)), ("white", (245, 245, 245)), ("cream", (236, 222, 190)), ("beige", (200, 180, 140)),
    ("dark brown", (80, 50, 25)), ("brown", (130, 85, 45)), ("tan", (190, 150, 100)), ("gold", (205, 165, 75)),
    ("yellow", (245, 215, 60)), ("pale yellow", (250, 235, 150)), ("orange", (240, 130, 40)), ("coral", (240, 125, 90)),
    ("red", (200, 40, 40)), ("dark red", (130, 25, 30)), ("crimson", (175, 30, 60)), ("pink", (240, 150, 180)),
    ("maroon", (95, 30, 50)), ("purple", (120, 50, 140)), ("plum", (95, 40, 95)), ("lavender", (180, 160, 220)),
    ("navy", (25, 35, 80)), ("dark blue", (30, 50, 120)), ("blue", (55, 95, 200)), ("steel blue", (50, 80, 115)),
    ("sky blue", (120, 180, 230)), ("teal", (20, 120, 130)), ("turquoise", (40, 200, 180)), ("mint green", (150, 230, 170)),
    ("lime green", (165, 235, 110)), ("green", (55, 160, 80)), ("dark green", (30, 85, 45)), ("olive", (100, 110, 60)),
    ("sage", (140, 160, 120)),
)


def parse_hex(value: Any) -> tuple[int, int, int] | None:
    """"#5b3a1e", "5b3a1e" or "#fff" -> (r, g, b); None for anything else."""
    if not isinstance(value, str):
        return None
    h = value.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return None


def parse_palette(palette: Any, limit: int = 64) -> list[tuple[int, int, int]]:
    out = []
    for v in palette or []:
        c = parse_hex(v)
        if c is not None and c not in out:
            out.append(c)
    return out[:limit]


def _colour_distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    """The "redmean" weighted RGB distance: close enough to how people see colour for naming."""
    rm = (a[0] + b[0]) / 2
    dr, dg, db = a[0] - b[0], a[1] - b[1], a[2] - b[2]
    return ((2 + rm / 256) * dr * dr + 4 * dg * dg + (2 + (255 - rm) / 256) * db * db) ** 0.5


def colour_name(rgb: tuple[int, int, int]) -> str:
    return min(COLOUR_NAMES, key=lambda nc: _colour_distance(rgb, nc[1]))[0]


def palette_names(palette: Any, limit: int = 8) -> list[str]:
    """The palette's colours as plain names, without repeats, at most `limit`."""
    out: list[str] = []
    for c in parse_palette(palette):
        n = colour_name(c)
        if n not in out:
            out.append(n)
    return out[:limit]


def shrink_image(path: Path | str, size: int, crisp: bool, dest: Path | str | None = None) -> Path:
    """Resize a map to size x size when it is larger: nearest neighbour for crisp looks (every texel is one whole
    source texel, no blended in-between colours), a box filter otherwise (an even average, the classic small soft
    texture). Writes dest (default: in place) in the same mode; a map already at or under the size is copied as is."""
    p = Path(path)
    out = Path(dest) if dest else p
    with Image.open(p) as im:
        im.load()
        if max(im.size) > size:
            im = im.resize((size, size), Image.NEAREST if crisp else Image.BOX)
        im.save(out)
    return out


def quantise_colours(base: np.ndarray, mask: np.ndarray | None = None, colours: int | None = None,
                     palette: list[tuple[int, int, int]] | None = None) -> np.ndarray:
    """A base colour (h, w, 3, floats 0..1) limited to a few colours, without dithering: to the fixed `palette` when
    given (each texel takes the nearest palette colour), else to `colours` picked from the texels inside `mask` (median
    cut). Texels outside the mask take their nearest colour too, so the gutter matches its piece."""
    a = np.clip(np.round(base * 255.0), 0, 255).astype(np.uint8)
    h, w = a.shape[:2]
    if palette:
        pal = np.asarray(palette, dtype=np.float32)
    else:
        n = max(2, min(256, int(colours or 32)))
        sample = a[mask] if mask is not None and mask.any() else a.reshape(-1, 3)
        side = int(np.ceil(np.sqrt(len(sample))))
        pad = np.zeros((side * side, 3), np.uint8)
        pad[:len(sample)] = sample
        pad[len(sample):] = sample[0] if len(sample) else 0
        q = Image.fromarray(pad.reshape(side, side, 3), "RGB").quantize(colors=n, method=Image.Quantize.MEDIANCUT,
                                                                        dither=Image.Dither.NONE)
        pal = np.asarray(q.getpalette()[:3 * n], dtype=np.float32).reshape(-1, 3)
        used = np.unique(np.asarray(q))
        pal = pal[used[used < len(pal)]]
    flat = a.reshape(-1, 3).astype(np.float32)
    best = np.zeros(len(flat), dtype=np.int64)
    best_d = np.full(len(flat), np.inf, dtype=np.float32)
    for i, c in enumerate(pal):  # a loop over palette colours keeps memory at one distance row
        rm = (flat[:, 0] + c[0]) / 2
        d = (2 + rm / 256) * (flat[:, 0] - c[0]) ** 2 + 4 * (flat[:, 1] - c[1]) ** 2 + (2 + (255 - rm) / 256) * (flat[:, 2] - c[2]) ** 2
        closer = d < best_d
        best[closer] = i
        best_d[closer] = d[closer]
    return (pal[best].reshape(h, w, 3) / 255.0).astype(np.float32)


def colour_count(path: Path | str, mask: np.ndarray | None = None) -> int:
    """How many different colours a map uses (inside mask when given)."""
    with Image.open(path) as im:
        a = np.asarray(im.convert("RGB"))
    px = a[mask] if mask is not None and mask.shape == a.shape[:2] else a.reshape(-1, 3)
    return int(len(np.unique(px.reshape(-1, 3), axis=0)))
