"""texfidelity wider fit: a picture drawn from nearly overhead fits with a steep camera, and a
picture with the same outline but none of the model's inner detail is refused by the edge guard."""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from vgac import face, texfidelity

TEX = 256


def _texture() -> np.ndarray:
    rng = np.random.default_rng(3)
    noise = face.blur(rng.random((TEX, TEX, 3)), 1.0)
    noise = (noise - noise.min()) / (noise.max() - noise.min())
    stripes = (np.sin(np.arange(TEX) / 5.0)[None, :, None] * 0.5 + 0.5) * np.array([0.9, 0.5, 0.2])
    return np.clip(0.55 * noise + 0.45 * stripes, 0, 1)


def _plate(path, tex: np.ndarray) -> str:
    """A flat seven-sided plate lying on the ground (a lumpy outline with no mirror symmetry), its top textured."""
    import trimesh

    n = 7
    ang = np.linspace(0, 2 * np.pi, n, endpoint=False)
    r = 0.5 * (1 + 0.25 * np.sin(3 * ang) + 0.1 * np.cos(ang))
    rim = np.stack([r * np.cos(ang), np.zeros(n), -r * np.sin(ang)], axis=1)
    verts = np.vstack([[0, 0, 0], rim])
    faces = np.array([[0, 1 + i, 1 + (i + 1) % n] for i in range(n)])
    uv = np.stack([verts[:, 0] + 0.5, -verts[:, 2] + 0.5], axis=1)
    mat = trimesh.visual.material.PBRMaterial(baseColorTexture=Image.fromarray(face.to_uint8(tex)))
    mesh = trimesh.Trimesh(verts, faces, visual=trimesh.visual.TextureVisuals(uv=uv, material=mat), process=False)
    mesh.export(str(path))
    return str(path)


def _from_above(glb: str, tex: np.ndarray, flat: bool = False) -> np.ndarray:
    """The model drawn from tilt 75 as an RGBA cut-out; flat paints the cut-out one colour (same outline, no detail)."""
    mesh = face.load_mesh(glb)
    cam = face.Camera(target=np.zeros(3), yaw_deg=0, scale=1.6, size=1024, tilt_deg=75.0)
    r = face.render_view(mesh, tex, cam, background=0.0)
    rgb = face.to_uint8(r["image"])
    if flat:
        rgb[:] = (120, 90, 60)
    return np.dstack([rgb, (r["mask"] * 255).astype(np.uint8)])


@pytest.fixture(scope="module")
def plate(tmp_path_factory):
    d = tmp_path_factory.mktemp("wide")
    tex = _texture()
    return _plate(d / "plate.glb", tex), tex


def test_picture_from_overhead_fits_with_a_steep_camera(plate):
    glb, tex = plate
    r = texfidelity.score(glb, _from_above(glb, tex), views=False)
    assert r["fit_ok"], r
    assert r["tilt"] >= 60 and r["silhouette_iou"] > 0.9
    assert r["edge_term"] >= texfidelity.EDGE_MIN


def test_same_outline_without_the_inner_detail_is_refused(plate):
    glb, tex = plate
    r = texfidelity.score(glb, _from_above(glb, tex, flat=True), views=False)
    assert not r["fit_ok"], r
    refused = r["wide_fit_refused"]
    assert refused["silhouette_iou"] > 0.9 and refused["edge_term"] < texfidelity.EDGE_MIN
    checks = texfidelity.fidelity_checks(r)
    assert all("did not match the picture's" in str(c) for c in checks)
