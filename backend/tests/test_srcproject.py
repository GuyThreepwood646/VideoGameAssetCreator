"""vgac/srcproject.py: the picture's own pixels painted into the atlas through the UVs (synthetic models, no Blender)."""
from __future__ import annotations

import numpy as np
import pytest

from vgac import face, srcproject

T = 128
SIZE = 256


def grid_quad(z: float, front: bool, u0: float, v0: float, n: int = 8) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A 1 x 1 quad in the XY plane at depth z, n x n cells, its island at (u0, v0) of size 0.4 in the atlas.
    front: it faces +Z; otherwise -Z."""
    xs = np.linspace(-0.5, 0.5, n + 1)
    verts, uv = [], []
    for j in range(n + 1):
        for i in range(n + 1):
            verts.append([xs[i], xs[j], z])
            uv.append([u0 + 0.4 * (xs[i] + 0.5), v0 + 0.4 * (xs[j] + 0.5)])
    faces = []
    for j in range(n):
        for i in range(n):
            a, b, c, d = j * (n + 1) + i, j * (n + 1) + i + 1, (j + 1) * (n + 1) + i + 1, (j + 1) * (n + 1) + i
            tris = [(a, b, c), (a, c, d)]
            faces += tris if front else [(t[0], t[2], t[1]) for t in tris]
    return np.array(verts), np.array(faces), np.array(uv)


def model(with_back: bool = False) -> dict[str, np.ndarray]:
    v, f, uv = grid_quad(0.0, True, 0.05, 0.1)
    if with_back:
        v2, f2, uv2 = grid_quad(-1.0, False, 0.55, 0.1)
        f = np.concatenate([f, f2 + len(v)])
        v, uv = np.concatenate([v, v2]), np.concatenate([uv, uv2])
    return {"verts": v, "faces": f, "uv": uv, "vnormals": face.smooth_normals(v, f)}


def pattern(fx: np.ndarray, fy: np.ndarray) -> np.ndarray:
    """A colourful pattern with detail at a few texels' scale; fx, fy in 0..1 across the quad (fy down)."""
    r = 0.5 + 0.4 * np.sin(2 * np.pi * 6 * fx)
    g = 0.15 + 0.7 * fy
    b = 0.5 + 0.4 * np.sin(2 * np.pi * 5 * fy + 1.0)
    return np.stack([r, g, b], axis=-1)


def picture(rect: tuple[int, int, int, int] = (40, 216, 40, 216), n: int = 256) -> np.ndarray:
    """RGBA picture: the pattern inside rect (x0, x1, y0, y1), transparent outside."""
    x0, x1, y0, y1 = rect
    ys, xs = np.mgrid[0:n, 0:n] + 0.5
    rgb = pattern((xs - x0) / (x1 - x0), (ys - y0) / (y1 - y0))
    a = ((xs > x0) & (xs < x1) & (ys > y0) & (ys < y1)).astype(np.float64)
    return np.concatenate([np.clip(rgb, 0, 1) * a[..., None], a[..., None]], axis=-1)


def island_expected() -> tuple[np.ndarray, np.ndarray]:
    """The front island's texels and what the pattern says each should be."""
    ys, xs = np.mgrid[0:T, 0:T] + 0.5
    u, v = xs / T, 1.0 - ys / T
    inside = (u > 0.05 + 0.03) & (u < 0.45 - 0.03) & (v > 0.1 + 0.03) & (v < 0.5 - 0.03)
    x = (u - 0.05) / 0.4 - 0.5
    y = (v - 0.1) / 0.4 - 0.5
    return inside, pattern(x + 0.5, 0.5 - y)


def soft_base() -> np.ndarray:
    """What TRELLIS would have given: the pattern blurred, and a little darker."""
    inside, exp = island_expected()
    base = np.full((T, T, 3), 0.4)
    base[inside] = exp[inside]
    ys, xs = np.mgrid[0:T, 0:T] + 0.5
    u, v = xs / T, 1.0 - ys / T
    whole = (u > 0.05) & (u < 0.45) & (v > 0.1) & (v < 0.5)
    full = pattern((u - 0.05) / 0.4, 1.0 - (v - 0.1) / 0.4)
    base = np.where(whole[..., None], full, base)
    return np.clip(face.blur(base, 3.0) * 0.8, 0, 1)


