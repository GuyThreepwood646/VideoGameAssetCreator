"""Shape loss (WP-16, metrics only): how far the budget reduction moved the surface of a model.

Loaded by prep.py inside Blender (the same way as gridfit.py and headweight.py; not imported by the backend except by
the tests). Pure numpy: prep.py supplies the nearest-surface distances (mathutils BVHTree) as callables, so the
sampling, the summary and the regions are testable without Blender.

Method: a few thousand points are drawn on each surface (by area, seeded so a model gives the same numbers every run).
Each point's distance to the nearest point of the other surface is measured. "lost" is the pre-reduction surface
measured against the final one (where the reduction took shape away); "added" is the final surface measured against
the pre-reduction one (where it bulged or moved). Every distance is a share of the model's height: the Z extent of the
final mesh in world units (the same height the prep report calls height_m). Metrics only: nothing is warned or
blocked on these numbers until they are calibrated on real models.
"""
import numpy as np

SEED = 16
SAMPLES_CAP = 4000  # points per surface: enough for stable percentiles, a few tens of milliseconds of BVH queries
THRESHOLD_SHARE = 0.01  # a sample counts as lost when it is more than 1 percent of the height away
MIN_REGION_SAMPLES = 20  # a region with fewer sampled points is too noisy to name
REGION_ROWS = ("bottom", "middle", "top")  # thirds of the height, bottom first
REGION_COLS = ("x_low", "x_high")  # halves of the X extent (Blender X), low half first


def sample_surface(vertices, triangles, n, seed=SEED):
    """n points on the surface, each drawn on a triangle chosen by area and placed uniformly inside it.

    vertices: (V, 3) float array; triangles: (T, 3) integer array of vertex indices. Returns an (n, 3) array, empty when
    the surface has no area. The same seed gives the same points.
    """
    vertices = np.asarray(vertices, dtype=float)
    triangles = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
    if n <= 0 or len(triangles) == 0:
        return np.zeros((0, 3))
    a, b, c = vertices[triangles[:, 0]], vertices[triangles[:, 1]], vertices[triangles[:, 2]]
    areas = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    total = float(areas.sum())
    if total <= 0.0:
        return np.zeros((0, 3))
    rng = np.random.default_rng(seed)
    pick = rng.choice(len(triangles), size=n, p=areas / total)
    r1, r2 = rng.random(n), rng.random(n)
    s = np.sqrt(r1)  # uniform inside a triangle: the square-root warp of the first draw
    wa, wb, wc = 1.0 - s, s * (1.0 - r2), s * r2
    return wa[:, None] * a[pick] + wb[:, None] * b[pick] + wc[:, None] * c[pick]


def summarize(distances, height):
    """Mean, 95th percentile and maximum of the distances, each as a share of the height, and the share of samples
    more than THRESHOLD_SHARE of the height away. Values are None when there are no samples or no height."""
    d = np.asarray(distances, dtype=float)
    if d.size == 0 or not height or height <= 0:
        return {"mean": None, "p95": None, "max": None, "over_threshold": None, "samples": int(d.size)}
    rel = d / height
    return {
        "mean": round(float(rel.mean()), 6),
        "p95": round(float(np.percentile(rel, 95)), 6),
        "max": round(float(rel.max()), 6),
        "over_threshold": round(float((rel > THRESHOLD_SHARE).mean()), 4),
        "samples": int(d.size),
    }


def region_index(points, lo, hi):
    """The region of each point: 0..5, row * 2 + column, with row 0 the bottom third of the height (Z), 2 the top,
    and column 0 the lower half of the X extent, 1 the upper half."""
    p = np.asarray(points, dtype=float).reshape(-1, 3)
    lo, hi = np.asarray(lo, dtype=float), np.asarray(hi, dtype=float)
    h = max(float(hi[2] - lo[2]), 1e-12)
    t = (p[:, 2] - lo[2]) / h
    row = np.where(t < 1.0 / 3.0, 0, np.where(t < 2.0 / 3.0, 1, 2))
    col = (p[:, 0] >= (lo[0] + hi[0]) / 2.0).astype(int)
    return row * 2 + col


def region_name(index):
    return f"{REGION_ROWS[index // 2]} {REGION_COLS[index % 2]}"


def worst_regions(points, distances, lo, hi, height, k=3):
    """The k regions with the largest mean distance (a share of the height), among regions with at least
    MIN_REGION_SAMPLES points. Each entry: {"region", "mean", "samples"}."""
    d = np.asarray(distances, dtype=float)
    if d.size == 0 or not height or height <= 0:
        return []
    labels = region_index(points, lo, hi)
    found = []
    for i in range(len(REGION_ROWS) * len(REGION_COLS)):
        m = labels == i
        count = int(m.sum())
        if count < MIN_REGION_SAMPLES:
            continue
        found.append({"region": region_name(i), "mean": round(float(d[m].mean()) / height, 6), "samples": count})
    found.sort(key=lambda r: r["mean"], reverse=True)
    return found[:k]


def measure(pre_vertices, pre_triangles, fin_vertices, fin_triangles, dist_to_final, dist_to_pre,
            samples=SAMPLES_CAP, seed=SEED):
    """The shape_loss block for prep's report.

    dist_to_final(points) and dist_to_pre(points) return the distance from each point (an (n, 3) array) to the nearest
    point of the final and the pre-reduction surface. All coordinates are world units in the same space.
    """
    fin = np.asarray(fin_vertices, dtype=float).reshape(-1, 3)
    if len(fin) == 0:
        raise ValueError("the final mesh has no vertices")
    lo, hi = fin.min(axis=0), fin.max(axis=0)
    height = float(hi[2] - lo[2])
    n = max(0, min(int(samples), SAMPLES_CAP))
    lost_pts = sample_surface(pre_vertices, pre_triangles, n, seed)
    added_pts = sample_surface(fin_vertices, fin_triangles, n, seed)
    lost_d = dist_to_final(lost_pts) if len(lost_pts) else np.zeros(0)
    added_d = dist_to_pre(added_pts) if len(added_pts) else np.zeros(0)
    return {
        "height_m": round(height, 6),
        "height_measure": "Z extent of the final mesh (world units)",
        "samples": n,
        "seed": seed,
        "threshold_share": THRESHOLD_SHARE,
        "lost": summarize(lost_d, height),
        "added": summarize(added_d, height),
        "worst_regions": worst_regions(lost_pts, lost_d, lo, hi, height),
    }
