"""Clean-up says when the character is wrong: skin never metal on the whole body (vgac/skinmetal.py), a metal crown is
not always a helmet (vgac/hair.py), and the checks for a refused picture projection, "not measured" results and the two
independent facing tests (vgac/stages/prep.py, vgac/quality.py, vgac/guidance.py)."""

from __future__ import annotations

import numpy as np
import pytest

from vgac import face, guidance, hair, quality, skinmetal, srcproject
from vgac.stages import prep


def lab_of(rgb):
    return face.to_lab(np.array([rgb], dtype=np.float64))[0]


# --- skin by hue ------------------------------------------------------------------------------------------------------


def test_skin_hue_reads_shadowed_skin_but_not_steel_gold_or_black():
    face_skin = lab_of((0.80, 0.58, 0.46))
    dark = lab_of((0.30, 0.19, 0.14))  # the same skin in shadow
    steel = lab_of((0.62, 0.64, 0.66))
    gold = lab_of((0.85, 0.65, 0.10))
    blue = lab_of((0.10, 0.15, 0.45))
    black = lab_of((0.01, 0.01, 0.01))
    got = skinmetal.skin_hue(np.stack([face_skin, dark, steel, gold, blue, black]), face_skin)
    assert got.tolist() == [True, True, False, False, False, False]


def test_armour_in_the_description_limits_the_clearing_to_head_and_neck():
    for d in ("a knight in steel plate armour", "a barbarian with bronze bracers", "a pirate with a gold earring"):
        assert skinmetal.armoured(d)
    for d in (None, "", "a woman in a blue bikini", "a man in a tan jacket and jeans"):
        assert not skinmetal.armoured(d)


def strip(n=40, size=1.0):
    """A flat grid of n x n quads (2 triangles each) laid out in UV over the unit square, in the x-y plane."""
    xs = np.linspace(0, size, n + 1)
    v = np.array([(x, y, 0.0) for y in xs for x in xs])
    uv = np.array([(x / size, y / size) for y in xs for x in xs])
    f = []
    for j in range(n):
        for i in range(n):
            a, b, c, d = j * (n + 1) + i, j * (n + 1) + i + 1, (j + 1) * (n + 1) + i, (j + 1) * (n + 1) + i + 1
            f += [(a, b, c), (b, d, c)]
    return {"verts": v, "faces": np.array(f), "uv": uv, "vnormals": np.tile([0.0, 0.0, 1.0], (len(v), 1))}


def test_a_blanket_metal_map_keeps_only_small_metal_looking_pieces():
    T = 128
    mesh = strip()
    base = np.zeros((T, T, 3))
    base[:] = (0.80, 0.58, 0.46)  # skin everywhere
    base[:, 64:] = (0.2, 0.3, 0.5)  # a big blue cloth half
    base[10:18, 10:18] = (0.62, 0.64, 0.66)  # a small steel piece (a buckle)
    base[100:104, 100:104] = (0.62, 0.64, 0.66)  # a second one
    met = np.ones((T, T))  # the builder marked everything metal
    keep = skinmetal.small_metal_pieces(mesh, base, met, T)
    assert keep[12, 12] and keep[101, 101]  # the buckles keep their metal
    assert not keep[40, 30] and not keep[60, 100]  # skin and cloth do not
    # a big steel piece (a whole jacket) is not small: it is cleared
    big = np.zeros((T, T, 3))
    big[:] = (0.62, 0.64, 0.66)
    assert not skinmetal.small_metal_pieces(mesh, big, met, T).any()


# --- the helmet test ----------------------------------------------------------------------------------------------------


def test_a_dark_or_everywhere_metal_crown_is_not_a_helmet():
    steel = lab_of((0.60, 0.61, 0.62))
    black_hair = lab_of((0.04, 0.04, 0.04))
    silver_hair = lab_of((0.8, 0.8, 0.8))
    assert hair.helmet_possible(steel, None) and hair.helmet_possible(steel, 0.0)
    assert not hair.helmet_possible(black_hair, 0.0)  # the 12 stored "helmets" were all hair, black to brown
    assert not hair.helmet_possible(silver_hair, 0.97)  # the face is metal too: the map says nothing
    assert hair.helmet_possible(silver_hair, 0.1)  # metal on the head only: could be a helm


# --- the checks ----------------------------------------------------------------------------------------------------------


def test_not_measured_never_passes_and_has_no_fix():
    c = quality.not_measured("texture_sharp", "how sharp it is", "no overlap")
    assert not c["passed"] and not c["ok"] and c["value"] == "not measured" and c["vars"]["measured"] is False
    assert c["help"] == "Not measured: how sharp it is was not checked, because no overlap." and "fix" not in c
    assert not quality.summarise([c])["gate_ok"]


