"""Blender headless: stretch skin pass on a rigged GLB (PL-127), run after the sleeve skin pass in the rig step.

Run: blender -b --python stretch_skin.py -- <rig.glb> <out.glb> --report <json>

The generator's skin weights can change a lot between two vertices a few millimetres apart (thin cloth such as a collar
rim or a hem, mixed bones from one neighbour to the next). When the bones then move against each other in a run or a walk,
that edge is stretched to several times its rest length.
The weight change per metre of rest edge, g = sum of |weight difference| over the rest length, finds those edges.

The pass takes every vertex on an edge with g above GRAD_LIMIT and pulls its weights toward the length-weighted mean of its
neighbours, in a few rounds (only vertices still on a steep edge move in a round). Rules, as in the leg skin pass: a vertex
keeps its strongest bone, no vertex changes by more than MAX_MOVE of weight in total, at most four influences, rows add up to
one. Vertices at the same place with the same weights (the copies a seam splits one vertex into) are treated as one, so the
pass does not pull apart copies that started together. The split of a vertex's leg weight between the left and the right leg
is never changed, so skirt or hem skin is not pulled toward the other leg, and leg skin gains no arm weight.

Vertices that carry weight on a hand or finger bone are never changed (the hand skin pass has set them), nor is the ring of
vertices round them; they still count as neighbours. The rest mesh, the bones and the animation are not touched.

Everything above main() is plain numpy so the backend tests can run it without Blender.
"""
import json
import os
import sys

import numpy as np

try:
    from . import sleeve_skin
except ImportError:  # run by Blender as a script
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import sleeve_skin

GRAD_LIMIT = 8.0  # weight change per metre of rest edge above which an edge is steep
MIN_EDGE_M = 0.003  # shorter edges count as this long when the gradient is taken
ROUNDS = 12
BLEND = 0.7  # share of the way to the neighbour mean a vertex moves in one round
MAX_MOVE = 0.3  # largest total weight (half the sum of absolute changes) one vertex may move from its first weights
HAND_FREE = 0.02  # a vertex with at least this much weight on hand or finger bones is left as it is
MAX_INFLUENCES = 4
SEAM_TOL = 0.02  # vertices at one place count as one when their weights differ by at most this (half the L1 difference)
FREEZE_RINGS = 1  # the frozen hand skin is widened by this many edge steps


def is_hand_bone(name):
    n = str(name or "").replace("mixamorig:", "").lower()
    if "spring" in n or "hair" in n:
        return False
    return "hand" in n or any(k in n for k in ("thumb", "index", "middle", "ring", "pinky", "finger"))


def hand_columns(names):
    return [i for i, n in enumerate(names) if is_hand_bone(n)]


def frozen_rows(W, names):
    """Vertices that carry at least HAND_FREE of weight on a hand or finger bone."""
    cols = hand_columns(names)
    if not cols:
        return np.zeros(len(W), dtype=bool)
    return np.asarray(W)[:, cols].sum(axis=1) >= HAND_FREE


def leg_columns(names):
    """([columns of the left leg's chain], [columns of the right one]): thigh, shin, foot and toe bones."""
    out = ([], [])
    for i, name in enumerate(names):
        n = str(name or "").replace("mixamorig:", "").lower()
        if "spring" in n or "hair" in n or not any(k in n for k in ("upleg", "leg", "thigh", "calf", "foot", "toe", "ankle")):
            continue
        if n.startswith("left") or n.endswith(("_l", ".l")):
            out[0].append(i)
        elif n.startswith("right") or n.endswith(("_r", ".r")):
            out[1].append(i)
    return out


def lock_leg_split(new, orig, legs):
    """Keep each row's split of leg weight between the two legs as it was in `orig`, so no weight moves from one leg's chain
    toward the other. The row's total leg weight may change; within one chain the new proportions stay. A row that had no
    leg weight gets none. What a leg loses or gains this way comes from, or goes to, the row's other bones in proportion."""
    left, right = legs
    if not left or not right:
        return new
    out = np.array(new, dtype=float, copy=True)
    o_l, o_r = orig[:, left].sum(axis=1), orig[:, right].sum(axis=1)
    total_o = o_l + o_r
    total_n = out[:, left].sum(axis=1) + out[:, right].sum(axis=1)
    rows = np.where(total_n + total_o > 1e-12)[0]
    for i in rows:
        if total_o[i] <= 1e-12:
            keep_total = 0.0
        else:
            keep_total = total_n[i]
        for cols, share in ((left, o_l[i]), (right, o_r[i])):
            want = keep_total * share / total_o[i] if total_o[i] > 1e-12 else 0.0
            have = out[i, cols].sum()
            if have > 1e-12:
                out[i, cols] *= want / have
            elif want > 1e-12:
                out[i, cols] = orig[i, cols] * want / share
        rest = np.ones(out.shape[1], dtype=bool)
        rest[left + right] = False
        spare = 1.0 - out[i, left + right].sum()
        others = out[i, rest].sum()
        if others > 1e-12:
            out[i, rest] *= max(spare, 0.0) / others
        else:
            out[i] /= max(out[i].sum(), 1e-12)
    return out


