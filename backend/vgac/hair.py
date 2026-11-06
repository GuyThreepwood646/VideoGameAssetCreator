"""Hair detail pass: make the hair the 3D builder fused into one shell read as hair (docs/HAIR-RESEARCH.md, stage 1 (a)).

Pure numpy on the CPU (the texture layout change is Blender's, blender/faceuv.py). Nothing is projected from the
pictures onto the model: the model's own base colour is the starting point, and every pattern is a function of the 3D
position on the surface, so nothing shows at the borders of texture pieces (UV islands). The pictures inform only the
hair mask (a vote), the flow direction (oriented line filters with a confidence) and colour statistics (how much
light and dark the painted hair has, and the colour of its lightest strands).

Parts, each replaceable (a creature's fur uses another mask and flow, same paint):
1. find_hair: which triangles and which texels are hair. The colours of the head region are grouped (k-means in Lab);
   the group nearest the crown is hair, the one at the nose is skin. Every triangle gets the model's own colour vote
   and, from every turnaround picture whose head outline lines up with a render of the model from that side, the
   picture's vote; hair is the triangles that win the vote and join the crown. Skips with a plain reason when hair
   cannot be told from skin or clothes with confidence.
2. flow: a direction per texel, tangent to the surface. Downward from the crown along the surface, unless the painted
   picture shows a clear direction (a bank of oriented line filters, gated by its coherence and by how squarely the
   surface faces the picture).
3. paint (paint_hair): streaks of lighter and darker hair along the flow in clumps and strands (noise averaged along
   streamlines on the surface in 3D: line integral convolution), darker under-layers and roots (ambient occlusion and
   distance to the hair's upstream edge), lighter tips (near its downstream edge), a matching strand height written
   into the normal map, roughness in a hair range, and for painted looks a soft highlight band across the flow where
   the surface faces up and outward. The hairline is softened on the hair side in a narrow band.

Coordinates: glTF (Y up, the character faces +Z), texture rows as face.uv_pixels.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image

from . import face

K_LAB = np.array([0.5, 1.0, 1.0])  # Lab distances count lightness half, as face.py does

# --- cameras with pitch -----------------------------------------------------------------------------------------


@dataclass
class ViewCam:
    """An orthographic camera like face.Camera, with pitch: positive pitch looks down from above."""
    target: np.ndarray
    yaw_deg: float
    scale: float
    size: int
    pitch_deg: float = 0.0

    @property
    def basis(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        a, b = math.radians(self.yaw_deg), math.radians(self.pitch_deg)
        back = np.array([math.sin(a) * math.cos(b), math.sin(b), math.cos(a) * math.cos(b)])
        right = np.array([math.cos(a), 0.0, -math.sin(a)])
        up = np.cross(back, right)
        return right, up, -back

    def project(self, p: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        right, up, fwd = self.basis
        d = p - self.target
        s = self.size / self.scale
        return np.stack([(d @ right) * s + self.size / 2, -(d @ up) * s + self.size / 2], axis=1), d @ fwd


def hair_camera(head: face.Head, yaw: float = 0.0, pitch: float = 0.0, size: int = 512, reach: float = 0.42) -> ViewCam:
    """A camera framing the top `reach` of the figure's height (long hair down the back included)."""
    scale = reach * head.height
    target = np.array([head.centre[0], head.top - 0.5 * scale + 0.02 * head.height, head.centre[2]])
    return ViewCam(target=target, yaw_deg=yaw, scale=scale, size=size, pitch_deg=pitch)


# The vote's views: (name, yaw, pitch, picture key). Pictures: the turnaround's cut-outs; the concept for the front.
VOTE_VIEWS = (("front", 0.0, 0.0, "front"), ("quarter_l", 40.0, 0.0, "quarter"), ("quarter_r", -40.0, 0.0, "quarter"),
              ("left", 90.0, 0.0, "left"), ("right", -90.0, 0.0, "right"), ("back", 180.0, 0.0, "back"),
              ("back_l", 135.0, 0.0, None), ("back_r", -135.0, 0.0, None), ("top", 0.0, 75.0, None))
PICTURE_IOU_MIN = 0.78  # a picture's head outline against the model's: under it the picture does not vote

# --- small helpers ----------------------------------------------------------------------------------------------


def lab_dist(lab: np.ndarray, ref: Any) -> np.ndarray:
    return np.linalg.norm((lab - np.asarray(ref, dtype=np.float64)) * K_LAB, axis=-1)


def weld_ids(verts: np.ndarray) -> np.ndarray:
    key = np.round(verts * 1e5).astype(np.int64)
    _, wid = np.unique(key, axis=0, return_inverse=True)
    return wid.reshape(-1)


def triangle_neighbours(faces: np.ndarray, verts: np.ndarray) -> list[np.ndarray]:
    """For each triangle, the triangles sharing an edge with it in 3D (vertices welded across UV seams)."""
    wid = weld_ids(verts)
    f = wid[faces]
    edges = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    edges.sort(axis=1)
    tri = np.tile(np.arange(len(faces)), 3)
    order = np.lexsort((edges[:, 1], edges[:, 0]))
    e, t = edges[order], tri[order]
    same = np.all(e[1:] == e[:-1], axis=1)
    a, b = t[:-1][same], t[1:][same]
    nb: list[list[int]] = [[] for _ in range(len(faces))]
    for x, y in zip(a.tolist(), b.tolist()):
        nb[x].append(y)
        nb[y].append(x)
    return [np.array(n, dtype=np.int64) for n in nb]


def grow_tris(seed: np.ndarray, allowed: np.ndarray, nb: list[np.ndarray]) -> np.ndarray:
    """Triangles in `allowed` joined (through shared edges) to `seed`."""
    out = seed & allowed
    stack = list(np.nonzero(out)[0])
    while stack:
        t = stack.pop()
        for n in nb[t]:
            if allowed[n] and not out[n]:
                out[n] = True
                stack.append(n)
    return out


BARY = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1], [1 / 3, 1 / 3, 1 / 3], [0.5, 0.5, 0], [0, 0.5, 0.5], [0.5, 0, 0.5],
                 [2 / 3, 1 / 6, 1 / 6], [1 / 6, 2 / 3, 1 / 6], [1 / 6, 1 / 6, 2 / 3]])


def triangle_colours(mesh: dict[str, np.ndarray], tex: np.ndarray) -> np.ndarray:
    """The median Lab colour of each triangle's texture (ten points per triangle, the inner ones counted twice)."""
    T = tex.shape[0]
    uvp = face.uv_pixels(mesh["uv"], T)[mesh["faces"]]  # (m,3,2)
    pts = np.einsum("kj,mjc->mkc", BARY, uvp)  # (m,10,2)
    col = face.sample(tex, pts[..., 0].ravel(), pts[..., 1].ravel()).reshape(len(uvp), len(BARY), 3)
    lab = face.to_lab(np.clip(col, 0, 1))
    return np.median(np.concatenate([lab, lab[:, 3:]], axis=1), axis=1)


def kmeans(x: np.ndarray, init: np.ndarray, k: int, seed: int = 0, iters: int = 20) -> np.ndarray:
    """k-means in weighted Lab with the first centres given (hair, skin) and the rest chosen by k-means++."""
    rng = np.random.default_rng(seed)
    xw = x * K_LAB
    cent = [c * K_LAB for c in np.asarray(init, dtype=np.float64)]
    while len(cent) < k:
        d = np.min(np.stack([np.linalg.norm(xw - c, axis=1) for c in cent]), axis=0) ** 2
        if d.sum() <= 0:
            break
        cent.append(xw[rng.choice(len(xw), p=d / d.sum())])
    C = np.array(cent)
    for _ in range(iters):
        lab = np.argmin(np.linalg.norm(xw[:, None] - C[None], axis=2), axis=1)
        for j in range(len(C)):
            if (lab == j).any():
                C[j] = xw[lab == j].mean(axis=0)
    return C / K_LAB


