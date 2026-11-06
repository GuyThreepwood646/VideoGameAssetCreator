"""Hair from the library: fit a library hairstyle to a bald character (headless Blender; modes `rank` and `attach`).

    blender -b --python hairfit.py -- rank   <job.json>
    blender -b --python hairfit.py -- attach <job.json>

The job JSON is written by vgac/hairfit.py; results go to the job's out_dir (rank.json / attach.json plus files).
The numpy parts below (skull fit, radial correction, placing, scoring, recolour) do not need Blender and are also
imported by the offline library builder (tools/hairlib_blender.py) and the tests; `bpy` is only needed by the
functions that read meshes, cast rays and render.

Method (docs/HAIR-LIBRARY-PLAN.md section 6, from the round-two trial in runtime/tmp/hairlib/r2):
 1. Skull fit: an ellipsoid with forward tilt fitted over the cranium (above the brow and the back of the head), on the
    library's reference head when the library is built and on the bald body here. Library vertices are stored in the
    reference skull's normalised frame (`q`) with the reference scalp radius under each vertex (`rho_r`).
 2. Radial correction: in every direction from the skull centre, the bald scalp's normalised radius (ray casts on a
    smoothed 10 degree grid) replaces the reference one; the hair's height over the scalp is kept and scaled by
    `volume`; below the skull centre the hair is stretched by `length`; `mirror` swaps left and right; `dz_head`
    moves the hair up or down in head heights.
 3. Push-out: every hair vertex closer than 1.5 mm to the body is moved out along the surface normal.
 4. Scalp cap: the scalp above the hairline, 2.5 mm off the skin, kept where a ray outwards meets the hair (role 1).
Frames: Blender works Z up with the character facing -Y (as glTF import gives); files written for the app are glTF
Y up metres in the body's own model space (x, z, -y)."""
import json
import math
import os
import sys
import time

import numpy as np

try:  # the numpy parts are importable without Blender
    import bpy  # noqa: F401
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    HAVE_BPY = True
except ImportError:  # pragma: no cover - outside Blender
    HAVE_BPY = False

HEIGHT = 1.7
PUSH_EPS = 0.0015
CAP_OFFSET = 0.0025
VOLUME = (0.7, 1.7)
DZ = (-0.17, 0.17)          # head heights (about +-3 cm)
LENGTH = (0.45, 1.6)

# --------------------------------------------------------------------------------------------- small maths


def nrm(v):
    v = np.asarray(v, float)
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-12)


def dirv(theta, phi):
    """Unit direction: theta round the head from the front (0 front, +90 the character's left), phi elevation."""
    t, p = np.radians(theta), np.radians(phi)
    return np.stack([np.sin(t) * np.cos(p), -np.cos(t) * np.cos(p), np.sin(p) * np.ones_like(t)], -1)


def rot_x(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def hairline(theta, front, ear):
    th = np.abs((np.asarray(theta, float) + 180) % 360 - 180)
    kx = [0, 35, 68, 92, 112, 140, 180]
    ky = [front, front - 5, ear + 14, ear + 5, ear - 8, -40, -47]
    return np.interp(th, kx, ky)


def to_local(S, P):
    return ((np.asarray(P, float) - np.asarray(S["c"])) @ rot_x(S["pitch"])) / np.asarray(S["R"])


def to_world(S, Q):
    return np.asarray(S["c"]) + (np.asarray(Q, float) * np.asarray(S["R"])) @ rot_x(S["pitch"]).T


def gltf_to_z(V):
    V = np.asarray(V, float)
    return np.stack([V[:, 0], -V[:, 2], V[:, 1]], 1)


def z_to_gltf(V):
    V = np.asarray(V, float)
    return np.stack([V[:, 0], V[:, 2], -V[:, 1]], 1)


def yaw_matrix(deg):
    a = math.radians(deg)
    return np.array([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]])


def normalise(V, height=HEIGHT):
    """Scale to `height` with the feet at 0. Returns (V', s, z0) with V' = V * s - (0, 0, z0)."""
    s = height / max(1e-9, V[:, 2].max() - V[:, 2].min())
    z0 = V[:, 2].min() * s
    return V * s - np.array([0, 0, z0]), s, z0


# --------------------------------------------------------------------------------------------- skull fit


def find_skull(V, head_h):
    """Ellipsoid (centre c, radii R, forward tilt) over the cranium of a body scaled to 1.7 m.
    head_h: head height as a share of the figure (top of the head to the narrowest point of the neck)."""
    ztop = V[:, 2].max()
    HH = head_h * HEIGHT
    band = V[V[:, 2] > ztop - 0.6 * HH]
    hcx, hcy = float(np.median(band[:, 0])), float(np.median(band[:, 1]))
    near = np.hypot(V[:, 0] - hcx, V[:, 1] - hcy) < 0.16
    top = V[near & (V[:, 2] > ztop - 0.42 * HH)]
    back = V[near & (V[:, 2] > ztop - 0.72 * HH) & (V[:, 1] > hcy + 0.02)]
    pts = np.vstack([top, back])
    b2 = V[near & (V[:, 2] < ztop - 0.15 * HH) & (V[:, 2] > ztop - 0.40 * HH)]
    rx = (np.percentile(b2[:, 0], 99) - np.percentile(b2[:, 0], 1)) / 2
    ry = (np.percentile(b2[:, 1], 99) - np.percentile(b2[:, 1], 1)) / 2
    cy = (np.percentile(b2[:, 1], 99) + np.percentile(b2[:, 1], 1)) / 2
    rz = 1.02 * ry
    x = np.array([hcx, cy, ztop - rz, rx, ry, rz, 0.0])

    def resid(x):
        c, R, a = x[:3], x[3:6], x[6]
        q = (pts - c) @ rot_x(a)
        r = np.linalg.norm(q / R, axis=1) - 1
        prior = [3 * (R[2] / R[1] - 1.05), 6 * a]
        return np.concatenate([r * np.mean(R), np.array(prior) * 0.03])

    lam = 1e-3
    for _ in range(40):
        r = resid(x)
        J = np.zeros((len(r), len(x)))
        for k in range(len(x)):
            dx = np.zeros(len(x)); dx[k] = 1e-5
            J[:, k] = (resid(x + dx) - r) / 1e-5
        A = J.T @ J
        step = np.linalg.solve(A + lam * np.diag(np.diag(A) + 1e-9), -(J.T @ r))
        xn = x + step
        if np.sum(resid(xn) ** 2) < np.sum(r ** 2):
            x, lam = xn, lam * 0.5
        else:
            lam *= 4
        if np.linalg.norm(step) < 1e-7:
            break
    c, R, a = x[:3], x[3:6], float(np.clip(x[6], -0.15, 0.15))
    res = float(np.sqrt(np.mean(resid(x)[:-2] ** 2)))
    q = (V - c) @ rot_x(a)
    side = (np.abs(q[:, 1]) < 0.45 * R[1]) & (q[:, 2] < 0.15 * R[2]) & (q[:, 2] > -0.9 * R[2]) & (np.abs(q[:, 0]) > 0.97 * R[0])
    ear_phi, ear_out = -12.0, 0.0
    if side.sum() > 10:
        ez = np.percentile(q[side, 2], 97)
        ear_phi = float(np.degrees(np.arcsin(np.clip(ez / R[2], -1, 1))))
        ear_out = float(max(0.0, np.percentile(np.abs(q[side, 0]), 98) - R[0]))
    return dict(c=[float(v) for v in c], R=[float(v) for v in R], pitch=a, resid_mm=res * 1000, ztop=float(ztop), HH=float(HH),
                hcx=hcx, hcy=hcy, ear_phi=ear_phi, ear_out=ear_out)


