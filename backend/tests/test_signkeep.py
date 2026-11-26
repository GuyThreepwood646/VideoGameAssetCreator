"""PL-123: triangles under a lettered sign stay as painted through the Low-poly one-colour-per-triangle clean-up."""
from __future__ import annotations

import types

import numpy as np

from vgac import looktex, signkeep


def test_faces_under_picks_triangles_mostly_under_the_mask_and_ignores_edge_on_pixels():
    tri = np.full((10, 10), -1)
    tri[:, :5] = 0          # triangle 0 on the left half
    tri[:, 5:] = 1          # triangle 1 on the right half
    mask = np.zeros((10, 10), bool)
    mask[:, :4] = True      # 80 percent of triangle 0, none of triangle 1
    facing = np.ones((10, 10))
    out = signkeep.faces_under(mask, tri, facing, 3)
    assert out.tolist() == [True, False, False]
    # the same pixels seen edge-on do not count (a sign seen from the side is not read)
    assert not signkeep.faces_under(mask, tri, np.full((10, 10), 0.1), 3).any()


def test_a_tiny_triangle_is_not_judged():
    tri = np.full((6, 6), -1)
    tri[0, :2] = 0
    mask = np.ones((6, 6), bool)
    assert not signkeep.faces_under(mask, tri, np.ones((6, 6)), 1).any()


def test_kept_triangles_stay_as_painted_and_the_rest_flatten():
    # two triangles each holding a thin stroke (too little detail to count as "detailed"): flattened today, kept when named
    uv = np.array([[0.05, 0.05], [0.45, 0.05], [0.05, 0.45], [0.55, 0.55], [0.95, 0.55], [0.55, 0.95]], float)
    mesh = {"verts": np.zeros((6, 3)), "faces": np.array([[0, 1, 2], [3, 4, 5]]), "uv": uv, "vnormals": np.zeros((6, 3))}
    base = np.full((64, 64, 3), 0.9)
    tri = looktex.triangle_map(mesh, 64)
    spots = []
    for f in (0, 1):   # a stroke of 10 texels along a row well inside each triangle
        rows = [r for r in range(64) if (tri[r] == f).sum() >= 14]
        r = rows[len(rows) // 2]
        cols = np.nonzero(tri[r] == f)[0][2:12]
        base[r, cols] = 0.1
        spots.append((r, int(cols[4])))
    today = looktex.colour_per_face(base, mesh, None)["base_color"]
    kept = looktex.colour_per_face(base, mesh, np.array([0]))["base_color"]
    assert abs(today[spots[0]] - 0.1).max() > 0.5            # flattened: the stroke is gone
    assert abs(kept[spots[0]] - 0.1).max() < 0.05            # kept: the stroke is still there
    assert abs(kept[spots[1]] - today[spots[1]]).max() < 1e-6   # the other triangle is unchanged


def test_no_finder_or_no_sign_keeps_nothing(tmp_path, monkeypatch):
    from vgac import face

    mesh = {"verts": np.array([[0, 0, 0], [1, 0, 0], [0, 0, 1]], float), "faces": np.array([[0, 1, 2]]),
            "uv": np.array([[0.1, 0.1], [0.9, 0.1], [0.1, 0.9]], float), "vnormals": np.tile([0.0, -1.0, 0.0], (3, 1))}
    monkeypatch.setattr(face, "load_mesh", lambda p: mesh)
    base = np.full((32, 32, 3), 0.5)
    st = types.SimpleNamespace(finder_python=str(tmp_path / "none.exe"), finder_models=str(tmp_path))
    tris, rec = signkeep.sign_faces(st, "x.glb", base, tmp_path)
    assert tris is None and rec["kept"] == 0 and rec["reason"] == "finder unavailable"
    tris, rec = signkeep.sign_faces(st, "x.glb", base, tmp_path, masks_for=lambda imgs: [np.zeros(i.shape[:2], bool) for i in imgs])
    assert tris is None and rec["reason"] == "no sign found"


def test_a_mask_over_most_of_the_model_is_the_wall_not_a_sign(tmp_path, monkeypatch):
    from vgac import face

    mesh = {"verts": np.array([[0, 0, 0], [1, 0, 0], [0, 0, 1]], float), "faces": np.array([[0, 1, 2]]),
            "uv": np.array([[0.1, 0.1], [0.9, 0.1], [0.1, 0.9]], float), "vnormals": np.tile([0.0, -1.0, 0.0], (3, 1))}
    monkeypatch.setattr(face, "load_mesh", lambda p: mesh)
    base = np.full((32, 32, 3), 0.5)
    everything = lambda imgs: [np.ones(i.shape[:2], bool) for i in imgs]
    tris, rec = signkeep.sign_faces(None, "x.glb", base, tmp_path, masks_for=everything)
    assert tris is None and rec["kept"] == 0 and rec["views_skipped_large"] > 0
