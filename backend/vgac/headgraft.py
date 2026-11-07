"""The head built on its own and put on the body (docs/HEAD-BUILD-PLAN.md). Pure CPU work (numpy, trimesh, PIL).

Why: the 3D builder decides a full figure's face from a few patches of the picture (its first grid is 32 cells; the
head is about four cells tall), so the face comes out smudged. Built from a head-and-shoulders crop, the face fills the
picture and comes out with real brows, eyes, nose, lips and ears, in shape and colour (bake-off knight, bald). With
hair, the same crop built as a flat board twice, so this runs on the bald route only: the body and the head are built
bald and the hair comes from the library afterwards.

The steps, each its own function so the 3D step can run them in order and fall back on any failure:
1. crop: the top CROP_SHARE of the bald figure's height, square, centred on the head, enlarged to 1024, with alpha.
2. depth_ratio: a build less than FLAT_RATIO as deep as it is wide is a flat board (a failed build).
3. graft: keystones from each model's side outline (top of the head, nose tip, chin bottom, neck), the head scaled so
   its top-to-chin equals the body's, the tops and nose tips met, centred across; then cut by colour, not distance (the
   bake-off failed three ways with distance rules): the head keeps its head and, below the chin, only skin; the body
   drops its skin above the cut and what lies strictly inside the new head above the chin, so a collar stays whole.
   The joined mesh is written with both textures on one square sheet twice the size (body top-left, head top-right),
   for blender/headjoin.py to pack into one texture of the usual size and bake again. The head's share of the packed
   texture is capped at HEAD_SHARE_MAX so the body keeps most of its texels.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from . import face

CATEGORIES = ("humanoid",)
CROP_SHARE = 0.26  # of the figure's height, from the top: head, neck and the top of the shoulders
CROP_RANGE = (0.18, 0.4)
CROP_SIZE = 1024
FLAT_RATIO = 0.1  # depth over width under this: a flat board
WIDTH_RANGE = (0.85, 1.15)  # the scaled head's width over the body head's (the knight: 0.97)
BODY_SPAN = 0.2  # side camera for a full figure: the top fifth of its height (head, neck, collar)
HEAD_SPAN = 0.7  # side camera for a head-and-shoulders build
STUB = 0.18  # how far the new neck runs below the cut, as a share of the top-to-chin height
JAW_KEEP = 0.03  # the new head keeps everything from this share of the top-to-chin height above the chin
SKIN_AB_TOL = 11.0  # skin: within this colour distance (Lab a and b) of the nose's colour...
SKIN_L_TOL = 30.0  # ...and this much lighter or darker (light and shade); pale skin and polished silver differ in colour
# far more than in lightness (the live knight: skin 65, 14, 17 against silver about 70, 0, 2)
HAIR_TOL = 18.0  # Lab distance from the crown's colour that still counts as hair (only when the crown is not skin)
NEAR = 0.1  # of the top-to-chin height: body surface this close outside the new head is the old head
COLOURFUL = 8.0  # Lab colourfulness (a, b) above which surface is not grey metal or cloth: old skin near the neck
KEY_AGREE = 0.12  # of the top-to-chin height: skin and whole-outline keystones further apart than this disagree
COLLAR_BINS = 24  # slices round the neck for the collar's edge
COLLAR_MIN_BINS = 0.6  # share of slices that must find collar surface; fewer: no collar, the colour rules alone
OVERLAP = 0.06  # of the top-to-chin height: the new neck runs this far down inside the collar
TIDY_SMALL = 60  # triangles: smaller loose pieces and holes the cut leaves are tidied away
HEAD_SHARE_MAX = 0.3  # the head's share of the packed texture area, at most
MIN_HEAD_FACES = 300
MIN_DROPPED = 30
HEAD_FACES_SHARE = 0.5  # the head build's triangle target over the body's: the joined model stays near the target

REASONS = {
    "no_picture": "the bald picture has no figure to crop",
    "flat": "it came out flat",
    "no_keys": "its chin or neck could not be found",
    "size": "it was a different size from the body's head",
    "nothing_kept": "too little of it was left after the cut",
    "nothing_dropped": "the body's own head could not be found to replace",
    "join": "joining it to the body failed",
    "error": "building it failed",
}


def wanted(category: str | None, params: dict[str, Any], bald_route: bool, engine: str) -> bool:
    """Whether the 3D step builds the head on its own: TRELLIS, the bald route (hair from the library), a person, and
    the version has not turned it off (head_build false)."""
    return (engine == "trellis" and bald_route and (category or "") in CATEGORIES
            and params.get("head_build") is not False)


def crop_share(params: dict[str, Any]) -> float:
    v = params.get("head_crop")
    try:
        v = float(v) if v not in (None, "") else CROP_SHARE
    except (TypeError, ValueError):
        v = CROP_SHARE
    return float(min(CROP_RANGE[1], max(CROP_RANGE[0], v)))


def crop(cutout: Path | str, dest: Path | str, share: float = CROP_SHARE, size: int = CROP_SIZE) -> dict[str, Any]:
    """The head-and-shoulders crop of an RGBA cut-out: from the figure's top down `share` of its height, square,
    centred across on the top of the figure (the head), enlarged to `size`, background fully clear. ValueError when the
    picture has no figure."""
    im = Image.open(cutout).convert("RGBA")
    a = np.asarray(im)
    alpha = a[..., 3] > 127
    rows = np.where(alpha.any(axis=1))[0]
    if len(rows) < 20:
        raise ValueError("no figure in the picture")
    top, bot = int(rows[0]), int(rows[-1])
    h = max(8, int(share * (bot - top)))
    band = alpha[top: top + max(2, int(0.12 * (bot - top)))]
    xs = np.where(band.any(axis=0))[0]
    cx = int((xs[0] + xs[-1]) / 2)
    side = int(h * 1.1)
    x0, y0 = cx - side // 2, top - int(0.05 * h)
    canvas = np.zeros((side, side, 4), dtype=np.uint8)
    sx0, sy0 = max(0, x0), max(0, y0)
    sub = a[sy0: y0 + side, sx0: x0 + side]
    canvas[sy0 - y0: sy0 - y0 + sub.shape[0], sx0 - x0: sx0 - x0 + sub.shape[1]] = sub
    canvas[canvas[..., 3] <= 127] = 0  # clear background: the builder builds whatever is not clear
    out = Image.fromarray(canvas, "RGBA").resize((size, size), Image.LANCZOS)
    o = np.asarray(out).copy()
    o[o[..., 3] <= 127] = 0
    Image.fromarray(o, "RGBA").save(dest)
    return {"box": [int(x0), int(y0), int(x0 + side), int(y0 + side)], "figure_px": int(bot - top), "share": round(float(share), 3)}


def depth_ratio(glb: Path | str) -> float | None:
    """The model's depth (front to back) over its width; None without a mesh."""
    mesh = face.load_mesh(glb)
    if mesh is None:
        return None
    ext = mesh["verts"].max(axis=0) - mesh["verts"].min(axis=0)
    return round(float(ext[2] / max(ext[0], 1e-9)), 4)


