"""vgac/texfidelity.py on small synthetic models: a flat polygon with a textured face, drawn flat from the front."""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from vgac import face, quality, texfidelity

TEX = 256


def _texture(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    noise = face.blur(rng.random((TEX, TEX, 3)), 1.0)
    noise = (noise - noise.min()) / (noise.max() - noise.min())
    stripes = (np.sin(np.arange(TEX) / 5.0)[None, :, None] * 0.5 + 0.5) * np.array([0.9, 0.5, 0.2])
    return np.clip(0.55 * noise + 0.45 * stripes, 0, 1)


def _glb(path, tex: np.ndarray) -> str:
    import trimesh

    n = 7
    ang = np.linspace(0, 2 * np.pi, n, endpoint=False)
    rim = np.stack([0.45 * np.cos(ang) * (1 + 0.2 * np.sin(3 * ang)), 0.5 * np.sin(ang), np.zeros(n)], axis=1)
    verts = np.vstack([[0, 0, 0], rim])
    faces = np.array([[0, 1 + i, 1 + (i + 1) % n] for i in range(n)])
    uv = np.stack([verts[:, 0] + 0.5, verts[:, 1] + 0.5], axis=1)
    mat = trimesh.visual.material.PBRMaterial(baseColorTexture=Image.fromarray(face.to_uint8(tex)))
    mesh = trimesh.Trimesh(verts, faces, visual=trimesh.visual.TextureVisuals(uv=uv, material=mat), process=False)
    mesh.export(str(path))
    return str(path)


def _picture(glb: str, tex: np.ndarray, roll: int = 0) -> np.ndarray:
    """The model drawn the way a picture of it would be: an RGBA cut-out, optionally shifted sideways."""
    mesh = face.load_mesh(glb)
    cam = face.Camera(target=np.array([0.0, 0.0, 0.0]), yaw_deg=0, scale=1.3, size=1024)
    r = face.render_view(mesh, tex, cam, background=0.0)
    rgba = np.dstack([face.to_uint8(r["image"]), (r["mask"] * 255).astype(np.uint8)])
    return np.roll(rgba, roll, axis=1)


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    d = tmp_path_factory.mktemp("fid")
    tex = _texture()
    glb = _glb(d / "a.glb", tex)
    return d, tex, glb, _picture(glb, tex)


def _score(glb, pic):
    return texfidelity.score(glb, pic, yaw=0, views=False)


def test_identical_copy_scores_as_identical(base):
    _, _, glb, pic = base
    r = _score(glb, pic)
    assert r["fit_ok"] and r["silhouette_iou"] > 0.95
    assert r["ssim"] > 0.9
    assert r["delta_e"] < 3
    assert 0.6 < r["sharpness_ratio"] < 1.6


def test_fit_absorbs_a_shift(base):
    _, _, glb, pic = base
    shifted = _picture(glb, base[1], roll=60)
    r = _score(glb, shifted)
    assert r["silhouette_iou"] > 0.95 and r["ssim"] > 0.9


def test_blurred_copy_is_less_sharp(base):
    d, tex, glb, pic = base
    blurred = face.blur(tex, 5.0)
    soft_glb = _glb(d / "soft.glb", blurred)
    sharp = _score(glb, pic)
    soft = _score(soft_glb, pic)
    assert soft["sharpness_ratio"] < 0.7 * sharp["sharpness_ratio"]
    assert soft["ssim"] < sharp["ssim"]


def test_hue_shifted_copy_has_larger_colour_difference(base):
    d, tex, glb, pic = base
    shifted_glb = _glb(d / "hue.glb", tex[..., [2, 0, 1]])
    same = _score(glb, pic)
    shifted = _score(shifted_glb, pic)
    assert shifted["delta_e"] > same["delta_e"] + 8


def test_checks_pass_and_fail_report_only(base):
    d, tex, glb, pic = base
    ok = texfidelity.fidelity_checks(_score(glb, pic))
    assert {c["id"] for c in ok} == {"texture_sharp", "texture_colour_matches_picture"}
    assert all(c["ok"] and c["severity"] == "warn" and c.get("help") for c in ok)
    bad_glb = _glb(d / "bad.glb", face.blur(tex[..., [2, 0, 1]], 3.0))
    bad = texfidelity.fidelity_checks(_score(bad_glb, pic))
    assert all(not c["ok"] and c["severity"] == "warn" and c.get("help") for c in bad) and len(bad) == 2
    assert not quality.summarise(bad)["gate_ok"] or all(c["severity"] == "warn" for c in bad)


def test_not_measured_checks_when_the_fit_is_poor(base):
    d, tex, glb, pic = base
    half = pic.copy()
    half[:, : half.shape[1] // 2, 3] = 0  # half the silhouette missing
    r = _score(glb, half)
    for res in ({**r, "fit_ok": False}, {"error": "x"}):
        got = texfidelity.fidelity_checks(res)
        # never silently missing and never a pass
        assert {c["id"] for c in got} == {"texture_sharp", "texture_colour_matches_picture"}
        assert all(not c["passed"] and c["vars"]["measured"] is False and c["help"].startswith("Not measured:") and "fix" not in c
                   for c in got)
    assert "line up" in texfidelity.fidelity_checks({**r, "fit_ok": False})[0]["help"]
    assert "could not be drawn" in texfidelity.fidelity_checks({"error": "x"})[0]["help"]


def test_checks_for_never_raises(tmp_path):
    assert texfidelity.texture_checks_for(tmp_path / "missing.glb", tmp_path / "missing.png") == []


def test_picture_without_alpha_uses_bright_pixels():
    img = np.zeros((64, 64, 3), np.uint8)
    img[16:48, 16:48] = 200
    img[30:34, 30:34] = 0  # a hole inside the figure is filled
    rgb, mask = texfidelity.load_picture(img)
    assert mask[32, 32] and not mask[2, 2] and mask.sum() == 32 * 32


def test_atlas_detail_is_lower_for_detailed_textures():
    mesh = face.load_mesh  # noqa: F841 (keeps the import used)
    rng = np.random.default_rng(1)
    noisy = rng.random((128, 128, 3))
    smooth = face.blur(noisy, 4.0)
    cover = np.ones((128, 128), bool)
    assert texfidelity.real_detail_db(noisy, cover) < texfidelity.real_detail_db(smooth, cover)
