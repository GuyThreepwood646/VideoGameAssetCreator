"""Paint the model's own base colour from the source picture (and the turnaround views), through the existing UVs.

docs/TEXTURE-FIDELITY-PLAN.md lane B. Today no pixel of the concept picture reaches the body texture: the 3D step sees
only features of it and predicts a soft colour field. This pass projects the picture itself into the finished atlas,
with no new unwrap and no rebake, so a texel the picture sees squarely shows the picture's own pixel.

The sources, in order of priority: the original picture (the one the asset started from: what the texture should look
like), or the front view drawn from it when the original does not fit the model; then the picture the 3D model was built
from (for a character that is the three-quarter view); then the other turnaround views. A view only paints where the
views before it do not see well.

For each view:
1. the model is drawn from an orthographic camera (the face pass's rasteriser) and the picture's cut-out is fitted to
   that drawing by silhouette (scale, shift, a few degrees of turn, and the camera's yaw when it is not known); a view
   whose outline matches under MIN_IOU is refused, and so is one whose turn the outline cannot settle (a box fits at any
   turn, and a wrong turn paints a feature twice);
2. the picture is warped onto the drawing with a dense optical flow (the soft TRELLIS colour is the target), limited to
   a few percent of the frame, smoothed, and scaled back if it would fold; the flow is dropped when it does not improve
   the structure match;
3. every texel (islands and a few texels of border) the view sees squarely takes the picture's colour with weight
   view weight x cos^4 of the angle to the surface, depth tested, zero past 75 degrees, feathered at the outline. The
   picture wins where it sees well: the turnaround views fade out where the picture's own weight is high;
4. turnaround views either give full colour ("full", the default: the back and far sides then look like the drawn
   views) or only fine detail on top of the TRELLIS colour ("detail": the TRELLIS colour below a few texels' worth of
   surface, the view's detail above it, so an AI side or back view cannot shift tone);
   Gates keep the picture out of places it would smear or paint twice: surface seen more edge-on than the stretch limit
   (one picture pixel covering more than 2 to 3 texels along the surface), pixels next to a jump in depth (a limb in
   front of the body), regions where the picture and the model's own colour disagree on structure (a padlock painted
   beside the modelled padlock; tone is not compared), and regions where the flow ran into its limit. The share of
   painting weight each gate removes is recorded in the metrics;
5. texels no view reaches keep their colour with its low frequencies shifted, along the surface, to agree with the
   painted colour near them (no tone step), and island borders are grown into the gutter.

Pure numpy and Pillow. Coordinates are the glTF ones face.load_mesh gives. Measured on the bake-off knight, wolf and
chest (bakeoff/source_project_trial.py, bakeoff/out/source_project/results.md).
"""

from __future__ import annotations

import asyncio
import contextvars
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image

from . import face, imaging

SIZE = 2048              # working render size; the picture is sampled bicubic (Catmull-Rom) once, at this size
PIC_N = 1024            # side the pictures are worked at when they come as they are
PIC_N_BIG = 2048        # ... and when they were made bigger first (upscale_sources), or came bigger
UPSCALE_BELOW = 1536    # a picture whose longer side is under this is made bigger before it is painted
FIT_N = 256              # size the camera and silhouette search runs at
FLOW_N = 512             # size the optical flow runs at
MIN_IOU = 0.85           # silhouette match below this refuses a view
COS_CUT = math.cos(math.radians(75.0))
MAX_FLOW = 0.02          # of the frame
FLOW_ALPHA = 2.0         # Horn-Schunck smoothness (higher: a stiffer field)
FLOW_SMOOTH = 8.0        # Gaussian sigma of the field in flow pixels
VIEW_WEIGHT = {"source": 1.0, "front": 1.0, "picture": 1.0, "quarter": 0.5, "quarter2": 1.0, "left": 0.5, "right": 0.5, "back": 0.5}
BLEND_POW = 2.0          # the dominant view of a texel counts as weight squared, so views do not blur each other
FRONT_PRIORITY = 0.4     # where a priority picture's own weight reaches this, the views after it are switched off. At 0.15
                         # the original photo kept painting round the cheek to about 52 degrees off its line of sight and
                         # laid its own cheek outline there as a crease; at 0.4 it hands over at about 37 degrees.
ALPHA_RAMP = (0.004, 0.06)
GUTTER_PX = 3
DETAIL_TEXELS = 2.0      # "detail" mode: the TRELLIS colour is kept below this many texels of surface
BG = 0.5
STRETCH = (2.0, 3.5)     # one picture pixel covering this many texels along the surface: weight falls from 1 to 0
DISC_PX = 4              # pixels (at the working size) kept away from depth discontinuities
GATES = {"disc": True, "stretch": True, "agree": True, "flowsat": True}
AGREE = {"lo": 0.025, "hi": 0.05, "reach": 0.25, "grow": 0.07}   # structure agreement gate, see agreement_gate
TURN_YAWS = {
    "quarter": [y for y in range(20, 71, 10)] + [-y for y in range(20, 71, 10)],
    "quarter2": [y for y in range(20, 71, 10)] + [-y for y in range(20, 71, 10)],  # the other side's three-quarter view
    "left": [y for y in range(70, 111, 10)] + [-y for y in range(70, 111, 10)],
    "right": [y for y in range(70, 111, 10)] + [-y for y in range(70, 111, 10)],
    "back": list(range(140, 221, 10)),
}
PICTURE_YAWS = list(range(0, 360, 10))
FRONT_YAWS = [-20, -10, 0, 10, 20]
SOURCE_MIN_IOU = 0.82    # the original picture is taken at a slightly looser outline match than the drawn views (the
                         # photo boy: 0.843, and with it colour difference 9.0 to 4.7 and his own face on the model)
TURN_AGREE = False       # the structure gate on the drawn side and back views when they paint in full colour: off,
                         # because there it compares two guesses (the builder's back and the drawn back) and cuts the
                         # drawn one into patches
TURN_COLOUR_MARGIN = 3.0  # colour difference (Lab) by which the chosen turn must beat every far turn to settle an
                          # outline that fits both ways round
MAX_SHARED = 0.15        # share of the texture layout used by more than one triangle above which nothing is painted
WORKERS = max(1, min(6, (os.cpu_count() or 2) - 1))   # views fitted at the same time
SECOND_QUARTER = "quarter2"
TURN_VIEWS = ("quarter", SECOND_QUARTER, "left", "right", "back")
# The face region (docs/AFTER-TEST-CHANGES.md D2 items 5 to 8): the face as the face pass finds it (face.find_face on the
# model's own colour, seen from where the face looks), carried to the texels.
OFF_FACE_VIEWS = ("left", "right", "back")   # views that never paint the face region (item 5)
FACE_ZONE_N = 1024        # size of the head picture the face region is found on
FACE_ZONE_FEATHER = 0.02  # of that picture: how far past the face region's edge the side views fade back in
GLASSES_JUMP_M = 0.004    # a step in depth of this much inside the face is the rim of a solid piece in front of it (glasses)
FILL_FEATHER = 0.05       # of the face's width: how softly the skin fill ends round the eyes-nose-mouth box
SKIN_TOL = 14.0           # Lab distance (lightness half) from the painted face's middle colour that still counts as skin
HEAD_VIEW = "head"        # the first picture fitted on the head alone, by its eyes and mouth (item 6)
HEAD_NECK = (0.95, 0.75)  # the head view fades in up the neck between these shares of (nose tip to top of head) below the
                          # nose tip (the chin is at about 0.65): the hand-over to the body's own fit lies on the neck
HEAD_SIDES = ("quarter", SECOND_QUARTER)  # the three-quarter views that get a head view of their own, after the front's
SIDE_HEAD_TURN = (15.0, 80.0)  # a three-quarter view's whole-body fit beyond the second is not of the face
SIDE_HEAD_YAW = 40.0      # the turn from the face a three-quarter head view is seen from when its body fit gives none
HEAD_HANDOVER = 0.1       # where a head view's weight reaches this, no whole-body fit paints (they hand over at the neck)
SQUASH_MAX = 0.05         # a head fit whose two scales differ by more than this share is fitted again with one scale
SIDE_AGREE_MAX = 0.12     # of the face's width: how far a three-quarter head view's own fit may put the eyes and mouth from
                          # where the front head view painted them before it is refused
# The turn a drawn view was fitted at, against the front's (item 8). The side a view belongs on (+1: toward the
# character's left, a positive camera yaw; every good stored run fits them so), the least turn from the front a full
# side view may sit at, and the least for the back.
TURN_SIDE = {"quarter": 1, SECOND_QUARTER: -1, "left": 1, "right": -1}
SIDE_MIN_TURN = 45.0
BACK_MIN_TURN = 120.0


# --- image helpers ----------------------------------------------------------------------------------------------


def catmull(img: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Bicubic (Catmull-Rom) sample of img (H,W,c) at pixel coordinates (centres at +0.5), edges clamped."""
    h, w = img.shape[:2]
    fx, fy = x - 0.5, y - 0.5
    x0, y0 = np.floor(fx).astype(int), np.floor(fy).astype(int)
    tx, ty = (fx - x0)[..., None], (fy - y0)[..., None]

    def wts(t: np.ndarray) -> list[np.ndarray]:
        t2, t3 = t * t, t * t * t
        return [(-t3 + 2 * t2 - t) / 2, (3 * t3 - 5 * t2 + 2) / 2, (-3 * t3 + 4 * t2 + t) / 2, (t3 - t2) / 2]

    wx, wy = wts(tx), wts(ty)
    out: Any = 0.0
    for j in range(4):
        yy = np.clip(y0 + j - 1, 0, h - 1)
        row: Any = 0.0
        for i in range(4):
            xx = np.clip(x0 + i - 1, 0, w - 1)
            row = row + wx[i] * img[yy, xx]
        out = out + wy[j] * row
    return out


def small(a: np.ndarray, n: int) -> np.ndarray:
    return face.resize(a, n, Image.BOX if a.shape[0] >= n else Image.BILINEAR)


def _rs(a: np.ndarray, h: int, w: int) -> np.ndarray:
    if a.shape == (h, w):
        return a
    return np.asarray(Image.fromarray(a.astype(np.float32), "F").resize((w, h), Image.BILINEAR), dtype=np.float64)


def square_pad(rgb: np.ndarray, alpha: np.ndarray | None) -> tuple[np.ndarray, np.ndarray | None]:
    """A picture that is not square, centred on a square of its longer side. The added border is transparent when the
    picture has a cut-out, else the backdrop's colour (the corners', which is what prepare_picture reads as backdrop).
    Resizing a tall picture or a phone photo straight to a square squashed the figure, and it then failed the outline
    match (the tall test picture: 0.552 squashed, 0.843 padded; the real photo: 0.577 and 0.921)."""
    h, w = rgb.shape[:2]
    if h == w:
        return rgb, alpha
    s = max(h, w)
    y0, x0 = (s - h) // 2, (s - w) // 2
    if alpha is None:
        corners = np.array([rgb[min(2, h - 1), min(2, w - 1)], rgb[min(2, h - 1), max(w - 3, 0)],
                            rgb[max(h - 3, 0), min(2, w - 1)], rgb[max(h - 3, 0), max(w - 3, 0)]])
        fill = np.median(corners, axis=0)
    else:
        fill = np.zeros(3)
    out = np.empty((s, s, 3), dtype=np.float64)
    out[...] = fill
    out[y0:y0 + h, x0:x0 + w] = rgb
    if alpha is None:
        return out, None
    a = np.zeros((s, s), dtype=np.float64)
    a[y0:y0 + h, x0:x0 + w] = alpha
    return out, a


def prepare_picture(src: Path | str | Image.Image | np.ndarray, n: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Colour (n,n,3) 0..1 and cut-out alpha (n,n) of a picture. The alpha is the picture's own when it has one; else the
    cut-out is whatever is not the backdrop (the colour of the corners), closed and with holes filled. Edges are
    defringed: colour is pulled outward from 2 px inside the cut-out. A picture that is not square is padded to a
    square first (square_pad), never squashed: the subject keeps its proportions."""
    n = PIC_N if n is None else n
    if isinstance(src, np.ndarray):
        arr = np.asarray(src, dtype=np.float64)
        rgb = arr[..., :3]
        alpha = arr[..., 3] if arr.shape[-1] == 4 else None
    else:
        im = src if isinstance(src, Image.Image) else Image.open(src)
        rgba = np.asarray(im.convert("RGBA"), dtype=np.float64) / 255.0
        rgb, alpha = rgba[..., :3], rgba[..., 3]
    if alpha is not None and alpha.min() > 0.98:
        alpha = None
    rgb, alpha = square_pad(rgb, alpha)
    if rgb.shape[0] != n:
        rgb = face.resize(rgb, n, Image.LANCZOS)
        alpha = face.resize(alpha, n, Image.BILINEAR) if alpha is not None else None
    if alpha is None:
        corners = np.array([rgb[2, 2], rgb[2, -3], rgb[-3, 2], rgb[-3, -3]])
        bg = np.median(corners, axis=0)
        nm = np.abs(rgb - bg).max(2) > 0.035
        alpha = face.fill_holes(face.erode(face.dilate_mask(nm, 3), 3)).astype(np.float64)
    core = face.erode(alpha > 0.5, 2)
    rgb = imaging.dilate(rgb, core, 8)
    alpha = np.clip(face.blur(face.dilate_mask(core, 3).astype(np.float64), 0.8), 0.0, 1.0)
    return rgb, alpha


