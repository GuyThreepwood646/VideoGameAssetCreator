"""Finger limits for the Unity Humanoid description (written by VgacImport when asked: -vgacFingerLimits <finger_limits.json>).

Unity plays the shared library clips on a Humanoid avatar through muscle values. The library closes every finger joint by about 77 degrees
(a tight fist on our shorter fingers), and the rig step's own finger cap (stages/rig.py finger_cap, default 40) only reaches the moves
baked into the character's own FBX, which a valid Humanoid does not play. A HumanBone limit rescales a muscle: with a finger joint's stretch
range set to +-cap degrees, the library's full curl closes the joint by `cap` degrees on this avatar, the same size the app's own preview
shows. Pure data, so the backend tests can check what the importer will write without Unity.

What is assumed and has to be confirmed in Unity (nobody runs it here): the bone names are Unity's HumanTrait.BoneName strings; a finger
joint curls about its local Z axis (Unity's "Stretched" muscle) and spreads about Y; the range is symmetric so the sign of flexion does not
matter (the cost is that backward bending is limited to `cap` too); the thumb is left on Unity's defaults because its axes differ.
"""
from __future__ import annotations

from typing import Any

DEFAULT_CAP = 40.0  # degrees per finger joint; the same as the rig step's finger_cap default (tests keep them equal)
SPREAD = 20.0  # sideways range of a finger joint in degrees, either way
SIDES = ("Left", "Right")
FINGERS = ("Index", "Middle", "Ring", "Little")  # Unity's name for the little finger
JOINTS = ("Proximal", "Intermediate", "Distal")


def cap_from_params(params: dict[str, Any] | None) -> float | None:
    """The cap in degrees from a rig version's params (finger_cap): the default when the rig has none recorded, None when it was turned off
    (0, false or null recorded)."""
    p = params or {}
    if "finger_cap" not in p:
        return DEFAULT_CAP
    v = p["finger_cap"]
    if v is None or v is False:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return DEFAULT_CAP
    return v if v > 0 else None


def finger_limits(cap: float, spread: float = SPREAD) -> list[dict[str, Any]]:
    """The 24 finger joints (four fingers, three joints, two hands) as {bone: Unity's human bone name, min: [x, y, z], max: [x, y, z]} in degrees."""
    cap = float(cap)
    return [{"bone": f"{side} {finger} {joint}", "min": [0.0, -float(spread), -cap], "max": [0.0, float(spread), cap]}
            for side in SIDES for finger in FINGERS for joint in JOINTS]


def document(cap: float) -> dict[str, Any]:
    """The finger_limits.json the export writes next to the FBX."""
    return {"version": 1, "finger_cap": float(cap), "limits": finger_limits(cap)}
