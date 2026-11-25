"""Moving UV islands apart (layoutspread): a short gutter grows to what the texture_padding check asks for and every texel
inside every island stays as it was; a good gutter is not touched by the stage's rule."""
import math

import numpy as np
from PIL import Image

from vgac import imaging, layoutspread
from vgac.stages import prep

TEX = 256


def _glb(path, gap_px):
    """Two square pieces side by side in UV space with `gap_px` empty texels between them."""
    import trimesh

    w = 0.4
    x1 = 0.1 + w + gap_px / TEX
    uv, verts, faces = [], [], []
    for i, x0 in enumerate((0.1, x1)):
        for dx, dy in ((0, 0), (w, 0), (w, w), (0, w)):
            uv.append((x0 + dx, 1.0 - (0.1 + dy)))
            verts.append((i * 2.0 + dx, dy, 0.0))
        b = 4 * i
        faces += [(b, b + 1, b + 2), (b, b + 2, b + 3)]
    m = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
    img = Image.fromarray(np.random.default_rng(2).integers(0, 255, (TEX, TEX, 3), dtype=np.uint8))
    m.visual = trimesh.visual.TextureVisuals(uv=np.array(uv), material=trimesh.visual.material.PBRMaterial(baseColorTexture=img))
    m.export(str(path))


def _map(path):
    rng = np.random.default_rng(1)
    Image.fromarray(rng.integers(0, 255, (TEX, TEX, 3), dtype=np.uint8)).save(path)


def test_short_gutter_is_widened_and_interiors_are_unchanged(tmp_path):
    glb, tex_png = tmp_path / "m.glb", tmp_path / "base_color.png"
    _glb(glb, 1)
    _map(tex_png)
    old = imaging.load_uv_mesh(glb)
    old_labels = imaging.rasterise_uv(old, TEX)["labels"]
    pad = imaging.unwrap_padding(TEX)
    need = math.ceil(prep.PADDING_PASS_FRACTION * pad)
    assert prep.layout_gutter_short(glb, TEX)
    r = layoutspread.spread(glb, {"base_color": tex_png}, tmp_path, TEX, need)
    assert r["moved"], r
    new = imaging.load_uv_mesh(glb)
    new_labels = imaging.rasterise_uv(new, TEX)["labels"]
    assert imaging.gutter_px(new_labels, pad // 2 + 2) >= need
    assert not prep.layout_gutter_short(glb, TEX)
    before, after = np.array(Image.open(tex_png)), np.array(Image.open(r["maps"]["base_color"]))
    for island, (dy, dx) in r["shifts"].items():
        ys, xs = np.nonzero(old_labels == island)
        assert np.array_equal(before[ys, xs], after[ys + int(dy), xs + int(dx)])


def test_islands_that_cannot_be_separated_are_left_alone(tmp_path):
    glb, tex_png = tmp_path / "m.glb", tmp_path / "base_color.png"
    _glb(glb, 1)
    _map(tex_png)
    data = glb.read_bytes()
    r = layoutspread.spread(glb, {"base_color": tex_png}, tmp_path, TEX, 200)
    assert not r["moved"] and r["reason"]
    assert glb.read_bytes() == data


def test_a_good_gutter_is_not_short(tmp_path):
    glb = tmp_path / "good.glb"
    _glb(glb, 12)
    assert not prep.layout_gutter_short(glb, TEX)
