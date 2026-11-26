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
import dataclasses
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image

from . import face, imaging, picture_view

SIZE = 2048              # working render size; the picture is sampled bicubic (Catmull-Rom) once, at this size
PIC_N = 1024            # side the pictures are worked at when they come as they are
PIC_N_BIG = 2048        # ... and when they were made bigger first (upscale_sources), or came bigger
UPSCALE_BELOW = 1536    # a picture whose longer side is under this is made bigger before it is painted
FIT_N = 256              # size the camera and silhouette search runs at
FLOW_N = 512             # size the optical flow runs at
MIN_IOU = 0.85           # silhouette match below this refuses a view
COS_CUT = math.cos(math.radians(75.0))
# Props and buildings (OBJECT_CATEGORIES): a view paints only up to about 53 degrees off square, in full by about 43. A
# picture of a building or a prop is often fitted at 45 to 60 degrees to the walls it sees, and there its detail is
# stretched across the wall; past this the model's own colour stays.
OBJECT_COS_CUT = 0.6
# Rigid props (CORNER_CATEGORIES): a picture fitted more than CORNER_MAX_DEG from the nearest side is seen from a corner.
# Every side is then at a slant and only the corners' bevels face it, so it would paint the picture's lit and shaded
# sides of each corner onto them as streaks; it does not paint.
CORNER_MAX_DEG = 35.0
CORNER_CATEGORIES = ("prop_rigid",)
# Rigid props (CORNER_CATEGORIES): a picture whose silhouette match is under this does not paint. A prop's fine parts
# (slats, gaps, panels) are narrower than the outline error such a fit allows, so its parts land beside the model's.
RIGID_MIN_IOU = 0.90
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
# A gutter texel's surface position continues the border triangle's plane outward. For a triangle that is a sliver in the
# layout the continuation runs far past the model, and the dense grids of surface_correction are sized by the extent of
# all positions. A gutter texel more than this many average texels (per gutter texel of distance) from the island texel
# it copies gets no position and is left out.
GUTTER_REACH_TEXELS = 8.0
DETAIL_TEXELS = 2.0      # "detail" mode: the TRELLIS colour is kept below this many texels of surface
BG = 0.5
STRETCH = (2.0, 3.5)     # one picture pixel covering this many texels along the surface: weight falls from 1 to 0
DISC_PX = 4              # pixels (at the working size) kept away from depth discontinuities
OVERLAP_JUMP_M = 0.03    # a step in depth of this much is one part of the body in front of another (a hand before the hip)
OVERLAP_PX = 24          # pixels (at the working size) round such a step where a whole-body view paints neither part: the
                         # drawn picture's hand sits a few pixels off the model's, so the hip's colour (W1 R5's navy bikini,
                         # M1 R4's green jacket) landed on the hand's edge past the 4 pixels of DISC_PX; those texels
                         # come from views that see them clear, else keep the 3D step's colour
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
FRONT_HALF = 100.0       # with the model's facing decided (front_known), the original picture and the picture the model was
                         # built from are fitted only within this of the front: a front picture fitted from behind paints
                         # the face onto the back of the head and then refuses the true back view as contradicting it (the
                         # Low-poly dwarf L2 of 2026-11-16: the original fitted at 185 by the head's own match, its colour
                         # 26.7 there against 20.6 from the front). 100, not 90, leaves room for a picture from the side.
FRONT_YAWS = [-20, -10, 0, 10, 20]
SOURCE_MIN_IOU = 0.82    # the original picture is taken at a slightly looser outline match than the drawn views (the
                         # photo boy: 0.843, and with it colour difference 9.0 to 4.7 and his own face on the model)
TURN_AGREE = False       # the structure gate on the drawn side and back views when they paint in full colour: off,
                         # because there it compares two guesses (the builder's back and the drawn back) and cuts the
                         # drawn one into patches
TURN_HEAD_W = 20.0        # weight of the head's own match in the choice between turns whose outlines fit about equally
                          # (turn_score): a side view's outline fits about as well from in front of the profile as from
                          # behind it; on the live M1 R4 run the right view took -105 by the whole body's colour and
                          # detail and painted a second ear; the head's match said -70 (0.53 against 0.38), as M1 R2 and
                          # R3 fitted it
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
HEAD_NECK = (1.35, 0.85)  # the head view fades in up the neck between these shares of (nose tip to top of head) below the
                          # nose tip (the chin is at about 0.65): the hand-over to the body's own fit lies on the neck, over
                          # about 6 cm (it was 0.95 to 0.75, about 2.5 cm, and showed as a line on the live W2-clothed run)
HEAD_SIDES = ("quarter", SECOND_QUARTER)  # the three-quarter views that get a head view of their own, after the front's
SIDE_HEAD_TURN = (15.0, 80.0)  # turn from the face a three-quarter head view must be seen from
SIDE_HEAD_YAW = 40.0      # the turn from the face a three-quarter head view is seen from when its body fit gives none
HEAD_HANDOVER = 0.1       # where a head view's weight reaches this, no whole-body fit paints (they hand over at the neck)
SQUASH_MAX = 0.05         # a head fit whose two scales differ by more than this share is fitted again with one scale
HEAD_FRAME_MARGIN = (0.03, 0.10)  # of the head picture's frame: a head view's weight is zero this close to the frame's edge,
                                 # full from the second; and zero where its fit reaches past the frame (face.apply_alignment
                                 # repeats the last row there: on the live W2-clothed run the bottom 18 mm of the neck)
HEAD_FRAME_BELOW = 0.01   # metres: a head view's frame reaches this far below the bottom of its neck fade at full weight
HEAD_FACING = (0.35, 0.45)  # surface-to-camera cosine over which a head view fades in: nothing seen more edge-on than
                            # about 70 degrees (the underside of the chin from a level camera: the live W2-clothed run had
                            # the chin's last rows stretched over it as a pale band)
CHIN = (1.15, 0.45, 0.8)    # the chin's underside and the upper neck: between these shares of (nose tip to top of head)
                            # below the nose tip, within this share of the head's width of its middle, facing down
SIDE_AGREE_MAX = 0.12     # of the face's width: how far a three-quarter head view's own fit may put the eyes and mouth from
                          # where the front head view painted them before it is refused
# A three-quarter head picture is lined up by its face, and its own ear can then land on the cheek in front of the
# model's (the live M1 R4 run: about 3 cm, a second ear at the side). The picture round its near ear is moved onto the
# model's ear (MediaPipe pose ear points on the picture and on the model's own colour), fully within EAR_MOVE_FULL_CM of
# the ear, fading to nothing EAR_MOVE_FADE of the way to the nearest of its eyes and mouth, which do not move; not at all
# when the two ears are under EAR_MOVE_MIN_CM apart (the clean stored runs: 0.3 to 0.8 cm).
EAR_MOVE_MIN_CM = 1.0
EAR_MOVE_FULL_CM = 1.0
EAR_MOVE_FADE = 0.85
EAR_POSE_N = 1024          # the picture is shown to the pose finder at this size
# The pose finder gives an ear point with full visibility whether or not an ear shows (stored runs: 1.0 on all 17
# sides, also under hair). An ear is taken as seen only where at least EAR_SKIN_SHARE of the picture within
# EAR_SKIN_CM of the point lies within EAR_SKIN_DE (Lab) of the head's own skin (the median round the middle of its eyes
# and mouth, the same image); both the picture's point and the model's must pass. On W2 R3 the right ear is under hair
# (0.0 of skin): moved, it dragged the hair, the cheek and the glasses arm. Known miss: an ear in deep shadow reads as
# unseen (M1 R4's right side), which leaves it as before.
EAR_SKIN_CM = 1.0
EAR_SKIN_DE = 15.0
EAR_SKIN_SHARE = 0.5
# The turn a drawn view was fitted at, against the front's (item 8). The side a view belongs on (+1: toward the
# character's left, a positive camera yaw; every good stored run fits them so), the least turn from the front a full
# side view may sit at, and the least for the back.
TURN_SIDE = {"quarter": 1, SECOND_QUARTER: -1, "left": 1, "right": -1}
QUARTERS = ("quarter", SECOND_QUARTER)


def other_quarter(built_from: str | None) -> str:
    """The three-quarter view that is not the one the 3D model was built from (the mesh version's input_view: "quarter"
    unless step 3 built from the second one, when the first lost the pose)."""
    return "quarter" if built_from == SECOND_QUARTER else SECOND_QUARTER
SIDE_MIN_TURN = 45.0
PICTURE_MAX_TURN = 80.0  # a picture the model was built from that is not a front view (a three-quarter view) fitted more
                         # than this from the front is refused: it would paint one side's picture onto the back
BACK_MIN_TURN = 120.0


# --- image helpers ----------------------------------------------------------------------------------------------


def _sample(img: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """face.sample (bilinear, centres at +0.5, clamped) with the same arithmetic, the four taps read with one flat gather
    each."""
    h, w = img.shape[:2]
    fx = np.clip(x - 0.5, 0, w - 1)
    fy = np.clip(y - 0.5, 0, h - 1)
    x0 = np.floor(fx).astype(int)
    y0 = np.floor(fy).astype(int)
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)
    ax = (fx - x0)[..., None]
    ay = (fy - y0)[..., None]
    flat = img.reshape(h * w, -1)
    shp = (*x0.shape, flat.shape[1])
    r0, r1 = y0 * w, y1 * w
    top = flat.take(r0 + x0, axis=0).reshape(shp) * (1 - ax) + flat.take(r0 + x1, axis=0).reshape(shp) * ax
    bot = flat.take(r1 + x0, axis=0).reshape(shp) * (1 - ax) + flat.take(r1 + x1, axis=0).reshape(shp) * ax
    return top * (1 - ay) + bot * ay


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
    # the same sums in the same order as one fancy index per tap, with one flat gather per tap and the sums made in place
    flat = img.reshape(h * w, -1)
    cols = [np.clip(x0 + i - 1, 0, w - 1) for i in range(4)]
    out: Any = None
    for j in range(4):
        base = np.clip(y0 + j - 1, 0, h - 1) * w
        row: Any = None
        for i in range(4):
            term = np.multiply(wx[i], flat.take(base + cols[i], axis=0).reshape(*x0.shape, -1))
            if row is None:
                row = term
            else:
                row += term
        row *= wy[j]
        if out is None:
            out = row
        else:
            out += row
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
        if cam.is_ortho:
            self.cos = np.where(m, -(n @ cam.basis[2]), 0.0)
        else:
            pos = face.interpolate(mesh["verts"], mesh["faces"], ras)
            self.cos = np.where(m, cam.cos_to_camera(n, pos), 0.0)
        v, f = mesh["verts"], mesh["faces"]
        fn = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
        fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
        if cam.is_ortho:
            self.fcos = np.where(m, -(fn[np.maximum(ras["tri"], 0)] @ cam.basis[2]), 0.0)
        else:
            fpos = face.interpolate(mesh["verts"], mesh["faces"], ras)
            self.fcos = np.where(m, cam.cos_to_camera(fn[np.maximum(ras["tri"], 0)], fpos), 0.0)
        self.uv = face.interpolate(face.uv_pixels(mesh["uv"], 1), mesh["faces"], ras)


def render_colour(ras: Raster, tex: np.ndarray, bg: float = BG) -> np.ndarray:
    T = tex.shape[0]
    img = np.full(ras.mask.shape + (3,), bg)
    uv = ras.uv[ras.mask] * T
    img[ras.mask] = _sample(tex, uv[:, 0], uv[:, 1])
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
    return _sample(pic[..., None], q[0].reshape(n, n), q[1].reshape(n, n))[..., 0]


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
    a = _sample(pm[..., None], qx, qy)[..., 0] > 0.5
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


def _avg_buf(a: np.ndarray, p: np.ndarray) -> np.ndarray:
    """_avg with a caller-owned padded buffer p (h+2, w+2): the same sums in the same order, no pad call."""
    p[1:-1, 1:-1] = a
    p[0, 1:-1] = a[0]
    p[-1, 1:-1] = a[-1]
    p[:, 0] = p[:, 1]
    p[:, -1] = p[:, -2]
    return (p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:]) / 4.0


def _hs_update(gx, gy, it, ubar, vbar, a2):  # noqa: ANN001
    sxx, sxy, syy = (gx * gx).sum(-1), (gx * gy).sum(-1), (gy * gy).sum(-1)
    b1 = a2 * ubar - (gx * it).sum(-1)
    b2 = a2 * vbar - (gy * it).sum(-1)
    det = (sxx + a2) * (syy + a2) - sxy * sxy
    return ((syy + a2) * b1 - sxy * b2) / det, ((sxx + a2) * b2 - sxy * b1) / det


def _hs_terms(gx, gy, it, a2):  # noqa: ANN001
    """What _hs_update computes from the gradients alone: the same arrays, made once for all the iterations."""
    sxx, sxy, syy = (gx * gx).sum(-1), (gx * gy).sum(-1), (gy * gy).sum(-1)
    return sxx + a2, syy + a2, sxy, (gx * it).sum(-1), (gy * it).sum(-1), (sxx + a2) * (syy + a2) - sxy * sxy


def _hs_step(terms, ubar, vbar, a2):  # noqa: ANN001
    sxxa, syya, sxy, gxit, gyit, det = terms
    b1 = a2 * ubar - gxit
    b2 = a2 * vbar - gyit
    return (syya * b1 - sxy * b2) / det, (sxxa * b2 - sxy * b1) / det


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
            mw = _sample(m, X + u[..., 0], Y + u[..., 1])
            gx, gy = np.zeros_like(mw), np.zeros_like(mw)
            for c in range(mw.shape[2]):
                gx[..., c], gy[..., c] = _grad(mw[..., c])
            it = mw - r
            du, dv = np.zeros((h, w)), np.zeros((h, w))
            terms = _hs_terms(gx, gy, it, a2)
            pbuf = np.empty((h + 2, w + 2))
            for _ in range(iters):
                du, dv = _hs_step(terms, _avg_buf(du, pbuf), _avg_buf(dv, pbuf), a2)
            u = u + np.stack([du, dv], -1) * reg[..., None]
            lim = max_px * (w / W)
            u = np.clip(u, -lim, lim)
        u = np.stack([face.blur(u[..., 0], 1.0 + 0.5 * lv), face.blur(u[..., 1], 1.0 + 0.5 * lv)], -1)
    return u


# --- one view ---------------------------------------------------------------------------------------------------


class View:
    def __init__(self, name: str, weight: float):
        self.name, self.weight = name, weight
        self.cos_cut = COS_CUT  # the steepest angle it paints at (cos_weight); OBJECT_COS_CUT for props and buildings
        self.ok = False
        self.info: dict[str, Any] = {"name": name}


