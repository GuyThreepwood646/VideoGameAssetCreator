"""Every shipped map is the base colour's size: a 4096 normal map beside a 2048 base colour ships at 2048.

WP-50 trial C (pc_mid, photo subject, 4096 bake): the base colour shipped at 2048 but the normal map stayed at 4096 in the
GLB (15.0 MB against 5.0 MB at 2048). The texture pass shrank the normal file but never wrote it into the GLB.
"""

import io
import json
import struct
from pathlib import Path

import numpy as np
from PIL import Image

from vgac import imaging
from vgac.stages import prep


def _png(path: Path, size: int, colour=(128, 128, 128)) -> Path:
    Image.new("RGB", (size, size), colour).save(path)
    return path


def _unit_normals(path: Path, size: int, seed: int = 3) -> Path:
    """A random tangent-space normal map: every vector unit length, encoded the usual way."""
    rng = np.random.default_rng(seed)
    v = rng.normal(size=(size, size, 3)).astype(np.float32)
    v /= np.linalg.norm(v, axis=-1, keepdims=True)
    enc = np.clip(np.round((v * 0.5 + 0.5) * 255), 0, 255).astype(np.uint8)
    Image.fromarray(enc, "RGB").save(path)
    return path


def _decoded(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"), dtype=np.float64) / 255.0 * 2.0 - 1.0


def _quad_glb(path: Path, base: Path, normal: Path) -> Path:
    """A one-quad mesh with a UV layout covering the texture, a base colour and a normal map, written as a GLB by
    trimesh (the same loader the clean-up reads the layout with)."""
    import trimesh
    from trimesh.visual.material import PBRMaterial
    from trimesh.visual.texture import TextureVisuals

    vertices = [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]]
    faces = [[0, 1, 2], [0, 2, 3]]
    uv = [[0, 0], [1, 0], [1, 1], [0, 1]]
    material = PBRMaterial(baseColorTexture=Image.open(base), normalTexture=Image.open(normal),
                           metallicFactor=0.0, roughnessFactor=1.0)
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, visual=TextureVisuals(uv=uv, material=material), process=False)
    mesh.export(str(path))
    return path


def _embedded_normal_size(glb: Path) -> int:
    """The width of the normal image the GLB itself carries (what the download pays for)."""
    data = glb.read_bytes()
    jlen = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20:20 + jlen])
    bin_off = 20 + jlen
    bin_data = data[bin_off + 8: bin_off + 8 + struct.unpack_from("<I", data, bin_off)[0]]
    material = gltf["materials"][0]
    texture = gltf["textures"][material["normalTexture"]["index"]]
    view = gltf["bufferViews"][gltf["images"][texture["source"]]["bufferView"]]
    start = view.get("byteOffset", 0)
    with Image.open(io.BytesIO(bin_data[start:start + view["byteLength"]])) as im:
        return im.size[0]


def test_a_4096_normal_beside_a_2048_base_colour_ships_at_2048_in_the_glb(tmp_path):
    base = _png(tmp_path / "base.png", 2048, (150, 110, 80))
    normal = _unit_normals(tmp_path / "normal.png", 4096)
    glb = _quad_glb(tmp_path / "model.glb", base, normal)
    assert _embedded_normal_size(glb) == 4096  # the input really carries the 4096 normal
    out_dir = tmp_path / "shipped"
    out_dir.mkdir()

    prep.texture_pass(glb, glb, {"base_color": base, "normal": normal}, out_dir, 2048, "keep", False, False,
                      {"cap": 2048, "normal_map": True}, False)

    assert _embedded_normal_size(glb) == 2048


def test_a_shrunk_normal_map_keeps_unit_length_normals(tmp_path):
    src = _unit_normals(tmp_path / "normal.png", 256)
    out = imaging.shrink_normal(src, 128, tmp_path / "small.png")

    assert imaging.image_size(out)[0] == 128
    lengths = np.linalg.norm(_decoded(out), axis=-1)
    assert abs(lengths.mean() - 1.0) < 0.01
    assert np.percentile(np.abs(lengths - 1.0), 99) < 0.01


def test_two_islands_with_opposite_normals_keep_their_own_vectors_at_the_border(tmp_path):
    """Island A (left) leans toward +x, island B (right) toward -x, and they touch along u = 0.5. A plain box filter
    averages the two at the border; the island shrink must keep every texel within 10 degrees of its own island."""
    uvmesh = {
        "uv": np.array([[0, 0], [0.5, 0], [0.5, 1], [0, 1], [0.5, 0], [1, 0], [1, 1], [0.5, 1]], dtype=np.float64),
        "faces": np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7]]),
        "island": np.array([0, 0, 1, 1]),
        "up": np.zeros(4),
    }
    lean = {1: np.array([0.6, 0.0, 0.8]), 2: np.array([-0.6, 0.0, 0.8])}  # island label (island + 1) -> its vector
    big = 256
    labels = imaging.rasterise_uv(uvmesh, big)["labels"]
    enc = np.full((big, big, 3), 0.0)
    enc[:] = (np.array([0.0, 0.0, 1.0]) * 0.5 + 0.5)
    for lab, vec in lean.items():
        enc[labels == lab] = vec * 0.5 + 0.5
    src = tmp_path / "normal.png"
    Image.fromarray(np.round(enc * 255).astype(np.uint8), "RGB").save(src)

    out = imaging.shrink_normal_islands(src, uvmesh, 64, tmp_path / "small.png")

    small_labels = imaging.rasterise_uv(uvmesh, 64)["labels"]
    vec = _decoded(out)
    for lab, own in lean.items():
        texels = vec[small_labels == lab]
        assert len(texels) > 0
        cos = np.clip(texels @ own / np.linalg.norm(texels, axis=-1), -1.0, 1.0)
        assert np.degrees(np.arccos(cos)).max() < 10.0
