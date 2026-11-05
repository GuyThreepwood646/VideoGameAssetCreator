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


def make_group(obj, cut, h):
    g = obj.vertex_groups.get(GROUP) or obj.vertex_groups.new(name=GROUP)
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


def _evaluate(obj, mod, factor, cut):
    """(triangles, head triangles) of the decimation with this factor, without applying it."""
    mod.vertex_group_factor = float(factor)
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    try:
        total = len(_tris(me))
        head = head_count(me, cut)
    finally:
        ev.to_mesh_clear()
    return total, head


def decimate(obj, ratio, share, gain, symmetric=False):
    """Decimate `obj` (object mode, transforms applied) to `ratio` of its triangles with the head protected. Returns a
    report: head_triangles_source (before), head_triangles_even (an even decimation), head_triangles_target,
    head_triangles (after), head_weight_factor, head_weight_seconds."""
    t0 = time.time()
    cut, h = head_cut(obj, share)
    source = head_count(obj.data, cut)
    make_group(obj, cut, h)
    mod = obj.modifiers.new("budget", "DECIMATE")
    mod.ratio = ratio
    mod.use_collapse_triangulate = True
    if symmetric:
        mod.use_symmetry = True
        mod.symmetry_axis = "X"
    mod.vertex_group = GROUP
    mod.invert_vertex_group = True
    _, even = _evaluate(obj, mod, 0.0, cut)
    # Nearly everything the head has, when that is less than gain times its even share.
    target = min(int(round(gain * even)), int(0.97 * source))
    lo, hi = 0.0, FACTOR_MAX
    best = (0.0, even)
    if target > even:
        # Search the factor on a log scale: head triangles grow with it.
        f_lo, f_hi = FACTOR_MIN, FACTOR_MAX
        for _ in range(SEARCH_STEPS):
            mid = (f_lo * f_hi) ** 0.5
            _, head = _evaluate(obj, mod, mid, cut)
            if abs(head - target) < abs(best[1] - target):
                best = (mid, head)
            if head < target:
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
    return {"head_triangles_source": source, "head_triangles_even": even, "head_triangles_target": target,
            "head_triangles": after, "head_weight_factor": float("%.3g" % best[0]), "head_cut_share": share,
            "head_weight_seconds": round(time.time() - t0, 2), "head_search": [float("%.3g" % lo), float("%.3g" % hi)]}
