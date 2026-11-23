"""Foot slide and hit frame (plain Python, gait.py): the foot leaves and lands with no world speed, a planted foot rolls
about a point that stays put, a leg with a named foot keeps its IK on thigh and shin, and the hit is contact (the
furthest reach after the peak speed), not the peak speed itself.
"""
import math

import pytest

from vgac.blender import gait

FPS = 24.0


# ---- swing ends match the stance ----------------------------------------------------------------------------------

@pytest.mark.parametrize("run,duty", [(False, 0.6), (True, 0.4)])
def test_foot_leaves_and_lands_at_the_stance_speed(run, duty):
    stride, lift = 0.6, 0.3 if run else 0.1
    h = 1e-5
    stance_slope = (gait.foot_path(0.3 * duty + h, duty, stride, lift, run)[0]
                    - gait.foot_path(0.3 * duty, duty, stride, lift, run)[0]) / h
    lift_off = (gait.foot_path(duty + h, duty, stride, lift, run)[0] - gait.foot_path(duty, duty, stride, lift, run)[0]) / h
    landing = (gait.foot_path(1.0 - 1e-9, duty, stride, lift, run)[0] - gait.foot_path(1.0 - h, duty, stride, lift, run)[0]) / h
    # relative to the body the foot moves back at the stance speed at both ends of the swing: no speed in the world
    assert lift_off == pytest.approx(stance_slope, rel=0.02)
    assert landing == pytest.approx(stance_slope, rel=0.02)


# ---- roll about a fixed point ----------------------------------------------------------------------------------------

FWD, LAT = (0.0, -1.0, 0.0), (1.0, 0.0, 0.0)
ANKLE, BALL, FLOOR = (0.2, 0.0, 0.10), (0.2, -0.15, 0.02), 0.0


def _turn(p, piv, pitch):
    q = gait.q_axis_angle(LAT, math.radians(-pitch))
    return gait.v_add(piv, gait.q_rotate(q, gait.v_sub(p, piv)))


@pytest.mark.parametrize("pitch", [-18.0, -9.0, -22.0])
def test_toes_down_rolls_about_the_ball(pitch):
    a = gait.rolled_ankle(ANKLE, BALL, FLOOR, FWD, LAT, 0.15, pitch)
    # the foot turned by the same rotation and moved to the new ankle puts the ball back where it was
    ball_after = gait.v_add(a, gait.q_rotate(gait.q_axis_angle(LAT, math.radians(-pitch)), gait.v_sub(BALL, ANKLE)))
    assert ball_after == pytest.approx(BALL, abs=1e-9)
    assert a[2] > ANKLE[2]  # the heel comes up


@pytest.mark.parametrize("pitch", [12.0, 6.0])
def test_toes_up_rolls_about_the_heel_on_the_floor(pitch):
    heel = (ANKLE[0] - FWD[0] * gait.HEEL_BACK_SHARE * 0.15, ANKLE[1] - FWD[1] * gait.HEEL_BACK_SHARE * 0.15, FLOOR)
    a = gait.rolled_ankle(ANKLE, BALL, FLOOR, FWD, LAT, 0.15, pitch)
    heel_after = gait.v_add(a, gait.q_rotate(gait.q_axis_angle(LAT, math.radians(-pitch)), gait.v_sub(heel, ANKLE)))
    assert heel_after == pytest.approx(heel, abs=1e-9)
    assert a == pytest.approx(_turn(ANKLE, heel, pitch), abs=1e-12)


def test_flat_foot_keeps_the_ankle():
    assert gait.rolled_ankle(ANKLE, BALL, FLOOR, FWD, LAT, 0.15, 0.0) == pytest.approx(ANKLE, abs=1e-12)


def test_old_heel_raise_lets_the_ball_slide():
    # master's rule: lift the ankle by toe_len * sin(pitch) and leave it over the same forward place; the ball moves
    pitch = -18.0
    a_old = (ANKLE[0], ANKLE[1], ANKLE[2] + math.sin(math.radians(-pitch)) * 0.15)
    ball_old = gait.v_add(a_old, gait.q_rotate(gait.q_axis_angle(LAT, math.radians(-pitch)), gait.v_sub(BALL, ANKLE)))
    assert math.dist(ball_old[:2], BALL[:2]) > 0.02


# ---- a named foot keeps the IK on thigh and shin ----------------------------------------------------------------------

def test_foot_bone_index_finds_named_feet():
    assert gait.foot_bone_index(["mixamorig:LeftUpLeg", "mixamorig:LeftLeg", "mixamorig:LeftFoot", "mixamorig:LeftToeBase"]) == 2
    assert gait.foot_bone_index(["UpperLeg.R", "LowerLeg.R", "Foot.R", "Toes.R"]) == 2
    assert gait.foot_bone_index(["bone_25", "bone_26", "bone_27", "bone_28"]) is None


def test_big_cartoon_foot_keeps_thigh_and_shin():
    # goblin: thigh 0.19, shin 0.115, foot 0.25, toe 0.05; shin + foot is the longer pair
    segs = [0.19, 0.115, 0.25, 0.05]
    assert gait.ik_pair_start(segs) == 1
    assert gait.ik_pair_start(segs, foot_index=2) == 0


def test_unnamed_leg_takes_the_longest_pair():
    assert gait.ik_pair_start([0.2, 0.4, 0.4, 0.1]) == 1
    assert gait.ik_pair_start([0.4, 0.4, 0.2, 0.1]) == 0


# ---- hit frame is contact ------------------------------------------------------------------------------------------

def _strike(contact, n=32):
    """A hand that accelerates and stops at 1.04 m on frame `contact`: the WP-19 rerun planted attack track."""
    shape = [0.02, 0.05, 0.15, 0.32, 0.55, 0.79, 0.97, 1.04]  # metres on frames contact - 7 to contact
    xs = []
    for i in range(n):
        k = i - (contact - 7)
        xs.append(0.0 if k < 0 else (shape[k] if k < len(shape) else 1.04))
    return [(x, 0.0, 1.0) for x in xs]


@pytest.mark.parametrize("contact,peak", [(11, 8), (16, 13), (13, 10)])
def test_hit_frame_is_contact_not_peak_speed(contact, peak):
    ends = {"hand_r": _strike(contact), "foot_l": [(0.0, 0.0, 0.0)] * 32}
    assert gait.peak_speed_frame(ends, FPS)["frame"] == peak
    out = gait.hit_frame(ends, FPS, (1.0, 0.0, 0.0), 1.0)
    assert out["frame"] == contact
    assert out["peak_frame"] == peak and out["end"] == "hand_r" and out["method"] == "furthest_reach"
    assert out["time"] == pytest.approx(contact / FPS, abs=1e-3)


def test_hit_frame_ignores_a_wind_up_that_reaches_as_far():
    # the hand reaches forward in the wind-up (slowly), comes back, then strikes to the same reach: the strike counts
    xs = [0.0, 0.2, 0.4, 0.5, 0.5, 0.3, 0.1, 0.0, 0.0, 0.1, 0.35, 0.5, 0.5, 0.5, 0.5, 0.5]
    ends = {"hand_r": [(x, 0.0, 1.0) for x in xs]}
    out = gait.hit_frame(ends, FPS, (1.0, 0.0, 0.0), 1.0)
    assert out["frame"] == 11


def test_hit_frame_needs_three_frames():
    assert gait.hit_frame({"hand_r": [(0.0, 0.0, 0.0), (0.1, 0.0, 0.0)]}, FPS, (1.0, 0.0, 0.0), 1.0)["frame"] == -1
