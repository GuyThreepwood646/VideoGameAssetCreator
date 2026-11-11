"""Lane C of docs/TEXTURE-FIDELITY-PLAN.md: the bake chain settings and the resizes after the 3D step."""
import re
from pathlib import Path

import numpy as np
from PIL import Image

from vgac import imaging
from vgac.stages import export

REPO = Path(__file__).resolve().parents[2]
REBAKE = (REPO / "backend" / "vgac" / "blender" / "rebake.py").read_text(encoding="utf-8")
IMPORTER = (REPO / "unity-sandbox" / "Assets" / "Editor" / "VgacImport.cs").read_text(encoding="utf-8")


def _detail(n=256) -> Image.Image:
    rng = np.random.default_rng(3)
    y, x = np.mgrid[0:n, 0:n]
    a = 128 + 60 * np.sin(x / 5.0) * np.cos(y / 7.0) + rng.normal(0, 12, (n, n))
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "L")


def _psnr(a, b):
    return 10 * np.log10(255.0 ** 2 / max(((np.asarray(a, float) - np.asarray(b, float)) ** 2).mean(), 1e-9))


def test_rebake_defaults_measured_on_the_knight_and_chest():
    """Linear source sampling, 4 samples, no oversampling: Cubic (Blender's is a B-spline), 16 samples and a 2x bake were
    all softer or no sharper in bakeoff/out/bake_chain/results.md. Adjacent-faces margin gave a smaller seam step."""
    m = re.search(r"OPTIONS = \{([^}]*)\}", REBAKE)
    assert m
    opts = m.group(1)
    assert '"interpolation": "Linear"' in opts and '"samples": 4' in opts and '"oversample": 1' in opts
    assert '"margin_type": "ADJACENT_FACES"' in opts
    assert "view_transform = \"Standard\"" in REBAKE  # the colour bake is not passed through a view transform


def test_read_map_resizes_colour_with_lanczos(tmp_path):
    big = _detail(256)
    src = tmp_path / "c.png"
    big.convert("RGB").save(src)
    out = imaging.read_map(src, 64)
    assert out.shape == (64, 64, 3) and 0.0 <= out.min() and out.max() <= 1.0
    lanczos = np.asarray(big.resize((64, 64), Image.LANCZOS), float) / 255.0
    assert np.allclose(out[..., 0], lanczos, atol=1 / 255 + 1e-6)
    g = imaging.read_map(src, 64, grey=True)
    assert g.shape == (64, 64)


def test_export_channel_resize_is_lanczos_and_keeps_flat_maps(tmp_path):
    flat = tmp_path / "f.png"
    Image.new("L", (16, 16), 77).save(flat)
    assert set(np.unique(export._channel(flat, (64, 64)))) == {77}
    big = _detail(128)
    p = tmp_path / "d.png"
    big.resize((32, 32), Image.BOX).save(p)
    up = export._channel(p, (128, 128))
    ref = np.asarray(Image.open(p).resize((128, 128), Image.LANCZOS))
    assert (up == ref).all()
    assert _psnr(up, big) >= _psnr(np.asarray(Image.open(p).resize((128, 128), Image.BILINEAR)), big)


def test_importer_smooth_colour_is_high_quality_and_anisotropic():
    assert 'Version = "18"' in IMPORTER
    assert "TextureCompressionQuality.Best" in IMPORTER and "TextureImporterFormat.BC7" in IMPORTER
    assert "TextureImporterFormat.ASTC_4x4" in IMPORTER and "anisoLevel = 8" in IMPORTER
    # only base colour on smooth, compressed, mip-mapped looks; the crisp path resets what an earlier import set
    assert 'var sharpColour = (t == "base_color.png" || t == "hair_base_color.png") && !point && compress;' in IMPORTER
    assert "ti.anisoLevel = 1;" in IMPORTER and "ps.overridden = false;" in IMPORTER
    assert "TextureImporterType.NormalMap" in IMPORTER


def test_trellis_texture_steps_is_a_parameter_defaulting_to_12():
    from vgac.graphs.trellis import convert_graph

    assert convert_graph("a.png", "p")["k4"]["inputs"]["steps"] == 12
    assert convert_graph("a.png", "p", tex_steps=24)["k4"]["inputs"]["steps"] == 24
