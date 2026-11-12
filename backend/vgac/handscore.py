"""Hand score: how well the 3D model resolved its hands (docs/HANDS-PLAN.md, phase 0).

Pure functions on a loaded mesh (face.load_mesh: y up, +z toward the viewer, +x the character's left). Nothing here
changes the product; bakeoff/hands_measure.py runs it and renders the close-up sheets.

Per hand:
- find the hand tip: the vertex farthest out sideways on that side (the arm's far end in the A or T pose);
- the arm axis runs from the shoulder (on the centre line, SHOULDER_SHARE of the height up) to the tip;
- the wrist sits HAND_SHARE of the model's height back from the tip along the axis (anthropometric: a hand is about a
  tenth of the body height; stylised heads and gloves vary, so the result is checked by eye on the sheet);
- cross-sections: the surface cut by a plane across the axis at 70 percent of the hand length, drawn into a grid, the
  holes filled, and the separate pieces counted (4 or 5 is fingers, 1 is a mitten or blob; pieces under MIN_PIECE of
  the largest are specks and not counted);
- triangles beyond the wrist and the count of spike triangles among them (a long thin sliver: longest edge over the
  triangle's shortest altitude above SPIKE_RATIO).
"""

from __future__ import annotations

from typing import Any

import numpy as np

SHOULDER_X = 0.13  # shoulder joint's sideways offset as a share of the height
ARM_X = 0.16  # outside this sideways offset (above the knees) only arms are found: legs stay inside it
RADIUS = 0.55  # the cut and the triangle count only use surface this close to the arm axis (in hand lengths)
MIN_RADIUS = 3.0  # grid cells: a piece whose thickest point is thinner than this is a sliver, not a finger
SHOULDER_SHARE = 0.80  # shoulder height as a share of the model's height above its lowest point
HAND_SHARE = 0.105  # hand length as a share of the model's height
CUT_AT = 0.70  # where along the hand (from the wrist) the cross-section is taken
SLAB = 0.035  # unused width reserved for a thick cut (share of hand length); the cut itself is exact
GRID = 192  # cells across the cross-section grid (a finger gap on a flat hand is about 3 cells)
MIN_PIECE = 0.06  # a piece smaller than this share of the largest piece is a speck
SPIKE_RATIO = 12.0


def _unit(v: np.ndarray) -> np.ndarray:
    return v / max(float(np.linalg.norm(v)), 1e-12)


def find_hand(verts: np.ndarray, side: int) -> dict[str, Any]:
    """The geometry of one hand: side is +1 (the character's left, +x) or -1. Returns tip, axis (unit, shoulder to
    tip), wrist, length, height."""
    lo, hi = float(verts[:, 1].min()), float(verts[:, 1].max())
    height = hi - lo
    # The vertex farthest from the shoulder joint, among those outside the torso and above the knees: the fingertips of
    # an arm held out or hanging (the farthest-out point is a cuff or a flared sleeve on a hanging arm).
    shoulder = np.array([side * SHOULDER_X * height, lo + SHOULDER_SHARE * height, float(np.median(verts[:, 2]))])
    arm = verts[(side * verts[:, 0] > ARM_X * height) & (verts[:, 1] > lo + 0.3 * height)]
    if len(arm) == 0:  # no arm out beside the body (a figure with its arms at its sides): the farthest-out vertex on that side
        arm = verts
    tip = arm[int(np.argmax(np.linalg.norm(arm - shoulder, axis=1)))]
    axis = _unit(tip - shoulder)
    length = HAND_SHARE * height
    wrist = tip - axis * length
    return {"tip": tip, "axis": axis, "wrist": wrist, "length": length, "height": height, "side": side}


