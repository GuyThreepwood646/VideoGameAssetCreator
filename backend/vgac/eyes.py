"""Eyes as their own pieces for a character's face (clean-up step).

The built head has only a few edges across an eye, so painted eyes look soft and the lids crease. Instead the lid
opening is cut out of the skin, its edge turned inward into a shallow socket, and a separate closed piece, curved like
an eyeball, is set behind it (blender/eyes.py does the mesh work). This module decides where the eyes go and paints
them, on the processor:

- the two corners of each eye are found on a front render of the finished model (where its lids really are);
- the lid outline and the iris are read from the picture's face and set onto those corners;
- the iris and the white are painted from the picture into free space of the texture the model already has, so the
  model keeps one material.

Everything is measured in the face pass's head camera: u to the camera's right, v up, w away from the camera, metres
from the camera's target. Nothing here raises for a face it cannot use: plan() says why and the clean-up stays as it was.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import face, imaging
from .blender import uvfit

CATEGORY = "humanoid"
LOOKS = ("painted", "realistic_pbr")  # cartoon and flat looks keep their drawn eyes
TRIANGLE_RESERVE = 1200  # triangles the clean-up's budget leaves free for the cut, the sockets and the two eyes
RENDER = 1024
# MediaPipe's numbering: each lid outline (16 points, from one corner round to the other) and its iris (centre first)
RINGS = ((33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246),
         (362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398))
IRISES = ((468, 469, 470, 471, 472), (473, 474, 475, 476, 477))
FAR_CORNER = 8  # index into a ring of the other corner
UPPER_FROM = 8  # ring[8:] and ring[0] are the upper lid

WIDTH_OF_HEAD = (0.09, 0.24)  # a believable eye width as a share of the head's width
OPEN_MIN = 0.14  # opening height over width below this reads as closed or hooded
WIDTHS_ALIKE = 1.5
SKIN_MIN = 0.9  # share of the opening that must show model surface
IN_FRONT_MAX_M = 0.012  # something this far in front of the lids covers the eye (hair, a frame)
DEPTH_SPREAD_MAX_M = 0.03
TEXTURE_PIECE_PX = (192, 144, 96)  # the eye's square on a 2048 texture; smaller ones are tried when there is no room
RIM_PIECE_SHARE = 0.25  # the socket's colour swatch beside the eye's square
BEYOND_OPENING_M = 0.003  # the painted square reaches this far past the opening's half width
SHADE_ROUGHNESS = {"eye": 0.85, "rim": 0.75}
EDGE_FADE_M = 0.0014  # the eye fades into the skin beside it over this far inside the outline
EDGE_FADE = 0.85
EDGE_SHADE = (0.8, 0.75)  # that skin under the upper lid and above the lower one, as a share of its own light
SKIN_FLOOR = 0.6  # the colour beside the eye counts as no darker than this share of the ordinary skin round it
LID_EDGE_M = 0.0008  # the lid's own colour is read this far past the opening
LID_LIGHT_M = 0.002  # light paint is taken off the skin this far outside the opening
LASH_M = 0.0011  # the upper lash line on the skin
LASH = 0.45
SKIN_RING_M = (0.0025, 0.0055)
SKIN_FROM_M = 0.0035  # the lid takes its colour from this far past the opening
TONE_PERCENTILE = 90
TONE_LIFT = 0.95  # a drawn eye is no brighter than this times the painted one it replaces
TONE_RANGE = (0.6, 1.08)
OUTLINE_POINTS = 48  # the lid outline is rounded to this many points before the cut


def wanted(category_id: str | None, look: str | None, params: dict[str, Any], face_matters: bool) -> bool:
    """Whether the clean-up tries separate eyes: a character in a painted or photographic look whose face matters and
    whose face pass is not turned off."""
    return (category_id == CATEGORY and look in LOOKS and face_matters and params.get("face_detail") is not False)


def landmarks(img: np.ndarray) -> np.ndarray | None:
    """MediaPipe's 478 face points of img (floats 0..1) in pixels, or None."""
    from . import lipmarks

    lm = lipmarks._get_landmarker()
    if lm is None:
        return None
    import mediapipe as mp

    u8 = np.ascontiguousarray(face.to_uint8(img))
    with lipmarks._lock:
        res = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=u8))
    if not res.face_landmarks:
        return None
    return np.array([[p.x * u8.shape[1], p.y * u8.shape[0]] for p in res.face_landmarks[0]])


