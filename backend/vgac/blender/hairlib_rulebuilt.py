"""Rule-built hairstyles for the hair library (pure numpy), ported from the round-one trial generator
(runtime/tmp/hairlib/hairgen.py, which is not in git). Used by tools/hairlib_build.py for the rule-built library styles:
waist-length long hair with a centre parting, a high ponytail and a bun (docs/HAIR-LIBRARY-PLAN.md section 4).

The generator text below is the trial's, unchanged in behaviour; `place_on` (wrap onto the scalp, push out of the body,
smooth) and `grey_texture` (the strip painter in grey) are added for the library builder.

Parametric hairstyle generator (pure numpy, no Blender) for the hair-library trial.

REFERENCE SKULL
  The hair is defined on a normalised skull: an ellipsoid (the cranium, from the top of the head down to about the brow
  and the top of the ears) with its centre at the origin, x to the character's left, y to the back, z up (the character
  faces -y, as glTF models do after import into Blender). Directions on it are written (theta, phi) in degrees:
  theta is the angle round the head from the front (0 = front, +90 = character's left ear, 180 = back),
  phi is the elevation (90 = top, 0 = the widest ring, about brow height at the front). A point at height h above the
  skull in direction u is  R*u + h*normal(u)  with R = (rx, ry, rz) the half-width, half-depth and half-height.
  Reference radii (a 1.7 m adult) R0 = (0.078, 0.098, 0.100) m. The fit (fit.py) measures the character's own
  centre, radii and forward tilt and the generator is then evaluated with those radii, so every style follows the
  character's skull; lengths that hang below the skull are in units of rz (the head's half-height).

STYLES and their numbers
  crop      short crop (men). length 0..1: how far the hair reaches below the hairline at the sides and back.
  swept     swept-back short (men). length 0..1: as for crop, at the nape.
  bob       bob. length: tip height below the skull centre in rz (chin is about 1.2).
  long      long straight. length: as bob (shoulders about 2.5, chest about 4, waist about 6).
  ponytail  pulled back to a tie. length: tail length in rz. tie: elevation of the tie at the back (deg).
  bun       pulled back to a bun. length: bun radius in rz (0.3 to 0.6). tie: elevation of the bun (deg).
  volume    metres the outer hair stands off the skull.
  fringe    elevation (deg) of the front edge of the hair over the forehead. Above the natural hairline (about 30)
            it moves the hairline itself; for bob and long, below it, it is the tip of a fringe over the forehead.
  parting   left, middle, right or none (left = the character's left).

Each style is a modest number of large locks. A lock is a centreline with a lens-shaped cross-section (6 points),
closed at the root and at the tip; its texture strip has U across the lock and V from root (0) to tip (1).
Locks are grouped in fans (neighbours side by side) and each lock's width is set from the distance to its
neighbours so the fan closes with overlap. Parts that hang free carry a sway weight per point (vertex group).
"""
import math
import numpy as np

R0 = np.array([0.078, 0.098, 0.100])
STYLES = ("crop", "swept", "bob", "long", "ponytail", "bun")
DEFAULTS = {
    "crop": dict(length=0.5, volume=0.010, fringe=28.0, parting="none", tie=0.0),
    "swept": dict(length=0.5, volume=0.014, fringe=30.0, parting="none", tie=0.0),
    "bob": dict(length=1.3, volume=0.016, fringe=8.0, parting="middle", tie=0.0),
    "long": dict(length=4.0, volume=0.014, fringe=30.0, parting="middle", tie=0.0),
    "ponytail": dict(length=3.5, volume=0.008, fringe=30.0, parting="none", tie=45.0),
    "bun": dict(length=0.45, volume=0.008, fringe=30.0, parting="none", tie=50.0),
}
PARTING_THETA = {"left": 24.0, "middle": 0.0, "right": -24.0, "none": None}
RING = 6          # points round the lens cross-section


def nrm(v):
    v = np.asarray(v, float)
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-12)


def dirv(theta, phi):
    t, p = np.radians(theta), np.radians(phi)
    return np.stack([np.sin(t) * np.cos(p), -np.cos(t) * np.cos(p), np.sin(p) * np.ones_like(t)], -1)


def elev(u):
    return np.degrees(np.arcsin(np.clip(nrm(u)[..., 2], -1, 1)))


def surf(u, R, h):
    """Point at height h over the ellipsoid R in direction u, and the outward normal there."""
    u = nrm(u)
    e = R * u
    n = nrm(u / R)
    return e + np.asarray(h)[..., None] * n, n


def slerp(a, b, n):
    a, b = nrm(a), nrm(b)
    d = float(np.clip(np.dot(a, b), -1, 1))
    om = math.acos(d)
    t = np.linspace(0, 1, n)[:, None]
    if om < 1e-4:
        return np.repeat(a[None], n, 0)
    return (np.sin((1 - t) * om) * a + np.sin(t * om) * b) / math.sin(om)