# --- the side outline -------------------------------------------------------------------------------------------
def _smooth(a: np.ndarray, r: int) -> np.ndarray:
    k = np.ones(2 * r + 1) / (2 * r + 1)
    return np.convolve(np.pad(a, r, mode="edge"), k, mode="valid")


def _outline(mask: np.ndarray, faces_left: bool) -> np.ndarray:
    """Per row, how far forward the outline reaches, growing forward (nan where empty)."""
    H, W = mask.shape
    out = np.full(H, np.nan)
    for y in range(H):
        xs = np.where(mask[y])[0]
        if len(xs):
            out[y] = (W - xs.min()) if faces_left else xs.max()
    return out


def profile_keys(mask: np.ndarray, faces_left: bool, top_row: int | None = None) -> dict[str, Any]:
    """Keystone rows on a side silhouette of a head with the face to one side. `mask` may be the face's skin only (hair
    reaching further forward than the nose would be read as the face); `top_row` is then the whole outline's top.
    Returns {top, tip, subnasale, chin_front, chin_bottom, neck} (None where not found)."""
    f = _outline(mask, faces_left)
    rows = np.where(np.isfinite(f))[0]
    if len(rows) < 20:
        return {"top": top_row, "tip": None, "subnasale": None, "chin_front": None, "chin_bottom": None, "neck": None}
    top = int(rows[0]) if top_row is None else int(top_row)
    g = _smooth(np.nan_to_num(f, nan=0.0), 3)
    a, b = int(rows[0]) + int(0.2 * (rows[-1] - rows[0])), int(rows[0]) + int(0.6 * (rows[-1] - rows[0]))
    if b <= a + 2:
        return {"top": top, "tip": None, "subnasale": None, "chin_front": None, "chin_bottom": None, "neck": None}
    tip = a + int(np.argmax(g[a:b]))
    D = max(4, tip - top)
    end = min(len(g), tip + int(0.8 * D))
    neck = tip + int(np.argmin(g[tip:end])) if end > tip + 2 else None
    sub = None
    if neck is not None:
        for i in range(tip + 2, neck):
            if g[i] <= g[i - 1] and g[i] < g[i + 1] and g[tip] - g[i] > 0.01 * mask.shape[1]:
                sub = i
                break
    chin_front = chin_bottom = None
    if neck is not None:
        mid = (sub or tip) + (neck - (sub or tip)) // 2
        if neck > mid + 2:
            chin_front = mid + int(np.argmax(g[mid:neck]))
            thr = g[chin_front] - 0.6 * (g[chin_front] - g[neck])
            w = np.where(g[chin_front: neck + 1] <= thr)[0]
            chin_bottom = int(chin_front + w[0]) if len(w) else None
    return {"top": top, "tip": tip, "subnasale": sub, "chin_front": chin_front, "chin_bottom": chin_bottom, "neck": neck}


