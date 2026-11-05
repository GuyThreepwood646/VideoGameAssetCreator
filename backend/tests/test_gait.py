"""Gait and clip maths behind blender/procanim.py (G30, G31). Pure Python: no Blender needed."""
import math

import pytest

from vgac.blender import gait


def _legs(n_per_side):
    """Legs spread front (fwd 1) to back (fwd -1) on each side."""
    out = []
    for side in (True, False):
        for i in range(n_per_side):
            fwd = 1.0 - 2.0 * i / max(1, n_per_side - 1)
            out.append({"left": side, "front": fwd > 0, "fwd": fwd})
    return out


@pytest.mark.parametrize("duty", [0.4, 0.5, 0.65, 0.7])
def test_foot_cycle_is_continuous_and_periodic(duty):
    prev = gait.foot_cycle(0.0, duty)
    assert prev == pytest.approx((1.0, 0.0))
    assert gait.foot_cycle(1.0, duty) == pytest.approx(prev)
    for i in range(1, 1001):
        cur = gait.foot_cycle(i / 1000, duty)
        assert abs(cur[0] - prev[0]) < 0.05 and abs(cur[1] - prev[1]) < 0.05
        assert -1.0 <= cur[0] <= 1.0 and 0.0 <= cur[1] <= 1.0
        prev = cur


def test_foot_is_planted_for_the_duty_fraction():
    duty = 0.65
    on_ground = sum(1 for i in range(1000) if gait.foot_cycle(i / 1000, duty)[1] == 0.0)
    assert on_ground / 1000 == pytest.approx(duty, abs=0.01)
    # While planted the foot slides from front to back at constant speed.
    s0, s1 = gait.foot_cycle(0.1, duty)[0], gait.foot_cycle(0.2, duty)[0]
    assert s1 < s0


def test_biped_legs_alternate():
    legs = _legs(1)
    assert gait.gait_for(2) == "alternate"
    ph = gait.leg_phases(legs, "alternate")
    assert abs(ph[0] - ph[1]) == pytest.approx(0.5)


def test_quadruped_walk_is_a_four_beat_lateral_sequence():
    legs = _legs(2)
    ph = gait.leg_phases(legs, "walk4")
    assert sorted(ph) == pytest.approx([0.0, 0.25, 0.5, 0.75])
    by = {(l["left"], l["front"]): p for l, p in zip(legs, ph)}
    assert by[(True, True)] - by[(True, False)] == pytest.approx(0.25)  # fore follows hind on the same side


def test_trot_moves_diagonal_pairs_together():
    legs = _legs(2)
    by = {(l["left"], l["front"]): p for l, p in zip(legs, gait.leg_phases(legs, "trot"))}
    assert by[(True, True)] == by[(False, False)]
    assert by[(False, True)] == by[(True, False)]
    assert by[(True, True)] != by[(False, True)]


def test_six_legs_use_an_alternating_tripod():
    legs = _legs(3)
    assert gait.gait_for(6) == "tripod"
    a, b = gait.tripod_groups(legs)
    assert len(a) == 3 and len(b) == 3
    for group in (a, b):
        sides = [legs[i]["left"] for i in group]
        assert sides.count(True) in (1, 2) and sides.count(False) in (1, 2)
        # Front and back legs of one side go with the middle leg of the other side.
        ranks = gait.side_ranks(legs)
        left_ranks = sorted(ranks[i] for i in group if legs[i]["left"])
        right_ranks = sorted(ranks[i] for i in group if not legs[i]["left"])
        assert (left_ranks, right_ranks) in (([0, 2], [1]), ([1], [0, 2]))


def test_eight_legs_use_a_wave_gait_that_moves_every_leg():
    legs = _legs(4)
    assert gait.gait_for(8) == "wave"
    ph = gait.leg_phases(legs, "wave")
    assert len(ph) == 8
    left = sorted((l["fwd"], p) for l, p in zip(legs, ph) if l["left"])
    # Back to front: the wave runs from the rear leg forward, a quarter cycle apart.
    steps = [round((b[1] - a[1]) % 1.0, 6) for a, b in zip(left, left[1:])]
    assert steps == [0.25, 0.25, 0.25]
    right = {l["fwd"]: p for l, p in zip(legs, ph) if not l["left"]}
    for fwd, p in left:
        assert (right[fwd] - p) % 1.0 == pytest.approx(0.5)  # the two sides are half a cycle apart
    # At any moment some legs are in the air and most are on the ground.
    for i in range(20):
        lifts = [gait.foot_cycle(i / 20 + p, gait.DUTY["wave"])[1] for p in ph]
        assert sum(1 for x in lifts if x == 0.0) >= 4


def test_run_is_faster_and_longer_than_walk():
    assert gait.clip_seconds("run", "quadruped") == pytest.approx(gait.clip_seconds("walk", "quadruped") / gait.RUN_FREQ)
    assert gait.RUN_FREQ == 1.8 and gait.RUN_STRIDE == 1.4
    assert gait.gait_for(4, run=True) == "gallop"
    assert gait.gait_for(8, run=True) == "alternating"


def test_flap_downstroke_takes_forty_percent():
    assert gait.flap_curve(0.0) == pytest.approx(1.0)
    assert gait.flap_curve(0.4) == pytest.approx(-1.0)
    assert gait.flap_curve(1.0) == pytest.approx(1.0)
    samples = [gait.flap_curve(i / 1000) for i in range(1001)]
    falling = sum(1 for a, b in zip(samples, samples[1:]) if b < a)
    assert falling / 1000 == pytest.approx(0.4, abs=0.01)


