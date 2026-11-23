"""facing.by_picture: a building's front placed by the square-on step 1 picture against the audit renders at quarter
turns, refused for odd angles and for a picture of something else; and its use in prep.resolve_facing."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from vgac import facing, quality
from vgac.stages import prep

WALL = (70, 140, 180, 255)
WIN = (240, 200, 120, 255)


def _box(path: Path, width: int, facade: float | None, height: int = 120) -> Path:
    """A box seen level from one side: a wall `width` wide; `facade` (0 to 1) is where a window band and door sit
    across it, None for a blank wall."""
    im = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    x0, y0 = (256 - width) // 2, (256 - height) // 2
    d.rectangle([x0, y0, x0 + width, y0 + height], fill=WALL)
    if facade is not None:
        cx = x0 + int(facade * width)
        for dx in (-50, -20, 20, 50):
            d.rectangle([cx + dx - 8, y0 + 20, cx + dx + 8, y0 + 45], fill=WIN)
        d.rectangle([cx - 8, y0 + 70, cx + 8, y0 + height], fill=WIN)
    im.save(path)
    return path


def _renders(tmp: Path, facade_yaws: tuple[int, ...], shift: int = 0) -> Path:
    """Audit renders of a 3x2 box every 15 degrees: wide at yaws 90 and 270, narrow at 0 and 180, the facade on the
    sides in facade_yaws, everything turned by `shift` (the model built turned)."""
    d = tmp / f"views_{shift}"
    d.mkdir()
    import math
    for y in range(0, 360, 15):
        a = math.radians(y - shift)
        long_, short = abs(math.sin(a)) * 150, abs(math.cos(a)) * 100
        width = int(long_ + short)
        near = min(facade_yaws, key=lambda f: facing.angle_diff(f, y - shift))
        seen = facing.angle_diff(near, y - shift) < 60
        frac = 0.5 + 0.5 * (short / max(1, width)) * (1 if math.sin(a) * math.cos(a) > 0 else -1) if seen else None
        _box(d / f"shade_{y}.png", max(width, 20), frac if seen else None)
    return d


def test_side_on_building_is_placed_by_the_picture(tmp_path):
    pic = _box(tmp_path / "pic.png", 150, 0.5)
    r = facing.picture_of_files(pic, _renders(tmp_path, (90, 270)))
    assert r is not None and r["decisive"], r
    assert r["turn"] in (90, 270)
    assert r["both_sides_match"]


def test_planted_quarter_turns_follow_the_model(tmp_path):
    pic = _box(tmp_path / "pic.png", 150, 0.5)
    for s in (0, 90, 180, 270):
        r = facing.picture_of_files(pic, _renders(tmp_path, (90,), shift=s))
        assert r["decisive"], (s, r)
        assert r["turn"] == (-(90 + s)) % 360, (s, r)


def test_odd_angles_are_refused(tmp_path):
    pic = _box(tmp_path / "pic.png", 150, 0.5)
    for s in (30, 45, 60):
        r = facing.picture_of_files(pic, _renders(tmp_path, (90,), shift=s))
        assert not r["decisive"], (s, r)


def test_a_picture_of_something_else_is_refused(tmp_path):
    other = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    ImageDraw.Draw(other).polygon([(128, 30), (220, 220), (36, 220)], fill=(200, 60, 60, 255))
    other.save(tmp_path / "other.png")
    r = facing.picture_of_files(tmp_path / "other.png", _renders(tmp_path, (90,)))
    assert r is not None and not r["decisive"], r


def test_table_rules():
    flat = {y: {"iou": 0.84, "de": 28.0} for y in range(0, 360, 15)}
    assert not facing.by_picture(flat)["decisive"]
    clear = {**flat, 0: {"iou": 0.69, "de": 30.3}, 180: {"iou": 0.69, "de": 31.1},
             90: {"iou": 0.96, "de": 15.0}, 270: {"iou": 0.96, "de": 14.8}}
    r = facing.by_picture(clear)
    assert r["decisive"] and r["turn"] == 90 and r["both_sides_match"]
    # the lowest colour difference off the quarters (a model left at an odd angle) refuses it
    assert not facing.by_picture({**clear, 240: {"iou": 0.85, "de": 9.0}})["decisive"]
    # the outline and the colour pointing at different pairs refuse it
    assert not facing.by_picture({**clear, 0: {"iou": 0.69, "de": 12.0}})["decisive"]
    assert facing.by_picture({0: {"iou": 1.0, "de": 1.0}}) is None


def _audit():
    # a flat outline table with a front/back tie, as a box building gives (Check a: best 105, margin 0.0021)
    scores = {str(t): 0.86 for t in range(0, 360, 15)}
    scores.update({"0": 0.786, "180": 0.786, "105": 0.8706, "285": 0.8685, "60": 0.869})
    return {"silhouette_offset": 255, "silhouette_sign": 1, "silhouette_iou": 0.87, "silhouette_turn_scores": scores}


def test_resolve_facing_turns_a_side_on_building():
    cat = {"id": "environment", "facing_by_picture": True}
    pic = {"decisive": True, "turn": 90, "both_sides_match": True}
    r = prep.resolve_facing(cat, "auto", _audit(), None, None, pic)
    assert r["face_yaw_applied"] == 90 and r["face_decided_by"] == "picture" and r["face_decision_sure"]
    chk = prep.facing_check(r, "environment")
    assert chk is not None and chk["passed"] and chk["vars"]["state"] == "picture"


def test_resolve_facing_without_the_picture_is_unchanged():
    base = prep.resolve_facing({"id": "environment"}, "auto", _audit(), None, None)
    assert base["face_yaw_applied"] == 0 and base["face_decision_sure"] is False
    # a refused picture, or a category that does not opt in, leaves the outline path as it was
    refused = prep.resolve_facing({"id": "environment", "facing_by_picture": True}, "auto", _audit(), None, None,
                                  {"decisive": False, "turn": 90})
    other = prep.resolve_facing({"id": "prop_rigid"}, "auto", _audit(), None, None, {"decisive": True, "turn": 90})
    for r in (refused, other):
        assert r["face_yaw_applied"] == base["face_yaw_applied"] and r["face_best_turn"] == base["face_best_turn"]
    assert quality.angle_diff(base["face_best_turn"], 0) <= 90