def nearest_centre(lab: np.ndarray, centres: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(index of the nearest centre, distance to it) for Lab colours (any leading shape)."""
    d = np.stack([lab_dist(lab, c) for c in centres], axis=-1)
    return np.argmin(d, axis=-1), np.min(d, axis=-1)


# --- 1. the hair mask ---------------------------------------------------------------------------------------------

SEPARATION_MIN = 14.0  # hair's colour against skin and against the nearest other colour (weighted Lab)
CROWN_SHARE_MIN = 0.55  # share of the crown in the hair colour group: under it the top of the head is not one colour
HELMET_METAL = 0.6  # median metallic of a colourless crown above this: a helmet
HELMET_CHROMA = 12.0  # Lab chroma under this is grey (steel, silver)
REACH_BELOW = 0.42  # hair reaching lower than this share of the height from the top joins the clothing (a cloak, a hood)
BODY_SHARE_MAX = 1.0  # hair surface below the shoulders against hair surface on the head: more is clothing
AGREE_MIN = 0.6  # the model's vote and the pictures' vote agree on at least this share of the seen hair candidates
MIN_HAIR_TRIS = 40

REASONS = {
    "off": "the option is off",
    "not_category": "this kind of asset has no hair pass",
    "style": "this look keeps plain shapes",
    "strength_zero": "the strength is 0",
    "no_maps": "the model has no texture",
    "no_head": "no head was found",
    "no_face": "no face was found to tell skin from hair",
    "bald": "no hair was found on the head",
    "helmet": "the head is covered by something metal (a helmet)",
    "crown_mixed": "the top of the head is not one hair colour (a hat or a hood)",
    "not_hair_colour": "the top of the head is not a usual hair colour (most likely a hood or a hat)",
    "hair_like_skin": "the hair is too close in colour to the skin to tell them apart",
    "hair_like_clothes": "the hair is too close in colour to the clothing next to it",
    "joined_to_clothing": "the hair colour runs on into the clothing (a hood or a cloak in the same colour)",
    "pictures_disagree": "the pictures and the model disagree on where the hair is",
    "error": "something went wrong on the way",
}


def reason_words(reason: str | None) -> str:
    return REASONS.get(reason or "", reason or "")


def render_views(mesh: dict[str, np.ndarray], tex: np.ndarray, head: face.Head, size: int = 512) -> dict[str, dict[str, Any]]:
    out = {}
    for name, yaw, pitch, _ in VOTE_VIEWS:
        cam = hair_camera(head, yaw, pitch, size)
        out[name] = {"cam": cam, **face.render_view(mesh, tex, cam)}
    return out


def box_fit(pic: np.ndarray, render: np.ndarray, rows: tuple[int, int], most: float = 0.12) -> dict[str, Any]:
    """One scale and shift that put the picture's head outline on the render's (both framed alike already): the
    outlines' widths and middles inside the rows given, and their tops. Returns the alignment (as face.align) and
    iou, the overlap of the two outlines in those rows after it (1 the same shape)."""
    S = render.shape[0]
    band_ = np.zeros_like(render)
    band_[rows[0]:rows[1]] = True

    def extent(m: np.ndarray) -> tuple[float, float, float] | None:
        ys, xs = np.nonzero(m & band_)
        if len(xs) < 50:
            return None
        top = float(np.nonzero(m.any(axis=1))[0][0])
        return float(np.percentile(xs, 2)), float(np.percentile(xs, 98)), top

    a, b = extent(pic), extent(render)
    if a is None or b is None:
        return {"sx": 1.0, "sy": 1.0, "tx": 0.0, "ty": 0.0, "centre": (S / 2, S / 2), "iou": 0.0}
    k = float(np.clip((b[1] - b[0]) / max(a[1] - a[0], 1.0), 1 - most, 1 + most))
    c = ((a[0] + a[1]) / 2, a[2])
    fit = {"sx": k, "sy": k, "tx": (b[0] + b[1]) / 2 - c[0], "ty": b[2] - c[1], "centre": c}
    moved = face.apply_alignment(pic.astype(np.float64)[..., None].repeat(3, axis=2), fit)[..., 0] > 0.5
    inter = (moved & render & band_).sum()
    union = ((moved | render) & band_).sum()
    fit["iou"] = round(float(inter) / float(max(union, 1)), 4)
    return fit


def picture_votes(mesh: dict[str, np.ndarray], views: dict[str, dict[str, Any]], head: face.Head, pictures: dict[str, Path],
                  centres: np.ndarray, hair_i: int, skin_i: int, tri_label: np.ndarray) -> dict[str, Any]:
    """Each picture's vote per triangle: the picture is framed like the render (face.painted_head) and lined up with
    it by the head outline (face.silhouette_fit); not used under PICTURE_IOU_MIN. Its colours are grouped like the
    model's, with the hair and skin centres moved to the picture's own (sampled where the model has hair and skin).
    Returns {hair (m,) summed weight of hair votes, seen (m,) summed weight, used: {view: iou}}."""
    m = len(mesh["faces"])
    hair_v, seen_v = np.zeros(m), np.zeros(m)
    used: dict[str, Any] = {}
    cache: dict[str, np.ndarray] = {}
    for name, yaw, pitch, key in VOTE_VIEWS:
        path = pictures.get(key or "")
        if key is None or path is None or not Path(path).is_file() or pitch != 0:
            continue
        if key not in cache:
            cache[key] = np.asarray(Image.open(path).convert("RGBA"), dtype=np.float64) / 255.0
        v = views[name]
        crop = face.painted_head(cache[key], head, v["cam"], size=v["cam"].size)
        if crop is None:
            continue
        rows_ = np.nonzero(v["mask"].any(axis=1))[0]
        if len(rows_) < 20:
            continue
        rows = (int(rows_[0]), int(rows_[0]) + int(0.45 * v["cam"].size))
        fit = box_fit(crop["alpha"] > 0.5, v["mask"], rows)
        if fit["iou"] < PICTURE_IOU_MIN:
            used[name] = {"iou": fit["iou"], "used": False}
            continue
        pic = face.apply_alignment(crop["image"], fit)
        alpha = face.apply_alignment(np.repeat(crop["alpha"][..., None], 3, axis=2), fit)[..., 0]
        lab = face.to_lab(np.clip(pic, 0, 1))
        tri = v["raster"]["tri"]
        ok = v["mask"] & (alpha > 0.5) & (v["facing"] > 0.25)
        # the picture's own hair and skin colours, where the model's colour says hair or skin
        lt = np.where(tri >= 0, tri_label[np.maximum(tri, 0)], -1)
        c = centres.copy()
        for idx in (hair_i, skin_i):
            sel = ok & (lt == idx) & face.erode(lt == idx, 2)
            if sel.sum() > 80:
                c[idx] = np.median(lab[sel], axis=0)
        lbl, _ = nearest_centre(lab, c)
        t = tri[ok]
        w = v["facing"][ok]
        np.add.at(seen_v, t, w)
        np.add.at(hair_v, t, w * (lbl[ok] == hair_i))
        used[name] = {"iou": fit["iou"], "used": True}
    return {"hair": hair_v, "seen": seen_v, "used": used}


def find_hair(glb: Path, base_color: Path, metallic: Path | None = None, pictures: dict[str, Path] | None = None,
              seed: int = 0, size: int = 384) -> dict[str, Any]:
    """Which triangles are hair (humanoid). Returns {found, reason, tris (m,) bool, centres, hair_lab, skin_lab,
    separation, agreement, ...measures}. found is False with a REASONS key when the hair cannot be found with
    confidence."""
    mesh = face.load_mesh(glb)
    if mesh is None:
        return {"found": False, "reason": "no_maps"}
    tex = face.read_rgb(base_color)
    head = face.find_head(mesh["verts"])
    if head is None:
        return {"found": False, "reason": "no_head"}
    # the skin: around the nose of the front view (face.find_face)
    fcam = face.head_camera(head, 0, 1024)
    fview = face.render_view(mesh, tex, fcam)
    info = face.find_face(fview, mesh, fcam, head)
    if not info.get("found"):
        return {"found": False, "reason": "no_face"}
    skin_lab = np.asarray(info["skin"], dtype=np.float64)
    verts, faces = mesh["verts"], mesh["faces"]
    centres_xyz = verts[faces].mean(axis=1)
    tri_lab = triangle_colours(mesh, tex)
    a = verts[faces[:, 1]] - verts[faces[:, 0]]
    b = verts[faces[:, 2]] - verts[faces[:, 0]]
    area = 0.5 * np.linalg.norm(np.cross(a, b), axis=1)
    H = head.height
    near_head = centres_xyz[:, 1] >= head.top - REACH_BELOW * 1.3 * H
    crown = centres_xyz[:, 1] >= head.top - 0.03 * H
    if crown.sum() < 5:
        return {"found": False, "reason": "no_head"}
    crown_lab = np.median(tri_lab[crown], axis=0)
    # helmet: the crown is grey and metal. The 3D builder often marks hair as metal too (the knight's red hair is 0.86),
    # so metal alone says nothing; a colourless metal crown does.
    crown_chroma = float(np.hypot(crown_lab[1], crown_lab[2]))
    if metallic is not None and Path(metallic).is_file() and crown_chroma < HELMET_CHROMA:
        met = face.read_rgb(metallic)[..., 0]
        T = met.shape[0]
        uvc = face.uv_pixels(mesh["uv"], T)[faces[crown]].mean(axis=1)
        crown_metal = float(np.median(face.sample(met[..., None], uvc[:, 0], uvc[:, 1])[:, 0]))
        if crown_metal > HELMET_METAL:
            return {"found": False, "reason": "helmet", "crown_metal": round(crown_metal, 3), "crown_chroma": round(crown_chroma, 1)}
    # colour groups of the upper body: hair (from the crown), skin (from the nose), and four others
    rng = np.random.default_rng(seed)
    pool = np.nonzero(near_head)[0]
    pick = rng.choice(pool, size=min(len(pool), 6000), replace=False, p=area[pool] / area[pool].sum())
    centres = kmeans(tri_lab[pick], np.stack([crown_lab, skin_lab]), 6, seed)
    label, _ = nearest_centre(tri_lab, centres)
    hair_i, skin_i = 0, 1
    crown_share = float(np.average(label[crown] == hair_i, weights=area[crown]))
    others = [j for j in range(len(centres)) if j not in (hair_i, skin_i)]
    d_skin = float(lab_dist(centres[hair_i], centres[skin_i]))
    d_other = min((float(lab_dist(centres[hair_i], centres[j])) for j in others), default=99.0)
    record: dict[str, Any] = {"hair_lab": [round(float(v), 1) for v in centres[hair_i]],
                              "skin_lab": [round(float(v), 1) for v in skin_lab], "crown_share": round(crown_share, 3),
                              "separation_skin": round(d_skin, 1), "separation_other": round(d_other, 1)}
    nb = triangle_neighbours(faces, verts)
    if verdict(record):
        # what would have been taken as hair (the colour group joined to the crown), for the picture
        return {"found": False, "reason": verdict(record), "tris": grow_tris(crown & (label == hair_i), label == hair_i, nb), **record}
    # the vote: the model's own colour per triangle, and every lined-up picture's
    views = render_views(mesh, tex, head, size)
    pics = picture_votes(mesh, views, head, pictures or {}, centres, hair_i, skin_i, label)
    model_hair = (label == hair_i).astype(np.float64)
    W_MODEL = 1.0
    score = (W_MODEL * model_hair + pics["hair"]) / (W_MODEL + pics["seen"])
    candidate = score >= 0.5
    hair = grow_tris(crown & candidate, candidate, nb)
    # flakes and specks: small hair-coloured patches off the main hair are left alone (they are not joined)
    record["triangles"] = int(hair.sum())
    if verdict(record):
        return {"found": False, "reason": verdict(record), "tris": hair, **record}
    # agreement between the model and the pictures, on the triangles the pictures saw well
    seen = pics["seen"] > 0.5
    cand_seen = seen & (hair | (model_hair > 0) | (pics["hair"] > 0.25 * pics["seen"]))
    agree = float(np.mean((model_hair[cand_seen] > 0.5) == (pics["hair"][cand_seen] > 0.5 * pics["seen"][cand_seen]))) if cand_seen.sum() > 30 else None
    record["agreement"] = None if agree is None else round(agree, 3)
    record["pictures"] = pics["used"]
    # how far the hair reaches down, and how much of it is below the shoulders
    y = centres_xyz[:, 1]
    low = float(np.percentile(y[hair], 2))
    reach = (head.top - low) / H
    on_head = hair & (y >= head.top - 0.18 * H)
    below = hair & (y < head.top - 0.18 * H)
    body_share = float(area[below].sum() / max(area[on_head].sum(), 1e-12))
    record.update(reach=round(reach, 3), body_share=round(body_share, 3), triangles=int(hair.sum()),
                  area_m2=round(float(area[hair].sum()), 5))
    if verdict(record):
        return {"found": False, "reason": verdict(record), "tris": hair, **record}
    # the nearest other colour that the hair actually touches decides "too close to the clothes"
    touch = np.zeros(len(faces), dtype=bool)
    for t in np.nonzero(hair)[0]:
        touch[nb[t]] = True
    touch &= ~hair
    touched = [j for j in others if (touch & (label == j)).sum() >= 5]
    d_touch = min((float(lab_dist(centres[hair_i], centres[j])) for j in touched), default=99.0)
    record["separation_touching"] = round(d_touch, 1)
    if verdict(record):
        return {"found": False, "reason": verdict(record), "tris": hair, **record}
    confidence = min(1.0, min(d_skin, d_touch) / (2 * SEPARATION_MIN)) * (1.0 if agree is None else min(1.0, agree / 0.85))
    return {"found": True, "reason": None, "tris": hair, "centres": centres, "hair_i": hair_i, "skin_i": skin_i,
            "tri_label": label, "confidence": round(confidence, 3), **record}


def texels_per_cm(mesh: dict[str, np.ndarray], tris: np.ndarray, T: int) -> float:
    """Texels (texture pixels) per centimetre on the selected triangles: their texture area over their surface area."""
    f = mesh["faces"][np.nonzero(tris)[0]]
    v, uv = mesh["verts"], mesh["uv"] * T
    a3 = 0.5 * np.linalg.norm(np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]), axis=1).sum()
    d1, d2 = uv[f[:, 1]] - uv[f[:, 0]], uv[f[:, 2]] - uv[f[:, 0]]
    a2 = 0.5 * np.abs(d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]).sum()
    return round(math.sqrt(a2 / max(a3, 1e-12)) / 100.0, 2)


def atlas_share(mesh: dict[str, np.ndarray], tris: np.ndarray) -> float:
    f = mesh["faces"][np.nonzero(tris)[0]]
    uv = mesh["uv"]
    d1, d2 = uv[f[:, 1]] - uv[f[:, 0]], uv[f[:, 2]] - uv[f[:, 0]]
    return round(float(0.5 * np.abs(d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]).sum()), 5)


TARGET_TPC_2048 = 14.0  # texels per cm the hair is given at a 2048 atlas (strands of about 2 mm stay 3 texels wide)
HAIR_GAIN_MOST = 2.5
HAIR_GAIN_MIN = 1.15


def hair_gain(tpc: float, T: int) -> float:
    """How many times its texels across the hair asks for: to TARGET_TPC_2048 scaled to the atlas, at most
    HAIR_GAIN_MOST; 1 when it already has them or would gain less than HAIR_GAIN_MIN."""
    target = TARGET_TPC_2048 * T / 2048.0
    g = min(HAIR_GAIN_MOST, target / max(tpc, 1e-6))
    return round(g, 3) if g >= HAIR_GAIN_MIN else 1.0


HAIR_HUE = (-35.0, 115.0)  # degrees of Lab hue that hair takes when it has colour: red, auburn, brown, blond
HAIR_CHROMA_GREY = 12.0  # under this the colour is black, grey or white: any of those is a hair colour


def natural_hair(lab: Any) -> bool:
    """Whether a Lab colour is one hair has (black, grey, white, or red to blond); green, blue and violet are taken
    for cloth (a hood). Dyed hair in those colours is skipped, which is the safe side."""
    L, a, b = (float(v) for v in lab)
    if math.hypot(a, b) < HAIR_CHROMA_GREY:
        return True
    hue = math.degrees(math.atan2(b, a))
    return HAIR_HUE[0] <= hue <= HAIR_HUE[1]


def tri_mask(mesh: dict[str, np.ndarray], tris: np.ndarray, T: int) -> np.ndarray:
    """(T,T) bool: the texels of the selected triangles (for a picture of what was taken as hair)."""
    from PIL import ImageDraw  # noqa: PLC0415

    px = face.uv_pixels(mesh["uv"], T) - 0.5
    im = Image.new("L", (T, T), 0)
    dr = ImageDraw.Draw(im)
    for f in mesh["faces"][np.nonzero(tris)[0]]:
        dr.polygon([(float(px[i, 0]), float(px[i, 1])) for i in f], fill=255)
    return np.asarray(im) > 0


def verdict(m: dict[str, Any]) -> str | None:
    """Why the hair cannot be found with confidence (a REASONS key), from find_hair's measures so far, or None. In
    the order a person would ask: is the top of the head one hair colour, can it be told from the skin, is there
    enough of it, does it stop at the head, can it be told from the clothes it touches, do the pictures agree."""
    if m.get("crown_share") is not None and float(m["crown_share"]) < CROWN_SHARE_MIN:
        return "crown_mixed"
    if m.get("hair_lab") is not None and not natural_hair(m["hair_lab"]):
        return "not_hair_colour"
    if m.get("separation_skin") is not None and float(m["separation_skin"]) < SEPARATION_MIN:
        return "hair_like_skin"
    if m.get("triangles") is not None and int(m["triangles"]) < MIN_HAIR_TRIS:
        return "bald"
    if (m.get("reach") is not None and float(m["reach"]) > REACH_BELOW) or (m.get("body_share") is not None and float(m["body_share"]) > BODY_SHARE_MAX):
        return "joined_to_clothing"
    if m.get("separation_touching") is not None and float(m["separation_touching"]) < SEPARATION_MIN:
        return "hair_like_clothes"
    if m.get("agreement") is not None and float(m["agreement"]) < AGREE_MIN:
        return "pictures_disagree"
    return None


def hair_points(mesh: dict[str, np.ndarray], tris: np.ndarray) -> list[list[float]]:
    """Centres of the hair triangles (glTF coordinates), as blender/faceuv.py --hair-points reads them."""
    return face.triangle_centres(mesh, np.nonzero(tris)[0]).round(6).tolist()


def match_tris(mesh: dict[str, np.ndarray], points: Any, tol: float = 2e-4) -> np.ndarray:
    """The triangles of `mesh` whose centres are within tol of one of points (a mask found on another export of the
    same mesh: the relayout keeps every triangle where it was)."""
    c = mesh["verts"][mesh["faces"]].mean(axis=1)
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    out = np.zeros(len(c), dtype=bool)
    if len(pts) == 0:
        return out
    cell = 4 * tol
    keys: dict[tuple[int, int, int], list[int]] = {}
    for i, k in enumerate(map(tuple, np.floor(pts / cell).astype(np.int64).tolist())):
        keys.setdefault(k, []).append(i)
    ck = np.floor(c / cell).astype(np.int64)
    offs = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)]
    for t in range(len(c)):
        x, y, z = ck[t]
        for dx, dy, dz in offs:
            for i in keys.get((x + dx, y + dy, z + dz), ()):
                if np.linalg.norm(pts[i] - c[t]) < tol:
                    out[t] = True
                    break
            if out[t]:
                break
    return out


# --- texels of the hair ---------------------------------------------------------------------------------------------


def ring(tris: np.ndarray, nb: list[np.ndarray], n: int = 1) -> np.ndarray:
    out = tris.copy()
    for _ in range(n):
        add = np.zeros_like(out)
        for t in np.nonzero(out)[0]:
            add[nb[t]] = True
        out |= add
    return out


def uv_jacobian(mesh: dict[str, np.ndarray], T: int) -> tuple[np.ndarray, np.ndarray]:
    """Per triangle, the surface direction of increasing texture column (u) and of increasing v (trimesh's v, which
    points up the texture image: the normal map's green), in metres per texel. (m,3) each."""
    v, f = mesh["verts"], mesh["faces"]
    uv = mesh["uv"] * T
    p0, p1, p2 = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    q0, q1, q2 = uv[f[:, 0]], uv[f[:, 1]], uv[f[:, 2]]
    e1, e2 = p1 - p0, p2 - p0
    d1, d2 = q1 - q0, q2 - q0
    det = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    det = np.where(np.abs(det) < 1e-12, 1e-12, det)
    tu = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) / det[:, None]
    tv = (e2 * d1[:, 0:1] - e1 * d2[:, 0:1]) / det[:, None]
    return tu, tv


def texel_region(mesh: dict[str, np.ndarray], T: int, tris: np.ndarray) -> dict[str, Any]:
    """The texels of the triangles in `tris` at texture size T: {ys, xs, tri, pos (n,3), nrm (n,3), tu, tv (n,3)}."""
    uvp = face.uv_pixels(mesh["uv"], T)
    ras = face.rasterise(uvp, np.zeros(len(uvp)), mesh["faces"], T, np.nonzero(tris)[0])
    cov = ras["tri"] >= 0
    ys, xs = np.nonzero(cov)
    pos = face.interpolate(mesh["verts"], mesh["faces"], ras)[cov]
    nrm = face.interpolate(mesh["vnormals"], mesh["faces"], ras)[cov]
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    tri = ras["tri"][cov]
    tu, tv = uv_jacobian(mesh, T)
    return {"ys": ys, "xs": xs, "tri": tri, "pos": pos, "nrm": nrm, "tu": tu[tri], "tv": tv[tri], "covered": cov}


def texel_mask(region: dict[str, Any], tex: np.ndarray, hair_tris: np.ndarray, centres: np.ndarray, hair_i: int,
               skin_i: int) -> dict[str, np.ndarray]:
    """Per texel of the region: hair (on a hair triangle or next to one, and nearest the hair colour), skin (nearest
    the skin colour) and the label. The hairline painted inside a triangle decides, not the triangle's edge."""
    col = tex[region["ys"], region["xs"]]
    lab = face.to_lab(np.clip(col, 0, 1))
    lbl, _ = nearest_centre(lab, centres)
    on_hair_tri = hair_tris[region["tri"]]
    # On a hair triangle everything but skin is hair (texels at a piece's edge pick up the gutter's colour, and the
    # shadow under a lock groups with the dark clothes); elsewhere only hair-coloured texels joined to it on the
    # surface (the fringe's locks painted onto the forehead's triangles), grown a few millimetres at a time in 3D.
    hair = on_hair_tri & (lbl != skin_i)
    cand = ~on_hair_tri & (lbl == hair_i)
    if cand.any():
        g = Grid(region["pos"], 0.003)
        for _ in range(GROW_STEPS):
            s, n = g.splat(region["pos"][hair], np.ones(int(hair.sum())), blur=1)
            near = g.read(s[..., 0], region["pos"]) > 0.02
            add = cand & near & ~hair
            if not add.any():
                break
            hair = hair | add
    # small holes (a stray texel or a short line of another colour inside the hair) are hair too: left as they were
    # they show as dark or light dashes once the hair around them is painted
    T = tex.shape[0]
    img = np.zeros((T, T), dtype=bool)
    img[region["ys"], region["xs"]] = hair
    closed = face.erode(face.dilate_mask(img, HOLE_PX), HOLE_PX)[region["ys"], region["xs"]]
    hair = hair | closed
    return {"hair": hair, "skin": lbl == skin_i, "label": lbl}


HOLE_PX = 2


GROW_STEPS = 12  # each step reaches about 3 to 6 mm along the surface


# --- 3D grids: a value per cell, read anywhere ----------------------------------------------------------------------


class Grid:
    """Values averaged into a 3D grid (cell metres) and read back with trilinear interpolation."""

    def __init__(self, pts: np.ndarray, cell: float, pad: float = 0.04):
        self.lo = pts.min(axis=0) - pad
        self.cell = cell
        self.dims = tuple(int(d) for d in np.ceil((pts.max(axis=0) + pad - self.lo) / cell).astype(int) + 1)

    def idx(self, p: np.ndarray) -> tuple[np.ndarray, ...]:
        i = np.clip(np.floor((p - self.lo) / self.cell).astype(int), 0, np.array(self.dims) - 1)
        return tuple(i.T)

    def splat(self, p: np.ndarray, v: np.ndarray, blur: int = 1) -> tuple[np.ndarray, np.ndarray]:
        """(sum of v per cell, count per cell), box-blurred `blur` times."""
        vv = v if v.ndim > 1 else v[:, None]
        s = np.zeros(self.dims + (vv.shape[1],))
        n = np.zeros(self.dims)
        ii = self.idx(p)
        np.add.at(s, ii, vv)
        np.add.at(n, ii, 1.0)
        for _ in range(blur):
            s, n = face._box3(s, 1), face._box3(n, 1)
        return s, n

    def read(self, grid: np.ndarray, p: np.ndarray) -> np.ndarray:
        return trilinear(grid, (p - self.lo) / self.cell - 0.5)

    def nearest(self, grid: np.ndarray, p: np.ndarray) -> np.ndarray:
        """The value of the cell each point is in (fast; for fields already smooth at the cell's size)."""
        return grid[self.idx(p)]


# --- 2. flow ------------------------------------------------------------------------------------------------------


def tangent(v: np.ndarray, n: np.ndarray) -> np.ndarray:
    t = v - (v * n).sum(axis=1, keepdims=True) * n
    return t / np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-12)