class Skull:
    """Radii of the character's cranium plus the two picture/ear numbers the styles need."""

    def __init__(self, R=R0, ear_phi=-12.0, ear_out=0.0):
        self.R = np.asarray(R, float)
        self.ear_phi = float(ear_phi)   # elevation of the top of the ear at theta 90
        self.ear_out = float(ear_out)   # how far the ear stands out past the ellipsoid (m)


def hairline(theta, front, ear):
    """Elevation (deg) of the hairline round the head, symmetric. Passes above the ears and down to the nape."""
    th = np.abs((np.asarray(theta, float) + 180) % 360 - 180)
    kx = [0, 35, 68, 92, 112, 140, 180]
    ky = [front, front - 5, ear + 14, ear + 5, ear - 8, -40, -47]
    return np.interp(th, kx, ky)


class Lock:
    def __init__(self, pts, wrap, fan, order, group="scalp", hang=None, taper="round", centre=None, lift=None):
        self.pts = np.asarray(pts, float)
        n = len(self.pts)
        self.wrap = np.broadcast_to(np.asarray(wrap, float), (n,)).copy()   # 1 = lies on the scalp (wrapped by the fit)
        self.hang = np.zeros(n) if hang is None else np.asarray(hang, float)  # sway weight 0..1
        self.lift = np.zeros(n) if lift is None else np.asarray(lift, float)  # height over the scalp the wrap keeps
        self.fan, self.order, self.group, self.taper = fan, order, group, taper
        self.centre = centre        # None: frames point away from the head; else away from this point (bun)
        self.width = None
        self.thick = None


def resample(lock, n):
    p = lock.pts
    seg = np.linalg.norm(np.diff(p, axis=0), axis=1)
    s = np.concatenate([[0], np.cumsum(seg)])
    if s[-1] < 1e-6:
        s = np.linspace(0, 1, len(p))
    t = np.linspace(0, s[-1], n)
    lock.pts = np.stack([np.interp(t, s, p[:, k]) for k in range(3)], -1)
    for a in ("wrap", "hang", "lift"):
        setattr(lock, a, np.interp(t, s, getattr(lock, a)))
    return lock


def smooth_pts(p, it=2, fix_root=True):
    p = p.copy()
    for _ in range(it):
        q = p.copy()
        q[1:-1] = 0.25 * p[:-2] + 0.5 * p[1:-1] + 0.25 * p[2:]
        if fix_root:
            q[0] = p[0]
        q[-1] = p[-1]
        p = q
    return p


def scalp_path(u0, u1, h0, h1, R, m=20, hmid=None):
    """Great-circle path over the skull between two directions, height h0 -> h1 (optionally a bump hmid)."""
    U = slerp(u0, u1, m)
    t = np.linspace(0, 1, m)
    h = h0 + (h1 - h0) * t
    if hmid is not None:
        h = h + (hmid - (h0 + h1) / 2) * np.sin(np.pi * t)
    P, N = surf(U, R, h)
    return P, h


def hang_path(p0, d0, g, z_tip, k=0.18, ds=0.004, tuck=0.0, axis_xy=(0.0, 0.0), out=None, most=400):
    """Falls from p0 starting along d0, bending towards the gravity direction g until z <= z_tip.
    tuck > 0 curls the last part in towards the head axis (a bob's ends)."""
    p, d = np.array(p0, float), nrm(d0)
    pts = [p.copy()]
    z0 = p[2]
    for _ in range(most):
        f = (z0 - p[2]) / max(1e-6, z0 - z_tip)
        gg = np.array(g, float)
        if out is not None:
            gg = gg + out
        if tuck > 0 and f > 0.7:
            inward = np.array([axis_xy[0] - p[0], axis_xy[1] - p[1], 0.0])
            gg = gg + tuck * nrm(inward)
        d = nrm(d + k * (nrm(gg) - d))
        p = p + d * ds
        pts.append(p.copy())
        if p[2] <= z_tip:
            break
    return np.array(pts)


def _ear_h(skull, th, V):
    """Side height so a hanging curtain clears the ear."""
    s = np.clip(1 - np.abs(np.abs(th) - 95) / 40, 0, 1)
    return np.maximum(V, (skull.ear_out + 0.008) * s)


# --------------------------------------------------------------------------------------------- styles

def style_crop(sk, P, rng):
    R, V = sk.R, P["volume"]
    front = min(P["fringe"], 36.0)
    pth = PARTING_THETA.get(P["parting"])
    locks = []
    N = 18
    whorl = dirv(172, 64)
    ext = 4 + 20 * P["length"]          # degrees below the hairline
    for i in range(N):
        th = -180 + 360 * (i + 0.5) / N
        # short at the sides: the ears stay clear; only the back reaches down by `length`
        ph_tip = float(hairline(th, front, sk.ear_phi)) - (ext if abs(th) > 125 else 1.0)
        tip_th = th
        if pth is not None and abs(th) < 70:
            tip_th = th - 0.6 * pth * (1 - abs(th) / 70)  # combed away from the parting
        root = nrm(whorl + 0.10 * nrm(dirv(th, 40) - whorl))
        hv = V * (1.0 + 0.12 * rng.standard_normal())
        hfront = hv * (1.7 if abs(th) < 50 else (1.0 if abs(th) > 110 else 0.7))
        hend = hv * (0.9 if abs(th) < 50 else 0.3)
        pts, h = scalp_path(root, dirv(tip_th, ph_tip), 0.35 * hv, hend, R, 22, hmid=hfront)
        locks.append(Lock(pts, 1.0, "crop", i, lift=h, taper="flat"))
    return locks


