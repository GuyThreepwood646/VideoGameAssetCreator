"""Give every UV island a wider gutter without touching a texel inside any island.

The texture_padding check fails when the empty texels between islands (the gutter) are too few. Re-baking onto a new layout
fixes that but resamples every map. This module moves the islands instead: each keeps its shape, size and orientation and
is shifted by a whole number of texels (an even number, so a half-size map such as the occlusion map moves by whole texels
too). The texels of each island are copied to the new place unchanged in every map and in the images inside the GLB, the
UV coordinates move by exactly that many texels, and the new gutter is filled by growing the edge texels outward.
Nothing is baked or filtered. When the islands cannot be placed with the wanted gutter, or the result does not measure up,
the model is left as it is and the reason is returned.
"""
from __future__ import annotations

import io
import json
import math
import struct
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from . import imaging

OVERLAP_KEEP = 0.85  # the share of an island's texels that must have been its own before the move
STEP = 2  # islands move in steps of this many texels of the layout size


def _grow(mask: np.ndarray, r: int) -> np.ndarray:
    out = mask.copy()
    for _ in range(r):
        o = out.copy()
        o[1:] |= out[:-1]
        o[:-1] |= out[1:]
        out = o.copy()
        o[:, 1:] |= out[:, :-1]
        o[:, :-1] |= out[:, 1:]
        out = o
    return out


def plan(labels: np.ndarray, need: int) -> tuple[dict[int, tuple[int, int]] | None, str | None]:
    """Island label -> (rows, columns) to move it by, in texels (multiples of STEP), so that every pair of islands is at
    least `need` texels apart; or (None, reason). Largest islands first, each at the allowed spot nearest to where it is."""
    c = labels.shape[0] // STEP
    r = int(math.ceil(need / (2.0 * STEP)))  # each island grown by r cells on every side: apart by at least 2 r cells
    ids, counts = np.unique(labels[labels > 0], return_counts=True)
    occ = np.zeros((c, c), bool)
    shifts: dict[int, tuple[int, int]] = {}
    for i in ids[np.argsort(-counts)]:
        ys, xs = np.nonzero(labels == i)
        y0, y1, x0, x1 = ys.min() // STEP, ys.max() // STEP + 1, xs.min() // STEP, xs.max() // STEP + 1
        hh, ww = y1 - y0, x1 - x0
        sub = np.zeros((hh * STEP, ww * STEP), bool)
        part = labels[y0 * STEP:y1 * STEP, x0 * STEP:x1 * STEP] == i
        sub[:part.shape[0], :part.shape[1]] = part
        m = sub.reshape(hh, STEP, ww, STEP).any(axis=(1, 3))
        md = _grow(np.pad(m, r), r)
        h, w = md.shape
        oy, ox = y0 - r, x0 - r
        if h > c or w > c:
            return None, "an island is larger than the sheet allows"
        if 0 <= oy and 0 <= ox and oy + h <= c and ox + w <= c and not (occ[oy:oy + h, ox:ox + w] & md).any():
            py, px = oy, ox
        else:
            pad = np.zeros((c, c), np.float32)
            pad[:h, :w] = md
            ov = np.fft.irfft2(np.fft.rfft2(occ.astype(np.float32)) * np.conj(np.fft.rfft2(pad)), s=(c, c))
            ok = ov < 0.5
            ok[c - h + 1:, :] = False
            ok[:, c - w + 1:] = False
            if not ok.any():
                return None, "no room to separate the islands without scaling them"
            cy, cx = np.nonzero(ok)
            k = int(np.argmin((cy - oy) ** 2 + (cx - ox) ** 2))
            py, px = int(cy[k]), int(cx[k])
        occ[py:py + h, px:px + w] |= md
        shifts[int(i)] = ((py - oy) * STEP, (px - ox) * STEP)
    return shifts, None


def _moved(arr: np.ndarray, labels: np.ndarray, shifts: dict[int, tuple[int, int]], new_mask: np.ndarray, scale: float) -> np.ndarray | None:
    """arr with the texels of each island copied to its shifted place, then the gutter filled from the edge texels
    (new_mask: the texels the moved islands cover). labels, new_mask and arr share one size, `scale` times the layout size
    (the shifts are in layout texels). None when a shift is not a whole number of texels at that size."""
    out = np.zeros_like(arr)
    new_mask = new_mask.copy()
    for i, (dy, dx) in shifts.items():
        sy, sx = dy * scale, dx * scale
        if sy != int(sy) or sx != int(sx):
            return None
        ys, xs = np.nonzero(labels == i)
        out[ys + int(sy), xs + int(sx)] = arr[ys, xs]
        new_mask[ys + int(sy), xs + int(sx)] = True  # a copied texel is never overwritten by the fill
    grown = imaging.dilate(out, new_mask, imaging.dilation_px(arr.shape[0]))
    return imaging.fill_empty(grown, imaging.dilate(new_mask.astype(np.uint8), new_mask, imaging.dilation_px(arr.shape[0])) > 0)


def _png(arr: np.ndarray, mode: str) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr, mode).save(buf, "PNG")
    return buf.getvalue()


