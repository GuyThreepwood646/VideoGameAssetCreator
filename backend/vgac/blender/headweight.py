"""Weighted decimation: decimate to a triangle count while the head keeps more of its triangles (vgac/headtris.py).

Loaded by prep.py inside Blender (not a script of its own and not imported by the backend):

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import headweight

The head is the top `share` of the model's height (Blender z up; the same rule as face.find_head). Its vertices get
weight 1 in a vertex group, fading to 0 over FADE of the height below the cut. The Decimate modifier (collapse) is
given the group inverted, so the body weighs 1 and is decimated as usual while head edges get an extra collapse cost
that grows with (2 - w1 - w2) * factor (w the inverted weight) and are collapsed last. The extra cost dwarfs the
shape error on a model a metre or two tall: on the knight a factor of 3 kept every head triangle, so the factor is
searched on a log scale down to FACTOR_MIN. The total triangle count is the decimation's target whatever the factor.

decimate() first measures an even decimation (factor 0, evaluated, not applied) to learn how many head triangles it
leaves, then searches the factor so the head keeps `gain` times that (or nearly all it has, when the model has fewer),
and applies that decimation.
"""
import time

import bpy
import numpy as np

GROUP = "vgac_head"
FADE = 0.04  # of the height: the weight falls from 1 at the cut to 0 this far below it
FACTOR_MAX = 1000.0  # the modifier's vertex_group_factor limit
FACTOR_MIN = 1e-9
SEARCH_STEPS = 14


def _tris(mesh):
    mesh.calc_loop_triangles()
    return mesh.loop_triangles


def head_cut(obj, share):
    zs = [v.co.z for v in obj.data.vertices]
    top, bottom = max(zs), min(zs)
    h = top - bottom
    return top - share * h, h


def head_count(mesh, cut):
    """Triangles whose centre is above the cut (the head)."""
    n = 0
    verts = mesh.vertices
    for t in _tris(mesh):
        a, b, c = (verts[i].co.z for i in t.vertices)
        if (a + b + c) / 3.0 >= cut:
            n += 1
    return n


# Hands (vgac/handscore.py's rule on Blender's axes: x sideways, z up). The arm's far end is the vertex farthest from the
# shoulder joint among those outside the legs; the hand is the HAND_SHARE of the height back from there along the arm.
ARM_X, SHOULDER_X, SHOULDER_SHARE, HAND_SHARE, HAND_RADIUS = 0.16, 0.13, 0.80, 0.105, 0.55


