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


def find_face(view: dict[str, Any], mesh: dict[str, np.ndarray], cam: Camera, head: Head,
              skin_tolerance: float = 16.0) -> dict[str, Any]:
    """The face in a front render: the nose tip (the nearest visible point in the upper head), the skin colour around
    it, and the skin-coloured area joined to it with its holes (eyes, mouth) filled. Returns {found, reason,
    nose (x, y), skin (Lab), region (S,S) bool, box (x0, y0, x1, y1), width_px}."""
    S = cam.size
    ras = view["raster"]
    m = view["mask"]
    pos = interpolate(mesh["verts"], mesh["faces"], ras)
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
    return {"found": True, "reason": None, "nose": (int(ix), int(iy)), "skin": [round(float(v), 2) for v in skin], "region": region,
            "box": (int(x0), int(y0), int(x1), int(y1)), "width_px": float(x1 - x0), "area_px": int(region.sum())}


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
    """One scale and a shift that put the picture's eye line and mouth on the model's: the scale from the distance
    between the eye line and the mouth, the shift from the middle of the eyes and the mouth. The spacing of the eyes
    stays the picture's: the 3D builder models no eye sockets at this triangle count (the nose and the mouth line are
    what the shape has), and its blurred eye smudges are only a rough guess. None when the scale would be more than
    `limit` away from 1 (then the points are not the same features). residual_px: how far the eyes still differ."""
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
    c = np.array(centre, dtype=np.float64)
    mid_s, mid_d = (se + sm) / 2, (de + dm) / 2
    t = mid_d - (c + k * (mid_s - c))
    pe = c + k * (np.array(src["eyes"]) - c) + t
    res = float(np.abs(pe - np.array(dst["eyes"])).max())
    return {"sx": round(k, 4), "sy": round(k, 4), "tx": round(float(t[0]), 2), "ty": round(float(t[1]), 2),
            "centre": (float(c[0]), float(c[1])), "residual_px": round(res, 2)}


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


def screen_weight(region: np.ndarray, feather_frac: float = 0.008) -> np.ndarray:
    """0..1 weight of the face region in the picture, shrunk and feathered so it has faded out by the region's edge
    (hairline, jaw, ears): nothing is painted outside the face."""
    r = max(1, int(round(feather_frac * region.shape[0])))
    inner = erode(region, 2 * r)
    return np.clip(blur(inner.astype(np.float64), r * 0.8), 0.0, 1.0) * region


def project_back(mesh: dict[str, np.ndarray], texture: np.ndarray, cam: Camera, picture: np.ndarray, weight: np.ndarray,
                 facing: tuple[float, float] = (0.3, 0.7), strength: float = 1.0) -> dict[str, Any]:
    """Paint `picture` (seen through cam) onto the texture through the model's UVs. Each texel the camera sees
    takes weight(screen) * smoothstep(facing) * strength of the picture's colour, where facing is the cosine between
    the surface and the view (surfaces seen edge-on keep their old colour). Texels the camera does not see (behind
    something nearer, or facing away) are untouched. Returns {texture, texel_weight (T,T), changed (texels with weight
    over 0.5), touched (texels with any weight), bbox}."""
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
    outside = (sx < 0) | (sy < 0) | (sx > rgb.shape[1]) | (sy > rgb.shape[0])
    img[outside] = background
    return {"image": img, "box": tuple(round(float(v), 2) for v in box), "px_per_m": round(float(ppm), 2), "head_px": round(float(2 * half), 1)}


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
