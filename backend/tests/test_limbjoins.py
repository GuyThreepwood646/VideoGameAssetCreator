"""Limb joins (vgac/limbjoins.py, stages/rig.py limb_joins_check) on a synthetic figure built from voxels, so its
surface is one closed shell like a 3D build's: free limbs, upper arms grown into the body, legs pressed together knee
to knee, a robe; plus the GLB reader, the readings and the words."""

from __future__ import annotations

import json
import re
import struct

import numpy as np
import pytest

from vgac import limbjoins as lj
from vgac.stages import rig

VOX = 0.01  # metres per voxel; the figure is 1.8 m tall


def _box(occ, x0, x1, y0, y1, z0=-0.1, z1=0.1):
    def ix(v, lo):
        return int(round((v - lo) / VOX))
    occ[ix(x0, -0.4):ix(x1, -0.4), ix(y0, 0.0):ix(y1, 0.0), ix(z0, -0.15):ix(z1, -0.15)] = True


def _surface(occ):
    """Closed triangle surface of the filled voxels (faces between filled and empty), welded; y up."""
    nx, ny, nz = occ.shape
    pad = np.zeros((nx + 2, ny + 2, nz + 2), bool)
    pad[1:-1, 1:-1, 1:-1] = occ
    quads = []
    corners = {  # axis, direction -> the four corner offsets of the face, counter-clockwise from outside
        (0, 1): [(1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)], (0, -1): [(0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)],
        (1, 1): [(0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)], (1, -1): [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)],
        (2, 1): [(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)], (2, -1): [(0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0)],
    }
    for (axis, d), cs in corners.items():
        sh = [0, 0, 0]
        sh[axis] = d
        nb = pad[1 + sh[0]:nx + 1 + sh[0], 1 + sh[1]:ny + 1 + sh[1], 1 + sh[2]:nz + 1 + sh[2]]
        idx = np.argwhere(occ & ~nb)
        quads.append(np.stack([idx + np.array(c) for c in cs], 1))
    Q = np.concatenate(quads).reshape(-1, 3)
    uniq, inv = np.unique(Q, axis=0, return_inverse=True)
    inv = inv.reshape(-1, 4)
    F = np.concatenate([inv[:, [0, 1, 2]], inv[:, [0, 2, 3]]])
    V = uniq * VOX + np.array([-0.4, 0.0, -0.15])
    return V.astype(float), F.astype(np.int64)


def figure(arms_in=False, legs_together=False, robe=False):
    """A standing figure, the character's left on +x: torso, head, shoulders, arms hanging, legs."""
    occ = np.zeros((80, 182, 30), bool)
    _box(occ, -0.18, 0.18, 0.9, 1.5)  # torso
    _box(occ, -0.1, 0.1, 1.5, 1.8)  # head
    leg_x = 0.06 if legs_together else 0.13
    for s in (1, -1):
        ax0 = 0.18 if arms_in else 0.22  # upper arm against the torso, or 4 cm clear of it
        lo, hi = sorted((s * ax0, s * (ax0 + 0.08)))
        _box(occ, *sorted((s * 0.18, s * 0.30)), 1.45, 1.5)  # shoulder
        _box(occ, lo, hi, 0.95, 1.45)  # upper arm
        _box(occ, *sorted((s * 0.22, s * 0.30)), 0.48, 0.95)  # forearm and hand, clear of the legs
        _box(occ, *sorted((s * (leg_x - 0.06), s * (leg_x + 0.06))), 0.0, 0.9)  # leg
    if robe:
        _box(occ, -0.2, 0.2, 0.45, 0.9)
    V, F = _surface(occ)
    J = {"Hips": (0, 0.9, 0), "Spine": (0, 1.05, 0), "Spine1": (0, 1.2, 0), "Spine2": (0, 1.35, 0), "Neck": (0, 1.5, 0),
         "Head": (0, 1.6, 0)}
    for side, s in (("Left", 1), ("Right", -1)):
        ax = s * ((0.18 if arms_in else 0.22) + 0.04)
        J.update({f"{side}Arm": (ax, 1.44, 0), f"{side}ForeArm": (s * 0.26, 0.95, 0), f"{side}Hand": (s * 0.26, 0.62, 0),
                  f"{side}UpLeg": (s * leg_x, 0.88, 0), f"{side}Leg": (s * leg_x, 0.5, 0), f"{side}Foot": (s * leg_x, 0.08, 0)})
    return V, F, {k: np.array(v, float) for k, v in J.items()}


