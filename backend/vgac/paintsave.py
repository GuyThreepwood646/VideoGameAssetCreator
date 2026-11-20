"""Save and carry the Touch up paint (WP-23).

A painted save makes a new step 4 (clean-up) version whose parent is the version that was painted. The geometry is
the parent's; only the base-colour picture changes. The picture the person painted is merged into the parent's:

- texels in the edit mask (the ones the paint changed) take the painted colour;
- every other texel on a UV island keeps the parent's colour exactly;
- the gutter around the islands (the edge padding) is filled from the nearest island or painted colour, so no seam
  shows at mip levels. Gutter texels are the only ones outside the mask that may change.

The painted version is marked with metrics.painted. Adjust and Refine on it are refused (refusal()): both rebuild the
clean-up from the mesh step and would drop the paint. A rig made from it reuses the rig of the same geometry and only
swaps the picture (rig.py, retexture).

Files on the new version: base_color (the padded picture), glb (the parent's model with the new picture in it),
paint_mask.png (the edit mask, white where painted) and paint_strokes.json (the stroke log the panel sent).
"""

from __future__ import annotations

import base64
import binascii
import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from sqlalchemy.orm import Session

from . import imaging, library
from .db import Version

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_PICTURE_B64 = 40_000_000  # characters of base64; a 4096 px picture is well under this
MAX_STROKES = 5000
PAINT_INPUT = "paint_input.png"  # the picture the panel sent, held in the version folder until the job has read it
PAINT_MASK_INPUT = "paint_mask_input.png"
PAINT_STROKES_INPUT = "paint_strokes_input.json"
# Metrics of one run that a painted version does not carry over from its parent.
_RUN_METRICS = ("checks", "checks_passed", "checks_failed", "gate_ok", "elapsed_seconds", "refit_of")

REFUSAL = ("This step has your painted changes. Adjust and Refine would rebuild the clean-up from the 3D model and lose the "
           "paint. Paint again on this version to change the colours, or go back to the version before the paint.")


def is_painted(v: Version | None) -> bool:
    m = getattr(v, "metrics", None)
    return bool(isinstance(m, dict) and m.get("painted"))


SHAPE_REFUSAL = ("This step has your separated arm. Adjust and Refine would rebuild the clean-up from the 3D model and lose "
                 "the cut. Separate the arm again from the rig to change the cut.")


def refusal(parent: Version | None) -> str | None:
    """The plain reason Adjust or Refine is refused on `parent`, or None when they may run."""
    if parent is not None and parent.stage == "prep" and is_painted(parent):
        return REFUSAL
    if parent is not None and parent.stage == "prep" and (parent.metrics or {}).get("shaped"):
        return SHAPE_REFUSAL
    return None


def geometry_root(db: Session, v: Version) -> Version:
    """The clean-up version the painted chain of `v` starts from: the first version up the parent chain that is not
    painted. Paint never changes the geometry, so every painted version has the geometry of this root."""
    cur = v
    seen: set[str] = set()
    while is_painted(cur) and cur.parent_version_id and cur.id not in seen:
        seen.add(cur.id)
        parent = db.get(Version, cur.parent_version_id)
        if parent is None:
            break
        cur = parent
    return cur


def _decode_png(text: str, what: str, limit: int) -> bytes:
    if len(text) > limit:
        raise ValueError(f"{what} is too large")
    data = text.split(",", 1)[1] if text.startswith("data:") else text
    try:
        raw = base64.b64decode(data, validate=True)
    except (ValueError, binascii.Error) as e:
        raise ValueError(f"{what} is not valid base64") from e
    if not raw.startswith(PNG_SIGNATURE):
        raise ValueError(f"{what} is not a PNG image")
    return raw


