"""The back view's hair against the side views' (hair_sides.py, turnaround hair_matches_sides, back_references).

Graph: the two finished side views ride along as pictures 2 and 3 on the same nodes, so no model is loaded twice.
Check: measured on drawn figures (a bob seen from the front, the sides and the back); quiet for bald, short hair, a hat and
a hood. Stage: the back is drawn last, with the references, for characters whose hair is their own; off for library
hair, other categories and `back_references: false`; a failing back is redrawn."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from vgac import hair_sides, imaging
from vgac.graphs import qwen_edit
from vgac.stages import turnaround

from test_stage_offloop import FakeDB, _lineage, _rgba
from test_second_quarter import MetricsSC

HAIR = (40, 30, 25, 255)
SKIN = (222, 182, 152, 255)
SHIRT = (200, 60, 60, 255)
H = 512


def _figure(path, view, end=108, back_width=90, hair_colour=HAIR, bald=False, hat=None):
    """A figure 512 rows high: head, shoulders at row 100, hair hanging to row `end`. front: two curtains beside the face;
    left and right: a block behind the head; back: a block of `back_width` pixels."""
    im = Image.new("RGBA", (300, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle((50, 100, 250, H - 1), fill=SHIRT)
    d.rectangle((135, 80, 165, 100), fill=SKIN)
    hc = SKIN if bald else hair_colour
    if view == "front":
        d.ellipse((110, 8, 190, 90), fill=SKIN)
        d.ellipse((105, 0, 195, 50), fill=hc)
        if not bald:
            d.rectangle((100, 20, 118, end), fill=hc)
            d.rectangle((182, 20, 200, end), fill=hc)
    elif view in ("left", "right"):
        d.rectangle((115, 100, 185, H - 1), fill=SHIRT)
        d.ellipse((140, 8, 190, 90), fill=SKIN)
        d.rectangle((110, 0, 170, end if not bald else 60), fill=hc)
    else:
        d.ellipse((108, 8, 192, 90), fill=SKIN)
        half = back_width // 2
        d.rectangle((150 - half, 0, 150 + half, end if not bald else 60), fill=hc)
    if hat:
        d.rectangle((100, 0, 200, 30), fill=hat)
    im.save(path)
    return path


def _set(tmp, **back):
    tmp.mkdir(exist_ok=True)
    paths = {v: _figure(tmp / f"{v}.png", v, **(back if v == "back" else {k: x for k, x in back.items() if k in ("end", "hair_colour", "bald", "hat")}))
             for v in ("front", "left", "back", "right")}
    return paths


def _compare(p):
    return hair_sides.compare(p["back"], p["front"], p["left"], p["right"])


def test_a_back_that_matches_the_sides_passes(tmp_path):
    res = _compare(_set(tmp_path))
    assert res is not None and res["ok"] is True
    assert abs(res["end_gap"]) < 0.05 and res["width_ratio"] >= hair_sides.MIN_WIDTH_RATIO


def test_a_narrow_column_down_the_back_fails(tmp_path):
    res = _compare(_set(tmp_path, back_width=30))
    assert res is not None and res["ok"] is False and res["width_ratio"] < hair_sides.MIN_WIDTH_RATIO
    assert res["end_gap"] <= res["limit"]   # the length matched; the width did not
    c = turnaround.hair_check(res)
    assert c["ok"] is False and c["severity"] == "warn" and "narrow strip down the middle of the back" in c["help"]


def test_hair_that_stops_higher_up_the_back_fails(tmp_path):
    p = _set(tmp_path)
    _figure(p["back"], "back", end=62)
    res = _compare(p)
    assert res is not None and res["ok"] is False and res["end_gap"] > res["limit"]
    assert "shorter than the side views show it" in turnaround.hair_check(res)["help"]


def test_no_verdict_where_there_is_no_hair_to_match(tmp_path):
    assert _compare(_set(tmp_path / "bald", bald=True)) is None
    assert _compare(_set(tmp_path / "short", end=40)) is None                  # hair that does not reach the shoulders
    assert _compare(_set(tmp_path / "hood", hair_colour=(40, 120, 50, 255))) is None   # a green hood is not hair
    assert _compare(_set(tmp_path / "pale", hair_colour=(235, 215, 150, 255))) is None  # too pale to tell from skin


def test_no_verdict_when_a_picture_is_missing_or_empty(tmp_path):
    p = _set(tmp_path)
    Image.new("RGBA", (300, H), (0, 0, 0, 0)).save(p["left"])
    assert _compare(p) is None
    p["right"] = tmp_path / "nothing.png"
    assert _compare(p) is None


# --- the graph -------------------------------------------------------------------------------------------------------


def _classes(g):
    return sorted(n["class_type"] for n in g.values())


@pytest.mark.parametrize("zero_negative", [False, True])
def test_the_side_views_are_pictures_two_and_three_on_the_same_model_nodes(zero_negative):
    plain = qwen_edit.angle_graph("a.png", "p", 1, "x", zero_negative=zero_negative)
    g = qwen_edit.angle_graph("a.png", "p", 1, "x", zero_negative=zero_negative, references=["l.png", "r.png"])
    assert g["pos"]["inputs"]["image1"] == ["sc", 0]
    assert g["pos"]["inputs"]["image2"] == ["sc2", 0] and g["pos"]["inputs"]["image3"] == ["sc3", 0]
    assert g["img2"]["inputs"]["image"] == "l.png" and g["img3"]["inputs"]["image"] == "r.png"
    if not zero_negative:
        assert g["neg"]["inputs"]["image3"] == ["sc3", 0]
    # nothing that loads a model is added: only two pictures and their scalers
    from collections import Counter
    added = Counter(_classes(g)) - Counter(_classes(plain))
    assert dict(added) == {"LoadImage": 2, "ImageScaleToTotalPixels": 2}
    assert plain == qwen_edit.angle_graph("a.png", "p", 1, "x", zero_negative=zero_negative, references=None)


def test_the_cutout_graph_takes_the_references_too_and_three_is_the_most():
    g = qwen_edit.angle_cutout_graph("a.png", "p", 1, "x", "c", references=["l.png", "r.png"])
    assert "image3" in g["pos"]["inputs"] and qwen_edit.CUT_NODE in g
    with pytest.raises(ValueError):
        qwen_edit.angle_graph("a.png", "p", 1, "x", references=["1", "2", "3"])


def test_the_sentence_names_the_pictures_and_does_not_ask_for_their_turn():
    s = qwen_edit.BACK_SIDES_SENTENCE
    assert "Picture 2" in s and "Picture 3" in s and "length" in s and "shoulders" in s and "Do not copy the pose" in s


# --- the stage -------------------------------------------------------------------------------------------------------

ORDER = ["front", "left", "back", "right", "quarter"]


class StagedSC(MetricsSC):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.staged_names: list[tuple[str, str | None]] = []

    async def stage_input(self, src, name, instance=None):
        self.staged_names.append((name, instance))
        return name


@pytest.fixture
def drawn(monkeypatch):
    monkeypatch.setattr(turnaround.library, "version_file", lambda v, key, settings: v.image if key == "image" else None)
    state = {"calls": [], "back_fails": 0}

    async def fake_generate(sc, src_name, view, seed, lora_strength, extra, base, span, name=None, instance=None, references=None):
        state["calls"].append({"view": view, "extra": extra, "references": references, "seed": seed})
        raw = sc.dir / f"{view}_raw.png"
        cut = sc.dir / f"{view}.png"
        bad = view == "back" and state["back_fails"] > 0
        if view == "back" and bad:
            state["back_fails"] -= 1
        _figure(cut, view, back_width=30 if bad else 90)
        _figure(raw, view)
        return {"raw": raw, "cutout": cut, "white": imaging.white_composite(cut), "stats": None}

    monkeypatch.setattr(turnaround, "_generate_view", fake_generate)
    return state


def run(tmp_path, params=None, category="humanoid", views=None):
    concept, ta, new = _lineage(tmp_path)
    sc = StagedSC(tmp_path, new, FakeDB(concept, ta, new), category=category)
    asyncio.run(turnaround._run_views(sc, list(views or ORDER), {"seed": 100, **(params or {})}, concept))
    return sc


def calls_by_view(state):
    out: dict[str, list] = {}
    for c in state["calls"]:
        out.setdefault(c["view"], []).append(c)
    return out


def test_the_back_is_drawn_last_with_the_two_side_views_for_a_character(tmp_path, drawn):
    sc = run(tmp_path, {"back_references": True})
    order = [c["view"] for c in drawn["calls"]]
    assert order == ["front", "left", "right", "quarter", "back"]
    back = calls_by_view(drawn)["back"][0]
    assert back["references"] == ["vgac_nnnnnnnn_back_ref_left.png", "vgac_nnnnnnnn_back_ref_right.png"]
    assert qwen_edit.BACK_SIDES_SENTENCE in back["extra"]
    assert all(c["references"] is None for c in drawn["calls"] if c["view"] != "back")
    assert sc.metrics["views"]["back"]["references"] == ["left", "right"] and sc.metrics["back_references"] is True
    assert back["seed"] == 102      # still its place in the usual order, so the seeds do not move
    staged = [n for n, _ in sc.staged_names if "back_ref" in n]
    assert staged == ["vgac_nnnnnnnn_back_ref_left.png", "vgac_nnnnnnnn_back_ref_right.png"]   # staged once, not per attempt


@pytest.mark.parametrize("params, category", [
    ({"back_references": False}, "humanoid"),
    ({}, "quadruped"),
    ({"bald_views": True}, "humanoid"),      # library hair: the haired views are replaced, the bald ones have nothing to match
])
def test_the_references_stay_off_where_there_is_nothing_to_match(tmp_path, drawn, params, category):
    sc = run(tmp_path, params, category=category)
    assert all(c["references"] is None and "Picture 2" not in c["extra"] for c in drawn["calls"])
    assert "references" not in sc.metrics["views"].get("back", {}) and "back_references" not in sc.metrics


def test_a_failing_back_is_redrawn_with_the_references_each_time(tmp_path, drawn):
    drawn["back_fails"] = 1
    sc = run(tmp_path, {"back_references": True})
    backs = calls_by_view(drawn)["back"]
    assert len(backs) == 2 and all(b["references"] for b in backs)
    assert backs[1]["seed"] != backs[0]["seed"]
    info = sc.metrics["views"]["back"]
    assert info["rerolls"] == 1 and info["passed"] is True
    assert any(c["name"] == "hair_matches_sides" for c in info["checks"])


def test_a_back_that_never_matches_is_flagged_after_two_redraws(tmp_path, drawn):
    drawn["back_fails"] = 5
    sc = run(tmp_path, {"back_references": True})
    assert len(calls_by_view(drawn)["back"]) == 1 + turnaround.MAX_REROLLS
    info = sc.metrics["views"]["back"]
    failed = [c for c in info["checks"] if not c["passed"]]
    assert info["flagged"] is True and [c["name"] for c in failed] == ["hair_matches_sides"]


def test_a_back_redrawn_alone_takes_the_side_views_it_already_has(tmp_path, drawn):
    concept, ta, new = _lineage(tmp_path)
    sc = StagedSC(tmp_path, new, FakeDB(concept, ta, new))
    for v in ("front", "left", "right"):
        _figure(tmp_path / f"{v}.png", v)
        sc.register(f"{v}_cutout", tmp_path / f"{v}.png")
        sc.register(f"{v}_white", imaging.white_composite(tmp_path / f"{v}.png"))
    asyncio.run(turnaround._run_views(sc, ["back"], {"seed": 100, "back_references": True}, concept))
    back = calls_by_view(drawn)["back"][0]
    assert back["references"] and "back" in sc.metrics["views"]
    assert sc.metrics["views"]["back"]["checks"][-1]["name"] == "hair_matches_sides"


def test_a_side_view_missing_means_no_references_and_a_check_that_says_it_was_not_measured(tmp_path, drawn):
    concept, ta, new = _lineage(tmp_path)
    sc = StagedSC(tmp_path, new, FakeDB(concept, ta, new))
    asyncio.run(turnaround._run_views(sc, ["back"], {"seed": 100, "back_references": True}, concept))
    assert calls_by_view(drawn)["back"][0]["references"] is None
    c = [x for x in sc.metrics["views"]["back"]["checks"] if x["name"] == "hair_matches_sides"]
    assert len(c) == 1 and c[0]["passed"] is True and c[0]["help"].startswith("Not measured")


def hair_entries(sc):
    return [x for x in sc.metrics["views"]["back"]["checks"] if x["name"] == "hair_matches_sides"]


def test_on_one_card_the_back_is_checked_once_the_right_view_is_in_and_a_failure_redraws_it(tmp_path, drawn):
    """The usual order on one card is front, left, back, right: the right view does not exist when the back finishes."""
    drawn["back_fails"] = 1
    sc = run(tmp_path)
    assert [c["view"] for c in drawn["calls"]] == ["front", "left", "back", "right", "back", "quarter"]
    info = sc.metrics["views"]["back"]
    assert info["rerolls"] == 1 and info["passed"] is True and info["flagged"] is False
    assert len(hair_entries(sc)) == 1 and hair_entries(sc)[0]["passed"] is True and "hair_end_gap" in info
    assert [c["seed"] for c in calls_by_view(drawn)["back"]][0] == 102   # its place in the usual order, as before


def test_on_one_card_a_back_that_never_matches_is_flagged_after_two_redraws(tmp_path, drawn):
    drawn["back_fails"] = 5
    sc = run(tmp_path)
    assert len(calls_by_view(drawn)["back"]) == 1 + turnaround.MAX_REROLLS
    info = sc.metrics["views"]["back"]
    assert info["flagged"] is True and info["rerolls"] == turnaround.MAX_REROLLS
    assert [c["name"] for c in info["checks"] if not c["passed"]] == ["hair_matches_sides"]


def test_a_back_that_matches_is_not_redrawn_in_either_order(tmp_path, drawn):
    for order in (ORDER, ["front", "left", "right", "back", "quarter"]):
        drawn["calls"].clear()
        (tmp_path / "-".join(order)).mkdir(exist_ok=True)
        sc = run(tmp_path / "-".join(order), views=order)
        assert len(calls_by_view(drawn)["back"]) == 1
        assert len(hair_entries(sc)) == 1 and hair_entries(sc)[0]["passed"] is True


def test_with_the_right_view_drawn_first_a_failing_back_is_redrawn_as_before(tmp_path, drawn):
    drawn["back_fails"] = 1
    sc = run(tmp_path, views=["front", "left", "right", "back", "quarter"])
    assert [c["view"] for c in drawn["calls"]] == ["front", "left", "right", "back", "back", "quarter"]
    assert sc.metrics["views"]["back"]["rerolls"] == 1 and sc.metrics["views"]["back"]["passed"] is True


def test_a_run_that_never_draws_the_right_view_says_the_hair_was_not_measured(tmp_path, drawn):
    sc = run(tmp_path, views=["front", "left", "back"])
    c = hair_entries(sc)
    assert len(c) == 1 and c[0]["passed"] is True and c[0]["help"].startswith("Not measured")
    assert "could not be read" in c[0]["help"]


def test_a_back_with_nothing_to_compare_says_not_measured_and_is_not_redrawn(tmp_path, drawn):
    sc = run(tmp_path, {"back_references": True}, views=ORDER)
    c = [x for x in sc.metrics["views"]["back"]["checks"] if x["name"] == "hair_matches_sides"]
    assert len(c) == 1 and c[0]["passed"] is True
    res, why = hair_sides.compare_with_reason(*(_set(tmp_path / "bald", bald=True)[v] for v in ("back", "front", "left", "right")))
    assert res is None and why == "colour"
    note = turnaround.hair_not_measured(why)
    assert note["ok"] is True and note["help"].startswith("Not measured: the hair in this back view was not compared")
    assert "pale" not in note["help"] and "skin" in note["help"]
    _, why2 = hair_sides.compare_with_reason(*(_set(tmp_path / "short", end=40)[v] for v in ("back", "front", "left", "right")))
    assert why2 == "short"
