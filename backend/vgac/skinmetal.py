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

5. the rest of the body's bare skin (arms, hands, legs, torso, feet) is cleared the same way: skin-coloured texels (the
   face's own skin colour, tightly) on pieces of skin that are either joined to the head's skin or large enough to be a
   limb (BODY_MIN_AREA of the surface). A small skin-coloured piece (a bronze bracelet, a copper pendant) is not a limb
   and keeps its metal. Left as it was (head and neck only) when the description names armour or metal clothing,
   because bronze and copper armour sits close to a skin tone.

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
BODY_HUE_TOL = 22.0  # degrees: the body's skin has the face's hue (Lab angle of a and b), lit or in shadow
BODY_CHROMA_MIN = 0.3  # of the face's colourfulness: greyer than this is not skin (grey cloth, steel)
BODY_CHROMA_MAX = 1.8  # and redder or yellower than this is paint or cloth
BODY_L_MIN = 8.0  # shadowed skin is dark: lower than this is black
# When the builder marked the face itself as metal (BLANKET_SHARE of its skin), its metal map says nothing about the
# character: it is metal everywhere (clothes, hair, skin, the shadow side). Then only small metal-looking pieces keep
# their metal: grey or gold coloured, separate from each other, each smaller than KEEP_MAX_AREA of the surface
# (earrings, glasses frames, buckles, a pendant). Large pieces (a whole jacket, a head of hair) are cleared.
BLANKET_SHARE = 0.5
KEEP_MAX_AREA = 0.015
METAL_GREY_CHROMA = 14.0  # Lab chroma under this is grey (steel, silver, chrome)
METAL_GOLD_HUE = (65.0, 105.0)  # Lab hue angle of gold and brass (skin is at about 45 to 60)
METAL_GOLD_CHROMA = 22.0
BODY_MIN_AREA = 0.004  # a piece of body skin not joined to the head is a limb when it covers this share of the surface
# A description that puts metal on the body (armour, plate, mail, bronze or gold trim): the body's skin tone is then
# not told from metal by colour, so only the head and neck are cleared.
ARMOURED = re.compile(r"\b(armou?r(ed)?|plate|chain ?mail|mail|gauntlets?|greaves|cuirass|breastplate|bracers?|vambraces?|"
                      r"pauldrons?|bronze|brass|copper|gold(en)?|silver|steel|iron|metal(lic)?)\b", re.I)
# Descriptions where the character itself is metal: its skin keeps the builder's metal.
METAL_BODY = re.compile(r"\b(robot|robotic|android|cyborg|golem|automaton|mech|clockwork|statue|"
                        r"(metal|metallic|steel|iron|bronze|golden|gold|silver|copper|brass)[ -](skin|skinned|body|bodied|face)|"
                        r"made (entirely )?of (metal|steel|iron|bronze|gold|silver|copper|brass))\b", re.I)


def metal_body(description: str | None) -> bool:
    """Whether the description makes the character itself metal."""
    return bool(description and METAL_BODY.search(description))


def armoured(description: str | None) -> bool:
    """Whether the description puts metal on the body, so that only the head and neck are cleared."""
    return bool(description and ARMOURED.search(description))


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


def skin_texels(mesh: dict[str, np.ndarray], base: np.ndarray, T: int, body: bool = False) -> dict[str, Any] | None:
    """The visible skin of the head and neck at texture size T, and with body the rest of the bare skin too.
    -> {ys, xs (texels), face_texels, skin_lab, region (the texel region of the head and neck), body_texels,
    body_pieces, body_small_pieces (skin-coloured pieces too small to be a limb, left alone)} or None when no face
    is found."""
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
    ys, xs = reg["ys"][take], reg["xs"][take]
    out = {"ys": ys, "xs": xs, "face_texels": int(on_face.sum()), "skin_lab": skin.round(2).tolist(), "region": reg,
           "body_texels": 0, "body_pieces": 0, "body_small_pieces": 0, "head_texels": int(len(ys)),
           "left_ys": np.zeros(0, dtype=np.int64), "left_xs": np.zeros(0, dtype=np.int64), "covered": None}
    if body:
        by, bx, info = body_skin(mesh, b, T, skin, joined, nb)
        out.update(ys=np.concatenate([ys, by]), xs=np.concatenate([xs, bx]), **info)
    return out