def head_share(V):
    """Head height as a share of the figure, from horizontal slices (narrowest neck between 8 and 20 percent)."""
    zt, zb = V[:, 2].max(), V[:, 2].min()
    H = zt - zb
    best = None
    for f in np.linspace(0.08, 0.2, 25):
        z = zt - f * H
        s = V[np.abs(V[:, 2] - z) < 0.004 * H]
        s = s[np.abs(s[:, 0] - np.median(V[V[:, 2] > zt - 0.1 * H][:, 0])) < 0.12 * H]
        if len(s) < 5:
            continue
        w = s[:, 0].max() - s[:, 0].min()
        if best is None or w < best[0]:
            best = (w, f)
    return float(best[1]) if best else 0.12


# --------------------------------------------------------------------------------------------- radial correction

GRID_TH = np.arange(-180, 180, 10, dtype=float)
GRID_PH = np.arange(-70, 91, 10, dtype=float)


def radius_grid(S, cast):
    """Normalised distance from the skull centre to the outermost surface on the theta/phi grid.
    cast(origin, direction, dist) -> hit point or None (a BVH ray cast)."""
    G = np.full((len(GRID_PH), len(GRID_TH)), np.nan)
    c = np.asarray(S["c"])
    for i, p in enumerate(GRID_PH):
        for j, t in enumerate(GRID_TH):
            pw = to_world(S, dirv(t, p) * 0.6)
            d = nrm(pw - c)
            h = cast(c + d * 0.6, -d, 0.6)
            if h is not None:
                G[i, j] = np.linalg.norm(to_local(S, np.asarray(h)))
    return smooth_grid(G)


def smooth_grid(G):
    G = G.copy()
    for _ in range(4):
        m = np.isnan(G)
        if not m.any():
            break
        Gp = np.pad(G, ((1, 1), (0, 0)), mode="edge")
        with np.errstate(all="ignore"):
            nb = np.nanmean(np.stack([np.roll(G, 1, 1), np.roll(G, -1, 1), Gp[:-2], Gp[2:]]), 0)
        G[m] = nb[m]
    G = np.where(np.isnan(G), 1.0, G)
    for _ in range(2):
        Gp = np.pad(G, ((1, 1), (0, 0)), mode="edge")
        G = 0.4 * G + 0.15 * (np.roll(G, 1, 1) + np.roll(G, -1, 1) + Gp[:-2] + Gp[2:])
    return G


def sample_grid(G, U):
    u = nrm(U)
    p = np.degrees(np.arcsin(np.clip(u[:, 2], -1, 1)))
    t = np.degrees(np.arctan2(u[:, 0], -u[:, 1]))
    p = np.clip(p, GRID_PH[0], GRID_PH[-1])
    fi = (p - GRID_PH[0]) / 10.0
    fj = (t - GRID_TH[0]) / 10.0
    i0 = np.clip(np.floor(fi).astype(int), 0, len(GRID_PH) - 2); a = np.clip(fi - i0, 0, 1)
    j0 = np.floor(fj).astype(int) % len(GRID_TH); j1 = (j0 + 1) % len(GRID_TH); b = fj - np.floor(fj)
    return G[i0, j0] * (1 - a) * (1 - b) + G[i0, j1] * (1 - a) * b + G[i0 + 1, j0] * a * (1 - b) + G[i0 + 1, j1] * a * b


def place(q, rho_r, G_b, S_b, volume=1.0, dz_head=0.0, length=1.0, mirror=False):
    """Library vertices (normalised reference skull frame) onto the bald skull. Returns world points (Z up)."""
    q = np.asarray(q, float)
    r = np.linalg.norm(q, axis=1)
    u = q / np.maximum(r, 1e-9)[:, None]
    if mirror:
        u = u * np.array([-1.0, 1.0, 1.0])
    rho_b = sample_grid(G_b, u)
    delta = np.clip(rho_b - rho_r, -0.2, 0.2)
    off = r - rho_r
    r_new = rho_r + delta + np.where(off > 0, off * volume, off)
    Q = u * r_new[:, None]
    z = Q[:, 2]
    Q[:, 2] = np.where(z < -0.2, -0.2 + (z + 0.2) * length, z)
    W = to_world(S_b, Q)
    W[:, 2] += dz_head * S_b["HH"]
    return W


def mirror_tris(tris, mirror):
    return np.asarray(tris)[:, ::-1] if mirror else np.asarray(tris)


def mirror_uv(uv, mirror):
    return np.asarray(uv)[:, ::-1] if mirror else np.asarray(uv)


# --------------------------------------------------------------------------------------------- picture side


class PicMap:
    """Model (1.7 m, front -y) to picture pixels, from the picture measures (`target`) and an alignment."""

    def __init__(self, T, S, k=1.0, dx=0.0, dy=0.0):
        self.T, self.ztop, self.hcx, self.k, self.dx, self.dy = T, S["ztop"], S["hcx"], k, dx, dy

    def __call__(self, P):
        P = np.asarray(P)
        s = self.T["H_px"] / HEIGHT * self.k
        return np.stack([self.T["head_cx_px"] + self.dx + (P[..., 0] - self.hcx) * s,
                         self.T["top_px"] + self.dy + (self.ztop - P[..., 2]) * s], -1)


