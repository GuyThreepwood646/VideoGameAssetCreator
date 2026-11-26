"""Head spike check (PL-122): the tallest bump of surface near the eyes.

The 3D builder can grow flaps of geometry at the lids (up to about 7 mm on a 1.75 m person). For every vertex within
RADIUS_IPD times the distance between the eyes from an eye, the height of the vertex over the median of its small neighbourhood is
measured along the direction the face looks, in millimetres on a 1.75 m person. A flap is a spike; a gentle brow or lid
fold is not, because it is part of its own neighbourhood's median. The eyes are found on a textured render of the head
with the face finder, then lifted onto the surface.

protrusions() is the pure measure (planted-defect tests build a tiny mesh); measure_glb() reads a finished build.
Never raises: no finder, no face, or no texture gives None, and the check is then not made.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

# The area is a disc round each eye of RADIUS_IPD times the distance between the eyes (about 2.8 cm on a person: the lids and
# lashes, not the brow, the nose or the hair at the temple). The neighbourhood a bump is measured against is WINDOW_SHARE of the
# model's height (1.4 cm on a 1.75 m person; the PL-116 trial's value).
RADIUS_IPD = 0.45
WINDOW_SHARE = 0.008 / 0.9776
PERSON_MM = 1750.0
FLAG_MM = 1.0  # a vertex over its neighbourhood by more than this is counted as flagged
LIMIT_MM = 2.0  # the check's limit: the largest protrusion near the eyes
YAWS = (0, 90, 180, 270)
_CHUNK = 1500


def protrusions(verts: np.ndarray, eyes3d: list[np.ndarray], toward: np.ndarray, height: float, radius: float | None = None) -> dict[str, Any]:
    """{max_mm, p95_mm, flagged_verts, verts, eyes: [...]}: for each vertex within `radius` of an eye (RADIUS_IPD times the distance
    between the eyes when not given), its height along
    `toward` (unit vector, out of the face) over the median of the vertices within the window of it. `height` is the model's
    height in the same unit as verts."""
    v = np.asarray(verts, dtype=np.float64)
    toward = np.asarray(toward, dtype=np.float64)
    toward = toward / max(float(np.linalg.norm(toward)), 1e-12)
    mm = PERSON_MM / height
    if radius is None:
        radius = RADIUS_IPD * float(np.linalg.norm(np.asarray(eyes3d[0]) - np.asarray(eyes3d[1]))) if len(eyes3d) > 1 else 0.03 * height
    win = WINDOW_SHARE * height
    h_all = np.zeros(len(v))
    seen = np.zeros(len(v), bool)
    eyes = []
    for e in eyes3d:
        idx = np.flatnonzero(np.linalg.norm(v - e, axis=1) < radius)
        if len(idx) == 0:
            eyes.append({"verts": 0, "max_mm": 0.0, "flagged_verts": 0})
            continue
        sub = v[idx]
        along = sub @ toward
        h = np.zeros(len(idx))
        for s in range(0, len(idx), _CHUNK):
            d = np.linalg.norm(sub[s:s + _CHUNK, None, :] - sub[None, :, :], axis=2) < win
            for k in range(d.shape[0]):
                nb = along[d[k]]
                ref = float(np.median(nb)) if len(nb) > 3 else along[s + k]
                h[s + k] = (along[s + k] - ref) * mm
        h_all[idx] = np.maximum(h_all[idx], h)
        seen[idx] = True
        eyes.append({"verts": int(len(idx)), "max_mm": round(float(h.max()), 2), "flagged_verts": int((h > FLAG_MM).sum())})
    vals = h_all[seen]
    if len(vals) == 0:
        return {"max_mm": 0.0, "p95_mm": 0.0, "flagged_verts": 0, "verts": 0, "eyes": eyes}
    return {"max_mm": round(float(vals.max()), 2), "p95_mm": round(float(np.percentile(vals, 95)), 2),
            "flagged_verts": int((vals > FLAG_MM).sum()), "verts": int(len(vals)), "eyes": eyes}


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
    from . import face, facemarks

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
            out = protrusions(mesh["verts"], eyes3d, -cam.basis[2], head.height)
            out.update(height=round(float(head.height), 4), yaw=yaw)
            return out
        return None
    except Exception:  # noqa: BLE001  (a metric, never a reason to fail a stage)
        return None
    finally:
        facemarks.done_local(tok)