class Prepared:
    """prepare_picture once per picture and size within one paint() call: the original, the drawn front and both
    three-quarter pictures are each wanted again by the head views after their whole-body fit (docs/SPEED-AUDIT.md O4
    a0, picture loading). The arrays are handed out read-only, so no caller can change what another one reads. Thread
    safe: a picture asked for twice at once is prepared once."""

    def __init__(self) -> None:
        import threading

        self._lock = threading.Lock()
        self._done: dict[Any, Any] = {}

    def __call__(self, src: Any, n: int | None = None) -> tuple[np.ndarray, np.ndarray]:
        from concurrent.futures import Future

        key = (str(src) if isinstance(src, (str, Path)) else id(src), n)
        with self._lock:
            fut = self._done.get(key)
            mine = fut is None
            if mine:
                fut = self._done[key] = Future()
        if mine:
            try:
                rgb, alpha = prepare_picture(src, n)
                rgb.setflags(write=False)
                alpha.setflags(write=False)
                fut.set_result((rgb, alpha))
            except BaseException as e:  # noqa: BLE001  (the asker sees the same error)
                fut.set_exception(e)
        return fut.result()


# --- camera, raster ---------------------------------------------------------------------------------------------


def model_frame(mesh: dict[str, np.ndarray]) -> tuple[np.ndarray, float]:
    v = mesh["verts"]
    lo, hi = v.min(0), v.max(0)
    centre = (lo + hi) / 2
    scale = float(max(hi[1] - lo[1], np.hypot(hi[0] - lo[0], hi[2] - lo[2]))) * 1.06
    return centre, scale


class Raster:
    """One camera's view of the mesh: triangle map, depth, facing cosines, and the atlas position of every pixel."""

    def __init__(self, mesh: dict[str, np.ndarray], cam: face.Camera):
        self.cam = cam
        ras = face.view_raster(mesh, cam)
        self.mask = ras["tri"] >= 0
        self.z = ras["z"]
        m = self.mask
        n = face.interpolate(mesh["vnormals"], mesh["faces"], ras)
        n /= np.maximum(np.linalg.norm(n, axis=2, keepdims=True), 1e-12)
        self.cos = np.where(m, -(n @ cam.basis[2]), 0.0)
        v, f = mesh["verts"], mesh["faces"]
        fn = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
        fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
        self.fcos = np.where(m, -(fn[np.maximum(ras["tri"], 0)] @ cam.basis[2]), 0.0)
        self.uv = face.interpolate(face.uv_pixels(mesh["uv"], 1), mesh["faces"], ras)


def render_colour(ras: Raster, tex: np.ndarray, bg: float = BG) -> np.ndarray:
    T = tex.shape[0]
    img = np.full(ras.mask.shape + (3,), bg)
    uv = ras.uv[ras.mask] * T
    img[ras.mask] = face.sample(tex, uv[:, 0], uv[:, 1])
    return img


# --- fit: similarity transform of the picture onto the drawing -------------------------------------------------


def outline(mesh: dict[str, np.ndarray], cam: face.Camera) -> np.ndarray:
    """The model's outline from a camera: exactly the pixels face.view_raster covers (the same inside test on the same
    pixel centres), without the depth buffer the turn search has no use for, and with the triangles tested many at a
    time instead of one by one."""
    s = cam.size
    pts, _ = cam.project(mesh["verts"])
    P = pts[mesh["faces"]]
    P = P[(P[:, :, 0].max(1) >= 0) & (P[:, :, 0].min(1) <= s) & (P[:, :, 1].max(1) >= 0) & (P[:, :, 1].min(1) <= s)]
    mask = np.zeros((s, s), dtype=bool)
    if len(P) == 0:
        return mask
    x0 = np.clip(np.floor(P[:, :, 0].min(1) - 0.5).astype(int), 0, s)
    x1 = np.clip(np.ceil(P[:, :, 0].max(1) - 0.5).astype(int) + 1, 0, s)
    y0 = np.clip(np.floor(P[:, :, 1].min(1) - 0.5).astype(int), 0, s)
    y1 = np.clip(np.ceil(P[:, :, 1].max(1) - 0.5).astype(int) + 1, 0, s)
    ax, ay, bx, by, cx, cy = P[:, 0, 0], P[:, 0, 1], P[:, 1, 0], P[:, 1, 1], P[:, 2, 0], P[:, 2, 1]
    area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    span = np.maximum(x1 - x0, y1 - y0)
    live = (x1 > x0) & (y1 > y0) & (np.abs(area) >= 1e-12)
    lo = 0
    while lo < s:
        hi = max(4, lo * 2)   # triangles whose box is at most hi pixels wide and tall, tested on a hi x hi grid each
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
            xi_b, yi_b = np.broadcast_to(xi, inside.shape), np.broadcast_to(yi, inside.shape)
            mask[yi_b[inside], xi_b[inside]] = True
    return mask


_GRIDS: dict[int, np.ndarray] = {}


def _grid(n: int) -> np.ndarray:
    """The pixel centres of an n x n frame as rows (x, y, 1), made once per size."""
    g = _GRIDS.get(n)
    if g is None:
        ys, xs = np.mgrid[0:n, 0:n] + 0.5
        g = _GRIDS[n] = np.stack([xs.ravel(), ys.ravel(), np.ones(n * n)])
    return g


def sim_matrix(s: float, rot: float, tx: float, ty: float, n: int) -> np.ndarray:
    """3x3 map from a drawing pixel p to a picture pixel q = c + R(rot)/s (p - c - t), c the frame centre."""
    c = n / 2.0
    cr, sr = math.cos(rot), math.sin(rot)
    R = np.array([[cr, -sr], [sr, cr]]) / s
    M = np.eye(3)
    M[:2, :2] = R
    M[:2, 2] = np.array([c, c]) - R @ np.array([c + tx, c + ty])
    return M


def warp_mask(pic: np.ndarray, M: np.ndarray, n: int) -> np.ndarray:
    q = M @ _grid(n)
    return face.sample(pic[..., None], q[0].reshape(n, n), q[1].reshape(n, n))[..., 0]


def iou(a: np.ndarray, b: np.ndarray) -> float:
    return float((a & b).sum()) / float(max((a | b).sum(), 1))


def iou_warped(pm: np.ndarray, support: tuple[int, int, int, int] | None, M: np.ndarray, rm: np.ndarray, rsum: int,
               n: int) -> float:
    """iou(warp_mask(pm, M, n) > 0.5, rm), exactly, sampling only the part of the frame that lands on the picture's
    figure: a bilinear sample is above zero only when one of its four neighbours is, so outside the figure's box (one
    pixel round) the warped mask is zero and only the model's outline counts there (rsum is its size). The turn search
    scores about 150 warps per candidate turn, and the figure covers a small part of the frame (docs/SPEED-AUDIT.md O4
    a0). A figure touching the picture's edge, which the border clamp repeats outward, is warped whole as before."""
    h, w = pm.shape
    if support is None:
        return 0.0
    x0s, x1s, y0s, y1s = support
    if x0s <= 0 or y0s <= 0 or x1s >= w - 1 or y1s >= h - 1:
        return iou(warp_mask(pm, M, n) > 0.5, rm)
    corners = np.array([[x0s - 1.5, x1s + 2.5, x0s - 1.5, x1s + 2.5], [y0s - 1.5, y0s - 1.5, y1s + 2.5, y1s + 2.5], [1.0, 1.0, 1.0, 1.0]])
    d = np.linalg.inv(M) @ corners
    dx0, dx1 = max(0, int(np.floor(d[0].min())) - 2), min(n, int(np.ceil(d[0].max())) + 2)
    dy0, dy1 = max(0, int(np.floor(d[1].min())) - 2), min(n, int(np.ceil(d[1].max())) + 2)
    if dx1 <= dx0 or dy1 <= dy0:
        return 0.0
    ys, xs = np.mgrid[dy0:dy1, dx0:dx1] + 0.5
    qx = M[0, 0] * xs + M[0, 1] * ys + M[0, 2]
    qy = M[1, 0] * xs + M[1, 1] * ys + M[1, 2]
    a = face.sample(pm[..., None], qx, qy)[..., 0] > 0.5
    inter = int((a & rm[dy0:dy1, dx0:dx1]).sum())
    return float(inter) / float(max(int(a.sum()) + rsum - inter, 1))


def fit_similarity(pic_mask: np.ndarray, render_mask: np.ndarray, n: int = FIT_N) -> dict[str, Any]:
    """Scale, shift and a few degrees of turn of the picture's cut-out onto the drawing's mask, by overlap (IoU), from
    the bounding boxes."""
    pm = small(pic_mask.astype(np.float64), n)
    rm = small(render_mask.astype(np.float64), n) > 0.5
    if not rm.any() or not (pm > 0.5).any():
        return {"s": 1.0, "rot": 0.0, "t": (0.0, 0.0), "iou": 0.0, "n": n}

    def bbox(m: np.ndarray) -> tuple[int, int, int, int]:
        ys, xs = np.where(m)
        return xs.min(), xs.max() + 1, ys.min(), ys.max() + 1

    px0, px1, py0, py1 = bbox(pm > 0.5)
    rx0, rx1, ry0, ry1 = bbox(rm)
    ratios = ((ry1 - ry0) / (py1 - py0), (rx1 - rx0) / (px1 - px0))
    s0 = 0.5 * (max(ratios) + min(ratios))
    c = n / 2.0
    tx0 = (rx0 + rx1) / 2 - c - s0 * ((px0 + px1) / 2 - c)
    ty0 = (ry0 + ry1) / 2 - c - s0 * ((py0 + py1) / 2 - c)

    ys_, xs_ = np.nonzero(pm > 0)
    support = (int(xs_.min()), int(xs_.max()), int(ys_.min()), int(ys_.max()))
    rsum = int(rm.sum())

    def score(p: list[float]) -> float:
        return iou_warped(pm, support, sim_matrix(p[0], p[1], p[2], p[3], n), rm, rsum, n)

    best = [s0, 0.0, tx0, ty0]
    bs = score(best)
    steps = [0.04 * s0, math.radians(2.0), 6.0, 6.0]
    for _ in range(40):
        improved = False
        for i in range(4):
            for d in (-1, 1):
                cand = list(best)
                cand[i] += d * steps[i]
                if (i == 0 and cand[0] <= 0.2) or abs(cand[1]) > math.radians(6):
                    continue
                sc = score(cand)
                if sc > bs + 1e-5:
                    best, bs, improved = cand, sc, True
        if not improved:
            steps = [x * 0.5 for x in steps]
            if steps[2] < 0.05:
                break
    return {"s": best[0], "rot": best[1], "t": (best[2], best[3]), "iou": bs, "n": n}


def sim_to_full(fit: dict[str, Any], pic_n: int, out_n: int) -> np.ndarray:
    n = fit["n"]
    M = sim_matrix(fit["s"], fit["rot"], fit["t"][0], fit["t"][1], n)
    return np.diag([pic_n / n, pic_n / n, 1.0]) @ M @ np.diag([n / out_n, n / out_n, 1.0])


# --- dense optical flow (multi-scale Horn-Schunck on structure channels) -----------------------------------------


def _grad(a: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    gx = np.zeros_like(a)
    gy = np.zeros_like(a)
    gx[:, 1:-1] = (a[:, 2:] - a[:, :-2]) / 2
    gy[1:-1] = (a[2:] - a[:-2]) / 2
    return gx, gy


def _avg(a: np.ndarray) -> np.ndarray:
    p = np.pad(a, 1, mode="edge")
    return (p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:]) / 4.0


def _hs_update(gx, gy, it, ubar, vbar, a2):  # noqa: ANN001
    sxx, sxy, syy = (gx * gx).sum(-1), (gx * gy).sum(-1), (gy * gy).sum(-1)
    b1 = a2 * ubar - (gx * it).sum(-1)
    b2 = a2 * vbar - (gy * it).sum(-1)
    det = (sxx + a2) * (syy + a2) - sxy * sxy
    return ((syy + a2) * b1 - sxy * b2) / det, ((sxx + a2) * b2 - sxy * b1) / det


def structure_channels(img: np.ndarray, mask: np.ndarray, s: float) -> np.ndarray:
    """Channels for matching a sharp picture with a soft, differently lit drawing: the luminance band between s and 4s
    over its local spread, and the smoothed silhouette."""
    lum = face.luminance(img)
    band = face.masked_blur(lum, mask, s) - face.masked_blur(lum, mask, 4 * s)
    spread = np.sqrt(np.maximum(face.masked_blur(band * band, mask, 6 * s), 1e-6))
    norm = np.clip(band / np.maximum(spread, 0.02), -3, 3) * 0.4 * mask
    return np.stack([norm, face.blur(mask.astype(np.float64), 1.5 * s)], axis=-1)


