"""Synthetic pictures for the "start from your own images" tests: Pillow drawings, no graphics card."""

from __future__ import annotations

import base64
import io

from PIL import Image, ImageDraw

GREY = (235, 235, 235)
# A figure the size the later steps want (about 945 px tall: readiness.DETAIL_SUBJECT_PX is 860).
BIG = {"size": (800, 1150), "scale": 1.5}


def figure(arms: str = "a", size=(512, 768), bg=GREY, offset=(0, 0), scale: float = 1.0, legs: str = "together") -> Image.Image:
    """A standing person: head, torso, legs, and arms in a narrow A-pose ("a", about 17 degrees from the body), a
    wide one ("wide", about 45 degrees), down against the body ("down") or held straight out ("t"); legs side by side
    ("together") or with the feet a quarter of the height apart ("apart")."""
    im = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(im)
    ox, oy = offset

    def p(x, y):
        return (ox + x * scale, oy + y * scale)

    body = (180, 60, 60)
    skin = (230, 190, 150)
    d.ellipse([*p(216, 70), *p(296, 150)], fill=skin)
    d.rectangle([*p(216, 150), *p(296, 430)], fill=body)
    if legs == "apart":
        d.line([p(236, 430), p(160, 700)], fill=(40, 40, 120), width=max(1, int(28 * scale)))
        d.line([p(276, 430), p(352, 700)], fill=(40, 40, 120), width=max(1, int(28 * scale)))
    else:
        d.rectangle([*p(222, 430), *p(250, 700)], fill=(40, 40, 120))
        d.rectangle([*p(262, 430), *p(290, 700)], fill=(40, 40, 120))
    if arms == "wide":
        d.line([p(212, 165), p(60, 330)], fill=skin, width=max(1, int(18 * scale)))
        d.line([p(300, 165), p(452, 330)], fill=skin, width=max(1, int(18 * scale)))
    elif arms == "a":
        d.line([p(212, 165), p(140, 400)], fill=skin, width=max(1, int(18 * scale)))
        d.line([p(300, 165), p(372, 400)], fill=skin, width=max(1, int(18 * scale)))
    elif arms == "down":
        d.rectangle([*p(196, 155), *p(216, 400)], fill=skin)
        d.rectangle([*p(296, 155), *p(316, 400)], fill=skin)
    elif arms == "t":
        d.rectangle([*p(60, 160), *p(452, 185)], fill=skin)
    return im


def cut(im: Image.Image, bg=GREY, tol: int = 40) -> Image.Image:
    """A cutout like BiRefNet's: pixels close to the plain background become see-through."""
    import numpy as np

    arr = np.array(im.convert("RGBA"))
    diff = np.abs(arr[..., :3].astype(int) - np.array(bg)).sum(axis=2)
    arr[..., 3] = np.where(diff <= tol, 0, 255)
    return Image.fromarray(arr, "RGBA")


def b64(im: Image.Image, fmt: str = "PNG", **save) -> str:
    buf = io.BytesIO()
    im.save(buf, format=fmt, **save)
    return base64.b64encode(buf.getvalue()).decode()


def data_url(im: Image.Image, fmt: str = "PNG") -> str:
    return f"data:image/{fmt.lower()};base64," + b64(im, fmt)
