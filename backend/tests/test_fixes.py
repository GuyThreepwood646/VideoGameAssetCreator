"""Fixing several failed checks with one redraw (vgac/fixes.py, data/fixes.yaml): the merged words."""
from __future__ import annotations

import copy

import pytest

from vgac import buildpose, fixes, hairpic, readiness

REAL = fixes.data()
# Every snippet, withheld ones too: most tests below are about how words are joined, whatever the card offers.
ALL = copy.deepcopy(REAL)
for _s in ALL["snippets"].values():
    _s["offered"] = True


def _c(ids, cat=None, d=None):
    return fixes.compose(ids, cat, d or ALL)
EMOJI_FREE = lambda s: all(ord(ch) < 0x2190 or ch in "’‘“”–—" for ch in s)  # noqa: E731


def test_the_data_has_one_snippet_per_picture_check_and_no_emoji():
    assert set(fixes.known_checks(ALL)) == {"in_frame", "arms_away", "pose_for_3d", "soft_light", "hair_off_eyes", "face_drawn"}
    assert set(fixes.known_checks()) == {"in_frame", "soft_light", "face_drawn"}  # the card offers a tick box for these only
    for sid, s in REAL["snippets"].items():
        if not s.get("offered", True):
            assert s["tested"] is True, sid  # a snippet is only withheld because it was tried
        assert fixes._fill(s["change"], "person").strip().endswith(".") and s["keep"], sid
        assert EMOJI_FREE(s["change"]) and all(EMOJI_FREE(k) for k in s["keep"]), sid
        assert isinstance(s["tested"], bool), sid
    assert [s for s in REAL["snippets"]][:2] == ["frame", "pose_a"]  # the order in the file is the order in the redraw


def test_one_snippet_gives_the_same_words_the_hair_button_sends_today():
    out = _c(["hair_off_eyes"])
    assert out["instruction"] == hairpic.FIX_INSTRUCTION
    assert out["used"] == ["hair_off_eyes"] and out["snippets"] == ["hair_off"] and out["dropped"] == []


def test_the_face_snippet_keeps_the_meaning_of_the_face_button():
    out = _c(["face_drawn"])["instruction"]
    assert out.startswith("Draw the face: the person faces the viewer, with both eyes open and clearly drawn, eyebrows, a nose and a mouth. Keep everything else exactly the same:")
    assert "hair colour" in out and out.endswith("the plain light grey background.")


def test_the_a_pose_comes_from_the_one_source():
    out = _c(["arms_away"])["instruction"]
    assert buildpose.A_POSE in out and out.startswith("Redraw the pose as a wide A pose: ")


def test_the_order_of_the_ticks_does_not_change_the_words():
    a = _c(["face_drawn", "in_frame", "hair_off_eyes"])
    b = _c(["hair_off_eyes", "face_drawn", "in_frame"])
    assert a["instruction"] == b["instruction"] and a["snippets"] == b["snippets"] == ["frame", "hair_off", "face_draw"]
    assert a["used"] == b["used"]


def test_two_ticks_with_one_snippet_are_joined_once():
    out = _c(["pose_for_3d", "arms_away", "arms_away"])
    assert out["snippets"] == ["pose_a"] and sorted(out["used"]) == ["arms_away", "pose_for_3d"]
    assert out["instruction"].count(buildpose.A_POSE) == 1 and out["dropped"] == []


def test_a_merged_redraw_numbers_its_changes_and_keeps_one_keep_list():
    out = _c(["in_frame", "hair_off_eyes"])["instruction"]
    assert out.startswith("Make these 2 changes: (1) Zoom out so the whole person")
    assert "(2) Move the hair off the face" in out
    assert out.count("Keep everything else exactly the same:") == 1
    keep = out.split("Keep everything else exactly the same: ")[1]
    assert keep.count("the same person") == 1 and keep.endswith("and the plain light grey background.")
    # a thing a change alters is not on the keep list: the frame change alters the framing, the hair change the hair
    assert "framing" not in keep and "the whole body in frame" not in keep
    assert "hair colour" in keep and "hair length" in keep and ", hair," not in keep


def test_pose_and_face_do_not_keep_what_the_other_changes():
    out = _c(["arms_away", "face_drawn"])["instruction"]
    keep = out.split("Keep everything else exactly the same: ")[1]
    items = keep.rstrip(".").replace(" and the plain", ", the plain").split(", ")
    assert "face" not in items and "pose" not in items  # the face is drawn and the pose redrawn, so neither is kept as it was
    assert "hair" in items and "hair colour" in items and "the same person" in items