def crown_point(pos: np.ndarray) -> np.ndarray:
    """The top of the hair: the middle of its highest 2 percent, a little above their top."""
    y = pos[:, 1]
    top = pos[y >= np.percentile(y, 98)]
    return np.array([np.median(top[:, 0]), float(top[:, 1].max()) + 0.01, np.median(top[:, 2])])


def flow_down_from_crown(pos: np.ndarray, nrm: np.ndarray) -> np.ndarray:
    """The plain direction: away from the crown along the surface (outward on top, downward on the sides)."""
    c = crown_point(pos)
    d = pos - c
    d = d / np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-12) + np.array([0.0, -0.35, 0.0])
    return tangent(d, nrm)


LINE_ANGLES = 12
LINE_SIGMA = 1.5  # pixels (second-derivative scale: about one painted strand at the picture's framing)
LINE_POOL = 6.0  # the energy per orientation is pooled over about this many pixels


def line_orientation(img: np.ndarray, sigma: float = LINE_SIGMA, pool: float = LINE_POOL) -> dict[str, np.ndarray]:
    """A bank of oriented line filters (steered second derivatives of a Gaussian, LINE_ANGLES directions) over the
    picture's lightness. Returns {angle (H,W): the line's direction in radians (x right, y down), confidence (H,W)
    0..1: how much the best direction stands out from the others}."""
    lum = face.blur(face.luminance(img), sigma)
    gy, gx = np.gradient(lum)
    ixy = np.gradient(gx, axis=0)
    ixx = np.gradient(gx, axis=1)
    iyy = np.gradient(gy, axis=0)
    angs = np.arange(LINE_ANGLES) * math.pi / LINE_ANGLES
    energy = []
    for a in angs:  # a: the direction across the line
        c, s = math.cos(a), math.sin(a)
        r = c * c * ixx + 2 * c * s * ixy + s * s * iyy
        energy.append(face.blur(r * r, pool))
    E = np.stack(energy)
    mx = E.max(axis=0)
    mean = E.mean(axis=0)
    conf = np.clip((mx - mean) / np.maximum(mx, 1e-12), 0.0, 1.0)
    c2 = (E * np.cos(2 * angs)[:, None, None]).sum(axis=0)
    s2 = (E * np.sin(2 * angs)[:, None, None]).sum(axis=0)
    across = 0.5 * np.arctan2(s2, c2)
    return {"angle": across + math.pi / 2, "confidence": conf}