# --- colours -----------------------------------------------------------------------------------------------------
def face_lab(mesh: dict[str, np.ndarray], tex: np.ndarray, faces: np.ndarray | None = None) -> np.ndarray:
    """Each triangle's texture colour at its middle, in Lab (UVs v up, as face.load_mesh gives them)."""
    f = mesh["faces"] if faces is None else faces
    uv = mesh["uv"][f].mean(axis=1)
    T = tex.shape[0]
    x = np.clip((uv[:, 0] * T).astype(int), 0, T - 1)
    y = np.clip(((1 - uv[:, 1]) * T).astype(int), 0, T - 1)
    return face.to_lab(tex[y, x][:, None, :])[:, 0, :]


def _nose(view: dict[str, Any], mesh: dict[str, np.ndarray], cam: face.Camera, head: face.Head) -> tuple[int, int]:
    pos = face.interpolate(mesh["verts"], mesh["faces"], view["raster"])
    nz = face.find_nose(view["raster"]["z"], view["mask"], pos, cam, head)
    if nz is not None:
        return int(nz[0]), int(nz[1])
    S = cam.size
    z = np.where(view["mask"], view["raster"]["z"], np.inf)
    sub = z[int(0.3 * S): int(0.73 * S), int(0.34 * S): int(0.66 * S)]
    iy, ix = np.unravel_index(int(np.argmin(sub)), sub.shape)
    return int(ix + 0.34 * S), int(iy + 0.3 * S)


def colour_refs(mesh: dict[str, np.ndarray], tex: np.ndarray, head_share: float) -> np.ndarray:
    """[skin, crown] in Lab: the nose (always skin; a cheek can sit in a fringe's shadow) and the top of the head (hair,
    or skin on a bald head), on a front render of the head."""
    head = face.find_head(mesh["verts"], head_share)
    cam = face.head_camera(head, 0, 1024)
    view = face.render_view(mesh, tex, cam)
    nx, ny = _nose(view, mesh, cam, head)
    lab = face.to_lab(view["image"])
    win = lab[max(0, ny - 30): ny + 4, max(0, nx - 8): nx + 9]
    ok = view["mask"][max(0, ny - 30): ny + 4, max(0, nx - 8): nx + 9]
    skin = np.median(win[ok] if ok.any() else win.reshape(-1, 3), axis=0)
    rows = np.where(view["mask"][:, nx])[0]
    crown = skin
    if len(rows):
        r0 = rows[0]
        sl = (slice(r0 + 8, r0 + 40), slice(max(0, nx - 30), nx + 30))
        m = view["mask"][sl]
        if m.any():
            crown = np.median(lab[sl][m], axis=0)
    return np.stack([skin, crown])


def is_skin(lab: np.ndarray, skin: np.ndarray) -> np.ndarray:
    """Skin: close to the nose's colour in hue and saturation (Lab a and b), with any lightness from shade to light."""
    return (np.abs(lab[:, 0] - skin[0]) < SKIN_L_TOL) & (np.linalg.norm(lab[:, 1:] - skin[1:], axis=1) < SKIN_AB_TOL)


def is_head_colour(lab: np.ndarray, refs: np.ndarray) -> np.ndarray:
    """Skin, or hair: skin (is_skin), or near the crown's colour when the crown is clearly not skin (lightness in full:
    dark hair and grey armour differ mostly in lightness)."""
    skin, crown = refs
    out = is_skin(lab, skin)
    if np.linalg.norm(crown - skin) > 15.0:
        out |= np.linalg.norm(lab - crown, axis=1) < HAIR_TOL
    return out


