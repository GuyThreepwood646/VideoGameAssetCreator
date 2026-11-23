"""The clean-up's hole fill keeps the texture layout whole (vgac/blender/fillmend.py and prep.py's fill_holes_mended).

Blender's fill gave new faces texture corners from different texture pieces, so they spanned the texture: an open
TRELLIS.2 cottage read 94 percent shared and the picture paint refused it as mirrored (bakeoff/mirrored-layout). The
pure helpers are tested here, and prep.py on a cube whose every side is its own texture piece, with holes cut out."""

from __future__ import annotations

import json
import subprocess

import numpy as np
import pytest

from vgac.blender import fillmend

from test_great_assets_laneb import BACKEND, _blender

approx = pytest.approx


# --- the pure helpers -------------------------------------------------------------------


def test_edge_ratios_and_a_sliver_is_wrong_though_it_has_little_texture_area():
    pts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float)
    assert fillmend.edge_ratios(pts, pts[:, :2] * 0.1) == approx([0.1, 0.1, 0.1])
    # corners nearly in a line in the texture but far apart: almost no texture area, one edge eight times too long
    sliver = np.array([[0.1, 0.1], [0.9, 0.1001], [0.5, 0.1]])
    assert not fillmend.is_wrong(pts, pts[:, :2] * 0.1, median=0.1)
    assert fillmend.is_wrong(pts, sliver, median=0.1)
    assert not fillmend.is_wrong(pts, sliver, median=0.0)  # no median: nothing is called wrong


def test_place_polygon_keeps_the_edge_the_shape_and_lands_away_from_the_neighbour():
    square = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=float)
    ua, ub = np.array([0.5, 0.5]), np.array([0.6, 0.5])
    for away in (np.array([0.55, 0.45]), np.array([0.55, 0.55])):
        got = fillmend.place_polygon(square, 0, 1, ua, ub, away)
        assert got[0] == approx(ua) and got[1] == approx(ub)
        sides = np.linalg.norm(np.roll(got, -1, axis=0) - got, axis=1)
        assert sides == approx([0.1, 0.1, 0.1, 0.1])  # the shape kept at the edge's texel size
        assert np.sign(got[:, 1].mean() - 0.5) == -np.sign(away[1] - 0.5)


def test_place_polygon_keeps_the_area_of_a_bent_patch():
    # two unit squares at a right angle (a hole across a cube edge): laid onto one square's plane it would fold flat
    bent = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [1, 1, 1], [0, 1, 1], [0, 1, 0]], dtype=float)
    got = fillmend.place_polygon(bent, 0, 1, (0.0, 0.0), (0.1, 0.0), (0.05, -0.05))
    x, y = got[:, 0], got[:, 1]
    area = 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
    assert area > 0.5 * 0.01  # at least half the two squares' texture area (0.02 at this texel size)
    assert fillmend.place_polygon(bent, 0, 1, (0.0, 0.0), (0.0, 0.0), (0.0, -1.0)) is None  # an edge of no length


def test_coverage_counts_the_used_texels_under_a_polygon():
    c = fillmend.Coverage(100)
    c.add(np.array([[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]]))
    assert c.over(np.array([[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]])) == 2500
    assert c.over(np.array([[0.6, 0.6], [0.9, 0.6], [0.9, 0.9]])) == 0
    assert c.over(np.array([[0.4, 0.0], [0.6, 0.0], [0.6, 0.5], [0.4, 0.5]])) == approx(500, abs=60)


# --- Blender ----------------------------------------------------------------------------


