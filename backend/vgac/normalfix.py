"""The 3D step's normal map (bump texture) with its green channel the right way up (docs/KNOWN-FAULTS-RESEARCH.md 1).

TRELLIS.2's BakeNormalMapFromMesh writes the green channel the DirectX way (green meaning "down the texture"); glTF,
Blender, Unity, Godot and the app's own viewer read it the OpenGL way (green meaning "up the texture"). Read the wrong
way, the half of the map that cancels the lower-detail model's facets doubles them instead, so every triangle catches
the light on its own: triangle-shaped facets and dark patches on necks, faces and armour edges.

- stages/mesh.py flips green once, straight after the download, in normal.png and in the image the GLB's materials use
  as their normal texture, and records metrics normal_fix = NORMAL_FIX on the version (the marker).
- stages/prep.py: a 3D-step version from before the fix (TRELLIS, a normal map, no marker) gets the flip on copies in
  the clean-up step's own folder before its re-bake (repair_inputs); the stored files are never touched, and a
  version with the marker is never flipped again.
- upright(): the check. A point on a texture seam is read twice, once from each side's piece of the texture. Both
  pieces were baked from the same detailed surface, so read the right way the two mapped normals agree; read upside
  down each is mirrored about its own piece's up direction, and the pieces lie at different angles in the texture, so
  they disagree. The measure is the median angle between the two sides over the seams, for the map as it is and with
  green flipped. When the flipped map agrees clearly better (ratio over UPSIDE_DOWN_RATIO), the map is upside down.

The tileable material's normal map (graphs/material.derive_maps) is OpenGL already; Hunyuan3D builds no texture.
"""

from __future__ import annotations

import io
import json
import shutil
import struct
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

# The marker on a 3D-step version whose normal map was flipped at download. Raise it if the flip ever changes.
NORMAL_FIX = 1
# The check: the map is upside down when the two sides of its seams disagree this many times more than they do with
# green flipped. Calibrated on five characters and two props (runtime/tmp/normalfix/calibrate_mesh.json): as the 3D step
# built them 1.54 to 2.49, flipped 0.40 to 0.65.
UPSIDE_DOWN_RATIO = 1.25
# The check's texture size: the measure is about the shape of the map, not its fine detail.
MEASURE_SIZE = 1024


def flip_green_array(rgb: np.ndarray) -> np.ndarray:
    """uint8 (H, W, >=3): green inverted (255 - g), every other channel kept."""
    out = np.array(rgb, copy=True)
    out[..., 1] = 255 - out[..., 1]
    return out


def _flip_image_bytes(data: bytes) -> tuple[bytes, str]:
    """PNG (or JPEG) bytes -> PNG bytes with green inverted, and the mime type."""
    with Image.open(io.BytesIO(data)) as im:
        mode = "RGBA" if "A" in im.getbands() or (im.mode == "P" and "transparency" in im.info) else "RGB"
        arr = np.asarray(im.convert(mode))
    buf = io.BytesIO()
    Image.fromarray(flip_green_array(arr), mode).save(buf, "PNG")
    return buf.getvalue(), "image/png"


def flip_green_png(src: Path | str, dest: Path | str | None = None) -> Path:
    """Invert the green channel of a normal map PNG; in place without dest."""
    out = Path(dest) if dest is not None else Path(src)
    data, _ = _flip_image_bytes(Path(src).read_bytes())
    out.write_bytes(data)
    return out


def _read_glb(p: Path) -> tuple[dict[str, Any], bytes] | None:
    data = p.read_bytes()
    if data[:4] != b"glTF":
        return None
    jlen = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20:20 + jlen])
    bin_off = 20 + jlen
    if len(data) < bin_off + 8:
        return gltf, b""
    blen = struct.unpack_from("<I", data, bin_off)[0]
    return gltf, data[bin_off + 8: bin_off + 8 + blen]


def _write_glb(p: Path, gltf: dict[str, Any], views: list[dict[str, Any]], bin_data: bytes, replace: dict[int, bytes]) -> None:
    new_bin = bytearray()
    for vi, bv in enumerate(views):
        if bv.get("buffer", 0) != 0:
            continue
        o = bv.get("byteOffset", 0)
        chunk = replace.get(vi, bin_data[o:o + bv["byteLength"]])
        while len(new_bin) % 4:
            new_bin.append(0)
        bv["byteOffset"] = len(new_bin)
        bv["byteLength"] = len(chunk)
        new_bin += chunk
    while len(new_bin) % 4:
        new_bin.append(0)
    gltf["buffers"][0]["byteLength"] = len(new_bin)
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(new_bin)
    p.write_bytes(struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I", len(js)) + b"JSON" + js
                  + struct.pack("<I", len(new_bin)) + b"BIN\x00" + bytes(new_bin))