def decode_inputs(picture_b64: str, mask_b64: str, strokes: list[dict[str, Any]]) -> tuple[bytes, bytes, bytes]:
    """Validate what the panel sent and return (picture PNG bytes, mask PNG bytes, strokes JSON bytes)."""
    if not isinstance(strokes, list) or len(strokes) > MAX_STROKES:
        raise ValueError("the stroke log is missing or too long")
    picture = _decode_png(picture_b64, "the picture", MAX_PICTURE_B64)
    mask = _decode_png(mask_b64, "the edit mask", MAX_PICTURE_B64)
    return picture, mask, json.dumps(strokes, indent=1).encode("utf-8")


def write_inputs(folder: Path, picture: bytes, mask: bytes, strokes: bytes) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / PAINT_INPUT).write_bytes(picture)
    (folder / PAINT_MASK_INPUT).write_bytes(mask)
    (folder / PAINT_STROKES_INPUT).write_bytes(strokes)


def merge_picture(parent_px: np.ndarray, painted_px: np.ndarray, edit: np.ndarray, island: np.ndarray,
                  pad_px: int) -> tuple[np.ndarray, dict[str, int]]:
    """The new picture (H, W, C uint8). Texels in `edit` take the painted colour; island texels outside `edit` keep the
    parent's colour exactly; gutter texels within pad_px of the island or the edit take the nearest colour (the edge
    padding); gutter texels further away keep the parent's colour. Returns the picture and counts."""
    if parent_px.shape != painted_px.shape or edit.shape != parent_px.shape[:2] or island.shape != edit.shape:
        raise ValueError("the picture, the mask and the layout have different sizes")
    out = parent_px.copy()
    out[edit] = painted_px[edit]
    out[island & ~edit] = parent_px[island & ~edit]
    filled = island | edit
    padded = imaging.dilate(out, filled, int(pad_px))
    gutter = ~filled
    out[gutter] = padded[gutter]
    changed_island = int((island & edit).sum())
    padding = int((gutter & (padded != parent_px).any(axis=-1)).sum())
    return out, {"painted_texels": int(edit.sum()), "painted_on_island": changed_island, "padding_texels": padding}


def _copy_parent_files(sc: Any, parent: Version) -> None:
    """Copy the parent version's files into this version at the same relative paths, so the parent can be removed
    later without breaking this one (the same rule as the rig re-fits)."""
    parent_dir = library.version_dir(sc.asset, parent, sc.settings, create=False).resolve()
    for key in list(parent.files):
        src = library.version_file(parent, key, sc.settings)
        if src is None:
            continue
        src = Path(src).resolve()
        try:
            rel = src.relative_to(parent_dir)
        except ValueError:
            rel = Path(src.name)
        dest = sc.dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src != dest.resolve():
            shutil.copy2(src, dest)
        sc.register(key, dest)


def _picture_labels(glb: Path, size: int) -> np.ndarray:
    """int32 (size, size): the UV island of each texel plus one, 0 where no island lies (rasterise_uv labels)."""
    uvf = imaging.load_uv_mesh(glb)
    if uvf is None:
        raise ValueError("the model has no picture layout to paint on")
    return imaging.rasterise_uv(uvf, size)["labels"]


def _picture_layout(glb: Path, size: int) -> np.ndarray:
    return _picture_labels(glb, size) > 0


def _pad_px(size: int) -> int:
    return imaging.dilation_px(size)