def arm_columns(names):
    """Columns of both arm chains (shoulder to fingers), as the sleeve skin pass reads them."""
    cols = sleeve_skin.kind_columns(names)
    return cols["fore"] + cols["arm"]


def cap_arm_gain(new, orig, legs, arms):
    """Leg skin (sleeve_skin.LEG_MASS_MIN of weight on the legs in `orig`) gains no arm weight, so smoothing across a welded
    sleeve cannot hang a skirt on the forearm. The excess goes to the row's other bones in proportion."""
    leg_cols = list(legs[0]) + list(legs[1])
    if not leg_cols or not arms:
        return new
    out = np.array(new, dtype=float, copy=True)
    have, had = out[:, arms].sum(axis=1), orig[:, arms].sum(axis=1)
    rows = np.where((orig[:, leg_cols].sum(axis=1) >= sleeve_skin.LEG_MASS_MIN) & (have > had + 1e-12))[0]
    rest = np.ones(out.shape[1], dtype=bool)
    rest[arms] = False
    for i in rows:
        out[i, arms] *= had[i] / have[i]
        others = out[i, rest].sum()
        if others > 1e-12:
            out[i, rest] *= (1.0 - had[i]) / others
        else:
            out[i] = orig[i]
    return out


def keep_sides(new, orig, legs, arms):
    """Both rules that stop skin being pulled across: the leg split is kept, and leg skin gains no arm weight."""
    return cap_arm_gain(lock_leg_split(new, orig, legs), orig, legs, arms)


def grow(mask, edges, rings=FREEZE_RINGS):
    """`mask` widened by `rings` steps along the mesh edges."""
    edges = np.asarray(edges, dtype=int).reshape(-1, 2)
    out = np.array(mask, dtype=bool, copy=True)
    for _ in range(rings):
        nxt = out.copy()
        nxt[edges[out[edges[:, 0]], 1]] = True
        nxt[edges[out[edges[:, 1]], 0]] = True
        out = nxt
    return out


def edge_gradient(W, edges, rest):
    """Per edge, the weight change per metre of rest length (lengths under MIN_EDGE_M count as MIN_EDGE_M)."""
    edges = np.asarray(edges, dtype=int).reshape(-1, 2)
    if len(edges) == 0:
        return np.zeros(0)
    L = np.maximum(np.linalg.norm(rest[edges[:, 0]] - rest[edges[:, 1]], axis=1), MIN_EDGE_M)
    return np.abs(W[edges[:, 0]] - W[edges[:, 1]]).sum(axis=1) / L


def neighbour_mean(W, edges, rest):
    """Per vertex, the mean of its neighbours' weights, each weighted by 1 over the edge length. Vertices with no edge
    get their own row back."""
    edges = np.asarray(edges, dtype=int).reshape(-1, 2)
    L = np.maximum(np.linalg.norm(rest[edges[:, 0]] - rest[edges[:, 1]], axis=1), MIN_EDGE_M)
    k = 1.0 / L
    acc = np.zeros_like(W)
    tot = np.zeros(len(W))
    np.add.at(acc, edges[:, 0], W[edges[:, 1]] * k[:, None])
    np.add.at(acc, edges[:, 1], W[edges[:, 0]] * k[:, None])
    np.add.at(tot, edges[:, 0], k)
    np.add.at(tot, edges[:, 1], k)
    has = tot > 0
    out = W.copy()
    out[has] = acc[has] / tot[has][:, None]
    return out