def skin_hue(lab: np.ndarray, skin: np.ndarray) -> np.ndarray:
    """Whether Lab colours (n,3) are the face's skin, lit or in shadow: the hue angle of a and b within BODY_HUE_TOL of the
    face's, a colourfulness near it, and not black or white. Light changes the lightness of skin far more than its
    hue, so a figure lit from one side (its shadow side is dark) still reads as skin. Gold and brass sit at a far more
    yellow hue, and steel and silver are grey."""
    sa, sb = float(skin[1]), float(skin[2])
    c0 = max(float(np.hypot(sa, sb)), 1e-6)
    h0 = np.degrees(np.arctan2(sb, sa))
    chroma = np.hypot(lab[:, 1], lab[:, 2])
    dh = np.abs((np.degrees(np.arctan2(lab[:, 2], lab[:, 1])) - h0 + 180.0) % 360.0 - 180.0)
    return ((dh <= BODY_HUE_TOL) & (chroma >= BODY_CHROMA_MIN * c0) & (chroma <= BODY_CHROMA_MAX * c0)
            & (lab[:, 0] >= BODY_L_MIN) & (lab[:, 0] <= 94))


def small_metal_pieces(mesh: dict[str, np.ndarray], base: np.ndarray, met: np.ndarray, T: int) -> np.ndarray:
    """Texels of the small metal-looking pieces of a model whose metal map cannot be trusted (BLANKET_SHARE): triangles
    whose texels are mostly grey or gold coloured and marked metal, joined through shared edges into pieces, the
    pieces smaller than KEEP_MAX_AREA of the surface. -> bool (T, T) of the texels that keep their metal."""
    n_tri = len(mesh["faces"])
    v, f = mesh["verts"], mesh["faces"]
    area = 0.5 * np.linalg.norm(np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]), axis=1)
    reg = hair.texel_region(mesh, T, np.ones(n_tri, dtype=bool))
    lab = face.to_lab(np.clip(base[reg["ys"], reg["xs"]], 0, 1))
    chroma = np.hypot(lab[:, 1], lab[:, 2])
    hue = np.degrees(np.arctan2(lab[:, 2], lab[:, 1])) % 360.0
    colour = (chroma < METAL_GREY_CHROMA) | ((hue >= METAL_GOLD_HUE[0]) & (hue <= METAL_GOLD_HUE[1]) & (chroma >= METAL_GOLD_CHROMA))
    cand = colour & (met[reg["ys"], reg["xs"]] > METAL_LIMIT)
    total = np.bincount(reg["tri"], minlength=n_tri)
    cnt = np.bincount(reg["tri"], weights=cand.astype(np.float64), minlength=n_tri)
    tri = (total > 0) & (cnt >= SKIN_TRIANGLE_SHARE * np.maximum(total, 1))
    nb = hair.triangle_neighbours(f, v)
    piece = np.full(n_tri, -1, dtype=np.int64)
    sizes: list[float] = []
    for t0 in np.nonzero(tri)[0]:
        if piece[t0] >= 0:
            continue
        k = len(sizes)
        piece[t0] = k
        stack, a = [int(t0)], 0.0
        while stack:
            t = stack.pop()
            a += float(area[t])
            for u in nb[t]:
                if tri[u] and piece[u] < 0:
                    piece[u] = k
                    stack.append(int(u))
        sizes.append(a)
    keep = np.zeros((T, T), dtype=bool)
    if not sizes:
        return keep
    small = np.asarray(sizes) < KEEP_MAX_AREA * float(area.sum())
    in_piece = piece[reg["tri"]]
    take = cand & (in_piece >= 0) & small[np.maximum(in_piece, 0)]
    keep[reg["ys"][take], reg["xs"][take]] = True
    return keep


