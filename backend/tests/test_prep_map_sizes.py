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


def _decoded_lengths(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        rgb = np.asarray(im.convert("RGB"), dtype=np.float64)
    v = rgb / 255.0 * 2.0 - 1.0
    return np.linalg.norm(v, axis=-1)


def _minimal_glb(path: Path, base: Path, normal: Path) -> Path:
    """A GLB with one material: a base colour image and a normal image, no mesh. glb_replace_images reads only these."""
    blobs = [base.read_bytes(), normal.read_bytes()]
    offsets, bin_data = [], bytearray()
    for b in blobs:
        while len(bin_data) % 4:
            bin_data.append(0)
        offsets.append(len(bin_data))
        bin_data += b
    while len(bin_data) % 4:
        bin_data.append(0)
    gltf = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(bin_data)}],
        "bufferViews": [{"buffer": 0, "byteOffset": offsets[i], "byteLength": len(blobs[i])} for i in range(2)],
        "images": [{"bufferView": 0, "mimeType": "image/png"}, {"bufferView": 1, "mimeType": "image/png"}],
        "textures": [{"source": 0}, {"source": 1}],
        "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}, "normalTexture": {"index": 1}}],
        "meshes": [],
    }
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(bin_data)
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I", len(js)) + b"JSON" + js
                     + struct.pack("<I", len(bin_data)) + b"BIN\x00" + bytes(bin_data))
    return path


def _embedded_normal_size(glb: Path) -> int:
    data = glb.read_bytes()
    jlen = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20:20 + jlen])
    bin_off = 20 + jlen
    bin_data = data[bin_off + 8: bin_off + 8 + struct.unpack_from("<I", data, bin_off)[0]]
    view = gltf["bufferViews"][gltf["images"][gltf["textures"][gltf["materials"][0]["normalTexture"]["index"]]["source"]]["bufferView"]]
    start = view.get("byteOffset", 0)
    with Image.open(io.BytesIO(bin_data[start:start + view["byteLength"]])) as im:
        return im.size[0]


def test_a_4096_normal_beside_a_2048_base_colour_ships_at_2048(tmp_path):
    base = _png(tmp_path / "base.png", 2048, (150, 110, 80))
    normal = _unit_normals(tmp_path / "normal.png", 4096)
    glb = _minimal_glb(tmp_path / "model.glb", base, normal)
    out_dir = tmp_path / "shipped"
    out_dir.mkdir()

    maps = {"base_color": base, "normal": normal}
    files: dict[str, Path] = {}
    new_base = imaging.read_map(base)
    look = {"cap": 2048, "normal_map": True}
    _, mr_size = prep.apply_look(look, None, maps, files, new_base, 2048, out_dir)

    assert imaging.image_size(files["normal"])[0] == 2048
    assert imaging.glb_replace_images(glb, base_color=files.get("base_color"), normal=files.get("normal"), mr_size=mr_size)
    assert _embedded_normal_size(glb) == 2048


def test_a_shrunk_normal_map_keeps_unit_length_normals(tmp_path):
    src = _unit_normals(tmp_path / "normal.png", 256)
    out = imaging.shrink_normal(src, 128, tmp_path / "small.png")

    assert imaging.image_size(out)[0] == 128
    lengths = _decoded_lengths(out)
    assert abs(lengths.mean() - 1.0) < 0.01
    assert np.percentile(np.abs(lengths - 1.0), 99) < 0.01