def rounded(points: list[list[float]], count: int = OUTLINE_POINTS) -> list[list[float]]:
    """A closed curve through the points (Catmull-Rom), count points of it."""
    p = np.asarray(points, dtype=np.float64)
    n, out = len(p), []
    for k in range(count):
        at = k * n / count
        i, t = int(at), at - int(at)
        a, b, c, d = (p[(i + j) % n] for j in (-1, 0, 1, 2))
        q = 0.5 * (2 * b + (c - a) * t + (2 * a - 5 * b + 4 * c - d) * t * t + (3 * b - a - 3 * c + d) * t ** 3)
        out.append([float(q[0]), float(q[1])])
    return out


def onto_corners(src_a: complex, src_b: complex, dst_a: complex, dst_b: complex) -> tuple[complex, complex]:
    """(s, t) of the 2D similarity z -> s z + t that takes src_a to dst_a and src_b to dst_b."""
    s = (dst_b - dst_a) / (src_b - src_a)
    return s, dst_a - s * src_a


def inside(poly: np.ndarray, size: int) -> np.ndarray:
    """The pixels of a size x size picture inside the polygon of complex pixel positions."""
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).polygon([(float(p.real), float(p.imag)) for p in poly], fill=255)
    return np.asarray(m) > 127


def shape_fault(outlines: list[np.ndarray], irises: list[tuple[complex, float]], px_m: float, head_width: float) -> str | None:
    """Why two lid outlines (complex pixels) cannot be real open eyes, or None."""
    widths = []
    for outline, (ic, ir) in zip(outlines, irises):
        width = abs(outline[FAR_CORNER] - outline[0])
        if width <= 0:
            return "odd_shape"
        widths.append(width)
        if not WIDTH_OF_HEAD[0] <= width * px_m / head_width <= WIDTH_OF_HEAD[1]:
            return "odd_shape"
        axis = (outline[FAR_CORNER] - outline[0]) / width
        across = np.imag((outline - outline[0]) / axis)
        if np.ptp(across) < OPEN_MIN * width:
            return "closed"
        lo, hi = outline.real.min(), outline.real.max()
        if not (lo < ic.real < hi) or not 0 < ir < 0.45 * width:
            return "odd_shape"
    if max(widths) > WIDTHS_ALIKE * min(widths):
        return "odd_shape"
    return None


def framed_picture(glb: Path | str, cutout: Path | str, head_yaw: float) -> np.ndarray | None:
    """The step 1 picture's head framed like the head camera frames the model's, or None."""
    mesh = face.load_mesh(glb)
    head = face.find_head(mesh["verts"]) if mesh is not None else None
    if head is None:
        return None
    rgba = np.asarray(Image.open(cutout).convert("RGBA"), dtype=np.float64) / 255.0
    framed = face.painted_head(rgba, head, face.head_camera(head, float(head_yaw), RENDER), RENDER)
    return None if framed is None else framed["image"]


