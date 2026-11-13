"""Face detail pass: make a character's 3D face match the face in its painted picture.

"Render, repaint, project back" (pure numpy, no Blender):
1. render_view: the model's head, base colour only, from an orthographic camera we know exactly (the same
   rasteriser is used for the render and for the projection, so the two always line up).
2. A sharp picture of that render, repainted elsewhere (stages/prep.py runs Qwen-Image-Edit with the painted head as
   a second picture). check_registration says whether the repaint kept the head where the render had it.
3. project_back: every texel of the base colour that the camera sees squarely, inside the face region, takes the
   repainted colour; tone is matched to the old texture at a coarse scale and the edges are feathered.

Coordinates are the glTF ones trimesh loads: Y up, the character faces +Z (prep turns it that way). Texture rows
follow imaging.rasterise_uv: row = (1 - v) * size.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

# --- mesh -----------------------------------------------------------------------------------------------------


def load_mesh(glb: Path | str) -> dict[str, np.ndarray] | None:
    """{verts (n,3), faces (m,3), uv (n,2), vnormals (n,3) smoothed across UV seams} or None without UVs."""
    import trimesh

    mesh = trimesh.load(str(glb), force="mesh", process=False)
    uv = getattr(getattr(mesh, "visual", None), "uv", None)
    if uv is None or len(uv) != len(mesh.vertices) or len(mesh.faces) == 0:
        return None
    verts = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    return {"verts": verts, "faces": faces, "uv": np.asarray(uv, dtype=np.float64), "vnormals": smooth_normals(verts, faces)}


def smooth_normals(verts: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """Area-weighted vertex normals with vertices at the same position joined (glTF splits them along UV seams)."""
    key = np.round(verts * 1e5).astype(np.int64)
    _, wid = np.unique(key, axis=0, return_inverse=True)
    wid = wid.reshape(-1)
    a, b, c = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
    fn = np.cross(b - a, c - a)  # length = twice the area
    acc = np.zeros((int(wid.max()) + 1, 3))
    for k in range(3):
        np.add.at(acc, wid[faces[:, k]], fn)
    n = acc[wid]
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)


@dataclass
class Head:
    centre: np.ndarray  # (3,) a point in the middle of the head
    width: float  # across the head (x), metres
    top: float  # highest y
    height: float  # the model's full height


def find_head(verts: np.ndarray, head_share: float = 0.13) -> Head | None:
    """The head of a standing figure: the top `head_share` of its height. Its middle in x and z is the median of the
    vertices up there, its width the 5th to 95th percentile spread. None when there is nothing up there."""
    y = verts[:, 1]
    top, bottom = float(y.max()), float(y.min())
    h = top - bottom
    if h <= 0:
        return None
    sel = verts[y >= top - head_share * h]
    if len(sel) < 20:
        return None
    x0, x1 = np.percentile(sel[:, 0], [5, 95])
    cx = float(np.median(sel[:, 0]))
    cz = float(np.median(sel[:, 2]))
    cy = top - 0.5 * head_share * h
    return Head(centre=np.array([cx, cy, cz]), width=float(x1 - x0), top=top, height=h)


# --- camera ---------------------------------------------------------------------------------------------------


@dataclass
class Camera:
    """Orthographic camera looking at `target` from `yaw_deg` around the up axis (0: from the front, +Z; positive
    turns toward the character's left, +X). `scale` is the width and height of the picture in metres."""
    target: np.ndarray
    yaw_deg: float
    scale: float
    size: int

    @property
    def basis(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        a = math.radians(self.yaw_deg)
        back = np.array([math.sin(a), 0.0, math.cos(a)])  # from the target toward the camera
        right = np.array([math.cos(a), 0.0, -math.sin(a)])
        up = np.array([0.0, 1.0, 0.0])
        return right, up, -back

    def project(self, p: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Points (n,3) -> pixel coordinates (n,2) (x right, y down; pixel centres at +0.5) and depth (n,) (smaller
        is nearer)."""
        right, up, fwd = self.basis
        d = p - self.target
        s = self.size / self.scale
        x = (d @ right) * s + self.size / 2
        y = -(d @ up) * s + self.size / 2
        return np.stack([x, y], axis=1), d @ fwd

    def to_dict(self) -> dict[str, Any]:
        return {"target": [round(float(v), 5) for v in self.target], "yaw_deg": self.yaw_deg, "scale": round(self.scale, 5), "size": self.size}


def head_camera(head: Head, yaw_deg: float = 0.0, size: int = 1024, margin: float = 1.6) -> Camera:
    """A camera framing the head with some room around it (the hair and the neck)."""
    scale = max(head.width * margin, 0.11 * head.height)
    target = head.centre + np.array([0.0, -0.1 * scale, 0.0])
    return Camera(target=target, yaw_deg=yaw_deg, scale=scale, size=size)


# --- rasteriser -----------------------------------------------------------------------------------------------


def rasterise(pts: np.ndarray, depth: np.ndarray, faces: np.ndarray, size: int | tuple[int, int],
              tris: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """Z-buffered triangle rasteriser. pts (n,2) pixel coordinates (centres at +0.5), depth (n,).
    Returns {tri (H,W) int64, -1 empty; bary (H,W,3); z (H,W), inf empty}."""
    hgt, wid = (size, size) if isinstance(size, int) else size
    tri_map = np.full((hgt, wid), -1, dtype=np.int64)
    bary = np.zeros((hgt, wid, 3), dtype=np.float64)
    zbuf = np.full((hgt, wid), np.inf)
    idx = np.arange(len(faces)) if tris is None else np.asarray(tris)
    if len(idx) == 0:
        return {"tri": tri_map, "bary": bary, "z": zbuf}
    P = pts[faces[idx]]  # (k,3,2)
    xmin = np.clip(np.floor(P[:, :, 0].min(1) - 0.5).astype(int), 0, wid)
    xmax = np.clip(np.ceil(P[:, :, 0].max(1) - 0.5).astype(int) + 1, 0, wid)
    ymin = np.clip(np.floor(P[:, :, 1].min(1) - 0.5).astype(int), 0, hgt)
    ymax = np.clip(np.ceil(P[:, :, 1].max(1) - 0.5).astype(int) + 1, 0, hgt)
    for k, t in enumerate(idx):
        x0, x1, y0, y1 = xmin[k], xmax[k], ymin[k], ymax[k]
        if x1 <= x0 or y1 <= y0:
            continue
        (ax, ay), (bx, by), (cx, cy) = P[k]
        area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        if abs(area) < 1e-12:
            continue
        xs = np.arange(x0, x1) + 0.5
        ys = np.arange(y0, y1) + 0.5
        X, Y = np.meshgrid(xs, ys)
        w0 = ((bx - X) * (cy - Y) - (by - Y) * (cx - X)) / area
        w1 = ((cx - X) * (ay - Y) - (cy - Y) * (ax - X)) / area
        w2 = 1.0 - w0 - w1
        inside = (w0 >= -1e-9) & (w1 >= -1e-9) & (w2 >= -1e-9)
        if not inside.any():
            continue
        f = faces[t]
        z = w0 * depth[f[0]] + w1 * depth[f[1]] + w2 * depth[f[2]]
        zb = zbuf[y0:y1, x0:x1]
        win = inside & (z < zb)
        if not win.any():
            continue
        zb[win] = z[win]
        tri_map[y0:y1, x0:x1][win] = t
        bsub = bary[y0:y1, x0:x1]
        bsub[win] = np.stack([w0[win], w1[win], w2[win]], axis=1)
    return {"tri": tri_map, "bary": bary, "z": zbuf}


def interpolate(values: np.ndarray, faces: np.ndarray, ras: dict[str, np.ndarray]) -> np.ndarray:
    """Per-vertex values (n,c) interpolated over a raster (H,W,c); zeros where empty."""
    tri = ras["tri"]
    out = np.zeros(tri.shape + (values.shape[1],))
    m = tri >= 0
    f = faces[tri[m]]
    b = ras["bary"][m]
    out[m] = b[:, 0:1] * values[f[:, 0]] + b[:, 1:2] * values[f[:, 1]] + b[:, 2:3] * values[f[:, 2]]
    return out


def sample(img: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Bilinear sample of img (H,W,c) at pixel coordinates (centres at +0.5), clamped at the border."""
    h, w = img.shape[:2]
    fx = np.clip(x - 0.5, 0, w - 1)
    fy = np.clip(y - 0.5, 0, h - 1)
    x0 = np.floor(fx).astype(int)
    y0 = np.floor(fy).astype(int)
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)
    ax = (fx - x0)[..., None]
    ay = (fy - y0)[..., None]
    top = img[y0, x0] * (1 - ax) + img[y0, x1] * ax
    bot = img[y1, x0] * (1 - ax) + img[y1, x1] * ax
    return top * (1 - ay) + bot * ay


def uv_pixels(uv: np.ndarray, size: int) -> np.ndarray:
    """UV (n,2) -> texture pixel coordinates (centres at +0.5), rows as imaging.rasterise_uv lays them out."""
    return np.stack([uv[:, 0] * size, (1.0 - uv[:, 1]) * size], axis=1)


# --- render ---------------------------------------------------------------------------------------------------


def view_raster(mesh: dict[str, np.ndarray], cam: Camera) -> dict[str, np.ndarray]:
    pts, depth = cam.project(mesh["verts"])
    s = cam.size
    P = pts[mesh["faces"]]
    keep = np.where((P[:, :, 0].max(1) >= 0) & (P[:, :, 0].min(1) <= s) & (P[:, :, 1].max(1) >= 0) & (P[:, :, 1].min(1) <= s))[0]
    ras = rasterise(pts, depth, mesh["faces"], s, keep)
    ras["pts"], ras["depth"] = pts, depth
    return ras


def render_view(mesh: dict[str, np.ndarray], texture: np.ndarray, cam: Camera, background: float = 0.25) -> dict[str, np.ndarray]:
    """{image (S,S,3) float 0..1, mask (S,S) bool, facing (S,S) cosine between the surface and the view, raster}."""
    ras = view_raster(mesh, cam)
    m = ras["tri"] >= 0
    tex_px = uv_pixels(mesh["uv"], texture.shape[0])
    t = interpolate(tex_px, mesh["faces"], ras)
    img = np.full((cam.size, cam.size, 3), background, dtype=np.float64)
    img[m] = sample(texture, t[m][:, 0], t[m][:, 1])
    n = interpolate(mesh["vnormals"], mesh["faces"], ras)
    n /= np.maximum(np.linalg.norm(n, axis=2, keepdims=True), 1e-12)
    facing = np.where(m, -(n @ cam.basis[2]), 0.0)
    return {"image": img, "mask": m, "facing": facing, "raster": ras}


# --- image helpers --------------------------------------------------------------------------------------------


def box_blur(a: np.ndarray, r: int) -> np.ndarray:
    """Box blur with radius r along both axes (edges clamped), any trailing channels."""
    if r <= 0:
        return a.astype(np.float64)
    out = a.astype(np.float64)
    for axis in (0, 1):
        pad = [(0, 0)] * out.ndim
        pad[axis] = (r + 1, r)
        p = np.pad(out, pad, mode="edge")
        c = np.cumsum(p, axis=axis)
        n = out.shape[axis]
        hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
        lo = np.take(c, np.arange(0, n), axis=axis)
        out = (hi - lo) / (2 * r + 1)
    return out


def blur(a: np.ndarray, sigma: float) -> np.ndarray:
    """About a Gaussian blur: three box blurs."""
    r = max(0, int(round(math.sqrt(12 * sigma * sigma / 3 + 1) / 2 - 0.5))) if sigma > 0 else 0
    out = a
    for _ in range(3):
        out = box_blur(out, r)
    return out


def masked_blur(a: np.ndarray, mask: np.ndarray, sigma: float) -> np.ndarray:
    """Blur of a counting only the pixels in mask (normalised), so the background does not bleed in."""
    w = mask.astype(np.float64)
    num = blur(a * (w[..., None] if a.ndim == 3 else w), sigma)
    den = blur(w, sigma)
    den = den[..., None] if a.ndim == 3 else den
    return np.where(den > 1e-6, num / np.maximum(den, 1e-6), 0.0)


def luminance(img: np.ndarray) -> np.ndarray:
    return img[..., 0] * 0.2126 + img[..., 1] * 0.7152 + img[..., 2] * 0.0722


def sharpness(img: np.ndarray, mask: np.ndarray) -> float:
    """Fine detail inside mask: the standard deviation of the luminance minus a 2 px blur, times 100. A smeared face
    scores low; real eyes, brows and lips score high. Compare numbers only at the same picture size and framing."""
    if mask.sum() < 50:
        return 0.0
    lum = luminance(img)
    hp = lum - blur(lum, 2.0)
    inner = erode(mask, 4)
    if inner.sum() < 50:
        inner = mask
    return round(float(hp[inner].std()) * 100.0, 3)


def erode(mask: np.ndarray, r: int) -> np.ndarray:
    if r <= 0:
        return mask.copy()
    return box_blur(mask.astype(np.float64), r) > 1.0 - 1e-6


def dilate_mask(mask: np.ndarray, r: int) -> np.ndarray:
    if r <= 0:
        return mask.copy()
    return box_blur(mask.astype(np.float64), r) > 1e-6


def smoothstep(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - e0) / max(e1 - e0, 1e-9), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def to_uint8(img: np.ndarray) -> np.ndarray:
    return (np.clip(img, 0, 1) * 255).round().astype(np.uint8)


def read_rgb(path: Path | str, size: int | None = None) -> np.ndarray:
    im = Image.open(path).convert("RGB")
    if size and im.size != (size, size):
        im = im.resize((size, size), Image.LANCZOS)
    return np.asarray(im, dtype=np.float64) / 255.0


def save_rgb(img: np.ndarray, path: Path | str) -> Path:
    Image.fromarray(to_uint8(img), "RGB").save(path)
    return Path(path)


def resize(img: np.ndarray, size: int, resample: int = Image.BOX) -> np.ndarray:
    if img.ndim == 2:
        im = Image.fromarray(img.astype(np.float32), "F").resize((size, size), resample)
        return np.asarray(im, dtype=np.float64)
    return np.asarray(Image.fromarray(to_uint8(img)).resize((size, size), resample), dtype=np.float64) / 255.0


# --- the face region and landmarks ----------------------------------------------------------------------------


def grow(seed: np.ndarray, allowed: np.ndarray, max_steps: int = 4000) -> np.ndarray:
    """The part of `allowed` connected (4-neighbour) to `seed`."""
    cur = seed & allowed
    for _ in range(max_steps):
        nxt = cur.copy()
        nxt[1:] |= cur[:-1]
        nxt[:-1] |= cur[1:]
        nxt[:, 1:] |= cur[:, :-1]
        nxt[:, :-1] |= cur[:, 1:]
        nxt &= allowed
        if (nxt == cur).all():
            break
        cur = nxt
    return cur


def fill_holes(mask: np.ndarray) -> np.ndarray:
    """mask plus every hole in it (background not connected to the border)."""
    border = np.zeros_like(mask)
    border[0, :] = border[-1, :] = True
    border[:, 0] = border[:, -1] = True
    outside = grow(border & ~mask, ~mask)
    return ~outside


def to_lab(img: np.ndarray) -> np.ndarray:
    """sRGB 0..1 -> CIE Lab (D65)."""
    c = np.where(img <= 0.04045, img / 12.92, ((np.maximum(img, 0) + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = c @ m.T / np.array([0.9505, 1.0, 1.089])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def find_nose(z: np.ndarray, m: np.ndarray, pos: np.ndarray, cam: Camera, head: Head) -> tuple[int, int] | None:
    """The nose tip in a front render: the point in the middle of the head (between 6 and 16 percent of the height
    below the top) that stands out most from the surface around it, measured a twentieth of the picture away up,
    down, left and right, on unbroken surface only (a chin over the neck is an edge, not a bump). A fringe of hair is
    further forward than the nose but broad and round, so it stands out less."""
    S = cam.size
    band_ = m & (pos[..., 1] >= head.top - 0.16 * head.height) & (pos[..., 1] <= head.top - 0.06 * head.height)
    band_ &= np.abs(pos[..., 0] - head.centre[0]) <= 0.12 * head.width
    if band_.sum() < 50:
        return None
    jump = 0.1 * cam.scale
    zf = np.where(m, z, np.nan)
    ok = band_.copy()
    total = np.zeros_like(zf)
    for frac in (0.05, 0.08):
        d = max(2, int(frac * S))
        pad = np.pad(zf, d, mode="constant", constant_values=np.nan)
        diffs = []
        for dy, dx in ((-d, 0), (d, 0), (0, -d), (0, d)):
            nb = pad[d + dy: d + dy + zf.shape[0], d + dx: d + dx + zf.shape[1]]
            diffs.append(nb - zf)
        D = np.stack(diffs)
        ok &= np.all(np.isfinite(D), axis=0) & np.all(D > 0, axis=0) & np.all(D < jump, axis=0)
        total += np.nan_to_num(D.mean(axis=0))
    score = np.where(ok, total, -np.inf)
    if not np.isfinite(score.max()):
        return None
    iy, ix = np.unravel_index(int(np.argmax(score)), score.shape)
    return int(ix), int(iy)


FACE_TOP = 0.65  # the face reaches at most this share of (head top - nose tip) above the nose tip
FACE_DEPTH = 0.6  # ... and lies at most this share of it behind the nose tip
FACE_DEPTH_FROM = 0.25  # ... except this share of it and more below the nose tip (the jaw and neck, as before)


def face_bounds(z: np.ndarray, pos: np.ndarray, head: Head, nose: tuple[int, int]) -> np.ndarray:
    """Where the face can be, from the shape alone, so a skin-coloured scalp or ears cannot join it (a bald head: the
    whole head is skin, and the region grew over the scalp and both ears, twice the face's width). Measured in L, the
    height from the nose tip to the top of the head (about the same with and without hair: 0.085 m on both bake-off
    knights): the face goes up at most FACE_TOP L above the nose tip (the haired knight's skin reaches 0.64 L, the
    bald knight's brows 0.38 L), and above the jaw at most FACE_DEPTH L behind it (the cheeks lie under 0.5 L back,
    the ears from 0.7 L). Below the jaw nothing is cut: the neck under a chin lies deep but always joined the face."""
    ix, iy = nose
    ny, nz = float(pos[iy, ix, 1]), float(z[iy, ix])
    L = head.top - ny
    if not np.isfinite(nz) or L <= 0:
        return np.ones(z.shape, dtype=bool)
    up = (pos[..., 1] - ny) / L
    deep = np.nan_to_num((z - nz) / L, nan=np.inf) > FACE_DEPTH
    return (up <= FACE_TOP) & (~deep | (up < -FACE_DEPTH_FROM))


def find_face(view: dict[str, Any], mesh: dict[str, np.ndarray], cam: Camera, head: Head,
              skin_tolerance: float = 16.0, nose: tuple[int, int] | None = None) -> dict[str, Any]:
    """The face in a front render: the nose tip (the nearest visible point in the upper head, or `nose` when given),
    the skin colour around it, and the skin-coloured area joined to it with its holes (eyes, mouth) filled. Returns
    {found, reason, nose (x, y), skin (Lab), region (S,S) bool, box (x0, y0, x1, y1), width_px}."""
    S = cam.size
    ras = view["raster"]
    m = view["mask"]
    pos = interpolate(mesh["verts"], mesh["faces"], ras)
    if nose is None:
        nose = find_nose(ras["z"], m, pos, cam, head)
    if nose is None:
        return {"found": False, "reason": "no_head"}
    ix, iy = nose
    r = max(4, S // 64)
    win = (slice(max(0, iy - r), iy + r + 1), slice(max(0, ix - r), ix + r + 1))
    lab = to_lab(view["image"])
    skin = np.median(lab[win][m[win]], axis=0)
    dist = np.linalg.norm((lab - skin) * np.array([0.5, 1.0, 1.0]), axis=-1)
    allowed = m & (dist < skin_tolerance) & (pos[..., 1] >= head.top - 0.16 * head.height)
    allowed &= face_bounds(ras["z"], pos, head, nose)
    seed = np.zeros_like(m)
    seed[win] = True
    region = grow(seed, allowed)
    # Close and fill: eyes, brows and lips are not skin coloured but belong to the face.
    close = max(2, S // 80)
    region = erode(dilate_mask(region, close), close) & m
    region = fill_holes(region) & m
    ys, xs = np.where(region)
    if len(xs) < 200:
        return {"found": False, "reason": "no_skin", "nose": (int(ix), int(iy))}
    x0, x1 = np.percentile(xs, [2, 98])
    y0, y1 = np.percentile(ys, [2, 98])
    region, added = close_eye_gaps(region, m, lab, skin, (int(ix), int(iy)), (int(x0), int(y0), int(x1), int(y1)))
    return {"found": True, "reason": None, "nose": (int(ix), int(iy)), "skin": [round(float(v), 2) for v in skin], "region": region,
            "box": (int(x0), int(y0), int(x1), int(y1)), "width_px": float(x1 - x0), "area_px": int(region.sum()),
            "eye_gap_px": added}


_SHAPE_MARKS: dict[str, Any] = {}


def shape_picture(view: dict[str, Any]) -> np.ndarray:
    """The head as a plain lit surface from its shape alone (the view's facing, one skin-ish colour, whatever the
    texture holds): a picture a face finder can read when the texture is still the soft, mottled one the model came with."""
    img = np.clip(0.15 + 0.85 * view["facing"], 0.0, 1.0)[..., None] * np.array([0.85, 0.72, 0.62])
    return np.where(view["mask"][..., None], img, 0.25)


def shape_marks(view: dict[str, Any]) -> dict[str, Any] | None:
    """MediaPipe's eyes and mouth on the shape-only picture (vgac/lipmarks.py, on the processor), or None: no package or
    model, or no face found. Remembered per picture, so the passes that look at the same head ask once."""
    import hashlib

    from . import lipmarks

    if not lipmarks.available():
        return None
    img = shape_picture(view)
    key = hashlib.md5(to_uint8(img).tobytes()).hexdigest()
    if key not in _SHAPE_MARKS:
        from . import facemarks

        if len(_SHAPE_MARKS) >= 16:
            _SHAPE_MARKS.pop(next(iter(_SHAPE_MARKS)))
        try:
            _SHAPE_MARKS[key] = facemarks.local(img)
        except Exception:  # noqa: BLE001  (no finder: the shape's nose stands)
            _SHAPE_MARKS[key] = None
    return _SHAPE_MARKS[key]


def find_face_checked(view: dict[str, Any], mesh: dict[str, np.ndarray], cam: Camera, head: Head,
                      skin_tolerance: float = 16.0) -> dict[str, Any]:
    """find_face, with the nose tip checked against MediaPipe's eyes and mouth read off the head's shape. The shape alone
    puts the nose on loose curls, a glasses rim, the brow or the chin on some heads (the stored runs W2-clothed, W2-underwear
    and P1-photo, 60 to 90 mm off, and everything the pass measures from the nose goes with it). When the nose is not
    between the eyes and mouth the finder found, the nose tip is taken as the nearest surface point about 60 percent of the
    way from the eyes' middle to the mouth, and the face is found again from there ("nose_from": "mediapipe", the first
    nose kept as "nose_by_shape"). Without the finder, or when it finds no face or the new nose does not fit either, the
    first finding stands."""
    from . import facemarks

    info = find_face(view, mesh, cam, head, skin_tolerance)
    try:
        m = shape_marks(view)
        if m is None:
            return info
        eyes = np.array(m["eyes"], dtype=np.float64)
        # the face's width as the eyes give it (about 0.45 of it between them), not the grown skin region's: on a texture
        # that is still mottled the region can be a sliver, and a nose is then never "between the eyes and mouth"
        width = max(float(np.linalg.norm(eyes[1] - eyes[0])) / 0.45, 1.0)

        def holds(nose_xy: Any) -> bool:
            return facemarks.plausible(m, {"nose": tuple(nose_xy), "width_px": width})

        if info.get("found") and holds(info["nose"]):
            return info
        mid, mouth = eyes.mean(axis=0), np.array(m["mouth"], dtype=np.float64)
        guess = mid + 0.6 * (mouth - mid)
        S = cam.size
        r = max(2, int(0.15 * float(np.linalg.norm(eyes[1] - eyes[0]))))
        x0, x1 = int(max(0, guess[0] - r)), int(min(S, guess[0] + r + 1))
        y0, y1 = int(max(0, guess[1] - r)), int(min(S, guess[1] + r + 1))
        z = np.where(view["mask"], view["raster"]["z"], np.inf)[y0:y1, x0:x1]
        if z.size == 0 or not np.isfinite(z).any():
            return info
        iy, ix = np.unravel_index(int(np.argmin(z)), z.shape)
        again = find_face(view, mesh, cam, head, skin_tolerance, nose=(int(x0 + ix), int(y0 + iy)))
        if not again.get("found") or not holds(again["nose"]):
            return info
        again["nose_from"] = "mediapipe"
        again["nose_by_shape"] = info.get("nose")
        return again
    except Exception:  # noqa: BLE001  (a finder that fails changes nothing)
        return info


def hair_colour_above(lab: np.ndarray, m: np.ndarray, region: np.ndarray, nose: tuple[int, int],
                      box: tuple[int, int, int, int]) -> np.ndarray | None:
    """The middle colour (Lab) of the head above the face: the model's surface outside the face region, in the top
    quarter of the face's box and up, within 0.8 face widths of the nose. None when there is too little of it."""
    S = m.shape[0]
    x0, y0, x1, y1 = box
    w = float(x1 - x0)
    Y, X = np.mgrid[0:S, 0:S]
    above = m & ~dilate_mask(region, max(2, int(w / 40))) & (Y < y0 + 0.25 * (y1 - y0)) & (np.abs(X - nose[0]) < 0.8 * w)
    if above.sum() < 200:
        return None
    return np.median(lab[above], axis=0)


def close_eye_gaps(region: np.ndarray, m: np.ndarray, lab: np.ndarray, skin: np.ndarray, nose: tuple[int, int],
                   box: tuple[int, int, int, int], margin: float = 6.0) -> tuple[np.ndarray, int]:
    """The face region with the eye smudges that touch the hair put back. find_face fills the holes in the skin (eyes,
    brows, lips), but an eye smudge that runs into a lock of hair is no hole: it joins the hair, and the eye was left
    out of the face (the second knight: her right eye stayed a dark smudge). Here the hair is a wall too: what the skin
    and the hair together enclose, in the band of the eyes and brows, joins the face, except what is hair-coloured
    itself (nearer the hair colour than the skin tone by margin, Lab with lightness half), so hair that covers the face
    on the model stays out of it. Returns (region, how many pixels were added)."""
    hair = hair_colour_above(lab, m, region, nose, box)
    if hair is None:
        return region, 0
    k = np.array([0.5, 1.0, 1.0])
    skin = np.asarray(skin, dtype=np.float64)
    if np.linalg.norm((hair - skin) * k) < 2 * margin:
        return region, 0
    d_h = np.linalg.norm((lab - hair) * k, axis=-1)
    d_s = np.linalg.norm((lab - skin) * k, axis=-1)
    hairy = m & (d_h < d_s - margin)
    enclosed = fill_holes(region | hairy) & m & ~hairy & ~region
    S = m.shape[0]
    x0, y0, x1, y1 = box
    w = float(x1 - x0)
    Y, X = np.mgrid[0:S, 0:S]
    band_ = (np.abs(X - nose[0]) <= 0.45 * w) & (Y >= nose[1] - 0.5 * w) & (Y <= nose[1] - 0.02 * w)
    add = enclosed & band_
    # only pieces that touch the face (a gap in the hair far from the skin is not an eye)
    add = grow(add & dilate_mask(region, 1), add) if add.any() else add
    return region | add, int(add.sum())


def features_box(info: dict[str, Any]) -> np.ndarray:
    """Where the eyes, brows, nose and mouth can be, wider than inner_face (42 percent of the face's width to each
    side of the nose, from 36 percent above it to 30 percent below): nothing there is ever replaced by a fill."""
    region = info["region"]
    nx, ny = info["nose"]
    w = float(info["width_px"])
    Y, X = np.mgrid[0:region.shape[0], 0:region.shape[1]]
    return (np.abs(X - nx) <= 0.42 * w) & (Y >= ny - 0.36 * w) & (Y <= ny + 0.3 * w)


def inner_face(info: dict[str, Any]) -> np.ndarray:
    """The part of the face region that holds the eyes, nose and mouth (around the nose tip: 30 percent of the face's
    width to each side, from 32 percent above it to 25 percent below). The fit is judged there only, so the jaw line
    and the hairline, which the picture and the model draw differently, do not pull the eyes off their places."""
    region = info["region"]
    nx, ny = info["nose"]
    w = float(info["width_px"])
    Y, X = np.mgrid[0:region.shape[0], 0:region.shape[1]]
    box = (np.abs(X - nx) <= 0.3 * w) & (Y >= ny - 0.32 * w) & (Y <= ny + 0.25 * w)
    inner = region & box
    return inner if inner.sum() >= 0.2 * region.sum() else region


def landmarks(img: np.ndarray, info: dict[str, Any]) -> dict[str, Any] | None:
    """Eyes and mouth of a front face picture: MediaPipe's when a face pass has set a card and it finds the model's
    face (vgac/facemarks.py), else darkest_landmarks."""
    from . import facemarks

    m = facemarks.find(img, info)
    if m is None:
        return darkest_landmarks(img, info)
    return add_mouth_corners(m, img, info)


def add_mouth_corners(lm: dict[str, Any], img: np.ndarray, info: dict[str, Any]) -> dict[str, Any]:
    """lm (MediaPipe's eyes and lips centroid, vgac/facemarks.py) with "mouth_corners" ([left, right] in the picture) from
    the lip points of the mediapipe Python package (vgac/lipmarks.py), so fit_landmarks can fit the mouth's width too.
    lm itself is returned when the package or its model is missing, no face is found, or the lips found are not the
    ones lm has (their middle more than a tenth of the face's width from lm's mouth): then the fit is as before."""
    from . import lipmarks

    if "mouth_corners" in lm or not lipmarks.available():
        return lm
    lp = lipmarks.find(img)
    if lp is None:
        return lm
    w = float(info["width_px"])
    mid = (np.array(lp["left"]) + np.array(lp["right"])) / 2
    if np.hypot(*(mid - np.array(lm["mouth"]))) > 0.1 * w or lipmarks.width_px(lp) < 0.12 * w:
        return lm
    out = dict(lm)  # the finder's cache holds lm
    out["mouth_corners"] = [lp["left"], lp["right"]]
    return out


def landmark_pair(a: np.ndarray, b: np.ndarray, info: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Landmarks of two pictures found the same way (MediaPipe's lips centre and the darkest lip line are not the same
    point): when only one was found by MediaPipe, both by the darkest spots."""
    la, lb = landmarks(a, info), landmarks(b, info)
    if la and lb and la.get("how") != lb.get("how"):
        la, lb = darkest_landmarks(a, info), darkest_landmarks(b, info)
    return la, lb


def darkest_landmarks(img: np.ndarray, info: dict[str, Any]) -> dict[str, Any] | None:
    """Eyes and mouth of a front face picture, searched around the model's nose tip (info from find_face; the picture
    is framed like the render, so the same places hold its features within a few percent): each eye the centre of the
    darkest 2 percent of its side of the eye band (pupils, lashes), the mouth the darkest row of the band under the
    nose (the line between the lips) and the centre of its dark pixels. None when a band falls outside the picture."""
    nx, ny = info["nose"]
    w = float(info["width_px"])
    lum = blur(luminance(img), max(1.0, w / 250))
    # only inside the face (a little shrunk): the hair's dark edges are not eyes
    lum = np.where(erode(info["region"], max(1, int(w / 60))), lum, np.inf)
    H, W = lum.shape
    y0, y1 = int(ny - 0.36 * w), int(ny - 0.06 * w)
    if y0 < 0 or y1 <= y0:
        return None
    eyes = []
    for xa, xb in ((nx - 0.42 * w, nx - 0.06 * w), (nx + 0.06 * w, nx + 0.42 * w)):
        xa, xb = max(0, int(xa)), min(W, int(xb))
        sub = lum[y0:y1, xa:xb]
        fin = np.isfinite(sub)
        if fin.sum() < 50:
            return None
        thr = np.percentile(sub[fin], 2)
        ys, xs = np.where(sub <= thr)
        eyes.append((float(xs.mean() + xa + 0.5), float(ys.mean() + y0 + 0.5)))
    m0, m1 = int(ny + 0.06 * w), min(H, int(ny + 0.34 * w))
    mx0, mx1 = max(0, int(nx - 0.2 * w)), min(W, int(nx + 0.2 * w))
    sub = lum[m0:m1, mx0:mx1]
    fin = np.isfinite(sub)
    rows_ok = fin.mean(axis=1) > 0.6
    if fin.sum() < 50 or not rows_ok.any():
        return None
    means = np.array([sub[i][fin[i]].mean() if rows_ok[i] else np.inf for i in range(sub.shape[0])])
    row = int(np.argmin(means))
    line = sub[max(0, row - 2): row + 3]
    lf = np.isfinite(line)
    thr = np.percentile(line[lf], 30)
    ys, xs = np.where(lf & (line <= thr))
    mouth = (float(xs.mean() + mx0 + 0.5), float(ys.mean() + max(0, row - 2) + m0 + 0.5))
    return {"eyes": eyes, "mouth": mouth}


def fit_landmarks(src: dict[str, Any], dst: dict[str, Any], centre: tuple[float, float], limit: float = 0.2) -> dict[str, Any] | None:
    """A scale and a shift that put the picture's eye line and mouth on the model's: the scale from the distance
    between the eye line and the mouth, the shift from the middle of the eyes and the mouth. The spacing of the eyes
    stays the picture's: the 3D builder models no eye sockets at this triangle count (the nose and the mouth line are
    what the shape has), and its blurred eye smudges are only a rough guess. None when the scale would be more than
    `limit` away from 1 (then the points are not the same features). residual_px: how far the eyes still differ.

    When both have "mouth_corners" ([left, right], add_mouth_corners) the sideways scale is the model's mouth width
    over the picture's instead, kept within 15 percent of the upright scale (a corner found on a crease is a rough
    guess too) and within `limit` of 1, else equal to the upright one as before. corner_residual_px: how far the
    corners still differ (both axes, the largest of the two)."""
    def parts(lm: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
        e = np.array(lm["eyes"], dtype=np.float64)
        return e.mean(axis=0), np.array(lm["mouth"], dtype=np.float64)

    se, sm = parts(src)
    de, dm = parts(dst)
    hs, hd = sm[1] - se[1], dm[1] - de[1]
    if hs <= 1 or hd <= 1:
        return None
    k = float(hd / hs)
    if not (1 - limit <= k <= 1 + limit):
        return None
    kx = k
    cs, cd = src.get("mouth_corners"), dst.get("mouth_corners")
    if cs and cd:
        ws = abs(cs[1][0] - cs[0][0])
        wd = abs(cd[1][0] - cd[0][0])
        if ws > 1 and wd > 1 and 1 - limit <= wd / ws <= 1 + limit:
            kx = float(np.clip(wd / ws, k / 1.15, k * 1.15))
    c = np.array(centre, dtype=np.float64)
    s2 = np.array([kx, k])
    mid_s, mid_d = (se + sm) / 2, (de + dm) / 2
    t = mid_d - (c + s2 * (mid_s - c))
    if cs and cd:
        # the mouth's own middle decides the sideways shift (the eyes' places are the rough guess here)
        ms, md = np.mean(np.array(cs, dtype=np.float64)[:, 0]), np.mean(np.array(cd, dtype=np.float64)[:, 0])
        t[0] = md - (c[0] + kx * (ms - c[0]))
    pe = c + s2 * (np.array(src["eyes"]) - c) + t
    res = float(np.abs(pe - np.array(dst["eyes"])).max())
    out = {"sx": round(kx, 4), "sy": round(k, 4), "tx": round(float(t[0]), 2), "ty": round(float(t[1]), 2),
           "centre": (float(c[0]), float(c[1])), "residual_px": round(res, 2)}
    if cs and cd:
        pc = c + s2 * (np.array(cs, dtype=np.float64) - c) + t
        out["corner_residual_px"] = round(float(np.abs(pc - np.array(cd, dtype=np.float64)).max()), 2)
    return out


def band(img: np.ndarray, lo: float, hi: float) -> np.ndarray:
    lum = luminance(img) if img.ndim == 3 else img
    return blur(lum, lo) - blur(lum, hi)


def scale_image(img: np.ndarray, sx: float, sy: float, tx: float, ty: float, centre: tuple[float, float]) -> np.ndarray:
    """out(p) = img(c + (p - c - t) / s): img scaled about centre by (sx, sy), then moved by (tx, ty) pixels."""
    h, w = img.shape[:2]
    X, Y = np.meshgrid(np.arange(w) + 0.5, np.arange(h) + 0.5)
    cx, cy = centre
    xs = cx + (X - cx - tx) / sx
    ys = cy + (Y - cy - ty) / sy
    out = sample(img if img.ndim == 3 else img[..., None], xs, ys)
    return out if img.ndim == 3 else out[..., 0]


def _ncc_shift(dst: np.ndarray, src: np.ndarray, mask: np.ndarray, max_shift: int) -> tuple[float, int, int]:
    """Best translation d (pixels, within max_shift) for src(p - d) against dst(p) over mask, by normalised
    cross-correlation (dst is zero outside mask already)."""
    h, w = dst.shape
    H, W = 2 * h, 2 * w
    e_dst = float((dst * dst).sum())
    num = np.fft.irfft2(np.fft.rfft2(dst, (H, W)) * np.conj(np.fft.rfft2(src, (H, W))), (H, W))
    den = np.fft.irfft2(np.fft.rfft2(mask, (H, W)) * np.conj(np.fft.rfft2(src * src, (H, W))), (H, W))
    # num[d] = sum_p dst(p) src(p - d): src moved by +d lines up.
    rng = np.arange(-max_shift, max_shift + 1)
    sub_n = num[np.ix_(rng % H, rng % W)]
    sub_d = den[np.ix_(rng % H, rng % W)]
    score = sub_n / np.sqrt(np.maximum(e_dst * sub_d, 1e-12))
    iy, ix = np.unravel_index(int(np.argmax(score)), score.shape)
    return float(score[iy, ix]), int(rng[ix]), int(rng[iy])


def align(src: np.ndarray, dst: np.ndarray, region: np.ndarray, work: int = 256,
          scales: tuple[float, float] = (0.82, 1.18), max_aspect: float = 0.08) -> dict[str, Any]:
    """The scale (sx, sy) and shift that lay picture src over picture dst inside region, found on band-passed
    luminance (eyes, brows, nostrils and lips, not flat colour). Returns {sx, sy, tx, ty, centre, score} in the
    pixels of the full-size pictures; score is the normalised correlation inside the region (1: the same features)."""
    S = dst.shape[0]
    best: dict[str, Any] = {"score": -2.0}
    centre_full = None
    for level, (wk, lo, hi, steps, shift) in enumerate(((work, 1.0, 6.0, None, int(0.1 * work)), (work * 2, 1.5, 8.0, None, 4))):
        k = S / wk
        d = band(resize(dst, wk), lo, hi)
        s_img = resize(src, wk)
        msk = (resize(region.astype(np.float64), wk) > 0.5).astype(np.float64)
        ys, xs = np.where(msk > 0)
        if centre_full is None:
            centre_full = ((xs.mean() + 0.5) * k, (ys.mean() + 0.5) * k)
        c = (centre_full[0] / k, centre_full[1] / k)
        d = (d - d[msk > 0].mean()) * msk
        if level == 0:
            grid = [(sx, sy, 0.0, 0.0) for sx in np.arange(scales[0], scales[1] + 1e-6, 0.03) for sy in np.arange(scales[0], scales[1] + 1e-6, 0.03)]
        else:
            grid = [(best["sx"] + i * 0.01, best["sy"] + j * 0.01, best["tx"] / k, best["ty"] / k) for i in range(-3, 4) for j in range(-3, 4)]
        # a face is not stretched more one way than the other by more than max_aspect: that would change its look
        grid = [g for g in grid if abs(g[0] / g[1] - 1.0) <= max_aspect]
        found: dict[str, Any] = {"score": -2.0}
        for sx, sy, tx, ty in grid:
            b = band(scale_image(s_img, sx, sy, tx, ty, c), lo, hi)
            score, dx, dy = _ncc_shift(d, b, msk, shift)
            if score > found["score"]:
                found = {"score": score, "sx": float(sx), "sy": float(sy), "tx": (tx + dx) * k, "ty": (ty + dy) * k}
        if level == 0:
            coarse = found["score"]
        best = found
    return {"sx": round(best["sx"], 4), "sy": round(best["sy"], 4), "tx": round(best["tx"], 2), "ty": round(best["ty"], 2),
            "centre": (float(centre_full[0]), float(centre_full[1])), "score": round(best["score"], 4), "coarse_score": round(coarse, 4)}


def apply_alignment(img: np.ndarray, a: dict[str, Any]) -> np.ndarray:
    return scale_image(img, a["sx"], a["sy"], a["tx"], a["ty"], a["centre"])


def compose_alignment(first: dict[str, Any], then: dict[str, Any]) -> dict[str, Any]:
    """One alignment that does `first` and then `then` (so the picture is resampled once): about first's centre, the
    scales multiplied and the shift worked out so apply_alignment(img, result) equals
    apply_alignment(apply_alignment(img, first), then)."""
    c1 = np.array(first["centre"], dtype=np.float64)
    c2 = np.array(then["centre"], dtype=np.float64)
    s1 = np.array([first["sx"], first["sy"]], dtype=np.float64)
    s2 = np.array([then["sx"], then["sy"]], dtype=np.float64)
    t1 = np.array([first["tx"], first["ty"]], dtype=np.float64)
    t2 = np.array([then["tx"], then["ty"]], dtype=np.float64)
    s = s1 * s2
    t = c2 + t2 - s2 * (c2 - c1 - t1) - c1
    return {"sx": round(float(s[0]), 4), "sy": round(float(s[1]), 4), "tx": round(float(t[0]), 2), "ty": round(float(t[1]), 2),
            "centre": (float(c1[0]), float(c1[1]))}


def feature_match(a: np.ndarray, b: np.ndarray, region: np.ndarray, lo: float = 3.0, hi: float = 12.0) -> float:
    """How well the features of two pictures sit on each other inside region: the correlation of their band-passed
    luminance (1: the same features at the same places, 0: unrelated). lo and hi are in pixels at 1024."""
    k = a.shape[0] / 1024.0
    inner = erode(region, max(1, int(0.01 * a.shape[0])))
    if inner.sum() < 100:
        return 0.0
    x = band(a, lo * k, hi * k)[inner]
    y = band(b, lo * k, hi * k)[inner]
    x = x - x.mean()
    y = y - y.mean()
    return round(float((x * y).sum() / math.sqrt(max((x * x).sum() * (y * y).sum(), 1e-12))), 4)


def tone_match(new: np.ndarray, old: np.ndarray, region: np.ndarray, sigma_frac: float = 0.1) -> np.ndarray:
    """new with its broad light and colour (blurred at sigma_frac of the picture, inside region) replaced by old's:
    fine detail from the new picture, overall tone from the surrounding texture, so no tone jump shows at the edge."""
    s = sigma_frac * new.shape[0]
    return np.clip(new + masked_blur(old, region, s) - masked_blur(new, region, s), 0.0, 1.0)


def tone_to_reference(pic: np.ndarray, ref: np.ndarray, ref_mask: np.ndarray, sigma_frac: float = 0.03) -> np.ndarray:
    """pic with its colour and tone moved to ref's where ref is to be trusted (ref_mask: what the front painting
    covers, seen from this side): the difference ref - pic is blurred over ref_mask and carried outward from it; far
    from any trusted pixel the mean difference is used. Fine detail stays pic's."""
    s = sigma_frac * pic.shape[0]
    m = ref_mask.astype(np.float64)
    if m.sum() < 50:
        return pic.copy()
    diff = ref - pic
    num = blur(diff * m[..., None], s)
    den = blur(m, s)[..., None]
    mean = (diff * m[..., None]).sum(axis=(0, 1)) / m.sum()
    t = np.clip(den / 0.3, 0.0, 1.0)  # how much trusted surface is near
    local = np.where(den > 1e-6, num / np.maximum(den, 1e-6), mean)
    return np.clip(pic + local * t + mean * (1 - t), 0.0, 1.0)


def silhouette_fit(pic_alpha: np.ndarray, render_mask: np.ndarray, rows: tuple[int, int],
                   scales: tuple[float, float] = (0.85, 1.15)) -> dict[str, Any]:
    """Line a side picture up with a render of the model from the same side by their outlines (the profile of the
    forehead, nose, lips and chin and the back of the head; the model has no eye sockets or ears to go on), inside the
    rows given (the head, not the shoulders). Returns the alignment (as align) plus iou: how well the two head
    outlines overlap after it (1 is the same shape)."""
    S = render_mask.shape[0]
    band_rows = np.zeros((S, S), dtype=bool)
    band_rows[rows[0]:rows[1]] = True
    a_img = np.repeat(pic_alpha[..., None].astype(np.float64), 3, axis=2)
    r_img = np.repeat(render_mask[..., None].astype(np.float64), 3, axis=2)
    region = dilate_mask(render_mask | (pic_alpha > 0.5), max(2, S // 40)) & band_rows
    a = align(a_img, r_img, region, work=128, scales=scales, max_aspect=0.06)
    moved = apply_alignment(a_img, a)[..., 0] > 0.5
    inter = (moved & render_mask & band_rows).sum()
    union = ((moved | render_mask) & band_rows).sum()
    a["iou"] = round(float(inter) / float(max(union, 1)), 4)
    return a


def side_skin(old: np.ndarray, mask: np.ndarray, skin_lab: Any, hair_lab: Any, top_rows: int, margin: float = 6.0,
              tolerance: float = 22.0) -> dict[str, np.ndarray]:
    """Skin and hair on a side render of the model's old texture, by colour distance to the front's skin tone and hair
    colour (Lab, lightness half). Hair is the hair-coloured surface joined to the hair in the first top_rows rows of
    the picture; skin is near the skin tone and not hair."""
    lab = to_lab(np.clip(old, 0, 1))
    k = np.array([0.5, 1.0, 1.0])
    d_s = np.linalg.norm((lab - np.asarray(skin_lab)) * k, axis=-1)
    d_h = np.linalg.norm((lab - np.asarray(hair_lab)) * k, axis=-1)
    hairy = mask & (d_h < d_s - margin)
    seed = hairy.copy()
    seed[top_rows:] = False
    hair = grow(seed, hairy)
    skin = mask & ~hair & (d_s < tolerance)
    return {"skin": skin, "hair": hair}


def tone_match_local(new: np.ndarray, old: np.ndarray, region: np.ndarray, weight: np.ndarray,
                     broad: float = 0.1, near: float = 0.025, keep: np.ndarray | None = None) -> np.ndarray:
    """tone_match, with the tone taken at a much finer scale toward the edge of the painted area (where weight falls
    below 1): inside, the old texture only lends its broad tone (its blurred eyes must not come back); along the edge
    the painted face takes the old texture's local tone, so there is no step where the paint stops."""
    # inside, the painting stays as it is (its colours and its own light and shade); broad > 0 also matches the
    # lightness at that scale (the knight showed that this brightens the painting's shaded cheek into a patch)
    if broad > 0:
        s_ = broad * new.shape[0]
        shift = masked_blur(luminance(old), region, s_) - masked_blur(luminance(new), region, s_)
        far = np.clip(new + shift[..., None], 0.0, 1.0)
    else:
        far = new
    close = tone_match(new, old, region, near)
    edge = np.clip(1.0 - weight, 0.0, 1.0)
    edge = blur(edge, near * new.shape[0])  # a smooth hand-over between the two
    edge = np.clip(edge * 1.5, 0.0, 1.0)
    if keep is not None:  # the eyes keep the painting's colour even next to an edge
        edge = edge * (1.0 - np.clip(blur(keep.astype(np.float64), near * new.shape[0] / 3), 0, 1))
    edge = edge[..., None]
    return far * (1 - edge) + close * edge


def hair_and_skin(old: np.ndarray, picture: np.ndarray, info: dict[str, Any], mask: np.ndarray,
                  margin: float = 6.0) -> dict[str, Any]:
    """Which pixels are hair and which are skin, on the model's render (old) and on the fitted picture, by colour
    distance (Lab, lightness counted half) to a skin tone and a hair colour sampled on each. The model's skin tone is
    find_face's (around the nose); its hair colour the middle colour of the head above the face. The picture's are
    sampled where the model has skin near the nose and where the model has hair. A pixel is hair when it is nearer the
    hair colour than the skin tone by `margin`. Eyes, brows and lips are nearer the skin tone than a hair colour that
    differs from the skin (checked on red hair and brown brows). Returns {old_hair, pic_hair, pic_background (S,S) bool,
    hair_lab, found}; found is False (nothing marked) when no hair colour that differs from the skin is found."""
    S = old.shape[0]
    region = info["region"]
    w = float(info["width_px"])
    x0, y0, x1, y1 = info["box"]
    lab_o = to_lab(old)
    lab_p = to_lab(picture)
    k = np.array([0.5, 1.0, 1.0])
    skin_o = np.array(info["skin"])
    Y, X = np.mgrid[0:S, 0:S]
    above = mask & ~dilate_mask(region, max(2, int(w / 40))) & (Y < y0 + 0.25 * (y1 - y0)) & (np.abs(X - info["nose"][0]) < 0.8 * w)
    empty = np.zeros((S, S), dtype=bool)
    if above.sum() < 200:
        return {"old_hair": empty, "pic_hair": empty, "pic_background": empty, "found": False}
    hair_o = np.median(lab_o[above], axis=0)
    if np.linalg.norm((hair_o - skin_o) * k) < 2 * margin:
        return {"old_hair": empty, "pic_hair": empty, "pic_background": empty, "found": False}
    d = lambda lab, ref: np.linalg.norm((lab - ref) * k, axis=-1)  # noqa: E731
    # Hair is only hair when it joins the hair around the face: a red mouth or a dark eye smudge on its own is not.
    old_hair = grow(above, mask & (d(lab_o, hair_o) < d(lab_o, skin_o) - margin))
    nx, ny = info["nose"]
    r = max(4, S // 64)
    win = np.zeros((S, S), dtype=bool)
    win[max(0, ny - 3 * r): ny + r, max(0, nx - 2 * r): nx + 2 * r] = True
    skin_p = np.median(lab_p[win & region & ~old_hair], axis=0) if (win & region & ~old_hair).sum() > 20 else skin_o
    hair_src = old_hair & mask
    hair_p = np.median(lab_p[hair_src], axis=0) if hair_src.sum() > 200 else hair_o
    pic_hair_any = d(lab_p, hair_p) < d(lab_p, skin_p) - margin
    pic_hair = grow(pic_hair_any & ~dilate_mask(region, max(2, int(w / 40))), pic_hair_any)
    # the grey background around the painted head (where the painted face is narrower than the model's): grey of the
    # background's colour joined to the picture's edge, so a dark grey pupil or brow inside the face is not background
    # (the silver-haired mage's close-up had both filled as background when the model's nose was found too low)
    bg = to_lab(np.full((1, 1, 3), 0.25))[0, 0]
    grey = d(lab_p, bg) < 6.0
    edge = np.zeros_like(grey)
    edge[0, :] = edge[-1, :] = edge[:, 0] = edge[:, -1] = True
    pic_background = grow(grey & edge, grey) if (grey & edge).any() else np.zeros_like(grey)
    return {"old_hair": old_hair, "pic_hair": pic_hair, "pic_background": pic_background, "found": True,
            "hair_lab": [round(float(v), 1) for v in hair_o]}


def merge_detail(painted: np.ndarray, sharp: np.ndarray, sigma: float = 3.0, rgb: bool = False) -> np.ndarray:
    """The painting decides colour and broad tone, the sharpened picture only adds fine detail: the painted crop's
    colours below sigma pixels (at 1024) plus the sharpened picture's luminance above it. So the eyes, skin and lips
    keep the painting's hue everywhere, and the broad look stays the painting's (the redraw cannot make a stylised
    painting look photographed at any scale bigger than sigma)."""
    s = sigma * painted.shape[0] / 1024.0
    if rgb:  # the sharp picture's own colour detail too (a checked close-up: its iris and lip colour are drawn finer)
        return np.clip(blur(painted, s) + sharp - blur(sharp, s), 0.0, 1.0)
    lum = luminance(sharp)
    detail = lum - blur(lum, s)
    return np.clip(blur(painted, s) + detail[..., None], 0.0, 1.0)


def likeness(original: np.ndarray, candidate: np.ndarray, region: np.ndarray, source_px: float) -> float:
    """Whether a redrawn picture is still the same face as the original painted crop (both framed alike), judged at
    the original's own resolution: both are shrunk to the size the painting really has across the crop (source_px,
    its head box in the concept's pixels), and the correlation of their band-passed lightness (about one to four
    source pixels: eyes, brows, nostrils, lips, the shadow shapes) inside region is returned (1 the same). Detail the
    redraw adds beyond the painting's resolution does not count, and a different face with the same hair and framing
    scores low (validated on the knight: four good redraws 0.976 to 0.980; four different faces 0.19 to 0.87, the
    highest two keeping her hair, framing and armour and changing only the face)."""
    n = int(max(32, min(original.shape[0], round(float(source_px)))))
    a = resize(original, n)
    b = resize(candidate, n)
    r = np.asarray(Image.fromarray(region.astype(np.uint8) * 255).resize((n, n), Image.BOX)) > 127
    # feature_match takes its band in pixels at 1024 and scales it to the picture's size; here the band is 0.9 to 3.6
    # pixels of the shrunk picture, that is of the painting's own pixels
    return feature_match(a, b, r, 0.9 * 1024 / n, 3.6 * 1024 / n)


def colour_distance(a: np.ndarray, b: np.ndarray, mask: np.ndarray) -> float | None:
    """The middle colour of two pictures inside mask, compared (Lab, lightness counted half). None with too few
    pixels."""
    if mask.sum() < 50:
        return None
    la = np.median(to_lab(np.clip(a, 0, 1))[mask], axis=0)
    lb = np.median(to_lab(np.clip(b, 0, 1))[mask], axis=0)
    return round(float(np.linalg.norm((la - lb) * np.array([0.5, 1.0, 1.0]))), 2)


def eye_boxes(shape: tuple[int, int], eyes: list[tuple[float, float]], width_px: float, grow: float = 1.6) -> np.ndarray:
    """A mask of the boxes eye_colour measures, made `grow` times bigger (the whole eye and its lids)."""
    m = np.zeros(shape, dtype=bool)
    hx, hy = max(2, int(0.05 * width_px * grow)), max(2, int(0.025 * width_px * grow))
    for x, y in eyes:
        m[max(0, int(y) - hy): int(y) + hy + 1, max(0, int(x) - hx): int(x) + hx + 1] = True
    return m


def eye_colour(img: np.ndarray, eyes: list[tuple[float, float]], width_px: float) -> list[list[float]]:
    """Mean colour (Lab a and b: hue and how colourful, not how light) in a small box around each eye point: the iris
    and the white around it."""
    out = []
    lab = to_lab(np.clip(img, 0, 1))
    hx, hy = max(2, int(0.05 * width_px)), max(2, int(0.025 * width_px))
    for x, y in eyes:
        xi, yi = int(x), int(y)
        box = lab[max(0, yi - hy): yi + hy + 1, max(0, xi - hx): xi + hx + 1]
        out.append([round(float(box[..., 1].mean()), 2), round(float(box[..., 2].mean()), 2)])
    return out


def colour_shift(a: list[list[float]], b: list[list[float]]) -> float:
    """The largest difference in hue and colourfulness (Lab a, b distance) between matching eyes of two lists."""
    return round(max(float(np.hypot(p[0] - q[0], p[1] - q[1])) for p, q in zip(a, b)), 2) if a and b else 0.0


def inpaint(img: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """img with every pixel outside valid filled smoothly from the valid pixels around it (push-pull)."""
    from . import imaging

    if valid.all() or not valid.any():
        return img.copy()
    out = imaging.fill_empty(img.astype(np.float32), valid).astype(np.float64)
    # smooth the fill (the push-pull pyramid leaves blocks) while the valid pixels stay as they are
    sigma = max(2.0, img.shape[0] / 120)
    for _ in range(8):
        out = blur(out, sigma)
        out[valid] = img[valid]
    return out


def screen_weight(region: np.ndarray, feather_frac: float = 0.008) -> np.ndarray:
    """0..1 weight of the face region in the picture, shrunk and feathered so it has faded out by the region's edge
    (hairline, jaw, ears): nothing is painted outside the face."""
    r = max(1, int(round(feather_frac * region.shape[0])))
    inner = erode(region, 2 * r)
    return np.clip(blur(inner.astype(np.float64), r * 0.8), 0.0, 1.0) * region


def project_back(mesh: dict[str, np.ndarray], texture: np.ndarray, cam: Camera, picture: np.ndarray, weight: np.ndarray,
                 facing: tuple[float, float] = (0.3, 0.7), strength: float = 1.0, soften_m: float = 0.0,
                 cover: np.ndarray | None = None) -> dict[str, Any]:
    """Paint `picture` (seen through cam) onto the texture through the model's UVs. Each texel the camera sees
    takes weight(screen) * smoothstep(facing) * strength of the picture's colour, where facing is the cosine between
    the surface and the view (surfaces seen edge-on keep their old colour). Texels the camera does not see (behind
    something nearer, or facing away) are untouched. Returns {texture, texel_weight (T,T), changed (texels with weight
    over 0.5), touched (texels with any weight), bbox}. With cover (T,T), how much of each texel an earlier camera
    already painted (0..1), this camera only fills the rest: its weight is multiplied by 1 - cover."""
    T = texture.shape[0]
    verts, faces = mesh["verts"], mesh["faces"]
    view = view_raster(mesh, cam)
    pts, depth = view["pts"], view["depth"]
    a, b, c = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
    fn = np.cross(b - a, c - a)
    toward = -(fn @ cam.basis[2]) > 0
    P = pts[faces]
    inside = (P[:, :, 0].min(1) >= 0) & (P[:, :, 0].max(1) <= cam.size) & (P[:, :, 1].min(1) >= 0) & (P[:, :, 1].max(1) <= cam.size)
    wtri = np.zeros(len(faces))
    cx = np.clip(P[:, :, 0].mean(1).astype(int), 0, cam.size - 1)
    cy = np.clip(P[:, :, 1].mean(1).astype(int), 0, cam.size - 1)
    near = dilate_mask(weight > 0.001, max(2, cam.size // 100))
    wtri = near[cy, cx]
    tris = np.where(inside & wtri)[0]
    uvp = uv_pixels(mesh["uv"], T)
    ras = rasterise(uvp, np.zeros(len(uvp)), faces, T, tris)
    covered = ras["tri"] >= 0
    pos = interpolate(verts, faces, ras)[covered]
    nrm = interpolate(mesh["vnormals"], faces, ras)[covered]
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    sp, sd = cam.project(pos)
    # visible: nothing nearer at that pixel. The allowance grows on slanted surface, where the depth changes quickly
    # between the texel's point and the pixel centre.
    z = view["z"]
    xi = np.clip(sp[:, 0].astype(int), 0, cam.size - 1)
    yi = np.clip(sp[:, 1].astype(int), 0, cam.size - 1)
    cosv = -(nrm @ cam.basis[2])
    px_m = cam.scale / cam.size
    tol = 2.0 * px_m / np.maximum(np.abs(cosv), 0.15)
    visible = sd <= z[yi, xi] + tol
    w = smoothstep(facing[0], facing[1], cosv) * sample(weight[..., None], sp[:, 0], sp[:, 1])[:, 0] * visible * float(strength)
    w = np.clip(w, 0.0, 1.0)
    if soften_m > 0 and len(w):
        # Fade the paint out on the surface itself wherever it stops short (behind a hair flap, at a sharp turn): the
        # mean weight within about soften_m in 3D; a texel keeps its weight only where most of its surroundings are
        # painted too, so no hard line shows from another side.
        cell = soften_m / 2
        lo = pos.min(axis=0)
        idx = np.floor((pos - lo) / cell).astype(int)
        dims = tuple(idx.max(axis=0) + 1)
        sw, sn = np.zeros(dims), np.zeros(dims)
        np.add.at(sw, tuple(idx.T), w)
        np.add.at(sn, tuple(idx.T), 1.0)
        for _ in range(2):
            sw, sn = _box3(sw, 1), _box3(sn, 1)
        wa = _trilinear(sw / np.maximum(sn, 1e-9), (pos - lo) / cell - 0.5)
        w = w * smoothstep(0.3, 0.85, wa)
    if cover is not None:
        w = w * (1.0 - np.clip(cover[covered], 0.0, 1.0))
    col = sample(picture, sp[:, 0], sp[:, 1])
    out = texture.copy()
    tw = np.zeros((T, T))
    old = out[covered]
    out[covered] = old * (1 - w[:, None]) + col * w[:, None]
    tw[covered] = w
    # Texels on a triangle's edge whose centre falls just outside every triangle are still read by texture filtering:
    # where a written neighbour was changed they take its colour, so no thin line of the old colour shows.
    from . import imaging

    if covered.any():
        ys_, xs_ = np.where(covered)
        y0, y1 = max(0, ys_.min() - 3), min(T, ys_.max() + 4)
        x0, x1 = max(0, xs_.min() - 3), min(T, xs_.max() + 4)
        cov = covered[y0:y1, x0:x1]
        sub = out[y0:y1, x0:x1]
        grown = imaging.dilate(sub, cov, 2)
        gw = imaging.dilate(tw[y0:y1, x0:x1], cov, 2)
        take = ~cov & (gw > 0.001)
        sub[take] = grown[take]
        tw[y0:y1, x0:x1][take] = gw[take]
    touched = tw > 0.001
    ys, xs = np.where(touched)
    bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1) if len(xs) else None
    return {"texture": out, "texel_weight": tw, "changed": int((tw > 0.5).sum()), "touched": int(touched.sum()), "bbox": bbox}


def _trilinear(grid: np.ndarray, f: np.ndarray) -> np.ndarray:
    """grid (X, Y, Z[, c]) read at fractional cell coordinates f (n, 3), cell centres at whole numbers (clamped)."""
    dims = np.array(grid.shape[:3])
    f = np.clip(f, 0, dims - 1)
    i0 = np.floor(f).astype(int)
    i1 = np.minimum(i0 + 1, dims - 1)
    t = f - i0
    out = 0.0
    for dx in (0, 1):
        for dy in (0, 1):
            for dz in (0, 1):
                ix = i1[:, 0] if dx else i0[:, 0]
                iy = i1[:, 1] if dy else i0[:, 1]
                iz = i1[:, 2] if dz else i0[:, 2]
                wgt = (t[:, 0] if dx else 1 - t[:, 0]) * (t[:, 1] if dy else 1 - t[:, 1]) * (t[:, 2] if dz else 1 - t[:, 2])
                v = grid[ix, iy, iz]
                out = out + (v * (wgt[:, None] if v.ndim > 1 else wgt))
    return out


def _box3(a: np.ndarray, r: int) -> np.ndarray:
    """Box blur of radius r along the first three axes (edges clamped)."""
    out = a.astype(np.float64)
    for axis in range(3):
        pad = [(0, 0)] * out.ndim
        pad[axis] = (r + 1, r)
        p = np.pad(out, pad, mode="edge")
        c = np.cumsum(p, axis=axis)
        n = out.shape[axis]
        out = (np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis) - np.take(c, np.arange(0, n), axis=axis)) / (2 * r + 1)
    return out


def spread_tone(mesh: dict[str, np.ndarray], old: np.ndarray, new: np.ndarray, tw: np.ndarray, skin_lab: Any,
                radius_m: float = 0.03, cell_m: float = 0.005, skin_tolerance: float = 20.0) -> tuple[np.ndarray, int]:
    """Carry the painted face's broad tone a few centimetres onto the skin around it, on the surface in 3D (across
    texture seams), so the old texture beyond the paint (the cheek behind a hair flap, the side of the face the front
    camera saw edge-on) meets it without a step. The difference new - old on painted texels is averaged in a coarse 3D
    grid and blurred; a skin texel the paint did not (fully) reach is moved by that difference, as much as painted
    surface lies around it and as much as it was not painted. Texels whose old colour is not skin stay. Returns (the
    texture, how many texels were moved)."""
    T = old.shape[0]
    verts, faces = mesh["verts"], mesh["faces"]
    painted_any = tw > 0.5
    if painted_any.sum() < 50:
        return new, 0
    ras = rasterise(uv_pixels(mesh["uv"], T), np.zeros(len(verts)), faces, T)
    cov = ras["tri"] >= 0
    pos = interpolate(verts, faces, ras)
    painted = painted_any & cov
    pp = pos[painted]
    lo = pp.min(axis=0) - radius_m
    hi = pp.max(axis=0) + radius_m
    dims = np.maximum(1, np.ceil((hi - lo) / cell_m).astype(int) + 1)
    inbox = cov & np.all((pos >= lo) & (pos <= hi), axis=-1)
    idx_all = np.floor((pos[inbox] - lo) / cell_m).astype(int)
    idx_p = np.floor((pp - lo) / cell_m).astype(int)
    diff = new[painted] - old[painted]
    s = np.zeros(tuple(dims) + (3,))
    n = np.zeros(tuple(dims))
    m = np.zeros(tuple(dims))
    np.add.at(s, tuple(idx_p.T), diff)
    np.add.at(n, tuple(idx_p.T), 1.0)
    np.add.at(m, tuple(idx_all.T), 1.0)
    r = max(1, int(round(radius_m / cell_m / 2)))
    for _ in range(2):
        s, n, m = _box3(s, r), _box3(n, r), _box3(m, r)
    D = s / np.maximum(n, 1e-9)[..., None]
    frac = np.clip(n / np.maximum(m, 1e-9), 0.0, 1.0)
    lab = to_lab(np.clip(old[inbox], 0, 1))
    skin = np.linalg.norm((lab - np.asarray(skin_lab)) * np.array([0.5, 1.0, 1.0]), axis=-1) < skin_tolerance
    f = (pos[inbox] - lo) / cell_m - 0.5
    amount = (1.0 - np.clip(tw[inbox], 0, 1)) * _trilinear(frac, f) * skin
    # the painted colour itself, smoothed: skin the front camera could not see (a cheek behind a hair flap) takes it
    # where most of the surface around is painted, so the hand-over is a gradual blend, not a line
    cs = np.zeros(tuple(dims) + (3,))
    np.add.at(cs, tuple(idx_p.T), new[painted])
    cn = np.zeros(tuple(dims))
    np.add.at(cn, tuple(idx_p.T), 1.0)
    for _ in range(2):
        cs, cn = _box3(cs, r), _box3(cn, r)
    C = _trilinear(cs / np.maximum(cn, 1e-9)[..., None], f)
    out = new.copy()
    vals = out[inbox] + _trilinear(D, f) * amount[:, None]
    take = np.clip(amount * 1.2, 0.0, 1.0)[:, None]
    vals = vals * (1 - take) + C * take
    out[inbox] = np.clip(vals, 0.0, 1.0)
    return out, int((amount > 0.05).sum())


def grow_into_gutter(texture: np.ndarray, coverage: np.ndarray, bbox: tuple[int, int, int, int] | None, px: int) -> np.ndarray:
    """Re-grow the edited islands' colours into the empty texture space around them (inside bbox plus a margin), so
    texture filtering at an island's edge does not mix in the old colours."""
    from . import imaging

    if bbox is None:
        return texture
    T = texture.shape[0]
    m = 2 * px + 2
    x0, y0, x1, y1 = max(0, bbox[0] - m), max(0, bbox[1] - m), min(T, bbox[2] + m), min(T, bbox[3] + m)
    out = texture.copy()
    out[y0:y1, x0:x1] = imaging.dilate(texture[y0:y1, x0:x1], coverage[y0:y1, x0:x1], px)
    return out


def face_texels(mesh: dict[str, np.ndarray], cam: Camera, region: np.ndarray, tex_size: int) -> dict[str, Any]:
    """How much texture the face has: texels whose surface the front camera sees inside the face region, and the
    width of the face in texels (the face region's width in metres times the texels per metre on the face)."""
    weight = region.astype(np.float64)
    dummy = np.zeros((tex_size, tex_size, 3))
    res = project_back(mesh, dummy, cam, np.ones((cam.size, cam.size, 3)), weight, facing=(0.0, 0.05))
    texels = int((res["texel_weight"] > 0.5).sum())
    px = int(region.sum())
    m2_per_px = (cam.scale / cam.size) ** 2
    area_m2 = px * m2_per_px
    ys, xs = np.where(region)
    width_m = (np.percentile(xs, 98) - np.percentile(xs, 2)) * cam.scale / cam.size if len(xs) else 0.0
    per_m = math.sqrt(texels / area_m2) if area_m2 > 0 else 0.0
    return {"face_texels": texels, "face_width_texels": int(round(width_m * per_m)), "face_width_m": round(float(width_m), 4),
            "texels_per_m": round(per_m, 1)}


def painted_head(cutout: np.ndarray, head: Head, cam: Camera, size: int = 1024, background: float = 0.25) -> dict[str, Any] | None:
    """The painted picture's head framed like cam frames the model's head: the figure's height in the cut-out (alpha)
    stands for the model's height, its top for the model's top, and the head's middle (the 5th to 95th percentile of
    the top 13 percent of the figure) for the camera's middle. cutout is (H, W, 4) floats 0..1. Returns {image (size,
    size, 3) on a grey background, box (x0, y0, x1, y1) in the picture, px_per_m, head_px} or None when the
    picture has no figure."""
    alpha = cutout[..., 3] > 0.5
    rows = np.where(alpha.any(axis=1))[0]
    if len(rows) < 20:
        return None
    top, bot = int(rows[0]), int(rows[-1])
    ppm = (bot - top + 1) / head.height
    hrows = alpha[top: top + max(2, int(0.13 * (bot - top)))]
    ys, xs = np.where(hrows)
    if len(xs) < 20:
        return None
    cx = 0.5 * (np.percentile(xs, 5) + np.percentile(xs, 95)) + 0.5
    cy = top + (head.top - cam.target[1]) * ppm
    half = 0.5 * cam.scale * ppm
    box = (cx - half, cy - half, cx + half, cy + half)
    rgb = cutout[..., :3] * cutout[..., 3:4] + background * (1 - cutout[..., 3:4])
    X, Y = np.meshgrid(np.arange(size) + 0.5, np.arange(size) + 0.5)
    sx = box[0] + X / size * (box[2] - box[0])
    sy = box[1] + Y / size * (box[3] - box[1])
    # a box filter first when the crop is shrunk, else plain bilinear (the crop is usually enlarged)
    img = sample(rgb, sx, sy)
    alpha = sample(cutout[..., 3:4], sx, sy)[..., 0]
    outside = (sx < 0) | (sy < 0) | (sx > rgb.shape[1]) | (sy > rgb.shape[0])
    img[outside] = background
    alpha[outside] = 0.0
    return {"image": img, "alpha": alpha, "box": tuple(round(float(v), 2) for v in box), "px_per_m": round(float(ppm), 2),
            "head_px": round(float(2 * half), 1)}


def covered_head(info: dict[str, Any], head: Head, cam: Camera, min_width: float = 0.35) -> bool:
    """True when the visible skin of the face is too small to be a face that can be painted: narrower than
    min_width of the head (a full helmet, a hood or a mask leaves a slit or nothing)."""
    if not info.get("found"):
        return True
    head_px = head.width * cam.size / cam.scale
    return float(info["width_px"]) < min_width * head_px


def face_triangles(view: dict[str, Any], region: np.ndarray) -> np.ndarray:
    """Indices of the triangles the front camera sees inside the face region."""
    t = view["raster"]["tri"][region]
    return np.unique(t[t >= 0])


def triangle_centres(mesh: dict[str, np.ndarray], tris: np.ndarray) -> np.ndarray:
    return mesh["verts"][mesh["faces"][tris]].mean(axis=1)


def gain_needed(face_width_texels: float, target: float = 256.0, most: float = 3.0) -> float:
    """How many times more texels across the face needs to reach target (1 when it has enough), at most `most`."""
    if face_width_texels <= 0:
        return 1.0
    return float(min(most, max(1.0, target / face_width_texels)))


def eye_points(img: np.ndarray, face_info: dict[str, Any]) -> list[tuple[float, float]] | None:
    """The darkest spot left and right of the nose in the eye band of the face (between the top of the face box and
    the nose): the centre of the darkest 4 percent of each half. None when the face is too small to say."""
    region = face_info["region"]
    x0, y0, x1, y1 = face_info["box"]
    nx, ny = face_info["nose"]
    lum = blur(luminance(img), 1.5)
    out = []
    top = int(y0 + 0.15 * (ny - y0))
    bottom = int(ny - 0.15 * (ny - y0))
    for xa, xb in ((x0, nx - 0.08 * (x1 - x0)), (nx + 0.08 * (x1 - x0), x1)):
        sl = (slice(top, bottom), slice(int(xa), int(xb)))
        sub = lum[sl]
        rm = region[sl]
        if rm.sum() < 30:
            return None
        thr = np.percentile(sub[rm], 4)
        ys, xs = np.where(rm & (sub <= thr))
        out.append((float(xs.mean() + sl[1].start + 0.5), float(ys.mean() + sl[0].start + 0.5)))
    return out