def turn_score(colour: float, detail: float, head: float) -> float:
    """How well a picture agrees with the model's own colour at one turn, lower is better: the colour difference
    (Lab) less the fine detail's match over the whole figure and the head's coarser match (fit_view's pool of turns
    whose outlines fit about equally)."""
    return colour - 40.0 * detail - TURN_HEAD_W * head


def _camera(centre: np.ndarray, yaw: float, scale: float, size: int, tilt: float = 0.0,
            lens: float | None = None) -> face.Camera:
    """The camera of one fit: turn, and (WP-61) the tilt and the lens. Tilt 0 with no lens is the old orthographic camera."""
    return face.Camera(target=centre, yaw_deg=float(yaw), scale=scale, size=size, tilt_deg=float(tilt), lens_mm=lens)


def fit_view(mesh: dict[str, np.ndarray], tex: np.ndarray, pic: np.ndarray, alpha: np.ndarray, name: str,
             yaws: list[float], centre: np.ndarray, scale: float, size: int = SIZE, min_iou: float = MIN_IOU,
             use_flow: bool = True, log: Callable[[str], None] | None = None,
             outlines: dict[tuple[float, int], np.ndarray] | None = None, tilt: float = 0.0,
             lens: float | None = None) -> View:
    """Find the camera turn (among yaws), the picture's similarity fit and its flow warp; refuse what does not fit.
    `outlines` is a store of the model's outline per (turn, size) shared by the views of one paint() call: two pictures
    searching the same turns draw the model once. `tilt` and `lens` (WP-61) give the camera the picture was taken from;
    the defaults are the old orthographic camera."""
    v = View(name, VIEW_WEIGHT.get(name, 0.5))
    n = min(FIT_N, size)
    pm = alpha > 0.5
    fits: dict[float, dict[str, Any]] = {}
    pic_s = small(pic, n)

    def try_yaw(yaw: float, nn: int = n) -> dict[str, Any]:
        if yaw not in fits or fits[yaw]["n"] != nn:
            key = (float(yaw), nn, float(tilt), lens)
            rm = outlines.get(key) if outlines is not None else None
            if rm is None:
                rm = outline(mesh, _camera(centre, yaw, scale, nn, tilt, lens))
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
    head = face.find_head(mesh["verts"])
    head_rows = None
    if head is not None:  # the head's rows at the fit size: the top 13 percent of the figure (face.find_head)
        yb = head.top - 0.13 * head.height
        head_rows = int(np.clip((float(centre[1]) - yb) / scale * n + n / 2, 0, n))
    v.info["turn_scores"] = {}
    if len(pool) > 1:
        for y in pool:
            f = fits[y]
            rc = render_colour(Raster(mesh, _camera(centre, y, scale, n, tilt, lens)), tex)
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
                hcorr = 0.0
                if head_rows:
                    hb = both.copy()
                    hb[head_rows:] = False
                    if hb.sum() > 200:
                        hcorr = face.feature_match(ap, rc, hb, lo=8.0, hi=30.0)
                d = turn_score(ld, corr, hcorr)
                v.info["turn_scores"][float(y)] = {"iou": round(float(f["iou"]), 3), "colour": round(ld, 2),
                                                   "detail": round(float(corr), 3), "head": round(float(hcorr), 3),
                                                   "score": round(float(d), 2)}
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
    cam = _camera(centre, yaw, scale, size, tilt, lens)
    v.cam, v.raster = cam, Raster(mesh, cam)
    M = sim_to_full(fit, pic.shape[0], size)
    ys, xs = np.mgrid[0:size, 0:size] + 0.5
    qx, qy = M[0, 0] * xs + M[0, 1] * ys + M[0, 2], M[1, 0] * xs + M[1, 1] * ys + M[1, 2]
    a_aff = catmull(pic, qx, qy)
    al_aff = _sample(alpha[..., None], qx, qy)[..., 0]
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
            al_use = _sample(alpha[..., None], qx2, qy2)[..., 0]
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


def overlap_band(v: "View") -> np.ndarray:
    """(S, S) bool: the pixels of a whole-body view within OVERLAP_PX (scaled to its size) of a step in depth of
    OVERLAP_JUMP_M or more, where one part of the body lies in front of another in the picture. Neither part is painted
    from this view there."""
    size = v.size
    px_m = v.cam.scale / size
    big = depth_discontinuities(v.raster.z, px_m, OVERLAP_JUMP_M / px_m)
    return face.dilate_mask(big, max(1, int(round(OVERLAP_PX * size / SIZE))))


