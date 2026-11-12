"""Hand score (vgac/handscore.py, docs/HANDS-PLAN.md phase 0) and the hands part of the weighted decimation
(blender/headweight.py hand_zones, checked here with a stand-in for bpy against handscore's rule, and vgac/headtris.py)."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from vgac import handscore, headtris


def box(cx, cy, cz, sx, sy, sz):
    """A closed box as (verts, faces)."""
    xs, ys, zs = (cx - sx / 2, cx + sx / 2), (cy - sy / 2, cy + sy / 2), (cz - sz / 2, cz + sz / 2)
    v = np.array([[x, y, z] for x in xs for y in ys for z in zs], dtype=float)
    f = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1], [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
    return v, f


def join(parts):
    verts, faces, off = [], [], 0
    for v, f in parts:
        verts.append(v)
        faces.append(f + off)
        off += len(v)
    return np.concatenate(verts), np.concatenate(faces)


def figure(fingers: int):
    """A 1.8 m figure (y up) with arms out to the sides: torso, head, legs, and on each side an arm, a palm and `fingers`
    finger boxes (they overlap into one block when fingers is 0 and the palm is long)."""
    parts = [box(0, 1.1, 0, 0.4, 0.7, 0.2), box(0, 1.7, 0, 0.2, 0.2, 0.2), box(-0.1, 0.4, 0, 0.12, 0.8, 0.12), box(0.1, 0.4, 0, 0.12, 0.8, 0.12)]
    for side in (1, -1):
        parts.append(box(side * 0.55, 1.4, 0, 0.7, 0.1, 0.1))  # arm from x 0.2 to 0.9
        parts.append(box(side * 0.98, 1.4, 0, 0.12, 0.2, 0.05))  # palm, to x 1.04
        if fingers:
            for k in range(fingers):
                parts.append(box(side * 1.14, 1.4 + (k - (fingers - 1) / 2) * 0.03, 0, 0.2, 0.018, 0.03))
        else:
            parts.append(box(side * 1.14, 1.4, 0, 0.2, 0.1, 0.05))  # a mitten
    return join(parts)


def mesh_of(fingers):
    v, f = figure(fingers)
    return {"verts": v, "faces": f}


def test_the_hand_tip_is_the_far_end_of_the_arm():
    v, _ = figure(4)
    for side in (1, -1):
        h = handscore.find_hand(v, side)
        assert h["tip"][0] * side == pytest.approx(1.24, abs=0.01)
        assert h["axis"][0] * side > 0.9
        assert h["length"] == pytest.approx(handscore.HAND_SHARE * 1.8, rel=0.05)


def test_legs_are_not_arms():
    v, _ = figure(4)
    h = handscore.find_hand(v, 1)
    assert h["tip"][1] > 1.0  # the tip is up at the arm, not down at a foot


def test_separate_fingers_are_counted_and_a_mitten_is_one_piece():
    four = handscore.score(mesh_of(4))
    mitten = handscore.score(mesh_of(0))
    assert four["left"]["pieces"] == 4 and four["right"]["pieces"] == 4
    assert mitten["left"]["pieces"] == 1 and mitten["right"]["pieces"] == 1


def test_triangles_beyond_the_wrist_stay_on_the_hand():
    s = handscore.score(mesh_of(4))
    total = s["total_triangles"]
    # the hand is the finger boxes (12 triangles each) and part of the palm: never the torso or the legs
    assert 12 * 3 <= s["left"]["triangles"] <= 12 * 6 and s["hand_share"] < 0.5
    assert abs(s["left"]["triangles"] - s["right"]["triangles"]) <= 6


def test_a_spike_is_a_long_sliver():
    verts = np.array([[0, 0, 0], [1, 0, 0], [0.5, 0.01, 0], [0, 0, 0], [1, 0, 0], [0.5, 1, 0]], dtype=float)
    faces = np.array([[0, 1, 2], [3, 4, 5]])
    assert handscore.spike_count(verts, faces, np.array([0, 1])) == 1


def test_an_empty_hand_scores_zero_without_failing():
    v, f = box(0, 0.9, 0, 0.4, 1.8, 0.2)
    s = handscore.score({"verts": v, "faces": f})
    assert s["left"]["pieces"] in (0, 1)


# --- the Blender side's rule matches the tested one -------------------------------------------------------------------

HEADWEIGHT = Path(__file__).resolve().parents[1] / "vgac" / "blender" / "headweight.py"


@pytest.fixture()
def headweight(monkeypatch):
    monkeypatch.setitem(sys.modules, "bpy", types.ModuleType("bpy"))
    spec = importlib.util.spec_from_file_location("headweight_under_test", HEADWEIGHT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_blender_constants_match_the_score(headweight):
    assert (headweight.ARM_X, headweight.SHOULDER_X, headweight.SHOULDER_SHARE, headweight.HAND_SHARE, headweight.HAND_RADIUS) == \
        (handscore.ARM_X, handscore.SHOULDER_X, handscore.SHOULDER_SHARE, handscore.HAND_SHARE, handscore.RADIUS)


def test_blender_hand_zone_selects_the_scores_triangles(headweight):
    v, f = figure(4)
    centres = v[f].mean(1)
    # Blender's axes: x sideways, y depth, z up; the figure's y is up and its z is depth.
    to_blender = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], dtype=float)
    vb = v @ to_blender.T
    obj = types.SimpleNamespace(data=types.SimpleNamespace(vertices=[types.SimpleNamespace(co=p) for p in vb]))

    class V(list):
        def foreach_get(self, name, out):
            out[:] = np.array([c for p in self for c in p.co])

    obj.data.vertices = V(obj.data.vertices)
    zones = headweight.hand_zones(obj)
    assert len(zones) == 2
    mine = headweight.in_hands(centres @ to_blender.T, zones)
    theirs = np.zeros(len(f), dtype=bool)
    for side in (1, -1):
        theirs[handscore.hand_triangles(v, f, handscore.find_hand(v, side))] = True
    assert (mine == theirs).all() and mine.any()


# --- the options ------------------------------------------------------------------------------------------------------


def test_the_hands_part_is_on_unless_turned_off():
    assert headtris.hands_on({}) and headtris.hands_on(None)
    assert not headtris.hands_on({"hand_triangles": False})
    assert headtris.HAND_GAIN >= 2.0


def test_room_leaves_the_hands_room_to_double():
    # the boy's model at room 2 had 2,106 hand triangles and an even decimation left 1,184; twice that is more than 97
    # percent of what the model had, so room 2 could never double the hands' share
    assert 0.97 * 2106 < 2 * 1184 < 0.97 * 2106 * headtris.HEAD_ROOM / 2.0


def test_clean_up_metrics_carry_the_hands():
    from vgac.stages import prep as prep_stage

    head = {"weighted": True, "reason": None}
    m = prep_stage.head_metrics({"head_weighted": True, "head_triangles": 3200, "head_triangles_even": 1800,
                                 "hand_triangles_final": 2100, "hand_triangles_even": 1200, "hand_triangles_source": 3000}, head)
    assert m["hand_triangles"] == 2100 and m["hand_triangles_even"] == 1200 and m["hand_triangles_source"] == 3000
    assert "hand_triangles" not in prep_stage.head_metrics({"head_weighted": True, "head_triangles": 3200}, head)


def test_the_hand_options_are_humanoid_parameters():
    from vgac import reference
    from vgac.stages import mesh as mesh_stage

    by = {s: {p["name"]: p for p in params} for s, params in reference.STAGE_SCHEMAS.items()}
    assert by["mesh"]["hand_edit"]["categories"] == ["humanoid"] and by["prep"]["hand_triangles"]["default"] is True
    assert "hands" in mesh_stage.edit_prompt(mesh_stage.HAND_EDIT) and mesh_stage.edit_prompt(mesh_stage.HAND_EDIT).endswith("white background.")
