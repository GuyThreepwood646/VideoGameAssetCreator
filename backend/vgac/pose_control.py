"""The guide pictures the step 1 picture is drawn to (graphs/concept.py zimage_graph `control`).

Z-Image Turbo's pose control model (Fun Controlnet Union 2.1) takes a picture of a skeleton in the standard 18-point
colour-coded stick format and makes the painted figure follow it. A category that sets `pose_control` in its yaml
gets one fixed skeleton: the A-pose (ARM_ANGLE, FEET_APART), standing upright, the whole figure inside the middle four
fifths of the height (the same frame the category's pose_block asks for in words).

A category that sets `long_body_control` gets the outline of a long body lying in flat open S bends seen from above, head at the left and tail
tip at the right, for the descriptions it treats as long limbless animals (stages/concept.py long_body). The same model
reads it as an edge guide.
"""

from __future__ import annotations

import math
from typing import Any

from PIL import Image, ImageDraw

# The standard format's 18 points in its own order (nose, neck, then paired points down each side, then eyes and ears);
# index 2 to 4 and 8 to 10 are on the picture's left when the figure faces the camera.
STICKS = [(1, 2), (1, 5), (2, 3), (3, 4), (5, 6), (6, 7), (1, 8), (8, 9), (9, 10), (1, 11), (11, 12), (12, 13), (1, 0), (0, 14),
         (14, 16), (0, 15), (15, 17)]
COLOURS = [(255, 0, 0), (255, 85, 0), (255, 170, 0), (255, 255, 0), (170, 255, 0), (85, 255, 0), (0, 255, 0), (0, 255, 85),
           (0, 255, 170), (0, 255, 255), (0, 170, 255), (0, 85, 255), (0, 0, 255), (85, 0, 255), (170, 0, 255), (255, 0, 255),
           (255, 0, 170), (255, 0, 85)]

TOP, BOTTOM = 0.10, 0.90  # the figure's top and bottom as shares of the image height
ARM_ANGLE = 45.0  # degrees from straight down, both sides
FEET_APART = 1.0 / 3.0  # the distance between the feet over the figure's height (the category wording's "a third")
DEFAULT_STRENGTH = 0.8


def points(width: int, height: int) -> list[tuple[float, float]]:
    """The 18 skeleton points for a picture of this size, figure centred."""
    cx, top, bottom = width / 2, TOP * height, BOTTOM * height
    h = bottom - top
    head = h / 7.5
    nose_y, neck_y = top + head * 0.5, top + head * 1.25
    sh_y, sh_dx = top + head * 1.45, head * 0.8
    upper, fore = head * 1.45, head * 1.2
    dx, dy = math.sin(math.radians(ARM_ANGLE)), math.cos(math.radians(ARM_ANGLE))
    hip_y, hip_dx = top + head * 4.0, head * 0.42
    ank_dx = h * FEET_APART / 2
    knee_y = (hip_y + bottom) / 2 + head * 0.15
    knee_dx = (hip_dx + ank_dx) / 2
    eye_y, eye_dx, ear_dy, ear_dx = nose_y - head * 0.12, head * 0.14, head * 0.05, head * 0.3

    def side(s: int) -> list[tuple[float, float]]:  # s = -1 picture left, +1 picture right
        sh = (cx + s * sh_dx, sh_y)
        el = (sh[0] + s * upper * dx, sh[1] + upper * dy)
        wr = (el[0] + s * fore * dx, el[1] + fore * dy)
        return [sh, el, wr]

    r, l = side(-1), side(1)
    return [(cx, nose_y), (cx, neck_y), r[0], r[1], r[2], l[0], l[1], l[2],
            (cx - hip_dx, hip_y), (cx - knee_dx, knee_y), (cx - ank_dx, bottom),
            (cx + hip_dx, hip_y), (cx + knee_dx, knee_y), (cx + ank_dx, bottom),
            (cx - eye_dx, eye_y), (cx + eye_dx, eye_y), (cx - ear_dx, eye_y + ear_dy), (cx + ear_dx, eye_y + ear_dy)]


