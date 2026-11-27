"""PL-153: a front sign is found as a panel, flattened where its lettering has no depth, and painted from the step 1 picture."""
from __future__ import annotations

import io
import json
import struct

import numpy as np
from PIL import Image

from vgac import face, glow, signface, srcproject

CREAM, INK, WALL = (0.95, 0.92, 0.84), (0.20, 0.55, 0.65), (0.30, 0.20, 0.15)


def _sign(ink=INK, cream=CREAM, shadow=None, w=800, h=400):
    """A wall holding a cream panel (x 100..700, y 120..280) with block letters; `shadow` adds an offset side face."""
    img = np.full((h, w, 3), WALL)
    img[120:280, 100:700] = cream
    letters = [(x, x + 40) for x in range(160, 640, 70)]
    for x0, x1 in letters:
        if shadow is not None:
            img[172:244, x0 + 12:x1 + 12] = shadow
        img[160:240, x0:x1] = ink
    mask = np.zeros((h, w), bool)
    mask[110:290, 90:710] = True
    return img, mask


def test_the_panel_is_found_and_flat_letters_read_as_flat():
    img, mask = _sign()
    (box,) = signface.boxes_of(mask)
    panel = signface.panel_in(img, box)
    x0, y0, x1, y1 = panel["rect"]
    assert max(abs(x0 - 100), abs(y0 - 120), abs(x1 - 700), abs(y1 - 280)) <= 2
    got = signface.lettering(img, panel)
    assert signface.LETTER_SHARE[0] < got["share"] < signface.LETTER_SHARE[1]
    assert got["depth"] <= signface.DEPTH_SHARE


def test_letters_with_a_side_face_read_as_having_depth():
    img, mask = _sign(shadow=(0.35, 0.28, 0.30))
    panel = signface.panel_in(img, signface.boxes_of(mask)[0])
    assert signface.lettering(img, panel)["depth"] > signface.DEPTH_SHARE


def _write_glb(path, pos, tris, uv, tex):
    """A one-material GLB: positions, normals, UVs, indices and a base colour picture."""
    png = io.BytesIO()
    Image.fromarray((tex * 255).astype(np.uint8)).save(png, "PNG")
    nrm = np.tile([0.0, 0.0, 1.0], (len(pos), 1))
    chunks = [pos.astype("<f4").tobytes(), nrm.astype("<f4").tobytes(), uv.astype("<f4").tobytes(),
              tris.astype("<u4").tobytes(), png.getvalue()]
    views, blob = [], b""
    for c in chunks:
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(c)})
        blob += c + b"\0" * (-len(c) % 4)
    n = len(pos)
    gltf = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2}, "indices": 3, "material": 0}]}],
            "buffers": [{"byteLength": len(blob)}], "bufferViews": views,
            "accessors": [{"bufferView": 0, "componentType": 5126, "count": n, "type": "VEC3", "min": pos.min(0).tolist(), "max": pos.max(0).tolist()},
                          {"bufferView": 1, "componentType": 5126, "count": n, "type": "VEC3"},
                          {"bufferView": 2, "componentType": 5126, "count": n, "type": "VEC2"},
                          {"bufferView": 3, "componentType": 5125, "count": tris.size, "type": "SCALAR"}],
            "images": [{"bufferView": 4, "mimeType": "image/png"}], "textures": [{"source": 0}],
            "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}]}
    js = json.dumps(gltf).encode()
    js += b" " * (-len(js) % 4)
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 28 + len(js) + len(blob)) + struct.pack("<I4s", len(js), b"JSON") + js
                     + struct.pack("<I4s", len(blob), b"BIN\0") + blob)


def _board(path):
    """A 4 x 2 front face, a grid of vertices, with a low ridge across the middle of its inner rectangle."""
    xs, ys = np.linspace(-2, 2, 33), np.linspace(-1, 1, 17)
    gx, gy = np.meshgrid(xs, ys)
    z = np.where((np.abs(gx) < 1.2) & (np.abs(gy) < 0.3), 0.02, 0.0)
    pos = np.stack([gx, gy, z], axis=-1).reshape(-1, 3)
    uv = np.stack([(gx + 2) / 4, 1 - (gy + 1) / 2], axis=-1).reshape(-1, 2)   # glTF UVs: v down
    tris = []
    for j in range(16):
        for i in range(32):
            a = j * 33 + i
            tris += [[a, a + 1, a + 34], [a, a + 34, a + 33]]
    _write_glb(path, pos, np.array(tris), uv, np.full((64, 64, 3), 0.5))