def raster_depth(xy, depth, tris, W, H, buf=None):
    """Smallest depth per pixel over the triangles (orthographic)."""
    if buf is None:
        buf = np.full((H, W), np.inf)
    tris = np.asarray(tris)
    if not len(tris):
        return buf
    A, B, C = xy[tris[:, 0]], xy[tris[:, 1]], xy[tris[:, 2]]
    dA, dB, dC = depth[tris[:, 0]], depth[tris[:, 1]], depth[tris[:, 2]]
    L = np.maximum.reduce([np.linalg.norm(A - B, axis=1), np.linalg.norm(B - C, axis=1), np.linalg.norm(C - A, axis=1)])
    k_all = np.clip(np.ceil(L * 1.5).astype(int) + 1, 2, 40)
    for k in np.unique(k_all):
        sel = k_all == k
        i, j = np.meshgrid(np.arange(k + 1), np.arange(k + 1))
        m = (i + j) <= k
        a = (i[m] / k)[None, :]
        b = (j[m] / k)[None, :]
        Px = A[sel][:, None, 0] * (1 - a - b) + B[sel][:, None, 0] * a + C[sel][:, None, 0] * b
        Py = A[sel][:, None, 1] * (1 - a - b) + B[sel][:, None, 1] * a + C[sel][:, None, 1] * b
        D = dA[sel][:, None] * (1 - a - b) + dB[sel][:, None] * a + dC[sel][:, None] * b
        x = np.round(Px).astype(int).ravel(); y = np.round(Py).astype(int).ravel(); D = D.ravel()
        ok = (x >= 0) & (x < W) & (y >= 0) & (y < H)
        np.minimum.at(buf, (y[ok], x[ok]), D[ok])
    return buf


def iou(a, b, reg):
    a, b = a & reg, b & reg
    u = (a | b).sum()
    return float((a & b).sum() / u) if u else 0.0


def score_vis(vis, hair, reg, eye_boxes, pic_eye_cover):
    s = iou(vis, hair, reg)
    pen = 0.0
    for b, pc in zip(eye_boxes, pic_eye_cover):
        b = [int(round(v)) for v in b]
        box = vis[max(0, b[1]):b[3], max(0, b[0]):b[2]]
        if box.size:
            pen += max(0.0, float(box.mean()) - pc) / 2
    return s - 0.5 * pen, s, pen


# --------------------------------------------------------------------------------------------- colour


def srgb_to_lin(c):
    c = np.asarray(c, float) / 255.0
    return np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)


def lin_to_srgb(c):
    c = np.clip(c, 0, 1)
    return np.where(c > 0.0031308, 1.055 * c ** (1 / 2.4) - 0.055, 12.92 * c)


def to_lab(rgb255):
    c = srgb_to_lin(rgb255)
    M = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = c @ M.T / np.array([0.9505, 1.0, 1.089])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def recolour(grey, alpha, colour, mask=None, gain=None):
    """Tint a grey hair texture (luminance 0..1, median hair texel 0.5) with the picture's tones.
    colour: {dark, median, light} sRGB 0..255. The median tone is multiplied by the texture's relative brightness
    (compressed by a power of 0.75); the darker half leans to the picture's dark tone and the brightest texels to the
    light tone, so the hue is the picture's and the strand detail the library's. Returns sRGB 0..255 float (H, W, 3)."""
    dark, med, light = srgb_to_lin(colour["dark"]), srgb_to_lin(colour["median"]), srgb_to_lin(colour["light"])
    g = np.asarray(grey, float) / 0.5
    gg = np.clip(g, 0, 2.5) ** 0.75
    col = med * gg[..., None]
    dk = np.clip((1.0 - g) / 0.6, 0, 1)[..., None]
    col = col + (dark - col) * 0.7 * dk
    lt = np.clip((g - 1.5) / 0.6, 0, 1)[..., None]
    col = col + (light - col) * 0.5 * lt
    # balance: the average of the hair texels (linear) is brought to the picture's median colour, channel by channel
    # within limits, so a texture with a strong light or dark cast does not read grey or washed out
    m = (np.asarray(alpha) > 0.5) if alpha is not None else np.ones(col.shape[:2], bool)
    if m.any():
        mean = col[m].mean(0)
        f = np.clip(med / np.maximum(mean, 1e-5), 0.3, 3.0)
        col = col * f
    if gain is not None:
        col = col * np.asarray(gain, float)
    return lin_to_srgb(col) * 255.0


def colour_delta(rgb255, alpha, colour):
    """Lab distance between the average of the opaque recoloured texels (averaged in linear light, as the eye mixes
    strands at a distance) and the picture's median hair colour."""
    m = np.asarray(alpha) > 127 if alpha is not None else np.ones(rgb255.shape[:2], bool)
    if not m.any():
        return None
    got = lin_to_srgb(srgb_to_lin(np.asarray(rgb255, float)[m]).mean(0)) * 255
    return float(np.linalg.norm(to_lab(got) - to_lab(np.asarray(colour["median"], float))))


# --------------------------------------------------------------------------------------------- Blender parts


def _cast_fn(bvh):
    def cast(o, d, dist):
        h = bvh.ray_cast(Vector(o), Vector(d), dist)
        return None if h[0] is None else np.array(h[0])
    return cast


def bvh_from(V, T):
    return BVHTree.FromPolygons([Vector(v) for v in V], [tuple(int(i) for i in t) for t in T])


