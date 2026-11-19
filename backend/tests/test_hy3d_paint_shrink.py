"""Hunyuan3D paint-then-shrink (WP-52 option 1): the raw mesh is unwrapped at step 3, prep paints it from the pictures at
full detail, then shrinks it to the budget with the paint baked onto the shrunk model. TRELLIS.2 is not affected."""

from types import SimpleNamespace

from PIL import Image

from vgac.graphs import hy3d
from vgac.stages import prep

VIEWS = {v: f"{v}_white.png" for v in hy3d.HY3D_VIEWS}


def test_unwrap_node_is_added_only_when_asked():
    plain = hy3d.hy3d_graph(VIEWS, "p", seed=733377461)
    assert "unwrap" not in plain
    assert plain["glb"]["inputs"]["mesh"] == ["mesh", 0]

    g = hy3d.hy3d_graph(VIEWS, "p", seed=733377461, unwrap_resolution=2048, unwrap_padding=8)
    assert g["unwrap"]["class_type"] == "UnwrapMesh"
    assert g["unwrap"]["inputs"]["mesh"] == ["mesh", 0]
    assert g["unwrap"]["inputs"]["segmenter"] == "pec"
    assert g["unwrap"]["inputs"]["resolution"] == 2048
    assert g["unwrap"]["inputs"]["padding"] == 8
    assert g["glb"]["inputs"]["mesh"] == ["unwrap", 0]
    # the rest of the graph is the same as the plain one
    assert {k: v for k, v in g.items() if k not in ("unwrap", "glb")} == {k: v for k, v in plain.items() if k not in ("glb",)}


def test_hy3d_mesh_without_its_own_base_colour_is_painted_first():
    hy = SimpleNamespace(stage="mesh", params={"engine": "hy3d"}, metrics={})
    assert prep.paints_full_first(hy, {}) is True


def test_trellis_mesh_is_never_painted_first():
    trellis = SimpleNamespace(stage="mesh", params={"engine": "trellis"}, metrics={})
    assert prep.paints_full_first(trellis, {}) is False
    assert prep.paints_full_first(trellis, {"base_color": "x.png"}) is False


def test_hy3d_with_its_own_base_colour_is_not_painted_first():
    hy = SimpleNamespace(stage="mesh", params={"engine": "hy3d"}, metrics={})
    assert prep.paints_full_first(hy, {"base_color": "x.png"}) is False


def test_no_mesh_version_is_not_painted_first():
    assert prep.paints_full_first(None, {}) is False


def test_placeholder_maps_are_neutral_and_the_right_size(tmp_path):
    files = prep.placeholder_maps(tmp_path, 512, keep_normal=True)
    assert set(files) == {"base_color", "normal", "metallic", "roughness", "ao"}
    base = Image.open(files["base_color"])
    assert base.size == (512, 512)
    assert base.getpixel((0, 0)) == (128, 128, 128)
    assert Image.open(files["metallic"]).getpixel((5, 5)) == (0, 0, 0)
    assert Image.open(files["normal"]).getpixel((5, 5)) == (128, 128, 255)


def test_placeholder_without_a_normal_map_when_the_look_keeps_none(tmp_path):
    files = prep.placeholder_maps(tmp_path, 256, keep_normal=False)
    assert "normal" not in files
    assert set(files) == {"base_color", "metallic", "roughness", "ao"}


def test_remesh_sits_between_the_raw_mesh_and_the_unwrap():
    g = hy3d.hy3d_graph(VIEWS, "p", seed=733377461, unwrap_resolution=2048, unwrap_padding=8, remesh=256)
    assert g["remesh"]["class_type"] == "RemeshMesh"
    assert g["remesh"]["inputs"]["mesh"] == ["mesh", 0]
    assert g["remesh"]["inputs"]["resolution"] == 256
    assert g["remesh"]["inputs"]["sign_mode"] == "solid"
    assert g["unwrap"]["inputs"]["mesh"] == ["remesh", 0]
    assert g["glb"]["inputs"]["mesh"] == ["unwrap", 0]


def test_no_remesh_unless_asked():
    g = hy3d.hy3d_graph(VIEWS, "p", seed=733377461, unwrap_resolution=2048, unwrap_padding=8)
    assert "remesh" not in g
    assert g["unwrap"]["inputs"]["mesh"] == ["mesh", 0]