def prepare_gates(v: "View", gates: dict[str, bool] | None = None) -> None:
    """The per-view gates on top of the angle cut-off: depth-edge erosion (changes the feather), the structure agreement
    gate and the flow-limit gate (images in v.gate_ag and v.gate_fs)."""
    g = GATES if gates is None else gates
    size = v.size
    r = max(2, size // 340)
    valid = v.valid & ~face.dilate_mask(v.disc, DISC_PX) if g.get("disc") else v.valid
    if g.get("disc") and not v.name.startswith(HEAD_VIEW) and getattr(v, "raster", None) is not None:
        valid = valid & ~overlap_band(v)
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
            own = lut[srcflat]
            reach = GUTTER_REACH_TEXELS * gutter * self.pitch_m
            keep = ok & (own >= 0) & (np.linalg.norm(gpos - pos[np.maximum(own, 0)], axis=1) <= reach)
            self.idx = np.concatenate([isl, gi[keep]])
            self.pos = np.concatenate([pos, gpos[keep]])
            self.nrm = np.concatenate([nrm, gn[keep]])
            self.is_gut = np.concatenate([self.is_gut, np.ones(int(keep.sum()), dtype=bool)])
            self.src = np.concatenate([self.src, lut[srcflat][keep]])
            ext = self.cover.copy().reshape(-1)
            ext[gi[keep]] = True
            self.cover_ext = ext.reshape(T, T)


def cos_weight(cosv: np.ndarray, cut: float = COS_CUT) -> np.ndarray:
    """cos^4 of the angle between surface and view, zero past `cut` (75 degrees; feathered over the next 0.12 of cosine)."""
    return np.clip(cosv, 0.0, 1.0) ** 4 * face.smoothstep(cut, cut + 0.12, cosv)


def project_view(atlas: Atlas, v: View, size: int | None = None, pitch_m: float | None = None) -> dict[str, np.ndarray]:
    """The texels this view sees squarely: index into atlas.idx, screen position, weight. A left, right or back view
    stays off the face region (atlas.zone, set by compose). `size` is ignored: the view's camera has its own."""
    cam = v.cam
    size = cam.size
    pitch_m = atlas.pitch_m if pitch_m is None else pitch_m
    sp, sd = cam.project(atlas.pos)
    xi = np.clip(sp[:, 0].astype(int), 0, size - 1)
    yi = np.clip(sp[:, 1].astype(int), 0, size - 1)
    cosv = cam.cos_to_camera(atlas.nrm, atlas.pos)
    tol = 2.5 * (cam.scale / size) / np.maximum(np.abs(cosv), 0.15)
    inb = (sp[:, 0] >= 0) & (sp[:, 0] < size) & (sp[:, 1] >= 0) & (sp[:, 1] < size)
    visible = inb & (sd <= v.raster.z[yi, xi] + tol) & (v.raster.fcos[yi, xi] > 0.05)
    f0 = _sample(v.feather0[..., None], sp[:, 0], sp[:, 1])[:, 0]
    f1 = _sample(v.feather[..., None], sp[:, 0], sp[:, 1])[:, 0]
    ga = _sample(v.gate_ag[..., None].astype(np.float64), sp[:, 0], sp[:, 1])[:, 0]
    gf = _sample(v.gate_fs[..., None].astype(np.float64), sp[:, 0], sp[:, 1])[:, 0]
    foot = v.pic_px_m / np.maximum(cosv, 0.05) / pitch_m
    st = 1.0 - face.smoothstep(STRETCH[0], STRETCH[1], foot) if GATES.get("stretch") else np.ones_like(foot)
    fd = np.clip(f1 / np.maximum(f0, 1e-6), 0.0, 1.0)
    base = v.weight * cos_weight(cosv, getattr(v, "cos_cut", COS_CUT)) * f0 * visible
    if v.name.startswith(HEAD_VIEW):
        base = base * face.smoothstep(HEAD_FACING[0], HEAD_FACING[1], cosv)
    zi = getattr(v, "zone_img", None)  # the head view: the head above the neck only
    if zi is not None:
        base = base * _sample(zi[..., None], sp[:, 0], sp[:, 1])[:, 0]
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
    out = {"sel": keep, "sp": sp[keep], "w": w[keep]}
    if v.name.startswith(HEAD_VIEW):
        # where this head view can paint at all, whatever its weight: seen, facing it, inside its frame and its neck fade,
        # not by a depth edge and not stretched. The body fits stay off there (blend), so they hand over down the neck
        # as the neck fade does, and not on the cheeks, where a head view's weight is low but its placement is right.
        cover = f0 * visible * fd * st * face.smoothstep(HEAD_FACING[0], HEAD_FACING[1], cosv)
        if zi is not None:
            cover = cover * _sample(zi[..., None], sp[:, 0], sp[:, 1])[:, 0]
        ck = np.flatnonzero(cover > 1e-3)
        out["cover_sel"], out["cover"] = ck, np.clip(cover[ck], 0.0, 1.0)
    return out


def detail_only(v: View, sigma_px: float) -> np.ndarray:
    """The view's picture with its broad colour and tone (below sigma_px) taken from the TRELLIS drawing: the drawing's
    low frequencies plus the picture's high frequencies, masked so the backdrop does not bleed in."""
    lo_r = face.masked_blur(v.render, v.valid, sigma_px)
    lo_p = face.masked_blur(v.pic_aligned, v.valid, sigma_px)
    return np.clip(lo_r + v.pic_aligned - lo_p, 0.0, 1.0)


def blend(atlas: Atlas, projs: list[dict[str, np.ndarray]], images: list[np.ndarray],
          n_primary: int = 1, heads: list[bool] | None = None,
          parts: dict[str, Any] | None = None,
          shifts: list[np.ndarray | None] | None = None,
          layers: list[dict[str, np.ndarray]] | None = None,
          colours: list[np.ndarray] | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Weighted blend of the views (the dominant one counts as weight^BLEND_POW). The first n_primary views are in order
    of priority: each one, and every view after them, fades out where the ones before it see well. Head views (heads[i],
    first among them) hand over to one another the same way, and every other view stays off as far as a head view covers
    the surface (its "cover" from project_view; HEAD_HANDOVER of its weight when it has none): no whole-body fit paints
    the head once a head view has, and they meet down the neck, over the head views' neck fade. parts, when given, gets
    the head views' and the other views' sums apart ("head", "body": (num (N,3), den (N))), for the tone match. layers, when
    given, is filled with each view's texels, the weight it ended with after the hold-offs and the colour sampled for it
    (for tone_adjust); colours, when given, stands in for that sampling (the same arrays, adjusted). Returns (paint (N,3), alpha (N), dominant view per texel, -1 for none)."""
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
            if "cover" in p:  # by where the head view can paint (project_view), so the hand-over follows the neck fade
                cov = np.zeros(N)
                cov[p["cover_sel"]] = p["cover"]
                # and where it paints with HEAD_HANDOVER of weight or more, as before the gradual hand-over: cover alone
                # let the body fits back onto the upper neck under the chin, where the wider neck fade starts
                # (M1-shorts' built-from picture 12.0 to 16.5 percent of the face region)
                # (M1-shorts' built-from picture 12.0 to 16.5 percent of the face region); not in the neck fade itself,
                # where that held the body fits off until the head views' weight was nearly gone, so the two met in a
                # line at the fade's foot instead of crossing over its height (live round four: a line across the hair)
                above = 1.0
                zt_ = getattr(atlas, "zone", None)
                if zt_ is not None and zt_.get("above_neck") is not None:
                    above = zt_["above_neck"]
                cov = np.maximum(cov, np.clip(seen / HEAD_HANDOVER, 0.0, 1.0) * above)
                under_head = under_head * (1.0 - cov)
            else:
                under_head = under_head * (1.0 - np.clip(seen / HEAD_HANDOVER, 0.0, 1.0))
        else:
            w = w * suppress[sel] * under_head[sel]
        if i < n_primary and not heads[i]:
            seen = np.zeros(N)
            seen[sel] = w
            suppress = suppress * (1.0 - np.clip(seen / FRONT_PRIORITY, 0.0, 1.0))
        if colours is not None:
            col = colours[i]
        else:
            col = catmull(img, p["sp"][:, 0].reshape(-1, 1), p["sp"][:, 1].reshape(-1, 1))[:, 0]
            if shifts is not None and shifts[i] is not None:  # a three-quarter head view's tone, matched to the front's
                col = np.clip(col + shifts[i][None], 0.0, 1.0)
        if layers is not None:
            layers.append({"sel": sel, "w": w, "col": col})
        ww = w ** BLEND_POW
        num[sel] += col * ww[:, None]
        den[sel] += ww
        if parts is not None:
            key = "head" if heads[i] else "body"
            if key not in parts:
                parts[key] = (np.zeros((N, 3)), np.zeros(N))
            parts[key][0][sel] += col * ww[:, None]
            parts[key][1][sel] += ww
        better = w > wmax[sel]
        dom[sel[better]] = i
        wmax[sel] = np.maximum(wmax[sel], w)
    return num / np.maximum(den, 1e-12)[:, None], face.smoothstep(ALPHA_RAMP[0], ALPHA_RAMP[1], wmax), dom


def _trilinear_all(grid: np.ndarray, f: np.ndarray) -> np.ndarray:
    """face._trilinear of every channel of grid (X, Y, Z, c) at once: the corner indices and weights are made once and
    each channel gets the same arithmetic as its own call. Returns (n, c)."""
    dims = np.array(grid.shape[:3])
    f = np.clip(f, 0, dims - 1)
    i0 = np.floor(f).astype(int)
    i1 = np.minimum(i0 + 1, dims - 1)
    t = f - i0
    ny, nz = int(dims[1]), int(dims[2])
    flat = grid.reshape(-1, grid.shape[3])
    out: Any = 0.0
    for dx in (0, 1):
        for dy in (0, 1):
            for dz in (0, 1):
                ix = i1[:, 0] if dx else i0[:, 0]
                iy = i1[:, 1] if dy else i0[:, 1]
                iz = i1[:, 2] if dz else i0[:, 2]
                wgt = (t[:, 0] if dx else 1 - t[:, 0]) * (t[:, 1] if dy else 1 - t[:, 1]) * (t[:, 2] if dz else 1 - t[:, 2])
                out = out + flat.take((ix * ny + iy) * nz + iz, axis=0) * wgt[:, None]
    return out


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
    both = _trilinear_all(np.concatenate([g, frac[..., None]], -1), fc)
    return both[:, :3], np.clip(face.smoothstep(0.03, 0.4, both[:, 3]), 0, 1)


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


FILL_CELLS = (0.032, 0.016, 0.008, 0.004)  # metres: the scales shaded_fill carries painted colour in at, coarse to fine


def _smooth_mean(atlas: Atlas, vals: np.ndarray, wt: np.ndarray, cell: float, passes: int) -> tuple[np.ndarray, np.ndarray]:
    """The weighted mean of vals (N,3) round each texel over a few cells of `cell` metres along the surface, as a
    normalised average: sums and weights are smoothed and interpolated apart and divided last, so empty cells do not
    pull the mean toward black. Returns (mean (N,3), weight round the texel (N), 0 where nothing is painted near)."""
    pos = atlas.pos
    lo = pos.min(0) - 3 * cell
    f = (pos - lo) / cell
    idx = np.floor(f).astype(int)
    dims = tuple(int(d) for d in idx.max(0) + 4)
    flat = np.ravel_multi_index(tuple(idx.T), dims)
    size = int(np.prod(dims))
    sv = [np.bincount(flat, weights=vals[:, c] * wt, minlength=size).reshape(dims) for c in range(3)]
    sw = np.bincount(flat, weights=wt, minlength=size).reshape(dims)
    for _ in range(passes):
        sv = [face._box3(x, 1) for x in sv]
        sw = face._box3(sw, 1)
    fc = f - 0.5
    tri = _trilinear_all(np.stack([*sv, sw], -1), fc)
    w = tri[:, 3]
    mean = tri[:, :3] / np.maximum(w, 1e-12)[:, None]
    return mean, w


FILL_CELLS = (0.032, 0.016, 0.008, 0.004)  # metres: the scales shaded_fill carries painted colour in at, coarse to fine


def shaded_fill(atlas: Atlas, colour: np.ndarray, painted: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The painted colour carried into the texels around it, coarse to fine: at each of FILL_CELLS the mean painted
    colour a few cells round each texel (_smooth_mean), each finer scale taking over as far as it has enough painted
    texels near. Next to painted surface the fill has that surface's own shading and tone a few millimetres in;
    further in, the larger scales blend the borders on either side (the jaw above, the neck below) into each other.
    Returns (colour (N,3), reach (N) 0..1: how much painted surface the coarsest scale found near)."""
    wt = painted.astype(np.float64)
    vals = np.where(painted[:, None], colour, 0.0)
    out, w0 = _smooth_mean(atlas, vals, wt, FILL_CELLS[0], 3)
    reach = np.clip(w0 / max(float(np.percentile(w0[painted], 50)) if painted.any() else 1.0, 1e-9) * 4.0, 0.0, 1.0)
    for cell in FILL_CELLS[1:]:
        m, w = _smooth_mean(atlas, vals, wt, cell, 1)
        full = float(np.percentile(w[painted], 50)) if painted.any() else 1.0
        k = face.smoothstep(0.05, 0.5, w / max(full, 1e-9))[:, None]
        out = out * (1 - k) + m * k
    return np.clip(out, 0.0, 1.0), reach


HOLE_CELL = 0.02          # metres: the scale at which a hole no picture painted counts as small (hole_fill)
HOLE_SHARE = (0.55, 0.75)  # share of the surface round a texel that is painted, over which a hole there is filled


def hole_fill(atlas: Atlas, paint: np.ndarray, alpha: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    """The painted colour round the small holes no picture painted inside painted surface: where painted texels make up
    HOLE_SHARE of the surface within a few HOLE_CELL of a texel, an unpainted texel takes their mean colour (_smooth_mean)
    instead of the 3D step's. The 3D step's own colour kept in a thin unpainted strip showed through: on the live W1 R5
    run the depth-edge gate left the edges of the model's raised bikini strap unpainted on the back, and the strap's
    navy, pulled pale by the colour correction, drew the strap's outline over the skin. Large unpainted areas (a hand
    no view sees clear, the inside of a sleeve) keep the 3D step's colour as before. Returns (share filled (N), colour
    (N,3)) or None."""
    sel = (alpha > 0.5) & ~atlas.is_gut
    if sel.sum() < 100:
        return None
    m, w = _smooth_mean(atlas, np.where(sel[:, None], paint, 0.0), sel.astype(np.float64), HOLE_CELL, 1)
    _, wall = _smooth_mean(atlas, np.zeros_like(paint), (~atlas.is_gut).astype(np.float64), HOLE_CELL, 1)
    frac = w / np.maximum(wall, 1e-9)
    k = face.smoothstep(HOLE_SHARE[0], HOLE_SHARE[1], frac) * (1.0 - alpha) * (w > 1e-6)
    return np.clip(k, 0.0, 1.0), np.clip(m, 0.0, 1.0)


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
        holes = hole_fill(atlas, paint, alpha)
        if holes is not None:
            hk, hc = holes
            out = out * (1 - hk[:, None]) + hc * hk[:, None]
            if out_info is not None:
                out_info["hole_fill"] = hk
    if zt is not None:
        fw = zt.get("fill", zt["face"]) * ~zt["keep"]
        if (fw * (1 - alpha) > 0.5).any():
            out = out * (1 - fw[:, None]) + skin_tone(atlas, kept, paint, alpha, zt) * fw[:, None]
        else:
            fw = np.zeros_like(alpha)
        if out_info is not None:
            out_info["fill"] = fw * (1 - alpha)
        under = zt.get("under")
        if under is not None and (under * (1 - alpha) > 0.5).any():
            # the underside of the chin, which the head views no longer paint at a grazing angle: the painted colour
            # around it (skin under a bare chin, the beard's colour under a beard), never the step 3 colour; carried in
            # from both its borders, the jaw above and the neck below, with their shading (shaded_fill), not as one
            # colour (one colour read as a flat patch with an edge against the face on the live W2-clothed run)
            sel_p = (alpha > 0.5) & ~atlas.is_gut
            hp = zt.get("head_painted")
            if hp is not None and (sel_p & hp).sum() >= 200:
                # its colour from the head views' paint, and from the body fits' only where that is skin: the body fits
                # also paint the collar beside the neck (M1-clothed's green jacket made the fill brown-green, W2-clothed's
                # cream cardigan made it cream)
                head_lab = face.to_lab(paint[sel_p & hp][None])[0]
                skin = np.median(head_lab, axis=0)
                lab = face.to_lab(paint[None])[0]
                skin_like = np.linalg.norm((lab - skin) * np.array([0.5, 1.0, 1.0]), axis=1) < SKIN_TOL
                sel_p = sel_p & (hp | skin_like)
            field, reach = shaded_fill(atlas, paint, sel_p)
            took = under * (reach > 0.05)
            out = out * (1 - took[:, None]) + field * took[:, None]
            if out_info is not None:
                out_info["under_fill"] = took * (1 - alpha)
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
    out["chin"] = {"nose_y": nose_y, "L": L, "centre": [float(head.centre[0]), float(head.centre[2])], "width": float(head.width)}
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


def frame_weight(a: dict[str, Any], size: int) -> np.ndarray:
    """(size, size) weight of a head picture moved by the alignment a (face.apply_alignment): 0 where the moved picture
    takes its colour from outside the picture's own frame or within HEAD_FRAME_MARGIN[0] of its edge, 1 from
    HEAD_FRAME_MARGIN[1] inward, so a head view never paints its picture's outermost rows."""
    cx, cy = a["centre"]
    p = np.arange(size) + 0.5
    qx = cx + (p - cx - float(a["tx"])) / float(a["sx"])
    qy = cy + (p - cy - float(a["ty"])) / float(a["sy"])
    dx = np.minimum(qx, size - qx)
    dy = np.minimum(qy, size - qy)
    d = np.minimum(dy[:, None], dx[None, :])
    return face.smoothstep(HEAD_FRAME_MARGIN[0] * size, HEAD_FRAME_MARGIN[1] * size, d) * (d > 0)


def neck_frame(cam: face.Camera, zone: dict[str, Any]) -> tuple[face.Camera, int]:
    """The head picture's camera grown down until the neck fade (zone["neck"]) lies inside its frame at full weight
    (HEAD_FRAME_MARGIN, HEAD_FRAME_BELOW), keeping its top edge, its pixels per metre and its middle; and how many pixels
    it grew on each side. The face pass's framing (face.head_camera) ends about at the jaw: on the four live round-four
    runs its bottom edge sat 0 to 13 cm above the neck fade's foot, at or above the fade's top, so the head views stopped
    at their frame's edge over 2 cm, at one height all round, and the whole-body fits took over there at once: a hard
    line across the neck (and the hair beside it), with the neck tone match (which works in the fade) never reached.
    The camera as it is, and 0, when the frame already reaches."""
    y0 = float(zone["neck"][0])
    top = float(cam.target[1]) + 0.5 * cam.scale
    need = (top - y0 + HEAD_FRAME_BELOW) / (1.0 - HEAD_FRAME_MARGIN[1])
    if need <= cam.scale:
        return cam, 0
    px = cam.scale / cam.size
    n = int(np.ceil((need - cam.scale) / (2.0 * px)))
    d = n * px
    return face.Camera(target=np.asarray(cam.target, dtype=np.float64) - np.array([0.0, d, 0.0]), yaw_deg=cam.yaw_deg,
                       scale=cam.scale + 2.0 * d, size=cam.size + 2 * n), n


def make_head_view(mesh: dict[str, np.ndarray], name: str, cam: face.Camera, render: dict[str, Any], crop: dict[str, Any],
                   a: dict[str, Any], zone: dict[str, Any], body_px_m: float, pic_n0: int,
                   reframe: Callable[[face.Camera], tuple[dict[str, Any] | None, dict[str, Any]]] | None = None) -> View:
    """A head view: the picture's head (crop, framed by face.painted_head for cam) moved by the alignment a, painting the
    head above the neck (zone["neck"], HEAD_NECK), with the depth-edge and stretch gates as the body's views have them.
    With reframe (the crop and the model's render for another camera), the picture is framed again on neck_frame's
    taller camera, so it reaches down the whole neck fade; the fit a, found on the face pass's framing, moves with the
    frame (the same pixels per metre, its middle as many pixels across as the frame grew on each side)."""
    if reframe is not None and zone.get("neck") is not None:
        cam2, n = neck_frame(cam, zone)
        if n > 0:
            crop2, render2 = reframe(cam2)
            if crop2 is not None:
                cam, crop, render = cam2, crop2, render2
                a = {**a, "centre": (float(a["centre"][0]) + n, float(a["centre"][1]))}
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
    v.zone_img = face.smoothstep(y0, y1, pos[..., 1]) * render["mask"] * frame_weight(a, S)
    v.align = dict(a)  # the fit that placed the picture (checks read it)
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
             prep: Callable[..., tuple[np.ndarray, np.ndarray]] = prepare_picture,
             tex: np.ndarray | None = None) -> tuple[View | None, dict[str, Any]]:
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
        v = make_head_view(mesh, HEAD_VIEW, cam, view, crop, a, zone, body_px_m, pic.shape[0], None if tex is None else
                           lambda c, pic=pic, alpha=alpha: (face.painted_head(np.concatenate([pic, alpha[..., None]], axis=-1),
                                                                               head, c, size=c.size), face.render_view(mesh, tex, c)))
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

    from . import facemarks

    if facemarks.finder()["finder"] == "none":
        return refuse("no face finder: " + facemarks.finder().get("missing", "none was set up"))
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
    v = make_head_view(mesh, rec["name"], cam, render, crop, a, zone, body_px_m, pic.shape[0],
                       lambda c: (face.painted_head(np.concatenate([pic, alpha[..., None]], axis=-1), head, c, size=c.size),
                                  face.render_view(mesh, tex, c)))
    rec.update(fit={kk: a.get(kk) for kk in ("sx", "tx", "ty")}, head_px=crop.get("head_px"))
    rec["ear"] = ear_onto_model(v)
    rec["outline"] = head_outline_match(v, zone)  # a record only: how well its head lies on the model's
    v.info = rec
    if log:
        log(f"view {rec['name']}: yaw {yaw:.1f}, lined up to {rec['lined_up_to']}, residual {rec['residual']:.3f}")
    return v, rec


def pose_points(img: np.ndarray, mask: np.ndarray) -> dict[str, tuple[float, float, float]] | None:
    """The nose, both eyes and both ears of the one figure in img (the pose landmarker, buildpose's), as (x, y, visibility)
    in img's pixels, the figure shown on grey outside mask; None without the finder or a figure."""
    from . import buildpose

    model = buildpose._model("pose")
    if model is None:
        return None
    import mediapipe as mp

    S = img.shape[0]
    pic = np.where(mask[..., None], np.clip(img, 0.0, 1.0), 0.5)
    small = np.asarray(Image.fromarray((pic * 255).astype(np.uint8)).resize((EAR_POSE_N, EAR_POSE_N), Image.BILINEAR))
    r = model.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(small)))
    if not r.pose_landmarks:
        return None
    lm = r.pose_landmarks[0]
    k = S / float(EAR_POSE_N)
    return {n: (lm[i].x * EAR_POSE_N * k, lm[i].y * EAR_POSE_N * k, float(lm[i].visibility))
            for n, i in (("nose", 0), ("eye_a", 2), ("eye_b", 5), ("ear_L", 7), ("ear_R", 8))}


def near_ear(pts: dict[str, tuple[float, float, float]]) -> str | None:
    """Which ear a turned face shows: of the ears seen (visibility over a half), the one on the other side of the eyes'
    middle from the nose (the nose points away from the near ear), the further out if both are; None when neither is."""
    mid_x = 0.5 * (pts["eye_a"][0] + pts["eye_b"][0])
    s = np.sign(pts["nose"][0] - mid_x)
    seen = [n for n in ("ear_L", "ear_R") if pts[n][2] > 0.5 and (pts[n][0] - mid_x) * s < 0]
    return max(seen, key=lambda n: abs(pts[n][0] - mid_x)) if seen else None


def skin_share(img: np.ndarray, mask: np.ndarray, at: np.ndarray, pins: np.ndarray, px_per_cm: float) -> float:
    """The share of img's figure (mask) within EAR_SKIN_CM of `at` whose colour lies within EAR_SKIN_DE (Lab) of the
    head's own skin: the median of the figure within 0.6 of the eyes' distance of the middle of its eyes and mouth
    (pins: two eyes, then the mouth). 0 when too little of the figure is there."""
    lab_img = face.to_lab(np.clip(img, 0.0, 1.0))
    S = img.shape[0]
    yy, xx = np.mgrid[0:S, 0:S]
    c = np.asarray(pins, dtype=np.float64).mean(axis=0)
    eyes = float(np.hypot(*(np.asarray(pins[0]) - np.asarray(pins[1]))))
    face_px = (np.hypot(xx + 0.5 - c[0], yy + 0.5 - c[1]) < 0.6 * eyes) & mask
    ring = (np.hypot(xx + 0.5 - at[0], yy + 0.5 - at[1]) < EAR_SKIN_CM * px_per_cm) & mask
    if face_px.sum() < 20 or ring.sum() < 20:
        return 0.0
    skin = np.median(lab_img[face_px], axis=0)
    return float((np.linalg.norm(lab_img[ring] - skin, axis=1) < EAR_SKIN_DE).mean())