FLOW_CONF = (0.35, 0.7)  # filter confidence where the picture's direction starts to count, and where it counts fully
FLOW_FACING = (0.45, 0.8)
FLOW_USE_MIN = 0.1  # the share of the hair the picture gives a confident direction for; under it the picture is not used


def picture_flow(pos: np.ndarray, nrm: np.ndarray, base: np.ndarray, picture: np.ndarray, hair_px: np.ndarray, cam: Any,
                 mesh: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """The painted picture's direction at each texel (tangent, its sign matching base) and its weight 0..1: the
    filter confidence times how squarely the surface faces the picture, on texels the camera sees and the picture
    shows as hair."""
    ori = line_orientation(picture)
    sp, sd = cam.project(pos)
    view = face.view_raster(mesh, cam)
    S = cam.size
    xi = np.clip(sp[:, 0].astype(int), 0, S - 1)
    yi = np.clip(sp[:, 1].astype(int), 0, S - 1)
    right, up, fwd = cam.basis
    cosv = -(nrm @ fwd)
    tol = 3.0 * cam.scale / S / np.maximum(np.abs(cosv), 0.2)
    visible = sd <= view["z"][yi, xi] + tol
    ang = ori["angle"][yi, xi]
    d = tangent(np.cos(ang)[:, None] * right[None] - np.sin(ang)[:, None] * up[None], nrm)
    d = np.where(((d * base).sum(axis=1) < 0)[:, None], -d, d)
    w = face.smoothstep(*FLOW_CONF, ori["confidence"][yi, xi]) * face.smoothstep(*FLOW_FACING, cosv) * visible * hair_px[yi, xi]
    return d, w


def smooth_flow(pos: np.ndarray, flow: np.ndarray, cell: float = 0.006, blur: int = 2) -> tuple[Grid, np.ndarray]:
    """The flow averaged in a 3D grid (cell metres), for reading anywhere near the surface."""
    g = Grid(pos, cell)
    s, n = g.splat(pos, flow, blur)
    return g, s / np.maximum(n, 1e-9)[..., None]


# --- 3. the pattern: noise averaged along the flow on the surface ----------------------------------------------------


_LATTICE: dict[int, np.ndarray] = {}
LATTICE = 128  # the noise repeats every LATTICE cells (28 cm at the finest strands): not visible on one head


def _lattice(seed: int) -> np.ndarray:
    lat = _LATTICE.get(int(seed))
    if lat is None:
        lat = np.random.default_rng(int(seed) & 0xFFFFFFFF).uniform(-1.0, 1.0, LATTICE ** 3).astype(np.float32)
        if len(_LATTICE) > 8:
            _LATTICE.clear()
        _LATTICE[int(seed)] = lat
    return lat


def value_noise(p: np.ndarray, cell: float, seed: int) -> np.ndarray:
    """Smooth 3D value noise (-1..1) with features about `cell` metres apart; the same at the same point and seed."""
    lat = _lattice(seed)
    q = (p / cell).astype(np.float32)  # float32: a quarter faster on the hair's own texture's 600,000 texels
    f0 = np.floor(q)
    t = q - f0
    i0 = f0.astype(np.int64)
    t = t * t * (3 - 2 * t)
    L = LATTICE
    ix, iy, iz = i0[:, 0] % L, i0[:, 1] % L, i0[:, 2] % L
    jx, jy, jz = (ix + 1) % L, (iy + 1) % L, (iz + 1) % L
    tx, ty, tz = t[:, 0], t[:, 1], t[:, 2]
    out = np.zeros(len(p), dtype=np.float32)
    for ax, wx in ((ix, 1 - tx), (jx, tx)):
        for ay, wy in ((iy, 1 - ty), (jy, ty)):
            base = (ax * L + ay) * L
            wxy = wx * wy
            out += wxy * ((1 - tz) * lat[base + iz] + tz * lat[base + jz])
    return out.astype(np.float64)


def trilinear(grid: np.ndarray, f: np.ndarray) -> np.ndarray:
    """grid (X,Y,Z[,C]) read at fractional cell coordinates f (n,3), cell centres at whole numbers (clamped)."""
    dims = np.array(grid.shape[:3])
    f = np.clip(f, 0, dims - 1)
    i0 = np.minimum(np.floor(f).astype(np.int64), dims - 1)
    i1 = np.minimum(i0 + 1, dims - 1)
    t = f - i0
    flat = grid.reshape(int(dims.prod()), -1)
    Y, Z = int(dims[1]), int(dims[2])
    out = 0.0
    for ax, wx in ((i0[:, 0], 1 - t[:, 0]), (i1[:, 0], t[:, 0])):
        for ay, wy in ((i0[:, 1], 1 - t[:, 1]), (i1[:, 1], t[:, 1])):
            b = (ax * Y + ay) * Z
            out = out + (wx * wy)[:, None] * ((1 - t[:, 2])[:, None] * flat[b + i0[:, 2]] + t[:, 2][:, None] * flat[b + i1[:, 2]])
    return out if grid.ndim > 3 else out[:, 0]


def streamlines(pos: np.ndarray, grid: Grid, F: np.ndarray, step: float, n: int) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Points along the flow from every position: n steps of `step` metres downstream and upstream."""
    out = []
    for sign in (1.0, -1.0):
        p = pos.copy()
        path = []
        for _ in range(n):
            d = grid.read(F, p)
            d /= np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-9)
            p = p + sign * step * d
            path.append(p)
        out.append(path)
    return out[0], out[1]


FIELD_CELL = 0.0025  # metres: the roots and tips carried from one texture of the hair to the other


def read_field(f: tuple[Any, np.ndarray, np.ndarray], p: np.ndarray) -> np.ndarray:
    """A value splatted into a 3D grid (Grid, sums, counts), read back at p as a weighted mean: cells with no
    samples do not pull the value toward 0."""
    g, vs, vn = f
    w = g.read(vn, p)
    return np.clip(g.read(vs, p) / np.maximum(w, 1e-9), 0.0, 1.0) * (w > 1e-6)


FAST_PATHS_MIN = 200_000  # texels; above this streamlines are traced from 3 mm cells (streamlines_cells)
PATH_CELL = 0.003


def streamlines_cells(pos: np.ndarray, grid: Grid, F: np.ndarray, step: float, n: int,
                      cell: float = PATH_CELL) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """streamlines() for many texels (the hair's own texture holds about 600,000): traced once from the centre of
    every occupied `cell`-metre cell and carried to each texel as an offset (the flow is smooth over 6 mm, so the
    offsets hardly change inside a 3 mm cell). About ten times faster; the paths differ by well under a strand."""
    lo = pos.min(axis=0) - cell
    ijk = np.floor((pos - lo) / cell).astype(np.int64)
    keys, inv = np.unique(ijk, axis=0, return_inverse=True)
    inv = np.asarray(inv).reshape(-1)
    centres = lo + (keys + 0.5) * cell
    fwd, back = streamlines(centres, grid, F, step, n)
    return [pos + (p - centres)[inv] for p in fwd], [pos + (p - centres)[inv] for p in back]


def lic_paths(pos: np.ndarray, paths: tuple[list[np.ndarray], list[np.ndarray]], cell: float, seed: int, n: int,
              offset: np.ndarray | None = None, every: int = 1, halves: bool = False) -> np.ndarray:
    """Line integral convolution on the surface in 3D: noise (features `cell` apart) averaged along the first n
    points of each streamline with a Hann window, so it changes across the flow and hardly along it. offset moves
    every sample (the same streamline, shifted across the flow: the strand height beside the texel)."""
    o = 0.0 if offset is None else offset
    acc = value_noise(pos + o, cell, seed)
    wsum = np.ones(len(pos))
    for k in range(every, n + 1, every):
        w = 0.5 + 0.5 * math.cos(math.pi * k / (n + 1))
        for path in paths:
            acc += w * value_noise(path[k - 1] + o, cell, seed)
            wsum += w
            if halves:  # also halfway to the previous point: samples closer than the noise's features
                prev = pos if k == 1 else path[k - 2]
                wh = 0.5 + 0.5 * math.cos(math.pi * (k - 0.5) / (n + 1))
                acc += wh * value_noise(0.5 * (prev + path[k - 1]) + o, cell, seed)
                wsum += wh
    return acc / wsum


def lic(pos: np.ndarray, grid: Grid, F: np.ndarray, cell: float, half_len: float, seed: int, steps: int = 12) -> np.ndarray:
    """lic_paths over +-half_len metres (the streamlines traced here)."""
    paths = streamlines(pos, grid, F, half_len / steps, steps)
    return lic_paths(pos, paths, cell, seed, steps)


def normalise(v: np.ndarray) -> np.ndarray:
    s = float(v.std())
    return (v - float(v.mean())) / (s if s > 1e-9 else 1.0)


# --- 4. paint ---------------------------------------------------------------------------------------------------------

STRAND_CELL = 0.0022  # metres between strand features across the flow
STRAND_LEN = 0.025  # half the length strands are averaged over
PATH_STEPS = 10  # streamline points each way over STRAND_LEN (the clumps use the same points, further out)
CLUMP_CELL = 0.009
CLUMP_LEN = 0.05
STREAK_AMP = (0.07, 0.16)  # lightness swing of the streaks (fraction), the least and the most
CLUMP_GROOVE = 0.0  # extra darkening in the hollows between clumps (0.16 drew the outline of a fringe flake onto the brow; off)
AO_DARK = 0.45  # under-layers: lightness lost where the ambient occlusion map is black
ROOT_DARK = 0.1
TIP_LIGHT = 0.08
EDGE_REACH = 0.03  # how far along the flow the hair's edge is looked for (metres)
HIGHLIGHT = 0.4  # how far the painted highlight band moves toward the highlight colour at its middle
HIGHLIGHT_UP = (0.62, 0.2)  # the band: surface up-component at its middle, and its half-width
BUMP_TILT = 0.18  # normal map: the typical tilt the strand relief gives the surface (tangent units; 0.18 is about 10 degrees)
ROUGH = {"painted": 0.55, "photoreal": 0.45}
ROUGH_SWING = 0.06
HAIRLINE_M = 0.0035  # the softened band where hair meets skin (metres, hair side)
HAIRLINE_MIX = 0.45  # the most a hairline texel moves toward the skin next to it
STRAND_TEXELS = 3.0  # strands are never finer than this many texels (finer ones break into stair steps)


def picture_stats(picture: np.ndarray, hair_px: np.ndarray, px_per_strand: float = 2.0) -> dict[str, Any]:
    """How much light and dark the painted hair has: strand contrast (fine lightness changes against the mean),
    clump contrast (broad changes), and the colour of its lightest strands (the 95th percentile of lightness)."""
    if hair_px.sum() < 200:
        return {}
    lum = face.luminance(picture)
    mean = float(lum[hair_px].mean())
    fine = lum - face.masked_blur(lum, hair_px, px_per_strand * 2)
    broad = face.masked_blur(lum, hair_px, px_per_strand * 2) - face.masked_blur(lum, hair_px, px_per_strand * 12)
    inner = face.erode(hair_px, 3)
    if inner.sum() < 100:
        inner = hair_px
    hi = lum >= np.percentile(lum[inner], 95)
    return {"strand_contrast": round(float(fine[inner].std()) / max(mean, 1e-3), 4),
            "clump_contrast": round(float(broad[inner].std()) / max(mean, 1e-3), 4),
            "highlight_rgb": [round(float(v), 4) for v in picture[inner & hi].mean(axis=0)],
            "mean_rgb": [round(float(v), 4) for v in picture[inner].mean(axis=0)]}


def edge_along_flow(path: list[np.ndarray], step: float, hair_frac: Grid, Hf: np.ndarray, reach: float = EDGE_REACH) -> np.ndarray:
    """0..1, nearer 1 the closer the hair's edge lies along the streamline `path` (downstream: the tips; upstream: the
    roots): the first point where the hair share of the surface around drops under half."""
    n = min(len(path), max(1, int(round(reach / step))))
    hit = np.full(len(path[0]), np.inf)
    for k in range(n):
        v = hair_frac.nearest(Hf, path[k])
        edge = (v[:, 1] > 0.05) & (v[:, 0] < 0.5) & ~np.isfinite(hit)
        hit[edge] = (k + 1) * step
    return np.where(np.isfinite(hit), np.clip(1.0 - hit / reach, 0.0, 1.0), 0.0)


def texture_slope(H: np.ndarray, V: np.ndarray, axis: int) -> np.ndarray:
    """The change of H per texel along an image axis, from valid neighbours only (central where both are valid,
    one-sided where one is, 0 where none is)."""
    f, b = np.roll(H, -1, axis), np.roll(H, 1, axis)
    vf, vb = np.roll(V, -1, axis), np.roll(V, 1, axis)
    return np.where(vf & vb, (f - b) / 2, np.where(vf, f - H, np.where(vb, H - b, 0.0))) * V


def encode_normal(v: np.ndarray) -> np.ndarray:
    return np.clip(v * 0.5 + 0.5, 0.0, 1.0)


def decode_normal(c: np.ndarray) -> np.ndarray:
    v = c * 2.0 - 1.0
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-9)


def paint_hair(mesh: dict[str, np.ndarray], maps: dict[str, np.ndarray | None], hair_tris: np.ndarray, centres: np.ndarray,
               hair_i: int, skin_i: int, *, strength: float = 1.0, seed: int = 0, highlight: bool = True,
               photoreal: bool = False, protect: np.ndarray | None = None, flow_pictures: list[dict[str, Any]] | None = None,
               stats: dict[str, Any] | None = None, flow_fn: Callable[..., np.ndarray] | None = None,
               view_mesh: dict[str, np.ndarray] | None = None, paint_tris: np.ndarray | None = None,
               fields: dict[str, Any] | None = None, strand_cell: float | None = None) -> dict[str, Any]:
    """Paint the hair. maps: {base_color (T,T,3), normal, roughness, metallic, ao (T,T) or (T,T,3), each optional but
    the base colour}. protect (T,T) 0..1: texels the pass must not change (the face's features). flow_pictures:
    [{picture (S,S,3), hair_px (S,S) bool, cam}] painted pictures framed like their camera. view_mesh: the whole model,
    for what each picture's camera can see, when `mesh` is only the hair (its own texture: vgac/hairmat.py).
    Hair as its own material (vgac/hairmat.py) paints the hair once per texture from the same 3D fields:
    paint_tris (m,) bool: only the hair texels on these triangles are written (the hairline band the body keeps); the
    flow, the under-layers, roots and tips are still worked out over all of the hair. fields: the "fields" another call
    returned (the flow grid, and the occlusion, root and tip values in 3D grids): the hair alone has no surroundings to
    find its roots and tips, so they are read from the whole model's. strand_cell: the strands' width in metres (the
    same on both textures). Returns {maps (the changed ones, same shapes), mask (T,T) bool (the hair texels), measures,
    fields}. With strength 0 every map comes back unchanged. Metal is removed and roughness set on every hair texel,
    the face's included (the protection is for the face's colours: a lock over the brow kept the 3D step's metal)."""
    base = maps["base_color"]
    T = base.shape[0]
    out_maps: dict[str, np.ndarray] = {}
    strength = float(min(1.0, max(0.0, strength)))
    nb = triangle_neighbours(mesh["faces"], mesh["verts"])
    region_tris = ring(hair_tris, nb, 2)
    reg = texel_region(mesh, T, region_tris)
    tm = texel_mask(reg, base, hair_tris, centres, hair_i, skin_i)
    hair = tm["hair"]
    ys, xs = reg["ys"][hair], reg["xs"][hair]
    pos, nrm = reg["pos"][hair], reg["nrm"][hair]
    mask = np.zeros((T, T), dtype=bool)
    mask[ys, xs] = True
    measures: dict[str, Any] = {"hair_texels": int(hair.sum())}
    if hair.sum() < 50 or strength <= 0.0:
        return {"maps": {}, "mask": mask, "measures": {**measures, "painted": False}}
    # 2. flow
    base_flow = flow_fn(pos, nrm) if flow_fn else flow_down_from_crown(pos, nrm)
    flow = base_flow.copy()
    pic_w_total = np.zeros(len(pos))
    used_pictures = []
    for fp in (flow_pictures or []) if fields is None else []:
        d, w = picture_flow(pos, nrm, base_flow, fp["picture"], fp["hair_px"], fp["cam"], view_mesh or mesh)
        share = float((w > 0.5).mean())
        if share < FLOW_USE_MIN:
            used_pictures.append({"name": fp.get("name"), "used": False, "confident_share": round(share, 3)})
            continue
        new = w > pic_w_total
        flow = np.where(new[:, None], tangent(base_flow * (1 - w[:, None]) + d * w[:, None], nrm), flow)
        pic_w_total = np.maximum(pic_w_total, w)
        used_pictures.append({"name": fp.get("name"), "used": True, "confident_share": round(share, 3)})
    grid, F = smooth_flow(pos, flow) if fields is None else (fields["grid"], fields["F"])
    flow = tangent(grid.read(F, pos), nrm)
    measures.update(flow_from_picture=round(float((pic_w_total > 0.5).mean()), 3), flow_pictures=used_pictures,
                    flow_turn_deg=round(float(np.degrees(np.arccos(np.clip((flow * base_flow).sum(axis=1), -1, 1))).mean()), 1))
    # 3. the pattern
    texel_m = texel_size(mesh, reg, hair)
    s_cell = float(strand_cell) if strand_cell else max(STRAND_CELL, STRAND_TEXELS * texel_size(mesh, reg, hair, 85))
    measures.update(texel_mm=round(1000 * texel_m, 3), texels_per_cm=round(0.01 / max(texel_m, 1e-9), 2), strand_mm=round(1000 * s_cell, 2))
    step = STRAND_LEN / PATH_STEPS
    # the texels written: all of the hair, or those on paint_tris; the 3D fields always come from all of it
    W = np.ones(len(pos), dtype=bool) if paint_tris is None else np.asarray(paint_tris, dtype=bool)[reg["tri"][hair]]
    trace = streamlines_cells if len(pos) > FAST_PATHS_MIN else streamlines
    if fields is None:
        paths = trace(pos, grid, F, step, int(round(CLUMP_LEN / step)))
        paths_w = paths if W.all() else ([q[W] for q in paths[0]], [q[W] for q in paths[1]])
    else:
        paths = None
        paths_w = trace(pos[W], grid, F, step, int(round(CLUMP_LEN / step)))
    strand = normalise(lic_paths(pos[W], paths_w, s_cell, seed, PATH_STEPS, halves=True)) if W.any() else np.zeros(0)
    clump = normalise(lic_paths(pos[W], paths_w, CLUMP_CELL, seed + 1, int(round(CLUMP_LEN / step)), every=2)) if W.any() else np.zeros(0)
    st = stats or {}
    amp = float(np.clip(1.6 * float(st.get("strand_contrast") or 0.0), *STREAK_AMP)) if st else STREAK_AMP[0] + 0.03
    height = 0.6 * strand + 0.4 * clump
    lmul = 1.0 + amp * np.clip(0.6 * strand + 0.45 * clump, -2.5, 2.5)
    lmul -= CLUMP_GROOVE * face.smoothstep(0.4, 1.6, -clump)
    # under-layers and roots, tips: worked out on all of the hair (or read from the whole model's fields)
    out_fields: dict[str, Any] = {"grid": grid, "F": F, "s_cell": s_cell}
    ao = None
    if fields is not None:
        if fields.get("ao") is not None:
            ao = np.clip(fields["ao"][0].read(fields["ao"][1], pos), 0.0, 1.0)
        tip = read_field(fields["tip"], pos)
        root = read_field(fields["root"], pos)
        crown = np.asarray(fields["crown"])
    else:
        ao_map = maps.get("ao")
        if ao_map is not None:
            aa = ao_map if ao_map.ndim == 2 else ao_map[..., 0]
            if aa.shape[0] != T:
                aa = face.resize(aa, T, Image.BILINEAR)
            ao = np.clip(aa[ys, xs], 0.0, 1.0)
            # averaged on the surface over about a centimetre: under-layers are broad, and the occlusion map's own
            # edges (seams, its gutter) must not print as dark dashes
            ag = Grid(pos, 0.004)
            a_s, a_n = ag.splat(pos, ao, blur=1)
            ao_grid = a_s[..., 0] / np.maximum(a_n, 1e-9)
            ao = np.clip(ag.read(ao_grid, pos), 0.0, 1.0)
            out_fields["ao"] = (ag, ao_grid)
        all_lbl = reg["pos"]
        hair_frac = Grid(all_lbl, 0.004)
        s, n = hair_frac.splat(all_lbl, np.stack([hair.astype(np.float64), np.ones(len(hair))], axis=1), blur=1)
        Hf = np.stack([s[..., 0] / np.maximum(n, 1e-9), np.clip(n / max(float(np.percentile(n[n > 0], 50)), 1e-9), 0, 1)], axis=-1)
        tip = edge_along_flow(paths[0], step, hair_frac, Hf)
        root = edge_along_flow(paths[1], step, hair_frac, Hf)
        crown = crown_point(pos)
        # the roots and tips as 3D fields (2.5 mm cells, no blur): another texture of the same hair reads them there
        for key, val in (("tip", tip), ("root", root)):
            g = Grid(pos, FIELD_CELL)
            vs, vn = g.splat(pos, val, blur=0)
            out_fields[key] = (g, vs[..., 0], vn)
    out_fields["crown"] = crown
    # from here on only the texels written
    ys, xs, pos, nrm, flow = ys[W], xs[W], pos[W], nrm[W], flow[W]
    tip, root = tip[W], root[W]
    if ao is not None:
        lmul *= 1.0 - AO_DARK * (1.0 - ao[W])
    lmul *= (1.0 - ROOT_DARK * root ** 1.5) * (1.0 + TIP_LIGHT * tip)
    written = np.zeros((T, T), dtype=bool)
    written[ys, xs] = True
    col = base[ys, xs]
    new = np.clip(col * lmul[:, None], 0.0, 1.0)
    # the painted highlight band, across the flow where the surface faces up and outward
    band = np.zeros(len(pos))
    if highlight and not photoreal:
        hl = np.asarray(st.get("highlight_rgb") or np.clip(col.mean(axis=0) * 1.6 + 0.08, 0, 1), dtype=np.float64)
        # never darker or greyer than the hair itself: the lightest strands' colour, kept at least 25 percent lighter
        hl = np.maximum(hl, np.clip(col.mean(axis=0) * 1.25, 0, 1))
        up = nrm[:, 1]
        centre_xz = crown[[0, 2]]
        outward = (nrm[:, [0, 2]] * (pos[:, [0, 2]] - centre_xz)).sum(axis=1) >= -0.002
        band = np.exp(-((up - HIGHLIGHT_UP[0]) / HIGHLIGHT_UP[1]) ** 2) * outward
        band *= np.clip(0.55 + 0.45 * strand, 0.0, 1.0)  # broken by the strands
        new = new * (1 - HIGHLIGHT * band[:, None]) + hl[None] * (HIGHLIGHT * band[:, None]) * lmul[:, None].clip(0.6, 1.2)
    # 5. the hairline: hair texels next to skin lean toward the skin there, thinning out between strands
    skin = tm["skin"]
    soft = np.zeros(len(pos))
    # the hairline: not on the hair's own texture (fields given): the triangles at the hairline stay in the body's
    if skin.any() and fields is None:
        sg = Grid(reg["pos"], HAIRLINE_M / 2)
        ss, sn = sg.splat(reg["pos"][skin], reg["pos"][skin][:, :1] * 0 + 1.0, blur=0)
        cs, _ = sg.splat(reg["pos"][skin], base[reg["ys"][skin], reg["xs"][skin]], blur=0)
        alls, alln = sg.splat(reg["pos"], np.ones(len(reg["pos"])), blur=0)
        for _ in range(2):
            ss, sn, cs, alln = face._box3(ss, 1), face._box3(sn, 1), face._box3(cs, 1), face._box3(alln, 1)
        skin_share = sg.read(ss[..., 0] / np.maximum(alln, 1e-9), pos)
        skin_col = sg.read(cs / np.maximum(sn, 1e-9)[..., None], pos)
        fine = normalise(value_noise(pos, s_cell * 0.8, seed + 3) * 0.4 + strand * 0.6)
        soft = face.smoothstep(0.02, 0.5, skin_share) * face.smoothstep(-0.2, 0.9, -fine) * HAIRLINE_MIX
        new = new * (1 - soft[:, None]) + skin_col * soft[:, None]
    # the face keeps what the face pass gave it
    if protect is None:
        keep = np.ones(len(pos))
    else:
        # faded on the surface itself over about a centimetre, so the edge of what the face owns never shows as a
        # line along a triangle (a fringe flap half inside the face's region)
        raw = np.clip(protect[ys, xs], 0.0, 1.0)
        pg = Grid(pos, 0.004)
        ps, pn = pg.splat(pos, raw, blur=2)
        smooth = pg.read(ps[..., 0] / np.maximum(pn, 1e-9), pos)
        keep = 1.0 - np.maximum(raw, np.clip(1.5 * smooth, 0.0, 1.0))
    w = strength * keep
    nb_ = base.copy()
    nb_[ys, xs] = col + (new - col) * w[:, None]
    out_maps["base_color"] = nb_
    # normal map: the strand height's slope across the flow, in the texel's tangent frame
    if maps.get("normal") is not None:
        # the height's slope in the texture's own directions (what a baker writes): neighbouring hair texels only
        hgt = strand + 0.5 * clump
        Himg = np.zeros((T, T))
        V = np.zeros((T, T), dtype=bool)
        Himg[ys, xs] = hgt
        V[ys, xs] = True
        du = texture_slope(Himg, V, 1)[ys, xs]
        dv = -texture_slope(Himg, V, 0)[ys, xs]  # rows run down the image, v up
        su = du / np.maximum(np.linalg.norm(reg["tu"][hair][W], axis=1), 1e-9) * s_cell  # height change per strand width
        sv = dv / np.maximum(np.linalg.norm(reg["tv"][hair][W], axis=1), 1e-9) * s_cell
        slope = np.hypot(su, sv)
        nm = maps["normal"]
        old = decode_normal(nm[ys, xs])
        k = BUMP_TILT / max(float(np.median(slope)) if len(slope) else 0.0, 1e-6)  # the same relief at any texel density
        bent = old + np.stack([-k * su, -k * sv, np.zeros(len(su))], axis=1) * w[:, None]
        bent /= np.maximum(np.linalg.norm(bent, axis=1, keepdims=True), 1e-9)
        nn = nm.copy()
        nn[ys, xs] = encode_normal(bent)
        out_maps["normal"] = nn
        measures["bump_tilt_mean"] = round(float(np.abs(slope).mean() * k), 3)
    # roughness in a hair range, lower on the lighter strands; hair is not metal
    target = ROUGH["photoreal" if photoreal else "painted"]
    if maps.get("roughness") is not None:
        r = maps["roughness"]
        r2 = r if r.ndim == 2 else r[..., 0]
        rn = r2.copy()
        want = np.clip(target - ROUGH_SWING * np.clip(strand, -2, 2) * 0.5, 0.3, 0.75)
        rn[ys, xs] = r2[ys, xs] + (want - r2[ys, xs]) * strength
        out_maps["roughness"] = rn
        measures["roughness_before"] = round(float(r2[ys, xs].mean()), 3)
        measures["roughness_after"] = round(float(rn[ys, xs].mean()), 3)
    if maps.get("metallic") is not None:
        m = maps["metallic"]
        m2 = m if m.ndim == 2 else m[..., 0]
        mn = m2.copy()
        mn[ys, xs] = m2[ys, xs] * (1 - strength)
        out_maps["metallic"] = mn
        measures["metallic_before"] = round(float(m2[ys, xs].mean()), 3)
    # texels at a piece's edge whose centres fall outside every triangle are still read by texture filtering: they
    # take the painted neighbour's value, so no line of the old colour shows along the seams
    # (texels whose centre misses every triangle, and the gutter); the region's own other texels (skin, clothes) are
    # never overwritten
    blocked = reg["covered"] & ~written
    for k in list(out_maps):
        out_maps[k] = spill(out_maps[k], written, blocked)
    lum0 = face.luminance(col)
    lum1 = face.luminance(nb_[ys, xs])
    measures.update(painted=True, streak_amplitude=round(amp, 3), highlight_share=round(float((band > 0.3).mean()), 3),
                    hairline_texels=int((soft > 0.05).sum()), protected_texels=int((keep < 0.5).sum()),
                    lightness_spread_before=round(float(lum0.std() / max(lum0.mean(), 1e-3)), 4),
                    lightness_spread_after=round(float(lum1.std() / max(lum1.mean(), 1e-3)), 4),
                    tip_share=round(float((tip > 0.3).mean()), 3) if len(tip) else 0.0,
                    root_share=round(float((root > 0.3).mean()), 3) if len(root) else 0.0, texels_written=int(W.sum()))
    return {"maps": out_maps, "mask": mask, "measures": measures, "flow": flow, "pos": pos, "ys": ys, "xs": xs, "fields": out_fields}


STRAY_METAL_RING = 8  # triangles this many steps from the hair are looked at for hair-coloured metal
STRAY_METAL_MIN = 0.5
STRAY_METAL_LAB = 30.0


def clear_hair_metal(mesh: dict[str, np.ndarray], metallic: np.ndarray, base: np.ndarray, hair_tris: np.ndarray,
                     centres: np.ndarray, hair_i: int, skin_i: int | None = None) -> tuple[np.ndarray, int]:
    """The 3D step marks hair as metal (the knight's red hair was 0.83 to 0.97), and a lock of hair it modelled onto
    the forehead lies outside the hair the pass finds (on the face's own triangles), so it kept that metal and
    rendered as a dark blotch in Unity. Every texel on the head near the hair (STRAY_METAL_RING triangles) that is
    hair-coloured (or skin-coloured, with skin_i) and metal loses the metal; armour and other colours keep theirs.
    -> (the metallic map, texels changed)."""
    nb = triangle_neighbours(mesh["faces"], mesh["verts"])
    near = ring(hair_tris, nb, STRAY_METAL_RING)
    head = face.find_head(mesh["verts"])
    if head is not None:
        c = mesh["verts"][mesh["faces"]].mean(axis=1)[:, 1]
        near &= c >= head.top - 0.18 * head.height
    T = metallic.shape[0]
    reg = texel_region(mesh, T, near)
    m2 = metallic if metallic.ndim == 2 else metallic[..., 0]
    b = base if base.shape[0] == T else face.resize(base, T, Image.BOX)
    col = b[reg["ys"], reg["xs"]]
    lab = face.to_lab(np.clip(col, 0, 1))
    lbl, _ = nearest_centre(lab, centres)
    # hair colour, or close to it (the lock's own shadow side groups with the darker clothes)
    hairish = (lbl == hair_i) | (lab_dist(lab, centres[hair_i]) < STRAY_METAL_LAB)
    if skin_i is not None:
        hairish |= lbl == skin_i
    hit = hairish & (m2[reg["ys"], reg["xs"]] > STRAY_METAL_MIN)
    if not hit.any():
        return metallic, 0
    out = m2.copy()
    out[reg["ys"][hit], reg["xs"][hit]] = 0.0
    written = np.zeros((T, T), dtype=bool)
    written[reg["ys"][hit], reg["xs"][hit]] = True
    out = spill(out, written, reg["covered"] & ~written)
    return out, int(hit.sum())


def texel_size(mesh: dict[str, np.ndarray], reg: dict[str, Any], sel: np.ndarray, percentile: float | None = None) -> float:
    """Metres per texel on the selected texels: the surface area of the triangles they lie on over the texels those
    triangles have. With percentile, that percentile of the per-triangle sizes (weighted by area): the coarser
    texture pieces decide how fine a pattern can be."""
    tris, counts = np.unique(reg["tri"][sel], return_counts=True)
    allc = np.bincount(reg["tri"], minlength=int(reg["tri"].max()) + 1)[tris]
    v, f = mesh["verts"], mesh["faces"][tris]
    area = 0.5 * np.linalg.norm(np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]), axis=1)
    if percentile is None:
        a = float((area * counts / np.maximum(allc, 1)).sum())
        return math.sqrt(a / max(int(sel.sum()), 1))
    per = np.sqrt(area / np.maximum(allc, 1))
    order = np.argsort(per)
    cw = np.cumsum(area[order]) / max(float(area.sum()), 1e-12)
    return float(per[order][min(len(order) - 1, int(np.searchsorted(cw, percentile / 100.0)))])