def side_keys(mesh: dict[str, np.ndarray], tex: np.ndarray, head_share: float, span_share: float) -> dict[str, Any]:
    """Keystone heights in model units from a side render: top, tip, chin_bottom, neck, and tip_z (the nose tip's
    forward position), centre_x and head_width. The face's outline is read from skin pixels only."""
    head = face.find_head(mesh["verts"], head_share)
    span = span_share * head.height * 1.1
    tgt = np.array([head.centre[0], head.top - 0.5 * span + 0.04 * span, head.centre[2]])
    cam = face.Camera(target=tgt, yaw_deg=90, scale=span, size=1024)
    view = face.render_view(mesh, tex, cam)
    p, _ = cam.project(np.array([[head.centre[0], head.centre[1], head.centre[2] + 0.1]]))
    refs = colour_refs(mesh, tex, head_share)
    lab = face.to_lab(view["image"]).reshape(-1, 3)
    skin_px = is_skin(lab, refs[0]).reshape(view["mask"].shape)
    skin_px = face.fill_holes(skin_px & view["mask"])
    rows = np.where(view["mask"].any(axis=1))[0]
    top = int(rows[0]) if len(rows) else None
    faces_left = bool(p[0, 0] < 512)
    k = profile_keys(skin_px, faces_left, top_row=top)
    # the whole outline as a cross-check: a face whose skin is not one colour (a red nose, pale cheeks) can break the
    # skin outline, and its nose tip is then found at the brow (live knight, 2026-11-06); when the skin keystones
    # disagree with the whole outline's by more than KEY_AGREE of its top-to-chin height, the whole outline is used
    full = profile_keys(view["mask"], faces_left, top_row=top)
    if full.get("tip") is not None and full.get("chin_bottom") is not None and top is not None:
        tol = KEY_AGREE * (full["chin_bottom"] - top)
        bad = any(k.get(n) is None or abs(k[n] - full[n]) > tol for n in ("tip", "chin_bottom"))
        if bad:
            k = {**full, "source": "outline"}
        else:
            k = {**k, "source": "skin"}

    def y(r: int | None) -> float | None:
        return None if r is None else float(cam.target[1] + (512 - r) * span / 1024)

    out: dict[str, Any] = {n: y(k.get(n)) for n in ("top", "tip", "chin_bottom", "neck")}
    out["rows"] = k
    out["centre_x"] = float(head.centre[0])
    out["centre_z"] = float(head.centre[2])
    out["head_width"] = float(head.width)
    out["refs"] = refs
    if out["tip"] is not None:
        v = mesh["verts"]
        near = (np.abs(v[:, 1] - out["tip"]) < 0.02 * span) & (np.abs(v[:, 0] - head.centre[0]) < 0.25 * head.width)
        out["tip_z"] = float(v[near, 2].max()) if near.any() else None
        # the head's width where both models agree on what it is: across the head at the nose tip's height (cheeks and
        # ears); find_head's width is the top share of the model's height, which on a bust takes in the shoulders
        if out["top"] is not None:
            band = v[np.abs(v[:, 1] - out["tip"]) < 0.05 * (out["top"] - out["tip"])]
            if len(band) > 20:
                x0, x1 = np.percentile(band[:, 0], [2, 98])
                out["head_width"] = float(x1 - x0)
    else:
        out["tip_z"] = None
    return out


# --- which way a head faces ---------------------------------------------------------------------------------------
def _turned(verts: np.ndarray, yaw_deg: float, centre: np.ndarray) -> np.ndarray:
    """The vertices turned about the vertical axis through `centre` by yaw_deg (x toward z positive)."""
    a = np.radians(yaw_deg)
    c, s = np.cos(a), np.sin(a)
    d = verts - centre
    out = verts.copy()
    out[:, 0] = centre[0] + c * d[:, 0] + s * d[:, 2]
    out[:, 2] = centre[2] - s * d[:, 0] + c * d[:, 2]
    return out


def facing_yaw(mesh: dict[str, np.ndarray], head_share: float) -> float:
    """The turn (degrees) that makes the head face the front camera (+Z). A model built from a three-quarter picture
    comes out turned about 40 degrees. First guess from the shoulders (the figure is wider across than deep; the face
    points across that axis, on the side toward the camera, where the builder puts the picture's front), then the
    turn within 8 degrees of it under which the head's depth seen from the front is most nearly mirror-symmetric with
    the nose tip in the middle. A symmetry search alone was fooled: a bald head seen from the side is nearly
    symmetric front to back too."""
    head = face.find_head(mesh["verts"], head_share)
    v = mesh["verts"]
    fc = v[mesh["faces"]].mean(axis=1)
    sel = mesh["faces"][fc[:, 1] > head.top - 0.8 * head_share * head.height]
    size = 128
    cam = face.head_camera(head, 0, size)
    cx = float(cam.project(head.centre[None, :])[0][0, 0])

    def score(yaw: float) -> float:
        m = {"verts": _turned(v, yaw, head.centre), "faces": sel}
        z = face.view_raster(m, cam)["z"]
        ok = np.isfinite(z)
        cols = np.arange(size)
        mirror = np.clip(np.round(2 * cx - cols - 1).astype(int), 0, size - 1)
        zm, okm = z[:, mirror], ok[:, mirror]
        both = ok & okm
        if both.sum() < 20:
            return 1e9
        union = (ok | okm).sum()
        diff = float(np.abs(z[both] - zm[both]).mean() / max(head.width, 1e-9))
        # the nearest point (the nose tip) sits in the middle from the front, at the edge from the side (a bald head's
        # outline is nearly symmetric front to back as well)
        iy, ix = np.unravel_index(int(np.argmin(np.where(ok, z, np.inf))), z.shape)
        off_centre = abs(ix + 0.5 - cx) / (0.5 * size)
        return diff + 0.5 * (1.0 - both.sum() / max(union, 1)) + 2.0 * off_centre

    # first guess from the shoulders: below the head the figure is wider across than deep, so the main axis of its
    # outline from above runs left to right and the face points across it, on the side nearer the camera (+Z)
    below = v[v[:, 1] < head.top - head_share * head.height]
    guess = 0.0
    if len(below) > 50:
        d = below[:, [0, 2]] - below[:, [0, 2]].mean(axis=0)
        w, vec = np.linalg.eigh(d.T @ d)
        lr = vec[:, int(np.argmax(w))]  # (x, z) left to right
        fwd = np.array([-lr[1], lr[0]])
        if fwd[1] < 0:
            fwd = -fwd
        if w.max() > 1.3 * w.min():  # a clear left-right axis
            guess = -float(np.degrees(np.arctan2(fwd[0], fwd[1])))
    return float(min(np.arange(round(guess) - 8, round(guess) + 9, 1), key=score))


