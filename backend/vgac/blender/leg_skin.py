"""Blender headless: leg and hip skin pass on a rigged GLB (WP-18), run after rig_fix and the joins.

Run: blender -b --python leg_skin.py -- <rig.glb> <out.glb> --report <json> [--passes 4] [--alpha 0.5]
     [--band 0.03] [--half 0] [--no-keep-dominant] [--max-change 0.3]

Three changes, all on the skin weights of the leg zone only (vertices with at least LEG_MASS_MIN of their weight on
thigh, calf, foot or toe bones). Bone lengths, the rest mesh and the animation are not touched.
1. Smoothing (on by default, PASSES 4): each zone vertex takes ALPHA of its neighbours' average weights, but only
   where all its neighbours are in the zone too, so the edge of the zone (the hip and the crotch) is not pulled.
2. Knee transition (off by default, HALF 0): thigh and calf weights are re-split by height about the knee joint.
   Inside HALF of the body height either side of the knee the calf share runs from 0 to 1 over BAND (a 2 BAND wide
   ramp centred on the knee). Tried at HALF 0.10 it raised leg stretch on five of eight rigs, so it stays off.
3. Influences: every zone vertex keeps its four largest weights and the rest are normalised to sum 1.

Everything above main() is plain numpy so the backend tests can run it without Blender.
"""
import json
import os
import sys

import numpy as np

LEG_MASS_MIN = 0.05  # share of a vertex's weight on leg bones from which it is in the leg zone
MAX_INFLUENCES = 4
MAX_CHANGE = 0.3  # a smoothed row whose weights moved by more than this (sum of absolute changes) keeps its own


def leg_group_kind(name):
    """'thigh', 'calf', 'foot', 'left' or 'right' tags for a bone name, or None when it is not a leg bone."""
    n = str(name or "").replace("mixamorig:", "").lower()
    if "upleg" in n or "thigh" in n:
        kind = "thigh"
    elif "foot" in n or "toe" in n or "ankle" in n:
        kind = "foot"
    elif n.endswith("leg") or "calf" in n or n.startswith("leg") or "leg" in n:
        kind = "calf"
    else:
        return None
    side = "left" if "left" in n or n.startswith("l_") or n.startswith("l ") else ("right" if "right" in n or n.startswith("r_") else None)
    return kind, side


def leg_zone(W, leg_cols):
    """Boolean per vertex: the share of its weight on leg columns is at least LEG_MASS_MIN."""
    if not leg_cols:
        return np.zeros(W.shape[0], dtype=bool)
    mass = W[:, leg_cols].sum(axis=1)
    return mass >= LEG_MASS_MIN


def neighbour_sums(W, edges):
    """Sum of neighbour weights per vertex, and the neighbour count and the all-neighbours-in-zone flag come from the
    caller. edges: (E, 2) vertex index pairs."""
    V, G = W.shape
    acc = np.zeros((V, G))
    deg = np.zeros(V)
    if len(edges):
        a, b = edges[:, 0], edges[:, 1]
        np.add.at(acc, a, W[b])
        np.add.at(acc, b, W[a])
        np.add.at(deg, a, 1.0)
        np.add.at(deg, b, 1.0)
    return acc, deg


def smooth_zone(W, edges, zone, passes=4, alpha=0.5, keep_dominant=True, max_change=MAX_CHANGE):
    """Smooth the weights of the zone vertices whose neighbours are all in the zone. Returns (new W, changed count).
    Rows are kept normalised where they were. With keep_dominant (default) a vertex whose strongest bone would change
    keeps its original weights: on a dwarf's cloak hem, smoothing handed hem vertices to a leg bone's neighbours and
    the hem tore into spikes at the knee (WP-18 review, frame 8 of the run clip)."""
    W = np.array(W, dtype=float, copy=True)
    if not np.any(zone) or not len(edges):
        return W, 0
    orig = W.copy()
    V = W.shape[0]
    # A vertex is smoothable when every neighbour is in the zone.
    bad = np.zeros(V, dtype=bool)
    a, b = edges[:, 0], edges[:, 1]
    np.logical_or.at(bad, a, ~zone[b])
    np.logical_or.at(bad, b, ~zone[a])
    movable = zone & ~bad
    changed = np.zeros(V, dtype=bool)
    for _ in range(max(0, int(passes))):
        acc, deg = neighbour_sums(W, edges)
        ok = movable & (deg > 0)
        if not np.any(ok):
            break
        avg = acc[ok] / deg[ok, None]
        W[ok] = (1.0 - alpha) * W[ok] + alpha * avg
        changed |= ok
    if keep_dominant:
        moved = changed & (np.argmax(W, axis=1) != np.argmax(orig, axis=1))
        W[moved] = orig[moved]
        changed &= ~moved
    if max_change > 0:
        # Rows whose weights moved by more than max_change (sum of absolute changes) keep their original weights.
        far = changed & (np.abs(W - orig).sum(axis=1) > max_change)
        W[far] = orig[far]
        changed &= ~far
    return W, int(changed.sum())