def _clamp_to_origin(new, orig, legs=((), ()), arms=()):
    """Scale each row's move from `orig` so it is at most MAX_MOVE, and so the strongest bone stays the strongest."""
    d = new - orig
    move = 0.5 * np.abs(d).sum(axis=1)
    scale = np.where(move > MAX_MOVE, MAX_MOVE / np.maximum(move, 1e-12), 1.0)
    out = keep_sides(orig + d * scale[:, None], orig, legs, arms)
    top = np.argmax(orig, axis=1)
    idx = np.arange(len(orig))
    bad = np.argmax(out, axis=1) != top
    for _ in range(8):
        if not bad.any():
            break
        d[bad] *= 0.5
        out[bad] = keep_sides(orig[bad] + d[bad] * scale[bad][:, None], orig[bad], legs, arms)
        bad = np.argmax(out, axis=1) != top
    out[bad] = orig[bad]
    return out


def limit_rows(W, keep=MAX_INFLUENCES):
    """Each row cut to its `keep` largest weights and normalised to 1 (an all-zero row stays as it is)."""
    W = np.array(W, dtype=float, copy=True)
    if W.shape[1] > keep:
        cut = np.argsort(-W, axis=1)[:, keep:]
        np.put_along_axis(W, cut, 0.0, axis=1)
    s = W.sum(axis=1)
    ok = s > 1e-12
    W[ok] /= s[ok][:, None]
    return W


def coincident_groups(rest, W, tol=SEAM_TOL):
    """Group number per vertex. Vertices at the same place (the copies a UV or normal seam splits a vertex into) share a
    group when their weights differ by at most `tol` (half the sum of absolute differences) from the group's first member.
    Copies that already carry different weights stay apart."""
    rest = np.asarray(rest, dtype=float)
    key = np.round(rest / 1e-5).astype(np.int64)
    _, place = np.unique(key, axis=0, return_inverse=True)
    place = place.ravel()
    out = np.zeros(len(rest), dtype=int)
    heads = {}  # place -> [(first vertex, group)]
    count = 0
    for v, at in enumerate(place):
        for first, grp in heads.setdefault(int(at), []):
            if 0.5 * np.abs(W[v] - W[first]).sum() <= tol:
                out[v] = grp
                break
        else:
            heads[int(at)].append((v, count))
            out[v] = count
            count += 1
    return out


def smooth_steep(W, edges, rest, frozen=None, limit=GRAD_LIMIT, rounds=ROUNDS, blend=BLEND, legs=((), ()), arms=()):
    """Like _smooth_rows, but seam copies with the same weights move together: none is given weights its twin does not get,
    so no tear opens along the seam. Each group works with the mean of its members' weights, is frozen if any member is,
    and a group that moves gives every member the same new row. Returns (new W, rows changed, steep edges before, steep
    edges after)."""
    W = np.asarray(W, dtype=float)
    rest = np.asarray(rest, dtype=float)
    grp = coincident_groups(rest, W)
    n = int(grp.max()) + 1 if len(grp) else 0
    if n == len(W):
        return _smooth_rows(W, edges, rest, frozen, limit, rounds, blend, legs, arms)
    cnt = np.bincount(grp, minlength=n).astype(float)
    Wg = np.zeros((n, W.shape[1]))
    np.add.at(Wg, grp, W)
    Wg /= cnt[:, None]
    Pg = np.zeros((n, 3))
    np.add.at(Pg, grp, rest)
    Pg /= cnt[:, None]
    fg = np.zeros(n, dtype=bool)
    if frozen is not None:
        np.logical_or.at(fg, grp, np.asarray(frozen, dtype=bool))
    eg = grp[np.asarray(edges, dtype=int).reshape(-1, 2)]
    eg = np.unique(np.sort(eg[eg[:, 0] != eg[:, 1]], axis=1), axis=0)
    Wg2, _, before, after = _smooth_rows(Wg, eg, Pg, fg, limit, rounds, blend, legs, arms)
    moved = np.abs(Wg2 - Wg).sum(axis=1) > 1e-9
    out = W.copy()
    rows = moved[grp]
    out[rows] = Wg2[grp[rows]]
    return out, int(rows.sum()), before, after