def body_skin(mesh: dict[str, np.ndarray], base: np.ndarray, T: int, skin: np.ndarray, joined: np.ndarray,
              nb: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """The skin-coloured texels of the pieces of body skin outside the head and neck. A piece (triangles joined through
    shared edges, most of whose texels are skin-coloured) counts when it is joined to the head's skin or covers
    BODY_MIN_AREA of the model's surface. -> (ys, xs, {body_texels, body_pieces, body_small_pieces})."""
    n_tri = len(mesh["faces"])
    v, f = mesh["verts"], mesh["faces"]
    area = 0.5 * np.linalg.norm(np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]), axis=1)
    reg = hair.texel_region(mesh, T, ~joined)
    lab = face.to_lab(np.clip(base[reg["ys"], reg["xs"]], 0, 1))
    is_skin = skin_hue(lab, skin)
    total = np.bincount(reg["tri"], minlength=n_tri)
    skin_count = np.bincount(reg["tri"], weights=is_skin.astype(np.float64), minlength=n_tri)
    skin_tri = (total > 0) & (skin_count >= SKIN_TRIANGLE_SHARE * np.maximum(total, 1)) & ~joined
    # the pieces: skin triangles joined through shared edges
    piece = np.full(n_tri, -1, dtype=np.int64)
    sizes: list[float] = []
    touches_head: list[bool] = []
    for t0 in np.nonzero(skin_tri)[0]:
        if piece[t0] >= 0:
            continue
        k = len(sizes)
        piece[t0] = k
        stack, a, head = [int(t0)], 0.0, False
        while stack:
            t = stack.pop()
            a += float(area[t])
            for u in nb[t]:
                if joined[u]:
                    head = True
                elif skin_tri[u] and piece[u] < 0:
                    piece[u] = k
                    stack.append(int(u))
        sizes.append(a)
        touches_head.append(head)
    if not sizes:
        left = is_skin
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64), {"body_texels": 0, "body_pieces": 0, "body_small_pieces": 0,
                                                                          "left_ys": reg["ys"][left], "left_xs": reg["xs"][left], "covered": reg["covered"]}
    sizes_a = np.asarray(sizes)
    keep = np.asarray(touches_head) | (sizes_a >= BODY_MIN_AREA * float(area.sum()))
    in_piece = piece[reg["tri"]]
    take = is_skin & (in_piece >= 0) & keep[np.maximum(in_piece, 0)]
    left = is_skin & ~take  # skin-coloured, but on a piece too small to be a limb or not on a skin piece at all
    return reg["ys"][take], reg["xs"][take], {"body_texels": int(take.sum()), "body_pieces": int(keep.sum()),
                                              "body_small_pieces": int((~keep).sum()),
                                              "left_ys": reg["ys"][left], "left_xs": reg["xs"][left], "covered": reg["covered"]}


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
                with_body = not armoured(description)
                found = skin_texels(mesh, base, T, body=with_body)
                if found is None:
                    rec["skipped"] = "no face found"
                else:
                    ys, xs = found["ys"], found["xs"]
                    before = met[ys, xs]
                    out = met.copy()
                    out[ys, xs] = 0.0
                    written = np.zeros((T, T), dtype=bool)
                    written[ys, xs] = True
                    written_skin = written.copy()
                    covered = found["region"]["covered"] | found["covered"] if found.get("covered") is not None else found["region"]["covered"]
                    head_before = before[: found["head_texels"]]  # the head and neck: the part that is certainly not metal
                    share = float((head_before > METAL_LIMIT).mean()) if len(head_before) else 0.0
                    rec["blanket"] = bool(with_body and share >= BLANKET_SHARE)
                    if rec["blanket"]:
                        # metal everywhere: nothing in the map is evidence. Clear it all but the small metal-looking pieces.
                        keep = small_metal_pieces(mesh, base, met, T)
                        every = hair.texel_region(mesh, T, np.ones(len(mesh["faces"]), dtype=bool))["covered"]
                        wipe = every & (met > 0.0) & ~keep
                        out[wipe] = 0.0
                        written |= wipe
                        covered = covered | every
                        rec["kept_metal_texels"] = int((keep & (met > METAL_LIMIT)).sum())
                        rec["blanket_cleared_texels"] = int((wipe & (met > METAL_LIMIT)).sum())
                    out = hair.spill(out, written, covered & ~written)
                    left = out[found["left_ys"], found["left_xs"]]  # after the clearing: what is left on small skin-coloured pieces
                    changed = int((before > 0.0).sum()) + (int((wipe & ~written_skin).sum()) if rec["blanket"] else 0)
                    rec.update(ran=True, skin_texels=int(len(ys)), face_texels=found["face_texels"], body=with_body,
                               left_skin_coloured=int(len(left)), left_metal=int((left > METAL_LIMIT).sum()),
                               skin_coloured_texels=int(len(left)) + int(found["body_texels"]) + int(found["head_texels"]),
                               body_texels=found["body_texels"], body_pieces=found["body_pieces"],
                               body_small_pieces=found["body_small_pieces"],
                               metal_before=round(float(before.mean()), 3) if len(before) else 0.0,
                               metal_share_before=round(share, 4), metal_share_before_all=round(float((before > METAL_LIMIT).mean()), 4) if len(before) else 0.0,
                               metal_after=0.0, texels_changed=changed)
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
