"""Light from the pictures, measured and removed after all painting (docs/AFTER-TEST-CHANGES.md C4).

The picture projection (srcproject.py) paints the pictures' own pixels into the base colour, light included, and the
face pass keeps the picture's light on purpose. So a hard-lit picture leaves its cast light in the game texture, where
the game engine lights the model a second time. This pass runs in the clean-up step after the last pass that paints
colour (hair detail) and before the colour checks.

The measure (no colour regions: colour cannot tell skin from a mustard cardigan or red hair, and lit skin from shadowed
skin is not one chromaticity either). Pairs of nearby texels of the same hue on one texture piece (about 1 to 6 cm
apart, OFFSETS at WORK size), or both grey, have the same material, so their brightness difference is light. A robust
least-squares fit of those differences gives one smooth light field S = a . normal + b . height over the figure (log of
linear luminance). Black (L under 10) and dark grey do not vote; a jump over two stops between near texels is an edge.
- patch light (the gate): inside each patch (one 30-degree hue band or grey, on one texture piece, at least
  PATCH_MIN of the surface) the 5 to 95 percent range of S in stops, area weighted: how much the light varies across
  one material, which is what can be removed;
- whole-figure light: the 5 to 95 percent range of S over the figure, in stops, with its side, up-down, front-back and
  height parts;
- cast shadow share: same-hue texels over 1.5 stops darker than their patch once S is taken out. Reported only: dark
  material (pocket insides, glasses, a beard) reads the same, so it can neither gate nor correct.
On the 17 stored test runs the two hard-lit ones read 1.03 and 1.20 patch light, every soft-lit one 0.56 or under.

The correction (gain_field): inside each material band (hue band, or grey, over the whole figure), the part of the
texture's own smoothed brightness deviation that the fitted field also predicts (same sign, the smaller of the two) is
taken out, times the strength; the same gain on the three channels in linear light (hue kept), varying over
centimetres only (pores, weave and strands stay), with no step at texture seams or at the face region's border. It is
judged on the texture itself (unevenness: the spread of brightness inside each material), not on the fitted field,
and kept only when the materials got more even by KEEP_MIN_GAIN; otherwise nothing is written and the check says so.
At most UP_MAX brighter and DOWN_MIN darker; brightening takes a texel at most half way to BRIGHT_MAX, and a texel
already over it (eye whites, teeth) is never brightened. Left alone: black and near black (never lifted), the hair's
triangles when the hair pass found hair. At strength 0 nothing is written.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from . import face, imaging

WORK = 1024              # the fit and the field are worked at this texture size, the gain is scaled up after
OFFSETS = (4, 10, 24)    # texel steps of the pairs at WORK size (about 1 to 6 cm on a standing figure)
HUE_TOL = 10.0           # degrees: a pair of the same material
JUMP = float(np.log(4.0))  # a step over two stops between near texels is an edge, not light
CHROMA_MIN, L_MIN, GREY_L_MIN = 7.0, 10.0, 15.0
PATCH_MIN = 0.005
# The gate on patch light, in stops: nothing at or under GATE[0], full strength from GATE[1]. It rests on two hard-lit
# runs only (W1-bikini-hard 1.03, M1-clothed-hard 1.20) against fifteen soft-lit ones (0.56 at most, median 0.22), so it
# is the narrowest-tested number here; more hard-lit pictures should move it before anything else does.
GATE = (0.7, 1.0)
MAX_STRENGTH = 0.8       # never the whole fitted light: a slightly lit character is better than lifted noise
UP_MAX, DOWN_MIN = 2.0, 0.6  # linear gain limits: one stop brighter at most, about 0.75 stop darker at most
BLACK_L = (10.0, 20.0)   # Lab L: no lift under the first, full lift from the second
BRIGHT_MAX = 0.94        # sRGB 240, the top of the physically based range (imaging.ALBEDO_MAX)
FEATHER_CELL = 0.006     # metres: the field is smoothed this much along the surface so guards have no hard edge
CHECK_MAX = 0.6          # the check passes when the patch light left is under this
CAST_NOTE = 0.06         # a cast shadow share over this, with the pass on, is said in the note
OBSERVED_CELL = 0.02     # metres: the texture's own brightness is compared at this scale (pores and weave are finer)
BAND_MIN = 0.01          # a material band counts in the unevenness when it covers this share of the surface
KEEP_MIN_GAIN = 0.02     # stops: the correction is kept only when the materials got at least this much more even


def lin(c: np.ndarray) -> np.ndarray:
    c = np.asarray(c, dtype=np.float64)
    return np.where(c <= 0.04045, c / 12.92, ((np.maximum(c, 0) + 0.055) / 1.055) ** 2.4)


def enc(c: np.ndarray) -> np.ndarray:
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def strength_of(patch_stops: float) -> float:
    t = (float(patch_stops) - GATE[0]) / (GATE[1] - GATE[0])
    return float(MAX_STRENGTH * min(1.0, max(0.0, t)))


class Surface:
    """The texels of a model's texture at size T: atlas (srcproject.Atlas: index, position, normal), each texel's texture
    piece, and the features of the light field (normal and height)."""

    def __init__(self, mesh: dict[str, np.ndarray], T: int = WORK):
        from . import srcproject  # noqa: PLC0415  (srcproject imports a great deal; only this pass needs it here)

        self.mesh, self.T = mesh, T
        self.atlas = srcproject.Atlas(mesh, T, gutter=0)
        uvp = face.uv_pixels(mesh["uv"], T)
        tri = face.rasterise(uvp, np.zeros(len(uvp)), mesh["faces"], T)["tri"].reshape(-1)[self.atlas.idx]
        uv_key = np.round(np.asarray(mesh["uv"], dtype=np.float64) * 2**20).astype(np.int64)
        _, uv_id = np.unique(uv_key, axis=0, return_inverse=True)
        uv_id = uv_id.reshape(-1)
        island = np.asarray(imaging.face_islands(uv_id[mesh["faces"]], int(uv_id.max()) + 1))
        self.tri = tri
        self.piece = island[np.maximum(tri, 0)]
        lo, hi = mesh["verts"].min(0), mesh["verts"].max(0)
        h = (self.atlas.pos[:, 1] - lo[1]) / max(float(hi[1] - lo[1]), 1e-9)
        self.X = np.stack([self.atlas.nrm[:, 0], self.atlas.nrm[:, 1], self.atlas.nrm[:, 2], h], 1)
        self.cache: dict[Any, Any] = {}


def _pairs(surf: Surface, ok: np.ndarray, hue: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    T = surf.T
    grid = np.full(T * T, -1, dtype=np.int64)
    grid[surf.atlas.idx] = np.arange(len(surf.atlas.idx))
    gy, gx = np.divmod(surf.atlas.idx, T)
    A, B = [], []
    for d in OFFSETS:
        for dy, dx in ((0, d), (d, 0)):
            ny, nx = gy + dy, gx + dx
            inb = (ny < T) & (nx < T)
            j = np.full(len(gy), -1, dtype=np.int64)
            j[inb] = grid[ny[inb] * T + nx[inb]]
            i = np.flatnonzero(j >= 0)
            j = j[i]
            m = ok[i] & ok[j] & (surf.piece[i] == surf.piece[j])
            i, j = i[m], j[m]
            dh = np.abs((hue[i] - hue[j] + 180.0) % 360.0 - 180.0)
            grey_i, grey_j = hue[i] < 0, hue[j] < 0
            same = np.where(grey_i & grey_j, True, (dh < HUE_TOL) & ~grey_i & ~grey_j)
            m = same & (np.abs(y[i] - y[j]) < JUMP)
            A.append(i[m])
            B.append(j[m])
    return np.concatenate(A), np.concatenate(B)


def _fit(X: np.ndarray, y: np.ndarray, a: np.ndarray, b: np.ndarray, iters: int = 5) -> np.ndarray:
    """The light field's coefficients from the pairs' differences (Huber-weighted least squares)."""
    D, t = X[a] - X[b], y[a] - y[b]
    w = np.ones(len(t))
    coef = np.zeros(X.shape[1])
    for _ in range(iters):
        sw = np.sqrt(w)
        coef, *_ = np.linalg.lstsq(D * sw[:, None], t * sw, rcond=None)
        r = t - D @ coef
        k = 1.345 * (float(np.median(np.abs(r))) / 0.6745 + 1e-6)
        w = np.where(np.abs(r) <= k, 1.0, k / np.maximum(np.abs(r), 1e-9))
    return coef


def _stops(v: np.ndarray) -> float:
    return float(np.percentile(v, 95) - np.percentile(v, 5)) / float(np.log(2)) if v.size else 0.0


def measure(surf: Surface, base: np.ndarray) -> dict[str, Any]:
    """The light painted into base (WORK size, sRGB 0..1). -> {patch_stops, whole_stops, parts, cast_shadow_share,
    pairs, measured, S (per texel), lab, ok}; measured False when there were too few pairs to say."""
    rgb = base.reshape(-1, 3)[surf.atlas.idx]
    lab = face.to_lab(np.clip(rgb, 0, 1)[None])[0]
    L, C = lab[:, 0], np.hypot(lab[:, 1], lab[:, 2])
    chrom = (C >= CHROMA_MIN) & (L >= L_MIN)
    grey = (C < CHROMA_MIN) & (L >= GREY_L_MIN)
    ok = chrom | grey
    hue = np.where(grey, -1.0, np.degrees(np.arctan2(lab[:, 2], lab[:, 1])) % 360.0)
    y = np.log(np.clip(lin(rgb) @ np.array([0.2126, 0.7152, 0.0722]), 1e-3, 1.0))
    a, b = _pairs(surf, ok, hue, y)
    out: dict[str, Any] = {"pairs": int(len(a)), "lab": lab, "ok": ok, "rgb": rgb, "work": base}
    if len(a) < 500 or not ok.any():
        out.update(measured=False, patch_stops=None, whole_stops=None, S=np.zeros(len(y)))
        return out
    coef = _fit(surf.X, y, a, b)
    S = surf.X @ coef
    band = np.where(hue < 0, 12, hue // 30).astype(np.int64)
    key = surf.piece.astype(np.int64) * 16 + band
    n_all = len(S)
    tot = acc = 0.0
    for k in np.unique(key[ok]):
        sel = ok & (key == k)
        share = float(sel.sum()) / n_all
        if share >= PATCH_MIN:
            tot += share
            acc += share * _stops(S[sel])
    alb = y - S
    dark = np.zeros(len(y), dtype=bool)
    for k in np.unique(key[chrom]):
        sel = chrom & (key == k)
        if sel.sum() >= 200:
            dark[sel] = alb[sel] < np.median(alb[sel]) - 1.5 * np.log(2)
    parts = {n: round(_stops(surf.X[ok, i] * coef[i]), 3) for i, n in enumerate(("side", "up_down", "front_back", "height"))}
    out.update(measured=True, patch_stops=round(acc / max(tot, 1e-9), 3), whole_stops=round(_stops(S[ok]), 3), parts=parts,
               cast_shadow_share=round(float(dark.sum()) / max(int(chrom.sum()), 1), 4), S=S, coef=coef)
    return out


def _lowpass(surf: Surface, v: np.ndarray, cell: float, passes: int = 3, r: int = 2) -> np.ndarray:
    """v (one value per texel) smoothed along the surface: averaged in cells of `cell` metres, box-blurred `passes` times
    with radius r cells, read back trilinearly (srcproject.surface_correction for one channel and even weights)."""
    key = ("grid", cell)
    if key not in surf.cache:
        pos = surf.atlas.pos
        lo = pos.min(0) - 3 * cell
        f = (pos - lo) / cell
        idx = np.floor(f).astype(np.int64)
        dims = tuple(int(d) for d in idx.max(0) + 4)
        flat = np.ravel_multi_index(tuple(idx.T), dims)
        sn = np.bincount(flat, minlength=int(np.prod(dims))).astype(np.float64).reshape(dims)
        for _ in range(passes):
            sn = face._box3(sn, r)
        surf.cache[key] = (flat, dims, sn, f - 0.5)
    flat, dims, sn, fc = surf.cache[key]
    sv = np.bincount(flat, weights=v, minlength=int(np.prod(dims))).reshape(dims)
    for _ in range(passes):
        sv = face._box3(sv, r)
    return face._trilinear(sv / np.maximum(sn, 1e-9), fc)


def bands(lab: np.ndarray) -> np.ndarray:
    """Each texel's material band: its 30-degree hue band, 12 for grey, -1 for black and near black."""
    C = np.hypot(lab[:, 1], lab[:, 2])
    hue = np.degrees(np.arctan2(lab[:, 2], lab[:, 1])) % 360.0
    out = np.where(C < CHROMA_MIN, 12, hue // 30).astype(np.int64)
    out[(lab[:, 0] < L_MIN) | ((C < CHROMA_MIN) & (lab[:, 0] < GREY_L_MIN))] = -1
    return out


def observed(surf: Surface, rgb: np.ndarray) -> np.ndarray:
    """The texture's own log luminance, smoothed OBSERVED_CELL along the surface: the low frequencies light lives in."""
    y = np.log(np.clip(lin(rgb) @ np.array([0.2126, 0.7152, 0.0722]), 1e-3, 1.0))
    return _lowpass(surf, y, OBSERVED_CELL)


def unevenness(surf: Surface, rgb: np.ndarray, band: np.ndarray, lp: np.ndarray | None = None) -> float:
    """How uneven each material is in the texture itself (not in the fitted field): the 10 to 90 percent range of the
    smoothed log luminance inside each band of at least BAND_MIN of the surface, in stops, area weighted. A correction
    that is right makes this smaller; one that adds light makes it larger."""
    lp = observed(surf, rgb) if lp is None else lp
    tot = acc = 0.0
    for k in np.unique(band[band >= 0]):
        sel = band == k
        share = float(sel.mean())
        if share >= BAND_MIN:
            tot += share
            acc += share * float(np.percentile(lp[sel], 90) - np.percentile(lp[sel], 10)) / float(np.log(2))
    return round(acc / max(tot, 1e-9), 3)


def face_texels(surf: Surface, base: np.ndarray | None) -> np.ndarray | None:
    """The face's texels (face.find_face on the front of the head, as the face pass and the skin-metal clearing find it),
    or None. A hard picture's true exposure cannot be known: on the test runs the same woman's lit face was near her
    soft-lit one (L 65 against 70) while her legs were far darker (20 against 67). Her face is what the likeness is
    judged by, so its skin keeps the brightness it was painted with and the rest of that skin is evened out toward it."""
    if base is None:
        return None
    head = face.find_head(surf.mesh["verts"])
    if head is None:
        return None
    try:
        cam = face.head_camera(head, 0, 1024)
        view = face.render_view(surf.mesh, base, cam)
        info = face.find_face(view, surf.mesh, cam, head)
        if not info.get("found"):
            return None
        tris = np.zeros(len(surf.mesh["faces"]), dtype=bool)
        tris[face.face_triangles(view, info["region"])] = True
        return (surf.tri >= 0) & tris[np.maximum(surf.tri, 0)]
    except Exception:  # noqa: BLE001  (every band centred on itself)
        return None


def gain_field(surf: Surface, m: dict[str, Any], strength: float, keep: np.ndarray | None = None) -> tuple[np.ndarray, dict[str, Any]]:
    """The gain per texel (WORK texels of surf.atlas), and what was left alone: {black_held, hair_held, limited}.

    Inside each material band (over the whole figure, so no step at texture seams), only the light that is both seen
    and explained is removed: the texture's own smoothed deviation from the band's middle, where the fitted field
    points the same way, and never more than the smaller of the two. A field that misses where the light really fell
    (a spot beam, a cast shadow, a back view drawn dark) then changes nothing there instead of adding light. Tried and
    dropped: dividing out the whole fitted field (centred on the figure or on the face); against the same outfits in
    soft light it moved the garments further from their true colour and made them less even (the green jacket's spread
    4.6 to 5.1 stops)."""
    S, lab = m["S"], m["lab"]
    band = bands(lab)
    lp = m["lp"] = observed(surf, m["rgb"])
    on_face = face_texels(surf, m.get("work"))
    logg = np.zeros(len(S))
    raw_c = np.zeros(len(S))
    face_band = None
    for k in np.unique(band[band >= 0]):
        sel = band == k
        if int(sel.sum()) < 500:
            continue
        # the face's own band (its skin) is centred on the face: the face keeps the brightness it was painted with
        ref = sel
        if on_face is not None:
            in_face = sel & on_face
            if int(in_face.sum()) >= 200 and int(in_face.sum()) >= 0.3 * int((on_face & (band >= 0)).sum()):
                ref, face_band = in_face, int(k)
        pred = S[sel] - float(np.median(S[ref]))
        dev = lp[sel] - float(np.median(lp[ref]))
        agree = np.sign(pred) == np.sign(dev)
        c = np.where(agree, np.sign(pred) * np.minimum(np.abs(pred), np.abs(dev)), 0.0)
        raw_c[sel] = c
        logg[sel] = -strength * c
    raw = np.exp(logg)
    g = np.clip(raw, DOWN_MIN, UP_MAX)
    limited = float((np.abs(logg - np.log(g)) > 0.05).mean())
    L = lab[:, 0]
    up = g > 1
    lift = np.clip((L - BLACK_L[0]) / (BLACK_L[1] - BLACK_L[0]), 0, 1)
    black_held = float(((band < 0) & (np.abs(S - float(np.median(S))) * strength > 0.1)).mean())
    g[up] = 1 + (g[up] - 1) * lift[up]
    keep = np.zeros(len(g), dtype=bool) if keep is None else keep
    hair_held = float((keep & (np.abs(raw_c) * strength > 0.1)).mean())
    g[keep] = 1.0
    g = np.exp(_lowpass(surf, np.log(g), FEATHER_CELL))
    g[keep] = 1.0
    return g, {"black_held": round(black_held, 4), "hair_held": round(hair_held, 4), "limited": round(limited, 4),
               "face_band": face_band}


def apply_gain(full: np.ndarray, G: np.ndarray) -> np.ndarray:
    """full (N, N, 3 sRGB) times the gain image G (N, N) in linear light. Brightening takes a texel at most half way to
    BRIGHT_MAX, so nothing is pressed flat against the limit, and a texel already over it (eye whites, teeth) is never
    brightened."""
    li = lin(full)
    mx = li.max(-1)
    lim = float(lin(np.array(BRIGHT_MAX)))
    cap = np.maximum(1.0, 1.0 + 0.5 * (lim / np.maximum(mx, 1e-9) - 1.0))
    # the black guard again at full size (the gain was made at WORK size and scaled up): never lift a near-black texel
    Y = li @ np.array([0.2126, 0.7152, 0.0722])
    Lstar = np.where(Y > 0.008856, 116 * np.cbrt(Y) - 16, 903.3 * Y)
    lift = np.clip((Lstar - BLACK_L[0]) / (BLACK_L[1] - BLACK_L[0]), 0, 1)
    g = np.where(G > 1, 1 + (np.minimum(G, cap) - 1) * lift, G)
    return enc(li * g[..., None])


def hair_keep(surf: Surface, hair_tris: np.ndarray | None) -> np.ndarray:
    if hair_tris is None or not np.asarray(hair_tris).any():
        return np.zeros(len(surf.atlas.idx), dtype=bool)
    ht = np.asarray(hair_tris, dtype=bool)
    return (surf.tri >= 0) & ht[np.maximum(surf.tri, 0)]


def left_alone(m: dict[str, Any], held: dict[str, Any], strength: float, hair_found: bool) -> list[str]:
    """What the person is told was not corrected (keys guidance turns into words)."""
    if strength <= 0:
        return []
    out = []
    if held["black_held"] > 0.01:
        out.append("black")
    if hair_found and held["hair_held"] > 0.005:
        out.append("hair")
    if float(m.get("cast_shadow_share") or 0) > CAST_NOTE:
        out.append("cast_shadows")
    if held["limited"] > 0.05:
        out.append("limit")
    return out


def run(glb: Path | str, base_png: Path | str, out_dir: Path | str, hair_tris: np.ndarray | None = None,
        hair_found: bool = False, measure_after: bool = False) -> dict[str, Any]:
    """Measure and, when strong enough, remove the light in the finished base colour. Writes base_color.png (the
    corrected colour) and base_color_lit.png (the colour before) into out_dir only when it corrects. -> {metrics:
    {light_fix: {...}}, files: {base_color, base_color_lit}}. measure_after: fit the light again on the corrected colour
    (patch_after, whole_after; about 8 s, for reports: the check reads the unevenness). Never raises."""
    t0 = time.time()
    rec: dict[str, Any] = {"ran": False}
    try:
        mesh = face.load_mesh(glb)
        if mesh is None:
            rec.update(reason="no texture layout")
            return {"metrics": {"light_fix": rec}, "files": {}}
        full = imaging.read_map(base_png)
        work = full if full.shape[0] == WORK else imaging.read_map(base_png, WORK)
        surf = Surface(mesh, WORK)
        m = measure(surf, work)
        if not m["measured"]:
            rec.update(measured=False, reason="too little coloured surface to tell light from colour", pairs=m["pairs"],
                       seconds=round(time.time() - t0, 1))
            return {"metrics": {"light_fix": rec}, "files": {}}
        s = strength_of(m["patch_stops"])
        rec.update(ran=True, measured=True, patch_before=m["patch_stops"], whole_before=m["whole_stops"], parts_before=m["parts"],
                   cast_shadow_share=m["cast_shadow_share"], pairs=m["pairs"], strength=round(s, 3), applied=s > 0,
                   patch_after=m["patch_stops"], whole_after=m["whole_stops"], hair_found=bool(hair_found), left=[])
        files: dict[str, Path] = {}
        if s > 0:
            keep = hair_keep(surf, hair_tris)
            g, held = gain_field(surf, m, s, keep)
            T = WORK
            G = np.ones(T * T)
            G[surf.atlas.idx] = g
            G = imaging.dilate(G.reshape(T, T), surf.atlas.cover, imaging.dilation_px(T))
            N = full.shape[0]
            if N != T:
                G = np.asarray(Image.fromarray(G.astype(np.float32), "F").resize((N, N), Image.BILINEAR), dtype=np.float64)
            new = apply_gain(full, G)
            out_dir = Path(out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            lit = out_dir / "base_color_lit.png"
            Image.fromarray(np.clip(np.round(full * 255), 0, 255).astype(np.uint8), "RGB").save(lit)
            files = {"base_color_lit": lit, "base_color": imaging.write_map(new, out_dir / "base_color.png")}
            new_work = new if N == T else imaging.read_map(files["base_color"], T)
            after = measure(surf, new_work) if measure_after else {"patch_stops": None, "whole_stops": None}
            band = bands(m["lab"])
            uneven_before = unevenness(surf, m["rgb"], band, m.get("lp"))
            uneven_after = unevenness(surf, new_work.reshape(-1, 3)[surf.atlas.idx], band)
            rec.update(patch_after=after["patch_stops"], whole_after=after["whole_stops"], parts_after=after.get("parts"),
                       uneven_before=uneven_before, uneven_after=uneven_after,
                       gain_range=[round(float(np.percentile(g, 1)), 3), round(float(np.percentile(g, 99)), 3)],
                       changed_share=round(float((np.abs(g - 1) > 0.05).mean()), 4), held=held,
                       left=left_alone(m, held, s, hair_found))
            if uneven_after > uneven_before - KEEP_MIN_GAIN:
                # the texture itself did not get more even: the field missed the light, so the colour stays as painted
                for p_ in files.values():
                    Path(p_).unlink(missing_ok=True)
                files = {}
                rec.update(applied=False, rejected=True, left=["not_even"] + list(rec["left"]))
        rec["seconds"] = round(time.time() - t0, 1)
        return {"metrics": {"light_fix": rec}, "files": files}
    except Exception as e:  # noqa: BLE001  (the pass never fails a run: the colour stays as it was)
        rec.update(ran=False, error=str(e)[:200], seconds=round(time.time() - t0, 1))
        return {"metrics": {"light_fix": rec}, "files": {}}