def spill(img: np.ndarray, written: np.ndarray, covered: np.ndarray, px: int = 3) -> np.ndarray:
    """img with the texels next to written ones (within px) that are not in `covered` (texels that must keep their
    own value) set from the nearest written texel (ring by ring, so a nearer one wins)."""
    if not written.any():
        return img
    T = img.shape[0]
    reach = face.dilate_mask(written, px) & ~covered & ~written
    ys, xs = np.nonzero(reach)
    if len(ys) == 0:
        return img
    out = img.copy()
    done = np.zeros(len(ys), dtype=bool)
    for r in range(1, px + 1):
        offs = [(dy, dx) for dy in range(-r, r + 1) for dx in range(-r, r + 1) if max(abs(dy), abs(dx)) == r]
        offs.sort(key=lambda o: o[0] * o[0] + o[1] * o[1])
        for dy, dx in offs:
            ny, nx = np.clip(ys + dy, 0, T - 1), np.clip(xs + dx, 0, T - 1)
            take = ~done & written[ny, nx]
            if take.any():
                out[ys[take], xs[take]] = img[ny[take], nx[take]]
                done |= take
    return out


FACE_REGION_KEEP = 0.6  # hair lying inside the face's region (fringe locks) gets this much less of the paint


def face_protect(mesh: dict[str, np.ndarray], tex: np.ndarray) -> dict[str, Any]:
    """The texels the face pass owns outright, 0..1 (T,T): what the front camera sees of the eyes, brows, nose and
    mouth box inside the face (face.features_box), feathered. Also the face measures the hair pass must not change:
    {protect, info, measures}. protect is None when no face is found."""
    head = face.find_head(mesh["verts"])
    if head is None:
        return {"protect": None, "measures": {}}
    cam = face.head_camera(head, 0, 1024)
    view = face.render_view(mesh, tex, cam)
    info = face.find_face(view, mesh, cam, head)
    if not info.get("found"):
        return {"protect": None, "measures": {}}
    # the eyes-to-mouth box and the whole face region the face pass measures (its sharpness is taken there), feathered
    # outward over a few pixels so the hair's pattern fades in rather than stopping at a line
    box = face.features_box(info) & face.dilate_mask(info["region"], 8)
    feat = np.clip(face.blur(face.dilate_mask(box, 6).astype(np.float64), 4.0) * 1.5, 0.0, 1.0)
    reg = np.clip(face.blur(face.dilate_mask(info["region"], 4).astype(np.float64), 5.0) * 1.6, 0.0, 1.0)
    weight = np.maximum(feat, FACE_REGION_KEEP * reg)
    res = face.project_back(mesh, np.zeros_like(tex), cam, np.ones_like(view["image"]), weight, facing=(0.0, 0.05))
    return {"protect": np.clip(res["texel_weight"], 0.0, 1.0), "info": info, "cam": cam, "head": head,
            "measures": face_measures(view, info)}


