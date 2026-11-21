"""A look that ships the maps smaller than they were painted shrinks every map per UV island, so no texel takes in the
colour of a neighbouring island or of the empty space around the islands.

Two islands touch along u = 65/128 on a 128 texture, shrunk 2x to 64: the boundary falls between two texels of one 2x2
block, so a plain box filter blends the two colours in that block. The island shrink must not.
"""

import io
import json
import struct
from pathlib import Path

import numpy as np
from PIL import Image

from vgac import imaging
from vgac.stages import prep

BAKE = 128
SHIPPED = 64
TOL = 2 / 255  # the largest allowed deviation of a shipped texel from its island's colour
U_SPLIT = 66 / 128  # the seam between the islands, inside a 2x block (texels 64 and 65 differ)

COLOUR_A = (0.9, 0.1, 0.1)
COLOUR_B = (0.1, 0.1, 0.9)
BACKGROUND = 0.5


def _two_islands(gap: float = 0.0) -> dict:
    """Island 0 (left, label 1) and island 1 (right, label 2) meeting along U_SPLIT; the rest of the texture is empty.
    gap moves island 1 right by that much in u: a loaded GLB joins islands whose corners share a UV position, so the GLB
    test needs a real gap between them, as an atlas has."""
    lo = U_SPLIT + gap
    uv = np.array([[0, 0], [U_SPLIT, 0], [U_SPLIT, 1], [0, 1], [lo, 0], [1, 0], [1, 1], [lo, 1]], dtype=np.float64)
    return {
        "uv": uv,
        "faces": np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7]]),
        "island": np.array([0, 0, 1, 1]),
        "up": np.zeros(4),
    }


def _assert_island_values(path: Path, uvf: dict, expected: dict[int, object], label: str) -> None:
    """Every texel of each island (by the island's label at the shipped size) is within TOL of that island's value."""
    with Image.open(path) as im:
        arr = np.asarray(im.convert("RGB" if im.mode == "RGB" else "L"), dtype=np.float64) / 255.0
    labels = imaging.rasterise_uv(uvf, arr.shape[0])["labels"]
    for lab, value in expected.items():
        texels = arr[labels == lab]
        assert texels.size > 0, f"{label}: island {lab} has no texels at {arr.shape[0]}"
        want = np.asarray(value, dtype=np.float64)
        dev = float(np.abs(texels - want).max())
        assert dev <= TOL + 1e-9, (
            f"{label}: island {lab} texels deviate up to {dev * 255:.1f}/255 from its own colour {value}; "
            f"border texels are blending in a neighbouring island or the background")


def _maps(tmp_path: Path, uvf: dict) -> tuple[dict[str, Path], np.ndarray]:
    labels = imaging.rasterise_uv(uvf, BAKE)["labels"]
    base = np.full((BAKE, BAKE, 3), BACKGROUND, dtype=np.float32)
    base[labels == 1] = COLOUR_A
    base[labels == 2] = COLOUR_B
    metal = np.full((BAKE, BAKE), BACKGROUND, dtype=np.float32)
    metal[labels == 1] = 1.0
    metal[labels == 2] = 0.0
    rough = np.full((BAKE, BAKE), BACKGROUND, dtype=np.float32)
    rough[labels == 1] = 0.2
    rough[labels == 2] = 0.9
    maps = {
        "base_color": imaging.write_map(base, tmp_path / "base_color_in.png"),
        "metallic": imaging.write_map(metal, tmp_path / "metallic_in.png"),
        "roughness": imaging.write_map(rough, tmp_path / "roughness_in.png"),
    }
    return maps, base


def test_two_touching_islands_keep_their_own_colour_when_shrunk_2x(tmp_path):
    uvf = _two_islands()
    maps, base = _maps(tmp_path, uvf)
    out_dir = tmp_path / "shipped"
    out_dir.mkdir()
    files: dict[str, Path] = {}

    prep.apply_look({"cap": SHIPPED, "normal_map": False}, uvf, maps, files, base, BAKE, out_dir)

    _assert_island_values(files["base_color"], uvf, {1: COLOUR_A, 2: COLOUR_B}, "base_color")
    _assert_island_values(files["roughness"], uvf, {1: 0.2, 2: 0.9}, "roughness")
    _assert_island_values(files["metallic"], uvf, {1: 1.0, 2: 0.0}, "metallic")