async def paint(sc: Any, params: dict[str, Any]) -> None:
    """The job behind a painted save (stage prep, op paint). Runs on the host."""
    import asyncio

    parent = sc.parent
    if parent is None or not parent.files:
        raise ValueError("the painted version has no clean-up to paint on")
    if parent.stage != "prep":
        raise ValueError("only a clean-up version can be painted")
    parent_glb = library.version_file(parent, "glb", sc.settings)
    parent_base = library.version_file(parent, "base_color", sc.settings)
    if parent_glb is None or parent_base is None:
        raise ValueError("the clean-up has no model or picture to paint on")
    picture_in = sc.dir / PAINT_INPUT
    mask_in = sc.dir / PAINT_MASK_INPUT
    strokes_in = sc.dir / PAINT_STROKES_INPUT
    if not (picture_in.is_file() and mask_in.is_file() and strokes_in.is_file()):
        raise ValueError("the painted picture was not received")

    _copy_parent_files(sc, parent)
    base_img = Image.open(parent_base)
    base_mode = base_img.mode
    base_arr = np.asarray(base_img.convert("RGBA"))
    size = base_arr.shape[0]
    painted = Image.open(picture_in).convert("RGBA")
    if painted.size != (base_arr.shape[1], base_arr.shape[0]):
        raise ValueError(f"the painted picture is {painted.size[0]} by {painted.size[1]}, the picture is "
                         f"{base_arr.shape[1]} by {base_arr.shape[0]}")
    mask_img = Image.open(mask_in).convert("L")
    if mask_img.size != painted.size:
        raise ValueError("the edit mask and the painted picture have different sizes")
    edit = np.asarray(mask_img) > 127
    if not edit.any():
        raise ValueError("nothing was painted")

    sc.progress(0.1, "padding the paint onto the picture edges")

    def work() -> dict[str, Any]:
        island = _picture_layout(parent_glb, size)
        out, counts = merge_picture(base_arr, np.asarray(painted), edit, island, _pad_px(size))
        mode = "RGBA" if base_mode == "RGBA" else "RGB"
        img = Image.fromarray(out if mode == "RGBA" else out[..., :3], mode)
        new_base = sc.dir / "base_color.png"
        img.save(new_base)
        new_glb = sc.dir / Path(parent_glb).name
        shutil.copyfile(parent_glb, new_glb)
        if not imaging.glb_replace_images(new_glb, base_color=new_base):
            raise ValueError("the new picture could not be written into the model")
        mask_out = sc.dir / "paint_mask.png"
        Image.fromarray((edit * 255).astype(np.uint8), "L").save(mask_out)
        return {"base": new_base, "glb": new_glb, "mask": mask_out, "counts": counts}

    done = await asyncio.to_thread(work)
    sc.register("base_color", done["base"])
    sc.register("glb", done["glb"])
    sc.register("paint_mask", done["mask"])
    strokes_out = sc.dir / "paint_strokes.json"
    shutil.copyfile(strokes_in, strokes_out)
    sc.register("paint_strokes", strokes_out)
    carried = {k: v for k, v in (parent.metrics or {}).items() if k not in _RUN_METRICS}
    sc.set_metrics(**carried, painted=True, painted_of=parent.id, geometry_of=(parent.metrics or {}).get("geometry_of") or parent.id,
                   paint_strokes=len(json.loads(strokes_out.read_text(encoding="utf-8"))), paint_texture_size=size,
                   paint_texels=done["counts"]["painted_texels"], paint_island_texels=done["counts"]["painted_on_island"],
                   paint_padding_texels=done["counts"]["padding_texels"])
    for p in (picture_in, mask_in, strokes_in):
        p.unlink(missing_ok=True)
    sc.set_params(paint_of=parent.id)


# --- Smooth the paint (WP-24) ------------------------------------------------------------------------------------
#
# A painted clean-up can be smoothed once or more: an edge-aware (bilateral) filter runs over the painted texels only.
# Each neighbour counts only when it lies on the same UV island, so no colour crosses an island border. Texels outside
# the paint mask keep the parent's colour exactly; the edge padding is re-run only where a smoothed texel changed.
#
# Strength is the number of filter passes and the colour difference (0 to 1) past which the filter stops mixing.
# Every pass samples each neighbour texel (no striding, so no grid pattern). The number of passes grows with the
# texture size: passes_per_1024 * max(1, size // 1024), so a 4096 picture gets about the same reach on the surface
# as a 1024 one.

SMOOTH_STRENGTHS = {
    "light": {"passes_per_1024": 2, "colour_sigma": 0.15},
    "strong": {"passes_per_1024": 5, "colour_sigma": 0.22},
}
SMOOTH_RADIUS = 2  # neighbour texels each side per pass (a 5 by 5 footprint)
SMOOTH_SPACE_SIGMA = 1.5  # in texels
SMOOTH_REFUSAL = "Smooth needs a painted clean-up with a paint mask. Paint the picture and save first."


