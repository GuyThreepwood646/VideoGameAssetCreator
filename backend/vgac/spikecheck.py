"""Head spike check: the tallest thin flap of surface at the lids.

The 3D builder can grow flaps of geometry at the lids (several millimetres on a 1.75 m person). A depth picture of the
face is taken from the front and "opened" with a small disc: anything narrower than twice the disc's radius is taken off,
while broad shapes, slopes and steps (brow, nose, lid fold) stay. What was taken off is the height of the thin things.
Only an oval round each eye is read, so the brow and the side of the nose are left out, and so is anything well in
front of the skin there (strands of hair). "In front" is judged from the lid surface itself, not from the eye point,
which lies deep when a head has holes for eyes. The eyes are found on a textured render of the head with the face finder, then lifted
onto the surface.

protrusions() is the pure measure; measure_glb() reads a finished build. Never raises: no finder, no face, or no texture
gives None, and the check is then not made.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from . import face

PERSON_MM = 1750.0  # all lengths are read on the model scaled to this height
PX_MM = 0.5  # one pixel of the depth picture
VIEW_MM = 170.0  # the width and height of the picture, centred between the eyes
OPEN_MM = 4.0  # the disc's radius: things narrower than twice this are what is measured
MIN_AREA_MM2 = 8.0  # the value is the height reached by at least this much of the picture, so single pixels do not count
# The area round each eye, in shares of the distance between the eyes: an oval over the lids that stops short of the nose,
# and no further from the eye than BALL_SHARE in three dimensions.
ACROSS_SHARE = 0.42
UPDOWN_SHARE = 0.27
NOSEWARD_SHARE = 0.32
BALL_SHARE = 0.45
# The skin level of an eye's area is this percentile of its depths toward the viewer (high enough that a hole where the eye
# is does not pull it back); surface more than AHEAD_MM in front of it is not part of the lids.
SKIN_PERCENTILE = 75.0
AHEAD_MM = 13.0
REACH_MM = 130.0  # triangles further than this from the middle between the eyes are not drawn
LIMIT_MM = 2.0  # the check's limit
YAWS = (0, 90, 180, 270)


def _disc(radius: int) -> list[tuple[int, int]]:
    span = range(-radius, radius + 1)
    return [(dy, dx) for dy in span for dx in span if dx * dx + dy * dy <= radius * radius + 0.5]


def _filter(a: np.ndarray, offsets: list[tuple[int, int]], fn: np.ufunc, fill: float) -> np.ndarray:
    """Running minimum or maximum of a picture over the offsets."""
    r = max(max(abs(y), abs(x)) for y, x in offsets)
    padded = np.pad(a, r, constant_values=fill)
    h, w = a.shape
    out = np.full(a.shape, fill)
    for dy, dx in offsets:
        fn(out, padded[r + dy:r + dy + h, r + dx:r + dx + w], out=out)
    return out


def _thin_height(depth: np.ndarray) -> np.ndarray:
    """What an opening takes off a picture of heights toward the viewer (nan where there is no surface), per pixel."""
    offsets = _disc(int(round(OPEN_MM / PX_MM)))
    valid = np.isfinite(depth)
    low = _filter(np.where(valid, depth, np.inf), offsets, np.minimum, np.inf)
    opened = _filter(np.where(valid & np.isfinite(low), low, -np.inf), offsets, np.maximum, -np.inf)
    return np.where(valid & np.isfinite(opened), depth - np.where(np.isfinite(opened), opened, 0.0), 0.0)


def protrusions(verts: np.ndarray, faces: np.ndarray, eyes3d: list[np.ndarray], yaw_deg: float, height: float) -> dict[str, Any] | None:
    """{max_mm}: the height, in mm on a 1.75 m person, of the tallest thin thing at the lids. `eyes3d` are the two points
    where the eyes are on the surface, `yaw_deg` the turn from which the face is seen (as face.Camera takes it), `height`
    the model's height in the same unit as verts. None when too little of the area round the eyes is in the picture."""
    scale = PERSON_MM / 1000.0 / height  # model units -> metres on a 1.75 m person
    v = np.asarray(verts, dtype=np.float64) * scale
    f = np.asarray(faces, dtype=np.int64)
    eyes = np.asarray(eyes3d, dtype=np.float64) * scale
    middle = eyes.mean(axis=0)
    apart = float(np.linalg.norm(eyes[0] - eyes[1]))
    cam = face.Camera(target=middle, yaw_deg=float(yaw_deg), scale=VIEW_MM / 1000.0, size=int(VIEW_MM / PX_MM))
    near = f[np.linalg.norm(v[f].mean(axis=1) - middle, axis=1) < REACH_MM / 1000.0]
    if len(near) == 0 or apart <= 0 or not math.isfinite(apart):
        return None
    raster = face.view_raster({"verts": v, "faces": near}, cam)
    point = face.interpolate(v, near, raster)  # the surface point behind each pixel
    drawn = raster["tri"] >= 0
    depth = np.where(drawn, -raster["z"] * 1000.0, np.nan)  # toward the viewer, mm
    thin = _thin_height(depth)
    right, up, _ = cam.basis
    area = np.zeros(drawn.shape, bool)
    for eye in eyes:
        d = point - eye
        across, rise = d @ right, d @ up
        noseward = np.sign((middle - eye) @ right) * across
        oval = (across / (ACROSS_SHARE * apart)) ** 2 + (rise / (UPDOWN_SHARE * apart)) ** 2 < 1
        lids = oval & (noseward < NOSEWARD_SHARE * apart) & (np.linalg.norm(d, axis=-1) < BALL_SHARE * apart) & drawn
        if lids.any():
            area |= lids & (depth < np.percentile(depth[lids], SKIN_PERCENTILE) + AHEAD_MM)
    values = np.sort(thin[area])[::-1]
    count = int(round(MIN_AREA_MM2 / PX_MM ** 2))
    if len(values) <= count:
        return None
    return {"max_mm": round(float(values[count]), 2)}


