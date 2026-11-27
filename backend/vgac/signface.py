"""Flat panels of a building, painted from the step 1 picture: a front sign, flattened where its lettering has no depth, and
(on a picture taken at an angle) side signs, neon figures and display shelves.

The 3D build scrambles lettering into the shape as well as the colour: a flat printed word comes out as raised blocks, which
the budget reduction turns into jagged facets, and its colour as a smear that the picture paint only partly covers (not at all
where a whole-view fit was refused). So the one board the item finder names on both the step 1 picture and a square-on front
render of the model is treated by itself: its panel (the plain-coloured rectangle the letters sit in) is found in each, the
two are checked to be the same shape and colour, the model's panel is set flat when the picture shows flat lettering, and the
picture's panel is copied onto it. Anything that does not match leaves the model as it was.

A picture taken at an angle shows panels on more than one wall. For these the camera the picture was taken from is found
against the model, the item finder names the panels on the picture and on a render from that camera, and each pair that
overlaps is treated: the picture's box is mapped onto the model's, the surface is set flat where the kind of panel is flat
(never under a figure), and the picture's colours are copied onto it."""
from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image

from . import face, imaging

VIEW = 1600              # the square-on front render, pixels
PANEL_DE = 12.0          # Lab distance from the panel colour that still counts as plain panel
PANEL_FILL = 0.85        # the filled panel covers at least this share of its own rectangle
MIN_PANEL_ASPECT = 1.6   # a sign board with a line of words is wider than tall
ASPECT_TOL = 0.15        # picture panel and model panel: width over height within this share
COLOUR_TOL = 25.0        # Lab distance between the two panel colours
LETTER_DE = 18.0         # Lab distance from the panel colour that counts as lettering
LETTER_SHARE = (0.04, 0.60)  # lettering as a share of the panel: less is a blank board, more is not a lettered panel
SIDE_DE = 12.0           # a colour this far from the line between panel and letter colour is a third colour
DEPTH_SHARE = 0.10       # third-colour pixels beside the letters, as a share of the letters: over this, the letters have depth
MAX_RELIEF = 0.015       # share of the model's size: a front face further off one plane than this is not a flat board
PLANE_BAND = 0.02        # share of the model's size: vertices this near the panel's plane belong to the panel
INSET_PX = 2             # the panel rectangle is shrunk this much before vertices are moved, so a frame's wall stays put
FRONT_NZ = 0.3           # a triangle facing at least this much to the front can be painted
EDGE_PX = 3              # the picture fades into the model's own colour over this many pixels at the panel's edge

CAMERA = 1024            # the render from the picture's camera, pixels
PICTURE = 2048           # the step 1 picture is read at this size when its colours are copied
MIN_TURN = 20.0          # a picture turned less than this from the front shows its panels to the front route only
MAX_TURN = 100.0         # the picture's camera is searched within this of the model's front
MIN_FIT = 0.85           # outline overlap the picture's camera must reach
MATCH_IOU = 0.3          # a picture panel and a model panel of one kind overlap at least this much ...
MATCH_SIZE = 0.6         # ... and neither side is under this share of the other's
INSIDE_CASE = 0.8        # a display's shelves lie at least this far inside a machine or case the finder names
AXIS_SHARE = 0.4         # share of the panel's pixels facing one wall direction for it to count as a panel of that wall
PANEL_RELIEF = 0.03      # share of the model's size (90th percentile off the plane): rougher than this is not flattened
BANDS = {"sign": 0.02, "figure": 0.02, "display": 0.03}   # share of the model's size: how near the plane belongs to the panel
FLATTENED = ("sign", "display")   # a figure is drawn on its wall, which stays as it is


# --- the panel in a picture ---------------------------------------------------------------------------------------


def boxes_of(mask: np.ndarray, min_share: float = 0.003) -> list[tuple[int, int, int, int]]:
    """(x0, y0, x1, y1) of each connected piece of `mask` of at least `min_share` of the picture, largest first."""
    small = mask[::4, ::4].copy()
    out = []
    while small.any():
        seed = np.zeros_like(small)
        seed.flat[np.argmax(small)] = True
        piece = face.grow(seed, small)
        small &= ~piece
        if piece.sum() < min_share * small.size:
            continue
        ys, xs = np.nonzero(piece)
        out.append((int(piece.sum()), (int(xs.min()) * 4, int(ys.min()) * 4, int(xs.max()) * 4 + 4, int(ys.max()) * 4 + 4)))
    return [b for _, b in sorted(out, key=lambda t: -t[0])]


