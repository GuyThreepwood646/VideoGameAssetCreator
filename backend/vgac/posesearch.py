"""Find the camera a picture was taken from: turn, tilt and lens (WP-61 part C).

Each candidate pose is drawn from the model and compared with the picture after its centre and area are lined up with the
picture's (scale and shift, which a cut-out picture never shares with the model's frame). The score has three terms:
- outline: IoU of the model's outline against the picture's silhouette;
- inner edges: the model's creases and the colour edges of its built colour against the picture's own edges, as a
  symmetric chamfer distance tolerant to a few pixels. A box's top-face edges settle tilt and turn where its outline cannot;
- colour structure: the model's built colour against the picture on a coarse grid of cells, as the correlation of each Lab
  channel across the cells (offset and gain fall out, so lighting matters little).
A coarse grid (turn in 10 degree steps, tilt -20 to 60 in 10 degree steps, lens orthographic, 85, 50 or 35 mm) is scored on
the outline at a small size; the best of it get the edge and colour terms; the best three of those are refined locally to
2 degrees at the larger size with all three terms. Without a picture or a texture the search scores the outline only.

The term weights (W_EDGE, W_COLOUR) were chosen on the WP-61 Part A synthetic set (bakeoff/wp61bc, weights.json)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from vgac import face
from vgac import srcproject as sp

TURNS = list(range(0, 360, 10))
TILTS = list(range(-20, 61, 10))  # pictures taken from slightly below are real (WP-61 part C)
LENSES: list[float | None] = [None, 85.0, 50.0, 35.0]
COARSE_N = 128
REFINE_N = 192       # WP-61: 192 against 256 keeps the synthetic gate and cuts the refine time
KEEP_FULL = 24        # coarse poses (best outline first) that get the edge and colour terms
W_EDGE = 0.5          # weight of the inner-edge term (chosen on the synthetic set, bakeoff weights.json)
W_COLOUR = 2.0        # weight of the colour-structure term (chosen on the synthetic set, bakeoff weights.json)
EDGE_TAU_PX = 2.5     # chamfer distance (pixels at COARSE_N, scaled with the size) counted as a full miss
EDGE_SHARE = 0.12     # the strongest share of gradient pixels inside the figure that count as edges
CELLS = 10            # colour-structure grid, CELLS x CELLS over the picture's frame


@dataclass
class Pose:
    turn: float
    tilt: float
    lens: float | None
    iou: float                 # outline IoU after the centre-and-area alignment
    score: float = 0.0         # the combined score the search ranks by
    terms: dict = field(default_factory=dict)


@dataclass
class Target:
    """The picture at one size: silhouette, its frame (centre x, centre y, sqrt area), edges and their distance map, Lab."""
    n: int
    mask: np.ndarray
    frame: tuple[float, float, float] | None
    edges: np.ndarray | None = None
    dist: np.ndarray | None = None
    lab: np.ndarray | None = None


def _frame(m: np.ndarray) -> tuple[float, float, float] | None:
    ys, xs = np.nonzero(m)
    if len(ys) == 0:
        return None
    return float(xs.mean()) + 0.5, float(ys.mean()) + 0.5, float(np.sqrt(len(ys)))


def _align(img: np.ndarray, src: tuple[float, float, float], dst: tuple[float, float, float]) -> np.ndarray:
    """img (drawn in the model's frame) resampled into the picture's frame: centre onto centre, area onto area."""
    n = img.shape[0]
    k = src[2] / dst[2]
    g = np.arange(n) + 0.5
    x = np.broadcast_to((g[None, :] - dst[0]) * k + src[0], (n, n))
    y = np.broadcast_to((g[:, None] - dst[1]) * k + src[1], (n, n))
    inside = (x >= 0) & (x < n) & (y >= 0) & (y < n)
    out = img[np.clip(y.astype(int), 0, n - 1), np.clip(x.astype(int), 0, n - 1)]
    return np.where(inside if out.ndim == 2 else inside[..., None], out, 0)


def _edges(lum: np.ndarray, m: np.ndarray) -> np.ndarray:
    """The strongest EDGE_SHARE of the luminance gradient inside the figure (its own outline left out)."""
    gy, gx = np.gradient(face.blur(lum, 0.7))
    g = np.hypot(gx, gy)
    inner = face.erode(m, 1)
    if not inner.any():
        return np.zeros_like(m)
    thr = max(float(np.quantile(g[inner], 1.0 - EDGE_SHARE)), 0.02)
    return (g > thr) & inner


def _dist(edges: np.ndarray, cap: float) -> np.ndarray:
    """Distance (in pixels, by one-pixel dilations) to the nearest edge pixel, capped at `cap`."""
    d = np.full(edges.shape, float(cap))
    cur = edges.copy()
    for k in range(int(np.ceil(cap))):
        d[cur & (d >= cap)] = float(k)
        cur = face.dilate_mask(cur, 1)
    return d


def target(alpha: np.ndarray, n: int, rgb: np.ndarray | None = None) -> Target:
    m = sp.small(alpha, n) > 0.5
    t = Target(n, m, _frame(m))
    if rgb is not None:
        c = np.clip(sp.small(rgb[..., :3], n), 0.0, 1.0)
        t.edges = _edges(face.luminance(c), m)
        t.dist = _dist(t.edges, EDGE_TAU_PX * n / COARSE_N)
        t.lab = face.to_lab(c)
    return t


def _camera(centre: np.ndarray, pose: tuple[float, float, float | None], scale: float, n: int) -> face.Camera:
    turn, tilt, lens = pose
    return face.Camera(target=np.asarray(centre, dtype=np.float64), yaw_deg=float(turn), scale=float(scale), size=n,
                       tilt_deg=float(tilt), lens_mm=lens)


def _cells(lab: np.ndarray, m: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Mean Lab per cell and the share of each cell the mask covers."""
    k = n // CELLS
    lab = lab[:k * CELLS, :k * CELLS]
    w = m[:k * CELLS, :k * CELLS].astype(np.float64)
    sw = w.reshape(CELLS, k, CELLS, k).sum(axis=(1, 3))
    s = (lab * w[..., None]).reshape(CELLS, k, CELLS, k, 3).sum(axis=(1, 3))
    return s / np.maximum(sw[..., None], 1e-9), sw / (k * k)


def _colour_term(t: Target, lab: np.ndarray, m: np.ndarray) -> float:
    a, ca = _cells(t.lab, t.mask, t.n)
    b, cb = _cells(lab, m, t.n)
    use = (ca > 0.5) & (cb > 0.5)
    if use.sum() < 6:
        return 0.0
    rs = []
    for ch in range(3):
        x, y = a[..., ch][use], b[..., ch][use]
        if x.std() < 1.0 or y.std() < 1.0:   # a channel with no structure in either says nothing
            continue
        rs.append(float(np.corrcoef(x, y)[0, 1]))
    return float(np.mean(rs)) if rs else 0.0


def _edge_term(t: Target, model_edges: np.ndarray) -> float:
    tau = EDGE_TAU_PX * t.n / COARSE_N
    if not model_edges.any() or not t.edges.any():
        return 0.0
    a = np.minimum(t.dist[model_edges], tau).mean() / tau                 # model edges to the picture's
    b = np.minimum(_dist(model_edges, tau)[t.edges], tau).mean() / tau    # and back
    return float(1.0 - 0.5 * (a + b))


CREASE_COS = float(np.cos(np.radians(30.0)))  # neighbouring faces bent by more than this angle make a crease


def _draw(mesh: dict, cam: face.Camera, tex: np.ndarray) -> dict:
    """A fast drawing of the model for the score: per pixel the nearest triangle (smallest depth, as view_raster's depth
    test), its facing cosine and normal, and the built colour sampled from the texture at the pixel's UV (as
    srcproject.render_colour). Triangles are filled many at a time on the same pixel centres as srcproject.outline."""
    s = cam.size
    pts, dep = cam.project(mesh["verts"])
    F = mesh["faces"]
    P = pts[F]
    vis = (P[:, :, 0].max(1) >= 0) & (P[:, :, 0].min(1) <= s) & (P[:, :, 1].max(1) >= 0) & (P[:, :, 1].min(1) <= s)
    fidx = np.flatnonzero(vis)
    P, D = P[fidx], dep[F[fidx]]
    x0 = np.clip(np.floor(P[:, :, 0].min(1) - 0.5).astype(int), 0, s)
    x1 = np.clip(np.ceil(P[:, :, 0].max(1) - 0.5).astype(int) + 1, 0, s)
    y0 = np.clip(np.floor(P[:, :, 1].min(1) - 0.5).astype(int), 0, s)
    y1 = np.clip(np.ceil(P[:, :, 1].max(1) - 0.5).astype(int) + 1, 0, s)
    ax, ay, bx, by, cx, cy = P[:, 0, 0], P[:, 0, 1], P[:, 1, 0], P[:, 1, 1], P[:, 2, 0], P[:, 2, 1]
    area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    span = np.maximum(x1 - x0, y1 - y0)
    live = (x1 > x0) & (y1 > y0) & (np.abs(area) >= 1e-12)
    pix_l, f_l, z_l, w_l = [], [], [], []
    lo = 0
    while lo < s:
        hi = max(4, lo * 2)
        pick = np.flatnonzero(live & (span > lo) & (span <= hi))
        lo = hi
        step = max(1, 2_000_000 // (hi * hi))
        for c in range(0, len(pick), step):
            k = pick[c:c + step]
            off = np.arange(hi)
            xi = x0[k][:, None, None] + off[None, None, :]
            yi = y0[k][:, None, None] + off[None, :, None]
            X, Y = xi + 0.5, yi + 0.5
            a_ = area[k][:, None, None]
            AX, AY, BX, BY, CX, CY = (v[k][:, None, None] for v in (ax, ay, bx, by, cx, cy))
            w0 = ((BX - X) * (CY - Y) - (BY - Y) * (CX - X)) / a_
            w1 = ((CX - X) * (AY - Y) - (CY - Y) * (AX - X)) / a_
            w2 = 1.0 - w0 - w1
            inside = (w0 >= -1e-9) & (w1 >= -1e-9) & (w2 >= -1e-9) & (xi < x1[k][:, None, None]) & (yi < y1[k][:, None, None])
            kk = np.broadcast_to(k[:, None, None], inside.shape)[inside]
            xb, yb = np.broadcast_to(xi, inside.shape)[inside], np.broadcast_to(yi, inside.shape)[inside]
            W = np.stack([w0[inside], w1[inside], w2[inside]], axis=1)
            pix_l.append(yb * s + xb)
            f_l.append(kk)
            z_l.append((W * D[kk]).sum(1))
            w_l.append(W)
    out = {"mask": np.zeros((s, s), dtype=bool)}
    if not pix_l:
        out.update(cos=np.zeros((s, s)), nrm=np.zeros((s, s, 3)), colour=np.full((s, s, 3), sp.BG))
        return out
    pix, fk, z, W = np.concatenate(pix_l), np.concatenate(f_l), np.concatenate(z_l), np.concatenate(w_l)
    order = np.lexsort((z, pix))
    pix, fk, W = pix[order], fk[order], W[order]
    first = np.r_[True, pix[1:] != pix[:-1]]
    pix, fk, W = pix[first], fk[first], W[first]
    f = fidx[fk]
    v = mesh["verts"]
    tri = v[F[f]]
    fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
    cosv = cam.cos_to_camera(fn, tri.mean(axis=1))
    T = tex.shape[0]
    uvp = face.uv_pixels(mesh["uv"], T)[F[f]]
    uvx, uvy = (W * uvp[:, :, 0]).sum(1), (W * uvp[:, :, 1]).sum(1)
    mask = out["mask"].reshape(-1)
    mask[pix] = True
    cos = np.zeros(s * s)
    cos[pix] = cosv
    nrm = np.zeros((s * s, 3))
    nrm[pix] = fn
    col = np.full((s * s, 3), sp.BG)
    col[pix] = face.sample(tex, uvx, uvy)
    out.update(cos=cos.reshape(s, s), nrm=nrm.reshape(s, s, 3), colour=col.reshape(s, s, 3))
    return out


def _creases(nrm: np.ndarray, m: np.ndarray) -> np.ndarray:
    """Pixels where the neighbouring pixel's face is bent by more than the crease angle (both on the model)."""
    c = np.zeros_like(m)
    dx = (nrm[:, 1:] * nrm[:, :-1]).sum(-1) < CREASE_COS
    dy = (nrm[1:] * nrm[:-1]).sum(-1) < CREASE_COS
    both_x, both_y = m[:, 1:] & m[:, :-1], m[1:] & m[:-1]
    c[:, 1:] |= dx & both_x
    c[1:] |= dy & both_y
    return c


def score_pose(mesh: dict, t: Target, centre: np.ndarray, scale: float, pose: tuple[float, float, float | None],
               tex: np.ndarray | None = None) -> Pose:
    """One pose scored against the picture: the outline alone without tex (or without the picture's colour), else all three."""
    cam = _camera(centre, pose, scale, t.n)
    if tex is None or t.lab is None:
        m = sp.outline(mesh, cam)
        fm = _frame(m)
        if fm is None or t.frame is None:
            return Pose(float(pose[0]), float(pose[1]), pose[2], 0.0, 0.0)
        iou = float(sp.iou(t.mask, _align(m, fm, t.frame) > 0))
        return Pose(float(pose[0]), float(pose[1]), pose[2], iou, iou, {"outline": iou})
    dr = _draw(mesh, cam, tex)
    m = dr["mask"]
    fm = _frame(m)
    if fm is None or t.frame is None:
        return Pose(float(pose[0]), float(pose[1]), pose[2], 0.0, 0.0)
    rc = np.clip(dr["colour"], 0.0, 1.0)
    model_edges = (_creases(dr["nrm"], m) & face.erode(m, 1)) | _edges(face.luminance(rc), m)
    am = _align(m, fm, t.frame) > 0
    iou = float(sp.iou(t.mask, am))
    edge = _edge_term(t, _align(model_edges, fm, t.frame) > 0)
    colour = _colour_term(t, _align(face.to_lab(rc), fm, t.frame), am)
    s = iou + W_EDGE * edge + W_COLOUR * colour
    return Pose(float(pose[0]), float(pose[1]), pose[2], iou, s, {"outline": iou, "edge": edge, "colour": colour})


LENS_TOP = 12  # the best orthographic coarse poses that are also tried with each lens


def coarse(mesh: dict, alpha: np.ndarray, centre: np.ndarray, scale: float, n: int = COARSE_N,
           lenses: list[float | None] | None = None, turns: list[float] | None = None,
           tilts: list[float] | None = None) -> list[Pose]:
    """The coarse poses scored on the outline (after alignment), best first. turns and tilts default to the full grid.
    With the default lens set, the whole turn and tilt grid is scored orthographic, and only its LENS_TOP best are tried
    with each lens (a lens changes the outline far less than the turn and tilt do)."""
    t = target(alpha, n)
    lens_set = LENSES if lenses is None else lenses
    grid = [(float(turn), float(tilt)) for tilt in (TILTS if tilts is None else tilts) for turn in (TURNS if turns is None else turns)]
    first = lens_set[0]
    out = [score_pose(mesh, t, centre, scale, (tu, ti, first)) for tu, ti in grid]
    if len(lens_set) > 1:
        out.sort(key=lambda p: -p.score)
        out += [score_pose(mesh, t, centre, scale, (p.turn, p.tilt, lens)) for p in out[:LENS_TOP] for lens in lens_set[1:]]
    out.sort(key=lambda p: -p.score)
    return out


def _angdiff(a: float, b: float) -> float:
    return (float(a) - float(b) + 180.0) % 360.0 - 180.0


def refine(mesh: dict, alpha: np.ndarray, centre: np.ndarray, scale: float, start: Pose, n: int = REFINE_N,
           step: float = 2.0, span: float = 10.0, rgb: np.ndarray | None = None, tex: np.ndarray | None = None,
           t: Target | None = None, turn_limit: tuple[float, float] | None = None) -> Pose:
    """Local search around a pose: turn and tilt by a pattern search from span/2 down to `step` degrees, staying within
    `span` of the start, at size n, with every term the inputs allow. turn_limit (centre, half width): the turn also stays
    within half width of centre (the window the caller searched in)."""
    t = t or target(alpha, n, rgb if tex is not None else None)
    best = score_pose(mesh, t, centre, scale, (start.turn, start.tilt, start.lens), tex)
    stride = span / 2.0
    while stride >= step - 1e-9:
        moved = True
        while moved:
            moved = False
            for dt, dl in ((stride, 0), (-stride, 0), (0, stride), (0, -stride),
                           (stride, stride), (stride, -stride), (-stride, stride), (-stride, -stride)):
                tu, ti = best.turn + dt, best.tilt + dl
                if abs(_angdiff(tu, start.turn)) > span + 1e-9 or abs(ti - start.tilt) > span + 1e-9:
                    continue
                if turn_limit is not None and abs(_angdiff(tu, turn_limit[0])) > turn_limit[1] + 1e-9:
                    continue
                p = score_pose(mesh, t, centre, scale, (tu, ti, start.lens), tex)
                if p.score > best.score + 1e-9:
                    best, moved = p, True
                    break
        stride /= 2.0
    return best


def search(mesh: dict, alpha: np.ndarray, centre: np.ndarray, scale: float, keep: int = 3,
           turns: list[float] | None = None, tilts: list[float] | None = None, rgb: np.ndarray | None = None,
           tex: np.ndarray | None = None, timings: dict | None = None,
           lenses: list[float | None] | None = None, turn_limit: tuple[float, float] | None = None) -> list[Pose]:
    """Coarse outline grid, the edge and colour terms on its best KEEP_FULL, then a local refinement of the `keep` best.
    Best first. turns and tilts restrict the coarse grid (paint's view searches use the turn the view is meant to be seen
    from, plus or minus 20 degrees). rgb is the picture and tex the model's built colour; without them only the outline
    is scored. timings, when given, gets the seconds of each part: outline, terms, refine. turn_limit (centre, half
    width): the refinement keeps the turn within it, so a search restricted to a window answers inside it."""
    import time

    t0 = time.time()
    grid = coarse(mesh, alpha, centre, scale, turns=turns, tilts=tilts, lenses=lenses)
    t1 = time.time()
    full = tex is not None and rgb is not None
    if full:
        tc = target(alpha, COARSE_N, rgb)
        grid = sorted((score_pose(mesh, tc, centre, scale, (p.turn, p.tilt, p.lens), tex) for p in grid[:KEEP_FULL]),
                      key=lambda p: -p.score)
    t2 = time.time()
    tr = target(alpha, REFINE_N, rgb if full else None)
    refined = [refine(mesh, alpha, centre, scale, p, rgb=rgb, tex=tex if full else None, t=tr, turn_limit=turn_limit)
               for p in grid[:keep]]
    refined.sort(key=lambda p: -p.score)
    if timings is not None:
        timings.update(outline=round(t1 - t0, 2), terms=round(t2 - t1, 2), refine=round(time.time() - t2, 2))
    return refined