def load_body(path):
    """Mesh of a glb (all mesh parts joined, small helper meshes left out), as Z-up arrays (V, T)."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    before = set(bpy.data.objects)
    try:
        bpy.ops.import_scene.gltf(filepath=str(path), bone_shape_type="NONE")
    except TypeError:
        bpy.ops.import_scene.gltf(filepath=str(path))
    obs = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
    big = [o for o in obs if len(o.data.vertices) >= 300] or obs
    Vs, Ts, base = [], [], 0
    dg = bpy.context.evaluated_depsgraph_get()
    for o in big:
        for m in list(o.modifiers):
            if m.type == "ARMATURE":
                o.modifiers.remove(m)
        me = o.data
        me.calc_loop_triangles()
        v = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", v); v = v.reshape(-1, 3)
        mw = np.array(o.matrix_world)
        v = v @ mw[:3, :3].T + mw[:3, 3]
        t = np.empty(len(me.loop_triangles) * 3, int); me.loop_triangles.foreach_get("vertices", t)
        Vs.append(v); Ts.append(t.reshape(-1, 3) + base); base += len(v)
    return np.vstack(Vs), np.vstack(Ts)


def push_out(W, bvh, eps=PUSH_EPS, reach=0.05):
    moved = 0
    for i in range(len(W)):
        hn = bvh.find_nearest(Vector(W[i]), reach)
        if hn[0] is None:
            continue
        q, n = np.array(hn[0]), np.array(hn[1])
        if float(np.dot(W[i] - q, n)) < eps:
            W[i] = q + n * eps
            moved += 1
    return moved


def push_out_faces(W, T, bvh, eps=PUSH_EPS, rounds=2):
    """Large triangles can dip into a curved head between vertices that are outside it: where a triangle's centre or
    edge middles are closer than eps to the body, its three vertices move out along the surface normal by the
    shortfall."""
    moved = 0
    T = np.asarray(T)
    for _ in range(rounds):
        need = np.zeros(len(W))            # per vertex: the largest shortfall of any face around it (not the sum)
        dirn = np.zeros((len(W), 3))
        A, B, C = W[T[:, 0]], W[T[:, 1]], W[T[:, 2]]
        for pts in ((A + B + C) / 3, (A + B) / 2, (B + C) / 2, (C + A) / 2):
            for f in range(len(T)):
                hn = bvh.find_nearest(Vector(pts[f]), 0.03)
                if hn[0] is None:
                    continue
                q, n = np.array(hn[0]), np.array(hn[1])
                short = eps - float(np.dot(pts[f] - q, n))
                if short > 0:
                    for v in T[f]:
                        if short > need[v]:
                            need[v] = short
                            dirn[v] = n
        sel = need > 0
        if not sel.any():
            break
        W[sel] += dirn[sel] * np.minimum(need[sel], 0.006)[:, None]
        moved += int(sel.sum())
    return moved


def cap_grid(front, ear, cols=24, rows=6):
    th = np.linspace(-180, 180, cols, endpoint=False)
    lo = hairline(th, front, ear) - 2.5
    out = np.zeros((rows + 1, cols, 3))
    for r in range(rows + 1):
        f = r / rows
        ph = 90 - (90 - lo) * (f ** 0.9)
        out[r] = dirv(th, np.maximum(ph, lo)) if r else dirv(th, 89.5)
    return out


SIDES_FULL = True      # module switch for the before/after survey (tools); the app always uses True


def scalp_cap(S, bvh_body, bvh_hair, front=30.0, offset=CAP_OFFSET, extra_rows=0, sides_full=None):
    """Cap under the hair (see the module text). `extra_rows` > 0 lowers the cap edge for more cover."""
    G = cap_grid(front - 4 * extra_rows, S["ear_phi"] - 4 * extra_rows)
    rows, cols = G.shape[:2]
    c = np.asarray(S["c"])
    P = np.zeros((rows, cols, 3))
    for r in range(rows):
        for k in range(cols):
            p = to_world(S, G[r, k][None])[0]
            d = nrm(p - c)
            h = bvh_body.ray_cast(Vector(c), Vector(d), 0.4)
            P[r, k] = (np.array(h[0]) + d * offset) if h[0] is not None else p
    V = [P[0].mean(0)] + [P[r, k] for r in range(1, rows) for k in range(cols)]
    F, RC = [], []          # faces and their (row, column) cell
    idx = lambda r, k: 1 + (r - 1) * cols + (k % cols)
    for k in range(cols):
        F.append((0, idx(1, k), idx(1, k + 1))); RC.append((0, k))
    for r in range(1, rows - 1):
        for k in range(cols):
            a, b, cc, d = idx(r, k), idx(r, k + 1), idx(r + 1, k + 1), idx(r + 1, k)
            F.append((a, d, cc)); RC.append((r, k)); F.append((a, cc, b)); RC.append((r, k))
    V = np.array(V)
    hit = np.zeros((rows, cols), bool)
    for f, (r, k) in zip(F, RC):
        p = V[list(f)].mean(0)
        d = nrm(p - c)
        if bvh_hair.ray_cast(Vector(p + d * 0.001), Vector(d), 0.06)[0] is not None:
            hit[r, k] = True
    # gaps in the hair (a thin shell's holes, card gaps) are covered too: a cell is kept when the hair is met at it
    # or further down its column, so the cap reaches from the crown down to the lowest row the hair covers there
    lowest = np.full(cols, -1)
    th_cols = np.linspace(-180, 180, cols, endpoint=False)
    full = SIDES_FULL if sides_full is None else sides_full
    for k in range(cols):
        rr = np.nonzero(hit[:, k])[0]
        if len(rr):
            lowest[k] = rr.max()
            # at the sides and the back (not over the forehead) the cap goes all the way down to the hairline wherever
            # the column has hair: a pulled-back style leaves the scalp above and behind the ears uncovered, and a
            # hair-coloured cap there reads as short hair, not skin (the live ponytail: 8 percent of the sides bare)
            if full and abs(th_cols[k]) >= 60:
                lowest[k] = rows - 1
    keep = [f for f, (r, k) in zip(F, RC) if r <= lowest[k]]
    UV = np.array([(0.5, 0.5)] + [(0.5 + 0.5 * (r / (rows - 1)) * math.cos(2 * math.pi * k / cols),
                                    0.5 + 0.5 * (r / (rows - 1)) * math.sin(2 * math.pi * k / cols))
                                   for r in range(1, rows) for k in range(cols)])
    return V, np.array(keep, int).reshape(-1, 3), UV


def skin_through(BV, BN, S, bvh_hair, front):
    """Share of scalp points above the hairline (crown: above 40 degrees; sides: the rest) whose outward ray meets no
    hair within 6 cm."""
    q = to_local(S, BV)
    r = np.linalg.norm(q, axis=1)
    u = q / np.maximum(r, 1e-9)[:, None]
    ph = np.degrees(np.arcsin(np.clip(u[:, 2], -1, 1)))
    th = np.degrees(np.arctan2(u[:, 0], -u[:, 1]))
    scalp = (r < 1.25) & (ph > hairline(th, front, S["ear_phi"]) + 3)
    # the ears are skin, not scalp: their rim stands out through a pulled-back style above the hairline the generic
    # rule assumes (the live ponytail's 8 percent "sides" were all ear rim)
    ear = (np.abs(th) >= 70) & (np.abs(th) <= 130) & (ph < S["ear_phi"] + 6)
    scalp &= ~ear
    out = {}
    for name, sel in (("crown", scalp & (ph > 40)), ("sides", scalp & (ph <= 40))):
        idx = np.nonzero(sel)[0][::2]
        bare = sum(1 for i in idx if bvh_hair.ray_cast(Vector(BV[i] + BN[i] * 1e-4), Vector(BN[i]), 0.06)[0] is None)
        out[name] = round(bare / max(1, len(idx)), 4)
        out[name + "_points"] = int(len(idx))
    tot = out["crown_points"] + out["sides_points"]
    out["all"] = round((out["crown"] * out["crown_points"] + out["sides"] * out["sides_points"]) / max(1, tot), 4)
    return out


def vertex_normals(V, T):
    n = np.zeros_like(V)
    fn = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    for k in range(3):
        np.add.at(n, T[:, k], fn)
    return nrm(n)


# --------------------------------------------------------------------------------------------- the two modes


def _setup_body(job):
    V, T = load_body(job["glb"])
    V = gltf_to_z(V) if False else V           # the importer already gives Z up
    yaw = float(job.get("yaw", 0) or 0)
    if yaw:
        V = V @ yaw_matrix(yaw).T
    Vn, s, z0 = normalise(V)
    pic = json.load(open(job["picture_json"]))
    S = find_skull(Vn, pic["target"]["head_h"])
    bvh = bvh_from(Vn, T)
    G = radius_grid(S, _cast_fn(bvh))
    return dict(V=Vn, T=T, s=s, z0=z0, yaw=yaw, S=S, bvh=bvh, G=G, pic=pic)


def _read_png(path):
    img = bpy.data.images.load(str(path), check_existing=False)
    w, h = img.size
    a = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, img.channels)[::-1]
    ch = img.channels
    bpy.data.images.remove(img)
    return a, ch


def _picture(job, body):
    """Hair mask, eye boxes and the alignment of the model's front outline with the bald picture's cut-out."""
    pic = body["pic"]
    T = pic["target"]
    out, _ = _read_png(job["outline"])
    hair = out[..., 0] > 0.5
    cut, ch = _read_png(job["bald_cutout"])
    alpha = cut[..., 3] > 0.5 if ch == 4 else cut[..., 0] > 0.05
    H, W = hair.shape
    if alpha.shape != hair.shape:      # nearest resize to the outline's size
        yy = (np.arange(H) * alpha.shape[0] / H).astype(int); xx = (np.arange(W) * alpha.shape[1] / W).astype(int)
        alpha = alpha[yy][:, xx]
    V, Tr, S = body["V"], body["T"], body["S"]
    keep = V[:, 2] > S["ztop"] - 2.6 * S["HH"]
    BT = Tr[keep[Tr].all(1)]
    rows = (max(0, int(T["top_px"] - 20)), min(H, int(T["neck_px"] + 1.2 * (T["neck_px"] - T["top_px"]))))
    reg = np.zeros((H, W), bool); reg[rows[0]:rows[1]] = True

    def sil(pm):
        return np.isfinite(raster_depth(pm(V), V[:, 1], BT, W, H))
    cur = dict(k=1.0, dx=0.0, dy=0.0)
    steps = dict(k=0.03, dx=6.0 * W / 1024, dy=6.0 * W / 1024)
    best = (iou(sil(PicMap(T, S)), alpha, reg), dict(cur))
    start = best[0]
    for _ in range(4):
        improved = True
        while improved:
            improved = False
            for key in ("k", "dx", "dy"):
                for sg in (-1, 1):
                    t = dict(best[1]); t[key] += sg * steps[key]
                    sc = iou(sil(PicMap(T, S, **t)), alpha, reg)
                    if sc > best[0] + 1e-4:
                        best = (sc, t); improved = True
        steps = {k: v / 2 for k, v in steps.items()}
    PM = PicMap(T, S, **best[1])
    head_depth = raster_depth(PM(V), V[:, 1], BT, W, H)
    ys, xs = np.nonzero(hair)
    if len(ys):
        x0 = max(0, min(xs.min(), int(T["head_cx_px"] - 0.8 * T["head_w"] * T["H_px"])) - 30)
        x1 = min(W, max(xs.max(), int(T["head_cx_px"] + 0.8 * T["head_w"] * T["H_px"])) + 30)
        y0 = max(0, min(ys.min(), int(T["top_px"])) - 30)
        y1 = min(H, max(ys.max(), int(T["neck_px"] + 0.3 * (T["neck_px"] - T["top_px"]))) + 40)
    else:
        x0, y0, x1, y1 = 0, 0, W, H
    if job.get("head_only"):
        # tied styles (ponytail, bun, braid): only the outline on the head counts; the tail or braid below the jaw is
        # mostly behind the head in a front picture, or falls where the picture's pose put it (over a shoulder)
        y1 = min(y1, int(T["neck_px"]))
    EV = np.zeros((H, W), bool); EV[y0:y1, x0:x1] = True
    return dict(PM=PM, hair=hair, EV=EV, box=[int(x0), int(y0), int(x1), int(y1)], head_depth=head_depth, W=W, H=H,
                align=dict(start_iou=round(start, 4), iou=round(best[0], 4), **{k: round(v, 4) for k, v in best[1].items()}))


def _score(P, body, st, params):
    W = place(st["q"], st["rho_r"], body["G"], body["S"], **params)
    tris = mirror_tris(st["tris"], params.get("mirror", False))
    buf = raster_depth(P["PM"](W), W[:, 1], tris, P["W"], P["H"])
    vis = np.isfinite(buf) & (buf < P["head_depth"] - 0.001)
    pj = body["pic"]
    return score_vis(vis, P["hair"], P["EV"], pj.get("eye_boxes", []), pj.get("pic_eye_cover", [])), vis


def search(P, body, st, full=True, deadline=None):
    """Grid then coordinate search over volume, dz_head, length and mirror. Returns (score, iou, pen, params)."""
    best = None
    if full:
        grid = [(m, v, d, l) for m in (False, True) for v in (0.9, 1.1, 1.3, 1.5) for d in (-0.08, 0.0, 0.08) for l in (0.8, 1.0, 1.25)]
    else:
        grid = [(m, v, d, l) for m in (False, True) for v in (0.95, 1.25, 1.5) for d in (-0.06, 0.06) for l in (0.75, 1.1)]
    for m, v, d, l in grid:
        p = dict(volume=v, dz_head=d, length=l, mirror=m)
        (sc, s_iou, pen), _ = _score(P, body, st, p)
        if best is None or sc > best[0]:
            best = (sc, s_iou, pen, p)
        if deadline and time.time() > deadline:
            return best
    if full:
        lim = dict(volume=VOLUME, dz_head=DZ, length=LENGTH)
        steps = dict(volume=0.1, dz_head=0.045, length=0.15)
        for _ in range(3):
            for key in ("length", "volume", "dz_head"):
                for sg in (-1, 1):
                    p = dict(best[3]); p[key] = float(np.clip(p[key] + sg * steps[key], *lim[key]))
                    if p[key] == best[3][key]:
                        continue
                    (sc, s_iou, pen), _ = _score(P, body, st, p)
                    if sc > best[0] + 1e-4:
                        best = (sc, s_iou, pen, p)
            steps = {k: v / 2 for k, v in steps.items()}
            if deadline and time.time() > deadline:
                break
    return best


def load_style(path):
    d = np.load(path)
    return {k: d[k] for k in d.files}


def build(body, st, params, front, cover="normal"):
    """Placed hair plus cap, pushed out. Returns arrays in the working frame and measures."""
    mir = bool(params.get("mirror", False))
    W = place(st["q"], st["rho_r"], body["G"], body["S"], **params)
    tris = mirror_tris(st["tris"], mir)
    uv = mirror_uv(st["uv"], mir)
    moved = push_out(W, body["bvh"])
    big = np.linalg.norm(W[tris[:, 0]] - W[tris[:, 1]], axis=1) > 0.012       # only shells with large triangles need it
    if big.any():
        moved += push_out_faces(W, tris[big], body["bvh"])
    hb = bvh_from(W, tris)
    cV, cF, cUV = scalp_cap(body["S"], body["bvh"], hb, front=front, extra_rows=1 if cover == "more" else 0)
    if len(cF):
        push_out_faces(cV, cF, body["bvh"], eps=CAP_OFFSET)
    flow = st["flow"].astype(float) * (np.array([-1.0, 1, 1]) if mir else 1)
    # flow to the world frame (rotate with the skull's tilt; radii scale is close to uniform)
    flow = nrm(flow @ rot_x(body["S"]["pitch"]).T)
    role = np.asarray(st["role"]).astype(np.int8)
    return dict(W=W, tris=tris, uv=uv, cV=cV, cF=cF, cUV=cUV, flow=flow, role=role, pushed_out=int(moved))


def to_model_space(body, P):
    """Working frame (normalised, turned) back to the body's own glTF Y-up model space."""
    P = (np.asarray(P, float) + np.array([0, 0, body["z0"]])) / body["s"]
    if body["yaw"]:
        P = P @ yaw_matrix(-body["yaw"]).T
    return z_to_gltf(P)