def _positions(mesh):
    n = len(mesh.vertices)
    co = np.empty(n * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", co)
    return co.reshape(n, 3)


def hand_zones(obj):
    """[(wrist, axis, length)] for each hand found in the model (a person in the A or T pose); empty when an arm is not
    found. Only the top level geometry of the rule lives here; handscore.find_hand is the tested twin."""
    v = _positions(obj.data)
    lo, hi = float(v[:, 2].min()), float(v[:, 2].max())
    height = hi - lo
    out = []
    for side in (1, -1):
        arm = v[(side * v[:, 0] > ARM_X * height) & (v[:, 2] > lo + 0.3 * height)]
        if len(arm) == 0:
            continue
        shoulder = np.array([side * SHOULDER_X * height, float(np.median(v[:, 1])), lo + SHOULDER_SHARE * height])
        tip = arm[int(np.argmax(np.linalg.norm(arm - shoulder, axis=1)))]
        axis = tip - shoulder
        axis = axis / max(float(np.linalg.norm(axis)), 1e-12)
        length = HAND_SHARE * height
        out.append((tip - axis * length, axis, length))
    return out


def _tri_centres(mesh):
    """Centre of every triangle (numpy (m, 3))."""
    mesh.calc_loop_triangles()
    m = len(mesh.loop_triangles)
    idx = np.empty(m * 3, dtype=np.int64)
    mesh.loop_triangles.foreach_get("vertices", idx)
    return _positions(mesh)[idx.reshape(m, 3)].mean(axis=1)


def in_hands(centres, zones):
    """Boolean mask of the points past a wrist, within HAND_RADIUS hand lengths of the arm axis."""
    hit = np.zeros(len(centres), dtype=bool)
    for wrist, axis, length in zones:
        d = centres - wrist
        t = d @ axis
        radial = np.linalg.norm(d - t[:, None] * axis, axis=1)
        hit |= (t > 0) & (t < 1.2 * length) & (radial < HAND_RADIUS * length)
    return hit


def hand_count(mesh, zones):
    return int(in_hands(_tri_centres(mesh), zones).sum()) if zones else 0


def make_group(obj, cut, h, zones=()):
    g = obj.vertex_groups.get(GROUP) or obj.vertex_groups.new(name=GROUP)
    if zones:
        hv = np.where(in_hands(_positions(obj.data), zones))[0]
        if len(hv):
            g.add([int(i) for i in hv], 1.0, "REPLACE")
    fade = max(1e-6, FADE * h)
    full, part = [], {}
    for v in obj.data.vertices:
        z = v.co.z
        if z >= cut:
            full.append(v.index)
        elif z > cut - fade:
            w = round(1.0 - (cut - z) / fade, 3)
            part.setdefault(w, []).append(v.index)
    if full:
        g.add(full, 1.0, "REPLACE")
    for w, idx in part.items():
        g.add(idx, w, "REPLACE")
    return g


def _evaluate(obj, mod, factor, cut, zones=()):
    """(triangles, head triangles, hand triangles) of the decimation with this factor, without applying it."""
    mod.vertex_group_factor = float(factor)
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    try:
        total = len(_tris(me))
        head = head_count(me, cut)
        hands = hand_count(me, zones)
    finally:
        ev.to_mesh_clear()
    return total, head, hands


def decimate(obj, ratio, share, gain, symmetric=False, hand_gain=0.0):
    """Decimate `obj` (object mode, transforms applied) to `ratio` of its triangles with the head protected, and the
    hands too when hand_gain > 0 (they join the head's vertex group, and the search aims at gain times the head's even
    share plus hand_gain times the hands'). Returns a report: head_triangles_source (before), head_triangles_even (an
    even decimation), head_triangles_target, head_triangles (after), head_weight_factor, head_weight_seconds; with hands
    also hand_triangles_source, hand_triangles_even, hand_triangles (after)."""
    t0 = time.time()
    cut, h = head_cut(obj, share)
    source = head_count(obj.data, cut)
    zones = hand_zones(obj) if hand_gain > 0 else []
    hand_source = hand_count(obj.data, zones)
    make_group(obj, cut, h, zones)
    mod = obj.modifiers.new("budget", "DECIMATE")
    mod.ratio = ratio
    mod.use_collapse_triangulate = True
    if symmetric:
        mod.use_symmetry = True
        mod.symmetry_axis = "X"
    mod.vertex_group = GROUP
    mod.invert_vertex_group = True
    _, even, hand_even = _evaluate(obj, mod, 0.0, cut, zones)
    # Nearly everything the head and hands have, when that is less than gain times their even share.
    target_head = min(int(round(gain * even)), int(0.97 * source))
    target_hands = min(int(round(hand_gain * hand_even)), int(0.97 * hand_source)) if zones else 0
    target = target_head + target_hands
    lo, hi = 0.0, FACTOR_MAX
    best = (0.0, even + hand_even)
    if target > even + hand_even:
        # Search the factor on a log scale: protected triangles grow with it.
        f_lo, f_hi = FACTOR_MIN, FACTOR_MAX
        for _ in range(SEARCH_STEPS):
            mid = (f_lo * f_hi) ** 0.5
            _, head, hands = _evaluate(obj, mod, mid, cut, zones)
            if abs(head + hands - target) < abs(best[1] - target):
                best = (mid, head + hands)
            if head + hands < target:
                f_lo = mid
            else:
                f_hi = mid
        lo, hi = f_lo, f_hi
    mod.vertex_group_factor = best[0]
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=mod.name)
    after = head_count(obj.data, cut)
    g = obj.vertex_groups.get(GROUP)
    if g is not None:
        obj.vertex_groups.remove(g)
    report = {"head_triangles_source": source, "head_triangles_even": even, "head_triangles_target": target_head,
              "head_triangles": after, "head_weight_factor": float("%.3g" % best[0]), "head_cut_share": share,
              "head_weight_seconds": round(time.time() - t0, 2), "head_search": [float("%.3g" % lo), float("%.3g" % hi)]}
    if zones:
        report.update(hand_triangles_source=hand_source, hand_triangles_even=hand_even, hand_triangles_target=target_hands,
                      hand_triangles=hand_count(obj.data, zones))
    return report
