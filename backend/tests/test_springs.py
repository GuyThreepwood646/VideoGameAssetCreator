"""Spring bones (docs/HAIR-RESEARCH.md 3.2 (f)): finding the parts that hang free on synthetic shapes, the chain and
weight rules, the spring simulation and its loop seam, springs.json, the check sentences and the hand-off."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vgac" / "blender"))
import rig_fix  # noqa: E402
import springs as sp  # noqa: E402

from vgac import guidance, quality, reference  # noqa: E402
from vgac import springs as springs_mod  # noqa: E402
from vgac.stages import export as export_stage  # noqa: E402
from vgac.stages import rig as rig_stage  # noqa: E402

# --- a synthetic character (Blender space: Z up, front -Y, left +X) ---------------------------------------------------


def tube(points, r, n=16):
    """A closed tube along a polyline (rings at each point, capped). -> (V, F)."""
    P = np.asarray(points, float)
    V, F = [], []
    for i, p in enumerate(P):
        d = P[min(i + 1, len(P) - 1)] - P[max(i - 1, 0)]
        d /= np.linalg.norm(d)
        a = np.cross(d, [1.0, 0.0, 0.0]) if abs(d[0]) < 0.9 else np.cross(d, [0.0, 1.0, 0.0])
        a /= np.linalg.norm(a)
        b = np.cross(d, a)
        for k in range(n):
            t = 2 * math.pi * k / n
            V.append(p + r * (math.cos(t) * a + math.sin(t) * b))
    for i in range(len(P) - 1):
        for k in range(n):
            a0, a1 = i * n + k, i * n + (k + 1) % n
            b0, b1 = a0 + n, a1 + n
            F += [[a0, b0, a1], [a1, b0, b1]]
    c0, c1 = len(V), len(V) + 1
    V += [P[0], P[-1]]
    last = (len(P) - 1) * n
    for k in range(n):
        F += [[c0, (k + 1) % n, k], [c1, last + k, last + (k + 1) % n]]
    return np.array(V), np.array(F)


def box(lo, hi):
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    V = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    F = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1], [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4],
                  [1, 5, 7], [1, 7, 3]])
    return V, F


def join(*parts):
    V, F, off = [], [], 0
    for v, f in parts:
        V.append(v)
        F.append(f + off)
        off += len(v)
    return np.vstack(V), np.vstack(F)


BONES = [
    ("Hips", (0, 0, 0.9), (0, 0, 1.0)), ("Spine", (0, 0, 1.0), (0, 0, 1.15)), ("Spine1", (0, 0, 1.15), (0, 0, 1.3)),
    ("Spine2", (0, 0, 1.3), (0, 0, 1.45)), ("Neck", (0, 0, 1.45), (0, 0, 1.55)), ("Head", (0, 0, 1.55), (0, 0, 1.75)),
    ("LeftUpLeg", (0.09, 0, 0.9), (0.09, 0, 0.45)), ("LeftLeg", (0.09, 0, 0.45), (0.09, 0, 0.02)),
    ("RightUpLeg", (-0.09, 0, 0.9), (-0.09, 0, 0.45)), ("RightLeg", (-0.09, 0, 0.45), (-0.09, 0, 0.02)),
    ("LeftArm", (0.17, 0, 1.4), (0.24, 0, 1.1)), ("LeftForeArm", (0.24, 0, 1.1), (0.3, 0, 0.82)),
    ("RightArm", (-0.17, 0, 1.4), (-0.24, 0, 1.1)), ("RightForeArm", (-0.24, 0, 1.1), (-0.3, 0, 0.82)),
]


def body_parts():
    return [tube([(0.09, 0, 0.0), (0.09, 0, 0.95)], 0.06), tube([(-0.09, 0, 0.0), (-0.09, 0, 0.95)], 0.06),
            tube([(0, 0, 0.85), (0, 0, 1.45)], 0.12), tube([(0, 0, 1.4), (0, 0, 1.58)], 0.05),
            tube([(0, 0, 1.52), (0, 0, 1.75)], 0.1),
            tube([(0.15, 0, 1.4), (0.24, 0, 1.1), (0.3, 0, 0.8)], 0.045), tube([(-0.15, 0, 1.4), (-0.24, 0, 1.1), (-0.3, 0, 0.8)], 0.045)]


def character(*extra):
    V, F = join(*body_parts(), *extra)
    bones = [{"name": "mixamorig:" + n, "head": list(h), "tail": list(t), "parent": None} for n, h, t in BONES]
    # weights: each vertex on its nearest bone
    segs = [(np.array(b["head"]), np.array(b["tail"])) for b in bones]
    D = np.stack([np.linalg.norm(V - np.array([sp._closest_on_segment(v, a, b) for v in V]), axis=1) for a, b in segs], axis=1)
    WI = np.full((len(V), 4), -1)
    WW = np.zeros((len(V), 4))
    WI[:, 0] = np.argmin(D, axis=1)
    WW[:, 0] = 1.0
    return V, F, bones, (WI, WW)


PONYTAIL = [(0, 0.06, 1.63), (0, 0.17, 1.5), (0, 0.22, 1.2)]


@pytest.fixture(scope="module")
def ponytail_case():
    pt = tube(PONYTAIL, 0.03)
    V, F, bones, W = character(pt)
    hair = np.zeros(len(V), bool)
    hair[len(V) - len(pt[0]):] = True  # the ponytail's vertices are the last ones
    hair[V[:, 2] > 1.72] = True  # and the crown
    return sp.find_parts(V, F, bones, W, hair, None), V


def test_a_ponytail_clear_of_the_neck_is_found_as_hair_on_the_head(ponytail_case):
    found, V = ponytail_case
    assert len(found["parts"]) == 1
    p = found["parts"][0]
    assert p["kind"] == "Hair" and p["parent"] in ("mixamorig:Head", "mixamorig:Neck")
    assert p["names"] == ["Spring_Hair_01"]
    assert p["gap_cm"] >= 1.5 and p["confidence"] >= sp.CONFIDENCE_MIN
    col = np.array(p["columns"][0])
    assert len(col) - 1 in (2, 3, 4)
    assert col[0][2] > col[-1][2] + 0.2  # root at the top, tip at the bottom
    assert col[-1][1] > 0.15  # behind the back


def test_a_bob_around_the_head_has_nothing_that_hangs():
    bob = join(tube([(0, 0, 1.5), (0, 0, 1.72)], 0.115), tube([(0, 0, 1.5), (0, 0, 1.72)], 0.108))
    V, F, bones, W = character(bob)
    hair = V[:, 2] > 1.49
    found = sp.find_parts(V, F, bones, W, hair, None)
    assert found["parts"] == []


def test_a_scabbard_at_the_hip_is_left_alone():
    scab = tube([(0.15, 0, 0.95), (0.26, 0, 0.45)], 0.025)
    V, F, bones, W = character(scab)
    found = sp.find_parts(V, F, bones, W, None, None)
    assert found["parts"] == []
    side = next(r for r in found["rejected"] if r["reason"] in ("side", "limb"))
    assert "rigid things do not sway" in side["why"]


def test_metal_hanging_at_the_back_is_not_a_tail():
    rod = tube([(0, 0.1, 0.95), (0, 0.2, 0.45)], 0.02)
    V, F, bones, W = character(rod)
    metal = np.zeros(len(V))
    metal[len(V) - len(rod[0]):] = 0.9
    found = sp.find_parts(V, F, bones, W, None, metal)
    assert found["parts"] == []


def test_a_cape_behind_the_legs_is_found_with_chains_side_by_side():
    cape = join(box((-0.18, 0.16, 0.5), (0.18, 0.18, 1.42)), box((-0.18, 0.05, 1.37), (0.18, 0.18, 1.42)))
    V, F, bones, W = character(cape)
    found = sp.find_parts(V, F, bones, W, None, None)
    assert len(found["parts"]) == 1
    p = found["parts"][0]
    assert p["kind"] == "Cape" and p["parent"].endswith(("Spine2", "Spine1"))
    assert len(p["columns"]) in (2, 3)
    assert p["names"][0] == "Spring_Cape_01"
    assert sum(len(c) - 1 for c in p["columns"]) <= sp.MAX_BONES


def test_a_part_that_points_up_is_rejected_and_one_joined_at_both_ends_too():
    quiver_arrows = tube([(0.05, 0.14, 1.3), (0.08, 0.2, 1.75)], 0.03)
    V, F, bones, W = character(quiver_arrows)
    found = sp.find_parts(V, F, bones, W, None, None)
    assert found["parts"] == []


# --- chains and weights -----------------------------------------------------------------------------------------------


def test_bones_per_chain_by_length():
    assert sp.bones_for(0.1) == 2 and sp.bones_for(0.25) == 3 and sp.bones_for(0.6) == 4
    pts = sp.resample([(0, 0, 1), (0, 0, 0.5), (0, 0.5, 0.5)], 4)
    assert len(pts) == 5 and np.allclose(pts[0], (0, 0, 1)) and np.allclose(pts[-1], (0, 0.5, 0.5))


def test_weights_fade_from_the_root_to_the_tip():
    joints = [(0, 0, 1.0), (0, 0, 0.8), (0, 0, 0.6), (0, 0, 0.4)]
    cols = [{"bones": ["S_1", "S_2", "S_3"], "joints": joints}]
    P = np.array([(0, 0.01, 1.0), (0, 0.01, 0.9), (0, 0.01, 0.7), (0, 0.01, 0.41)])
    old = [{"Head": 0.7, "Neck": 0.2, "Spine2": 0.1}] * 4
    w = sp.chain_weights(P, old, cols)
    for m in w:
        assert len(m) <= 4 and abs(sum(m.values()) - 1) < 1e-6
    # the root keeps the body's weights, so the surface there cannot stretch
    assert sum(v for k, v in w[0].items() if not k.startswith("S_")) > 0.99
    # the tip is held by the chain only, mostly by its last bone
    assert sum(v for k, v in w[3].items() if k.startswith("S_")) > 0.99 and max(w[3], key=w[3].get) == "S_3"
    # in between, the share rises
    share = [sum(v for k, v in m.items() if k.startswith("S_")) for m in w]
    assert share == sorted(share)


def test_the_stray_weight_clean_up_never_touches_a_spring_chain():
    segs = {"mixamorig:Head": ((0, 0, 1.55), (0, 0, 1.75)), "Spring_Hair_01_3": ((0, 0.2, 1.3), (0, 0.22, 1.2))}
    tip = (0, 0.22, 1.2)
    assert rig_fix.stray_influences(tip, {"mixamorig:Head": 0.05, "Spring_Hair_01_3": 0.95}, segs, 0.6) == []


# --- the simulation ---------------------------------------------------------------------------------------------------

REST = [(0, 0.05, 1.6), (0, 0.15, 1.45), (0, 0.18, 1.3), (0, 0.2, 1.15)]
SET = sp.SETTINGS["Hair"]
ID = (1.0, 0.0, 0.0, 0.0)


def frames_bob(n, amp=0.0, sway_deg=0.0):
    out = []
    for i in range(n):
        t = 2 * math.pi * i / (n - 1)
        a = math.radians(sway_deg) * math.sin(t) / 2
        out.append({"root": np.array(REST[0]) + (0, 0, amp * math.sin(2 * t)), "q": np.array([math.cos(a), 0, math.sin(a), 0])})
    return out


def test_still_parent_keeps_the_rest_shape():
    res = sp.simulate(frames_bob(25), {"rest": REST, "radius": 0.02}, SET, 24, None, None, loop=True)
    assert np.max(np.abs(res["joints"] - np.array(REST))) < 1e-3


def test_a_looping_clip_ends_where_it_starts():
    res = sp.simulate(frames_bob(33, amp=0.04, sway_deg=12), {"rest": REST, "radius": 0.02}, SET, 24, None, None, loop=True)
    J = res["joints"]
    assert sp.seam_error(J) < 1e-6
    # and it does move
    assert np.max(np.linalg.norm(J[:, -1] - J[0, -1], axis=1)) > 0.01
    # lengths are kept
    L = np.linalg.norm(np.diff(J, axis=1), axis=2)
    assert np.allclose(L, np.linalg.norm(np.diff(np.array(REST), axis=0), axis=1), atol=1e-6)


def test_walking_through_the_air_makes_the_part_trail_behind():
    still = sp.simulate(frames_bob(33), {"rest": REST, "radius": 0.02}, SET, 24, None, None, loop=True)["joints"]
    walk = sp.simulate(frames_bob(33), {"rest": REST, "radius": 0.02}, SET, 24, None, (0, -sp.travel_for("walk", 1.8), 0), loop=True)["joints"]
    assert walk[:, -1, 1].mean() > still[:, -1, 1].mean() + 0.01  # behind is +Y
    assert sp.travel_for("Run", 1.8) == 3.5 and sp.travel_for("idle", 1.8) == 0.0 and sp.travel_for("crouch_walk", 0.9) == pytest.approx(0.4)


def test_the_chain_is_pushed_out_of_a_hit_box():
    body = [(np.array([0, 0.25, 1.35]), np.array([0, 0.25, 1.0]), 0.06)]  # a capsule right where the part hangs
    allow = sp.collider_allowance(REST, body, 0.0)
    assert allow[0] < 0.06  # the rest pose is already inside: the chain uses a smaller radius, so it is not pushed off
    fr = frames_bob(25, amp=0.03, sway_deg=20)
    cols = [[(a, b, r) for (a, b, _), r in zip(body, allow)]] * len(fr)
    res = sp.simulate(fr, {"rest": REST, "radius": 0.0}, SET, 24, cols, None, loop=True)
    assert sp.deepest_beyond_rest(res["joints"], [body] * len(fr), allow) < 0.005


def test_close_loop_makes_first_and_last_equal():
    J = np.array([[[0, 0, 1], [0, 0.1, 0.9]], [[0, 0, 1], [0.05, 0.1, 0.9]], [[0, 0, 1], [0.02, 0.1, 0.9]]], float)
    out = sp.close_loop(J, rest_lengths=[math.hypot(0.1, 0.1)])
    assert sp.seam_error(out) < 1e-9


# --- springs.json -----------------------------------------------------------------------------------------------------


def test_springs_json_is_in_gltf_space_with_settings_and_colliders():
    hb = {"hitboxes": [{"name": "Hit_Head", "bone": "mixamorig:Head", "kind": "capsule", "center": [0, 1.6, 0], "axis": [0, 1, 0],
                        "forward": [0, 0, 1], "radius": 0.1, "height": 0.25, "size": [0.2, 0.25, 0.2], "offset": [0, 0, 0]}]}
    chains = [{"name": "Spring_Hair_01", "kind": "Hair", "parent": "mixamorig:Head", "bones": ["Spring_Hair_01_1", "Spring_Hair_01_2"],
               "joints": [(0, 0.05, 1.6), (0, 0.15, 1.45), (0, 0.2, 1.2)], "radius": 0.02, "settings": dict(SET), "confidence": 0.9}]
    doc = sp.springs_doc(chains, hb, 1.7)
    c = doc["chains"][0]
    assert doc["space"] == "gltf" and c["joints"][0] == [0.0, 1.6, -0.05]  # Blender (x, y, z) -> glTF (x, z, -y)
    assert c["stiffness"] == SET["stiffness"] and c["drag"] == SET["drag"] and c["gravity"] == SET["gravity"]
    assert doc["colliders"][0]["name"] == "Hit_Head" and c["colliders"] == ["Hit_Head"]
    json.dumps(doc)


# --- the backend: whether it runs, metrics and the check sentences ----------------------------------------------------


def test_who_gets_spring_bones():
    assert springs_mod.enabled("humanoid", {}) == (True, None)
    assert springs_mod.enabled("humanoid", {"springs": False}) == (False, "off")
    assert springs_mod.enabled("quadruped", {}, {"tail": ["bone_14", "bone_15"]}) == (False, "tail_moves")
    assert springs_mod.enabled("serpent", {}, {"tail": []}) == (False, "category")
    assert springs_mod.enabled("prop_rigid", {}) == (False, None)
    p = next(x for x in reference.STAGE_SCHEMAS["rig"] if x["name"] == "springs")
    assert p["default"] is True and p["label"] == "Spring bones (small extra bones that sway on their own)"
    assert "springs" in rig_stage.MOVES_PARAMS  # turning it on or off makes only the moves again


REPORT = {"ok": True, "bones_added": 7, "find_seconds": 3.1, "bake_seconds": 9.0,
          "chains": [{"name": "Spring_Hair_01", "kind": "Hair", "parent": "mixamorig:Head", "bones": ["a", "b", "c", "d"], "confidence": 0.9,
                      "part": {"length": 0.41, "gap_cm": 3.2}},
                     {"name": "Spring_Cape_01", "kind": "Cape", "parent": "mixamorig:Spine2", "bones": ["e", "f", "g"], "confidence": 0.8,
                      "part": {"length": 0.8, "gap_cm": 6.0}}],
          "rejected": [{"reason": "side", "why": sp.REASONS["side"], "length": 0.4, "top": 0.95}],
          "clips": {"walk": {"loop": True, "chains": {"Spring_Hair_01": {"seam_m": 0.0, "into_beyond_rest_m": 0.004, "into_hitbox_m": 0.01, "swing_deg": 18}}},
                    "attack": {"loop": False, "chains": {"Spring_Hair_01": {"seam_m": None, "into_beyond_rest_m": 0.01, "into_hitbox_m": 0.02, "swing_deg": 40}}}}}


def _help(m):
    return rig_stage.springs_check(m)


def test_check_says_what_sways_and_what_was_left_alone():
    m = springs_mod.metrics(REPORT, True, None, 14.2)
    assert m["made"] and m["bones_added"] == 7 and m["seam_max_m"] == 0.0 and m["into_body_max_m"] == 0.01
    c = _help(m)
    assert c["passed"] and c["severity"] == "warn"
    assert c["help"].startswith("Spring bones (small extra bones that sway on their own): the hair (a ponytail or a braid) and the cape sway ")
    assert "7 added bones" in c["help"] and "scabbard" in c["help"] and "{" not in c["help"]


def test_check_stands_back_with_a_plain_reason():
    for reason, words in (("none_found", "nothing hangs clear of the body"), ("off", "they are turned off"),
                          ("tail_moves", "the tail already moves"), ("category", "people-shaped")):
        c = _help(springs_mod.metrics({"chains": [], "reason": reason} if reason == "none_found" else None, reason == "none_found", reason))
        assert c["passed"] and words in c["help"] and c["help"].startswith("No spring bones"), c["help"]
        assert "fix" not in c


def test_check_fails_softly_on_a_jump_at_the_seam_or_a_part_inside_the_body():
    bad = json.loads(json.dumps(REPORT))
    bad["clips"]["walk"]["chains"]["Spring_Hair_01"]["seam_m"] = 0.03
    c = _help(springs_mod.metrics(bad, True, None))
    assert not c["passed"] and "jumps by 3 cm" in c["help"] and c["fix"]["stage"] == "rig" and c["fix"]["params"] == {"springs": True}
    deep = json.loads(json.dumps(REPORT))
    deep["clips"]["attack"]["chains"]["Spring_Hair_01"]["into_beyond_rest_m"] = 0.06
    c = _help(springs_mod.metrics(deep, True, None))
    assert not c["passed"] and "6 cm into the body" in c["help"]
    err = _help(springs_mod.metrics(None, True, None, error="boom"))
    assert not err["passed"] and "went wrong" in err["help"]


def test_the_hand_off_readme_and_manifest():
    doc = sp.springs_doc([{"name": "Spring_Hair_01", "kind": "Hair", "parent": "mixamorig:Head", "bones": ["Spring_Hair_01_1"],
                           "joints": [(0, 0, 1), (0, 0, 0.8)], "radius": 0.02, "settings": dict(SET), "confidence": 0.9}], None, 1.7)
    lines = "\n".join(springs_mod.readme_lines("Knight", doc))
    assert "SpringBoneSimulator3D" in lines and "AnimDynamics" in lines and lines.count("not tried here") == 2
    assert "Spring bones (small extra bones that sway on their own)" in lines
    block = springs_mod.manifest_block(doc, {"springChains": 1})
    assert block["bones"] == 1 and block["unity"]["chains"] == 1 and "not tried here" in block["godot"]
    assert springs_mod.manifest_block({}, None) is None and springs_mod.readme_lines("x", {}) == []


def test_springs_shipped_check(tmp_path):
    doc = {"chains": [{"name": "Spring_Hair_01", "bones": ["Spring_Hair_01_1", "Spring_Hair_01_2"]}]}
    (tmp_path / "springs.json").write_text(json.dumps(doc))
    gltf = json.dumps({"nodes": [{"name": "Spring_Hair_01_1"}, {"name": "Spring_Hair_01_2"}]}).encode()
    gltf += b" " * (-len(gltf) % 4)
    glb = b"glTF" + (2).to_bytes(4, "little") + (20 + len(gltf)).to_bytes(4, "little") + len(gltf).to_bytes(4, "little") + b"JSON" + gltf
    (tmp_path / "x.glb").write_bytes(glb)
    files = {"springs": tmp_path / "springs.json", "glb": tmp_path / "x.glb", "fbx": None}
    rig_v = SimpleNamespace(metrics={"springs": {"made": True}})
    sc = SimpleNamespace(stage_version=lambda s: rig_v, file=lambda k: files.get(k), version=SimpleNamespace(metrics={"unity_report": {"ok": True, "springChains": 1}}))
    c = export_stage.springs_shipped_checks(sc)[0]
    assert c["passed"] and "In Unity the prefab has the spring script" in c["help"]
    sc.version.metrics["unity_report"]["springChains"] = 0
    c = export_stage.springs_shipped_checks(sc)[0]
    assert not c["passed"] and "Unity did not put the spring script" in c["help"]
    rig_v.metrics["springs"] = {"made": False}
    assert export_stage.springs_shipped_checks(sc) == []


def test_spring_script_travels_with_the_importer(tmp_path):
    src = tmp_path / "src.cs"
    src.write_text("// VgacSpringBones Version 2\n")
    dest = tmp_path / "proj" / "Assets" / "VgacScripts" / "VgacSpringBones.cs"
    assert export_stage.sync_spring_script(src, dest)["action"] == "copied"
    assert export_stage.sync_spring_script(src, dest)["action"] == "current"
    sandbox = Path(__file__).resolve().parents[2] / "unity-sandbox"
    assert export_stage.spring_script_version(sandbox / export_stage.SPRING_SCRIPT_REL) >= 1
    assert export_stage.importer_version(sandbox / "Assets" / "Editor" / "VgacImport.cs") >= 15


def test_guide_entries_exist():
    assert guidance.CHECK_GUIDE["springs"]["severity"] == "warn" and guidance.CHECK_GUIDE["springs_shipped"]["severity"] == "warn"
    assert guidance.PARAM_HELP["springs"]["help"]["meaning"].startswith("Spring bones (small extra bones that sway on their own)")
    q = quality.check("springs_shipped", True, 2, 2, vars={"unity": False})
    assert q["help"].startswith("The files carry the spring bones: 2 chains")


# --- what may sway on the head (Vivi's hat swayed as "a scarf end") -----------------------------------------------------


def test_a_non_hair_part_hanging_from_the_head_stays_with_the_head():
    pt = tube(PONYTAIL, 0.03)
    V, F, bones, W = character(pt)
    plain = sp.find_parts(V, F, bones, W, None, None)  # no hair found on it: a hat's back, a hood's point
    assert not plain["parts"] and any(r["reason"] == "headwear" for r in plain["rejected"])
    assert "moves with the head" in next(r for r in plain["rejected"] if r["reason"] == "headwear")["why"]
    asked = sp.find_parts(V, F, bones, W, None, None, head_cloth="sway")  # a headscarf's end, when asked
    assert [p["kind"] for p in asked["parts"]] == ["Cloth"]


def test_under_headwear_a_head_part_must_be_clearly_hair():
    pt = tube(PONYTAIL, 0.03)
    V, F, bones, W = character(pt)
    hair = np.zeros(len(V), bool)
    tail = np.arange(len(V) - len(pt[0]), len(V))
    hair[tail] = True
    hair[tail[::3]] = False  # two thirds hair-coloured: a dark brim, say
    assert [p["kind"] for p in sp.find_parts(V, F, bones, W, hair, None)["parts"]] == ["Hair"]
    assert not sp.find_parts(V, F, bones, W, hair, None, headwear=True)["parts"]


def test_head_options_follow_the_description_and_the_setting():
    from vgac import springs as sm

    assert sm.head_options({}, "a little black mage in a tall pointed hat") == {"head_sway": "auto", "head_cloth": "stiff", "headwear": True, "why": "default"}
    assert sm.head_options({}, "a dancer with long red ribbons in her hair")["head_cloth"] == "sway"
    assert sm.head_options({"head_sway": "all"}, "a knight in a helmet")["head_cloth"] == "sway"
    assert sm.head_options({"head_sway": "hair"}, "a woman in a headscarf")["head_cloth"] == "stiff"
    assert not sm.head_options({}, "a chatty villager")["headwear"]
    args = sm.blender_args(Path("a.glb"), Path("b.glb"), Path("w"), None, None, None, None, head={"head_cloth": "stiff", "headwear": True})
    assert args[-3:] == ["--head-cloth", "stiff", "--headwear"]
