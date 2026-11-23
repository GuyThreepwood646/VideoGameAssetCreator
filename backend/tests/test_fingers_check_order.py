"""The rig check `fingers` also needs the fingers named in lane order from the thumb and every chain on the hand's skin (built from the stored
W1 R4 hands, whose fingers were named out of order)."""
from __future__ import annotations

from test_finger_roles import LEFT, RIGHT, _roles
from vgac.stages import rig

P = "mixamorig:"


def _segments(left_rows, right_rows):
    segs = []
    for rows in (left_rows, right_rows):
        for name, parent, head, tail in rows:
            segs.append({"name": P + name, "parent": (P + parent) if parent else None, "head": list(head), "tail": list(tail)})
    return segs


def _renamed(rows, side):
    """The stored hand with its four fingers named by lane from the thumb (what rig_fix names them now)."""
    roles = _roles(rows, side)  # new role -> stored finger
    back = {stored: role[len(side + "Hand"):-1] for role, stored in roles.items()}  # stored finger -> new finger
    out = []
    for name, parent, head, tail in rows:
        def fix(n):
            if n is None or not n.startswith(side + "Hand") or n == side + "Hand":
                return n
            finger, idx = n[len(side + "Hand"):-1], n[-1]
            return f"{side}Hand{back[finger]}{idx}"
        out.append((fix(name), fix(parent), head, tail))
    return out


def _names(segs):
    return [s["name"] for s in segs]


def test_five_fingers_named_out_of_order_is_a_fault_even_with_thirty_bones():
    segs = _segments(LEFT, RIGHT)
    c = rig.fingers_check(_names(segs), segs)
    assert c["value"] == 30 and not c["passed"] and c["severity"] == "warn"
    assert "named out of order" in c["help"] and "hands are" in c["help"]
    assert c["vars"]["wrong_order"]["left"] and c["vars"]["wrong_order"]["right"]
    assert c["fix"]["stage"] == "rig"


def test_the_same_hands_named_by_lane_pass():
    segs = _segments(_renamed(LEFT, "Left"), _renamed(RIGHT, "Right"))
    c = rig.fingers_check(_names(segs), segs)
    assert c["passed"] and c["value"] == 30 and "out of order" not in c["help"]
    assert rig.finger_order_found(segs, "Left") == ["Index", "Middle", "Ring", "Pinky"]
    assert rig.finger_order_found(segs, "Right") == ["Index", "Middle", "Ring", "Pinky"]


def test_one_hand_out_of_order_names_that_hand_only():
    segs = _segments(_renamed(LEFT, "Left"), RIGHT)
    c = rig.fingers_check(_names(segs), segs)
    assert not c["passed"] and "right hand are named out of order" in c["help"] and "left" not in c["vars"]["wrong_order"]


def test_a_chain_off_the_skin_is_a_fault_and_a_near_one_is_not():
    segs = _segments(_renamed(LEFT, "Left"), _renamed(RIGHT, "Right"))
    # the values are how far a chain's worst joint lies outside the skin (0 inside it); up to 1.5 cm is on the hand
    ok = rig.fingers_check(_names(segs), segs, None, {"Left": {"Thumb": 0.0, "Index": 1.0}, "Right": {"Pinky": 1.5}})
    assert ok["passed"]
    assert not rig.fingers_check(_names(segs), segs, None, {"Right": {"Pinky": 2.0}})["passed"]
    bad = rig.fingers_check(_names(segs), segs, None, {"Left": {"Thumb": 6.5}})
    assert not bad["passed"] and "outside the hand's skin (up to 6 cm away)" in bad["help"] and "left hand lies" in bad["help"]
    assert rig.fingers_check(_names(segs), segs, None, None)["passed"]  # an older rig without the measurement is not failed for it


def test_an_incomplete_hand_keeps_the_missing_fingers_message():
    segs = _segments(_renamed(LEFT, "Left"), _renamed(RIGHT, "Right"))
    names = [n for n in _names(segs) if "HandPinky" not in n]
    c = rig.fingers_check(names, segs)
    assert not c["passed"] and "missing the little finger" in c["help"] and "out of order" not in c["help"]