def plan(glb: Path | str, base_color: Path | str, picture: np.ndarray, head_yaw: float) -> dict[str, Any]:
    """Where the two eyes go. picture: the picture's face framed in the head camera (RENDER square, floats), the checked
    close-up or the step 1 picture. -> {ok, reason} and with ok: camera, eyes (the mesh work's numbers and what paint()
    needs under "paint")."""
    mesh = face.load_mesh(glb)
    if mesh is None:
        return {"ok": False, "reason": "no_face"}
    head = face.find_head(mesh["verts"])
    if head is None:
        return {"ok": False, "reason": "no_face"}
    cam = face.head_camera(head, float(head_yaw), RENDER)
    view = face.render_view(mesh, face.read_rgb(base_color), cam)
    on_model, on_picture = landmarks(view["image"]), landmarks(picture)
    if on_model is None or on_picture is None:
        return {"ok": False, "reason": "no_eyes"}
    px_m = cam.scale / cam.size
    z = view["raster"]["z"]
    cplx = lambda P, idx: P[list(idx), 0] + 1j * P[list(idx), 1]  # noqa: E731
    outlines, irises, maps_ = [], [], []
    for ring, iris in zip(RINGS, IRISES):
        q, m = cplx(on_picture, ring), cplx(on_model, ring)
        s, t = onto_corners(q[0], q[FAR_CORNER], m[0], m[FAR_CORNER])
        outline = s * q + t
        pts = s * cplx(on_picture, iris) + t
        outlines.append(outline)
        irises.append((complex(pts[0]), float(np.mean(np.abs(pts[1:] - pts[0])))))
        maps_.append((s, t, q, cplx(on_picture, iris)))
    fault = shape_fault(outlines, irises, px_m, head.width)
    if fault:
        return {"ok": False, "reason": fault}

    def uv(c: complex) -> list[float]:
        return [float((c.real - cam.size / 2) * px_m), float(-(c.imag - cam.size / 2) * px_m)]

    eyes = []
    for outline, (ic, ir), (s, t, q, q_iris) in zip(outlines, irises, maps_):
        opening = inside(outline, cam.size)
        seen = opening & np.isfinite(z)
        if opening.sum() < 30 or seen.sum() < SKIN_MIN * opening.sum():
            return {"ok": False, "reason": "covered"}
        xy = np.clip(np.stack([outline.real, outline.imag], axis=1).astype(int), 0, cam.size - 1)
        edge = z[xy[:, 1], xy[:, 0]]
        if not np.isfinite(edge).any():
            return {"ok": False, "reason": "covered"}
        lid = float(np.median(edge[np.isfinite(edge)]))
        near, far = np.percentile(z[seen], [1, 99])
        if lid - near > IN_FRONT_MAX_M or far - near > DEPTH_SPREAD_MAX_M:
            return {"ok": False, "reason": "covered"}
        width = float(abs(outline[FAR_CORNER] - outline[0]))
        centre = complex((outline[0].real + outline[FAR_CORNER].real) / 2, float(outline.imag.mean()))
        half_m = width * px_m / 2 + BEYOND_OPENING_M
        eyes.append({
            "outline": [uv(p) for p in outline], "centre": uv(centre), "iris": uv(ic), "iris_radius": ir * px_m,
            "width": width * px_m, "half": half_m, "lid": lid,
            "paint": {"s": s, "t": t, "ring": q, "picture_iris": q_iris, "outline_px": outline, "iris_px": (ic, ir),
                      "centre_px": centre, "half_px": half_m / px_m},
        })
    right, up, fwd = cam.basis
    return {"ok": True, "reason": None, "camera": {"target": cam.target.tolist(), "right": right.tolist(), "up": up.tolist(),
                                                    "forward": fwd.tolist()},
            "eyes": eyes, "render": view["image"], "px_m": px_m, "mesh": mesh}


