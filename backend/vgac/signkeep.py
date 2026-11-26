"""Lettered signs keep their letters through the Low-poly clean-up (PL-123).

The look's one colour per triangle (looktex.colour_per_face) flattens a triangle to its median colour unless it is
"detailed"; a sign's letters are cut across many small triangles, most holding only a sliver of a stroke, so they were
flattened and the sign read "CORNER SHOA" although the picture paint had drawn it cleanly. The item finder (the same one the
glow map uses, item_suggest.py) finds the sign boards on renders of the painted model (sign_worker.py); the triangles under them go
into colour_per_face's `keep` list and stay as painted. Without the finder, without a sign, or with a mask that covers too
much of the painted texture, nothing is kept and the clean-up runs as it always did."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np

FINDER_SIDE = 1024      # the longer side the finder sees (as glow.FINDER_SIDE)
MIN_SHARE = 0.5         # a triangle is under the sign when at least this share of its visible pixels are
MAX_VIEW_SHARE = 0.15   # a sign mask over this share of the model in a view is the wall, not a board: that view is skipped
MIN_PIXELS = 3          # a triangle seen in fewer pixels than this is not judged in that view
YAWS = (45, 135, 225, 315)   # the turns the model is looked at: the four diagonals each see two faces (PL-123: same result as eight turns, half the finder time)
SIZE = 768              # the render the finder sees
FACING = 0.3            # only pixels this square-on to the camera count (a sign seen edge-on is not read)


def finder_masks(settings: Any, images: list[np.ndarray], work: Path) -> tuple[list[np.ndarray] | None, dict[str, Any]]:
    """The sign boards on each picture (bool, picture size), or (None, record) when the finder is not installed or fails."""
    from PIL import Image

    from . import item_suggest

    if not item_suggest.installed(settings):
        return None, {"finder": "not installed"}
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    for i, im in enumerate(images):
        Image.fromarray((np.clip(im, 0.0, 1.0) * 255).astype(np.uint8), "RGB").save(work / f"view_{i}.png")
    env = {**os.environ, "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2"}
    t0 = time.time()
    r = subprocess.run([str(item_suggest.finder_python(settings)), str(Path(__file__).with_name("sign_worker.py")), str(work),
                        str(len(images)), str(item_suggest.finder_models(settings))], capture_output=True, text=True, timeout=1800, env=env)
    if r.returncode != 0 or "SIGN_OK" not in r.stdout:
        return None, {"finder": "failed: " + (r.stderr or r.stdout)[-160:].strip()}
    masks = [np.load(work / f"view_{i}_sign.npy") for i in range(len(images))]
    return masks, {"finder": "ok", "finder_seconds": round(time.time() - t0, 1), "found": json.loads((work / "sign_report.json").read_text())}


def faces_under(mask: np.ndarray, tri: np.ndarray, facing: np.ndarray, n_faces: int, min_share: float = MIN_SHARE) -> np.ndarray:
    """(n_faces,) bool: the triangles that, in this view, have at least `min_share` of their square-on pixels under the sign
    mask. mask (S, S) bool; tri (S, S) the triangle at each pixel, -1 where none; facing (S, S) cosine to the camera."""
    seen = (tri >= 0) & (facing > FACING)
    t = tri[seen]
    count = np.bincount(t, minlength=n_faces).astype(float)
    hit = np.bincount(t[mask[seen]], minlength=n_faces).astype(float)
    out = np.zeros(n_faces, dtype=bool)
    ok = count >= MIN_PIXELS
    out[ok] = hit[ok] / count[ok] >= min_share
    return out


def sign_faces(settings: Any, glb: Any, base: np.ndarray, work: Path, masks_for: Any = None) -> tuple[np.ndarray | None, dict[str, Any]]:
    """The triangles of the model under the sign boards the finder finds on renders of it painted with `base` (T, T, 3 floats):
    (face indexes, record), or (None, record) with the reason (no finder, no sign, every mask too large). `masks_for(images)`
    stands in for the finder (tests and trials)."""
    from . import face, texfidelity

    mesh = face.load_mesh(glb)
    if mesh is None:
        return None, {"kept": 0, "reason": "no UVs"}
    rends = [face.render_view(mesh, base, texfidelity._frame_camera(mesh, float(y), 0.8 * SIZE, SIZE), background=1.0) for y in YAWS]
    images = [r["image"] for r in rends]
    if masks_for is not None:
        masks, rec = masks_for(images), {"finder": "stand-in"}
    else:
        masks, rec = finder_masks(settings, images, work)
    if masks is None:
        return None, {**rec, "kept": 0, "reason": "finder unavailable"}
    out = np.zeros(len(mesh["faces"]), dtype=bool)
    skipped = 0
    for r, m in zip(rends, masks):
        if not m.any():
            continue
        if float(m.sum()) / max(int(r["mask"].sum()), 1) > MAX_VIEW_SHARE:
            skipped += 1
            continue
        out |= faces_under(m & r["mask"], r["raster"]["tri"], r["facing"], len(out))
    rec = {**rec, "views_with_sign": sum(bool(m.any()) for m in masks), "views_skipped_large": skipped}
    if not out.any():
        return None, {**rec, "kept": 0, "reason": "no sign found" if not any(m.any() for m in masks) else "no triangle under the sign"}
    return np.nonzero(out)[0], {**rec, "kept": int(out.sum())}