def hs_flow(ref: np.ndarray, mov: np.ndarray, region: np.ndarray, max_px: float, levels: int = 5, iters: int = 60,
            alpha: float = FLOW_ALPHA) -> np.ndarray:
    """Flow u (H,W,2) so that mov(p + u(p)) matches ref(p); coarse to fine, displacement clipped to max_px."""
    H, W = ref.shape[:2]
    u = None
    for lv in range(levels - 1, -1, -1):
        k = 2 ** lv
        h, w = max(H // k, 8), max(W // k, 8)
        m = np.stack([_rs(mov[..., c], h, w) for c in range(mov.shape[2])], -1)
        r = np.stack([_rs(ref[..., c], h, w) for c in range(ref.shape[2])], -1)
        reg = _rs(region.astype(np.float64), h, w)
        if u is None:
            u = np.zeros((h, w, 2))
        else:
            sc = w / u.shape[1]
            u = np.stack([_rs(u[..., 0], h, w), _rs(u[..., 1], h, w)], -1) * sc
        Y, X = np.mgrid[0:h, 0:w] + 0.5
        a2 = alpha ** 2
        for _outer in range(3):
            mw = face.sample(m, X + u[..., 0], Y + u[..., 1])
            gx, gy = np.zeros_like(mw), np.zeros_like(mw)
            for c in range(mw.shape[2]):
                gx[..., c], gy[..., c] = _grad(mw[..., c])
            it = mw - r
            du, dv = np.zeros((h, w)), np.zeros((h, w))
            for _ in range(iters):
                du, dv = _hs_update(gx, gy, it, _avg(du), _avg(dv), a2)
            u = u + np.stack([du, dv], -1) * reg[..., None]
            lim = max_px * (w / W)
            u = np.clip(u, -lim, lim)
        u = np.stack([face.blur(u[..., 0], 1.0 + 0.5 * lv), face.blur(u[..., 1], 1.0 + 0.5 * lv)], -1)
    return u


# --- one view ---------------------------------------------------------------------------------------------------


class View:
    def __init__(self, name: str, weight: float):
        self.name, self.weight = name, weight
        self.ok = False
        self.info: dict[str, Any] = {"name": name}


def fit_view(mesh: dict[str, np.ndarray], tex: np.ndarray, pic: np.ndarray, alpha: np.ndarray, name: str,
             yaws: list[float], centre: np.ndarray, scale: float, size: int = SIZE, min_iou: float = MIN_IOU,
             use_flow: bool = True, log: Callable[[str], None] | None = None,
             outlines: dict[tuple[float, int], np.ndarray] | None = None) -> View:
    """Find the camera turn (among yaws), the picture's similarity fit and its flow warp; refuse what does not fit.
    `outlines` is a store of the model's outline per (turn, size) shared by the views of one paint() call: two pictures
    searching the same turns draw the model once."""
    v = View(name, VIEW_WEIGHT.get(name, 0.5))
    n = min(FIT_N, size)
    pm = alpha > 0.5
    fits: dict[float, dict[str, Any]] = {}
    pic_s = small(pic, n)

    def try_yaw(yaw: float, nn: int = n) -> dict[str, Any]:
        if yaw not in fits or fits[yaw]["n"] != nn:
            key = (float(yaw), nn)
            rm = outlines.get(key) if outlines is not None else None
            if rm is None:
                rm = outline(mesh, face.Camera(target=centre, yaw_deg=float(yaw), scale=scale, size=nn))
                if outlines is not None:
                    outlines[key] = rm
            f = fit_similarity(pm, rm, n=nn)
            f["rm"] = rm
            fits[yaw] = f
        return fits[yaw]

    if len(yaws) > 6:  # a wide search: a coarse pass at half size, then the best turns again at full size
        for yaw in yaws:
            try_yaw(float(yaw), n // 2)
        coarse_top = max(f["iou"] for f in fits.values())
        keep = sorted((y for y, f in fits.items() if f["iou"] >= coarse_top - 0.06), key=lambda y: -fits[y]["iou"])[:14]
        fits = {y: fits[y] for y in keep}
    for yaw in (fits if len(yaws) > 6 else yaws):
        try_yaw(float(yaw))
    if len(yaws) > 1:
        top = max(fits, key=lambda y: fits[y]["iou"])
        for d in (-5.0, -2.5, 2.5, 5.0):
            try_yaw(top + d)
    top_iou = max(f["iou"] for f in fits.values())
    pool = [y for y, f in fits.items() if f["iou"] >= top_iou - 0.03]
    best_y, best_d, best_corr = pool[0], 1e9, -1.0
    lds: dict[float, float] = {}
    if len(pool) > 1:
        for y in pool:
            f = fits[y]
            rc = render_colour(Raster(mesh, face.Camera(target=centre, yaw_deg=float(y), scale=scale, size=n)), tex)
            M = sim_matrix(f["s"], f["rot"], f["t"][0], f["t"][1], n)
            ys_, xs_ = np.mgrid[0:n, 0:n] + 0.5
            ap = catmull(pic_s, M[0, 0] * xs_ + M[0, 1] * ys_ + M[0, 2], M[1, 0] * xs_ + M[1, 1] * ys_ + M[1, 2])
            both = f["rm"] & (warp_mask(small(pm.astype(np.float64), n), M, n) > 0.5)
            if both.sum() > 200:
                s_ = 0.008 * n
                la = face.to_lab(np.clip(face.masked_blur(rc, both, s_), 0, 1))
                lb = face.to_lab(np.clip(face.masked_blur(ap, both, s_), 0, 1))
                ld = float(np.linalg.norm(la - lb, axis=-1)[face.erode(both, 2)].mean())
                corr = face.feature_match(ap, rc, both, lo=1.0, hi=4.0)
                d = ld - 40.0 * corr  # colour and structure agreement together
                lds[y] = ld
            else:
                corr, d = -1.0, 1e9
            best_corr = max(best_corr, corr)
            if d < best_d:
                best_y, best_d = y, d
    yaw, fit = best_y, fits[best_y]
    ys_sorted = sorted(float(y) % 360 for y in pool)
    gaps = [(ys_sorted[(i + 1) % len(ys_sorted)] - ys_sorted[i]) % 360 for i in range(len(ys_sorted))]
    spread = 360.0 - max(gaps) if len(ys_sorted) > 1 else 0.0
    v.info.update(yaw=float(yaw), iou=round(float(fit["iou"]), 4), scale=round(float(fit["s"]), 4),
                  rot_deg=round(math.degrees(fit["rot"]), 2), pool_spread_deg=round(spread, 1))
    # The original picture of a figure standing square fits its outline from the front and from the back alike. When the
    # model's own colour is clearly closer to the picture at the chosen turn than at any turn far from it, the colour has
    # settled it (the photo boy: yellow shirt front, spiral back). Only for the original: a prop's picture that fits at any
    # turn stays refused.
    far = [lds[y] for y in pool if y in lds and abs(((y - best_y + 180.0) % 360.0) - 180.0) > 60.0]
    by_colour = name == "source" and best_y in lds and bool(far) and lds[best_y] <= min(far) - TURN_COLOUR_MARGIN
    if spread > 90.0 and best_corr < 0.05 and by_colour:
        v.info["turn_by_colour"] = {"here": round(lds[best_y], 2), "elsewhere": round(min(far), 2)}
    if spread > 90.0 and best_corr < 0.05 and not by_colour:
        v.info["refused"] = f"the turn is ambiguous (the outline fits over {spread:.0f} degrees)"
    elif fit["iou"] < min_iou:
        v.info["refused"] = f"silhouette match {fit['iou']:.3f} is under {min_iou}"
    if "refused" in v.info:
        if log:
            log(f"view {name}: refused, {v.info['refused']}")
        return v
    cam = face.Camera(target=centre, yaw_deg=float(yaw), scale=scale, size=size)
    v.cam, v.raster = cam, Raster(mesh, cam)
    M = sim_to_full(fit, pic.shape[0], size)
    ys, xs = np.mgrid[0:size, 0:size] + 0.5
    qx, qy = M[0, 0] * xs + M[0, 1] * ys + M[0, 2], M[1, 0] * xs + M[1, 1] * ys + M[1, 2]
    a_aff = catmull(pic, qx, qy)
    al_aff = face.sample(alpha[..., None], qx, qy)[..., 0]
    rm = v.raster.mask
    v.render = render_colour(v.raster, tex)
    fn = min(FLOW_N, size)
    k = size // fn
    r_s, a_s = small(v.render, fn), small(a_aff, fn)
    m_s = small(rm.astype(np.float64), fn) > 0.5
    pm_s = small((al_aff > 0.5).astype(np.float64), fn) > 0.5
    both_s = m_s & pm_s
    sg = fn / 256.0 * 1.2
    flow_used, a_use, al_use = False, a_aff, al_aff
    if use_flow and both_s.any():
        u = hs_flow(structure_channels(r_s, m_s, sg), structure_channels(a_s, pm_s, sg), face.dilate_mask(m_s | pm_s, 6), MAX_FLOW * fn)
        u = np.stack([face.blur(u[..., 0], FLOW_SMOOTH), face.blur(u[..., 1], FLOW_SMOOTH)], -1)
        fold = 1.0  # no folding: scale the field down until its Jacobian determinant stays above 0.6 inside the figure
        for _ in range(8):
            dux, duy = _grad(u[..., 0] * fold)
            dvx, dvy = _grad(u[..., 1] * fold)
            if float((((1 + dux) * (1 + dvy) - duy * dvx)[both_s] < 0.6).mean()) < 0.002:
                break
            fold *= 0.8
        u = u * fold
        uf = np.stack([_rs(u[..., c], size, size) * k for c in range(2)], -1)
        qx2 = M[0, 0] * (xs + uf[..., 0]) + M[0, 1] * (ys + uf[..., 1]) + M[0, 2]
        qy2 = M[1, 0] * (xs + uf[..., 0]) + M[1, 1] * (ys + uf[..., 1]) + M[1, 2]
        a_flow = catmull(pic, qx2, qy2)
        inner = face.erode(small(rm.astype(np.float64), 1024) > 0.5, 3)
        fm0 = face.feature_match(small(a_aff, 1024), small(v.render, 1024), inner)
        fm1 = face.feature_match(small(a_flow, 1024), small(v.render, 1024), inner)
        v.info.update(feature_match_affine=fm0, feature_match_flow=fm1, flow_fold_scale=round(fold, 3))
        if fm1 >= fm0 - 0.005:
            flow_used, a_use = True, a_flow
            al_use = face.sample(alpha[..., None], qx2, qy2)[..., 0]
    v.info["flow_used"] = flow_used
    v.pic_aligned, v.pic_affine, v.alpha_img = a_use, a_aff, al_use
    v.valid = rm & (al_use > 0.5)
    r = max(2, size // 340)
    v.feather = v.feather0 = np.clip(face.blur(face.erode(v.valid, r).astype(np.float64), r * 0.8) / 0.9, 0.0, 1.0)
    v.size = size
    v.pic_px_m = (cam.scale / size) / float(np.hypot(M[0, 0], M[1, 0]))     # metres of surface one picture pixel spans
    # The stretch limit is in pixels of a picture at its own size: a picture made twice as big before it came here has no
    # more to say at a steep angle than it had, so its pixels count double (else upscaling quietly loosens the limit).
    v.pic_px_m *= pic.shape[0] / float(PIC_N)
    v.flow_u, v.flow_cap = (u if (use_flow and both_s.any()) else np.zeros((fn, fn, 2))), MAX_FLOW * fn
    v.disc = depth_discontinuities(v.raster.z, cam.scale / size)
    v.gate_ag = v.gate_fs = np.ones((size, size), dtype=np.float32)
    v.ok = True
    if log:
        log(f"view {name}: yaw {yaw}, iou {fit['iou']:.3f}, flow {flow_used}")
    return v


# --- gates ------------------------------------------------------------------------------------------------------


def depth_discontinuities(z: np.ndarray, px_m: float, jump_px: float = 10.0) -> np.ndarray:
    """Pixels beside a jump in depth of more than jump_px pixel sizes (a limb in front of the body): the picture's colour
    there is a mix of two surfaces."""
    zf = np.where(np.isfinite(z), z, np.nan)
    d = np.zeros(z.shape, dtype=bool)
    h, w = zf.shape
    for dy, dx in ((0, 1), (1, 0)):
        j = np.nan_to_num(np.abs(zf[dy:, dx:] - zf[:h - dy, :w - dx]) > jump_px * px_m, nan=False).astype(bool)
        d[dy:, dx:] |= j
        d[:h - dy, :w - dx] |= j
    return d


def agreement_gate(v: "View", fn: int = 512) -> tuple[np.ndarray, float]:
    """Where the picture and the model's own colour each hold a feature the other lacks, close to one another: the same
    thing drawn in two places (a padlock painted beside the modelled padlock). Both are reduced to fn and band-passed
    (luminance between 4 and 14 px: a few texels to a few dozen); the structure energy over a 16 px window says where a
    feature is. A feature in one with nothing in the other at that place, and a feature of the other within a fifth of
    the frame with nothing in the first at its place, are both not painted. Fine texture that merely differs (fur,
    wood grain) has energy in both, a feature only one of them has has no partner, and tone is not compared. Returns
    (gate at fn, share of the figure removed)."""
    k = fn / 512.0
    a, b = small(v.pic_aligned, fn), small(v.render, fn)
    m = small(v.valid.astype(np.float64), fn) > 0.5
    la, lb = face.luminance(a), face.luminance(b)
    s1, s2, sg = 4.0 * k, 14.0 * k, 16.0 * k
    sa = (face.masked_blur(la, m, s1) - face.masked_blur(la, m, s2)) * m
    sb = (face.masked_blur(lb, m, s1) - face.masked_blur(lb, m, s2)) * m
    ea, eb = face.blur(sa * sa, sg), face.blur(sb * sb, sg)
    hi, lo = AGREE["hi"] ** 2, AGREE["lo"] ** 2
    orphan_a = (ea > hi) & (eb < lo) & m          # the picture has a feature here and the model's colour has none
    orphan_b = (eb > hi) & (ea < lo) & m          # the model's colour has one here and the picture has none
    reach = max(2, int(round(AGREE["reach"] * fn)))
    bad = (orphan_a & face.dilate_mask(orphan_b, reach)) | (orphan_b & face.dilate_mask(orphan_a, reach))
    # a feature is gated as a whole: close up the rings the band picks out, so the inside of a padlock goes with its edge
    bad = face.fill_holes(face.dilate_mask(bad, max(2, int(round(AGREE["grow"] * fn))))) & m
    g = np.where(m, 1.0 - np.clip(face.blur(bad.astype(np.float64), 1.5), 0.0, 1.0), 1.0)
    return g, float(bad.sum() / max(m.sum(), 1))


def prepare_gates(v: "View", gates: dict[str, bool] | None = None) -> None:
    """The per-view gates on top of the angle cut-off: depth-edge erosion (changes the feather), the structure agreement
    gate and the flow-limit gate (images in v.gate_ag and v.gate_fs)."""
    g = GATES if gates is None else gates
    size = v.size
    r = max(2, size // 340)
    valid = v.valid & ~face.dilate_mask(v.disc, DISC_PX) if g.get("disc") else v.valid
    v.feather = np.clip(face.blur(face.erode(valid, r).astype(np.float64), r * 0.8) / 0.9, 0.0, 1.0)
    ones = np.ones((size, size), dtype=np.float32)
    v.gate_ag = v.gate_fs = ones
    v.info["gate_pixels"] = {}
    if g.get("agree"):
        ga, share = agreement_gate(v, min(512, v.size))
        v.gate_ag = _rs(ga, size, size).astype(np.float32)
        v.info["gate_pixels"]["agreement"] = round(share, 4)
    if g.get("flowsat"):
        fn = v.flow_u.shape[0]
        sat = (np.hypot(v.flow_u[..., 0], v.flow_u[..., 1]) >= 0.9 * v.flow_cap).astype(np.float64)
        f = 1.0 - 0.7 * np.clip(face.blur(sat, 3.0) * 2.0, 0.0, 1.0)
        v.gate_fs = _rs(f, size, size).astype(np.float32)
        inside = small(v.valid.astype(np.float64), fn) > 0.5
        v.info["gate_pixels"]["flow_cap"] = round(float(((f < 0.5) & inside).sum() / max(inside.sum(), 1)), 4)


# --- the atlas side ---------------------------------------------------------------------------------------------


class Atlas:
    """Texels of the islands and GUTTER_PX texels around them, each with a surface position and normal (the border
    triangle's plane continued outward)."""

    def __init__(self, mesh: dict[str, np.ndarray], T: int, gutter: int = GUTTER_PX):
        self.T = T
        uvp = face.uv_pixels(mesh["uv"], T)
        ras = face.rasterise(uvp, np.zeros(len(mesh["uv"])), mesh["faces"], T)
        self.cover = ras["tri"] >= 0
        isl = np.flatnonzero(self.cover.ravel())
        # Share of the layout that more than one triangle uses (a mirrored model: both halves on one piece of texture).
        # The triangles' own area in texels against the texels they cover; without sharing the two agree.
        tp = uvp[mesh["faces"]]
        tri_px = 0.5 * np.abs((tp[:, 1, 0] - tp[:, 0, 0]) * (tp[:, 2, 1] - tp[:, 0, 1])
                              - (tp[:, 1, 1] - tp[:, 0, 1]) * (tp[:, 2, 0] - tp[:, 0, 0])).sum()
        self.shared = float(max(0.0, 1.0 - isl.size / max(tri_px, 1.0)))
        pos = face.interpolate(mesh["verts"], mesh["faces"], ras)[self.cover]
        n = face.interpolate(mesh["vnormals"], mesh["faces"], ras)[self.cover]
        nrm = n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
        v, f = mesh["verts"], mesh["faces"]
        area = 0.5 * np.linalg.norm(np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]), axis=1).sum()
        self.pitch_m = math.sqrt(area / max(isl.size, 1))  # surface metres per texel
        self.is_gut = np.zeros(isl.size, dtype=bool)
        self.src = np.arange(isl.size)
        self.idx, self.pos, self.nrm = isl, pos, nrm
        self.cover_ext = self.cover
        if gutter > 0 and isl.size:
            tri = np.where(self.cover, ras["tri"], -1).astype(np.float64)
            Y, X = np.mgrid[0:T, 0:T].astype(np.float64)
            gtri = imaging.dilate(tri, self.cover, gutter)
            gx = imaging.dilate(X, self.cover, gutter)
            gy = imaging.dilate(Y, self.cover, gutter)
            gi = np.flatnonzero(((gtri >= 0) & ~self.cover).ravel())
            t = gtri.ravel()[gi].astype(np.int64)
            p = np.stack([(gi % T) + 0.5, (gi // T) + 0.5], 1)
            tv = uvp[f[t]]
            d1, d2 = tv[:, 1] - tv[:, 0], tv[:, 2] - tv[:, 0]
            det = d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]
            ok = np.abs(det) > 1e-9
            safe = np.where(ok, det, 1)
            rel = p - tv[:, 0]
            b1 = (rel[:, 0] * d2[:, 1] - rel[:, 1] * d2[:, 0]) / safe
            b2 = (d1[:, 0] * rel[:, 1] - d1[:, 1] * rel[:, 0]) / safe
            b0 = 1 - b1 - b2
            gpos = b0[:, None] * v[f[t, 0]] + b1[:, None] * v[f[t, 1]] + b2[:, None] * v[f[t, 2]]
            vn = mesh["vnormals"]
            gn = b0[:, None] * vn[f[t, 0]] + b1[:, None] * vn[f[t, 1]] + b2[:, None] * vn[f[t, 2]]
            gn /= np.maximum(np.linalg.norm(gn, axis=1, keepdims=True), 1e-12)
            lut = np.full(T * T, -1, dtype=np.int64)
            lut[isl] = np.arange(isl.size)
            srcflat = gy.ravel()[gi].astype(np.int64) * T + gx.ravel()[gi].astype(np.int64)
            keep = ok & (lut[srcflat] >= 0)
            self.idx = np.concatenate([isl, gi[keep]])
            self.pos = np.concatenate([pos, gpos[keep]])
            self.nrm = np.concatenate([nrm, gn[keep]])
            self.is_gut = np.concatenate([self.is_gut, np.ones(int(keep.sum()), dtype=bool)])
            self.src = np.concatenate([self.src, lut[srcflat][keep]])
            ext = self.cover.copy().reshape(-1)
            ext[gi[keep]] = True
            self.cover_ext = ext.reshape(T, T)


def cos_weight(cosv: np.ndarray) -> np.ndarray:
    """cos^4 of the angle between surface and view, zero past 75 degrees (feathered over the next 0.12 of cosine)."""
    return np.clip(cosv, 0.0, 1.0) ** 4 * face.smoothstep(COS_CUT, COS_CUT + 0.12, cosv)


def project_view(atlas: Atlas, v: View, size: int | None = None, pitch_m: float | None = None) -> dict[str, np.ndarray]:
    """The texels this view sees squarely: index into atlas.idx, screen position, weight. A left, right or back view
    stays off the face region (atlas.zone, set by compose). `size` is ignored: the view's camera has its own."""
    cam = v.cam
    size = cam.size
    pitch_m = atlas.pitch_m if pitch_m is None else pitch_m
    sp, sd = cam.project(atlas.pos)
    xi = np.clip(sp[:, 0].astype(int), 0, size - 1)
    yi = np.clip(sp[:, 1].astype(int), 0, size - 1)
    cosv = -(atlas.nrm @ cam.basis[2])
    tol = 2.5 * (cam.scale / size) / np.maximum(np.abs(cosv), 0.15)
    inb = (sp[:, 0] >= 0) & (sp[:, 0] < size) & (sp[:, 1] >= 0) & (sp[:, 1] < size)
    visible = inb & (sd <= v.raster.z[yi, xi] + tol) & (v.raster.fcos[yi, xi] > 0.05)
    f0 = face.sample(v.feather0[..., None], sp[:, 0], sp[:, 1])[:, 0]
    f1 = face.sample(v.feather[..., None], sp[:, 0], sp[:, 1])[:, 0]
    ga = face.sample(v.gate_ag[..., None].astype(np.float64), sp[:, 0], sp[:, 1])[:, 0]
    gf = face.sample(v.gate_fs[..., None].astype(np.float64), sp[:, 0], sp[:, 1])[:, 0]
    foot = v.pic_px_m / np.maximum(cosv, 0.05) / pitch_m
    st = 1.0 - face.smoothstep(STRETCH[0], STRETCH[1], foot) if GATES.get("stretch") else np.ones_like(foot)
    fd = np.clip(f1 / np.maximum(f0, 1e-6), 0.0, 1.0)
    base = v.weight * cos_weight(cosv) * f0 * visible
    zi = getattr(v, "zone_img", None)  # the head view: the head above the neck only
    if zi is not None:
        base = base * face.sample(zi[..., None], sp[:, 0], sp[:, 1])[:, 0]
    zt = getattr(atlas, "zone", None)
    fz = (1.0 - zt["face"]) if (zt is not None and v.name in OFF_FACE_VIEWS) else np.ones_like(base)
    w = base * fd * st * ga * gf * fz
    wsum = max(float(base.sum()), 1e-12)
    v.stats = {"removed_by_depth_edge": round(float((base * (1 - fd)).sum() / wsum), 4),
               "removed_by_stretch": round(float((base * (1 - st)).sum() / wsum), 4),
               "removed_by_agreement": round(float((base * (1 - ga)).sum() / wsum), 4),
               "removed_by_flow_cap": round(float((base * (1 - gf)).sum() / wsum), 4),
               "removed_by_face_region": round(float((base * (1 - fz)).sum() / wsum), 4),
               "removed_any": round(float(1.0 - w.sum() / wsum), 4)}
    keep = np.flatnonzero(w > 1e-5)
    return {"sel": keep, "sp": sp[keep], "w": w[keep]}


def detail_only(v: View, sigma_px: float) -> np.ndarray:
    """The view's picture with its broad colour and tone (below sigma_px) taken from the TRELLIS drawing: the drawing's
    low frequencies plus the picture's high frequencies, masked so the backdrop does not bleed in."""
    lo_r = face.masked_blur(v.render, v.valid, sigma_px)
    lo_p = face.masked_blur(v.pic_aligned, v.valid, sigma_px)
    return np.clip(lo_r + v.pic_aligned - lo_p, 0.0, 1.0)


def blend(atlas: Atlas, projs: list[dict[str, np.ndarray]], images: list[np.ndarray],
          n_primary: int = 1, heads: list[bool] | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Weighted blend of the views (the dominant one counts as weight^BLEND_POW). The first n_primary views are in order
    of priority: each one, and every view after them, fades out where the ones before it see well. Head views (heads[i],
    first among them) hand over to one another the same way, and every other view stays off wherever a head view paints
    with HEAD_HANDOVER or more (no whole-body fit paints the head once a head view has; they meet on the neck, where the
    head views fade). Returns (paint (N,3), alpha (N), dominant view per texel, -1 for none)."""
    N = atlas.idx.size
    heads = heads or [False] * len(projs)
    num, den, wmax, dom = np.zeros((N, 3)), np.zeros(N), np.zeros(N), np.full(N, -1)
    suppress, under_head = np.ones(N), np.ones(N)
    head_chain = np.ones(N)
    for i, (p, img) in enumerate(zip(projs, images)):
        sel, w = p["sel"], p["w"]
        if heads[i]:
            w = w * head_chain[sel]
            seen = np.zeros(N)
            seen[sel] = w
            head_chain = head_chain * (1.0 - np.clip(seen / FRONT_PRIORITY, 0.0, 1.0))
            under_head = under_head * (1.0 - np.clip(seen / HEAD_HANDOVER, 0.0, 1.0))
        else:
            w = w * suppress[sel] * under_head[sel]
        if i < n_primary and not heads[i]:
            seen = np.zeros(N)
            seen[sel] = w
            suppress = suppress * (1.0 - np.clip(seen / FRONT_PRIORITY, 0.0, 1.0))
        col = catmull(img, p["sp"][:, 0].reshape(-1, 1), p["sp"][:, 1].reshape(-1, 1))[:, 0]
        ww = w ** BLEND_POW
        num[sel] += col * ww[:, None]
        den[sel] += ww
        better = w > wmax[sel]
        dom[sel[better]] = i
        wmax[sel] = np.maximum(wmax[sel], w)
    return num / np.maximum(den, 1e-12)[:, None], face.smoothstep(ALPHA_RAMP[0], ALPHA_RAMP[1], wmax), dom


def surface_correction(atlas: Atlas, delta: np.ndarray, alpha: np.ndarray, cell: float = 0.012, passes: int = 3,
                       r: int = 2) -> tuple[np.ndarray, np.ndarray]:
    """A smooth, surface-following version of delta (the colour offset of the painted texels), carried to every texel and
    fading out with distance from painted surface. Returns (correction (N,3), reach (N) 0..1)."""
    pos = atlas.pos
    lo = pos.min(0) - 3 * cell
    f = (pos - lo) / cell
    idx = np.floor(f).astype(int)
    dims = tuple(int(d) for d in idx.max(0) + 4)
    size = int(np.prod(dims))
    flat = np.ravel_multi_index(tuple(idx.T), dims)
    sd = np.stack([np.bincount(flat, weights=delta[:, c] * alpha, minlength=size).reshape(dims) for c in range(3)], -1)
    sa = np.bincount(flat, weights=alpha, minlength=size).reshape(dims)
    sn = np.bincount(flat, minlength=size).astype(np.float64).reshape(dims)
    for _ in range(passes):
        sd = np.stack([face._box3(sd[..., c], r) for c in range(3)], -1)
        sa, sn = face._box3(sa, r), face._box3(sn, r)
    g = sd / np.maximum(sa, 1e-9)[..., None]
    frac = sa / np.maximum(sn, 1e-9)
    fc = f - 0.5
    corr = np.stack([face._trilinear(g[..., c], fc) for c in range(3)], -1)
    return corr, np.clip(face.smoothstep(0.03, 0.4, face._trilinear(frac, fc)), 0, 1)


def skin_tone(atlas: Atlas, kept: np.ndarray, paint: np.ndarray, alpha: np.ndarray, zt: dict[str, np.ndarray]) -> np.ndarray:
    """A plain skin colour for every texel, from the painted skin of the face beside it: the painted texels of the face
    region whose colour is within SKIN_TOL of the painted face's middle colour (eyes, brows, lips and hair are not),
    spread along the surface (surface_correction), and their middle colour where none is near. When too little of the
    face was painted, the model's own face colour gives it the same way."""
    fz = zt["face"]
    base_ok = (fz > 0.3) & ~atlas.is_gut & ~zt["keep"]
    painted = base_ok & (alpha > 0.5)
    col, cand = (paint, painted) if painted.sum() >= 50 else (kept, base_ok)
    if cand.sum() < 10:
        return kept
    lab = face.to_lab(col[cand][None])[0]
    mid = np.median(lab, axis=0)
    near = np.linalg.norm((lab - mid) * np.array([0.5, 1.0, 1.0]), axis=1) < SKIN_TOL
    ci = np.flatnonzero(cand)[near] if near.sum() >= 10 else np.flatnonzero(cand)
    ref = np.median(col[ci], axis=0)
    wsel = np.zeros(alpha.shape)
    wsel[ci] = 1.0
    field, reach = surface_correction(atlas, np.where(wsel[:, None] > 0, col, 0.0), wsel)
    return np.clip(field * reach[:, None] + ref[None] * (1 - reach[:, None]), 0, 1)


def finish(atlas: Atlas, base: np.ndarray, paint: np.ndarray, alpha: np.ndarray, zt: dict[str, np.ndarray] | None = None,
           out_info: dict[str, Any] | None = None) -> np.ndarray:
    """The new atlas: painted texels take the painted colour; the others keep base with its low frequencies shifted to agree
    with the painted colour near them; the islands are then grown into the gutter. With the face region (zt, from
    zone_texels), what no picture painted inside the face takes a plain skin tone from the painted skin beside it
    (skin_tone) instead of the model's own colour, so none of the model's own features is left beside a painted one
    (docs/AFTER-TEST-CHANGES.md D2 item 7); the model's hair and glasses there (zt["keep"]) keep their colour.
    out_info gets "fill": each texel's share of skin."""
    T = atlas.T
    if atlas.is_gut.any():  # a border texel the views do not reach takes its island neighbour's painted colour
        need = atlas.is_gut & (alpha < 0.5) & (alpha[atlas.src] >= 0.5)
        paint, alpha = paint.copy(), alpha.copy()
        paint[need] = paint[atlas.src[need]]
        alpha[need] = alpha[atlas.src[need]]
    kept = base.reshape(-1, 3)[atlas.idx]
    out = kept
    sel = alpha > 0.5
    if sel.sum() > 100:
        corr, reach = surface_correction(atlas, np.where(sel[:, None], paint - kept, 0.0), np.where(sel, alpha, 0.0))
        out = np.clip(kept + corr * reach[:, None], 0, 1)
    if zt is not None:
        fw = zt.get("fill", zt["face"]) * ~zt["keep"]
        if (fw * (1 - alpha) > 0.5).any():
            out = out * (1 - fw[:, None]) + skin_tone(atlas, kept, paint, alpha, zt) * fw[:, None]
        else:
            fw = np.zeros_like(alpha)
        if out_info is not None:
            out_info["fill"] = fw * (1 - alpha)
    out = out * (1 - alpha[:, None]) + paint * alpha[:, None]
    res = base.copy().reshape(-1, 3)
    res[atlas.idx] = np.clip(out, 0, 1)
    return imaging.dilate(res.reshape(T, T, 3), atlas.cover_ext, imaging.dilation_px(T))


# --- the face region ----------------------------------------------------------------------------------------------


def face_zone(mesh: dict[str, np.ndarray], tex: np.ndarray, head_yaw: float = 0.0, size: int = FACE_ZONE_N) -> dict[str, Any]:
    """The face region in a picture of the head seen from where the face looks (head_yaw, the face pass's HEAD_YAW): the
    face pass's own face finding (face.find_face) on the model's colour, feathered outward by FACE_ZONE_FEATHER. Kept
    as a picture with its camera and depth, so it can be carried to any texture layout of the model (zone_texels).
    Returns {found, reason, head, cam, view, info, weight (S,S), z}; nothing else when the face is not found."""
    head = face.find_head(mesh["verts"])
    if head is None:
        return {"found": False, "reason": "no_head"}
    cam = face.head_camera(head, float(head_yaw), size)
    view = face.render_view(mesh, tex, cam)
    info = face.find_face(view, mesh, cam, head)
    info = nose_by_landmarks(view, mesh, cam, head, info)
    out: dict[str, Any] = {"found": bool(info.get("found")), "reason": info.get("reason"), "head": head, "cam": cam,
                           "view": view, "info": info, "z": view["raster"]["z"]}
    if not info.get("found"):
        return out
    r = max(2, int(round(FACE_ZONE_FEATHER * size)))
    out["weight"] = np.clip(face.blur(info["region"].astype(np.float64), r) * 2.0, 0.0, 1.0)
    out["keep"] = never_skin(view, info, cam)
    pos = face.interpolate(mesh["verts"], mesh["faces"], view["raster"])
    nx, ny = info["nose"]
    nose_y = float(pos[ny, nx, 1])
    L = max(head.top - nose_y, 1e-3)
    out["neck"] = (nose_y - HEAD_NECK[0] * L, nose_y - HEAD_NECK[1] * L)  # heights the head views fade in between
    # where a skin fill may go: the eyes, nose and mouth (face.features_box), feathered. The model's own features all
    # lie there; on the rest of the face (cheeks, jaw, temples) a fill showed as a flat band round the face on the
    # stored W1-clothed and P1-photo runs, and the old colour with its tone shifted (finish) reads better there.
    box = (face.features_box(info) & info["region"]).astype(np.float64)
    out["fill"] = np.clip(face.blur(box, max(2.0, FILL_FEATHER * float(info["width_px"]))) * 2.0, 0.0, 1.0) * info["region"]
    return out


def nose_by_landmarks(view: dict[str, Any], mesh: dict[str, np.ndarray], cam: face.Camera, head: face.Head,
                      info: dict[str, Any]) -> dict[str, Any]:
    """The face found again from MediaPipe's eyes and mouth when the nose tip the shape gave is not between them (on
    the stored W2-clothed run the shape put it 60 mm to one side, beside the loose curls; on P1-photo on the chin):
    the nose tip taken as the nearest surface point around 60 percent of the way from the eyes' middle to the mouth.
    Only while a MediaPipe finder is set (facemarks); the first finding otherwise, or when the new one does not hold
    up either. Records "nose_from" in the face info."""
    from . import facemarks

    m = facemarks.points(view["image"])
    if m is None or (info.get("found") and facemarks.plausible(m, info)):
        return info
    eyes = np.array(m["eyes"], dtype=np.float64)
    mid, mouth = eyes.mean(axis=0), np.array(m["mouth"], dtype=np.float64)
    guess = mid + 0.6 * (mouth - mid)
    gap = float(np.linalg.norm(eyes[1] - eyes[0]))
    S = cam.size
    r = max(2, int(0.15 * gap))
    x0, x1 = int(max(0, guess[0] - r)), int(min(S, guess[0] + r + 1))
    y0, y1 = int(max(0, guess[1] - r)), int(min(S, guess[1] + r + 1))
    z = np.where(view["mask"], view["raster"]["z"], np.inf)[y0:y1, x0:x1]
    if z.size == 0 or not np.isfinite(z).any():
        return info
    iy, ix = np.unravel_index(int(np.argmin(z)), z.shape)
    again = face.find_face(view, mesh, cam, head, nose=(int(x0 + ix), int(y0 + iy)))
    if not again.get("found") or not facemarks.plausible(m, again):
        return info
    again["nose_from"] = "mediapipe"
    again["nose_by_shape"] = info.get("nose")
    return again


def never_skin(view: dict[str, Any], info: dict[str, Any], cam: face.Camera) -> np.ndarray:
    """Where the face region must keep the model's own colour rather than take a skin fill: the model's hair (the face
    pass's face.hair_and_skin: hair-coloured surface joined to the hair above the face, so a fringe, sideburns and a
    beard that joins them) and glasses (a solid piece in front of the face: the depth steps of GLASSES_JUMP_M or more,
    and what a ring of them encloses). A moustache or beard not joined to the hair, and a lens joined to the face with
    no step around it, are not found; the steps also keep the model's colour at other overhangs inside the face."""
    seg = face.hair_and_skin(view["image"], view["image"], info, view["mask"])
    px_m = cam.scale / cam.size
    steps = depth_discontinuities(view["raster"]["z"], px_m, jump_px=GLASSES_JUMP_M / px_m)
    ring = face.dilate_mask(steps, 2)
    # the steps themselves too: where a rim stands in front of the face the pictures are kept off by the depth-edge gate,
    # and a fill there drew the frames in skin (the stored W2-clothed run, whose lenses are joined to the face)
    glasses = (face.fill_holes(ring) | face.dilate_mask(steps, 4)) & face.dilate_mask(info["region"], 4)
    return face.dilate_mask(seg["old_hair"], 2) | glasses


def head_points(img: np.ndarray) -> np.ndarray | None:
    """The eyes (left, right in the picture) and the mouth of the one face in img, by MediaPipe when a finder is set
    (facemarks.points; at any turn, no check that it is the model's face), as a (3, 2) array; None when not found."""
    from . import facemarks

    m = facemarks.points(img)
    if m is None or not m.get("eyes") or m.get("mouth") is None:
        return None
    eyes = sorted(m["eyes"])
    return np.array([eyes[0], eyes[1], m["mouth"]], dtype=np.float64)


def fit_points(src: np.ndarray, dst: np.ndarray, size: int) -> dict[str, Any]:
    """One scale and a shift (no squash, no turn) that move the points src onto dst, least squares, as an alignment
    face.apply_alignment takes (about the frame's middle), with "residual_px", the largest distance left."""
    c = np.array([size / 2.0, size / 2.0])
    a, b = src - c, dst - c
    am, bm = a.mean(axis=0), b.mean(axis=0)
    a0, b0 = a - am, b - bm
    k = float((a0 * b0).sum() / max((a0 * a0).sum(), 1e-9))
    t = bm - k * am
    res = float(np.linalg.norm(a * k + t - b, axis=1).max())
    return {"sx": round(k, 4), "sy": round(k, 4), "tx": round(float(t[0]), 2), "ty": round(float(t[1]), 2),
            "centre": (float(c[0]), float(c[1])), "residual_px": round(res, 2), "how": "eyes and mouth, one scale"}


def moved_points(pts: np.ndarray, a: dict[str, Any]) -> np.ndarray:
    """Where face.apply_alignment(img, a) puts the points pts of img."""
    c = np.array(a["centre"], dtype=np.float64)
    return c + (pts - c) * np.array([a["sx"], a["sy"]]) + np.array([a["tx"], a["ty"]])


def make_head_view(mesh: dict[str, np.ndarray], name: str, cam: face.Camera, render: dict[str, Any], crop: dict[str, Any],
                   a: dict[str, Any], zone: dict[str, Any], body_px_m: float, pic_n0: int) -> View:
    """A head view: the picture's head (crop, framed by face.painted_head for cam) moved by the alignment a, painting the
    head above the neck (zone["neck"], HEAD_NECK), with the depth-edge and stretch gates as the body's views have them."""
    S = cam.size
    v = View(name, 1.0)
    v.cam, v.raster = cam, Raster(mesh, cam)
    v.pic_aligned = v.pic_affine = np.clip(face.apply_alignment(crop["image"], a), 0.0, 1.0)
    v.alpha_img = face.apply_alignment(crop["alpha"], a)
    v.render = render["image"]
    v.valid = v.raster.mask & (v.alpha_img > 0.5)
    r = max(2, S // 340)
    v.feather = v.feather0 = np.clip(face.blur(face.erode(v.valid, r).astype(np.float64), r * 0.8) / 0.9, 0.0, 1.0)
    pos = face.interpolate(mesh["verts"], mesh["faces"], render["raster"])
    y0, y1 = zone["neck"]
    v.zone_img = face.smoothstep(y0, y1, pos[..., 1]) * render["mask"]
    v.size = S
    k = 0.5 * (float(a["sx"]) + float(a["sy"]))
    v.pic_px_m = 1.0 / float(crop["px_per_m"]) / max(k, 1e-6) * pic_n0 / float(PIC_N)
    v.flow_u, v.flow_cap = np.zeros((8, 8, 2)), 1.0
    # depth steps as the body's views see them: the same jump in metres, the same reach around them
    px_m = cam.scale / S
    disc = depth_discontinuities(render["raster"]["z"], body_px_m, 10.0)
    v.disc = face.dilate_mask(disc, max(0, int(round(DISC_PX * body_px_m / px_m)) - DISC_PX))
    # the structure gate compares the picture with the model's own face, which is what the head fit already did
    prepare_gates(v, {"disc": True, "stretch": True, "agree": False, "flowsat": False})
    v.ok = True
    return v


def limit_squash(crop: np.ndarray, render: np.ndarray, a: dict[str, Any], size: int) -> dict[str, Any]:
    """The face pass's fit with its two scales held together: when they differ by more than SQUASH_MAX (the stored
    W1-clothed run: 0.87 by 0.81, the face came out narrow with a second jaw beside it), the eyes and mouth are fitted
    again with one scale (fit_points). The fit as it was when they do not differ that much, or when the points are not
    found; the fit's record says which ("squash")."""
    sx, sy = float(a["sx"]), float(a["sy"])
    if abs(sx / max(sy, 1e-6) - 1.0) <= SQUASH_MAX:
        return a
    pc, pr = head_points(crop), head_points(render)
    if pc is None or pr is None:
        return {**a, "squash": "kept: the eyes and mouth were not found to fit with one scale"}
    u = fit_points(pc, pr, size)
    u["score"] = a.get("score", 0.0)
    u["squash"] = f"{sx:.2f} by {sy:.2f} fitted again with one scale"
    return u


def fit_head(mesh: dict[str, np.ndarray], zone: dict[str, Any], pictures: list[tuple[str, Any]], body_px_m: float,
             pic_n: int | None = None, log: Callable[[str], None] | None = None,
             prep: Callable[..., tuple[np.ndarray, np.ndarray]] = prepare_picture) -> tuple[View | None, dict[str, Any]]:
    """The first picture fitted to the head on its own, by its eyes and mouth (docs/AFTER-TEST-CHANGES.md D2 item 6): the
    face pass's own framing (face.painted_head: the picture's head cut out as the model's head is framed from where its
    face looks) and fit (face_detail.fit_picture: eyes and mouth found by MediaPipe when facemarks has a finder set, else
    the darkest spots). Tried on each of `pictures` ((name, picture) in order, the original first) whatever its
    whole-body fit said; the first whose eyes and mouth land on the model's is the head view: its camera is the head
    picture's, it paints the head above the neck (HEAD_NECK), and it comes first in priority. Returns (the view or
    None, its record for the metrics)."""
    from .stages import face_detail

    rec: dict[str, Any] = {"name": HEAD_VIEW}
    if not zone.get("found"):
        rec["refused"] = f"the model's face was not found ({zone.get('reason')})"
        return None, rec
    head, cam, view, info = zone["head"], zone["cam"], zone["view"], zone["info"]
    S = cam.size
    inner = face.inner_face(info)
    tried: dict[str, str] = {}
    for name, src in pictures:
        if src is None:
            continue
        pic, alpha = prep(src, pic_n)
        crop = face.painted_head(np.concatenate([pic, alpha[..., None]], axis=-1), head, cam, size=S)
        if crop is None:
            tried[name] = "no figure in the picture"
            continue
        a = limit_squash(crop["image"], view["image"], face_detail.fit_picture(crop["image"], view["image"], info, inner), S)
        err = head_fit_error(crop["image"], view["image"], info, a)
        if err is None or err > face_detail.LANDMARK_RESIDUAL_MAX or a.get("score", 0) < face_detail.ALIGN_MIN:
            tried[name] = ("its eyes and mouth could not be found on both" if err is None else
                           f"its eyes land {err:.2f} of the face's width from the model's") + f" (fit: {a.get('how')}, score {a.get('score', 0):.2f})"
            continue
        v = make_head_view(mesh, HEAD_VIEW, cam, view, crop, a, zone, body_px_m, pic.shape[0])
        rec.update(picture=name, yaw=round(float(cam.yaw_deg), 1), how=a.get("how"), score=round(float(a.get("score", 0)), 3),
                   eye_error=round(float(err), 3), fit={kk: a.get(kk) for kk in ("sx", "sy", "tx", "ty")},
                   head_px=crop.get("head_px"), nose_from=info.get("nose_from", "shape"), squash=a.get("squash"))
        if tried:
            rec["passed_over"] = tried
        v.info = rec
        if log:
            log(f"view head: from {name}, {a.get('how')}, score {a.get('score', 0):.2f}, eye error {err:.3f} of the face's width")
        return v, rec
    rec["refused"] = "; ".join(f"{k}: {why}" for k, why in tried.items()) or "no picture to fit"
    if log:
        log(f"view head: refused, {rec['refused']}")
    return None, rec


def fit_side_head(mesh: dict[str, np.ndarray], tex: np.ndarray, painted: np.ndarray, zone: dict[str, Any], name: str,
                  src: Any, yaw: float, body_px_m: float, pic_n: int | None = None,
                  log: Callable[[str], None] | None = None,
                  prep: Callable[..., tuple[np.ndarray, np.ndarray]] = prepare_picture) -> tuple[View | None, dict[str, Any]]:
    """A three-quarter view's head view (the chain of head views, docs/AFTER-TEST-CHANGES.md D2 item 10 for the head):
    the picture's head framed from `yaw` (face.painted_head), its eyes and mouth found by MediaPipe and fitted with one
    scale onto the model's own (the model's colour seen from there), then checked against where the front head view
    painted them (`painted`: the model's colour with the front head view painted on): when its own fit puts them more
    than SIDE_AGREE_MAX of the face's width from the front's, it is refused (the two would paint the face twice); else it
    is lined up to the front's points where they are found. Returns (the view or None, its record)."""
    rec: dict[str, Any] = {"name": f"{HEAD_VIEW}_{name}", "picture": name, "yaw": round(float(yaw), 1)}

    def refuse(why: str) -> tuple[None, dict[str, Any]]:
        rec["refused"] = why
        if log:
            log(f"view {rec['name']}: refused, {why}")
        return None, rec

    head, w = zone["head"], float(zone["info"]["width_px"])
    S = zone["cam"].size
    cam = face.head_camera(head, float(yaw), S)
    pic, alpha = prep(src, pic_n)
    crop = face.painted_head(np.concatenate([pic, alpha[..., None]], axis=-1), head, cam, size=S)
    if crop is None:
        return refuse("no figure in the picture")
    render = face.render_view(mesh, tex, cam)
    pc = head_points(crop["image"])
    if pc is None:
        return refuse("no face found on the picture's head")
    pm = head_points(render["image"])
    if pm is None:
        return refuse("no face found on the model from this side")
    own = fit_points(pc, pm, S)
    pf = head_points(face.render_view(mesh, painted, cam)["image"])
    if pf is not None:
        apart = float(np.linalg.norm(moved_points(pc, own) - pf, axis=1).max()) / w
        rec["apart_from_front"] = round(apart, 3)
        if apart > SIDE_AGREE_MAX:
            return refuse(f"its eyes and mouth land {apart:.2f} of the face's width from where the front painted them")
        a, rec["lined_up_to"] = fit_points(pc, pf, S), "front"
    else:
        a, rec["lined_up_to"] = own, "model (the front's face was not found from this side)"
    rec["residual"] = round(float(a["residual_px"]) / w, 3)
    if rec["residual"] > SIDE_AGREE_MAX:
        return refuse(f"its eyes and mouth do not fit with one scale (left {rec['residual']:.2f} of the face's width)")
    v = make_head_view(mesh, rec["name"], cam, render, crop, a, zone, body_px_m, pic.shape[0])
    rec.update(fit={kk: a.get(kk) for kk in ("sx", "tx", "ty")}, head_px=crop.get("head_px"))
    v.info = rec
    if log:
        log(f"view {rec['name']}: yaw {yaw:.1f}, lined up to {rec['lined_up_to']}, residual {rec['residual']:.3f}")
    return v, rec


def side_head_yaw(name: str, body: View | None, head_yaw: float) -> tuple[float | None, str]:
    """The yaw a three-quarter view's head is framed from: SIDE_HEAD_YAW from the head's own turn, to the side its
    whole-body fit puts it on (also when that fit was refused for its outline match, which says nothing about the head),
    else to its usual side. The whole-body turn itself is not used: on the stored W1-clothed run it sat 12 degrees from
    the turned face and the side was skipped. None when its turn was refused as contradicting the front (item 8), or
    when the whole-body fit put it more than SIDE_HEAD_TURN[1] from the face (looking at the back of the head): that
    side keeps to today's painting."""
    if body is not None and "contradicts" in str(body.info.get("refused")):
        return None, "its turn contradicts the front"
    side, why = TURN_SIDE[name], "its usual side"
    if body is not None and body.info.get("yaw") is not None:
        rel = _signed(float(body.info["yaw"]) - head_yaw)
        if abs(rel) > SIDE_HEAD_TURN[1]:
            return None, f"its whole-body fit is {abs(rel):.0f} degrees from the face"
        if abs(rel) >= 5.0:
            side, why = (1 if rel > 0 else -1), "the side of its whole-body fit"
    return float(head_yaw + side * SIDE_HEAD_YAW), why


def one_head_per_side(yaws: dict[str, float], head_yaw: float) -> dict[str, str]:
    """When both three-quarter views would frame the head from the same side of the face (the stored M1-clothed run:
    -42.5 and -20, both drawn from one side), the one nearer the front is dropped: two head views of one side paint
    that side twice. Returns {view: reason} for the dropped."""
    if len(yaws) < 2:
        return {}
    (a, ya), (b, yb) = list(yaws.items())[:2]
    ra, rb = _signed(ya - head_yaw), _signed(yb - head_yaw)
    if np.sign(ra) != np.sign(rb):
        return {}
    drop = a if abs(ra) < abs(rb) else b
    return {drop: "both three-quarter views frame the head from the same side; this one is nearer the front"}


def head_fit_error(crop: np.ndarray, render: np.ndarray, info: dict[str, Any], a: dict[str, Any]) -> float | None:
    """How far the fitted picture's eyes sit from the model's, as a share of the face's width (the larger of the two
    eyes' distances in x or y), by MediaPipe on both when a finder is set and finds both, else the fit's own residual
    when it is the eye-and-mouth fit; None when neither is known. The face pass's fit keeps its whole-face fit when that
    already lands the eyes within a margin of the eye-and-mouth fit, so its "how" alone does not say whether the eyes
    are on the model's."""
    from . import facemarks

    w = float(info["width_px"])
    if facemarks.active():
        lp = facemarks.find(face.apply_alignment(crop, a), info)
        lr = facemarks.find(render, info)
        if lp is not None and lr is not None:
            return float(np.abs(np.array(lp["eyes"]) - np.array(lr["eyes"])).max()) / w
    if a.get("how") == "eyes and mouth" and a.get("residual_px") is not None:
        return float(a["residual_px"]) / w
    return None


def front_painted(atlas: Atlas, hv: View, tex: np.ndarray) -> np.ndarray:
    """The model's colour with the front head view painted on it alone, to line the three-quarter head views up to."""
    p = project_view(atlas, hv)
    paint_, alpha_, _ = blend(atlas, [p], [hv.pic_aligned], 1)
    out = tex.copy().reshape(-1, 3)
    out[atlas.idx] = out[atlas.idx] * (1 - alpha_[:, None]) + paint_ * alpha_[:, None]
    return out.reshape(tex.shape)


def zone_texels(atlas: Atlas, zone: dict[str, Any] | None) -> dict[str, np.ndarray] | None:
    """The face region on this layout: {face (N,) 0..1, keep (N,) bool} for atlas.idx; face is the region's weight
    where the head picture sees the texel (depth tested, facing the camera), keep the texels whose colour is never
    replaced by a skin fill (zone["keep"], when there is one). None without a face."""
    if not zone or not zone.get("found") or zone.get("weight") is None:
        return None
    cam = zone["cam"]
    S = cam.size
    sp, sd = cam.project(atlas.pos)
    xi = np.clip(sp[:, 0].astype(int), 0, S - 1)
    yi = np.clip(sp[:, 1].astype(int), 0, S - 1)
    cosv = -(atlas.nrm @ cam.basis[2])
    tol = 2.5 * (cam.scale / S) / np.maximum(np.abs(cosv), 0.15)
    inb = (sp[:, 0] >= 0) & (sp[:, 0] < S) & (sp[:, 1] >= 0) & (sp[:, 1] < S)
    z = zone["z"][yi, xi]
    vis = inb & np.isfinite(z) & (sd <= np.where(np.isfinite(z), z, -np.inf) + tol) & (cosv > 0.0)
    w = face.sample(zone["weight"][..., None], sp[:, 0], sp[:, 1])[:, 0] * vis
    keep = zone["keep"][yi, xi] & vis if zone.get("keep") is not None else np.zeros(w.shape, dtype=bool)
    out = {"face": np.clip(w, 0.0, 1.0), "keep": keep}
    if zone.get("fill") is not None:
        out["fill"] = np.clip(face.sample(zone["fill"][..., None], sp[:, 0], sp[:, 1])[:, 0] * vis, 0.0, 1.0)
    return out


def compose(atlas: Atlas, views: list[View], images: list[np.ndarray], n_primary: int, tex: np.ndarray,
            zone: dict[str, Any] | None = None) -> dict[str, Any]:
    """Project the fitted views onto this layout, blend them and finish the atlas, with the face region when there is
    one. Returns {projs, paint, alpha, dom, base_color, zone (zone_texels), fill}."""
    atlas.zone = zone_texels(atlas, zone)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        projs = list(pool.map(lambda v: project_view(atlas, v), views))
    paint_, alpha_, dom = blend(atlas, projs, images, n_primary, [v.name.startswith(HEAD_VIEW) for v in views])
    extra: dict[str, Any] = {}
    new = finish(atlas, tex, paint_, alpha_, atlas.zone, extra)
    return {"projs": projs, "paint": paint_, "alpha": alpha_, "dom": dom, "base_color": new, "zone": atlas.zone,
            "fill": extra.get("fill")}


# --- measures ---------------------------------------------------------------------------------------------------


def _lap_var(img: np.ndarray, mask: np.ndarray) -> float:
    lum = face.luminance(img)
    lap = 4 * lum - np.roll(lum, 1, 0) - np.roll(lum, -1, 0) - np.roll(lum, 1, 1) - np.roll(lum, -1, 1)
    inner = face.erode(mask, 4)
    return float(lap[inner].var()) if inner.sum() > 50 else 0.0


def zone_record(zone: dict[str, Any] | None) -> dict[str, Any] | None:
    """What of the face region is kept to paint again on another layout (repaint): the head picture's camera and maps."""
    if not zone or not zone.get("found"):
        return None
    return {k: zone.get(k) for k in ("found", "cam", "weight", "z", "keep", "fill")}


def zone_metrics(zone: dict[str, Any] | None, c: dict[str, Any]) -> dict[str, Any]:
    """The face region's numbers for the stage metrics: found or why not, texels in it, and what the side and back views
    lost to it."""
    if not zone or not zone.get("found"):
        return {"found": False, "reason": (zone or {}).get("reason")}
    zt = c.get("zone")
    m: dict[str, Any] = {"found": True, "head_yaw_deg": round(float(zone["cam"].yaw_deg), 1)}
    if zt is not None:
        face_ = zt["face"] > 0.5
        m["texels"] = int(face_.sum())
        m["kept_hair_or_glasses"] = round(float((zt["keep"] & face_).sum() / max(face_.sum(), 1)), 4)
        if c.get("fill") is not None:
            m["skin_filled"] = round(float(((c["fill"] > 0.5) & face_).sum() / max(face_.sum(), 1)), 4)
    return m


def front_measures(v: View, tex: np.ndarray) -> dict[str, float]:
    """The picture's view of the model against the picture (fitted, not warped): low-pass colour difference in Lab, and
    the Laplacian-variance sharpness of the drawing over the picture's (1 is as sharp as the picture)."""
    rend = render_colour(v.raster, tex)
    mask = face.erode(small((v.valid & (v.alpha_img > 0.5)).astype(np.float64), 1024) > 0.5, 6)
    r1, p1 = small(rend, 1024), small(v.pic_affine, 1024)
    if mask.sum() < 100:
        return {"lab_diff": float("nan"), "sharp_ratio": float("nan")}
    s = 0.008 * 1024
    la = face.to_lab(np.clip(face.masked_blur(r1, mask, s), 0, 1))
    lb = face.to_lab(np.clip(face.masked_blur(p1, mask, s), 0, 1))
    return {"lab_diff": round(float(np.linalg.norm(la - lb, axis=-1)[mask].mean()), 3),
            "sharp_ratio": round(_lap_var(r1, mask) / max(_lap_var(p1, mask), 1e-12), 4)}


# --- the pass ---------------------------------------------------------------------------------------------------


def _signed(a: float) -> float:
    return float((a + 180.0) % 360.0 - 180.0)


def front_turn(fitted: dict[str, View], head_yaw: float = 0.0) -> tuple[float, str]:
    """The camera yaw the model's front is seen from, and what says so: the first frontal picture that fitted (the
    original, the drawn front view, or the built-from picture when it is a front view), else the head's turn."""
    for name in ("source", "front", "picture"):
        v = fitted.get(name)
        if v is not None and v.ok and (name != "picture" or v.info.get("front_view")):
            return float(v.info["yaw"]), name
    return float(head_yaw), "head"


def refuse_contradicting_turns(fitted: dict[str, View], front_yaw: float, picture_view: View | None = None) -> dict[str, str]:
    """Refuse the drawn views whose fitted turn contradicts the front (docs/AFTER-TEST-CHANGES.md D2 item 8): a back view
    fitted less than BACK_MIN_TURN from the front, a full side view less than SIDE_MIN_TURN from it, and of two views
    that belong on opposite sides (left and right; the two three-quarter views) but were fitted on the same side, the
    one on the side its partner belongs on. The three-quarter views take their sides from the picture the model was
    built from when that is a three-quarter view itself, 10 to 80 degrees from the front (the model was built from it, so
    its side is the model's).
    Each refused view gets "refused" and "turn_from_front_deg" in its info and ok False. Returns {view: reason}."""
    out: dict[str, str] = {}
    rel = {n: _signed(v.info["yaw"] - front_yaw) for n, v in fitted.items() if v.ok and n in TURN_VIEWS and "yaw" in v.info}
    side = dict(TURN_SIDE)
    anchored = False
    if picture_view is not None and picture_view.ok and not picture_view.info.get("front_view"):
        pr = _signed(picture_view.info["yaw"] - front_yaw)
        if 10.0 <= abs(pr) <= 80.0:
            side["quarter"], side[SECOND_QUARTER] = (1, -1) if pr > 0 else (-1, 1)
            anchored = True

    def refuse(n: str, why: str) -> None:
        if n in out:
            return
        v = fitted[n]
        v.ok = False
        v.info["refused"] = why
        v.info["turn_from_front_deg"] = round(rel[n], 1)
        out[n] = why

    if "back" in rel and abs(rel["back"]) < BACK_MIN_TURN:
        refuse("back", f"its turn contradicts the front: the back view fits {abs(rel['back']):.0f} degrees from the front")
    for n in ("left", "right"):
        if n in rel and abs(rel[n]) < SIDE_MIN_TURN:
            refuse(n, f"its turn contradicts the front: the {n} side view fits {abs(rel[n]):.0f} degrees from the front")
    for a, b in (("left", "right"), ("quarter", SECOND_QUARTER)):
        if a in rel and b in rel and a not in out and b not in out and np.sign(rel[a]) == np.sign(rel[b]) != 0:
            wrong = a if np.sign(rel[a]) != side[a] else b
            refuse(wrong, f"its turn contradicts the front: {a} and {b} both fit on the same side, and this one belongs on the other")
    # with the built-from picture to say which side is which, a three-quarter view on the wrong side of it is refused
    # on its own too (a second three-quarter view on the picture's side paints a second version of that side's face)
    if anchored:
        for n in ("quarter", SECOND_QUARTER):
            if n in rel and n not in out and abs(rel[n]) >= 10.0 and np.sign(rel[n]) != side[n]:
                refuse(n, "its turn contradicts the front: it fits on the other side from where the picture the model was "
                          "built from puts it")
    return out


def paint(mesh: dict[str, np.ndarray], tex: np.ndarray, picture: Any, turnaround: dict[str, Any] | None = None, *,
          mode: str = "detail", size: int = SIZE, picture_yaws: list[float] | None = None, min_iou: float = MIN_IOU,
          yaw_hint: Callable[[], float | None] | None = None, source: Any = None, front_view: Any = None,
          pic_n: int | None = None, log: Callable[[str], None] | None = None,
          keep: dict[str, Any] | None = None, head_yaw: float = 0.0) -> dict[str, Any]:
    """Paint the picture (and the turnaround views, mode "full" or "detail"; "off" leaves them out) into tex (T,T,3) through
    the mesh's UVs. Returns {applied, base_color (T,T,3), reason, metrics}. Nothing is applied when the picture does not
    fit the model, or when the result is no closer to the picture than what the model had. `picture` is the one the 3D
    model was built from. `source` is the original picture when that is a different one (a character is built from the
    three-quarter view drawn from it): it paints first, in full colour, at whatever turn it fits; `front_view` (the
    front view drawn from the original) stands in for it when the original does not fit the model. head_yaw: the camera
    yaw the model's face is seen straight on from (face_detail.HEAD_YAW); the face region is found from there."""
    t0 = time.time()
    res: dict[str, Any] = {"applied": False, "base_color": tex, "reason": None, "metrics": {}}
    centre, scale = model_frame(mesh)
    outlines: dict[tuple[float, int], np.ndarray] = {}
    prepared = Prepared()  # each picture prepared once in this call

    def fit(name: str, src: Any, yaws: list[float]) -> View:
        p_, a_ = prepared(src, pic_n)
        bar = min(min_iou, SOURCE_MIN_IOU) if name == "source" else min_iou
        return fit_view(mesh, tex, p_, a_, name, yaws, centre, scale, size, bar, log=log, outlines=outlines)

    # Every view is fitted at the same time, in threads (each is independent of the others; separate processes were
    # measured and are no faster, the large images saturate memory either way); which ones are used, and in which
    # order, is decided afterwards exactly as if they had been fitted one after another.
    wanted: list[tuple[str, Any, list[float]]] = [("picture", picture, picture_yaws or PICTURE_YAWS)]
    wanted += [(n, s, y) for n, s, y in (("source", source, PICTURE_YAWS), ("front", front_view, FRONT_YAWS)) if s is not None]
    if mode != "off":
        wanted += [(n, (turnaround or {}).get(n), TURN_YAWS[n]) for n in TURN_VIEWS if (turnaround or {}).get(n) is not None]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        atlas_job = pool.submit(Atlas, mesh, tex.shape[0])
        def zone_and_head() -> tuple[dict[str, Any], View | None, dict[str, Any]]:
            z = face_zone(mesh, tex, head_yaw)
            hv, hrec = fit_head(mesh, z, [("source", source), ("front", front_view)], scale / size, pic_n, log, prepared)
            return z, hv, hrec

        zone_job = pool.submit(contextvars.copy_context().run, zone_and_head)
        jobs = {n: pool.submit(fit, n, s, y) for n, s, y in wanted}
        fitted = {n: j.result() for n, j in jobs.items()}
        front = fitted["picture"]
        if not front.ok and "ambiguous" in str(front.info.get("refused")) and yaw_hint is not None:
            # a box fits the outline at any turn: take the turn the fidelity score (texfidelity) finds, and say so
            hint = yaw_hint()
            if hint is not None:
                pic, alpha = prepared(picture, pic_n)
                front = fit_view(mesh, tex, pic, alpha, "picture", [float(hint)], centre, scale, size, min_iou, log=log)
                front.info["turn_source"] = "texfidelity"
        front.info["front_view"] = bool(abs(((front.info.get("yaw", 0.0) + 180) % 360) - 180) <= 25)
        # a drawn view fitted at a turn that contradicts the front is refused like one that does not fit (item 8)
        front_yaw, front_by = front_turn({**fitted, "picture": front}, head_yaw)
        for n, why in refuse_contradicting_turns(fitted, front_yaw, front).items():
            if log:
                log(f"view {n}: refused, {why}")
        infos = {"picture": front.info}
        views: list[View] = []
        for name in ("source", "front"):
            if name not in fitted or views:  # the drawn front view only when the original did not fit
                continue
            infos[name] = fitted[name].info
            if fitted[name].ok:
                views.append(fitted[name])
        if front.ok:
            views.append(front)
        # The three-quarter view from the other side stands where the built-from picture stands on its own side: ahead of
        # the full side views. As one of them it got a thin strip between the front and the side view, which took the
        # whole cheek (and painted the side view's own mouth beside the photo's).
        other = fitted.get(SECOND_QUARTER)
        if views and other is not None and other.ok:
            views.append(other)
        body = bool(views)
        zone, head_v, head_rec = zone_job.result()
        infos[HEAD_VIEW] = head_rec
        sides: list[View] = []
        if head_v is not None:
            # each three-quarter view's own head view, lined up to what the front head view painted; the two sides are
            # independent of each other and wait for the front only
            atlas_ = atlas_job.result()
            painted = front_painted(atlas_, head_v, tex)
            side_jobs, side_yaw = {}, {}
            for n in HEAD_SIDES:
                src_ = (turnaround or {}).get(n) if mode != "off" else None
                if src_ is None:
                    infos[f"{HEAD_VIEW}_{n}"] = {"refused": "no such view"}
                    continue
                y_, why_ = side_head_yaw(n, fitted.get(n), head_yaw)
                if y_ is None:
                    infos[f"{HEAD_VIEW}_{n}"] = {"refused": why_}
                    continue
                side_yaw[n] = (y_, src_)
            dropped = one_head_per_side({n: y for n, (y, _) in side_yaw.items()}, head_yaw)
            for n, (y_, src_) in side_yaw.items():
                if n in dropped:
                    infos[f"{HEAD_VIEW}_{n}"] = {"refused": dropped[n]}
                    continue
                side_jobs[n] = pool.submit(contextvars.copy_context().run, fit_side_head, mesh, tex, painted, zone, n, src_,
                                           y_, scale / size, pic_n, log, prepared)
            for n, j in side_jobs.items():
                sv, srec = j.result()
                infos[f"{HEAD_VIEW}_{n}"] = srec
                if sv is not None:
                    sides.append(sv)
        if head_v is not None:  # the head on its own comes first, also when every whole-body fit was refused
            views[:0] = [head_v] + sides
        n_primary = len(views)
        if not views:
            res["reason"] = front.info.get("refused", "the picture was refused")
            res["metrics"] = {"srcproject_views": infos, "srcproject_seconds": round(time.time() - t0, 1)}
            return res
        for name in TURN_VIEWS:
            if not body:  # the drawn views paint only around a picture the whole body was fitted to
                break
            if name in fitted:
                infos[name] = fitted[name].info
                if fitted[name].ok and fitted[name] not in views:
                    views.append(fitted[name])
        atlas = atlas_job.result()
        if atlas.shared > MAX_SHARED:
            # A picture shows one side; on a mirrored layout its texels are the other side's too, and it comes out in pieces
            res["reason"] = "the model's two halves share one piece of texture (mirrored), so a picture cannot be painted on one side"
            res["metrics"] = {"srcproject_layout_shared": round(atlas.shared, 3), "srcproject_views": infos,
                              "srcproject_seconds": round(time.time() - t0, 1)}
            return res
        pitch_px = atlas.pitch_m * size / scale

        def ready(i: int) -> np.ndarray:
            v = views[i]
            if v.name.startswith(HEAD_VIEW):  # its gates were set when it was fitted
                return v.pic_aligned
            full = i < n_primary or mode == "full"
            drawn = i >= n_primary or v.name == SECOND_QUARTER  # a view drawn from the picture, not the picture itself
            prepare_gates(v, {**GATES, "agree": False} if (drawn and full and not TURN_AGREE) else None)
            return v.pic_aligned if full else detail_only(v, DETAIL_TEXELS * pitch_px)

        images = list(pool.map(ready, range(len(views))))
    c = compose(atlas, views, images, n_primary, tex, zone)
    paint_, alpha_, dom, new = c["paint"], c["alpha"], c["dom"], c["base_color"]
    for v in views:
        infos.setdefault(v.name, v.info)["gates"] = {**v.stats, **v.info.get("gate_pixels", {})}
    mv = next((v for v in views if not v.name.startswith(HEAD_VIEW)), views[0])
    before, after = front_measures(mv, tex), front_measures(mv, new)  # against the first source that fitted
    painted = alpha_ > 0.5
    m = {"srcproject_picture_is_front_view": front.info["front_view"], "srcproject_measured_against": mv.name, "srcproject_views": infos, "srcproject_mode": mode, "srcproject_painted_share": round(float(painted[~atlas.is_gut].mean()), 4),
         "srcproject_share_by_view": {views[i].name: round(float(((dom == i) & painted)[~atlas.is_gut].mean()), 4) for i in range(len(views))},
         "srcproject_front_before": before, "srcproject_front_after": after,
         "srcproject_texel_pitch_mm": round(atlas.pitch_m * 1000, 3), "srcproject_face_region": zone_metrics(zone, c),
         "srcproject_front_turn": {"yaw": round(front_yaw, 1), "from": front_by},
         "srcproject_seconds": round(time.time() - t0, 1)}
    res["metrics"] = m
    worse = after["lab_diff"] == after["lab_diff"] and before["lab_diff"] == before["lab_diff"] and after["lab_diff"] > before["lab_diff"] - 0.5
    if worse:
        res["reason"] = "the result is no closer to the picture than the model's own colour"
        return res
    res.update(applied=True, base_color=new)
    if keep is not None:  # the fitted views, to paint again on another layout of the same model (repaint)
        keep.update(views=views, images=images, n_primary=n_primary, size=size, zone=zone_record(zone))
    return res


def repaint(mesh: dict[str, np.ndarray], tex: np.ndarray, kept: dict[str, Any]) -> dict[str, Any]:
    """The views paint() fitted, painted again into `tex` on another texture layout of the same model (the face pass
    packs the texture again and bakes every map onto the new layout, which softens what was painted: measured on the
    knight, front sharpness 0.73 before that bake and 0.41 after). Nothing is fitted again: the model has not moved,
    only its layout has. Returns {applied, base_color, reason}."""
    atlas = Atlas(mesh, tex.shape[0])
    if atlas.shared > MAX_SHARED:
        return {"applied": False, "base_color": tex, "reason": "the new layout is mirrored"}
    c = compose(atlas, kept["views"], kept["images"], kept["n_primary"], tex, kept.get("zone"))
    return {"applied": True, "base_color": c["base_color"], "reason": None}


def apply_files(glb: Path | str, base_color: Path | str, picture: Path | str, turnaround: dict[str, Path | None] | None,
                out_png: Path | str, *, mode: str = "detail", size: int = SIZE, source: Path | str | None = None,
                front_view: Path | str | None = None, pic_n: int | None = None,
                log: Callable[[str], None] | None = None, keep: dict[str, Any] | None = None,
                head_yaw: float = 0.0) -> dict[str, Any]:
    """paint() on files: reads the model and its base colour, writes the new base colour to out_png when it applies.
    Returns {applied, reason, base_color (the path to use), metrics}."""
    mesh = face.load_mesh(glb)
    if mesh is None:
        return {"applied": False, "reason": "the model has no texture layout", "base_color": Path(base_color), "metrics": {}}
    tex = face.read_rgb(base_color)
    def hint() -> float | None:
        from . import texfidelity

        try:
            return float(texfidelity.score(glb, picture, base_color=base_color, views=False).get("yaw"))
        except Exception:  # noqa: BLE001
            return None

    r = paint(mesh, tex, picture, {k: p for k, p in (turnaround or {}).items() if p is not None and Path(p).is_file()},
              mode=mode, size=size, yaw_hint=hint, source=source, front_view=front_view, pic_n=pic_n, log=log, keep=keep,
              head_yaw=head_yaw)
    path = Path(base_color)
    if r["applied"]:
        path = face.save_rgb(r["base_color"], out_png)
    return {"applied": r["applied"], "reason": r["reason"], "base_color": path, "metrics": r["metrics"]}


# --- the prep stage's call ----------------------------------------------------------------------------------------

PARAM = "source_project"
MODES = ("detail", "full", "off")
# "full": the drawn side and back views paint in full colour, as the original picture does. Measured on the boy, the knight
# and the caped hero against "detail" (their fine detail over the builder's own colour): the back and far sides come
# out as the drawn views show them (8-view sharpness 3.9 to 4.7, 4.6 to 5.0, 2.1 to 2.2), the front is unchanged.
DEFAULT_MODE = "full"


AUTO_CATEGORIES = ("humanoid",)


def on(params: dict[str, Any], category: str | None = None, look: dict[str, Any] | None = None) -> bool:
    """Whether the pass runs. The version's own choice wins (on or off). With no choice it runs by itself for a character
    on a smooth look (never a crisp or limited-palette one, which it would undo)."""
    if params.get(PARAM) is not None:
        return bool(params.get(PARAM))
    lk = look or {}
    return category in AUTO_CATEGORIES and not lk.get("crisp") and not lk.get("palette")


def original_pictures(sc: Any, picture: Path) -> tuple[Path | None, Path | None]:
    """(the original picture, the front view drawn from it) to paint before the picture the 3D model was built from; each
    None when it is that same picture or must not be used. A model built bald for hair from the library takes only the
    bald front view (the original has the hair on it); a model built from an edited picture takes neither (the original
    shows what the edit removed)."""
    from . import hairlib

    def other(p: Any) -> Path | None:
        if p is None or not Path(p).is_file():
            return None
        try:
            if Path(p).samefile(picture) or Path(p).read_bytes() == Path(picture).read_bytes():
                return None
        except OSError:
            return None
        return Path(p)

    mv = sc.stage_version("mesh")
    if mv is not None and (mv.params or {}).get("picture_edit"):
        return None, None
    if hairlib.is_library(sc):
        return None, other(sc.lineage_file("bald_front_cutout", ("turnaround",)))
    return other(sc.lineage_file("cutout", ("concept",))), other(sc.lineage_file("front_cutout", ("turnaround",)))


def mode_of(params: dict[str, Any]) -> str:
    m = params.get("source_project_mode")
    return m if m in MODES else DEFAULT_MODE


def _longer_side(p: Path) -> int:
    with Image.open(p) as im:
        return max(im.size)


def _with_alpha_of(big: Path, small_: Path, dest: Path) -> Path:
    """The upscaled picture with the original's cut-out (the upscaler returns colour only)."""
    with Image.open(big) as b, Image.open(small_) as s:
        out = b.convert("RGB")
        if "A" in s.getbands():
            out.putalpha(s.getchannel("A").resize(out.size, Image.LANCZOS))
        out.save(dest)
    return dest


async def upscale_sources(sc: Any, files: dict[str, Path | None], work: Path) -> tuple[dict[str, Path | None], dict[str, Any]]:
    """Each picture under UPSCALE_BELOW made twice as big with the first step's learned upscaler (an original photo, or
    the original of a Realistic look: 4xRealWebPhoto; everything else: Real-ESRGAN x2) on the card that draws pictures, before it is painted
    onto the model: a picture sampled at its own size cannot be sharper than its pixels, and the atlas can hold more
    (measured on the knight: front sharpness 0.47 without, 0.73 with, colour unchanged). A picture that cannot be
    upscaled (no card, a failure) is used as it is. Returns (the files to use, {upscaled: [...], model, seconds})."""
    from .jobs import JobCancelled
    from .stages import concept, face_detail

    t0 = time.time()
    info: dict[str, Any] = {"upscaled": [], "model": None}
    inst = face_detail.image_instance(sc)
    if inst is None:
        return files, {**info, "skipped": "no card to draw on"}
    cv = sc.stage_version("concept")
    photo = getattr(sc.asset, "style_preset", None) == "realistic_pbr" or (
        cv is not None and (cv.params or {}).get("source") in ("upload", "upload_edit"))
    # The photo upscaler is far slower (about 40 s a picture against 5): it is kept for the original photo, where its
    # care with camera noise matters; the views drawn from it are clean pictures and take the quick one.
    def model_for(key: str) -> str:
        return concept.UPSCALE_PHOTO_MODEL if photo and key == "source" else concept.UPSCALE_ART_MODEL

    used: set[str] = set()
    out = dict(files)
    done: dict[str, Path] = {}
    for key, p in files.items():
        if p is None or not Path(p).is_file():
            continue
        try:
            side = _longer_side(Path(p))
            if side >= UPSCALE_BELOW:
                continue
            if str(p) in done:
                out[key] = done[str(p)]
                continue
            with Image.open(p) as im:
                w, h = im.size
            f = PIC_N_BIG / float(side)
            name = await sc.stage_input(p, f"vgac_{sc.version.id[:8]}_srcproject_{key}.png", instance=inst)
            model = model_for(key)
            graph = concept.upscale_graph(name, model, int(round(w * f)), int(round(h * f)), sc.prefix(f"srcproject_{key}"))
            entry = await sc.comfy(graph, instance=inst, message="Sharpening the pictures to paint from")
            raw = await sc.download_image(entry, "save", work / f"up_{key}_raw.png", instance=inst)
            out[key] = done[str(p)] = await asyncio.to_thread(_with_alpha_of, Path(raw), Path(p), work / f"up_{key}.png")
            info["upscaled"].append(key)
            used.add(model)
        except (JobCancelled, asyncio.CancelledError):
            raise
        except Exception as e:  # noqa: BLE001  (the picture is used as it is)
            info.setdefault("failed", {})[key] = str(e)[:160]
    if used:
        info["model"] = sorted(used)
        for m in used:
            sc.use_model("upscale_photo" if m == concept.UPSCALE_PHOTO_MODEL else "upscale_art")
    info["seconds"] = round(time.time() - t0, 1)
    note = upscale_note(info)
    if note:  # shown as the clean-up's pictures_sharpened warning (stages/prep.py upscale_check)
        info["note"] = note
    return out, info


def upscale_note(info: dict[str, Any]) -> str | None:
    """A plain sentence when pictures were not made bigger before painting (the upscaler failed, or no card could run
    it), else None. Before this the failure was only in the metrics, and the model was painted from the small pictures
    with nothing said (one stored test run: all 8 pictures, "All connection attempts failed", after a reboot)."""
    failed = info.get("failed") or {}
    if failed:
        first = next(iter(failed.values()))
        n, m = len(failed), len(failed) + len(info.get("upscaled") or [])
        return (f"{n} of {m} pictures could not be made sharper before painting ({first}); "
                "the model was painted from the smaller pictures, so its texture is softer than it could be.")
    if info.get("skipped"):
        return (f"The pictures were not made sharper before painting ({info['skipped']}); "
                "the model was painted from the smaller pictures, so its texture is softer than it could be.")
    return None


async def run_stage(sc: Any, glb: Path, current: dict[str, Path], params: dict[str, Any],
                    keep: dict[str, Any] | None = None, head_yaw: float = 0.0) -> dict[str, Path]:
    """The pass inside the prep stage (stages/prep.py), after the last rebake and before the face pass: paints the picture
    the asset started from, the picture the 3D step was given and the turnaround views into the current base colour; replaces it in the GLB and in
    `current` when it applies, and records the before and after numbers in the stage metrics. head_yaw: the camera yaw
    the face is seen straight on from (as the face pass is given it). Returns `current`."""
    mode = mode_of(params)
    sc.set_params(**{"source_project_mode": mode})
    base = current.get("base_color")
    picture = sc.lineage_file("input", ("mesh",)) or sc.lineage_file("cutout", ("concept",))
    if base is None or picture is None:
        sc.set_metrics(srcproject_applied=False, srcproject_reason="no base colour or no picture to paint from")
        return current
    turn = {v: sc.lineage_file(f"{v}_cutout", ("turnaround",)) for v in TURN_VIEWS}
    try:
        source, front_view = original_pictures(sc, Path(picture))
    except Exception:  # noqa: BLE001  (no lineage to read: the pass paints from the model's own picture, as before)
        source, front_view = None, None
    work = sc.dir / "srcproject"
    work.mkdir(parents=True, exist_ok=True)
    from .jobs import JobCancelled

    srcs: dict[str, Path | None] = {"picture": Path(picture), "source": source, "front": front_view, **turn}
    try:
        srcs, up = await upscale_sources(sc, srcs, work)
    except (JobCancelled, asyncio.CancelledError):
        raise
    except Exception as e:  # noqa: BLE001  (the pictures are used as they are)
        up = {"upscaled": [], "skipped": f"failed: {str(e)[:160]}"}
        up["note"] = upscale_note(up)
    sc.set_metrics(srcproject_upscale=up)
    big = any(p is not None and Path(p).is_file() and _longer_side(Path(p)) >= UPSCALE_BELOW for p in srcs.values())
    picture, source, front_view = srcs["picture"], srcs["source"], srcs["front"]
    turn = {v: srcs.get(v) for v in TURN_VIEWS}
    # The head is fitted by its eyes and mouth (fit_head): MediaPipe on the processor (the mediapipe package), else on
    # the card that draws pictures, as the face pass asks it; else the darkest-spot finder.
    from . import facemarks

    local_tok = facemarks.use_local() if params.get("face_marks") is not False else None
    card_tok = None
    try:
        from .stages import face_detail

        inst = face_detail.image_instance(sc)
        if inst is not None and params.get("face_marks") is not False:
            client = sc.client(inst)
            card_tok = facemarks.use(getattr(client, "base_url", None), getattr(client, "input_dir", None))
    except Exception:  # noqa: BLE001  (no card: the local finder or the darkest spots)
        card_tok = None
    try:
        r = await asyncio.to_thread(apply_files, glb, base, picture, turn, work / "base_color.png", mode=mode, source=source,
                                    front_view=front_view, pic_n=PIC_N_BIG if big else None, keep=keep, head_yaw=head_yaw)
    except Exception as e:  # noqa: BLE001  (the pass never fails a run: the colour stays as it was)
        sc.set_metrics(srcproject_applied=False, srcproject_reason=f"failed: {str(e)[:200]}")
        return current
    finally:
        facemarks.done(card_tok)
        facemarks.done_local(local_tok)
    sc.set_metrics(**r["metrics"], srcproject_applied=bool(r["applied"]), srcproject_reason=r["reason"])
    if r["applied"]:
        await asyncio.to_thread(imaging.glb_replace_images, glb, base_color=r["base_color"])
        sc.register("base_color", r["base_color"])
        current = {**current, "base_color": r["base_color"]}
    return current


async def run_again(sc: Any, glb: Path, maps: dict[str, Path], kept: dict[str, Any] | None) -> dict[str, Path]:
    """After the face pass has packed the texture again (stages/face_detail.py): the same pictures painted onto the new
    layout, so the bake onto it is not the last word on the body's sharpness. `glb` and `maps` are the newly laid out
    model and its maps; returns the maps with the base colour replaced when it applies. Never fails the step."""
    base = maps.get("base_color")
    if not kept or not kept.get("views") or base is None:
        return maps
    t0 = time.time()
    out = sc.dir / "srcproject" / "base_color_relaid.png"

    def work() -> dict[str, Any]:
        mesh = face.load_mesh(glb)
        if mesh is None:
            return {"applied": False, "reason": "the model has no texture layout"}
        r = repaint(mesh, face.read_rgb(base), kept)
        if r["applied"]:
            face.save_rgb(r["base_color"], out)
        return {"applied": r["applied"], "reason": r["reason"]}

    try:
        r = await asyncio.to_thread(work)
    except Exception as e:  # noqa: BLE001
        r = {"applied": False, "reason": f"failed: {str(e)[:200]}"}
    sc.set_metrics(srcproject_again={**r, "seconds": round(time.time() - t0, 1)})
    if not r["applied"]:
        return maps
    await asyncio.to_thread(imaging.glb_replace_images, glb, base_color=out)
    return {**maps, "base_color": out}