def _smooth_rows(W, edges, rest, frozen=None, limit=GRAD_LIMIT, rounds=ROUNDS, blend=BLEND, legs=((), ()), arms=()):
    """Pull the weights of vertices on steep edges toward their neighbours. Returns (new W, rows changed, steep edges before,
    steep edges after). Row sums are one when the input rows sum to one."""
    W0 = np.array(W, dtype=float, copy=True)
    edges = np.asarray(edges, dtype=int).reshape(-1, 2)
    frozen = np.zeros(len(W0), dtype=bool) if frozen is None else np.asarray(frozen, dtype=bool)
    before = int((edge_gradient(W0, edges, rest) > limit).sum()) if len(edges) else 0
    if len(edges) == 0 or before == 0:
        return W0, 0, before, before
    cur = W0.copy()
    touched = np.zeros(len(cur), dtype=bool)
    for _ in range(rounds):
        steep = edge_gradient(cur, edges, rest) > limit
        if not steep.any():
            break
        hot = np.zeros(len(cur), dtype=bool)
        hot[edges[steep].ravel()] = True
        hot &= ~frozen
        if not hot.any():
            break
        touched |= hot
        target = neighbour_mean(cur, edges, rest)
        new = cur.copy()
        new[hot] = cur[hot] + blend * (target[hot] - cur[hot])
        new[hot] = _clamp_to_origin(new[hot], W0[hot], legs, arms)
        cur = new
    cur[touched] = keep_sides(limit_rows(cur[touched]), W0[touched], legs, arms)
    changed = np.abs(cur - W0).sum(axis=1) > 1e-9
    cur[~changed] = W0[~changed]
    after = int((edge_gradient(cur, edges, rest) > limit).sum())
    return cur, int(changed.sum()), before, after


# ---- Blender side -------------------------------------------------------------------------------------------------


def _process_mesh(o, report):  # pragma: no cover - runs inside Blender
    import bmesh

    names = [g.name for g in o.vertex_groups]
    me = o.data
    bm = bmesh.new()
    bm.from_mesh(me)
    dl = bm.verts.layers.deform.active
    if dl is None:
        bm.free()
        return False
    bm.verts.ensure_lookup_table()
    V, G = len(bm.verts), len(names)
    W = np.zeros((V, G))
    for v in bm.verts:
        for g, w in v[dl].items():
            if g < G:
                W[v.index, g] = w
    rest = np.array([tuple(v.co) for v in bm.verts], dtype=float).reshape(-1, 3)
    edges = np.array([[e.verts[0].index, e.verts[1].index] for e in bm.edges], dtype=int).reshape(-1, 2)
    W2, n, before, after = smooth_steep(W, edges, rest, grow(frozen_rows(W, names), edges), legs=leg_columns(names),
                                     arms=arm_columns(names))
    report["vertices"] += n
    report["steep_edges_before"] += before
    report["steep_edges_after"] += after
    if n:
        for i in np.where(np.abs(W2 - W).sum(axis=1) > 1e-9)[0]:
            for g in list(bm.verts[i][dl].keys()):
                del bm.verts[i][dl][g]
            for g in np.nonzero(W2[i] > 1e-9)[0]:
                bm.verts[i][dl][int(g)] = float(W2[i, g])
        bm.to_mesh(me)
        me.update()
    bm.free()
    return n > 0


def main():  # pragma: no cover - runs inside Blender
    import bpy

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _common  # noqa: E402

    argv = _common.after_double_dash()
    src, dst = os.path.abspath(argv[0]), os.path.abspath(argv[1])
    report_path = _common.opt("--report", None)
    _common.open_glb(src)
    scene = bpy.context.scene
    arm = next((o for o in scene.objects if o.type == "ARMATURE"), None)
    meshes = [o for o in scene.objects if o.type == "MESH" and o.vertex_groups]
    report = {"ok": False, "vertices": 0, "steep_edges_before": 0, "steep_edges_after": 0, "changed": 0}
    if arm is None or not meshes:
        report["reason"] = "no armature or no skinned mesh"
    else:
        any_changed = False
        for o in meshes:
            any_changed = _process_mesh(o, report) or any_changed
        report["ok"] = True
        report["changed"] = int(any_changed)
    if report_path:
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
    if report["ok"] and report["changed"]:
        bpy.ops.object.select_all(action="DESELECT")
        keep_objs = [o for o in scene.objects if o.type == "ARMATURE" or (o.type == "MESH" and o.vertex_groups)]
        for o in keep_objs:
            o.select_set(True)
        bpy.context.view_layer.objects.active = keep_objs[0]
        bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", use_selection=True, export_apply=False, export_animations=True)
    print("STRETCH_SKIN_OK" if report["ok"] else "STRETCH_SKIN_SKIPPED", report.get("reason", ""), report["changed"],
          report["vertices"])


if __name__ == "__main__":  # pragma: no cover
    main()
