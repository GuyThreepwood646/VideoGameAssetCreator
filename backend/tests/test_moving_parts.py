"""Moving parts (WP-29): the piece finder's numpy core in blender/moving_parts.py, on synthetic meshes. The measured
trial on real library assets (chest, car, wheelbarrow) is in bakeoff/wp29/trial."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vgac" / "blender"))
import moving_parts as mp  # noqa: E402


def torus(R=0.4, r=0.12, n=48, m=16, centre=(0.0, 0.0, 0.0), axis="x"):
    """A tyre: a torus round an axis, points and triangles."""
    u, v = np.meshgrid(np.linspace(0, 2 * np.pi, n, endpoint=False), np.linspace(0, 2 * np.pi, m, endpoint=False), indexing="ij")
    rad = R + r * np.cos(v)
    along = r * np.sin(v)
    if axis == "x":
        p = np.stack([along, rad * np.cos(u), rad * np.sin(u)], -1).reshape(-1, 3)
    else:
        p = np.stack([rad * np.cos(u), rad * np.sin(u), along], -1).reshape(-1, 3)
    f = []
    for i in range(n):
        for j in range(m):
            a, b, c, d = i * m + j, ((i + 1) % n) * m + j, ((i + 1) % n) * m + (j + 1) % m, i * m + (j + 1) % m
            f += [(a, b, c), (a, c, d)]
    return p + np.asarray(centre), np.array(f)


def voxels(cells, step=0.1):
    """A closed surface round a set of unit cells (i, j, k): two triangles for every cell face not shared."""
    cells = set(map(tuple, cells))
    co, faces, index = [], [], {}

    def vid(p):
        if p not in index:
            index[p] = len(co)
            co.append([x * step for x in p])
        return index[p]

    for (i, j, k) in cells:
        for axis in range(3):
            for side in (0, 1):
                nb = [i, j, k]
                nb[axis] += 1 if side else -1
                if tuple(nb) in cells:
                    continue
                o = [i, j, k]
                o[axis] += side
                u, v = [(axis + 1) % 3, (axis + 2) % 3]
                q = []
                for du, dv in ((0, 0), (1, 0), (1, 1), (0, 1)):
                    c = list(o)
                    c[u] += du
                    c[v] += dv
                    q.append(vid(tuple(c)))
                if side == 0:
                    q = q[::-1]
                faces += [(q[0], q[1], q[2]), (q[0], q[2], q[3])]
    return np.array(co, float), np.array(faces)


def cylinder_wheel(radius=0.4, width=0.25, centre=(0.0, 0.0, 0.4)):
    """A boxy tyre (a closed cylinder) round the x axis."""
    w = trimesh.creation.cylinder(radius=radius, height=width, sections=48)
    v, f = trimesh.remesh.subdivide_to_size(w.vertices, w.faces, max_edge=0.03)  # dense, like a built model
    w = trimesh.Trimesh(v, f)
    w.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, (0, 1, 0)))
    w.apply_translation(centre)
    return np.asarray(w.vertices), np.asarray(w.faces)


def test_weld_joins_seam_copies():
    co = np.array([[0, 0, 0], [1, 0, 0], [0, 0, 1e-7], [1, 1, 1]], float)
    inv, wco = mp.weld(co)
    assert inv[0] == inv[2] and len(wco) == 3


def test_creases_are_found_only_where_the_surface_folds_inward():
    co, faces = voxels([(i, j, k) for i in range(3) for j in range(3) for k in range(3)])
    assert mp.surface_graph(co, faces)[2].max() == 0  # a box's own edges are convex ridges
    co, faces = voxels([(i, j, 0) for i in range(3) for j in range(3)] + [(1, 1, 1)])  # a step: an inner corner
    sharp = mp.surface_graph(co, faces)[2]
    ring = co[sharp > 0]  # where the small block meets the slab: z 0.1, on the block's foot
    assert sharp.max() == 1.0 and np.allclose(ring[:, 2], 0.1) and ring[:, :2].min() >= 0.1 - 1e-9 and ring[:, :2].max() <= 0.2 + 1e-9


def test_the_axis_of_a_tyre_comes_from_its_normals_even_with_clutter():
    p, f = torus(centre=(0.3, 1.5, 0.52))
    n = mp.point_normals(p, f)
    rng = np.random.default_rng(1)
    clutter = rng.uniform(-1, 1, (40, 3)) + [0.3, 1.5, 0.52]
    cn = rng.normal(size=(40, 3))
    cn /= np.linalg.norm(cn, axis=1)[:, None]
    a, c, w = mp.revolution_axis(np.r_[p, clutter], np.r_[n, cn])
    assert abs(abs(a[0]) - 1) < 1e-3
    assert np.allclose(c[1:], [1.5, 0.52], atol=1e-3)
    # a patch of tread alone: the direction from the normals' least spread, placed by axis_through
    top = p[:, 2] > 0.52 + 0.4
    guesses = mp.axis_guesses(p[top], n[top])
    best = max(guesses, key=lambda g: abs(g[0][0]))
    assert abs(abs(best[0][0]) - 1) < 1e-3 and np.allclose(best[1][1:], [1.5, 0.52], atol=0.02)


def test_a_wheel_is_found_by_its_shape_and_two_wheels_make_two_pieces():
    w1, f1 = cylinder_wheel(centre=(0.0, 1.0, 0.4))
    w2, f2 = cylinder_wheel(centre=(0.0, -1.0, 0.4))
    body = trimesh.creation.box((0.5, 3.0, 0.4), trimesh.transformations.translation_matrix((-0.5, 0, 0.9)))
    co = np.r_[w1, w2, body.vertices]
    faces = np.r_[f1, f2 + len(w1), body.faces + len(w1) + len(w2)]
    seeds = [w1[np.argmax(w1[:, 0])], [0.0, -1.0, 0.8]]  # one wheel's side, the other's tread top
    found, pieces = mp.find_pieces(co, faces, seeds, kind="spin")
    assert len(pieces) == 2
    assert found[: len(w1) + len(w2)].all() and not found[len(w1) + len(w2):].any()


def test_a_lid_inset_on_a_box_is_cut_at_the_crease_and_takes_all_its_seeds():
    box = [(i, j, k) for i in range(6) for j in range(6) for k in range(4)]
    lid = [(i, j, 4) for i in range(1, 5) for j in range(1, 5)]
    co, faces = voxels(box + lid)
    found, pieces = mp.find_pieces(co, faces, [[0.3, 0.3, 0.5], [0.45, 0.3, 0.45]], kind="hinge")
    assert len(pieces) == 1
    lid_pts = co[:, 2] > 0.4 + 1e-9
    assert found[lid_pts].all()
    assert not found[co[:, 2] < 0.4 - 1e-9].any()
