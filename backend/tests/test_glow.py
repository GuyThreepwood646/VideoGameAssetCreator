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