# --- the graft ---------------------------------------------------------------------------------------------------
def _uv_area(uv: np.ndarray, faces: np.ndarray) -> float:
    a, b, c = uv[faces[:, 0]], uv[faces[:, 1]], uv[faces[:, 2]]
    return float(np.abs((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1])).sum() / 2)


def _against_new_head(points: np.ndarray, head_verts: np.ndarray, head_normals: np.ndarray,
                      chunk: int = 512) -> tuple[np.ndarray, np.ndarray]:
    """For each point, from its nearest vertex of the new head: the distance, and the signed offset along that vertex's
    normal (negative: inside the new head)."""
    dist = np.empty(len(points))
    off = np.empty(len(points))
    for i in range(0, len(points), chunk):
        p = points[i: i + chunk]
        d2 = ((p[:, None, :] - head_verts[None, :, :]) ** 2).sum(axis=2)
        j = np.argmin(d2, axis=1)
        dist[i: i + chunk] = np.sqrt(d2[np.arange(len(p)), j])
        off[i: i + chunk] = ((p - head_verts[j]) * head_normals[j]).sum(axis=1)
    return dist, off


def _bin_of(x: np.ndarray, z: np.ndarray, axis: tuple[float, float]) -> np.ndarray:
    a = np.arctan2(z - axis[1], x - axis[0])
    return ((a + np.pi) / (2 * np.pi) * COLLAR_BINS).astype(int) % COLLAR_BINS


def _fill_round(v: np.ndarray) -> np.ndarray:
    """Empty slices (nan) filled from their neighbours round the circle."""
    ok = np.isfinite(v)
    if ok.all() or not ok.any():
        return v
    i = np.arange(len(v))
    xi = np.concatenate([i[ok] - len(v), i[ok], i[ok] + len(v)])
    yi = np.concatenate([v[ok]] * 3)
    return np.interp(i, xi, yi)


def collar_edge(centres: np.ndarray, grey: np.ndarray, axis: tuple[float, float], chin: float, tip: float, H: float,
                head_width: float) -> dict[str, np.ndarray] | None:
    """The body's collar round the neck: per slice round the neck's axis, the height of its top edge and its inner
    radius (the opening the neck goes through). From grey (not skin, not colourful) body surface near the neck,
    between well below the chin and the nose tip. None when too few slices find any (an open neck)."""
    r = np.hypot(centres[:, 0] - axis[0], centres[:, 2] - axis[1])
    sel = grey & (r > 0.12 * head_width) & (r < 0.8 * head_width) & (centres[:, 1] > chin - 0.7 * H) & (centres[:, 1] < tip)
    if sel.sum() < 50:
        return None
    c, rr = centres[sel], r[sel]
    b = _bin_of(c[:, 0], c[:, 2], axis)
    top = np.full(COLLAR_BINS, np.nan)
    inner = np.full(COLLAR_BINS, np.nan)
    for k in range(COLLAR_BINS):
        m = b == k
        if m.sum() < 4:
            continue
        top[k] = np.percentile(c[m, 1], 95)
        near_top = m & (c[:, 1] > top[k] - 0.1 * H)
        inner[k] = np.percentile(rr[near_top], 10) if near_top.sum() >= 2 else np.nan
    if np.isfinite(top).mean() < COLLAR_MIN_BINS:
        return None
    return {"top": _fill_round(top), "inner": _fill_round(inner)}


