"""WP-19 part 2: locomotion speed from planted feet and attack hit time from peak limb speed (plain Python, gait.py).

No Blender here: the functions take posed positions and return the numbers the sidecar carries.
"""
import math

from vgac.blender import gait

FPS = 24.0
HEIGHT = 1.0  # metres: the plant tolerance is 2 cm


def _cycle(n, duty, speed, stance_z=0.0, swing_z=0.25):
    """One foot over an n-frame cycle: planted (z = stance_z) for the first duty share, sliding backward at `speed`
    relative to the body; then lifted and carried forward back to where the stance began."""
    planted = int(round(n * duty))
    track = []
    x_end = -speed * planted / FPS
    for i in range(n):
        if i < planted:
            track.append((-speed * i / FPS, 0.0, stance_z))
        else:
            k = (i - planted + 1) / (n - planted)
            track.append((x_end * (1 - k), 0.0, stance_z + swing_z * math.sin(math.pi * k)))
    return track


def test_planted_feet_give_the_stance_speed():
    # two feet half a cycle apart, each planted for 60 percent of an 0.9 s cycle at 1.4 m/s
    n = 22
    left = _cycle(n, 0.6, 1.4)
    right = _cycle(n, 0.6, 1.4)
    right = right[n // 2:] + right[:n // 2]
    out = gait.planted_speed([left, right], FPS, HEIGHT)
    assert out["method"] == "planted_feet"
    assert abs(out["speed"] - 1.4) < 0.05
    assert 0.5 < out["planted_share"] < 0.9


def test_speed_scales_with_the_clip_speed():
    slow = gait.planted_speed([_cycle(30, 0.6, 1.0)], FPS, HEIGHT)
    fast = gait.planted_speed([_cycle(30, 0.6, 3.5)], FPS, HEIGHT)
    assert abs(slow["speed"] - 1.0) < 0.05
    assert abs(fast["speed"] - 3.5) < 0.05


def test_in_place_clip_falls_back():
    # feet that never leave the ground and never travel: no stance speed
    still = [(0.0, 0.0, 0.0)] * 30
    out = gait.planted_speed([still, still], FPS, HEIGHT)
    assert out["speed"] is None
    assert out["method"] == "fallback"


def test_flat_clip_with_feet_lifted_falls_back():
    # the foot never touches the ground (a hover or a wing clip): fewer planted frames than the minimum share
    track = [(0.1 * i / FPS, 0.0, 0.3 + 0.2 * math.sin(i / 3)) for i in range(60)]
    out = gait.planted_speed([track], FPS, HEIGHT)
    assert out["speed"] is None
    assert "below" in out["reason"]


def test_empty_or_too_short_input_falls_back():
    assert gait.planted_speed([], FPS, HEIGHT)["speed"] is None
    assert gait.planted_speed([[(0.0, 0.0, 0.0)]], FPS, HEIGHT)["speed"] is None
    assert gait.planted_speed([[(0.0, 0.0, 0.0)] * 5], 0, HEIGHT)["speed"] is None


def test_peak_limb_speed_frame_is_the_strike():
    # a hand that winds back slowly (0.5 m/s) to frame 8, holds, then strikes forward at 2.0 m/s over frames 14 to 19
    hand = []
    x = 0.0
    for i in range(24):
        if i < 8:
            v = -0.5  # the wind-up is slower than the strike here
        elif i < 14:
            v = 0.0
        elif i < 19:
            v = 2.0
        else:
            v = 0.0
        x += v / FPS
        hand.append((x, 0.0, 1.0))
    foot = [(0.0, 0.0, 0.0)] * 24
    out = gait.peak_speed_frame({"hand_r": hand, "foot_l": foot}, FPS)
    assert out["end"] == "hand_r"
    assert 14 <= out["frame"] <= 18  # inside the strike, not the wind-up
    assert abs(out["time"] - out["frame"] / FPS) < 1e-3
    assert out["speed"] > 1.9


def test_peak_speed_frame_needs_three_frames():
    out = gait.peak_speed_frame({"hand_r": [(0.0, 0.0, 0.0), (0.1, 0.0, 0.0)]}, FPS)
    assert out["frame"] == -1
