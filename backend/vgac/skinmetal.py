"""Skin is never metal (docs/KNOWN-FAULTS-RESEARCH.md 1, "How widespread").

The 3D builder spreads metal from armour and helmets onto the face: the first knight's face shipped at about 0.68
metal, the goblin's at about 0.49, and the clean-up step's metal sharpening can then push that to full metal. Run by
the clean-up step right after the face pass, on the final layout, for humanoids:

1. the face the face pass finds (face.find_face on a front render: the skin colour at the nose and the skin-coloured
   region joined to it) gives the skin colour and the face triangles;
2. the head and neck are the triangles in the top NECK_SHARE of the height;
3. a head or neck triangle is skin when most of its texels are skin-coloured; skin triangles joined to the face
   (through shared edges) are the visible skin of the head and neck;
4. metal goes to 0 on the face triangles and on the skin-coloured texels of the joined skin triangles. Armour,
   helmets, jewellery and metal collars are other colours, or not joined to the face, and keep their metal.

The hair pass clears metal on hair and on skin-coloured texels near the hair (hair.clear_hair_metal); this is the
rest of the skin. Skipped when the description makes the character itself metal (a robot, a golem, a statue).
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from . import face, hair, imaging

NECK_SHARE = 0.2  # head and neck: the top fifth of a standing figure's height (the head alone is about 0.13)
SKIN_TOLERANCE = 18.0  # weighted Lab distance to the face's skin colour (face.find_face grows the face at 16)
SKIN_TRIANGLE_SHARE = 0.5  # a triangle is skin when at least this share of its texels is skin-coloured
SKIN_RED_MIN = 6.0  # Lab a: skin is reddish; leather, steel and cloth hoods sit under it
METAL_LIMIT = 0.5  # the measure: texels over this count as metal
# Descriptions where the character itself is metal: its skin keeps the builder's metal.
METAL_BODY = re.compile(r"\b(robot|robotic|android|cyborg|golem|automaton|mech|clockwork|statue|"
                        r"(metal|metallic|steel|iron|bronze|golden|gold|silver|copper|brass)[ -](skin|skinned|body|bodied|face)|"
                        r"made (entirely )?of (metal|steel|iron|bronze|gold|silver|copper|brass))\b", re.I)


def metal_body(description: str | None) -> bool:
    """Whether the description makes the character itself metal."""
    return bool(description and METAL_BODY.search(description))


def skin_like(lab: Any) -> bool:
    """Whether a Lab colour is a human or goblin-like skin tone: warm (red and yellow over grey) and not too dark."""
    L, a, b = (float(v) for v in lab)
    return 20.0 <= L <= 92.0 and a >= SKIN_RED_MIN and b >= 4.0


def face_from_skin(view: dict[str, Any], mesh: dict[str, np.ndarray], cam: Any, head: Any) -> dict[str, Any] | None:
    """face.find_face's region grown from another seed: the most forward skin-like pixel in the middle of the head
    (6 to 25 percent of the height below the top, within a fifth of the head's width of its middle)."""
    S = cam.size
    ras, m = view["raster"], view["mask"]
    pos = face.interpolate(mesh["verts"], mesh["faces"], ras)
    lab = face.to_lab(view["image"])
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    cand = m & (pos[..., 1] <= head.top - 0.06 * head.height) & (pos[..., 1] >= head.top - 0.25 * head.height)
    cand &= np.abs(pos[..., 0] - head.centre[0]) <= 0.2 * head.width
    cand &= (L >= 20) & (L <= 92) & (a >= SKIN_RED_MIN) & (b >= 4)
    cand = face.erode(cand, max(2, S // 128))  # a patch of skin, not a stray texel
    if cand.sum() < 50:
        return None
    z = np.where(cand, ras["z"], np.inf)
    iy, ix = np.unravel_index(int(np.argmin(z)), z.shape)
    r = max(4, S // 64)
    win = (slice(max(0, iy - r), iy + r + 1), slice(max(0, ix - r), ix + r + 1))
    skin = np.median(lab[win][m[win]], axis=0)
    dist = np.linalg.norm((lab - skin) * np.array([0.5, 1.0, 1.0]), axis=-1)
    allowed = m & (dist < 16.0) & (pos[..., 1] >= head.top - 0.25 * head.height)
    seed = np.zeros_like(m)
    seed[win] = True
    region = face.grow(seed, allowed)
    close = max(2, S // 80)
    region = face.fill_holes(face.erode(face.dilate_mask(region, close), close) & m) & m
    if region.sum() < 200:
        return None
    return {"found": True, "skin": [round(float(v), 2) for v in skin], "region": region, "nose": (int(ix), int(iy))}


def skin_texels(mesh: dict[str, np.ndarray], base: np.ndarray, T: int) -> dict[str, Any] | None:
    """The visible skin of the head and neck at texture size T. -> {ys, xs (texels), face_texels, skin_lab, region
    (the texel region of the head and neck)} or None when no face is found."""
    head = face.find_head(mesh["verts"])
    if head is None:
        return None
    cam = face.head_camera(head, 0, 1024)
    view = face.render_view(mesh, base, cam)
    info = face.find_face(view, mesh, cam, head)
    if not info.get("found") or not skin_like(info["skin"]):
        # the nearest bump was not skin (the brim of a hood, a helmet's nose guard): the face is the most forward
        # skin-coloured surface in the middle of the head instead
        info = face_from_skin(view, mesh, cam, head) or info
    if not info.get("found"):
        return None
    skin = np.asarray(info["skin"], dtype=np.float64)
    face_tris = np.zeros(len(mesh["faces"]), dtype=bool)
    face_tris[face.face_triangles(view, info["region"])] = True
    cy = mesh["verts"][mesh["faces"]].mean(axis=1)[:, 1]
    near = cy >= head.top - NECK_SHARE * head.height
    reg = hair.texel_region(mesh, T, near | face_tris)
    b = base if base.shape[0] == T else face.resize(base, T, Image.BOX)
    lab = face.to_lab(np.clip(b[reg["ys"], reg["xs"]], 0, 1))
    dist = np.linalg.norm((lab - skin) * np.array([0.5, 1.0, 1.0]), axis=-1)
    # the face's own skin colour, or a lighter or redder skin tone near it (ears, a flushed neck); bronze and gold are
    # far yellower than they are red and stay out
    warm = (lab[:, 0] >= 20) & (lab[:, 0] <= 92) & (lab[:, 1] >= SKIN_RED_MIN) & (lab[:, 2] >= 0) & (lab[:, 2] <= 2.5 * lab[:, 1])
    is_skin = (dist < SKIN_TOLERANCE) | (warm & (dist < 2 * SKIN_TOLERANCE))
    n_tri = len(mesh["faces"])
    total = np.bincount(reg["tri"], minlength=n_tri)
    skin_count = np.bincount(reg["tri"], weights=is_skin.astype(np.float64), minlength=n_tri)
    skin_tri = (total > 0) & (skin_count >= SKIN_TRIANGLE_SHARE * np.maximum(total, 1)) & near
    # the skin triangles joined to the face, through shared edges
    nb = hair.triangle_neighbours(mesh["faces"], mesh["verts"])
    joined = face_tris.copy()
    frontier = list(np.nonzero(face_tris)[0])
    while frontier:
        nxt = []
        for t in frontier:
            for u in nb[t]:
                if skin_tri[u] and not joined[u]:
                    joined[u] = True
                    nxt.append(u)
        frontier = nxt
    on_face = face_tris[reg["tri"]]
    take = on_face | (joined[reg["tri"]] & is_skin)
    return {"ys": reg["ys"][take], "xs": reg["xs"][take], "face_texels": int(on_face.sum()), "skin_lab": skin.round(2).tolist(),
            "region": reg}


def clear(glb: Path | str, maps: dict[str, Path], category: str | None, description: str | None, out_dir: Path) -> dict[str, Any]:
    """The skin's metal set to 0 (pure CPU work, run in a thread). -> {files: {metallic: path} when changed,
    metrics: {skin_metal: {...}}}. Never raises: a failure is recorded and the maps stay as they are."""
    t0 = time.time()
    rec: dict[str, Any] = {"ran": False}
    try:
        if category != "humanoid":
            rec["skipped"] = "not a humanoid"
        elif metal_body(description):
            rec["skipped"] = "the description makes the character metal"
        elif not maps.get("metallic") or not maps.get("base_color"):
            rec["skipped"] = "no metal map"
        else:
            mesh = face.load_mesh(glb)
            met = imaging.read_map(maps["metallic"], grey=True)
            if mesh is None or met is None:
                rec["skipped"] = "no texture layout"
            else:
                T = met.shape[0]
                base = face.read_rgb(maps["base_color"])
                found = skin_texels(mesh, base, T)
                if found is None:
                    rec["skipped"] = "no face found"
                else:
                    ys, xs = found["ys"], found["xs"]
                    before = met[ys, xs]
                    out = met.copy()
                    out[ys, xs] = 0.0
                    written = np.zeros((T, T), dtype=bool)
                    written[ys, xs] = True
                    out = hair.spill(out, written, found["region"]["covered"] & ~written)
                    rec.update(ran=True, skin_texels=int(len(ys)), face_texels=found["face_texels"],
                               metal_before=round(float(before.mean()), 3) if len(before) else 0.0,
                               metal_share_before=round(float((before > METAL_LIMIT).mean()), 4) if len(before) else 0.0,
                               metal_after=0.0, texels_changed=int((before > 0.0).sum()))
                    if rec["texels_changed"]:
                        out_dir = Path(out_dir)
                        out_dir.mkdir(parents=True, exist_ok=True)
                        path = imaging.write_map(out, out_dir / "metallic.png")
                        rec["seconds"] = round(time.time() - t0, 2)
                        return {"files": {"metallic": path}, "metrics": {"skin_metal": rec}}
    except Exception as e:  # noqa: BLE001  (the maps stay as they are)
        rec.update(ran=False, error=str(e)[:300])
    rec["seconds"] = round(time.time() - t0, 2)
    return {"files": {}, "metrics": {"skin_metal": rec}}
