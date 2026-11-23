"""facing.by_picture's square-footprint branch: a square box's outline is the same from every quarter, so colour alone
places the side with the door; odd angles, a wrong picture and a long box never get placed by it."""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

from vgac import facing

WALL = (230, 190, 110, 255)
DOOR = (60, 120, 200, 255)
SQUARE = [1.0, 1.0, 2.0]  # the mesh audit's dims: X, Y, Z up
LONG = [1.5, 1.0, 2.0]


def _side(path: Path, width: int, door: float | None, height: int = 200) -> Path:
    """A wall `width` wide; `door` (0 to 1) is where a door and two windows sit across it, None for a blank wall."""
    im = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    x0, y0 = (256 - width) // 2, (256 - height) // 2
    d.rectangle([x0, y0, x0 + width, y0 + height], fill=WALL)
    if door is not None:
        cx = x0 + int(door * width)
        d.rectangle([cx - 15, y0 + 130, cx + 15, y0 + height], fill=DOOR)
        for wy in (20, 70):
            d.rectangle([cx - 12, y0 + wy, cx + 12, y0 + wy + 30], fill=DOOR)
    im.save(path)
    return path


def _renders(tmp: Path, door_yaw: int, shift: int = 0) -> Path:
    """Audit renders of a square tower every 15 degrees, the door on the side seen from door_yaw, turned by shift."""
    d = tmp / f"views_{shift}"
    d.mkdir()
    for y in range(0, 360, 15):
        a = math.radians(y - shift)
        width = int(80 * (abs(math.sin(a)) + abs(math.cos(a))))
        off = facing.signed(y - shift - door_yaw)
        door = None if abs(off) >= 60 else 0.5 - 0.5 * math.sin(math.radians(off))
        _side(d / f"shade_{y}.png", width, door)
    return d


def test_square_tower_door_found_at_every_quarter(tmp_path):
    pic = _side(tmp_path / "pic.png", 80, 0.5)
    for s in (0, 90, 180, 270):
        r = facing.picture_of_files(pic, _renders(tmp_path, 0, shift=s), SQUARE)
        assert r["decisive"] and r["rule"] == "square", (s, r)
        assert r["turn"] == (-s) % 360, (s, r)
        assert "pair" in r["failed"]  # the pair branch refused it: the outline cannot tell the sides apart


def test_square_tower_odd_angles_refused(tmp_path):
    pic = _side(tmp_path / "pic.png", 80, 0.5)
    for s in (30, 45, 60):
        r = facing.picture_of_files(pic, _renders(tmp_path, 0, shift=s), SQUARE)
        assert not r["decisive"], (s, r)


def test_square_tower_wrong_picture_refused(tmp_path):
    other = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    ImageDraw.Draw(other).ellipse([40, 40, 216, 216], fill=(200, 60, 60, 255))
    other.save(tmp_path / "other.png")
    r = facing.picture_of_files(tmp_path / "other.png", _renders(tmp_path, 0), SQUARE)
    assert not r["decisive"], r


def test_long_box_never_reaches_the_square_branch(tmp_path):
    pic = _side(tmp_path / "pic.png", 80, 0.5)
    r = facing.picture_of_files(pic, _renders(tmp_path, 0), LONG)
    assert r["rule"] == "pair" and "square_failed" not in r and not r["decisive"]


def test_square_table_rules():
    t = {y: {"iou": 0.95, "de": 30.0} for y in range(0, 360, 15)}
    t[0] = {"iou": 0.96, "de": 20.0}
    r = facing.by_picture(t, SQUARE)
    assert r["decisive"] and r["rule"] == "square" and r["turn"] == 0
    assert not facing.by_picture(t, LONG)["decisive"]          # not square: the pair branch alone, which refuses it
    assert not facing.by_picture(t, None)["decisive"]          # no dims: never tried
    assert not facing.by_picture({**t, 90: {"iou": 0.95, "de": 25.0}}, SQUARE)["decisive"]  # margin 5.0 < 6.0
    assert not facing.by_picture({**t, 30: {"iou": 0.9, "de": 15.0}}, SQUARE)["decisive"]   # lowest off the quarters
    # the fresh tower and Check a, as their audits recorded them
    assert facing.square_footprint([0.433, 0.416, 1.001]) and not facing.square_footprint([0.70, 1.0, 0.74])