def _render(path, body, hair_parts, size=384, yaw=0, hair_only=False):
    """Small preview: the body in light grey and the hair with its texture, orthographic, from `yaw`."""
    sc = bpy.context.scene
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    sc.render.engine = "BLENDER_EEVEE"
    sc.render.resolution_x = sc.render.resolution_y = size
    sc.render.film_transparent = hair_only
    sc.view_settings.view_transform = "Standard"
    w = bpy.data.worlds.new("w"); w.use_nodes = True
    w.node_tree.nodes["Background"].inputs[0].default_value = (0.62, 0.63, 0.66, 1)
    w.node_tree.nodes["Background"].inputs[1].default_value = 0.6
    sc.world = w

    def obj(name, V, T, CU=None, mat=None):
        me = bpy.data.meshes.new(name)
        me.from_pydata(np.asarray(V).tolist(), [], np.asarray(T).tolist()); me.update()
        if CU is not None:
            uvl = me.uv_layers.new(name="UVMap"); uvl.data.foreach_set("uv", np.asarray(CU, np.float32).reshape(-1))
        for p in me.polygons:
            p.use_smooth = True
        ob = bpy.data.objects.new(name, me); sc.collection.objects.link(ob)
        if mat:
            ob.data.materials.append(mat)
        return ob
    skin = bpy.data.materials.new("skin"); skin.use_nodes = True
    skin.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.42, 0.40, 0.39, 1)
    bo = obj("body", body["V"], body["T"], mat=skin)
    if hair_only:
        bo.hide_render = True
    for name, V, T, CU, mat in hair_parts:
        obj(name, V, T, CU, mat)
    cd = bpy.data.cameras.new("c"); cd.type = "ORTHO"
    cam = bpy.data.objects.new("c", cd); sc.collection.objects.link(cam); sc.camera = cam
    S = body["S"]
    cd.ortho_scale = 2.6 * S["HH"]
    centre = Vector((S["hcx"], S["hcy"], S["ztop"] - 0.9 * S["HH"]))
    a = math.radians(yaw)
    cam.location = centre + Vector((math.sin(a), -math.cos(a), 0)) * 4
    cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
    for nm, rot, e in (("key", (55, 0, -35 + yaw), 2.4), ("fill", (70, 0, 50 + yaw), 0.8), ("rim", (-40, 0, 180 + yaw), 1.5)):
        ld = bpy.data.lights.new(nm, "SUN"); ld.energy = e
        lo = bpy.data.objects.new(nm, ld); lo.rotation_euler = [math.radians(x) for x in rot]; sc.collection.objects.link(lo)
    sc.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


