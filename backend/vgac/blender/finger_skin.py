"""Is a finger chain on the hand's skin? (rig check `fingers`, measured by rig_info.py; rig-hands-wings, 2026-11-22.)

A finger joint is on the hand when it lies inside the closed skin, or within ON_SURFACE_M of the nearest point on the skin
surface. Inside is only defined for a closed skin: where the skin near the wrist is open or non-manifold (an edge with
other than two faces), the surface test alone decides. The earlier measure (distance to the nearest skin vertex, over
3 cm fails) failed joints deep in thick or low-poly fingers and missed fingers lying just outside the skin.

The decision is plain Python so tests can run it without Blender; rig_info.py supplies the inside and surface tests.
"""

ON_SURFACE_M = 0.015  # a joint outside the skin by more than this is off the hand (stages/rig.py FINGER_OFF_SKIN_CM)


def joint_outside_m(inside, surface_m, closed):
    """How far one joint lies outside the skin, in metres: 0 when it is inside a closed skin, else its distance to the
    surface. inside: the ray-parity verdict (ignored when the skin is not closed)."""
    if closed and inside:
        return 0.0
    return float(surface_m)


def chain_outside_cm(joints, inside_fn, surface_fn, closed):
    """Worst outside distance (cm, one decimal) over a finger chain's joints (knuckle, two inner joints, tip). A chain
    whose value is above ON_SURFACE_M in cm is off the hand."""
    worst = 0.0
    for j in joints:
        worst = max(worst, joint_outside_m(inside_fn(j) if closed else False, surface_fn(j), closed))
    return round(worst * 100, 1)


def edges_closed(polygons_edge_keys, near_vertex):
    """True when every edge of the polygons touching the region has exactly two faces. polygons_edge_keys: one list of
    (v0, v1) keys per polygon of the whole mesh; near_vertex(i) says whether vertex i is in the region."""
    users = {}
    touch = []
    for keys in polygons_edge_keys:
        for k in keys:
            users[k] = users.get(k, 0) + 1
        if any(near_vertex(v) for k in keys for v in k):
            touch.append(keys)
    return all(users[k] == 2 for keys in touch for k in keys)
