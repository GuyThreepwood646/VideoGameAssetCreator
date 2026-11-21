"""WP-69: the numpy core of the stiff-part pass (blender/worn_region.py) and the stiff kind (declared_parts.py).
Runs without Blender."""
import json
from pathlib import Path

import numpy as np

from vgac import declared_parts as dp
from vgac.blender import worn_region as wr


def _chain(n):
    return np.array([[i, i + 1] for i in range(n - 1)], dtype=int)


def test_gltf_to_blender_swaps_up_axis():
    assert wr.gltf_to_blender([1.0, 2.0, 3.0]).tolist() == [1.0, -3.0, 2.0]


def _quad_pair(z_fold):
    # two triangles sharing an edge, first flat in XY, second folded by z_fold (a crease) about the shared edge
    co = np.array([[0.0, 0, 0], [1.0, 0, 0], [0.0, 1, 0], [1.0, 1, z_fold]])
    faces = np.array([[0, 1, 2], [1, 3, 2]])
    return co, faces


def _convex_strip():
    # a convex half-cylinder (axis along y), 4 segments by 2 rows: every interior edge is a convex ridge, with outward
    # normals. Growth from one corner must cross the shared interior edges to reach the far corner.
    co, faces = [], []
    for y in (0.0, 1.0):
        for k in range(5):
            t = k * np.pi / 8
            co.append([np.cos(t), y, np.sin(t)])
    co = np.asarray(co, dtype=float)
    for k in range(4):
        a0, b0, c0, d0 = k, k + 1, k + 6, k + 5
        for tri in ((a0, b0, c0), (a0, c0, d0)):
            p0, p1, p2 = co[list(tri)]
            n = np.cross(p1 - p0, p2 - p0)
            radial = np.array([(p0[0] + p1[0] + p2[0]) / 3, 0.0, (p0[2] + p1[2] + p2[2]) / 3])
            faces.append(tri if np.dot(n, radial) > 0 else (tri[0], tri[2], tri[1]))
    return co, np.asarray(faces, dtype=int)


def test_grow_crosses_the_shared_edges_of_a_convex_ridge():
    co, faces = _convex_strip()
    m = wr.grow_region(co, faces, co[0], radius=10.0)
    assert m.all()


def _cube():
    corners = [[x, y, z] for x in (-1.0, 1.0) for y in (-1.0, 1.0) for z in (-1.0, 1.0)]
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    faces = []
    for q in quads:
        for tri in ((q[0], q[1], q[2]), (q[0], q[2], q[3])):
            a, b, c = (np.asarray(corners[i], dtype=float) for i in tri)
            n = np.cross(b - a, c - a)
            if np.dot(n, (a + b + c) / 3.0) < 0:  # outward
                tri = (tri[0], tri[2], tri[1])
            faces.append(tri)
    return np.asarray(corners, dtype=float), np.asarray(faces, dtype=int)


def test_one_click_covers_a_box():
    co, faces = _cube()
    m = wr.grow_region(co, faces, co[0], radius=10.0)
    assert m.all()


_FIXTURE = Path(__file__).parent / "fixtures" / "stiff_region_floor.json"


def test_box_on_a_floor_stops_at_the_valley_and_takes_the_box():
    fx = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    co = np.asarray(fx["verts"], dtype=float)
    faces = np.asarray(fx["faces"], dtype=int)
    m = wr.grow_region(co, faces, co[fx["click"]], radius=fx["radius"], limit=fx["limit"])
    assert m[fx["box_top"]].all() and m[fx["box_bottom"]].all()
    floor = [i for i in range(len(co)) if i not in set(fx["box_top"]) | set(fx["box_bottom"])]
    assert not m[floor].any()


def test_shared_fixture_region_matches_the_typescript_region():
    # frontend/tests/stiff-region.test.ts reads the same file and must give the same region: grow, then hole fill
    fx = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    co = np.asarray(fx["verts"], dtype=float)
    faces = np.asarray(fx["faces"], dtype=int)
    m = wr.grow_region(co, faces, co[fx["click"]], radius=fx["radius"], limit=fx["limit"])
    edges = np.array([[f[k], f[(k + 1) % 3]] for f in faces for k in range(3)], dtype=int)
    m, _ = wr.fill_holes(m, edges)
    assert sorted(int(i) for i in np.where(m)[0]) == fx["expected_region"]