def face_measures(view: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    """The face pass's own measures on a front render: sharpness in the face region and the eye points."""
    eyes = face.eye_points(view["image"], info)
    return {"sharpness": face.sharpness(view["image"], info["region"]),
            "eyes": None if not eyes else [[round(x, 1), round(y, 1)] for x, y in eyes]}


def normalise_like(v: np.ndarray) -> np.ndarray:
    """The strand pattern's scale for the bump (the same normalisation the colour uses: LIC output has a stable
    spread, so dividing by a fixed typical spread keeps the two offsets comparable)."""
    return v / LIC_SPREAD


LIC_SPREAD = 0.18  # typical standard deviation of lic() output with the defaults (measured), for the bump offsets


def glb_replace_normal(glb: Path | str, normal_png: Path | str) -> bool:
    """Swap, in place, the image of the first material's normal map in a GLB (the binary chunk is rebuilt view by
    view, as imaging.glb_replace_images does for the other maps). False when the GLB has no normal map image."""
    import json  # noqa: PLC0415
    import struct  # noqa: PLC0415

    p = Path(glb)
    data = p.read_bytes()
    if data[:4] != b"glTF":
        return False
    jlen = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20:20 + jlen])
    bin_off = 20 + jlen
    blen = struct.unpack_from("<I", data, bin_off)[0]
    bin_data = data[bin_off + 8: bin_off + 8 + blen]
    mats = gltf.get("materials") or []
    ref = (mats[0].get("normalTexture") if mats else None) or None
    if not ref:
        return False
    src = (gltf.get("textures") or [])[ref["index"]].get("source")
    images = gltf.get("images") or []
    if src is None or "bufferView" not in images[src]:
        return False
    views = gltf["bufferViews"]
    target = images[src]["bufferView"]
    images[src]["mimeType"] = "image/png"
    new_bin = bytearray()
    for vi, bv in enumerate(views):
        if bv.get("buffer", 0) != 0:
            continue
        o = bv.get("byteOffset", 0)
        chunk = Path(normal_png).read_bytes() if vi == target else bin_data[o:o + bv["byteLength"]]
        while len(new_bin) % 4:
            new_bin.append(0)
        bv["byteOffset"] = len(new_bin)
        bv["byteLength"] = len(chunk)
        new_bin += chunk
    while len(new_bin) % 4:
        new_bin.append(0)
    gltf["buffers"][0]["byteLength"] = len(new_bin)
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(new_bin)
    p.write_bytes(struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I", len(js)) + b"JSON" + js
                  + struct.pack("<I", len(new_bin)) + b"BIN\x00" + bytes(new_bin))
    return True