def _texture_of(glb: Path | str) -> np.ndarray | None:
    import trimesh

    mesh = trimesh.load(str(glb), force="mesh", process=False)
    mat = getattr(getattr(mesh, "visual", None), "material", None)
    img = getattr(mat, "baseColorTexture", None) or getattr(mat, "image", None)
    if img is None:
        return None
    return np.asarray(img.convert("RGB"), dtype=np.float64) / 255.0


def measure_glb(glb: Path | str, texture: Path | str | None = None) -> dict[str, Any] | None:
    """protrusions() on a finished model, with the eyes found on a textured render of the head (the four turns are tried
    until the finder sees a face). The record adds {height, yaw}. None when anything is missing."""
    from . import facemarks

    tok = facemarks.use_local()
    try:
        mesh = face.load_mesh(glb)
        if mesh is None:
            return None
        tex = face.read_rgb(texture) if texture is not None else _texture_of(glb)
        if tex is None:
            return None
        head = face.find_head(mesh["verts"])
        if head is None:
            return None
        for yaw in YAWS:
            cam = face.head_camera(head, yaw_deg=float(yaw))
            r = face.render_view(mesh, tex, cam)
            m = facemarks.points(r["image"])
            if not m or len(m.get("eyes") or []) != 2:
                continue
            pos = face.interpolate(mesh["verts"], mesh["faces"], r["raster"])
            eyes3d = []
            for x, y in m["eyes"]:
                xi, yi = int(x), int(y)
                if 0 <= xi < cam.size and 0 <= yi < cam.size and r["mask"][yi, xi]:
                    eyes3d.append(pos[yi, xi])
            if len(eyes3d) != 2:
                continue
            out = protrusions(mesh["verts"], mesh["faces"], eyes3d, float(yaw), head.height)
            if out is None:
                return None
            out.update(height=round(float(head.height), 4), yaw=yaw)
            return out
        return None
    except Exception:  # noqa: BLE001  (a metric, never a reason to fail a stage)
        return None
    finally:
        facemarks.done_local(tok)
