"""Lettered signs keep their letters through the Low-poly clean-up (PL-123).

The look's one colour per triangle (looktex.colour_per_face) flattens a triangle to its median colour unless it is
"detailed"; a sign's letters are cut across many small triangles, most holding only a sliver of a stroke, so they were
flattened and the sign read "CORNER SHOA" although the picture paint had drawn it cleanly. The item finder (the same one the
glow map uses, item_suggest.py) finds the sign boards on renders of the painted model (finder_worker.py, sign mode); the triangles under them go
into colour_per_face's `keep` list and stay as painted. Without the finder, without a sign, or with a mask that covers too
much of the painted texture, nothing is kept and the clean-up runs as it always did.

The same finder run also looks at the step 1 picture and a square-on front render: where one sign matches on both, vgac/signface.py
flattens it and copies the picture's lettering onto it, and its triangles are kept whatever their share of the model. A picture
taken at an angle is also compared from its own camera, so signs, neon figures and shelves on other walls are treated too."""
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


def finder_masks(settings: Any, images: list[np.ndarray], work: Path,
                 panels_from: int | None = None) -> tuple[list[np.ndarray] | None, dict[str, Any]]:
    """The sign boards on each picture (bool, picture size), or (None, record) when the finder is not installed or fails.
    From picture `panels_from` on, the record's "panels" holds each picture's named panels: [{kind, box, mask}]."""
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
    r = subprocess.run([str(item_suggest.finder_python(settings)), str(Path(__file__).with_name("finder_worker.py")), "sign", str(work),
                        str(len(images)), str(item_suggest.finder_models(settings)),
                        str(len(images) if panels_from is None else panels_from)], capture_output=True, text=True, timeout=1800, env=env)
    if r.returncode != 0 or "FINDER_OK" not in r.stdout:
        return None, {"finder": "failed: " + (r.stderr or r.stdout)[-160:].strip()}
    masks = [np.load(work / f"view_{i}_sign.npy") for i in range(len(images))]
    rec = {"finder": "ok", "finder_seconds": round(time.time() - t0, 1), "found": json.loads((work / "sign_report.json").read_text())}
    if panels_from is not None:
        named = json.loads((work / "panel_report.json").read_text())
        rec["panels"] = [[{**d, "mask": m} for d, m in zip(named[str(i)], np.load(work / f"view_{i}_panels.npy"))]
                         for i in range(panels_from, len(images))]
    return masks, rec


def _finder_here(settings: Any) -> bool:
    from . import item_suggest

    return item_suggest.installed(settings)


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


def sign_faces(settings: Any, glb: Any, base: np.ndarray, work: Path, masks_for: Any = None,
               panels_for: Any = None) -> tuple[np.ndarray | None, dict[str, Any]]:
    """The triangles of the model under the sign boards the finder finds on renders of it painted with `base` (T, T, 3 floats):
    (face indexes, record), or (None, record) with the reason (no finder, no sign, every mask too large). `masks_for(images)`
    stands in for the finder, and `panels_for(images)` for its named panels (tests and trials).

    With the step 1 picture beside `work` (the picture paint's copy), a sign found on both the picture and the front of the
    model is also flattened (in `glb`) and repainted from the picture (in `base`), both in place; so are the panels of a picture
    taken at an angle, compared from its own camera."""
    from . import face, signface, texfidelity

    mesh = face.load_mesh(glb)
    if mesh is None:
        return None, {"kept": 0, "reason": "no UVs"}
    rends = [face.render_view(mesh, base, texfidelity._frame_camera(mesh, float(y), 0.8 * SIZE, SIZE), background=1.0) for y in YAWS]
    images = [r["image"] for r in rends]
    picture = signface.read_picture(Path(work).parent)
    if picture is not None:
        front = face.render_view(mesh, base, signface.front_camera(mesh["verts"]), background=1.0)["image"]
        images += [front, picture]
    frame, camera_rec, angled = None, None, len(images)
    if picture is not None and (panels_for is not None or (masks_for is None and _finder_here(settings))):
        t0 = time.time()
        try:
            frame, camera_rec = signface.picture_camera(mesh, base, signface.picture_path(Path(work).parent))
        except Exception as e:  # noqa: BLE001  (the front sign and the diagonal keep still run)
            camera_rec = {"reason": f"failed: {str(e)[:160]}"}
        camera_rec["seconds"] = round(time.time() - t0, 1)
        if frame is not None:
            images = images + [frame["view"], face.render_view(mesh, base, frame["cam"], background=1.0)["image"]]
    if masks_for is not None:
        masks, rec = masks_for(images[:angled]), {"finder": "stand-in"}
        if frame is not None:
            rec["panels"] = panels_for(images[angled:])
    else:
        masks, rec = finder_masks(settings, images, work, angled if frame is not None else None)
    panels = rec.pop("panels", None)
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
    seen, masks, images = masks[:len(rends)], masks[:angled], images[:angled]
    rec = {**rec, "views_with_sign": sum(bool(m.any()) for m in seen), "views_skipped_large": skipped}
    if picture is not None:
        try:
            tris, rec["face"] = signface.apply(Path(glb), base, picture, masks[-1], masks[-2], images[-2])
        except Exception as e:  # noqa: BLE001  (the keep from the diagonal views still holds)
            tris, rec["face"] = None, {"applied": False, "reason": f"failed: {str(e)[:160]}"}
        if tris is not None:
            out[tris] = True
    if camera_rec is not None:
        rec["camera"] = camera_rec
    if frame is not None and panels is not None:
        try:
            tris, rec["panels"] = signface.apply_panels(Path(glb), base, frame, panels[0], panels[1])
        except Exception as e:  # noqa: BLE001  (what the other routes did still holds)
            tris, rec["panels"] = None, [{"applied": False, "reason": f"failed: {str(e)[:160]}"}]
        if tris is not None:
            out[tris] = True
    if not out.any():
        return None, {**rec, "kept": 0, "reason": "no sign found" if not any(m.any() for m in seen) else "no triangle under the sign"}
    return np.nonzero(out)[0], {**rec, "kept": int(out.sum())}