def test_picture_pixels_reach_the_atlas() -> None:
    mesh, tex = model(), soft_base()
    r = srcproject.paint(mesh, tex, picture(), mode="off", size=SIZE, picture_yaws=[0.0])
    assert r["applied"], r["reason"]
    inside, exp = island_expected()
    new = r["base_color"]
    err = np.abs(new - exp)[inside].mean()
    old = np.abs(tex - exp)[inside].mean()
    assert err < 0.06, err                    # the picture's pixels, not the soft colour
    assert err < old * 0.5                    # and much closer than what the model had
    lap = lambda a: face.luminance(a) - face.blur(face.luminance(a), 1.5)  # noqa: E731
    assert lap(new)[inside].std() > 1.5 * lap(tex)[inside].std()   # sharper too
    m = r["metrics"]
    assert m["srcproject_painted_share"] > 0.7
    assert m["srcproject_front_after"]["lab_diff"] < m["srcproject_front_before"]["lab_diff"]


def test_a_misfitted_view_is_refused() -> None:
    mesh, tex = model(with_back=True), soft_base()   # the far back quad gives the frame its margin
    thin = picture(rect=(110, 146, 20, 236))          # a tall thin strip: not this model's outline
    r = srcproject.paint(mesh, tex, thin, mode="off", size=SIZE, picture_yaws=[0.0])
    assert not r["applied"]
    assert "silhouette" in (r["reason"] or "")
    assert r["base_color"] is tex or np.array_equal(r["base_color"], tex)
    assert r["metrics"]["srcproject_views"]["picture"]["refused"]


def test_unseen_texels_keep_their_colour() -> None:
    mesh = model(with_back=True)
    tex = soft_base()
    ys, xs = np.mgrid[0:T, 0:T] + 0.5
    u, v = xs / T, 1.0 - ys / T
    back = (u > 0.55) & (u < 0.95) & (v > 0.1) & (v < 0.5)
    tex[back] = [0.1, 0.2, 0.8]                       # the back island: a flat blue the picture knows nothing about
    r = srcproject.paint(mesh, tex, picture(), mode="off", size=SIZE, picture_yaws=[0.0])
    assert r["applied"], r["reason"]
    assert np.allclose(r["base_color"][back], tex[back], atol=1e-6)
    assert r["metrics"]["srcproject_share_by_view"]["picture"] > 0.2


def test_grazing_surface_is_not_painted() -> None:
    """The same quad seen edge-on (turned 85 degrees) is past the angle cut-off: nothing is painted."""
    mesh = model()
    cam = face.Camera(target=np.zeros(3), yaw_deg=85.0, scale=1.5, size=SIZE)
    v = srcproject.View("picture", 1.0)
    v.cam, v.raster = cam, srcproject.Raster(mesh, cam)
    v.feather = np.ones((SIZE, SIZE))
    atlas = srcproject.Atlas(mesh, T)
    p = srcproject.project_view(atlas, v, SIZE)
    assert len(p["sel"]) == 0


def test_cos_weight_shape() -> None:
    c = np.array([1.0, 0.7, 0.5, 0.3, 0.2, 0.0, -0.5])
    w = srcproject.cos_weight(c)
    assert w[0] == pytest.approx(1.0)
    assert all(w[i] >= w[i + 1] for i in range(len(w) - 1))
    assert w[4] == 0.0 and w[5] == 0.0 and w[6] == 0.0   # past 75 degrees


def test_prepare_picture_cuts_out_the_backdrop() -> None:
    img = np.zeros((128, 128, 3))
    img[30:100, 40:90] = [0.8, 0.3, 0.2]
    rgb, alpha = srcproject.prepare_picture(img, n=128)
    assert alpha[65, 65] > 0.9 and alpha[5, 5] < 0.1
    assert abs(float(rgb[65, 65, 0]) - 0.8) < 0.02