def test_grow_respects_radius_and_empty_when_far():
    co, faces = _quad_pair(-0.5)
    assert not wr.grow_region(co, faces, [9.0, 9.0, 9.0], radius=0.5).any()
    m = wr.grow_region(co, faces, [0.0, 0.0, 0.0], radius=0.6)
    assert m[0] and not m[3]


def test_spine_bone_is_nearest_chain_bone():
    names = ["mixamorig:Hips", "mixamorig:Spine", "mixamorig:Spine2", "mixamorig:LeftArm"]
    heads = [[0, 0, 0.9], [0, 0, 1.0], [0, 0, 1.2], [0.3, 0, 1.2]]
    assert wr.spine_bone(names, heads, [0, 0, 1.15]) == "mixamorig:Spine2"
    assert wr.spine_bone(["mixamorig:Hips"], [[0, 0, 0]], [0, 0, 0]) is None


def test_run_moves_region_to_one_spine_bone_and_normalises():
    names = ["mixamorig:Spine2", "mixamorig:Hips"]
    heads = [[0, 0, 1.2], [0, 0, 0.9]]
    co = np.array([[0, 0.2, 1.0], [0, 0.0, 1.0], [0, -0.2, 1.0]])
    W = np.array([[0.2, 0.8], [0.5, 0.5], [0.9, 0.1]])
    region = np.array([True, False, False])
    out, rep = wr.run_numpy(W, _chain(3), co, names, heads, region, seam=0)
    assert rep["ok"] is True and rep["bone"] == "mixamorig:Spine2"
    assert out[0].tolist() == [1.0, 0.0]
    assert np.allclose(out[1], W[1])
    assert np.allclose(out.sum(axis=1), 1.0)


def test_run_reports_empty_region_and_leaves_weights():
    W = np.ones((2, 1))
    out, rep = wr.run_numpy(W, _chain(2), np.zeros((2, 3)), ["mixamorig:Spine"], [[0, 0, 1]], np.zeros(2, bool))
    assert rep["ok"] is False and np.array_equal(out, W)


def test_influences_capped_at_four():
    names = [f"b{i}" for i in range(6)] + ["mixamorig:Spine2"]
    heads = [[0, 0, 0.5]] * 6 + [[0, 0, 1.2]]
    rng = np.random.default_rng(1)
    W = rng.random((4, 7))
    W /= W.sum(axis=1, keepdims=True)
    co = np.array([[0, 0.2, 1.0], [0, 0.2, 1.0], [0, 0.0, 1.0], [0, 0.0, 1.0]])
    region = np.array([True, True, False, False])
    out, rep = wr.run_numpy(W, _chain(4), co, names, heads, region, seam=2)
    assert ((out > 1e-6).sum(axis=1) <= 4).all()
    assert np.allclose(out.sum(axis=1), 1.0)


def test_stiff_kind_is_allowed_for_humanoids_and_selects_for_rig():
    assert "stiff" in dp.KINDS
    assert dp.clean_kind("stiff", "humanoid") == "stiff"
    rows = [{"name": "backpack", "kind": "stiff", "location": None, "locations": [[0.0, 1.1, -0.25], [0.1, 1.0, -0.2]]},
            {"name": "cape", "kind": "sway", "location": [0.0, 0.1, 1.0], "locations": []},
            {"name": "lid", "kind": "stiff", "location": None, "locations": []}]
    assert [r["name"] for r in dp.for_stiff(rows)] == ["backpack"]
    assert len(dp.for_stiff(rows)[0]["locations"]) == 2
    assert [r["name"] for r in dp.for_springs(rows)] == ["cape"]


def test_placed_stiff_takes_points_and_caps_them():
    declared = [{"name": "backpack", "kind": "stiff"}]
    rows = dp.placed([{"name": "backpack", "locations": [[0, 1, 0], [0.1, 1, 0]]}], declared, "humanoid")
    assert rows[0]["locations"] == [[0.0, 1.0, 0.0], [0.1, 1.0, 0.0]]
    too_many = [[0, 1, 0]] * (dp.MAX_POINTS + 1)
    try:
        dp.placed([{"name": "backpack", "locations": too_many}], declared, "humanoid")
        raise AssertionError("expected a PartError")
    except dp.PartError:
        pass
