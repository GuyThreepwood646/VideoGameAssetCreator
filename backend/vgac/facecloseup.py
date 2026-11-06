"""The close-up guard, second version: judge where the close-up's features are by matching, and put them back.

The face pass (stages/face_detail.py) asks Qwen-Image-Edit for a 1024 close-up of the painted head, because the painted
face in a full-body concept has only 60 to 85 pixels across. The first guard found the eyes and mouth on the painting
and on the close-up as their darkest spots (face.landmarks) and compared the two. On a sharp close-up the darkest spots
are the lashes and the corners of the eyes, on the blurred painting the middle of the eye smudge, so the same eyes
were measured as moved (the fresh knight: "eyes 238 px apart against 212", 0.0525 of the face's width against 0.04,
while her close-up has its eyes where the painting has them to within 1.4 px) and the close-up was thrown away.

Here:
1. The painting's eyes and mouth are found as before (only on the painting, where the first guard placed them).
2. Each one is looked for in the close-up by block matching at a little over the painting's own resolution
   (match_shifts): the detail both pictures really share. The best correlation says whether that feature is there at
   all (feature_scores: same eyes about 0.93 to 0.97, another person's 0.42 to 0.88) and the shift where it is.
3. A feature that sits a little off (up to MOVE_MAX of the face's width) is put back: a smooth local warp (warp_field)
   moves it onto the painting's place and leaves the head's outline, the hairline and the jaw where they are. What
   is left after the warp is the feature_move that is judged.
4. Then the same identity and colour measures as before: likeness at the painting's resolution, eye, skin and hair
   colour; and the style: how much fine texture the close-up draws on the bare skin under the eyes (skin_texture,
   pores and creases: a painted face turned photographic). The fit to the model is the painting's (the close-up's
   features now sit where the painting's do), so face_detail fits the painted crop and paints the close-up through it.

Identity and style are not corrected: a different face is still rejected by likeness and the feature scores, one turned
photographic by skin_texture (tests/test_face_closeup.py; the real wrong faces are in runtime/tmp/sharpface).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from . import face

MOVE_TOL = 0.006  # moves under this share of the face's width are left alone (under a pixel of the painting)
MOVE_MAX = 0.12  # a point moved further than this share of the face's width is not the same feature
REACH = 0.16  # how far around each feature the warp spreads, in face widths
RING = 0.55  # where the warp is held at zero, in face widths from the features' middle
FEATURE_MIN = 0.85  # each feature's best correlation (match_shifts): the knights' close-ups 0.93 to 0.97
# Fine texture on the bare skin of the upper cheeks, under the eyes (skin_texture): the knights' close-ups 13.4 to 15.6,
# the same faces asked for as photographs 21.6 and 23.0, the Ranger's close-ups drawn photographic (pores, creases)
# 52 to 62, his close-ups drawn in the painted look 15.6 and 18.9. Calibrated on three characters of one look.
SKIN_TEXTURE_MAX = 20.0


def points(lm: dict[str, Any]) -> np.ndarray:
    """(3, 2): right eye, left eye (as seen: the first is the viewer's left), mouth."""
    return np.array(list(lm["eyes"]) + [lm["mouth"]], dtype=np.float64)


def warp_field(shape: tuple[int, int], src: np.ndarray, dst: np.ndarray, width_px: float, reach: float = REACH,
               ring: float = RING) -> np.ndarray:
    """(H, W, 2): for each pixel of the corrected picture, how far to look back into the close-up (x, y) so that the
    close-up's points src land on dst while the head's outline stays where it is: a smooth Gaussian blend (reach times
    the face's width) through the moves at the points, held at zero on a ring of points `ring` face widths around the
    middle of the features (the hairline, the jaw and the cheeks' edges, which the close-up keeps)."""
    c = dst.mean(axis=0)
    r = ring * width_px
    ang = np.linspace(0, 2 * np.pi, 12, endpoint=False)
    anchors = c + r * np.stack([np.cos(ang), np.sin(ang)], axis=1)
    ctrl = np.concatenate([dst, anchors])
    val = np.concatenate([src - dst, np.zeros_like(anchors)])
    s = reach * width_px
    k = lambda p, q: np.exp(-((p[:, None, :] - q[None, :, :]) ** 2).sum(-1) / (2 * s * s))  # noqa: E731
    wts = np.linalg.solve(k(ctrl, ctrl) + 1e-6 * np.eye(len(ctrl)), val)
    H, W = shape
    Y, X = np.mgrid[0:H, 0:W]
    p = np.stack([X.ravel() + 0.5, Y.ravel() + 0.5], axis=1)
    u = np.zeros((p.shape[0], 2))
    for i in range(0, p.shape[0], 1 << 18):
        u[i:i + (1 << 18)] = k(p[i:i + (1 << 18)], ctrl) @ wts
    # nothing far outside the ring moves at all
    d = np.hypot(p[:, 0] - c[0], p[:, 1] - c[1])
    u *= np.clip((1.4 * r - d) / (0.4 * r), 0.0, 1.0)[:, None]
    return u.reshape(H, W, 2)


def warp(img: np.ndarray, field: np.ndarray) -> np.ndarray:
    """img read through warp_field's field."""
    H, W = img.shape[:2]
    Y, X = np.mgrid[0:H, 0:W]
    return face.sample(img, X + 0.5 + field[..., 0], Y + 0.5 + field[..., 1])


PATCHES = ((0.34, 0.2), (0.34, 0.2), (0.44, 0.22))  # each feature's box (across, up and down) in face widths


def match_shifts(a: np.ndarray, b: np.ndarray, centres: np.ndarray, width_px: float, source_px: float,
                 search: float = MOVE_MAX) -> list[dict[str, Any]]:
    """Where each feature of the painting (a box around each of centres: both eyes and the mouth) is in the close-up b,
    by block matching on band-passed lightness at a little over the painting's own resolution (the detail both
    pictures really share; the close-up's finer lashes and pores do not count). Returns per feature {shift (x, y) in
    pixels of the 1024 picture: b's feature sits at centre + shift, score: the correlation there}."""
    S = a.shape[0]
    n = int(max(96, min(S, round(2 * float(source_px)))))
    k = S / n
    lo, hi = 0.9 * n / float(source_px), 3.6 * n / float(source_px)
    r = int(np.ceil(search * width_px / k))
    m = r + int(max(f[0] for f in PATCHES) * width_px / k) + 2  # room around the picture (a mouth near its edge)
    A = np.pad(face.band(face.resize(a, n), lo, hi), m)
    B = np.pad(face.band(face.resize(b, n), lo, hi), m)
    out = []
    for (cx, cy), (fw, fh) in zip(centres, PATCHES):
        hx, hy = max(3, int(fw * width_px / k / 2)), max(3, int(fh * width_px / k / 2))
        x, y = int(cx / k) + m, int(cy / k) + m
        if not (m <= x < n + m and m <= y < n + m):
            out.append({"shift": (0.0, 0.0), "score": None})
            continue
        pa = A[y - hy: y + hy + 1, x - hx: x + hx + 1]
        pa = pa - pa.mean()
        na = np.sqrt((pa * pa).sum())
        best = (-2.0, 0, 0)
        scores = np.full((2 * r + 1, 2 * r + 1), -2.0)
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                pb = B[y + dy - hy: y + dy + hy + 1, x + dx - hx: x + dx + hx + 1]
                pb = pb - pb.mean()
                s = float((pa * pb).sum() / max(na * np.sqrt((pb * pb).sum()), 1e-12))
                scores[dy + r, dx + r] = s
                if s > best[0]:
                    best = (s, dx, dy)
        s, dx, dy = best
        # a parabola through the neighbours for a part-pixel shift
        fx = fy = 0.0
        iy, ix = dy + r, dx + r
        if 0 < ix < 2 * r:
            l_, c_, r_ = scores[iy, ix - 1], scores[iy, ix], scores[iy, ix + 1]
            den = l_ - 2 * c_ + r_
            fx = 0.5 * (l_ - r_) / den if den < 0 else 0.0
        if 0 < iy < 2 * r:
            u_, c_, d_ = scores[iy - 1, ix], scores[iy, ix], scores[iy + 1, ix]
            den = u_ - 2 * c_ + d_
            fy = 0.5 * (u_ - d_) / den if den < 0 else 0.0
        out.append({"shift": (round((dx + fx) * k, 2), round((dy + fy) * k, 2)), "score": round(s, 4)})
    return out


def unmove_points(p: np.ndarray, a: dict[str, Any]) -> np.ndarray:
    """Points of a picture moved by face.apply_alignment(img, a) back to the picture's own pixels."""
    c = np.array(a["centre"], dtype=np.float64)
    s = np.array([a["sx"], a["sy"]], dtype=np.float64)
    t = np.array([a["tx"], a["ty"]], dtype=np.float64)
    return c + (p - c - t) / s


def guard(view: dict[str, Any], info: dict[str, Any], crop: np.ndarray, closeup: np.ndarray, painted_head_px: float,
          landmark_residual_max: float, align_min: float) -> dict[str, Any]:
    """The measures of a close-up against the painted crop (both at 1024 in the crop's framing) and the model's front
    render (view, info from face.render_view and face.find_face). Returns the measures face_detail.closeup_verdict
    reads (likeness, feature_move, eye_colour_shift, skin_shift, hair_shift, fit_residual), plus feature_move_raw (before
    the correction), correction ({sx, sy, tx, ty, centre} in the crop's own pixels, or None) and "registered": the
    corrected close-up (1024, 3) to paint from, or None when it could not be corrected."""
    w = float(info["width_px"])
    inner = face.inner_face(info)
    a, b = crop, closeup
    out: dict[str, Any] = {"source_face_px": round(w, 1), "correction": None, "registered": None}
    seg = face.hair_and_skin(view["image"], a, info, view["mask"])
    lm = face.landmarks(view["image"], info)
    la = face.landmarks(a, info)
    direct = face.fit_landmarks(la, lm, info["nose"]) if la and lm else None
    pre = None
    if direct is None or direct["residual_px"] > landmark_residual_max * w:
        p = face.align(a, view["image"], inner)
        if face.feature_match(face.apply_alignment(a, p), view["image"], inner, 6.0, 24.0) >= align_min:
            pre = p
            out["lined_up_first"] = True
    a_m = face.apply_alignment(a, pre) if pre else a
    b_m = face.apply_alignment(b, pre) if pre else b
    la, lb = face.landmarks(a_m, info), face.landmarks(b_m, info)
    if not la:  # the painting's own eyes and mouth are needed to know where to look
        out["likeness"] = face.likeness(a, b, inner, painted_head_px)
        return out
    pa = points(la)
    # the painting's features in the crop's own pixels: the close-up is searched and corrected in the crop's framing
    pa0 = unmove_points(pa, pre) if pre else pa
    found = match_shifts(a, b, pa0, w, painted_head_px)
    shifts = np.array([f["shift"] for f in found], dtype=np.float64)
    moves = np.hypot(*shifts.T) / w
    scores = [f["score"] for f in found]
    out["feature_move_raw"] = round(float(moves.max()), 4)
    out["feature_scores_raw"] = scores
    if lb:  # the first guard's measure (darkest spots), for the record only
        out["landmark_move"] = round(float(np.hypot(*(pa - points(lb)).T).max()) / w, 4)
    b_fixed = b
    if all(s_ is not None for s_ in scores) and float(moves.max()) < MOVE_MAX:
        if float(moves.max()) > MOVE_TOL:
            b_fixed = warp(b, warp_field(b.shape[:2], pa0 + shifts, pa0, w))
            out["correction"] = {"moves": [round(float(m), 4) for m in moves], "at": pa0.round(1).tolist(),
                                 "shifts": shifts.round(1).tolist()}
            found = match_shifts(a, b_fixed, pa0, w, painted_head_px)
            shifts = np.array([f["shift"] for f in found], dtype=np.float64)
            moves = np.hypot(*shifts.T) / w
            scores = [f["score"] for f in found]
        out["registered"] = b_fixed
    out["feature_move"] = round(float(moves.max()), 4)
    out["feature_scores"] = scores
    out["feature_score_min"] = None if any(s_ is None for s_ in scores) else round(float(min(scores)), 4)
    out["likeness"] = face.likeness(a, b_fixed, inner, painted_head_px)
    out["likeness_raw"] = face.likeness(a, b, inner, painted_head_px)
    out["skin_texture"] = skin_texture(b, a, info)
    skin = inner & ~seg["pic_hair"]
    out["skin_shift"] = face.colour_distance(a, b_fixed, skin)
    out["hair_shift"] = face.colour_distance(a, b_fixed, seg["pic_hair"]) if seg.get("found") else None
    bf_m = face.apply_alignment(b_fixed, pre) if pre else b_fixed
    out["eye_colour_shift"] = face.colour_shift(face.eye_colour(a_m, la["eyes"], w), face.eye_colour(bf_m, la["eyes"], w))
    # The close-up now has its features where the painting has them, so it lands on the model as the painting does:
    # the fit is the painting's (face_detail.fit_and_paint fits the crop and paints the close-up through that fit).
    fit = face.fit_landmarks(la, lm, info["nose"]) if lm else None
    out["painting_fit_residual"] = None if fit is None else round(fit["residual_px"] / w, 4)
    out["fit_residual"] = None
    return out


def skin_texture(img: np.ndarray, crop: np.ndarray, info: dict[str, Any]) -> float | None:
    """How much fine texture (pores, creases) a picture has on the bare skin of the upper cheeks, under the eyes: the
    spread of its lightness above a 1.5 px blur (at 1024), times 1000. A painted face is smooth there; the image
    editor, when it drifts toward a photograph, draws pores and creases. Under the eyes, because a beard or stubble
    lower on the cheek and a fringe over the forehead are texture of another kind. The skin is where the painted crop
    has the skin tone. None with too little bare skin."""
    w = float(info["width_px"])
    nx, ny = info["nose"]
    S = crop.shape[0]
    Y, X = np.mgrid[0:S, 0:S]
    dx = np.abs(X - nx)
    m = (dx > 0.14 * w) & (dx < 0.34 * w) & (Y > ny - 0.14 * w) & (Y < ny) & info["region"]
    if m.sum() < 500:
        return None
    lab = face.to_lab(np.clip(crop, 0, 1))
    tone = np.median(lab[m], axis=0)
    m &= np.linalg.norm((lab - tone) * np.array([0.5, 1.0, 1.0]), axis=-1) < 12.0
    if m.sum() < 500:
        return None
    lum = face.luminance(img)
    return round(float((lum - face.blur(lum, 1.5))[m].std()) * 1000.0, 2)


def save_registered(m: dict[str, Any], dest: Path) -> Path | None:
    """Write the corrected close-up (guard's "registered") and drop the array from the measures."""
    img = m.pop("registered", None)
    if img is None:
        return None
    return face.save_rgb(img, dest)