def hair_material(name, base_png, normal_png, cutout, two_sided):
    m = bpy.data.materials.new(name); m.use_nodes = True
    nt = m.node_tree; bs = nt.nodes.get("Principled BSDF")
    bs.inputs["Roughness"].default_value = 0.55; bs.inputs["Metallic"].default_value = 0.0
    t = nt.nodes.new("ShaderNodeTexImage"); t.image = bpy.data.images.load(str(base_png), check_existing=False)
    nt.links.new(t.outputs["Color"], bs.inputs["Base Color"])
    if cutout:
        gt = nt.nodes.new("ShaderNodeMath"); gt.operation = "GREATER_THAN"; gt.inputs[1].default_value = 0.5
        nt.links.new(t.outputs["Alpha"], gt.inputs[0]); nt.links.new(gt.outputs[0], bs.inputs["Alpha"])
        try:
            m.surface_render_method = "DITHERED"
        except Exception:
            pass
    if normal_png and os.path.exists(normal_png):
        t2 = nt.nodes.new("ShaderNodeTexImage"); t2.image = bpy.data.images.load(str(normal_png), check_existing=False)
        t2.image.colorspace_settings.name = "Non-Color"
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nt.links.new(t2.outputs["Color"], nm.inputs["Color"]); nt.links.new(nm.outputs["Normal"], bs.inputs["Normal"])
    m.use_backface_culling = not two_sided and not cutout
    return m


def flat_material(name, rgb255):
    m = bpy.data.materials.new(name); m.use_nodes = True
    bs = m.node_tree.nodes.get("Principled BSDF")
    bs.inputs["Base Color"].default_value = (*[float(v) for v in srgb_to_lin(rgb255)], 1); bs.inputs["Roughness"].default_value = 0.6
    return m