@pytest.fixture(scope="module")
def free():
    return lj.measure_arrays(*figure())


def _states(m, seg):
    return {m["limbs"][f"{s}_{seg}"]["state"] for s in ("Left", "Right")}


def test_free_figure_passes(free):
    assert free["measured"]
    for seg in ("upperarm", "forearm", "hand", "thigh"):
        assert _states(free, seg) == {"free"}, (seg, free["limbs"])
    c = rig.limb_joins_check(free)
    assert c["passed"] and c["severity"] == "warn" and c["id"] == "limb_joins" and "fix" not in c
    assert c["help"].startswith("The arms and legs are free")


def test_upper_arms_grown_into_the_body():
    m = lj.measure_arrays(*figure(arms_in=True))
    assert _states(m, "upperarm") == {"joined"}
    assert "body" in m["limbs"]["Left_upperarm"]["with"]
    c = rig.limb_joins_check(m)
    assert not c["passed"]
    assert c["help"].startswith("Both upper arms are grown into the body")
    assert "web" in c["help"] and "Pose for 3D first" in c["help"]
    assert c["fix"]["stage"] == "turnaround" and c["fix"]["params"] == {"pose_ready": True} and c["fix_label"]


def test_legs_pressed_together_knee_to_knee_are_a_fault_not_clothing():
    # The feet-apart guard of the clothing rule: untested on real models, so tested here.
    m = lj.measure_arrays(*figure(legs_together=True))
    assert m["feet_apart"] < lj.FEET_APART
    assert all(m["limbs"][f"{s}_thigh"]["reach"] >= lj.KNEE_REACH for s in ("Left", "Right"))
    assert _states(m, "thigh") == {"joined"}
    c = rig.limb_joins_check(m)
    assert not c["passed"] and "thighs are joined to each other" in c["help"] and "walk" in c["help"]


def test_a_robe_to_the_knee_is_clothing():
    m = lj.measure_arrays(*figure(robe=True))
    assert m["feet_apart"] >= lj.FEET_APART
    assert _states(m, "thigh") == {"clothing"}
    c = rig.limb_joins_check(m)
    assert c["passed"] and "robe, skirt, tunic or wide trousers" in c["help"]


def test_unmapped_skeleton_is_a_visible_note_not_a_silent_pass():
    V, F, J = figure()
    del J["RightHand"]
    m = lj.measure_arrays(V, F, J)
    assert m == {"measured": False, "reason": "unmapped", "missing": ["RightHand"]}
    c = rig.limb_joins_check(m, {"legs_parted": 4})
    assert c["passed"] and c["value"] == "not measured"
    assert c["help"].startswith("Not measured:") and "parted" in c["help"]


def test_broken_arm_bones_are_not_measured_with_the_reason(free):
    V, F, J = figure()
    J["LeftForeArm"] = J["LeftArm"] + np.array([0.0, -0.03, 0.0])  # squashed at the shoulder
    J["LeftHand"] = J["LeftArm"] + np.array([0.0, -0.06, 0.0])
    m = lj.measure_arrays(V, F, J)
    assert m["limbs"]["Left_upperarm"]["state"] == "unmeasured" and m["limbs"]["Left_upperarm"]["why"] == "short_bone"
    c = rig.limb_joins_check(m)
    assert c["passed"] and c["help"].startswith("The measured arms and legs")
    assert "Not measured: the left arm, because the skeleton's bones there are too short" in c["help"]


def test_no_check_without_the_metric():
    assert rig.limb_joins_check(None) is None


def test_reach_reads_a_single_free_cut_as_noise():
    assert lj.reach([True] * 5 + [False] * 11) == 5
    assert lj.reach([False] + [True] * 14 + [False]) == 15
    assert lj.reach([True] * 6 + [False] + [True] * 3 + [False] * 6) == 10
    assert lj.reach([True] * 6 + [False] * 6 + [True] + [False] * 3) == 6
    assert lj.reach([False] * 16) == 0