def test_a_plain_single_island_map_comes_out_the_same_colour(tmp_path):
    uv = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float64)
    uvf = {"uv": uv, "faces": np.array([[0, 1, 2], [0, 2, 3]]), "island": np.array([0, 0]), "up": np.zeros(2)}
    colour = (0.3, 0.6, 0.2)
    base = np.broadcast_to(np.asarray(colour, dtype=np.float32), (BAKE, BAKE, 3)).copy()
    maps = {"base_color": imaging.write_map(base, tmp_path / "base_in.png")}
    out_dir = tmp_path / "shipped"
    out_dir.mkdir()
    files: dict[str, Path] = {}

    prep.apply_look({"cap": SHIPPED, "normal_map": False}, uvf, maps, files, base, BAKE, out_dir)

    with Image.open(files["base_color"]) as im:
        assert im.size == (SHIPPED, SHIPPED)
        arr = np.asarray(im.convert("RGB"), dtype=np.float64) / 255.0
    assert float(np.abs(arr - np.asarray(colour)).max()) <= TOL + 1e-9, "a plain colour shifted when shrunk"


def test_without_a_layout_the_box_path_still_shrinks_to_the_target(tmp_path):
    uvf = _two_islands()
    maps, base = _maps(tmp_path, uvf)
    out_dir = tmp_path / "shipped"
    out_dir.mkdir()
    files: dict[str, Path] = {}

    m, mr_size = prep.apply_look({"cap": SHIPPED, "normal_map": False}, None, maps, files, base, BAKE, out_dir)

    assert m["texture_size_shipped"] == SHIPPED
    assert mr_size == SHIPPED
    assert imaging.image_size(files["base_color"]) == (SHIPPED, SHIPPED)
    assert imaging.image_size(files["metallic"]) == (SHIPPED, SHIPPED)
    assert imaging.image_size(files["roughness"]) == (SHIPPED, SHIPPED)


# --- a real GLB through texture_pass ------------------------------------------------------------------------------

def _two_island_glb(path: Path, base: Path, uvf: dict) -> Path:
    import trimesh
    from trimesh.visual.material import PBRMaterial
    from trimesh.visual.texture import TextureVisuals

    vertices = [[u, v, 0.0] for u, v in uvf["uv"]]
    material = PBRMaterial(baseColorTexture=Image.open(base), metallicFactor=0.0, roughnessFactor=1.0)
    mesh = trimesh.Trimesh(vertices=vertices, faces=uvf["faces"], visual=TextureVisuals(uv=uvf["uv"], material=material),
                           process=False)
    mesh.export(str(path))
    return path


def _embedded_image(glb: Path, texture_index: int) -> Image.Image:
    data = glb.read_bytes()
    jlen = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20:20 + jlen])
    bin_off = 20 + jlen
    bin_data = data[bin_off + 8: bin_off + 8 + struct.unpack_from("<I", data, bin_off)[0]]
    view = gltf["bufferViews"][gltf["images"][gltf["textures"][texture_index]["source"]]["bufferView"]]
    start = view.get("byteOffset", 0)
    im = Image.open(io.BytesIO(bin_data[start:start + view["byteLength"]]))
    im.load()
    return im


def test_texture_pass_ships_a_glb_at_the_look_cap_with_island_colours(tmp_path):
    uvf = _two_islands(gap=2 / BAKE)
    maps, base = _maps(tmp_path, uvf)
    glb = _two_island_glb(tmp_path / "model.glb", maps["base_color"], uvf)
    out_dir = tmp_path / "shipped"
    out_dir.mkdir()

    prep.texture_pass(glb, glb, maps, out_dir, BAKE, "keep", False, False, {"cap": SHIPPED, "normal_map": False}, False)

    material = json.loads(glb.read_bytes()[20:20 + struct.unpack_from("<I", glb.read_bytes(), 12)[0]])["materials"][0]
    base_index = material["pbrMetallicRoughness"]["baseColorTexture"]["index"]
    shipped = _embedded_image(glb, base_index).convert("RGB")
    assert shipped.size == (SHIPPED, SHIPPED), f"base colour ships at {shipped.size}, not the look cap {SHIPPED}"
    shipped_path = tmp_path / "embedded_base.png"
    shipped.save(shipped_path)
    _assert_island_values(shipped_path, uvf, {1: COLOUR_A, 2: COLOUR_B}, "embedded base_color")
