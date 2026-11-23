"""Landing approach of a biped foot (plain Python, gait.py): the swing's forward speed falls away before heel contact, so
the foot does not reach the floor still travelling forward; the landing spot and the stance are unchanged; quadrupeds
keep the path they had.

World travel of the foot per frame = change in its forward offset + the body's speed V / fps (the stance moves the
foot back at V, so a planted foot travels 0 in the world).
"""
import math

import pytest

from vgac.blender import gait

FPS = 24.0
CYCLE_S = 1.0
DUTY = 0.6
STRIDE = 0.40  # a walk stride; V = STRIDE / (DUTY * CYCLE_S) = 0.67 m/s
V = STRIDE / (DUTY * CYCLE_S)
PX = 0.0096  # metres per strip pixel at 1.8 m


LAND = 1.0 - 1e-9  # the contact frame; u = 1.0 itself wraps to the start of the stance


def _swing_frames():
    """Cycle fractions u of the swing frames, from the first frame past lift-off to the contact frame."""
    first = math.ceil(DUTY * FPS - 1e-9)
    return [k / FPS for k in range(first, int(FPS))] + [LAND]


def _fwd(u, late_ease):
    return gait.foot_path(u, DUTY, STRIDE, 0.07, late_ease=late_ease)[0]


def _travel(late_ease):
    """World travel (m) of the landing foot per swing frame, first to last."""
    us = _swing_frames()
    out = []
    for a, b in zip(us[:-1], us[1:]):
        out.append((_fwd(b, late_ease) - _fwd(a, late_ease)) + V / FPS)
    return out


def test_last_frame_before_contact_is_under_one_pixel():
    last = _travel(True)[-1]
    assert abs(last) <= PX, f"last frame travel {last * 100:.2f} cm"


def test_two_frame_approach_drops_by_thirty_percent_against_no_ease():
    base = sum(_travel(False)[-2:])
    eased = sum(_travel(True)[-2:])
    assert eased <= 0.7 * base, f"approach {eased * 100:.2f} cm against {base * 100:.2f} cm"


def test_forward_speed_falls_monotonically_in_the_last_quarter():
    tr = _travel(True)
    n_late = max(2, len(tr) // 4)
    late = tr[-n_late - 1:]
    for a, b in zip(late[:-1], late[1:]):
        assert b <= a + 1e-9, f"speed rose: {a * 100:.2f} to {b * 100:.2f} cm per frame"


def test_landing_spot_and_stance_are_unchanged():
    for u in [0.1, 0.3, 0.5]:  # the stance is not touched
        assert _fwd(u, True) == pytest.approx(_fwd(u, False), abs=1e-12)


def test_landing_spot_within_half_a_centimetre_of_master():
    # the bump is zero at both ends of the swing, so the foot lands where the master path lands it
    assert abs(_fwd(LAND, True) - _fwd(LAND, False)) <= 0.005


def test_no_hover_in_the_last_quarter():
    # within the last 25 percent of the swing, no frame with both forward and downward travel under 0.2 cm while the
    # foot is more than 1 cm up
    us = _swing_frames()
    for a, b in zip(us[:-1], us[1:]):
        if a < DUTY + 0.75 * (1 - DUTY):
            continue
        fwd = (_fwd(b, True) - _fwd(a, True)) + V / FPS
        up_a = gait.foot_path(a, DUTY, STRIDE, 0.07, late_ease=True)[1]
        up_b = gait.foot_path(b, DUTY, STRIDE, 0.07, late_ease=True)[1]
        down = up_a - up_b
        if up_a > 0.01:
            assert not (fwd < 0.002 and down < 0.002), f"hover at u={a:.3f}"


def test_quadruped_path_is_unchanged_by_default():
    for u in [0.2, 0.7, 0.95]:
        assert gait.foot_path(u, 0.5, 0.3, 0.1) == gait.foot_path(u, 0.5, 0.3, 0.1, late_ease=False)

def test_furthest_forward_point_is_within_five_percent_of_a_stride_past_the_landing_spot():
    # the late ease may not reach far ahead of the landing spot mid-swing (a reach reads as an over-straight knee)
    us = [i / 2000.0 for i in range(0, 2001)]
    grid = [DUTY + (1 - DUTY) * f for f in us]
    grid[-1] = LAND
    vals = [_fwd(u, True) for u in grid]
    over = (max(vals) - vals[-1]) / STRIDE
    assert over <= 0.05, f"overshoot {over * 100:.2f} percent of the stride"


def test_foot_does_not_move_backward_in_the_late_swing():
    # peak backward world speed in the last quarter of the swing, in m/s (none expected: the foot only slows)
    us = [DUTY + (1 - DUTY) * (0.75 + 0.25 * i / 2000.0) for i in range(0, 2001)]
    us[-1] = LAND
    vals = [_fwd(u, True) for u in us]
    speeds = []
    for a, b, ua, ub in zip(vals[:-1], vals[1:], us[:-1], us[1:]):
        speeds.append((b - a) / (ub - ua) * (1 - DUTY) / (1 - DUTY) + V)  # cycle time 1 s: u runs 0..1 per second
    assert min(speeds) >= -0.01, f"peak backward speed {min(speeds):.3f} m/s"