def style_swept(sk, P, rng):
    R, V = sk.R, P["volume"]
    front = min(P["fringe"], 36.0)
    pth = PARTING_THETA.get(P["parting"]) or 0.0
    locks = []
    # back fill underneath: crown down to the nape
    nb = 8
    for i in range(nb):
        th = 100 + 160 * (i + 0.5) / nb
        ph_tip = float(hairline(th, front, sk.ear_phi)) - (3 + 14 * P["length"])
        pts, h = scalp_path(dirv(180, 52), dirv(th, ph_tip), 0.35 * V, 0.25 * V, R, 18, hmid=0.55 * V)
        locks.append(Lock(pts, 1.0, "back", i, lift=h, taper="flat"))
    # front: from the hairline straight back over the top; the temples go back above the ears
    nf = 12
    for i in range(nf):
        th = -105 + 210 * (i + 0.5) / nf
        a = min(abs(th), 105.0) / 105.0
        ph0 = float(hairline(th + 0.3 * pth, front, sk.ear_phi)) + 1.0
        th_end = float(np.sign(th) * (180 - 58 * a)) if abs(th) > 0.5 else 180.0
        ph_end = 34 - 46 * a - 8 * P["length"]
        hq = V * (1.8 - 1.45 * a)
        pts, h = scalp_path(dirv(th + 0.3 * pth, ph0), dirv(th_end, ph_end), V * (0.8 - 0.5 * a), V * (0.5 - 0.25 * a), R, 26, hmid=hq)
        lk = Lock(pts, 1.0, "front", i, lift=h, taper="round")
        lk.root_taper = 1.0
        locks.append(lk)
    return locks


def _parting_line(pth, front, m):
    a = dirv(pth, front + 1.0)
    b = dirv(180 - 0.3 * pth, 62)
    return slerp(a, b, m)


def _falling(sk, P, rng, long=False):
    R, V = sk.R, P["volume"]
    front = 30.0 if P["fringe"] < 27 else min(P["fringe"], 36.0)
    pth = PARTING_THETA.get(P["parting"])
    crown = dirv(180, 60)
    z_tip = -P["length"] * R[2]
    locks = []
    g = np.array([0, 0, -1.0])
    if pth is None:
        line = slerp(dirv(0, front + 6), crown, 8)
        pth0 = 0.0
    else:
        line = _parting_line(pth, front, 8)
        pth0 = pth
    # side fans: rooted along the parting, released round the side at ear-top height, then falling
    ns = 6
    for side in (+1, -1):
        for j in range(ns):
            f = (j + 0.5) / ns
            root = line[min(len(line) - 1, int(round(f * (len(line) - 1))))]
            th_rel = side * (52 + 100 * f)
            if pth is not None:
                th_rel += 0.25 * pth0 * (1 - f)
            ph_rel = sk.ear_phi + 6 - 6 * f
            hs = float(_ear_h(sk, th_rel, V)) * (1 + 0.1 * rng.standard_normal())
            pts, h = scalp_path(root, dirv(th_rel, ph_rel), 0.35 * V, hs, R, 18, hmid=1.1 * hs)
            d0 = nrm(pts[-1] - pts[-2])
            drape_front = long and abs(th_rel) < 95
            gg = g + (np.array([0, -0.22, 0]) if drape_front else (np.array([0, 0.18, 0]) if long else 0))
            outv = np.array([side * 0.06, 0, 0]) if (long and not drape_front) else None
            hp = hang_path(pts[-1], d0, gg, z_tip + (0.06 * R[2] * (1 - f) if not long else 0), k=0.35 if long else 0.2,
                           tuck=0.0 if long else 1.0, out=outv)
            allp = np.vstack([pts, hp[1:]])
            m1, m2 = len(pts), len(hp) - 1
            wrap = np.concatenate([np.ones(m1), np.linspace(1, 0, m2) ** 3 if m2 else []])
            hang = np.concatenate([np.zeros(m1), np.linspace(0, 1, m2) if m2 else []])
            lift = np.concatenate([h, np.full(m2, hs)])
            grp = ("long_front" if drape_front else "long_back") if long else "scalp"
            fan = f"F{side}" if drape_front else "ring"
            order = (th_rel % 360) if not drape_front else j
            locks.append(Lock(allp, wrap, fan, order, group=grp, hang=hang if long else hang * 0.5,
                              taper="long" if long else "round", lift=lift))
    # back fan from the crown
    nb = 6 if long else 4
    for j in range(nb):
        th_rel = 152 + 56 * (j + 0.5) / nb
        pts, h = scalp_path(crown, dirv(th_rel, -18), 0.35 * V, V, R, 14, hmid=1.15 * V)
        d0 = nrm(pts[-1] - pts[-2])
        gg = g + (np.array([0, 0.15, 0]) if long else 0)
        hp = hang_path(pts[-1], d0, gg, z_tip + (0 if long else 0.04 * R[2]), k=0.35 if long else 0.2, tuck=0.0 if long else 1.0)
        allp = np.vstack([pts, hp[1:]])
        m1, m2 = len(pts), len(hp) - 1
        wrap = np.concatenate([np.ones(m1), np.linspace(1, 0, m2) ** 3 if m2 else []])
        hang = np.concatenate([np.zeros(m1), np.linspace(0, 1, m2) if m2 else []])
        lift = np.concatenate([h, np.full(m2, V)])
        locks.append(Lock(allp, wrap, "ring", th_rel % 360, group="long_back" if long else "scalp", hang=hang if long else 0.5 * hang,
                          taper="long" if long else "round", lift=lift))
    # fringe: rooted on the front of the parting, combed forward over the forehead
    if P["fringe"] < 27:
        nf = 6
        for j in range(nf):
            th_tip = -50 + 100 * (j + 0.5) / nf
            if pth is None or pth0 == 0:
                # straight fringe, a little longer in the middle
                ph_tip = P["fringe"] + 7 * (abs(th_tip) / 48) ** 2
                root = dirv(0.35 * th_tip, 64)
            else:
                # side-swept: rooted along the front of the parting, combed across to the far side,
                # short next to the parting and longest on the far side
                sg = np.sign(pth0)
                s_ = np.clip((th_tip * sg + 50) / 100, 0, 1)          # 1 = parting side
                th_tip = th_tip - 0.35 * pth0
                ph_tip = P["fringe"] + 22 * s_ ** 1.3
                root = dirv(pth0 * 0.9, front + 6 + 26 * (1 - s_))
            pts, h = scalp_path(root, dirv(th_tip, ph_tip), 0.45 * V, 0.6 * V, R, 18, hmid=1.3 * V)
            locks.append(Lock(pts, 1.0, "fringe", j, taper="round", lift=h))
    return locks