def _plane_frame(axis: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    ref = np.array([0.0, 0.0, 1.0]) if abs(axis[2]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = _unit(np.cross(axis, ref))
    return u, np.cross(axis, u)


def cross_section(verts: np.ndarray, faces: np.ndarray, hand: dict[str, Any], at: float = CUT_AT) -> dict[str, Any]:
    """Cut the hand across its axis at `at` of its length: {pieces, areas, mask, extent}. mask is the filled
    cross-section grid (GRID x GRID), extent its half width in metres."""
    import cv2

    from . import face

    axis, wrist, L = hand["axis"], hand["wrist"], hand["length"]
    t = (verts - wrist) @ axis
    t0 = at * L
    f = faces
    ta, tb, tc = t[f[:, 0]] - t0, t[f[:, 1]] - t0, t[f[:, 2]] - t0
    cross = (np.minimum(np.minimum(ta, tb), tc) < 0) & (np.maximum(np.maximum(ta, tb), tc) >= 0)
    # Only the hand's own surroundings: within 1.2 hand lengths of the cut centre, on this side of the body.
    centre = wrist + axis * t0
    d = verts[f].mean(1) - wrist
    radial = np.linalg.norm(d - (d @ axis)[:, None] * axis, axis=1)
    near = radial < RADIUS * L
    sel = np.where(cross & near)[0]
    u, w = _plane_frame(axis)
    half = 0.9 * L
    pts: list[np.ndarray] = []
    for i in sel:
        p = verts[f[i]]
        d = t[f[i]] - t0
        hit = []
        for a, b in ((0, 1), (1, 2), (2, 0)):
            if (d[a] < 0) != (d[b] < 0):
                s = d[a] / (d[a] - d[b])
                hit.append(p[a] + s * (p[b] - p[a]))
        if len(hit) == 2:
            for s in np.linspace(0, 1, 24):
                pts.append(hit[0] + s * (hit[1] - hit[0]))
    mask = np.zeros((GRID, GRID), dtype=bool)
    if not pts:
        return {"pieces": 0, "areas": [], "mask": mask, "extent": half}
    P = np.array(pts) - centre
    x = ((P @ u) / half * 0.5 + 0.5) * (GRID - 1)
    y = ((P @ w) / half * 0.5 + 0.5) * (GRID - 1)
    xi, yi = np.clip(np.round(x).astype(int), 0, GRID - 1), np.clip(np.round(y).astype(int), 0, GRID - 1)
    mask[yi, xi] = True
    # Two closing strengths: the tight one keeps narrow finger gaps open but leaves a hole in the surface as an open
    # ring; the loose one closes such rings but can merge neighbouring fingers. The larger count stands.
    best: dict[str, Any] | None = None
    for r in (1, 2, 4, 6):
        res = _pieces(face.erode(face.fill_holes(face.dilate_mask(mask, r)), r))
        if best is None or res["pieces"] > best["pieces"]:
            best = res
    best["extent"] = half
    return best


def _pieces(mask: np.ndarray) -> dict[str, Any]:
    import cv2

    n, lab = cv2.connectedComponents(mask.astype(np.uint8), connectivity=8)
    if n <= 1:
        return {"pieces": 0, "areas": [], "mask": mask}
    areas = np.bincount(lab.ravel())[1:].astype(float)
    keep = areas >= MIN_PIECE * areas.max()
    # A thin sliver (a tabard hem or a cape edge that the cut crosses) is not a finger.
    for k in range(1, n):
        if keep[k - 1]:
            dist = cv2.distanceTransform((lab == k).astype(np.uint8), cv2.DIST_L2, 3)
            if dist.max() < MIN_RADIUS:
                keep[k - 1] = False
    return {"pieces": int(keep.sum()), "areas": [int(a) for a in areas[keep]], "mask": np.isin(lab, np.where(keep)[0] + 1)}


def hand_triangles(verts: np.ndarray, faces: np.ndarray, hand: dict[str, Any]) -> np.ndarray:
    """Indices of the triangles beyond the wrist (centre past the wrist plane, on the hand's side)."""
    c = verts[faces].mean(1)
    d = c - hand["wrist"]
    t = d @ hand["axis"]
    radial = np.linalg.norm(d - t[:, None] * hand["axis"], axis=1)
    L = hand["length"]
    return np.where((t > 0) & (t < 1.2 * L) & (radial < RADIUS * L))[0]


def spike_count(verts: np.ndarray, faces: np.ndarray, idx: np.ndarray) -> int:
    if len(idx) == 0:
        return 0
    p = verts[faces[idx]]
    e = np.stack([np.linalg.norm(p[:, 1] - p[:, 0], axis=1), np.linalg.norm(p[:, 2] - p[:, 1], axis=1), np.linalg.norm(p[:, 0] - p[:, 2], axis=1)], 1)
    area = 0.5 * np.linalg.norm(np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1)
    longest = e.max(1)
    alt = 2.0 * area / np.maximum(longest, 1e-12)  # altitude on the longest edge = the shortest altitude
    return int(((longest / np.maximum(alt, 1e-12)) > SPIKE_RATIO).sum())


def score(mesh: dict[str, np.ndarray]) -> dict[str, Any]:
    """{left, right: {pieces, triangles, spikes, length_m, tip, wrist}, total_triangles, hand_share}."""
    verts, faces = mesh["verts"], mesh["faces"]
    out: dict[str, Any] = {"total_triangles": int(len(faces))}
    hand_tris = 0
    for name, side in (("left", 1), ("right", -1)):
        h = find_hand(verts, side)
        cs = cross_section(verts, faces, h)
        cs90 = cross_section(verts, faces, h, 0.9)
        idx = hand_triangles(verts, faces, h)
        hand_tris += len(idx)
        out[name] = {"pieces": cs["pieces"], "pieces_90": cs90["pieces"], "areas": cs["areas"], "triangles": int(len(idx)), "spikes": spike_count(verts, faces, idx),
                     "length_m": round(float(h["length"]), 4), "tip": [round(float(v), 4) for v in h["tip"]], "wrist": [round(float(v), 4) for v in h["wrist"]]}
    out["hand_share"] = round(hand_tris / max(1, len(faces)), 5)
    return out