def paint(picture: np.ndarray, eye: dict[str, Any], px_m: float, size: int) -> np.ndarray:
    """The eye seen from the front, (size, size, 3), covering the square of half-width eye["half"] round its centre. Where
    the picture's eye is open its own pixels are used. What the lids hide there is made up from the picture: its white,
    its iris mirrored from the part that shows, and a pupil. A soft lid line runs along the upper lid."""
    p = eye["paint"]
    s, t, ring, outline = p["s"], p["t"], p["ring"], p["outline_px"]
    ic, ir = p["iris_px"]
    Y, X = np.mgrid[0:size, 0:size] + 0.5
    at = (p["centre_px"].real - p["half_px"] + X / size * 2 * p["half_px"]) + 1j * (
        p["centre_px"].imag - p["half_px"] + Y / size * 2 * p["half_px"])
    n = picture.shape[0]
    open_q = Image.fromarray(inside(ring, n).astype(np.uint8) * 255).filter(ImageFilter.MinFilter(3))
    open_q = np.asarray(open_q) > 127

    def take(z: np.ndarray) -> np.ndarray:
        return face.sample(picture, z.real, z.imag)

    def shows(z: np.ndarray) -> np.ndarray:
        return open_q[np.clip(z.imag.astype(int), 0, n - 1), np.clip(z.real.astype(int), 0, n - 1)]

    Yp, Xp = np.mgrid[0:n, 0:n] + 0.5
    q_ic = p["picture_iris"][0]
    q_ir = float(np.mean(np.abs(p["picture_iris"][1:] - q_ic)))
    r_q = np.abs(Xp + 1j * Yp - q_ic) / q_ir

    def colour(mask: np.ndarray, percentile: float, fallback: tuple[float, float, float]) -> np.ndarray:
        px = picture[mask & open_q]
        if len(px) < 8:
            return np.array(fallback)
        lum = face.luminance(px)
        return np.median(px[lum >= np.percentile(lum, percentile)], axis=0)

    white = colour(r_q > 1.15, 50, (0.75, 0.72, 0.70))
    iris_c = colour((r_q > 0.45) & (r_q < 0.9), 0, (0.3, 0.2, 0.15))
    white = np.clip(white * 0.75 + 0.25 * np.array([0.80, 0.74, 0.72]), 0, 0.82)
    r_c = np.abs(at - p["centre_px"]) / p["half_px"]
    tex = white * (1.0 - 0.22 * np.clip(r_c, 0, 1)[..., None] ** 2)
    col = np.broadcast_to(iris_c, tex.shape).copy()
    d = at - ic
    for z in (((ic - d) - t) / s, ((ic + np.conj(d)) - t) / s, (at - t) / s):  # later ones win
        ok = shows(z)
        col[ok] = take(z[ok])
    rr = np.abs(d) / ir
    alpha = np.clip((1.08 - rr) / 0.12, 0, 1)[..., None]
    col = col * (1 - 0.35 * np.clip((rr - 0.75) / 0.3, 0, 1)[..., None])
    tex = tex * (1 - alpha) + col * alpha
    pupil = np.clip((0.38 - rr) / 0.08, 0, 1)[..., None]
    tex = tex * (1 - pupil) + iris_c * 0.2 * pupil
    # the upper lid's shade on what is made up here, then the picture's own pixels wherever its eye is open: they carry
    # its glint and shade already
    upper = sorted((c.real, c.imag) for c in list(outline[UPPER_FROM:]) + [outline[0]])
    top = np.interp(at.real, [c[0] for c in upper], [c[1] for c in upper])
    below_top_m = (at.imag - top) * px_m
    tex = tex * (1 - 0.3 * np.clip(1 - below_top_m / 0.004, 0, 1))[..., None]
    src = (at - t) / s
    soft = face.blur(open_q.astype(np.float64), n / 700.0)
    own = np.clip((face.sample(soft[..., None], src.real, src.imag)[..., 0] - 0.35) / 0.4, 0, 1)[..., None]
    tex = tex * (1 - own) + take(src) * own
    # a soft lid line along the upper lid only: a line all round reads as a cut-out
    lash = colour(r_q > 0, 0, (0.2, 0.13, 0.1)) * 0.5
    line = np.clip((0.0011 - below_top_m) / 0.0011, 0, 1)[..., None] * 0.4
    tex = tex * (1 - line) + lash * line
    return np.clip(tex, 0, 1)


def tone_to_face(patch: np.ndarray, render: np.ndarray, eye: dict[str, Any]) -> np.ndarray:
    """The eye patch brought to the light of the face it sits in: its bright end (the white of the eye) no brighter than a
    little over the bright end of what the model shows in the opening now."""
    opening = inside(eye["paint"]["outline_px"], render.shape[0])
    now = np.percentile(face.luminance(render[opening]), TONE_PERCENTILE)
    size = patch.shape[0]
    p = eye["paint"]
    corner = p["centre_px"] - complex(p["half_px"], p["half_px"])
    local = (p["outline_px"] - corner) * (size / (2 * p["half_px"]))
    new = np.percentile(face.luminance(patch[inside(local, size)]), TONE_PERCENTILE)
    gain = float(np.clip(now * TONE_LIFT / max(new, 1e-3), TONE_RANGE[0], TONE_RANGE[1]))
    return np.clip(patch * gain, 0, 1)