def glb_normal_images(glb: Path | str) -> list[int]:
    """The image indices (embedded in the binary chunk) that some material uses as its normal texture."""
    got = _read_glb(Path(glb))
    if got is None:
        return []
    gltf, _ = got
    textures, images = gltf.get("textures") or [], gltf.get("images") or []
    out: list[int] = []
    for m in gltf.get("materials") or []:
        ref = m.get("normalTexture")
        if not isinstance(ref, dict) or "index" not in ref or ref["index"] >= len(textures):
            continue
        src = textures[ref["index"]].get("source")
        if src is not None and src < len(images) and "bufferView" in images[src] and src not in out:
            out.append(src)
    return out


def glb_flip_normal_green(glb: Path | str, dest: Path | str | None = None) -> int:
    """Invert green in every image a material of the GLB uses as its normal texture (each image once, even when
    several materials share it); in place without dest. -> the number of images flipped (0 leaves the file alone,
    or copies it unchanged to dest)."""
    p = Path(glb)
    out = Path(dest) if dest is not None else p
    got = _read_glb(p)
    idx = glb_normal_images(p) if got is not None else []
    if not idx:
        if out != p:
            shutil.copy2(p, out)
        return 0
    gltf, bin_data = got
    images, views = gltf["images"], gltf.get("bufferViews") or []
    replace: dict[int, bytes] = {}
    for i in idx:
        vi = images[i]["bufferView"]
        if vi in replace:
            continue
        bv = views[vi]
        o = bv.get("byteOffset", 0)
        replace[vi], images[i]["mimeType"] = _flip_image_bytes(bin_data[o:o + bv["byteLength"]])
    _write_glb(out, gltf, views, bin_data, replace)
    return len(idx)


# --- the marker --------------------------------------------------------------------------------------------------


def needs_fix(params: dict[str, Any] | None, metrics: dict[str, Any] | None, has_normal: bool) -> bool:
    """Whether a 3D-step version's normal map is still upside down: built by TRELLIS (the default builder), with a
    normal map, and without the marker (metrics normal_fix at NORMAL_FIX or more)."""
    p, m = params or {}, metrics or {}
    if not has_normal:
        return False
    if (p.get("engine") or m.get("engine") or "trellis") != "trellis":
        return False
    return int(m.get("normal_fix") or 0) < NORMAL_FIX


def fix_download(glb: Path | str | None, normal_png: Path | str | None) -> dict[str, Any]:
    """The 3D step, right after its download: green flipped in normal.png and in the GLB's normal image. -> the
    metrics to record (normal_fix, normal_fix_glb_images)."""
    images = 0
    if glb is not None and Path(glb).is_file():
        images = glb_flip_normal_green(glb)
    if normal_png is not None and Path(normal_png).is_file():
        flip_green_png(normal_png)
    return {"normal_fix": NORMAL_FIX, "normal_fix_glb_images": images}


