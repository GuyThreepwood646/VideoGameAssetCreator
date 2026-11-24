"""The attack-grip guard for built-in clips: the rule that picks how far the attack grip closes.

The attack hand closes into a grip over the strike. With the curled resting hand, that grip can push many hand edges
past 1.3 times their rest length. The rig animate step measures the built-in attack's hands with the skin-stretch
measure. When that share reaches GRIP_BAR, it rebuilds the attack with the grip scale stepped down through GRIP_STEPS
and keeps the first step under the bar. At the last step it keeps that step and returns a warning. Rigs under the bar
are never rebuilt, so their clips stay byte-identical. Library attacks replace the built-in one at the retarget and
are not guarded here.

This module only chooses scales. The animate step runs the measure and the builds (rig.py, _grip_guard).
"""

GRIP_BAR = 0.07  # hands over-share on the attack at or above this triggers a rebuild
GRIP_STEPS = (0.75, 0.5, 0.35)  # grip scales tried in order; the last one is the floor
DEFAULT_SCALE = 1.0  # the grip as designed (procanim --grip-scale 1)


def trial_scales(first_share, bar=GRIP_BAR, steps=GRIP_STEPS):
    """The grip scales to try after the attack measured at the default grip: none when the hands are under the bar
    (or there are no hand edges to measure), otherwise every step in order."""
    if first_share is None or first_share < bar:
        return ()
    return tuple(steps)


def choose(trials, bar=GRIP_BAR, steps=GRIP_STEPS):
    """The grip scale to keep and a warning (or None). trials is the (scale, hands over-share) list in the order it was
    measured. The first trial under the bar wins; a missing share counts as under the bar, because there are no hand
    edges to tear. With no trials the grip stays at the default. At the floor, a share still at or over the bar keeps
    the floor and returns the warning."""
    if not trials:
        return DEFAULT_SCALE, None
    for scale, share in trials:
        if share is None or share < bar:
            return scale, None
    floor = steps[-1]
    last_share = trials[-1][1]
    return floor, (f"The attack's hands still stretch past the bar at the grip floor {floor:g} "
                   f"(share {last_share:.3f}, bar {bar:g}); the hand may tear on attack.")