def smooth_passes(strength: str, size: int) -> int:
    return int(SMOOTH_STRENGTHS[strength]["passes_per_1024"]) * max(1, int(size) // 1024)


def smooth_colour(colour: np.ndarray, target: np.ndarray, labels: np.ndarray, strength: str, passes: int) -> np.ndarray:
    """Bilateral smoothing of `colour` (H, W, C float 0..1) at the `target` texels only, `passes` times. A neighbour is
    used only when its label equals the texel's own and is on an island (labels > 0). Returns a new array; untargeted
    texels are equal to the input."""
    sigma_r = float(SMOOTH_STRENGTHS[strength]["colour_sigma"])
    out = np.array(colour, dtype=np.float32, copy=True)
    if not target.any() or passes < 1:
        return out
    H, W = target.shape
    rows, cols = np.where(target)
    margin = passes * SMOOTH_RADIUS + 1
    y0, y1 = max(0, int(rows.min()) - margin), min(H, int(rows.max()) + 1 + margin)
    x0, x1 = max(0, int(cols.min()) - margin), min(W, int(cols.max()) + 1 + margin)
    cur = out[y0:y1, x0:x1].copy()
    lab = labels[y0:y1, x0:x1]
    tgt = target[y0:y1, x0:x1]
    offsets = [(ky, kx) for ky in range(-SMOOTH_RADIUS, SMOOTH_RADIUS + 1) for kx in range(-SMOOTH_RADIUS, SMOOTH_RADIUS + 1)]
    space = {(ky, kx): np.exp(-(ky * ky + kx * kx) / (2.0 * SMOOTH_SPACE_SIGMA ** 2)) for ky, kx in offsets}
    for _ in range(passes):
        num = np.zeros_like(cur)
        den = np.zeros(lab.shape, dtype=np.float32)
        for ky, kx in offsets:
            nb = imaging._shift(cur, ky, kx, 0.0)
            nl = imaging._shift(lab, ky, kx, 0)
            diff2 = ((nb - cur) ** 2).sum(axis=-1)
            w = space[(ky, kx)] * np.exp(-diff2 / (2.0 * sigma_r ** 2)) * ((nl == lab) & (lab > 0))
            num += w[..., None] * nb
            den += w
        new = num / np.maximum(den, 1e-12)[..., None]
        cur = np.where(tgt[..., None], new, cur)
    out[y0:y1, x0:x1] = cur
    return out


def smooth_picture(parent_px: np.ndarray, edit: np.ndarray, labels: np.ndarray, strength: str,
                   size: int) -> tuple[np.ndarray, dict[str, int]]:
    """The smoothed picture (H, W, 4 uint8). Only painted island texels (edit and labels > 0) change colour; alpha is
    kept. The edge padding is re-run within its own reach of a changed texel and nowhere else."""
    if strength not in SMOOTH_STRENGTHS:
        raise ValueError("choose light or strong")
    if parent_px.shape[:2] != edit.shape or labels.shape != edit.shape:
        raise ValueError("the picture, the mask and the layout have different sizes")
    island = labels > 0
    target = edit & island
    out = parent_px.copy()
    if not target.any():
        return out, {"polished_texels": 0, "padding_texels": 0}
    colour = parent_px[..., :3].astype(np.float32) / 255.0
    smoothed = smooth_colour(colour, target, labels, strength, smooth_passes(strength, size))
    new_rgb = np.clip(np.round(smoothed * 255.0), 0, 255).astype(np.uint8)
    out[..., :3] = np.where(target[..., None], new_rgb, parent_px[..., :3])
    changed = (out != parent_px).any(axis=-1)
    polished = int((changed & target).sum())
    if not changed.any():
        return out, {"polished_texels": polished, "padding_texels": 0}
    # Re-run the edge padding near the changed texels only: crop to them plus the reach of the padding, pad the crop,
    # and take the gutter texels inside the padding reach of a change.
    pad = _pad_px(size)
    filled = island | edit
    rows, cols = np.where(changed)
    H, W = changed.shape
    inner = (max(0, int(rows.min()) - pad), min(H, int(rows.max()) + 1 + pad),
             max(0, int(cols.min()) - pad), min(W, int(cols.max()) + 1 + pad))
    outer = (max(0, inner[0] - pad - 1), min(H, inner[1] + pad + 1), max(0, inner[2] - pad - 1), min(W, inner[3] + pad + 1))
    y0, y1, x0, x1 = outer
    crop_out = out[y0:y1, x0:x1].copy()
    crop_filled = filled[y0:y1, x0:x1]
    padded = imaging.dilate(crop_out, crop_filled, pad)
    gutter = ~crop_filled
    iy0, iy1, ix0, ix1 = inner[0] - y0, inner[1] - y0, inner[2] - x0, inner[3] - x0
    region = np.zeros(crop_filled.shape, dtype=bool)
    region[iy0:iy1, ix0:ix1] = True
    take = gutter & region
    crop_out[take] = padded[take]
    out[y0:y1, x0:x1] = crop_out
    padding = int((take & (crop_out != parent_px[y0:y1, x0:x1]).any(axis=-1)).sum())
    return out, {"polished_texels": polished, "padding_texels": padding}


async def smooth(sc: Any, params: dict[str, Any]) -> None:
    """The job behind Smooth the paint (stage prep, op smooth). Runs on the host. Makes a new clean-up version."""
    import asyncio

    parent = sc.parent
    if parent is None or not parent.files:
        raise ValueError("the version has no clean-up to smooth")
    if parent.stage != "prep" or not is_painted(parent):
        raise ValueError("only a painted clean-up can be smoothed")
    strength = str(params.get("strength") or "")
    if strength not in SMOOTH_STRENGTHS:
        raise ValueError("choose light or strong")
    parent_glb = library.version_file(parent, "glb", sc.settings)
    parent_base = library.version_file(parent, "base_color", sc.settings)
    parent_mask = library.version_file(parent, "paint_mask", sc.settings)
    if parent_glb is None or parent_base is None or parent_mask is None:
        raise ValueError(SMOOTH_REFUSAL)

    _copy_parent_files(sc, parent)
    sc.progress(0.1, "smoothing the paint inside its edges")

    def work() -> dict[str, Any]:
        base_img = Image.open(parent_base)
        base_mode = base_img.mode
        base_arr = np.asarray(base_img.convert("RGBA"))
        size = base_arr.shape[0]
        mask_img = Image.open(parent_mask).convert("L")
        if mask_img.size != (base_arr.shape[1], base_arr.shape[0]):
            raise ValueError("the paint mask and the picture have different sizes")
        edit = np.asarray(mask_img) > 127
        if not edit.any():
            raise ValueError("nothing was painted")
        labels = _picture_labels(parent_glb, size)
        out, counts = smooth_picture(base_arr, edit, labels, strength, size)
        mode = "RGBA" if base_mode == "RGBA" else "RGB"
        img = Image.fromarray(out if mode == "RGBA" else out[..., :3], mode)
        new_base = sc.dir / "base_color.png"
        img.save(new_base)
        new_glb = sc.dir / Path(parent_glb).name
        shutil.copyfile(parent_glb, new_glb)
        if not imaging.glb_replace_images(new_glb, base_color=new_base):
            raise ValueError("the new picture could not be written into the model")
        return {"base": new_base, "glb": new_glb, "counts": counts}

    done = await asyncio.to_thread(work)
    sc.register("base_color", done["base"])
    sc.register("glb", done["glb"])
    carried = {k: v for k, v in (parent.metrics or {}).items() if k not in _RUN_METRICS}
    metrics = {**carried, "painted": True, "painted_of": parent.id,
               "geometry_of": (parent.metrics or {}).get("geometry_of") or parent.id, "polished": strength,
               "polish_texels": done["counts"]["polished_texels"], "polish_padding_texels": done["counts"]["padding_texels"]}
    sc.set_metrics(**metrics)
    sc.set_params(smooth_of=parent.id, polish_strength=strength)