def skeleton(width: int = 1024, height: int = 1024) -> Image.Image:
    """The A-pose skeleton on black, drawn as the pose control model expects: tapered coloured sticks, a dot per point."""
    scale = 4  # drawn large and reduced, for smooth edges
    pts = [(x * scale, y * scale) for x, y in points(width, height)]
    im = Image.new("RGB", (width * scale, height * scale), (0, 0, 0))
    d = ImageDraw.Draw(im)
    stick = max(2.0, height / 256) * scale
    for k, (a, b) in enumerate(STICKS):
        (x1, y1), (x2, y2) = pts[a], pts[b]
        length = math.hypot(x2 - x1, y2 - y1)
        ang = math.atan2(y2 - y1, x2 - x1)
        # a flat ellipse along the stick, as a polygon
        a_, b_ = length / 2, stick
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        ell = [(cx + a_ * math.cos(t) * math.cos(ang) - b_ * math.sin(t) * math.sin(ang),
                cy + a_ * math.cos(t) * math.sin(ang) + b_ * math.sin(t) * math.cos(ang))
               for t in [2 * math.pi * i / 36 for i in range(36)]]
        d.polygon(ell, fill=tuple(int(c * 0.6) for c in COLOURS[k]))
    for k, (x, y) in enumerate(pts):
        d.ellipse([x - stick, y - stick, x + stick, y + stick], fill=COLOURS[k])
    return im.resize((width, height), Image.LANCZOS)


def s_line(u: float) -> tuple[float, float, float]:
    """The rest pose at u (0 the nose, 1 the tail tip): centre (x, y) and half body width, in picture widths."""
    x = 0.10 + 0.80 * u
    y = 0.5 + 0.17 * math.sin(3 * math.pi * u)  # one and a half waves: a body about thirty times as long as it is wide
    # Head slightly wider than the neck, then an even body that narrows to a point over the last third.
    if u < 0.08:
        r = 0.018 + 0.009 * math.sin(math.pi * u / 0.08)
    elif u < 0.18:
        r = 0.018 + 0.006 * (u - 0.08) / 0.10
    else:
        r = 0.024 * min(1.0, (1.0 - u) / 0.35)
    return x, y, r * math.sqrt(min(1.0, 0.1 + u / 0.025))  # a rounded nose


def s_outline(width: int = 1024, height: int = 1024) -> Image.Image:
    """The rest pose's outline, white on black."""
    scale, steps = 4, 240
    w, h = width * scale, height * scale
    side = min(w, h)
    left, right = [], []
    for i in range(steps + 1):
        u = i / steps
        x, y, r = s_line(u)
        (x0, y0, _), (x1, y1, _) = s_line(max(0.0, u - 0.002)), s_line(min(1.0, u + 0.002))
        length = math.hypot(x1 - x0, y1 - y0)
        nx, ny = -(y1 - y0) / length, (x1 - x0) / length
        for edge, sign in ((left, 1.0), (right, -1.0)):
            edge.append((w / 2 + (x - 0.5 + sign * nx * r) * side, h / 2 + (y - 0.5 + sign * ny * r) * side))
    im = Image.new("RGB", (w, h), (0, 0, 0))
    outline = left + right[::-1] + left[:1]
    ImageDraw.Draw(im).line(outline, fill=(255, 255, 255), width=max(2, side // 400), joint="curve")
    return im.resize((width, height), Image.LANCZOS)


def control_spec(category: dict[str, Any], key: str = "pose_control") -> dict[str, Any] | None:
    """The category's guide settings under `key`, or None. `<key>: {strength, end}` in the category yaml."""
    spec = category.get(key)
    if not isinstance(spec, dict):
        return None
    return {"strength": float(spec.get("strength", DEFAULT_STRENGTH)), "end": float(spec.get("end", 1.0))}