def lid_colour(render: np.ndarray, eye: dict[str, Any], px_m: float) -> np.ndarray:
    """The colour of the ordinary skin round the opening on the model: a ring SKIN_RING_M out from it, past the dark
    lashes and creases right beside the eye."""
    n = render.shape[0]
    m = Image.fromarray(inside(eye["paint"]["outline_px"], n).astype(np.uint8) * 255)
    near, far = (2 * int(round(d / px_m)) + 1 for d in SKIN_RING_M)
    ring = (np.asarray(m.filter(ImageFilter.MaxFilter(far))) > 127) & ~(np.asarray(m.filter(ImageFilter.MaxFilter(near))) > 127)
    px = render[ring]
    lum = face.luminance(px)
    return np.median(px[(lum >= np.percentile(lum, 45)) & (lum <= np.percentile(lum, 85))], axis=0)


def edge_distance(outline: np.ndarray, at: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For each position of `at` (complex): its distance to the closed outline (complex positions), and whether it is
    inside."""
    near = np.full(at.shape, np.inf)
    within = np.zeros(at.shape, dtype=bool)
    for a, b in zip(outline, np.roll(outline, -1)):
        d = b - a
        t = np.clip(np.real((at - a) * np.conj(d)) / max(abs(d) ** 2, 1e-12), 0, 1)
        near = np.minimum(near, np.abs(at - a - d * t))
        if d.imag:
            within ^= ((a.imag > at.imag) != (b.imag > at.imag)) & (at.real < a.real + d.real * (at.imag - a.imag) / d.imag)
    return near, within


def depth_inside(outline: np.ndarray, at: np.ndarray) -> np.ndarray:
    """How far inside the outline each position is, 0 on it and outside it."""
    near, within = edge_distance(outline, at)
    return np.where(within, near, 0.0)


def skin_beside(render: np.ndarray, outline: np.ndarray, centre: complex, at: np.ndarray, px_m: float,
                past: float | None = None) -> np.ndarray:
    """For each position of `at` (complex, metres, any shape): the model's colour `past` (SKIN_FROM_M) past the outline
    in the same direction from the eye's centre, (..., 3)."""
    away = at - centre
    away = away / np.maximum(np.abs(away), 1e-9)
    turn = np.linspace(-np.pi, np.pi, 181)
    reach = np.array([np.max(np.real((outline - centre) * np.exp(-1j * a))) for a in turn])
    spot = centre + away * (np.interp(np.angle(away), turn, reach) + (SKIN_FROM_M if past is None else past))
    half = render.shape[0] / 2
    return face.sample(render, spot.real / px_m + half, -spot.imag / px_m + half)


def upper_share(outline: np.ndarray, at: np.ndarray) -> np.ndarray:
    """1 above the line through the two corners, 0 below it, easing over a millimetre."""
    a, b = outline[0], outline[len(outline) // 2]
    corners = a.imag + (b.imag - a.imag) * np.clip((at.real - a.real) / (b.real - a.real), 0, 1)
    return np.clip(0.5 + (at.imag - corners) / 0.001, 0, 1)


def dress_lids(base_color: Path, planned: dict[str, Any]) -> None:
    """The skin just outside each opening, in the texture. The painted eye the model had can reach past the new opening:
    a texel lighter than the model's own skin SKIN_FROM_M further out takes that skin (LID_LIGHT_M out, and on the cut
    edge, which is still sampled); darker ones stay. A lash line darkens the upper lid (LASH_M out), where the cut removed
    the painted one."""
    mesh = planned["mesh"]
    cam = planned["camera"]
    px_m, render = planned["px_m"], planned["render"]
    rel = mesh["verts"] - np.array(cam["target"])
    uvw = np.stack([rel @ np.array(cam[k]) for k in ("right", "up", "forward")], axis=1)
    tex = face.read_rgb(base_color)
    size = tex.shape[0]
    pts = face.uv_pixels(mesh["uv"], size)
    for eye in planned["eyes"]:
        cu, cv = eye["centre"]
        close = (np.hypot(uvw[:, 0] - cu, uvw[:, 1] - cv) < eye["width"]) & (np.abs(uvw[:, 2] - eye["lid"]) < 0.015)
        tris = np.where(close[mesh["faces"]].any(axis=1))[0]
        if len(tris) == 0:
            continue
        ras = face.rasterise(pts, np.zeros(len(pts)), mesh["faces"], size, tris)
        covered = ras["tri"] >= 0
        at3 = face.interpolate(uvw, mesh["faces"], ras)[covered]
        outline = np.array([complex(u, v) for u, v in eye["outline"]])
        at = at3[:, 0] + 1j * at3[:, 1]
        near, within = edge_distance(outline, at)
        deep = np.abs(at3[:, 2] - eye["lid"]) < 0.015
        use = deep & (within | (near < LID_LIGHT_M))
        px = tex[covered]
        a, b = outline[0], outline[len(outline) // 2]
        skin = skin_beside(render, outline, complex(cu, cv), at[use], px_m)
        old = px[use]
        lighter = np.clip((face.luminance(old) - face.luminance(skin)) / 0.06, 0, 1)
        k = np.where(within[use], 1.0, np.clip((LID_LIGHT_M - near[use]) / (0.35 * LID_LIGHT_M), 0, 1))
        k = (k * lighter)[:, None]
        px[use] = old * (1 - k) + skin * k
        corners = a.imag + (b.imag - a.imag) * np.clip((at.real - a.real) / (b.real - a.real), 0, 1)
        lash = np.where(deep & ~within & (at.imag > corners), np.clip((LASH_M - near) / (0.6 * LASH_M), 0, 1), 0.0)[:, None]
        tex[covered] = px * (1 - LASH * lash)
    face.save_rgb(tex, base_color)


def blend_edge(patch: np.ndarray, render: np.ndarray, eye: dict[str, Any], px_m: float) -> np.ndarray:
    """The eye patch fading into the skin beside it over the last EDGE_FADE_M inside the outline (and past it), where a
    white edge would read as a bright line between the lid and the eye. Shaded under the upper lid only."""
    size = patch.shape[0]
    p = eye["paint"]
    Y, X = np.mgrid[0:size, 0:size] + 0.5
    at = (p["centre_px"].real - p["half_px"] + X / size * 2 * p["half_px"]) + 1j * (
        p["centre_px"].imag - p["half_px"] + Y / size * 2 * p["half_px"])
    k = np.clip(1.0 - depth_inside(p["outline_px"], at) * px_m / EDGE_FADE_M, 0, 1)[..., None] * EDGE_FADE
    outline = np.array([complex(u, v) for u, v in eye["outline"]])
    metres = (at.real - render.shape[0] / 2) * px_m - 1j * (at.imag - render.shape[0] / 2) * px_m
    skin = skin_beside(render, outline, complex(*eye["centre"]), metres, px_m)
    lid = skin_beside(render, outline, complex(*eye["centre"]), metres, px_m, LID_EDGE_M)
    skin = np.where((face.luminance(lid) < face.luminance(skin))[..., None], lid, skin)
    floor = lid_colour(render, eye, px_m) * SKIN_FLOOR
    skin = skin * np.maximum(1.0, face.luminance(floor) / np.maximum(face.luminance(skin), 1e-3))[..., None]
    up = upper_share(outline, metres)
    shade = (EDGE_SHADE[1] + (EDGE_SHADE[0] - EDGE_SHADE[1]) * up)[..., None]
    return np.clip(patch * (1 - k) + skin * shade * k, 0, 1)


def rim_swatch(render: np.ndarray, eye: dict[str, Any], px_m: float, size: int) -> np.ndarray:
    """The socket's colour (size, size, 3): the skin just round the opening on the model, shading darker downwards (the
    top row is the lid's edge, the bottom row the back of the socket)."""
    skin = lid_colour(render, eye, px_m)
    fall = np.linspace(1.0, 0.75, size)[:, None, None]
    return np.clip(np.broadcast_to(skin, (size, size, 3)) * fall, 0, 1)


def place(uvmesh: dict[str, np.ndarray], tex: int) -> list[list[float]] | None:
    """Squares [u0, v0, side] in free texture space for the two eyes and then their two socket swatches, or None."""
    fixed = uvmesh["uv"][uvmesh["faces"]]
    for px in TEXTURE_PIECE_PX:
        side = px / 2048.0
        sides = [side, side, side * RIM_PIECE_SHARE, side * RIM_PIECE_SHARE]
        islands = [np.array([[[0, 0], [s, 0], [s, s]], [[0, 0], [s, s], [0, s]]], dtype=np.float64) for s in sides]
        spots = uvfit.place_islands(fixed, islands, res=max(512, min(tex, 1024)), pad_cells=4, keep_near=False)
        if spots is None:
            continue
        out = []
        for island, spot, s in zip(islands, spots, sides):
            moved = uvfit.apply_placement(island.reshape(-1, 2), spot)
            lo = moved.min(axis=0)
            out.append([float(lo[0]), float(lo[1]), float(s)])
        return out
    return None


def write_square(path: Path | str, rect: list[float], pixels: np.ndarray) -> None:
    """Write pixels (floats 0..1, or one value) into the texture's square rect, with a margin of edge colour round it."""
    with Image.open(path) as im:
        mode = im.mode
        arr = np.array(im)
    h, w = arr.shape[:2]
    u0, v0, side = rect
    x0, y0 = int(round(u0 * w)), int(round((1.0 - v0 - side) * h))
    sw, sh = max(2, int(round(side * w))), max(2, int(round(side * h)))
    top = 65535 if arr.dtype == np.uint16 else 255
    if pixels.ndim == 1 or pixels.shape[0] == 1:
        block = np.broadcast_to(pixels, (sh, sw) + pixels.shape[-1:])
    else:
        block = np.stack([np.asarray(Image.fromarray((pixels[..., c] * 255).astype(np.float32), "F").resize((sw, sh), Image.BOX))
                          for c in range(pixels.shape[2])], axis=-1) / 255.0
    margin = max(2, w // 512)
    block = np.pad(block, ((margin, margin), (margin, margin), (0, 0)), mode="edge")
    block = np.clip(block * top + 0.5, 0, top).astype(arr.dtype)
    y1, x1 = max(0, y0 - margin), max(0, x0 - margin)
    block = block[y1 - (y0 - margin):, x1 - (x0 - margin):][: h - y1, : w - x1]
    target = arr[y1:y1 + block.shape[0], x1:x1 + block.shape[1]]
    if arr.ndim == 2:
        target[...] = block[..., 0]
    else:
        k = min(block.shape[2], arr.shape[2])
        target[..., :k] = block[..., :k]
        if arr.shape[2] == 4:
            target[..., 3] = top
    Image.fromarray(arr, mode).save(path)


def paint_maps(maps: dict[str, Path], planned: dict[str, Any], picture: np.ndarray, rects: list[list[float]],
               out_dir: Path) -> dict[str, Path]:
    """Copies of the texture maps in out_dir with the eyes and socket swatches written into their squares. -> {key: path}."""
    out: dict[str, Path] = {}
    for key in ("base_color", "normal", "metallic", "roughness", "ao"):
        src = maps.get(key)
        if src is None or not Path(src).is_file():
            continue
        dest = Path(out_dir) / f"{key}.png"
        Image.open(src).save(dest)
        out[key] = dest
    if "base_color" in out:
        dress_lids(out["base_color"], planned)
    for i, eye in enumerate(planned["eyes"]):
        for kind, rect in (("eye", rects[i]), ("rim", rects[2 + i])):
            if kind == "eye":
                colour = tone_to_face(paint(picture, eye, planned["px_m"], 256), planned["render"], eye)
                colour = blend_edge(colour, planned["render"], eye, planned["px_m"])
            else:
                colour = rim_swatch(planned["render"], eye, planned["px_m"], 32)
            values = {"base_color": colour, "normal": np.array([[0.5, 0.5, 1.0]]), "metallic": np.array([[0.0, 0.0, 0.0]]),
                      "roughness": np.array([[SHADE_ROUGHNESS[kind]] * 3]), "ao": np.array([[1.0, 1.0, 1.0]])}
            for key, path in out.items():
                v = values[key]
                write_square(path, rect, v[0] if v.ndim == 2 else v)
    return out


def mesh_job(planned: dict[str, Any], rects: list[list[float]]) -> dict[str, Any]:
    """What blender/eyes.py needs (plain numbers)."""
    keys = ("outline", "centre", "width", "half", "lid")
    return {"camera": planned["camera"],
            "eyes": [{**{k: eye[k] for k in keys}, "outline": rounded(eye["outline"]), "eye_rect": rects[i],
                      "rim_rect": rects[2 + i]}
                     for i, eye in enumerate(planned["eyes"])]}

