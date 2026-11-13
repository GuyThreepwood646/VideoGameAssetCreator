"""Step 2's pose_held check (stages/turnaround.py): a front or back view whose arms or legs reach a different amount
sideways than the picture's is flagged and drawn again (realistic test, 2026-11-13: drawn front views had the arms
hanging while the picture and the back view held them out, and clean-up streaked the painted arms)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from vgac import imaging
from vgac.stages import turnaround

STATS = {"coverage": 0.3, "height": 100, "aspect": 0.4}


def figure(path: Path, arms_out: bool, legs_apart: bool = True) -> Path:
    """A front view: head, torso, two arms (out sideways or hanging) and two legs (apart or together)."""
    im = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    c = (60, 60, 200, 255)
    im.paste(Image.new("RGBA", (10, 10), c), (45, 0))                      # head
    im.paste(Image.new("RGBA", (24, 36), c), (38, 12))                     # torso
    if arms_out:
        im.paste(Image.new("RGBA", (30, 5), c), (8, 16))
        im.paste(Image.new("RGBA", (30, 5), c), (62, 16))                  # arms to the sides
    else:
        im.paste(Image.new("RGBA", (5, 34), c), (33, 14))
        im.paste(Image.new("RGBA", (5, 34), c), (62, 14))                  # arms down
    if legs_apart:
        im.paste(Image.new("RGBA", (8, 50), c), (22, 50))
        im.paste(Image.new("RGBA", (8, 50), c), (70, 50))
    else:
        im.paste(Image.new("RGBA", (8, 50), c), (38, 50))
        im.paste(Image.new("RGBA", (8, 50), c), (54, 50))
    im.save(path)
    return path


def ratios(tmp_path, view_arms, view_legs, ref_arms=True, ref_legs=True):
    ref = imaging.pose_extents(figure(tmp_path / "ref.png", ref_arms, ref_legs))
    ext = imaging.pose_extents(figure(tmp_path / "view.png", view_arms, view_legs))
    return ext, ref


def test_pose_extents_tell_arms_out_from_arms_down(tmp_path):
    out = imaging.pose_extents(figure(tmp_path / "a.png", True))
    down = imaging.pose_extents(figure(tmp_path / "b.png", False))
    assert out["arm"] > down["arm"] * 1.5
    assert abs(out["leg"] - down["leg"]) < 0.02


def test_same_pose_passes_for_front_and_back(tmp_path):
    ext, ref = ratios(tmp_path, True, True)
    for view in ("front", "back"):
        c = turnaround.pose_check(view, ext, ref)
        assert c["ok"] and c["value"] == 1.0 and "where they are" in c["help"]


def test_arms_down_against_arms_out_fails_with_a_plain_sentence(tmp_path):
    ext, ref = ratios(tmp_path, False, True)
    c = turnaround.pose_check("front", ext, ref)
    assert not c["ok"] and c["severity"] == "warn"
    assert "front view" in c["help"] and "arms" in c["help"] and "closer to the body" in c["help"]
    assert "closer together" not in c["help"] and "further apart" not in c["help"]


def test_legs_together_against_legs_apart_fails(tmp_path):
    ext, ref = ratios(tmp_path, True, False)
    c = turnaround.pose_check("back", ext, ref)
    assert not c["ok"] and "back view" in c["help"] and "closer together" in c["help"] and "arms reach" not in c["help"]


def test_arms_further_out_than_the_picture_fails(tmp_path):
    ext, ref = ratios(tmp_path, True, True, ref_arms=False)
    c = turnaround.pose_check("front", ext, ref)
    assert not c["ok"] and "further out" in c["help"]


def test_view_checks_adds_pose_held_only_to_front_and_back(tmp_path):
    ext, ref = ratios(tmp_path, False, True)
    front = turnaround.view_checks("front", STATS, None, None, None, None, pose=(ext, ref))
    assert not front["passed"] and not {c["id"]: c for c in front["checks"]}["pose_held"]["ok"]
    assert front["pose_arm_ratio"] < 0.8
    left = turnaround.view_checks("left", STATS, None, None, None, None, pose=(ext, ref))
    assert left["passed"] and "pose_held" not in {c["id"] for c in left["checks"]}
    nopose = turnaround.view_checks("front", STATS, None, None, None, None)
    assert nopose["passed"] and "pose_held" not in {c["id"] for c in nopose["checks"]}