def style_bob(sk, P, rng):
    return _falling(sk, P, rng, long=False)


def style_long(sk, P, rng):
    return _falling(sk, P, rng, long=True)


def _pulled_back(sk, P, rng, tie):
    R, V = sk.R, P["volume"]
    front = min(P["fringe"], 36.0)
    tdir = dirv(180, tie)
    locks = []
    N = 16
    for i in range(N):
        th = -180 + 360 * (i + 0.5) / N
        ph = float(hairline(th, front, sk.ear_phi)) - 1.5
        u0 = dirv(th, ph)
        tgt = nrm(tdir + 0.12 * nrm(u0 - tdir))
        hv = V * (1 + 0.08 * rng.standard_normal())
        pts, h = scalp_path(u0, tgt, 0.6 * hv, 0.9 * hv, R, 22, hmid=hv)
        lk = Lock(pts, 1.0, "pull", i, lift=h, taper="flat")
        lk.root_taper = 1.0
        locks.append(lk)
    return locks, tdir


def style_ponytail(sk, P, rng):
    R, V = sk.R, P["volume"]
    tie = P["tie"]
    locks, tdir = _pulled_back(sk, P, rng, tie)
    p0, n0 = surf(tdir, R, np.array(V + 0.004))
    up = np.array([0, 0, 1.0])
    d0 = nrm(0.7 * n0 + up * (0.25 + 1.4 * max(0.0, tie - 40) / 50 if tie > 40 else 0.1))
    z_tip = p0[2] - P["length"] * R[2]
    rt = 0.40 * R[0] * (1 + 8 * max(0.0, V - 0.006))
    hp = hang_path(p0, d0, np.array([0, 0.05, -1.0]), z_tip, k=0.16, ds=0.004)
    hp = smooth_pts(hp, 3)
    m = len(hp)
    # four sub-locks in a bundle: offsets in the plane across the tail
    T = nrm(np.gradient(hp, axis=0))
    side = nrm(np.cross(T, np.array([0, 0, 1.0])) + 1e-6)
    side[np.linalg.norm(np.cross(T, np.array([0, 0, 1.0])), axis=1) < 1e-3] = [1, 0, 0]
    upv = nrm(np.cross(side, T))
    s = np.linspace(0, 1, m)
    bulge = np.interp(s, [0, 0.1, 0.4, 1], [0.6, 1.0, 0.95, 0.4])
    for k, (a, b) in enumerate([(0.0, 1.0), (1.0, 0.0), (0.0, -1.0), (-1.0, 0.0), (0.0, 0.0)]):
        off = (side * a + upv * b) * (0.5 * rt * bulge)[:, None]
        pts = hp + off
        pts[0] = p0 - n0 * 0.006
        hang = np.clip(s * 1.3, 0, 1)
        lk = Lock(pts, 0.0, "tail", k, group="ponytail", hang=hang, taper="long")
        lk.fixed_width = (1.75 if (a or b) else 1.2) * rt * bulge
        lk.side_axis = (0.0, 1.0, 0.0) if a else (1.0, 0.0, 0.0)
        locks.append(lk)
    tie_frame = (p0 + d0 * 0.012, d0, rt * 0.62)
    return locks, dict(tie=tie_frame)