# --- pictures for the person ------------------------------------------------------------------------------------------

SHEET_VIEWS = (("From the front", 0.0, 0.0), ("Left side", 90.0, 0.0), ("Right side", -90.0, 0.0), ("From behind", 180.0, 0.0),
               ("From above", 0.0, 70.0))


def head_view_cam(head: face.Head, yaw: float, pitch: float, size: int) -> ViewCam:
    """The head with its hair, framed a little wider than face.head_camera."""
    scale = max(head.width * 2.0, 0.15 * head.height)
    target = head.centre + np.array([0.0, 0.0, 0.0])
    return ViewCam(target=target, yaw_deg=yaw, scale=scale, size=size, pitch_deg=pitch)


def render_lit(mesh: dict[str, np.ndarray], base: np.ndarray, normal: np.ndarray | None, rough: np.ndarray | None,
               cam: Any, light: np.ndarray, background: float = 0.25) -> np.ndarray:
    """A simple lit render (one light, Lambert plus a Blinn highlight) with the normal map in its tangent frame, to
    show the strand relief and the shine in the before and after picture."""
    ras = face.view_raster(mesh, cam)
    m = ras["tri"] >= 0
    T = base.shape[0]
    t = face.interpolate(face.uv_pixels(mesh["uv"], T), mesh["faces"], ras)[m]
    col = face.sample(base, t[:, 0], t[:, 1])
    n = face.interpolate(mesh["vnormals"], mesh["faces"], ras)[m]
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    if normal is not None:
        Tn = normal.shape[0]
        tn = t * (Tn / T)
        ts = decode_normal(face.sample(normal, tn[:, 0], tn[:, 1]))
        tu, tv = uv_jacobian(mesh, Tn)
        tri = ras["tri"][m]
        a = tangent(tu[tri], n)
        b = tv[tri] - (tv[tri] * n).sum(axis=1, keepdims=True) * n - (tv[tri] * a).sum(axis=1, keepdims=True) * a
        b /= np.maximum(np.linalg.norm(b, axis=1, keepdims=True), 1e-12)
        n = a * ts[:, :1] + b * ts[:, 1:2] + n * ts[:, 2:3]
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    r = 0.55 if rough is None else face.sample(rough[..., None] if rough.ndim == 2 else rough[..., :1], *(t * (rough.shape[0] / T)).T)[:, 0]
    L = light / np.linalg.norm(light)
    V = -cam.basis[2]
    Hh = (L + V) / np.linalg.norm(L + V)
    diff = np.clip(n @ L, 0.0, 1.0)
    shin = 2.0 / np.maximum(np.asarray(r) ** 4, 1e-3)
    spec = np.clip(n @ Hh, 0.0, 1.0) ** np.minimum(shin, 400.0) * (1.0 - np.asarray(r)) * 0.6
    img = np.full((cam.size, cam.size, 3), background)
    img[m] = np.clip(col * (0.35 + 0.8 * diff[:, None]) + spec[:, None], 0.0, 1.0)
    return img