def panel_in(img: np.ndarray, box: tuple[int, int, int, int]) -> dict[str, Any] | None:
    """The plain-coloured rectangle holding the lettering inside `box` of `img` (floats 0..1): {rect (x0, y0, x1, y1) in
    picture pixels, colour (Lab)}, or None."""
    x0, y0, x1, y1 = box
    h, w = img.shape[:2]
    mx, my = (x1 - x0) // 20 + 4, (y1 - y0) // 20 + 4
    x0, y0, x1, y1 = max(0, x0 - mx), max(0, y0 - my), min(w, x1 + mx), min(h, y1 + my)
    if x1 - x0 < 16 or y1 - y0 < 8:
        return None
    lab = face.to_lab(img[y0:y1, x0:x1])
    ch, cw = lab.shape[:2]
    core = lab[ch // 5: ch - ch // 5, cw // 10: cw - cw // 10].reshape(-1, 3)
    # the panel colour: the most common colour in the middle of the box
    keys, inv, counts = np.unique(np.round(core / 6.0).astype(int), axis=0, return_inverse=True, return_counts=True)
    colour = np.median(core[inv.reshape(-1) == np.argmax(counts)], axis=0)
    plain = np.linalg.norm(lab - colour, axis=2) < PANEL_DE
    # the connected plain piece with the most pixels in the middle of the box
    middle = np.zeros_like(plain)
    middle[ch // 5: ch - ch // 5, cw // 10: cw - cw // 10] = True
    left, best = plain & middle, None
    for _ in range(6):
        if not left.any():
            break
        seed = np.zeros_like(plain)
        seed.flat[np.argmax(left)] = True
        piece = face.grow(seed, plain)
        left &= ~piece
        if best is None or piece.sum() > best.sum():
            best = piece
    if best is None:
        return None
    filled = face.fill_holes(best)
    rows, cols = filled.sum(1), filled.sum(0)
    ys = np.nonzero(rows > 0.5 * rows.max())[0]
    xs = np.nonzero(cols > 0.5 * cols.max())[0]
    rx0, rx1, ry0, ry1 = int(xs[0]), int(xs[-1]) + 1, int(ys[0]), int(ys[-1]) + 1
    if (ry1 - ry0) < 8 or (rx1 - rx0) < MIN_PANEL_ASPECT * (ry1 - ry0):
        return None
    if filled[ry0:ry1, rx0:rx1].mean() < PANEL_FILL:
        return None
    return {"rect": (rx0 + x0, ry0 + y0, rx1 + x0, ry1 + y0), "colour": colour}


def lettering(img: np.ndarray, panel: dict[str, Any]) -> dict[str, float]:
    """{share: lettering as a share of the panel, depth: third-colour pixels beside the letters as a share of the letters}.

    Flat printed letters show only the panel colour, the letter colour and their blend along each edge. Letters with depth
    also show a side face or a cast shadow: a colour off the line between the two."""
    x0, y0, x1, y1 = panel["rect"]
    in_x, in_y = max(2, (x1 - x0) // 50), max(2, (y1 - y0) // 20)
    lab = face.to_lab(img[y0 + in_y: y1 - in_y, x0 + in_x: x1 - in_x])
    bg = np.asarray(panel["colour"])
    de = np.linalg.norm(lab - bg, axis=2)
    strong = de > LETTER_DE
    share = float(strong.mean())
    if not strong.any():
        return {"share": 0.0, "depth": 0.0}
    keys, inv, counts = np.unique(np.round(lab[strong] / 8.0).astype(int), axis=0, return_inverse=True, return_counts=True)
    ink = np.median(lab[strong][inv.reshape(-1) == np.argmax(counts)], axis=0)
    axis = ink - bg
    t = np.clip(((lab - bg) @ axis) / max(float(axis @ axis), 1e-9), 0.0, 1.0)
    off = np.linalg.norm(lab - (bg + t[..., None] * axis), axis=2)
    letters = np.linalg.norm(lab - ink, axis=2) < LETTER_DE
    reach = max(2, (y1 - y0) // 25)
    third = (off > SIDE_DE) & (de > SIDE_DE) & face.dilate_mask(letters, reach)
    return {"share": share, "depth": float(third.sum()) / max(float(letters.sum()), 1.0)}


# --- the model ----------------------------------------------------------------------------------------------------


def front_camera(verts: np.ndarray, size: int = VIEW) -> face.Camera:
    """Square-on from the front (+Z), orthographic, the whole model in frame."""
    lo, hi = verts.min(0), verts.max(0)
    return face.Camera(target=(lo + hi) / 2.0, yaw_deg=0.0, scale=float((hi - lo)[:2].max()) * 1.1, size=size)


def to_model(cam: face.Camera, x: float, y: float) -> tuple[float, float]:
    """Picture pixel of the front camera -> model (x, y)."""
    s = cam.scale / cam.size
    return float(cam.target[0] + (x - cam.size / 2) * s), float(cam.target[1] - (y - cam.size / 2) * s)


def _inside(p: np.ndarray, rect: tuple[float, float, float, float], by: float = 0.0) -> np.ndarray:
    xl, yb, xr, yt = rect
    return (p[:, 0] >= xl - by) & (p[:, 0] <= xr + by) & (p[:, 1] >= yb - by) & (p[:, 1] <= yt + by)


def panel_plane(mesh: dict[str, np.ndarray], rect: tuple[float, float, float, float]) -> dict[str, Any] | None:
    """The plane the panel sits in (a depth z), and how far its front surface stands off it, as a share of the model size."""
    verts = mesh["verts"]
    size = float(np.ptp(verts, axis=0).max())
    cam = front_camera(verts, 800)
    ras = face.view_raster(mesh, cam)
    s = cam.scale / cam.size
    rows, cols = np.nonzero(ras["tri"] >= 0)
    x = cam.target[0] + (cols + 0.5 - cam.size / 2) * s
    y = cam.target[1] - (rows + 0.5 - cam.size / 2) * s
    z = cam.target[2] - ras["z"][rows, cols]  # the front camera looks along -Z
    pts = np.stack([x, y], axis=1)
    here = _inside(pts, rect)
    if not here.any():
        return None
    xl, yb, xr, yt = rect
    ring = here & ~_inside(pts, (xl + 0.06 * (xr - xl), yb + 0.12 * (yt - yb), xr - 0.06 * (xr - xl), yt - 0.12 * (yt - yb)))
    plane = float(np.median(z[ring] if ring.any() else z[here]))
    relief = float(np.percentile(np.abs(z[here] - plane), 99)) / size
    return {"z": plane, "relief": relief, "size": size}


def move_to_plane(glb: Path, rect: tuple[float, float, float, float], plane: float, band: float) -> int:
    """Every vertex of the GLB inside `rect` and within `band` of the plane z = `plane` goes onto it (move_vertices)."""
    return move_vertices(glb, lambda p: _inside(p, rect) & (np.abs(p[:, 2] - plane) <= band), 2, plane)


def move_vertices(glb: Path, select: Callable[[np.ndarray], np.ndarray], axis: int, value: float) -> int:
    """The vertices of the GLB that `select(positions)` picks get coordinate `axis` set to `value`; the normals of the
    triangles touched are made again. In place; -> the vertices moved. Only for a GLB whose nodes carry no transform."""
    data = bytearray(glb.read_bytes())
    jlen = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(bytes(data[20:20 + jlen]))
    if any(k in n for n in gltf.get("nodes", []) for k in ("matrix", "translation", "rotation", "scale")):
        return 0
    start = 20 + jlen + 8

    def view(i: int) -> np.ndarray:
        a = gltf["accessors"][i]
        bv = gltf["bufferViews"][a["bufferView"]]
        dtype = {5121: np.uint8, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}[a["componentType"]]
        wide = {"SCALAR": 1, "VEC2": 2, "VEC3": 3}[a["type"]]
        if bv.get("byteStride") not in (None, wide * np.dtype(dtype).itemsize):
            raise ValueError("interleaved vertex data")
        off = start + bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        return np.frombuffer(data, dtype=dtype, count=a["count"] * wide, offset=off).reshape(a["count"], wide)

    moved = 0
    for mesh in gltf.get("meshes", []):
        for prim in mesh["primitives"]:
            pos = view(prim["attributes"]["POSITION"])
            hit = select(pos)
            if not hit.any():
                continue
            pos[hit, axis] = value
            moved += int(hit.sum())
            a = gltf["accessors"][prim["attributes"]["POSITION"]]
            a["min"], a["max"] = pos.min(0).astype(float).tolist(), pos.max(0).astype(float).tolist()
            if "NORMAL" not in prim["attributes"]:
                continue
            nrm = view(prim["attributes"]["NORMAL"])
            tris = view(prim["indices"]).reshape(-1, 3).astype(np.int64) if "indices" in prim else np.arange(len(pos)).reshape(-1, 3)
            p = pos.astype(np.float64)
            fn = np.cross(p[tris[:, 1]] - p[tris[:, 0]], p[tris[:, 2]] - p[tris[:, 0]])
            acc = np.zeros((len(pos), 3))
            for k in range(3):
                np.add.at(acc, tris[:, k], fn)
            touched = np.zeros(len(pos), bool)
            touched[tris[hit[tris].any(axis=1)].ravel()] = True
            length = np.linalg.norm(acc, axis=1)
            ok = touched & (length > 1e-12)
            nrm[ok] = (acc[ok] / length[ok, None]).astype(np.float32)
    text = json.dumps(gltf, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    rest = bytes(data[20 + jlen:])
    glb.write_bytes(struct.pack("<4sII", b"glTF", 2, 20 + len(text) + len(rest)) + struct.pack("<I4s", len(text), b"JSON") + text + rest)
    return moved


def paint_panel(mesh: dict[str, np.ndarray], base: np.ndarray, rect: tuple[float, float, float, float], plane: float,
                band: float, picture: np.ndarray, pic_rect: tuple[int, int, int, int]) -> tuple[np.ndarray, np.ndarray]:
    """Copy the picture's panel onto the model's panel, into `base` (T, T, 3) in place. -> (the triangles painted, the
    texels painted (T, T) bool)."""
    verts, faces = mesh["verts"], mesh["faces"]
    T = base.shape[0]
    p = verts[faces]
    n = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    xl, yb, xr, yt = rect
    # either way round: a flattened overhang lies turned over, and the material shows both sides
    near = (np.abs(n[:, 2]) > FRONT_NZ) & (np.abs(p[:, :, 2].mean(1) - plane) <= band)
    near &= (p[:, :, 0].max(1) >= xl) & (p[:, :, 0].min(1) <= xr) & (p[:, :, 1].max(1) >= yb) & (p[:, :, 1].min(1) <= yt)
    pick = np.nonzero(near)[0]
    painted = np.zeros((T, T), bool)
    if not len(pick):
        return pick, painted
    uvp = face.uv_pixels(mesh["uv"], T)
    ras = face.rasterise(uvp, np.zeros(len(uvp)), faces, T, pick)
    covered = ras["tri"] >= 0
    pos = face.interpolate(verts, faces, ras)[covered]
    inside = _inside(pos, rect) & (np.abs(pos[:, 2] - plane) <= band)
    u = (pos[:, 0] - xl) / (xr - xl)
    v = (yt - pos[:, 1]) / (yt - yb)
    px0, py0, px1, py1 = pic_rect
    colour = face.sample(picture, px0 + u * (px1 - px0), py0 + v * (py1 - py0))
    edge = np.minimum(np.minimum(u, 1 - u) * (px1 - px0), np.minimum(v, 1 - v) * (py1 - py0))
    a = np.clip(edge / EDGE_PX, 0.0, 1.0)[:, None]
    rows, cols = np.nonzero(covered)
    rows, cols, colour, a = rows[inside], cols[inside], colour[inside], a[inside]
    base[rows, cols] = base[rows, cols] * (1 - a) + colour * a
    painted[rows, cols] = True
    _fill_gutter(mesh, base, painted)
    return np.unique(ras["tri"][covered][inside]), painted


def _fill_gutter(mesh: dict[str, np.ndarray], base: np.ndarray, painted: np.ndarray) -> None:
    """The painted colours a little way into the empty texture beside the painted pieces, in place."""
    uvp = face.uv_pixels(mesh["uv"], base.shape[0])
    used = face.rasterise(uvp, np.zeros(len(uvp)), mesh["faces"], base.shape[0])["tri"] >= 0
    grown = imaging.dilate(np.where(painted[..., None], base, 0.0), painted, 2)
    gutter = face.dilate_mask(painted, 2) & ~used
    base[gutter] = grown[gutter]


# --- panels seen at an angle --------------------------------------------------------------------------------------


def picture_camera(mesh: dict[str, np.ndarray], base: np.ndarray, picture: Path) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """The camera the step 1 picture was taken from, found against the model painted with `base`: (camera_frame, record),
    or (None, record) when the picture faces the front or fits no turn."""
    from . import posesearch as ps
    from . import srcproject

    colour, alpha = srcproject.prepare_picture(picture, PICTURE)
    centre, scale = srcproject.model_frame(mesh)
    turns = [float(t) for t in range(-int(MAX_TURN), int(MAX_TURN) + 1, 10)]
    best = ps.search(mesh, alpha, centre, scale, turns=turns, rgb=colour, tex=base, turn_limit=(0.0, MAX_TURN),
                     keep_full=ps.KEEP_FULL * 2, lens_top=ps.LENS_TOP * 2)[0]
    rec: dict[str, Any] = {"turn": round(float(best.turn), 1), "tilt": round(float(best.tilt), 1), "lens": best.lens}
    if abs(best.turn) < MIN_TURN:
        return None, {**rec, "reason": "the picture faces the front"}
    n = min(srcproject.FIT_N, CAMERA)
    drawn = srcproject.outline(mesh, srcproject._camera(centre, best.turn, scale, n, best.tilt, best.lens))
    fit = srcproject.fit_similarity(alpha > 0.5, drawn, n=n)
    rec["fit"] = round(float(fit["iou"]), 3)
    if fit["iou"] < MIN_FIT:
        return None, {**rec, "reason": "the picture does not fit the model"}
    cam = srcproject._camera(centre, best.turn, scale, CAMERA, best.tilt, best.lens)
    return camera_frame(cam, srcproject.sim_to_full(fit, PICTURE, CAMERA), colour, alpha), rec


def camera_frame(cam: face.Camera, matrix: np.ndarray, colour: np.ndarray, alpha: np.ndarray) -> dict[str, Any]:
    """{cam, matrix (render pixel -> picture pixel), colour, alpha (the picture), view (the picture seen in the render's
    frame, on white)}."""
    ys, xs = np.mgrid[0:cam.size, 0:cam.size] + 0.5
    qx, qy = _to_picture(matrix, xs, ys)
    size = colour.shape[0]
    a = face.sample(alpha[..., None], qx, qy)
    a = np.where(((qx >= 0) & (qx < size) & (qy >= 0) & (qy < size))[..., None], a, 0.0)
    view = face.sample(colour, qx, qy) * a + (1.0 - a)
    return {"cam": cam, "matrix": matrix, "colour": colour, "alpha": alpha, "view": view}


def _to_picture(m: np.ndarray, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return m[0, 0] * x + m[0, 1] * y + m[0, 2], m[1, 0] * x + m[1, 1] * y + m[1, 2]


def _overlap(a: list[float], b: list[float]) -> tuple[float, float]:
    """(intersection over union, share of box a inside box b)."""
    inter = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area_a, area_b = (a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])
    return inter / max(area_a + area_b - inter, 1e-9), inter / max(area_a, 1e-9)


def match_panels(picture: list[dict[str, Any]], model: list[dict[str, Any]]) -> list[tuple[float, dict[str, Any], dict[str, Any]]]:
    """(overlap, picture panel, model panel) pairs, one to one, best first. Each panel is {kind, box, mask}; kind "case"
    (a machine or cabinet) is not a panel itself: a display counts only inside one named on the picture."""
    cases = [d["box"] for d in picture if d["kind"] == "case"]
    pairs = []
    for i, a in enumerate(picture):
        if a["kind"] == "case" or (a["kind"] == "display" and not any(_overlap(a["box"], c)[1] >= INSIDE_CASE for c in cases)):
            continue
        for j, b in enumerate(model):
            if b["kind"] != a["kind"]:
                continue
            iou = _overlap(a["box"], b["box"])[0]
            sides = [(a["box"][k + 2] - a["box"][k]) / max(b["box"][k + 2] - b["box"][k], 1e-9) for k in (0, 1)]
            if iou >= MATCH_IOU and all(MATCH_SIZE <= s <= 1.0 / MATCH_SIZE for s in sides):
                pairs.append((iou, i, j))
    used_a: set[int] = set()
    used_b: set[int] = set()
    out = []
    for iou, i, j in sorted(pairs, reverse=True):
        if i not in used_a and j not in used_b:
            used_a.add(i)
            used_b.add(j)
            out.append((iou, picture[i], model[j]))
    return out


def wall_plane(mesh: dict[str, np.ndarray], cam: face.Camera, mask: np.ndarray, kind: str) -> dict[str, Any] | None:
    """The wall the panel under `mask` (render pixels) lies on: {axis (0 or 2), value, relief, size}, the wall direction
    (x or z) seen by the camera that most of its pixels face, or None. A display's plane is its frame's (the mask's rim)."""
    verts, faces = mesh["verts"], mesh["faces"]
    ras = face.view_raster(mesh, cam)
    hit = face.erode(mask, 2) & (ras["tri"] >= 0)
    if hit.sum() < 50:
        return None
    n = np.cross(verts[faces[:, 1]] - verts[faces[:, 0]], verts[faces[:, 2]] - verts[faces[:, 0]])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    tri = np.maximum(ras["tri"], 0)
    pos = face.interpolate(verts, faces, ras)
    forward = cam.basis[2]
    shares = {a: float((np.abs(n[ras["tri"][hit], a]) > 0.8).mean()) for a in (0, 2) if abs(forward[a]) > 0.2}
    if not shares:
        return None
    axis = max(shares, key=lambda a: shares[a])
    if shares[axis] < AXIS_SHARE:
        return None
    flat = hit & (np.abs(n[tri, axis]) > 0.8)
    if kind == "display":
        rim = flat & ~face.erode(mask, max(3, int(np.sqrt(mask.sum()) * 0.08)))
        flat = rim if rim.sum() >= 20 else flat
    value = float(np.median(pos[flat][:, axis]))
    size = float(np.ptp(verts, axis=0).max())
    relief = float(np.percentile(np.abs(pos[hit][:, axis] - value), 90)) / size
    return {"axis": axis, "value": value, "relief": relief, "size": size}


def _pixels(cam: face.Camera, p: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Points -> (x, y in the render, depth, column and row clipped to the frame)."""
    xy, depth = cam.project(p)
    col = np.clip(xy[:, 0].astype(int), 0, cam.size - 1)
    row = np.clip(xy[:, 1].astype(int), 0, cam.size - 1)
    return xy[:, 0], xy[:, 1], depth, col, row


def paint_angled(mesh: dict[str, np.ndarray], base: np.ndarray, frame: dict[str, Any], plane: dict[str, Any], band: float,
                 picture: dict[str, Any], model: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """Copy the picture's panel onto the model's, the picture box mapped onto the model box, into `base` (T, T, 3) in
    place. -> (the triangles painted, the texels painted (T, T) bool)."""
    verts, faces = mesh["verts"], mesh["faces"]
    cam, T = frame["cam"], base.shape[0]
    axis, value, size = plane["axis"], plane["value"], plane["size"]
    pb, mb = picture["box"], model["box"]
    kx = (pb[2] - pb[0]) / max(mb[2] - mb[0], 1e-9)
    ky = (pb[3] - pb[1]) / max(mb[3] - mb[1], 1e-9)
    ys, xs = np.mgrid[0:cam.size, 0:cam.size] + 0.5
    px = np.clip((pb[0] + (xs - mb[0]) * kx).astype(int), 0, cam.size - 1)
    py = np.clip((pb[1] + (ys - mb[1]) * ky).astype(int), 0, cam.size - 1)
    # paint only where the model's panel and the picture's panel both are: the model mask alone also covers wall
    # beside the sign, and that wall is not in the picture's panel
    region = face.dilate_mask(model["mask"], 3) & face.dilate_mask(picture["mask"][py, px], 3)
    weight = np.clip(face.blur(region.astype(np.float64), EDGE_PX / 2) * 2.0 - 0.5, 0.0, 1.0)
    seen = face.view_raster(mesh, cam)["z"]
    p = verts[faces]
    n = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    pick = np.nonzero((np.abs(n[:, axis]) > FRONT_NZ) & (np.abs(p[:, :, axis].mean(1) - value) <= band))[0]
    painted = np.zeros((T, T), bool)
    if not len(pick):
        return pick, painted
    uvp = face.uv_pixels(mesh["uv"], T)
    ras = face.rasterise(uvp, np.zeros(len(uvp)), faces, T, pick)
    covered = ras["tri"] >= 0
    pos = face.interpolate(verts, faces, ras)[covered]
    x, y, depth, col, row = _pixels(cam, pos)
    ok = (np.abs(pos[:, axis] - value) <= band) & region[row, col] & (depth <= seen[row, col] + 0.01 * size)
    qx, qy = _to_picture(frame["matrix"], pb[0] + (x - mb[0]) * kx, pb[1] + (y - mb[1]) * ky)
    ok &= face.sample(frame["alpha"][..., None], qx, qy)[:, 0] > 0.5
    rows, cols = np.nonzero(covered)
    rows, cols = rows[ok], cols[ok]
    colour = face.sample(frame["colour"], qx[ok], qy[ok])
    a = weight[row[ok], col[ok]][:, None]
    base[rows, cols] = base[rows, cols] * (1 - a) + colour * a
    painted[rows, cols] = True
    _fill_gutter(mesh, base, painted)
    return np.unique(ras["tri"][covered][ok]), painted


def apply_panels(glb: Path, base: np.ndarray, frame: dict[str, Any], picture: list[dict[str, Any]],
                 model: list[dict[str, Any]]) -> tuple[np.ndarray | None, list[dict[str, Any]]]:
    """Every panel the finder names on both the picture (seen from its camera) and the model render. `base` and the GLB
    change in place. -> (the triangles painted or None, one record per matched pair)."""
    out: list[np.ndarray] = []
    records: list[dict[str, Any]] = []
    for iou, a, b in match_panels(picture, model):
        rec: dict[str, Any] = {"kind": a["kind"], "overlap": round(float(iou), 2), "box": [int(v) for v in b["box"]], "applied": False}
        records.append(rec)
        mesh = face.load_mesh(glb)
        plane = None if mesh is None else wall_plane(mesh, frame["cam"], b["mask"], a["kind"])
        if plane is None:
            rec["reason"] = "no wall under the panel"
            continue
        rec["relief"] = round(plane["relief"], 4)
        band = BANDS[a["kind"]] * plane["size"]
        if a["kind"] in FLATTENED and plane["relief"] <= PANEL_RELIEF:
            inner = face.erode(b["mask"], INSET_PX + 1)

            def under(p: np.ndarray) -> np.ndarray:
                _, _, _, col, row = _pixels(frame["cam"], p)
                return inner[row, col] & (np.abs(p[:, plane["axis"]] - plane["value"]) <= band)

            rec["vertices_moved"] = move_vertices(glb, under, plane["axis"], plane["value"])
            mesh = face.load_mesh(glb)
        tris, painted = paint_angled(mesh, base, frame, plane, band, a, b)
        if not painted.any():
            rec["reason"] = "no texture under the panel"
            continue
        rec.update(applied=True, texels=int(painted.sum()))
        out.append(tris)
    return (np.unique(np.concatenate(out)) if out else None), records


# --- the whole step -----------------------------------------------------------------------------------------------


def apply(glb: Path, base: np.ndarray, picture: np.ndarray, picture_mask: np.ndarray, front_mask: np.ndarray,
          front_image: np.ndarray) -> tuple[np.ndarray | None, dict[str, Any]]:
    """Flatten and repaint the one board named on both pictures. `picture` is the step 1 picture (floats 0..1),
    `picture_mask` the finder's sign mask on it (its own size), `front_image` the square-on front render at VIEW pixels and
    `front_mask` the finder's mask on that. Changes `base` and the GLB in place only when a board matches.
    -> (the triangles painted or None, record)."""
    mesh = face.load_mesh(glb)
    if mesh is None:
        return None, {"applied": False, "reason": "no UVs"}
    pics = [p for p in (panel_in(picture, b) for b in boxes_of(picture_mask)) if p]
    models = [p for p in (panel_in(front_image, b) for b in boxes_of(front_mask)) if p]
    if not pics or not models:
        return None, {"applied": False, "reason": "no sign panel in the picture" if not pics else "no sign panel on the model's front"}

    def aspect(r: tuple[int, int, int, int]) -> float:
        return (r[2] - r[0]) / (r[3] - r[1])

    pairs = []
    for a in pics:
        for b in models:
            shape = abs(aspect(a["rect"]) / aspect(b["rect"]) - 1.0)
            colour = float(np.linalg.norm(a["colour"] - b["colour"]))
            if shape <= ASPECT_TOL and colour <= COLOUR_TOL:
                pairs.append((shape + colour / 100.0, a, b))
    if not pairs:
        return None, {"applied": False, "reason": "the picture's sign and the model's differ in shape or colour",
                      "picture_aspect": round(aspect(pics[0]["rect"]), 2), "model_aspect": round(aspect(models[0]["rect"]), 2)}
    _, pic, mod = min(pairs, key=lambda t: t[0])
    letters = lettering(picture, pic)
    rec: dict[str, Any] = {"letter_share": round(letters["share"], 3), "depth_share": round(letters["depth"], 3)}
    if not LETTER_SHARE[0] <= letters["share"] <= LETTER_SHARE[1]:
        return None, {**rec, "applied": False, "reason": "no lettering on the picture's panel"}
    cam = front_camera(mesh["verts"])
    x0, y0, x1, y1 = mod["rect"]
    left, top = to_model(cam, x0, y0)
    right, bottom = to_model(cam, x1, y1)
    rect = (left, bottom, right, top)
    plane = panel_plane(mesh, rect)
    if plane is None:
        return None, {**rec, "applied": False, "reason": "no surface under the sign"}
    rec["relief"] = round(plane["relief"], 4)
    band = PLANE_BAND * plane["size"]
    flat = letters["depth"] <= DEPTH_SHARE and plane["relief"] <= MAX_RELIEF
    moved = 0
    if flat:
        inset = INSET_PX * cam.scale / cam.size
        moved = move_to_plane(glb, (left + inset, bottom + inset, right - inset, top - inset), plane["z"], band)
        mesh = face.load_mesh(glb)
    tris, painted = paint_panel(mesh, base, rect, plane["z"], band, picture, pic["rect"])
    if not painted.any():
        return None, {**rec, "applied": False, "flattened": moved > 0, "reason": "no texture under the sign"}
    return tris, {**rec, "applied": True, "flattened": moved > 0, "vertices_moved": moved, "texels": int(painted.sum())}


def picture_path(prep_dir: Path) -> Path | None:
    """The step 1 picture as the picture paint of this step 4 sharpened it (up_source, or up_picture when the model was
    built from the step 1 picture itself); None when the picture paint did not run."""
    for name in ("up_source.png", "up_picture.png"):
        p = Path(prep_dir) / "srcproject" / name
        if p.is_file():
            return p
    return None


def read_picture(prep_dir: Path) -> np.ndarray | None:
    """The picture of picture_path, floats 0..1 on white."""
    p = picture_path(prep_dir)
    if p is None:
        return None
    im = Image.open(p)
    if im.mode == "RGBA":
        white = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(white, im)
    return np.asarray(im.convert("RGB"), dtype=np.float64) / 255.0