def style_bun(sk, P, rng):
    R, V = sk.R, P["volume"]
    tie = P["tie"]
    locks, tdir = _pulled_back(sk, P, rng, tie)
    rb = P["length"] * R[2]
    p0, n0 = surf(tdir, R, np.array(V))
    c = p0 + n0 * rb * 0.62
    # two interleaved spirals from the outer pole of the bun down towards the head
    ax = n0
    e1 = nrm(np.cross(ax, [0, 0, 1.0]) + 1e-9)
    e2 = np.cross(ax, e1)
    turns = 2.6
    m = 70
    t = np.linspace(0, 1, m)
    lat = np.radians(88 - 140 * t)          # from the outer pole round and down towards the head
    ang = 2 * np.pi * turns * t
    r = rb * (0.9 + 0.1 * np.sin(np.pi * t))
    pts = c + (np.cos(lat) * np.cos(ang) * r)[:, None] * e1 + (np.cos(lat) * np.sin(ang) * r)[:, None] * e2         + (np.sin(lat) * r)[:, None] * ax
    lk = Lock(pts, 0.0, "bun", 0, group="bun", taper="round", centre=c)
    lk.fixed_width = np.interp(t, [0, 0.08, 0.85, 1], [0.35, 1, 1, 0.6]) * (np.radians(140) / turns) * rb * 1.55
    lk.n_samples = 44
    locks.append(lk)
    return locks, dict(bun_centre=c, tie=(p0 + n0 * 0.004, n0, rb * 0.55))


def generate(style, params, skull, seed=1):
    """Returns (locks, extras) in the skull's local frame (metres, centre at the origin)."""
    P = dict(DEFAULTS[style])
    P.update({k: v for k, v in (params or {}).items() if v is not None})
    rng = np.random.default_rng(seed)
    fn = {"crop": style_crop, "swept": style_swept, "bob": style_bob, "long": style_long,
          "ponytail": style_ponytail, "bun": style_bun}[style]
    out = fn(skull, P, rng)
    locks, extras = (out if isinstance(out, tuple) else (out, {}))
    extras["front"] = 30.0 if (style in ("bob", "long") and P["fringe"] < 27) else min(P["fringe"], 36.0)
    extras["params"] = P
    return locks, extras


def cap_grid(front, ear, cols=24, rows=6):
    """Directions of the scalp cap: from the top down to just below the hairline, (rows+1, cols)."""
    th = np.linspace(-180, 180, cols, endpoint=False)
    lo = hairline(th, front, ear) - 2.5
    out = np.zeros((rows + 1, cols, 3))
    for r in range(rows + 1):
        f = r / rows
        ph = 90 - (90 - lo) * (f ** 0.9)
        out[r] = dirv(th, np.maximum(ph, lo)) if r else dirv(th, 89.5)
    return out


# --------------------------------------------------------------------------------------------- mesh

def lock_samples(lock, base=0.006):
    if hasattr(lock, "n_samples"):
        return lock.n_samples
    L = float(np.linalg.norm(np.diff(lock.pts, axis=0), axis=1).sum())
    return int(np.clip(round(L / 0.021), 7, 14))


