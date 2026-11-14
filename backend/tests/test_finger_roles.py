"""Finger roles in rig_fix: the four fingers are named by lane from the thumb (rig_fix.finger_lane_order), built from the stored W1 R4
rig, whose fingers had been named out of order (the old rule ordered them by how far forward their bases sit)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np

RF_PATH = Path(__file__).resolve().parent.parent / "vgac" / "blender" / "rig_fix.py"
_spec = importlib.util.spec_from_file_location("vgac_rig_fix_fingers", RF_PATH)
rf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rf)

# (name, parent, head, tail) of the arm and hand bones of the stored W1 R4 rig (Blender space, palm forward), names as stored.
LEFT = [
    ('LeftShoulder', None, (0.06, 0.039, 1.438), (0.105, 0.044, 1.546)),
    ('LeftArm', 'LeftShoulder', (0.172, 0.039, 1.403), (0.412, 0.039, 1.328)),
    ('LeftForeArm', 'LeftArm', (0.285, 0.039, 1.178), (0.397, 0.039, 0.952)),
    ('LeftHand', 'LeftForeArm', (0.432, 0.039, 0.974), (0.457, 0.039, 0.94)),
    ('LeftHandThumb1', 'LeftHand', (0.468, 0.032, 0.953), (0.5, 0.025, 0.933)),
    ('LeftHandThumb2', 'LeftHandThumb1', (0.503, 0.025, 0.939), (0.529, 0.019, 0.928)),
    ('LeftHandThumb3', 'LeftHandThumb2', (0.531, 0.018, 0.939), (0.559, 0.011, 0.939)),
    ('LeftHandIndex1', 'LeftHand', (0.51, 0.046, 0.882), (0.527, 0.047, 0.862)),
    ('LeftHandIndex2', 'LeftHandIndex1', (0.524, 0.039, 0.861), (0.538, 0.032, 0.84)),
    ('LeftHandIndex3', 'LeftHandIndex2', (0.545, 0.032, 0.847), (0.566, 0.025, 0.833)),
    ('LeftHandMiddle1', 'LeftHand', (0.447, 0.046, 0.861), (0.449, 0.047, 0.838)),
    ('LeftHandMiddle2', 'LeftHandMiddle1', (0.454, 0.039, 0.84), (0.458, 0.034, 0.826)),
    ('LeftHandMiddle3', 'LeftHandMiddle2', (0.454, 0.032, 0.826), (0.454, 0.025, 0.812)),
    ('LeftHandRing1', 'LeftHand', (0.475, 0.046, 0.854), (0.482, 0.047, 0.832)),
    ('LeftHandRing2', 'LeftHandRing1', (0.482, 0.039, 0.833), (0.489, 0.032, 0.812)),
    ('LeftHandRing3', 'LeftHandRing2', (0.489, 0.032, 0.812), (0.496, 0.025, 0.791)),
    ('LeftHandPinky1', 'LeftHand', (0.496, 0.046, 0.861), (0.509, 0.047, 0.838)),
    ('LeftHandPinky2', 'LeftHandPinky1', (0.51, 0.039, 0.84), (0.522, 0.033, 0.822)),
    ('LeftHandPinky3', 'LeftHandPinky2', (0.517, 0.039, 0.819), (0.524, 0.039, 0.798)),
]
RIGHT = [
    ('RightShoulder', None, (-0.053, 0.039, 1.438), (-0.098, 0.044, 1.546)),
    ('RightArm', 'RightShoulder', (-0.165, 0.039, 1.403), (-0.405, 0.039, 1.328)),
    ('RightForeArm', 'RightArm', (-0.278, 0.039, 1.178), (-0.39, 0.039, 0.952)),
    ('RightHand', 'RightForeArm', (-0.425, 0.039, 0.974), (-0.45, 0.039, 0.94)),
    ('RightHandThumb1', 'RightHand', (-0.461, 0.032, 0.953), (-0.493, 0.025, 0.933)),
    ('RightHandThumb2', 'RightHandThumb1', (-0.496, 0.025, 0.939), (-0.522, 0.019, 0.928)),
    ('RightHandThumb3', 'RightHandThumb2', (-0.524, 0.018, 0.939), (-0.552, 0.011, 0.939)),
    ('RightHandIndex1', 'RightHand', (-0.439, 0.046, 0.861), (-0.442, 0.047, 0.838)),
    ('RightHandIndex2', 'RightHandIndex1', (-0.447, 0.039, 0.84), (-0.451, 0.034, 0.826)),
    ('RightHandIndex3', 'RightHandIndex2', (-0.447, 0.032, 0.826), (-0.447, 0.025, 0.812)),
    ('RightHandMiddle1', 'RightHand', (-0.503, 0.046, 0.882), (-0.52, 0.047, 0.862)),
    ('RightHandMiddle2', 'RightHandMiddle1', (-0.517, 0.039, 0.861), (-0.528, 0.033, 0.844)),
    ('RightHandMiddle3', 'RightHandMiddle2', (-0.531, 0.032, 0.847), (-0.545, 0.025, 0.833)),
    ('RightHandRing1', 'RightHand', (-0.468, 0.046, 0.861), (-0.478, 0.047, 0.833)),
    ('RightHandRing2', 'RightHandRing1', (-0.475, 0.039, 0.833), (-0.48, 0.033, 0.811)),
    ('RightHandRing3', 'RightHandRing2', (-0.482, 0.032, 0.812), (-0.489, 0.025, 0.791)),
    ('RightHandPinky1', 'RightHand', (-0.482, 0.046, 0.868), (-0.497, 0.048, 0.84)),
    ('RightHandPinky2', 'RightHandPinky1', (-0.496, 0.039, 0.84), (-0.507, 0.033, 0.818)),
    ('RightHandPinky3', 'RightHandPinky2', (-0.51, 0.039, 0.819), (-0.524, 0.039, 0.798)),
]


def _roles(rows, side):
    """Role map from generic bone names: every finger bone renamed to c<n> so only the geometry can decide."""
    generic = {}
    for name, parent, head, tail in rows:
        short = name[len(side):]
        generic[name] = name if short in ("Shoulder", "Arm", "ForeArm", "Hand") else "c" + name[len(side + "Hand"):]
    bones = [{"name": generic[n], "parent": generic.get(p), "head": h, "tail": t} for n, p, h, t in rows]
    bs = rf.Bones(bones)
    roles, why = rf._arm_roles(bs, generic[side + "Shoulder"], side, np.array([0.0, -1.0, 0.0]))
    assert why is None
    # which stored finger each role was given to
    stored = {v: k for k, v in generic.items()}
    return {role: stored[b][len(side + "Hand"):-1] for role, b in roles.items() if role.startswith(side + "Hand") and role.endswith("1")}


def test_the_thumb_is_the_chain_nearest_the_wrist_and_the_fingers_follow_in_lane_order():
    # Stored names, from the thumb side across the hand: left Index, Pinky, Ring, Middle; right Middle, Pinky, Ring, Index
    # (found by looking at the hand from the front). Corrected names are Index, Middle, Ring, Pinky in that order.
    left = _roles(LEFT, "Left")
    assert left == {"LeftHandThumb1": "Thumb", "LeftHandIndex1": "Index", "LeftHandMiddle1": "Pinky", "LeftHandRing1": "Ring", "LeftHandPinky1": "Middle"}
    right = _roles(RIGHT, "Right")
    assert right == {"RightHandThumb1": "Thumb", "RightHandIndex1": "Middle", "RightHandMiddle1": "Pinky", "RightHandRing1": "Ring", "RightHandPinky1": "Index"}


def test_the_order_does_not_depend_on_how_the_hand_is_turned():
    # Turn the whole stored left hand a quarter turn about the arm's own direction (palm forward to palm down): same order.
    rows = LEFT
    arm_dir = np.array(rows[3][2]) - np.array(rows[2][2])
    arm_dir = arm_dir / np.linalg.norm(arm_dir)
    pivot = np.array(rows[3][2])
    c, s = np.cos(np.pi / 2), np.sin(np.pi / 2)
    K = np.array([[0, -arm_dir[2], arm_dir[1]], [arm_dir[2], 0, -arm_dir[0]], [-arm_dir[1], arm_dir[0], 0]])
    R = np.eye(3) * c + s * K + (1 - c) * np.outer(arm_dir, arm_dir)
    turned = []
    for n, p, h, t in rows:
        if n.startswith("LeftHand") or n == "LeftHand":
            h = tuple(pivot + R @ (np.array(h) - pivot))
            t = tuple(pivot + R @ (np.array(t) - pivot))
        turned.append((n, p, h, t))
    assert _roles(turned, "Left") == _roles(LEFT, "Left")


def test_lane_order_of_two_or_one_finger_and_a_degenerate_hand():
    bs = rf.Bones([{"name": "hand", "parent": None, "head": (0, 0, 0)}, {"name": "a", "parent": "hand", "head": (0.0, 0.0, -0.1), "tail": (0.0, 0.0, -0.2)}])
    assert rf.finger_lane_order(bs, bs.pos["hand"], "a", [], {"a": ["a"]}, np.array([0, -1.0, 0])) == []


def _rotated(rows, R, origin):
    out = []
    for n, p, h, t in rows:
        out.append((n, p, tuple(origin + R @ (np.array(h) - origin)), tuple(origin + R @ (np.array(t) - origin))))
    return out


def _rot(axis, deg):
    a = np.array(axis, float)
    a /= np.linalg.norm(a)
    c, s = np.cos(np.radians(deg)), np.sin(np.radians(deg))
    K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) * c + s * K + (1 - c) * np.outer(a, a)


def test_the_order_holds_for_a_hand_held_in_front_behind_or_beside_the_body():
    # The whole arm turned about the shoulder: raised to the front, swung behind the back, hanging beside the hip, across the
    # chest and a few odd angles. The order is read in each hand's own frame (the wrist, the fingers, the thumb), never against
    # the body's front or sides, so the names are the same wherever the hand is.
    origin = np.array(LEFT[0][2], float)  # the shoulder
    want = _roles(LEFT, "Left")
    for axis, deg in (((0, 1, 0), 90), ((0, 1, 0), -90), ((0, 0, 1), 90), ((0, 0, 1), 180), ((1, 0, 0), 90), ((1, 0, 0), -90), ((1, 1, 0), 135), ((1, -2, 3), 200)):
        assert _roles(_rotated(LEFT, _rot(axis, deg), origin), "Left") == want, (axis, deg)
    wantr = _roles(RIGHT, "Right")
    origin_r = np.array(RIGHT[0][2], float)
    for axis, deg in (((0, 1, 0), 90), ((0, 0, 1), 180), ((1, 0, 0), -90), ((1, 2, -3), 77)):
        assert _roles(_rotated(RIGHT, _rot(axis, deg), origin_r), "Right") == wantr, (axis, deg)
