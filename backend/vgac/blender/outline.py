"""Outline check (WP-16 follow-up): did the budget reduction change what the model looks like from the side?

Loaded by prep.py inside Blender (like shapeloss.py). Pure numpy: prep.py supplies a BVH tree per mesh (mathutils
BVHTree) and this module casts parallel rays through it, so the grids, the comparison and the regions are testable
without Blender (a stand-in tree with a ray_cast method is enough).

Why it exists: shape_loss measures distance to the nearest surface, so a thin part that collapses flat stays "near" its old
surface and a hole that is filled over stays near the rim; a silhouette cannot hide either. The pre-reduction copy and the
final mesh are drawn as black-and-white outlines from the same fixed views, on the same pixel grid (the box around both),
and compared.

Views: six around the model at 30 degree steps (0 to 150; an orthographic outline from the opposite side is the same
picture mirrored, so 180 to 330 would add nothing), plus the top (the bottom is the top's mirror, also nothing new).
"""
import math

import numpy as np

RESOLUTION = 192  # pixels along the longer side of each view
PAD_SHARE = 0.02  # empty margin around the two meshes, as a share of the longer side
TOLERANCE_PX = 2  # an outline pixel counts as lost or added only when it is more than this far from the other outline
AZIMUTHS = (0, 30, 60, 90, 120, 150)  # degrees around Z (Blender), ray direction (cos, sin, 0)
VIEW_NAMES = tuple(f"side {a}" for a in AZIMUTHS) + ("top",)


def view_axes():
    """(name, ray direction, image right axis, image up axis) of each view, as float arrays."""
    out = []
    for a in AZIMUTHS:
        r = math.radians(a)
        d = np.array([math.cos(r), math.sin(r), 0.0])
        out.append((f"side {a}", d, np.array([-math.sin(r), math.cos(r), 0.0]), np.array([0.0, 0.0, 1.0])))
    out.append(("top", np.array([0.0, 0.0, -1.0]), np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])))
    return out


def plan_views(lo, hi, resolution=RESOLUTION):
    """The pixel grid of each view, shared by both meshes: lo and hi are the corners of the box around BOTH meshes.

    Returns a list of dicts: name, d, u, v, origin (the world point of pixel (0, 0)'s ray start), px (pixel size in world
    units), nu, nv (grid width and height), umin, vmin (the grid's corner in image coordinates).
    """
    lo, hi = np.asarray(lo, dtype=float), np.asarray(hi, dtype=float)
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    centre = (lo + hi) / 2.0
    reach = float(np.linalg.norm(hi - lo)) + 1e-9
    plans = []
    for name, d, u, v in view_axes():
        us, vs = corners @ u, corners @ v
        span = max(float(us.max() - us.min()), float(vs.max() - vs.min()), 1e-9)
        pad = span * PAD_SHARE
        px = (span + 2 * pad) / resolution
        umin, vmin = float(us.min()) - pad, float(vs.min()) - pad
        nu = int(math.ceil((float(us.max()) + pad - umin) / px))
        nv = int(math.ceil((float(vs.max()) + pad - vmin) / px))
        plans.append({"name": name, "d": d, "u": u, "v": v, "px": px, "nu": nu, "nv": nv, "umin": umin, "vmin": vmin,
                      "centre": centre, "reach": reach})
    return plans


def silhouette(tree, plan, vertices=None):
    """The outline of one mesh in one view: a (nv, nu) bool array, True where a ray through the pixel centre hits the mesh.

    tree.ray_cast(origin, direction) returns a location (or None on a miss), like mathutils BVHTree. vertices (V, 3)
    limits the rays to the pixels the mesh's own box covers.
    """
    nv, nu = plan["nv"], plan["nu"]
    mask = np.zeros((nv, nu), dtype=bool)
    u, v, d, px = plan["u"], plan["v"], plan["d"], plan["px"]
    i0, i1, j0, j1 = 0, nu, 0, nv
    if vertices is not None and len(vertices):
        pts = np.asarray(vertices, dtype=float)
        us, vs = pts @ u, pts @ v
        i0 = max(0, int(math.floor((us.min() - plan["umin"]) / px)) - 1)
        i1 = min(nu, int(math.ceil((us.max() - plan["umin"]) / px)) + 1)
        j0 = max(0, int(math.floor((vs.min() - plan["vmin"]) / px)) - 1)
        j1 = min(nv, int(math.ceil((vs.max() - plan["vmin"]) / px)) + 1)
    # A ray starts one reach behind the centre of the box and runs along d; the (u, v) position picks the pixel.
    base = plan["centre"] - d * plan["reach"] - (plan["centre"] @ u) * u - (plan["centre"] @ v) * v
    direction = tuple(float(x) for x in d)
    for j in range(j0, j1):
        vv = plan["vmin"] + (j + 0.5) * px
        row_base = base + v * vv
        for i in range(i0, i1):
            o = row_base + u * (plan["umin"] + (i + 0.5) * px)
            if tree.ray_cast((float(o[0]), float(o[1]), float(o[2])), direction)[0] is not None:
                mask[j, i] = True
    return mask


