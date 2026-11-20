"""Where a worn item sits on a humanoid, from the detector's votes (WP-69 automatic suggestion). Numpy only.

The suggestion pipeline (item_suggest.py) gives a vote per body vertex: the share of views in which the item's mask
covered the vertex's polygons. This module keeps the votes inside the item's expected place, tidies them into the
pieces that grow together (worn_region.grow_region's rules), and chooses the stiff points that reproduce the region
when the person keeps it.

Coordinates: the rest mesh in Blender's frame (Z up, front -Y), as the body dump gives it. Stored points are in the rig
GLB's own glTF frame; `to_gltf` converts (inverse of worn_region.gltf_to_blender).
"""
from __future__ import annotations

import numpy as np

from .blender import worn_region as wr

PLACE_BELOW_HIPS = 0.15  # the item may hang this far below the hips, as a share of the body's height
PLACE_SIDE = 1.4  # the item may stand this many shoulder half-widths from the middle line
PLACE_BEHIND = 0.06  # the item is behind the spine line by at least this share of the height
MAX_POINTS = 8
COVERAGE = 0.9  # the chosen points must grow back at least this share of the region
SPILL_SHARE = 0.05  # a point whose growth leaves the region by more than this share of its growth is not used
EXPECTED_SHARE = {"back": (0.02, 0.25)}  # a worn back item: the share of the body's vertices it is expected to take
LARGE_SHARE = 0.12  # a region over this share of the body's vertices is flagged "may include hair or a cape"


def frame(names, heads, co) -> dict:
    """The body's skeleton frame from its bones: height, hips height, spine depth, shoulder half-width (metres).
    ok is False when the skeleton does not name what the placement needs (no item suggestion then)."""
    co = np.asarray(co, dtype=float).reshape(-1, 3)
    names = [str(n) for n in names]
    low = [n.replace("mixamorig:", "").lower() for n in names]
    H = float(co[:, 2].max() - co[:, 2].min()) if len(co) else 0.0
    head_of = {n: np.asarray(h, dtype=float) for n, h in zip(names, heads)}

    def find(*words, exclude=()):
        for n, lw in zip(names, low):
            if all(w in lw for w in words) and not any(e in lw for e in exclude):
                return n
        return None

    hips = find("hips") or find("pelvis")
    chest = find("spine2") or find("chest") or find("spine1") or find("spine")
    la = find("left", "arm", exclude=("fore", "twist", "extra"))
    ra = find("right", "arm", exclude=("fore", "twist", "extra"))
    out = {"ok": False, "H": H}
    if not (hips and chest and la and ra) or H <= 0:
        out["reason"] = "the skeleton does not name hips, a spine, and both arms"
        return out
    out.update(ok=True, hips_z=float(head_of[hips][2]), chest_y=float(head_of[chest][1]),
               S=float(0.5 * (abs(head_of[la][0]) + abs(head_of[ra][0]))))
    return out


def expected_place(co, fr: dict) -> np.ndarray:
    """Vertices where a worn back item can be: from below the hips up to the top of the body, within the shoulders'
    width, and behind the spine line."""
    co = np.asarray(co, dtype=float).reshape(-1, 3)
    x, y, z = co[:, 0], co[:, 1], co[:, 2]
    H, hz, cy, S = fr["H"], fr["hips_z"], fr["chest_y"], fr["S"]
    return (z >= hz - PLACE_BELOW_HIPS * H) & (np.abs(x) <= PLACE_SIDE * S) & (y >= cy + PLACE_BEHIND * H)


def tidy(co, faces, allowed, min_piece=wr.MIN_PIECE_TIDY) -> np.ndarray:
    """The pieces of `allowed` that grow together (valley rule, within the set): a bool mask."""
    return wr.tidy_region(co, faces, allowed, min_piece=min_piece)


def points_for_region(co, faces, region, max_points: int = MAX_POINTS, coverage: float = COVERAGE):
    """Stiff points (Blender frame) whose growth reproduces the region, as the rig pass grows them (no limit to the
    region: a point's growth may not leave it). A candidate point is kept only when its growth stays inside the region;
    the points are chosen greedily for the most new coverage. A point may spill a few vertices past the region (up to
    SPILL_SHARE of its growth). Returns (points, covered_share)."""
    co = np.asarray(co, dtype=float).reshape(-1, 3)
    region = np.asarray(region, dtype=bool)
    idx = np.where(region)[0]
    if len(idx) == 0:
        return [], 0.0
    # candidates: the region's own vertices, thinned to a spread sample so the search stays small
    cands = [int(idx[0])]
    while len(cands) < min(len(idx), 60):
        d = np.min(np.linalg.norm(co[idx][:, None, :] - co[cands][None, :, :], axis=2), axis=1)
        cands.append(int(idx[int(np.argmax(d))]))
    grows = {}
    for c in cands:
        g = wr.grow_region(co, faces, co[c], radius=wr.RADIUS, limit=wr.SEED_SIZE)
        spill = int((g & ~region).sum())
        if g.any() and spill <= SPILL_SHARE * int(g.sum()):
            grows[c] = g
    chosen, covered = [], np.zeros(len(co), dtype=bool)
    while len(chosen) < max_points and grows:
        best = max(grows, key=lambda c: int((grows[c] & ~covered).sum()))
        gain = int((grows[best] & ~covered).sum())
        if gain == 0:
            break
        chosen.append(best)
        covered |= grows[best]
        del grows[best]
        if covered.sum() >= coverage * region.sum():
            break
    share = float((covered & region).sum() / max(1, region.sum()))
    return [list(map(float, co[c])) for c in chosen], share


def to_gltf(p) -> list[float]:
    """A Blender-frame point as the rig GLB's glTF frame (the inverse of worn_region.gltf_to_blender)."""
    bx, by, bz = p
    return [round(float(bx), 4), round(float(bz), 4), round(float(-by), 4)]


def vote_vertices(polys, face_votes: np.ndarray, nverts: int, min_votes: int) -> np.ndarray:
    """Per vertex: True when at least half of its polygons have at least min_votes view votes."""
    kept = face_votes >= min_votes
    vsum = np.zeros(nverts, dtype=np.int64)
    vcount = np.zeros(nverts, dtype=np.int64)
    for pi, p in enumerate(polys):
        for vi in p:
            vcount[vi] += 1
            if kept[pi]:
                vsum[vi] += 1
    return (vcount > 0) & (vsum * 2 >= vcount) & (vsum > 0)
