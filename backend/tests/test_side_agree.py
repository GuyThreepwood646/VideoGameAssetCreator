"""side_agree (vgac/side_agree.py): a left or right view is the same height as the front and back, feet level.
UNTESTED ON A CARD: the limits are calibrated on the processor only (bakeoff/wp51a)."""
from PIL import Image, ImageDraw

from vgac import side_agree


def _figure(path, h=400, w=120, bottom=700, size=(512, 800)):
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    x0 = (size[0] - w) // 2
    d.rectangle((x0, bottom - h, x0 + w, bottom), fill=(200, 60, 60, 255))
    im.save(path)
    return path


def test_same_figure_agrees(tmp_path):
    f = _figure(tmp_path / "front.png")
    b = _figure(tmp_path / "back.png")
    s = _figure(tmp_path / "side.png", w=60)
    m = side_agree.measures(s, f, b)
    assert m["height"] == 1.0
    assert m["feet"] == 0.0
    c = side_agree.check("left", s, f, b)
    assert c["passed"] is True and c["name"] == "side_agree"


def test_shorter_side_is_flagged_and_named(tmp_path):
    f = _figure(tmp_path / "front.png")
    b = _figure(tmp_path / "back.png")
    s = _figure(tmp_path / "side.png", w=60, h=320, bottom=700)  # 0.8 as tall
    c = side_agree.check("right", s, f, b)
    assert c["passed"] is False
    assert "right side" in c["message"] or "right side" in c["vars"]["words"]
    assert "Draw this view again" in c["vars"]["words"]


def test_feet_out_of_line_are_flagged(tmp_path):
    f = _figure(tmp_path / "front.png")
    b = _figure(tmp_path / "back.png")
    s = _figure(tmp_path / "side.png", w=60, bottom=700 - 60)  # feet 60 px (15 percent of height) higher
    c = side_agree.check("left", s, f, b)
    assert c["passed"] is False
    assert "higher" in c["vars"]["words"]


def test_guidance_has_the_check():
    from vgac import guidance

    assert "side_agree" in guidance.CHECK_GUIDE


def test_empty_cutout_is_not_read_as_agreement(tmp_path):
    f = _figure(tmp_path / "front.png")
    b = _figure(tmp_path / "back.png")
    empty = Image.new("RGBA", (512, 800), (0, 0, 0, 0))
    p = tmp_path / "empty.png"
    empty.save(p)
    c = side_agree.check("left", p, f, b)
    assert c["passed"] is False
