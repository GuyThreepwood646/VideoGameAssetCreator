"""Step 2's same_side check (stages/turnaround.py): a view drawn from the same side as its partner is flagged and drawn
again. Left and right, and the two three-quarter views, are mirror images of each other when each is drawn from its own
side; when their outlines match better as drawn than mirrored, both were drawn from one side (realistic test,
2026-11-13: one run's side views both faced the same way and its model came out backwards)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from PIL import Image

from tests.test_stage_offloop import FakeDB, FakeSC, _lineage
from vgac import imaging
from vgac.stages import turnaround

STATS = {"coverage": 0.3, "height": 100, "aspect": 0.4}


def profile(path: Path, facing_right: bool = True) -> Path:
    """A figure seen from the side: a tall body with a nose and a foot sticking out the way it faces."""
    im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    body = Image.new("RGBA", (16, 48), (200, 50, 50, 255))
    im.paste(body, (24, 8))
    im.paste(Image.new("RGBA", (12, 6), (200, 50, 50, 255)), (40, 12))   # nose
    im.paste(Image.new("RGBA", (16, 6), (200, 50, 50, 255)), (40, 50))   # foot
    if not facing_right:
        im = im.transpose(Image.FLIP_LEFT_RIGHT)
    im.save(path)
    return path


def test_pair_overlap_tells_mirror_images_from_copies(tmp_path):
    a = profile(tmp_path / "a.png")
    same = profile(tmp_path / "same.png")
    other = profile(tmp_path / "other.png", facing_right=False)
    as_drawn, mirrored = imaging.pair_overlap(a, same)
    assert as_drawn > 0.99 and mirrored < 0.8          # both face the same way
    as_drawn, mirrored = imaging.pair_overlap(a, other)
    assert mirrored > 0.99 and as_drawn < 0.8          # one from each side


def test_same_side_fails_with_a_plain_sentence():
    info = turnaround.view_checks("right", STATS, None, None, None, mirror=0.599, as_drawn=0.884)   # the test's bad pair
    c = {c["id"]: c for c in info["checks"]}["same_side"]
    assert not c["ok"] and not info["passed"] and c["severity"] == "warn"
    assert "same side as the left side view" in c["help"] and "88%" in c["help"] and "60%" in c["help"]
    assert c["value"] == 0.884 and info["pair_as_drawn"] == 0.884 and info["pair_mirrored"] == 0.599
    assert {c["id"]: c for c in info["checks"]}["mirror_iou"]["ok"]   # 0.599 still passes the unchanged 0.55 limit


def test_same_side_passes_opposite_sides_and_subjects_that_look_alike_from_both():
    good = turnaround.view_checks("right", STATS, None, None, None, mirror=0.855, as_drawn=0.660)
    assert good["passed"] and "opposite sides" in {c["id"]: c for c in good["checks"]}["same_side"]["help"]
    # a barrel: the two overlaps are equal, and a hair's difference either way is not a wrong side
    assert turnaround.view_checks("right", STATS, None, None, None, mirror=0.991, as_drawn=1.0)["passed"]
    # the closest good pair of the test (as drawn 0.668, mirrored 0.685)
    assert turnaround.view_checks("quarter2", STATS, None, None, None, mirror=0.685, as_drawn=0.668)["passed"]


def test_the_three_quarter_pair_takes_same_side_but_not_the_mirror_limit():
    info = turnaround.view_checks("quarter2", STATS, None, None, None, mirror=0.550, as_drawn=0.700)   # the test's bad pair
    ids = [c["id"] for c in info["checks"]]
    assert "same_side" in ids and "mirror_iou" not in ids and "mirror_iou" not in info
    assert not info["passed"]
    assert "same side as the three-quarter view" in {c["id"]: c for c in info["checks"]}["same_side"]["help"]
    # a low mirrored overlap alone does not fail a three-quarter view: the 0.55 limit is for left and right only
    assert turnaround.view_checks("quarter2", STATS, None, None, None, mirror=0.40, as_drawn=0.30)["passed"]


def test_without_a_partner_nothing_is_added():
    info = turnaround.view_checks("left", STATS, None, None, None, None)
    assert [c["id"] for c in info["checks"]] == ["coverage"]


def _run(tmp_path, monkeypatch, right_faces, views=("front", "left", "right")):
    """The step with drawn profiles: left faces left; each draw of the right view faces as right_faces says in turn
    (True: to the right, as it should)."""
    monkeypatch.setattr(turnaround.library, "version_file", lambda v, key, settings: v.image if key == "image" else None)
    draws: dict[str, int] = {}
    seq = list(right_faces)

    async def fake_generate(sc, src_name, view, seed, lora_strength, extra, base, span):
        n = draws[view] = draws.get(view, 0) + 1
        later = view in ("right", "quarter2")
        facing_right = seq[min(n, len(seq)) - 1] if later else False
        cut = profile(sc.dir / f"{view}_{n}.png", facing_right=facing_right)
        return {"raw": cut, "cutout": cut, "white": imaging.white_composite(cut), "stats": None}

    monkeypatch.setattr(turnaround, "_generate_view", fake_generate)
    concept, ta, new = _lineage(tmp_path)
    sc = FakeSC(tmp_path, new, FakeDB(concept, ta, new))
    asyncio.run(turnaround.adjust(sc, {"views": list(views), "seed": 7}))
    return sc.metrics["views"], draws


def test_a_wrong_side_view_is_drawn_again(tmp_path, monkeypatch):
    views, draws = _run(tmp_path, monkeypatch, right_faces=[False, True])   # first try: the same way as the left view
    assert draws["right"] == 2 and draws["left"] == 1
    assert views["right"]["rerolls"] == 1 and views["right"]["passed"] and not views["right"]["flagged"]
    assert views["left"]["passed"]


def test_a_view_still_on_the_wrong_side_after_two_redraws_stays_flagged(tmp_path, monkeypatch):
    views, draws = _run(tmp_path, monkeypatch, right_faces=[False])
    assert draws["right"] == 1 + turnaround.MAX_REROLLS
    r = views["right"]
    assert r["flagged"] and not r["passed"] and r["rerolls"] == turnaround.MAX_REROLLS
    failed = {c["id"]: c for c in r["checks"] if not c["ok"]}
    assert "same_side" in failed and "Draw this view again" in failed["same_side"]["help"]


def test_the_second_three_quarter_view_is_drawn_again_too(tmp_path, monkeypatch):
    views, draws = _run(tmp_path, monkeypatch, right_faces=[False, True], views=("front", "quarter", "quarter2"))
    assert draws["quarter2"] == 2 and views["quarter2"]["passed"] and views["quarter2"]["rerolls"] == 1