def _limbs(**states):
    base = {f"{s}_{g}": {"state": "free", "reach": 0, "joined": 0, "with": []} for s in ("Left", "Right")
            for g in ("upperarm", "forearm", "hand", "thigh")}
    for k, v in states.items():
        base[k].update(v)
    return {"measured": True, "limbs": base, "feet_apart": 0.5}


def test_words_have_no_numbers_and_say_partly_on_the_line():
    m = _limbs(Right_upperarm={"state": "joined", "reach": lj.ARM_REACH, "partly": True, "with": ["body"]},
               Left_thigh={"state": "joined", "reach": 9, "with": ["other_leg"]},
               Right_thigh={"state": "joined", "reach": 9, "with": ["other_leg"]},
               Left_hand={"state": "joined", "joined": 4, "with": ["leg"]})
    v = lj.check_values(m)
    text = v["help"]
    assert text.startswith("The right upper arm is partly grown into the body")
    assert "The left hand is grown into the leg." in text and "The thighs are joined to each other" in text
    assert not re.search(r"\d", text.replace("3D", "").replace("step 2", "")), text
    cape = lj.check_values(_limbs(Left_upperarm={"state": "joined", "reach": 14, "with": ["other"]}))
    assert "something it wears or carries (a cape, cloak, hair or an item)" in cape["help"]


def _glb(V, F, joints):
    """A minimal skinned GLB: one mesh, one skin whose joints sit at the given positions (no rotation)."""
    names = list(joints)
    pos = np.asarray(V, np.float32).tobytes()
    idx = np.asarray(F, np.uint32).tobytes()
    ibm = np.stack([np.linalg.inv(np.block([[np.eye(3), np.asarray(joints[n], float)[:, None]], [np.zeros((1, 3)), np.ones((1, 1))]])).T
                    for n in names]).astype(np.float32).tobytes()
    bin_ = pos + idx + ibm
    nodes = [{"name": f"mixamorig:{n}", "translation": [float(x) for x in joints[n]]} for n in names]
    nodes.append({"name": "body", "mesh": 0, "skin": 0})
    js = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": list(range(len(nodes)))}], "nodes": nodes,
          "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
          "skins": [{"joints": list(range(len(names))), "inverseBindMatrices": 2}],
          "buffers": [{"byteLength": len(bin_)}],
          "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(pos)},
                          {"buffer": 0, "byteOffset": len(pos), "byteLength": len(idx)},
                          {"buffer": 0, "byteOffset": len(pos) + len(idx), "byteLength": len(ibm)}],
          "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(V), "type": "VEC3",
                         "min": np.asarray(V).min(0).tolist(), "max": np.asarray(V).max(0).tolist()},
                        {"bufferView": 1, "componentType": 5125, "count": int(np.asarray(F).size), "type": "SCALAR"},
                        {"bufferView": 2, "componentType": 5126, "count": len(names), "type": "MAT4"}]}
    jb = json.dumps(js).encode()
    jb += b" " * (-len(jb) % 4)
    bin_ += b"\0" * (-len(bin_) % 4)
    body = struct.pack("<II", len(jb), 0x4E4F534A) + jb + struct.pack("<II", len(bin_), 0x004E4942) + bin_
    return b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body


def test_reads_a_rigged_glb_and_never_raises(tmp_path, free):
    V, F, J = figure()
    p = tmp_path / "rig.glb"
    p.write_bytes(_glb(V, F, J))
    V2, F2, J2 = lj.load_rig(p)
    assert len(F2) == len(F) and set(J2) == set(J)
    assert np.allclose(J2["LeftHand"], J["LeftHand"], atol=1e-5)
    m = lj.measure(p)
    assert m["measured"] and m["seconds"] >= 0 and m["limbs"] == free["limbs"]
    bad = tmp_path / "bad.glb"
    bad.write_bytes(b"not a glb")
    assert lj.measure(bad)["reason"] == "error"