def ear_move_maps(size: int, ear_from: np.ndarray, ear_to: np.ndarray, pins: np.ndarray,
                  px_per_cm: float) -> tuple[np.ndarray | None, np.ndarray | None, dict[str, Any]]:
    """The backward maps (for a remap of the picture) that move the picture's ear at ear_from onto ear_to: in full
    within EAR_MOVE_FULL_CM of ear_to, fading to nothing EAR_MOVE_FADE of the way to the nearest pin (the eyes and mouth,
    which keep their place). (None, None, why) when the ears are under EAR_MOVE_MIN_CM apart or a pin is too close."""
    d = np.asarray(ear_to, dtype=np.float64) - np.asarray(ear_from, dtype=np.float64)
    off = float(np.hypot(*d)) / px_per_cm
    rec: dict[str, Any] = {"offset_cm": round(off, 2)}
    if off < EAR_MOVE_MIN_CM:
        return None, None, {**rec, "moved": False, "why": f"the ears are {off:.1f} cm apart"}
    r_full = EAR_MOVE_FULL_CM * px_per_cm
    r_zero = EAR_MOVE_FADE * float(np.min(np.hypot(*(np.asarray(pins, dtype=np.float64) - ear_to).T)))
    if r_zero <= r_full:
        return None, None, {**rec, "moved": False, "why": "the face is too close to the ear"}
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    w = 1.0 - face.smoothstep(r_full, r_zero, np.hypot(xx + 0.5 - ear_to[0], yy + 0.5 - ear_to[1]))
    gy, gx = np.gradient(w)
    rec.update(moved=True, fade_cm=round(r_zero / px_per_cm, 2), stretch=round(1.0 + float(np.hypot(gx, gy).max() * np.hypot(*d)), 2))
    return (xx - d[0] * w).astype(np.float32), (yy - d[1] * w).astype(np.float32), rec