def set_widths(locks, lo=0.008, hi=0.085, overlap=1.7):
    """Width from the distance to the neighbouring locks of the same fan at the same fraction of length."""
    fans = {}
    for lk in locks:
        fans.setdefault(lk.fan, []).append(lk)
    for name, fl in fans.items():
        fl.sort(key=lambda l: l.order)
        cyclic = name in ("crop", "pull")
        for i, lk in enumerate(fl):
            if hasattr(lk, "fixed_width"):
                w = np.asarray(lk.fixed_width, float)
                if len(w) != len(lk.pts):
                    w = np.interp(np.linspace(0, 1, len(lk.pts)), np.linspace(0, 1, len(w)), w)
                lk.width = w
                continue
            ds = []
            for j in (i - 1, i + 1):
                if cyclic:
                    j %= len(fl)
                if 0 <= j < len(fl) and j != i:
                    o = fl[j]
                    t = np.linspace(0, 1, len(lk.pts))
                    to = np.linspace(0, 1, len(o.pts))
                    q = np.stack([np.interp(t, to, o.pts[:, k]) for k in range(3)], -1)
                    ds.append(np.linalg.norm(q - lk.pts, axis=1))
            w = np.max(ds, axis=0) * overlap if ds else np.full(len(lk.pts), 0.03)
            w = np.clip(w, lo, hi)
            # do not let the width collapse where neighbours converge at the root, keep it smooth along the lock
            for _ in range(3):
                w[1:-1] = 0.25 * w[:-2] + 0.5 * w[1:-1] + 0.25 * w[2:]
            lk.width = w
    for lk in locks:
        s = np.linspace(0, 1, len(lk.pts))
        r0 = getattr(lk, "root_taper", 0.65)
        knots = {"round": (0.85, 0.9), "point": (0.6, 0.3), "long": (0.72, 0.5), "flat": (0.9, 1.0)}[lk.taper]
        tp = np.interp(s, [0, 0.12, knots[0], 1.0], [r0, 1.0, 1.0, knots[1]])
        lk.width = lk.width * tp
        lk.thick = np.clip(0.30 * lk.width, 0.0035, 0.010)
        if lk.group == "ponytail":
            lk.thick = np.clip(0.5 * lk.width, 0.004, 0.018)
        elif lk.fan == "pull":
            lk.thick = np.clip(0.16 * lk.width, 0.003, 0.006)    # pulled tight: sleek, flat locks
            lk.thick = lk.thick * np.interp(s, [0, 0.15, 1], [0.35, 1, 1])   # thin at the hairline: no ledge


def frames(lock, head_c, head_rz):
    p = lock.pts
    t = nrm(np.gradient(p, axis=0))
    if getattr(lock, "side_axis", None) is not None:
        s = np.asarray(lock.side_axis, float)
        b = nrm(s[None] - (t @ s)[:, None] * t)
        n = nrm(np.cross(b, t))
        return t, n, b
    if lock.centre is not None:
        nd = nrm(p - lock.centre)
    else:
        v = p - head_c
        below = (head_c[2] - p[:, 2]) / head_rz
        v[:, 0] *= np.interp(below, [0.9, 1.8], [1.0, 0.3])
        fz = np.clip(1 - (head_c[2] - p[:, 2]) / (0.6 * head_rz), 0, 1)
        v[:, 2] *= np.where(p[:, 2] >= head_c[2], 1.0, fz)
        nd = nrm(v)
    n = np.zeros_like(p)
    prev = None
    for i in range(len(p)):
        a = nd[i] - np.dot(nd[i], t[i]) * t[i]
        if np.linalg.norm(a) < 0.3 and prev is not None:
            a = prev - np.dot(prev, t[i]) * t[i]
        a = nrm(a)
        if prev is not None and np.dot(a, prev) < 0:
            a = -a
        n[i] = a
        prev = a
    b = nrm(np.cross(t, n))
    return t, n, b


def build_mesh(locks, head_c, head_rz, ring=RING):
    """Tubes with a lens cross-section, closed at both ends (no open edges, no duplicated vertices).
    Returns verts, tris, corner UVs (nt, 3, 2) in lock-local 0..1 (U across the lock, V from root 0 to tip 1),
    lock id per vertex, sway weight per vertex and a description per lock."""
    V, F, CUV, LID, SW = [], [], [], [], []
    ang = -np.pi / 2 + 2 * np.pi * np.arange(ring) / ring      # start on the inner side (seam hidden)
    cx = np.cos(ang)
    cy = np.sin(ang) * np.where(np.sin(ang) > 0, 1.0, 0.55)
    base = 0
    meta = []
    for li, lk in enumerate(locks):
        t, n, b = frames(lk, head_c, head_rz)
        m = len(lk.pts)
        w, th = lk.width / 2, lk.thick / 2
        rp = lk.pts[:, None, :] + b[:, None, :] * (w[:, None, None] * cx[None, :, None])             + n[:, None, :] * (th[:, None, None] * cy[None, :, None])
        rounded = lk.taper in ("round", "flat", "long")
        if rounded:
            # a smaller ring a little past the end, so the end is rounded rather than a point
            endw = float(lk.width[-1])
            extra = lk.pts[-1] + t[-1] * 0.16 * endw + b[-1] * (0.62 * w[-1] * cx)[:, None] + n[-1] * (0.62 * th[-1] * cy)[:, None]
            rp = np.concatenate([rp, extra[None]], 0)
        mm = len(rp)
        vs = rp.reshape(-1, 3)
        root = lk.pts[0] - t[0] * 0.002
        endw = float(lk.width[-1])
        tip = lk.pts[-1] + t[-1] * (0.3 * endw if rounded else min(0.012, endw))
        V += [vs, root[None], tip[None]]
        nv = len(vs)
        LID.append(np.full(nv + 2, li))
        hg_ = np.concatenate([lk.hang, [lk.hang[-1]]]) if rounded else lk.hang
        SW.append(np.concatenate([np.repeat(hg_, ring), [lk.hang[0], lk.hang[-1]]]))
        vv = np.linspace(0, 1, m)
        if rounded:
            vv = np.concatenate([vv * 0.97, [0.985]])
        idx = lambda r, c: base + r * ring + (c % ring)
        for r in range(mm - 1):
            for c in range(ring):
                a, b_, c_, d = idx(r, c), idx(r, c + 1), idx(r + 1, c + 1), idx(r + 1, c)
                ua, ub = c / ring, (c + 1) / ring
                F.append((a, b_, c_)); CUV.append(((ua, vv[r]), (ub, vv[r]), (ub, vv[r + 1])))
                F.append((a, c_, d)); CUV.append(((ua, vv[r]), (ub, vv[r + 1]), (ua, vv[r + 1])))
        ri, ti = base + nv, base + nv + 1
        for c in range(ring):
            ua, ub = c / ring, (c + 1) / ring
            F.append((ri, idx(0, c + 1), idx(0, c))); CUV.append(((0.5, 0.0), (ub, 0.0), (ua, 0.0)))
            F.append((ti, idx(mm - 1, c), idx(mm - 1, c + 1))); CUV.append(((0.5, 1.0), (ua, 1.0), (ub, 1.0)))
        meta.append(dict(group=lk.group, fan=lk.fan, m=m, length=float(np.linalg.norm(np.diff(lk.pts, axis=0), axis=1).sum()),
                         width=float(np.median(lk.width)), z=lk.pts[:, 2].tolist()))
        base += nv + 2
    return dict(verts=np.vstack(V), tris=np.array(F, int), cuv=np.array(CUV, float), lock=np.concatenate(LID),
                sway=np.concatenate(SW), meta=meta)


