"""Rig check `fingers`: finger bones per hand from the mapped bone names (stages/rig.py fingers_check)."""
from vgac.stages import rig

P = "mixamorig:"
BODY = [P + n for n in ("Hips", "Spine", "LeftArm", "LeftForeArm", "LeftHand", "RightArm", "RightForeArm", "RightHand")]


def names(per_hand: dict[str, int]) -> list[str]:
    """Body plus, on each hand, the given fingers with that many bones each (e.g. {"Thumb": 3, "Index": 3})."""
    out = list(BODY)
    for side in ("Left", "Right"):
        for f, n in per_hand.items():
            out += [f"{P}{side}Hand{f}{k}" for k in range(1, n + 1)]
    return out


ALL = {"Thumb": 3, "Index": 3, "Middle": 3, "Ring": 3, "Pinky": 3}


def test_zero_bones():
    c = rig.fingers_check(names({}))
    assert not c["passed"] and c["value"] == 0 and c["severity"] == "warn"
    assert "left hand has no finger bones" in c["help"] and "right hand has no finger bones" in c["help"]
    assert "cannot close into a fist" in c["help"]


def test_twelve_bones_index_and_thumb():
    c = rig.fingers_check(names({"Thumb": 3, "Index": 3}))
    assert not c["passed"] and c["value"] == 12
    assert "only the thumb and index finger" in c["help"]
    assert "missing the middle finger, ring finger and little finger" in c["help"]


def test_eighteen_bones_three_fingers():
    c = rig.fingers_check(names({"Thumb": 3, "Index": 3, "Middle": 3}))
    assert not c["passed"] and c["value"] == 18
    assert "missing the ring finger and little finger" in c["help"]


def test_thirty_bones_full():
    c = rig.fingers_check(names(ALL))
    assert c["passed"] and c["value"] == 30 and "help" in c and "fix" not in c


def test_forty_bones_four_bone_fingers_still_full():
    c = rig.fingers_check(names({f: 4 for f in ALL}))
    assert c["passed"] and c["value"] == 30


def test_one_hand_short_names_that_hand_only():
    n = names(ALL)
    n = [x for x in n if not x.startswith(P + "RightHandPinky")]
    c = rig.fingers_check(n)
    assert not c["passed"] and c["value"] == 27
    assert "right hand has only" in c["help"] and "left hand has" not in c["help"].lower().replace("the left hand has all", "")


def test_two_bone_finger_is_short():
    n = [x for x in names(ALL) if x != P + "LeftHandRing3"]
    c = rig.fingers_check(n)
    assert not c["passed"] and "missing the ring finger" in c["help"]


def test_not_a_humanoid_skeleton_has_no_check():
    assert rig.fingers_check([P + "Hips", P + "Spine"]) is None
    assert rig.fingers_check(None) is None


def seg(parent, name):
    return {"name": name, "parent": parent, "head": [0, 0, 0]}


def test_single_chain_hand_blames_the_model():
    segs = [seg(None, P + "LeftHand"), seg(None, P + "RightHand")] + [seg(P + "LeftHand", "x1"), seg("x1", "x2")] + [seg(P + "RightHand", "y1")]
    c = rig.fingers_check(names({}), segs)
    assert "The cause is the model's hands, not the skeleton" in c["help"]
    assert c["fix"]["stage"] == "mesh" and c["fix"]["params"] == {"hand_edit": True}


def test_no_chain_hand_points_to_rig_again_and_model():
    c = rig.fingers_check(names({"Thumb": 3, "Index": 3}), [seg(None, P + "LeftHand")])
    assert "not the skeleton" not in c["help"] and c["fix"]["stage"] == "rig"
    assert "usually came out the same" in c["help"]
