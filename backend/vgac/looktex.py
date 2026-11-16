"""Texture treatments for the looks with small or flat textures (styles.yaml recipe.surface switches; pure CPU work).

front_of_head: the triangles of a character's face, for the Retro 64 face layout (blender/faceuv.py gives them more of
the texture before the pictures are painted on) and for Low-poly's face_keeps_texture. The face finder of the face
pass is asked first (face.find_face_checked on a render of the head); stylised heads often defeat it (a hat filling the
top of the figure, a block-shaped head), so the fallback is plain geometry: the triangles of the head (down to where the
figure first gets much wider than the head: the shoulders, or the arms of a T pose) that face the camera and lie within
the head's width of its middle line.

colour_per_face: every triangle one colour, the median of the texels inside it (Low-poly: each plane one tone); the
kept triangles keep their texels. Look research 2026-11-16 (docs/research/looks/retro64-lowpoly.md, sheet
lowpoly_L1_sheet.png rows 3 and 6).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from . import face, imaging

FINDER_MIN_TRIANGLES = 8   # fewer triangles than this from the face finder: use the geometric fallback
FRONT_SHARE = (0.15, 0.40)  # of the height: the band the fallback looks in ends where the head does, within these (a big hat
                            # can fill the top 0.17, as on the dwarf; the beard runs on to about 0.40)
HEAD_TOP = 0.15             # of the height: the head's own width is measured up here
SHOULDER_JUMP = 2.5         # a slice this many times wider than the head is the shoulders or arms: the head ends above it
SLICE = 0.02                # of the height: the slices the width is measured in
FRONT_FACING = 0.25        # cosine between a triangle's normal and the view: at least this to count as facing forward
FRONT_HALF_WIDTH = 0.5     # of the band's width (5th to 95th percentile across): how far from the middle line counts


def front_of_head(mesh: dict[str, np.ndarray], tex: np.ndarray | None = None, head_yaw: float = 0.0) -> dict[str, Any]:
    """{tris (indices into mesh faces), how: "face finder" | "front of the head" | None}. tex: the colour the finder
    looks at (the finder is not asked without one)."""
    v, f = mesh["verts"], mesh["faces"]
    head = face.find_head(v)
    if tex is not None and head is not None:
        try:
            cam = face.head_camera(head, head_yaw, 1024)
            view = face.render_view(mesh, tex, cam)
            info = face.find_face_checked(view, mesh, cam, head)
            if info.get("found"):
                tris = face.face_triangles(view, info["region"])
                if len(tris) >= FINDER_MIN_TRIANGLES:
                    return {"tris": np.asarray(tris, dtype=np.int64), "how": "face finder"}
        except Exception:  # noqa: BLE001  (the finder is a help here, never a reason to fail the step)
            pass
    y = v[:, 1]
    top, h = float(y.max()), float(y.max() - y.min())
    if h <= 0:
        return {"tris": np.zeros(0, dtype=np.int64), "how": None}
    a = math.radians(head_yaw)
    toward = np.array([math.sin(a), 0.0, math.cos(a)])   # from the model toward a camera at head_yaw (face.Camera)
    across = np.array([math.cos(a), 0.0, -math.sin(a)])
    x = v @ across
    upper = y >= top - HEAD_TOP * h
    if upper.sum() < 3:
        return {"tris": np.zeros(0, dtype=np.int64), "how": None}
    mid = float(np.median(x[upper]))
    head_w = float(np.percentile(x[upper], 95) - np.percentile(x[upper], 5))
    share = head_share(y, x, top, h, head_w)
    half = FRONT_HALF_WIDTH * head_w
    c = v[f].mean(axis=1)
    n = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    sel = (c[:, 1] >= top - share * h) & (n @ toward >= FRONT_FACING) & (np.abs(c @ across - mid) <= half)
    tris = np.flatnonzero(sel)
    return {"tris": tris, "how": "front of the head" if len(tris) else None}


def head_share(y: np.ndarray, x: np.ndarray, top: float, h: float, head_w: float) -> float:
    """How far down from the top, as a share of the height, the head goes: to the first slice below HEAD_TOP that is
    SHOULDER_JUMP times wider than the head, within FRONT_SHARE."""
    lo, hi = FRONT_SHARE
    s = HEAD_TOP
    while s < hi:
        band = (y < top - s * h) & (y >= top - (s + SLICE) * h)
        if band.sum() >= 3 and float(np.percentile(x[band], 95) - np.percentile(x[band], 5)) > SHOULDER_JUMP * max(head_w, 1e-9):
            return max(lo, s)
        s += SLICE
    return hi


def triangle_map(mesh: dict[str, np.ndarray], size: int) -> np.ndarray:
    """(size, size) int: the triangle each texel belongs to, -1 where none (the UV layout rasterised)."""
    uvp = face.uv_pixels(mesh["uv"], size)
    return face.rasterise(uvp, np.zeros(len(mesh["uv"])), mesh["faces"], size)["tri"]


def colour_per_face(base: np.ndarray, mesh: dict[str, np.ndarray], keep: np.ndarray | None = None) -> dict[str, Any]:
    """base (T, T, 3) floats 0..1 -> {base_color (T, T, 3), faces (triangles made one colour), kept (texels left as
    painted)}. Every triangle takes the median of the texels inside it; one too small to own a texel takes the colour
    at its UV centre. The triangles in keep stay as painted. The empty space is grown from the islands again."""
    T = base.shape[0]
    tri = triangle_map(mesh, T)
    inside = tri >= 0
    t, col = tri[inside], base[inside]
    order = np.argsort(t, kind="stable")
    t, col = t[order], col[order]
    ids, start = np.unique(t, return_index=True)
    ends = np.append(start[1:], len(t))
    uvc = face.uv_pixels(mesh["uv"], T)[mesh["faces"]].mean(axis=1)
    colour = face.sample(base, uvc[:, 0], uvc[:, 1])
    for i, (s, e) in enumerate(zip(start, ends)):
        colour[ids[i]] = np.median(col[s:e], axis=0)
    out = base.copy()
    flat = inside.copy()
    if keep is not None and len(keep):
        kept = np.zeros(len(mesh["faces"]), dtype=bool)
        kept[np.asarray(keep, dtype=np.int64)] = True
        flat &= ~kept[np.maximum(tri, 0)]
    out[flat] = colour[tri[flat]]
    grown = np.empty_like(out)
    for ch in range(3):
        grown[..., ch] = imaging.dilate(out[..., ch], inside, imaging.dilation_px(T))
    return {"base_color": grown, "faces": int(len(mesh["faces"]) - (0 if keep is None else len(keep))),
            "kept": int((inside & ~flat).sum())}