def _face_pairs(verts: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """Pairs of triangles sharing an edge (vertices welded by position: glTF splits them at texture seams)."""
    _, inv = np.unique(np.round(verts, 6), axis=0, return_inverse=True)
    f = inv.reshape(-1)[faces]
    e = np.sort(np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]), axis=1)
    owner = np.tile(np.arange(len(f)), 3)
    order = np.lexsort((e[:, 1], e[:, 0]))
    e, owner = e[order], owner[order]
    same = (e[1:] == e[:-1]).all(axis=1)
    return np.stack([owner[:-1][same], owner[1:][same]], axis=1)


def _components(n: int, pairs: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Connected component label per triangle among those in `mask` (-1 outside it)."""
    parent = np.arange(n)
    pr = pairs[mask[pairs[:, 0]] & mask[pairs[:, 1]]]

    def find(x: np.ndarray) -> np.ndarray:
        while True:
            px = parent[x]
            if (px == x).all():
                return x
            parent[x] = parent[px]
            x = parent[x]

    while len(pr):
        a, b = find(pr[:, 0]), find(pr[:, 1])
        diff = a != b
        if not diff.any():
            break
        lo, hi = np.minimum(a[diff], b[diff]), np.maximum(a[diff], b[diff])
        parent[hi] = lo
        pr = pr[diff]
    lab = find(np.arange(n))
    return np.where(mask, lab, -1)


def tidy(verts: np.ndarray, faces: np.ndarray, keep: np.ndarray, only_largest: bool, region: np.ndarray | None = None,
         small: int = TIDY_SMALL) -> np.ndarray:
    """A cleaner cut: kept fragments cut loose are dropped (all but the largest piece, or pieces under `small` that
    the cut made), and small dropped holes inside what is kept are filled back. `region`: where the cut happened
    (fragments and holes are only looked for there). So each part ends with one clean opening to stitch."""
    keep = keep.copy()
    region = np.ones(len(faces), dtype=bool) if region is None else region
    pairs = _face_pairs(verts, faces)
    for _ in range(2):
        lab = _components(len(faces), pairs, keep)
        ids, counts = np.unique(lab[lab >= 0], return_counts=True)
        if len(ids):
            biggest = ids[np.argmax(counts)]
            for i, c in zip(ids, counts):
                if i == biggest:
                    continue
                m = lab == i
                if only_largest or (c < small and (m & region).any()):
                    keep[m] = False
        hole = ~keep & region
        lab = _components(len(faces), pairs, hole)
        ids, counts = np.unique(lab[lab >= 0], return_counts=True)
        for i, c in zip(ids, counts):
            if c < small:
                keep[lab == i] = True
    return keep


def _sheet(body: np.ndarray | None, head: np.ndarray | None, f: float, neutral: tuple[float, float, float]) -> np.ndarray:
    """One square sheet twice the size: body top-left, head top-right shrunk by f into its corner, the rest neutral."""
    ref = body if body is not None else head
    T = ref.shape[0]
    out = np.empty((2 * T, 2 * T, 3))
    out[:] = neutral
    if body is not None:
        out[:T, :T] = face.resize(body, T) if body.shape[0] != T else body
    if head is not None:
        n = max(1, int(round(f * T)))
        out[:n, T: T + n] = face.resize(head, n, Image.LANCZOS)
    return out


NEUTRAL = {"base_color": (0.5, 0.5, 0.5), "metallic": (0.0, 0.0, 0.0), "roughness": (0.5, 0.5, 0.5),
           "normal": (0.5, 0.5, 1.0), "ao": (1.0, 1.0, 1.0)}


def graft(body_glb: Path, body_maps: dict[str, Path], head_glb: Path, head_maps: dict[str, Path], out_dir: Path) -> dict[str, Any]:
    """The head model put on the body model. Writes <out_dir>/graft.glb (one mesh, UVs on the double sheet) and
    <out_dir>/graft_<key>.png (the sheets). Returns {ok, reason (a REASONS key or None), glb, maps, metrics}."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    body = face.load_mesh(body_glb)
    head = face.load_mesh(head_glb)
    if body is None or head is None:
        return {"ok": False, "reason": "join", "metrics": {"error": "a model has no texture layout"}}
    # both turned to face the front (a three-quarter picture builds a turned model); the result is turned back by the
    # body's own turn, so the body ends where it was built
    body_yaw, head_yaw = facing_yaw(body, 0.13), facing_yaw(head, 0.45)
    body_centre = face.find_head(body["verts"], 0.13).centre
    body = {**body, "verts": _turned(body["verts"], body_yaw, body_centre)}
    head = {**head, "verts": _turned(head["verts"], head_yaw, face.find_head(head["verts"], 0.45).centre)}
    body["vnormals"] = face.smooth_normals(body["verts"], body["faces"])
    head["vnormals"] = face.smooth_normals(head["verts"], head["faces"])
    btex = face.read_rgb(body_maps["base_color"])
    htex = face.read_rgb(head_maps["base_color"])
    kb = side_keys(body, btex, 0.13, BODY_SPAN)
    kh = side_keys(head, htex, 0.45, HEAD_SPAN)
    need = ("top", "tip", "chin_bottom", "neck", "tip_z")
    metrics: dict[str, Any] = {"body_rows": kb["rows"], "head_rows": kh["rows"], "body_yaw": body_yaw, "head_yaw": head_yaw}
    if any(kb.get(n) is None for n in need) or any(kh.get(n) is None for n in ("top", "tip", "chin_bottom", "tip_z")):
        return {"ok": False, "reason": "no_keys", "metrics": metrics}
    H = kb["top"] - kb["chin_bottom"]
    s = H / max(kh["top"] - kh["chin_bottom"], 1e-9)
    t = np.array([kb["centre_x"] - kh["centre_x"] * s, kb["top"] - kh["top"] * s, kb["tip_z"] - kh["tip_z"] * s])
    hv = head["verts"] * s + t
    width_ratio = (kh["head_width"] * s) / max(kb["head_width"], 1e-9)
    metrics.update(scale=round(float(s), 5), move=[round(float(x), 5) for x in t], width_ratio=round(float(width_ratio), 4))
    if not (WIDTH_RANGE[0] <= width_ratio <= WIDTH_RANGE[1]):
        return {"ok": False, "reason": "size", "metrics": metrics}

    cut = kb["neck"]
    stub = STUB * H
    # the new head: its head, and below it only skin and hair (the neck, hair hanging down), down to the stub's end
    hc = hv[head["faces"]].mean(axis=1)
    lab_h = face_lab(head, htex)
    h_head = is_head_colour(lab_h, kh["refs"])
    # below the nose tip, grey surface that is not skin is the head build's own collar (it rides up at the back of the
    # neck); lips and shaded skin are colourful and stay
    h_grey = ~h_head & (np.linalg.norm(lab_h[:, 1:], axis=1) <= COLOURFUL) & (hc[:, 1] < kb["tip"])
    keep_h = ((hc[:, 1] > kb["chin_bottom"] + JAW_KEEP * H) & ~h_grey) | ((hc[:, 1] > cut - stub) & h_head)
    # the body: drops its old head, hair and neck: head colours above the stub's end; above the chin anything inside
    # the new head or within NEAR of its outside (the old nose, lips and ears stick out past the new ones, and the old
    # face is not one colour: the live knight's lips and cheeks are redder than the nose the skin colour is taken
    # from); below the chin, near and colourful (old skin) but never grey (a collar hugging the new neck stays)
    # the collar's edge (when there is a collar): the new neck runs down to just inside it, nothing of it below the
    # edge outside the opening (it would poke through the collar), and the body loses everything inside the opening
    band = h_head & (hc[:, 1] < kb["chin_bottom"]) & (hc[:, 1] > kb["chin_bottom"] - 0.3 * H)
    axis = (float(np.median(hc[band, 0])), float(np.median(hc[band, 2]))) if band.sum() > 10 else         (float(kb["centre_x"]), float(kb["centre_z"]))
    bc = body["verts"][body["faces"]].mean(axis=1)
    lab_all = face_lab(body, btex)
    b_head = is_head_colour(lab_all, kb["refs"])
    b_grey = ~b_head & (np.linalg.norm(lab_all[:, 1:], axis=1) <= COLOURFUL)
    edge = collar_edge(bc, b_grey, axis, kb["chin_bottom"], kb["tip"], H, kb["head_width"])
    if edge is not None:
        kh_bin = _bin_of(hc[:, 0], hc[:, 2], axis)
        h_r = np.hypot(hc[:, 0] - axis[0], hc[:, 2] - axis[1])
        h_top, h_inner = edge["top"][kh_bin], edge["inner"][kh_bin]
        low = hc[:, 1] < h_top
        keep_h = ~h_grey & (hc[:, 1] > np.minimum(kb["chin_bottom"] + JAW_KEEP * H, h_top - OVERLAP * H))
        keep_h &= ~(low & (h_r > h_inner))
        metrics.update(collar_top=[round(float(x), 4) for x in edge["top"]], collar_inner=[round(float(x), 4) for x in edge["inner"]])
    keep_h = tidy(hv, head["faces"], keep_h, only_largest=True)
    hk = head["faces"][keep_h]
    hvn = face.smooth_normals(hv, hk)
    used = np.unique(hk)
    cand = np.where(bc[:, 1] > cut - stub)[0]
    dist, off = _against_new_head(bc[cand], hv[used], hvn[used])
    above_chin = bc[cand, 1] > kb["chin_bottom"]
    inside = (off < 0.0) & (dist < 0.6 * kb["head_width"])
    near = dist < NEAR * H
    lab_b = face_lab(body, btex, body["faces"][cand])
    colourful = np.linalg.norm(lab_b[:, 1:], axis=1) > COLOURFUL
    drop_b = np.zeros(len(bc), dtype=bool)
    drop_b[cand] = b_head[cand] | (above_chin & (inside | near)) | (near & colourful)
    if edge is not None:
        bb = _bin_of(bc[:, 0], bc[:, 2], axis)
        b_r = np.hypot(bc[:, 0] - axis[0], bc[:, 2] - axis[1])
        b_top, b_inner = edge["top"][bb], edge["inner"][bb]
        # inside the opening: old neck skin anywhere in it; grey (the collar's own lining) only well inside
        in_opening = ((b_r < b_inner) & ~b_grey) | (b_r < 0.8 * b_inner)
        drop_b |= in_opening & (bc[:, 1] > b_top - 0.3 * H)
        # the collar itself stays whole: at or outside its opening and not above its edge, whatever its colour (its
        # rim can be painted skin-coloured, and the colour rules would tear it)
        drop_b &= ~((b_r >= 0.9 * b_inner) & (bc[:, 1] <= b_top + 0.02 * H))
        # old neck stubs standing just above the edge, below the chin (the new neck is there)
        drop_b |= (b_r < 1.2 * b_inner) & (bc[:, 1] > b_top + 0.02 * H) & (bc[:, 1] < kb["chin_bottom"])
    near_cut = bc[:, 1] > cut - stub - 0.1 * H
    drop_b = ~tidy(body["verts"], body["faces"], ~drop_b, only_largest=False, region=near_cut)
    metrics.update(cut_y=round(float(cut), 5), body_faces=int(len(bc)), body_faces_dropped=int(drop_b.sum()),
                   head_faces=int(len(hc)), head_faces_kept=int(keep_h.sum()))
    if keep_h.sum() < MIN_HEAD_FACES:
        return {"ok": False, "reason": "nothing_kept", "metrics": metrics}
    if drop_b.sum() < MIN_DROPPED:
        return {"ok": False, "reason": "nothing_dropped", "metrics": metrics}

    # one mesh: the kept faces of both, unused vertices dropped, UVs on the double sheet
    bf = body["faces"][~drop_b]
    b_used = np.unique(bf)
    h_used = np.unique(hk)
    b_area = _uv_area(body["uv"], bf)
    h_area = _uv_area(head["uv"], hk)
    f = 1.0
    if h_area > 0 and b_area > 0:
        share = HEAD_SHARE_MAX
        f = float(min(1.0, np.sqrt(share * b_area / ((1 - share) * h_area))))
    b_map = -np.ones(len(body["verts"]), dtype=np.int64)
    b_map[b_used] = np.arange(len(b_used))
    h_map = -np.ones(len(head["verts"]), dtype=np.int64)
    h_map[h_used] = np.arange(len(h_used)) + len(b_used)
    verts = _turned(np.concatenate([body["verts"][b_used], hv[h_used]]), -body_yaw, body_centre)
    buv = body["uv"][b_used]
    huv = head["uv"][h_used]
    uv = np.concatenate([np.stack([buv[:, 0] * 0.5, 0.5 + buv[:, 1] * 0.5], axis=1),
                         np.stack([0.5 + f * huv[:, 0] * 0.5, 1.0 - f * (1.0 - huv[:, 1]) * 0.5], axis=1)])
    faces = np.concatenate([b_map[bf], h_map[hk]])

    import trimesh

    tm = trimesh.Trimesh(vertices=verts, faces=faces, process=False,
                         visual=trimesh.visual.TextureVisuals(uv=uv))
    glb = out_dir / "graft.glb"
    tm.export(str(glb))
    maps: dict[str, Path] = {}
    for key, neutral in NEUTRAL.items():
        bp, hp = body_maps.get(key), head_maps.get(key)
        if bp is None and hp is None:
            continue
        b_img = face.read_rgb(bp) if bp is not None and Path(bp).is_file() else None
        h_img = face.read_rgb(hp) if hp is not None and Path(hp).is_file() else None
        if b_img is None and h_img is None:
            continue
        if b_img is not None and h_img is not None and h_img.shape[0] != b_img.shape[0]:
            h_img = face.resize(h_img, b_img.shape[0], Image.LANCZOS)
        maps[key] = face.save_rgb(_sheet(b_img, h_img, f, neutral), out_dir / f"graft_{key}.png")
    metrics.update(head_sheet_scale=round(f, 4), head_texture_share=round(h_area * f * f / max(b_area + h_area * f * f, 1e-12), 4),
                   triangles=int(len(faces)))
    return {"ok": True, "reason": None, "glb": glb, "maps": maps, "metrics": metrics}