def torus(c, axis, r_major, r_minor, nu=14, nv=6):
    axis = nrm(axis)
    e1 = np.cross(axis, [0, 0, 1.0])
    e1 = nrm(e1) if np.linalg.norm(e1) > 1e-3 else np.array([1.0, 0, 0])
    e2 = np.cross(axis, e1)
    V, F, UV = [], [], []
    for i in range(nu):
        a = 2 * np.pi * i / nu
        d = np.cos(a) * e1 + np.sin(a) * e2
        for j in range(nv):
            b = 2 * np.pi * j / nv
            V.append(c + d * (r_major + r_minor * np.cos(b)) + axis * r_minor * np.sin(b))
            UV.append((0.1 + 0.8 * i / nu, 0.1 + 0.8 * j / nv))
    for i in range(nu):
        for j in range(nv):
            a, b = i * nv + j, ((i + 1) % nu) * nv + j
            a2, b2 = i * nv + (j + 1) % nv, ((i + 1) % nu) * nv + (j + 1) % nv
            F.append((a, b, b2)); F.append((a, b2, a2))
    return np.array(V), np.array(F), np.array(UV, float)


# --------------------------------------------------------------------------------------------- library builder parts

RULE_STYLES = {
    "rule_long_centre": dict(style="long", params=dict(length=6.0, volume=0.012, fringe=30.0, parting="middle"),
                             name="Long straight, centre parting (rule-built)", family="long", fringe=False, parting="centre"),
    "rule_ponytail_high": dict(style="ponytail", params=dict(length=3.5, volume=0.004, fringe=30.0, tie=72.0),
                               name="High ponytail (rule-built)", family="ponytail", fringe=False, parting="none"),
    "rule_bun": dict(style="bun", params=dict(length=0.42, volume=0.005, fringe=30.0, tie=55.0),
                     name="Bun (rule-built)", family="bun", fringe=False, parting="none"),
}


def to_world_s(S, p):
    import math as _m
    a = S["pitch"]
    c, s = _m.cos(a), _m.sin(a)
    Rx = np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    return np.asarray(S["c"]) + np.asarray(p) @ Rx.T


def place_on(locks, S, ray, nearest):
    """Move locks from the skull frame to the world, wrap scalp parts onto the real scalp at their height, push every
    centreline point out of the body with a margin of its half-thickness plus 3 mm, smooth. ray(o, d, dist) -> hit or
    None; nearest(p) -> (point, normal) or None. Modifies the locks in place (the trial's fitlib.place)."""
    c = np.asarray(S["c"])
    for lk in locks:
        lk.pts = to_world_s(S, lk.pts)
        if lk.centre is not None:
            lk.centre = to_world_s(S, lk.centre)
        resample(lk, lock_samples(lk))
    for li, lk in enumerate(locks):
        # neighbouring locks meet at the crown at the same height; a small stagger in depth (0 to 1.8 mm) keeps their
        # surfaces from coinciding, which a renderer shows as speckles
        stagger = 0.0006 * (li % 4)
        for i in range(len(lk.pts)):
            if lk.wrap[i] <= 0.01:
                continue
            d = lk.pts[i] - c
            L = np.linalg.norm(d)
            if L < 1e-6:
                continue
            d = d / L
            h = ray(c, d, 0.4)
            if h is None:
                continue
            q = np.asarray(h) + d * (max(0.003, lk.lift[i]) + stagger)
            lk.pts[i] = (1 - lk.wrap[i]) * lk.pts[i] + lk.wrap[i] * q
    set_widths(locks)
    for it in range(4):
        for lk in locks:
            margin = 0.6 * lk.thick / 2 + 0.003
            for i in range(len(lk.pts)):
                hn = nearest(lk.pts[i])
                if hn is None:
                    continue
                q, nq = np.asarray(hn[0]), np.asarray(hn[1])
                if float(np.dot(lk.pts[i] - q, nq)) < margin[i]:
                    lk.pts[i] = q + nq * margin[i]
            if it < 3:
                lk.pts = smooth_pts(lk.pts, 1, fix_root=True)
    set_widths(locks)
    return locks


