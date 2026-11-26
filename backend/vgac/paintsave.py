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

from . import glow, imaging, library
from .db import Version

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_PICTURE_B64 = 40_000_000  # characters of base64; a 4096 px picture is well under this
MAX_STROKES = 5000
PAINT_INPUT = "paint_input.png"  # the picture the panel sent, held in the version folder until the job has read it
PAINT_MASK_INPUT = "paint_mask_input.png"
PAINT_STROKES_INPUT = "paint_strokes_input.json"
# Shine (roughness) and metal pictures (WP-32), held the same way until the job has read them.
PAINT_SHINE_INPUT = "paint_shine_input.png"
PAINT_SHINE_MASK_INPUT = "paint_shine_mask_input.png"
PAINT_METAL_INPUT = "paint_metal_input.png"
PAINT_METAL_MASK_INPUT = "paint_metal_mask_input.png"
# The glow picture (strength painted by hand) and its edit mask, held the same way.
PAINT_GLOW_INPUT = "paint_glow_input.png"
PAINT_GLOW_MASK_INPUT = "paint_glow_mask_input.png"
# Metrics of one run that a painted version does not carry over from its parent.
_RUN_METRICS = ("checks", "checks_passed", "checks_failed", "gate_ok", "elapsed_seconds", "refit_of")

REFUSAL = ("This step has your painted changes. Adjust and Refine would rebuild the clean-up from the 3D model and lose the "
           "paint. Paint again on this version to change the colours, or go back to the version before the paint.")


def is_painted(v: Version | None) -> bool:
    m = getattr(v, "metrics", None)
    return bool(isinstance(m, dict) and m.get("painted"))


def is_shaped(v: Version | None) -> bool:
    """True when `v` is a clean-up made by the Shape tab (WP-26). Stubs without metrics read as not shaped."""
    m = getattr(v, "metrics", None)
    return bool(isinstance(m, dict) and m.get("shaped"))


SHAPE_REFUSAL = ("This step has your separated arm. Adjust and Refine would rebuild the clean-up from the 3D model and lose "
                 "the cut. Separate the arm again from the rig to change the cut.")


def refusal(parent: Version | None) -> str | None:
    """The plain reason Adjust or Refine is refused on `parent`, or None when they may run."""
    if parent is not None and parent.stage == "prep" and is_painted(parent):
        return REFUSAL
    if parent is not None and parent.stage == "prep" and is_shaped(parent):
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


def decode_inputs(picture_b64: str | None, mask_b64: str | None, strokes: list[dict[str, Any]], *,
                  shine_b64: str | None = None, shine_mask_b64: str | None = None,
                  metal_b64: str | None = None, metal_mask_b64: str | None = None,
                  glow_b64: str | None = None, glow_mask_b64: str | None = None) -> dict[str, bytes]:
    """Validate what the panel sent. Returns the bytes that are present: "picture" and "mask" (the colour picture and
    its edit mask), "shine" and "shine_mask", "metal" and "metal_mask", "glow" and "glow_mask", and "strokes" (the stroke log as JSON). Each
    picture comes with its mask, and at least one picture must be sent."""
    if not isinstance(strokes, list) or len(strokes) > MAX_STROKES:
        raise ValueError("the stroke log is missing or too long")
    out: dict[str, bytes] = {"strokes": json.dumps(strokes, indent=1).encode("utf-8")}
    pairs = {"colour": (picture_b64, mask_b64, "picture", "mask"),
             "shine": (shine_b64, shine_mask_b64, "shine", "shine_mask"),
             "metal": (metal_b64, metal_mask_b64, "metal", "metal_mask"),
             "glow": (glow_b64, glow_mask_b64, "glow", "glow_mask")}
    for label, (pic_text, mask_text, pic_key, mask_key) in pairs.items():
        if pic_text is None and mask_text is None:
            continue
        if pic_text is None or mask_text is None:
            raise ValueError("a painted picture needs its edit mask too")
        out[pic_key] = _decode_png(pic_text, f"the {label} picture", MAX_PICTURE_B64)
        out[mask_key] = _decode_png(mask_text, f"the {label} edit mask", MAX_PICTURE_B64)
    if not any(k in out for k in ("picture", "shine", "metal", "glow")):
        raise ValueError("nothing was sent to paint")
    return out