def narrow_knee(W, z, zone, thigh_cols, calf_cols, knee_z, height, band=0.03, half=0.10):
    """Re-split thigh and calf weight by height about the knee. Only zone vertices within `half` * height of the knee
    change. Returns (new W, count changed)."""
    W = np.array(W, dtype=float, copy=True)
    if height <= 0 or not thigh_cols or not calf_cols:
        return W, 0
    band_m = max(1e-6, band * height)
    half_m = half * height
    near = zone & (np.abs(z - knee_z) <= half_m)
    tot = W[:, thigh_cols].sum(axis=1) + W[:, calf_cols].sum(axis=1)
    sel = near & (tot > 1e-9)
    if not np.any(sel):
        return W, 0
    rows = np.where(sel)[0]
    # Calf share: 0 above the knee, 1 below it, a linear ramp of 2 * band_m centred on the knee.
    calf_share = np.clip(0.5 - (z[rows] - knee_z) / (2.0 * band_m), 0.0, 1.0)
    # Within each group the columns keep their own proportions (left and right legs never share a column pair).
    for cols, share in ((thigh_cols, 1.0 - calf_share), (calf_cols, calf_share)):
        sub = W[np.ix_(rows, cols)]
        s = sub.sum(axis=1)
        frac = np.where(s[:, None] > 1e-12, sub / np.maximum(s[:, None], 1e-12), 0.0)
        W[np.ix_(rows, cols)] = frac * (tot[rows] * share)[:, None]
    return W, int(len(rows))


def cap_influences(W, k=MAX_INFLUENCES, rows=None):
    """Keep the k largest weights of each row (rows: a boolean mask, default all) and normalise to sum 1.
    Returns (new W, rows whose influence count was above k before)."""
    W = np.array(W, dtype=float, copy=True)
    idx = np.arange(W.shape[0]) if rows is None else np.where(rows)[0]
    if len(idx) == 0:
        return W, 0
    sub = W[idx]
    over = int(np.sum((sub > 1e-6).sum(axis=1) > k))
    if sub.shape[1] > k:
        cut = -np.sort(-sub, axis=1)[:, k - 1:k]
        sub = np.where(sub >= cut, sub, 0.0)
        # Ties at the cut can keep more than k; take the first k in column order among them.
        extra = (sub > 1e-6).sum(axis=1) > k
        for r in np.where(extra)[0]:
            cols = np.where(sub[r] > 1e-6)[0]
            keep = cols[np.argsort(-sub[r, cols], kind="stable")][:k]
            row = np.zeros(sub.shape[1])
            row[keep] = sub[r, keep]
            sub[r] = row
    s = sub.sum(axis=1)
    sub = np.where(s[:, None] > 1e-12, sub / np.maximum(s[:, None], 1e-12), sub)
    W[idx] = sub
    return W, over


def max_influences(W):
    return int((np.asarray(W) > 1e-6).sum(axis=1).max()) if np.size(W) else 0