def test_serpent_wave_travels_head_to_tail_with_rising_amplitude():
    n, amp = 12, 10.0
    # Amplitude grows from 0.2 to 1.0 of the maximum toward the tail.
    peak = [max(abs(gait.wave_angle(i / (n - 1), t / 200, amp)) for t in range(200)) for i in range(n)]
    assert peak[0] == pytest.approx(0.2 * amp, rel=0.02)
    assert peak[-1] == pytest.approx(amp, rel=0.02)
    assert all(b >= a - 1e-9 for a, b in zip(peak, peak[1:]))

    # A crest reaches a joint nearer the tail later: phase lag grows along the body.
    def crest_time(u):
        return max(range(400), key=lambda k: gait.wave_angle(u, k / 400, amp)) / 400

    lag = (crest_time(0.5) - crest_time(0.25)) % 1.0
    assert lag == pytest.approx(0.25 * 1.25 % 1.0, abs=0.01)
    assert len(gait.wave_angles(n, 0.3, amp)) == n


def test_wave_amplitude_shrinks_with_more_joints():
    assert gait.wave_amplitude_deg(8) > gait.wave_amplitude_deg(24) > 0


def test_envelope_holds_ends_and_eases_between_keys():
    keys = [(0.0, 0.0), (0.5, 1.0), (1.0, 0.0)]
    assert gait.envelope(-1, keys) == 0.0
    assert gait.envelope(0.5, keys) == pytest.approx(1.0)
    assert gait.envelope(0.25, keys) == pytest.approx(0.5)
    assert gait.envelope(2, keys) == 0.0


def test_default_clips_follow_the_plan_table():
    assert gait.DEFAULT_CLIPS["quadruped"] == ["idle", "walk", "run", "turn_left", "turn_right", "attack", "hit", "death"]
    assert gait.DEFAULT_CLIPS["multileg"] == gait.DEFAULT_CLIPS["quadruped"]
    assert gait.DEFAULT_CLIPS["winged"] == ["idle", "walk", "flap", "glide", "takeoff", "land", "attack", "hit", "death"]
    assert gait.DEFAULT_CLIPS["serpent"] == ["idle", "slither", "turn_left", "turn_right", "attack", "hit", "death"]
    assert gait.DEFAULT_CLIPS["none"] == []


def test_resolve_motion_aliases_and_leg_counts():
    assert gait.resolve_motion(None, 4) == "quadruped"
    assert gait.resolve_motion("legs", 2) == "biped"
    assert gait.resolve_motion("legs", 8) == "multileg"
    assert gait.resolve_motion("auto", 0) == "unknown"
    assert gait.resolve_motion("wings", 4) == "winged"
    assert gait.resolve_motion("wave", 0) == "serpent"
    assert gait.resolve_motion("library", 2) == "biped"
    with pytest.raises(ValueError):
        gait.resolve_motion("hover", 2)


def test_resolve_clips_defaults_aliases_and_order():
    assert gait.resolve_clips("quadruped", None, legacy=True) == (["idle", "walk"], [])
    assert gait.resolve_clips("serpent", None)[0] == gait.DEFAULT_CLIPS["serpent"]
    clips, unknown = gait.resolve_clips("winged", "death, fly,turn, Idle,moonwalk")
    assert clips == ["idle", "flap", "turn_left", "turn_right", "death"]
    assert unknown == ["moonwalk"]
    assert gait.resolve_clips("biped", ["die", "get-hit"])[0] == ["hit", "death"]


def test_clip_supported_needs_wings_and_a_spine():
    assert not gait.clip_supported("flap", "winged", 4, False, 5)
    assert gait.clip_supported("flap", "winged", 4, True, 5)
    assert not gait.clip_supported("slither", "serpent", 0, False, 2)
    assert gait.clip_supported("attack", "serpent", 0, False, 2)


def test_clip_table_marks_loops_and_holds_death():
    rows = {r["name"]: r for r in gait.clip_table(["idle", "walk", "run", "turn_left", "attack", "hit", "death", "flap", "glide",
                                                    "takeoff", "land", "slither"], "quadruped", 24)}
    for name in ("idle", "walk", "run", "flap", "glide", "slither"):
        assert rows[name]["loop"] is True
    for name in ("turn_left", "attack", "hit", "death", "takeoff", "land"):
        assert rows[name]["loop"] is False
    d = rows["death"]
    assert d["hold_last"] and d["frame_end"] == d["motion_frames"] + round(gait.DEATH_HOLD * 24)
    assert rows["walk"]["frame_start"] == 0 and rows["walk"]["frame_end"] == 24 and rows["walk"]["frames"] == 25
    assert rows["attack"]["seconds"] == pytest.approx(0.8, abs=1 / 24)
    assert rows["hit"]["seconds"] == pytest.approx(0.4, abs=1 / 24)
    assert all(r["in_place"] for r in rows.values())


def test_move_speed_and_size_factors():
    assert gait.factor("big", gait.MOVE_SIZE) == 1.3
    assert gait.factor("slow", gait.MOVE_SPEED) == 0.8
    assert gait.factor("1.1", gait.MOVE_SIZE) == pytest.approx(1.1)
    assert gait.factor(None, gait.MOVE_SIZE) == 1.0
    assert gait.factor("weird", gait.MOVE_SPEED) == 1.0
    slow = gait.clip_frames("walk", "quadruped", 24, gait.MOVE_SPEED["slow"])[0]
    fast = gait.clip_frames("walk", "quadruped", 24, gait.MOVE_SPEED["fast"])[0]
    assert slow > 24 > fast
    assert math.isclose(gait.MOVE_SIZE["small"], 0.7)