def write_inputs(folder: Path, inputs: dict[str, bytes]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    names = {"picture": PAINT_INPUT, "mask": PAINT_MASK_INPUT, "strokes": PAINT_STROKES_INPUT,
             "shine": PAINT_SHINE_INPUT, "shine_mask": PAINT_SHINE_MASK_INPUT,
             "metal": PAINT_METAL_INPUT, "metal_mask": PAINT_METAL_MASK_INPUT,
             "glow": PAINT_GLOW_INPUT, "glow_mask": PAINT_GLOW_MASK_INPUT}
    for key, data in inputs.items():
        (folder / names[key]).write_bytes(data)


# The island map counts every texel a triangle's outline touches, but the brush only paints texels within half a texel
# of a triangle, so a band of island texels (up to two deep) on each island's border is never painted. Unfilled, that band kept the old
# colour and showed as a thin seam along every picture-layout border inside a stroke (WP-32: 23,460 of 23,597 unpainted
# island texels were on the border).
RIM_PX = 2


def _island_rim(island: np.ndarray) -> np.ndarray:
    """Island texels within RIM_PX 8-neighbour steps of a texel outside the island (the brush misses up to two)."""
    inner = island.copy()
    for _ in range(RIM_PX):
        step = inner.copy()
        for dy, dx in imaging._neighbours():
            step &= imaging._shift(inner, dy, dx, False)
        inner = step
    return island & ~inner


HOLE_NEIGHBOURS = 5  # an island texel the brush missed is a hole when at least this many of its 8 neighbours were painted
HOLE_PASSES = 2


def _close_holes(px: np.ndarray, edit: np.ndarray, island: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Island texels outside `edit` that are mostly surrounded by painted texels (single missed texels and one-texel
    cracks inside a stroke, where the per-texel facing or slab test or a sliver triangle dropped them) take the mean of
    their painted neighbours. Returns the picture and the texels filled."""
    out = px.astype(np.float32)
    done = edit.copy()
    filled = np.zeros_like(edit)
    for _ in range(HOLE_PASSES):
        count = np.zeros(edit.shape, np.int32)
        total = np.zeros(px.shape, np.float32)
        for dy, dx in imaging._neighbours():
            nb = imaging._shift(done, dy, dx, False)
            count += nb
            total += imaging._shift(out, dy, dx) * nb[..., None]
        hole = island & ~done & (count >= HOLE_NEIGHBOURS)
        if not hole.any():
            break
        out[hole] = total[hole] / count[hole][:, None]
        done |= hole
        filled |= hole
    return np.clip(np.rint(out), 0, 255).astype(px.dtype), filled


def merge_picture(parent_px: np.ndarray, painted_px: np.ndarray, edit: np.ndarray, island: np.ndarray,
                  pad_px: int) -> tuple[np.ndarray, dict[str, int]]:
    """The new picture (H, W, C uint8). Texels in `edit` take the painted colour; island texels the brush missed inside
    a stroke take the mean of their painted neighbours (_close_holes); island-border texels within RIM_PX of
    the edit take the nearest painted colour (the brush cannot reach them); other island texels outside `edit` keep the
    parent's colour exactly; gutter texels within pad_px of the island or the edit take the nearest colour (the edge
    padding); gutter texels further away keep the parent's colour. Returns the picture and counts."""
    if parent_px.shape != painted_px.shape or edit.shape != parent_px.shape[:2] or island.shape != edit.shape:
        raise ValueError("the picture, the mask and the layout have different sizes")
    out = parent_px.copy()
    out[edit] = painted_px[edit]
    out, holes = _close_holes(out, edit, island)
    edit = edit | holes
    out[island & ~edit] = parent_px[island & ~edit]
    near = imaging.dilate(edit.astype(np.uint8), edit, RIM_PX).astype(bool)
    rim = _island_rim(island) & ~edit & near
    out[rim] = imaging.dilate(out, edit, RIM_PX)[rim]
    edit = edit | rim
    filled = island | edit
    padded = imaging.dilate(out, filled, int(pad_px))
    gutter = ~filled
    out[gutter] = padded[gutter]
    changed_island = int((island & edit).sum())
    padding = int((gutter & (padded != parent_px).any(axis=-1)).sum())
    return out, {"painted_texels": int(edit.sum()), "painted_on_island": changed_island, "padding_texels": padding,
                 "rim_texels": int(rim.sum()), "hole_texels": int(holes.sum())}


GLOW_FLOOR = 0.02  # a picture colour this dark (peak channel, 0..1) has no glow strength to read from it


def glow_strength_of(painted_px: np.ndarray) -> np.ndarray:
    """The strength (0..1, float32) the Glow brush laid down. The glow picture holds 0 where untouched and 1 + 254 x
    strength where the brush went, so an erased texel (strength 0) is told from one never touched by the edit mask alone."""
    return np.clip((painted_px[..., 0].astype(np.float32) - 1.0) / 254.0, 0.0, 1.0)


def carry_glow(parent_em: np.ndarray | None, parent_base: np.ndarray, new_base: np.ndarray, strength: np.ndarray | None = None,
               edit: np.ndarray | None = None, island: np.ndarray | None = None, pad_px: int = 0) -> np.ndarray | None:
    """The glow picture (H, W, 3 uint8) of a painted version, or None when nothing glows.

    The parent's glow (`parent_em`, None when it has none) keeps its strength where a colour repaint changed the picture
    (`new_base` against `parent_base`): the strength is the glow over the old colour, and the glow takes the new colour
    at that strength. Where the Glow brush went (`edit`, with `strength` 0..1 at those texels) the glow is the new
    colour at the painted strength, so strength 0 is the eraser. Glow is cleared only that way."""
    h, w = new_base.shape[:2]
    old = parent_base[..., :3].astype(np.float32) / 255.0
    new = new_base[..., :3].astype(np.float32) / 255.0
    em = np.zeros((h, w, 3), np.float32) if parent_em is None else parent_em.astype(np.float32) / 255.0
    peak_old = old.max(axis=-1)
    lit = em.max(axis=-1) > 0
    readable = lit & (peak_old >= GLOW_FLOOR)
    changed = (new_base[..., :3] != parent_base[..., :3]).any(axis=-1) & readable
    if changed.any():
        gain = np.clip(em.max(axis=-1) / np.maximum(peak_old, GLOW_FLOOR), 0.0, 1.0)
        em[changed] = new[changed] * gain[changed][:, None]
    em8 = np.clip(np.rint(em * 255.0), 0, 255).astype(np.uint8)
    if edit is not None and strength is not None and edit.any():
        painted = np.clip(np.rint(new * strength[..., None] * 255.0), 0, 255).astype(np.uint8)
        em8, _ = merge_picture(em8, painted, edit, island if island is not None else np.ones((h, w), bool), pad_px)
    return em8 if em8.any() else None


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
    """The job behind a painted save (stage prep, op paint). Runs on the host. The colour picture, the shine picture and
    the metal picture are each merged into the parent's in the same way (merge_picture); a picture that was not sent
    keeps the parent's file. The model gets the new colour and the new shine and metal channels."""
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
    names = {"picture": PAINT_INPUT, "mask": PAINT_MASK_INPUT, "strokes": PAINT_STROKES_INPUT,
             "shine": PAINT_SHINE_INPUT, "shine_mask": PAINT_SHINE_MASK_INPUT,
             "metal": PAINT_METAL_INPUT, "metal_mask": PAINT_METAL_MASK_INPUT,
             "glow": PAINT_GLOW_INPUT, "glow_mask": PAINT_GLOW_MASK_INPUT}
    inputs = {key: sc.dir / name for key, name in names.items()}
    has_colour = inputs["picture"].is_file()
    has_glow = inputs["glow"].is_file()
    maps = [k for k in ("shine", "metal") if inputs[k].is_file()]
    if not inputs["strokes"].is_file() or not (has_colour or maps or has_glow):
        raise ValueError("the painted picture was not received")
    needed = (["picture", "mask"] if has_colour else []) + [x for k in maps for x in (k, f"{k}_mask")] + (["glow", "glow_mask"] if has_glow else [])
    if any(not inputs[k].is_file() for k in needed):
        raise ValueError("the painted picture was not received")

    _copy_parent_files(sc, parent)
    base_img = Image.open(parent_base)
    base_mode = base_img.mode
    base_arr = np.asarray(base_img.convert("RGBA"))
    size = base_arr.shape[0]
    edits: dict[str, np.ndarray] = {}
    painted_px: dict[str, np.ndarray] = {}
    if has_colour:
        painted = Image.open(inputs["picture"]).convert("RGBA")
        if painted.size != (base_arr.shape[1], base_arr.shape[0]):
            raise ValueError(f"the painted picture is {painted.size[0]} by {painted.size[1]}, the picture is "
                             f"{base_arr.shape[1]} by {base_arr.shape[0]}")
        painted_px["colour"] = np.asarray(painted)
        edits["colour"] = _edit_of(inputs["mask"], painted.size)
    for key in maps:
        painted_map = Image.open(inputs[key]).convert("RGBA")
        if painted_map.size != (size, size):
            raise ValueError(f"the painted {key} picture is {painted_map.size[0]} by {painted_map.size[1]}, the picture is {size} by {size}")
        painted_px[key] = np.asarray(painted_map)
        edits[key] = _edit_of(inputs[f"{key}_mask"], painted_map.size)
    glow_strength = None
    if has_glow:
        painted_glow = Image.open(inputs["glow"]).convert("RGBA")
        if painted_glow.size != (size, size):
            raise ValueError(f"the painted glow picture is {painted_glow.size[0]} by {painted_glow.size[1]}, the picture is {size} by {size}")
        glow_strength = glow_strength_of(np.asarray(painted_glow))
        edits["glow"] = _edit_of(inputs["glow_mask"], painted_glow.size)
    if not any(e.any() for e in edits.values()):
        raise ValueError("nothing was painted")

    sc.progress(0.1, "padding the paint onto the picture edges")

    def work() -> dict[str, Any]:
        island = _picture_layout(parent_glb, size)
        out: dict[str, Any] = {"counts": {}}
        new_colour = base_arr
        if has_colour:
            merged, counts = merge_picture(base_arr, painted_px["colour"], edits["colour"], island, _pad_px(size))
            mode = "RGBA" if base_mode == "RGBA" else "RGB"
            new_base = sc.dir / "base_color.png"
            Image.fromarray(merged if mode == "RGBA" else merged[..., :3], mode).save(new_base)
            out.update(base=new_base, counts=counts)
            new_colour = merged
        parent_em_file = library.version_file(parent, "emission", sc.settings)
        parent_em = None
        if parent_em_file is not None:
            with Image.open(parent_em_file) as em_img:
                em_img = em_img.convert("RGB")
                if em_img.size != (size, size):
                    em_img = em_img.resize((size, size), Image.BILINEAR)
                parent_em = np.asarray(em_img)
        if has_colour or has_glow:
            out["emission"] = carry_glow(parent_em, base_arr, new_colour, glow_strength, edits.get("glow"), island, _pad_px(size))
        for key, file_name in (("shine", "roughness"), ("metal", "metallic")):
            if key not in maps:
                continue
            parent_file = library.version_file(parent, file_name, sc.settings)
            if parent_file is None:
                raise ValueError(f"the clean-up has no {key} picture to paint on")
            parent_map = np.asarray(Image.open(parent_file).convert("RGBA"))
            if parent_map.shape[:2] != (size, size):
                raise ValueError(f"the clean-up's {key} picture is not the same size as its colour picture")
            merged, counts = merge_picture(parent_map, painted_px[key], edits[key], island, _pad_px(size))
            new_file = sc.dir / f"{file_name}.png"
            Image.fromarray(merged[..., :3], "RGB").save(new_file)
            out[key] = new_file
            out[f"{key}_counts"] = counts
        new_glb = sc.dir / Path(parent_glb).name
        shutil.copyfile(parent_glb, new_glb)
        swapped = any(out.get(k) for k in ("base", "shine", "metal"))  # a glow-only save leaves the model's pictures alone
        if swapped and not imaging.glb_replace_images(new_glb, base_color=out.get("base"), roughness=out.get("shine"), metallic=out.get("metal")):
            raise ValueError("the new picture could not be written into the model")
        out["glb"] = new_glb
        return out

    done = await asyncio.to_thread(work)
    counts = done["counts"]
    if has_colour:
        mask_out = sc.dir / "paint_mask.png"
        Image.fromarray((edits["colour"] * 255).astype(np.uint8), "L").save(mask_out)
        sc.register("base_color", done["base"])
        sc.register("paint_mask", mask_out)
    sc.register("glb", done["glb"])
    if has_colour or has_glow:  # the glow keeps its strength under a colour repaint; the Glow brush and eraser change it
        em_out = sc.dir / "emission.png"
        if done["emission"] is not None:
            Image.fromarray(done["emission"], "RGB").save(em_out)
            sc.register("emission", em_out)
        elif parent.files.get("emission"):
            em_out.unlink(missing_ok=True)
            files = sc.version.files
            files.pop("emission", None)
            sc.version.files = files
            sc.save()
    for key, file_name in (("shine", "roughness"), ("metal", "metallic")):
        if key not in maps:
            continue
        sc.register(file_name, done[key])
        mask_file = sc.dir / f"paint_{key}_mask.png"
        Image.fromarray((edits[key] * 255).astype(np.uint8), "L").save(mask_file)
        sc.register(f"paint_{key}_mask", mask_file)
    strokes_out = sc.dir / "paint_strokes.json"
    shutil.copyfile(inputs["strokes"], strokes_out)
    sc.register("paint_strokes", strokes_out)
    carried = {k: v for k, v in (parent.metrics or {}).items() if k not in _RUN_METRICS}
    sc.set_metrics(**carried, painted=True, painted_of=parent.id, geometry_of=(parent.metrics or {}).get("geometry_of") or parent.id,
                   paint_strokes=len(json.loads(strokes_out.read_text(encoding="utf-8"))), paint_texture_size=size,
                   paint_texels=int(edits["colour"].sum()) if has_colour else 0,
                   paint_island_texels=counts.get("painted_on_island", 0),
                   paint_padding_texels=counts.get("padding_texels", 0),
                   paint_shine_texels=int(edits["shine"].sum()) if "shine" in edits else None,
                   paint_metal_texels=int(edits["metal"].sum()) if "metal" in edits else None,
                   paint_glow_texels=int(edits["glow"].sum()) if "glow" in edits else None)
    for p in inputs.values():
        p.unlink(missing_ok=True)
    sc.set_params(paint_of=parent.id)


def _edit_of(mask_path: Path, size: tuple[int, int]) -> np.ndarray:
    mask_img = Image.open(mask_path).convert("L")
    if mask_img.size != size:
        raise ValueError("the edit mask and the painted picture have different sizes")
    return np.asarray(mask_img) > 127


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
    em = glow.carry(library.version_file(parent, "emission", sc.settings), sc.dir / "emission.png")
    if em is not None:
        sc.register("emission", em)
    carried = {k: v for k, v in (parent.metrics or {}).items() if k not in _RUN_METRICS}
    metrics = {**carried, "painted": True, "painted_of": parent.id,
               "geometry_of": (parent.metrics or {}).get("geometry_of") or parent.id, "polished": strength,
               "polish_texels": done["counts"]["polished_texels"], "polish_padding_texels": done["counts"]["padding_texels"]}
    sc.set_metrics(**metrics)
    sc.set_params(smooth_of=parent.id, polish_strength=strength)