def test_conflicting_snippets_do_not_join_and_the_other_is_said():
    d = copy.deepcopy(ALL)
    d["snippets"]["frame"]["conflicts"] = ["pose_a"]
    out = _c(["arms_away", "in_frame"], d=d)
    assert out["snippets"] == ["frame"] and out["used"] == ["in_frame"]
    assert out["dropped"][0]["check"] == "arms_away" and out["dropped"][0]["reason"] == "conflict"
    assert "pull against each other" in out["dropped"][0]["text"] and "in frame" in out["dropped"][0]["text"]
    # a conflict named on either side counts, whichever way round it is written
    d2 = copy.deepcopy(ALL)
    d2["snippets"]["pose_a"]["conflicts"] = ["frame"]
    out2 = _c(["in_frame", "arms_away"], d=d2)
    assert out2["snippets"] == ["frame"] and out2["dropped"][0]["check"] == "arms_away"


def test_no_more_than_the_cap_in_one_redraw():
    out = _c(["in_frame", "arms_away", "soft_light", "hair_off_eyes", "face_drawn"])
    assert len(out["snippets"]) == REAL["max_changes"] == 3
    assert [x["reason"] for x in out["dropped"]] == ["too_many", "too_many"]
    assert out["snippets"] == ["frame", "pose_a", "even_light"] and "left out" in out["dropped"][0]["text"]


def test_too_long_drops_the_last_change_until_it_fits():
    d = copy.deepcopy(ALL)
    alone = _c(["arms_away"])["words"]
    d["max_words"] = alone + 5
    out = _c(["arms_away", "hair_off_eyes"], d=d)
    assert out["snippets"] == ["pose_a"] and out["used"] == ["arms_away"] and out["words"] <= alone + 5
    assert out["dropped"][0]["check"] == "hair_off_eyes" and out["dropped"][0]["reason"] == "too_long"
    d["max_words"] = 5  # nothing fits: no instruction at all, and everything is said
    none = _c(["arms_away"], d=d)
    assert none["instruction"] is None and none["used"] == [] and none["dropped"][0]["reason"] == "too_long"


def test_a_check_with_no_words_is_said_and_an_empty_tick_gives_nothing():
    out = _c(["manifold", "hair_off_eyes"])
    assert out["used"] == ["hair_off_eyes"] and out["dropped"][0]["reason"] == "no_snippet"
    none = _c([])
    assert none == {"instruction": None, "used": [], "snippets": [], "dropped": [], "words": 0}


def test_other_kinds_of_asset_use_their_own_word():
    out = _c(["in_frame"], "vehicle")["instruction"]
    assert "the whole vehicle" in out and "the same vehicle" in out and "person" not in out


def test_the_steps_two_redraw_and_the_pose_snippet_share_the_a_pose():
    assert buildpose.A_POSE in readiness.HUMANOID_REDRAW and buildpose.A_POSE in _c(["pose_for_3d"])["instruction"]


def test_fix_for_keeps_known_ids_in_order_without_repeats():
    assert fixes.check_fix_for(["face_drawn", "bogus", "in_frame", "face_drawn"]) == ["face_drawn", "in_frame"]


def test_each_of_the_six_checks_is_pinned_to_its_own_wording():
    """A tick box must ask for what its label says (the old "Redraw it in even light" button sent the A-pose redraw)."""
    expect = {"in_frame": ("frame", "Zoom out"), "arms_away": ("pose_a", "A pose"), "pose_for_3d": ("pose_a", "A pose"),
              "soft_light": ("even_light", "even light"), "hair_off_eyes": ("hair_off", "Move the hair off the face"),
              "face_drawn": ("face_draw", "Draw the face")}
    for check, (sid, words) in expect.items():
        assert fixes.snippet_for(check, withheld=True) == sid, check
        text = fixes.compose([check], d=ALL)["instruction"]
        assert words in text, check
        for other, (osid, owords) in expect.items():
            if osid != sid:
                assert owords not in text, (check, other)  # no wording carries another check's change


def test_the_frame_wording_asks_for_a_named_share_of_the_height_not_just_smaller():
    ins = _c(["in_frame"])["instruction"]
    assert "92 percent of the picture height" in ins and "Both feet must be completely visible" in ins and "slightly smaller" not in ins


def test_after_a_frame_redraw_a_short_subject_says_so_and_offers_upscale():
    from vgac import fixnotes, quality
    from vgac.readiness import DETAIL_SUBJECT_PX

    short = quality.check("detail_size", False, 744, DETAIL_SUBJECT_PX, vars={"measure": "tall", "target": 930})
    fine = quality.check("detail_size", True, 940, DETAIL_SUBJECT_PX, vars={"measure": "tall", "target": 930})
    out = fixnotes.after_fix_notes({"checks": [short]}, {"fix_for": ["in_frame"]})["checks"][0]
    assert "now 744 pixels tall" in out["fix_note"] and "at least 860" in out["fix_note"] and "Upscale it" in out["fix_note"]
    assert "fix_note" not in short  # a copy
    assert "fix_note" not in fixnotes.after_fix_notes({"checks": [fine]}, {"fix_for": ["in_frame"]})["checks"][0]
    assert fixnotes.after_fix_notes({"checks": [short]}, {"fix_for": ["soft_light"]})["checks"][0].get("fix_note") != out["fix_note"]
    assert fixnotes.after_fix_notes({"checks": [short]}, {})["checks"][0].get("fix_note") != out["fix_note"]