def repair_inputs(glb: Path, normal_png: Path | None, out_dir: Path) -> dict[str, Any]:
    """The clean-up step on a 3D-step version from before the fix: copies of its GLB and normal map with green flipped
    in out_dir (the stored files are never changed). -> {glb, normal (or None), glb_images}."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    g = out_dir / "model.glb"
    images = glb_flip_normal_green(glb, g)
    n = None
    if normal_png is not None and Path(normal_png).is_file():
        n = flip_green_png(normal_png, out_dir / "normal.png")
    return {"glb": g, "normal": n, "glb_images": images}


# --- the check ---------------------------------------------------------------------------------------------------


def seam_edges(mesh: dict[str, np.ndarray]) -> tuple[np.ndarray, ...]:
    """The model's texture seams: edges shared by exactly two triangles (vertices joined by position) whose two sides
    lie in different places of the texture. -> (tri1, tri2, a1, b1, a2, b2): the two triangles and each side's own
    vertex indices of the edge, a1 and a2 at the same position."""
    v, f, uv = mesh["verts"], mesh["faces"], mesh["uv"]
    _, wid = np.unique(np.round(v * 1e5).astype(np.int64), axis=0, return_inverse=True)
    wid = wid.reshape(-1)
    ea = np.concatenate([f[:, 0], f[:, 1], f[:, 2]])
    eb = np.concatenate([f[:, 1], f[:, 2], f[:, 0]])
    et = np.concatenate([np.arange(len(f))] * 3)
    lo, hi = np.minimum(wid[ea], wid[eb]), np.maximum(wid[ea], wid[eb])
    key = lo.astype(np.int64) * (int(wid.max()) + 1) + hi
    o = np.argsort(key, kind="stable")
    ks = key[o]
    first = np.nonzero(ks[:-1] == ks[1:])[0]
    last = len(ks) - 1
    two = ((first + 2 > last) | (ks[np.minimum(first + 2, last)] != ks[first])) & ((first == 0) | (ks[np.maximum(first - 1, 0)] != ks[first]))
    first = first[two]
    i1, i2 = o[first], o[first + 1]
    a1, b1, a2, b2 = ea[i1], eb[i1], ea[i2], eb[i2]
    swap = wid[a1] != wid[a2]
    a2, b2 = np.where(swap, b2, a2), np.where(swap, a2, b2)
    apart = np.maximum(np.abs(uv[a1] - uv[a2]).max(1), np.abs(uv[b1] - uv[b2]).max(1)) > 1e-4
    return et[i1][apart], et[i2][apart], a1[apart], b1[apart], a2[apart], b2[apart]


def upright(glb: Path | str, normal_png: Path | str, size: int = MEASURE_SIZE) -> dict[str, Any] | None:
    """How well the normal map agrees with itself across the texture seams, as it is and with green flipped.

    A point on a seam is read twice, once from each side's piece of the texture. Both pieces were baked from the same
    detailed surface, so read the right way the two mapped normals agree; read with green the wrong way each one is
    mirrored about its own piece's up direction, and the pieces lie at different angles in the texture, so they
    disagree. -> {seam_deg (median angle between the two sides, as is), seam_deg_flipped, ratio (as is over flipped),
    upside_down, seams} or None when the model has no texture layout or too few seams."""
    from . import face, hair  # noqa: PLC0415  (the mesh loader, the sampler and the tangent directions)

    mesh = face.load_mesh(glb)
    if mesh is None:
        return None
    t1, t2, a1, b1, a2, b2 = seam_edges(mesh)
    if len(t1) < 20:
        return None
    with Image.open(normal_png) as im:
        nm = np.asarray(im.convert("RGB").resize((size, size), Image.BOX), dtype=np.float64) / 255.0
    f, vn = mesh["faces"], mesh["vnormals"]
    tu, tv = hair.uv_jacobian(mesh, size)
    uvp = face.uv_pixels(mesh["uv"], size)
    angles: dict[float, list[np.ndarray]] = {1.0: [], -1.0: []}
    for s in (0.25, 0.5, 0.75):
        sides = []
        for t, a, b in ((t1, a1, b1), (t2, a2, b2)):
            p = uvp[a] * (1 - s) + uvp[b] * s
            # two texels into the triangle (at most a third of the way to its middle), clear of the texture's gutter
            d = uvp[f[t]].mean(1) - p
            ln = np.linalg.norm(d, axis=1, keepdims=True)
            p = p + d / np.maximum(ln, 1e-9) * np.minimum(2.0, ln / 3.0)
            n = vn[a] * (1 - s) + vn[b] * s
            n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
            ts = face.sample(nm, p[:, 0], p[:, 1]) * 2.0 - 1.0
            ts /= np.maximum(np.linalg.norm(ts, axis=1, keepdims=True), 1e-9)
            ax = tu[t] - (tu[t] * n).sum(1, keepdims=True) * n
            ax /= np.maximum(np.linalg.norm(ax, axis=1, keepdims=True), 1e-12)
            up = tv[t] - (tv[t] * n).sum(1, keepdims=True) * n - (tv[t] * ax).sum(1, keepdims=True) * ax
            up /= np.maximum(np.linalg.norm(up, axis=1, keepdims=True), 1e-12)
            sides.append((n, ts, ax, up))
        for sign in angles:
            w = []
            for n, ts, ax, up in sides:
                x = ax * ts[:, :1] + sign * up * ts[:, 1:2] + n * ts[:, 2:3]
                w.append(x / np.maximum(np.linalg.norm(x, axis=1, keepdims=True), 1e-12))
            angles[sign].append(np.degrees(np.arccos(np.clip((w[0] * w[1]).sum(1), -1.0, 1.0))))
    as_is = float(np.median(np.concatenate(angles[1.0])))
    flipped = float(np.median(np.concatenate(angles[-1.0])))
    ratio = as_is / max(flipped, 1e-6)
    return {"seam_deg": round(as_is, 2), "seam_deg_flipped": round(flipped, 2), "ratio": round(ratio, 3),
            "upside_down": bool(ratio > UPSIDE_DOWN_RATIO), "seams": int(len(t1))}


def check(measure: dict[str, Any] | None, stage: str) -> dict[str, Any] | None:
    """The normal_map_upright check (warn) from upright()'s measure, for the 3D step ("mesh") or the clean-up step
    ("prep"); None without a measure."""
    from . import quality  # noqa: PLC0415

    if not measure:
        return None
    return quality.check("normal_map_upright", not measure.get("upside_down"), measure.get("ratio"), UPSIDE_DOWN_RATIO,
                         vars={"stage": stage})
