"""WP-47 item 2: one_subject counts a subject made of several pieces as one when the pieces lie close together
(readiness.concept_checks, the category's subject_gap). Categories without subject_gap count every separate piece, as
before, so a person or a creature with two separate parts still fails."""

from __future__ import annotations

from PIL import Image, ImageDraw

from vgac import readiness


def _cutout(boxes, size=(600, 600)):
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for x0, y0, x1, y1 in boxes:
        d.rectangle([x0, y0, x1, y1], fill=(120, 90, 60, 255))
    return im


def _one_subject(im, category):
    checks = readiness.concept_checks(im, category)
    return next(c for c in checks if c["name"] == "one_subject")


# A crate and a wall touching at a small gap (about 3 percent of the 300 px span), and a far second piece.
NEAR = [(100, 200, 250, 400), (262, 200, 500, 400)]
FAR = [(100, 200, 250, 400), (420, 20, 560, 120)]


def test_a_gap_group_is_one_subject_for_scenery():
    check = _one_subject(_cutout(NEAR), {"id": "environment", "subject_gap": 0.08})
    assert check["passed"] and check["value"] == 1


def test_separate_subjects_still_fail_for_scenery():
    check = _one_subject(_cutout(FAR), {"id": "environment", "subject_gap": 0.08})
    assert not check["passed"] and check["value"] == 2


def test_without_subject_gap_every_piece_counts_as_before():
    check = _one_subject(_cutout(NEAR), {"id": "humanoid"})
    assert not check["passed"] and check["value"] == 2


def test_group_parts_joins_only_within_the_gap():
    # (area, top, left, bottom, right): two 101 by 101 boxes with 9 px between them
    boxes = [(10201, 100, 100, 200, 200), (10201, 210, 100, 310, 200)]
    assert readiness.group_parts(boxes, 5) == [10201, 10201]
    assert readiness.group_parts(boxes, 10) == [20402]
