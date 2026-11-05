"""Safe image intake for "start from your own images".

Pictures arrive as base64 text inside JSON bodies (optionally a data: URL), so the app needs no multipart parser.
Every picture goes through one path:

1. decode the base64 strictly, at most MAX_BYTES (25 MB) decoded;
2. open it with Pillow and sniff the real format from the bytes: PNG, JPEG and WebP only (the file name and the
   data: URL type are never trusted);
3. refuse more than MAX_PIXELS before decoding the pixels, and treat Pillow's decompression-bomb warning as an error;
4. turn it the right way up from its EXIF orientation, then drop every piece of metadata (EXIF with GPS, ICC
   profiles, text chunks) by copying the pixels into a fresh image;
5. convert to RGB, or RGBA when it really has see-through pixels, and shrink so the longest side is at most
   MAX_SIDE (2048);
6. write it as PNG only into the given version folder, under a fixed name.

Errors are UploadError (a ValueError) with a plain sentence the API answers with as a 400.
"""

from __future__ import annotations

import base64
import binascii
import io
import re
import warnings
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_BYTES = 25 * 1024 * 1024
# base64 grows the data by a third; a data: URL prefix and line breaks add a little more.
MAX_B64_CHARS = MAX_BYTES * 4 // 3 + 1024
MAX_PIXELS = 50_000_000  # about 8000 x 6000; a phone photo is 12 to 50 million
MIN_SIDE = 32
MAX_SIDE = 2048
FORMATS = {"PNG": "PNG", "JPEG": "JPEG", "WEBP": "WebP", "MPO": "JPEG"}  # MPO: a JPEG from some cameras
FORMAT_WORDS = {"GIF": "a GIF", "BMP": "a BMP", "TIFF": "a TIFF", "ICO": "an icon", "PSD": "a Photoshop file"}

# Pillow's own guard, kept on: a picture over twice this raises DecompressionBombError while opening, and one over it
# warns (the warning is turned into an error below). Never None, so the guard cannot be switched off by accident.
if Image.MAX_IMAGE_PIXELS is None or Image.MAX_IMAGE_PIXELS > MAX_PIXELS:
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS

# The fixed names an upload may be written under (the version folder decides the rest of the path).
SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}\.png$")

TRY_AGAIN = "Try adding it again."


class UploadError(ValueError):
    """A picture the app will not take, with a plain sentence saying why."""


def decode_base64(data: str, field: str = "picture") -> bytes:
    """Strict base64 (optionally a data: URL) to bytes, at most MAX_BYTES."""
    if not isinstance(data, str) or not data.strip():
        raise UploadError(f"No {field} was sent. {TRY_AGAIN}")
    if len(data) > MAX_B64_CHARS:
        raise UploadError(f"That {field} is larger than 25 MB. Save a smaller copy and try again.")
    text = data.strip()
    if text.startswith("data:"):
        head, sep, text = text.partition(",")
        if not sep or ";base64" not in head:
            raise UploadError(f"That {field} could not be read. {TRY_AGAIN}")
    text = re.sub(r"\s+", "", text)
    try:
        raw = base64.b64decode(text, validate=True)
    except (ValueError, binascii.Error):
        raise UploadError(f"That {field} could not be read. {TRY_AGAIN}") from None
    if not raw:
        raise UploadError(f"That {field} is empty. {TRY_AGAIN}")
    if len(raw) > MAX_BYTES:
        raise UploadError(f"That {field} is larger than 25 MB. Save a smaller copy and try again.")
    return raw


def _has_transparency(im: Image.Image) -> bool:
    return im.mode in ("RGBA", "LA", "PA") or (im.mode in ("P", "L", "RGB") and "transparency" in im.info)


def load_image(raw: bytes, field: str = "picture", max_side: int = MAX_SIDE) -> Image.Image:
    """Bytes to a clean RGB or RGBA image: sniffed format, pixel cap, EXIF orientation applied, no metadata, longest
    side at most max_side. Raises UploadError."""
    if len(raw) > MAX_BYTES:
        raise UploadError(f"That {field} is larger than 25 MB. Save a smaller copy and try again.")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            im = Image.open(io.BytesIO(raw))
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise UploadError(f"That {field} is too large to open safely (more than 50 million pixels). "
                              "Save a smaller copy and try again.") from None
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
            raise UploadError(f"That file is not a picture the app can read. Use a PNG, JPEG or WebP picture.") from None
        fmt = (im.format or "").upper()
        if fmt not in FORMATS:
            what = FORMAT_WORDS.get(fmt, "a kind of picture the app does not take")
            raise UploadError(f"That {field} is {what}. Use a PNG, JPEG or WebP picture.")
        w, h = im.size
        if w * h > MAX_PIXELS:
            raise UploadError(f"That {field} is too large to open safely (more than 50 million pixels). "
                              "Save a smaller copy and try again.")
        if min(w, h) < MIN_SIDE:
            raise UploadError(f"That {field} is too small ({w} x {h} pixels). Use one at least {MIN_SIDE} pixels on each side.")
        try:
            if getattr(im, "n_frames", 1) > 1:
                im.seek(0)  # an animated picture: its first frame
            im.load()
            im = ImageOps.exif_transpose(im)  # turned the right way up; returns a copy
            alpha = _has_transparency(im)
            im = im.convert("RGBA" if alpha else "RGB")
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise UploadError(f"That {field} is too large to open safely (more than 50 million pixels). "
                              "Save a smaller copy and try again.") from None
        except (OSError, SyntaxError, ValueError):
            raise UploadError(f"That {field} is damaged or cut short. {TRY_AGAIN}") from None
    if im.mode == "RGBA":
        lo, _hi = im.getchannel("A").getextrema()
        if lo >= 250:
            im = im.convert("RGB")  # an alpha channel with nothing see-through in it
    if max(im.size) > max_side:
        im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    # A fresh image from the pixels alone: no EXIF (location), no ICC profile, no text chunks.
    return Image.frombytes(im.mode, im.size, im.tobytes())


def decode_image(data: str, field: str = "picture", max_side: int = MAX_SIDE) -> Image.Image:
    """base64 text (or a data: URL) to a clean image. Raises UploadError."""
    return load_image(decode_base64(data, field), field, max_side)


def save_png(img: Image.Image, folder: Path, name: str) -> Path:
    """Write `img` as PNG into `folder` under the fixed `name` (lower-case letters, digits and _ then .png). The
    folder must exist; nothing is written anywhere else."""
    if not SAFE_NAME.match(name):
        raise ValueError(f"not a fixed upload name: {name!r}")
    folder = Path(folder).resolve()
    if not folder.is_dir():
        raise ValueError(f"upload folder does not exist: {folder}")
    dest = (folder / name).resolve()
    if dest.parent != folder:
        raise ValueError("upload name escapes its folder")
    img.save(dest, format="PNG")
    return dest


def flatten(img: Image.Image, background: tuple[int, int, int] = (235, 235, 235)) -> Image.Image:
    """An RGBA picture on a plain background (light grey, as the painter's pose blocks ask for); RGB as it is."""
    if img.mode != "RGBA":
        return img.convert("RGB")
    bg = Image.new("RGB", img.size, background)
    bg.paste(img, mask=img.getchannel("A"))
    return bg


def describe(img: Image.Image) -> dict[str, object]:
    """What the app records about an upload: size and whether it has see-through pixels. No metadata is kept."""
    return {"size": list(img.size), "transparent": img.mode == "RGBA"}
