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
   its top-to-chin equals the body's, the tops and nose tips met, centred across. Then both are cut along one ring
   round the neck: a plane under the chin (tilted up toward the back by at most the largest of TILTS) at the narrow,
   round part of the neck, found from the innermost surface round the neck's own axis, so an open shirt or a collar
   is never cut into. Each model is split exactly at the plane (the head's cut one triangle higher, leaving a gap);
   above the chin the head keeps its head and the body drops what lies inside the new head, below the chin the head
   keeps only the neck inside a cone round the axis and the body drops only its neck surface joined to the ring. The
   head is moved so the two rings' middles meet, its neck is fitted radially to the body's old neck at the same height
   and faded upward, and its skin tone is matched to the old neck's (also faded). A strip of triangles zipped round
   the axis in order of angle closes the gap, reading the body's texture.
   The joined mesh is written with both textures on one square sheet twice the size (body top-left, head top-right),
   for blender/headjoin.py to weld, smooth the strip, pack into one texture of the usual size and copy every map
   across texel by texel. The head's share of the packed texture is capped at HEAD_SHARE_MAX so the body keeps most
   of its texels.
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
RING_BINS = 36  # slices round the neck for the ring the two models are joined at
TILTS = (0.0, 10.0, 20.0)  # degrees the ring's plane may rise toward the back (30 reached the hairline at the back)
LEVELS = 9  # heights tried for the ring...
RING_COVER = 0.9  # share of the slices round the axis a ring height must find surface in
RING_LOW, RING_HIGH = 0.4, 0.1  # ...from this far under the chin to this far, as shares of the top-to-chin height
PROFILE_LEVELS = 12  # heights at which the neck's innermost surface is read, from the ring to the chin
INNER_SLACK = 0.25  # surface within this share outside the innermost radius is the neck too
SHEET_BINS, SHEET_SLACK = 72, 0.05  # the head's neck under its chin read finely: a shirt collar can hug the neck closer
# than INNER_SLACK (about a tenth outside it, all round)
NECK_CONE = 1.4  # the cut's cone: this many times the neck's own radius at the ring, per slice (three times at the chin)
BLEND = 0.6  # of the height from the ring to the chin: the head's neck is fitted to the body's over this
FIT_RANGE = (0.7, 1.4)  # the head's neck radius is scaled by at most this
GAIN_RANGE = (0.75, 1.33)  # the head's skin colour is scaled by at most this, per channel
NECK_ROWS, NECK_COLS = 16, 72  # the body's old neck read round its axis for the head's neck: heights by slices
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


def _bin_of(x: np.ndarray, z: np.ndarray, axis: tuple[float, float], bins: int = COLLAR_BINS) -> np.ndarray:
    a = np.arctan2(z - axis[1], x - axis[0])
    return ((a + np.pi) / (2 * np.pi) * bins).astype(int) % bins


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


# --- the ring across the neck --------------------------------------------------------------------------------------
def _crossings(verts: np.ndarray, faces: np.ndarray, y: float) -> tuple[np.ndarray, np.ndarray]:
    """Where the faces' edges cross height y: (points (k, 3), the face each belongs to (k,))."""
    pts, own = [], []
    for i, j in ((0, 1), (1, 2), (2, 0)):
        a, b = verts[faces[:, i]], verts[faces[:, j]]
        s = (a[:, 1] - y) * (b[:, 1] - y) < 0
        t = (y - a[s, 1]) / (b[s, 1] - a[s, 1])
        pts.append(a[s] + t[:, None] * (b[s] - a[s]))
        own.append(np.where(s)[0])
    return np.concatenate(pts), np.concatenate(own)


def _ring_radius(points: np.ndarray, axis: tuple[float, float], stat: Any, bins: int = RING_BINS) -> np.ndarray:
    """Per slice round the axis, a statistic of the points' distances from it; empty slices filled from their
    neighbours (all nan when there are no points)."""
    out = np.full(bins, np.nan)
    if len(points):
        k = _bin_of(points[:, 0], points[:, 2], axis, bins)
        r = np.hypot(points[:, 0] - axis[0], points[:, 2] - axis[1])
        for i in range(bins):
            m = k == i
            if m.any():
                out[i] = stat(r[m])
    return _fill_round(out)


def _edge_lengths(verts: np.ndarray, faces: np.ndarray) -> np.ndarray:
    if not len(faces):
        return np.zeros(1)
    v = verts[faces]
    return np.linalg.norm(v - np.roll(v, 1, axis=1), axis=2).reshape(-1)


def _fade(x: np.ndarray) -> np.ndarray:
    """1 at x <= 0, 0 at x >= 1, smooth in between."""
    t = np.clip(x, 0.0, 1.0)
    return 1.0 - t * t * (3.0 - 2.0 * t)


def _split(verts: np.ndarray, uv: np.ndarray, faces: np.ndarray, y: float,
           keep_above: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The faces cut exactly at height y, the kept side's pieces returned as new triangles (winding kept). New
    vertices go on the cut edges, one per edge, so neighbouring pieces share them. Returns (verts, uv, new faces,
    the face each piece came from): the arrays with the new vertices appended."""
    n0 = len(verts)
    extra_v: list[np.ndarray] = []
    extra_uv: list[np.ndarray] = []
    at: dict[tuple[int, int], int] = {}
    out: list[tuple[int, int, int]] = []
    parent: list[int] = []

    def mid(i: int, j: int) -> int:
        key = (min(i, j), max(i, j))
        if key not in at:
            a, b = key
            t = (y - verts[a, 1]) / (verts[b, 1] - verts[a, 1])
            at[key] = n0 + len(extra_v)
            extra_v.append(verts[a] + t * (verts[b] - verts[a]))
            extra_uv.append(uv[a] + t * (uv[b] - uv[a]))
        return at[key]

    for n, f in enumerate(faces):
        f = [int(x) for x in f]
        side = [(verts[i, 1] > y) == keep_above for i in f]
        k = sum(side)
        if k == 3:
            out.append((f[0], f[1], f[2]))
            parent.append(n)
        elif k == 1:
            i = side.index(True)
            a, b, c = f[i], f[(i + 1) % 3], f[(i + 2) % 3]
            out.append((a, mid(a, b), mid(a, c)))
            parent.append(n)
        elif k == 2:
            i = side.index(False)
            c, a, b = f[i], f[(i + 1) % 3], f[(i + 2) % 3]
            bc, ca = mid(b, c), mid(c, a)
            out.append((a, b, bc))
            out.append((a, bc, ca))
            parent += [n, n]
    if extra_v:
        verts = np.concatenate([verts, np.array(extra_v)])
        uv = np.concatenate([uv, np.array(extra_uv)])
    return verts, uv, np.array(out, dtype=np.int64).reshape(-1, 3), np.array(parent, dtype=np.int64)


def _tilted(verts: np.ndarray, tilt_deg: float, pivot: np.ndarray) -> np.ndarray:
    """The vertices turned about the left-right axis through `pivot` so that a plane rising toward the back (-z) by
    tilt_deg becomes level; -tilt_deg turns them back."""
    if not tilt_deg:
        return verts
    a = np.radians(tilt_deg)
    c, s = np.cos(a), np.sin(a)
    d = verts - pivot
    out = verts.copy()
    out[:, 1] = pivot[1] + c * d[:, 1] + s * d[:, 2]
    out[:, 2] = pivot[2] - s * d[:, 1] + c * d[:, 2]
    return out


def _tilted_height(y: float, z: float, tilt_deg: float, pivot: np.ndarray) -> float:
    """The height in the tilted frame of a point at height y and depth z (a keystone on the front outline)."""
    return float(_tilted(np.array([[pivot[0], y, z]]), tilt_deg, pivot)[0, 1])


def _neck_level(verts: np.ndarray, faces: np.ndarray, lo: float, hi: float, start: tuple[float, float],
                head_width: float) -> tuple[float, tuple[float, float], np.ndarray] | None:
    """The height between lo and hi where the neck is narrowest and roundest: at LEVELS heights, the innermost
    surface round the neck's axis, scored by its mean radius plus its spread (a shirt collar, the shoulders or the jaw
    make it lumpy). Returns (score, height, axis, radius per slice) or None."""
    best = None
    for y in np.linspace(lo, hi, LEVELS):
        pts, _ = _crossings(verts, faces, float(y))
        if len(pts) < 12:
            continue
        axis = _neck_axis(pts, start, head_width)
        near = np.hypot(pts[:, 0] - axis[0], pts[:, 2] - axis[1]) < 0.8 * head_width
        found = np.unique(_bin_of(pts[near, 0], pts[near, 2], axis, RING_BINS))
        if len(found) < RING_COVER * RING_BINS:  # not round the neck (the shoulders, an open shirt)
            continue
        r = _ring_radius(pts[near], axis, np.min)
        score = float(r.mean() + r.std())
        if best is None or score < best[0]:
            best = (score, float(y), axis, r)
    return best


def _neck_axis(points: np.ndarray, start: tuple[float, float], head_width: float) -> tuple[float, float]:
    """The neck's middle (x, z) from where a level plane crosses the surface: from a first guess under the head, the
    innermost surface in each slice round it (an open shirt or the shoulders lie further out) gives one point per
    slice; their mean is the next guess, five times."""
    c = np.array(start, dtype=float)
    p = points[:, [0, 2]]
    for _ in range(5):
        d = p - c
        r = np.linalg.norm(d, axis=1)
        ok = r < 0.8 * head_width
        if ok.sum() < 12:
            break
        k = _bin_of(p[ok, 0], p[ok, 1], (c[0], c[1]), RING_BINS)
        inner = [p[ok][k == i][np.argmin(r[ok][k == i])] for i in range(RING_BINS) if (k == i).any()]
        if len(inner) < RING_BINS // 3:
            break
        c = np.mean(inner, axis=0)
    return float(c[0]), float(c[1])


def _inner_profile(verts: np.ndarray, faces: np.ndarray, lo: float, hi: float, axis: tuple[float, float],
                   head_width: float, bins: int = RING_BINS) -> tuple[np.ndarray, np.ndarray]:
    """The innermost surface round the axis at PROFILE_LEVELS heights from lo to hi: (heights, radius per height and
    slice). Between the ring and the chin that is the neck (and the throat under the jaw); shoulders, collars and an
    open shirt lie further out."""
    ys = np.linspace(lo, hi, PROFILE_LEVELS)
    rad = np.full((len(ys), bins), np.nan)
    for i, y in enumerate(ys):
        pts, _ = _crossings(verts, faces, float(y))
        if len(pts):
            near = np.hypot(pts[:, 0] - axis[0], pts[:, 2] - axis[1]) < 0.8 * head_width
            rad[i] = _ring_radius(pts[near], axis, np.min, bins)
    return ys, rad


def _in_layer(p: np.ndarray, profile: tuple[np.ndarray, np.ndarray], axis: tuple[float, float],
              slack: float = INNER_SLACK) -> np.ndarray:
    """Whether points lie on the innermost surface: within `slack` of its radius at the nearest profile height."""
    ys, rad = profile
    j = np.clip(np.round((p[:, 1] - ys[0]) / max(ys[-1] - ys[0], 1e-12) * (len(ys) - 1)).astype(int), 0, len(ys) - 1)
    k = _bin_of(p[:, 0], p[:, 2], axis, rad.shape[1])
    lim = rad[j, k] * (1.0 + slack)
    r = np.hypot(p[:, 0] - axis[0], p[:, 2] - axis[1])
    return np.isfinite(lim) & (r < lim)


def _ring_ratio(ring: np.ndarray, target: np.ndarray, axis: tuple[float, float]) -> tuple[np.ndarray, np.ndarray]:
    """Per ring vertex, sorted by angle round the axis: (angle, the target surface's radius at that angle over the
    vertex's own, within FIT_RANGE). Of the target points within one slice's angle, the one nearest the vertex's own
    radius is the matching surface (another layer, a shirt collar, can cross the same height further out)."""
    ta = np.arctan2(target[:, 2] - axis[1], target[:, 0] - axis[0])
    tr = np.hypot(target[:, 0] - axis[0], target[:, 2] - axis[1])
    a = np.arctan2(ring[:, 2] - axis[1], ring[:, 0] - axis[0])
    r = np.hypot(ring[:, 0] - axis[0], ring[:, 2] - axis[1])
    o = np.argsort(ta)
    near = np.interp(a, ta[o], tr[o], period=2 * np.pi)
    dang = np.abs(np.angle(np.exp(1j * (a[:, None] - ta[None, :]))))
    within = dang < np.pi / RING_BINS
    cand = np.where(within, np.abs(tr[None, :] - r[:, None]), np.inf)
    j = np.argmin(cand, axis=1)
    near = np.where(np.isfinite(cand[np.arange(len(r)), j]), tr[j], near)
    q = np.clip(near / np.maximum(r, 1e-9), *FIT_RANGE)
    k = np.argsort(a)
    return a[k], q[k]


def _ring_middle(radius: np.ndarray, axis: tuple[float, float]) -> np.ndarray:
    """The middle (x, z) of a ring given by its radius per slice round the axis."""
    a = (np.arange(RING_BINS) + 0.5) / RING_BINS * 2 * np.pi - np.pi
    return np.array([axis[0] + np.mean(radius * np.cos(a)), axis[1] + np.mean(radius * np.sin(a))])


def _facing_out(verts: np.ndarray, faces: np.ndarray, axis: tuple[float, float]) -> np.ndarray:
    """Whether each face faces away from the axis: the outside of the neck. A head build can come out as a thin
    shell at its neck, with an inner wall facing the axis just inside the skin; the innermost surface is then that
    wall, which nobody sees."""
    v = verts[faces]
    n = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])
    c = v.mean(axis=1)
    return n[:, 0] * (c[:, 0] - axis[0]) + n[:, 2] * (c[:, 2] - axis[1]) > 0


def _ring_faces(verts: np.ndarray, faces: np.ndarray, cand: np.ndarray, axis: tuple[float, float],
                pairs: np.ndarray) -> np.ndarray:
    """Of the candidate faces (those the cut crosses), the connected piece that is the innermost round the axis in
    the most slices: the neck's own surface."""
    idx = np.where(cand)[0]
    if not len(idx):
        return cand.copy()
    lab = _components(len(faces), pairs, cand)
    c = verts[faces[idx]].mean(axis=1)
    r = np.hypot(c[:, 0] - axis[0], c[:, 2] - axis[1])
    k = _bin_of(c[:, 0], c[:, 2], axis, RING_BINS)
    winners = [lab[idx[np.where(k == i)[0][np.argmin(r[k == i])]]] for i in range(RING_BINS) if (k == i).any()]
    vals, counts = np.unique(winners, return_counts=True)
    return lab == vals[np.argmax(counts)]


def _barycentric(tri: np.ndarray, p: np.ndarray) -> np.ndarray:
    """(n, 3) barycentric weights of points p (n, 3) in triangles tri (n, 3, 3)."""
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    v0, v1, v2 = b - a, c - a, p - a
    d00, d01, d11 = (v0 * v0).sum(1), (v0 * v1).sum(1), (v1 * v1).sum(1)
    d20, d21 = (v2 * v0).sum(1), (v2 * v1).sum(1)
    den = np.where(np.abs(d00 * d11 - d01 * d01) < 1e-30, 1e-30, d00 * d11 - d01 * d01)
    v = (d11 * d20 - d01 * d21) / den
    w = (d00 * d21 - d01 * d20) / den
    return np.stack([1.0 - v - w, v, w], axis=1)


def _ring_loop(verts: np.ndarray, faces: np.ndarray, first_new: int) -> tuple[list[int], dict[int, list[int]], dict[tuple[int, int], int]] | None:
    """The loop a cut left in `faces` (the cut faces' pieces): edges between the cut's new vertices (index from
    first_new on) that one piece uses, vertices joined by position (a texture seam splits them), walked round; the
    longest loop. Returns (vertex indices in order, {vertex: pieces using it}, {edge: the piece using it}), edges
    and vertices by their joined position's first index, or None."""
    key = np.round(verts * 1e7).astype(np.int64)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    first = {}
    for i in range(len(verts)):
        first.setdefault(int(inv[i]), i)
    wid = np.array([first[int(inv[i])] for i in range(len(verts))])
    edges: dict[tuple[int, int], list[int]] = {}
    users: dict[int, list[int]] = {}
    for t, tri in enumerate(faces):
        for a, b in ((0, 1), (1, 2), (2, 0)):
            i, j = int(tri[a]), int(tri[b])
            users.setdefault(int(wid[i]), []).append(t)
            if i >= first_new and j >= first_new and wid[i] != wid[j]:
                edges.setdefault((min(wid[i], wid[j]), max(wid[i], wid[j])), []).append(t)
    adj: dict[int, list[int]] = {}
    for (a, b), ts in edges.items():
        if len(ts) == 1:
            adj.setdefault(a, []).append(b)
            adj.setdefault(b, []).append(a)
    seen: set[int] = set()
    best: list[int] = []
    # chains first from their ends (a ring with a gap), then closed loops
    for s0 in sorted(adj, key=lambda v: len(adj[v])):
        if s0 in seen:
            continue
        loop, prev, cur = [s0], None, s0
        seen.add(s0)
        while True:
            nxt = [n for n in adj[cur] if n != prev and n not in seen]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            loop.append(cur)
            seen.add(cur)
        if len(loop) > len(best):
            best = loop
    if len(best) < 8:
        return None
    one = {e: ts[0] for e, ts in edges.items() if len(ts) == 1}
    return best, users, one


def _zip(pb: np.ndarray, ph: np.ndarray, axis: tuple[float, float]) -> list[tuple[tuple[str, int], ...]]:
    """Triangles joining two loops round the axis (body loop pb under, head loop ph over), zipped in order of angle so
    none cross: each step advances the loop whose next vertex comes first. Corners are ("b", i) or ("h", i) into the
    loops as given; facing outward."""
    def order(p):
        a = np.arctan2(p[:, 2] - axis[1], p[:, 0] - axis[0])
        turn = np.angle(np.exp(1j * np.diff(np.append(a, a[0])))).sum()
        idx = np.arange(len(p)) if turn > 0 else np.arange(len(p))[::-1]
        s = int(np.argmin(a[idx]))
        idx = np.roll(idx, -s)
        return idx, np.unwrap(a[idx])

    ib, ab = order(pb)
    ih, ah = order(ph)
    nb, nh = len(ib), len(ih)
    ab = np.append(ab, ab[0] + 2 * np.pi)
    ah = np.append(ah, ah[0] + 2 * np.pi)
    tris = []
    i = j = 0
    while i < nb or j < nh:
        if j >= nh or (i < nb and ab[i + 1] <= ah[j + 1]):
            tris.append((("b", int(ib[i % nb])), ("b", int(ib[(i + 1) % nb])), ("h", int(ih[j % nh]))))
            i += 1
        else:
            tris.append((("b", int(ib[i % nb])), ("h", int(ih[(j + 1) % nh])), ("h", int(ih[j % nh]))))
            j += 1
    # facing outward: the first triangle's normal against the way out from the axis
    p = [pb[k] if s == "b" else ph[k] for s, k in tris[0]]
    n = np.cross(p[1] - p[0], p[2] - p[0])
    c = (p[0] + p[1] + p[2]) / 3.0
    if n[0] * (c[0] - axis[0]) + n[2] * (c[2] - axis[1]) < 0:
        tris = [(a, c_, b) for a, b, c_ in tris]
    return tris


def _strip(bv: np.ndarray, buv: np.ndarray, b_new: np.ndarray, b_whole: np.ndarray, b_first: int, hv: np.ndarray,
           h_new: np.ndarray, h_first: int, axis: tuple[float, float]) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """The strip of triangles closing the gap between the body's ring and the head's ring: (vertices, body texture
    positions, faces), three vertices of its own per triangle (Blender joins them to the rings by position). Each
    triangle reads the body's texture through the whole body triangle (b_whole, per ring piece) that the ring piece it
    stands on (its edge, else its corner) was cut from: that triangle's texture runs on above the cut, where the
    body's old neck was, so the strip shows the body's neck as it was there. Positions are held inside it (past its
    edges the texture can be another piece or padding). None when a ring has no loop."""
    lb = _ring_loop(bv, b_new, b_first)
    lh = _ring_loop(hv, h_new, h_first)
    if lb is None or lh is None:
        return None
    loop_b, users_b, edge_b = lb
    loop_h = lh[0]
    pb, ph = bv[loop_b], hv[loop_h]
    out_v, out_uv = [], []
    for tri in _zip(pb, ph, axis):
        body = [loop_b[k] for s, k in tri if s == "b"]
        base = edge_b.get((min(body), max(body))) if len(body) == 2 else None
        if base is None:
            base = users_b[body[0]][0]
        fv = b_whole[base]
        for s, k in tri:
            p = pb[k] if s == "b" else ph[k]
            w = np.clip(_barycentric(bv[fv][None], p[None])[0], 0.0, None)
            w /= max(w.sum(), 1e-12)
            out_v.append(p)
            out_uv.append(w @ buv[fv])
    v = np.array(out_v)
    return v, np.array(out_uv), np.arange(len(v)).reshape(-1, 3)


def _neck_colour(verts: np.ndarray, faces: np.ndarray, uv: np.ndarray, tex: np.ndarray, lo: float, hi: float,
                 axis: tuple[float, float]) -> tuple[np.ndarray, np.ndarray] | None:
    """The colour of a neck seen from its axis: (heights, colour per height and slice round the axis), NECK_ROWS
    heights from lo to hi and NECK_COLS slices, each the colour of the innermost surface there (the skin, not a
    collar further out); empty cells filled from their neighbours. None when nothing crosses."""
    ys = np.linspace(lo, hi, NECK_ROWS)
    out = np.full((NECK_ROWS, NECK_COLS, 3), np.nan)
    T = tex.shape[0]
    for i, y in enumerate(ys):
        pts, own = _crossings(verts, faces, float(y))
        if not len(pts):
            continue
        f = faces[own]
        w = np.clip(_barycentric(verts[f], pts), 0.0, None)
        w /= np.maximum(w.sum(axis=1, keepdims=True), 1e-12)
        u = (uv[f] * w[:, :, None]).sum(axis=1)
        col = tex[np.clip(((1 - u[:, 1]) * T).astype(int), 0, T - 1), np.clip((u[:, 0] * T).astype(int), 0, T - 1)]
        k = _bin_of(pts[:, 0], pts[:, 2], axis, NECK_COLS)
        r = np.hypot(pts[:, 0] - axis[0], pts[:, 2] - axis[1])
        for b in np.unique(k):
            m = np.where(k == b)[0]
            out[i, b] = col[m[np.argmin(r[m])]]
    rows = ~np.isnan(out[:, :, 0]).all(axis=1)
    if not rows.any():
        return None
    for i in np.where(rows)[0]:
        for c in range(3):
            out[i, :, c] = _fill_round(out[i, :, c])
    for c in range(3):
        for b in range(NECK_COLS):
            col_ = out[:, b, c]
            ok = ~np.isnan(col_)
            out[:, b, c] = np.interp(np.arange(NECK_ROWS), np.where(ok)[0], col_[ok])
    return ys, out


def _texels(uv: np.ndarray, faces: np.ndarray, size: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """The texels whose centres fall inside each face's texture triangle: (rows, columns, face, barycentric weights).
    Texture rows run from the top (v = 1)."""
    rows, cols, own, bary = [], [], [], []
    for n, f in enumerate(faces):
        t = uv[f] * size
        t[:, 1] = size - t[:, 1]
        x0, y0 = np.floor(t.min(axis=0)).astype(int)
        x1, y1 = np.ceil(t.max(axis=0)).astype(int)
        x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, size - 1), min(y1, size - 1)
        if x1 < x0 or y1 < y0:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
        c = np.stack([gx.ravel() + 0.5, gy.ravel() + 0.5], axis=1)
        a, b, cc = t
        den = (b[1] - cc[1]) * (a[0] - cc[0]) + (cc[0] - b[0]) * (a[1] - cc[1])
        if abs(den) < 1e-12:
            continue
        l1 = ((b[1] - cc[1]) * (c[:, 0] - cc[0]) + (cc[0] - b[0]) * (c[:, 1] - cc[1])) / den
        l2 = ((cc[1] - a[1]) * (c[:, 0] - cc[0]) + (a[0] - cc[0]) * (c[:, 1] - cc[1])) / den
        l3 = 1.0 - l1 - l2
        inside = (l1 >= -1e-6) & (l2 >= -1e-6) & (l3 >= -1e-6)
        if not inside.any():
            continue
        rows.append(gy.ravel()[inside])
        cols.append(gx.ravel()[inside])
        own.append(np.full(int(inside.sum()), n))
        bary.append(np.stack([l1[inside], l2[inside], l3[inside]], axis=1))
    if not rows:
        e = np.zeros(0, dtype=int)
        return e, e, e, np.zeros((0, 3))
    return np.concatenate(rows), np.concatenate(cols), np.concatenate(own), np.concatenate(bary)


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

    # The join is one ring across the neck at its narrowest and roundest, a set distance under the chin (the side
    # outline's neck keystone is unreliable: at the bottom of the neck on an open shirt, under the chin where a collar
    # rises). Both models are cut
    # there by the same level plane, inside a cone round the neck's axis that is the neck's own width at the ring and
    # widens to take in the jaw at the chin: the body keeps everything under the plane and everything outside the
    # cone (an open shirt, a collar), the head everything over the plane inside the cone. The head's cut sits one
    # triangle's height above the body's, its neck is fitted to the body's at every angle round the ring (fading out
    # toward the chin), and its skin tone is matched to the body's neck the same way, so blender/headjoin.py bridges
    # two rings that already meet in shape and colour.
    if not kb["chin_bottom"] > kb["neck"]:
        return {"ok": False, "reason": "no_keys", "metrics": metrics}
    # a short neck seen from the side runs up at the back (from under the jaw to the base of the skull): the ring's
    # plane is tilted up at the back by the tilt in TILTS that gives the neatest ring; the work below is done in that
    # tilted frame (its level planes are the tilted ones) and the result tilted back
    pivot = np.array([kb["centre_x"], kb["neck"], kb["centre_z"]])
    start = (float(kb["centre_x"]), float(kb["centre_z"]))
    best = None
    for tilt in TILTS:
        tv = _tilted(body["verts"], tilt, pivot)
        chin_t = _tilted_height(kb["chin_bottom"], kb["tip_z"], tilt, pivot)
        level = _neck_level(tv, body["faces"], chin_t - RING_LOW * H, chin_t - RING_HIGH * H, start, kb["head_width"])
        if level is not None and (best is None or level[0] < best[0]):
            best = (level[0], tilt, level[1], level[2], level[3], chin_t)
    if best is None:
        return {"ok": False, "reason": "no_keys", "metrics": metrics}
    _, tilt, cut, axis, r_in, chin = best
    metrics.update(keys={"neck": round(float(kb["neck"]), 5), "chin": round(float(kb["chin_bottom"]), 5), "top": round(float(kb["top"]), 5)})
    body = {**body, "verts": _tilted(body["verts"], tilt, pivot)}
    hv = _tilted(hv, tilt, pivot)
    metrics.update(ring_tilt=tilt)
    hc = hv[head["faces"]].mean(axis=1)
    lab_h = face_lab(head, htex)
    h_head = is_head_colour(lab_h, kh["refs"])
    # grey surface below the nose that is not skin is the head build's own collar (it rides up at the back of the neck)
    h_grey = ~h_head & (np.linalg.norm(lab_h[:, 1:], axis=1) <= COLOURFUL) & (hc[:, 1] < kb["tip"])
    bv, bf_all, buv = body["verts"], body["faces"], body["uv"]
    bc = bv[bf_all].mean(axis=1)
    lab_all = face_lab(body, btex)
    b_head = is_head_colour(lab_all, kb["refs"])
    b_grey = ~b_head & (np.linalg.norm(lab_all[:, 1:], axis=1) <= COLOURFUL)
    r_cyl = NECK_CONE * r_in
    edge = collar_edge(bc, b_grey, axis, chin, kb["tip"], H, kb["head_width"])
    if edge is not None:
        # a collar round the neck stays whole: the cone stops halfway between the neck and the collar's opening
        inner = np.interp(np.arange(RING_BINS) * COLLAR_BINS / RING_BINS, np.arange(COLLAR_BINS + 1),
                          np.append(edge["inner"], edge["inner"][0]))
        wide = inner > 1.05 * r_in
        r_cyl = np.where(wide, np.minimum(r_cyl, 0.5 * (r_in + inner)), r_cyl)
        metrics.update(collar_top=[round(float(x), 4) for x in edge["top"]], collar_inner=[round(float(x), 4) for x in edge["inner"]])

    def cone(p: np.ndarray, base: float, widen: float = 1.0) -> np.ndarray:
        """Whether points lie inside the cone: the neck's radius at `base`, three times it at the chin."""
        k = _bin_of(p[:, 0], p[:, 2], axis, RING_BINS)
        grow = 1.0 + 2.0 * np.clip((p[:, 1] - base) / max(chin - base, 1e-9), 0.0, 1.0)
        return np.hypot(p[:, 0] - axis[0], p[:, 2] - axis[1]) < r_cyl[k] * grow * widen

    b_lo, b_hi = bv[bf_all][:, :, 1].min(axis=1), bv[bf_all][:, :, 1].max(axis=1)
    b_pairs = _face_pairs(bv, bf_all)
    # the body's ring: of the pieces of surface the plane crosses inside the cone, the one that is innermost round
    # most of the neck (an open shirt's collar or a coat's lining can sit inside the cone too)
    near_axis = lambda p: np.hypot(p[:, 0] - axis[0], p[:, 2] - axis[1]) < 0.8 * kb["head_width"]  # noqa: E731
    b_out = _facing_out(bv, bf_all, axis)
    b_cross = _ring_faces(bv, bf_all, near_axis(bc) & b_out & (b_lo < cut) & (b_hi > cut), axis, b_pairs)
    if not b_cross.any():
        return {"ok": False, "reason": "no_keys", "metrics": metrics}
    # the head: its face and skull above the chin, and inside the cone its neck down to its own ring, one triangle's
    # height above the body's, joined to the face (a collar or shoulders of the head build stay out)
    gap = float(np.median(_edge_lengths(bv, bf_all[b_cross])))
    ring_h = cut + gap
    h_faces = head["faces"]
    h_lo, h_hi = hv[h_faces][:, :, 1].min(axis=1), hv[h_faces][:, :, 1].max(axis=1)
    h_pairs = _face_pairs(hv, h_faces)
    h_out = _facing_out(hv, h_faces, axis)
    h_cross = _ring_faces(hv, h_faces, near_axis(hc) & h_out & (h_lo < ring_h) & (h_hi > ring_h), axis, h_pairs)
    # grey surface is kept where it cannot be a collar: on the front half above the chin (stubble on the jaw), and on
    # the neck's innermost surface below it (stubble on the throat; a collar stands off the neck)
    front = (hc[:, 2] - axis[1]) * np.sign(kb["tip_z"] - axis[1]) > 0
    h_inner = _in_layer(hc, _inner_profile(hv, h_faces[h_out], ring_h, chin, axis, kb["head_width"]), axis)
    # under the chin only the neck's own skin, facing out: a sheet standing outside it there (the head build's shirt
    # collar) or the inner wall of a thin shell would be left with an open edge just over the ring
    h_skin = h_out & _in_layer(hc, _inner_profile(hv, h_faces[h_out], ring_h, chin, axis, kb["head_width"], SHEET_BINS),
                               axis, SHEET_SLACK)
    keep_top = (~h_grey | front) & (hc[:, 1] > chin) & (h_lo >= ring_h)
    h_neck = cone(hc, ring_h, 2.0) & (~h_grey | h_inner) & h_skin & (h_lo >= ring_h) & (hc[:, 1] <= chin)
    lab = _components(len(h_faces), h_pairs, h_neck | h_cross | keep_top)
    ring_labels = np.unique(lab[h_cross])
    keep_h = keep_top | (h_neck & np.isin(lab, ring_labels[ring_labels >= 0]))
    keep_h = tidy(hv, h_faces, keep_h | h_cross, only_largest=True)
    h_cross &= keep_h
    keep_h &= ~h_cross & ((h_lo >= ring_h) | keep_top)  # tidying fills small holes back: never below the ring
    # the body: its old neck above its ring, joined to the ring inside the cone up to the chin; above the chin, its old
    # head (head colours, or inside or near the new head: the old nose, lips and ears stick out past the new ones);
    # head-coloured surface near the new neck
    b_neck = _in_layer(bc, _inner_profile(bv, bf_all[b_out], cut, chin, axis, kb["head_width"]), axis) \
        & (b_lo >= cut) & (bc[:, 1] <= chin)
    lab = _components(len(bf_all), b_pairs, b_neck | b_cross)
    ring_labels = np.unique(lab[b_cross])
    drop_b = b_neck & np.isin(lab, ring_labels[ring_labels >= 0])
    old_neck = bf_all[drop_b | b_cross]  # read below for the head's ring: where the body's own neck was
    cand = np.where(bc[:, 1] > cut)[0]
    hk_new = h_faces[keep_h | h_cross]
    if len(cand) and len(hk_new):
        used_new = np.unique(hk_new)
        hvn = face.smooth_normals(hv, hk_new)
        dist, off = _against_new_head(bc[cand], hv[used_new], hvn[used_new])
        above_chin = bc[cand, 1] > chin
        inside = (off < 0.0) & (dist < 0.6 * kb["head_width"])
        near = dist < NEAR * H
        colourful = np.linalg.norm(lab_all[cand, 1:], axis=1) > COLOURFUL
        drop_b[cand] |= (above_chin & (b_head[cand] | inside | (near & colourful))) | (~above_chin & b_head[cand] & near)
    drop_b &= ~b_cross
    near_cut = bc[:, 1] > cut - 0.1 * H
    neck_drop = drop_b & (b_lo >= cut)
    drop_b = ~tidy(bv, bf_all, ~drop_b, only_largest=False, region=near_cut) | neck_drop
    b_cross &= ~drop_b
    metrics.update(cut_y=round(float(cut), 5), gap=round(gap, 5), body_faces=int(len(bc)),
                   body_faces_dropped=int(drop_b.sum()), head_faces=int(len(hc)), head_faces_kept=int(keep_h.sum()),
                   neck_radius=[round(float(x), 4) for x in r_in])
    if keep_h.sum() < MIN_HEAD_FACES:
        return {"ok": False, "reason": "nothing_kept", "metrics": metrics}
    if drop_b.sum() < MIN_DROPPED:
        return {"ok": False, "reason": "nothing_dropped", "metrics": metrics}

    # the ring faces cut exactly at the ring heights
    b_keep = ~drop_b & ~b_cross
    bv2, buv2, b_new, b_parent = _split(bv, buv, bf_all[b_cross], cut, keep_above=False)
    hv2, huv2, h_new, _ = _split(hv, head["uv"], h_faces[h_cross], ring_h, keep_above=True)
    bf = np.concatenate([bf_all[b_keep], b_new])
    hk = np.concatenate([h_faces[keep_h], h_new])
    # the head's neck fitted to the body's: at the head's ring height, the body's own (dropped) neck is where the
    # head's ring goes, per angle; head vertices between the ring and the chin move toward that, fully at the ring
    rb_pts, _ = _crossings(bv, old_neck, ring_h)
    rh_pts, _ = _crossings(hv2, h_new, ring_h + 1e-7 * H)
    if len(rb_pts) < 12 or len(rh_pts) < 12:
        return {"ok": False, "reason": "join", "metrics": metrics}
    # first the whole head moved across so the two rings share a middle (the placement by keystones can be a few
    # millimetres off), then the rest fitted per angle
    r_b = _ring_radius(rb_pts, axis, np.median)
    r_h = _ring_radius(rh_pts, axis, np.median)
    shift = _ring_middle(r_b, axis) - _ring_middle(r_h, axis)
    shift *= min(1.0, 0.5 * float(r_b.mean()) / max(float(np.linalg.norm(shift)), 1e-12))
    hv2 = hv2.copy()
    hv2[:, 0] += shift[0]
    hv2[:, 2] += shift[1]
    rh_pts, _ = _crossings(hv2, h_new, ring_h + 1e-7 * H)
    r_h = _ring_radius(rh_pts, axis, np.median)
    metrics.update(ring_shift=[round(float(x), 5) for x in shift])
    # per head ring vertex (the split's new vertices, all on the ring): the body's neck radius at its own angle over
    # its radius, so each lands on the body's old neck; vertices further up take the ratio at their angle, faded
    ring_ids = np.arange(len(hv), len(hv2))
    ring_ids = ring_ids[np.isin(ring_ids, np.unique(h_new))]
    rb_pts, _ = _crossings(bv, old_neck, ring_h)
    if len(ring_ids) < 12:
        return {"ok": False, "reason": "join", "metrics": metrics}
    q_ang, q = _ring_ratio(hv2[ring_ids], rb_pts, axis)
    span = max(chin - ring_h, 1e-9)
    used_h = np.unique(hk)
    p = hv2[used_h]
    w = _fade((p[:, 1] - ring_h) / (BLEND * span))
    a = np.arctan2(p[:, 2] - axis[1], p[:, 0] - axis[0])
    rr = np.interp(a, q_ang, q, period=2 * np.pi)
    scale = 1.0 + w * (rr - 1.0)
    hv2 = hv2.copy()
    hv2[used_h, 0] = axis[0] + (p[:, 0] - axis[0]) * scale
    hv2[used_h, 2] = axis[1] + (p[:, 2] - axis[1]) * scale
    ratio = q
    metrics.update(neck_fit=[round(float(ratio.min()), 4), round(float(ratio.max()), 4)])

    # skin tone: the colour just over the head's ring against the body's own neck at the same height, per channel,
    # applied to the whole head (a head build's colour can drift as a whole, greyer or warmer than the body's, which
    # was matched to the picture; a fade would leave a band of the body's tone up the neck)
    def tone(verts, uv, faces, tex, y):
        pts, own = _crossings(verts, faces, y)
        if len(pts) < 12:
            return None
        f = faces[own]
        bary = _barycentric(verts[f], pts)
        u = (uv[f] * bary[:, :, None]).sum(axis=1)
        T = tex.shape[0]
        x = np.clip((u[:, 0] * T).astype(int), 0, T - 1)
        yy = np.clip(((1 - u[:, 1]) * T).astype(int), 0, T - 1)
        return np.median(tex[yy, x], axis=0)

    tb = tone(bv, buv, old_neck, btex, ring_h + 0.25 * gap)
    th = tone(hv2, huv2, h_new, htex, ring_h + 0.25 * gap)
    gain = None
    if tb is not None and th is not None:
        gain = np.clip(tb / np.maximum(th, 1e-4), *GAIN_RANGE)
        metrics.update(tone_gain=[round(float(x), 4) for x in gain])
    # the head's neck painted with the body's old neck at the same height and angle, fully at the ring and fading out
    # at the chin: a head build paints its own collar's colour low on its neck, and its skin there need not follow
    # the body's (the strip under it reads the body's)
    neck_col = _neck_colour(bv, old_neck, buv, btex, ring_h, chin, axis)
    neck_faces = hk[(hv2[hk][:, :, 1].min(axis=1) < chin)]

    def paint_neck(img: np.ndarray) -> np.ndarray:
        rows, cols, own, bary = _texels(huv2, neck_faces, img.shape[0])
        if neck_col is None or not len(rows):
            return img
        pts = (hv2[neck_faces[own]] * bary[:, :, None]).sum(axis=1)
        ys, grid = neck_col
        ri = np.clip((pts[:, 1] - ys[0]) / max(ys[-1] - ys[0], 1e-12) * (NECK_ROWS - 1), 0, NECK_ROWS - 1)
        ci = _bin_of(pts[:, 0], pts[:, 2], axis, NECK_COLS)
        r0 = np.floor(ri).astype(int)
        r1 = np.minimum(r0 + 1, NECK_ROWS - 1)
        t = (ri - r0)[:, None]
        col = grid[r0, ci] * (1 - t) + grid[r1, ci] * t
        wgt = _fade((pts[:, 1] - ring_h) / max(chin - ring_h, 1e-9))[:, None]
        img = img.copy()
        img[rows, cols] = img[rows, cols] * (1 - wgt) + col * wgt
        metrics.update(neck_painted_texels=int(len(rows)))
        return img

    # one mesh: the kept faces of both, unused vertices dropped, UVs on the double sheet
    b_used = np.unique(bf)
    h_used = np.unique(hk)
    b_area = _uv_area(buv2, bf)
    h_area = _uv_area(huv2, hk)
    f = 1.0
    if h_area > 0 and b_area > 0:
        share = HEAD_SHARE_MAX
        f = float(min(1.0, np.sqrt(share * b_area / ((1 - share) * h_area))))
    b_map = -np.ones(len(bv2), dtype=np.int64)
    b_map[b_used] = np.arange(len(b_used))
    h_map = -np.ones(len(hv2), dtype=np.int64)
    h_map[h_used] = np.arange(len(h_used)) + len(b_used)
    # the strip between the rings, zipped round the neck in order of angle (made here rather than by Blender's bridge,
    # which pairs rings of different vertex counts without regard to angle and can twist the strip)
    strip = _strip(bv2, buv2, b_new, bf_all[b_cross][b_parent], len(bv), hv2, h_new, len(hv), axis)
    if strip is None:
        return {"ok": False, "reason": "join", "metrics": metrics}
    s_v, s_uv, s_f = strip
    metrics.update(strip_faces=int(len(s_f)))
    verts = _turned(_tilted(np.concatenate([bv2[b_used], hv2[h_used], s_v]), -tilt, pivot), -body_yaw, body_centre)
    # the ring's plane in the model's own frame (a point on the neck's axis, and the plane's normal), for checks
    ends = _turned(_tilted(np.array([[axis[0], cut, axis[1]], [axis[0], cut + 1.0, axis[1]]]), -tilt, pivot), -body_yaw, body_centre)
    metrics.update(ring_plane=[round(float(x), 6) for x in (*ends[0], *(ends[1] - ends[0]))])
    buvu = buv2[b_used]
    huvu = huv2[h_used]
    uv = np.concatenate([np.stack([buvu[:, 0] * 0.5, 0.5 + buvu[:, 1] * 0.5], axis=1),
                         np.stack([0.5 + f * huvu[:, 0] * 0.5, 1.0 - f * (1.0 - huvu[:, 1]) * 0.5], axis=1),
                         np.stack([s_uv[:, 0] * 0.5, 0.5 + s_uv[:, 1] * 0.5], axis=1)])
    faces = np.concatenate([b_map[bf], h_map[hk], s_f + len(b_used) + len(h_used)])

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
        if key == "base_color" and h_img is not None and gain is not None:
            h_img = np.clip(h_img * gain[None, None, :], 0.0, 1.0)
        if key == "base_color" and h_img is not None:
            h_img = paint_neck(h_img)
        maps[key] = face.save_rgb(_sheet(b_img, h_img, f, neutral), out_dir / f"graft_{key}.png")
    metrics.update(head_sheet_scale=round(f, 4), head_texture_share=round(h_area * f * f / max(b_area + h_area * f * f, 1e-12), 4),
                   triangles=int(len(faces)))
    return {"ok": True, "reason": None, "glb": glb, "maps": maps, "metrics": metrics}