def dilate(mask, steps):
    """The mask grown by `steps` pixels in all eight directions."""
    m = np.asarray(mask, dtype=bool)
    for _ in range(max(0, int(steps))):
        p = np.pad(m, 1)
        grown = np.zeros_like(m)
        for dy in (0, 1, 2):
            for dx in (0, 1, 2):
                grown |= p[dy:dy + m.shape[0], dx:dx + m.shape[1]]
        m = grown
    return m


def largest_region(mask):
    """The pixel count of the largest 8-connected group of True pixels (0 when there is none)."""
    m = np.asarray(mask, dtype=bool)
    seen = np.zeros_like(m)
    best = 0
    h, w = m.shape
    for y0, x0 in zip(*np.nonzero(m)):
        if seen[y0, x0]:
            continue
        stack, count = [(int(y0), int(x0))], 0
        seen[y0, x0] = True
        while stack:
            y, x = stack.pop()
            count += 1
            for ny in (y - 1, y, y + 1):
                for nx in (x - 1, x, x + 1):
                    if 0 <= ny < h and 0 <= nx < w and m[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        best = max(best, count)
    return best


def compare_views(pre, fin, tolerance=TOLERANCE_PX):
    """One view: IoU of the two outlines, and the largest region lost (pre only) and added (final only), as a share of the
    pre-reduction outline's area. A pixel counts as lost only when it is more than `tolerance` pixels from the final
    outline (and added likewise), so the one-pixel disagreement along a clean reduction's edge is not a region."""
    pre, fin = np.asarray(pre, dtype=bool), np.asarray(fin, dtype=bool)
    union = int((pre | fin).sum())
    area = int(pre.sum())
    lost = pre & ~dilate(fin, tolerance)
    added = fin & ~dilate(pre, tolerance)
    return {
        "iou": round(float((pre & fin).sum()) / union, 4) if union else 1.0,
        "lost_region": round(largest_region(lost) / area, 4) if area else 0.0,
        "added_region": round(largest_region(added) / area, 4) if area else 0.0,
        "area_px": area,
    }


def measure(pre_tree, fin_tree, pre_vertices, fin_vertices, resolution=RESOLUTION):
    """The outline block for prep's report: per view numbers and the worst of them.

    iou_min: the lowest IoU over the views (and which view). region_max: the largest lost-or-added region over the views,
    as a share of that view's pre-reduction outline, with its view and kind ("lost" or "added")."""
    pv = np.asarray(pre_vertices, dtype=float).reshape(-1, 3)
    fv = np.asarray(fin_vertices, dtype=float).reshape(-1, 3)
    if len(pv) == 0 or len(fv) == 0:
        raise ValueError("a mesh has no vertices")
    both = np.vstack([pv, fv])
    plans = plan_views(both.min(axis=0), both.max(axis=0), resolution)
    views = {}
    for plan in plans:
        views[plan["name"]] = compare_views(silhouette(pre_tree, plan, pv), silhouette(fin_tree, plan, fv))
    worst_iou = min(views, key=lambda k: views[k]["iou"])
    best_region, best_kind, best_view = -1.0, "lost", None
    for k, r in views.items():
        for kind in ("lost", "added"):
            if r[f"{kind}_region"] > best_region:
                best_region, best_kind, best_view = r[f"{kind}_region"], kind, k
    return {
        "resolution": resolution,
        "tolerance_px": TOLERANCE_PX,
        "views": views,
        "iou_min": views[worst_iou]["iou"],
        "iou_min_view": worst_iou,
        "region_max": max(best_region, 0.0),
        "region_max_kind": best_kind,
        "region_max_view": best_view,
    }