def mask_sheet(mesh: dict[str, np.ndarray], base: np.ndarray, mask: np.ndarray, dest: Path, cell: int = 300) -> Path:
    """'What was taken as hair': the head from five sides with the hair tinted green."""
    from PIL import ImageDraw  # noqa: PLC0415

    head = face.find_head(mesh["verts"])
    tint = base.copy()
    tint[mask] = tint[mask] * 0.45 + np.array([0.15, 0.85, 0.2]) * 0.55
    sheet = Image.new("RGB", (cell * len(SHEET_VIEWS), cell + 24), (32, 32, 32))
    d = ImageDraw.Draw(sheet)
    for i, (label, yaw, pitch) in enumerate(SHEET_VIEWS):
        img = face.render_view(mesh, tint, head_view_cam(head, yaw, pitch, 512))["image"]
        sheet.paste(Image.fromarray(face.to_uint8(img)).resize((cell, cell), Image.LANCZOS), (i * cell, 24))
        d.text((i * cell + 8, 6), label, fill=(230, 230, 230))
    sheet.save(dest)
    return Path(dest)


def before_after_sheet(mesh: dict[str, np.ndarray], before: dict[str, Any], after: dict[str, Any], dest: Path,
                       cell: int = 300) -> Path:
    """'Hair: before and after': colour only from five sides (before, after), then lit with the normal map from the
    front-left and from behind (before, after), then a close-up of the fringe and the crown after."""
    from PIL import ImageDraw  # noqa: PLC0415

    head = face.find_head(mesh["verts"])
    rows: list[tuple[str, list[np.ndarray]]] = []
    for tag, maps in (("Colour before", before), ("Colour after", after)):
        rows.append((tag, [face.render_view(mesh, maps["base_color"], head_view_cam(head, y, p, 400))["image"] for _, y, p in SHEET_VIEWS]))
    lights = (np.array([0.6, 0.8, 0.9]), np.array([-0.8, 0.3, 0.4]))
    for tag, maps in (("Lit before", before), ("Lit after", after)):
        imgs = []
        for yaw, pitch in ((25.0, 10.0), (-35.0, 5.0), (180.0, 10.0)):
            for L in lights[:1] if yaw != -35.0 else lights[1:]:
                imgs.append(render_lit(mesh, maps["base_color"], maps.get("normal"), maps.get("roughness"),
                                       head_view_cam(head, yaw, pitch, 400), L))
        rows.append((tag, imgs))
    cols = len(SHEET_VIEWS)
    pad = 22
    sheet = Image.new("RGB", (cell * cols, (cell + pad) * len(rows)), (32, 32, 32))
    d = ImageDraw.Draw(sheet)
    for r, (tag, imgs) in enumerate(rows):
        y = r * (cell + pad)
        d.text((8, y + 4), tag, fill=(230, 230, 230))
        for c, img in enumerate(imgs):
            sheet.paste(Image.fromarray(face.to_uint8(img)).resize((cell, cell), Image.LANCZOS), (c * cell, y + pad))
    sheet.save(dest)
    return Path(dest)


# --- creatures: fur (a trial; see docs/HAIR-RESEARCH.md 5.2) -------------------------------------------------------------


def find_fur(glb: Path, base_color: Path, seed: int = 0, small_share: float = 0.04) -> dict[str, Any]:
    """The fur of a four-legged creature: the whole body minus the eyes, nose, mouth and claws. The body's colours
    are grouped (k-means in Lab, five groups); groups covering under small_share of the surface (dark eyes and nose,
    pale teeth and claws) are not fur. Returns {found, tris, centres, hair_i (the largest group), skin_i (-1: no
    skin), fur_groups}."""
    mesh = face.load_mesh(glb)
    tex = face.read_rgb(base_color)
    f = mesh["faces"]
    v = mesh["verts"]
    lab = triangle_colours(mesh, tex)
    area = 0.5 * np.linalg.norm(np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]), axis=1)
    rng = np.random.default_rng(seed)
    pick = rng.choice(len(f), size=min(len(f), 6000), replace=False, p=area / area.sum())
    order = np.argsort(lab[pick][:, 0])
    init = lab[pick][order[len(order) // 2]][None]
    C = kmeans(lab[pick], init, 5, seed)
    lbl, _ = nearest_centre(lab, C)
    share = np.array([area[lbl == j].sum() for j in range(len(C))]) / area.sum()
    fur_groups = [j for j in range(len(C)) if share[j] >= small_share]
    tris = np.isin(lbl, fur_groups)
    main = int(np.argmax(share))
    # every fur group is painted as fur: the centres are re-ordered so the hair index covers them all (texel_mask
    # asks for "not skin" on fur triangles)
    return {"found": bool(tris.sum() > 50), "tris": tris, "centres": C, "hair_i": main, "skin_i": -1,
            "fur_groups": [int(j) for j in fur_groups], "group_share": [round(float(s), 3) for s in share]}


def flow_nose_to_tail(pos: np.ndarray, nrm: np.ndarray, top: float | None = None, bottom: float | None = None,
                      belly: float = 0.55) -> np.ndarray:
    """Fur flow: from the nose (+Z, where prep turns the model to face) toward the tail, turning downward on the
    flanks and running straight down the legs (below `belly` of the height)."""
    y = pos[:, 1]
    top = float(y.max()) if top is None else top
    bottom = float(y.min()) if bottom is None else bottom
    h = max(top - bottom, 1e-6)
    rel = (y - bottom) / h
    legs = face.smoothstep(belly, belly - 0.15, rel)
    back = np.array([0.0, 0.0, -1.0])
    down = np.array([0.0, -1.0, 0.0])
    d = back[None] * (1 - legs)[:, None] + down[None] * (0.35 + 0.65 * legs)[:, None]
    return tangent(d, nrm)