def ear_onto_model(v: View) -> dict[str, Any]:
    """Move a three-quarter head view's picture round its own near ear onto the model's ear (EAR_MOVE_*), its eyes and
    mouth held where the fit put them; the gates are set again for the moved picture. Returns the record."""
    import cv2

    S = v.cam.size
    pp = pose_points(v.pic_aligned, v.alpha_img > 0.5)
    if pp is None:
        return {"moved": False, "why": "no pose finder or no figure on the picture"}
    side = near_ear(pp)
    if side is None:
        return {"moved": False, "why": "the picture's near ear was not seen"}
    pm = pose_points(v.render, v.raster.mask)
    if pm is None or pm[side][2] <= 0.5:
        return {"ear": side, "moved": False, "why": "the model's ear was not seen"}
    pmask = v.alpha_img > 0.5
    pins = head_points(np.where(pmask[..., None], v.pic_aligned, 0.5))
    if pins is None:
        return {"ear": side, "moved": False, "why": "the picture's eyes and mouth were not found"}
    pins_m = head_points(np.where(v.raster.mask[..., None], v.render, 0.5))
    if pins_m is None:
        return {"ear": side, "moved": False, "why": "the model's eyes and mouth were not found"}
    px_cm = S / (v.cam.scale * 100.0)
    ear_p, ear_m = np.array(pp[side][:2]), np.array(pm[side][:2])
    seen = {"picture": round(skin_share(v.pic_aligned, pmask, ear_p, pins, px_cm), 2),
            "model": round(skin_share(v.render, v.raster.mask, ear_m, pins_m, px_cm), 2)}
    if min(seen.values()) < EAR_SKIN_SHARE:
        return {"ear": side, "skin_share": seen, "moved": False, "why": "no ear shows at the finder's point"}
    mx, my, rec = ear_move_maps(S, ear_p, ear_m, pins, px_cm)
    rec = {"ear": side, "skin_share": seen, **rec}
    if mx is None:
        return rec
    v.pic_aligned = v.pic_affine = np.clip(np.stack(
        [cv2.remap(v.pic_aligned[..., c].astype(np.float32), mx, my, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
         for c in range(3)], -1), 0.0, 1.0).astype(np.float64)
    v.alpha_img = cv2.remap(v.alpha_img.astype(np.float32), mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                            borderValue=0).astype(np.float64)
    v.valid = v.raster.mask & (v.alpha_img > 0.5)
    r = max(2, S // 340)
    v.feather = v.feather0 = np.clip(face.blur(face.erode(v.valid, r).astype(np.float64), r * 0.8) / 0.9, 0.0, 1.0)
    prepare_gates(v, {"disc": True, "stretch": True, "agree": False, "flowsat": False})
    return rec


def head_outline_match(v: View, zone: dict[str, Any]) -> float | None:
    """How well a head view's picture, moved by its fit, lies on the model's head from its camera: intersection over
    union of the picture's cut-out and the model's outline, above the chin (from the zone's chin, so the neck and the
    shoulders, which the head frame shows differently in every picture, do not count). None without a chin. Recorded,
    not acted on: the live M1 R4 +40 view that painted a second ear and cheek had 0.74, every other stored
    three-quarter head 0.71 (M1 R3, a second jaw line) and 0.82 to 0.94; refusing a view under 0.8 left the cheek to
    worse paint on M1 and harmed W2-clothed."""
    ch = zone.get("chin")
    if ch is None:
        return None
    S = v.cam.size
    top = float(v.cam.target[1]) + 0.5 * v.cam.scale
    rows = int(np.clip((top - (float(ch["nose_y"]) - 0.65 * float(ch["L"]))) / v.cam.scale * S, 1, S))
    a, m = v.alpha_img[:rows] > 0.5, v.raster.mask[:rows]
    union = int((a | m).sum())
    return round(float((a & m).sum() / union), 3) if union else None


def side_head_yaw(name: str, body: View | None, head_yaw: float) -> tuple[float | None, str]:
    """The yaw a three-quarter view's head is framed from: its whole-body fit's turn (also when that fit was refused for
    its outline match, which says nothing about the head), else SIDE_HEAD_YAW to its usual side. None when its turn was
    refused as contradicting the front (item 8), or when the whole-body turn is not SIDE_HEAD_TURN from the face: that
    side keeps to today's painting. Framing from the head's own turn instead (308164e) tried the second side of the
    stored W1-clothed run at 73 degrees and left a seam on its far cheek; the picture's own turn, from its eyes and
    mouth against the front picture's, is about 36 degrees there, so framing from it would not have moved the view."""
    if body is not None and "contradicts" in str(body.info.get("refused")):
        return None, "its turn contradicts the front"
    if body is not None and body.info.get("yaw") is not None:
        y = float(body.info["yaw"])
        rel = abs(_signed(y - head_yaw))
        if not SIDE_HEAD_TURN[0] <= rel <= SIDE_HEAD_TURN[1]:
            return None, f"its whole-body fit is {rel:.0f} degrees from the face"
        return y, "its whole-body fit"
    return float(head_yaw + TURN_SIDE[name] * SIDE_HEAD_YAW), "its usual side"


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
    cosv = cam.cos_to_camera(atlas.nrm, atlas.pos)
    tol = 2.5 * (cam.scale / S) / np.maximum(np.abs(cosv), 0.15)
    inb = (sp[:, 0] >= 0) & (sp[:, 0] < S) & (sp[:, 1] >= 0) & (sp[:, 1] < S)
    z = zone["z"][yi, xi]
    vis = inb & np.isfinite(z) & (sd <= np.where(np.isfinite(z), z, -np.inf) + tol) & (cosv > 0.0)
    w = _sample(zone["weight"][..., None], sp[:, 0], sp[:, 1])[:, 0] * vis
    keep = zone["keep"][yi, xi] & vis if zone.get("keep") is not None else np.zeros(w.shape, dtype=bool)
    out = {"face": np.clip(w, 0.0, 1.0), "keep": keep}
    if zone.get("fill") is not None:
        out["fill"] = np.clip(_sample(zone["fill"][..., None], sp[:, 0], sp[:, 1])[:, 0] * vis, 0.0, 1.0)
    if zone.get("chin") is not None:
        out["under"] = chin_underside(atlas, zone["chin"])
    if zone.get("neck") is not None:  # above the head views' neck fade: there they keep the body fits off (blend)
        out["above_neck"] = face.smoothstep(0.9 * zone["neck"][1] + 0.1 * zone["neck"][0], zone["neck"][1], atlas.pos[:, 1])
    return out


def chin_underside(atlas: Atlas, chin: dict[str, Any]) -> np.ndarray:
    """(N,) 0..1: the underside of the chin and jaw and the upper neck below it (CHIN), the surface no level camera sees
    squarely: between CHIN[0] and CHIN[1] of (nose tip to top of head) below the nose tip, within CHIN[2] of the head's
    width of its middle, facing down. From the surface alone, so it is the same on any texture layout."""
    y = atlas.pos[:, 1]
    ny, L = float(chin["nose_y"]), float(chin["L"])
    band = face.smoothstep(ny - CHIN[0] * L - 0.1 * L, ny - CHIN[0] * L, y) * (1.0 - face.smoothstep(ny - CHIN[1] * L, ny - CHIN[1] * L + 0.1 * L, y))
    cx, cz = chin["centre"]
    near = 1.0 - face.smoothstep(CHIN[2] * chin["width"], (CHIN[2] + 0.1) * chin["width"], np.hypot(atlas.pos[:, 0] - cx, atlas.pos[:, 2] - cz))
    down = face.smoothstep(0.2, 0.45, -atlas.nrm[:, 1])
    return np.clip(band * near * down, 0.0, 1.0)


def neck_tone_match(atlas: Atlas, parts: dict[str, Any], zone: dict[str, Any], paint: np.ndarray) -> np.ndarray:
    """The blend again, with the whole-body fits' colour shifted toward the head views' in the band where they hand over
    (docs: neck seams, part d). The shift is the head views' colour minus the body fits', where both paint, spread
    smoothly along the surface (surface_correction, a few centimetres: low frequencies only, so no feature moves), and
    applied only in the band: from half the neck fade's height below its bottom up to a third above its top, within the
    head's width of its middle (not the chest, not the shoulders). Everywhere else `paint` (the blend) is kept as it is."""
    (nh, dh), (nb, db) = parts["head"], parts["body"]
    col_h = nh / np.maximum(dh, 1e-12)[:, None]
    col_b = nb / np.maximum(db, 1e-12)[:, None]
    y0, y1 = zone["neck"]
    span = max(y1 - y0, 1e-3)
    y = atlas.pos[:, 1]
    band = face.smoothstep(y0 - 0.5 * span, y0, y) * (1.0 - face.smoothstep(y1, y1 + 0.33 * span, y))
    cx, cz = zone["chin"]["centre"]
    width = float(zone["chin"]["width"])
    band = band * (1.0 - face.smoothstep(0.8 * width, 1.0 * width, np.hypot(atlas.pos[:, 0] - cx, atlas.pos[:, 2] - cz)))
    both = (dh > 1e-9) & (db > 1e-9) & (band > 0)
    if both.sum() < 50:
        return paint
    wgt = np.where(both, np.minimum(dh, db) / np.maximum(dh + db, 1e-12), 0.0)
    shift, reach = surface_correction(atlas, np.where(both[:, None], col_h - col_b, 0.0), wgt)
    k = reach * band
    touch = (k > 1e-6) & (db > 1e-9)
    out = paint.copy()
    cb = np.clip(col_b[touch] + shift[touch] * k[touch, None], 0.0, 1.0)
    out[touch] = (nh[touch] + cb * db[touch, None]) / np.maximum(dh[touch] + db[touch], 1e-12)[:, None]
    return out


HEAD_TONE_MIN = 500       # skin texels a three-quarter head view must share with the front head view for its tone to be matched
HEAD_TONE_SKIN = 20.0     # Lab distance (lightness half) from the front's face colour that counts as skin for that match
HEAD_TONE_MAX = 0.12      # the largest shift per channel (0..1) the match may give


def head_tone_offsets(atlas: Atlas, views: list[View], projs: list[dict[str, np.ndarray]],
                      images: list[np.ndarray]) -> tuple[list[np.ndarray | None], dict[str, Any]]:
    """For each three-quarter head view, the one colour offset that makes its skin agree with the front head view's
    where both paint the same skin (owner's suggestion, round four). The three-quarter pictures are drawn separately
    and come out lighter or darker than the photo: on the live W2 runs the left one 6 to 9 L lighter where the two
    overlap, which showed as a pale cheek beside the front's. Fitted on skin only (both colours within HEAD_TONE_SKIN of
    the front's face colour, so hair, glasses and collar do not pull it) and applied to the whole view: one offset
    left no gradient across the cheek, where a field fading out from the overlap left the far cheek pale (W2 R4) and a
    gain moved the clothing's colour as well (M1 R4's jacket went brown). Returns (offset per view or None, record)."""
    names = [v.name for v in views]
    out: list[np.ndarray | None] = [None] * len(views)
    rec: dict[str, Any] = {}
    if HEAD_VIEW not in names or getattr(atlas, "zone", None) is None:
        return out, rec
    f = names.index(HEAD_VIEW)
    N = atlas.idx.size
    pf = projs[f]
    cf_all, wf = np.zeros((N, 3)), np.zeros(N)
    cf_all[pf["sel"]] = catmull(images[f], pf["sp"][:, 0].reshape(-1, 1), pf["sp"][:, 1].reshape(-1, 1))[:, 0]
    wf[pf["sel"]] = pf["w"]
    face_t = (atlas.zone["face"] > 0.5) & (wf > 0.05)
    if face_t.sum() < HEAD_TONE_MIN:
        return out, rec
    mid = np.median(face.to_lab(cf_all[face_t][None])[0], axis=0)
    lw = np.array([0.5, 1.0, 1.0])
    for i, n in enumerate(names):
        if i == f or not n.startswith(HEAD_VIEW):
            continue
        p = projs[i]
        cs = catmull(images[i], p["sp"][:, 0].reshape(-1, 1), p["sp"][:, 1].reshape(-1, 1))[:, 0]
        cf = cf_all[p["sel"]]
        ov = ((wf[p["sel"]] > 0.05) & (p["w"] > 0.05) & ~atlas.is_gut[p["sel"]]
              & (np.linalg.norm((face.to_lab(cf[None])[0] - mid) * lw, axis=1) < HEAD_TONE_SKIN)
              & (np.linalg.norm((face.to_lab(cs[None])[0] - mid) * lw, axis=1) < HEAD_TONE_SKIN))
        if ov.sum() < HEAD_TONE_MIN:
            rec[n] = {"overlap": int(ov.sum()), "matched": False}
            continue
        off = np.clip((cf[ov] - cs[ov]).mean(0), -HEAD_TONE_MAX, HEAD_TONE_MAX)
        out[i] = off
        rec[n] = {"overlap": int(ov.sum()), "matched": True, "offset": [round(float(x), 4) for x in off]}
    return out, rec


JOIN_BLEND = True         # each whole-body view's colour levelled to the views before it by a smooth field fitted on the overlap, carried only a few centimetres from the joins
TONE_SEEN = 0.05          # raw weight over which a view counts as seeing a texel for the match
TONE_MIN = 500            # texels two views must share (same material) for a view's tone to be matched
TONE_GUARD = (12.0, 40.0)  # Lab distance in (a, b) and in L above which a texel is not the same material in both views
JOIN_CELL = 0.012         # metres: the surface cell the join field is smoothed at
JOIN_MAX = 0.2            # the largest shift per channel the join field may give
JOIN_RES = 0.08           # a texel whose difference is further than this from the first field is dropped and the field made again


def _guarded(ref: np.ndarray, col: np.ndarray) -> np.ndarray:
    """Which of the shared texels hold the same material in both views: the two colours within TONE_GUARD in Lab (a, b and L
    apart), so a hair edge lying over skin in one view and not the other is not read as a difference of tone."""
    d = face.to_lab(ref[None])[0] - face.to_lab(col[None])[0]
    return (np.linalg.norm(d[:, 1:], axis=1) < TONE_GUARD[0]) & (np.abs(d[:, 0]) < TONE_GUARD[1])


def tone_adjust(atlas: Atlas, views: list[View], projs: list[dict[str, np.ndarray]],
                layers: list[dict[str, np.ndarray]]) -> tuple[list[np.ndarray], dict[str, Any]]:
    """The colour each view paints with once its tone agrees with the views before it where they see the same surface
    (WP-50 trial B, JOIN_BLEND). Head views stay as they are (head_tone_offsets and neck_tone_match have them) and so does the first
    whole-body view, the person's own picture. Each other whole-body view, in order, is compared with the weighted mean
    of the views already settled, over the texels both see (raw weight over TONE_SEEN) that hold the same material in
    both (_guarded); its colour is moved by a smooth surface field (surface_correction) that is nothing beyond a few
    centimetres from those texels, so what is away from the joins is not touched. A single gain or offset per view was
    tried first and rejected: it recoloured the back of the live M1 run and failed the paint harness on W1 R5. Returns
    (colour per view, record)."""
    names = [v.name for v in views]
    cols = [l["col"] for l in layers]
    N = atlas.idx.size
    rn, rw = np.zeros((N, 3)), np.zeros(N)
    first = next((i for i, n in enumerate(names) if not n.startswith(HEAD_VIEW)), None)
    rec: dict[str, Any] = {}

    def settle(i: int) -> None:
        sel, w = projs[i]["sel"], projs[i]["w"] ** 2
        rn[sel] += cols[i] * w[:, None]
        rw[sel] += w

    for i, n in enumerate(names):
        if n.startswith(HEAD_VIEW) or i == first:
            settle(i)
    for i, n in enumerate(names):
        if n.startswith(HEAD_VIEW) or i == first:
            continue
        sel, w = projs[i]["sel"], projs[i]["w"]
        m = (w > TONE_SEEN) & (rw[sel] > TONE_SEEN ** 2) & ~atlas.is_gut[sel]
        if m.sum() >= TONE_MIN:
            ref = rn[sel[m]] / rw[sel[m]][:, None]
            ok = _guarded(ref, cols[i][m])
        else:
            ok = np.zeros(0, bool)
        if ok.sum() < TONE_MIN:
            rec[n] = {"shared": int(ok.sum()), "matched": False}
            settle(i)
            continue
        col = cols[i]
        r: dict[str, Any] = {"shared": int(ok.sum()), "matched": True}
        ref_ok, at = ref[ok], np.flatnonzero(m)[ok]
        delta = np.zeros((N, 3))
        alpha = np.zeros(N)
        tex = sel[at]
        delta[tex] = ref_ok - col[at]
        alpha[tex] = np.clip(w[at] / 0.3, 0.0, 1.0)
        corr, reach = surface_correction(atlas, delta, alpha, JOIN_CELL)
        far = np.abs(delta[tex] - corr[tex]).max(1) > JOIN_RES
        if far.any() and (~far).sum() >= TONE_MIN:
            alpha[tex[far]] = 0.0
            corr, reach = surface_correction(atlas, delta, alpha, JOIN_CELL)
        fld = np.clip(corr[sel], -JOIN_MAX, JOIN_MAX) * reach[sel][:, None]
        col = np.clip(col + fld, 0.0, 1.0)
        r["field_median"] = round(float(np.median(np.abs(fld).max(1))), 4)
        r["field_texels"] = int((np.abs(fld).max(1) > 0.005).sum())
        cols[i] = col
        rec[n] = r
        settle(i)
    return cols, rec


NECK_HANDOVER_MIN = 0.02  # metres: the least height over which the neck's paint should pass from the head views to the body fits


def neck_handover(atlas: Atlas, parts: dict[str, Any], zone: dict[str, Any]) -> dict[str, Any] | None:
    """How the front of the neck passes from the head views' paint to the whole-body fits': in 5 mm steps of height,
    the head views' share of the paint on the neck's front (facing the face, within 0.7 of the head's width of its
    middle), and the height between where that share falls through 0.9 and through 0.1 ("span_m"), with the colour step
    (Lab distance between the head views' and the body fits' paint) where both paint there ("step"). A span under
    NECK_HANDOVER_MIN is a line across the neck, at one height all round: the live round-four runs had 0.5 to 1.5 cm,
    from head pictures that ended at their frame above the neck fade, and showed it as pale and dark blocks under the
    chin. None when the head views or the body fits paint none of the neck."""
    if "head" not in parts or "body" not in parts or not zone or zone.get("neck") is None or zone.get("chin") is None:
        return None
    (nh, dh), (nb, db) = parts["head"], parts["body"]
    ch = zone["chin"]
    y = atlas.pos[:, 1]
    fwd = np.array([np.sin(np.radians(float(zone["cam"].yaw_deg))), 0.0, np.cos(np.radians(float(zone["cam"].yaw_deg)))])
    r = np.hypot(atlas.pos[:, 0] - ch["centre"][0], atlas.pos[:, 2] - ch["centre"][1])
    y0, y1 = (float(v) for v in zone["neck"])
    top = float(ch["nose_y"]) - 0.5 * float(ch["L"])  # from the chin
    front = (r < 0.7 * ch["width"]) & (atlas.nrm @ fwd > 0.5) & ~atlas.is_gut & ((dh + db) > 1e-9)
    share, hs = [], []
    for yb in np.arange(top, y0 - 0.06, -0.005):
        s_ = front & (y <= yb) & (y > yb - 0.005)
        if s_.sum() < 10:
            continue
        hs.append(yb - 0.0025)
        share.append(float(np.median(dh[s_] / (dh[s_] + db[s_]))))
    if len(share) < 3 or max(share) < 0.9 or min(share) > 0.1:
        return None
    share_, hs_ = np.array(share), np.array(hs)
    hi = hs_[np.flatnonzero(share_ >= 0.9)[-1]] if (share_ >= 0.9).any() else hs_[0]
    lo_i = np.flatnonzero((share_ <= 0.1) & (hs_ < hi))
    lo = hs_[lo_i[0]] if lo_i.size else hs_[-1]
    both = front & (dh > 1e-9) & (db > 1e-9) & (y <= hi + 0.005) & (y >= lo - 0.005)
    step = None
    if both.sum() >= 10:
        ch_ = face.to_lab((nh[both] / dh[both][:, None])[None])[0]
        cb_ = face.to_lab((nb[both] / db[both][:, None])[None])[0]
        step = round(float(np.median(np.linalg.norm(ch_ - cb_, axis=1))), 1)
    return {"span_m": round(float(hi - lo), 4), "at_m": round(float(0.5 * (hi + lo)), 3), "step": step,
            "fade_m": [round(y0, 3), round(y1, 3)], "ok": bool(hi - lo >= NECK_HANDOVER_MIN)}


def neck_checks(m: dict[str, Any]) -> list[dict[str, Any]]:
    """neck_join (warn) for the finish card, from the picture painting's neck_handover record: the neck's paint passes
    from the head views to the body fits over less than NECK_HANDOVER_MIN, at one height all round, so a line or band
    can show across the neck (the live round-four runs before the head pictures reached down the neck fade). Nothing
    when the painting did not record it."""
    from . import quality

    nh = m.get("srcproject_neck_handover")
    if not isinstance(nh, dict) or nh.get("span_m") is None:
        return []
    return [quality.check("neck_join", bool(nh.get("ok")), round(float(nh["span_m"]) * 100, 1),
                          round(NECK_HANDOVER_MIN * 100, 1), vars={"step": nh.get("step")})]


def compose(atlas: Atlas, views: list[View], images: list[np.ndarray], n_primary: int, tex: np.ndarray,
            zone: dict[str, Any] | None = None) -> dict[str, Any]:
    """Project the fitted views onto this layout, blend them and finish the atlas, with the face region when there is
    one. Returns {projs, paint, alpha, dom, base_color, zone (zone_texels), fill}."""
    atlas.zone = zone_texels(atlas, zone)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        projs = list(pool.map(lambda v: project_view(atlas, v), views))
    parts: dict[str, Any] = {}
    shifts, tone_rec = head_tone_offsets(atlas, views, projs, images)
    heads_ = [v.name.startswith(HEAD_VIEW) for v in views]
    layers: list[dict[str, np.ndarray]] | None = [] if JOIN_BLEND else None
    paint_, alpha_, dom = blend(atlas, projs, images, n_primary, heads_, parts, shifts, layers=layers)
    join_rec = None
    if layers is not None:  # the views' colours matched where they meet, then blended again with the same weights
        cols, join_rec = tone_adjust(atlas, views, projs, layers)
        parts = {}
        paint_, alpha_, dom = blend(atlas, projs, images, n_primary, heads_, parts, shifts, colours=cols)
    handover = neck_handover(atlas, parts, zone) if zone and zone.get("found") else None
    if "head" in parts and "body" in parts and zone and zone.get("neck") and zone.get("chin"):
        paint_ = neck_tone_match(atlas, parts, zone, paint_)
    extra: dict[str, Any] = {}
    if atlas.zone is not None:  # which texels the head views painted (the chin fill's colour comes from them, see finish)
        hv = np.array([v.name.startswith(HEAD_VIEW) for v in views] + [False])
        atlas.zone["head_painted"] = hv[np.where(dom >= 0, dom, len(views))]
    new = finish(atlas, tex, paint_, alpha_, atlas.zone, extra)
    return {"projs": projs, "paint": paint_, "alpha": alpha_, "dom": dom, "base_color": new, "zone": atlas.zone,
            "fill": extra.get("fill"), "under_fill": extra.get("under_fill"), "hole_fill": extra.get("hole_fill"), "neck_handover": handover, "head_tone": tone_rec, "join_blend": join_rec}


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
    return {k: zone.get(k) for k in ("found", "cam", "weight", "z", "keep", "fill", "chin", "neck")}


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


def front_half(yaws: list[float], front_yaw: float, half: float = FRONT_HALF) -> list[float]:
    """The yaws within `half` degrees of the front, as signed angles about it (the turn search reads them as camera yaws)."""
    return [float(front_yaw + d) for d in sorted({_signed(float(y) - front_yaw) for y in yaws}) if abs(d) <= half]


def front_view_yaws(head_yaw: float, front_known: bool) -> list[float]:
    """The turns the drawn front view is fitted at. It is drawn from the front, so once the clean-up has decided which way
    the model faces (front_known) it is fitted there only. One turn, not a window round it: fit_view refines a search of
    several turns by up to 5 degrees past the best of them."""
    return [float(head_yaw)] if front_known else list(FRONT_YAWS)


def front_turn(fitted: dict[str, View], head_yaw: float = 0.0) -> tuple[float, str]:
    """The camera yaw the model's front is seen from, and what says so: the first frontal picture that fitted (the
    original, the drawn front view, or the built-from picture when it is a front view), else the head's turn."""
    for name in ("source", "front", "picture"):
        v = fitted.get(name)
        if v is not None and v.ok and (name != "picture" or v.info.get("front_view")):
            return float(v.info["yaw"]), name
    return float(head_yaw), "head"


def picture_contradicts_front(picture_view: View, front_yaw: float) -> str | None:
    """Why the picture the model was built from is refused for its turn, or None: a three-quarter picture (not a front
    view) fitted more than PICTURE_MAX_TURN from the front. Its outline can fit as well from behind when the model's
    colour cannot tell the turns apart, and the turn hint can agree (a rerun that started from an already painted texture
    put a three-quarter picture at 150 degrees, onto the back). On the stored runs it refuses only M1-shorts' (135
    degrees, on a model that faced backwards: it painted the drawn view over the front)."""
    if not picture_view.ok or picture_view.info.get("front_view") or picture_view.info.get("yaw") is None:
        return None
    rel = abs(_signed(float(picture_view.info["yaw"]) - front_yaw))
    if rel > PICTURE_MAX_TURN:
        return f"its turn contradicts the front: the picture the model was built from fits {rel:.0f} degrees from the front"
    return None


def refuse_contradicting_turns(fitted: dict[str, View], front_yaw: float, picture_view: View | None = None,
                               built_from: str | None = None) -> dict[str, str]:
    """Refuse the drawn views whose fitted turn contradicts the front (docs/AFTER-TEST-CHANGES.md D2 item 8): a back view
    fitted less than BACK_MIN_TURN from the front, a full side view less than SIDE_MIN_TURN from it, and of two views
    that belong on opposite sides (left and right; the two three-quarter views) but were fitted on the same side, the
    one on the side its partner belongs on. The three-quarter views take their sides from the picture the model was
    built from when that is a three-quarter view itself, 10 to 80 degrees from the front (the model was built from it, so
    its side is the model's): the view it was drawn as (built_from, "quarter" unless the model was built from the second)
    takes the picture's side, the other three-quarter view the other side.
    Each refused view gets "refused" and "turn_from_front_deg" in its info and ok False. Returns {view: reason}."""
    out: dict[str, str] = {}
    rel = {n: _signed(v.info["yaw"] - front_yaw) for n, v in fitted.items() if v.ok and n in TURN_VIEWS and "yaw" in v.info}
    side = dict(TURN_SIDE)
    anchored = False
    if picture_view is not None and picture_view.ok and not picture_view.info.get("front_view"):
        pr = _signed(picture_view.info["yaw"] - front_yaw)
        if 10.0 <= abs(pr) <= 80.0:
            sgn = 1 if pr > 0 else -1
            mine = built_from if built_from in QUARTERS else "quarter"
            side[mine], side[other_quarter(mine)] = sgn, -sgn
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


# --- WP-61 part D2: a picture that fits at more than one pose is refused only when a far rival paints it differently ---

D2_THRESHOLD = 37.0   # Lab: the largest disagreement over the far rivals that still lets the best pose paint (part_d2_rule.md)
D2_MARGIN = 0.01      # search score within which a candidate pose is a rival to the best one
D2_FAR_DEG = 15.0     # turn or tilt further than this from the best pose: a far rival
D2_N = FIT_N          # the size the disagreement is measured at


def _angdiff(a: float, b: float) -> float:
    return abs((float(a) - float(b) + 180.0) % 360.0 - 180.0)


def _texels_painted(atlas: "Atlas", mesh: dict[str, np.ndarray], lab: np.ndarray, valid: np.ndarray,
                    centre: np.ndarray, scale: float, pose: tuple[float, float, float | None]) -> tuple[np.ndarray, np.ndarray]:
    """The texels a pose sees squarely (the picture's pixel at each texel's screen position is what the pose paints)."""
    turn, tilt, lens = pose
    cam = _camera(centre, turn, scale, D2_N, tilt, lens)
    ras = Raster(mesh, cam)
    sp_, sd = cam.project(atlas.pos)
    inb = (sp_[:, 0] >= 0) & (sp_[:, 0] < D2_N) & (sp_[:, 1] >= 0) & (sp_[:, 1] < D2_N)
    xi = np.clip(sp_[:, 0].astype(int), 0, D2_N - 1)
    yi = np.clip(sp_[:, 1].astype(int), 0, D2_N - 1)
    cosv = cam.cos_to_camera(atlas.nrm, atlas.pos)
    tol = 2.5 * (cam.scale / D2_N) / np.maximum(np.abs(cosv), 0.15)
    vis = inb & (sd <= ras.z[yi, xi] + tol) & (ras.fcos[yi, xi] > 0.05) & valid[yi, xi]
    return vis, lab[yi, xi]


def pose_disagreement(atlas: "Atlas", mesh: dict[str, np.ndarray], lab: np.ndarray, valid: np.ndarray,
                      centre: np.ndarray, scale: float, a: tuple[float, float, float | None],
                      b: tuple[float, float, float | None]) -> tuple[float, float]:
    """(median Lab distance over the texels both poses see, share of the texels only one of them sees)."""
    va, la = _texels_painted(atlas, mesh, lab, valid, centre, scale, a)
    vb, lb = _texels_painted(atlas, mesh, lab, valid, centre, scale, b)
    both = va & vb
    only = float((va ^ vb).sum() / max(1, (va | vb).sum()))
    if both.sum() == 0:
        return float("nan"), only
    return float(np.median(np.linalg.norm(la[both] - lb[both], axis=1))), only


INTENDED_TURN = {"quarter": 45.0, "quarter2": -45.0, "left": 90.0, "right": -90.0, "back": 180.0}  # the turn each drawn view is seen from
POSE_HINT_DEG = 20.0  # the pose search of a drawn view covers turns this far either side of its intended turn


PRIOR_DEG = 30.0      # a pose agrees with the expected turn when its turn is within this of it
# Objects (props and buildings) have no expected turn: their pictures are searched over every turn (the front half when the
# facing is decided) and the search's own score, outline with inner edges and colour, picks the pose; the D2 check refuses
# a far rival that fits as well and paints differently. Their concept art is three-quarter, not a front view, and the
# clean-up's quarter-turn facing snap can put any side at turn 0: the WP-50 crate's picture fit at turn 90 (expected 0),
# the cottage's at 0 and the shop's at 85 (both expected 45), and each was refused for it (bakeoff/posefit).
OBJECT_CATEGORIES = ("prop_rigid", "prop_articulated", "environment")


def _breadth(turns: list[float]) -> int:
    """How many 90-degree windows (ten turns of the search's grid) an object's pose search covers: its search gives that
    many times the usual outline poses the colour terms (a four-sided building's outline ties every quarter turn, and
    the full circle left its right pose out of the usual 24: bakeoff/posefit, the house at turn 30, tilt 35)."""
    return max(1, -(-len(turns) // 10))


def ps_turns() -> list[float]:
    """The pose search's whole turn grid."""
    from . import posesearch as ps

    return [float(t) for t in ps.TURNS]
DRAWN_PRIOR_DEG = 20.0  # the same for a drawn view and the turn it was drawn at
PICTURE_SEARCH_DEG = 45.0  # the step 1 picture's search covers turns this far either side of its expected turn


def _settle_by_prior(mesh: dict[str, np.ndarray], alpha: np.ndarray, pic: np.ndarray, tex: np.ndarray,
                     centre: np.ndarray, scale: float, poses: list[Any], prior: float, min_iou: float,
                     tilts: list[float] | None, lenses: list[float | None] | None, window: float = PRIOR_DEG,
                     keep: int = 3, searched: tuple[float, float] | None = None) -> tuple[Any, dict[str, Any], str | None]:
    """WP-61 option a: the pose to fit when the picture's turn is expected (the step 1 picture at the model's front, the
    original at the step 1 picture's pose). Among the poses scoring within D2_MARGIN of the best, the one nearest the
    expected turn is taken, when it is within `window` of it ("kept" when that is the best, else "tie-break"). When none
    is, the best pose within `window` of it is searched for: taken when it scores within 2 * D2_MARGIN of the best
    ("override"), else the picture is refused ("refused"). searched (centre, half width): the turn window `poses` were
    searched and refined in; when it lies inside the expected turn's window, those poses already are that search and are
    not searched again. Returns (pose, record, refusal reason or None)."""
    from . import posesearch as ps

    best = poses[0]
    # a best pose under the outline bar still has its turn settled here; the outline check is then fit_view's (round 5)
    bar = min_iou if best.iou >= min_iou else 0.0

    def pose_of(p: Any) -> list[Any]:
        return [round(p.turn, 2), round(p.tilt, 2), p.lens, round(p.score, 4)]

    near = [p for p in poses if best.score - p.score <= D2_MARGIN and p.iou >= bar]
    agree = [p for p in near if _angdiff(p.turn, prior) <= window]
    rec: dict[str, Any] = {"prior": round(prior, 2), "best": pose_of(best)}
    if agree:
        pick = min(agree, key=lambda p: (_angdiff(p.turn, prior), -p.score))
        rec.update(decision="kept" if pick is best else "tie-break", picked=pose_of(pick), gap=round(best.score - pick.score, 4))
        return pick, rec, None
    if searched is not None and _angdiff(searched[0], prior) + searched[1] <= window + 1e-9:
        alt = [p for p in poses if _angdiff(p.turn, prior) <= window and p.iou >= bar]  # already searched there
    else:
        turns = [prior + float(d) for d in np.linspace(-window, window, int(round(2 * window / 10.0)) + 1)]
        try:
            alt = [p for p in ps.search(mesh, alpha, centre, scale, keep=keep, turns=turns, tilts=tilts, lenses=lenses,
                                        rgb=pic, tex=tex, turn_limit=(prior, window))
                   if _angdiff(p.turn, prior) <= window and p.iou >= bar]
        except Exception:  # noqa: BLE001  (no pose near the expected turn: refused below)
            alt = []
    if alt and best.score - alt[0].score <= 2 * D2_MARGIN:
        rec.update(decision="override", picked=pose_of(alt[0]), gap=round(best.score - alt[0].score, 4))
        return alt[0], rec, None
    rec.update(decision="refused", picked=None, gap=round(best.score - alt[0].score, 4) if alt else None)
    why = (f"its best pose (turn {best.turn:.0f}) is {_angdiff(best.turn, prior):.0f} degrees from the turn it is expected at "
           f"({prior:.0f}), and no pose within {window:.0f} degrees of that scores within {2 * D2_MARGIN:g} of it")
    return best, rec, why


def view_disagreement(atlas: "Atlas", mesh: dict[str, np.ndarray], centre: np.ndarray, scale: float,
                      a: tuple[np.ndarray, np.ndarray, tuple[float, float, float | None]],
                      b: tuple[np.ndarray, np.ndarray, tuple[float, float, float | None]]) -> tuple[float, float]:
    """(median Lab distance between what two pictures paint on the texels both see squarely, share of a's texels that b
    also sees). a and b: (lab at D2_N, valid at D2_N, pose)."""
    va, la = _texels_painted(atlas, mesh, a[0], a[1], centre, scale, a[2])
    vb, lb = _texels_painted(atlas, mesh, b[0], b[1], centre, scale, b[2])
    both = va & vb
    share = float(both.sum() / max(1, va.sum()))
    if both.sum() == 0:
        return float("nan"), 0.0
    return float(np.median(np.linalg.norm(la[both] - lb[both], axis=1))), share


GUARD_MIN_SHARE = 0.05  # a drawn view overlaps a picture when it sees at least this share of the picture's texels


def _guard_by_views(v: "View", src: Any, fitted: dict[str, "View"], sources: dict[str, Any], atlas: "Atlas",
                    mesh: dict[str, np.ndarray], centre: np.ndarray, scale: float, prepared: "Prepared", pic_n: int | None,
                    log: Callable[[str], None] | None) -> None:
    """WP-61 option a's guard: a picture or drawn view whose pose the expected turn decided (a tie-break or an override)
    is refused when another fitted picture or view that sees the same texels paints them more than D2_THRESHOLD apart.
    The checks go in its record."""
    ps_ = v.info.get("pose_search")
    pr = ps_.get("prior") if isinstance(ps_, dict) else None
    if not v.ok or not pr or pr.get("decision") not in ("tie-break", "override"):
        return

    def lab_of(s: Any) -> tuple[np.ndarray, np.ndarray]:
        p, a = prepared(s, pic_n)
        return face.to_lab(np.clip(small(p, D2_N)[..., :3], 0.0, 1.0)), small(a, D2_N) > 0.5

    def pose_of(x: "View") -> tuple[float, float, float | None]:
        r = x.info.get("pose_search") if isinstance(x.info.get("pose_search"), dict) else {}
        return float(x.info["yaw"]), float(r.get("tilt") or 0.0), r.get("lens")

    la, va = lab_of(src)
    checks: list[dict[str, Any]] = []
    for n, s in sources.items():
        w = fitted.get(n)
        if w is None or w is v or n == v.name or not w.ok or "yaw" not in w.info:
            continue
        lb, vb = lab_of(s)
        d, share = view_disagreement(atlas, mesh, centre, scale, (la, va, pose_of(v)), (lb, vb, pose_of(w)))
        checks.append({"view": n, "dE": None if d != d else round(d, 2), "share": round(share, 3)})
        if share >= GUARD_MIN_SHARE and d == d and d > D2_THRESHOLD:
            v.ok = False
            v.info["refused"] = (f"the turn was settled by the turn it is expected at, and the {n} view paints the same "
                                 f"part {d:.1f} Lab away (over {D2_THRESHOLD:.0f})")
            if log:
                log(f"view {v.name}: refused, {v.info['refused']}")
            break
    pr["guard"] = checks


def _silhouette_refused(v: "View") -> bool:
    """Whether a view was refused for its silhouette (fit_view's own check), as opposed to the D2 refusal or the turn."""
    return "silhouette" in str(v.info.get("refused", ""))


HALF_TURN_MARGIN = 0.40  # an object's pose and its half-turn partner within this on the search's score: the outline and the
                         # colour cannot tell the two halves apart (tuned on the WP-50 crate, gaps up to 0.34; the closest
                         # asymmetric object seen, the cottage, 0.54; proved on a held-out set, bakeoff/posefit)
HALF_TURN_OUTLINE = 0.01  # the partner's search outline passes the outline bar, or is at least the best pose's less this
                          # (both can sit just under the bar while the full-size fit passes it: the crate at turn 60,
                          # 0.8454 and 0.8488)
HALF_TURN_FAR = 110.0    # the best pose this far from the facing the app knows ...
HALF_TURN_NEAR = 70.0    # ... and its partner this near it: the partner is taken (a pose 90 from it never switches)


def _half_turn_guard(mesh: dict[str, np.ndarray], alpha: np.ndarray, pic: np.ndarray, tex: np.ndarray, centre: np.ndarray,
                     scale: float, poses: list[Any], ref: float, min_iou: float,
                     turn_limit: tuple[float, float] | None) -> tuple[Any, list[Any], dict[str, Any]]:
    """An object's pose search has no expected turn, so a model that looks alike from opposite sides (a crate) can score
    its back half above its front. The best pose's half-turn partner, refined by the search's own refine, replaces it
    when its outline passes the bar or is as good (HALF_TURN_OUTLINE), scores within HALF_TURN_MARGIN, and lies on the side of the facing the app knows
    (`ref`: the build view's turn from the model's front) while the best pose does not. The abandoned half and the
    search's poses near it are no longer rivals: the guard decides between the halves and never refuses.
    Returns (pose, poses, record)."""
    from . import posesearch as ps

    top = poses[0]
    rec: dict[str, Any] = {"ref": round(ref, 1), "best": [round(top.turn, 2), round(top.tilt, 2), top.lens, round(top.score, 4),
                                                         round(top.iou, 4)]}
    if _angdiff(top.turn, ref) <= HALF_TURN_FAR:
        rec["decision"] = "kept: the best pose is on the facing's side"
        return top, poses, rec
    start = ps.Pose(float(top.turn) + 180.0, float(top.tilt), top.lens, 0.0)
    if turn_limit is not None and _angdiff(start.turn, turn_limit[0]) > turn_limit[1]:
        rec["decision"] = "kept: the half turn is outside the turns searched"
        return top, poses, rec
    part = ps.refine(mesh, alpha, centre, scale, start, rgb=pic, tex=tex, turn_limit=turn_limit)
    part = dataclasses.replace(part, turn=_signed(part.turn))  # the turn as the search gives its own, within +-180
    rec["partner"] = [round(part.turn, 2), round(part.tilt, 2), part.lens, round(part.score, 4), round(part.iou, 4)]
    if (part.iou < min(min_iou, top.iou - HALF_TURN_OUTLINE) or top.score - part.score > HALF_TURN_MARGIN
            or _angdiff(part.turn, ref) >= HALF_TURN_NEAR):
        rec["decision"] = "kept: the half turn misses the outline, scores lower or is not nearer the facing"
        return top, poses, rec
    rec["decision"] = "switched"
    rest = [p for p in poses if max(_angdiff(p.turn, top.turn), abs(p.tilt - top.tilt)) > D2_FAR_DEG]
    return part, [part, *rest], rec


def _pose_fit(mesh: dict[str, np.ndarray], tex: np.ndarray, prepared: "Prepared", src: Any, src_n: int | None, name: str,
              centre: np.ndarray, scale: float, size: int, min_iou: float, atlas_job: Any,
              log: Callable[[str], None] | None, hint: float | None = None,
              turns: list[float] | None = None, tilts: list[float] | None = None,
              lenses: list[float | None] | None = None, prior: float | None = None, window: float = PRIOR_DEG,
              keep: int = 3, turn_limit: tuple[float, float] | None = None, breadth: int = 1,
              half_turn_ref: float | None = None,
              outline_cache: dict | None = None) -> tuple["View | None", dict[str, Any]]:
    """WP-61: the pose search (turn, tilt, lens) for one picture, then the D2 check, then the fit at the picked pose.
    hint: the turn the view is meant to be seen from; the search covers turns within POSE_HINT_DEG of it (None: all turns).
    prior: the turn the picture is expected at, window: how far from it a pose still agrees (_settle_by_prior); the
    search still covers `turns`. keep: how many coarse poses are refined. turn_limit (centre, half width): the turn
    window the refinement stays in (with a hint, the hint's window). breadth: how many times the search's usual share of
    outline poses gets the colour terms and the lenses (objects searched over more than a 90 degree window).
    Returns (view, record). The view is None when the search fails outright (the caller keeps its turn-only fit, logged);
    otherwise it is ok, or refused for D2 (the reason names the rival) or for its silhouette."""
    picked = _pose_pick(mesh, tex, prepared, src, src_n, name, centre, scale, min_iou, atlas_job, log, hint, turns, tilts,
                        lenses, prior, window, keep, turn_limit, breadth, half_turn_ref, outline_cache)
    return _pose_view(mesh, tex, prepared, src, src_n, name, centre, scale, size, min_iou, log, picked)


def _pose_pick(mesh: dict[str, np.ndarray], tex: np.ndarray, prepared: "Prepared", src: Any, src_n: int | None, name: str,
               centre: np.ndarray, scale: float, min_iou: float, atlas_job: Any, log: Callable[[str], None] | None,
               hint: float | None = None, turns: list[float] | None = None, tilts: list[float] | None = None,
               lenses: list[float | None] | None = None, prior: float | None = None, window: float = PRIOR_DEG,
               keep: int = 3, turn_limit: tuple[float, float] | None = None,
               breadth: int = 1, half_turn_ref: float | None = None,
               outline_cache: dict | None = None) -> tuple[Any, dict[str, Any], str | None]:
    """_pose_fit's choice of pose, before the picture is fitted at it at full size: (pose or None when the search failed
    outright, record, refusal reason or None). The record is ready as soon as this returns (the drawn views take the
    step 1 picture's tilt and lens from it while the picture itself is still being fitted)."""
    from . import posesearch as ps

    pic, alpha = prepared(src, src_n)
    if hint is not None:
        turns = [float(hint) + d for d in (-POSE_HINT_DEG, -POSE_HINT_DEG / 2, 0.0, POSE_HINT_DEG / 2, POSE_HINT_DEG)]
        turn_limit = turn_limit or (float(hint), POSE_HINT_DEG)
    try:
        tm: dict[str, float] = {}
        poses = ps.search(mesh, alpha, centre, scale, keep=keep, turns=turns, tilts=tilts, lenses=lenses, rgb=pic, tex=tex,
                          timings=tm, turn_limit=turn_limit, keep_full=ps.KEEP_FULL * breadth,
                          lens_top=ps.LENS_TOP * breadth, cache=outline_cache)
    except Exception as e:  # a failed search keeps the turn-only fit, and the record says so
        if log:
            log(f"view {name}: pose search failed ({e}); the turn-only fit is kept")
        return None, {"search_failed": str(e)}, None
    top = poses[0]
    refuse_why = None
    prior_rec: dict[str, Any] | None = None
    half_rec: dict[str, Any] | None = None
    if half_turn_ref is not None:
        t_h = time.time()
        top, poses, half_rec = _half_turn_guard(mesh, alpha, pic, tex, centre, scale, poses, float(half_turn_ref), min_iou,
                                                turn_limit)
        tm["half_turn"] = round(time.time() - t_h, 2)
        if log and half_rec.get("decision") == "switched":
            log(f"view {name}: the half turn nearer the facing taken: {half_rec}")
    if prior is not None:  # also when the best pose misses the outline bar: the expected turn still decides (round 5)
        t_s = time.time()
        top, prior_rec, refuse_why = _settle_by_prior(mesh, alpha, pic, tex, centre, scale, poses, float(prior), min_iou,
                                                      tilts, lenses, window, keep, searched=turn_limit)
        tm["settle"] = round(time.time() - t_s, 2)
    rec: dict[str, Any] = {"turn": round(top.turn, 2), "tilt": round(top.tilt, 2), "lens": top.lens, "iou": round(top.iou, 4),
                           "score": round(top.score, 4), "terms": {k: round(v, 4) for k, v in top.terms.items()},
                           "hint": hint, "seconds": tm, "rivals": []}
    if prior_rec is not None:
        rec["prior"] = prior_rec
        if log and prior_rec["decision"] != "kept":
            log(f"view {name}: {prior_rec['decision']} by the expected turn {prior:.0f}: {prior_rec}")
    if half_rec is not None:
        rec["half_turn"] = half_rec
    t_d = time.time()
    lab = face.to_lab(np.clip(small(pic, D2_N)[..., :3], 0.0, 1.0))
    valid = small(alpha, D2_N) > 0.5
    atlas = atlas_job.result()
    # D2 decides only among poses that fit: a picture whose best pose misses the silhouette goes to fit_view's own refusal.
    # Two poses that fit as well and share no texel paint the picture onto different parts of the model: that refuses.
    # With an expected turn, a rival more than PRIOR_DEG from it is settled by it (option a), and does not refuse.
    for r in ([p for p in poses if p is not top] if top.iou >= min_iou and refuse_why is None else []):
        if top.score - r.score > D2_MARGIN:   # the rival must score within the margin on the search's own score
            continue
        far = max(_angdiff(r.turn, top.turn), abs(r.tilt - top.tilt)) > D2_FAR_DEG or r.lens != top.lens
        if not far:
            continue
        if prior is not None and _angdiff(r.turn, float(prior)) > window:
            continue
        d, only = pose_disagreement(atlas, mesh, lab, valid, centre, scale, (top.turn, top.tilt, top.lens),
                                    (r.turn, r.tilt, r.lens))
        rec["rivals"].append({"turn": round(r.turn, 2), "tilt": round(r.tilt, 2), "lens": r.lens, "iou": round(r.iou, 4),
                              "score": round(r.score, 4),
                              "dE": None if d != d else round(d, 2), "only": round(only, 3)})
        if d != d or d > D2_THRESHOLD:
            refuse_why = (f"the turn is ambiguous: a pose at turn {r.turn:.0f}, tilt {r.tilt:.0f} fits as well and paints "
                          + ("the picture onto other parts of the model" if d != d else f"the picture {d:.1f} Lab away")
                          + f" (over {D2_THRESHOLD:.0f})")
            break
    tm["d2"] = round(time.time() - t_d, 2)
    return top, rec, refuse_why


def _pose_view(mesh: dict[str, np.ndarray], tex: np.ndarray, prepared: "Prepared", src: Any, src_n: int | None, name: str,
               centre: np.ndarray, scale: float, size: int, min_iou: float, log: Callable[[str], None] | None,
               picked: tuple[Any, dict[str, Any], str | None]) -> tuple["View | None", dict[str, Any]]:
    """_pose_fit's second half: the picture fitted at the pose _pose_pick chose, or the refused view."""
    top, rec, refuse_why = picked
    if top is None:
        return None, rec
    if refuse_why is not None:
        v = View(name, VIEW_WEIGHT.get(name, 0.5))
        v.info.update({"name": name, "refused": refuse_why, "pose_search": rec})
        if log:
            log(f"view {name}: refused, {refuse_why}")
        return v, rec
    pic, alpha = prepared(src, src_n)
    t_f = time.time()
    v = fit_view(mesh, tex, pic, alpha, name, [float(top.turn)], centre, scale, size, min_iou, log=log,
                 tilt=top.tilt, lens=top.lens)
    if isinstance(rec.get("seconds"), dict):
        rec["seconds"]["fit"] = round(time.time() - t_f, 2)
    v.info["turn_source"] = "pose search"
    v.info["pose_search"] = rec
    return v, rec


def paint(mesh: dict[str, np.ndarray], tex: np.ndarray, picture: Any, turnaround: dict[str, Any] | None = None, *,
          mode: str = "detail", size: int = SIZE, picture_yaws: list[float] | None = None, min_iou: float = MIN_IOU,
          yaw_hint: Callable[[], float | None] | None = None, source: Any = None, front_view: Any = None,
          pic_n: int | None = None, log: Callable[[str], None] | None = None,
          keep: dict[str, Any] | None = None, head_yaw: float = 0.0, built_from: str | None = None,
          front_known: bool = False, category: str | None = None, picture_known: bool = True) -> dict[str, Any]:
    """Paint the picture (and the turnaround views, mode "full" or "detail"; "off" leaves them out) into tex (T,T,3) through
    the mesh's UVs. Returns {applied, base_color (T,T,3), reason, metrics}. Nothing is applied when the picture does not
    fit the model, or when the result is no closer to the picture than what the model had. `picture` is the one the 3D
    model was built from. `source` is the original picture when that is a different one (a character is built from the
    three-quarter view drawn from it): it paints first, in full colour, at whatever turn it fits; `front_view` (the
    front view drawn from the original) stands in for it when the original does not fit the model. head_yaw: the camera
    yaw the model's face is seen straight on from (face_detail.HEAD_YAW); the face region is found from there.
    front_known: the clean-up decided which way the model faces (facing.decide, or a turn chosen by hand), so its front is
    at head_yaw and the original picture and the built-from picture are fitted only within FRONT_HALF of it.
    category: the asset's category; for OBJECT_CATEGORIES neither picture has an expected turn (see OBJECT_CATEGORIES)."""
    from . import facemarks

    t0 = time.time()
    if category in OBJECT_CATEGORIES:
        # Props and buildings paint from the step 1 picture and the original painting only, never from the drawn side and
        # back views: on a box-like model a drawn view fits by its outline at any turn, and the turnaround draws objects
        # as three-quarter pictures with content of their own, so they put features and tone on the wrong side
        turnaround = None
    res: dict[str, Any] = {"applied": False, "base_color": tex, "reason": None, "metrics": {}}
    # which finder reads the eyes and mouth for the head views: without one the front head view can only be fitted by
    # the darkest spots and the three-quarter head views are refused, which the record has to say
    finder = facemarks.finder()
    centre, scale = model_frame(mesh)
    outlines: dict[tuple[float, int], np.ndarray] = {}
    prepared = Prepared()  # each picture prepared once in this call
    pose_outlines: dict[Any, Any] = {}  # the model's outline at each searched pose, shared by every picture's search in this call

    def fit(name: str, src: Any, yaws: list[float]) -> View:
        p_, a_ = prepared(src, pic_n)
        bar = min(min_iou, SOURCE_MIN_IOU) if name == "source" else min_iou
        if name == "source":  # WP-61: the original picture is searched like the picture, hint or not (the fidelity hint is
            # the built-from picture's turn, and the app always passes one; its turn is not left to colour)
            # its expected turn is the step 1 picture's chosen pose (option a), or the model's front when that has none
            # (the pose pick is enough: it does not wait for the picture's full-size fit)
            # the original shows the model's front: the step 1 picture's turn less the turn that picture was drawn at
            ptop, prec, pwhy = picture_pick_job.result()
            prior = (None if objects else
                     float(prec["turn"]) - picture_prior if ptop is not None and pwhy is None and prec.get("turn") is not None
                     else 0.0)
            pv, _ = _pose_fit(mesh, tex, prepared, src, pic_n, name, centre, scale, size, bar, atlas_job, log,
                              turns=wide if front_known else None, prior=prior,
                              breadth=_breadth(wide if front_known else ps_turns()) if objects else 1,
                              half_turn_ref=float(head_yaw) if objects else None, outline_cache=pose_outlines)
            if pv is not None and (pv.ok or not _silhouette_refused(pv)):
                return pv
            return fit_view(mesh, tex, p_, a_, name, yaws, centre, scale, size, bar, log=log, outlines=outlines)
        if name in INTENDED_TURN:  # WP-61: a drawn view is searched near the turn it is seen from, at the picture's camera
            _, prec, _ = picture_pick_job.result()   # height and lens: the drawn views keep the step 1 picture's camera
            near = prec.get("tilt") is not None
            pv, _ = _pose_fit(mesh, tex, prepared, src, pic_n, name, centre, scale, size, bar, atlas_job, log, INTENDED_TURN[name],
                              tilts=[float(prec["tilt"]) + d for d in (-20.0, -10.0, 0.0, 10.0, 20.0)] if near else None,
                              lenses=[prec.get("lens")] if near else None, prior=INTENDED_TURN[name],
                              window=DRAWN_PRIOR_DEG, keep=1, outline_cache=pose_outlines)  # expected at the turn it was drawn at; best coarse pose only
            if pv is None:  # the search failed outright: today's turn-only fit, logged by _pose_fit
                return fit_view(mesh, tex, p_, a_, name, yaws, centre, scale, size, bar, log=log, outlines=outlines)
            if pv.ok or not _silhouette_refused(pv):  # a D2 or expected-turn refusal stands
                return pv
            # The searched pose misses the outline: today's turn-only fit, over the turns within DRAWN_PRIOR_DEG of the
            # turn the view was drawn at only (round 5). Over every turn it put the hat's back view 28 degrees off, onto
            # the side; inside the window it keeps the side views of a candid photo build that fit.
            near_yaws = [y for y in yaws if _angdiff(float(y), INTENDED_TURN[name]) <= DRAWN_PRIOR_DEG]
            if not near_yaws:
                return pv
            tv = fit_view(mesh, tex, p_, a_, name, near_yaws, centre, scale, size, bar, log=log, outlines=outlines)
            # fit_view settles its turn a little past the turns it is given: the result must still be inside the window
            if not tv.ok or _angdiff(float(tv.info.get("yaw", 1e9)), INTENDED_TURN[name]) > DRAWN_PRIOR_DEG:
                return pv
            tv.info["turn_source"] = "turn-only fit near the drawn turn"
            tv.info["pose_search"] = {**(pv.info.get("pose_search") or {}), "refused": pv.info.get("refused")}
            return tv
        return fit_view(mesh, tex, p_, a_, name, yaws, centre, scale, size, bar, log=log, outlines=outlines)

    # Every view is fitted at the same time, in threads (each is independent of the others; separate processes were
    # measured and are no faster, the large images saturate memory either way); which ones are used, and in which
    # order, is decided afterwards exactly as if they had been fitted one after another.
    wide = front_half(PICTURE_YAWS, head_yaw) if front_known else PICTURE_YAWS
    # WP-61 option a: the step 1 picture is expected at the turn of the view the model was built from: a front view at
    # turn 0, a three-quarter view at its drawn turn (round 5: a model built from the three-quarter view, as Bob was,
    # has its picture near 40 degrees, and an expected 0 refused it), whatever the clean-up decided about the face; it is
    # searched within PICTURE_SEARCH_DEG of that
    picture_prior = INTENDED_TURN[built_from] if built_from in QUARTERS else 0.0
    objects = category in OBJECT_CATEGORIES  # no expected turn for either picture (OBJECT_CATEGORIES)
    # A character's picture whose angle nobody asked for (the person's own: picture_known False, see picture_known_of) is
    # searched as an object's is, over every turn (the front half once the facing is decided), unless the model was built
    # from a drawn three-quarter view, whose turn the app chose. A picture the app drew keeps the expected turn above.
    wide_pic = objects or (not picture_known and built_from not in QUARTERS)
    # the step 1 picture is fitted by its pose search; its turn-only fit is made only when that fails (below)
    wanted: list[tuple[str, Any, list[float]]] = []
    wanted += [(n, s, y) for n, s, y in (("source", source, wide), ("front", front_view, front_view_yaws(head_yaw, front_known))) if s is not None]
    if mode != "off":
        wanted += [(n, (turnaround or {}).get(n), TURN_YAWS[n]) for n in TURN_VIEWS if (turnaround or {}).get(n) is not None]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        atlas_job = pool.submit(Atlas, mesh, tex.shape[0])
        def zone_and_head() -> tuple[dict[str, Any], View | None, dict[str, Any]]:
            z = face_zone(mesh, tex, head_yaw)
            hv, hrec = fit_head(mesh, z, [("source", source), ("front", front_view)], scale / size, pic_n, log, prepared, tex)
            return z, hv, hrec

        zone_job = pool.submit(contextvars.copy_context().run, zone_and_head)
        # WP-61: the picture's pose search first; the drawn views wait for its tilt and lens (submitted earlier, so a
        # view waiting on it never holds back a job it needs)
        # the turns the caller allows (picture_yaws), else the front half when the facing is decided, else the whole circle
        picture_turns = ([float(y) for y in picture_yaws] if picture_yaws else [float(y) for y in wide] if wide_pic else
                         [picture_prior + float(d) for d in np.arange(-PICTURE_SEARCH_DEG, PICTURE_SEARCH_DEG + 1e-6, 10.0)])
        picture_limit = (None if picture_yaws or (wide_pic and not front_known) else (float(head_yaw), FRONT_HALF) if wide_pic
                         else (picture_prior, PICTURE_SEARCH_DEG))
        picture_pick_job = pool.submit(contextvars.copy_context().run, _pose_pick, mesh, tex, prepared, picture, pic_n,
                                       "picture", centre, scale, min_iou, atlas_job, log, None, picture_turns, None, None,
                                       None if wide_pic else picture_prior, PRIOR_DEG, 3, picture_limit,
                                       _breadth(picture_turns) if wide_pic else 1,
                                       float(head_yaw) + picture_prior if wide_pic else None, pose_outlines)
        picture_pose_job = pool.submit(contextvars.copy_context().run, lambda: _pose_view(
            mesh, tex, prepared, picture, pic_n, "picture", centre, scale, size, min_iou, log, picture_pick_job.result()))
        jobs = {n: pool.submit(fit, n, s, y) for n, s, y in wanted}
        fitted = {n: j.result() for n, j in jobs.items()}
        # WP-61: the picture is fitted at the pose the search finds (turn, tilt, lens), with or without a texfidelity hint.
        # A D2 refusal stands; today's turn-only fit stays when the search fails outright or its pose misses the silhouette.
        pv, _ = picture_pose_job.result()  # the facing decided: the picture's search kept to the front half
        front = pv
        if pv is None or not pv.ok:
            # the search did not paint the picture: today's turn-only fit, and the fidelity hint when its turn is ambiguous
            # (made only now: when the search's pose fits they were never used)
            pic, alpha = prepared(picture, pic_n)
            front = fit_view(mesh, tex, pic, alpha, "picture", list(picture_yaws or wide), centre, scale, size, min_iou,
                             log=log, outlines=outlines)
            if not front.ok and "ambiguous" in str(front.info.get("refused")) and yaw_hint is not None:
                # a box fits the outline at any turn: take the turn the fidelity score (texfidelity) finds, and say so
                hint = yaw_hint()
                if hint is not None:
                    front = fit_view(mesh, tex, pic, alpha, "picture", [float(hint)], centre, scale, size, min_iou, log=log)
                    front.info["turn_source"] = "texfidelity"
        if pv is None or (not pv.ok and _silhouette_refused(pv)):
            # the search failed outright (or its pose misses the silhouette, which the similarity fit scores differently):
            # today's turn-only fit stands, and the record says so
            front.info["pose_search"] = "failed: the turn-only fit is kept"
        elif not pv.ok and front.ok and front.info.get("turn_source") == "texfidelity":
            # the search cannot settle the turn, and the fidelity score's hint did: the hint's fit stands, the record says why
            front.info["pose_search"] = {"refused": pv.info.get("refused"), "settled_by": "texfidelity"}
        else:  # the search's answer stands, including a D2 refusal: a refused picture does not paint
            front = pv
        # a picture whose pose the expected turn decided is checked against the drawn views that see the same texels
        sources = {"picture": picture, **{n: s for n, s, _ in wanted}}
        views_now = {**fitted, "picture": front}
        for n in ["picture", "source", *INTENDED_TURN]:  # every picture or view whose turn its expected turn settled
            if n in views_now:
                _guard_by_views(views_now[n], sources[n], views_now, sources, atlas_job.result(), mesh, centre, scale,
                                prepared, pic_n, log)
        front.info["front_view"] = bool(abs(((front.info.get("yaw", 0.0) + 180) % 360) - 180) <= 25)
        # a drawn view fitted at a turn that contradicts the front is refused like one that does not fit (item 8)
        front_yaw, front_by = front_turn({**fitted, "picture": front}, head_yaw)
        for n, why in refuse_contradicting_turns(fitted, front_yaw, front, built_from).items():
            if log:
                log(f"view {n}: refused, {why}")
        # an object whose facing is not decided has no front to contradict: its turn 0 is whichever side the clean-up's
        # quarter-turn snap put there (bakeoff/posefit: a turn 90 picture of a box fitted at 90 was refused for it)
        why = None if objects and not front_known else picture_contradicts_front(front, front_yaw)
        if why:
            front.ok = False
            front.info["refused"] = why
            front.info["turn_from_front_deg"] = round(_signed(front.info["yaw"] - front_yaw), 1)
            if log:
                log(f"view picture: refused, {why}")
        if objects and category in CORNER_CATEGORIES:
            for n, v in (("picture", front), ("source", fitted.get("source")), ("front", fitted.get("front"))):
                if v is None or not v.ok or v.info.get("yaw") is None:
                    continue
                off = abs(((float(v.info["yaw"]) + 45.0) % 90.0) - 45.0)   # from the nearest quarter turn
                iou = v.info.get("iou")
                if off > CORNER_MAX_DEG:
                    why = (f"seen from a corner ({off:.1f} degrees from the nearest side): on a rigid prop it would paint "
                           "only the corners, at a slant")
                elif iou is not None and float(iou) < RIGID_MIN_IOU:
                    why = (f"silhouette match {float(iou):.3f} is under {RIGID_MIN_IOU:.2f}: on a rigid prop its parts would "
                           "land beside the model's")
                else:
                    continue
                v.ok = False
                v.info["refused"] = why
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
        other_name = other_quarter(built_from)
        other = fitted.get(other_name)
        if other_name != SECOND_QUARTER:
            # built from the second three-quarter view: the first one is the other side's, and takes its weight
            if other is not None:
                other.weight = VIEW_WEIGHT[SECOND_QUARTER]
            if fitted.get(SECOND_QUARTER) is not None:
                fitted[SECOND_QUARTER].weight = VIEW_WEIGHT["quarter"]
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
            res["metrics"] = {"srcproject_views": infos, "srcproject_face_finder": finder,
                              "srcproject_seconds": round(time.time() - t0, 1)}
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
        if objects:  # no steep angles on props and buildings (OBJECT_COS_CUT)
            for v in views:
                v.cos_cut = OBJECT_COS_CUT

        def ready(i: int) -> np.ndarray:
            v = views[i]
            if v.name.startswith(HEAD_VIEW):  # its gates were set when it was fitted
                return v.pic_aligned
            full = i < n_primary or mode == "full"
            drawn = i >= n_primary or v.name == other_name  # a view drawn from the picture, not the picture itself
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
         "srcproject_front_turn": {"yaw": round(front_yaw, 1), "from": front_by}, "srcproject_face_finder": finder,
         "srcproject_neck_handover": c.get("neck_handover"), "srcproject_head_tone": c.get("head_tone"), "srcproject_seconds": round(time.time() - t0, 1)}
    res["metrics"] = m
    nh = c.get("neck_handover")
    if log and nh is not None and not nh["ok"]:
        log(f"neck: the head views hand over to the body fits within {nh['span_m'] * 100:.1f} cm at {nh['at_m']:.3f} m "
            f"(colour step {nh['step']}): a line across the neck")
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
                head_yaw: float = 0.0, built_from: str | None = None, front_known: bool = False,
                category: str | None = None, picture_known: bool = True) -> dict[str, Any]:
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
              head_yaw=head_yaw, built_from=built_from, front_known=front_known, category=category,
              picture_known=picture_known)
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


# Categories that paint by themselves: characters, buildings and rigid props (whose pictures from a corner do not paint,
# CORNER_MAX_DEG). Articulated props stay off: their paint is slow and changes little.
AUTO_CATEGORIES = ("humanoid", "environment", "prop_rigid")


def on(params: dict[str, Any], category: str | None = None, look: dict[str, Any] | None = None) -> bool:
    """Whether the pass runs. The version's own choice wins (on or off). With no choice it runs by itself for a character, a building or a rigid prop.
    `look` is no longer read; it stays so the callers need not change."""
    if params.get(PARAM) is not None:
        return bool(params.get(PARAM))
    return category in AUTO_CATEGORIES


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


# The version's choice to paint from the two three-quarter pictures (on unless false). Off leaves quarter and quarter2
# out of this pass and of the face pass's side views (stages/face_detail.side_pictures): the head is then painted from
# the front pictures alone, the body from the front, side and back views.
QUARTER_PARAM = "quarter_pictures"


def quarters_on(params: dict[str, Any] | None) -> bool:
    return (params or {}).get(QUARTER_PARAM) is not False


def painted_views(params: dict[str, Any] | None) -> tuple[str, ...]:
    """The turnaround views this pass paints from: TURN_VIEWS, without the three-quarter views when they are off."""
    return TURN_VIEWS if quarters_on(params) else tuple(v for v in TURN_VIEWS if v not in QUARTERS)


def without_quarters(pictures: dict[str, Any], params: dict[str, Any] | None) -> dict[str, Any]:
    """pictures by view name, without the three-quarter views when the version leaves them out."""
    return dict(pictures) if quarters_on(params) else {k: p for k, p in pictures.items() if k not in QUARTERS}


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
    inst = await face_detail.live_image_instance(sc)
    if inst is None:
        return files, {**info, "skipped": "no card to draw on"}
    others = [c for c in face_detail.image_instances(sc) if c != inst]
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
            model = model_for(key)

            async def on_card(card: str) -> Path:
                name = await sc.stage_input(p, f"vgac_{sc.version.id[:8]}_srcproject_{key}.png", instance=card)
                g = concept.upscale_graph(name, model, int(round(w * f)), int(round(h * f)), sc.prefix(f"srcproject_{key}"))
                entry = await sc.comfy(g, instance=card, message="Sharpening the pictures to paint from")
                return await sc.download_image(entry, "save", work / f"up_{key}_raw.png", instance=card)

            try:
                raw = await on_card(inst)
            except (JobCancelled, asyncio.CancelledError):
                raise
            except Exception:  # noqa: BLE001  (the card went away part way: the next card that may draw pictures, once)
                if not others:
                    raise
                raw = await on_card(others[0])
                inst, others = others[0], [inst]
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


def picture_known_of(concept_params: dict[str, Any] | None) -> bool:
    """Whether the step 1 picture's angle may be expected (paint's picture_known): yes for a picture the app drew (a
    recorded square-on front, picture_view.assumed, or one drawn before views were recorded), no for the person's own
    (an upload, an edit of one, a picture combined from their photos: picture_view.PERSON_SOURCES), searched widely."""
    p = concept_params or {}
    return picture_view.assumed(p) is not None or p.get("source") not in picture_view.PERSON_SOURCES


async def run_stage(sc: Any, glb: Path, current: dict[str, Path], params: dict[str, Any],
                    keep: dict[str, Any] | None = None, head_yaw: float = 0.0,
                    front_known: bool = False) -> dict[str, Path]:
    """The pass inside the prep stage (stages/prep.py), after the last rebake and before the face pass: paints the picture
    the asset started from, the picture the 3D step was given and the turnaround views into the current base colour; replaces it in the GLB and in
    `current` when it applies, and records the before and after numbers in the stage metrics. head_yaw: the camera yaw
    the face is seen straight on from (as the face pass is given it). Returns `current`."""
    mode = mode_of(params)
    sc.set_params(**{"source_project_mode": mode})
    base = current.get("base_color")
    picture = sc.lineage_file("input", ("mesh",)) or sc.lineage_file("cutout", ("concept",))
    try:
        mv = sc.stage_version("mesh")
        built_from = ((mv.params or {}) if mv is not None else {}).get("input_view")
    except Exception:  # noqa: BLE001  (no lineage to read: the first three-quarter view, as before)
        built_from = None
    try:
        cv = sc.stage_version("concept")
        picture_known = picture_known_of(cv.params if cv is not None else None)
    except Exception:  # noqa: BLE001  (no lineage to read: expected, as before)
        picture_known = True
    sc.set_metrics(srcproject_picture_known=picture_known)
    if base is None or picture is None:
        sc.set_metrics(srcproject_applied=False, srcproject_reason="no base colour or no picture to paint from")
        return current
    turn = {v: sc.lineage_file(f"{v}_cutout", ("turnaround",)) for v in painted_views(params)}
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
    if hasattr(sc, "progress"):  # the sharpening label must not stay up while the pictures are painted
        sc.progress(None, "Painting the pictures onto the model")
    big = any(p is not None and Path(p).is_file() and _longer_side(Path(p)) >= UPSCALE_BELOW for p in srcs.values())
    picture, source, front_view = srcs["picture"], srcs["source"], srcs["front"]
    turn = {v: srcs.get(v) for v in painted_views(params)}
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
                                    front_view=front_view, pic_n=PIC_N_BIG if big else None, keep=keep, head_yaw=head_yaw,
                                    built_from=built_from, front_known=front_known, picture_known=picture_known,
                                    category=getattr(getattr(sc, "asset", None), "category", None))
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
