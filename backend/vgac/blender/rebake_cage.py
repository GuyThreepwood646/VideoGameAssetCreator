"""Cage (ray start) distance for the re-bake: pure maths, no bpy, so the tests can import it.

rebake.py sets the Cycles selected-to-active cage extrusion from this. The maximum ray distance stays at its own share
of the diagonal (rebake.py).
"""

# Share of the target's bounding-box diagonal the rays start off the surface by (today's value, every category).
CAGE_SHARE = 0.01

HUMANOID_CAGE_SCALE = 0.5  # humanoid only: PASS_MARK_2 confirm run, 2026-11-21 (knight stripes fell; colour improved)
HUMANOID_CATEGORY = "humanoid"


def cage_extrusion_for(category, diag):
    """The cage extrusion in metres for a re-bake of an asset in `category` whose target diagonal is `diag` metres.
    Every category other than humanoid (and any call without a category) keeps the share of the diagonal it has always had."""
    scale = HUMANOID_CAGE_SCALE if category == HUMANOID_CATEGORY else 1.0
    return CAGE_SHARE * scale * diag