def test_projection_check_says_why_it_was_refused_and_what_to_do():
    m = {"srcproject_applied": False, "srcproject_reason": "silhouette match 0.817 is under 0.85", "export_mesh_hash": "h"}
    c = prep.projection_check(m, {}, "humanoid", {})
    assert not c["passed"] and c["severity"] == "warn"
    assert "outline match 0.82, it needs 0.85" in c["help"] and "redraw the front view in step 2" in c["help"]
    ok = prep.projection_check({"srcproject_applied": True, "srcproject_painted_share": 0.83, "export_mesh_hash": "h"}, {}, "humanoid", {})
    assert ok["passed"] and "83%" in ok["help"]
    # off by the person's choice, a crisp look or another category: nothing to say
    assert prep.projection_check(m, {srcproject.PARAM: False}, "humanoid", {}) is None
    assert prep.projection_check(m, {}, "humanoid", {"crisp": True}) is None
    assert prep.projection_check(m, {}, "quadruped", {}) is None
    # the pass never recorded an answer: not measured, not a pass
    lost = prep.projection_check({"export_mesh_hash": "h"}, {}, "humanoid", {})
    assert not lost["passed"] and lost["vars"]["measured"] is False


@pytest.mark.parametrize("reason,words", [
    ("the result is no closer to the picture than the model's own colour", "would not have made the model look more like it"),
    ("the picture does not show the front", "does not show the character from the front"),
    ("failed: boom", "the painting failed (boom)"),
    (None, "no reason was recorded"),
])
def test_projection_reasons_are_plain_words(reason, words):
    assert words in prep.projection_reason_words(reason)


def test_skin_metal_check_after_the_clearing_and_not_measured():
    ran = {"ran": True, "metal_share_before": 0.97, "blanket": True, "left_metal": 10, "skin_coloured_texels": 1000, "kept_metal_texels": 50,
           "texels_changed": 9, "body": True}
    c = prep.skin_metal_check({"skin_metal": ran}, "humanoid")
    assert c["passed"] and "whole character as metal" in c["help"]
    bad = prep.skin_metal_check({"skin_metal": {**ran, "blanket": False, "left_metal": 400}}, "humanoid")
    assert not bad["passed"] and "40%" in bad["help"]
    nofind = prep.skin_metal_check({"skin_metal": {"ran": False, "skipped": "no face found"}}, "humanoid")
    assert not nofind["passed"] and nofind["help"].startswith("Not measured:") and "face could not be found" in nofind["help"]
    for skipped in ("not a humanoid", "the description makes the character metal", "no metal map"):
        assert prep.skin_metal_check({"skin_metal": {"ran": False, "skipped": skipped}}, "humanoid") is None
    assert prep.skin_metal_check({"skin_metal": ran}, "quadruped") is None


def test_texture_checks_are_not_measured_when_the_texture_pass_could_not_read_the_model():
    got = prep.surface_checks({"texture_pass_error": "the final model has no texture layout"})
    assert {c["id"] for c in got} == {"baked_light", "albedo_range", "metal_mixed"}
    assert all(not c["passed"] and c["help"].startswith("Not measured:") for c in got)
    assert prep.surface_checks({}) == []


FACING = {"face_outline_turn": 180, "face_best_turn": 180, "face_yaw_applied": 180, "face_toes_found": True, "face_toes_turn": 175.0,
          "face_tests_differ_deg": 5.0, "face_toes_reason": None}


def test_facing_tests_report_both_tests_and_whether_they_agreed():
    ok = prep.facing_check(FACING, "humanoid")
    assert ok["passed"] and "agree" in ok["help"]
    split = prep.facing_check({**FACING, "face_toes_turn": 0.0, "face_tests_differ_deg": 180.0, "face_best_turn": 0}, "humanoid")
    assert not split["passed"] and split["vars"]["outline_turn"] == 180 and split["vars"]["toes_turn"] == 0
    assert split["fix"]["params"] == {"face_yaw": 0}
    one = prep.facing_check({**FACING, "face_toes_found": False, "face_toes_turn": None, "face_tests_differ_deg": None,
                             "face_toes_reason": "the legs cannot be told apart"}, "humanoid")
    assert not one["passed"] and one["vars"]["measured"] is False and "fix" not in one
    assert "legs cannot be told apart" in one["help"] and "180 degrees" in one["help"]
    assert prep.facing_check(FACING, "quadruped") is None  # no second test exists for it, and the old circular check is gone
    assert prep.facing_check({"face_toes_found": False, "face_outline_turn": None, "face_toes_reason": "x"}, "humanoid") is None
    assert "facing" not in guidance.CHECK_GUIDE