def recolour_png(grey_png, colour, out_png, size=None, gain=None):
    """Grey LA library texture -> RGBA (or RGB) recoloured PNG through Blender's image IO. Returns colour delta."""
    a, ch = _read_png(grey_png)
    L = a[..., 0]
    alpha = a[..., 3] if ch == 4 else None
    rgb = recolour(L, alpha, colour, gain=gain)
    al255 = None if alpha is None else alpha * 255
    has_cut = alpha is not None and float((alpha < 0.5).mean()) > 0.01
    delta = colour_delta(rgb, al255, colour)
    H, W = L.shape
    img = bpy.data.images.new("rc", W, H, alpha=True)
    out = np.concatenate([rgb / 255.0, (alpha if alpha is not None else np.ones_like(L))[..., None]], -1)[::-1]
    img.pixels.foreach_set(out.astype(np.float32).ravel())
    img.filepath_raw = str(out_png); img.file_format = "PNG"
    if size and size != W:
        img.scale(int(size), int(size))
    img.save()
    bpy.data.images.remove(img)
    return delta, has_cut


def cap_texel(png):
    """UV of a fully opaque texel near the dark end of the hair texture, for the scalp cap (one material slot: the cap
    samples the hair texture there, so it is hair-coloured and never cut out)."""
    a, ch = _read_png(png)
    H, W = a.shape[:2]
    lum = a[..., :3].mean(-1)
    ok = a[..., 3] > 0.98 if ch == 4 else np.ones((H, W), bool)
    # keep away from texels next to transparent ones (mipmaps would pull the cap's alpha down)
    if ch == 4:
        k = max(1, W // 128)
        pad = np.pad(ok, k, constant_values=False)
        inner = np.ones_like(ok)
        for dy in (-k, 0, k):
            for dx in (-k, 0, k):
                inner &= pad[k + dy:k + dy + H, k + dx:k + dx + W]
        ok = inner if inner.any() else ok
    if not ok.any():
        return (0.5, 0.5)
    vals = lum[ok]
    target = np.percentile(vals, 20)
    ys, xs = np.nonzero(ok)
    i = int(np.argmin(np.abs(vals - target)))
    return ((xs[i] + 0.5) / W, 1.0 - (ys[i] + 0.5) / H)


def group_paths(W, role, n_points=7):
    """Per hang group, a path root first (the head end) through slice centroids of its placed vertices."""
    out = []
    for k in sorted(set(int(r) - 2 for r in np.unique(role) if r >= 2)):
        P = W[role == 2 + k]
        if len(P) < 10:
            continue
        zs = np.linspace(P[:, 2].max(), P[:, 2].min(), n_points)
        half = max(1e-4, abs(zs[0] - zs[-1]) / (n_points - 1) * 0.75)
        path = [P[np.abs(P[:, 2] - z) <= half].mean(0) for z in zs if (np.abs(P[:, 2] - z) <= half).any()]
        if len(path) >= 2:
            out.append((k, np.array(path)))
    return out


def rank_mode(job):
    t0 = time.time()
    deadline = t0 + float(job.get("seconds", 150)) - 25        # leave time to build and render the best
    out = job["out_dir"]
    os.makedirs(out, exist_ok=True)
    body = _setup_body(job)
    P = _picture(job, body)
    res = []
    full = bool(job.get("family"))
    for s in job["styles"]:
        if time.time() > deadline:
            break
        st = load_style(s["npz"])
        try:
            sc, s_iou, pen, p = search(P, body, st, full=full, deadline=deadline)
        except Exception as e:  # one bad style must not stop the ranking
            print("STYLE_ERROR", s["id"], e, flush=True)
            continue
        res.append(dict(style=s["id"], score=round(float(sc), 4), iou=round(float(s_iou), 4), eye_penalty=round(float(pen), 4),
                        params={k: (bool(v) if k == "mirror" else round(float(v), 4)) for k, v in p.items()}))
        print("STYLE", json.dumps(res[-1]), flush=True)
    res.sort(key=lambda r: -r["score"])
    cand = []
    for i, r in enumerate(res[:int(job.get("top", 4))]):
        s = next(x for x in job["styles"] if x["id"] == r["style"])
        st = load_style(s["npz"])
        b = build(body, st, r["params"], front=s.get("front", 30.0))
        r["triangles"] = int(len(b["tris"]) + len(b["cF"]))
        files = {}
        try:
            mat = hair_material("h", _recoloured(job, s, out, i + 1), s.get("normal"), s.get("alpha") == "cutout", s.get("two_sided", False))
            cap = flat_material("cap", np.asarray(job["colour"]["dark"]) * 0.85)
            parts = [("hair", b["W"], b["tris"], b["uv"], mat), ("cap", b["cV"], b["cF"], None, cap)]
            for view, yaw in (("front", 0), ("quarter", 35)):
                p = os.path.join(out, f"hair_cand_{i + 1}_{view}.png")
                _render(p, body, parts, size=int(job.get("render_px", 384)), yaw=yaw)
                files[view] = p
        except Exception as e:
            print("RENDER_ERROR", r["style"], e, flush=True)
        r["files"] = files
        cand.append(r)
    rep = dict(candidates=cand, ranked=res, align=P["align"], skull=body["S"], seconds=round(time.time() - t0, 1),
               searched=len(res), offered=len(job["styles"]), timed_out=len(res) < len(job["styles"]))
    json.dump(rep, open(os.path.join(out, "rank.json"), "w"), indent=1)


def _recoloured(job, s, out, n):
    p = os.path.join(out, f"cand_{n}_base.png")
    recolour_png(s["grey"], job["colour"], p)
    return p


def attach_mode(job):
    t0 = time.time()
    out = job["out_dir"]
    os.makedirs(out, exist_ok=True)
    body = _setup_body(job)
    s = job["style"]
    st = load_style(s["npz"])
    params = job.get("params")
    score = None
    if not params:
        P = _picture(job, body)
        sc, s_iou, pen, params = search(P, body, st, full=True)
        score = dict(score=round(float(sc), 4), iou=round(float(s_iou), 4), eye_penalty=round(float(pen), 4))
    elif job.get("measure", True):
        P = _picture(job, body)
        (sc, s_iou, pen), _ = _score(P, body, st, params)
        score = dict(score=round(float(sc), 4), iou=round(float(s_iou), 4), eye_penalty=round(float(pen), 4))
    eye_cover = None
    if score is not None:
        _, vis = _score(P, body, st, params)
        eye_cover = [round(float(vis[max(0, int(b[1])):int(b[3]), max(0, int(b[0])):int(b[2])].mean()), 3)
                     for b in body["pic"].get("eye_boxes", [])]
    b = build(body, st, params, front=s.get("front", 30.0), cover=job.get("cover", "normal"))
    hb = bvh_from(np.vstack([b["W"], b["cV"]]), np.vstack([b["tris"], b["cF"] + len(b["W"])]))
    BN = vertex_normals(body["V"], body["T"])
    skin = skin_through(body["V"], BN, body["S"], hb, s.get("front", 30.0))
    delta, has_cut = recolour_png(s["grey"], job["colour"], os.path.join(out, "hair_base_color.png"), size=job.get("tex"))
    cu = cap_texel(os.path.join(out, "hair_base_color.png"))
    # canonical npz: hair first, cap after (cap_first false), glTF Y-up metres in the body's model space
    nV = len(b["W"])
    verts = to_model_space(body, np.vstack([b["W"], b["cV"]]))
    tris = np.vstack([b["tris"], b["cF"] + nV]).astype(np.int32)
    cap_uv = np.zeros((len(b["cF"]), 3, 2)) + np.array(cu)
    uv = np.concatenate([b["uv"], cap_uv], 0).astype(np.float32)
    role = np.concatenate([b["role"], np.ones(len(b["cV"]), np.int8)]).astype(np.int8)
    flow_w = np.vstack([b["flow"], np.tile([0, 0, -1.0], (len(b["cV"]), 1))])
    if body["yaw"]:
        flow_w = flow_w @ yaw_matrix(-body["yaw"]).T
    flow = z_to_gltf(flow_w).astype(np.float32)
    np.savez_compressed(os.path.join(out, "hair_mesh.npz"), verts=verts.astype(np.float32), tris=tris, uv=uv, role=role, flow=flow)
    groups = []
    for k, path_w in group_paths(b["W"], b["role"]):
        pm = to_model_space(body, path_w)
        groups.append(dict(k=int(k), kind="Hair", path=np.round(pm, 5).tolist(),
                           length_m=round(float(np.linalg.norm(np.diff(pm, axis=0), axis=1).sum()), 4)))
    top_hair = verts[:, 1].max(); top_body = to_model_space(body, body["V"])[:, 1].max()
    parts = dict(style=s["id"], groups=groups, cap_first=False, extra_height_m=round(float(max(0.0, top_hair - top_body)), 4))
    json.dump(parts, open(os.path.join(out, "hair_parts.json"), "w"), indent=1)
    # previews for the fit sheet
    files = {}
    render_delta = None
    try:
        mat = hair_material("h", os.path.join(out, "hair_base_color.png"), s.get("normal"), s.get("alpha") == "cutout", s.get("two_sided", False))
        parts_r = [("hair", np.vstack([b["W"], b["cV"]]), np.vstack([b["tris"], b["cF"] + nV]), uv, mat)]
        for view, yaw in (("front", 0), ("quarter", 35), ("side", 90), ("back", 180)):
            p = os.path.join(out, f"fit_{view}.png")
            _render(p, body, parts_r, size=384, yaw=yaw)
            files[view] = p
        # what the preview render reads as (median of the hair's own pixels, front and three-quarter), recorded only:
        # the preview's white lights and grey world add grey to dark hair, so correcting the texture against it pushed
        # dark browns towards red (the live ponytail). The texture is matched to the picture instead (colour_delta).
        px = []
        for yaw in (0, 35):
            p = os.path.join(out, f"_hair_only_{yaw}.png")
            _render(p, body, parts_r, size=256, yaw=yaw, hair_only=True)
            a, _ = _read_png(p)
            os.remove(p)
            px.append(a[a[..., 3] > 0.9][:, :3])
        px = np.vstack(px)
        if len(px):
            render_delta = float(np.linalg.norm(to_lab(np.median(px, 0) * 255) - to_lab(np.asarray(job["colour"]["median"], float))))
    except Exception as e:
        print("RENDER_ERROR", e, flush=True)
    if job.get("export", True):
        try:
            export_glbs(job, out, verts, tris, uv, os.path.join(out, "hair_base_color.png"), s)
        except Exception as e:
            print("EXPORT_ERROR", e, flush=True)
    rep = dict(style=s["id"], params=params, score=score, eye_cover=eye_cover, triangles=int(len(tris)), cap_triangles=int(len(b["cF"])),
               hair_triangles=int(len(b["tris"])), pushed_out=b["pushed_out"], skin_through=skin, colour_delta=None if delta is None else round(delta, 2),
               render_delta=None if render_delta is None else round(render_delta, 2),
               extra_height_m=parts["extra_height_m"], skull=body["S"], renders=files, seconds=round(time.time() - t0, 1),
               scale=body["s"], groups=len(groups))
    json.dump(rep, open(os.path.join(out, "attach.json"), "w"), indent=1)


def export_glbs(job, out, verts, tris, uv, base_png, s):
    """hair.glb (the hair alone, material VGAC_Hair) and with_hair.glb (the body as given plus the hair) for the viewer."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    me = bpy.data.meshes.new("VGAC_Hair")
    me.from_pydata(gltf_to_z(verts).tolist(), [], np.asarray(tris).tolist()); me.update()
    uvl = me.uv_layers.new(name="UVMap"); uvl.data.foreach_set("uv", np.asarray(uv, np.float32).reshape(-1))
    for p in me.polygons:
        p.use_smooth = True
    ob = bpy.data.objects.new("VGAC_Hair", me); bpy.context.scene.collection.objects.link(ob)
    mat = hair_material("VGAC_Hair", base_png, s.get("normal"), s.get("alpha") == "cutout", s.get("two_sided", False))
    mat.name = "VGAC_Hair"
    ob.data.materials.append(mat)
    bpy.ops.object.select_all(action="DESELECT"); ob.select_set(True); bpy.context.view_layer.objects.active = ob
    bpy.ops.export_scene.gltf(filepath=os.path.join(out, "hair.glb"), use_selection=True, export_format="GLB")
    try:
        bpy.ops.import_scene.gltf(filepath=str(job["glb"]), bone_shape_type="NONE")
    except TypeError:
        bpy.ops.import_scene.gltf(filepath=str(job["glb"]))
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.export_scene.gltf(filepath=os.path.join(out, "with_hair.glb"), use_selection=True, export_format="GLB")


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    mode, job_path = argv[0], argv[1]
    job = json.load(open(job_path))
    {"rank": rank_mode, "attach": attach_mode}[mode](job)


if __name__ == "__main__":
    main()
