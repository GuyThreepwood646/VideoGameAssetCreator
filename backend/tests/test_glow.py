"""Glow map for scenery (vgac/glow.py): which picture pixels glow, and how the map travels to the export and the importer."""
from pathlib import Path

import numpy as np
import yaml

from vgac import glow
from vgac.stages import export

REPO = Path(__file__).resolve().parents[2]


def _sign(panel, letters):
    """A grey picture with a 60 x 30 sign panel carrying a bar of pale letters, and a plain 60 x 30 box of the same panel colour."""
    img = np.full((200, 200, 3), 0.3)
    img[20:50, 20:80] = panel
    for x in (30, 44, 58):                 # three letters, 6 x 8 pixels each
        img[30:38, x:x + 6] = letters
    img[120:150, 20:80] = panel            # the same colour, no letters
    return img


def test_letters_glow_brightest_and_plain_panels_do_not():
    out, rec = glow.mask_picture(_sign((0.9, 0.2, 0.8), (1.0, 0.9, 1.0)))   # neon pink panel, near-white letters
    assert rec["signs"] == 1
    assert out[33, 47].min() > 0.8                          # the letters glow in their own colour (pale: low saturation)
    assert 0.3 < out[25, 25].max() < out[33, 47].max()      # the panel glows less
    assert out[135, 50].max() == 0.0                        # the same panel without letters does not


def test_warm_signs_and_surfaces_do_not_glow():
    out, rec = glow.mask_picture(_sign((1.0, 0.6, 0.15), (1.0, 0.95, 0.8)))   # orange with pale marks: a box, an awning
    assert out.max() == 0.0 and rec["signs"] == 0


def test_scenery_only_and_travels_to_export():
    env = yaml.safe_load((REPO / "backend/vgac/data/categories/environment.yaml").read_text(encoding="utf-8"))
    assert env["emission_map"] is True
    for p in (REPO / "backend/vgac/data/categories").glob("*.yaml"):
        if p.stem != "environment":
            assert not yaml.safe_load(p.read_text(encoding="utf-8")).get("emission_map"), p.stem
    assert "emission" in export.TEXTURE_KEYS and "emission" in export.DELIVER_KEYS
    assert "emission.png" in export.HANDOFF_TEXTURES
    args = export.pack_args("in.glb", "out", "n", [0.5], "hull", ["glb"], {"shading": "smooth"}, emission="emission.png")
    assert args[args.index("--emission") + 1] == "emission.png"
    assert "--emission" not in export.pack_args("in.glb", "out", "n", [0.5], "hull", ["glb"], {"shading": "smooth"})


def test_importer_assigns_the_emission_slot():
    cs = (REPO / "unity-sandbox/Assets/Editor/VgacImport.cs").read_text(encoding="utf-8")
    assert '"_EmissionMap"' in cs and '"emission.png"' in cs and 'EnableKeyword("_EMISSION")' in cs
    pack = (REPO / "backend/vgac/blender/pack.py").read_text(encoding="utf-8")
    assert 'bsdf.inputs["Emission Color"]' in pack


def test_touch_up_keeps_glow_except_where_painted(tmp_path):
    from PIL import Image
    src = np.zeros((8, 8, 3), np.uint8)
    src[:, :] = (200, 100, 50)
    Image.fromarray(src).save(tmp_path / "e.png")
    painted = np.zeros((8, 8), bool)
    painted[:4] = True
    out = glow.carry(tmp_path / "e.png", tmp_path / "o.png", painted)
    arr = np.asarray(Image.open(out))
    assert arr[:4].max() == 0 and arr[4:].min() == 50
    assert glow.carry(tmp_path / "none.png", tmp_path / "o2.png") is None


def _wide_glow(monkeypatch, share_texels):
    """emission_map over a 10 x 10 texture of which `share_texels` texels glow, with the layout and the paint stubbed out."""
    class FakeAtlas:
        shared = 0
        idx = np.arange(100).reshape(10, 10)
        is_gut = np.zeros((10, 10), dtype=bool)

        def __init__(self, mesh, size):
            pass

    em = np.zeros((10, 10, 3))
    em.reshape(-1, 3)[:share_texels] = 1.0
    monkeypatch.setattr(glow, "Atlas", FakeAtlas)
    monkeypatch.setattr(glow.srcproject, "compose", lambda *a, **k: {"base_color": em, "alpha": np.ones((10, 10))})
    view = type("V", (), {"alpha_img": None})()
    kept = {"views": [view], "images": [np.zeros((4, 4, 3))], "n_primary": 1}
    return kept


def test_share_guard_holds_the_colour_rule_but_not_the_finders_named_parts(monkeypatch):
    kept = _wide_glow(monkeypatch, 60)   # 0.6 of the painted texels glow, over MAX_SHARE
    monkeypatch.setattr(glow, "mask_picture", lambda im, al=None: (np.ones((4, 4, 3)), {"signs": 1}))
    em, rec = glow.emission_map({}, kept, 10)
    assert em is None and rec["reason"] == "bright and saturated over most of the picture"
    em, rec = glow.emission_map({}, kept, 10, gains=[np.ones((4, 4))])
    assert em is not None and rec["made"] and rec["glow_share"] == 0.6


def test_window_glows_only_where_lit():
    from PIL import Image

    from vgac import glow_worker

    rgb = np.full((40, 40, 3), 0.7, np.float32)          # a pale wall
    rgb[10:20, 10:20] = 0.2                              # dark glass
    rgb[25:35, 10:20] = (1.0, 0.7, 0.2)                  # a warm lit pane
    hsv = np.asarray(Image.fromarray((rgb * 255).astype(np.uint8), "RGB").convert("HSV"))
    mask = np.zeros((40, 40), bool)
    mask[8:37, 8:22] = True
    lit = glow_worker.lit_pixels(rgb, hsv, (8, 8, 22, 37), mask)
    assert not lit[10:20, 10:20].any() and lit[25:35, 10:20].all()

    # a pale cream pane beside a bright wall is lit (bright and warm); a shaded brown patch and a grey-green pane are not
    rgb[:] = 0.92
    rgb[10:20, 10:20] = (0.84, 0.80, 0.66)    # cream: H about 44, S 0.21, V 0.84
    rgb[25:35, 10:20] = (0.49, 0.34, 0.25)    # shaded brown: H 17, V 0.49
    rgb[10:20, 25:35] = (0.74, 0.78, 0.70)    # grey-green glass
    hsv = np.asarray(Image.fromarray((rgb * 255).astype(np.uint8), "RGB").convert("HSV"))
    mask[:] = False
    mask[8:37, 8:37] = True
    lit = glow_worker.lit_pixels(rgb, hsv, (8, 8, 37, 37), mask)
    assert lit[10:20, 10:20].all() and not lit[25:35, 10:20].any() and not lit[10:20, 25:35].any()