def spread(glb: Path, maps: dict[str, Path], out_dir: Path, tex: int, need: int) -> dict[str, Any]:
    """Spread the islands of `glb` (rewritten in place) and write every map of `maps` again into `out_dir` (as
    <name>_spread.png). -> {moved, reason, maps (the new files when moved, else the old ones), islands, gutter_before,
    gutter_after}."""
    glb, out_dir = Path(glb), Path(out_dir)
    result: dict[str, Any] = {"moved": False, "reason": None, "maps": dict(maps)}
    uv = imaging.load_uv_mesh(glb)
    if uv is None:
        result["reason"] = "the model has no texture layout"
        return result
    pad = imaging.unwrap_padding(tex)
    labels = imaging.rasterise_uv(uv, tex)["labels"]
    result["gutter_before"] = imaging.gutter_px(labels, pad // 2 + 2)
    shifts, why = plan(labels, need)
    if shifts is None:
        result["reason"] = why
        return result
    faces, island = uv["faces"], uv["island"]
    moved_uv = dict(uv)
    moved_uv["uv"] = uv["uv"].copy()
    vert_shift = np.zeros((len(uv["uv"]), 2))
    for i, (dy, dx) in shifts.items():
        sel = np.unique(faces[island == i - 1])  # labels are island + 1
        vert_shift[sel] = (dx / tex, dy / tex)  # the file's V runs down the image, like the texel rows
    moved_uv["uv"] = uv["uv"] + vert_shift * np.array([1.0, -1.0])  # trimesh flips V on load
    new_labels = imaging.rasterise_uv(moved_uv, tex)["labels"]
    # An island touching a neighbour lost its border texels to it in the first layout; once apart it has them back. An island
    # that lost much more than a border (islands lying on top of each other, as after a mirror) cannot be moved unchanged.
    if any(int((labels == i).sum()) < OVERLAP_KEEP * int((new_labels == i).sum()) for i in shifts):
        result["reason"] = "the islands overlap in the texture, so they cannot be moved apart unchanged"
        return result
    result["gutter_after"] = imaging.gutter_px(new_labels, pad // 2 + 2)
    if result["gutter_after"] < need:
        result["reason"] = "the islands could not be placed far enough apart"
        return result

    def labels_at(layout: dict[str, np.ndarray], size: int, base: np.ndarray) -> np.ndarray:
        return base if size == tex else imaging.rasterise_uv(layout, size)["labels"]

    new_maps: dict[str, Path] = {}
    written: dict[Path, bytes] = {}
    for key, p in maps.items():
        with Image.open(p) as im:
            mode = im.mode
            arr = np.array(im)
        size = arr.shape[0]
        out = _moved(arr, labels_at(uv, size, labels), shifts, labels_at(moved_uv, size, new_labels) > 0, size / tex)
        if out is None:
            result["reason"] = f"the {key} map has a size the move does not fit"
            return result
        dest = out_dir / f"{Path(p).stem}_spread.png"
        written[dest] = _png(out, mode)
        new_maps[key] = dest

    data = glb.read_bytes()
    jlen = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20:20 + jlen])
    bin_off = 20 + jlen
    bin_data = bytearray(data[bin_off + 8:bin_off + 8 + struct.unpack_from("<I", data, bin_off)[0]])
    prims = [pr for m in gltf.get("meshes") or [] for pr in m["primitives"]]
    if len(prims) != 1 or "TEXCOORD_0" not in prims[0]["attributes"]:
        result["reason"] = "the model is not one mesh with one texture layout"
        return result
    acc = gltf["accessors"][prims[0]["attributes"]["TEXCOORD_0"]]
    bv = gltf["bufferViews"][acc["bufferView"]]
    if acc["componentType"] != 5126 or acc["type"] != "VEC2" or bv.get("byteStride") not in (None, 8) or acc["count"] != len(uv["uv"]):
        result["reason"] = "the texture coordinates are not one plain float pair per vertex"
        return result
    o = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
    coords = np.frombuffer(bytes(bin_data[o:o + acc["count"] * 8]), dtype="<f4").reshape(-1, 2).astype(np.float64)
    bin_data[o:o + acc["count"] * 8] = (coords + vert_shift).astype("<f4").tobytes()
    replace: dict[int, bytes] = {}
    for im in gltf.get("images") or []:
        if "bufferView" not in im:
            continue
        v = gltf["bufferViews"][im["bufferView"]]
        start = v.get("byteOffset", 0)
        with Image.open(io.BytesIO(bytes(bin_data[start:start + v["byteLength"]]))) as pim:
            mode = pim.mode
            arr = np.array(pim)
        size = arr.shape[0]
        out = _moved(arr, labels_at(uv, size, labels), shifts, labels_at(moved_uv, size, new_labels) > 0, size / tex)
        if out is None:
            result["reason"] = "an image in the model has a size the move does not fit"
            return result
        replace[im["bufferView"]] = _png(out, mode)
    out_bin = bytearray()
    for vi, v in enumerate(gltf["bufferViews"]):
        if v.get("buffer", 0) != 0:
            continue
        start = v.get("byteOffset", 0)
        chunk = replace.get(vi, bytes(bin_data[start:start + v["byteLength"]]))
        while len(out_bin) % 4:
            out_bin.append(0)
        v["byteOffset"] = len(out_bin)
        v["byteLength"] = len(chunk)
        out_bin += chunk
    while len(out_bin) % 4:
        out_bin.append(0)
    gltf["buffers"][0]["byteLength"] = len(out_bin)
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    for dest, b in written.items():
        dest.write_bytes(b)
    glb.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(js) + 8 + len(out_bin)) + struct.pack("<I", len(js)) + b"JSON" + js
                    + struct.pack("<I", len(out_bin)) + b"BIN\x00" + bytes(out_bin))
    result.update(moved=True, maps=new_maps, islands=len(shifts), shifts=shifts)
    return result