def _model_front(mesh, ink):
    """The board seen square-on, as the finder and the panel search see it: a cream panel with a smear where the words are."""
    cam = signface.front_camera(mesh["verts"])
    s = cam.size / cam.scale
    img = np.full((cam.size, cam.size, 3), WALL)

    def rows(y0, y1):
        return slice(int(cam.size / 2 - (y1 - cam.target[1]) * s), int(cam.size / 2 - (y0 - cam.target[1]) * s))

    def cols(x0, x1):
        return slice(int(cam.size / 2 + (x0 - cam.target[0]) * s), int(cam.size / 2 + (x1 - cam.target[0]) * s))

    img[rows(-0.5, 0.5), cols(-1.875, 1.875)] = CREAM
    img[rows(-0.2, 0.2), cols(-1.2, 1.2)] = ink
    mask = np.zeros((cam.size, cam.size), bool)
    mask[rows(-0.55, 0.55), cols(-1.9, 1.9)] = True
    return img, mask


def test_a_flat_sign_is_flattened_and_painted_from_the_picture(tmp_path):
    glb = tmp_path / "m.glb"
    _board(glb)
    picture, picture_mask = _sign()
    front, front_mask = _model_front(face.load_mesh(glb), (0.45, 0.65, 0.75))
    base = np.full((256, 256, 3), 0.5)
    tris, rec = signface.apply(glb, base, picture, picture_mask, front_mask, front)
    assert rec["applied"] and rec["flattened"] and rec["vertices_moved"] > 0 and len(tris) > 0
    mesh = face.load_mesh(glb)
    assert np.abs(mesh["verts"][:, 2]).max() < 1e-6                         # the ridge is gone
    # the middle of the panel holds the picture's colours: both letters and plain panel
    view = face.render_view(mesh, base, signface.front_camera(mesh["verts"]), background=1.0)["image"]
    row = view[view.shape[0] // 2]
    assert (np.abs(row - INK).max(1) < 0.08).sum() > 100 and (np.abs(row - CREAM).max(1) < 0.08).sum() > 100


def test_a_sign_with_depth_keeps_its_shape(tmp_path):
    glb = tmp_path / "m.glb"
    _board(glb)
    before = glb.read_bytes()
    picture, picture_mask = _sign(shadow=(0.35, 0.28, 0.30))
    front, front_mask = _model_front(face.load_mesh(glb), (0.45, 0.65, 0.75))
    tris, rec = signface.apply(glb, np.full((256, 256, 3), 0.5), picture, picture_mask, front_mask, front)
    assert rec["applied"] and not rec["flattened"] and glb.read_bytes() == before


def test_a_picture_of_another_shape_changes_nothing(tmp_path):
    glb = tmp_path / "m.glb"
    _board(glb)
    before = glb.read_bytes()
    picture, picture_mask = _sign()
    picture, picture_mask = picture[:, :500], picture_mask[:, :500]   # a much narrower panel than the model's
    picture[:, 440:] = WALL
    front, front_mask = _model_front(face.load_mesh(glb), INK)
    base = np.full((256, 256, 3), 0.5)
    tris, rec = signface.apply(glb, base, picture, picture_mask, front_mask, front)
    assert tris is None and not rec["applied"] and glb.read_bytes() == before and (base == 0.5).all()


def test_the_flattened_letters_are_not_dark_holes_in_the_glow_map(tmp_path):
    glb = tmp_path / "m.glb"
    _board(glb)
    before = face.load_mesh(glb)
    # a view fitted to the model before the sign was flattened, as the picture paint keeps it
    size = 512
    cam = face.Camera(target=np.zeros(3), yaw_deg=0.0, scale=4.4, size=size)
    view = srcproject.View("picture", 1.0)
    view.cam, view.raster, view.size = cam, srcproject.Raster(before, cam), size
    view.feather = view.feather0 = np.ones((size, size))
    view.gate_ag = view.gate_fs = np.ones((size, size), dtype=np.float32)
    view.pic_px_m, view.render, view.valid = cam.scale / size, np.full((size, size, 3), 0.8), view.raster.mask
    view.pic_aligned = view.pic_affine = view.render
    view.alpha_img = view.raster.mask.astype(float)
    picture = np.full((size, size, 3), 0.8)
    assert signface.move_to_plane(glb, (-1.8, -0.5, 1.8, 0.5), 0.0, 0.1) > 0
    kept = {"views": [view], "images": [picture], "n_primary": 1}
    path, rec = glow.write_emission(glb, kept, 128, tmp_path / "e.png", gains=[np.ones((size, size))])
    assert path is not None, rec
    em = np.asarray(Image.open(path).convert("RGB")) / 255.0
    inner = em[56:72, 30:98]   # the texels where the relief was (the middle of the board)
    assert (inner.max(axis=-1) > 0.5).mean() > 0.99


def test_no_picture_paint_means_no_picture(tmp_path):
    assert signface.read_picture(tmp_path) is None