def run_numpy(W, edges, z, names, knee_z, height, passes=4, alpha=0.5, band=0.03, half=0.0, keep_dominant=True,
              max_change=MAX_CHANGE):
    """The whole pass on arrays. names: one bone name per column. Returns (W, report dict)."""
    kinds = [leg_group_kind(n) for n in names]
    leg_cols = [i for i, k in enumerate(kinds) if k]
    zone = leg_zone(W, leg_cols)
    report = {"zone_vertices": int(zone.sum()), "max_influences_before": max_influences(W), "passes": passes,
              "alpha": alpha, "band": band, "half": half}
    W1, smoothed = smooth_zone(W, edges, zone, passes=passes, alpha=alpha, keep_dominant=keep_dominant,
                               max_change=max_change)
    report["smoothed_vertices"] = smoothed
    thigh_cols, calf_cols = [], []
    for side in ("left", "right"):
        t = [i for i, k in enumerate(kinds) if k and k[0] == "thigh" and k[1] == side]
        c = [i for i, k in enumerate(kinds) if k and k[0] == "calf" and k[1] == side]
        if t and c:
            thigh_cols += t
            calf_cols += c
    if half > 0 and thigh_cols and calf_cols and knee_z is not None:
        W2, narrowed = narrow_knee(W1, z, zone, thigh_cols, calf_cols, knee_z, height, band=band, half=half)
        report.update(narrowed_vertices=narrowed, knee_z=round(float(knee_z), 4))
    else:
        W2 = W1
        report.update(narrowed_vertices=0, knee_z=None,
                      reason="knee transition off" if half <= 0 else "no thigh and calf pair")
    W3, over = cap_influences(W2, rows=zone)
    report.update(over_four_before_cap=over, max_influences_after=max_influences(W3),
                  row_sum_error=float(np.abs(W3[zone].sum(axis=1) - 1.0).max()) if np.any(zone) else 0.0)
    return W3, report


# ---- Blender side -------------------------------------------------------------------------------------------------


def main():  # pragma: no cover - runs inside Blender
    import bmesh  # noqa: F401
    import bpy
    from mathutils import Vector  # noqa: F401

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _common  # noqa: E402

    argv = _common.after_double_dash()
    src, dst = os.path.abspath(argv[0]), os.path.abspath(argv[1])
    report_path = _common.opt("--report", None)
    passes = int(_common.opt("--passes", "4"))
    alpha = float(_common.opt("--alpha", "0.5"))
    band = float(_common.opt("--band", "0.03"))
    half = float(_common.opt("--half", "0"))
    keep_dom = not _common.flag("--no-keep-dominant")
    max_change = float(_common.opt("--max-change", str(MAX_CHANGE)))
    _common.open_glb(src)
    scene = bpy.context.scene
    arm = next((o for o in scene.objects if o.type == "ARMATURE"), None)
    meshes = [o for o in scene.objects if o.type == "MESH" and o.vertex_groups]
    report = {"ok": False, "meshes": []}
    if arm is None or not meshes:
        report["reason"] = "no armature or no skinned mesh"
    else:
        names = [b.name for b in arm.data.bones]
        bone_world = {b.name: arm.matrix_world @ b.head_local for b in arm.data.bones}
        for o in meshes:
            vg_names = [g.name for g in o.vertex_groups]
            V = len(o.data.vertices)
            W = np.zeros((V, len(vg_names)))
            for v in o.data.vertices:
                for g in v.groups:
                    W[v.index, g.group] = g.weight
            co = np.array([tuple(o.matrix_world @ v.co) for v in o.data.vertices], dtype=float).reshape(-1, 3)
            edges = np.array([[e.vertices[0], e.vertices[1]] for e in o.data.edges], dtype=int).reshape(-1, 2)
            height = float(co[:, 2].max() - co[:, 2].min()) if V else 0.0
            knee_z = None
            for side in ("left", "right"):
                for n in names:
                    k = leg_group_kind(n)
                    if k and k[0] == "calf" and k[1] == side and n in bone_world:
                        zk = float(bone_world[n].z)
                        knee_z = zk if knee_z is None else (knee_z + zk) / 2
            W2, rep = run_numpy(W, edges, co[:, 2], vg_names, knee_z, height, passes=passes, alpha=alpha, band=band, half=half,
                                 keep_dominant=keep_dom, max_change=max_change)
            for gi, g in enumerate(o.vertex_groups):
                g.remove([v.index for v in o.data.vertices])
            for gi, g in enumerate(o.vertex_groups):
                for vi in np.where(W2[:, gi] > 1e-6)[0]:
                    g.add([int(vi)], float(W2[vi, gi]), "REPLACE")
            rep["mesh"] = o.name
            report["meshes"].append(rep)
        report["ok"] = True
    if report_path:
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
    if report["ok"]:
        bpy.ops.object.select_all(action="DESELECT")
        keep = [o for o in scene.objects if o.type == "ARMATURE" or (o.type == "MESH" and o.vertex_groups)]
        for o in keep:
            o.select_set(True)
        bpy.context.view_layer.objects.active = keep[0]
        bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", use_selection=True, export_apply=False, export_animations=True)
    print("LEG_SKIN_OK" if report["ok"] else "LEG_SKIN_SKIPPED", report.get("reason", ""))


if __name__ == "__main__":  # pragma: no cover
    main()