def _cube(holes: tuple[str, ...], n: int = 8):
    """A 1 m cube of six n x n grids, every side its own cell of a 3 x 2 texture (split corners at the cube edges, as
    a 3D builder's unwrap leaves them). "across": a quad either side of the edge y = z = 0.5, so the hole's rim has
    corners from two texture pieces. "inside": 2 x 2 quads in the middle of one side, so the rim stays in one piece."""
    V, UV, F = [], [], []
    axes = [(0, 1, 2, 1), (0, 1, 2, -1), (1, 2, 0, 1), (1, 2, 0, -1), (2, 0, 1, 1), (2, 0, 1, -1)]
    for side, (a, b, c, s) in enumerate(axes):
        g = np.linspace(-0.5, 0.5, n + 1)
        A, B = np.meshgrid(g, g)
        p = np.zeros((A.size, 3))
        p[:, a], p[:, b], p[:, c] = A.ravel(), B.ravel(), 0.5 * s
        cu, cv = side % 3, side // 3
        uv = np.stack([(cu + 0.05 + 0.9 * (A.ravel() + 0.5)) / 3, (cv + 0.05 + 0.9 * (B.ravel() + 0.5)) / 2], 1)
        base = sum(len(x) for x in V)
        for i in range(n):
            for j in range(n):
                if "across" in holes and ((side == 0 and (i, j) == (n - 1, n // 2)) or (side == 4 and (i, j) == (n // 2, n - 1))):
                    continue
                if "inside" in holes and side == 2 and n // 2 - 1 <= i <= n // 2 and n // 2 - 1 <= j <= n // 2:
                    continue
                v0 = base + i * (n + 1) + j
                q = (v0, v0 + 1, v0 + n + 2, v0 + n + 1)[::1 if s > 0 else -1]
                F += [(q[0], q[1], q[2]), (q[0], q[2], q[3])]
        V.append(p)
        UV.append(uv)
    return np.concatenate(V), np.concatenate(UV), np.array(F)


def _layout(path):
    """(the app's own shared share at 512, the worst texture-per-surface stretch against the median, triangles)."""
    from vgac import face, srcproject

    m = face.load_mesh(path)
    v, f, uv = m["verts"], m["faces"], m["uv"]
    a3 = 0.5 * np.linalg.norm(np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]), axis=1)
    t = uv[f]
    a2 = 0.5 * np.abs((t[:, 1, 0] - t[:, 0, 0]) * (t[:, 2, 1] - t[:, 0, 1]) - (t[:, 1, 1] - t[:, 0, 1]) * (t[:, 2, 0] - t[:, 0, 0]))
    r = a2 / np.maximum(a3, 1e-12)
    return srcproject.Atlas(m, 512, gutter=0).shared, float(r.max() / np.median(r[a3 > 1e-9])), len(f)


def _prep_cube(tmp_path, holes):
    exe = _blender()
    if not exe:
        pytest.skip("Blender not installed")
    trimesh = pytest.importorskip("trimesh")
    from PIL import Image

    name = "+".join(holes) or "none"
    verts, uv, faces = _cube(holes)
    mat = trimesh.visual.material.PBRMaterial(baseColorTexture=Image.fromarray(np.full((16, 16, 3), 180, np.uint8)))
    src = tmp_path / f"cube_{name}.glb"
    trimesh.Trimesh(verts, faces, visual=trimesh.visual.TextureVisuals(uv=uv, material=mat), process=False).export(src)
    out = tmp_path / f"cube_{name}_prep.glb"
    subprocess.run([exe, "-b", "--factory-startup", "--python", str(BACKEND / "vgac" / "blender" / "prep.py"), "--", str(src),
                    str(out), "box", "--budget", "100000"], check=True, capture_output=True, timeout=300)
    return _layout(src), _layout(out), json.loads((tmp_path / f"cube_{name}_prep_report.json").read_text())


def test_blender_fill_across_two_texture_pieces_leaves_the_layout_whole(tmp_path):
    (shared0, _, tris0), (shared1, stretch1, tris1), report = _prep_cube(tmp_path, ("across",))
    assert tris1 > tris0  # the hole is still filled
    # Before the mend: one triangle 10.9 times the median stretch, the shared share 0.027 from 0.004.
    assert stretch1 <= 4.0
    assert shared1 <= shared0 + 0.002
    assert report["fill_faces"] >= 1 and report["fill_mended"] >= 1 and report["fill_unmended"] == 0


def test_blender_fill_inside_one_texture_piece_and_no_hole_are_left_as_they_were(tmp_path):
    (shared0, _, tris0), (shared1, stretch1, tris1), report = _prep_cube(tmp_path, ("inside",))
    assert tris1 > tris0 and report["fill_faces"] >= 1
    assert report["fill_mended"] == 0  # Blender's own corners were right: nothing to mend
    assert stretch1 <= 1.5 and shared1 <= shared0 + 0.001
    (_, _, tris0), (_, stretch1, tris1), report = _prep_cube(tmp_path, ())
    assert tris1 == tris0 and report["fill_faces"] == 0 and stretch1 <= 1.01
