"""Beauty picture for the finish card (PHOTOREAL-PLAN phase 1, "Preview in realistic light").

The export step renders the finished model once in Blender Eevee (blender/beauty.py) under the same CC0 light picture
the app viewer calls "Outdoors", and registers it as the version file `beauty` (beauty.png, plus beauty.json with
the timing). It only shows the asset; nothing in the game files changes, so a failure is noted in the metrics
(beauty_error) and never fails the export.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np

from .config import PROJECT_DIR

# The light pictures are shared with the app viewer and the Unity showcase (LICENCES.txt there).
HDRI_DIR = PROJECT_DIR / "frontend" / "public" / "hdri"
OUTDOORS_HDRI = HDRI_DIR / "kloofendal_48d_partly_cloudy_1k.hdr"
# Where the sun sits across that picture (0 to 1, left to right), measured once from its brightest pixel.
OUTDOORS_SUN_U = 0.6
BEAUTY_SIZE = 1024
BEAUTY_TIMEOUT_S = 90  # a render takes 2 s (about 15 s with Blender starting); the one stored hang held the export 5 minutes
# The Unity showcase gets the same picture with the sun taken out (its own Sun light stands in): Unity blurs a sky
# for rough surfaces with a plain average, and the sun (tens of thousands of times brighter than the sky in a few
# pixels) would otherwise turn into a bright grey sheen on cloth and leather.
SHOWCASE_SKY = PROJECT_DIR / "runtime" / "cache" / "showcase_sky_1k.hdr"
SKY_CLIP = 16.0


def read_hdr(path: Path) -> np.ndarray:
    """A Radiance .hdr (RGBE, flat or run-length scanlines) as float32 linear RGB, shape (h, w, 3)."""
    data = Path(path).read_bytes()
    pos = 0
    while True:
        end = data.index(b"\n", pos)
        line = data[pos:end]
        pos = end + 1
        if line.startswith((b"-Y", b"+Y")):
            parts = line.split()
            h, w = int(parts[1]), int(parts[3])
            break
    img = np.zeros((h, w, 4), np.uint8)
    for y in range(h):
        if 8 <= w < 0x8000 and data[pos] == 2 and data[pos + 1] == 2:
            pos += 4
            for c in range(4):
                x = 0
                while x < w:
                    n = data[pos]
                    pos += 1
                    if n > 128:
                        n -= 128
                        img[y, x:x + n, c] = data[pos]
                        pos += 1
                    else:
                        img[y, x:x + n, c] = np.frombuffer(data, np.uint8, n, pos)
                        pos += n
                    x += n
        else:
            img[y] = np.frombuffer(data, np.uint8, w * 4, pos).reshape(w, 4)
            pos += w * 4
    e = img[..., 3].astype(np.int32)
    scale = np.where(e > 0, np.ldexp(1.0, e - 136), 0.0).astype(np.float32)
    return img[..., :3].astype(np.float32) * scale[..., None]


def write_hdr(path: Path, rgb: np.ndarray) -> None:
    """Write float linear RGB as a Radiance .hdr with flat (uncompressed) scanlines."""
    h, w, _ = rgb.shape
    v = rgb.max(axis=2)
    m, e = np.frexp(v)
    scale = np.where(v > 1e-32, m * 256.0 / np.maximum(v, 1e-32), 0.0)
    out = np.zeros((h, w, 4), np.uint8)
    out[..., :3] = np.clip(rgb * scale[..., None], 0, 255).astype(np.uint8)
    out[..., 3] = np.where(v > 1e-32, e + 128, 0).astype(np.uint8)
    head = f"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y {h} +X {w}\n".encode()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".part")
    tmp.write_bytes(head + out.tobytes())
    os.replace(tmp, path)


def showcase_sky(src: Path = OUTDOORS_HDRI, dest: Path = SHOWCASE_SKY, clip: float = SKY_CLIP) -> Path | None:
    """The Outdoors picture with the sun clipped to `clip`, made once; None when the picture is missing."""
    if not Path(src).is_file():
        return None
    if not Path(dest).is_file() or Path(dest).stat().st_mtime < Path(src).stat().st_mtime:
        write_hdr(dest, np.minimum(read_hdr(src), clip))
    return Path(dest)


def beauty_args(glb: Path, out: Path, hdri: Path = OUTDOORS_HDRI, size: int = BEAUTY_SIZE) -> list[Any]:
    """The blender/beauty.py command-line arguments."""
    return [glb, out, hdri, "--size", size, "--sun-u", OUTDOORS_SUN_U]


def read_beauty_info(out: Path) -> dict[str, Any] | None:
    """beauty.json written next to the picture by the Blender script, or None."""
    p = out.with_suffix(".json")
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


async def render_beauty(sc: Any) -> Path | None:
    """Render and register the beauty picture for this export version. Returns its path, or None."""
    glb = sc.file("glb")
    if glb is None:
        return None
    if not OUTDOORS_HDRI.is_file():
        sc.set_metrics(beauty_error=f"light picture missing: {OUTDOORS_HDRI.name}")
        return None
    out = sc.dir / "beauty.png"
    sc.progress(0.9, "beauty picture")
    try:
        await sc.blender("beauty.py", beauty_args(Path(glb), out), log_name="beauty.log", timeout=BEAUTY_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001  (a picture for people; the game files are already done)
        sc.set_metrics(beauty_error=str(e)[-400:],
                       beauty_note="The picture in realistic light was skipped because it took too long to draw. The model and its game files are complete.")
        return None
    if not out.is_file():
        sc.set_metrics(beauty_error="Blender wrote no picture")
        return None
    sc.register("beauty", out)
    info = read_beauty_info(out) or {}
    sc.set_metrics(beauty={k: info[k] for k in ("seconds", "size", "view_transform", "hdri") if k in info})
    return out