def atlas(locks, extras):
    """One square cell per lock plus one for the tie band; returns cell rects [x, y, w, h] in UV."""
    import math as _m
    n = len(locks) + 1
    g = int(_m.ceil(_m.sqrt(n)))
    cell = 1.0 / g
    pad = 0.06 * cell
    rects = []
    for i in range(n):
        r, k = divmod(i, g)
        rects.append([k * cell + pad, r * cell + pad, cell - 2 * pad, cell - 2 * pad])
    return rects


def assemble_rule(locks, extras, head_c, head_rz):
    """Lock tubes (and the tie band) with atlas corner UVs. Returns verts, tris, cuv, sway and group per vertex."""
    M = build_mesh(locks, head_c, head_rz)
    rects = atlas(locks, extras)
    R = np.array(rects)[M["lock"][M["tris"][:, 0]]]
    cuv = np.stack([R[:, None, 0] + M["cuv"][..., 0] * R[:, None, 2], R[:, None, 1] + M["cuv"][..., 1] * R[:, None, 3]], -1)
    V, T, CU = [M["verts"]], [M["tris"]], [cuv]
    grp = [np.array([1 if locks[i].group in ("ponytail", "long_back", "long_front") else 0 for i in M["lock"]])]
    sway = [M["sway"]]
    if "tie" in extras:
        p, ax, r = extras["tie"]
        tv, tf, tu = torus(np.asarray(p), np.asarray(ax), r, max(0.004, 0.25 * r))
        rr = rects[-1]
        tu2 = np.stack([rr[0] + tu[:, 0] * rr[2], rr[1] + tu[:, 1] * rr[3]], -1)
        V.append(tv); T.append(tf + len(M["verts"])); CU.append(tu2[tf]); grp.append(np.zeros(len(tv), int)); sway.append(np.zeros(len(tv)))
    return dict(verts=np.vstack(V), tris=np.vstack(T), cuv=np.vstack(CU), hang=np.concatenate(grp), sway=np.concatenate(sway),
                rects=rects, meta=M["meta"])


def grey_texture(rects, meta, size=1024, seed=7):
    """The strip painter in grey: luminance only, median hair texel 0.5 (the library's grey convention): darker
    underside and roots, strand streaks along V, a soft lighter band, lighter tips. Returns (size, size) float."""
    rng = np.random.default_rng(seed)
    img = np.full((size, size), 0.38)

    def noise1d(n, freq):
        k = max(2, int(freq)); gk = rng.random(k + 3); x = np.linspace(0, k, n); i = np.floor(x).astype(int); f = x - i
        f = f * f * (3 - 2 * f)
        return gk[i] * (1 - f) + gk[i + 1] * f
    for li, r in enumerate(rects[:len(meta)]):
        X0, X1 = int(round(r[0] * size)), int(round((r[0] + r[2]) * size))
        Y0, Y1 = int(round((1 - r[1] - r[3]) * size)), int(round((1 - r[1]) * size))
        W, H = max(1, X1 - X0), max(1, Y1 - Y0)
        u = ((np.arange(W) + 0.5) / W)[None, :]
        v = (1 - (np.arange(H) + 0.5) / H)[:, None]
        outer = 0.5 - 0.5 * np.cos(2 * np.pi * u)
        root = np.clip(v / 0.16, 0, 1)
        g = (0.62 + 0.38 * outer ** 0.8) * (0.6 + 0.4 * root)
        s1 = noise1d(W, 70)[None, :]
        g = g * (0.84 + 0.26 * s1) * (1 - 0.22 * (noise1d(W, 9)[None, :] > 0.85))
        g = g * (1 + 0.12 * np.clip((v - 0.8) / 0.2, 0, 1))
        img[Y0:Y1, X0:X1] = g
    if len(rects) > len(meta):
        r = rects[-1]
        X0, X1 = int(round(r[0] * size)), int(round((r[0] + r[2]) * size))
        Y0, Y1 = int(round((1 - r[1] - r[3]) * size)), int(round((1 - r[1]) * size))
        img[Y0:Y1, X0:X1] = 0.15
    med = np.median(img)
    return np.clip(img * (0.5 / med), 0, 1)
